"""The only follower component that knows Cura scene/view/file lifecycle APIs."""
from __future__ import annotations

from contextlib import contextmanager
import os
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtWidgets import QMessageBox
from UM.Logger import Logger
from UM.Backend.Backend import BackendState

from .CuraLifecycleBridge import CuraLifecycleBridge
from .NativeNozzleLifecycle import keep_native_nozzle_visible

# Layer heights read per event-loop tick while building the table.
_HEIGHTS_PER_TICK = 200


class CuraIntegration(QObject):
    invalidated = pyqtSignal(str)
    changed = pyqtSignal()
    positionChanged = pyqtSignal()
    viewSwapped = pyqtSignal()
    fileLoaded = pyqtSignal(str)
    loadFailed = pyqtSignal(str)

    # The bound for the fileCompleted confirmation. Cura's fileCompleted
    # is NOT a terminal signal — several refusal paths return silently
    # before the parse starts — so a load that never confirms must not
    # latch `loading` for the process lifetime. Five minutes (the live
    # ruling): Cura legitimately takes longer than half a minute to
    # parse huge gcode, and a late completion is absorbed anyway.
    LOAD_WATCHDOG_MS = 300000

    def __init__(self, application, parent=None):
        super().__init__(parent)
        self.application = application
        self.controller = application.getController()
        self.lifecycle = CuraLifecycleBridge()
        self._connections = []
        self._view_connections = []
        self._root = self._view = None
        self._heights = None
        self._slicing = self._closed = False
        self._settle_until = 0.0
        self._writing = self._own_scene_changes = 0
        self._load_lease = None
        self._load_watch_lease = None
        self._watch = QTimer(self)
        self._watch.setInterval(75)
        self._watch.timeout.connect(self._position_changed)
        self._connect(application, "fileCompleted", self._file_completed)
        self._connect(application, "mainWindowChanged", self._refresh)
        self._connect(self.controller, "activeViewChanged", self._refresh)
        self._connect(self.controller, "activeStageChanged", self._refresh)
        backend = application.getBackend()
        if backend is not None:
            self._connect(backend, "slicingStarted", self._slicing_started)
            self._connect(backend, "slicingCancelled", self._slicing_finished)
            self._connect(backend, "backendStateChange", self._backend_changed)
        self._refresh()

    def _connect(self, owner, name, callback):
        signal = getattr(owner, name, None)
        if signal is not None:
            try:
                signal.connect(callback)
                self._connections.append((signal, callback))
            except Exception:
                Logger.log("w", "Moonraker: could not bind Cura signal %s", name)

    @property
    def generation(self): return self.lifecycle.generation
    @property
    def loading(self): return self._load_lease is not None
    @property
    def suspended(self): return self._slicing or self.loading or time.monotonic() < self._settle_until
    @property
    def view(self): return self._view

    def nudge_cura_activity(self):
        """Re-run Cura's own platform-activity computation after the
        plugin's load completes: the plugin-driven load path fires none
        of Cura's scene-change events, so Cura's action panel (and the
        card's panel host) stays hidden until some unrelated Cura
        activity. Cura's own computation sets the flag and emits
        activityChanged — the presenter's gate cascade then swaps the
        card hosts and shows the panel."""
        try:
            updater = getattr(self.application, "updatePlatformActivity", None)
            if callable(updater):
                updater()
        except Exception:
            pass

    def nudge_layer_view(self):
        """Re-announce the current layer so Cura's own chrome (the
        layer slider) wakes for the plugin-loaded print. Guarded as an
        own write so the follower never reads the bump as a user drag."""
        view = self._view
        if view is None:
            return
        try:
            self._writing += 1
            try:
                current = view.getCurrentLayer()
                view.setLayer(max(0, int(current) - 1))
                view.setLayer(int(current))
            finally:
                self._writing -= 1
        except Exception:
            pass

    @property
    def preview_active(self):
        try:
            stage = self.controller.getActiveStage()
            return stage is not None and stage.getId() == "PreviewStage"
        except Exception:
            return False
    @property
    def has_toolpath(self):
        view = self._view
        if view is None: return False
        try:
            # getActivity() tracks view ANIMATION, not content: it
            # drops once the render settles, which took the preview
            # card down after every load. Layer data is the
            # toolpath's own signature.
            if hasattr(view, "getLayerData") and view.getLayerData() is not None:
                return True
        except Exception:
            pass
        try:
            return max(0, int(view.getMaxLayers())) > 0
        except Exception:
            return False
    @property
    def scene_has_objects(self):
        # A loaded-but-unsliced model: the empty card must make way for
        # Cura's slice pane (the gate once read platformActivity, which
        # covers this but flaps during Cura's own busy cycles). The
        # scene root always carries the build plate, the nozzle and the
        # camera — and the first two carry mesh data — so the signal is
        # a selectable mesh-bearing child: a real model's signature.
        try:
            root = self.controller.getScene().getRoot()
            return any(node.isSelectable() and node.getMeshData() is not None
                       for node in root.getAllChildren())
        except Exception:
            return False
    @property
    def selected_layer(self):
        try: return max(0, int(self._view.getCurrentLayer()))
        except Exception: return None
    @property
    def max_layer(self):
        try: return max(0, int(self._view.getMaxLayers()))
        except Exception: return None
    @property
    def heights(self):
        if self._view is None:
            return ()
        if self._heights is None:
            # Build the layer-height table progressively: reading it for
            # a large file in one go is a visible UI freeze right after
            # the print loads. The per-layer reads are cheap; the batch
            # is what hurts, so spread it over event-loop ticks. Callers
            # already degrade gracefully with partial heights (the
            # resolver falls back to metadata paths).
            batch = []
            self._heights = batch
            self._heights_built = 0
            try:
                view = self._view
                if view is not None and hasattr(view, "_calculateLayerHeightsCache"):
                    view._calculateLayerHeightsCache()
            except Exception:
                pass
            self._build_heights_step(batch)
        return tuple(self._heights)

    def _build_heights_step(self, batch):
        view = self._view
        # The batch identity ties every queued tick to its own build: a
        # stale tick from an invalidated build (or a swapped view) must
        # not append into a newer one.
        if self._closed or view is None or self._heights is not batch:
            return
        total = (self.max_layer or 0) + 1
        target = min(self._heights_built + _HEIGHTS_PER_TICK, total)
        for layer in range(self._heights_built, target):
            try:
                batch.append(float(view._getLayerHeight(layer)))
            except Exception:
                batch.append(0.0)
        self._heights_built = target
        if target < total:
            QTimer.singleShot(0, lambda: self._build_heights_step(batch))
        else:
            self._heights = tuple(batch)
            self._heights_built = 0

    def watch(self, enabled):
        if enabled and not self._closed:
            if not self._watch.isActive(): self._watch.start()
        else:
            self._watch.stop()

    def queue(self, callback, delay_ms=0):
        token = self.generation
        def run():
            if not self._closed and self.lifecycle.is_current(token):
                callback()
        QTimer.singleShot(delay_ms, run)

    @contextmanager
    def writing_preview(self):
        self._writing += 1
        try: yield self._view
        finally: self._writing -= 1

    @contextmanager
    def decorating_scene(self):
        self._own_scene_changes += 1
        try: yield self.controller.getScene().getRoot()
        finally: self._own_scene_changes -= 1

    def invalidate(self, reason):
        self.lifecycle.invalidate(reason)
        self._heights = None
        self.invalidated.emit(str(reason))

    def _position_changed(self, *_args):
        if not self._writing and not self._closed:
            self.positionChanged.emit()

    def _refresh(self, *_args):
        if self._closed: return
        try:
            root = self.controller.getScene().getRoot()
            if root is not self._root:
                if self._root is not None:
                    self._root.childrenChanged.disconnect(self._scene_changed)
                self._root = root
                root.childrenChanged.connect(self._scene_changed)
        except Exception:
            pass
        try: view = self.controller.getView("SimulationView")
        except Exception: view = None
        if view is not self._view:
            for signal, callback in self._view_connections:
                try: signal.disconnect(callback)
                except Exception: pass
            self._view_connections.clear()
            self._view, self._heights = view, None
            # A swapped view (stage switches, window changes) restores its
            # own layer/path handles asynchronously — after rapid back-and-
            # forth switching Cura can hang and the restore lands seconds
            # late. Those echoes must never read as user overrides: clear
            # the follower's expectations and suspend override detection
            # generously. Each switch refreshes the window, so it always
            # spans the last switch plus the recovery lag.
            self._settle_until = time.monotonic() + 2.0
            self.viewSwapped.emit()
            if view is not None:
                for name in ("currentLayerNumChanged", "currentPathNumChanged"):
                    signal = getattr(view, name, None)
                    if signal is not None:
                        signal.connect(self._position_changed)
                        self._view_connections.append((signal, self._position_changed))
                signal = getattr(view, "activityChanged", None)
                if signal is not None:
                    signal.connect(self._activity_changed)
                    self._view_connections.append((signal, self._activity_changed))
                # The layer data's arrival (a slice, the engine's
                # toolpath) must refresh the panel: without this hook
                # the card waits for an unrelated refresh and boots
                # intermittently render the preview empty (the
                # harness's insert-slice flow exposed the race).
                signal = getattr(view, "maxLayersChanged", None)
                if signal is not None:
                    signal.connect(self._layers_changed)
                    self._view_connections.append((signal, self._layers_changed))
        self.changed.emit()

    def _layers_changed(self, *_args):
        self._heights = None
        self.changed.emit()

    def _activity_changed(self, *_args):
        self._heights = None
        self.changed.emit()

    def _scene_changed(self, *_args):
        if self.loading or self._slicing or self._own_scene_changes or self._closed: return
        self._settle_until = time.monotonic() + 0.35
        self.invalidate("Cura scene structure changed")
        self.queue(self._refresh, 360)

    def _slicing_started(self, *_args):
        if self.loading: return
        self._slicing = True
        self.invalidate("Cura slicing started")

    def _backend_changed(self, state, *_args):
        if state == BackendState.Done: self._slicing_finished()

    def _slicing_finished(self, *_args):
        self._slicing = False
        self._settle_until = time.monotonic() + 0.35
        self._heights = None
        self.queue(self._refresh, 360)

    def switch_to_preview(self):
        try:
            self.controller.setActiveStage("PreviewStage")
            return True
        except Exception:
            return False

    def confirm_replace(self, callback):
        self.switch_to_preview()
        def ask():
            answer = QMessageBox.question(None, "Moonraker Print Follower",
                "Replace Cura contents?\n\nThis will discard everything currently loaded in Cura and replace it "
                "with the G-code currently printing in Moonraker.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            # The answer is the whole gate on this load, and a box that
            # comes back with a code the plugin does not recognise
            # silently takes the No branch: log the raw code so a
            # refused replace is never invisible.
            Logger.log("i", "Moonraker replace confirm: answer=%s yes=%s",
                       int(answer), int(QMessageBox.StandardButton.Yes))
            if answer == QMessageBox.StandardButton.Yes: self.queue(callback)
        self.queue(ask)

    def load(self, lease):
        if self._closed:
            lease.close()
            return False
        if self.loading and self._load_lease is not None:
            # A user's explicit Load supersedes a pending one (the
            # critic's catch): the five-minute watchdog must never
            # lock the Load button after a silently-refused load.
            # The stale lease parks as the watch lease (its late
            # completion is absorbed, and the file's lease ends with
            # the next load's start below), then the new load
            # proceeds normally.
            self._load_watch_lease = self._load_lease
            self._load_lease = None
        if self._view is None:
            # The no-printer / no-build-volume window: Cura silently
            # drops the file without ever emitting fileCompleted.
            lease.close()
            self.loadFailed.emit("Cura has no active printer yet")
            return False
        if self._load_watch_lease is not None:
            self._load_watch_lease.close()
            self._load_watch_lease = None
        self._load_lease = lease
        self._heights = None
        # The application's parse job can outlive this plugin. Keep the file
        # lease on an application-owned callback, not the plugin QObject.
        signal = self.application.fileCompleted
        def release_when_complete(path):
            if os.path.abspath(str(path)) != os.path.abspath(lease.path):
                return
            lease.close()
            try: signal.disconnect(release_when_complete)
            except (RuntimeError, TypeError): pass
        signal.connect(release_when_complete)
        # The watchdog un-sticks `loading` when the confirmation never
        # arrives. The file is dropped, not deleted: Cura's parse may
        # still be reading it, and a late completion is absorbed below.
        def watchdog():
            if self._load_lease is not None and self._load_lease.path == lease.path:
                self._load_lease = None
                self._load_watch_lease = lease
                self.loadFailed.emit("Cura did not confirm the load in time")
            try: signal.disconnect(release_when_complete)
            except (RuntimeError, TypeError): pass
        QTimer.singleShot(self.LOAD_WATCHDOG_MS, watchdog)
        try:
            self.application.readLocalFile(QUrl.fromLocalFile(lease.path), add_to_recent_files=False)
            self.changed.emit()
            return True
        except Exception as error:
            self._load_lease = None
            signal.disconnect(release_when_complete)
            lease.close()
            self.loadFailed.emit(str(error))
            return False

    def _file_completed(self, path):
        lease = self._load_lease
        expected = lease is not None and os.path.abspath(str(path)) == os.path.abspath(lease.path)
        absorbed = False
        if expected:
            self._load_lease = None
            lease.close()
        elif self._load_watch_lease is not None and os.path.abspath(str(path)) == os.path.abspath(self._load_watch_lease.path):
            # A timed-out load finished late: complete it quietly. The
            # parse has finished, so releasing the temp file is safe.
            absorbed = True
            self._load_watch_lease.close()
            self._load_watch_lease = None
        self._heights = None
        self._settle_until = time.monotonic() + 0.25
        if not expected and not absorbed: self.invalidate("Cura file replaced")
        self.fileLoaded.emit(str(path))
        self.queue(self._refresh, 260)

    def show_nozzle(self):
        if self.preview_active and self._view is not None:
            keep_native_nozzle_visible(self._view)

    def close(self):
        if self._closed: return
        self._closed = True
        self.lifecycle.invalidate("shutdown")
        self._watch.stop()
        for signal, callback in self._connections + self._view_connections:
            try: signal.disconnect(callback)
            except Exception: pass
        self._connections.clear()
        self._view_connections.clear()
        if self._root is not None:
            try: self._root.childrenChanged.disconnect(self._scene_changed)
            except Exception: pass
        if self._load_lease is not None:
            # The application-owned completion callback may never run
            # (Cura's silent refusal paths) — release, never drop.
            self._load_lease.close()
            self._load_lease = None
        if self._load_watch_lease is not None:
            self._load_watch_lease.close()
            self._load_watch_lease = None


