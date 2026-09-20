from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import threading
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal

from .GCodeIndex import LayerMotionIndex, build_index_from_file, hydrate_layer_from_file
from .MonitorFormatting import _point_in_polygon
from .PlateProgress import plate_layers as _plate_layers, split_index as _split_index


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


class GCodeIndexService(QObject):
    """Own index lifecycle with at most ONE submitted worker job, no work queue.

    Requests coalesce into desired state; replacement waits asynchronously for
    the old worker. Cache restore/build/hydration/save all run off the UI thread.
    """
    changed = pyqtSignal()
    failed = pyqtSignal(str)
    _completed = pyqtSignal(int, str, object, object, object)

    def __init__(self, files, cache, parent=None):
        super().__init__(parent)
        self._files, self._cache = files, cache
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
        self._plate_layers_key = None
        self._plate_layers = {}
        self._visited_key = None
        self._visited = set()
        self._visited_upto = -1
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
        self._plate_layers_key = None
        self._plate_layers = {}
        self._visited_key = None
        self._visited = set()
        self._visited_upto = -1
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

    def plate_layers(self, anchor):
        """The follower's STATIC half: the prev/current/next bundle,
        memoised per anchor and hydration fill — the model republishes
        it with a stable identity so QML never re-wraps the polylines
        on a quiet poll (the perf panel's split)."""
        if self._view is None:
            return {}
        index = self._view._index
        with index.cache_lock:
            counts = tuple(index.motion_count(layer)
                           for layer in (anchor - 1, anchor, anchor + 1))
            if self._plate_layers_key != (anchor, counts):
                self._plate_layers = _plate_layers(index, anchor)
                self._plate_layers_key = (anchor, counts)
        return self._plate_layers

    def plate_split(self, anchor, file_position=None):
        """The follower's VOLATILE half: the printed/unprinted boundary
        — the only per-poll cost."""
        if self._view is None or file_position is None:
            return None
        if not self._plate_layers.get("current"):
            return None
        index = self._view._index
        with index.cache_lock:
            return _split_index(index, anchor, file_position)

    def plate_visited(self, anchor, split, rows):
        """The per-layer printed objects: which polygons the executed
        motions have touched. Built HERE (the raw arrays never cross
        the boundary) and READ BACK FROM THE LAYER'S START — an
        attach part-way through a layer still marks everything the
        toolhead already printed (the live ruling: the DEFINE order
        is not the print order on every machine, so the visits are
        the truth). The walk advances only the new motions per poll."""
        if self._view is None or split is None or anchor is None:
            return frozenset()
        index = self._view._index
        polygons = [(row["name"], row["polygon"]) for row in rows
                    if row.get("polygon") and row.get("name")]
        with index.cache_lock:
            if self._visited_key != (anchor,):
                self._visited_key = (anchor,)
                self._visited = set()
                self._visited_upto = -1
            if split <= self._visited_upto:
                return frozenset(self._visited)
            xs = index.motion_x[anchor] if anchor < len(index.motion_x) else ()
            ys = index.motion_y[anchor] if anchor < len(index.motion_y) else ()
            end = min(split + 1, len(xs))
            for motion in range(self._visited_upto + 1, end):
                x, y = float(xs[motion]), float(ys[motion])
                for name, polygon in polygons:
                    if _point_in_polygon(x, y, polygon):
                        self._visited.add(name)
                        break
            self._visited_upto = split
            return frozenset(self._visited)

    def plate_progress(self, anchor, file_position=None):
        """The composed payload (the tests and the one-shot consumers):
        the memoised layers plus the volatile split."""
        layers = self.plate_layers(anchor) if self._view is not None else {}
        split = self.plate_split(anchor, file_position)
        method = "motion index" if split is not None else "unavailable"
        return {"layers": layers, "split": split, "method": method, "anchor": anchor}

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
        # previous layer's ghost becomes worth reading.
        self._hydrate = {n for n in self._hydrate if layer - 1 <= n <= layer + 1}
        self._request_window(layer)
        self._advance()

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
        self._hydrate = {n for n in self._hydrate if n < len(self._view.ranges) and not self._view.hydrated(n)
                         and n not in self._failed_hydrate
                         and (index.followed_layer is None
                              or index.followed_layer - 1 <= n <= index.followed_layer + 1)}
        if self._hydrate:
            lease = self._files.lease()
            if lease is None:
                self._files.request_file()
                return
            layer = min(self._hydrate)
            self._hydrate.remove(layer)
            self._hydrating = layer
            # No anchor argument: the worker reads the index's
            # followed_layer at COMPLETION, so a worker that finishes
            # after an anchor change applies the latest policy.
            self._submit("hydrate", lambda: hydrate_layer_from_file(index, lease.path, layer), lease)
        elif self._save and strong:
            self._save = False
            index = self._view._index
            self._submit("save", lambda: self._cache.save(identity, index))

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
                if value:
                    self._save = True
                else:
                    # A failed hydration must not be re-attempted on every
                    # poll — each attempt re-reads the whole file. The latch
                    # clears when a new file arrives or the index is rebuilt.
                    if self._hydrating is not None:
                        self._failed_hydrate.add(self._hydrating)
                self._hydrating = None
            self.changed.emit()
        self._advance()

    def close(self):
        if self._closed: return
        self._closed = True
        self._generation += 1
        self._cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)
