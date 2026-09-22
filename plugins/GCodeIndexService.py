from __future__ import annotations

from UM.Logger import Logger


from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from dataclasses import dataclass
import sys
import threading
import time
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal

from .GCodeIndex import LayerMotionIndex, build_index_from_file, hydrate_layer_from_file
from .MonitorFormatting import _segment_in_polygon, polygon_bounds
from .PlateProgress import (
    PreparationYield,
    decode_layer as _decode_layer,
    encode_layer as _encode_layer,
    motion_edges as _motion_edges,
    prepare_layer as _prepare_layer,
    split_index as _split_index,
)
from .PreparedStore import STATE_CACHED, STATE_EMPTY, STATE_UNCACHEABLE


@dataclass(frozen=True)
class IndexView:
    """Read-only query capability, never mutable arrays or worker state."""
    job_key: tuple
    _index: LayerMotionIndex

    @property
    def ranges(self): return tuple(self._index.ranges)
    @property
    def current_layer_map(self): return MappingProxyType(self._index.current_layer_map)
    @property
    def elapsed_times(self): return tuple(self._index.layer_elapsed_times)
    @property
    def compact(self): return self._index.compact
    @property
    def pause_layers(self): return tuple(self._index.pauses)

    def hydrated(self, layer):
        return not self.compact or layer in self._index.hydrated_layers

    def fraction(self, layer, position, live, minimum=None):
        return self._index.refined_fraction(layer, position, live, minimum_fraction=minimum)

    def layer_at(self, position):
        low, high = 0, len(self._index.ranges) - 1
        while low <= high:
            middle = (low + high) // 2
            start, end = self._index.ranges[middle]
            if position < start: high = middle - 1
            elif position >= end: low = middle + 1
            else: return middle
        return min(low - 1, len(self._index.ranges) - 1) if low else None


# The RAM tier budgets , from the
# measured real print (467 MB, 327 layers): packed layers run
# 13.5 KB - 927 KB (p50 610 KB), decoded layers 0.2 MB - 11.3 MB
# (p50 8.0 MB). 64 MB holds ~105 median packed layers — a third of
# the print — and 128 MB holds six dense decoded windows with room.
_FULL_CACHE_MAX_BYTES = 64 * 1024 * 1024
_DECODED_LRU_MAX_BYTES = 128 * 1024 * 1024
# The decoded LRU's guaranteed floor: ONE entry — the just-committed
# layer before its render wrappers pin it. The live and frozen
# windows' protection moved to the pins (the wrappers charge their
# payloads against the budget explicitly), so the old 4-entry floor's
# RAM no longer outvotes the byte bound.
_DECODED_LRU_MIN_ENTRIES = 1
# Decoded PPL1 expands into nested Python lists. The measured branch
# ratios peak around the mid-teens, so charge a conservative 16x packed
# size instead of recursively walking every decoded point a second time.
# This is accounting, not serialization: an overestimate is safe and
# keeps the byte budget bounded without adding O(points) seek latency.
_DECODED_PACKED_EXPANSION = 16
_DECODED_CHARGE_FLOOR = 4 * 1024


def _decoded_charge(raw=None, payload=None) -> int:
    if isinstance(raw, (bytes, bytearray, memoryview)):
        return max(_DECODED_CHARGE_FLOOR, len(raw) * _DECODED_PACKED_EXPANSION)
    # Encoding failures are exceptional, but display must still work.
    # Charge from the already-known motion count without another geometry
    # traversal. 256 bytes/motion is deliberately conservative.
    motions = 0
    if isinstance(payload, dict):
        try:
            motions = max(0, int(payload.get("motions") or 0))
        except (TypeError, ValueError):
            motions = 0
    return max(_DECODED_CHARGE_FLOOR, motions * 256)


def _deep_size(obj) -> int:
    """A decoded payload's true byte footprint (its points dominate;
    a shallow getsizeof misses them)."""
    total = 0
    seen = set()

    def walk(o):
        nonlocal total
        oid = id(o)
        if oid in seen:
            return
        seen.add(oid)
        try:
            total += sys.getsizeof(o)
        except TypeError:
            return
        if isinstance(o, dict):
            for key, value in o.items():
                walk(key)
                walk(value)
        elif isinstance(o, (list, tuple)):
            for value in o:
                walk(value)

    walk(obj)
    return total


class _ByteBoundedLru:
    """A byte-budgeted access-order cache with a minimum-entry floor.
    ``peek`` reads without touching the order (the worker's path —
    only the owner mutates); ``set`` and ``get`` refresh recency.
    Sizes are explicit (the worker measures); a bare ``__setitem__``
    (the tests' fixture path) falls back to the deep walk."""

    def __init__(self, max_bytes: int, min_entries: int = 0) -> None:
        self._data = OrderedDict()
        self._sizes = {}
        self._bytes = 0
        self.max_bytes = max_bytes
        self.min_entries = min_entries
        # The demanded windows (the live print's ±1 and the detached
        # follower's frozen ±1): eviction skips these layers — the
        # owner updates the set as the anchors move.
        self.protected = set()

    def __len__(self):
        return len(self._data)

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        value = self._data[key]
        self._data.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        self.set(key, value, _deep_size(value))

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def peek(self, key):
        return self._data.get(key)

    def get(self, key):
        value = self._data.get(key)
        if value is not None:
            self._data.move_to_end(key)
        return value

    def touch(self, key) -> None:
        if key in self._data:
            self._data.move_to_end(key)

    def set(self, key, value, size: int) -> None:
        if key in self._data:
            self._bytes -= self._sizes.get(key, 0)
        self._data[key] = value
        self._sizes[key] = size
        self._bytes += size
        self._trim()

    def update(self, items) -> None:
        for key, value in items.items():
            if key in self._data:
                self._bytes -= self._sizes.get(key, 0)
            self._data[key] = value
            self._sizes[key] = len(value) if isinstance(value, (bytes, bytearray)) else _deep_size(value)
            self._bytes += self._sizes[key]
        self._trim()

    def pop(self, key, default=None):
        value = self._data.pop(key, default)
        if value is not default:
            self._bytes -= self._sizes.pop(key, 0)
        return value

    def popitem(self, last=True):
        key, value = self._data.popitem(last)
        self._bytes -= self._sizes.pop(key, 0)
        return key, value

    def move_to_end(self, key) -> None:
        self._data.move_to_end(key)

    def clear(self) -> None:
        self._data.clear()
        self._sizes.clear()
        self._bytes = 0

    def total_bytes(self) -> int:
        return self._bytes

    def _trim(self) -> None:
        # The floor (the decoded LRU's): below it, never evict — the
        # active windows must survive a single pathological layer.
        # The protected windows (the live ±1 and the frozen ±1) are
        # skipped: evicting a demanded layer flips the memoised
        # bundle's decoded bit and the demand re-decodes it — a
        # per-poll eviction/redemption thrash (the detached 114%
        # burn).
        while self._bytes > self.max_bytes and len(self._data) > self.min_entries:
            victim = None
            for key in self._data:
                if key not in self.protected:
                    victim = key
                    break
            if victim is None:
                break
            del self._data[victim]
            self._bytes -= self._sizes.pop(victim, 0)


def _polygon_identity(polygon):
    """A polygon's CONTENT identity: its pairs, as a hashable tuple.

    The live walk cannot key on object identity — the coordinator
    rebuilds the rows (and their polygon lists) from the status on
    every poll, so equal geometry arrives as a fresh object. Only the
    content says whether the geometry actually changed."""
    return tuple((point[0], point[1]) for point in polygon)


class GCodeIndexService(QObject):
    """Own index lifecycle with at most ONE submitted worker job, no work queue.

    Requests coalesce into desired state; replacement waits asynchronously for
    the old worker. Cache restore/build/hydration/save all run off the UI thread.
    """
    changed = pyqtSignal()
    failed = pyqtSignal(str)
    _completed = pyqtSignal(int, str, object, object, object)

    def __init__(self, files, cache, parent=None, prepared=None):
        super().__init__(parent)
        self._files, self._cache = files, cache
        # The file-backed prepared store: the
        # pass's encodings persist per print, random-accessible, so
        # a reopened print skips the whole preparation walk.
        self._prepared = prepared
        self._prepared_table = None
        self._prepared_identity = None
        self._prepared_saved = False
        self._prepared_writer = None
        # A failed final publish gets one autonomous bounded rebuild.
        # This is reset per print and on a successful publish.
        self._prepared_retry_count = 0
        # The reopen policy's adoption :
        # a complete clean table takes the fast path; a complete
        # table with holes repairs. The coverage set counts every
        # layer whose prepared representation exists — the pass
        # fraction's truth, independent of RAM residency.
        self._prepared_complete = False
        self._prepared_flag_complete = False
        self._prepared_coverage = set()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MoonrakerIndex")
        self._generation = 0
        self._job = None
        self._view = None
        self._cancel = threading.Event()
        # Worker-safe foreground signal. Background preparation reads
        # only this Event, never Qt/model state, and yields at coarse
        # preparation boundaries when a visible layer is demanded.
        self._foreground_pending = threading.Event()
        self._busy = ""
        # The generation that OWNS the busy flag: a stale worker's
        # terminal clears it only if it belongs to this generation —
        # an old completion must never misrepresent a newer task
        # (the review's cache-clear finding).
        self._busy_generation = -1
        self._wanted = self._restored = self._save = False
        self._hydrate = set()
        self._hydrating = None
        self._failed_hydrate = set()
        self._closed = False
        self._error = ""
        # The build's byte-offset fraction, written from the worker
        # thread's progress callback and read by the coordinator's
        # tick — a plain float, atomic enough under the GIL.
        self._progress = None
        self._plate_layers_memos = {}
        # The full prepared cache (the live request's instant-access
        # store): every layer's compact payload, filled by a
        # background pass that yields to the live demands. A layer
        # asked before the pass reaches it prepares on demand and
        # lands here too.
        self._full_cache = _ByteBoundedLru(_FULL_CACHE_MAX_BYTES)
        self._full_next = 0
        # The index-cache save's throttle: a save every hydrate starved
        # the background pass during a live print (the save branch runs
        # before the pass branch — the "no quicker" live report).
        self._last_save_at = None
        # The hot presentation cache: DECODED
        # payloads, keyed by layer, access-order bounded. The bundle
        # reads it first and reuses the same Python object, so an
        # adjacent seek shares two of its three layers verbatim (no
        # re-decode, no QVariant re-conversion) and the worker hands
        # the first display its own payload instead of a second
        # object decoded from the compact store. Byte-budgeted with
        # a slot floor: the live and
        # frozen windows side by side, bounded by measured bytes.
        self._decoded_lru = _ByteBoundedLru(_DECODED_LRU_MAX_BYTES, _DECODED_LRU_MIN_ENTRIES)
        # The render wrappers' pins: a wrapper holding a decoded
        # payload keeps its bytes charged against the budget even
        # after the LRU evicts the entry (the wrapper keeps the
        # object alive, so the charge must stay alive too). The
        # sizes mirror retains every charged layer's size — one int
        # per layer ever decoded, bounded by the file's layer count —
        # so a memoised payload the LRU already evicted still pins
        # with its true size.
        self._decoded_pins = {}
        self._decoded_sizes = {}
        # The follower's frozen layer (the pop-over's detach): a second
        # demand window beside the live print's own.
        self._manual_anchor = None
        self._manual_anchor_calls = 0
        self._manual_anchor_changes = 0
        # The within-layer scrub (the pop-over's progress slider): the
        # manual split for the frozen layer, None while the live print's
        # boundary is the one being shown.
        self._manual_split = None
        # The boundary already painted for a (file, layer) — the floor
        # the next poll's refinement may not fall below — and the last
        # one a LIVE position established, None while there is none.
        self._split_floor = None
        self._split_refined = None
        self._split_floor_key = None
        self._visited_key = None
        self._visited = set()
        self._visited_upto = -1
        self._visited_settled = frozenset()
        self._completed.connect(self._finish)
        files.changed.connect(self._on_files_changed)

    @property
    def view(self): return self._view
    @property
    def generation(self): return self._generation
    @property
    def progress(self): return self._progress
    @property
    def phase(self):
        if self._error: return "error"
        if self._busy in {"build", "restore"}: return "indexing"
        return "ready" if self._view else "idle"

    def bind(self, job_key):
        if self._job == job_key: return
        self._generation += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self._job, self._view = job_key, None
        # The panel's catch: the build's progress survives into the
        # cache RESTORE otherwise — the bar read the previous
        # print's final 100% through the whole restore phase.
        self._progress = None
        # The incremental writer: the pass
        # appends the encodings layer by layer, so the first session
        # never retains the whole cold store in RAM. A bind
        # RETIRES the old print's unfinished writer (the ownership
        # cutover — the freeze precedes the checkpoint) and
        # CHECKPOINTS it: its committed layers publish as an
        # incomplete store the print's next session resumes (never a
        # bare drop of the reference, never a needless loss of the
        # old print's progress).
        self._retire_prepared_writer()
        self._reset_print_state()
        # Keep _busy until the submitted worker actually completes. No new task
        # is submitted while a stale job is still executing.
        self.changed.emit()

    def request(self):
        self._wanted = True
        self._advance()

    def invalidate(self):
        """The cache-clear reset (the live ruling): the backing files
        are GONE, so every listener must drop to the no-index state —
        the in-memory index, the prepared tables, the decoded and
        full caches, the hydration latches and the memoised
        boundaries all go. The changed signal drives the
        coordinator's refresh, so the follower, the EOP and the ETA
        republish the unavailable state; a later request rebuilds
        from the session's downloaded file or re-downloads it."""
        self._cancel.set()
        self._cancel = threading.Event()
        self._generation += 1
        # The active writer retires BEFORE the state resets (the
        # review's cache-clear finding): the retire freezes every
        # later append and finish and checkpoints the committed
        # layers through its own store — no worker can ever write
        # to it again.
        self._retire_prepared_writer()
        self._view = None
        self._job = None
        self._wanted = self._restored = self._save = False
        self._busy = ""
        self._busy_generation = self._generation
        self._error = ""
        self._progress = None
        self._hydrate.clear()
        self._hydrating = None
        self._failed_hydrate.clear()
        self._prepared_table = None
        self._prepared_identity = None
        self._prepared_saved = False
        self._prepared_writer = None
        self._prepared_retry_count = 0
        self._prepared_complete = False
        self._prepared_flag_complete = False
        self._prepared_coverage = set()
        self._full_cache.clear()
        self._full_next = 0
        self._last_save_at = None
        self._decoded_lru.clear()
        self._decoded_lru.protected = set()
        self._decoded_pins = {}
        self._decoded_sizes = {}
        self._plate_layers_memos = {}
        self._manual_anchor = None
        self._manual_split = None
        self._split_floor = None
        self._split_refined = None
        self._split_floor_key = None
        self._visited_key = None
        self._visited = set()
        self._visited_upto = -1
        self._visited_settled = frozenset()
        self.changed.emit()

    def request_hydration(self, layer):
        view = self._view
        if view is None or not 0 <= layer < len(view.ranges):
            return
        self._request_window(int(layer))
        self._advance()

    def set_manual_anchor(self, layer):
        """The follower's DETACHED anchor: the window around the layer
        the user froze the face on, demanded BESIDE the live print's
        own.

        It rides its own demand path — the live ±1 window is what the
        retention bound is anchored to, and re-anchoring it to a frozen
        layer would evict the live layer the dot, the split and the
        printed fill all read. ``None`` rejoins the live print's window.
        """
        normalised = layer if isinstance(layer, int) and not isinstance(layer, bool) \
            and layer >= 0 else None
        # The idempotency diagnostics: the calls vs the real changes
        # (the detached burn's proof — the coordinator re-asserts the
        # same anchor every refresh).
        self._manual_anchor_calls = getattr(self, "_manual_anchor_calls", 0) + 1
        if normalised == self._manual_anchor:
            # The coordinator re-asserts the SAME anchor on every
            # refresh while detached; without this no-op the rewind,
            # the demand and the advance ran per refresh and the
            # worker's own completion fed the loop back through
            # changed -> refresh (the detached 114% burn).
            return
        self._manual_anchor_changes = getattr(self, "_manual_anchor_changes", 0) + 1
        self._manual_anchor = normalised
        if self._manual_anchor is None:
            # Rejoining the live print abandons the scrub: the live
            # split is the print's own again.
            self._manual_split = None
        else:
            # A seek focuses the pass: the sought window prepares
            # before the pass resumes wherever it stood (the live
            # report — a far seek waited for the pass to walk the
            # whole file).
            self._full_next = min(self._full_next, max(0, self._manual_anchor - 1))
        self._apply_manual_anchor()
        self._request_manual_window()
        self._advance()

    def set_manual_split(self, motions):
        """The follower's DETACHED split: the within-layer boundary the
        user scrubbed to, shown instead of the live print's own while
        the anchor is frozen. -1 is the FULL marker — the frozen
        layer draws every motion (a seek lands at 100%, the live
        request). ``None`` restores the frozen layer's whole-base
        draw.
        """
        self._manual_split = motions if isinstance(motions, int) and not isinstance(motions, bool) \
            and motions >= -1 else None

    def _apply_manual_anchor(self):
        """Hand the frozen anchor to the index's own retention bound.

        A layer outside the index is no anchor at all, and the index
        may not exist yet at the detach (the build lands later), so
        this is applied wherever a view is in hand.
        """
        view = self._view
        if view is None:
            return
        manual = self._manual_anchor
        if manual is not None and manual >= len(view.ranges):
            manual = None
        if view._index.manual_anchor == manual:
            return
        with view._index.cache_lock:
            view._index.manual_anchor = manual
        self._update_decoded_protection()

    def _update_decoded_protection(self):
        """The demanded windows must survive the decoded budget's
        eviction: the live print's ±1 and the frozen follower's ±1.
        Evicting a demanded layer flips the memoised bundle's decoded
        bit and the demand re-decodes it — a per-poll
        eviction/redemption thrash (the detached 114% burn)."""
        if self._view is None:
            return
        index = self._view._index
        protected = set()
        followed = getattr(index, "followed_layer", None)
        manual = getattr(index, "manual_anchor", None)
        for anchor in (followed, manual):
            if isinstance(anchor, int) and anchor >= 0:
                protected.update((anchor - 1, anchor, anchor + 1))
        self._decoded_lru.protected = protected

    def _presentation_source(self, layer):
        """Cheapest source for the PRESENTATION payload of one layer.

        Index hydration and presentation readiness are intentionally
        different states: a non-compact index is fully hydrated from the
        start, while its decoded presentation cache starts empty.
        """
        view = self._view
        if view is None or not 0 <= layer < len(view.ranges):
            return "invalid"
        if layer in self._decoded_lru:
            return "decoded"
        # A failed presentation source is latched until the underlying
        # file/index changes. Check the latch before selecting packed,
        # prepared or hydrated sources; otherwise the same broken source
        # is immediately re-demanded on the next poll.
        if layer in self._failed_hydrate:
            return "failed"
        if self._full_cache.peek(layer) is not None:
            return "packed"
        if self._prepared_served(layer):
            return "prepared"
        if view.hydrated(layer):
            return "hydrated"
        return "raw"

    def _request_manual_window(self):
        view = self._view
        if view is None or self._manual_anchor is None:
            return
        for candidate in (self._manual_anchor - 1, self._manual_anchor, self._manual_anchor + 1):
            # Presentation demand remains live until DECODED data exists.
            # Hydrated arrays, packed RAM and prepared disk are sources,
            # not readiness signals.
            if self._presentation_source(candidate) not in {"invalid", "decoded", "failed"}:
                self._hydrate.add(candidate)
                self._foreground_pending.set()

    def plate_layers(self, anchor):
        """The follower's STATIC half: the prev/current/next bundle,
        memoised per anchor — the model republishes it with a stable
        identity so QML never re-wraps the polylines on a quiet poll
        (the perf panel's split). TWO slots: the live payload and the
        frozen one alternate every poll while detached, and one slot
        thrashed — each ask evicted the other's bundle, both rebuilt
        every poll, and the whole plugin re-churned (the live report).

        The bundle reads the HOT presentation cache first: an adjacent
        seek shares two of its three layers as the SAME Python objects
        (no re-decode, no QVariant re-conversion), and the worker hands the first display its own
        payload instead of a second object decoded from the compact
        store. A miss reads as not loaded — the UI thread NEVER walks
        geometry ; the demand owns it.
        The counts/hydration/decoded states are all part of the memo
        key: a bundle built while a layer was still landing rebuilds
        when it does."""
        if self._view is None:
            return {}
        index = self._view._index
        window = (anchor - 1, anchor, anchor + 1)
        with index.cache_lock:
            counts = tuple(index.motion_count(layer) for layer in window)
            hydrated = tuple(self._view.hydrated(layer) for layer in window)
        decoded = tuple(1 if layer in self._decoded_lru else 0 for layer in window)
        key = (counts, hydrated, decoded)
        memo = self._plate_layers_memos.get(anchor)
        if memo is not None and memo[0] == key:
            return memo[1]

        # The decode-heavy work runs OUTSIDE the critical section
        # : the hot cache's reads are the
        # only per-layer cost here.
        def layer_or_full(layer):
            if 0 <= layer < len(index.ranges):
                payload = self._decoded_lru.get(layer)
                if payload is not None:
                    self._decoded_lru.move_to_end(layer)
                    return payload
            return None

        bundle = {"prev": layer_or_full(anchor - 1),
                  "current": layer_or_full(anchor),
                  "next": layer_or_full(anchor + 1)}
        if len(self._plate_layers_memos) >= 2:
            # The demand alternates two anchors at most; a third
            # (a layer change, a new seek) resets the pair.
            self._plate_layers_memos = {anchor: (key, bundle)}
        else:
            self._plate_layers_memos[anchor] = (key, bundle)
        return bundle

    def plate_pass_fraction(self):
        """The background optimisation's honest progress: the share of
        layers the pass has RESOLVED — CACHED and UNCACHEABLE alike (a
        refused layer is as resolved as a held one) — the persistent
        table, the incremental writer's completed entries and the
        demand-prepared set, never the RAM tier's bounded residency
        (a 64-entry cache must not cap a 1,000-layer print at 6%)."""
        if self._view is None:
            return None
        total = len(self._view.ranges)
        if not total:
            return None
        if self._prepared_complete:
            return 1.0
        if self._prepared is None or self._prepared_identity is None:
            # No persistence configured: the RAM tier's residency is
            # the only prepared store there is.
            return min(1.0, len(self._full_cache) / total)
        return min(1.0, len(self._prepared_coverage) / total)

    def plate_split(self, anchor, file_position=None, live_position=None):
        """The follower's VOLATILE half: the printed/unprinted boundary
        — the only per-poll cost.

        ``file_position`` is the COARSE anchor — the parser's dispatch
        point — and ``live_position`` refines it through the index's own
        ``refined_split``, so the coloured fill, the toolhead dot and
        the Preview's follower read one physical position instead of
        two that drift by the lookahead. An unrefinable poll keeps the
        coarse boundary: on a machine that reports no live position the
        fill behaves exactly as it did.

        The boundary is monotonic per (file, layer): whatever is
        painted becomes the floor for the next poll, so noisy telemetry
        can never walk the fill backwards. A poll that cannot refine
        holds that floor rather than reading ahead to the parser — an
        off-path head (a pause park, a Z-lift) is exactly when the
        parser's position is most wrong. The floor resets with the
        layer: another layer's count is another layer's boundary.
        """
        if self._view is None:
            return None
        if file_position is None:
            # The frozen layer's scrub: the manual split is the boundary
            # while detached, the whole base while it is unset. The FULL
            # marker resolves to the layer's own motion count — every
            # edge prints (a seek lands at 100%).
            if self._manual_split is not None and anchor == self._manual_anchor:
                if self._manual_split == -1:
                    return self._view._index.motion_count(anchor)
                return self._manual_split
            return None
        memo = self._plate_layers_memos.get(anchor)
        if memo is None or not memo[1].get("current"):
            return None
        index = self._view._index
        with index.cache_lock:
            coarse = _split_index(index, anchor, file_position)
            if coarse is None:
                return None
            if (self._view.job_key, anchor) != self._split_floor_key:
                self._split_floor_key = (self._view.job_key, anchor)
                self._split_floor = None
                self._split_refined = None
            floor = self._split_floor
            refined = None
            if live_position is not None:
                refined, _method = index.refined_split(anchor, file_position, live_position,
                                                       minimum_split=floor)
            if refined is None:
                # The coarse anchor is the only estimate this layer has
                # until a live position establishes one — on a machine
                # that reports none, that is the whole story. Once a
                # physical boundary exists, the parser's position is no
                # improvement on it: hold.
                split = coarse if self._split_refined is None else floor
            else:
                self._split_refined = refined
                split = refined
            self._split_floor = split if floor is None else max(floor, split)
            return split

    def plate_visited(self, anchor, split, rows):
        """The per-layer printed objects: which polygons the executed
        EXTRUSION edges have touched. Built HERE (the raw arrays never
        cross the boundary) and READ BACK FROM THE LAYER'S START — an
        attach part-way through a layer still marks everything the
        toolhead already printed (the live ruling: the DEFINE order
        is not the print order on every machine, so the visits are
        the truth). The walk advances only the new edges per poll.

        The edges are the G-code's own motion edges, and only the
        extruding ones count: a travel that merely crosses or ends
        inside a polygon deposits nothing there, while an extrusion
        edge that clips a corner does — the visit follows the material,
        never the motion endpoint.

        A bare cursor is not a valid cache here: EXCLUDE_OBJECT_DEFINE
        executes mid-layer on some machines, so a polygon can arrive
        after the extrusion it covers has already been walked. The
        cursor is therefore kept beside `_visited_settled` — the
        (name, content) geometry the consumed range has been judged
        against. A poll whose geometry still matches it walks only its
        new edges; a poll carrying a new or changed polygon replays the
        consumed range ONCE, for those polygons alone. The visited set
        only ever grows, so a backwards split keeps its verdicts."""
        if self._view is None or split is None or anchor is None:
            return frozenset()
        index = self._view._index
        entries = []
        for row in rows:
            polygon = row.get("polygon")
            if not polygon or not row.get("name"):
                continue
            entries.append((row["name"], _polygon_identity(polygon),
                            polygon, polygon_bounds(polygon)))
        with index.cache_lock:
            if self._visited_key != (anchor,):
                self._visited_key = (anchor,)
                self._visited = set()
                self._visited_upto = 0
                self._visited_settled = frozenset()
            settled = self._visited_settled
            current, pending = [], []
            for name, key, polygon, bounds in entries:
                current.append((name, polygon, bounds))
                if (name, key) not in settled:
                    pending.append((name, polygon, bounds))
            if pending and self._visited_upto > 0:
                # The late/changed geometry, against everything already
                # consumed. Replaying only these polygons is enough: the
                # settled ones have already seen every consumed edge.
                self._visit_edges(index, anchor, 0, self._visited_upto, pending)
            if split > self._visited_upto:
                self._visit_edges(index, anchor, self._visited_upto, split, current)
                self._visited_upto = split
            self._visited_settled = frozenset((name, key) for name, key, _p, _b in entries)
            return frozenset(self._visited)

    def _visit_edges(self, index, anchor, first, stop, polygons):
        """Mark every polygon an extruding edge in [first, stop) meets."""
        for motion, x0, y0, x1, y1, _feature, extruding in _motion_edges(index, anchor, first):
            if motion >= stop:
                break
            if not extruding:
                continue
            left, right = (x0, x1) if x0 <= x1 else (x1, x0)
            bottom, top = (y0, y1) if y0 <= y1 else (y1, y0)
            for name, polygon, bounds in polygons:
                # The bounds reject most pairs for the price of four
                # comparisons, before any vertex is touched. Every
                # hull the edge meets records the visit — overlapping
                # object hulls all touch the toolhead's path, and the
                # verdict must never depend on the define order.
                if right < bounds[0] or left > bounds[2] or top < bounds[1] or bottom > bounds[3]:
                    continue
                if _segment_in_polygon(x0, y0, x1, y1, polygon):
                    self._visited.add(name)

    def plate_progress(self, anchor, file_position=None, live_position=None):
        """The composed payload (the tests and the one-shot consumers):
        the memoised layers plus the volatile split. The motion total is
        the progress slider's range — the layer's own edge count."""
        layers = self.plate_layers(anchor) if self._view is not None else {}
        split = self.plate_split(anchor, file_position, live_position)
        method = "motion index" if split is not None else "unavailable"
        motion_total = 0
        if self._view is not None:
            with self._view._index.cache_lock:
                motion_total = self._view._index.motion_count(anchor)
        return {"layers": layers, "split": split, "method": method,
                "motionTotal": motion_total, "anchor": anchor}

    # The memory accounting (the RAM tiers' honest view): the
    # wrapper-pinned decoded payloads charge the same budget the LRU
    # draws from, so the combined number is the tier's real
    # residency, not just the LRU's own.
    def pin_decoded(self, layer):
        """A render wrapper pins the decoded payload: its bytes stay
        charged against the decoded budget even after the LRU evicts
        the entry (the wrapper keeps the object alive). Layers the
        service never charged pin nothing."""
        size = self._decoded_sizes.get(layer)
        if size is None:
            return
        self._decoded_pins[layer] = self._decoded_pins.get(layer, 0) + 1
        self._reconcile_decoded()

    def unpin_decoded(self, layer):
        """The wrapper is gone: the pin count drops, and the size
        record stays (it is one int per layer ever decoded)."""
        pins = self._decoded_pins.get(layer, 0)
        if pins <= 1:
            self._decoded_pins.pop(layer, None)
        else:
            self._decoded_pins[layer] = pins - 1

    def _reconcile_decoded(self):
        """The COMBINED bound: pinned payloads count against the
        decoded budget, so the LRU yields to them — entries evict
        until the total (LRU bytes plus pinned-evicted bytes) fits,
        the entry floor the only stop."""
        while (self._decoded_lru.total_bytes() + self.pinned_decoded_bytes()
               > self._decoded_lru.max_bytes
               and len(self._decoded_lru) > self._decoded_lru.min_entries):
            victim = None
            for key in self._decoded_lru:
                if key not in self._decoded_lru.protected:
                    victim = key
                    break
            if victim is None:
                break
            self._decoded_lru.pop(victim)

    def pinned_decoded_bytes(self):
        """The wrapper-pinned payload bytes the LRU has already
        evicted (in-LRU entries count in the LRU's own total)."""
        return sum(size for layer, size in self._decoded_sizes.items()
                   if self._decoded_pins.get(layer) and layer not in self._decoded_lru)

    def decoded_resident_bytes(self):
        """The decoded tier's real residency: the LRU's entries plus
        the wrapper-pinned payloads it evicted."""
        return self._decoded_lru.total_bytes() + self.pinned_decoded_bytes()

    def packed_bytes(self):
        """The packed tier's charged bytes."""
        return self._full_cache.total_bytes()

    def set_followed_layer(self, layer):
        """Anchor the retention window to the LIVE print's layer.

        Updated every poll, even when that layer is already hydrated,
        so the window follows the print between hydrations — the anchor
        is never the REQUESTED layer (a prefetch would drift the window
        one layer ahead of the print).
        """
        if not isinstance(layer, int) or layer < 0 or self._view is None:
            return
        index = self._view._index
        with index.cache_lock:
            index.followed_layer = layer
        self._update_decoded_protection()
        # A moved anchor may have stranded a pending prefetch outside
        # the window; drop it before the worker picks it. The window is
        # then topped back up: an anchor move is exactly when the new
        # previous layer's ghost becomes worth reading. The FROZEN
        # follower's window survives beside it — a live poll must never
        # drop a seek's demand (the live report: the first forward drag
        # waited a minute for the pass to walk to it).
        self._hydrate = {n for n in self._hydrate
                         if layer - 1 <= n <= layer + 1
                         or (index.manual_anchor is not None
                             and index.manual_anchor - 1 <= n <= index.manual_anchor + 1)}
        self._request_window(layer)
        self._advance()

    def _prepared_read(self, layer):
        """The file-backed store's random-access read for one
        layer — the reopened print's path (no decode of preceding
        layers, no RAM residency)."""
        if self._prepared is None or self._prepared_table is None:
            return None
        return self._prepared.read(self._prepared_identity, self._prepared_table, layer)

    def _prepared_served(self, layer):
        """The prepared store's table says this layer's payload is
        READABLE without the raw G-code file — a table-tuple probe,
        never a payload read (the lease decision must not cost a
        disk read on the owner thread)."""
        if self._prepared is None or self._prepared_table is None:
            return False
        if layer < 0 or layer >= len(self._prepared_table):
            return False
        state, _offset, length = self._prepared_table[layer]
        return state == STATE_CACHED and length > 0

    def _prepared_open(self, identity):
        """Open the table for the current file's prepared cache (a
        one-shot per identity). The adoption obeys the SAME strength
        gate as the index restore (the review's identity-policy
        finding): only a RELIABLE modified timestamp may reuse
        persisted geometry across sessions — a weak identity (name +
        size alone, whatever its uuid) never adopts an old prepared
        table, so a re-extracted file can never resurrect stale
        geometry. The key still targets the same path: the fresh
        pass's publish replaces the old representation."""
        if self._prepared is None or identity is None:
            return
        key = identity.stable_key() if hasattr(identity, "stable_key") else None
        if key is None or key == self._prepared_identity:
            return
        self._prepared_identity = key
        # The strength gate — the same decision the index restore
        # makes: modified > 0 is the only voucher for cross-session
        # reuse. A uuid is Moonraker's per-extraction token, never
        # content identity, and a name + size alone cannot tell two
        # extractions apart — prefer rebuilding over stale geometry.
        if not bool(getattr(identity, "modified", 0) > 0):
            self._prepared_table = None
            self._prepared_flag_complete = False
            self._prepared_coverage = set()
            return
        loaded = self._prepared.load_table(key)
        if loaded is None:
            self._prepared_table = None
            self._prepared_flag_complete = False
            self._prepared_coverage = set()
            return
        self._prepared_table = loaded["table"]
        self._prepared_flag_complete = loaded["complete"]
        # The resolved entries (CACHED and UNCACHEABLE alike) count
        # as coverage BEFORE the pass walks: a repair session starts
        # at the old file's fraction. The coverage truth — a layer
        # the codec refused is as resolved as one it held.
        self._prepared_coverage = {i for i, entry in enumerate(loaded["table"])
                                   if entry[0] != STATE_EMPTY}

    def _adopt_prepared(self):
        """The reopen policy once the view's layer count is known
        : a complete clean table
        takes the FAST path — the pass never walks the file again;
        a complete table with holes repairs (copy the valid, retry
        the holes); anything else prepares fresh."""
        table = self._prepared_table
        self._prepared_complete = False
        if not table or self._prepared is None:
            return
        total = len(self._view.ranges)
        if len(table) == total and self._prepared_flag_complete \
                and all(entry[0] != STATE_EMPTY for entry in table):
            # A valid complete cache must not read its own 197 MB
            # back merely to rediscover the table: the frontier and the saved latch both
            # stand down the background pass for good. UNCACHEABLE
            # layers count as complete — the pass already gave them
            # its best attempt.
            self._prepared_complete = True
            self._prepared_saved = True
            self._full_next = total
            Logger.log("i", "prepared store restored: %d/%d layers", total, total)
            return
        if len(table) != total:
            # The file's layer count no longer matches this print's
            # index: the table is unusable, and the fresh pass will
            # overwrite the file.
            self._prepared_table = None
            self._prepared_coverage = set()
            return
        covered = sum(1 for entry in table if entry[0] != STATE_EMPTY)
        Logger.log("i", "prepared store resumed: %d/%d layers", covered, total)

    def _prepared_persist(self, layer, encoded):
        """Every successfully encoded layer enters the incremental
        writer exactly once, whichever path produced it: a
        demand-prepared layer must never
        become a (0, 0) hole merely because the background pass
        found it already in the RAM cache."""
        self._prepared_coverage.add(layer)
        if self._prepared is None or self._prepared_identity is None or self._prepared_saved:
            return
        writer = self._prepared_writer
        if writer is None and self._view is not None:
            writer = self._prepared.open_for_write(
                self._prepared_identity, len(self._view.ranges))
            self._prepared_writer = writer
        if writer is not None:
            self._prepared.append(writer, layer, encoded)

    def _abort_prepared_writer(self, store=None):
        """Abandon an unfinished writer on every exit path: rebind,
        close and any abandonment —
        the temp file goes, the handle closes, and an old
        generation's worker can never finalise it."""
        if self._prepared_writer is None:
            return
        store = store if store is not None else self._prepared
        if store is not None:
            store.abort_write(self._prepared_writer)
        self._prepared_writer = None

    def _suspend_prepared_writer(self, store=None):
        """The normal-lifecycle checkpoint (the review's clean-shutdown
        finding): a close or a store rebind publishes the writer's
        committed layers as an INCOMPLETE store — the next session
        opens it and resumes from the EMPTY slots. Only a genuinely
        failed or stale writer is aborted. The store is the writer's
        OWN store — during a rebind the caller passes the OLD one
        explicitly, so the checkpoint lands in the machine whose
        pass produced it."""
        if self._prepared_writer is None:
            return
        store = store if store is not None else self._prepared
        if store is not None:
            covered = sum(1 for entry in self._prepared_writer["table"]
                          if entry is not None)
            store.suspend_write(self._prepared_writer)
            Logger.log("i", "prepared checkpoint published on shutdown: %d layers",
                       covered)
        self._prepared_writer = None

    def _retire_prepared_writer(self, store=None):
        """The writer-ownership cutover (the review's ownership
        finding): bind, close and the machine rebind all RETIRE
        before they checkpoint. The retired flag freezes every later
        append and finish, and the store's own lock makes the freeze
        and the publication one atomic step against an in-flight
        append — once this returns no worker can ever write to the
        writer again, and the checkpoint holds exactly the committed
        layers. No blocking beyond one bounded append: the suspend
        only ever waits for a write already in progress."""
        if self._prepared_writer is None:
            return
        self._prepared_writer["retired"] = True
        self._suspend_prepared_writer(store)

    def rebind_stores(self, cache, prepared, initial=False):
        """The machine-switch rebind (the review's namespace finding):
        the index and prepared stores swap while THIS service survives.
        The cutover is ordered (the review's ownership finding):
        retire/cancel the old generation, freeze its writer, then
        checkpoint it through the OLD machine's own store — all
        BEFORE the new stores install — so an A writer can never
        publish into B's namespace and a stale worker can never touch
        the retired writer again. The print-specific state resets.
        The INITIAL bind at construction only installs the stores —
        the state is already fresh."""
        if initial:
            self._cache = cache
            self._prepared = prepared
            return
        old_prepared = self._prepared
        self._generation += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self._job = None
        self._view = None
        self._progress = None
        self._retire_prepared_writer(old_prepared)
        self._cache = cache
        self._prepared = prepared
        self._reset_print_state()
        self.changed.emit()

    def _reset_print_state(self):
        """The print-specific state a new identity (or a new machine)
        renders meaningless: the memoised windows, the RAM tiers, the
        prepared table and the scrub records all belong to the file
        that was being served."""
        self._plate_layers_memos = {}
        self._full_cache = _ByteBoundedLru(_FULL_CACHE_MAX_BYTES)
        self._full_next = 0
        self._decoded_lru = _ByteBoundedLru(_DECODED_LRU_MAX_BYTES, _DECODED_LRU_MIN_ENTRIES)
        self._decoded_pins = {}
        self._decoded_sizes = {}
        self._prepared_table = None
        self._prepared_identity = None
        self._prepared_saved = False
        self._prepared_retry_count = 0
        self._prepared_complete = False
        self._prepared_flag_complete = False
        self._prepared_coverage = set()
        self._manual_anchor = None
        self._manual_anchor_calls = 0
        self._manual_anchor_changes = 0
        self._split_floor = None
        self._split_refined = None
        self._split_floor_key = None
        self._visited_key = None
        self._visited = set()
        self._visited_upto = -1
        self._visited_settled = frozenset()
        self._wanted = self._restored = self._save = False
        self._hydrate.clear()
        self._hydrating = None
        self._failed_hydrate.clear()
        self._error = ""

    def _request_window(self, layer):
        """Ask for the live anchor's presentation window, never a backlog.

        A hydrated index layer is still demanded when its decoded
        presentation payload is absent. Readiness means DECODED HOT;
        hydration merely selects the cheapest worker source.
        """
        view = self._view
        anchor = view._index.followed_layer
        for candidate in (layer - 1, layer, layer + 1):
            if not 0 <= candidate < len(view.ranges):
                continue
            if anchor is not None and not anchor - 1 <= candidate <= anchor + 1:
                continue
            if self._presentation_source(candidate) not in {"decoded", "failed"}:
                self._hydrate.add(candidate)
                self._foreground_pending.set()

    def _on_files_changed(self):
        # A new file (or a re-downloaded one) invalidates failed hydration
        # attempts: the bytes the latch was based on no longer exist.
        self._failed_hydrate.clear()
        self._advance()

    def _advance(self):
        if self._closed or self._busy or not self._job or not self._wanted or self._error:
            return
        if self._files.job_key != self._job: return
        identity = self._files.identity
        if identity is None:
            self._files.request_metadata()
            return
        # The restore's strength gate (the review's identity policy):
        # the RELIABLE modified timestamp is what makes a disk identity
        # strong enough to restore. The uuid is Moonraker's
        # per-extraction token — it must never be what vouches for a
        # restore, because the lookup and the validation both ignore
        # it. A name + size alone (no timestamp) is the weak case: the
        # content may have changed between extractions, so the restore
        # is skipped and the file rebuilds — the safe behaviour.
        strong = bool(getattr(identity, "modified", 0) > 0)
        if not self._restored and strong:
            self._restored = True
            self._submit("restore", lambda: self._cache.load(identity))
            return
        self._restored = True
        self._prepared_open(identity)
        if self._view is None:
            lease = self._files.lease()
            if lease is None:
                self._files.request_file()
                return
            cancel = self._cancel
            self._progress = 0.0
            self._submit("build", lambda: build_index_from_file(
                lease.path, cancel,
                progress=lambda fraction: setattr(self, "_progress", fraction)), lease)
            return
        index = self._view._index
        # An index built after the detach (or rebuilt) takes the frozen
        # anchor here, where the live window is applied each poll.
        self._apply_manual_anchor()
        # Two windows stand: the live print's own and the frozen
        # follower's (the pop-over's detach) — neither may drop the
        # other's demand. Each keeps what it needs: the LIVE window
        # hydrates for the physical refinement's arrays, the MANUAL
        # window fills the hot presentation cache (a decoded layer is
        # served — no rehydrate merely to display prepared geometry).
        manual = index.manual_anchor
        self._hydrate = {n for n in self._hydrate
                         if self._presentation_source(n) not in {"invalid", "decoded", "failed"}
                         and ((index.followed_layer is None
                               or index.followed_layer - 1 <= n <= index.followed_layer + 1)
                              or (manual is not None
                                  and manual - 1 <= n <= manual + 1))}
        if self._hydrate:
            # CURRENT is a foreground presentation demand. Detached/manual
            # current outranks live current; live current outranks every
            # ghost. Each current rides its own task and can publish as
            # soon as it is ready.
            window = sorted(self._hydrate)
            submitted = []
            for anchor in (manual, index.followed_layer):
                if anchor is not None and anchor in self._hydrate:
                    submitted = [anchor]
                    self._hydrate.remove(anchor)
                    break
            if not submitted:
                submitted = window
                self._hydrate.clear()

            # Only RAW source needs the G-code lease. Packed RAM,
            # prepared disk and already-hydrated index arrays are all
            # independently sufficient presentation sources.
            needs_raw = any(self._presentation_source(layer) == "raw"
                            for layer in submitted)
            lease = self._files.lease() if needs_raw else None
            if needs_raw and lease is None:
                self._hydrate.update(submitted)
                self._files.request_file()
                return
            self._hydrating = set(submitted)
            cache = self._full_cache
            # No anchor argument: the worker reads the index's
            # followed_layer at COMPLETION, so a worker that finishes
            # after an anchor change applies the latest policy.
            # The WHOLE demanded window rides ONE task: a seek's three
            # layers arrive together instead of through three chained
            # round-trips. The worker OWNS NOTHING: it returns (failed, stash) and the
            # generation-checked _finish commits — a stale old-job
            # worker can never touch the new job's stores. A cached
            # layer decodes straight off the compact store instead of
            # re-reading the file and re-walking the geometry.
            def hydrate_and_prepare():
                failed = []
                stash = {}
                for layer in submitted:
                    raw = cache.peek(layer)  # peek: the worker never reorders
                    ram_hit = raw is not None
                    if raw is None:
                        raw = self._prepared_read(layer)
                    if raw is not None:
                        try:
                            decoded = _decode_layer(raw)
                            stash[layer] = (raw, decoded, ram_hit,
                                            _decoded_charge(raw=raw, payload=decoded))
                        except Exception:
                            failed.append(layer)
                        continue
                    # Hydrated arrays are a complete source in their own
                    # right. Non-compact indexes always take this branch;
                    # compact indexes take it while the retention window
                    # still holds the layer. No raw lease is needed.
                    hydrated = not index.compact or layer in index.hydrated_layers
                    if not hydrated:
                        if lease is None:
                            failed.append(layer)
                            continue
                        result = hydrate_layer_from_file(index, lease.path, layer)
                        if not result:
                            failed.append(layer)
                            continue
                    payload = _prepare_layer(index, layer)
                    if payload is None:
                        # A hydrate that succeeded but prepared nothing
                        # is not a hydrate failure: it must not latch
                        # (the latch exists to stop whole-file re-reads,
                        # and a re-ask here costs neither).
                        continue
                    encoded = None
                    # An encode failure must never cost the layer its
                    # display — the decoded payload still lands in the
                    # hot cache (the compact store just misses it).
                    try:
                        encoded = _encode_layer(payload)
                    except Exception:
                        pass
                    stash[layer] = (encoded, payload, False,
                                    _decoded_charge(raw=encoded, payload=payload))
                return failed, stash
            self._submit("hydrate", hydrate_and_prepare, lease)
        elif self._save and strong \
                and (self._last_save_at is None or time.monotonic() - self._last_save_at >= 30.0):
            # The index cache save is a one-shot and must not wait for
            # the pass to walk the whole file. It is ALSO throttled:
            # a save per successful hydrate (every live layer change)
            # interleaved a serialise between the pass's batches and
            # starved the whole walk during a print (the live report —
            # far layers never sped up).
            self._save = False
            self._last_save_at = time.monotonic()
            index = self._view._index
            # The store is captured NOW, like the prepared workers: a
            # rebind swaps self._cache before the worker runs, and
            # this save belongs to the machine it was built for.
            cache_store = self._cache
            self._submit("save", lambda: cache_store.save(identity, index))
        elif self._view is not None and self._full_next >= len(self._view.ranges) \
                and self._prepared is not None and not self._prepared_saved:
            # The pass's completion finishes the incremental writer.
            # The latch rides the COMMIT: a failed publish never
            # reads as saved. A failed attempt self-heals — the next
            # demand's persist opens a fresh writer and this branch
            # retries the finish.
            writer = self._prepared_writer
            self._prepared_writer = None
            if writer is not None:
                # The store is captured NOW: a machine rebind swaps
                # self._prepared before the worker runs, and this
                # writer's finalise must publish through the store
                # that opened it (its own machine's namespace).
                store = self._prepared
                def prepared_save():
                    return store.finish_write(writer)
                self._submit("prepared_save", prepared_save)
        elif self._view is not None and self._full_next < len(self._view.ranges):
            # The full prepared cache's background pass (the live
            # request): one bounded batch per worker task, so the
            # demanded hydrates above always cut in. Every layer ends
            # up in the compact store. The frontier is the worker's
            # LOCAL state and its return value — never a live
            # mutation of the service  — and the freshly hydrated layer's own window
            # survives the retention until its prepare and encode
            # complete .
            index = self._view._index
            cache = self._full_cache
            start = self._full_next
            # A non-compact index (or a compact suffix already retained/
            # prepared) can rebuild the prepared store without touching
            # the raw G-code. Ask for a lease only when some remaining
            # layer genuinely has no other source.
            needs_raw = False
            if index.compact:
                for layer in range(start, len(index.ranges)):
                    if layer in index.hydrated_layers or cache.peek(layer) is not None \
                            or self._prepared_served(layer):
                        continue
                    if self._prepared_table is not None and layer < len(self._prepared_table) \
                            and self._prepared_table[layer][0] == STATE_UNCACHEABLE:
                        continue
                    needs_raw = True
                    break
            lease = self._files.lease() if needs_raw else None
            if needs_raw and lease is None:
                self._files.request_file()
                return
            # The batch's slice: short enough that a demanded hydrate
            # never queues long behind the pass, and the loop YIELDS
            # the moment a demand appears (the worker checks the
            # demand set between layers — the owner fills it).
            deadline = time.monotonic() + 0.12
            prepared_read = self._prepared_read
            prepared_table = self._prepared_table
            # The incremental writer opens whenever a pass must walk
            # (a fresh file, or a repair):
            # the fast path's `_prepared_saved` latch has already
            # stood it down for a complete clean table.
            if self._prepared is not None and self._prepared_identity is not None \
                    and self._prepared_writer is None \
                    and not self._prepared_saved:
                self._prepared_writer = self._prepared.open_for_write(
                    self._prepared_identity, len(self._view.ranges))
            prepared_writer = self._prepared_writer
            # The store the writer belongs to is captured NOW: a
            # machine rebind swaps self._prepared while the batch
            # runs, and the appends must reach the store that opened
            # the writer (which refuses them after the cutover's
            # retirement anyway — the capture makes the ownership
            # structural, never a thread-timing accident).
            prepared_store = self._prepared

            # No demand exists on the owner thread at this instant.
            # Any later request flips the event and cooperatively
            # interrupts the in-progress dense layer too, not merely
            # the gap between layers.
            self._foreground_pending.clear()

            def full_prep_batch():
                encoded = {}
                uncacheable = set()
                frontier = start
                while time.monotonic() < deadline:
                    # The loop-top yield reads the thread-safe EVENT,
                    # never the mutable hydrate set across the thread
                    # boundary — the owner records every demand in
                    # both, but only the event is the worker's signal.
                    if self._foreground_pending.is_set():
                        break  # a demand arrived — it outranks the pass
                    layer = frontier
                    if layer >= len(index.ranges):
                        break
                    if layer in self._failed_hydrate:
                        # The latch applies to the pass too: a refused
                        # layer must not retry every poll (the same
                        # whole-file re-read the demand path avoids).
                        # Its slot stays EMPTY: the next session
                        # retries it.
                        frontier = layer + 1
                        continue
                    packed = cache.peek(layer)
                    if packed is not None:
                        # A demand prepared this layer before the pass
                        # reached it: the writer receives the bytes
                        # HERE, so the pass's finish can never publish
                        # a hole for a layer that WAS prepared.
                        if prepared_writer is not None:
                            prepared_store.append(prepared_writer, layer, packed)
                        frontier = layer + 1
                        continue
                    if prepared_table is not None and layer < len(prepared_table) \
                            and prepared_table[layer][0] == STATE_UNCACHEABLE:
                        # The repair copy: an UNCACHEABLE layer rides
                        # into the new writer WITHOUT a re-walk — the
                        # codec's refusal stands across sessions.
                        uncacheable.add(layer)
                        frontier = layer + 1
                        continue
                    raw = prepared_read(layer) if prepared_table else None
                    if raw is not None:
                        # The repair copy :
                        # the old file's valid layer rides into the
                        # new writer — the rebuild never loses an
                        # entry while regenerating another. The bytes
                        # are read anyway for the table walk; the
                        # copy costs a write, not a decode.
                        if prepared_writer is not None:
                            prepared_store.append(prepared_writer, layer, raw)
                        frontier = layer + 1
                        continue
                    if index.compact and layer not in index.hydrated_layers:
                        if lease is None or not hydrate_layer_from_file(index, lease.path, layer):
                            break
                    try:
                        payload = _prepare_layer(
                            index, layer, self._foreground_pending.is_set)
                    except PreparationYield:
                        # Do not advance the frontier: this layer was
                        # deliberately abandoned before memo/publication.
                        # _finish() will immediately run the foreground
                        # demand, then resume this layer later.
                        break
                    if payload is not None:
                        try:
                            packed = _encode_layer(payload)
                            encoded[layer] = packed
                            if prepared_writer is not None:
                                prepared_store.append(prepared_writer, layer, packed)
                        except Exception:
                            # A layer the codec cannot hold simply
                            # stays out of the cache; the pass must
                            # walk on, never stall. The explicit
                            # UNCACHEABLE state records the refusal —
                            # the next session never retries it.
                            uncacheable.add(layer)
                    frontier = layer + 1
                return frontier, encoded, uncacheable

            self._submit("fullprep", full_prep_batch, lease)

    def _submit(self, kind, work, lease=None):
        generation = self._generation
        self._busy = kind
        self._busy_generation = generation
        future = self._executor.submit(work)
        def done(result):
            try: value, error = result.result(), None
            except Exception as exc: value, error = None, str(exc)
            try:
                self._completed.emit(generation, kind, value, error, lease)
            except RuntimeError:
                if lease is not None: lease.close()  # Qt owner destroyed at shutdown.
        future.add_done_callback(done)
        self.changed.emit()

    def _finish(self, generation, kind, value, error, lease):
        if lease is not None: lease.close()
        # The busy flag clears ONLY for the generation that owns it —
        # a stale worker's terminal (an old build completing after a
        # bind/invalidate) must never misrepresent a newer task's
        # state and let _advance pile a second job on the queue.
        if generation == self._busy_generation:
            self._busy = ""
        if self._closed: return
        if generation == self._generation:
            if kind in {"build", "restore"}:
                if isinstance(value, LayerMotionIndex) and value:
                    self._view = IndexView(self._job, value)
                    self._save = kind == "build"
                    self._failed_hydrate.clear()
                    # The restore path returns BEFORE _advance's open
                    # (the submission is its last step): open the
                    # table HERE so the adoption right below sees it —
                    # a reopened complete store must take the fast
                    # path, never wait for a later _advance that never
                    # re-adopts.
                    self._prepared_open(self._files.identity)
                    self._adopt_prepared()
                    # A detach or a scrub that raced the build/restore
                    # dropped its demand at the view-None guards; the
                    # coordinator's post-commit re-assertion carries
                    # the SAME anchor and the idempotency guard would
                    # swallow it — so the windows re-raise HERE, the
                    # moment the view exists (the live report: a
                    # future-layer scrub after a restore rendered
                    # nothing and the slider stayed disabled).
                    self._apply_manual_anchor()
                    self._request_manual_window()
                    index = self._view._index
                    if index.followed_layer is not None:
                        self._request_window(int(index.followed_layer))
                elif kind == "build":
                    self._error = error or "Remote G-code contains no supported layer markers"
                    self.failed.emit(self._error)
            elif kind == "hydrate":
                # The batch returns (failed, stash): the layers it could
                # not hydrate and the (encoded, decoded) payloads the
                # worker prepared. A task exception returns None: latch
                # the whole window. The commit runs HERE, under the
                # generation the worker was submitted for — a stale
                # worker's results never touch the new job's stores
                # . A failed hydration
                # must not be re-attempted on every poll — each attempt
                # re-reads the whole file. The latch clears when a new
                # file arrives or the index is rebuilt.
                window = self._hydrating
                self._hydrating = None
                if isinstance(window, (set, list, tuple)):
                    window_layers = list(window)
                elif window:
                    window_layers = [window]
                else:
                    window_layers = []
                if isinstance(value, tuple) and len(value) == 2:
                    failed, stash = value
                else:
                    failed = [] if value else window_layers
                    stash = {}
                for layer, (encoded, decoded, ram_hit, size) in stash.items():
                    if encoded is not None:
                        self._full_cache.set(layer, encoded, len(encoded))
                        # The demand's encoding persists NOW — the pass may walk
                        # past it or find it cached later; the writer
                        # must hold it either way.
                        self._prepared_persist(layer, encoded)
                    self._decoded_lru.set(layer, decoded, size)
                    self._decoded_sizes[layer] = size
                    if ram_hit:
                        # A RAM-cache hit refreshes the packed tier's
                        # recency (the worker only peeked).
                        self._full_cache.touch(layer)
                # The pins may hold evicted layers: the combined
                # bound yields the LRU to them after every commit.
                self._reconcile_decoded()
                if len(failed) < len(window_layers) or (bool(value) and not window_layers):
                    self._save = True
                self._failed_hydrate.update(failed)
            elif kind == "fullprep":
                # (frontier, encoded, uncacheable): the worker's
                # LOCAL results — committed here, never mutated
                # across the thread boundary. The
                # frontier never regresses; a mid-flight seek rewind
                # is superseded by the demand, which owns the sought
                # window now.
                if isinstance(value, tuple) and len(value) == 3:
                    frontier, encoded, uncacheable = value
                    if isinstance(encoded, dict):
                        self._full_cache.update(encoded)
                        # The coverage follows the SAME events the
                        # writer appended :
                        # the fraction is a store census, never the
                        # RAM tier's residency.
                        self._prepared_coverage.update(encoded.keys())
                    if isinstance(uncacheable, (set, list, tuple)):
                        # The codec's refusals resolve too: coverage
                        # counts them, and the writer records the
                        # state so the next session never retries.
                        self._prepared_coverage.update(uncacheable)
                        writer = self._prepared_writer
                        if writer is not None:
                            for layer in uncacheable:
                                self._prepared.append_uncacheable(writer, layer)
                    if isinstance(frontier, int):
                        self._full_next = max(self._full_next, frontier)
            elif kind == "prepared_save":
                # The published file replaces the table this session
                # holds: a fresh/repair pass's offsets differ from
                # the old file's, and a stale table would serve
                # mis-aligned reads for the rest of the session.
                # The saved latch closes ONLY on the publish itself.
                if value is not None and self._prepared_identity is not None:
                    self._prepared_saved = True
                    self._prepared_retry_count = 0
                    loaded = self._prepared.load_table(self._prepared_identity)
                    if loaded is not None:
                        self._prepared_table = loaded["table"]
                        self._prepared_flag_complete = loaded["complete"]
                        self._prepared_coverage = {
                            i for i, entry in enumerate(loaded["table"])
                            if entry[0] != STATE_EMPTY}
                        self._prepared_complete = (
                            loaded["complete"]
                            and all(entry[0] != STATE_EMPTY for entry in loaded["table"]))
                elif self._view is not None and self._prepared_retry_count < 1:
                    # finish_write detached/closed the failed writer.
                    # Rebuild once from the already-available prepared
                    # sources without waiting for another user demand.
                    self._prepared_retry_count += 1
                    self._prepared_saved = False
                    self._prepared_complete = False
                    self._full_next = 0
                    table = self._prepared_table or ()
                    self._prepared_coverage = {
                        i for i, entry in enumerate(table)
                        if entry[0] != STATE_EMPTY}
            self.changed.emit()
        self._advance()

    def close(self):
        if self._closed: return
        self._closed = True
        self._generation += 1
        self._cancel.set()
        # The normal shutdown RETIRES then CHECKPOINTS the unfinished
        # writer (the review's clean-shutdown and ownership findings):
        # the freeze precedes the publication under the store's lock,
        # so an in-flight pass worker can never write to the writer
        # again, and the committed layers publish as an incomplete
        # store the next session resumes — a plain abort would throw
        # away a partly-prepared print.
        self._retire_prepared_writer()
        self._executor.shutdown(wait=False, cancel_futures=True)
