"""Owns toolhead rendering, telemetry availability and native nozzle suppression."""
from __future__ import annotations

import math
import time
from contextlib import contextmanager

from PyQt6.QtCore import QObject, QTimer

from ..geometry.ToolheadGeometry import default_mesh, valid_tip


def estimated_position(view, root):
    """Public LayerData adapter using the same interpolation as SimulationPass."""
    if view is None: return None
    from UM.Math.Vector import Vector
    try:
        layer, path = int(view.getCurrentLayer()), float(view.getCurrentPath())
        if not math.isfinite(path) or path < 0: return None
        for node in root.getAllChildren():
            data = node.callDecoration("getLayerData")
            if data is None: continue
            selected = data.getLayer(layer)
            if selected is None: continue
            index = int(path)
            for polygon in selected.polygons:
                if index >= len(polygon.data):
                    index -= len(polygon.data)
                    continue
                ratio = path-math.floor(path)
                point = polygon.data[index]
                if ratio > .0001 and index+1 < len(polygon.data):
                    point = point*(1-ratio)+polygon.data[index+1]*ratio
                return Vector(*map(float, point)) + node.getWorldPosition()
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        pass
    return None


class ToolheadPresenter(QObject):
    def __init__(self, application, cura, client, binding, presentation, store, parent=None):
        super().__init__(parent)
        self._application, self._cura, self._client = application, cura, client
        self._binding, self._presentation, self._store = binding, presentation, store
        self._node = None
        self._model_key = None
        self._last_scene_state = None
        self._model_error = ""
        self._native = None
        self._native_parent = None
        self._updating = self._closed = False
        self._status = {}
        self._identity = binding.identity
        self._received = 0.0
        self._live_available = self._homing_available = False
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        getattr(client, "statusSnapshotReceived", client.statusReceived).connect(self._observe)
        client.statusAdmitted.connect(self._admitted)
        client.sessionInvalidated.connect(self._invalidate)
        client.connectionChanged.connect(self._connection)
        binding.changed.connect(self._rebind)
        cura.changed.connect(self.update)
        cura.positionChanged.connect(self.update)
        presentation.sceneLightingRequested.connect(self.update)
        presentation.toolheadVisibilityRequested.connect(self.update)
        presentation.reportedPositionRequested.connect(self.update)
        presentation.toolheadOpacityRequested.connect(self.update)

    def _observe(self, status):
        self._status = status
        self.update()

    def _admitted(self, patch, origin, stamp):
        self._received = stamp
        motion, toolhead = patch.get("motion_report") or {}, patch.get("toolhead") or {}
        if origin == "sync" or "live_position" in motion:
            self._live_available = "live_position" in motion
        if origin == "sync" or "homed_axes" in toolhead:
            self._homing_available = "homed_axes" in toolhead

    def _invalidate(self, *_args):
        self._status, self._received = {}, 0
        self._live_available = self._homing_available = False
        self.update()

    def _connection(self, connected, _reason):
        if not connected: self._invalidate()
        else: self.update()

    def _rebind(self, *_args):
        identity = self._binding.identity
        if identity != self._identity:
            self._identity = identity
            self._invalidate()
        else:
            self.update()

    def _reported(self, config):
        if not self._client.connected: return None, "Printer disconnected"
        stats = self._status.get("print_stats") or {}
        cadence = self._client.session.poll_policy.interval_ms("core", config.poll_interval_ms, stats.get("state", ""))
        # Moonraker's subscribed stream sends changes, so a stationary head
        # legitimately has no newer position frame. The socket owns liveness;
        # polling snapshots still need their admission-time freshness bound.
        socket = getattr(self._client.session, "socket", None)
        streaming = bool(socket is not None and socket.is_upgraded)
        if not self._received or (not streaming and time.monotonic()-self._received > max(2, cadence*.003)):
            return None, "Reported position is stale"
        if not self._homing_available: return None, "Printer homing status unavailable"
        homed = str((self._status.get("toolhead") or {}).get("homed_axes") or "").lower()
        if not all(axis in homed for axis in "xyz"): return None, "Home XYZ to show reported position"
        # True position is physical machine telemetry. Loaded G-code origins,
        # file provenance and follower attachment cannot move this marker.
        raw = (self._status.get("motion_report") or {}).get("live_position") if self._live_available else None
        try:
            position = tuple(float(value) for value in raw[:3]) if isinstance(raw, (list, tuple)) else None
            if position is not None and (len(position) != 3 or not all(math.isfinite(value) for value in position)):
                position = None
        except (TypeError, ValueError, IndexError):
            position = None
        if position is None: return None, "Reported position unavailable"
        try:
            stack = self._application.getGlobalContainerStack()
            width, depth = (float(stack.getProperty(key, "value")) for key in ("machine_width", "machine_depth"))
            centered = bool(stack.getProperty("machine_center_is_zero", "value"))
            x, y, z = position
            from UM.Math.Vector import Vector
            point = Vector(x if centered else x-width/2, z, -y if centered else depth/2-y)
            return point, f"Reported · X {x:.1f}  Y {y:.1f}  Z {z:.2f} mm"
        except (AttributeError, TypeError, ValueError):
            return None, "Printer dimensions unavailable"



    @contextmanager
    def _decorate(self):
        # SimulationView recalculates its path limit on childrenChanged and
        # moves the slider to that limit. Preserve all public handles while
        # editing our decoration; these changes are not user intervention.
        view = self._cura.view
        handles = []
        for getter, setter in (("getCurrentLayer", "setLayer"), ("getMinimumLayer", "setMinimumLayer"),
                               ("getCurrentPath", "setPath"), ("getMinimumPath", "setMinimumPath")):
            if view is not None and hasattr(view, getter) and hasattr(view, setter):
                handles.append((setter, getattr(view, getter)()))
        with self._cura.writing_preview(), self._cura.decorating_scene():
            try:
                yield
            finally:
                if self._cura.view is view:
                    for setter, value in handles:
                        getattr(view, setter)(value)

    def _suppress(self, override, root):
        if not override and self._native is None:
            self._cura.toolhead_override = False
            return
        view = self._cura.view
        try: native = view.getNozzleNode() if view is not None else None
        except AttributeError: native = None
        # Restore the previous view's native node before adopting a new owner.
        if self._native is not None and (not override or native is not self._native):
            if native is self._native and self._native.getParent() is None and self._native_parent is root:
                with self._decorate():
                    self._native.setParent(self._native_parent)
                    if self._cura.has_toolpath and view is not None: view.setActivity(True)
            self._native = self._native_parent = None
        self._cura.toolhead_override = override
        if override and native is not None:
            if native.getParent() is not None:
                self._native, self._native_parent = native, native.getParent()
                with self._decorate():
                    native.setParent(None)
                    # Root childrenChanged makes SimulationView inactive.
                    if self._cura.has_toolpath: view.setActivity(True)

    def update(self, *_args):
        if self._closed or self._updating: return
        self._updating = True
        try:
            config = self._binding.config
            reported = self._presentation.reported_position
            custom = bool(config.toolhead_model) and getattr(self._presentation, "toolhead_visible", True)
            override = (config.enabled and self._cura.preview_active
                        and (custom or reported))
            native_mesh = None
            if override and not custom:
                try:
                    native_mesh = self._cura.view.getNozzleNode().getMeshData()
                except AttributeError:
                    pass
                override = native_mesh is not None
            root = self._cura.controller.getScene().getRoot()
            self._suppress(override, root)
            if not override:
                if self._node is not None:
                    was_active = self._last_scene_state is not None
                    simulation_changed = self._node.set_simulation_active(False)
                    self._node.setVisible(False)
                    if was_active or simulation_changed:
                        self._cura.controller.getScene().sceneChanged.emit(self._node)
                self._last_scene_state = None
                status = "Estimated along toolpath" if config.enabled else "Following disabled in settings"
                if config.enabled and reported and self._cura.preview_active and native_mesh is None:
                    status = "Cura nozzle model unavailable"
                self._presentation.publish_toolhead(status)
                return
            if self._node is None:
                from .ToolheadSceneNode import ToolheadSceneNode
                self._node = ToolheadSceneNode()
            if self._node.getParent() is not root:
                with self._decorate():
                    self._node.setParent(root)
                    if self._cura.has_toolpath and self._cura.view is not None: self._cura.view.setActivity(True)
            key = (config.toolhead_model, tuple(config.toolhead_tip)) if custom else ("native", id(native_mesh))
            model_changed = key != self._model_key
            if model_changed:
                self._model_error = ""
                if custom:
                    try:
                        model = self._store.load(config.toolhead_model)
                    except (OSError, ValueError):
                        model = default_mesh()
                        self._model_error = " · saved model unavailable; default shown"
                    self._node.set_model(model, valid_tip(config.toolhead_tip) or model.automatic_tip)
                else:
                    self._node.set_native_model(native_mesh)
                self._model_key = key
            if reported:
                point, status = self._reported(config)
            else:
                point = estimated_position(self._cura.view, root)
                status = "Estimated along toolpath" if point is not None else "Estimated position unavailable"
            show = point is not None and config.show_toolhead_indicator and config.enabled
            opacity = self._presentation.toolhead_opacity if custom else 1.0
            self._node.set_opacity(opacity)
            attached = getattr(config, "toolhead_lights", []) if custom else []
            self._node.set_attached_lights(attached)
            effects = (self._presentation.light_bed, self._presentation.light_models) if custom else (False, False)
            lighting = custom and self._presentation.lighting_enabled
            self._node.set_lighting_enabled(lighting)
            self._node.set_scene_lighting(*effects)
            self._node.set_scene(self._cura.view, root)
            # Detached camera navigation still benefits from stable native
            # buffers. Keep observing the public view while the head is shown,
            # including the layer transition when a G-code load completes.
            simulation_changed = self._node.set_simulation_active(bool(show and custom)) or (model_changed and not custom)
            stack = self._application.getGlobalContainerStack()
            dimensions = None
            try:
                dimensions = tuple(float(stack.getProperty("machine_" + axis, "value")) for axis in ("width", "depth", "height"))
                if custom and all(math.isfinite(v) and v > 0 for v in dimensions): self._node.set_lights(*dimensions)
            except (AttributeError, TypeError, ValueError):
                pass
            scene_state = (key, show, (point.x, point.y, point.z) if show else None, id(root), opacity, dimensions, repr(attached), effects, lighting)
            scene_changed = scene_state != self._last_scene_state
            if scene_changed:
                if show: self._node.set_render_position(point)
                self._node.setVisible(show)
                self._last_scene_state = scene_state
            if not config.show_toolhead_indicator: status = "Toolhead indicator disabled in settings"
            failure = getattr(self._node, "render_failure", lambda: "")()
            if failure: status = failure
            self._presentation.publish_toolhead(status + self._model_error)
            if scene_changed or simulation_changed:
                window = getattr(self._application, "getMainWindow", lambda: None)()
                if window is not None and not simulation_changed and not self._node.requires_full_render():
                    window.update()
                else:
                    self._cura.controller.getScene().sceneChanged.emit(self._node)
        finally:
            self._updating = False

    def close(self):
        self._timer.stop()
        self._closed = True
        if self._node is not None:
            self._node.close()
            with self._decorate(): self._node.setParent(None)
        self._suppress(False, self._cura.controller.getScene().getRoot())
