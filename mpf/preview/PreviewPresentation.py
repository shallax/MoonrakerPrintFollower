"""Cura/QML presentation adapter. Inputs are values; outputs are user intents."""
from __future__ import annotations


import math
import time

from PyQt6.QtCore import QMetaObject, QObject, QPointF, QTimer, pyqtSignal
from UM.Logger import Logger

from ..resources.PluginPaths import plugin_path
from .ObjectNameProjection import footprint_hits, place_banners
from .CameraProjection import CameraProjection
from .ViewportHover import ViewportHover


def _definition_center(row):
    center = row.get("center")
    if center is not None:
        return center[:2]
    polygon = row.get("polygon") or ()
    if not polygon:
        return None
    return ((min(point[0] for point in polygon) + max(point[0] for point in polygon)) / 2.0,
            (min(point[1] for point in polygon) + max(point[1] for point in polygon)) / 2.0)


class PreviewPresentation(QObject):
    loadRequested = pyqtSignal()
    attachmentRequested = pyqtSignal()
    improveEtaRequested = pyqtSignal()
    pauseAtLayerRequested = pyqtSignal(int)
    printPauseRequested = pyqtSignal()
    removePauseRequested = pyqtSignal(int)
    clearPausesRequested = pyqtSignal()
    replaceConfirmed = pyqtSignal()
    replaceCancelled = pyqtSignal()
    bedMeshVisibilityRequested = pyqtSignal(bool)
    bedMeshThresholdsRequested = pyqtSignal(float, float)
    bedMeshExaggerationRequested = pyqtSignal(float)
    controlsChanged = pyqtSignal()
    sceneLightingRequested = pyqtSignal()
    toolheadVisibilityRequested = pyqtSignal(bool)
    reportedPositionRequested = pyqtSignal(bool)
    toolheadOpacityRequested = pyqtSignal(float)

    def __init__(self, application, cura, parent=None, persistence=None):
        super().__init__(parent)
        self._application, self._cura = application, cura
        self._panel_shell = None
        self._overlay_shell = None
        self._panel_card = None
        self._overlay_card = None
        self._tags_shell = None
        self._toolhead_setup_dialog = None
        self._persistence = persistence
        saved = persistence.state_global_document() if persistence is not None else {}
        self._card_expanded = bool(saved.get("previewCardExpanded", True))
        self._tags_enabled = bool(saved.get("previewObjectTagsEnabled", False))
        self._hover_only = bool(saved.get("previewObjectTagsHoverOnly", False))
        self.lighting_enabled = bool(saved.get("previewLightingEnabled", True))
        self.light_bed = bool(saved.get("previewLightBed", True))
        self.light_models = bool(saved.get("previewLightModels", True))
        self.toolhead_visible = bool(saved.get("previewToolheadVisible", True))
        self.reported_position = bool(saved.get("previewReportedToolhead", False))
        self.toolhead_opacity = self._opacity_value(saved.get("previewToolheadOpacity", 1.0))
        self._toolhead_status = "Estimated along toolpath"
        self._camera_projection = CameraProjection()
        self._viewport_hover = ViewportHover()
        self._hover_point = None
        self._hovered_node = 0
        self._hovered_names = []
        self._picked_point = None
        self._picked_at = 0.0
        self._footprint_picked_point = None
        self._footprint_picked_at = 0.0
        self._projected_rows = []
        self._pick_available = False
        self._tag_scene = self._tag_scene_signal = None
        self._tag_objects_key = self._tag_objects = None
        self._tag_timer = QTimer(self)
        self._tag_timer.setInterval(100)
        self._tag_timer.timeout.connect(self._update_tags)
        self._values = {}
        self._closed = False
        self._booted = False
        self._verdicts = None
        cura.changed.connect(self.refresh)
        # Cura's action panel is visible exactly while the platform is
        # active — its own property, its own signal. Recompute the
        # host gates on the same edge the panel itself flips on, so the
        # two cards can never both be up (the duplicate-card report).
        signal = getattr(application, "activityChanged", None)
        if signal is not None:
            signal.connect(self._publish_all)
        # The cards are never created during plugin load: Cura's QML
        # modules are not registered until the boot completes, and a
        # card created in the storm is the session's first QML import —
        # when it loses that race on Windows the failed type
        # registrations cascade into Cura's own dialogs (the
        # alternating Loading-UI stall). Build the hosts on the
        # boot-complete edge instead; hosts that cannot signal one
        # (the test doubles) keep the immediate refresh.
        finished = getattr(application, "initializationFinished", None)
        # The boot-ready predicate (the PrinterBinding contract): a
        # host with no signal, or a LATE construction whose signal
        # fired before the plugin existed, is ready NOW — waiting for
        # an edge that will never come again would keep the Preview
        # hosts unborn. Ordinary startup still defers to the
        # boot-complete edge (the Windows registration race).
        ready_now = finished is None or bool(getattr(application, "started", False))
        if ready_now:
            self._booted = True
            self.refresh()
        else:
            finished.connect(self._on_boot_finished)

    def _on_boot_finished(self):
        self._booted = True
        self.refresh()

    @property
    def controls(self): return tuple(control for control in (self._panel_card, self._overlay_card) if control is not None)

    def publish(self, values):
        if any(name in values and values[name] != self._values.get(name) for name in (
                "objectTagDefinitions", "objectTagMetrics", "objectTagHeight", "objectTagCurrentObject")):
            self._invalidate_tag_objects()
        self._values.update(values)
        self._publish_all()
        self._update_tags()

    def publish_pause_verdicts(self, can_pause, can_resume, pause_reason, resume_reason,
                               pause_detail="", resume_detail=""):
        """The pause/resume grey-out's single authority (the debt
        pack's two-clock unification): the monitor model's verdicts,
        pushed to every card — the strip's enable and reasons read
        these instead of the preview block's own copies."""
        # Kept for the boot-deferred cards: verdicts that arrive before
        # the hosts exist replay once they are created.
        self._verdicts = (can_pause, can_resume, pause_reason, resume_reason,
                          pause_detail, resume_detail)
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
            try: control.setProperty("cardExpanded", self._card_expanded)
            except RuntimeError: pass
            for name, value in self._values.items():
                if name in ("gateVisible", "objectTagSourceActive", "objectTagDefinitions",
                            "objectTagHeight", "objectTagCurrentObject", "objectTagMetrics"): continue
                if name == "replacePromptVisible":
                    # The prompt is a Popup, and a Popup renders in the
                    # WINDOW's overlay: it escapes whatever hidden
                    # ancestor holds its card, so both hostings would
                    # put one up at once (measured: two identical
                    # dialogs, overlapping, on the same screen). Only
                    # the card the model considers current may ask —
                    # the same term the gates above use, one level in.
                    value = bool(value) and (not panel_up if overlay else panel_up)
                try: control.setProperty(name, value)
                except RuntimeError: pass
        if self._tags_shell is not None:
            active = configured and preview
            self._tags_shell.setProperty("dockVisible", active)
            self._tags_shell.setProperty("tagsEnabled", self._tags_enabled)
            self._tags_shell.setProperty("hoverOnly", self._hover_only)
            self._tags_shell.setProperty("toolheadVisible", self.toolhead_visible)
            self._tags_shell.setProperty("customToolheadAvailable", bool(self._values.get("customToolheadAvailable")))
            self._tags_shell.setProperty("lightingEnabled", self.lighting_enabled)
            self._tags_shell.setProperty("lightBed", self.light_bed)
            self._tags_shell.setProperty("lightModels", self.light_models)
            for name, value in self._values.items():
                if name.startswith("bedMesh"):
                    self._tags_shell.setProperty(name, value)
            self._tags_shell.setProperty("reportedPosition", self.reported_position)
            self._tags_shell.setProperty("estimatedPositionAvailable", bool(self._cura.has_toolpath))
            self._tags_shell.setProperty("toolheadOpacity", self.toolhead_opacity)
            self._tags_shell.setProperty("toolheadStatus", self._toolhead_status)
            if active and not self._tag_timer.isActive():
                self._tag_timer.start()
            elif not active:
                self._tag_timer.stop()

    def publish_toolhead(self, status):
        self._toolhead_status = str(status)
        if self._tags_shell is not None:
            self._tags_shell.setProperty("toolheadStatus", self._toolhead_status)

    def _set_toolhead_visible(self, enabled):
        self.toolhead_visible = bool(enabled)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewToolheadVisible": self.toolhead_visible})
        self.toolheadVisibilityRequested.emit(self.toolhead_visible)
        self._publish_all()

    def _open_toolhead_settings(self):
        action = self._application.getMachineActionManager().getMachineAction("MoonrakerPrintFollowerConfigureAction")
        if action is None: return
        if self._toolhead_setup_dialog is None:
            self._toolhead_setup_dialog = self._application.createQmlComponent(
                plugin_path("settings", "ToolheadSetupDialog.qml"), {"configurationManager": action})
        if self._toolhead_setup_dialog is not None:
            QMetaObject.invokeMethod(self._toolhead_setup_dialog, "openToolheadSettings")

    def _set_lighting_enabled(self, enabled):
        self.lighting_enabled = bool(enabled)
        self._set_scene_lighting("previewLightingEnabled", self.lighting_enabled)

    def _set_light_bed(self, enabled):
        self.light_bed = bool(enabled)
        self._set_scene_lighting("previewLightBed", self.light_bed)

    def _set_light_models(self, enabled):
        self.light_models = bool(enabled)
        self._set_scene_lighting("previewLightModels", self.light_models)

    def _set_scene_lighting(self, key, value):
        if self._persistence is not None:
            self._persistence.merge_state_global({key: value})
        self.sceneLightingRequested.emit()
        self._publish_all()

    def _set_reported_position(self, enabled):
        self.reported_position = bool(enabled)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewReportedToolhead": self.reported_position})
        self.reportedPositionRequested.emit(self.reported_position)
        self._publish_all()

    @staticmethod
    def _opacity_value(value):
        try:
            value = float(value)
            return max(0.0, min(1.0, value)) if math.isfinite(value) else 1.0
        except (TypeError, ValueError):
            return 1.0

    def _set_toolhead_opacity(self, value):
        self.toolhead_opacity = self._opacity_value(value)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewToolheadOpacity": self.toolhead_opacity})
        self.toolheadOpacityRequested.emit(self.toolhead_opacity)
        self._publish_all()

    def _set_tags_enabled(self, enabled):
        self._tags_enabled = bool(enabled)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewObjectTagsEnabled": self._tags_enabled})
        self._publish_all()
        self._update_tags()

    def _set_card_expanded(self, expanded):
        self._card_expanded = bool(expanded)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewCardExpanded": self._card_expanded})
        for control in self.controls:
            try: control.setProperty("cardExpanded", self._card_expanded)
            except RuntimeError: pass

    def _set_hover_only(self, enabled):
        self._hover_only = bool(enabled)
        if self._persistence is not None:
            self._persistence.merge_state_global({"previewObjectTagsHoverOnly": self._hover_only})
        self._publish_all()
        self._update_tags()

    def _pointer_moved(self, x, y):
        self._hover_point = (float(x), float(y)) if x >= 0 and y >= 0 else None

    def _invalidate_tag_objects(self, *_args):
        self._tag_objects_key = self._tag_objects = None

    def _scene_objects(self):
        scene = self._cura.controller.getScene()
        if scene is not self._tag_scene:
            if self._tag_scene_signal is not None:
                try: self._tag_scene_signal.disconnect(self._invalidate_tag_objects)
                except (RuntimeError, TypeError): pass
            self._tag_scene, self._tag_scene_signal = scene, getattr(scene, "sceneChanged", None)
            if self._tag_scene_signal is not None:
                self._tag_scene_signal.connect(self._invalidate_tag_objects)
            self._invalidate_tag_objects()
        # Without host invalidation, keep the uncached behaviour rather than
        # risk stale anchors after a scene edit. Camera motion still projects
        # the retained world anchors normally; hover is evaluated separately.
        if self._tag_scene_signal is None:
            return self._build_scene_objects()
        stack = self._application.getGlobalContainerStack()
        selected, heights = self._cura.selected_layer, self._cura.heights
        height = heights[selected] if selected is not None and 0 <= selected < len(heights) else None
        key = (id(scene.getRoot()), id(stack), selected, height,
               stack.getProperty("machine_width", "value"), stack.getProperty("machine_depth", "value"),
               stack.getProperty("machine_center_is_zero", "value"))
        if key != self._tag_objects_key or self._tag_objects is None:
            self._tag_objects = self._build_scene_objects()
            self._tag_objects_key = key
        return self._tag_objects

    def _build_scene_objects(self):
        from UM.Math.Vector import Vector
        scene = self._cura.controller.getScene()
        root = scene.getRoot()
        definitions = self._values.get("objectTagDefinitions") or []
        centers = [(row, center) for row in definitions if (center := _definition_center(row)) is not None]
        metrics = self._values.get("objectTagMetrics") or {}
        stack = self._application.getGlobalContainerStack()
        width = float(stack.getProperty("machine_width", "value"))
        depth = float(stack.getProperty("machine_depth", "value"))
        centred = bool(stack.getProperty("machine_center_is_zero", "value"))
        rows = []
        for node in root.getAllChildren():
            try:
                if not node.isSelectable() or node.getMeshData() is None or not node.isVisible():
                    continue
                box = node.getBoundingBox()
                if box is None or not box.isValid():
                    continue
                center = box.center
                px = center.x if centred else center.x + width / 2.0
                py = -center.z if centred else depth / 2.0 - center.z
                nearest = min(centers,
                              key=lambda pair: math.hypot(px - pair[1][0], py - pair[1][1]),
                              default=None)
                matched = nearest is not None and math.hypot(px - nearest[1][0], py - nearest[1][1]) < 12.0
                if matched:
                    name = nearest[0]["name"]
                    status_name = nearest[0].get("statusName", name)
                else:
                    name = str(node.getName() or "")
                    status_name = name
                if not name:
                    continue
                metric = metrics.get(name) or {}
                current_height = self._values.get("objectTagHeight")
                progress = metric.get("progress")
                if (progress is None and current_height is not None and box.top > box.bottom
                        and status_name == self._values.get("objectTagCurrentObject")):
                    progress = min(1.0, max(0.0, (float(current_height) - box.bottom) / box.height))
                if matched and nearest[0].get("excluded"):
                    progress = None
                rows.append({"name": name, "nodeId": id(node), "position": Vector(center.x, box.top, center.z),
                             "progress": progress, "deadline": metric.get("deadline") if progress is not None else None})
            except (AttributeError, RuntimeError, TypeError, ValueError):
                continue
        if rows:
            return rows, True
        current_height = self._values.get("objectTagHeight")
        selected = self._cura.selected_layer
        heights = self._cura.heights
        if selected is not None and selected < len(heights) and math.isfinite(heights[selected]):
            current_height = heights[selected]
        z = max(0.0, float(current_height or 0.0))
        for row, center in centers:
            x, y = center[:2]
            scene_x = x if centred else x - width / 2.0
            scene_z = -y if centred else depth / 2.0 - y
            metric = metrics.get(row["name"]) or {}
            progress = None if row.get("excluded") else metric.get("progress")
            top = metric.get("top")
            anchor_z = min(z, float(top)) if top is not None else z
            polygon = row.get("polygon") or ()
            bounds = row.get("bounds")
            if len(polygon) < 3 and bounds is not None:
                xmin, ymin, xmax, ymax = bounds
                polygon = ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))
            footprint = [(px if centred else px - width / 2.0,
                          -py if centred else depth / 2.0 - py)
                         for px, py in polygon]
            rows.append({"name": row["name"], "nodeId": 0,
                         "position": Vector(scene_x, anchor_z, scene_z), "progress": progress,
                         "deadline": metric.get("deadline") if progress is not None else None,
                         "footprint": footprint, "footprintHeight": anchor_z})
        return rows, False

    def _pick_hover(self, window, scene):
        if self._hover_point is None or not self._pick_available:
            self._hovered_node = 0
            self._picked_point = None
            return 0
        now = time.monotonic()
        if self._hover_point == self._picked_point and now - self._picked_at < 0.18:
            return self._hovered_node
        try:
            x, y = self._hover_point
            render_pass = self._application.getRenderer().getRenderPass("selection")
            if render_pass is None:
                return 0
            node_id = render_pass.getIdAtPosition(2.0 * x / window.width() - 1.0,
                                                   2.0 * y / window.height() - 1.0)
            node = scene.findObject(node_id) if node_id else None
            self._picked_point = self._hover_point
            self._picked_at = now
            self._hovered_node = id(node) if node is not None else 0
            return self._hovered_node
        except (AttributeError, RuntimeError, TypeError, ValueError, ZeroDivisionError):
            return 0

    def _pick_footprints(self, window, camera, objects):
        if self._hover_point is None:
            self._hovered_names = []
            self._footprint_picked_point = None
            return []
        now = time.monotonic()
        if self._hover_point == self._footprint_picked_point and now - self._footprint_picked_at < 0.18:
            return self._hovered_names
        try:
            x, y = self._hover_point
            ray = self._camera_projection.ray(camera, x, y, window.width(), window.height())
            origin = (ray.origin.x, ray.origin.y, ray.origin.z)
            direction = (ray.direction.x, ray.direction.y, ray.direction.z)
            names = footprint_hits(objects, origin, direction)
            self._footprint_picked_point = self._hover_point
            self._footprint_picked_at = now
            self._hovered_names = names
            return names
        except (AttributeError, RuntimeError, TypeError, ValueError, ZeroDivisionError):
            return []

    def _update_tags(self):
        shell = self._tags_shell
        if self._closed or shell is None:
            return
        try:
            card = self._panel_card if self._cura_panel_visible() else self._overlay_card
            card_left = card.mapToScene(QPointF(0, 0)).x() if card is not None else -1
            card_bottom = card.mapToScene(QPointF(0, card.height())).y() if card is not None else -1
            shell.setProperty("previewCardLeft", card_left)
            shell.setProperty("previewCardBottom", card_bottom)
            window = self._application.getMainWindow()
            camera = self._cura.controller.getScene().getActiveCamera()
            supported = (window is not None and camera is not None
                         and callable(getattr(camera, "projectToViewport", None))
                         and camera.getViewportWidth() > 0 and camera.getViewportHeight() > 0)
            shell.setProperty("projectionAvailable", bool(supported))
            gate_state = (bool(supported), bool(self._cura.preview_active), bool(self._tags_enabled),
                          bool(self._cura.has_toolpath), bool(self._values.get("objectTagSourceActive")))
            if gate_state != getattr(self, "_last_tag_gate_state", None):
                self._last_tag_gate_state = gate_state
                Logger.log("i", "Moonraker banner projection: camera=%s preview=%s enabled=%s toolpath=%s source=%s", *gate_state)
            if not all(gate_state):
                if self._projected_rows:
                    self._projected_rows = []
                    shell.setProperty("tagRows", [])
                shell.setProperty("pickAvailable", False)
                shell.setProperty("pickMode", "none")
                shell.setProperty("hoveredNode", 0)
                shell.setProperty("hoveredNames", [])
                return
            scene = self._cura.controller.getScene()
            objects, pickable = self._scene_objects()
            ratio = float(window.devicePixelRatio())
            rect = window.viewportRect
            center_x = window.width() * (rect.x() + rect.width() / 2.0)
            center_y = window.height() * (rect.y() + rect.height() / 2.0)
            camera_project = self._camera_projection.projector(camera)
            def project(position):
                px, py = camera_project(position)
                return center_x + px / ratio, center_y - py / ratio
            self._pick_available = pickable and self._application.getRenderer().getRenderPass("selection") is not None
            footprint_available = not self._pick_available and any(item.get("footprint") for item in objects)
            dock = shell.findChild(QObject, "moonrakerPreviewObjectTagsDock")
            blocked = self._viewport_hover.blocked(window, self._hover_point, (*self.controls, dock))
            if blocked:
                self._hovered_node, self._hovered_names = 0, []
                self._picked_point = self._footprint_picked_point = None
            hovered = self._pick_hover(window, scene) if self._pick_available and not blocked else 0
            hovered_names = self._pick_footprints(window, camera, objects) if footprint_available and not blocked else []
            rows = place_banners(objects, project, window.width(), window.height(),
                                 hover_only=self._hover_only and (self._pick_available or footprint_available),
                                 hovered_node=hovered, hovered_names=hovered_names)
            result_state = (len(objects), len(rows), bool(self._hover_only), bool(self._pick_available),
                            bool(footprint_available))
            if result_state != getattr(self, "_last_tag_result_state", None):
                self._last_tag_result_state = result_state
                Logger.log("i", "Moonraker banner layout: objects=%s rows=%s hoverOnly=%s exactPick=%s footprintPick=%s", *result_state)
            if rows != self._projected_rows:
                self._projected_rows = rows
                shell.setProperty("tagRows", rows)
            shell.setProperty("pickAvailable", self._pick_available or footprint_available)
            shell.setProperty("pickMode", "exact" if self._pick_available else "footprint" if footprint_available else "none")
            shell.setProperty("hoveredNode", hovered)
            shell.setProperty("hoveredNames", hovered_names)
        except (AttributeError, RuntimeError, TypeError, ValueError, ZeroDivisionError) as error:
            Logger.log("d", "Moonraker object tags unavailable: %s", error)
            self._projected_rows = []
            shell.setProperty("tagRows", [])
            shell.setProperty("pickAvailable", False)
            shell.setProperty("pickMode", "none")
            shell.setProperty("hoveredNames", [])

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
            ("cardExpandedRequested", self._set_card_expanded),
            ("improveEtaRequested", self.improveEtaRequested.emit),
            ("pauseAtLayerRequested", self.pauseAtLayerRequested.emit),
            ("printPauseRequested", self.printPauseRequested.emit),
            ("removePauseAtLayerRequested", self.removePauseRequested.emit),
            ("clearPauseAtLayersRequested", self.clearPausesRequested.emit),
            ("replaceConfirmed", self.replaceConfirmed.emit),
            ("replaceCancelled", self.replaceCancelled.emit),
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
            if self._booted and self._panel_shell is None:
                shell = self._application.createQmlComponent(plugin_path("preview", "MoonrakerPreviewCardPanelHost.qml"))
                if shell is not None:
                    card = self._inner_card(shell)
                    self._panel_shell = shell
                    self._panel_card = card
                    if card is not None:
                        self._wire(card)
                    self._application.addAdditionalComponent("saveButton", shell)
                    shell.destroyed.connect(lambda: self._shell_destroyed("panel"))
                    created = True
            if self._booted and self._overlay_shell is None and content is not None:
                shell = self._application.createQmlComponent(plugin_path("preview", "MoonrakerPreviewCardOverlayHost.qml"))
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
            if self._booted and self._tags_shell is None and content is not None:
                shell = self._application.createQmlComponent(plugin_path("preview", "PreviewObjectTagsHost.qml"))
                if shell is not None:
                    self._tags_shell = shell
                    self._projected_rows = []
                    shell.setParentItem(content)
                    shell.setParent(content)
                    shell.tagsEnabledRequested.connect(self._set_tags_enabled)
                    shell.lightingEnabledRequested.connect(self._set_lighting_enabled)
                    shell.lightBedRequested.connect(self._set_light_bed)
                    shell.lightModelsRequested.connect(self._set_light_models)
                    self._wire(shell)
                    shell.toolheadVisibilityRequested.connect(self._set_toolhead_visible)
                    shell.toolheadSetupRequested.connect(self._open_toolhead_settings)
                    shell.reportedPositionRequested.connect(self._set_reported_position)
                    shell.toolheadOpacityRequested.connect(self._set_toolhead_opacity)
                    shell.hoverOnlyRequested.connect(self._set_hover_only)
                    shell.pointerMoved.connect(self._pointer_moved)
                    shell.destroyed.connect(lambda: setattr(self, "_tags_shell", None))
                    created = True
            if created:
                if self._verdicts is not None:
                    self.publish_pause_verdicts(*self._verdicts)
                self.controlsChanged.emit()
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
        if self._tag_scene_signal is not None:
            try: self._tag_scene_signal.disconnect(self._invalidate_tag_objects)
            except (RuntimeError, TypeError): pass
        self._tag_scene = self._tag_scene_signal = None
        self._invalidate_tag_objects()
        self._closed = True
        if self._toolhead_setup_dialog is not None:
            self._toolhead_setup_dialog.close()
            self._toolhead_setup_dialog.deleteLater()
            self._toolhead_setup_dialog = None
        self._tag_timer.stop()
        finished = getattr(self._application, "initializationFinished", None)
        if finished is not None:
            try: finished.disconnect(self._on_boot_finished)
            except Exception: pass
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
        for shell in (self._panel_shell, self._overlay_shell, self._tags_shell):
            if shell is not None:
                try: shell.deleteLater()
                except RuntimeError: pass
        self._panel_shell = self._panel_card = None
        self._overlay_shell = self._overlay_card = None
        self._tags_shell = None
