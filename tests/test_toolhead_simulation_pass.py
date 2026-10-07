"""Public native-pass contracts; doubles do not certify native GPU pixels."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import numpy as np


class SceneNode:
    def __init__(self, data=None): self.data, self.visible, self.outside, self.disabled = data, True, False, False
    def isVisible(self): return self.visible
    def getMeshData(self): return self.data
    def getWorldTransformation(self): return "world"
    def isOutsideBuildArea(self): return self.outside
    def callDecoration(self, name):
        return {"getLayerData": self.data, "isAssignedToDisabledExtruder": self.disabled, "isBlockSlicing": False}.get(name)


class ToolHandle(SceneNode):
    def getSolidMesh(self): return self.data


class RetainedCache:
    def __init__(self): self.key, self.end = None, None
    def restore(self, gl, output, key, end, rebuild, append):
        if self.key != key or self.end is None or end < self.end: rebuild()
        elif end > self.end: append(self.end, end)
        self.key, self.end = key, end


class SimulationPassTests(unittest.TestCase):
    def setUp(self):
        self.context = object()
        self.current_context = lambda: self.context
        self.legacy, self.allowed = False, True
        self.layer, self.minimum, self.path, self.running = 1, 0, 0., False
        self.compatibility = False
        self.top_mesh = self.jumps = None
        self.data = self.make_data()
        self.child = SceneNode(self.data)
        self.children = [self.child]
        self.root = SimpleNamespace(getAllChildren=lambda: self.children)
        self.view = SimpleNamespace(getCurrentLayer=lambda: self.layer, getMinimumLayer=lambda: self.minimum,
            getCurrentPath=lambda: self.path, isSimulationRunning=lambda: self.running,
            getCompatibilityMode=lambda: self.compatibility, getCurrentLayerMesh=lambda: self.top_mesh,
            getCurrentLayerJumps=lambda: self.jumps)
        for getter in ("SimulationViewType", "ExtruderOpacities", "ShowTravelMoves", "ShowHelpers", "ShowSkin", "ShowInfill", "ShowStarts",
                       "MinFeedrate", "MaxFeedrate", "MinThickness", "MaxThickness", "MinLineWidth", "MaxLineWidth", "MinFlowRate", "MaxFlowRate"):
            setattr(self.view, "get" + getter, Mock(return_value=7))
        self.original = Mock()
        self.size = (800, 600)
        self.original.getName.return_value = "simulationview"
        self.original.getSize.side_effect = lambda: self.size
        self.renderer = Mock()
        self.owner = self.original
        self.renderer.getRenderPass.side_effect = lambda name: self.owner
        self.renderer.removeRenderPass.side_effect = lambda old: setattr(self, "owner", None)
        self.renderer.addRenderPass.side_effect = lambda new: setattr(self, "owner", new)
        self.gl = Mock(GL_COLOR_BUFFER_BIT=16384, GL_DEPTH_BUFFER_BIT=256, GL_DEPTH_TEST=2929,
                       GL_LESS=513, GL_BLEND=3042, GL_CULL_FACE=2884)
        self.normal, self.shadow = Mock(), Mock()
        self.shader_factory = Mock(side_effect=[self.normal, self.shadow])
        self.opengl = SimpleNamespace(getBindingsObject=lambda: self.gl, createShaderProgram=self.shader_factory)
        self.fbo = Mock()
        self.fbo.bind.return_value = self.fbo.isValid.return_value = True
        self.fbo.texture.return_value = 83
        self.fbo_factory = Mock(return_value=self.fbo)
        self.fbo_factory.Attachment = SimpleNamespace(Depth=1)
        self.format = Mock()
        self.matrix = SimpleNamespace(getData=lambda: np.eye(4))
        self.camera = SimpleNamespace(getCameraLightPosition=lambda: "camera light",
            getProjectionMatrix=lambda: self.matrix, getInverseWorldTransformation=lambda: self.matrix)
        self.scene = SimpleNamespace(getActiveCamera=lambda: self.camera)
        self.application = SimpleNamespace(getController=lambda: SimpleNamespace(getScene=lambda: self.scene),
            getTheme=lambda: SimpleNamespace(getColor=lambda name: SimpleNamespace(getRgb=lambda: (1, 2, 3, 4))))
        self.logger = Mock()
        self.mesh_factory = Mock(side_effect=lambda **values: SimpleNamespace(**values))
        surfaces = {
            "PyQt6.QtGui": SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=self.current_context)),
            "PyQt6.QtOpenGL": SimpleNamespace(QOpenGLFramebufferObject=self.fbo_factory, QOpenGLFramebufferObjectFormat=lambda: self.format),
            "UM.Scene.SceneNode": SimpleNamespace(SceneNode=SceneNode), "UM.Scene.ToolHandle": SimpleNamespace(ToolHandle=ToolHandle),
            "UM.View.GL.OpenGLContext": SimpleNamespace(OpenGLContext=SimpleNamespace(isLegacyOpenGL=lambda: self.legacy)),
            "UM.View.GL.OpenGL": SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda: self.opengl)),
            "UM.Application": SimpleNamespace(Application=SimpleNamespace(getInstance=lambda: self.application)),
            "UM.PluginRegistry": SimpleNamespace(PluginRegistry=SimpleNamespace(getInstance=lambda: SimpleNamespace(getPluginPath=lambda name: "/native/shaders"))),
            "UM.Math.Color": SimpleNamespace(Color=lambda *values: values),
            "UM.Mesh.MeshData": SimpleNamespace(MeshData=self.mesh_factory),
            "cura.LayerPolygon": SimpleNamespace(LayerPolygon=SimpleNamespace(MoveUnretractedType=8)),
            "cura.Settings.ExtruderManager": SimpleNamespace(ExtruderManager=SimpleNamespace(getInstance=lambda: SimpleNamespace(activeExtruderIndex=-1))),
            "UM.Logger": SimpleNamespace(Logger=self.logger),
            "mpf.diagnostics.RenderTiming": SimpleNamespace(RenderTiming=lambda: self.timing),
            "mpf.toolhead.ToolheadSimulationCache": SimpleNamespace(ToolheadSimulationCache=RetainedCache),
        }
        self.timing = SimpleNamespace(measure=Mock(side_effect=lambda name, callback: callback()))
        surface = patch.dict(sys.modules, surfaces)
        surface.start()
        self.addCleanup(surface.stop)
        source = Path(__file__).resolve().parents[1] / "mpf/cura/ToolheadSimulationPass.py"
        spec = importlib.util.spec_from_file_location("mpf.cura._simulation_contract", source)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.geometry = Mock()
        self.geometry_factory = Mock(return_value=self.geometry)
        self.module.ToolheadPathGeometry = self.geometry_factory
        self.instanced = Mock()
        self.instanced.render.return_value = False
        self.module.ToolheadInstancedShadow = Mock(return_value=self.instanced)
        self.adapter = self.module.ToolheadSimulationPass(self.original, self.renderer, self.view, self.root, lambda: self.allowed)
        self.owner = self.adapter

    def make_data(self):
        polygons = [SimpleNamespace(data=np.array([[0., 1, 0], [1., 1, 0]])),
                    SimpleNamespace(data=np.array([[2., 1, 0], [3., 1, 0]]))]
        return SimpleNamespace(getElementCounts=lambda: {0: np.int64(6), 1: np.int64(8)},
            getLayer=lambda layer: SimpleNamespace(polygons=polygons), hasAttribute=lambda name: True)

    def activate(self, path=.5):
        self.adapter.render()  # Native warmup establishes deterministic state.
        self.path = path
        self.adapter.render()  # Synchronize original lower-shadow mode.
        self.adapter.render()

    def test_visible_depth_revision_covers_fractional_progress_and_camera_source_changes(self):
        self.activate(.5)
        self.fbo.handle.return_value = 83
        revision = self.adapter.get_visible_depth_revision(self.camera, (0, 0, 800, 600))
        self.assertIsNotNone(revision)
        self.path = .75
        self.assertIsNone(self.adapter.get_visible_depth_revision(self.camera, (0, 0, 800, 600)))
        self.adapter.render()
        next_revision = self.adapter.get_visible_depth_revision(self.camera, (0, 0, 800, 600))
        self.assertNotEqual(revision, next_revision)
        moved = SimpleNamespace(getInverseWorldTransformation=lambda: SimpleNamespace(getData=lambda: np.ones((4,4))),
                                getProjectionMatrix=lambda: self.matrix)
        self.assertIsNone(self.adapter.get_visible_depth_revision(moved, (0, 0, 800, 600)))
        self.assertIsNone(self.adapter.get_visible_depth_revision(self.camera, (1, 0, 800, 600)))
        self.context = object()
        self.assertIsNone(self.adapter.get_visible_depth_revision(self.camera, (0, 0, 800, 600)))

    def test_visible_depth_copy_crops_full_output_and_restores_destination_after_failure(self):
        self.activate(.5)
        self.fbo.handle.return_value = 83
        self.fbo.format.return_value.samples.return_value = 0
        output = Mock()
        output.handle.return_value = 91
        output.format.return_value.samples.return_value = 0
        output.size.return_value.width.return_value = 64
        output.size.return_value.height.return_value = 96
        self.gl.glGetIntegerv.side_effect = lambda parameter: 91 if parameter == 0x8CA6 else 0
        self.gl.glGetError.return_value = 0
        self.adapter._cache._depth_functions = Mock(return_value=())
        self.adapter._cache._depth_format = Mock(return_value=(32,0,0))
        viewport, crop = (0,0,800,600), (128,160,64,96)
        revision = self.adapter.get_visible_depth_revision(self.camera, viewport)
        self.assertTrue(self.adapter.copy_visible_depth(self.gl, output, self.camera, viewport, crop, revision))
        args = self.fbo_factory.blitFramebuffer.call_args.args
        self.assertIs(args[0], output)
        self.assertIs(args[2], self.fbo)
        self.assertEqual((args[3].x(),args[3].y(),args[3].width(),args[3].height()), crop)
        self.gl.glGetError.return_value = 0x0502
        self.assertFalse(self.adapter.copy_visible_depth(self.gl, output, self.camera, viewport, crop, revision))
        output.bind.assert_called()
        self.gl.glViewport.assert_called_with(0,0,64,96)
        self.path = .75
        count = self.fbo_factory.blitFramebuffer.call_count
        self.assertFalse(self.adapter.copy_visible_depth(self.gl, output, self.camera, viewport, crop, revision))
        self.assertEqual(self.fbo_factory.blitFramebuffer.call_count, count)

    def test_warmup_then_exact_three_ranges_with_native_shaders_and_cull_modes(self):
        self.activate()
        self.assertEqual(self.original.render.call_count, 2)
        self.assertEqual(self.shader_factory.call_args_list, [call(str(Path("/native/shaders") / name)) for name in ("layers3d.shader", "layers3d_shadow.shader")])
        self.assertEqual(self.geometry.render.call_args_list, [
            call(self.shadow, self.camera, "world", [(0, 6)], self.gl),
            call(self.normal, self.camera, "world", [(6, 8)], self.gl)])
        self.normal.setUniformValue.assert_any_call("u_last_line_ratio", .5)
        self.normal.setUniformValue.assert_any_call("u_last_vertex", [0., 1., 0.])
        self.gl.glEnable.assert_any_call(self.gl.GL_CULL_FACE)
        self.gl.glDisable.assert_any_call(self.gl.GL_CULL_FACE)
        self.format.setAttachment.assert_called_once_with(1)
        self.fbo.release.assert_called_once_with()
        self.assertEqual(self.adapter.getTextureId(), 83)
        self.timing.measure.assert_called_once()
        self.logger.log.assert_called_once()

    def test_completed_prefix_includes_interior_paths_and_fraction_does_not_rebuild_storage(self):
        self.activate(2.5)
        self.assertEqual(self.geometry.render.call_args_list[-2:], [call(self.normal, self.camera, "world", [(6, 10)], self.gl),
                                                                 call(self.normal, self.camera, "world", [(10, 12)], self.gl)])
        self.path = 2.75
        self.adapter.render()
        self.geometry_factory.assert_called_once_with(self.data)
        self.fbo_factory.assert_called_once()
        self.assertEqual(self.shader_factory.call_count, 2)
        self.assertEqual(self.logger.log.call_count, 1)

    def test_retained_completed_prefix_appends_and_partial_stays_transient(self):
        self.activate(2.5)
        self.geometry.render.reset_mock()
        self.path = 2.75
        self.adapter.render()
        self.geometry.render.assert_called_once_with(self.normal, self.camera, "world", [(10, 12)], self.gl)
        self.geometry.render.reset_mock()
        self.path = 3.5  # Polygon end: no fractional segment.
        self.adapter.render()
        self.geometry.render.assert_called_once_with(self.normal, self.camera, "world", [(10, 12)], self.gl)
        self.geometry.render.reset_mock()
        self.path = 2.25
        self.adapter.render()
        self.assertEqual(self.geometry.render.call_args_list, [
            call(self.shadow, self.camera, "world", [(0, 6)], self.gl),
            call(self.normal, self.camera, "world", [(6, 10)], self.gl),
            call(self.normal, self.camera, "world", [(10, 12)], self.gl)])

    def test_retained_key_observes_uniform_values_camera_light_and_minimum_layer(self):
        self.activate(2.5)
        for mutate in (lambda: self.view.getShowStarts.configure_mock(return_value=8),
                       lambda: setattr(self.adapter, "_starts_colour", lambda: "new theme"),
                       lambda: setattr(self.adapter, "_active_extruder", lambda: 2),
                       lambda: setattr(self.camera, "getCameraLightPosition", lambda: "new light"),
                       lambda: setattr(self.camera, "getProjectionMatrix", lambda: SimpleNamespace(getData=lambda: np.eye(4)*2)),
                       lambda: setattr(self, "minimum", 1)):
            self.geometry.render.reset_mock()
            mutate()
            self.adapter.render()
            self.assertGreaterEqual(self.geometry.render.call_count, 2)
        array = np.array([1, 2, 3], dtype=np.float32)
        key = self.module.value_key(array)
        array[0] = 9
        self.assertNotEqual(key, self.module.value_key(array))
        self.assertEqual(self.module.value_key([1, 2]), ("1", "2"))

    def test_lower_mode_and_layer_transitions_sync_original_without_private_mutation(self):
        self.activate(2.5)
        native_calls = self.original.render.call_count
        self.layer, self.path = 0, 2.
        self.adapter.render()
        self.assertFalse(self.adapter._owned_output)
        self.assertEqual(self.original.render.call_count, native_calls + 1)
        self.adapter.render()
        self.assertTrue(self.adapter._owned_output)
        self.path = 2.25
        self.adapter.render()
        self.assertFalse(self.adapter._owned_output)
        self.adapter.render()
        self.assertTrue(self.adapter._owned_output)
        self.allowed = False
        self.adapter.render()
        self.assertFalse(self.adapter._owned_output)
        self.assertEqual(self.original.method_calls[-1], call.render())

    def test_info_logger_failure_does_not_disable_owned_rendering(self):
        self.logger.log.side_effect = ValueError("log unavailable")
        self.activate()
        self.assertTrue(self.adapter._owned_output)
        self.assertTrue(self.adapter._logged)

    def test_layer_change_switches_lower_normal_then_next_path_shadows(self):
        self.activate()
        self.layer, self.path = 0, .5
        self.adapter.render()
        self.assertFalse(self.adapter._shadow)
        self.path = 1.
        self.adapter.render()
        self.assertTrue(self.adapter._shadow)
        self.running = True
        self.layer, self.path = 1, 1.
        self.adapter.render()
        self.assertTrue(self.adapter._shadow)

    def test_unsupported_scene_interactions_delegate_native_and_resume_after_path_change(self):
        self.activate()
        cases = [lambda: setattr(self, "allowed", False), lambda: setattr(self, "compatibility", True),
                 lambda: setattr(self, "legacy", True), lambda: setattr(self, "top_mesh", object()),
                 lambda: setattr(self, "jumps", object()),
                 lambda: setattr(self.child, "outside", True), lambda: setattr(self.child, "disabled", True),
                 lambda: self.children.append(SceneNode(self.make_data())), lambda: setattr(self.child, "visible", False)]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                mutate()
                before = self.original.render.call_count
                self.adapter.render()
                self.assertEqual(self.original.render.call_count, before + 1)
                self.assertFalse(self.adapter._owned_output)
                self.allowed, self.compatibility, self.legacy = True, False, False
                self.top_mesh = self.jumps = None
                self.child.outside = self.child.disabled = False
                self.child.visible = True
                self.children = [self.child]
                self.path += .1
                self.adapter.render()
                self.path += .1
                self.adapter.render()

    def test_selection_handles_preserve_cached_paths_depth_and_lighting_material(self):
        self.activate()
        handle = ToolHandle(object())
        self.children.append(handle)
        batch = Mock()
        factory = Mock(return_value=batch)
        factory.RenderType = SimpleNamespace(Overlay=3)
        self.shader_factory.side_effect = None
        self.shader_factory.return_value = self.normal
        native_calls = self.original.render.call_count
        with patch.dict(sys.modules, {
            'UM.Resources': SimpleNamespace(Resources=SimpleNamespace(Shaders=1, getPath=lambda *args:'toolhandle.shader')),
            'UM.View.RenderBatch': SimpleNamespace(RenderBatch=factory)}):
            self.adapter.render()
            self.adapter.render()
        self.assertEqual(self.original.render.call_count, native_calls)
        self.assertTrue(self.adapter._owned_output)
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.assertIsNotNone(self.adapter._depth_snapshot)
        factory.assert_called_with(self.normal, type=3, backface_cull=True)
        batch.addItem.assert_called_with('world', mesh=handle.data)
        self.assertEqual(batch.render.call_count, 2)

    def test_source_change_context_retirement_and_resize_recreate_only_owned_storage(self):
        self.activate()
        self.context = object()
        self.shader_factory.side_effect = None
        self.shader_factory.return_value = self.normal
        self.adapter.render()
        self.assertFalse(self.adapter._owned_output)
        self.path = .75
        self.adapter.render()
        self.adapter.render()
        self.assertEqual(self.shader_factory.call_count, 4)
        self.assertEqual(self.geometry_factory.call_count, 2)
        self.size = (1200, 900)
        self.adapter.render()
        self.assertEqual(self.fbo_factory.call_count, 3)
        replacement = self.make_data()
        self.child.data = replacement
        self.adapter.render()
        self.assertFalse(self.adapter._owned_output)
        self.path += .2
        self.adapter.render()
        self.adapter.render()
        self.geometry_factory.assert_called_with(replacement)

    def test_native_uniforms_include_camera_light_metrics_theme_and_active_extruder(self):
        self.activate()
        for shader in (self.normal, self.shadow):
            shader.setUniformValue.assert_any_call("u_lightPosition", "camera light")
            shader.setUniformValue.assert_any_call("u_max_flow_rate", 7)
            shader.setUniformValue.assert_any_call("u_extruder_opacity", 7)
        self.normal.setUniformValue.assert_any_call("u_active_extruder", 0.)
        self.normal.setUniformValue.assert_any_call("u_starts_color", (1, 2, 3, 4))
        self.adapter._active_extruder, self.adapter._starts_colour = lambda: 3, lambda: "chosen colour"
        self.adapter.render()
        self.normal.setUniformValue.assert_any_call("u_active_extruder", 3.)
        self.normal.setUniformValue.assert_any_call("u_starts_color", "chosen colour")

    def test_shader_camera_storage_bind_draw_and_cleanup_failure_fall_back_and_latch(self):
        for stage in ("camera", "shader", "size", "valid", "bind", "draw", "release"):
            with self.subTest(stage=stage):
                self.adapter._previous, self.adapter._shadow, self.adapter._last_source = (1, 0.), True, (self.child, self.data)
                self.adapter._context = self.context
                self.adapter._failed_context = self.adapter._fbo = self.adapter._size = self.adapter._shaders = None
                self.camera = None if stage == "camera" else SimpleNamespace(getCameraLightPosition=lambda: "light",
                    getProjectionMatrix=lambda: self.matrix, getInverseWorldTransformation=lambda: self.matrix)
                self.adapter._cache = RetainedCache()
                self.shader_factory.side_effect = [None, self.shadow] if stage == "shader" else [self.normal, self.shadow]
                self.size = (0, 0) if stage == "size" else (800, 600)
                self.fbo.isValid.return_value = stage != "valid"
                self.fbo.bind.return_value = stage != "bind"
                self.geometry.render.side_effect = RuntimeError("draw") if stage == "draw" else None
                self.fbo.release.side_effect = RuntimeError("release") if stage == "release" else None
                self.adapter.render()
                self.assertFalse(self.adapter._owned_output)
                self.assertIs(self.adapter._failed_context, self.context)
                calls = self.original.render.call_count
                self.adapter.render()
                self.assertEqual(self.original.render.call_count, calls + 1)

    def test_source_errors_and_failed_logging_are_contained(self):
        self.adapter._eligible = Mock(side_effect=ValueError("eligibility"))
        self.logger.log.side_effect = RuntimeError("logger")
        self.adapter.render()
        self.original.render.assert_called_once()
        self.assertFalse(self.adapter._owned_output)

    def test_public_delegation_and_guarded_restore(self):
        self.assertEqual(self.adapter.getName(), "simulationview")
        self.adapter.getPriority(); self.adapter.isEnabled(); self.adapter.setEnabled(True)
        self.adapter.setSize(20, 30); self.adapter.bind(); self.adapter.release()
        self.adapter.getOutput(); self.adapter.getTextureId()
        self.original.getOutput.assert_called_once()
        self.original.getTextureId.assert_called_once()
        self.activate()
        self.adapter.getOutput()
        self.fbo.toImage.assert_called_once()
        self.adapter.close()
        self.assertIs(self.owner, self.original)
        self.adapter.render()
        newer = object()
        self.owner = newer
        self.adapter.close()
        self.assertIs(self.owner, newer)

    def test_completed_shadow_capability_tracks_native_warmup_and_detached_layer_navigation(self):
        self.data.getElementCounts = lambda: {0: 6, 1: 8, 2: 4}
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.adapter.render()
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.path = .5
        self.adapter.render()  # Native transition frame already uses shadow grey.
        self.assertFalse(self.adapter._owned_output)
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        # Following remains eligible with a visible head when a manual layer
        # change detaches the follower. Match the native stopped-layer rule.
        self.layer = 2
        self.adapter.render()
        self.assertFalse(self.adapter._owned_output)
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), False)
        self.geometry.render.reset_mock()
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), False)
        self.assertIn(call(self.normal, self.camera, 'world', [(0, 14)], self.gl),
                      self.geometry.render.call_args_list)
        self.assertFalse(any(call.args[0] is self.shadow for call in self.geometry.render.call_args_list))
        self.path = 1.0  # Scrubbing paths restores native shadow material.
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)

    def test_completed_shadow_capability_is_unknown_after_source_retirement_and_close(self):
        self.activate()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.allowed = False
        self.adapter.render()
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.assertFalse(self.adapter._owned_output)
        self.allowed = True
        self.adapter.render()  # New eligible source must establish its own mode.
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.path = .75
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.adapter.set_scene(self.view, object())
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.adapter.set_scene(self.view, self.root)
        self.adapter.render()
        self.path = 1.0
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.adapter.close()
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())

    def test_completed_shadow_capability_stays_native_equivalent_after_owned_draw_failure(self):
        self.activate()
        self.path = 2.5
        self.geometry.render.side_effect = RuntimeError('owned drawing failed')
        native_calls = self.original.render.call_count
        self.adapter.render()
        self.assertEqual(self.original.render.call_count, native_calls + 1)
        self.assertFalse(self.adapter._owned_output)
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.layer = 0
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), False)

    def test_completed_shadow_capability_retires_mode_if_source_observation_fails_before_layer_transition(self):
        self.activate()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)
        self.layer = 0  # Native stopped-layer navigation now selects full colour.
        eligibility = self.adapter._eligible
        self.adapter._eligible = Mock(side_effect=RuntimeError('eligibility unavailable'))
        native_calls = self.original.render.call_count
        self.adapter.render()
        self.assertEqual(self.original.render.call_count, native_calls + 1)
        self.assertFalse(self.adapter._owned_output)
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.adapter._eligible = eligibility
        self.adapter.render()
        self.assertIsNone(self.adapter.getCompletedLayerShadowMode())
        self.path = .75  # A fresh observed transition can establish shadow again.
        self.adapter.render()
        self.assertIs(self.adapter.getCompletedLayerShadowMode(), True)

    def test_scene_change_and_explicit_timing_preserve_original_owner(self):
        explicit = self.module.ToolheadSimulationPass(self.original, self.renderer, self.view, self.root,
                                                       lambda: True, timing=self.timing)
        self.assertIs(explicit._timing, self.timing)
        explicit._owned_output = True
        explicit.set_scene(self.view, self.root)
        self.assertTrue(explicit._owned_output)
        explicit.set_scene(object(), object())
        self.assertFalse(explicit._owned_output)
        self.assertIsNone(explicit._previous)

    def test_range_admission_boundary_fractional_and_invalid_values(self):
        self.path = 1.5
        self.assertIsNone(self.module.path_ranges(self.data, self.view)[3])
        self.path = 2.5
        result = self.module.path_ranges(self.data, self.view)
        self.assertEqual(result[:3], (0, 6, 10))
        np.testing.assert_array_equal(result[3][0], [2, 1, 0])
        self.path = 3.5
        self.assertIsNone(self.module.path_ranges(self.data, self.view)[3])
        for path in (-1, float("nan"), float("inf"), 99):
            self.path = path
            self.assertIsNone(self.module.path_ranges(self.data, self.view))
        self.path, self.layer = 0., 7
        self.assertIsNone(self.module.path_ranges(self.data, self.view))
        self.layer, self.minimum = 1, 2
        self.assertIsNone(self.module.path_ranges(self.data, self.view))
        self.minimum = 0
        self.data.getLayer = lambda layer: None
        self.assertIsNone(self.module.path_ranges(self.data, self.view))

    def test_owned_missing_previous_types_are_built_once_without_source_mutation(self):
        types = np.array([1, 2, 3], np.float32)
        attributes = {"line_types": dict(value=types, opengl_name="a_line_type", opengl_type="float")}
        self.data.hasAttribute = lambda name: False
        self.data.attributeNames = lambda: list(attributes)
        self.data.getAttribute = attributes.__getitem__
        for getter in ("Vertices", "Normals", "Indices", "Colors", "UVCoordinates"):
            setattr(self.data, "get" + getter, lambda getter=getter: getter)
        mesh = self.module.path_mesh(self.data)
        np.testing.assert_array_equal(mesh.attributes["prev_line_types"]["value"], [8, 1, 2])
        self.assertFalse(mesh.attributes["prev_line_types"]["value"].flags.writeable)
        self.assertNotIn("prev_line_types", attributes)
        attributes["line_types"]["value"] = np.array([], np.float32)
        self.assertEqual(len(self.module.path_mesh(self.data).attributes["prev_line_types"]["value"]), 0)

    def depth_fixture(self, older_types=(6, 1, 6)):
        vertices = np.asarray([point for i in range(7) for point in ((-2., 0., float(i)), (2., 0., float(i)))], dtype=np.float32)
        attributes = {"line_types": {"value": np.repeat(list(older_types) + [6]*4, 2)},
            "line_dimensions": {"value": np.tile([.4, .2], (14, 1))},
            "colors": {"value": np.tile([.4, .5, .6, 1.], (14, 1))}}
        self.data.getVertices = Mock(return_value=vertices)
        self.data.getIndices = Mock(return_value=np.arange(14, dtype=np.int32).reshape(-1, 2))
        self.data.getAttribute = Mock(side_effect=lambda name: attributes[name])
        self.data.getColors = Mock(return_value=np.tile([.4, .5, .6, 1.], (14, 1)))
        self.child.getWorldTransformation = lambda: self.matrix
        self.camera.getWorldPosition = lambda: SimpleNamespace(x=0., y=20., z=30.)
        self.view.getSimulationViewType.return_value = 1
        self.view.getShowTravelMoves.return_value = False
        for flag in ('Helpers', 'Skin', 'Infill', 'Starts'):
            getattr(self.view, 'getShow'+flag).return_value = True
        self.gl.glGetIntegerv.side_effect = lambda parameter: {0x0B46:0x0901, 0x0B45:0x0405}[parameter]
        self.gl.glGetFloatv.return_value = (0., 1.)
        self.gl.glIsEnabled.return_value = False
        self.activate(2.5)
        self.adapter._cache.try_copy_depth = Mock(return_value=True)
        paths = [(Mock(mesh=self.data), self.matrix, (0, 12), [], {'u_last_vertex': [1., 1., 0.]}, 6)]
        return attributes, paths

    def test_shared_depth_accepts_default_starts_and_copies_only_completed_not_fraction(self):
        attributes, paths = self.depth_fixture()
        output = object()
        self.assertTrue(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.adapter._cache.try_copy_depth.assert_called_once_with(self.gl, output, self.adapter._depth_snapshot['key'], 10)
        first_risks = self.adapter._depth_risks
        self.assertTrue(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.assertIs(first_risks, self.adapter._depth_risks)
        self.data.getVertices.assert_called_once()  # Camera requests never rescan immutable source arrays.

    def test_shadow_support_starts_and_hidden_prime_towers_reject_only_affected_older_range(self):
        attributes, paths = self.depth_fixture((4, 1, 11))
        output = object()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.view.getShowStarts.return_value = False
        self.adapter.render()
        self.assertTrue(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.view.getShowHelpers.return_value = False
        self.adapter.render()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.minimum = 1
        self.adapter.render()
        paths[0] = (paths[0][0], self.matrix, (6, 12), [], paths[0][4], 6)
        self.assertTrue(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))

    def test_stale_camera_progress_data_and_filters_never_copy_into_lighting(self):
        attributes, paths = self.depth_fixture()
        output = object()
        changed_camera = SimpleNamespace(**vars(self.camera))
        changed_camera.getProjectionMatrix = lambda: SimpleNamespace(getData=lambda: np.eye(4)*2)
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, changed_camera, paths, self.view))
        changed = list(paths[0]); changed[2] = (0, 14)
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, [tuple(changed)], self.view))
        changed = list(paths[0]); changed[0] = Mock(mesh=object())
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, [tuple(changed)], self.view))
        self.view.getShowSkin.return_value = False
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.adapter._cache.try_copy_depth.assert_not_called()

    def test_cull_winding_camera_inside_and_double_sided_travels_reject_depth_reuse(self):
        attributes, paths = self.depth_fixture()
        output = object()
        self.camera.getWorldPosition = lambda: SimpleNamespace(x=0., y=0., z=0.)
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.camera.getWorldPosition = lambda: SimpleNamespace(x=0., y=20., z=30.)
        self.gl.glIsEnabled.return_value = True
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.gl.glIsEnabled.return_value = False
        self.view.getShowTravelMoves.return_value = True
        self.adapter.render()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.adapter._cache.try_copy_depth.assert_not_called()

    def test_unknown_source_context_and_copy_failure_are_fail_soft_and_do_not_use_host_fbos(self):
        attributes, paths = self.depth_fixture()
        output = object()
        self.allowed = False
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.allowed = True
        context = self.context; self.context = object()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.context = context
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths*2, self.view))
        self.adapter._cache.try_copy_depth.return_value = False
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.adapter._cache.try_copy_depth.side_effect = RuntimeError('copy failed')
        with self.assertRaisesRegex(RuntimeError, 'copy failed'):
            self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view)

    def test_transparent_material_starts_and_oblique_geometry_use_original_depth_capture(self):
        attributes, paths = self.depth_fixture()
        output = object()
        self.view.getSimulationViewType.return_value = 0
        attributes['colors']['value'][0, 3] = 0
        self.adapter._depth_risks = None
        self.adapter.render()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.view.getSimulationViewType.return_value = 1
        self.adapter._starts_colour = lambda: (1., 1., 1., 0.)
        self.adapter.render()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.adapter._starts_colour = lambda: (1., 1., 1., 1.)
        self.data.getVertices.return_value[1, 1] = 1.
        self.adapter._depth_risks = None
        self.adapter.render()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.assertIsNone(self.adapter._depth_risks[2])

    def test_depth_equivalence_rejects_reflections_and_unknown_attributes_without_copy(self):
        attributes, paths = self.depth_fixture()
        reflected = SimpleNamespace(getData=lambda: np.diag([-1.,1.,1.,1.]))
        self.assertIsNone(self.adapter._depth_geometry_risks(self.data, reflected))
        self.data.getAttribute.side_effect = KeyError('line_dimensions')
        self.adapter._depth_risks = None
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl, object(), self.camera, paths, self.view))
        self.adapter._cache.try_copy_depth.assert_not_called()

    def test_depth_snapshot_does_not_retain_departed_scene_or_closed_data(self):
        attributes, paths = self.depth_fixture()
        self.adapter.try_copy_completed_depth(self.gl, object(), self.camera, paths, self.view)
        self.assertIsNotNone(self.adapter._depth_snapshot)
        self.assertIsNotNone(self.adapter._depth_risks)
        self.adapter.set_scene(self.view, SimpleNamespace(getAllChildren=lambda: []))
        self.assertIsNone(self.adapter._depth_snapshot)
        self.assertIsNone(self.adapter._depth_risks)
        self.adapter._depth_snapshot = {'data':self.data}
        self.adapter._depth_risks = (self.data, b'', {})
        self.adapter.close()
        self.assertIsNone(self.adapter._depth_snapshot)
        self.assertIsNone(self.adapter._depth_risks)

    def test_depth_admission_diagnostic_is_opt_in_once_per_context_and_fail_soft(self):
        attributes, paths = self.depth_fixture()
        output = Mock()
        output.size.return_value.width.return_value = 800
        output.size.return_value.height.return_value = 600
        self.logger.reset_mock()
        self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view)
        self.logger.log.assert_not_called()
        self.adapter._timing.enabled = True
        for _ in range(3):
            self.assertTrue(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.logger.log.assert_called_once_with('i', 'toolhead shared simulation depth admitted: %sx%s, completed elements %s',800,600,10)
        self.adapter._depth_logged = False
        self.logger.log.side_effect = ValueError('logger unavailable')
        self.assertTrue(self.adapter.try_copy_completed_depth(self.gl, output, self.camera, paths, self.view))
        self.assertTrue(self.adapter._depth_logged)

    def large_depth_fixture(self):
        attributes, _paths = self.depth_fixture()
        lines = 65538
        vertices = np.empty((lines*2,3),np.float32)
        vertices[::2] = [-2.,0.,0.]; vertices[1::2] = [2.,0.,0.]
        vertices[-2:,2] = 80.
        self.data.getVertices.return_value = vertices
        self.data.getIndices.return_value = np.arange(lines*2,dtype=np.int32).reshape(-1,2)
        attributes['line_types']['value'] = np.full(lines*2,6,np.float32)
        attributes['line_dimensions']['value'] = np.tile([.4,.2],(lines*2,1)).astype(np.float32)
        attributes['colors']['value'] = np.tile([.4,.5,.6,1.],(lines*2,1)).astype(np.float32)
        self.data.getColors.return_value = attributes['colors']['value'].copy()
        self.adapter._depth_risks = None
        return attributes,vertices

    def test_chunked_depth_proof_preserves_sparse_element_offsets_and_global_bounds(self):
        attributes,vertices = self.large_depth_fixture()
        types = attributes['line_types']['value']
        types[2*65535:2*65536] = 4
        types[2*65536:2*65537] = 11
        types[2*65537:] = 4
        seen=[];original=self.module.np.isin
        def bounded(values,selected):
            seen.append(len(values));return original(values,selected)
        with patch.object(self.module.np,'isin',side_effect=bounded):
            risk=self.adapter._depth_geometry_risks(self.data,self.matrix)
        np.testing.assert_array_equal(risk['support'],[2*65535,2*65537])
        np.testing.assert_array_equal(risk['prime'],[2*65536])
        self.assertEqual(seen,[65536,2])
        self.assertLess(risk['bounds'][0][0],-2.)
        self.assertGreater(risk['bounds'][1][2],80.)  # Includes the final vertex chunk.
        self.assertTrue(self.adapter._depth_line_alpha(self.data,risk));self.assertTrue(risk['material_opaque'])

    def test_last_chunk_invalid_geometry_and_alpha_cannot_escape_equivalence_guards(self):
        for corruption in ('nonfinite vertex','nonfinite dimensions','oblique','colour alpha','material alpha','bad element'):
            with self.subTest(corruption=corruption):
                self.setUp();attributes,vertices=self.large_depth_fixture()
                if corruption=='nonfinite vertex':vertices[-1,2]=np.nan
                elif corruption=='nonfinite dimensions':attributes['line_dimensions']['value'][-1,0]=np.inf
                elif corruption=='oblique':vertices[-1,1]=1.
                elif corruption=='colour alpha':self.data.getColors.return_value[-1,3]=0.
                elif corruption=='material alpha':attributes['colors']['value'][-1,3]=np.nan
                elif corruption=='bad element':self.data.getIndices.return_value[-1,1]=len(vertices)
                risk=self.adapter._depth_geometry_risks(self.data,self.matrix)
                if corruption in ('colour alpha','material alpha'):
                    self.assertFalse(self.adapter._depth_line_alpha(self.data,risk) if corruption=='colour alpha' else risk['material_opaque'])
                else:
                    self.assertIsNone(risk)
                    self.assertIsNone(self.adapter._depth_geometry_risks(self.data,self.matrix))

    def test_large_float32_depth_transform_avoids_blas_and_preserves_world_bounds(self):
        self.large_depth_fixture()
        matrix = np.array([[0, 0, 2, 30], [0, 3, 0, 40], [-4, 0, 0, 50], [0, 0, 0, 1]], np.float32)
        transform = SimpleNamespace(getData=lambda: matrix)
        with patch.object(self.module.np, 'einsum', wraps=np.einsum) as contract:
            risk = self.adapter._depth_geometry_risks(self.data, transform)
        self.assertIsNotNone(risk)
        self.assertEqual([args.args[1].shape for args in contract.call_args_list], [(65536, 3), (2, 3)])
        for args in contract.call_args_list:
            self.assertIs(args.kwargs['optimize'], False)  # Optimised einsum may dispatch to BLAS.
            self.assertEqual(args.args[1].dtype, np.float32)
            self.assertEqual(args.args[2].dtype, np.float32)
        low, high = risk['bounds']
        self.assertTrue(np.all(low < [30, 40, 42]))
        self.assertTrue(np.all(high > [190, 40, 58]))
        self.data.getVertices.return_value[-1, 1] = 1
        self.adapter._depth_risks = None
        self.assertIsNone(self.adapter._depth_geometry_risks(self.data, transform))

    def test_empty_geometry_fails_soft_and_empty_element_stream_has_no_category_offsets(self):
        attributes,paths=self.depth_fixture()
        self.data.getVertices.return_value=np.empty((0,3),np.float32)
        self.assertIsNone(self.adapter._depth_geometry_risks(self.data,self.matrix))
        self.adapter._depth_risks=None
        self.data.getVertices.return_value=np.zeros((14,3),np.float32)
        self.data.getIndices.return_value=np.empty((0,2),np.int32)
        risk=self.adapter._depth_geometry_risks(self.data,self.matrix)
        self.assertEqual(risk['support'].size,0);self.assertEqual(risk['prime'].size,0)

    def test_depth_rejection_reasons_are_opt_in_distinct_and_bounded_per_context(self):
        attributes,paths=self.depth_fixture()
        output=object()
        changed=SimpleNamespace(**vars(self.camera))
        changed.getProjectionMatrix=lambda:SimpleNamespace(getData=lambda:np.eye(4)*2)
        self.logger.reset_mock()
        for _ in range(3):
            self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,output,changed,paths,self.view))
        self.logger.log.assert_not_called();self.assertEqual(self.adapter._depth_rejections,set())
        self.adapter._timing.enabled=True
        for _ in range(3):self.adapter.try_copy_completed_depth(self.gl,output,changed,paths,self.view)
        self.logger.log.assert_called_once_with('i','toolhead shared simulation depth rejected: %s','projection matrix differs')
        changed.getInverseWorldTransformation=lambda:SimpleNamespace(getData=lambda:np.eye(4)*3)
        self.adapter.try_copy_completed_depth(self.gl,output,changed,paths,self.view)
        self.assertEqual(len(self.adapter._depth_rejections),2)
        self.assertEqual(self.logger.log.call_args.args[-1],'view matrix differs')
        self.adapter._depth_rejections.update('seed reason '+str(i) for i in range(10))
        self.logger.reset_mock();self.allowed=False
        self.adapter.try_copy_completed_depth(self.gl,output,self.camera,paths,self.view)
        self.logger.log.assert_not_called();self.assertEqual(len(self.adapter._depth_rejections),12)
        self.context=object();self.adapter.render()
        self.assertEqual(self.adapter._depth_rejections,set())
        self.adapter.try_copy_completed_depth(self.gl,output,self.camera,paths,self.view)
        self.logger.log.assert_called_once()

    def test_depth_rejection_logging_failure_never_changes_false_result(self):
        attributes,paths=self.depth_fixture()
        self.adapter._timing.enabled=True
        self.logger.reset_mock()
        self.logger.log.side_effect=ValueError('logger unavailable')
        self.allowed=False
        for _ in range(3):self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
        self.assertEqual(len(self.adapter._depth_rejections),1)
        self.assertEqual(self.logger.log.call_count,1)

    def test_rejection_distinguishes_transform_colour_mode_and_native_source_changes(self):
        attributes,paths=self.depth_fixture()
        self.adapter._timing.enabled=True
        changed=list(paths[0]);changed[1]=SimpleNamespace(getData=lambda:np.eye(4)*2)
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,[tuple(changed)],self.view))
        self.assertEqual(self.logger.log.call_args.args[-1],'model matrix differs')
        self.view.getSimulationViewType.return_value=99
        self.adapter.render()
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
        self.assertEqual(self.logger.log.call_args.args[-1],'unsupported colour view mode')
        self.children=[SceneNode(self.data)]
        self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
        self.assertEqual(self.logger.log.call_args.args[-1],'native source eligibility or identity changed')


    def test_instanced_shadow_replaces_lower_only_and_preserves_normal_fractional_draw(self):
        self.instanced.render.return_value = True
        self.activate(.5)
        self.instanced.render.assert_called_once_with(self.geometry.mesh, Path('/native/shaders/layers3d_shadow.shader'),
            self.camera, 'world', 0, 6, self.view, self.gl)
        self.assertEqual(self.geometry.render.call_args_list, [call(self.normal, self.camera, 'world', [(6, 8)], self.gl)])
        self.normal.setUniformValue.assert_any_call('u_last_line_ratio', .5)

    def test_normal_lower_layer_mode_does_not_invoke_instanced_shadow(self):
        self.activate(1.)
        self.instanced.render.reset_mock()
        self.layer, self.path = 0, 1.
        self.adapter.render(); self.adapter.render()
        self.instanced.render.assert_not_called()

    def test_instanced_error_delegates_entire_frame_to_original_and_retires_owned_output(self):
        self.instanced.render.side_effect = RuntimeError('owned shader failure')
        self.activate(.5)
        self.assertFalse(self.adapter._owned_output)
        self.assertIs(self.adapter._failed_context, self.context)
        self.assertEqual(self.original.render.call_count, 3)
        self.geometry.render.assert_not_called()

    def test_real_cura_colour_getter_failure_uses_public_bytes_once_only_when_needed(self):
        attributes,paths=self.depth_fixture()
        colours=self.data.getColors.return_value.astype(np.float32)
        self.data.getColors.side_effect=ValueError('The truth value of an array with more than one element is ambiguous')
        self.data.getColorsAsByteArray=Mock(return_value=colours.tobytes())
        self.view.getSimulationViewType.return_value=0
        self.adapter.render()
        self.assertTrue(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
        self.data.getColors.assert_not_called();self.data.getColorsAsByteArray.assert_not_called()
        self.view.getSimulationViewType.return_value=1
        self.adapter.render()
        for _ in range(3):self.assertTrue(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
        self.data.getColors.assert_called_once();self.data.getColorsAsByteArray.assert_called_once()
        self.assertTrue(self.adapter._depth_risks[2]['colour_opaque'])

    def test_public_colour_bytes_wrong_size_or_missing_retains_specific_negative_reason(self):
        for raw in (None,b'not float32 colours'):
            with self.subTest(raw=raw):
                self.setUp();attributes,paths=self.depth_fixture()
                self.data.getColors.side_effect=ValueError('ambiguous ndarray truth')
                self.data.getColorsAsByteArray=Mock(return_value=raw)
                self.adapter._timing.enabled=True;self.logger.reset_mock()
                for _ in range(3):self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
                self.assertEqual(self.logger.log.call_count,1)
                self.assertIn('native float32 colour bytes have wrong size',self.logger.log.call_args.args[-1])
                self.data.getColorsAsByteArray.assert_called_once()

    def test_geometry_exception_reason_is_cached_without_changing_to_generic_rejection(self):
        attributes,paths=self.depth_fixture()
        self.data.getAttribute.side_effect=KeyError('line_dimensions')
        self.adapter._timing.enabled=True;self.logger.reset_mock()
        for _ in range(3):self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,paths,self.view))
        self.assertEqual(self.logger.log.call_count,1)
        self.assertIn("geometry metadata invalid (KeyError: 'line_dimensions')",self.logger.log.call_args.args[-1])
        self.data.getAttribute.assert_called_once()
        self.assertEqual(len(self.adapter._depth_rejections),1)

    def test_malformed_partial_metadata_stays_distinct_from_cached_geometry_failures(self):
        attributes,paths=self.depth_fixture()
        changed=list(paths[0]);changed[4]={'u_last_vertex':None}
        self.adapter._timing.enabled=True;self.logger.reset_mock()
        for _ in range(3):self.assertFalse(self.adapter.try_copy_completed_depth(self.gl,object(),self.camera,[tuple(changed)],self.view))
        self.logger.log.assert_called_once_with('i','toolhead shared simulation depth rejected: %s','malformed geometry/view metadata')
        self.adapter._cache.try_copy_depth.assert_not_called()

    def test_missing_previous_types_with_broken_native_colour_getter_preserves_public_colour_bytes(self):
        attributes,paths=self.depth_fixture()
        colours=self.data.getColors.return_value.astype(np.float32)
        self.data.hasAttribute=lambda name:False
        self.data.attributeNames=lambda:list(attributes)
        self.data.getNormals=lambda:None;self.data.getUVCoordinates=lambda:None
        self.data.getColors.side_effect=ValueError('ambiguous ndarray truth')
        self.data.getColorsAsByteArray=Mock(return_value=colours.tobytes())
        mesh=self.module.path_mesh(self.data)
        np.testing.assert_array_equal(mesh.colors,colours)
        np.testing.assert_array_equal(mesh.attributes['prev_line_types']['value'],np.r_[8,attributes['line_types']['value'][:-1]])
        self.assertFalse(mesh.colors.flags.writeable)
        self.data.getColorsAsByteArray.assert_called_once()
        self.assertNotIn('prev_line_types',attributes)
        self.data.getColorsAsByteArray.return_value=b'wrong size'
        with self.assertRaisesRegex(ValueError,'colour bytes have wrong size'):self.module.path_mesh(self.data)
