"""Cura mesh and renderer API contracts; these doubles do not certify GPU pixels."""
import importlib.util
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from mpf.geometry.ToolheadGeometry import mesh_from_arrays


class SceneNode:
    def __init__(self, **_args):
        self.mesh = None

    def setSelectable(self, value):
        self.selectable = value

    def setCalculateBoundingBox(self, value):
        self.bounding_box = value

    def setVisible(self, value):
        self.visible = value

    def isVisible(self):
        return self.visible

    def setMeshData(self, mesh):
        self.mesh = mesh

    def getMeshData(self):
        return self.mesh

    def getWorldPosition(self):
        return SimpleNamespace(x=10, y=20, z=30)

    def getWorldTransformation(self):
        return "toolhead transform"


class ToolheadSceneTests(unittest.TestCase):
    def setUp(self):
        self.shader = Mock()
        self.opaque_factory = Mock(return_value=self.shader)
        self.gl = Mock(GL_TRUE=1, GL_DEPTH_BUFFER_BIT=256, GL_DEPTH_TEST=2929, GL_BLEND=3042)
        self.opengl = SimpleNamespace(createShaderProgram=Mock(return_value=self.shader),
                                      getBindingsObject=lambda: self.gl)
        self.resources = SimpleNamespace(Shaders="shaders", getPath=Mock(return_value="default.shader"))
        self.batches = []
        def batch(shader, **options):
            result = Mock(shader=shader, options=options)
            self.batches.append(result)
            return result
        batch.RenderType = SimpleNamespace(Solid=1, Transparent=2)
        batch.RenderMode = SimpleNamespace(Triangles=1)
        batch.BlendMode = SimpleNamespace(Normal=1)
        identity = SimpleNamespace(getData=lambda: np.eye(4))
        self.camera = SimpleNamespace(getProjectionMatrix=lambda: identity,
            getInverseWorldTransformation=lambda: identity)
        scene = SimpleNamespace(getActiveCamera=lambda: self.camera)
        application = SimpleNamespace(getController=lambda: SimpleNamespace(getScene=lambda: scene),
            getTheme=lambda: SimpleNamespace(getColor=lambda name: SimpleNamespace(getRgb=lambda: (200, 210, 220, 255))))
        self.context = object()
        surfaces = {
            "mpf.toolhead.ToolheadOpaqueShader": SimpleNamespace(create_opaque_shader=self.opaque_factory),
            "PyQt6.QtGui": SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda: self.context)),
            "UM.Logger": SimpleNamespace(Logger=Mock()),
            "mpf.toolhead.ToolheadFrameCache": SimpleNamespace(ToolheadFrameCache=lambda **kwargs:
                SimpleNamespace(draw=lambda gl, camera, bounds, transform, state, render, **kwargs: render(camera))),
            "UM.Mesh.MeshData": SimpleNamespace(MeshData=lambda **values: SimpleNamespace(**values)),
            "UM.Math.Color": SimpleNamespace(Color=lambda *values: values),
            "UM.Math.Matrix": SimpleNamespace(Matrix=lambda values: SimpleNamespace(getData=lambda: values)),
            "UM.Resources": SimpleNamespace(Resources=self.resources),
            "UM.Scene.SceneNode": SimpleNamespace(SceneNode=SceneNode),
            "UM.View.GL.OpenGL": SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda: self.opengl)),
            "UM.View.RenderBatch": SimpleNamespace(RenderBatch=batch),
            "UM.Application": SimpleNamespace(Application=SimpleNamespace(getInstance=lambda: application)),
        }
        source = pathlib.Path(__file__).resolve().parents[1] / "mpf/toolhead/ToolheadSceneNode.py"
        spec = importlib.util.spec_from_file_location("mpf.toolhead.toolhead_scene_contract", source)
        module = importlib.util.module_from_spec(spec)
        context = patch.dict(sys.modules, surfaces)
        context.start()
        self.addCleanup(context.stop)
        spec.loader.exec_module(module)
        self.module = module
        self.node = module.ToolheadSceneNode()
        self.node._frame_cache = SimpleNamespace(draw=lambda gl, camera, bounds, transform, state, render, **kwargs: render(camera))
        self.original = Mock()
        self.original.getName.return_value = "composite"
        self.original.getSize.return_value = (640, 480)
        self.original.getPriority.return_value = 999
        self.renderer = SimpleNamespace(queueNode=Mock(), current=self.original)
        self.renderer.getBatches = lambda: ()
        self.renderer.getRenderPass = lambda name: self.renderer.current if name == "composite" else None
        self.renderer.removeRenderPass = Mock(side_effect=lambda owner: setattr(self.renderer, "current", None))
        self.renderer.addRenderPass = Mock(side_effect=lambda owner: setattr(self.renderer, "current", owner))

    def render_frame(self):
        self.node.render(self.renderer)
        self.renderer.current.render()

    def mesh(self, alphas=(1,)):
        triangles = [((1 + index * 3, 2, 3), (2 + index * 3, 2, 3), (1 + index * 3, 3, 3))
                     for index in range(len(alphas))]
        return mesh_from_arrays(triangles, [(0, 0, 0, alpha) for alpha in alphas])

    def test_indicator_is_hidden_and_cannot_change_selection_or_scene_bounds(self):
        self.assertFalse(self.node.visible)
        self.assertFalse(self.node.selectable)
        self.assertFalse(self.node.bounding_box)
        self.render_frame()
        self.renderer.queueNode.assert_not_called()
        self.opengl.createShaderProgram.assert_not_called()

    def test_hidden_source_bodies_do_not_use_translucent_rotor_budget(self):
        from mpf.geometry.ToolheadGeometry import mesh_metadata
        mesh=self.mesh();metadata=mesh_metadata(mesh)
        metadata['bodies']=[dict(name='Body',source='unknown',centre=None,axis=None) for _ in range(66)]
        mesh=mesh_from_arrays(np.repeat(mesh.triangles[:1],66,axis=0),[(1,0,0,0)]*66,body_ids=np.arange(66),metadata=metadata)
        self.node.set_model(mesh,(0,0,0))
        self.assertEqual(self.node._transparent_body_count,0)

    def test_invisible_rotor_body_and_faces_stop_motion_without_hiding_static_head(self):
        from mpf.geometry.ToolheadGeometry import mesh_metadata
        mesh=self.mesh((1,1));metadata=mesh_metadata(mesh)
        metadata['bodies']=[dict(name='Body',source='unknown',centre=None,axis=None)]*2
        mesh=mesh_from_arrays(mesh.triangles,mesh.colours,[0,1],body_ids=[0,1],metadata=metadata)
        node=self.node
        node.set_model(mesh,(0,0,0));node.setVisible(True)
        rotor=dict(body=0,centre=[0,0,0],axis=[0,0,1],rpm=3000,direction=1)
        node.set_rotors([rotor],{})
        self.assertTrue(node.rotors_moving())
        for bodies,faces in (({'0':0},{}),({}, {'0':0})):
            node.set_opacity_overrides(bodies,faces)
            self.assertFalse(node.rotors_moving())
            self.assertTrue(node.visible_for_render())
            self.assertIsNotNone(node.getMeshData())
            node.set_opacity_overrides({},{})
            self.assertTrue(node.rotors_moving())

    def test_body_and_face_colour_rebuilds_preserve_alpha_and_rotor_ranges(self):
        node = self.node
        node.set_colour_overrides({'0': '#ff0000'}, {})  # No imported mesh yet.
        mesh = self.mesh((.3, 1))
        node.set_model(mesh, (0, 0, 0))
        node.setVisible(True)
        before = mesh.colours.copy()
        node.set_colour_overrides({'0': '#00ff00'}, {'0': '#ff0000', '1': 'imported'})
        np.testing.assert_array_equal(node._body_colours, {'0': '#00ff00'})
        self.assertEqual(node._face_colours, {'0': '#ff0000', '1': 'imported'})
        self.assertEqual(node._transparent_body_count, 1)
        np.testing.assert_array_equal(mesh.colours, before)
        packed = node._translucent_mesh.colors
        self.assertTrue(np.any(np.all(packed[:, :3] == [1, 0, 0], axis=1)))
        node.set_colour_overrides({'0': '#00ff00'}, {'0': '#ff0000', '1': 'imported'})
        node.set_colour_overrides({}, {})
        self.assertEqual((node._body_colours, node._face_colours), ({}, {}))

    def test_retired_environment_textures_are_removed_from_cached_shader(self):
        from mpf.toolhead.ToolheadEnvironment import TEXTURE_UNIT,DEPTH_UNIT
        textures={}
        def register(unit,value):
            if value is None: textures.pop(unit,None)
            else: textures[unit]=value
        self.shader.setTexture.side_effect=register
        for alphas in ((1,),(.4,)):
            node=self.node
            node.set_model(self.mesh(alphas),(0,0,0))
            storage=object();environment=Mock()
            environment.apply.side_effect=lambda shader,storage=storage: [shader.setTexture(unit,storage) for unit in (TEXTURE_UNIT,DEPTH_UNIT)]
            node._environment=environment
            node._draw(self.camera)
            self.assertEqual(textures,{TEXTURE_UNIT:storage,DEPTH_UNIT:storage})
            node.set_scene(object(),object())
            environment.close.assert_called_once()
            node.set_lighting_enabled(False)
            node._draw(self.camera)
            self.assertEqual(textures,{})
        self.shader.setTexture.reset_mock()
        self.shader.setTexture.side_effect=lambda unit,value: (_ for _ in ()).throw(RuntimeError('registration failed')) if unit==TEXTURE_UNIT else None
        with self.assertRaisesRegex(RuntimeError,'registration failed'): node._draw(self.camera)
        self.shader.setTexture.assert_any_call(DEPTH_UNIT,None)

    def test_reflection_disable_retires_maps_and_invalidates_frame_without_mesh_rebuild(self):
        from mpf.toolhead.ToolheadEnvironment import TEXTURE_UNIT, DEPTH_UNIT
        for alphas in ((1,), (.4,)):
            node = self.node
            node.set_reflections_enabled(True)
            node.set_model(self.mesh(alphas), (0, 0, 0))
            opaque, translucent = node.getMeshData(), node._translucent_mesh
            textures = {}
            self.shader.setTexture.side_effect = lambda unit, value, textures=textures: textures.pop(unit, None) if value is None else textures.update({unit: value})
            storage = object()
            owner = Mock(available=True, revision=7)
            owner.apply.side_effect = lambda shader, storage=storage: [shader.setTexture(unit, storage) for unit in (TEXTURE_UNIT, DEPTH_UNIT)]
            node._environment = owner
            cache = Mock()
            cache.draw.side_effect = lambda gl, camera, bounds, transform, state, render, **kw: render(camera)
            node._frame_cache = cache
            node.draw(self.camera)
            state = cache.draw.call_args.args[4]
            self.assertEqual(textures, {TEXTURE_UNIT: storage, DEPTH_UNIT: storage})
            node.set_reflections_enabled(False)
            node.set_reflections_enabled(False)
            owner.close.assert_called_once()
            self.assertIsNone(node._environment)
            self.assertIsNone(node._environment_scene)
            node.draw(self.camera)
            self.assertNotEqual(cache.draw.call_args.args[4], state)
            self.assertEqual(textures, {})
            self.shader.setUniformValue.assert_any_call("u_environmentEnabled", 0)
            self.assertTrue(node._lighting_enabled)
            self.assertIs(node.getMeshData(), opaque)
            self.assertIs(node._translucent_mesh, translucent)

    def test_native_mesh_is_not_transformed_or_lit_and_uses_stock_colour_shader(self):
        mesh = SimpleNamespace(vertices=np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]]))
        self.node.set_native_model(mesh)
        self.node.setVisible(True)
        self.node._frame_cache = Mock()
        self.node._attached_lights = [{"invalid": "must never be inspected"}]
        self.node._scene_lighting = Mock()
        self.render_frame()
        self.assertIs(self.node.getMeshData(), mesh)
        np.testing.assert_array_equal(mesh.vertices, [[1, 2, 3], [4, 5, 6], [7, 8, 9]])
        self.resources.getPath.assert_called_once_with("shaders", "color.shader")
        self.shader.setUniformValue.assert_called_once_with("u_color", (200, 210, 220, 255))
        self.opaque_factory.assert_not_called()
        self.assertIsNone(self.node._frame_cache)  # context preparation never creates it
        self.assertEqual(len(self.batches), 1)
        self.assertEqual(self.batches[0].options, {"type": 2})
        self.batches[0].addItem.assert_called_once_with("toolhead transform", mesh=mesh)
        self.render_frame()
        self.assertEqual(self.opengl.createShaderProgram.call_count, 1)
        self.context = object()
        self.render_frame()
        self.assertEqual(self.opengl.createShaderProgram.call_count, 2)

    def test_custom_to_native_restores_simulation_and_discards_custom_resources(self):
        self.node.set_model(self.mesh((1, .4)), (0, 0, 0))
        owned = Mock()
        self.node._simulation_pass = owned
        self.node._simulation_active = True
        self.node._frame_cache = Mock()
        self.node._scene_lighting = Mock()
        mesh = object()
        self.node.set_native_model(mesh)
        owned.close.assert_called_once_with()
        self.assertIsNone(self.node._simulation_pass)
        self.assertFalse(self.node._simulation_active)
        self.assertIsNone(self.node._translucent_mesh)
        self.assertIsNone(self.node._frame_cache)
        self.assertIsNone(self.node._scene_lighting)
        self.assertEqual(self.node._attached_lights, [])
        self.assertEqual(self.node._opacity, 1)
        self.node.setVisible(True)
        self.node._simulation_active = True  # even an accidental request cannot install adapter
        self.node.render(self.renderer)
        self.assertIsNone(self.node._simulation_pass)
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.assertFalse(self.node._native_model)
        self.assertIsNot(self.node.getMeshData(), mesh)

    def test_empty_native_mesh_does_not_draw(self):
        self.node.set_native_model(None)
        self.node.draw(self.camera)
        self.opengl.createShaderProgram.assert_not_called()
        self.assertEqual(self.batches, [])

    def test_occlusion_copies_visible_paths_and_solid_depth_and_retains_batches(self):
        from mpf.toolhead.ToolheadOcclusion import ToolheadOcclusion
        helper = ToolheadOcclusion()
        provider = SimpleNamespace(get_visible_depth_revision=Mock(return_value="fraction-.5"),
                                   copy_visible_depth=Mock(return_value=True))
        mesh = Mock()
        mesh.getVertices.return_value = np.array([[-100,-1,-100], [100,-1,-100], [100,-1,100]])
        transform = SimpleNamespace(getData=lambda: np.eye(4))
        native = SimpleNamespace(renderMode=1, renderType=1, items=[{"mesh":mesh,"transformation":transform}])
        renderer = SimpleNamespace(getBatches=lambda: [native], getRenderPass=lambda name: provider)
        camera = SimpleNamespace(getProjectionMatrix=lambda: transform, getInverseWorldTransformation=lambda: transform,
                                 getWorldPosition=lambda: None, getCameraLightPosition=lambda: None)
        self.gl.glGetIntegerv.side_effect = lambda name: (0,0,800,600) if name == 0x0BA2 else 0
        revision, seed = helper.prepare(renderer, camera, self.gl)
        output = object()
        seed(self.gl, output, camera, (0,0,800,600), (128,160,64,96))
        provider.copy_visible_depth.assert_called_once_with(self.gl, output, camera, (0,0,800,600), (128,160,64,96), "fraction-.5")
        self.assertEqual(len(self.batches), 1)
        self.batches[0].addItem.assert_called_once_with(transform, mesh=mesh)
        self.gl.glColorMask.assert_called_with(True,True,True,True)
        renderer.getBatches = lambda: []
        self.assertEqual(helper.prepare(renderer, camera, self.gl)[0], revision)
        provider.copy_visible_depth.return_value = False
        self.assertFalse(seed(self.gl, output, camera, (0,0,800,600), (128,160,64,96)))
        self.assertEqual(len(self.batches), 1)
        provider.get_visible_depth_revision.return_value = None
        self.assertIsNotNone(helper.prepare(renderer, camera, self.gl)[1])

    def test_occlusion_mismatch_and_native_mode_do_not_hide_head(self):
        self.node._occlusion = SimpleNamespace(prepare=Mock(side_effect=RuntimeError("unavailable")))
        self.node.prepare_occlusion(self.renderer, self.camera, self.gl)
        self.assertIsNone(self.node._depth_seed)
        self.assertEqual(self.node.render_failure(), "")
        old = self.node._occlusion
        self.node.set_native_model(object())
        self.node.prepare_occlusion(self.renderer, self.camera, self.gl)
        old.prepare.assert_called_once()
        self.assertIsNone(self.node._occlusion)

    def test_transparent_bed_and_frame_blend_without_becoming_solid_occluders(self):
        from mpf.toolhead.ToolheadOcclusion import ToolheadOcclusion
        helper = ToolheadOcclusion()
        transform = SimpleNamespace(getData=lambda: np.eye(4))
        mesh = Mock()
        mesh.getVertices.return_value = np.array([[-100,0,-100], [100,0,-100], [100,0,100]])
        native = SimpleNamespace(renderMode=1, renderType=2, shader=object(), backfaceCull=True, renderRange=None,
            items=[{'mesh':mesh,'transformation':transform}])
        renderer = SimpleNamespace(getBatches=lambda:[native], getRenderPass=lambda name:None)
        self.gl.glGetIntegerv.side_effect = lambda name: (0,0,800,600) if name == 0x0BA2 else 0
        revision, seed = helper.prepare(renderer, self.camera, self.gl)
        self.assertTrue(seed(self.gl, object(), self.camera, (0,0,800,600), (0,0,64,64)))
        self.assertEqual(self.batches, [], 'transparent plane must not write solid depth')
        helper._transparency = SimpleNamespace(draw=Mock(return_value=True))
        output = object()
        self.assertTrue(helper.overlay(self.gl, output, self.camera))
        helper._transparency.draw.assert_called_once()
        self.assertEqual(helper._transparency.draw.call_args.args[3][0].items, native.items)
        renderer.getBatches = lambda:[]
        self.assertEqual(helper.prepare(renderer, self.camera, self.gl)[0], revision)

    def test_all_transparent_model_and_printer_surfaces_participate_in_depth_blending(self):
        from mpf.toolhead.ToolheadOcclusion import ToolheadOcclusion
        helper = ToolheadOcclusion()
        ghost, frame = Mock(), Mock()
        for mesh in (ghost, frame):
            mesh.getVertices.return_value = np.zeros((3,3))
        transform = SimpleNamespace(getData=lambda:np.eye(4))
        batch = SimpleNamespace(renderType=2, renderMode=1, shader=object(), backfaceCull=True,
            renderRange=None, items=[dict(mesh=mesh, transformation=transform) for mesh in (ghost,frame)])
        renderer = SimpleNamespace(getBatches=lambda:[batch], getRenderPass=lambda name:None)
        self.gl.glGetIntegerv.side_effect = lambda name:(0,0,800,600) if name==0x0BA2 else 0
        helper.prepare(renderer, self.camera, self.gl)
        self.assertEqual([item['mesh'] for item in helper._transparent[0].items], [ghost, frame])

    def test_occlusion_skips_line_overlays_empty_geometry_and_msaa(self):
        from mpf.toolhead.ToolheadOcclusion import ToolheadOcclusion
        helper = ToolheadOcclusion()
        provider = SimpleNamespace(get_visible_depth_revision=lambda *args: "full-depth", copy_visible_depth=lambda *args: True)
        empty = Mock()
        empty.getVertices.return_value = None
        renderer = SimpleNamespace(getBatches=lambda: [SimpleNamespace(renderMode=2,items=[]),
            SimpleNamespace(renderMode=1,renderType=1,items=[{"mesh":empty}])], getRenderPass=lambda name:provider)
        self.gl.glGetIntegerv.side_effect = lambda name: (0,0,800,600) if name==0x0BA2 else 0
        _, seed = helper.prepare(renderer, self.camera, self.gl)
        self.assertTrue(seed(self.gl, object(), self.camera, (0,0,800,600), (0,0,64,64)))
        self.assertEqual(self.batches, [])
        self.gl.glGetIntegerv.side_effect = lambda name: (0,0,800,600) if name==0x0BA2 else 4
        self.assertEqual(helper.prepare(renderer, self.camera, self.gl), (None,None))

    def test_anchor_and_z_up_rotation_preserve_surface_winding_and_unit_normals(self):
        model = self.mesh()
        self.node.set_model(model, (1, 2, 3))
        mesh = self.node.getMeshData()
        np.testing.assert_array_equal(mesh.vertices, [(0, 0, 0), (1, 0, 0), (0, 0, -1)])
        np.testing.assert_array_equal(mesh.normals, [(0, 1, 0)] * 3)
        np.testing.assert_array_equal(mesh.colors, [(0, 0, 0, 1)] * 3)
        np.testing.assert_array_equal(model.triangles[0, 0], (1, 2, 3))

    def test_projection_mode_switch_sets_world_view_ray_from_active_camera(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        view = np.eye(4)
        view[:3, :3] = ((0, 0, -1), (0, 1, 0), (1, 0, 0))
        self.camera.getInverseWorldTransformation = lambda: SimpleNamespace(getData=lambda: view)
        for orthographic in (1, 0, 1):
            projection = np.eye(4)
            if not orthographic: projection[3] = [0, 0, -1, 0]
            self.camera.getProjectionMatrix = lambda projection=projection: SimpleNamespace(getData=lambda: projection)
            self.render_frame()
            self.shader.setUniformValue.assert_any_call("u_orthographic", orthographic)
            self.shader.setUniformValue.assert_any_call("u_viewDirection", [1., 0., 0.])

    def test_opaque_faces_use_depth_writing_batch_and_black_shader_fallback(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.render_frame()
        self.shader.setUniformValue.assert_any_call("u_opacity", 1.0)
        self.opaque_factory.assert_called_once_with()
        self.opengl.createShaderProgram.assert_not_called()
        self.renderer.queueNode.assert_not_called()  # never in the underlying scene FBO
        self.assertEqual(self.batches[0].options["type"], 1)
        self.assertNotIn((("u_depthOnly", 1), {}), self.shader.setUniformValue.call_args_list)
        self.assertEqual(len(self.batches), 1)
        self.batches[0].addItem.assert_called_once_with("toolhead transform", mesh=self.node.getMeshData())
        self.batches[0].render.assert_called_once_with(self.camera)
        self.render_frame()
        self.assertEqual(self.opaque_factory.call_count, 1)

    def test_opaque_and_translucent_faces_have_separate_batches_without_duplication(self):
        self.node.set_model(self.mesh((1, .4, .999)), (0, 0, 0))
        self.node.setVisible(True)
        self.render_frame()
        self.assertEqual(len(self.node.getMeshData().vertices), 6)
        self.assertEqual(len(self.node._translucent_mesh.vertices), 3)
        np.testing.assert_allclose(self.node._translucent_mesh.colors[:, 3], [.4] * 3)
        self.assertEqual(len(self.batches), 3)
        self.assertEqual(self.batches[0].options["type"], 1)
        self.assertEqual(self.batches[2].options["type"], 2)
        self.batches[2].addItem.assert_called_once_with("toolhead transform", mesh=self.node._translucent_mesh)

    def test_fully_translucent_model_renders_with_no_opaque_mesh(self):
        self.node.set_model(self.mesh((.4,)), (0, 0, 0))
        self.assertIsNone(self.node.getMeshData())
        self.node.setVisible(True)
        self.render_frame()
        self.assertEqual(len(self.batches), 1)
        self.assertEqual(self.batches[0].options["type"], 2)
        self.assertTrue(self.batches[0].options["backface_cull"])

    def test_model_replacement_retires_translucent_buffers_and_hiding_stops_rendering(self):
        self.node.set_model(self.mesh((.4,)), (0, 0, 0))
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.assertIsNone(self.node._translucent_mesh)
        self.node.setVisible(True)
        self.render_frame()
        self.batches.clear()
        self.node.setVisible(False)
        self.render_frame()
        self.renderer.queueNode.assert_not_called()
        self.assertEqual(self.batches, [])

    def test_final_composition_precedes_head_and_clears_only_destination_depth(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        events = []
        self.original.render.side_effect = lambda: events.append("composite")
        self.gl.glClear.side_effect = lambda bits: events.append(("clear", bits))
        draw = self.node.draw
        with patch.object(self.node, "draw", side_effect=lambda camera: (events.append("toolhead"), draw(camera))):
            self.render_frame()
        self.assertEqual(events, ["composite", ("clear", self.gl.GL_DEPTH_BUFFER_BIT), "toolhead",
                                  ("clear", self.gl.GL_DEPTH_BUFFER_BIT)])
        self.gl.glDepthMask.assert_called_with(self.gl.GL_TRUE)
        self.gl.glDisable.assert_any_call(self.gl.GL_DEPTH_TEST)
        self.gl.glDisable.assert_any_call(self.gl.GL_BLEND)
        self.assertEqual(self.gl.glClear.call_count, 2)  # Qt controls start with clean depth

    def test_cached_composite_redraw_includes_head_and_does_not_wrap_twice(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.render_frame()
        owner = self.renderer.current
        self.node.render(self.renderer)
        self.assertIs(self.renderer.current, owner)
        owner.render()  # QtRenderer.reRenderLast calls only the last pass
        self.assertEqual(len(self.batches), 2)
        self.renderer.removeRenderPass.assert_called_once_with(self.original)
        self.assertEqual(owner.getName(), "composite")
        self.assertEqual(owner.getPriority(), 999)
        self.assertEqual(owner.getSize(), (640, 480))
        shader = object()
        owner.setCompositeShader(shader)
        owner.setLayerBindings(["default", "selection", "simulationview"])
        self.original.setCompositeShader.assert_called_once_with(shader)
        self.original.setLayerBindings.assert_called_once_with(["default", "selection", "simulationview"])

    def test_close_restores_original_pass_and_cannot_remove_a_new_owner(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.node.render(self.renderer)
        self.node.close()
        self.assertIs(self.renderer.current, self.original)
        self.node.render(self.renderer)
        newer = Mock()
        self.renderer.current = newer
        self.node.close()
        self.assertIs(self.renderer.current, newer)

    def test_simulation_pass_ownership_restores_on_disable_scene_change_and_close(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        view, root = object(), object()
        self.node.set_scene(view, root)
        simulation = SimpleNamespace(getName=lambda: "simulationview")
        passes = {"composite": self.original, "simulationview": simulation}
        renderer = SimpleNamespace(getRenderPass=passes.get,
            removeRenderPass=lambda owner: passes.pop(owner.getName()),
            addRenderPass=lambda owner: passes.__setitem__(owner.getName(), owner))
        def make_pass(original, renderer, view, root, eligible):
            owned = SimpleNamespace(original=original, renderer=renderer, view=view, root=root,
                eligible=eligible, getName=original.getName)
            def close():
                if renderer.getRenderPass(owned.getName()) is owned:
                    renderer.removeRenderPass(owned)
                    renderer.addRenderPass(original)
            owned.close = close
            return owned
        with patch.dict(sys.modules, {"mpf.cura.ToolheadSimulationPass":
                SimpleNamespace(ToolheadSimulationPass=make_pass)}):
            self.node.set_simulation_active(True)
            self.node.render(renderer)
            owned = passes["simulationview"]
            self.assertIs(owned.original, simulation)
            self.assertTrue(owned.eligible())
            self.node.render(renderer)
            self.assertIs(passes["simulationview"], owned)
            self.node.set_simulation_active(False)
            self.assertIs(passes["simulationview"], simulation)
            self.assertFalse(owned.eligible())
            self.node.set_simulation_active(True)
            self.node.render(renderer)
            self.node.set_scene(view, object())
            self.assertIs(passes["simulationview"], simulation)
            self.node.render(renderer)
            newer = SimpleNamespace(getName=lambda: "simulationview")
            passes["simulationview"] = newer
            self.node.close()
            self.assertIs(passes["simulationview"], newer)
            self.assertIs(passes["composite"], self.original)

    def test_missing_camera_and_missing_composite_do_not_draw_or_clear(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.camera = None
        self.render_frame()
        self.gl.glClear.assert_not_called()
        self.assertEqual(self.batches, [])
        self.node.close()
        self.renderer.current = None
        self.node.render(self.renderer)
        self.assertIsNone(self.node._composite_pass)

    def test_owned_shader_releases_when_batch_draw_raises(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.prepare_render_context()
        batch = Mock()
        batch.render.side_effect = RuntimeError('injected batch failure')
        factory = Mock(return_value=batch)
        factory.RenderType = SimpleNamespace(Solid=1, Transparent=2)
        with patch.object(self.module, 'RenderBatch', factory):
            with self.assertRaisesRegex(RuntimeError, 'injected batch failure'):
                self.node._draw_mesh(self.camera, self.node.getMeshData(), None, None, None)
        self.shader.release.assert_called_once()

    def test_draw_failure_restores_gl_state(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        with patch.object(self.node, "draw", side_effect=RuntimeError("shader failed")):
            self.render_frame()
        self.assertIn("shader failed", self.node.render_failure())
        self.gl.glDisable.assert_any_call(self.gl.GL_DEPTH_TEST)
        self.gl.glDisable.assert_any_call(self.gl.GL_BLEND)

    def test_scene_light_failure_keeps_head_visible_and_toggle_allows_retry(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.node._attached_lights = [object()]
        self.node._root = object()
        lighting = Mock()
        lighting.draw.side_effect = KeyError("line_types")
        self.node.prepare_render_context()
        self.node.render(self.renderer)
        self.node._scene_lighting = lighting
        with patch.object(self.node, "scene_light_bounds", return_value=[([0, 0, 0], 20)]), patch.object(self.node, "draw") as draw:
            self.render_frame()
            self.render_frame()
            self.assertEqual(draw.call_count, 2)
            self.assertEqual(lighting.draw.call_count, 1)
            self.assertEqual(self.node.render_failure(), "")
            self.node.set_scene_lighting(False, False)
            self.node.set_scene_lighting(True, True)
            self.render_frame()
            self.assertEqual(lighting.draw.call_count, 2)

    def test_context_replacement_retires_owned_shaders_and_recovers_failure_latch(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.render_frame()
        old_shader = self.node._opaque_shader_cache[1]
        self.node.fail_rendering(RuntimeError("retired context"))
        self.render_frame()
        self.assertEqual(self.opaque_factory.call_count, 1)
        self.context = object()
        self.render_frame()
        self.assertEqual(self.node.render_failure(), "")
        self.assertEqual(self.opaque_factory.call_count, 2)
        # Factory can return the same test double; creation count proves retry.
        self.assertIs(self.node._opaque_shader_cache[1], old_shader)
        self.context = None
        self.render_frame()
        self.assertIn("context unavailable", self.node.render_failure())

    def test_private_pose_keeps_scene_transform_unchanged_and_lights_follow_nozzle(self):
        class Matrix:
            def __init__(self): self.data = np.eye(4)
            def setByTranslation(self, position): self.data[:3, 3] = position.x, position.y, position.z
            def getData(self): return self.data
        self.node.set_model(self.mesh(), (1, 2, 3))
        point = SimpleNamespace(x=30, y=40, z=50)
        with patch.dict(sys.modules, {"UM.Math.Matrix": SimpleNamespace(Matrix=Matrix)}):
            self.node.set_render_position(point)
            first_normal = self.node._render_normal
            self.node.set_render_position(point)
        self.assertIs(self.node.render_position(), point)
        self.assertIs(self.node._render_normal, first_normal)
        np.testing.assert_array_equal(self.node.render_transformation().getData()[:3, 3], [30, 40, 50])
        self.assertEqual(self.node.getWorldTransformation(), "toolhead transform")
        self.node.set_attached_lights([dict(position=[1, 2, 3], direction=[0, 0, -1], colour="#ffffff", range=25)])
        position, reach = self.node.scene_light_bounds()[0]
        np.testing.assert_allclose(position, [30, 39.85, 50])
        self.assertEqual(reach, 25)

    def test_scene_binding_reuses_lighting_and_retires_it_on_world_change(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        root, view = object(), object()
        self.node.set_scene(view, root)
        self.node.set_scene_lighting(True, False)
        self.node.set_attached_lights([dict(position=[0, 0, 1], direction=[0, 0, -1], colour="#ff00ff")])
        lighting = Mock()
        factory = Mock(return_value=lighting)
        with patch.dict(sys.modules, {"mpf.toolhead.ToolheadSceneLighting": SimpleNamespace(ToolheadSceneLighting=factory)}):
            self.node.illuminate_scene(self.renderer, self.camera)
            self.node.set_scene(view, root)
            self.node.illuminate_scene(self.renderer, self.camera)
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(lighting.draw.call_count, 2)
            lighting.draw.assert_called_with(self.node, self.renderer, self.camera, view, root)
            before = self.node.scene_lighting_signature()
            self.node.set_opacity(.4)
            self.assertNotEqual(before, self.node.scene_lighting_signature())
            self.assertEqual(self.node.scene_lighting_effects(), (True, False))
            self.node.set_scene(view, object())
            self.assertIsNone(self.node._scene_lighting)
            self.node.illuminate_scene(self.renderer, self.camera)
            self.assertEqual(factory.call_count, 2)
        self.node.close()
        self.assertIsNone(self.node._scene_lighting)
        self.assertIsNone(self.node._frame_cache)

    def test_camera_and_cleanup_failures_cannot_escape_the_qt_callback(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        with patch.object(self.node, "getSceneCamera", side_effect=RuntimeError("camera retired")):
            self.render_frame()
        self.assertIn("camera retired", self.node.render_failure())
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.gl.glDisable.side_effect = RuntimeError("context retired")
        self.render_frame()
        self.assertIn("context retired", self.node.render_failure())

    def test_zero_energy_lights_do_not_construct_or_draw_scene_lighting(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node._root = object()
        self.node.set_attached_lights([dict(position=[0, 0, 1], direction=[0, 0, -1], colour="#000000", brightness=2)])
        self.node.illuminate_scene(self.renderer, self.camera)
        self.assertIsNone(self.node._scene_lighting)
        self.node.set_attached_lights([dict(position=[0, 0, 1], direction=[0, 0, -1], colour="#ffffff", brightness=0)])
        self.node.illuminate_scene(self.renderer, self.camera)
        self.assertIsNone(self.node._scene_lighting)

    def test_master_lighting_switch_keeps_head_visible_and_invalidates_cached_colours(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.setVisible(True)
        self.node._root = object()
        self.node.set_attached_lights([dict(position=[0, 0, 1], direction=[0, 0, -1], colour="#ff00ff", brightness=2)])
        state = self.node.scene_lighting_signature()
        self.node.set_lighting_enabled(False)
        self.assertNotEqual(state, self.node.scene_lighting_signature())
        self.node.illuminate_scene(self.renderer, self.camera)
        self.assertIsNone(self.node._scene_lighting)
        self.assertTrue(self.node.visible_for_render())
        self.node.draw(self.camera)
        self.shader.setUniformValue.assert_any_call("u_lightingEnabled", 0)
        self.node.set_lighting_enabled(True)
        self.assertEqual(state, self.node.scene_lighting_signature())
        self.node.draw(self.camera)
        self.shader.setUniformValue.assert_any_call("u_lightingEnabled", 1)

    def test_global_opacity_fades_composed_head_without_extra_geometry_or_cache_invalidation(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        mesh = self.node.getMeshData()
        self.node.setVisible(True)
        self.render_frame()
        cache = Mock()
        cache.draw.side_effect = lambda gl, camera, bounds, transform, state, render, **kwargs: render(camera)
        self.node._frame_cache = cache
        self.render_frame()
        opaque_key = cache.draw.call_args.args[4]
        self.batches.clear()
        self.node.set_opacity(.35)
        self.render_frame()
        self.assertIs(self.node.getMeshData(), mesh)
        self.shader.setUniformValue.assert_any_call("u_opacity", 1.0)
        self.assertEqual(cache.draw.call_args.kwargs["opacity"], .35)
        self.assertEqual(cache.draw.call_args.args[4], opaque_key)
        self.assertEqual(len(self.batches), 1)
        self.assertNotIn("blend_mode", self.batches[0].options)
        self.node.set_opacity(0)
        self.batches.clear()
        calls = cache.draw.call_count
        clears = self.gl.glClear.call_count
        self.render_frame()
        self.assertEqual(self.batches, [])
        self.assertEqual(cache.draw.call_count, calls)
        self.assertEqual(self.gl.glClear.call_count, clears)

    def test_opacity_fades_attached_scene_lights_without_changing_saved_intensity(self):
        self.node.set_model(self.mesh(), (1, 2, 3))
        light = dict(position=[1, 2, 3], direction=[0, 0, -1], colour="#00ff00", brightness=2)
        self.node.set_attached_lights([light])
        for opacity in (1, .5, 0):
            self.node.set_opacity(opacity)
            self.node.apply_attached_lights(self.shader)
            self.shader.setUniformValue.assert_any_call("u_lightOpacity", opacity)
            self.shader.setUniformValue.assert_any_call("u_attachedColour[0]", [0, 2, 0])
        self.assertEqual(self.node._attached_lights[0]["brightness"], 2)
        self.assertEqual(light["brightness"], 2)

    def test_lights_follow_top_perimeter_and_aim_inward_down_at_45_degrees(self):
        self.node.set_model(self.mesh(), (0, 0, 0))
        self.node.set_lights(300, 220, 350)
        self.node.setVisible(True)
        self.render_frame()
        self.shader.setUniformValue.assert_any_call("u_light0", [-150, 350, 0])
        self.shader.setUniformValue.assert_any_call("u_light3", [0, 350, 110])
        self.shader.setUniformValue.assert_any_call("u_direction0", [1, -1, 0])
        self.shader.setUniformValue.assert_any_call("u_direction3", [0, -1, -1])


class RotorMatrix:

    def __init__(self, data=None):
        self.data = np.eye(4) if data is None else np.array(data, dtype=float, copy=True)

    def getData(self):
        return self.data

    def setRow(self, index, value):
        self.data[index, :] = value

    def setColumn(self, index, value):
        self.data[:, index] = value

    def invert(self):
        self.data = np.linalg.inv(self.data)

    def transpose(self):
        self.data = self.data.T

class RotorDelta(unittest.TestCase):
    setUp = ToolheadSceneTests.setUp
    mesh = ToolheadSceneTests.mesh

    def install(self):
        node = self.node
        node._tip = np.zeros(3)
        node._render_normal = RotorMatrix()
        node._render_transform = RotorMatrix()
        node._render_transform.data[:3, 3] = [10, 20, 30]
        node._rotor_meshes = {0: ('moving opaque', 'moving glass')}
        node._static_transparent = ['far glass', 'near glass']
        node._mesh_centres = {id('moving glass'): np.array([0, 0, 0]), id('far glass'): np.array([0, 0, -20]), id('near glass'): np.array([0, 0, 20])}
        row = dict(body=0, centre=[1, 2, 3], axis=[0, 0, 1], direction=1)
        node._rotor_motion = SimpleNamespace(sample=lambda: [(row, np.pi / 2, 0.4, 'manual')])
        node._frame_cache = SimpleNamespace(_key='cached static')
        node._rotor_retry = 0.0
        node._depth_seed = object()
        node._occlusion = SimpleNamespace(overlay=Mock())
        self.calls = []
        node._draw_mesh = lambda camera, opaque, glass, transform, normal: self.calls.append((opaque, glass, transform.getData().copy()))
        identity = SimpleNamespace(getData=lambda: np.eye(4))
        self.camera = SimpleNamespace(getWorldPosition=lambda: SimpleNamespace(x=0, y=0, z=100),
            getProjectionMatrix=lambda: identity, getInverseWorldTransformation=lambda: identity)
        self.work = object()
        self.static = Mock()
        self.patcher = patch.dict(sys.modules, {'UM.Math.Matrix': SimpleNamespace(Matrix=RotorMatrix)})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        return row

    def test_pose_conversion_glass_back_to_front_and_native_overlay_once(self):
        self.install()
        node = self.node

        def combine(gl, static, camera, size, render, blurred):
            self.assertTrue(blurred)
            render(camera, 0.0)
            return 'completed shutter'
        node._rotor_render = SimpleNamespace(_work=self.work, combine=Mock(side_effect=combine))
        self.assertEqual(node._animate_rotors(self.gl, self.static, self.camera, (32, 32, 0)), 'completed shutter')
        self.assertEqual([(opaque, glass) for opaque, glass, _ in self.calls], [('moving opaque', None), (None, 'far glass'), (None, 'moving glass'), (None, 'near glass')])
        matrix = self.calls[0][2]
        centre = np.array([1, 3, -2, 1.0])
        np.testing.assert_allclose(matrix @ centre, [11, 23, 28, 1])
        np.testing.assert_allclose(matrix @ (centre + [1, 0, 0, 0]), [11, 23, 27, 1], atol=1e-08)
        node._occlusion.overlay.assert_called_once_with(self.gl, self.work, self.camera)

    def test_orthographic_transparency_sorts_view_depth_not_lateral_distance(self):
        self.install()
        self.node._mesh_centres[id('near glass')] = np.array([1000, 0, 20])
        def combine(gl, static, camera, size, render, blurred):
            render(camera, 0.)
            return static
        self.node._rotor_render = SimpleNamespace(_work=self.work, combine=combine)
        self.node._animate_rotors(self.gl, self.static, self.camera, (32, 32, 0))
        self.assertEqual([glass for _opaque, glass, _transform in self.calls if glass], ['far glass', 'moving glass', 'near glass'])

    def test_shutter_failure_retry_deadline_does_not_extend_during_backoff(self):
        self.install()
        node = self.node
        now = [10.0]
        failed = Mock(side_effect=RuntimeError('allocation fault'))
        node._rotor_render = SimpleNamespace(combine=failed)
        with patch('time.monotonic', side_effect=lambda: now[0]):
            for instant in (10.0, 11.0, 14.0):
                now[0] = instant
                self.assertIs(node._animate_rotors(self.gl, self.static, self.camera, (32, 32, 0)), self.static)
                self.assertEqual(node._rotor_retry, 15.0)
            self.assertEqual(failed.call_count, 1)
            self.assertIsNone(node._frame_cache._key)
            now[0] = 15.0
            node._animate_rotors(self.gl, self.static, self.camera, (32, 32, 0))
            self.assertEqual(failed.call_count, 2)
            self.assertEqual(node._rotor_retry, 20.0)
        self.assertEqual(self.static.bind.call_count, 4)

    def test_static_cache_excludes_glass_while_each_rotor_pose_owns_it(self):
        self.install()
        node = self.node
        node._translucent_mesh = 'static glass'
        node.setMeshData('static opaque')
        node._draw(self.camera)
        self.assertEqual([(a, b) for a, b, _ in self.calls], [('static opaque', None)])
        node._frame_cache = Mock()
        node._model_bounds = ((0, 0, 0), (1, 1, 1))
        node.draw(self.camera)
        kwargs = node._frame_cache.draw.call_args.kwargs
        self.assertIsNone(kwargs['post_render'])
        self.assertEqual(kwargs['animate'], node._animate_rotors)

    def test_native_model_retires_environment_rotor_targets_and_cad_partitions(self):
        node = self.node
        node.set_model(self.mesh((1, 0.4)), (0, 0, 0))
        old_environment = Mock()
        node._environment = old_environment
        node._environment_scene = object()
        node._rotor_render = object()
        node._static_transparent = [object()]
        node._mesh_centres = {123: np.ones(3)}
        node._occlusion = node._depth_seed = node._depth_revision = node._opaque_shader_cache = object()
        node.set_native_model(object())
        old_environment.close.assert_called_once()
        self.assertIsNone(node._environment)
        self.assertIsNone(node._environment_scene)
        self.assertIsNone(node._rotor_render)
        self.assertEqual(node._static_transparent, [])
        self.assertEqual(node._mesh_centres, {})
        self.assertIsNone(node._occlusion)
        self.assertIsNone(node._depth_seed)
        self.assertIsNone(node._depth_revision)
        self.assertIsNone(node._opaque_shader_cache)

    def test_readings_update_preserves_mesh_partitions_and_phase_owner(self):
        node = self.node
        node.set_model(self.mesh(), (0, 0, 0))
        node._build_meshes = Mock()
        row = dict(body=0, centre=[0, 0, 0], axis=[0, 0, 1], rpm=3000, direction=1, fan='fan', blur=True)
        readings = {'fan': {'available': True, 'rpm': 1200}}
        node.set_rotors([row], readings)
        motion = node._rotor_motion
        readings2 = {'fan': {'available': True, 'rpm': 2400}}
        node.set_rotors([row], readings2)
        node._build_meshes.assert_called_once()
        self.assertIs(node._rotor_motion, motion)
        self.assertIs(motion.readings, readings2)


class OptionalRenderTests(unittest.TestCase):
    setUp=ToolheadSceneTests.setUp
    mesh=ToolheadSceneTests.mesh

    def test_reflection_disable_fences_published_and_partial_owners_and_reenable_is_fresh(self):
        for available in (True, False):
            node = self.node
            node.set_reflections_enabled(True)
            node.setVisible(True)
            old = Mock(ready=True, available=available, wake_delay=5.)
            fresh = Mock(ready=True, wake_delay=0.)
            scene = Mock()
            scene.signature.return_value = ('file',)
            window = Mock()
            app = Mock(getMainWindow=lambda window=window: window)
            app.callLater.side_effect = lambda callback: callback()
            factory = Mock(return_value=fresh)
            modules = {'UM.Application': SimpleNamespace(Application=SimpleNamespace(getInstance=lambda app=app: app)),
                'mpf.toolhead.ToolheadEnvironment': SimpleNamespace(ToolheadEnvironment=factory),
                'mpf.toolhead.ToolheadEnvironmentScene': SimpleNamespace(ToolheadEnvironmentScene=Mock(return_value=scene))}
            timers = []
            with patch.dict(sys.modules, modules), patch('PyQt6.QtCore.QTimer.singleShot', side_effect=lambda delay, callback, timers=timers: timers.append(callback)):
                node._environment = old
                node._environment_scene = object()
                node._view = Mock()
                node._view.getCurrentLayer.return_value = 2
                node._view.getCurrentPath.return_value = 3.
                node._root = object()
                node._schedule_environment()
                node.set_reflections_enabled(False)
                timers[0]()
                window.update.assert_not_called()
                old.close.assert_called_once()
                node.prepare_environment(self.renderer, self.camera, self.gl)
                factory.assert_not_called()
                node.set_reflections_enabled(True)
                node.prepare_environment(self.renderer, self.camera, self.gl)
                factory.assert_called_once()
                self.assertIs(node._environment, fresh)
                fresh.step.assert_called_once()
                window.update.assert_called_once()
                node.set_reflections_enabled(False)

    def test_environment_wakes_belong_to_their_owner_and_stale_timer_cannot_suppress_new_capture(self):
        node=self.node; node.setVisible(True)
        old,new=Mock(wake_delay=5.),Mock(wake_delay=0.)
        window=Mock(); app=Mock(getMainWindow=lambda:window)
        app.callLater.side_effect=lambda callback:callback()
        timers=[]
        with patch.dict(sys.modules,{'UM.Application':SimpleNamespace(Application=SimpleNamespace(getInstance=lambda:app))}), \
                patch('PyQt6.QtCore.QTimer.singleShot',side_effect=lambda delay,callback:timers.append(callback)):
            node._environment=old;node._schedule_environment()
            first=node._environment_wake
            node._environment=new;node._schedule_environment()
            window.update.assert_called_once()
            self.assertIsNone(node._environment_wake)
            self.assertIsNot(first,node._environment_wake)
            timers[0]()
            window.update.assert_called_once()
            self.assertIsNone(node._environment_wake)

    def test_environment_schedule_is_bounded_fenced_by_visibility_and_optional_failure(self):
        owner, scene=Mock(ready=True, wake_delay=0.),Mock()
        scene.signature.return_value=('file',);scene.snapshot.return_value='frozen scene'
        owner.step.side_effect=lambda gl,context,hard,soft,snapshot: bool(snapshot())
        window=Mock();app=Mock(getMainWindow=lambda:window)
        app.callLater.side_effect=lambda callback:callback()
        modules={'mpf.toolhead.ToolheadEnvironment':SimpleNamespace(ToolheadEnvironment=Mock(return_value=owner)),
            'mpf.toolhead.ToolheadEnvironmentScene':SimpleNamespace(ToolheadEnvironmentScene=Mock(return_value=scene)),
            'UM.Application':SimpleNamespace(Application=SimpleNamespace(getInstance=lambda:app))}
        with patch.dict(sys.modules,modules):
            node=self.node;node.setVisible(True);node._view=Mock();node._root=object()
            node._view.getCurrentLayer.return_value=2;node._view.getCurrentPath.return_value=3.
            node.prepare_environment(self.renderer,self.camera,self.gl)
            scene.snapshot.assert_called_once();window.update.assert_called_once()
            node.setVisible(False);node.prepare_environment(self.renderer,self.camera,self.gl)
            window.update.assert_called_once()
            owner.ready=False;node.prepare_environment(self.renderer,self.camera,self.gl)
            self.assertEqual(owner.step.call_count,2)
            owner.ready=True;scene.signature.side_effect=RuntimeError('legacy mode')
            node.prepare_environment(self.renderer,self.camera,self.gl)
            self.assertEqual(str(owner.fail.call_args.args[0]),'legacy mode')
            self.assertEqual(node.render_failure(),'')

    def test_real_rotor_partition_is_retained_across_readings_and_crop_covers_all_angles(self):
        node=self.node;node.set_model(self.mesh((1.,.35)),(0,0,0));node.set_surface_detail(.7)
        row=dict(body=0,centre=[0,0,0],axis=[0,0,1],rpm=3000,direction=1,blur=True,fan='fan')
        node.set_rotors([row],{'fan':dict(available=True,rpm=100)})
        opaque,glass=node._rotor_meshes[0]
        self.assertIsNotNone(opaque);self.assertIsNotNone(glass);self.assertIsNone(node.getMeshData())
        low,high=node._model_bounds;self.assertTrue(np.all(low<0));self.assertTrue(np.all(high>0))
        node.set_rotors([row],{'fan':dict(available=True,rpm=200)})
        self.assertIs(node._rotor_meshes[0][0],opaque)
        node.set_rotors([],{});self.assertIsNotNone(node.getMeshData());self.assertEqual(node._rotor_meshes,{})
