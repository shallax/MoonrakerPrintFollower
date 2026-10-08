"""Reported positions retain verified toolpath provenance and native lifecycle."""
import time
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.qt_runtime_support import QT_AVAILABLE, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt required")
class ToolheadPresenterTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.module = self.qt.load("ToolheadPresenter")
        # Methods run on the real QObject; only Cura's rendering surface is fake.
        from PyQt6.QtCore import QObject
        self.presenter = self.module.ToolheadPresenter.__new__(self.module.ToolheadPresenter)
        QObject.__init__(self.presenter)
        self.presenter._cura = SimpleNamespace(has_toolpath=True, plugin_loaded_path="/loaded/A.gcode")
        self.presenter._status = {"print_stats": {"filename": "A.gcode", "state": "printing"}}
        self.presenter.update = Mock()




    def test_new_view_does_not_restore_obsolete_native_into_shared_root(self):
        root = object()
        old = SimpleNamespace(getParent=lambda: None, setParent=Mock())
        new = SimpleNamespace(getParent=lambda: None, setParent=Mock())
        self.presenter._native, self.presenter._native_parent = old, root
        self.presenter._cura.view = SimpleNamespace(getNozzleNode=lambda: new)
        self.presenter._cura.decorating_scene = nullcontext
        self.presenter._cura.writing_preview = nullcontext
        self.presenter._suppress(True, root)
        old.setParent.assert_not_called()
        self.assertTrue(self.presenter._cura.toolhead_override)


    def test_stationary_diff_retains_live_and_homing_but_sync_requires_both(self):
        self.presenter._admitted({"motion_report": {"live_position": [1,2,3]}, "toolhead": {"homed_axes": "xyz"}}, "sync", 10)
        self.presenter._admitted({"print_stats": {"state": "printing"}}, "push", 11)
        self.assertTrue(self.presenter._live_available)
        self.assertTrue(self.presenter._homing_available)
        self.presenter._admitted({}, "sync", 12)
        self.assertFalse(self.presenter._live_available)
        self.assertFalse(self.presenter._homing_available)

    def test_reported_uses_physical_live_position_without_g92_offsets(self):
        self.presenter._client = SimpleNamespace(connected=True, session=SimpleNamespace(poll_policy=SimpleNamespace(interval_ms=lambda *args: 750)))
        self.presenter._received = time.monotonic()
        self.presenter._live_available = self.presenter._homing_available = True
        self.presenter._status.update(motion_report={"live_position": [110,120,8,0]},
            gcode_move={"homing_origin": [10,20,3,0]}, toolhead={"homed_axes": "xyz"})
        self.presenter._application = SimpleNamespace(getGlobalContainerStack=lambda: SimpleNamespace(getProperty=lambda key, role: {"machine_width": 200, "machine_depth": 200, "machine_height": 200, "machine_center_is_zero": False}[key]))
        import sys
        with patch.dict(sys.modules, {"UM.Math.Vector": SimpleNamespace(Vector=lambda *point: point)}):
            point, caption = self.presenter._reported(SimpleNamespace(poll_interval_ms=750))
        self.assertEqual(point, (10, 8, -20))
        self.assertIn("Reported", caption)
        self.presenter._received -= 3
        self.assertIn("stale", self.presenter._reported(SimpleNamespace(poll_interval_ms=750))[1])
        self.presenter._client.session.socket = SimpleNamespace(is_upgraded=True)
        with patch.dict(sys.modules, {"UM.Math.Vector": SimpleNamespace(Vector=lambda *point: point)}):
            self.assertEqual(self.presenter._reported(SimpleNamespace(poll_interval_ms=750))[0], (10, 8, -20))

    def test_releasing_native_node_restores_activity(self):
        root = object()
        native = SimpleNamespace(getParent=lambda: None, setParent=Mock())
        view = SimpleNamespace(getNozzleNode=lambda: native, setActivity=Mock())
        self.presenter._native, self.presenter._native_parent = native, root
        self.presenter._cura.view = view
        self.presenter._cura.decorating_scene = nullcontext
        self.presenter._cura.writing_preview = nullcontext
        self.presenter._suppress(False, root)
        native.setParent.assert_called_once_with(root)
        view.setActivity.assert_called_once_with(True)
        self.assertFalse(self.presenter._cura.toolhead_override)

    def test_scene_decoration_preserves_slider_without_manual_override(self):
        from contextlib import contextmanager
        values = {"layer": 2, "minimum_layer": 0, "path": 1.25, "minimum_path": 0}
        original = values.copy()
        writing = [False]
        interventions = []
        view = SimpleNamespace()
        for key, getter, setter in (("layer", "getCurrentLayer", "setLayer"),
                                    ("minimum_layer", "getMinimumLayer", "setMinimumLayer"),
                                    ("path", "getCurrentPath", "setPath"),
                                    ("minimum_path", "getMinimumPath", "setMinimumPath")):
            setattr(view, getter, lambda key=key: values[key])
            def change(value, key=key):
                values[key] = value
                if not writing[0]: interventions.append(key)
            setattr(view, setter, change)
        @contextmanager
        def own_write():
            writing[0] = True
            try: yield
            finally: writing[0] = False
        self.presenter._cura.view = view
        self.presenter._cura.writing_preview = own_write
        self.presenter._cura.decorating_scene = nullcontext
        with self.presenter._decorate():
            view.setLayer(39)
            view.setMinimumLayer(3)
            view.setPath(6)
            view.setMinimumPath(2)
        self.assertEqual(values, original)
        self.assertEqual(interventions, [])
        with self.presenter._decorate():
            self.presenter._cura.view = None
            view.setPath(9)
        self.assertEqual(values["path"], 9)  # obsolete view is never restored

    def test_disabled_following_releases_native_override(self):
        self.presenter._closed = self.presenter._updating = False
        self.presenter._binding = SimpleNamespace(config=SimpleNamespace(enabled=False, toolhead_model="custom"))
        self.presenter._presentation = SimpleNamespace(reported_position=True, publish_toolhead=Mock())
        self.presenter._cura.preview_active = True
        self.presenter._cura.controller = SimpleNamespace(getScene=lambda: SimpleNamespace(getRoot=lambda: object()))
        self.presenter._node = None
        self.presenter._suppress = Mock()
        self.module.ToolheadPresenter.update(self.presenter)
        self.assertFalse(self.presenter._suppress.call_args.args[0])
        self.assertIn("disabled", self.presenter._presentation.publish_toolhead.call_args.args[0])


@unittest.skipUnless(QT_AVAILABLE, "Qt required")
class ToolheadPresenterLifecycleTests(unittest.TestCase):
    def setUp(self):
        import sys
        from PyQt6.QtCore import QObject, pyqtSignal
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.module = self.qt.load("ToolheadPresenter")
        class Point:
            def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z
        self.vector_patch = patch.dict(sys.modules, {"UM.Math.Vector": SimpleNamespace(Vector=Point)})
        self.vector_patch.start()
        self.addCleanup(self.vector_patch.stop)
        class Node:
            def __init__(self): self.parent = None; self.visible = False; self.models = []; self.point = None; self.mesh = object()
            def getParent(self): return self.parent
            def setParent(self, value): self.parent = value
            def setVisible(self, value): self.visible = value
            def visible_for_render(self): return self.visible
            def set_render_position(self, value): self.point = value
            def requires_full_render(self): return False
            def set_model(self, model, tip): self.models.append((model, tip))
            def getMeshData(self): return self.mesh
            def set_native_model(self, mesh): self.native_mesh = mesh; self.simulation_active = False
            def set_attached_lights(self, value): self.attached = value
            def set_surface_detail(self, value): self.detail = value
            def set_material_finishes(self, value): self.finishes = value
            def set_local_appearance(self, materials, body_finishes, face_finishes): self.local_appearance = (materials, body_finishes, face_finishes)
            def set_opacity_overrides(self, bodies, faces): self.opacity_overrides = (bodies, faces)

            def set_surface_materials(self, value): self.painted = value
            def set_rotors(self, values, readings): self.rotors = values
            def rotors_moving(self): return False
            def set_scene_lighting(self, bed, models): self.effects = (bed, models)
            def set_lighting_enabled(self, enabled): self.lighting_enabled = enabled
            def set_scene(self, view, root): self.scene = (view, root)
            def set_simulation_active(self, active):
                changed = getattr(self, "simulation_active", False) != active
                self.simulation_active = active
                return changed
            def set_opacity(self, value): self.opacity = value
            def set_lights(self, *values): self.lights = values
            def close(self): self.closed = True
        class Signals(QObject):
            changed = pyqtSignal()
            positionChanged = pyqtSignal()
            statusReceived = pyqtSignal(object)
            statusAdmitted = pyqtSignal(object, str, float)
            sessionInvalidated = pyqtSignal()
            connectionChanged = pyqtSignal(bool, str)
            sceneLightingRequested = pyqtSignal()
            light_bed = light_models = True
            lighting_enabled = True
            toolheadVisibilityRequested = pyqtSignal(bool)
            toolhead_visible = True
            reportedPositionRequested = pyqtSignal(bool)
            toolheadOpacityRequested = pyqtSignal(float)
            toolhead_opacity = 1.0
        self.Node = Node
        self.node_patch = patch.dict(sys.modules, {self.module.__package__ + ".ToolheadSceneNode": SimpleNamespace(ToolheadSceneNode=Node)})
        self.node_patch.start()
        self.addCleanup(self.node_patch.stop)
        self.root = SimpleNamespace(getAllChildren=lambda: [])
        self.scene = SimpleNamespace(getRoot=lambda: self.root, sceneChanged=Mock())
        self.cura = Signals()
        self.cura.has_toolpath = True
        self.cura.plugin_loaded_path = "/print.gcode"
        self.cura.preview_active = True
        self.cura.decorating_scene = nullcontext
        self.cura.writing_preview = nullcontext
        self.cura.controller = SimpleNamespace(getScene=lambda: self.scene)
        self.native = Node(); self.native.parent = self.root
        self.cura.view = SimpleNamespace(getNozzleNode=lambda: self.native, setActivity=Mock(),
            getCurrentLayer=lambda: 0, getCurrentPath=lambda: 0)
        self.binding = Signals()
        self.binding.identity = ("A", "Printer")
        self.binding.config = self.qt.load("PrinterConfig").PrinterConfig(enabled=True, toolhead_model="c"*64)
        self.client = Signals()
        self.client.connected = True
        self.client.session = SimpleNamespace(poll_policy=SimpleNamespace(interval_ms=lambda *args: 750))
        self.presentation = Signals()
        self.presentation.reported_position = True
        self.presentation.publish_toolhead = Mock()
        self.application = SimpleNamespace(getGlobalContainerStack=lambda: SimpleNamespace(getProperty=lambda key, role: {"machine_width": 200, "machine_depth": 200, "machine_height": 200, "machine_center_is_zero": False}[key]))
        self.store = SimpleNamespace(load=Mock(return_value=self.module.default_mesh()))
        self.presenter = self.module.ToolheadPresenter(self.application, self.cura, self.client, self.binding, self.presentation, self.store)
        self.addCleanup(self.presenter.close)
        self.presenter._timer.stop()
        self.presenter._animation_timer.stop()
        self.client.statusAdmitted.emit({"motion_report": {"live_position": [110,120,8]}, "toolhead": {"homed_axes": "xyz"}}, "sync", time.monotonic())
        self.client.statusReceived.emit({"print_stats": {"filename": "print.gcode", "state": "printing"}, "motion_report": {"live_position": [110,120,8]}, "gcode_move": {"homing_origin": [10,20,3]}, "toolhead": {"homed_axes": "xyz"}})

    def test_live_node_has_anchor_and_stationary_ticks_do_not_redraw(self):
        node = self.presenter._node
        self.assertTrue(node.visible)

        self.assertEqual((node.point.x, node.point.y, node.point.z), (10,8,-20))
        count = self.scene.sceneChanged.emit.call_count
        self.presenter.update()
        self.assertEqual(self.scene.sceneChanged.emit.call_count, count)
        self.assertEqual(len(node.models), 1)
        self.assertIsNone(self.native.parent)
        self.presentation.toolhead_visible = False
        self.presentation.toolheadVisibilityRequested.emit(False)
        self.assertIsNone(self.native.parent)
        self.assertTrue(node.visible)
        self.assertIs(node.native_mesh, self.native.mesh)


    def test_missing_asset_never_applies_old_body_rotation_tip_or_lights_to_default(self):
        self.binding.config.toolhead_model = 'd'*64
        self.binding.config.toolhead_tip = [100,200,300]
        self.binding.config.toolhead_rotors = [dict(body=0, centre=[0,0,0], axis=[0,0,1], rpm=3000, direction=1, fan='', blur=True)]
        self.binding.config.toolhead_lights = [{'obsolete':'surface'}]
        self.store.load.side_effect = ValueError('corrupt model')
        for _ in range(2): self.presenter.update()
        node = self.presenter._node
        self.assertEqual(node.rotors, [])
        self.assertEqual(node.attached, [])
        self.assertEqual(node.models[-1][1], self.module.default_mesh().automatic_tip)
        self.assertIn('saved model unavailable', self.presentation.publish_toolhead.call_args.args[0])
    def test_visual_rotors_sleep_for_hidden_windows_and_telemetry_stop_repaints_once(self):
        window=SimpleNamespace(update=Mock(), isVisible=lambda:True, isMinimized=lambda:False)
        self.application.getMainWindow=lambda:window
        node=self.presenter._node
        node.visible_for_render=lambda:True
        node.rotors_moving=lambda:True
        self.presenter.update()
        self.assertTrue(self.presenter._animation_timer.isActive())
        node.visible_for_render=lambda:False
        for _ in range(2): self.presenter.update()
        self.assertFalse(self.presenter._animation_timer.isActive())
        node.visible_for_render=lambda:True
        self.presenter.update()
        self.assertTrue(self.presenter._animation_timer.isActive())
        self.presenter._animate()
        self.assertTrue(window.update.called)
        window.isVisible=lambda:False
        self.presenter._animate()
        self.assertFalse(self.presenter._animation_timer.isActive())
        self.presenter.update()
        self.assertFalse(self.presenter._animation_timer.isActive())
        window.isVisible=lambda:True
        self.presenter.update()
        self.assertTrue(self.presenter._animation_timer.isActive())
        node.rotors_moving=lambda:False
        window.update.reset_mock()
        self.presenter.set_fan_readings({'fan':dict(available=True,rpm=0)})
        self.assertFalse(self.presenter._animation_timer.isActive())
        window.update.assert_called_once()
        self.presenter.set_fan_readings({'fan':dict(available=True,rpm=0)})
        window.update.assert_called_once()

    def test_pose_only_updates_request_composition_without_invalidating_native_gcode(self):
        window = SimpleNamespace(update=Mock())
        self.presenter._application.getMainWindow = lambda: window
        self.scene.sceneChanged.emit.reset_mock()
        self.presentation.toolhead_opacity = .5
        self.presentation.toolheadOpacityRequested.emit(.5)
        window.update.assert_called_once()
        self.scene.sceneChanged.emit.assert_not_called()

    def test_visible_head_retains_adapter_without_following_and_hiding_forces_native_frame(self):
        window = SimpleNamespace(update=Mock())
        self.presenter._application.getMainWindow = lambda: window
        self.assertTrue(self.presenter._node.simulation_active)
        self.presentation.toolhead_visible = False
        self.presentation.reported_position = False
        self.scene.sceneChanged.emit.reset_mock()
        self.presenter.update()
        self.assertFalse(self.presenter._node.simulation_active)
        self.scene.sceneChanged.emit.assert_called_once_with(self.presenter._node)
        window.update.assert_not_called()
        self.scene.sceneChanged.emit.reset_mock()
        self.presentation.toolhead_visible = True
        self.presentation.reported_position = True
        self.presenter.update()
        self.assertTrue(self.presenter._node.simulation_active)
        self.scene.sceneChanged.emit.assert_called_once_with(self.presenter._node)

    def test_each_local_appearance_map_redraws_a_stationary_head_without_native_scene_rebuild(self):
        window=SimpleNamespace(update=Mock(),isVisible=lambda:True,isMinimized=lambda:False)
        self.application.getMainWindow=lambda:window
        self.presenter.update()
        for field, value in (("toolhead_body_materials",{"0":"petg"}),
                ("toolhead_body_finishes",{"0":{"roughness":.2}}),
                ("toolhead_face_finishes",{"0":{"reflectivity":.7}}),
                ("toolhead_body_opacity",{"0":.3}), ("toolhead_face_opacity",{"0":.2})):
            with self.subTest(field=field):
                window.update.reset_mock();self.scene.sceneChanged.emit.reset_mock()
                setattr(self.binding.config,field,value)
                self.presenter.update()
                window.update.assert_called_once()
                self.scene.sceneChanged.emit.assert_not_called()
                self.presenter.update()
                window.update.assert_called_once()
    def test_master_light_toggle_updates_head_without_losing_receiver_choices(self):
        self.presentation.lighting_enabled = False
        self.presentation.sceneLightingRequested.emit()
        self.assertFalse(self.presenter._node.lighting_enabled)
        self.assertEqual(self.presenter._node.effects, (True, True))
        self.presentation.lighting_enabled = True
        self.presentation.sceneLightingRequested.emit()
        self.assertTrue(self.presenter._node.lighting_enabled)

    def test_hiding_the_custom_model_releases_native_nozzle_and_preserves_mesh(self):
        self.presentation.reported_position = False
        node = self.presenter._node
        mesh_count = len(node.models)
        self.presentation.toolhead_visible = False
        self.presentation.toolheadVisibilityRequested.emit(False)
        self.assertFalse(node.visible)
        self.assertIs(self.native.parent, self.root)
        self.assertFalse(self.cura.toolhead_override)
        self.assertFalse(node.simulation_active)
        self.presentation.toolhead_visible = True
        self.presentation.reported_position = True
        self.presentation.toolheadVisibilityRequested.emit(True)
        self.assertTrue(node.visible)
        self.assertIsNone(self.native.parent)
        self.assertEqual(len(node.models), mesh_count)

    def test_removing_custom_model_uses_plain_native_mesh_with_true_position(self):
        from dataclasses import replace
        self.presentation.toolhead_opacity = .25
        self.binding.config = replace(self.binding.config, toolhead_model="")
        self.binding.changed.emit()
        self.assertIsNone(self.native.parent)
        self.assertTrue(self.cura.toolhead_override)
        self.assertTrue(self.presenter._node.visible)
        self.assertFalse(self.presenter._node.simulation_active)
        self.assertIs(self.presenter._node.native_mesh, self.native.mesh)
        self.assertEqual(self.presenter._node.opacity, 1)
        self.assertEqual(self.presenter._node.effects, (False, False))
        self.assertFalse(self.presenter._node.lighting_enabled)

    def test_plain_reported_mode_works_without_toolpath_and_restores_estimated_native(self):
        from dataclasses import replace
        self.binding.config = replace(self.binding.config, toolhead_model="")
        self.cura.has_toolpath = False
        self.cura.plugin_loaded_path = ""
        self.store.load.reset_mock()
        self.presenter.update()
        node = self.presenter._node
        self.assertTrue(node.visible)
        self.assertIs(node.native_mesh, self.native.mesh)
        self.assertEqual((node.point.x, node.point.y, node.point.z), (10, 8, -20))
        self.store.load.assert_not_called()
        self.presentation.toolhead_opacity = 0
        self.presenter.update()
        self.assertTrue(node.visible)
        self.assertEqual(node.opacity, 1)
        self.presentation.reported_position = False
        self.presenter.update()
        self.assertFalse(node.visible)
        self.assertIs(self.native.parent, self.root)
        self.assertFalse(self.cura.toolhead_override)
        self.assertFalse(node.simulation_active)

    def test_plain_reported_mode_retains_homing_staleness_and_foreign_source_gates(self):
        from dataclasses import replace
        self.binding.config = replace(self.binding.config, toolhead_model="")
        self.presenter._homing_available = False
        self.presenter.update()
        self.assertFalse(self.presenter._node.visible)
        self.assertIn("homing status", self.presentation.publish_toolhead.call_args.args[0])
        self.presenter._homing_available = True
        self.presenter._received -= 10
        self.presenter.update()
        self.assertFalse(self.presenter._node.visible)
        self.assertIn("stale", self.presentation.publish_toolhead.call_args.args[0])
        self.presenter._received = time.monotonic()
        self.presenter.update()
        self.assertTrue(self.presenter._node.visible)
        self.assertIn("Reported", self.presentation.publish_toolhead.call_args.args[0])
        self.binding.config = replace(self.binding.config, show_toolhead_indicator=False)
        self.presenter.update()
        self.assertFalse(self.presenter._node.visible)
        self.assertIn("indicator disabled", self.presentation.publish_toolhead.call_args.args[0])

    def test_wrong_loaded_print_and_detached_view_cannot_shift_physical_true_position(self):
        self.cura.plugin_loaded_path = "/wrong/other.gcode"
        self.cura.attached = False
        self.cura.view.getCurrentLayer = lambda: 1000
        self.cura.view.getCurrentPath = lambda: 9000
        self.presenter._status["gcode_move"] = {
            "homing_origin": [50,60,70], "position": [400,500,600], "gcode_position": [1,2,3]}
        self.presenter.update()
        node = self.presenter._node
        self.assertTrue(node.visible)
        self.assertEqual((node.point.x,node.point.y,node.point.z), (10,8,-20))
        for raw in ([1,2], [1,float("nan"),3], "123", None):
            self.presenter._status["motion_report"]["live_position"] = raw
            self.presenter.update()
            self.assertFalse(node.visible)
        self.presenter._status["motion_report"]["live_position"] = [110,120,8]
        self.presenter.update()
        self.assertTrue(node.visible)

    def test_unavailable_native_mesh_does_not_substitute_default_cad_mesh(self):
        from dataclasses import replace
        self.binding.config = replace(self.binding.config, toolhead_model="")
        self.native.mesh = None
        node = self.presenter._node
        model_count = len(node.models)
        self.presenter.update()
        self.assertFalse(node.visible)
        self.assertIs(self.native.parent, self.root)
        self.assertEqual(len(node.models), model_count)
        self.assertIn("Cura nozzle model unavailable", self.presentation.publish_toolhead.call_args.args[0])

    def test_saved_model_failure_retries_on_new_key_and_hides_when_disabled(self):
        from dataclasses import replace
        self.store.load.reset_mock()
        self.store.load.side_effect = OSError("Missing")
        self.binding.config = replace(self.binding.config, toolhead_model="a"*64)
        self.binding.changed.emit()
        self.assertIn("saved model unavailable", self.presentation.publish_toolhead.call_args.args[0])
        self.assertEqual(self.store.load.call_count, 1)
        self.binding.changed.emit()
        self.assertEqual(self.store.load.call_count, 1)
        self.binding.config = replace(self.binding.config, show_toolhead_indicator=False)
        self.binding.changed.emit()
        self.assertFalse(self.presenter._node.visible)
        self.assertIn("indicator disabled", self.presentation.publish_toolhead.call_args.args[0])
        self.binding.identity = ("B", "Other")
        self.binding.changed.emit()
        self.assertEqual(self.presenter._received, 0)
        self.client.connectionChanged.emit(False, "offline")
        self.client.connected = False
        self.binding.config = replace(self.binding.config, show_toolhead_indicator=True)
        self.presenter.update()
        self.assertIn("disconnected", self.presentation.publish_toolhead.call_args.args[0])
        self.client.connected = True
        self.client.connectionChanged.emit(True, "connected")

    def test_estimated_custom_node_uses_public_layer_position(self):
        from dataclasses import replace
        self.presentation.reported_position = False
        self.binding.config = replace(self.binding.config, toolhead_model="b"*64)
        with patch.object(self.module, "estimated_position", return_value=SimpleNamespace(x=1,y=2,z=3)):
            self.presenter.update()
        self.assertTrue(self.presenter._node.visible)
        self.assertEqual(self.presenter._node.point.x, 1)
        self.assertIn("Estimated along", self.presentation.publish_toolhead.call_args.args[0])
        self.presenter.close()
        self.assertIsNone(self.presenter._node.parent)
        self.presenter.update()  # retired callback is harmless

    def test_reported_position_renders_without_any_toolpath(self):
        self.cura.has_toolpath = False
        self.cura.plugin_loaded_path = ""
        self.presenter.update()
        self.assertTrue(self.presenter._node.visible)
        self.assertEqual((self.presenter._node.point.x, self.presenter._node.point.y,
                          self.presenter._node.point.z), (10, 8, -20))
        self.presentation.reported_position = False
        self.presenter.update()
        self.assertFalse(self.presenter._node.visible)

    def test_reported_availability_reasons_are_explicit(self):
        config = self.binding.config
        self.assertIsNotNone(self.presenter._reported(config)[0])
        self.presenter._homing_available = False
        self.assertIn("homing status", self.presenter._reported(config)[1])
        self.presenter._homing_available = True
        self.presenter._status["toolhead"]["homed_axes"] = "xy"
        self.assertIn("Home XYZ", self.presenter._reported(config)[1])
        self.presenter._status["toolhead"]["homed_axes"] = "xyz"
        self.presenter._live_available = False
        self.assertIn("unavailable", self.presenter._reported(config)[1])
        self.presenter._live_available = True
        self.application.getGlobalContainerStack = lambda: None
        self.assertIn("dimensions", self.presenter._reported(config)[1])


class EstimatedPositionTests(unittest.TestCase):
    def test_public_layer_adapter_interpolates_and_rejects_unusable_paths(self):
        import sys
        import numpy as np
        with runtime() as qt:
            module = qt.load("ToolheadPresenter")
            selected = SimpleNamespace(polygons=[SimpleNamespace(data=np.array([[0,0,0]], dtype=float)), SimpleNamespace(data=np.array([[1,2,3],[5,6,7]], dtype=float))])
            node = SimpleNamespace(callDecoration=lambda name: SimpleNamespace(getLayer=lambda layer: selected), getWorldPosition=lambda: np.array([10,20,30]))
            root = SimpleNamespace(getAllChildren=lambda: [SimpleNamespace(callDecoration=lambda name: None), node])
            view = SimpleNamespace(getCurrentLayer=lambda: 0, getCurrentPath=lambda: 1.5)
            with patch.dict(sys.modules, {"UM.Math.Vector": SimpleNamespace(Vector=lambda *point: np.array(point))}):
                np.testing.assert_allclose(module.estimated_position(view, root), [13,24,35])
                view.getCurrentPath = lambda: 1
                np.testing.assert_allclose(module.estimated_position(view, root), [11,22,33])
                view.getCurrentPath = lambda: 99
                self.assertIsNone(module.estimated_position(view, root))
                view.getCurrentPath = lambda: float("nan")
                self.assertIsNone(module.estimated_position(view, root))
                view.getCurrentPath = lambda: -1
                self.assertIsNone(module.estimated_position(view, root))
                view.getCurrentLayer = lambda: "bad"
                self.assertIsNone(module.estimated_position(view, root))
                self.assertIsNone(module.estimated_position(None, root))
                view.getCurrentLayer = lambda: 0
                view.getCurrentPath = lambda: 0
                selected.polygons = []
                self.assertIsNone(module.estimated_position(view, root))
                node.callDecoration = lambda name: SimpleNamespace(getLayer=lambda layer: None)
                self.assertIsNone(module.estimated_position(view, root))
