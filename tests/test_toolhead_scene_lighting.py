"""Lighting persists through Cura's full-frame/cached-composition alternation."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np


class Mesh:
    def __init__(self, vertices=None, indices=None, normals=None):
        self.vertices, self.indices, self.normals = vertices, indices, normals
    def getVertices(self): return self.vertices
    def getIndices(self): return self.indices


class Batch:
    RenderType = SimpleNamespace(Solid=1, Transparent=2)
    RenderMode = SimpleNamespace(Triangles=4, Lines=1)
    BlendMode = SimpleNamespace(Additive=2)
    rendered = []
    def __init__(self, shader, **options):
        self.shader = shader
        self.renderType = options.get("type", 1)
        self.renderMode = options.get("mode", 4)
        self.renderRange = options.get("range")
        self.options, self.items = options, []
    def addItem(self, transform, mesh, normal_transformation=None):
        self.items.append(dict(transformation=transform, mesh=mesh, normal_transformation=normal_transformation))
    def render(self, camera): self.rendered.append(self)


class SceneLightingTests(unittest.TestCase):
    def setUp(self):
        shader = Mock()
        self.matrix_factory = Mock(side_effect=lambda *_: Mock())
        self.gl = Mock(GL_DEPTH_BUFFER_BIT=256, GL_COLOR_BUFFER_BIT=16384, GL_LEQUAL=515, GL_LESS=513, GL_BLEND=3042)
        opengl = SimpleNamespace(createShaderProgram=lambda *_: shader, getBindingsObject=lambda: self.gl)
        surfaces = {
            "UM.Math.Matrix": SimpleNamespace(Matrix=self.matrix_factory),
            "UM.Mesh.MeshData": SimpleNamespace(MeshData=Mesh),
            "UM.PluginRegistry": SimpleNamespace(PluginRegistry=Mock()),
            "UM.View.GL.OpenGLContext": SimpleNamespace(OpenGLContext=Mock()),
            "UM.View.GL.ShaderProgram": SimpleNamespace(ShaderProgram=Mock()),
            "UM.View.GL.OpenGL": SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda: opengl)),
            "UM.View.RenderBatch": SimpleNamespace(RenderBatch=Batch),
        }
        context = patch.dict(sys.modules, surfaces)
        context.start()
        self.addCleanup(context.stop)
        path = Path(__file__).resolve().parents[1] / "mpf/toolhead/ToolheadSceneLighting.py"
        spec = importlib.util.spec_from_file_location("mpf.toolhead.scene_lighting_contract", path)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.lighting = self.module.ToolheadSceneLighting()
        self.node = SimpleNamespace(apply_attached_lights=Mock(), light_dimensions=lambda: (250, 250, 250),
            scene_lighting_effects=lambda: (True, True), scene_lighting_signature=lambda: (),
            render_transformation=lambda: self.transform)
        # These geometry/depth contracts bypass the independent image cache.
        self.lighting._frame_cache = SimpleNamespace(draw=lambda gl, camera, bounds, transform, state, render, **options: render(camera))
        self.native = Batch(shader, type=2)
        self.transform = SimpleNamespace(getData=lambda: np.eye(4))
        self.native.addItem(self.transform, Mesh(np.array([[-125, 0, -125], [125, 0, -125], [125, 0, 125]], dtype=float)))
        self.batches = [self.native]
        self.renderer = SimpleNamespace(getBatches=lambda: self.batches)
        self.root, self.camera = object(), object()
        Batch.rendered = []

    def draw(self):
        Batch.rendered = []
        self.lighting.draw(self.node, self.renderer, self.camera, None, self.root)
        return [batch for batch in Batch.rendered if batch.options.get("blend_mode") == 2]

    def test_cached_redraws_keep_plate_receivers_after_renderer_clears_batches(self):
        full = self.draw()
        self.assertEqual(len(full), 1)
        mesh = full[0].items[0]["mesh"]
        self.batches.clear()  # Actual QtRenderer.endRendering contract.
        for _ in range(30):
            cached = self.draw()
            self.assertEqual(len(cached), 1)
            self.assertIs(cached[0].items[0]["mesh"], mesh)
            self.assertIs(cached[0].items[0]["transformation"], self.transform)
        self.node.apply_attached_lights.assert_called()

    def test_full_frame_replaces_receivers_and_scene_change_invalidates_retained_geometry(self):
        self.draw()
        replacement = Batch(Mock(), type=2)
        replacement_transform = SimpleNamespace(getData=lambda: np.eye(4) * 2)
        replacement.addItem(replacement_transform, self.native.items[0]["mesh"])
        self.batches = [replacement]
        self.assertIs(self.draw()[0].items[0]["transformation"], replacement_transform)
        self.batches = []
        self.root = object()
        self.assertEqual(self.draw(), [])

    def test_depth_prepass_and_lighting_share_geometry_and_restore_gl_state(self):
        lit = self.draw()[0]
        depth = Batch.rendered[0]
        self.assertEqual(depth.items, lit.items)
        depth.options["state_setup_callback"](self.gl)
        self.gl.glColorMask.assert_called_with(False, False, False, False)
        depth.options["state_teardown_callback"](self.gl)
        self.gl.glColorMask.assert_called_with(True, True, True, True)
        lit.options["state_setup_callback"](self.gl)
        self.gl.glDepthFunc.assert_called_with(self.gl.GL_LEQUAL)
        lit.options["state_teardown_callback"](self.gl)
        self.gl.glDepthFunc.assert_called_with(self.gl.GL_LESS)
        self.gl.glDisable.assert_called_with(self.gl.GL_BLEND)

    def test_bed_toggle_omits_receiving_geometry(self):
        self.node.scene_lighting_effects = lambda: (False, True)
        self.assertEqual(self.draw(), [])
        self.node.scene_lighting_effects = lambda: (True, False)
        self.assertEqual(len(self.draw()), 1)

    def test_plate_shape_scan_is_cached(self):
        with patch.object(self.module.np, "ptp", wraps=np.ptp) as scan:
            self.draw()
            self.draw()
            self.assertEqual(scan.call_count, 1)

    def test_normal_matrix_is_reused_until_receiver_transform_changes(self):
        first = self.draw()[0].items[0]["normal_transformation"]
        self.batches.clear()
        for _ in range(10):
            self.assertIs(self.draw()[0].items[0]["normal_transformation"], first)
        self.transform.getData = lambda: np.diag([2., 3., 4., 1.])
        changed = self.draw()[0].items[0]["normal_transformation"]
        self.assertIsNot(changed, first)
        self.assertEqual(sum(bool(call.args) for call in self.matrix_factory.call_args_list), 2)
        first.invert.assert_called_once()
        changed.invert.assert_called_once()

    def test_model_lighting_keeps_outer_walls_and_the_selected_top_layer_prefix(self):
        data = Mock()
        data.getElementCounts.return_value = {0: np.int64(100), 1: np.int64(100), 2: np.int64(20)}
        data.getLayer.return_value = None
        child = Mock()
        child.callDecoration.return_value = data
        child.getWorldTransformation.return_value = self.transform
        self.root = SimpleNamespace(getAllChildren=lambda: [child])
        self.camera = Mock()
        self.camera.getInverseWorldTransformation.return_value = self.transform
        self.camera.getProjectionMatrix.return_value = self.transform
        lights = [(np.zeros(3), 30)]
        self.node.scene_light_bounds = lambda: lights
        view = Mock()
        view.getCompatibilityMode.return_value = False
        view.getCurrentLayer.return_value = 2
        view.getMinimumLayer.return_value = 0
        view.getCurrentPath.return_value = 1
        geometry = Mock(mesh=data)
        geometry.exterior_geometry.return_value = None
        geometry.ranges.return_value = [(0, 202)]
        self.lighting._depth_cache = Mock()
        self.lighting._prefix_depth_cache = Mock()
        path_shader = Mock()
        with patch.object(self.module, "ToolheadPathGeometry", return_value=geometry), \
                patch.object(self.module, "create_path_shader", return_value=path_shader):
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        geometry.ranges.assert_called_once_with(self.transform, (0, 202), lights)
        self.assertEqual(geometry.render.call_args.args[3], [(0, 202)])
        path_shader.setUniformValue.assert_any_call("u_lightingFirstTopElement", 200)

    def test_path_range_keeps_lower_bound_and_partial_move_prefix(self):
        polygon = SimpleNamespace(data=np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=float))
        data = SimpleNamespace(getElementCounts=lambda: {0: 6, 1: 8, 2: 6},
                               getLayer=lambda _: SimpleNamespace(polygons=[polygon]))
        view = SimpleNamespace(getCurrentLayer=lambda: 2, getMinimumLayer=lambda: 1, getCurrentPath=lambda: 1.5)
        self.assertEqual(self.module.layer_range(data, view), (6, 18, ([4., 5., 6.], [7., 8., 9.], .5)))
        view.getCurrentPath = lambda: float("nan")
        self.assertIsNone(self.module.layer_range(data, view))

    def test_compact_outer_boundaries_and_native_top_prefix_have_separate_receiver_ranges(self):
        data = Mock()
        data.getElementCounts.return_value = {0: 100, 1: 100, 2: 20}
        data.getLayer.return_value = None
        child = Mock()
        child.callDecoration.return_value = data
        child.getWorldTransformation.return_value = self.transform
        self.root = SimpleNamespace(getAllChildren=lambda: [child])
        self.camera = Mock()
        self.camera.getInverseWorldTransformation.return_value = self.transform
        self.camera.getProjectionMatrix.return_value = self.transform
        lights = [(np.zeros(3), 30)]
        self.node.scene_light_bounds = lambda: lights
        view = Mock()
        view.getCompatibilityMode.return_value = False
        view.getCurrentLayer.return_value = 2
        view.getMinimumLayer.return_value = 1
        view.getCurrentPath.return_value = 1
        geometry, exterior = Mock(mesh=data), Mock(mesh=data)
        geometry.exterior_geometry.return_value = exterior
        geometry.exterior_range.return_value = (40, 80)
        geometry.ranges.return_value = [(200, 202)]
        exterior.ranges.return_value = [(40, 80)]
        self.lighting._depth_cache = Mock()
        self.lighting._prefix_depth_cache = Mock()
        path_shader = Mock()
        with patch.object(self.module, "ToolheadPathGeometry", return_value=geometry), \
                patch.object(self.module, "create_path_shader", return_value=path_shader):
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        geometry.exterior_range.assert_called_once_with(100, 200)
        exterior.ranges.assert_called_once_with(self.transform, (40, 80), lights)
        geometry.ranges.assert_called_once_with(self.transform, (200, 202), lights)
        self.assertEqual(exterior.render.call_args.args[3], [(40, 80)])
        self.assertEqual(geometry.render.call_args.args[3], [(200, 202)])

    def test_prefix_append_fractional_motion_and_backwards_rebuild_keep_static_depth(self):
        import math
        class Cache:
            def __init__(self): self.key = None; self.rebuilds = self.appends = 0
            def restore(cache, gl, key, draw, *, append=None):
                if key != cache.key:
                    if cache.key is not None and append is not None and append(cache.key, key):
                        cache.appends += 1
                    else:
                        cache.rebuilds += 1
                        draw()
                cache.key = key
        base, prefix = Cache(), Cache()
        self.lighting._depth_cache, self.lighting._prefix_depth_cache = base, prefix
        self.lighting._mesh_shader = Mock()
        self.camera = Mock()
        self.camera.getInverseWorldTransformation.return_value = self.transform
        self.camera.getProjectionMatrix.return_value = self.transform
        state = {}
        shader = Mock()
        shader.setUniformValue.side_effect = lambda name, value: state.__setitem__(name, value)
        draws = []
        geometry = Mock(mesh=object())
        geometry.render.side_effect = lambda shader, camera, transform, ranges, gl: draws.append(
            (state["u_depthOnly"], ranges, math.isfinite(state["u_last_vertex"][0]), state.get("u_last_line_ratio")))
        def frame(completed, fraction=None, camera_identity="same", lower_start=0):
            draws.clear()
            partial = fraction is not None
            uniforms = {"u_last_vertex": [1, 0, 0] if partial else [math.nan]*3,
                "u_next_vertex": [2, 0, 0] if partial else [math.nan]*3,
                "u_last_line_ratio": fraction if partial else 1.0, "u_lightingFirstTopElement": 6}
            end = completed + (2 if partial else 0)
            paths = [(geometry, self.transform, (lower_start, end), [(geometry, [(lower_start, end)])], uniforms, 6)]
            self.lighting._draw_lighting(self.gl, self.camera, [], [], paths, shader, camera_identity, [])
            return list(draws)
        first = frame(8, .25)
        self.assertEqual(first[:3], [(1, [(0, 6)], False, None),
                                    (1, [(6, 8)], False, 1.0), (1, [(8, 10)], True, .25)])
        self.assertEqual((base.rebuilds, prefix.rebuilds, prefix.appends), (1, 1, 0))
        fractional = frame(8, .75)
        self.assertEqual([draw[:3] for draw in fractional if draw[0] == 1], [(1, [(8, 10)], True)])
        self.assertEqual((base.rebuilds, prefix.rebuilds, prefix.appends), (1, 1, 0))
        forward = frame(10, .25)
        self.assertEqual([draw[:3] for draw in forward if draw[0] == 1],
                         [(1, [(8, 10)], False), (1, [(10, 12)], True)])
        self.assertEqual((base.rebuilds, prefix.rebuilds, prefix.appends), (1, 1, 1))
        backwards = frame(6, .5)
        self.assertEqual([draw[:3] for draw in backwards if draw[0] == 1], [(1, [(6, 8)], True)])
        self.assertEqual((base.rebuilds, prefix.rebuilds, prefix.appends), (1, 2, 1))
        frame(8, camera_identity="new camera")
        self.assertEqual((base.rebuilds, prefix.rebuilds), (2, 3))
        frame(8, camera_identity="new camera", lower_start=2)
        self.assertEqual((base.rebuilds, prefix.rebuilds), (3, 4))
        self.gl.glColorMask.assert_called_with(True, True, True, True)
        self.gl.glDepthFunc.assert_called_with(self.gl.GL_LESS)
        self.gl.glDepthMask.assert_called_with(True)

    def test_partial_path_finds_the_next_polygon_and_never_exceeds_the_selected_layer(self):
        polygons = [SimpleNamespace(data=np.array([[0, 0, 0], [1, 0, 0]])),
                    SimpleNamespace(data=np.array([[2, 0, 0], [3, 0, 0], [4, 0, 0]]))]
        data = SimpleNamespace(getElementCounts=lambda: {0: 2, 1: 6},
            getLayer=lambda _: SimpleNamespace(polygons=polygons))
        view = SimpleNamespace(getCurrentLayer=lambda: 1, getMinimumLayer=lambda: 0, getCurrentPath=lambda: 2.5)
        self.assertEqual(self.module.layer_range(data, view), (0, 8, ([2., 0., 0.], [3., 0., 0.], .5)))
        view.getCurrentPath = lambda: 4.5  # Last point has no following segment.
        self.assertEqual(self.module.layer_range(data, view), (0, 8, None))
        view.getCurrentLayer = lambda: -1
        self.assertIsNone(self.module.layer_range(data, view))

    def native_shader_fixture(self, directory):
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser['shaders'] = {
            'vertex': 'void main() { v_line_type = a_line_type; }',
            'vertex41core': '#version 410\nvoid main() { v_line_type = a_line_type; }',
            'fragment': 'varying vec4 v_color;\nvoid main() { if (v_line_type == 6) discard; gl_FragColor = v_color; }',
            'fragment41core': '#version 410\nout vec4 frag_color;\nvoid main() { if (v_line_type == 6) discard; frag_color = v_color; }',
        }
        parser['defaults'] = {'u_show_infill': 'True', 'u_min_feedrate': '0', 'u_max_feedrate': '200', 'u_colour': '(1, 0, 0, 1)'}
        parser['bindings'] = {'a_vertex': 'vertex', 'u_modelMatrix': 'model_matrix'}
        with (Path(directory) / 'layers.shader').open('w') as stream: parser.write(stream)
        geometry = ('out vec4 f_color;\nvoid myEmitVertex(vec3 vertex, vec4 color, vec3 normal, vec4 pos) '
                    '{ f_color = color; EmitVertex(); }\n'
                    'void main() { highp mat4 viewProjectionMatrix = u_projectionMatrix; }')
        parser['shaders']['geometry41core'] = geometry
        parser['shaders']['geometry'] = geometry
        with (Path(directory) / 'layers3d.shader').open('w') as stream: parser.write(stream)
        self.module.PluginRegistry.getInstance.return_value.getPluginPath.return_value = directory

    def test_owned_shader_sources_keep_native_discards_for_all_gl_and_compatibility_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            self.native_shader_fixture(directory)
            for legacy in (False, True):
                for compatibility in (False, True):
                    with self.subTest(legacy=legacy, compatibility=compatibility):
                        _parser, vertex, geometry, fragment = self.module.shader_sources(legacy, compatibility)
                        self.assertIn('lightSurface', fragment)
                        if compatibility:
                            self.assertIsNone(geometry)
                            self.assertIn(('varying' if legacy else 'out') + ' vec3 f_vertex;', vertex)
                            self.assertIn('f_vertex = (u_modelMatrix * a_vertex).xyz;', vertex)
                            self.assertIn('if (v_line_type == 6) discard;', fragment)
                            self.assertIn('vec4(lightSurface(f_vertex, f_normal, v_color.rgb), v_color.a)', fragment)
                            self.assertEqual(fragment.count('out vec4 frag_color;'), 0 if legacy else 1)
                        else:
                            self.assertIn('bool receivesLight', geometry)
                            self.assertIn('u_depthOnly == 0', geometry)
                            self.assertIn('v_line_type[0] != 1', geometry)
                            self.assertIn('u_drawElementStart + gl_PrimitiveIDIn * 2', geometry)

    def test_path_shader_compilation_reports_each_failed_stage_and_applies_native_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            self.native_shader_fixture(directory)
            self.module.OpenGLContext.isLegacyOpenGL.return_value = False
            for failed_stage in ('setVertexShader', 'setFragmentShader', 'setGeometryShader'):
                with self.subTest(stage=failed_stage):
                    shader = Mock()
                    getattr(shader, failed_stage).return_value = False
                    self.module.ShaderProgram.return_value = shader
                    with self.assertRaisesRegex(RuntimeError, 'could not compile'):
                        self.module.create_path_shader(False)
                    shader.build.assert_not_called()
            shader = Mock()
            self.module.ShaderProgram.return_value = shader
            self.assertIs(self.module.create_path_shader(False), shader)
            shader.build.assert_called_once()
            shader.setUniformValue.assert_any_call('u_min_feedrate', 0.0)
            shader.setUniformValue.assert_any_call('u_max_feedrate', 200.0)
            values = {call.args[0]: call.args[1] for call in shader.setUniformValue.call_args_list}
            self.assertIsInstance(values['u_min_feedrate'], float)
            self.assertIs(values['u_show_infill'], True)
            self.assertEqual(values['u_colour'], (1, 0, 0, 1))
            shader.addBinding.assert_any_call('a_vertex', 'vertex')
            shader.addBinding.assert_any_call('u_viewPosition', 'view_position')
            self.module.ShaderProgram.return_value = Mock()
            compatibility = self.module.create_path_shader(True)
            compatibility.setGeometryShader.assert_not_called()

    def test_owned_forward_and_deferred_geometry_preserves_current_colour_and_uses_native_shadow_material(self):
        with tempfile.TemporaryDirectory() as directory:
            self.native_shader_fixture(directory)
            for surface in (False, True):
                with self.subTest(surface=surface):
                    _parser, _vertex, geometry, _fragment = self.module.shader_sources(False, False, surface)
                    self.assertIn('u_drawElementStart + gl_PrimitiveIDIn * 2 < u_lightingShadowElements'
                                  ' ? vec4(0.4, 0.4, 0.4, 0.9) : color;', geometry)
                    self.assertLess(geometry.index('uniform int u_lightingShadowElements;'),
                                    geometry.index('void myEmitVertex('))
                    self.assertEqual(geometry.count('uniform int u_lightingShadowElements;'), 1)
                    self.assertIn('v_line_type[0] != 1', geometry)

    def test_explicit_shadow_mode_drives_forward_receivers_and_frame_invalidation(self):
        data, child, view, geometry = self.path_fixture()
        mode = Mock(return_value=True)
        self.renderer.getRenderPass = Mock(return_value=SimpleNamespace(getCompletedLayerShadowMode=mode))
        states = []
        self.lighting._frame_cache = SimpleNamespace(draw=lambda gl, camera, bounds, transform, state, render, **options:
            (states.append(state), render(camera)))
        shader = Mock()
        with patch.object(self.module, 'ToolheadPathGeometry', return_value=geometry), \
                patch.object(self.module, 'create_path_shader', return_value=shader):
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            shader.setUniformValue.assert_any_call('u_lightingShadowElements', 6)
            mode.return_value = False
            shader.reset_mock()
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            shader.setUniformValue.assert_any_call('u_lightingShadowElements', 0)
            self.assertNotEqual(states[0], states[1])
            self.renderer.getRenderPass.return_value = object()  # Original/native pass has no capability.
            shader.reset_mock()
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            shader.setUniformValue.assert_any_call('u_lightingShadowElements', 0)
            self.assertEqual(states[1], states[2])
        self.assertEqual(mode.call_count, 2)
        self.assertEqual(self.renderer.getRenderPass.call_count, 3)

    def path_fixture(self, compatibility=False):
        data = Mock()
        data.getElementCounts.return_value = {0: 6, 1: 8}
        data.getLayer.return_value = None
        child = Mock()
        child.callDecoration.return_value = data
        child.getWorldTransformation.return_value = self.transform
        self.root = SimpleNamespace(getAllChildren=lambda: [child])
        self.camera = Mock()
        self.camera.getInverseWorldTransformation.return_value = self.transform
        self.camera.getProjectionMatrix.return_value = self.transform
        self.node.scene_light_bounds = lambda: [(np.zeros(3), 30)]
        view = Mock()
        view.getCompatibilityMode.return_value = compatibility
        view.getCurrentLayer.return_value = 1
        view.getMinimumLayer.return_value = 0
        view.getCurrentPath.return_value = 2
        geometry = Mock(mesh=data)
        geometry.exterior_geometry.return_value = None
        geometry.ranges.return_value = [(0, 10)]
        self.lighting._depth_cache = Mock()
        self.lighting._prefix_depth_cache = Mock()
        return data, child, view, geometry

    def test_compatibility_receives_current_top_mesh_but_skips_absent_jumps(self):
        data, child, view, geometry = self.path_fixture(True)
        self.lighting._depth_cache_failed = True  # Exercise actual depth batches.
        mesh = Mesh(np.array([[0, 1, 0], [1, 1, 0], [0, 1, 1]]))
        view.getCurrentLayerMesh.return_value = mesh
        view.getCurrentLayerJumps.return_value = None
        with patch.object(self.module, 'ToolheadPathGeometry', return_value=geometry), \
                patch.object(self.module, 'create_path_shader', return_value=Mock()) as shaders:
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        shaders.assert_called_once_with(True)
        lit = [batch for batch in Batch.rendered if batch.options.get('blend_mode') == Batch.BlendMode.Additive]
        self.assertTrue(any(item['mesh'] is mesh for batch in lit for item in batch.items))
        self.assertTrue(any(item['mesh'] is mesh for batch in Batch.rendered if batch.renderType == Batch.RenderType.Solid for item in batch.items))

    def test_failed_prefix_cache_falls_back_once_and_keeps_correct_completed_depth(self):
        data, child, view, geometry = self.path_fixture()
        self.lighting._prefix_depth_cache.restore.side_effect = RuntimeError('incompatible depth destination')
        with patch.object(self.module, 'ToolheadPathGeometry', return_value=geometry), \
                patch.object(self.module, 'create_path_shader', return_value=Mock()):
            for _ in range(2): self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertTrue(self.lighting._depth_cache_failed)
        self.lighting._prefix_depth_cache.restore.assert_called_once()
        ranges = [call.args[3] for call in geometry.render.call_args_list]
        self.assertEqual(ranges, [[(0, 6)], [(6, 10)], [(0, 10)]] * 2)
        self.gl.glColorMask.assert_called_with(True, True, True, True)
        self.gl.glDepthFunc.assert_called_with(self.gl.GL_LESS)
        self.gl.glDepthMask.assert_called_with(True)

    def test_native_solid_receivers_only_occlude_and_non_triangle_or_empty_meshes_are_ignored(self):
        model = Mesh(np.array([[0, 0, 0], [1, 1, 0], [0, 0, 1]]))
        solid = Batch(Mock(), type=Batch.RenderType.Solid)
        solid.addItem(self.transform, model)
        lines = Batch(Mock(), mode=Batch.RenderMode.Lines)
        lines.addItem(self.transform, model)
        empty = Batch(Mock())
        empty.addItem(self.transform, Mesh())
        empty.addItem(self.transform, Mesh(np.zeros((2, 3))))
        self.batches.extend([solid, lines, empty])
        lit = self.draw()
        self.assertFalse(any(item['mesh'] is model for batch in lit for item in batch.items))
        depth = [batch for batch in Batch.rendered if batch.renderType == Batch.RenderType.Solid]
        self.assertEqual(sum(item['mesh'] is model for batch in depth for item in batch.items), 1)
        batch = self.lighting._batch(Mock(), self.transform, model, bounds=(2, 4), lines=True)
        self.assertEqual(batch.renderMode, Batch.RenderMode.Lines)
        self.assertEqual(batch.renderRange, (2, 4))

    def test_image_cache_state_is_stable_and_invalidates_for_lights_transform_and_progress(self):
        data, child, view, geometry = self.path_fixture()
        states, images = [], []
        def image_draw(gl, camera, bounds, transform, state, render, **options):
            self.assertIsNone(bounds)  # Full viewport preserves depth-cache keys.
            self.assertTrue(options['additive'])
            states.append(state)
            if len(states) == 1 or states[-2] != state:
                images.append(state)
                render(camera)
        self.lighting._frame_cache = SimpleNamespace(draw=image_draw)
        with patch.object(self.module, 'ToolheadPathGeometry', return_value=geometry) as factory, \
                patch.object(self.module, 'create_path_shader', return_value=Mock()) as shaders:
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            self.batches.clear()
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            self.assertEqual(states[0], states[1])
            self.node.scene_lighting_signature = lambda: ('light moved', .35)
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            self.transform.getData = lambda: np.diag([2., 1., 1., 1.])
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            view.getCurrentPath.return_value = 3
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(len(images), 4)
        factory.assert_called_once_with(data)
        shaders.assert_called_once_with(False)
        self.assertEqual(geometry.ranges.call_count, len(images))

    def test_unavailable_mesh_shader_skips_receivers_and_frame_work(self):
        module = sys.modules['UM.View.GL.OpenGL']
        with patch.object(module.OpenGL, 'getInstance', return_value=SimpleNamespace(createShaderProgram=lambda *_: None)):
            self.assertEqual(self.draw(), [])
        self.node.apply_attached_lights.assert_not_called()
        self.gl.glClear.assert_not_called()

    def deferred_fixture(self):
        data, child, view, geometry = self.path_fixture()
        self.module.OpenGLContext.isLegacyOpenGL.return_value = False
        data.getAttribute.return_value = {'value': np.array([[.4, .2]], dtype=np.float32)}
        surface_shader, mesh_shader = Mock(), Mock()
        self.module.ShaderProgram.return_value = mesh_shader
        state = {}
        surface_shader.setUniformValue.side_effect = lambda name, value: state.__setitem__(name, value)
        captures = []
        geometry.render.side_effect = lambda shader, camera, transform, ranges, gl: captures.append(
            (shader is surface_shader, ranges, state.get('u_lightingFirstTopElement')))
        class Cache:
            key = completed = None
            rebuilds = appends = 0
            copy_depth = Mock()
            shade = Mock()
            owned_target = object()
            def try_seed_depth(cache, callback):
                return False if callback is None else callback(cache.owned_target)
            def prepare(cache, gl, key, completed, rebuild, append):
                if key != cache.key or cache.completed is None or any(new < old for old, new in zip(cache.completed, completed, strict=True)):
                    cache.rebuilds += 1; rebuild()
                elif completed != cache.completed:
                    cache.appends += 1; append(cache.completed, completed)
                cache.key, cache.completed = key, completed
        cache = Cache()
        patches = [patch.object(self.module, 'ToolheadPathGeometry', return_value=geometry),
            patch.object(self.module, 'create_path_shader', side_effect=lambda compatibility, surface=False: surface_shader if surface else Mock()),
            patch('mpf.toolhead.ToolheadSurfaceCache.ToolheadSurfaceCache', return_value=cache)]
        for context in patches:
            context.start(); self.addCleanup(context.stop)
        return data, child, view, geometry, cache, captures, surface_shader, mesh_shader

    def test_deferred_surface_geometry_is_independent_of_light_motion_and_appends_only_new_prefix(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        def frame():
            captures.clear()
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            return list(captures)
        self.assertEqual(frame(), [(True, [(0, 6)], 6), (True, [(0, 10)], 6)])
        self.node.scene_lighting_signature = lambda: ('moving light', .35)
        self.assertEqual(frame(), [])
        view.getCurrentPath.return_value = 3
        self.assertEqual(frame(), [(True, [(10, 12)], 6)])
        self.assertEqual((cache.rebuilds, cache.appends), (1, 1))
        self.assertEqual(cache.shade.call_count, 3)
        self.assertEqual(cache.copy_depth.call_count, 3)
        mesh_shader.addBinding.assert_any_call('u_modelMatrix', 'model_matrix')
        mesh_shader.setUniformValue.assert_any_call('u_hasColour', 0)
        self.assertEqual(cache.shade.call_args.kwargs['padding'], self.lighting._surface_padding)
        geometry.ranges.assert_not_called()

    def test_deferred_material_mode_changes_invalidate_without_disabling_model_light(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        mode = Mock(return_value=True)
        self.renderer.getRenderPass = lambda _name: SimpleNamespace(getCompletedLayerShadowMode=mode)
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        surface_shader.setUniformValue.assert_any_call('u_lightingShadowElements', 6)
        self.assertEqual(cache.rebuilds, 1)
        mode.return_value = False
        surface_shader.reset_mock()
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        surface_shader.setUniformValue.assert_any_call('u_lightingShadowElements', 0)
        self.assertEqual(cache.rebuilds, 2)
        mode.return_value = None
        surface_shader.reset_mock()
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(cache.rebuilds, 3)
        self.assertNotIn("models_available", cache.shade.call_args.kwargs)
        self.assertTrue(self.node.scene_lighting_effects()[1])
        mode.return_value = True
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(cache.rebuilds, 4)
        self.assertNotIn("models_available", cache.shade.call_args.kwargs)
        self.assertEqual(cache.shade.call_count, 4)

    def test_deferred_fraction_stays_transient_and_model_toggle_preserves_occlusion_without_colour(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        polygon = SimpleNamespace(data=np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]]))
        data.getLayer.return_value = SimpleNamespace(polygons=[polygon])
        view.getCurrentPath.return_value = 2.25
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(cache.completed, (10,))
        self.assertEqual(captures, [(True, [(0, 6)], 6), (True, [(0, 10)], 6),
            (False, [(10, 12)], 6), (False, [(10, 12)], 6)])
        captures.clear()
        view.getCurrentPath.return_value = 2.75
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(captures, [(False, [(10, 12)], 6)] * 2)
        captures.clear()
        self.node.scene_lighting_effects = lambda: (True, False)
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(captures, [(False, [(10, 12)], 6)])  # Only occlusion depth.
        self.assertEqual(cache.rebuilds, 1)
        view.getCurrentPath.return_value = 1.5
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(cache.completed, (8,))
        self.assertEqual(cache.rebuilds, 2)
        self.gl.glColorMask.assert_called_with(True, True, True, True)
        self.gl.glDepthFunc.assert_called_with(self.gl.GL_LESS)

    def test_deferred_compact_boundaries_and_current_layer_use_separate_surface_ranges(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        exterior = Mock()
        geometry.exterior_geometry.return_value = exterior
        geometry.exterior_range.return_value = (0, 2)
        exterior.ranges.return_value = [(0, 2)]
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(captures, [(True, [(0, 6)], 6), (True, [(6, 10)], 6)])
        exterior.render.assert_called_once_with(surface_shader, self.camera, self.transform, [(0, 2)], self.gl)
        self.assertEqual(cache.rebuilds, 1)
        geometry.ranges.assert_not_called()
        exterior.ranges.assert_not_called()

    def test_deferred_nonreceivers_occlude_before_receiver_depth_and_colour_and_append_is_receiver_only(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        state, draws = {}, []
        surface_shader.setUniformValue.side_effect = lambda name, value: state.__setitem__(name, value)
        def record(shader, camera, transform, ranges, gl):
            draws.append((state['u_surfaceDepthOnly'], state['u_lightingFirstTopElement'], ranges,
                          gl.glDepthFunc.call_args.args, gl.glDepthMask.call_args.args,
                          gl.glColorMask.call_args.args))
        geometry.render.side_effect = record
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(draws, [
            (1, 6, [(0, 6)], (self.gl.GL_LESS,), (True,), (False,) * 4),
            (0, 6, [(0, 10)], (self.gl.GL_LEQUAL,), (True,), (True,) * 4)])
        draws.clear()
        view.getCurrentPath.return_value = 3
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(draws, [(0, 6, [(10, 12)], (self.gl.GL_LEQUAL,), (True,), (True,) * 4)])
        self.assertEqual((cache.rebuilds, cache.appends), (1, 1))

    def test_deferred_current_layer_only_does_not_submit_a_nonreceiver_depth_draw(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        view.getMinimumLayer.return_value = 1
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(captures, [(True, [(6, 10)], 6)])
        self.assertEqual(cache.completed, (10,))

    def test_deferred_owned_depth_provider_skips_only_nonreceiver_draw_and_preserves_receivers(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        provider = Mock(return_value=True)
        self.renderer.getRenderPass = Mock(return_value=SimpleNamespace(try_copy_completed_depth=provider))
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.renderer.getRenderPass.assert_called_once_with('simulationview')
        args = provider.call_args.args
        self.assertEqual(args[:3], (self.gl, cache.owned_target, self.camera))
        self.assertIs(args[3][0][0], geometry)
        self.assertEqual(args[3][0][2], (0, 10))
        self.assertIs(args[4], view)
        self.assertEqual(captures, [(True, [(0, 10)], 6)])
        view.getCurrentPath.return_value = 3
        captures.clear()
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(captures, [(True, [(10, 12)], 6)])
        provider.assert_called_once()  # A retained append needs no seed.
        provider.return_value = False
        view.getMinimumLayer.return_value = 0
        self.camera.getProjectionMatrix = lambda: SimpleNamespace(getData=lambda: np.eye(4) * 2)
        captures.clear()
        self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertEqual(captures, [(True, [(0, 6)], 6), (True, [(0, 12)], 6)])

    def test_deferred_depth_provider_failure_uses_cleared_forward_fallback(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        provider = Mock(side_effect=RuntimeError('depth provider unavailable'))
        self.renderer.getRenderPass = lambda _name: SimpleNamespace(try_copy_completed_depth=provider)
        with self.assertLogs(self.module.__name__, level='WARNING'):
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertTrue(self.lighting._surface_failed)
        self.assertIsNone(self.lighting._surface_cache)
        self.gl.glClear.assert_any_call(self.gl.GL_COLOR_BUFFER_BIT | self.gl.GL_DEPTH_BUFFER_BIT)

    def test_deferred_failure_clears_owned_image_then_uses_forward_fallback_on_later_frames(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        cache.shade.side_effect = RuntimeError('deferred draw interrupted')
        with patch.object(self.lighting, '_draw_lighting') as forward:
            with self.assertLogs(self.module.__name__, level='WARNING') as logs:
                self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
            self.assertTrue(self.lighting._surface_failed)
            self.assertIsNone(self.lighting._surface_cache)
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertIn('deferred draw interrupted', logs.output[0])
        self.assertEqual(forward.call_count, 2)
        self.assertEqual(geometry.ranges.call_count, 2)
        self.assertEqual(forward.call_args.args[4][0][3], [(geometry, [(0, 10)])])
        cache.shade.assert_called_once()
        self.gl.glClearColor.assert_called_with(0, 0, 0, 0)

    def test_deferred_shader_compile_failure_keeps_forward_path_available(self):
        data, child, view, geometry, cache, captures, surface_shader, mesh_shader = self.deferred_fixture()
        mesh_shader.setVertexShader.return_value = False
        with patch.object(self.lighting, '_draw_lighting') as forward:
            with self.assertLogs(self.module.__name__, level='WARNING') as logs:
                self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        self.assertIn('could not compile', logs.output[0])
        self.assertTrue(self.lighting._surface_failed)
        cache.copy_depth.assert_not_called()
        forward.assert_called_once()

    def test_forward_culling_is_skipped_when_models_are_disabled_or_no_lights_reach_scene(self):
        data, child, view, geometry = self.path_fixture()
        self.node.scene_lighting_effects = lambda: (True, False)
        self.node.scene_light_bounds = Mock(side_effect=AssertionError('disabled model lighting needs no bounds'))
        with patch.object(self.module, 'ToolheadPathGeometry', return_value=geometry), \
                patch.object(self.module, 'create_path_shader', return_value=Mock()):
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        geometry.ranges.assert_not_called()
        self.node.scene_light_bounds.assert_not_called()
        geometry.exterior_geometry.assert_not_called()
        self.node.scene_lighting_effects = lambda: (False, True)
        self.node.scene_light_bounds = Mock(return_value=[])
        self.batches.clear()
        self.lighting._native_receivers = ()
        with patch.object(self.lighting, '_draw_lighting') as forward:
            self.lighting.draw(self.node, self.renderer, self.camera, view, self.root)
        forward.assert_not_called()
        geometry.ranges.assert_not_called()
        self.node.scene_light_bounds.assert_called_once()

    def test_forward_resolves_light_bounds_once_for_multiple_receivers(self):
        data, child, view, geometry = self.path_fixture()
        lights = [(np.zeros(3), 30)]
        self.node.scene_light_bounds = Mock(return_value=lights)
        paths = [(geometry, self.transform, (0, 10), [], {}, 6),
                 (geometry, self.transform, (6, 14), [], {}, 8)]
        selected = self.lighting._forward_ranges(paths, self.node)
        self.node.scene_light_bounds.assert_called_once()
        self.assertEqual([path[3] for path in selected], [[(geometry, [(0, 10)])]] * 2)
        self.assertEqual([call.args[1] for call in geometry.ranges.call_args_list], [(0, 10), (6, 14)])
        self.assertEqual([path[3] for path in paths], [[], []])  # Surface snapshots stay immutable.

    def test_surface_shader_keeps_category_guard_without_light_culling_and_rejects_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            self.native_shader_fixture(directory)
            _parser, _vertex, geometry, fragment = self.module.shader_sources(False, False, surface=True)
        self.assertIn('gl_PrimitiveIDIn * 2', geometry)
        self.assertIn('v_line_type[0] != 1', geometry)
        self.assertIn('uniform int u_surfaceDepthOnly;', geometry)
        self.assertIn('if (u_surfaceDepthOnly == 1 ? !(', geometry)
        self.assertLess(geometry.index('if (u_surfaceDepthOnly'), geometry.index('highp mat4 viewProjectionMatrix'))
        self.assertNotIn('receivesLight', geometry)
        self.assertIn('gl_FragCoord.z', fragment)
        self.assertIn('surface_position = vec4(f_vertex', fragment)
        with self.assertRaisesRegex(RuntimeError, 'core geometry'):
            self.module.shader_sources(True, False, surface=True)
        with self.assertRaisesRegex(RuntimeError, 'core geometry'):
            self.module.shader_sources(False, True, surface=True)
