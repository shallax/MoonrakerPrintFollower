from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal

from .GCodeIndex import LayerMotionIndex, build_index_from_file, hydrate_layer_from_file
from .MonitorFormatting import _segment_in_polygon, polygon_bounds
from .PlateProgress import (
    decode_layer as _decode_layer,
    encode_layer as _encode_layer,
    motion_edges as _motion_edges,
    prepare_layer as _prepare_layer,
    split_index as _split_index,
)


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
        # The file-backed prepared store (the review's round 3): the
        # pass's encodings persist per print, random-accessible, so
        # a reopened print skips the whole preparation walk.
        self._prepared = prepared
        self._prepared_table = None
        self._prepared_identity = None
        self._prepared_saved = False
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MoonrakerIndex")
        self._generation = 0
        self._job = None
        self._view = None
        self._cancel = threading.Event()
        self._busy = ""
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
        self._full_cache = {}
        self._full_next = 0
        # The index-cache save's throttle: a save every hydrate starved
        # the background pass during a live print (the save branch runs
        # before the pass branch — the "no quicker" live report).
        self._last_save_at = None
        # The hot presentation cache (the review's design): DECODED
        # payloads, keyed by layer, access-order bounded. The bundle
        # reads it first and reuses the same Python object, so an
        # adjacent seek shares two of its three layers verbatim (no
        # re-decode, no QVariant re-conversion) and the worker hands
        # the first display its own payload instead of a second
        # object decoded from the compact store. Six layers = the
        # live and frozen windows side by side; a dense decoded layer
        # is tens of MB, so the bound is the memory budget.
        self._decoded_lru = OrderedDict()
        self._decoded_lru_capacity = 6
        # The follower's frozen layer (the pop-over's detach): a second
        # demand window beside the live print's own.
        self._manual_anchor = None
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
        # The memoised layers belong to the previous index; a new
        # print could coincidentally match the (anchor, counts) key.
        self._plate_layers_memos = {}
        # The full cache belongs to the file that was printing too.
        self._full_cache = {}
        self._full_next = 0
        # The hot presentation cache belongs to that file as well.
        self._decoded_lru = OrderedDict()
        # The prepared store's table follows the same identity.
        self._prepared_table = None
        self._prepared_identity = None
        self._prepared_saved = False
        # The frozen layer belongs to the file that was printing.
        self._manual_anchor = None
        # The painted boundary belongs to that file's layer too: a new
        # print's count is not this print's.
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
        # Keep _busy until the submitted worker actually completes. No new task
        # is submitted while a stale job is still executing.
        self.changed.emit()

    def request(self):
        self._wanted = True
        self._advance()

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
        self._manual_anchor = layer if isinstance(layer, int) and not isinstance(layer, bool) \
            and layer >= 0 else None
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

    def _request_manual_window(self):
        view = self._view
        if view is None or self._manual_anchor is None:
            return
        for candidate in (self._manual_anchor - 1, self._manual_anchor, self._manual_anchor + 1):
            # Demanded until the hot cache holds it — the bundle reads
            # the decoded store and the worker hands it over, so a
            # cached-and-decoded layer needs NO rehydration merely to
            # display (the review's finding): the worker's hydrate is
            # the compact store's own no-op when the arrays are still
            # present, and the manual scrub's split is used as-given.
            # The criterion covers the non-compact case too, where
            # nothing is ever unhydrated but the decoded store can
            # still miss — a cold seek must land via the worker,
            # never through a UI-thread prepare.
            if 0 <= candidate < len(view.ranges) \
                    and candidate not in self._decoded_lru \
                    and candidate not in self._failed_hydrate:
                self._hydrate.add(candidate)

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
        (no re-decode, no QVariant re-conversion — the review's warm-
        seek finding), and the worker hands the first display its own
        payload instead of a second object decoded from the compact
        store. A miss reads as not loaded — the UI thread NEVER walks
        geometry (the review's cold-seek finding); the demand owns it.
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
        # (the review's lock finding): the hot cache's reads are the
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
        layers the prepared stores hold — the persistent table's
        coverage AND the RAM cache's, so a reopened print reads
        100% without any RAM residency (the review's finding 8)."""
        if self._view is None:
            return None
        total = len(self._view.ranges)
        if not total:
            return None
        if self._prepared_table is not None and len(self._prepared_table) == total \
                and all(entry[1] > 0 for entry in self._prepared_table):
            return 1.0
        return min(1.0, len(self._full_cache) / total)

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
                # comparisons, before any vertex is touched.
                if right < bounds[0] or left > bounds[2] or top < bounds[1] or bottom > bounds[3]:
                    continue
                if _segment_in_polygon(x0, y0, x1, y1, polygon):
                    self._visited.add(name)
                    break

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

    def set_followed_layer(self, layer):
        """Anchor the retention window to the LIVE print's layer.

        Updated every poll, even when that layer is already hydrated,
        so the window follows the print between hydrations — the anchor
        is never the REQUESTED layer (a prefetch would drift the window
        one layer ahead of the print, the review repro).
        """
        if not isinstance(layer, int) or layer < 0 or self._view is None:
            return
        index = self._view._index
        with index.cache_lock:
            index.followed_layer = layer
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

    def _prepared_open(self, identity):
        """Open the table for the current file's prepared cache (a
        one-shot per identity)."""
        if self._prepared is None or identity is None:
            return
        key = identity.stable_key() if hasattr(identity, "stable_key") else None
        if key is None or key == self._prepared_identity:
            return
        self._prepared_identity = key
        self._prepared_table = self._prepared.load_table(key) or []

    def _request_window(self, layer):
        """Ask for the anchor's own three layers, never a backlog.

        The face reads the previous layer, the current one and the
        look-ahead, and each is useful only around the live layer — so
        the demand is a WINDOW, and asking for one already-hydrated
        layer must not stand the others down. Any layer the latch has
        given up on is left out; ``_advance`` would drop it anyway.
        """
        view = self._view
        anchor = view._index.followed_layer
        for candidate in (layer - 1, layer, layer + 1):
            if not 0 <= candidate < len(view.ranges) or view.hydrated(candidate):
                continue
            if anchor is not None and not anchor - 1 <= candidate <= anchor + 1:
                continue
            if candidate not in self._failed_hydrate:
                self._hydrate.add(candidate)

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
        strong = bool(identity.uuid or identity.modified > 0)
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
        # served — no rehydrate merely to display prepared geometry,
        # the review's finding).
        manual = index.manual_anchor
        self._hydrate = {n for n in self._hydrate if n < len(self._view.ranges)
                         and n not in self._failed_hydrate
                         and (((index.followed_layer is None
                                or index.followed_layer - 1 <= n <= index.followed_layer + 1)
                               and not self._view.hydrated(n))
                              or (manual is not None
                                  and manual - 1 <= n <= manual + 1
                                  and n not in self._decoded_lru))}
        if self._hydrate:
            lease = self._files.lease()
            if lease is None:
                self._files.request_file()
                return
            window = sorted(self._hydrate)
            priority = [n for n in window if n == self._manual_anchor]
            if len(priority) == 1:
                # A manual seek's anchor rides its own task: the
                # bundle's CURRENT lands as soon as the anchor is
                # decoded and the label clears, while the window's
                # edges follow in the NEXT task (the live report:
                # the label waited for the whole three-layer window).
                self._hydrate.difference_update(priority)
                submitted = priority
            else:
                self._hydrate.clear()
                submitted = window
            self._hydrating = set(submitted)
            cache = self._full_cache
            # No anchor argument: the worker reads the index's
            # followed_layer at COMPLETION, so a worker that finishes
            # after an anchor change applies the latest policy.
            # The WHOLE demanded window rides ONE task: a seek's three
            # layers arrive together instead of through three chained
            # round-trips. The worker OWNS NOTHING (the review's
            # ownership finding): it returns (failed, stash) and the
            # generation-checked _finish commits — a stale old-job
            # worker can never touch the new job's stores. A cached
            # layer decodes straight off the compact store instead of
            # re-reading the file and re-walking the geometry.
            def hydrate_and_prepare():
                failed = []
                stash = {}
                for layer in submitted:
                    raw = cache.get(layer)
                    if raw is None:
                        raw = self._prepared_read(layer)
                    if raw is not None:
                        try:
                            stash[layer] = (raw, _decode_layer(raw))
                        except Exception:
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
                    stash[layer] = (encoded, payload)
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
            self._submit("save", lambda: self._cache.save(identity, index))
        elif self._view is not None and self._full_next >= len(self._view.ranges) \
                and self._prepared is not None and not self._prepared_saved \
                and self._prepared_identity is not None:
            # The pass's completion persists the store: the whole
            # session's encodings finalise atomically on the worker.
            self._prepared_saved = True
            count = len(self._view.ranges)
            cache = self._full_cache
            def prepared_save():
                payloads = [cache.get(layer) or b"" for layer in range(count)]
                return self._prepared.finalise(self._prepared_identity, payloads)
            self._submit("prepared_save", prepared_save)
        elif self._view is not None and self._full_next < len(self._view.ranges):
            # The full prepared cache's background pass (the live
            # request): one bounded batch per worker task, so the
            # demanded hydrates above always cut in. Every layer ends
            # up in the compact store. The frontier is the worker's
            # LOCAL state and its return value — never a live
            # mutation of the service (the review's ownership
            # finding) — and the freshly hydrated layer's own window
            # survives the retention until its prepare and encode
            # complete (the review's incomplete-pass finding).
            lease = self._files.lease()
            if lease is None:
                self._files.request_file()
                return
            index = self._view._index
            cache = self._full_cache
            start = self._full_next
            deadline = time.monotonic() + 0.25
            prepared_read = self._prepared_read
            prepared_table = self._prepared_table

            def full_prep_batch():
                encoded = {}
                frontier = start
                while time.monotonic() < deadline:
                    layer = frontier
                    if layer >= len(index.ranges):
                        break
                    if layer in self._failed_hydrate:
                        # The latch applies to the pass too: a refused
                        # layer must not retry every poll (the same
                        # whole-file re-read the demand path avoids).
                        frontier = layer + 1
                        continue
                    if layer not in cache:
                        raw = prepared_read(layer) if prepared_table else None
                        if raw is not None:
                            # The reopened print's cached layer: the
                            # file IS the prepared truth — the pass
                            # advances WITHOUT replaying it into RAM
                            # (the review's finding 8: a 197 MB store
                            # must not become 197 MB of resident
                            # bytes).
                            frontier = layer + 1
                            continue
                        if index.compact and layer not in index.hydrated_layers:
                            if not hydrate_layer_from_file(index, lease.path, layer):
                                break
                        payload = _prepare_layer(index, layer)
                        if payload is not None:
                            try:
                                encoded[layer] = _encode_layer(payload)
                            except Exception:
                                # A layer the codec cannot hold simply
                                # stays out of the cache; the pass
                                # must walk on, never stall.
                                pass
                    frontier = layer + 1
                return frontier, encoded

            self._submit("fullprep", full_prep_batch, lease)

    def _submit(self, kind, work, lease=None):
        generation = self._generation
        self._busy = kind
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
        self._busy = ""
        if self._closed: return
        if generation == self._generation:
            if kind in {"build", "restore"}:
                if isinstance(value, LayerMotionIndex) and value:
                    self._view = IndexView(self._job, value)
                    self._save = kind == "build"
                    self._failed_hydrate.clear()
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
                # (the review's ownership finding). A failed hydration
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
                for layer, (encoded, decoded) in stash.items():
                    if encoded is not None:
                        self._full_cache.setdefault(layer, encoded)
                    self._decoded_lru[layer] = decoded
                    self._decoded_lru.move_to_end(layer)
                while len(self._decoded_lru) > self._decoded_lru_capacity:
                    self._decoded_lru.popitem(last=False)
                if len(failed) < len(window_layers) or (bool(value) and not window_layers):
                    self._save = True
                self._failed_hydrate.update(failed)
            elif kind == "fullprep":
                # (frontier, encoded): the worker's LOCAL results —
                # committed here, never mutated across the thread
                # boundary (the review's ownership finding). The
                # frontier never regresses; a mid-flight seek rewind
                # is superseded by the demand, which owns the sought
                # window now.
                if isinstance(value, tuple) and len(value) == 2:
                    frontier, encoded = value
                    if isinstance(encoded, dict):
                        self._full_cache.update(encoded)
                    if isinstance(frontier, int):
                        self._full_next = max(self._full_next, frontier)
            self.changed.emit()
        self._advance()

    def close(self):
        if self._closed: return
        self._closed = True
        self._generation += 1
        self._cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
