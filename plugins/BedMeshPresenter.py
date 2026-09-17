"""One owner of the active bed-mesh overlay, visibility and Preview controls."""
from __future__ import annotations

import time
from types import MappingProxyType

from PyQt6.QtCore import QObject, pyqtSignal


class BedMeshPresenter(QObject):
    changed = pyqtSignal()
    PREF_KEY = "moonrakerprintfollower/bed_mesh_visible"
    EXAGGERATION_PREF_KEY = "moonrakerprintfollower/bed_mesh_exaggeration"
    DEFAULT_EXAGGERATION = 20.0
    MAX_EXAGGERATION = 1000.0

    def __init__(self, application, cura, presentation, parent=None):
        super().__init__(parent)
        self._application, self._cura, self._presentation = application, cura, presentation
        self._preferences = application.getPreferences()
        self._preferences.addPreference(self.PREF_KEY, True)
        value = self._preferences.getValue(self.PREF_KEY)
        self._visible = value if isinstance(value, bool) else str(value).lower() not in {"0", "false", "no", "off"}
        # The "scale z-max" slider (the author's request): the Z
        # exaggeration of the Preview surface, 0 (flat) to 100.
        self._preferences.addPreference(self.EXAGGERATION_PREF_KEY, self.DEFAULT_EXAGGERATION)
        try:
            self._exaggeration = max(0.0, min(self.MAX_EXAGGERATION,
                float(self._preferences.getValue(self.EXAGGERATION_PREF_KEY))))
        except (TypeError, ValueError):
            self._exaggeration = self.DEFAULT_EXAGGERATION
        self._snapshot = {}
        self._fingerprint = None
        self._node = None
        self._closed = False
        self._thresholds = None  # the shared range-filter window (low, high)
        self._machine_width = 0.0
        self._machine_depth = 0.0
        self._machine_center_is_zero = False
        self._render_error_at = 0.0
        cura.changed.connect(self._render)
        presentation.controlsChanged.connect(self._publish)
        presentation.bedMeshVisibilityRequested.connect(self.set_visible)
        presentation.bedMeshExaggerationRequested.connect(self.set_exaggeration)
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
        self._clamp_thresholds()
        self._render(rebuild=True)
        self.changed.emit()

    def clear(self):
        self.update({})

    def set_visible(self, visible):
        self._visible = bool(visible)
        self._preferences.setValue(self.PREF_KEY, self._visible)
        self._render()
        self.changed.emit()

    def set_thresholds(self, low, high):
        # The heightmap range filter (the author's request): the
        # window the MONITOR model owns, mirrored here for the Preview
        # card and the scene node. A new mesh re-clamps a touched
        # window into the new range (see _clamp_thresholds).
        if not self._snapshot: return
        mesh_min = float(self._snapshot.get("minimum") or 0)
        mesh_max = float(self._snapshot.get("maximum") or 0)
        if mesh_max <= mesh_min: return
        low = min(max(float(low), mesh_min), mesh_max)
        high = min(max(float(high), mesh_min), mesh_max)
        if low > high: low, high = high, low
        if self._thresholds == (low, high): return
        self._thresholds = (low, high)
        self._render(rebuild=True)
        self.changed.emit()

    def set_exaggeration(self, scale):
        # The "scale z-max" slider (the author's request): 0 flattens
        # the surface, 1000 is the ceiling; the default 20 is the
        # historical fixed value.
        value = max(0.0, min(self.MAX_EXAGGERATION, float(scale)))
        if value == self._exaggeration: return
        self._exaggeration = value
        self._preferences.setValue(self.EXAGGERATION_PREF_KEY, value)
        self._render(rebuild=True)
        self.changed.emit()

    def _clamp_thresholds(self):
        if self._thresholds is None: return
        mesh_min = float(self._snapshot.get("minimum") or 0)
        mesh_max = float(self._snapshot.get("maximum") or 0)
        if not self._snapshot or mesh_max <= mesh_min:
            self._thresholds = None
            return
        low = min(max(self._thresholds[0], mesh_min), mesh_max)
        high = min(max(self._thresholds[1], mesh_min), mesh_max)
        if low > high: low, high = high, low
        self._thresholds = (low, high)

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
                    thresholds = self._thresholds or (None, None)
                    self._machine_width = float(stack.getProperty("machine_width", "value"))
                    self._machine_depth = float(stack.getProperty("machine_depth", "value"))
                    self._machine_center_is_zero = bool(stack.getProperty("machine_center_is_zero", "value"))
                    self._node.updateMesh(self._snapshot,
                        self._machine_width,
                        self._machine_depth,
                        self._machine_center_is_zero,
                        self._exaggeration,
                        low=thresholds[0], high=thresholds[1])
            elif self._node is not None:
                self._node.clear()
            if self._node is not None:
                self._node.setVisible(bool(self._snapshot) and self._visible and self._cura.preview_active)
                self._cura.controller.getScene().sceneChanged.emit(self._node)
        except Exception as error:
            # A missing renderer/API must not disable printer following —
            # but it must not vanish silently either: the
            # "Hide bed mesh does nothing" report needs the real cause
            # in Cura's log, throttled to once per 30 s.
            from UM.Logger import Logger
            now = time.monotonic()
            if now - self._render_error_at >= 30:
                self._render_error_at = now
                Logger.log("w", "Moonraker bed-mesh render failed: %s", error)
        self._publish()

    def _publish(self):
        snapshot = self._snapshot
        mesh_min = float(snapshot.get("minimum") or 0) if snapshot else 0.0
        mesh_max = float(snapshot.get("maximum") or 0) if snapshot else 0.0
        low, high = self._thresholds if self._thresholds else (mesh_min, mesh_max)
        self._presentation.publish({
            "bedMeshAvailable": bool(snapshot), "bedMeshVisible": self._visible,
            "bedMeshRangeText": f"{float(snapshot.get('range') or 0):.3f} mm range" if snapshot else "",
            "bedMeshMinimumText": f"{float(snapshot.get('minimum') or 0):+.3f} mm" if snapshot else "",
            "bedMeshMaximumText": f"{float(snapshot.get('maximum') or 0):+.3f} mm" if snapshot else "",
            # The numeric twins the range-filter slider binds to; the
            # thresholds mirror the model's shared window.
            "bedMeshMinimum": mesh_min, "bedMeshMaximum": mesh_max,
            "bedMeshThresholdLow": low, "bedMeshThresholdHigh": high,
            "bedMeshExaggeration": self._exaggeration,
        })

    def close(self):
        self.clear()
        self._closed = True
        for signal, callback in ((self._cura.changed, self._render),
                                 (self._presentation.controlsChanged, self._publish),
                                 (self._presentation.bedMeshVisibilityRequested, self.set_visible),
                                 (self._presentation.bedMeshExaggerationRequested, self.set_exaggeration)):
            try: signal.disconnect(callback)
            except Exception: pass
        if self._node is not None:
            try:
                with self._cura.decorating_scene(): self._node.setParent(None)
            except Exception: pass
        self._node = None

