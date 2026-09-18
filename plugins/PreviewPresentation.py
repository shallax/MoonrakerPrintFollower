"""Cura/QML presentation adapter. Inputs are values; outputs are user intents."""
from __future__ import annotations

import os
from PyQt6.QtCore import QObject, pyqtSignal
from UM.Logger import Logger


class PreviewPresentation(QObject):
    loadRequested = pyqtSignal()
    attachmentRequested = pyqtSignal()
    improveEtaRequested = pyqtSignal()
    pauseAtLayerRequested = pyqtSignal(int)
    printPauseRequested = pyqtSignal()
    removePauseRequested = pyqtSignal(int)
    clearPausesRequested = pyqtSignal()
    bedMeshVisibilityRequested = pyqtSignal(bool)
    bedMeshThresholdsRequested = pyqtSignal(float, float)
    bedMeshExaggerationRequested = pyqtSignal(float)
    controlsChanged = pyqtSignal()

    def __init__(self, application, cura, parent=None):
        super().__init__(parent)
        self._application, self._cura = application, cura
        self._panel_shell = None
        self._overlay_shell = None
        self._panel_card = None
        self._overlay_card = None
        self._values = {}
        self._closed = False
        cura.changed.connect(self.refresh)
        # Cura's action panel is visible exactly while the platform is
        # active — its own property, its own signal. Recompute the
        # host gates on the same edge the panel itself flips on, so the
        # two cards can never both be up (the duplicate-card report).
        signal = getattr(application, "activityChanged", None)
        if signal is not None:
            signal.connect(self._publish_all)
        self.refresh()

    @property
    def controls(self): return tuple(control for control in (self._panel_card, self._overlay_card) if control is not None)

    def publish(self, values):
        self._values.update(values)
        self._publish_all()

    def publish_pause_verdicts(self, can_pause, can_resume, pause_reason, resume_reason,
                               pause_detail="", resume_detail=""):
        """The pause/resume grey-out's single authority (the debt
        pack's two-clock unification): the monitor model's verdicts,
        pushed to every card — the strip's enable and reasons read
        these instead of the preview block's own copies."""
        for control in self.controls:
            try:
                control.setProperty("stripCanPause", bool(can_pause))
                control.setProperty("stripCanResume", bool(can_resume))
                control.setProperty("stripPauseReason", str(pause_reason or ""))
                control.setProperty("stripResumeReason", str(resume_reason or ""))
                control.setProperty("stripPauseReasonDetail", str(pause_detail or ""))
                control.setProperty("stripResumeReasonDetail", str(resume_detail or ""))
            except RuntimeError:
                pass

    def _publish_all(self):
        # The gate per instance: the panel host lives inside Cura's own
        # action panel (Cura hides that whole panel when the platform
        # is idle, the empty state included); the corner overlay takes
        # over exactly then, so exactly one card is ever visible.
        configured = bool(self._values.get("configuredForFollowing"))
        preview = bool(self._values.get("previewStageActive"))
        panel_up = self._cura_panel_visible()
        for control in self.controls:
            overlay = control is self._overlay_card
            gate = configured and preview and (not overlay or not panel_up)
            try: control.setProperty("gateVisible", gate)
            except RuntimeError: pass
            for name, value in self._values.items():
                if name == "gateVisible": continue
                try: control.setProperty(name, value)
                except RuntimeError: pass

    def _cura_panel_visible(self):
        # The panel's own visibility predicate (Cura's
        # ActionPanelWidget: visible: CuraApplication.platformActivity).
        try:
            return bool(self._application.platformActivity)
        except Exception:
            return False

    @staticmethod
    def _inner_card(shell):
        from PyQt6.QtQuick import QQuickItem
        for item in shell.findChildren(QQuickItem):
            try:
                if item.property("objectName") == "moonrakerPreviewCard":
                    return item
            except Exception:
                pass
        return None

    def _wire(self, card):
        for name, target in (
            ("loadClicked", self.loadRequested.emit),
            ("pauseClicked", self.attachmentRequested.emit),
            ("improveEtaRequested", self.improveEtaRequested.emit),
            ("pauseAtLayerRequested", self.pauseAtLayerRequested.emit),
            ("printPauseRequested", self.printPauseRequested.emit),
            ("removePauseAtLayerRequested", self.removePauseRequested.emit),
            ("clearPauseAtLayersRequested", self.clearPausesRequested.emit),
            ("bedMeshVisibilityRequested", self.bedMeshVisibilityRequested.emit),
            ("bedMeshThresholdsRequested", self.bedMeshThresholdsRequested.emit),
            ("bedMeshExaggerationRequested", self.bedMeshExaggerationRequested.emit),
        ):
            signal = getattr(card, name, None)
            if signal is not None: signal.connect(target)

    def refresh(self):
        if self._closed: return
        try:
            window = self._application.getMainWindow()
            content = window.contentItem() if window is not None else None
            created = False
            if self._panel_shell is None:
                shell = self._application.createQmlComponent(os.path.join(
                    os.path.dirname(__file__), "MoonrakerPreviewCardPanelHost.qml"))
                if shell is not None:
                    card = self._inner_card(shell)
                    self._panel_shell = shell
                    self._panel_card = card
                    if card is not None:
                        self._wire(card)
                    self._application.addAdditionalComponent("saveButton", shell)
                    shell.destroyed.connect(lambda: self._shell_destroyed("panel"))
                    created = True
            if self._overlay_shell is None and content is not None:
                shell = self._application.createQmlComponent(os.path.join(
                    os.path.dirname(__file__), "MoonrakerPreviewCardOverlayHost.qml"))
                if shell is not None:
                    card = self._inner_card(shell)
                    self._overlay_shell = shell
                    self._overlay_card = card
                    if card is not None:
                        # Distinct names so the harness can tell the two
                        # hostings apart in one walk.
                        card.setProperty("objectName", "moonrakerPreviewCardOverlay")
                        self._wire(card)
                    # Parented ONCE: re-parenting a dynamically created
                    # component on every refresh is exactly the churn
                    # that freezes its property handling (engine-proven).
                    # The window's contentItem is stable for its life;
                    # the destroyed path recreates the shell if it ever
                    # goes away.
                    shell.setParentItem(content)
                    shell.setParent(content)
                    shell.destroyed.connect(lambda: self._shell_destroyed("overlay"))
                    created = True
            if created: self.controlsChanged.emit()
        except Exception as error:
            Logger.log("w", "Moonraker Preview controls unavailable: %s", error)
        self.publish({"previewStageActive": self._cura.preview_active})

    def _shell_destroyed(self, which):
        if which == "panel":
            self._remove_panel_component()
            self._panel_shell = self._panel_card = None
        else:
            self._overlay_shell = self._overlay_card = None

    def _remove_panel_component(self):
        if self._panel_shell is None: return
        remover = getattr(self._application, "removeAdditionalComponent", None)
        if callable(remover):
            try:
                remover("saveButton", self._panel_shell)
                return
            except Exception: pass
        # Capability-guarded compatibility with Cura's add-only component API.
        components = getattr(self._application, "_additional_components", {})
        if isinstance(components, dict) and isinstance(components.get("saveButton"), list):
            components["saveButton"] = [item for item in components["saveButton"] if item is not self._panel_shell]
            signal = getattr(self._application, "additionalComponentsChanged", None)
            if signal is not None: signal.emit("saveButton")

    def close(self):
        self._closed = True
        try: self._cura.changed.disconnect(self.refresh)
        except Exception: pass
        signal = getattr(self._application, "activityChanged", None)
        if signal is not None:
            try: signal.disconnect(self._publish_all)
            except Exception: pass
        self._remove_panel_component()
        for control in self.controls:
            try:
                control.setProperty("visible", False)
                control.deleteLater()
            except RuntimeError: pass
        for shell in (self._panel_shell, self._overlay_shell):
            if shell is not None:
                try: shell.deleteLater()
                except RuntimeError: pass
        self._panel_shell = self._panel_card = None
        self._overlay_shell = self._overlay_card = None
