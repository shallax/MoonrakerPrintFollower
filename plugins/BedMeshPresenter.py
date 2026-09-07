"""One owner of the active bed-mesh overlay, visibility and Preview controls."""
from __future__ import annotations

from types import MappingProxyType
from PyQt6.QtCore import QObject, pyqtSignal


class BedMeshPresenter(QObject):
    changed = pyqtSignal()
    PREF_KEY = "moonraker_print_follower/bed_mesh_visible"

    def __init__(self, application, cura, presentation, parent=None):
        super().__init__(parent)
        self._application, self._cura, self._presentation = application, cura, presentation
        self._preferences = application.getPreferences()
        self._preferences.addPreference(self.PREF_KEY, True)
        value = self._preferences.getValue(self.PREF_KEY)
        self._visible = value if isinstance(value, bool) else str(value).lower() not in {"0", "false", "no", "off"}
        self._snapshot = {}
        self._fingerprint = None
        self._node = None
        self._closed = False
        cura.changed.connect(self._render)
        presentation.controlsChanged.connect(self._publish)
        presentation.bedMeshVisibilityRequested.connect(self.set_visible)
        self._publish()

    @property
    def snapshot(self): return MappingProxyType(self._snapshot)
    @property
    def visible(self): return self._visible

    def update(self, snapshot):
        values = dict(snapshot or {})
        if "values" in values: values["values"] = tuple(values["values"])
        fingerprint = tuple((key, values.get(key)) for key in
            ("profile", "source", "rows", "columns", "xMin", "xMax", "yMin", "yMax", "values"))
        if fingerprint == self._fingerprint: return
        self._snapshot, self._fingerprint = values, fingerprint
        self._render(rebuild=True)
        self.changed.emit()

    def clear(self):
        self.update({})

    def set_visible(self, visible):
        self._visible = bool(visible)
        self._preferences.setValue(self.PREF_KEY, self._visible)
        self._render()
        self.changed.emit()

    def _render(self, *args, rebuild=False):
        if self._closed: return
        try:
            if self._snapshot:
                if self._node is None:
                    from .BedMeshSceneNode import BedMeshSceneNode
                    self._node = BedMeshSceneNode()
                    rebuild = True
                with self._cura.decorating_scene() as root:
                    if self._node.getParent() is not root:
                        self._node.setParent(root)
                        rebuild = True
                if rebuild:
                    stack = self._application.getGlobalContainerStack()
                    self._node.updateMesh(self._snapshot,
                        float(stack.getProperty("machine_width", "value")),
                        float(stack.getProperty("machine_depth", "value")),
                        bool(stack.getProperty("machine_center_is_zero", "value")), 20.0)
            elif self._node is not None:
                self._node.clear()
            if self._node is not None:
                self._node.setVisible(bool(self._snapshot) and self._visible and self._cura.preview_active)
                self._cura.controller.getScene().sceneChanged.emit(self._node)
        except Exception:
            # A missing renderer/API must not disable printer following.
            pass
        self._publish()

    def _publish(self):
        snapshot = self._snapshot
        self._presentation.publish({
            "bedMeshAvailable": bool(snapshot), "bedMeshVisible": self._visible,
            "bedMeshRangeText": f"{float(snapshot.get('range') or 0):.3f} mm range" if snapshot else "",
            "bedMeshMinimumText": f"{float(snapshot.get('minimum') or 0):+.3f} mm" if snapshot else "",
            "bedMeshMaximumText": f"{float(snapshot.get('maximum') or 0):+.3f} mm" if snapshot else "",
        })

    def close(self):
        self.clear()
        self._closed = True
        for signal, callback in ((self._cura.changed, self._render),
                                 (self._presentation.controlsChanged, self._publish),
                                 (self._presentation.bedMeshVisibilityRequested, self.set_visible)):
            try: signal.disconnect(callback)
            except Exception: pass
        if self._node is not None:
            try:
                with self._cura.decorating_scene(): self._node.setParent(None)
            except Exception: pass
        self._node = None

