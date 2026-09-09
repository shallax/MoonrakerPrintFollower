"""Cura/QML presentation adapter. Inputs are values; outputs are user intents."""
from __future__ import annotations

import os
from PyQt6.QtCore import QObject, pyqtSignal
from UM.Logger import Logger


class PreviewPresentation(QObject):
    loadRequested = pyqtSignal()
    attachmentRequested = pyqtSignal()
    pauseRequested = pyqtSignal(int)
    removePauseRequested = pyqtSignal(int)
    clearPausesRequested = pyqtSignal()
    bedMeshVisibilityRequested = pyqtSignal(bool)
    controlsChanged = pyqtSignal()

    def __init__(self, application, cura, parent=None):
        super().__init__(parent)
        self._application, self._cura = application, cura
        self._overlay = self._actions = None
        self._values = {}
        self._closed = False
        cura.changed.connect(self.refresh)
        self.refresh()

    @property
    def controls(self): return tuple(item for item in (self._overlay, self._actions) if item is not None)

    def publish(self, values):
        self._values.update(values)
        for control in self.controls:
            for name, value in self._values.items():
                try: control.setProperty(name, value)
                except RuntimeError: pass

    def refresh(self):
        if self._closed: return
        try:
            window = self._application.getMainWindow()
            content = window.contentItem() if window is not None else None
            if content is not None:
                directory = os.path.dirname(__file__)
                created = False
                if self._overlay is None:
                    overlay = self._application.createQmlComponent(os.path.join(directory, "EmptyPreviewLoadButton.qml"))
                    if overlay is not None:
                        self._overlay = overlay
                        overlay.loadClicked.connect(self.loadRequested.emit)
                        # The overlay's bed-mesh signal must reach the
                        # presenter too: it was never connected, so the
                        # empty-preview "Hide bed mesh" clicked into the
                        # void while the panel variant worked (that one
                        # IS wired in the actions tuple below).
                        overlay.bedMeshVisibilityRequested.connect(self.bedMeshVisibilityRequested.emit)
                        overlay.destroyed.connect(lambda: self._overlay_destroyed(overlay))
                        created = True
                if self._overlay is not None:
                    self._overlay.setParentItem(content)
                    self._overlay.setParent(content)
                if self._actions is None:
                    actions = self._application.createQmlComponent(os.path.join(directory, "PreviewActionPanelControls.qml"))
                    if actions is not None:
                        self._actions = actions
                        for name, target in (
                            ("loadClicked", self.loadRequested.emit),
                            ("pauseClicked", self.attachmentRequested.emit),
                            ("pauseAtLayerRequested", self.pauseRequested.emit),
                            ("removePauseAtLayerRequested", self.removePauseRequested.emit),
                            ("clearPauseAtLayersRequested", self.clearPausesRequested.emit),
                            ("bedMeshVisibilityRequested", self.bedMeshVisibilityRequested.emit),
                        ):
                            signal = getattr(actions, name, None)
                            if signal is not None: signal.connect(target)
                        self._application.addAdditionalComponent("saveButton", actions)
                        actions.destroyed.connect(lambda: self._actions_destroyed(actions))
                        created = True
                if created: self.controlsChanged.emit()
        except Exception as error:
            Logger.log("w", "Moonraker Preview controls unavailable: %s", error)
        self.publish({"previewStageActive": self._cura.preview_active})

    def _overlay_destroyed(self, overlay):
        if overlay is self._overlay: self._overlay = None

    def _actions_destroyed(self, actions):
        self._remove_actions(actions)
        if actions is self._actions: self._actions = None

    def _remove_actions(self, actions):
        remover = getattr(self._application, "removeAdditionalComponent", None)
        if callable(remover):
            try:
                remover("saveButton", actions)
                return
            except Exception: pass
        # Capability-guarded compatibility with Cura's add-only component API.
        components = getattr(self._application, "_additional_components", {})
        if isinstance(components, dict) and isinstance(components.get("saveButton"), list):
            components["saveButton"] = [item for item in components["saveButton"] if item is not actions]
            signal = getattr(self._application, "additionalComponentsChanged", None)
            if signal is not None: signal.emit("saveButton")

    def close(self):
        self._closed = True
        try: self._cura.changed.disconnect(self.refresh)
        except Exception: pass
        if self._actions is not None: self._remove_actions(self._actions)
        for control in self.controls:
            try:
                control.setProperty("visible", False)
                control.deleteLater()
            except RuntimeError: pass
        self._overlay = self._actions = None

