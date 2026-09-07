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


class CuraIntegration(QObject):
    invalidated = pyqtSignal(str)
    changed = pyqtSignal()
    positionChanged = pyqtSignal()
    fileLoaded = pyqtSignal(str)
    loadFailed = pyqtSignal(str)

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
            if hasattr(view, "getActivity"): return bool(view.getActivity())
            return view.getLayerData() is not None
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
            result = []
            try:
                view = self._view
                if view is not None and hasattr(view, "_calculateLayerHeightsCache"):
                    view._calculateLayerHeightsCache()
                for layer in range((self.max_layer or 0) + 1):
                    try: result.append(float(view._getLayerHeight(layer)))
                    except Exception: result.append(0.0)
            except Exception:
                pass
            self._heights = tuple(result)
        return self._heights

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
            if answer == QMessageBox.StandardButton.Yes: self.queue(callback)
        self.queue(ask)

    def load(self, lease):
        if self.loading or self._closed:
            lease.close()
            return False
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
        if expected:
            self._load_lease = None
            lease.close()
        self._heights = None
        self._settle_until = time.monotonic() + 0.25
        if not expected: self.invalidate("Cura file replaced")
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
            # The application-owned completion callback releases this lease.
            self._load_lease = None


