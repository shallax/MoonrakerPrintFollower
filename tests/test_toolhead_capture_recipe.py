"""Immutable scene export, including mutable native texture publications."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np
from PyQt6.QtGui import QImage, QColor
from mpf.toolhead.ToolheadCaptureValues import UniformValue

with patch.dict(sys.modules, {
    'mpf.toolhead.ToolheadCaptureGL': NS(RawShaderProgram=Mock),
    'mpf.toolhead.ToolheadCaptureDraw': NS(CapturePaths=lambda *args: Mock(), CapturePlates=lambda *args: Mock()),
    'mpf.toolhead.ToolheadEnvironmentScene': NS(EnvironmentSnapshot=lambda *args, **kwargs: Mock()),
}):
    from mpf.toolhead import ToolheadCaptureRecipe as module


class Matrix:
    def __init__(self, data=None): self.data = np.eye(4) if data is None else np.array(data)
    def getData(self): return self.data


class CaptureMemoryReceiptTests(unittest.TestCase):
    def test_snapshot_retires_derived_receiver_when_same_plate_stops_lighting(self):
        mesh = module.CaptureMesh(19, np.zeros((3, 3), np.float32),
            np.array([[0, 1, 2]], np.uint32), None, None, None, ())
        settings = (('u_grid', UniformValue('scalar', 1)),)
        entry = ('grid', mesh, UniformValue('scalar', 1), None, (), settings)
        for lighting, effects, current_settings, expected in (
                ((('light', UniformValue('scalar', 1)),), (True, False), settings, {19, -19}),
                ((), (True, False), settings, {19}),
                ((('light', UniformValue('scalar', 1)),), (False, False), settings, {19}),
                ((('light', UniformValue('scalar', 1)),), (True, False), (), {19})):
            with self.subTest(lighting=lighting, effects=effects, settings=current_settings):
                scene = module.CaptureScene(Mock(), object(), Mock())
                scene.receivers = {19: object()}
                scene.shaders['grid'] = Mock()
                scene.texture = object()
                frame = module.CaptureFrame((0, 0, 0), UniformValue('scalar', 1),
                    (entry[:-1]+(current_settings,),), (), (), lighting, effects, (), None)
                with patch.object(module,'EnvironmentSnapshot',return_value=Mock()) as snapshot:
                    scene.snapshot(frame)
                    self.assertEqual(snapshot.call_args.kwargs['projection'],0)
                scene.plates.retain.assert_called_once_with(expected)
                self.assertEqual(bool(scene.receivers), -19 in expected)

    def test_derived_receivers_and_upload_copies_are_charged_from_actual_layout(self):
        vertices = np.zeros((3, 3), np.float32)
        indices = np.array([[0, 1, 2]], np.uint32)
        mesh = module.CaptureMesh(19, vertices, indices, None, None, np.zeros((3, 2), np.float32), ())
        entry = ('grid', mesh, None, None, (), (('u_grid', 1),))
        frame = module.CaptureFrame((), None, (entry,), (), (), (('light', UniformValue('scalar', 1)),), (True, False), (), (2, 2, bytes(16)))
        # 72 original GPU + 84 receiver GPU + 36 retained normals,
        # plus168 peak copied-parts/joined-receiver upload +16 texture GPU.
        self.assertEqual(frame.capture_storage_bytes(), 376)
        frame = module.CaptureFrame((), None, (entry,), (), (), (), (True, False), (), None)
        self.assertEqual(frame.capture_storage_bytes(), 216)
        path = (mesh, module.BufferLease(7, mesh.layout()[1], mesh.layout()[0]), None, None, None, None, None)
        frame = module.CaptureFrame((), None, (entry, entry), (path,), (), (('light', UniformValue('scalar', 1)),), (True, False), (), None)
        self.assertEqual(frame.capture_storage_bytes(), 720+36)


    def test_borrowed_index_storage_charged_once_without_private_upload_copies(self):
        indices=np.array([0,1],np.int32);indices.flags.writeable=False
        mesh=module.CaptureMesh(1,np.zeros((2,3),np.float32),indices,None,None,None,())
        lease=module.BufferLease(7,24,mesh.layout()[0],module.IndexLease(8,8,2,1,1))
        paths=((mesh,lease,None,(0,2),None,0,0),)*2
        frame=module.CaptureFrame((),None,(),paths,(),(),(False,False),(),None)
        self.assertEqual(frame.retained_source_bytes(),2*(mesh.retained_bytes()+24)+8)
        self.assertEqual(frame.capture_storage_bytes(),0)

class CaptureRecipeTests(unittest.TestCase):
    def setUp(self):
        self.gl = Mock()
        self.gl.glGetIntegerv.return_value = 7
        self.opengl = NS(VertexBufferProperty='buffer', getInstance=lambda: NS(getBindingsObject=lambda: self.gl))
        self.modules = patch.dict(sys.modules, {
            'UM.View.GL.OpenGL': NS(OpenGL=self.opengl), 'UM.Math.Matrix': NS(Matrix=Matrix),
            'UM.Math.Vector': NS(Vector=type('Vector', (), {})), 'UM.Math.Color': NS(Color=type('Color', (), {})),
        })
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.snapshot = NS(owner=NS(_shaders={}, _texture=None), paths=[], plates=[],
            origin=(0., 0., 0.), light=0., uniforms={}, lighting={}, light_effects=(False, False), path_top={})

    @staticmethod
    def mesh():
        vertices = np.zeros((2, 3), np.float32)
        return Mock(getVertices=Mock(return_value=vertices), getIndices=Mock(return_value=np.array([0, 1], np.int32)),
            getVertexCount=Mock(return_value=2), hasColors=Mock(return_value=False),
            hasNormals=Mock(return_value=False), hasUVCoordinates=Mock(return_value=False),
            attributeNames=Mock(return_value=[]))

    def test_source_memory_receipt_counts_borrowed_vbo_cpu_and_texture_without_copy(self):
        mesh = module.CaptureMesh.freeze(self.mesh())
        lease = module.BufferLease(7, 24, mesh.layout()[0])
        frame = module.CaptureFrame((0, 0, 0), None,
            (('bed', mesh, None, None, (), ()),),
            ((mesh, lease, None, (0, 2), None, 0, 0), (mesh, lease, None, (0, 2), None, 0, 0)),
            (), (), (False, False), (), (1, 3, bytes(12)))
        self.assertEqual(mesh.retained_bytes(), 32)
        self.assertEqual(frame.retained_source_bytes(), 156)
        self.assertIs(frame.paths[0][0], mesh)
        no_paths = module.CaptureFrame((0, 0, 0), None, frame.plates, (), (), (), (False, False), (), None)
        self.assertEqual(no_paths.retained_source_bytes(), 32)

    def test_same_texture_wrapper_republishes_changed_pixels_and_initial_lazy_image(self):
        freezer = module.CaptureFreezer()
        texture = self.snapshot.owner._texture = Mock()
        texture.getImage.return_value = None
        first, leases = freezer.freeze(self.snapshot, object())
        self.assertIsNone(first.texture); self.assertEqual(leases, ())
        image = QImage(1, 1, QImage.Format.Format_RGBA8888); image.fill(QColor('red'))
        texture.getImage.return_value = image
        red, _ = freezer.freeze(self.snapshot, object())
        repeated, _ = freezer.freeze(self.snapshot, object())
        self.assertIs(red.texture, repeated.texture)
        self.assertEqual(red.texture, (1, 1, b'\xff\x00\x00\xff'))
        image.fill(QColor(0, 255, 0))
        green, _ = freezer.freeze(self.snapshot, object())
        self.assertEqual(green.texture, (1, 1, b'\x00\xff\x00\xff'))
        self.assertEqual(red.texture, (1, 1, b'\xff\x00\x00\xff'))
        texture.getImage.return_value = None
        self.assertIsNone(freezer.freeze(self.snapshot, object())[0].texture)

    def test_invalid_platform_image_rejects_before_publication(self):
        self.snapshot.owner._texture = NS(getImage=lambda: QImage())
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            module.CaptureFreezer().freeze(self.snapshot, object())

    def test_path_export_retains_exact_buffer_and_restores_host_binding(self):
        source = self.mesh(); source.buffer = Mock(bufferId=Mock(return_value=31))
        geometry = Mock(vertex_mesh=Mock(return_value=source))
        self.snapshot.paths = [(geometry, Matrix(), (0, 2), None, 0)]
        self.snapshot.path_top[id(geometry)] = 2
        freezer = module.CaptureFreezer()
        recipe = module.ShaderRecipe((), (), ())
        def size(_target, _parameter, output): output._obj.value = 24
        with patch.object(module, 'procedure', return_value=size), patch.object(module, 'recipe_for', return_value=recipe):
            frame, leases = freezer.freeze(self.snapshot, object())
            same, _ = freezer.freeze(self.snapshot, object())
        self.assertEqual(leases, (source.buffer, source))
        self.assertIs(frame.paths[0][0], same.paths[0][0])
        self.assertEqual(frame.paths[0][1].name, 31)
        self.assertEqual(frame.paths[0][-1], 2)
        self.assertEqual(self.gl.glBindBuffer.call_args.args, (0x8892, 7))
        self.snapshot.paths = []
        freezer.freeze(self.snapshot, object())
        self.assertEqual(freezer.meshes, {})
        self.snapshot.paths = [(geometry, Matrix(), (0, 2), None, 0)]
        source.buffer = None
        self.assertIsNone(freezer.freeze(self.snapshot, object()))

    def test_plate_export_freezes_transform_uniforms_and_lighting_recipe(self):
        shader, source = Mock(), self.mesh()
        self.snapshot.owner._shaders = {'grid': shader}
        transform = Matrix()
        self.snapshot.plates = [(shader, {'mesh': source, 'transformation': transform,
            'normal_transformation': Matrix(), 'uniforms': {'red': .5}}, {'opacity': .75})]
        self.snapshot.lighting = {'strength': 1.}
        self.snapshot.light_effects = (True, True)
        with patch.object(module, 'recipe_for', return_value=module.ShaderRecipe((), (), ())) as load:
            frame, _ = module.CaptureFreezer().freeze(self.snapshot, object())
        self.assertEqual({call.args[0] for call in load.call_args_list}, {'grid', 'light-mesh'})
        transform.data[:] = 0
        np.testing.assert_array_equal(module.thaw_uniform(frame.plates[0][2]).getData(), np.eye(4))
        self.assertEqual(frame.light_effects, (True, True))

    def test_shader_recipe_roundtrip_and_failed_compile_retires_private_program(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'probe.shader'
            path.write_text('[shaders]\nvertex41core=vertex\nfragment41core=fragment\n'
                '[defaults]\nstrength=0.5\n[bindings]\ntransform=model_matrix\n')
            recipe = module.ShaderRecipe.load(path)
            shader = Mock()
            with patch.object(module, 'RawShaderProgram', return_value=shader):
                self.assertIs(recipe.build(), shader)
                shader.setUniformValue.assert_called_once_with('strength', .5)
                shader.addBinding.assert_called_once_with('transform', 'model_matrix')
                shader.setFragmentShader.return_value = False
                with self.assertRaisesRegex(RuntimeError, 'compile'): recipe.build()
                shader._shader_program.close.assert_called_once()
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                module.ShaderRecipe.load(path.with_name('absent.shader'))

    def test_shader_resource_routes_and_lighting_scalar_types_are_preserved(self):
        import configparser
        parser = configparser.ConfigParser()
        parser.read_string('[defaults]\nu_min_width=1\nstrength=0.5\n[bindings]\nposition=view_position\n')
        with patch.dict(sys.modules, {
            'UM.Resources': NS(Resources=NS(Shaders=1, getPath=lambda kind, name: '/resources/'+name)),
            'UM.PluginRegistry': NS(PluginRegistry=NS(getInstance=lambda: NS(getPluginPath=lambda name: '/simulation'))),
            'mpf.toolhead.ToolheadSceneLighting': NS(shader_sources=lambda *_: (parser, 'vertex', None, 'fragment')),
        }), patch.object(module.ShaderRecipe, 'load', side_effect=lambda path: str(path)):
            self.assertEqual(module.recipe_for('grid'), '/resources/grid.shader')
            self.assertEqual(Path(module.recipe_for(('paths', False, False))), Path('/simulation') / 'layers3d.shader')
            self.assertEqual(Path(module.recipe_for(('paths', False, True))), Path('/simulation') / 'layers3d_shadow.shader')
            self.assertEqual(Path(module.recipe_for('light-mesh')).parts[-2:], ('toolhead', 'scene-lighting.shader'))
            light = module.recipe_for(('light-path', False))
            self.assertEqual(light.stages, (('vertex', 'vertex'), ('fragment', 'fragment')))
            self.assertIs(type(dict(light.defaults)['u_min_width'].value), float)
            self.assertIn(('u_viewPosition', 'view_position'), light.bindings)
            with self.assertRaisesRegex(RuntimeError, 'normal Preview'): module.recipe_for(('paths', True, False))
            with self.assertRaisesRegex(RuntimeError, 'Unknown'): module.recipe_for(('unknown', False))

    def test_scene_reuses_resources_then_retires_removed_geometry_and_texture(self):
        freezer = module.CaptureFreezer()
        source = self.mesh()
        mesh = freezer.mesh(source)
        lease = module.BufferLease(8, 24, mesh.layout()[0])
        transform = module.freeze_uniform(Matrix())
        recipe = Mock(build=Mock(return_value=Mock()))
        frame = module.CaptureFrame((0, 0, 0), module.freeze_uniform(1.),
            (('platform', mesh, transform, None, (), ()),),
            ((mesh, lease, transform, (0, 2), None, 0, 2),), (), (), (False, False),
            (('platform', recipe), (('paths', False, False), recipe),
             (('light-path', False), recipe), ('light-mesh', recipe)), None)
        textures = Mock(side_effect=[Mock(), Mock()])
        scene = module.CaptureScene(Mock(), object(), textures)
        first = scene.snapshot(frame)
        geometry = next(iter(scene.paths.values()))
        scene.snapshot(frame)
        self.assertEqual(len(scene.paths), 1)
        self.assertEqual(textures.call_count, 1)
        self.assertEqual(first.path_top[id(geometry)], 2)
        self.assertIs(scene.path_shader(False), scene.shaders[('paths', False, False)])
        self.assertIs(scene.light_path_shader(False), scene.shaders[('light-path', False)])
        self.assertIs(scene.light_mesh_shader(), scene.shaders['light-mesh'])
        receiver = scene.light_receiver(mesh)
        self.assertIs(scene.light_receiver(mesh), receiver)
        np.testing.assert_array_equal(receiver.normals, [[0, 1, 0], [0, 1, 0]])
        for cache in (scene._mesh_bounds, scene._path_bounds, scene._path_edges): cache[mesh] = 'cached'
        from dataclasses import replace
        scene.snapshot(replace(frame, plates=(), paths=(), texture=(1, 1, b'abcd')))
        geometry.close.assert_called_once()
        self.assertEqual(scene.paths, {}); self.assertEqual(scene.receivers, {})
        self.assertEqual(scene._mesh_bounds, {})
        scene.close(); scene.close()
        self.assertIsNone(scene.texture); self.assertEqual(scene.shaders, {})
        values, sink = module.uniform_sink(); sink.setUniformValue('value', 5)
        self.assertEqual(values, {'value': 5})



    def test_freezer_exports_plain_index_receipt_and_retains_main_allocation(self):
        source=self.mesh();source.buffer=Mock(bufferId=Mock(return_value=31))
        indices=source.getIndices();indices.flags.writeable=False
        group=object();context=NS(shareGroup=lambda:group)
        allocation=module.IndexAllocation(source,indices,group,Mock(bufferId=Mock(return_value=39)),17)
        geometry=Mock(vertex_mesh=Mock(return_value=source),full_source_index_allocation=Mock(return_value=allocation))
        self.snapshot.paths=[(geometry,Matrix(),(0,2),None,0)]
        def size(target,parameter,out):out._obj.value=24
        with patch.object(module,'procedure',return_value=size),patch.object(module,'recipe_for',return_value=module.ShaderRecipe((),(),())):
            frame,owners=module.CaptureFreezer().freeze(self.snapshot,context)
        self.assertIs(owners[0],allocation)
        self.assertEqual(frame.paths[0][1].index,module.IndexLease(39,8,2,id(group),17))
        self.assertEqual(frame.capture_storage_bytes(),0)
        self.assertEqual(frame.retained_source_bytes(),32+24+8)

    def test_production_chunk_descriptor_freezes_registered_full_source_not_its_chunk(self):
        from mpf.toolhead import ToolheadPathGeometry as paths
        source=self.mesh();source.buffer=Mock(bufferId=Mock(return_value=31))
        indices=source.getIndices();indices.flags.writeable=False
        group=object();context=NS(shareGroup=lambda:group)
        main=paths.ToolheadPathGeometry(source,build_bounds=False)
        allocation=module.IndexAllocation(source,indices,group,Mock(bufferId=Mock(return_value=39)),17)
        paths._index_allocations[main._shared_index_key(context)]=allocation
        geometry=paths.ToolheadPathGeometry(source,build_bounds=False,index_chunk=32768)
        geometry.vertex_mesh=Mock(return_value=source)
        geometry._index_buffer=Mock(bufferId=Mock(return_value=77))
        geometry._element_base=2;geometry._index_bytes=4
        self.assertIsNone(geometry.shared_index_allocation(context))
        self.assertIs(geometry.full_source_index_allocation(context),allocation)
        self.snapshot.paths=[(geometry,Matrix(),(0,2),None,0)]
        def size(target,parameter,out):out._obj.value=24
        with patch.object(module,'procedure',return_value=size),patch.object(module,'recipe_for',return_value=module.ShaderRecipe((),(),())):
            frame,owners=module.CaptureFreezer().freeze(self.snapshot,context)
        self.assertEqual(frame.paths[0][1].index,module.IndexLease(39,8,2,id(group),17))
        self.assertIs(owners[0],allocation)
        self.assertEqual(geometry._index_buffer.bufferId(),77)
        self.assertEqual(geometry._element_base,2)
        self.assertIsNone(geometry.full_source_index_allocation(NS(shareGroup=lambda:object())))
        exterior=paths.ToolheadPathGeometry(source,indices=indices,build_bounds=False)
        self.assertIsNone(exterior.full_source_index_allocation(context))
        # Same-shaped frozen indices from another stream cannot certify this EBO.
        detached=module.CaptureMesh.freeze(source)
        from dataclasses import replace
        detached=replace(detached,indices=indices.copy())
        detached.indices.flags.writeable=False
        freezer=module.CaptureFreezer();freezer.meshes[source]=detached
        with patch.object(module,'procedure',return_value=size),patch.object(module,'recipe_for',return_value=module.ShaderRecipe((),(),())):
            frame,owners=freezer.freeze(self.snapshot,context)
        self.assertIsNone(frame.paths[0][1].index)
        self.assertNotIn(allocation,owners)
