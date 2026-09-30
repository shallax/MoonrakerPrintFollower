"""Index lifecycle and scheduling. One in-flight job; observations and caches have explicit owners."""
from __future__ import annotations

from .IndexTasks import LayerHydrationTask, LayerPreparationTask, LayerArrayTask

from .ObjectVisitTracker import ObjectVisitTracker
from .MotionRefinement import refine_payload

from UM.Logger import Logger


from concurrent.futures import ThreadPoolExecutor
import threading
import time

from PyQt6.QtCore import QObject, pyqtSignal

from .MotionIndex import LayerMotionIndex
from .GCodeIndex import build_index_from_file
from .PlateProgress import (
    split_index as _split_index,
)
from .PlateSplitTracker import PlateSplitTracker
from .PreparedSession import PreparedSession
from ..Printing.PrintState import MotionProgress


from .IndexView import IndexView
from .LayerCache import _ByteBoundedLru, _DECODED_LRU_MAX_BYTES, _DECODED_LRU_MIN_ENTRIES, _FULL_CACHE_MAX_BYTES, _GPU_DECODED_LRU_MAX_BYTES

# The progress signal's floor. A pass runs a 120 ms batch back to back,
# so every batch boundary is a tick; the listeners only redraw a bar,
# and 4 Hz keeps it moving without making the signal the new cost.
_PROGRESS_MIN_INTERVAL = 0.25
# The background pass's slice, and the worker's OWN bound: it is
# measured from the execution, never from the submission, so a pool
# queue delay is not the batch's to spend.


class GCodeIndexService(QObject):
    """Own index lifecycle with at most ONE submitted worker job, no work queue.

    Requests coalesce into desired state; replacement waits asynchronously for
    the old worker. Cache restore/build/hydration/save all run off the UI thread.
    """
    changed = pyqtSignal()
    # The pass's own tick: a batch boundary moved the progress, not the
    # state the UI latches on. Separated from `changed` because a full
    # refresh is what `changed` costs a listener, and the pass runs a
    # batch every 120 ms.
    progress_changed = pyqtSignal()
    failed = pyqtSignal(str)
    _completed = pyqtSignal(int, str, object, object, object)

    # The printed-object walk's owner-thread budget. A dense layer's
    # mandatory replay (an attach part-way through, a late polygon)
    # spans hundreds of thousands of motion edges, so one poll walks
    # at most this much wall time and the tail is the next poll's
    # work. The check's granularity is what a single poll may overshoot
    # by, and it is also the floor below which a range is walked
    # outright rather than cut: below it the walk is unmeasurable.


    # The replay's guaranteed share of the budget: a late polygon may
    # never starve the live delta, whose verdict is the poll's own.


    def __init__(self, files, cache, parent=None, prepared=None):
        super().__init__(parent)
        self._files, self._cache = files, cache
        # The file-backed prepared store's SESSION: the adopted table,
        # the incremental writer and the coverage census of the file
        # being served. The encodings persist per print,
        # random-accessible, so a reopened print skips the whole
        # preparation walk.
        self._prepared = PreparedSession(prepared)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="MoonrakerIndex")
        self._generation = 0
        self._job = None
        self._view = None
        # The last state published as a change (None: nothing has been,
        # so the first boundary publishes) and the progress tick's own
        # clock — the throttle reads wall time, which a task boundary
        # never moves.
        self._notified_key = None
        self._progress_at = 0.0
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
        # The store a cache clear just swept: an old generation's
        # queued save must never recreate that directory.
        self._swept_store = None
        self._wanted = self._restored = self._save = False
        self._hydrate = set()
        # The motion-array debt: compact layers whose GEOMETRY a
        # lease-less demand already served but whose physical arrays
        # the index still lacks.
        self._hydrate_arrays = set()
        self._hydrating = None
        self._failed_hydrate = set()
        # The pass's failure latch, cleared wherever the hydration
        # latch clears (the bytes it was reading are gone). A batch the
        # worker could not run walks no layer, and the terminal chains
        # `_advance`, whose pass branch tests only the frontier — so an
        # unlatched failure resubmits the same batch forever.
        self._pass_error = ""
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
        self._gpu_consumers = set()
        self._gpu_prefetch_key = None
        self._gpu_prefetch_done = set()
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
        self._split_tracker = PlateSplitTracker()
        self._objects = ObjectVisitTracker()


        # The replay frontier: the motion index up to which every
        # polygon in `_visited_pending` has been judged, and the
        # geometry waiting on it (the bounded replay's resume point).


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

    def _state_key(self):
        """The state the UI latches on: the phase (an error is one) and
        the pass's own completion. These are what a preparation batch's
        boundary can move that a listener would render differently."""
        return (self.phase, self._prepared.saved, self._prepared.complete)

    def _publish_change(self):
        self._notified_key = self._state_key()
        self.changed.emit()

    def _notify_changed(self, kind):
        """A task boundary's publish. The preparation pass runs a batch
        every 120 ms and every batch used to be a `changed`, which cost
        each listener a full refresh — the coordinator's plate payload
        and printed-object walk among them — for one batch's progress.
        Only those batches are progress: every other kind (a hydration,
        a build, a restore, a save) is a demand or a data change a
        listener rebuilds on, and keeps the unthrottled signal."""
        if kind != "fullprep" or self._state_key() != self._notified_key:
            self._publish_change()
            return
        now = time.monotonic()
        if now - self._progress_at < _PROGRESS_MIN_INTERVAL:
            return
        self._progress_at = now
        self.progress_changed.emit()

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
        self._prepared.retire()
        self._reset_print_state()
        # Keep _busy until the submitted worker actually completes. No new task
        # is submitted while a stale job is still executing.
        self._publish_change()

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
        self._prepared.retire()
        # The sweep's marker: a queued save from an older generation
        # must never recreate this store's directory (see _save_index).
        self._swept_store = self._cache
        self._view = None
        self._job = None
        self._wanted = self._restored = self._save = False
        self._busy = ""
        self._busy_generation = self._generation
        self._error = ""
        self._progress = None
        self._hydrate.clear()
        self._hydrate_arrays.clear()
        self._hydrating = None
        self._failed_hydrate.clear()
        self._pass_error = ""
        self._prepared.reset()
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
        self._split_tracker.reset()
        self._objects.reset()


        self._publish_change()

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
            # changed -> refresh (the detached 114% burn). The DEMAND
            # still stands on its own: a window whose request was
            # dropped while the worker was busy is raised again, never
            # rewritten and never advanced (the re-read per poll
            # stays out: a refused layer is skipped by the demand).
            self._request_manual_window()
            return
        self._manual_anchor_changes = getattr(self, "_manual_anchor_changes", 0) + 1
        self._manual_anchor = normalised
        if self._manual_anchor is None:
            # Rejoining the live print abandons the scrub: the live
            # split is the print's own again.
            self._manual_split = None
        else:
            # An explicit seek is a FRESH attempt: a refusal from an
            # earlier attempt must not refuse the user's own re-ask
            # silently (the live report — the label stood on
            # "Loading layer…" with no way back to the layer). Only a
            # changed anchor clears it, never a poll.
            self._failed_hydrate.discard(self._manual_anchor)
            # A seek focuses the pass: the sought window prepares
            # before the pass resumes wherever it stood (the live
            # report — a far seek waited for the pass to walk the
            # whole file). A fully restored prepared store has no
            # pass to focus — the fast path's saved latch stands the
            # walk down, and rewinding the frontier would only start
            # a pointless re-read of the whole store (the review's
            # finding).
            if not self._prepared.saved:
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
        if self._prepared.served(layer):
            return "prepared"
        if view.hydrated(layer):
            return "hydrated"
        return "raw"

    def set_gpu_rendering(self, consumer, enabled):
        """GPU consumers share a larger decoded tier; software keeps its budget.

        Count consumers separately so closing a popover does not withdraw the
        mini map's allowance. The owner thread alone changes cache policy.
        """
        if enabled:
            self._gpu_consumers.add(consumer)
        else:
            self._gpu_consumers.discard(consumer)
        self._decoded_lru.max_bytes = (_GPU_DECODED_LRU_MAX_BYTES if self._gpu_consumers
                                       else _DECODED_LRU_MAX_BYTES)
        self._reconcile_decoded()
        if not self._gpu_consumers:
            self._gpu_prefetch_key = None
            self._gpu_prefetch_done.clear()
        self._advance()

    def _gpu_prefetch_layer(self, index):
        """One nearby prepared layer, after every visible demand is served.

        No raw file download or motion-array hydration is triggered by this
        speculation. Attempt each candidate once per pair of anchors, so
        eviction cannot turn a full cache into a continuous decode loop.
        """
        if not self._gpu_consumers:
            return None
        anchors = (index.manual_anchor, index.followed_layer)
        if anchors != self._gpu_prefetch_key:
            self._gpu_prefetch_key = anchors
            self._gpu_prefetch_done.clear()
        if self.decoded_resident_bytes() >= self._decoded_lru.max_bytes * .85:
            return None
        visible = {n + delta for n in anchors if n is not None for delta in (-1, 0, 1)}
        for distance in (2, 3, 4):
            for anchor in anchors:
                if anchor is None:
                    continue
                for candidate in (anchor + distance, anchor - distance):
                    if (candidate in visible or candidate in self._gpu_prefetch_done
                            or self._presentation_source(candidate)
                            not in {"packed", "prepared", "hydrated"}):
                        continue
                    self._gpu_prefetch_done.add(candidate)
                    return candidate
        return None

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
        persisted = self._prepared.fraction(total)
        if persisted is not None:
            return persisted
        # No persistence configured: the RAM tier's residency is
        # the only prepared store there is.
        return min(1.0, len(self._full_cache) / total)

    def plate_split(self, anchor, file_position=None, live_position=None, paused=False, extruding=None):
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
        return self.observe_motion(anchor, file_position, live_position,
                                   paused=paused, extruding=extruding).split

    def observe_motion(self, anchor, file_position, live_position=None, paused=False, extruding=None):
        """Accept one live observation independently of presentation readiness.

        The coordinator calls this once per frame and publishes the immutable
        result to both renderers. Manual plate scrubbing never enters here.
        """
        if self._view is None or not isinstance(anchor, int) \
                or not 0 <= anchor < len(self._view.ranges):
            return MotionProgress(anchor, None, 0, "unavailable")
        index = self._view._index
        with index.cache_lock:
            total = index.motion_count(anchor)
            split = self._observe_split(anchor, file_position, live_position, paused, extruding)
            partial = 0.0 if self._split_tracker.awaiting_layer_entry else index.partial_motion(anchor, split, live_position)
        return MotionProgress(anchor, split, total,
                              "motion index" if split is not None else "unavailable", partial)

    def _observe_split(self, anchor, file_position, live_position, paused, extruding):
        if file_position is None:
            return None
        memo = self._plate_layers_memos.get(anchor)
        index = self._view._index
        with index.cache_lock:
            coarse = _split_index(index, anchor, file_position)
            if coarse is None:
                return None
            # Resolve geometry in the index; the pure tracker is the ONLY
            # owner of accepted progress, continuity and overshoot evidence.
            tracker = self._split_tracker
            advanced = tracker.begin(self._view.job_key, anchor)
            floor = tracker.floor
            if paused and floor is not None:
                return tracker.accept(coarse, None, None, live_position is not None, paused=True)
            refined = None
            raw = None
            if live_position is not None:
                # On a monotonic spiral, Z itself orders the motions.
                # XY repeats around the vase and can match an earlier
                # seam, holding the first few percent at zero and
                # truncating the last few percent. Flat layers still
                # use the geometry search and its overshoot evidence.
                raw = self._view.spiral_z_split(anchor, live_position[2]) \
                    if extruding is True else None
                if raw is None:
                    # The index uses the previous boundary as a search
                    # anchor and returns an UNCLAMPED geometric match.
                    # The tracker needs behind-floor evidence to recover
                    # an erroneously advanced repeated path.
                    raw, _method = index.refined_split(
                        anchor, file_position, live_position,
                        minimum_split=None, floor_split=floor,
                        stall=tracker.stall_polls)
                refined = raw if floor is None or raw is None \
                    else max(raw, floor)
                if refined is None:
                    offsets = index.motion_offsets[anchor] \
                        if anchor < len(index.motion_offsets) else ()
                    if not len(offsets):
                        # Compact layers search the prepared payload's geometry;
                        # they use the same boundary policy as hydrated layers.
                        refined = refine_payload(
                            memo[1].get("current"), coarse, live_position,
                            floor, ahead=tracker.payload_ahead_window,
                            stall=tracker.stall_polls) if memo is not None else None
                        raw = refined
                        tracker.observe_payload_advance(refined)
            spiral = self._view.continuous_z_at(anchor)
            entry_confirmed = extruding is not False and index.layer_entry_confirmed(
                anchor, raw, live_position, previous_z=tracker.entry_previous_z,
                continuous_z=spiral) \
                if tracker.awaiting_layer_entry else True
            # The ordinary first poll is deliberately held: the parser
            # can claim a flat layer before the nozzle gets there. For a
            # continuously rising layer, a geometric match at its actual
            # Z is already physical entry evidence. Discarding it leaves
            # a visible gap at the start of every short spiral turn.
            spiral_entry = advanced and raw is not None and entry_confirmed \
                and spiral
            result = tracker.accept(
                coarse, refined, raw, live_position is not None,
                advanced=advanced and not spiral_entry, entry_confirmed=entry_confirmed)
            if raw is not None and live_position is not None and not tracker.awaiting_layer_entry:
                tracker.last_confirmed_z = float(live_position[2])
            return result


    def plate_visited(self, anchor, split, rows):
        return self._objects.observe(self._view, anchor, split, rows)


    def plate_progress(self, anchor, file_position=None, live_position=None, paused=False, extruding=None, *, motion=...):
        """The composed payload (the tests and the one-shot consumers):
        the memoised layers plus the volatile split. The motion total is
        the progress slider's range — the layer's own edge count."""
        layers = self.plate_layers(anchor) if self._view is not None else {}
        split = self.plate_split(anchor, file_position, live_position, paused=paused, extruding=extruding) \
            if motion is ... else motion.split if motion is not None and motion.layer == anchor else None
        method = "motion index" if split is not None else "unavailable"
        motion_total = 0
        if self._view is not None:
            with self._view._index.cache_lock:
                motion_total = self._view._index.motion_count(anchor)
        # The refusal travels WITH the payload: a latched layer and one
        # still arriving look identical from here (no current), and the
        # face promised a load for the latched one forever. "outside" is
        # the anchor a shrunken file left behind — the same promise, the
        # same answer.
        refusal = ""
        if isinstance(anchor, int) and self._view is not None:
            if anchor in self._failed_hydrate:
                refusal = "failed"
            elif not 0 <= anchor < len(self._view.ranges):
                refusal = "outside"
        return {"layers": layers, "split": split, "method": method,
                "partial": motion.partial if motion is not ... and motion is not None else 0.0,
                "motionTotal": motion_total, "anchor": anchor, "refusal": refusal}

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
            self._prepared.rebind(prepared)
            return
        self._generation += 1
        self._cancel.set()
        self._cancel = threading.Event()
        self._job = None
        self._view = None
        self._progress = None
        # The cutover precedes the install: the OLD machine's writer is
        # retired and checkpointed through the OLD store, so an A
        # writer can never publish into B's namespace.
        self._prepared.retire()
        self._cache = cache
        self._prepared.rebind(prepared)
        self._reset_print_state()
        self._publish_change()

    def _reset_print_state(self):
        """The print-specific state a new identity (or a new machine)
        renders meaningless: the memoised windows, the RAM tiers, the
        prepared table and the scrub records all belong to the file
        that was being served."""
        self._plate_layers_memos = {}
        self._full_cache = _ByteBoundedLru(_FULL_CACHE_MAX_BYTES)
        self._full_next = 0
        self._decoded_lru = _ByteBoundedLru(
            _GPU_DECODED_LRU_MAX_BYTES if self._gpu_consumers else _DECODED_LRU_MAX_BYTES,
            _DECODED_LRU_MIN_ENTRIES)
        self._gpu_prefetch_key = None
        self._gpu_prefetch_done.clear()
        self._decoded_pins = {}
        self._decoded_sizes = {}
        self._prepared.reset()
        self._manual_anchor = None
        self._manual_anchor_calls = 0
        self._manual_anchor_changes = 0
        self._split_tracker.reset()
        self._objects.reset()


        self._wanted = self._restored = self._save = False
        self._hydrate.clear()
        self._hydrate_arrays.clear()
        self._hydrating = None
        self._failed_hydrate.clear()
        self._pass_error = ""
        self._error = ""

    def _open_prepared(self) -> None:
        """Open the current file's prepared table. The session owns the
        identity strength gate and the table itself; the service owns
        WHEN — here, the build/restore that just installed the view."""
        self._prepared.open(self._files.identity)

    def _adopt_prepared(self) -> None:
        """Adopt the table for the view that just landed. The session
        owns the fast-path/repair/fresh policy; the PASS FRONTIER is the
        service's own scheduling state, and a complete clean table stands
        it down with the fast path."""
        if self._prepared.adopt(len(self._view.ranges)):
            self._full_next = len(self._view.ranges)

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
            source = self._presentation_source(candidate)
            if source == "decoded" and view._index.compact \
                    and candidate not in view._index.hydrated_layers:
                # GPU speculation serves geometry without motion arrays.
                # Once that layer enters the LIVE window, its physical
                # tracking debt stands even though presentation is hot.
                self._hydrate_arrays.add(candidate)
            elif source not in {"decoded", "failed"}:
                self._hydrate.add(candidate)
                self._foreground_pending.set()

    def _on_files_changed(self):
        # A new file (or a re-downloaded one) invalidates failed hydration
        # attempts: the bytes the latch was based on no longer exist.
        self._failed_hydrate.clear()
        self._pass_error = ""
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
        self._prepared.open(identity)
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
        # The arrays debt is pruned on the same terms: a layer the
        # retention window no longer holds is no debt at all (its
        # arrays would evict again the moment another hydrate lands).
        self._hydrate_arrays = {
            n for n in self._hydrate_arrays
            if 0 <= n < len(index.ranges) and n not in index.hydrated_layers
            and ((index.followed_layer is None
                  or index.followed_layer - 1 <= n <= index.followed_layer + 1)
                 or (manual is not None and manual - 1 <= n <= manual + 1))}
        prefetch = self._gpu_prefetch_layer(index) if not self._hydrate and not self._hydrate_arrays else None
        if self._hydrate or prefetch is not None:
            self._start_hydration(index, manual, prefetch)
        elif self._drain_arrays_debt(index):
            return
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
            generation = self._generation
            self._submit("save",
                         lambda: self._save_index(cache_store, generation,
                                                  identity, index))
        elif self._view is not None and self._full_next >= len(self._view.ranges) \
                and self._prepared.has_source() and not self._prepared.saved:
            # The pass's completion finishes the incremental writer.
            # The latch rides the COMMIT: a failed publish never
            # reads as saved. A failed attempt self-heals — the next
            # demand's persist opens a fresh writer and this branch
            # retries the finish.
            writer = self._prepared.writer
            self._prepared.writer = None
            if writer is not None:
                # The store is captured NOW: a machine rebind swaps
                # self._prepared's store before the worker runs, and
                # this writer's finalise must publish through the store
                # that opened it (its own machine's namespace).
                store = self._prepared.store
                def prepared_save():
                    return store.finish_write(writer)
                self._submit("prepared_save", prepared_save)
        elif self._view is not None and not self._pass_error \
                and self._full_next < len(self._view.ranges):
            self._start_full_preparation(index)


    def _start_hydration(self, index, manual, prefetch):
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
        background = prefetch is not None
        if background:
            submitted = [prefetch]
            self._foreground_pending.clear()

        # Only a layer with NO presentation source at all needs the
        # G-code lease for the DECODE — packed RAM, prepared disk
        # and already-hydrated index arrays are each sufficient on
        # their own. A compact layer served from packed or prepared
        # data therefore decodes NOW, and the file it still wants is
        # the MOTION ARRAYS' alone: without them the split rides the
        # byte-fraction estimate (the live stall/jump saga). That
        # want is the debt recorded below when no lease is in hand,
        # never a bar on the decode.
        needs_raw = any(self._presentation_source(layer) == "raw"
                        for layer in submitted)
        arrays_owed = {layer for layer in submitted
                       if not background and index.compact and layer not in index.hydrated_layers
                       and (not self._gpu_consumers or index.followed_layer is None
                            or abs(layer - index.followed_layer) <= 1)}
        lease = self._files.lease() if (needs_raw or arrays_owed) else None
        if needs_raw and lease is None:
            self._hydrate.update(submitted)
            self._files.request_file()
            return
        if arrays_owed and lease is None:
            self._hydrate_arrays.update(arrays_owed)
            self._files.request_file()
        self._hydrating = set(submitted)
        cache = self._full_cache
        # The demanded encodings persist from the WORKER (below), so
        # the writer they enter is opened and captured HERE, on the
        # owner thread — a lazy open inside the worker would mutate
        # owner state across the thread boundary, and could resurrect
        # a writer a cutover had already retired. The capture plus
        # the writer's own retirement flag is the structural
        # ownership the pass already runs on. Opening it here also
        # keeps the self-heal a failed publish relies on: the next
        # demand opens a fresh writer and the finish retries it.
        prepared_writer = self._prepared.writer_target(len(self._view.ranges))
        prepared_store = self._prepared.store
        decode_cancel = self._cancel
        gpu_decode = bool(self._gpu_consumers)
        # No anchor argument: the worker reads the index's
        # followed_layer at COMPLETION, so a worker that finishes
        # after an anchor change applies the latest policy.
        # The WHOLE demanded window rides ONE task: a seek's three
        # layers arrive together instead of through three chained
        # round-trips. The worker owns its own ENCODINGS — they enter
        # the writer before it returns, which is the only way a
        # demanded layer's per-layer write+flush lands off the UI
        # thread — and returns (failed, stash) for the rest, which
        # the generation-checked _finish commits: a stale old-job
        # worker's appends reach a retired writer and are refused. A
        # cached layer decodes straight off the compact store instead
        # of re-reading the file and re-walking the geometry.
        task = LayerHydrationTask(
            index=index,
            layers=tuple(submitted),
            packed=cache,
            lease=lease,
            arrays_owed=frozenset(arrays_owed),
            background=background,
            cancelled=decode_cancel,
            immutable=gpu_decode,
            store=prepared_store,
            writer=prepared_writer,
            foreground=self._foreground_pending,
            read_prepared=self._prepared.reader(),
        )
        self._submit("hydrate", task.run, lease)


    def _start_full_preparation(self, index):
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
                        or self._prepared.served(layer):
                    continue
                if self._prepared.uncacheable(layer):
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
        prepared_read = self._prepared.reader()
        prepared_table = self._prepared.table
        # The incremental writer opens whenever a pass must walk
        # (a fresh file, or a repair):
        # the fast path's saved latch has already
        # stood it down for a complete clean table.
        prepared_writer = self._prepared.writer_target(len(self._view.ranges))
        # The store the writer belongs to is captured NOW: a
        # machine rebind swaps the session's store while the batch
        # runs, and the appends must reach the store that opened
        # the writer (which refuses them after the cutover's
        # retirement anyway — the capture makes the ownership
        # structural, never a thread-timing accident).
        prepared_store = self._prepared.store

        # No demand exists on the owner thread at this instant.
        # Any later request flips the event and cooperatively
        # interrupts the in-progress dense layer too, not merely
        # the gap between layers.
        self._foreground_pending.clear()

        task = LayerPreparationTask(
            index=index,
            packed=cache,
            start=start,
            lease=lease,
            read_prepared=prepared_read,
            table=prepared_table,
            store=prepared_store,
            writer=prepared_writer,
            foreground=self._foreground_pending,
            failed_layers=frozenset(self._failed_hydrate),
        )

        self._submit("fullprep", task.run, lease)


    def _drain_arrays_debt(self, index) -> bool:
        """The motion-array debt's one drain attempt: hydrate the
        physical arrays of layers whose geometry the prepared or packed
        store already served.

        The lease is the arrays' ONLY input, so while the file is
        absent this submits nothing — it asks for the file (the
        recovery's own trigger, the same request the raw presentation
        path makes) and leaves the poll to its other work. True means a
        worker was submitted. The attempt settles the debt either way:
        a hydration that fails degrades the split exactly as the demand
        path's own warning does, and the next lease-less demand
        re-records it.
        """
        if not self._hydrate_arrays or self._view is None:
            return False
        lease = self._files.lease()
        if lease is None:
            self._files.request_file()
            return False
        owed = sorted(self._hydrate_arrays)
        self._hydrate_arrays.clear()
        # _advance reaches this drain after foreground geometry is served.
        # Only a NEW demand may interrupt the array worker, not the flag
        # left by the demand whose cached geometry we just satisfied.
        self._foreground_pending.clear()
        # The window _finish reports on: the debt's own layers, so a
        # failure list can never name a layer this worker never saw.
        self._hydrating = set(owed)

        task = LayerArrayTask(
            index=index,
            layers=tuple(owed),
            lease=lease,
            foreground=self._foreground_pending,
        )

        self._submit("hydrate", task.run, lease)
        return True

    def _save_index(self, cache_store, generation, identity, index):
        """The index save's own gate (the clear's lifecycle): a save
        from a swept store's OLDER generation must never recreate the
        directory the clear just removed. A machine rebind's store is
        a different directory — that save still belongs to the
        machine it was built for."""
        if generation != self._generation and cache_store is self._swept_store:
            return None
        return cache_store.save(identity, index)

    def _submit(self, kind, work, lease=None):
        generation = self._generation
        self._busy = kind
        self._busy_generation = generation
        # The terminal is emitted by the WORK ITEM, never by a future
        # callback. add_done_callback runs its callback on whichever
        # thread attached it once the work has already finished, and the
        # pool can finish a fast job (a hot-store restore) before
        # _submit even reaches the attach — that put the emit, and with
        # it an auto-connected _finish, on the CALLING thread, inside
        # the stack of the public call that submitted the work. Emitting
        # here leaves exactly one thread for the terminal: the one that
        # ran the work.
        def run():
            try: value, error = work(), None
            except Exception as exc: value, error = None, str(exc)
            try:
                self._completed.emit(generation, kind, value, error, lease)
            except RuntimeError:
                if lease is not None: lease.close()  # Qt owner destroyed at shutdown.
        self._executor.submit(run)
        self._notify_changed(kind)

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
                    self._pass_error = ""
                    # The restore path returns BEFORE _advance's open
                    # (the submission is its last step): open the
                    # table HERE so the adoption right below sees it —
                    # a reopened complete store must take the fast
                    # path, never wait for a later _advance that never
                    # re-adopts.
                    self._open_prepared()
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
                        # The encoding entered the writer from the worker
                        # that produced it. The coverage still follows
                        # the COMMIT: a stale generation's stash is
                        # dropped here and must not count as resolved.
                        self._prepared.note_coverage((layer,))
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
                        self._prepared.note_coverage(encoded.keys())
                    if isinstance(uncacheable, (set, list, tuple)):
                        # The codec's refusals resolve too: coverage
                        # counts them, and the writer records the
                        # state so the next session never retries.
                        self._prepared.note_coverage(uncacheable)
                        self._prepared.mark_uncacheable(uncacheable)
                    if isinstance(frontier, int):
                        self._full_next = max(self._full_next, frontier)
                        self._pass_error = ""
                else:
                    # The batch walked nothing: it raised, or returned
                    # something this commit cannot read. The `_advance`
                    # below would submit the same frontier again at
                    # once, and the terminal after that again — so
                    # latch the pass instead of resubmitting it.
                    self._pass_error = str(error or "the batch returned no result")
                    Logger.log("w", "the preparation pass failed: %s", self._pass_error)
            elif kind == "prepared_save":
                # The published file replaces the table this session
                # holds: a fresh/repair pass's offsets differ from
                # the old file's, and a stale table would serve
                # mis-aligned reads for the rest of the session.
                # The saved latch closes ONLY on the publish itself.
                if not self._prepared.publish(value is not None) \
                        and self._view is not None and self._prepared.arm_retry():
                    # finish_write detached/closed the failed writer.
                    # Rebuild once from the already-available prepared
                    # sources without waiting for another user demand.
                    self._full_next = 0
            self._notify_changed(kind)
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
        self._prepared.retire()
        # WAITED, not abandoned. cancel_futures drops the queued work,
        # but the worker already RUNNING keeps the store open and keeps
        # writing: on Windows a directory holding a file that appears
        # after the delete has listed it cannot be removed (the test
        # teardown met WinError 145), and the same race leaves a write
        # landing after the plugin believes it has shut down. The work
        # in flight is one bounded unit — the retire above has already
        # frozen the writer, so there is nothing waiting behind it.
        self._executor.shutdown(wait=True, cancel_futures=True)
