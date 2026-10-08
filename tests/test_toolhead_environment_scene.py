"""Native scene admission, frozen probe cameras and bounded path commands."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import numpy as np
from PyQt6.QtGui import QMatrix4x4, QVector4D
from mpf.toolhead.ToolheadEnvironment import ToolheadEnvironment


class Matrix:
    def __init__(self, data=None): self.data = np.eye(4) if data is None else np.array(data)
    def getData(self): return self.data
    def getFlatData(self): return self.data.reshape(-1).tolist()


# SceneLighting's public pure range function is loaded by its normal tests.
# Only its irrelevant shader imports are isolated here.
with patch.dict(sys.modules, {
    'UM.Mesh.MeshData': NS(MeshData=object), 'UM.PluginRegistry': NS(PluginRegistry=object),
    'UM.View.GL.OpenGLContext': NS(OpenGLContext=object),
    'UM.View.GL.ShaderProgram': NS(ShaderProgram=object), 'UM.View.RenderBatch': NS(RenderBatch=object),
}):
    from mpf.toolhead import ToolheadEnvironmentScene as module


class EnvironmentSceneTests(unittest.TestCase):
    def setUp(self):
        self.owner = module.ToolheadEnvironmentScene()
        self.view = Mock()
        self.view.getCompatibilityMode.return_value = False
        self.view.getCurrentLayer.return_value = 1
        self.view.getCurrentPath.return_value = 1.
        self.view.getMinimumLayer.return_value = 0
        for _name, getter in module.VIEW_FIELDS:
            getattr(self.view, getter).return_value = [1., 1.] if getter == 'getExtruderOpacities' else 1
        for metric in ('Feedrate', 'Thickness', 'LineWidth', 'FlowRate'):
            getattr(self.view, 'getMin'+metric).return_value = 0.
            getattr(self.view, 'getMax'+metric).return_value = 10.
        self.root = NS(getAllChildren=lambda: self.children)
        self.children = []
        self.renderer = NS(getBatches=lambda: self.batches, getRenderPass=lambda name: NS(getCompletedLayerShadowMode=lambda: True))
        self.batches = []
        self.stack = NS(findContainer=lambda query: None, getMetaDataEntry=lambda *args: False)
        self.app = NS(getTheme=lambda: NS(getColor=lambda name: NS(getRgb=lambda: (100, 150, 200, 255))), getGlobalContainerStack=lambda: self.stack)
        self.shader = Mock()
        self.modules = {
            'UM.View.RenderBatch': NS(RenderBatch=Mock()),
            'UM.View.GL.OpenGLContext': NS(OpenGLContext=NS(isLegacyOpenGL=lambda:False)),
            'UM.View.GL.ShaderProgram': NS(ShaderProgram=lambda:self.shader),
            'UM.Math.Matrix': NS(Matrix=Matrix), 'UM.Math.Vector': NS(Vector=lambda *args: tuple(args)),
            'UM.Application': NS(Application=NS(getInstance=lambda: self.app)),
            'cura.Settings.ExtruderManager': NS(ExtruderManager=NS(getInstance=lambda: NS(activeExtruderIndex=2))),
            'UM.Scene.Platform': NS(Platform=Platform),
            'UM.Resources': NS(Resources=NS(Shaders=0, Images=1, getPath=lambda kind, value: value)),
            'UM.View.GL.OpenGL': NS(OpenGL=NS(getInstance=lambda: NS(createTexture=lambda: Mock()))),
        }
        p=patch.dict(sys.modules, self.modules); p.start(); self.addCleanup(p.stop)

    def test_cube_frustum_and_orientation_match_actual_qt_uniform_conversion(self):
        origin = np.array([3., 4., 5.])
        directions = ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1))
        for face, direction in enumerate(directions):
            camera = module.probe_camera(origin, face, 'frozen light')
            projection = QMatrix4x4(camera.getProjectionMatrix().getFlatData())
            view = QMatrix4x4(camera.getInverseWorldTransformation().getFlatData())
            clip = projection * view * QVector4D(*(origin+np.array(direction)*10), 1)
            self.assertAlmostEqual(clip.x()/clip.w(), 0.)
            self.assertAlmostEqual(clip.y()/clip.w(), 0.)
            self.assertTrue(-1 < clip.z()/clip.w() < 1)
            self.assertEqual(camera.getCameraLightPosition(), 'frozen light')
            for point, expected in (((10,10,-10,1),(1,1)), ((0,0,-.2,1),(0,0)), ((0,0,-10000,1),(0,0))):
                c=projection*QVector4D(*point)
                np.testing.assert_allclose((c.x()/c.w(),c.y()/c.w()),expected,atol=1e-6)
            self.assertAlmostEqual((projection*QVector4D(0,0,-.2,1)).z()/.2, -1, places=5)
            c=projection*QVector4D(0,0,-10000,1); self.assertAlmostEqual(c.z()/c.w(),1,places=5)

    def child(self, mesh, *, outside=False, disabled=False, visible=True):
        return NS(callDecoration=lambda name: mesh if name=='getLayerData' else disabled if name=='isAssignedToDisabledExtruder' else None,
            getWorldTransformation=lambda: Matrix(), isOutsideBuildArea=lambda: outside, isVisible=lambda: visible)

    def mesh(self, vertices, uv=False):
        return NS(getVertices=lambda: np.asarray(vertices), hasUVCoordinates=lambda: uv,
            getElementCounts=lambda: {0:2, 1:4}, getLayer=lambda index: None, getIndices=lambda: np.arange(6),
            getAttribute=lambda name: None)

    def test_signature_excludes_disabled_sources_and_retires_backward_scrubs(self):
        mesh = self.mesh([[0,0,0], [1,0,0]])
        self.children = [self.child(mesh), self.child(mesh, outside=True), self.child(mesh, disabled=True)]
        one = self.owner.signature(self.renderer, self.view, self.root)
        self.assertEqual(len(one[1]), 1)
        np.testing.assert_allclose(one[2]['u_starts_color'], np.array([100,150,200,255])/255)
        self.view.getCurrentPath.return_value = 0.
        two = self.owner.signature(self.renderer, self.view, self.root)
        self.assertNotEqual(one[0], two[0])
        self.view.getCompatibilityMode.return_value = True
        with self.assertRaisesRegex(RuntimeError, 'normal Preview'): self.owner.signature(self.renderer, self.view, self.root)

    def test_unknown_native_shadow_state_cannot_publish_incorrect_path_reflections(self):
        self.renderer.getRenderPass=lambda name:NS(getCompletedLayerShadowMode=lambda:None)
        self.children=[self.child(self.mesh([[0,0,0],[1,0,0]])),
                       self.child(self.mesh([[0,1,0],[1,1,0]]))]
        with self.assertRaisesRegex(RuntimeError,'shadow state unavailable'):
            self.owner.signature(self.renderer,self.view,self.root)
        self.children=[]
        # A bed-only probe needs no path-shadow observation.
        self.owner.signature(self.renderer,self.view,self.root)

    def test_shader_clones_native_sources_without_changing_geometry_semantics(self):
        parser='[shaders]\nvertex41core=vertex\nfragment41core=fragment\ngeometry41core=void myEmitVertex() { f_color = color; }\n[defaults]\nu_test=1\n[bindings]\nu_mvp=model_matrix\n'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'shader'; path.write_text(parser)
            with patch.dict(sys.modules, {'UM.View.GL.OpenGLContext':NS(OpenGLContext=NS(isLegacyOpenGL=lambda:False)),
                'UM.View.GL.ShaderProgram':NS(ShaderProgram=lambda:self.shader)}):
                self.assertIs(module.owned_shader(path),self.shader)
                text=self.shader.setGeometryShader.call_args.args[0]
                self.assertEqual(text, 'void myEmitVertex() { f_color = color; }')
                self.shader.addBinding.assert_called_with('u_mvp','model_matrix')
                self.shader.setUniformValue.assert_called_with('u_test',1)
                self.shader.setVertexShader.return_value=False
                with self.assertRaisesRegex(RuntimeError,'compile'): module.owned_shader(path)
            with self.assertRaisesRegex(RuntimeError, 'unavailable'): module.owned_shader(path.with_name('missing'))

    def test_path_commands_freeze_prefix_and_draw_bounded_chunks(self):
        geometry=Mock(); shader=Mock(); self.owner.path_shader=Mock(return_value=shader)
        snapshot=module.EnvironmentSnapshot(self.owner,(0,0,0), 'light', [],
            [(geometry,Matrix(),(0,module.PATH_CHUNK+2),([1,2,3],[4,5,6],.5),0)], {'u_test':9},False)
        snapshot.descriptor = module.ProbeDescriptor((0,0,0),(-10,-10,-10),(10,10,10))
        gl=Mock()
        commands=list(snapshot.commands(0)); self.assertEqual(len(commands),2)
        for command in commands: command(gl)
        self.assertEqual(geometry.render.call_args_list[0].args[3],[(0,module.PATH_CHUNK)])
        self.assertEqual(geometry.render.call_args_list[1].args[3],[(module.PATH_CHUNK,module.PATH_CHUNK+2)])
        shader.setUniformValue.assert_any_call('u_last_line_ratio',.5)
        empty=module.EnvironmentSnapshot(self.owner,(0,0,0),'light',[],[],{},False)
        list(empty.prepare())
        self.assertEqual(list(empty.commands(1)),[])

    def test_completed_shadow_and_current_prefix_use_distinct_native_geometry(self):
        geometry=Mock(); shadow, current=Mock(), Mock()
        self.owner.path_shader=Mock(side_effect=lambda compatibility, shaded: shadow if shaded else current)
        snapshot=module.EnvironmentSnapshot(self.owner,(0,0,0),'light',[],
            [(geometry,Matrix(),(4,12),([1,2,3],[4,5,6],.5),8)],{},False)
        snapshot.descriptor = module.ProbeDescriptor((0,0,0),(-10,-10,-10),(10,10,10))
        for command in snapshot.commands(0): command(Mock())
        calls=geometry.render.call_args_list
        self.assertEqual([(entry.args[0],entry.args[3]) for entry in calls],[(shadow,[(4,8)]),(current,[(8,12)])])
        shadow.setUniformValue.assert_any_call('u_last_line_ratio',1.)
        current.setUniformValue.assert_any_call('u_last_line_ratio',.5)
        with patch.object(module,'owned_shader',side_effect=['normal','past']) as factory:
            del self.owner.path_shader
            self.owner._shaders={}
            registry=NS(PluginRegistry=NS(getInstance=lambda:NS(getPluginPath=lambda name:'/synthetic/SimulationView')))
            with patch.dict(sys.modules,{'UM.PluginRegistry':registry}):
                self.assertEqual(self.owner.path_shader(False),'normal')
                self.assertEqual(self.owner.path_shader(False,True),'past')
                self.assertEqual(self.owner.path_shader(False,True),'past')
            self.assertEqual([call.args[0].name for call in factory.call_args_list],['layers3d.shader','layers3d_shadow.shader'])


class Platform: pass


from contextlib import nullcontext

class HashMesh:

    def __init__(self, vertices=None, uv=False):
        self.vertices = np.array(vertices if vertices is not None else [[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
        self.uv = uv

    def getVertices(self):
        return self.vertices

    def hasUVCoordinates(self):
        return self.uv

    def getElementCounts(self):
        return {0: 2, 1: 4}

    def getLayer(self, index):
        return None

    def getIndices(self):
        return np.arange(6)

    def getAttribute(self, name):
        return None

class EnvironmentDelta(unittest.TestCase):
    setUp = EnvironmentSceneTests.setUp
    child = EnvironmentSceneTests.child

    def snapshot_node(self):
        return NS(_view=self.view, _root=self.root, _model_bounds=np.array([[0, 2, 4], [4, 6, 8]]), render_position=lambda: NS(x=3, y=4, z=5), light_dimensions=lambda: (100, 100, 100))

    def test_owned_light_shader_and_receiver_caches_keep_native_mesh_unchanged(self):
        mesh=HashMesh();original=mesh.vertices.copy();receiver=object()
        with patch.object(module,'owned_shader',return_value=self.shader) as shader_factory, patch.object(module,'create_path_shader',side_effect=['core','legacy']) as path_factory, patch.dict(sys.modules,{'UM.Mesh.MeshData':NS(MeshData=Mock(return_value=receiver))}):
            self.assertIs(self.owner.light_mesh_shader(),self.shader)
            self.assertIs(self.owner.light_mesh_shader(),self.shader)
            shader_factory.assert_called_once()
            self.assertEqual(self.owner.light_path_shader(False),'core')
            self.assertEqual(self.owner.light_path_shader(False),'core')
            self.assertEqual(self.owner.light_path_shader(True),'legacy')
            self.assertEqual(path_factory.call_count,2)
            factory=sys.modules['UM.Mesh.MeshData'].MeshData
            self.assertIs(self.owner.light_receiver(mesh),receiver)
            self.assertIs(self.owner.light_receiver(mesh),receiver)
            factory.assert_called_once()
            np.testing.assert_array_equal(factory.call_args.kwargs['normals'],[[0,1,0]]*3)
            np.testing.assert_array_equal(mesh.vertices,original)

    def test_snapshot_freezes_attached_light_values_and_native_top_prefix(self):
        mesh=HashMesh();self.children=[self.child(mesh)]
        geometry=NS(mesh=mesh)
        node=self.snapshot_node();node._lighting_enabled=True;node._attached_lights=[dict(brightness=1)]
        position=[3,4,5]
        node.apply_attached_lights=lambda shader:shader.setUniformValue('u_attachedPosition[0]',position)
        node.scene_lighting_effects=lambda:(False,True)
        signature=self.owner.signature(self.renderer,self.view,self.root)
        with patch.object(module,'ToolheadPathGeometry',return_value=geometry):
            snap=self.owner.snapshot(node,self.renderer,NS(getCameraLightPosition=lambda:None),signature)
        position[0]=999;node._attached_lights.clear();self.view.getCurrentLayer.return_value=9
        self.assertEqual(snap.lighting['u_attachedPosition[0]'],[3,4,5])
        self.assertEqual(snap.light_effects,(False,True))
        self.assertEqual(snap.path_top[id(geometry)],2)

    def test_bounds_preflight_is_chunked_cached_and_transformed_without_whole_print_scan(self):
        vertices = np.zeros((module.BOUNDS_CHUNK+3,3))
        vertices[-1] = [10,20,30]
        mesh = HashMesh(vertices)
        geometry = NS(mesh=mesh)
        transform = Matrix(); transform.data[0,3] = 40
        snap = module.EnvironmentSnapshot(self.owner,(0,0,0),None,[],[(geometry,transform,(0,2),None,0)],{},False)
        work = snap.prepare()
        next(work)()
        self.assertIsNone(snap.descriptor)
        self.assertNotIn(mesh,self.owner._mesh_bounds)
        next(work)()
        self.assertIsNone(snap.descriptor)
        with self.assertRaises(StopIteration): next(work)
        np.testing.assert_allclose(snap.descriptor.minimum,(38,-2,-2))
        np.testing.assert_allclose(snap.descriptor.maximum,(52,22,32))
        self.assertLess(snap.descriptor.far,100.)
        transform.data[0,3] = -10
        other = module.EnvironmentSnapshot(self.owner,(0,0,0),None,[],[(geometry,transform,(0,2),None,0)],{},False)
        mesh.getVertices = Mock(side_effect=AssertionError('cached immutable mesh must not be rescanned'))
        self.assertEqual(list(other.prepare()),[])
        np.testing.assert_allclose(other.descriptor.minimum,(-12,-2,-2))

    def test_admission_prefix_transform_origin_and_pruning(self):
        visible, hidden, stale = (HashMesh(), HashMesh(), HashMesh())
        transform = Matrix()
        transform.data[0, 3] = 25
        child = self.child(visible)
        child.getWorldTransformation = lambda: transform
        self.children = [child, self.child(hidden, visible=False), self.child(HashMesh(), outside=True), self.child(HashMesh(), disabled=True)]
        platform = HashMesh([[-100, 2, -100], [100, 8, -100], [100, 2, 100]])
        grid = HashMesh([[-100, 0, -100], [100, 0, -100], [100, 0, 100]], uv=True)
        nonbed = HashMesh([[0, 0, 0], [1, 1, 1], [2, 2, 2]], uv=True)
        plate = Platform()
        plate.getMeshData = lambda: platform
        plate.callDecoration = lambda name: None
        self.children.append(plate)
        self.batches = [NS(renderMode=4, items=[dict(mesh=m, transformation=Matrix()) for m in (platform, grid, nonbed)]), NS(renderMode=1, items=[dict(mesh=HashMesh(), transformation=Matrix())])]
        self.owner._geometry = {stale: object()}
        self.owner._plate_kinds = {stale: 'grid'}
        shaders = {kind: Mock(name=kind) for kind in ('platform', 'grid')}
        self.owner.mesh_shader = Mock(side_effect=lambda kind: shaders[kind])
        geometry = Mock()
        factory = Mock(return_value=geometry)
        signature = self.owner.signature(self.renderer, self.view, self.root)
        transform.data[0, 3] = 999
        with patch.object(module, 'ToolheadPathGeometry', factory):
            snap = self.owner.snapshot(self.snapshot_node(), self.renderer, NS(getCameraLightPosition=lambda: 'light'), signature)
        self.assertEqual([plate[0] for plate in snap.plates], [shaders['platform'], shaders['grid']])
        self.assertEqual(len(snap.paths), 1)
        self.assertIs(snap.paths[0][0], geometry)
        np.testing.assert_allclose(snap.paths[0][1].getData()[0, 3], 25)
        self.assertEqual(snap.paths[0][2], (0, 4))
        self.assertEqual(snap.paths[0][4], 2)
        self.assertEqual(snap.origin, (5.0, 8.0, 11.0))
        self.assertEqual(snap.light, 'light')
        factory.assert_called_once_with(visible, build_bounds=False, index_chunk=module.PATH_CHUNK)
        self.assertEqual(set(self.owner._geometry), {visible})
        self.assertNotIn(stale, self.owner._plate_kinds)
        self.view.getCurrentLayer.return_value = 2
        self.view.getCurrentPath.return_value = 999
        self.assertEqual(snap.paths[0][2], (0, 4), 'new live prefix cannot extend admitted snapshot')

    def test_over_budget_bed_rejected_before_full_extent_scan(self):
        oversized = HashMesh(np.zeros((module.MAX_BED_VERTICES + 1, 3)), uv=True)
        self.batches = [NS(renderMode=4, items=[dict(mesh=oversized, transformation=Matrix())])]
        sig = self.owner.signature(self.renderer, self.view, self.root)
        with patch.object(module.np, 'ptp', side_effect=AssertionError('unbounded extent scan')):
            snap = self.owner.snapshot(self.snapshot_node(), self.renderer, NS(getCameraLightPosition=lambda: None), sig)
        self.assertEqual(snap.plates, [])
        self.assertIsNone(self.owner._plate_kinds[oversized])

    def test_explicit_platform_identity_wins_over_planar_uv_heuristic(self):
        bed = HashMesh([[-100, 0, -100], [100, 0, -100], [100, 0, 100]], uv=True)
        platform = Platform()
        platform.getMeshData = lambda: bed
        platform.callDecoration = lambda name: None
        self.children = [platform]
        self.batches = [NS(renderMode=4, items=[dict(mesh=bed, transformation=Matrix())])]
        self.owner.mesh_shader = Mock(return_value=Mock())
        sig = self.owner.signature(self.renderer, self.view, self.root)
        self.owner.snapshot(self.snapshot_node(), self.renderer, NS(getCameraLightPosition=lambda: None), sig)
        self.owner.mesh_shader.assert_called_once_with('platform')

    def test_attached_light_capture_is_frozen_additive_and_preserves_native_depth(self):
        geometry = Mock(); geometry.mesh = HashMesh()
        bed = HashMesh([[-10,0,-10],[10,0,-10],[10,0,10]])
        shader = Mock()
        self.owner.light_mesh_shader = Mock(return_value=shader)
        self.owner.light_path_shader = Mock(return_value=shader)
        self.owner.light_receiver = Mock(return_value=bed)
        self.owner.path_shader = Mock(return_value=shader)
        snap = module.EnvironmentSnapshot(self.owner,(0,0,0),None,
            [(shader, {'mesh':bed,'transformation':Matrix()}, {'u_plateColor':[1,1,1,1]})],
            [(geometry,Matrix(),(0,4),([0,0,0],[1,0,0],.3),2)], {}, False)
        snap.descriptor = module.ProbeDescriptor((0,0,0),(-10,-10,-10),(10,10,10))
        snap.lighting = {'u_lightOpacity':.4,'u_attachedCount':1,'u_attachedPosition[0]':[3,4,5]}
        snap.light_effects = (True,True);snap.path_top[id(geometry)]=2
        gl=Mock()
        commands=list(snap.commands(0))
        # One bed and two native path spans precede the additive commands.
        self.assertEqual(len(commands),5)
        commands[-2](gl);commands[-1](gl)
        gl.glDepthMask.assert_called_with(False)
        gl.glBlendFuncSeparate.assert_called_with(gl.GL_SRC_ALPHA,gl.GL_ONE,gl.GL_ZERO,gl.GL_ONE)
        shader.setUniformValue.assert_any_call('u_attachedPosition[0]',[3,4,5])
        shader.setUniformValue.assert_any_call('u_lightingFirstTopElement',2)
        shader.setUniformValue.assert_any_call('u_lightingShadowElements',2)
        shader.setUniformValue.assert_any_call('u_last_line_ratio',.3)
        geometry.render.assert_called_once()
        self.assertEqual(geometry.render.call_args.args[3],[(0,4)])
        options=self.modules['UM.View.RenderBatch'].RenderBatch.call_args.kwargs
        # RenderBatch resets depth/blending itself: reapply after its setup.
        gl.reset_mock();options['state_setup_callback'](gl)
        gl.glDepthMask.assert_called_once_with(False)
        gl.glCullFace.assert_called_once_with(gl.GL_BACK)
        gl.glFrontFace.assert_called_once_with(gl.GL_CCW)

    def test_path_shader_fault_backs_off_and_recovery_publishes_six_faces(self):
        now = [10.0]
        storage = Mock()
        environment = ToolheadEnvironment(clock=lambda: now[0], storage_factory=lambda *args: storage)
        geometry = Mock()
        geometry.mesh = HashMesh()
        snap = module.EnvironmentSnapshot(self.owner, (0, 0, 0), 'light', [], [(geometry, Matrix(), (0, 2), None, 0)], {}, False)
        self.owner.path_shader = Mock(side_effect=RuntimeError('native path shader compile failed'))
        with patch('mpf.toolhead.ToolheadEnvironment.preserved_state', side_effect=lambda *args: nullcontext()):
            step = lambda: environment.step(Mock(), object_context, 'file', 'pose', lambda: snap)
            object_context = object()
            self.assertTrue(step())  # Bounded scene-bounds preflight.
            self.assertFalse(step())
            self.assertIn('compile failed', environment.failure)
            for _ in range(20):
                self.assertFalse(step())
            self.owner.path_shader.assert_called_once()
            storage.publish.assert_not_called()
            now[0] = 15.0
            self.owner.path_shader.side_effect = None
            self.owner.path_shader.return_value = Mock()
            for _ in range(12):
                step()
        self.assertTrue(environment.available, environment.failure)
        self.assertEqual(environment.failure, '')
        self.assertEqual(storage.copy.call_count, 6)
        storage.publish.assert_called_once()


class EnvironmentImageTests(unittest.TestCase):
    setUp=EnvironmentSceneTests.setUp

    def snapshot(self, texture):
        node=NS(_view=self.view,_root=self.root,_model_bounds=np.array([[0,0,0],[1,1,1]]),render_position=lambda:NS(x=0,y=0,z=0))
        signature=self.owner.signature(self.renderer,self.view,self.root)
        return self.owner.snapshot(node,self.renderer,NS(getCameraLightPosition=lambda:None),(*signature[:4],texture))

    def test_platform_texture_budget_precedes_decode_and_failed_key_remains_retryable(self):
        from PyQt6.QtGui import QImage
        with tempfile.TemporaryDirectory() as folder:
            filename=str(Path(folder)/'bed.png')
            image=QImage(8,8,QImage.Format.Format_RGB32);image.fill(0xff55aa11);self.assertTrue(image.save(filename))
            reader=Mock();reader.size.return_value=NS(isValid=lambda:True,width=lambda:99999,height=lambda:99999)
            with patch('PyQt6.QtGui.QImageReader',return_value=reader):
                with self.assertRaisesRegex(RuntimeError,'pixel budget'):self.snapshot(filename)
                reader.read.assert_not_called()
                self.assertIsNone(self.owner._platform_key)
            with patch.object(Path,'stat',return_value=NS(st_size=17*1024*1024)):
                with self.assertRaisesRegex(RuntimeError,'reflection budget'):self.snapshot(filename)
            self.snapshot(filename)
            self.assertEqual(self.owner._platform_key,filename)
            self.assertEqual(self.owner._texture.setImage.call_args.args[0].size(),image.size())
            previous=self.owner._texture
            reader.size.return_value=image.size();reader.read.return_value=QImage()
            broken_filename=str(Path(folder)/'unavailable.png')
            self.assertTrue(image.save(broken_filename))
            with patch('PyQt6.QtGui.QImageReader',return_value=reader):
                with self.assertRaisesRegex(RuntimeError,'unavailable'):self.snapshot(broken_filename)
            self.assertIs(self.owner._texture,previous)

    def test_owned_platform_shader_cache_and_plate_command_use_detached_batch(self):
        with patch.object(module,'owned_shader',return_value=self.shader) as factory:
            self.assertIs(self.owner.mesh_shader('grid'),self.shader)
            self.assertIs(self.owner.mesh_shader('grid'),self.shader)
            factory.assert_called_once()
        batch=Mock();constructor=Mock(return_value=batch);constructor.RenderType=NS(Transparent=2)
        item=dict(mesh=object(),transformation=Matrix(),normal_transformation='normal',uniforms={'native':1})
        snapshot=module.EnvironmentSnapshot(self.owner,(0,0,0),'light',[(self.shader,item,{'u_plateColor':[1,0,0,1]})],[],{},False)
        snapshot.descriptor = module.ProbeDescriptor((0,0,0),(-10,-10,-10),(10,10,10))
        with patch.dict(sys.modules,{'UM.View.RenderBatch':NS(RenderBatch=constructor)}):
            commands=list(snapshot.commands(0));self.assertEqual(len(commands),1);commands[0](Mock())
        batch.addItem.assert_called_once_with(item['transformation'],mesh=item['mesh'],uniforms={'native':1},normal_transformation='normal')
        batch.render.assert_called_once()
        for inherited_equation in ('REVERSE_SUBTRACT','MIN'):
            bindings=Mock();bindings.GL_FUNC_ADD=0x8006;bindings.GL_LESS=0x0201
            bindings.glBlendEquationSeparate(inherited_equation,inherited_equation)
            constructor.call_args.kwargs['state_setup_callback'](bindings)
            bindings.glBlendEquation.assert_called_once_with(0x8006)
            bindings.glDepthMask.assert_called_once_with(True)
            bindings.glDepthFunc.assert_called_once_with(0x0201)
