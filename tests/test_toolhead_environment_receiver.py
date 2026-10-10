"""Frozen receiver publications and real completed consumer read tickets."""
from contextlib import contextmanager
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch
import numpy as np

from mpf.toolhead import ToolheadEnvironmentReceiver as receiver
from mpf.toolhead.ToolheadEnvironment import ProbeDescriptor
from tests import test_toolhead_async_environment as async_fixture


class _Matrix:
    def __init__(self,data): self.data=np.array(data,copy=True)
    def getData(self): return self.data


class _Vector:
    def __init__(self,x,y,z): self.x,self.y,self.z=x,y,z


class _Mesh:
    def __init__(self,vertices=None,normals=None,indices=None,colors=None,uvs=None,attributes=None):
        self.vertices=vertices;self.normals=normals;self.indices=indices;self._colors=colors
        self.uvs=uvs;self.attributes=attributes or {}
    def getVertices(self):return self.vertices
    def getNormals(self):return self.normals
    def getIndices(self):return self.indices
    def getUVCoordinates(self):return self.uvs
    def getVertexCount(self):return len(self.vertices)
    def getFaceCount(self):return len(self.indices)
    def hasIndices(self):return self.indices is not None
    def hasNormals(self):return self.normals is not None
    def hasColors(self):return self._colors is not None
    def hasUVCoordinates(self):return self.uvs is not None
    def attributeNames(self):return tuple(self.attributes)
    def getAttribute(self,name):return self.attributes.get(name)


class ReceiverValueTests(unittest.TestCase):
    def setUp(self):
        context=patch.dict(sys.modules,{'UM.Math.Matrix':NS(Matrix=_Matrix),
            'UM.Math.Vector':NS(Vector=_Vector),'UM.Math.Color':NS(Color=type('_Color',(),{})),
            'UM.Mesh.MeshData':NS(MeshData=_Mesh)})
        context.start();self.addCleanup(context.stop)

    def mesh(self):
        return _Mesh(np.arange(18,dtype=np.float32).reshape(6,3),
            np.ones((6,3),np.float32),np.array([[0,1,2],[3,4,5]],np.uint32),
            np.ones((6,4),np.float32),np.zeros((6,2),np.float32),
            {'finish':dict(value=np.arange(6,dtype=np.float32),opengl_name='a_finish',opengl_type='float')})

    def shutter_node(self,rotating=False):
        solid,glass=self.mesh(),self.mesh(); model=_Matrix(np.eye(4)); normal=_Matrix(np.eye(4))
        camera=NS(getInverseWorldTransformation=lambda:model,getProjectionMatrix=lambda:model,
            getWorldPosition=lambda:_Vector(1,2,3),getCameraLightPosition=lambda:_Vector(4,5,6))
        values=receiver.ReceiverValues.freeze(NS(receiver_uniforms=lambda camera:{}),camera)
        node=NS(_rotor_meshes={1:(solid,glass)} if rotating else {},_translucent_mesh=glass,
            getMeshData=lambda:solid,render_transformation=lambda:model,_render_normal=normal)
        return node,values,solid,glass,model,normal

    def test_frozen_static_shutter_preserves_solid_glass_group_and_matrices(self):
        node,values,solid,glass,model,normal=self.shutter_node()
        plan=receiver.ReceiverShutter.freeze(node,values,(),existing_bytes=0,byte_budget=1<<20)
        self.assertEqual(plan.fractions,(0.,)); self.assertEqual(len(plan.plans),1)
        batch=plan.plans[0][0]
        self.assertIsNotNone(batch.opaque); self.assertIsNotNone(batch.translucent)
        model.data[:]=5;normal.data[:]=6;solid.vertices[:]=9;glass._colors[:]=.2
        caller=Mock(); mapping=object(); layer=object(); batch.draw(caller,values,mapping,layer_draw=layer)
        args,kwargs=caller._draw_mesh.call_args
        self.assertIs(args[0],values.camera); self.assertIs(args[1],batch.opaque.mesh)
        self.assertIs(args[2],batch.translucent.mesh)
        np.testing.assert_array_equal(args[3].getData(),np.eye(4))
        np.testing.assert_array_equal(args[4].getData(),np.eye(4))
        self.assertIs(kwargs['receiver_values'],values); self.assertIs(kwargs['receiver_map'],mapping)
        self.assertIs(kwargs['layer_draw'],layer)
        replacement=receiver.ReceiverValues(values.camera,values.uniforms)
        with self.assertRaisesRegex(ValueError,'Original shutter camera'):
            batch.draw(caller,replacement,mapping)

    def test_static_partition_keeps_original_nonrotor_solid_glass_group(self):
        node,values,solid,glass,_model,_normal=self.shutter_node()
        frozen=receiver.ReceiverStatic.freeze(node,values,existing_bytes=0,byte_budget=1<<20)
        self.assertEqual(len(frozen.plan),1)
        self.assertIs(frozen.batch_for(solid),frozen.batch_for(glass))
        self.assertIsNotNone(frozen.plan[0].opaque);self.assertIsNotNone(frozen.plan[0].translucent)
        self.assertIsNone(frozen.batch_for(self.mesh()))

    def test_static_fan_partition_excludes_moving_meshes_and_retains_independent_glass(self):
        node,values,solid,_glass,model,normal=self.shutter_node(True)
        rotor_solid,rotor_glass=self.mesh(),self.mesh()
        glass_a,glass_b=self.mesh(),self.mesh()
        node._rotor_meshes={1:(rotor_solid,rotor_glass)}
        node._static_transparent=[glass_a,glass_b]
        node.rotor_draw_plan=Mock(side_effect=AssertionError('sampled a moving phase'))
        frozen=receiver.ReceiverStatic.freeze(node,values,existing_bytes=0,byte_budget=2<<20)
        self.assertEqual(len(frozen.plan),3);self.assertEqual(len(frozen.meshes),3)
        self.assertIsNone(frozen.plan[0].translucent)
        self.assertTrue(all(batch.opaque is None for batch in frozen.plan[1:]))
        self.assertIsNone(frozen.batch_for(rotor_solid));self.assertIsNone(frozen.batch_for(rotor_glass))
        # Native sorting retains original IDs/centres, then substitution selects
        # the exact static publication in either new glass order.
        for order in ((glass_a,rotor_glass,glass_b),(glass_b,rotor_glass,glass_a)):
            self.assertEqual(tuple(frozen.batch_for(mesh) is not None for mesh in order),(True,False,True))
        self.assertIs(frozen.batch_for(glass_a),frozen.plan[1])
        model.data[:]=7;normal.data[:]=8;solid.vertices[:]=9;glass_a._colors[:]=.2
        np.testing.assert_array_equal(receiver.thaw_uniform(frozen.plan[1].model).getData(),np.eye(4))
        np.testing.assert_array_equal(receiver.thaw_uniform(frozen.plan[2].normal).getData(),np.eye(4))
        self.assertEqual(frozen.retained_bytes,receiver.METADATA_BYTES+4096*3+
            sum(mesh.retained_bytes for mesh in frozen.meshes))

    def test_static_partition_refuses_duplicates_missing_or_unadmitted_sources(self):
        node,values,solid,glass,_model,_normal=self.shutter_node(True)
        args=dict(existing_bytes=0,byte_budget=1<<20)
        for sources in ([glass,glass],[solid],[glass]*65):
            node._static_transparent=sources
            with self.assertRaises(ValueError):receiver.ReceiverStatic.freeze(node,values,**args)
        node._static_transparent=[];node.getMeshData=lambda:None
        with self.assertRaises(ValueError):receiver.ReceiverStatic.freeze(node,values,**args)
        node.getMeshData=lambda:solid;node._static_transparent=[glass]
        with patch.object(receiver.ReceiverMesh,'freeze',side_effect=AssertionError('copy before admission')):
            with self.assertRaises(MemoryError):receiver.ReceiverStatic.freeze(node,values,existing_bytes=0,byte_budget=1)
        with patch.object(receiver.ReceiverMesh,'source_current',return_value=False):
            with self.assertRaisesRegex(ValueError,'changed during freeze'):
                receiver.ReceiverStatic.freeze(node,values,**args)
        with self.assertRaises(ValueError):receiver.ReceiverStatic.freeze(node,values,existing_bytes=True,byte_budget=1<<20)

    def test_three_pose_shutter_copies_rows_once_preserves_order_and_deduplicates_meshes(self):
        node,values,solid,glass,model,normal=self.shutter_node(True)
        rotor_solid,rotor_glass=self.mesh(),self.mesh(); calls=[]
        row=dict(body=1,centre=[1.,2.,3.],axis=[0.,0.,1.],direction=-1)
        def draw_plan(camera,poses,fraction):
            self.assertIs(camera,values.camera); self.assertIsNot(poses[0][0],row)
            calls.append((poses,fraction))
            matrix=_Matrix(np.eye(4));matrix.data[0,3]=fraction
            return ((rotor_solid,False,matrix,normal),(rotor_glass,True,matrix,normal),(glass,True,model,normal))
        node.rotor_draw_plan=draw_plan
        plan=receiver.ReceiverShutter.freeze(node,values,((row,.8,.3,'fan'),),existing_bytes=0,byte_budget=2<<20)
        self.assertEqual(plan.fractions,(-.5,0.,.5));self.assertEqual(len(calls),3)
        self.assertEqual(len(plan.meshes),4)
        self.assertTrue(all(len(pose)==4 for pose in plan.plans))
        self.assertTrue(all(pose[0].translucent is None for pose in plan.plans))
        self.assertTrue(all(pose[1].opaque is plan.plans[0][1].opaque for pose in plan.plans))
        self.assertTrue(all(pose[2].translucent is plan.plans[0][2].translucent for pose in plan.plans))
        self.assertTrue(all(pose[3].translucent is plan.plans[0][3].translucent for pose in plan.plans))
        row['centre'][0]=100;model.data[:]=10
        self.assertEqual(plan.phase_key[0][1],(1.,2.,3.))
        self.assertEqual(calls[0][0][0][0]['centre'],(1.,2.,3.))
        for fraction,pose in zip(plan.fractions,plan.plans,strict=True):
            self.assertEqual(receiver.thaw_uniform(pose[1].model).getData()[0,3],fraction)
        self.assertEqual(plan.retained_bytes,receiver.METADATA_BYTES+4096*12+
                         sum(mesh.retained_bytes for mesh in plan.meshes))

    def test_shutter_refuses_invalid_pose_matrix_budget_and_changed_publication(self):
        node,values,solid,_glass,model,_normal=self.shutter_node()
        for options in ({'poses':[]},{'existing_bytes':True},{'byte_budget':0}):
            args=dict(poses=(),existing_bytes=0,byte_budget=1<<20);args.update(options)
            with self.assertRaises(ValueError):receiver.ReceiverShutter.freeze(node,values,**args)
        row=dict(body=1,centre=(0.,0.,0.),axis=(0.,0.,1.),direction=1)
        with self.assertRaises(ValueError):
            receiver.ReceiverShutter.freeze(node,values,((row,0.,float('nan'),''),),existing_bytes=0,byte_budget=1<<20)
        model.data[0,0]=float('nan')
        with self.assertRaises(ValueError):receiver.ReceiverShutter.freeze(node,values,(),existing_bytes=0,byte_budget=1<<20)
        model.data[0,0]=1
        with patch.object(receiver.ReceiverMesh,'freeze',side_effect=AssertionError('copy before metadata admission')):
            with self.assertRaises(MemoryError):receiver.ReceiverShutter.freeze(node,values,(),existing_bytes=0,byte_budget=10)
        with patch.object(receiver.ReceiverMesh,'source_current',return_value=False):
            with self.assertRaisesRegex(ValueError,'shutter freeze'):
                receiver.ReceiverShutter.freeze(node,values,(),existing_bytes=0,byte_budget=1<<20)
        node.getMeshData=lambda:None;node._translucent_mesh=None
        with self.assertRaises(ValueError):receiver.ReceiverShutter.freeze(node,values,(),existing_bytes=0,byte_budget=1<<20)

    def test_sharp_shutter_and_absent_normal_need_no_new_motion_sample(self):
        node,values,_solid,_glass,_model,_normal=self.shutter_node(); node._render_normal=None
        node._rotor_meshes={1:(None,None)};node.rotor_draw_plan=lambda *args:()
        row=dict(body=1,centre=(0.,0.,0.),axis=(0.,0.,1.),direction=1)
        plan=receiver.ReceiverShutter.freeze(node,values,((row,.2,0.,''),),existing_bytes=0,byte_budget=1<<20)
        self.assertEqual(plan.fractions,(0.,))
        caller=Mock();plan.plans[0][0].draw(caller,values,None)
        self.assertIsNone(caller._draw_mesh.call_args.args[4])

    def test_missing_duplicate_and_oversized_rotor_poses_cannot_publish_complete_plan(self):
        node,values,_solid,_glass,_model,_normal=self.shutter_node(True)
        row=dict(body=1,centre=(0.,0.,0.),axis=(0.,0.,1.),direction=1)
        pose=(row,0.,.3,'')
        for poses in ((),(pose,pose)):
            with self.assertRaisesRegex(ValueError,'every original rotor'):
                receiver.ReceiverShutter.freeze(node,values,poses,existing_bytes=0,byte_budget=1<<20)
        row['centre']=(0.,)*100
        with self.assertRaisesRegex(ValueError,'Three bounded'):
            receiver.ReceiverShutter.freeze(node,values,(pose,),existing_bytes=0,byte_budget=1<<20)
        with self.assertRaisesRegex(ValueError,'bounded sampled'):
            receiver.ReceiverShutter.freeze(node,values,([],),existing_bytes=0,byte_budget=1<<20)
        row['centre']=(np.ones(10000),0.,0.)
        with patch.object(receiver.np,'isfinite',side_effect=AssertionError('Read nested array before scalar admission')):
            with self.assertRaisesRegex(ValueError,'Plain numeric'):
                receiver.ReceiverShutter.freeze(node,values,(pose,),existing_bytes=0,byte_budget=1<<20)

    def test_owned_mesh_isolated_from_host_mutation_and_gpu_cache(self):
        original=self.mesh();original._vertex_buffer=object()
        frozen=receiver.ReceiverMesh.freeze(original,existing_bytes=1024,byte_budget=1024*1024)
        before=frozen.mesh.vertices.copy()
        original.vertices[:]=-1;original._colors[:]=.25;original.attributes['finish']['value'][:]=9
        np.testing.assert_array_equal(frozen.mesh.vertices,before)
        np.testing.assert_array_equal(frozen.mesh._colors,np.ones((6,4)))
        np.testing.assert_array_equal(frozen.mesh.getAttribute('finish')['value'],np.arange(6))
        self.assertFalse(hasattr(frozen.mesh,'_vertex_buffer'))
        self.assertFalse(frozen.mesh.vertices.flags.writeable)
        self.assertFalse(frozen.mesh.vertices.base.flags.writeable)
        self.assertEqual(frozen.mesh.getAttribute('finish')['value'].tostring(),np.arange(6,dtype=np.float32).tobytes())
        self.assertTrue(frozen.source_current()) # Descriptor check, not arbitrary hidden writes.
        original.vertices=original.vertices.copy()
        self.assertFalse(frozen.source_current())
        cpu=sum(array.nbytes for array in (before,original.normals,original.indices,
            original._colors,original.uvs,original.getAttribute('finish')['value']))
        self.assertEqual(frozen.retained_bytes,3*cpu+receiver.METADATA_BYTES)
        self.assertEqual(frozen.count,2)

    def test_copy_refuses_before_allocation_and_handles_absent_channels(self):
        mesh=_Mesh(np.zeros((3,3),np.float32))
        for existing,budget in ((-1,1000),(0,0),(True,100000)):
            with self.assertRaisesRegex(ValueError,'ledger'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=existing,byte_budget=budget)
        with patch.object(sys.modules['UM.Mesh.MeshData'],'MeshData',side_effect=AssertionError('Allocated before admission')):
            with self.assertRaisesRegex(MemoryError,'copy exceeds'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=100000,byte_budget=100001)
        frozen=receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        self.assertIsNone(frozen.mesh.indices);self.assertEqual(frozen.count,1)

    def test_native_dtype_attribute_and_alignment_guards(self):
        mesh=self.mesh();mesh.vertices=mesh.vertices.astype(np.float64)
        with self.assertRaisesRegex(ValueError,'float32'):
            receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        mesh=self.mesh();mesh.attributes['finish']['opengl_type']='vector3f'
        mesh.attributes['finish']['value']=np.zeros((6,3),np.float32)
        with self.assertRaisesRegex(ValueError,'attribute ABI'):
            receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        mesh=self.mesh();mesh.attributes['finish']['opengl_type']='int'
        with self.assertRaisesRegex(ValueError,'attribute ABI'):
            receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        mesh.vertices=np.ndarray((6,3),np.float32,buffer=bytearray(73),offset=1)
        with self.assertRaisesRegex(ValueError,'Aligned'):
            receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)

    def test_source_replacement_during_copy_and_unexpected_native_copy_are_rejected(self):
        mesh=self.mesh()
        def changed(**values):mesh.vertices=mesh.vertices.copy();return _Mesh(**values)
        with patch.object(sys.modules['UM.Mesh.MeshData'],'MeshData',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed during'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        def extra_backing(**values):
            large=np.zeros((1000,3),np.float32);values['vertices']=large[:6];return _Mesh(**values)
        with patch.object(sys.modules['UM.Mesh.MeshData'],'MeshData',side_effect=extra_backing):
            with self.assertRaisesRegex(ValueError,'unexpected backing'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)

    def test_forged_readable_span_is_refused_before_copy(self):
        mesh=_Mesh(np.lib.stride_tricks.as_strided(np.ones(1,np.float32),shape=(3,3),strides=(12,4)))
        with patch.object(receiver.np,'array',side_effect=AssertionError('Copy before readable certificate')):
            with self.assertRaisesRegex(ValueError,'allocation'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        mesh.vertices=np.lib.stride_tricks.as_strided(np.ones(1,np.float32),shape=(3,3),strides=(24,8))
        with patch.object(receiver.np,'asarray',side_effect=AssertionError('Read/reshape before span certificate')):
            with self.assertRaisesRegex(ValueError,'Contiguous'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)

    def test_invalid_colour_publication_never_invokes_native_byte_fallback(self):
        mesh=self.mesh();mesh._colors=mesh._colors.astype(np.float64)
        mesh.getColorsAsByteArray=Mock(side_effect=AssertionError('Read before colour admission'))
        with self.assertRaisesRegex(ValueError,'receiver colours'):
            receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)
        mesh.getColorsAsByteArray.assert_not_called()

    def test_getter_replacement_cannot_reenter_copy_with_uncertified_arrays(self):
        mesh=self.mesh();valid=mesh.vertices
        forged=np.lib.stride_tricks.as_strided(np.ones(1,np.float32),shape=(6,3),strides=(12,4))
        calls=[0]
        def vertices():
            calls[0]+=1
            return valid if calls[0]<=2 else forged
        mesh.getVertices=vertices
        array=receiver.np.array
        def safe_copy(value,*args,**kwargs):
            if isinstance(value,np.ndarray) and value.ctypes.data==forged.ctypes.data:
                raise AssertionError('Uncertified replacement read during copy')
            return array(value,*args,**kwargs)
        with patch.object(receiver.np,'array',side_effect=safe_copy):
            with self.assertRaisesRegex(ValueError,'changed during freeze'):
                receiver.ReceiverMesh.freeze(mesh,existing_bytes=0,byte_budget=100000)

    def test_camera_and_material_values_survive_mutation_and_return_fresh_native_values(self):
        view=_Matrix(np.eye(4));projection=_Matrix(np.eye(4));eye=_Vector(1,2,3);light=_Vector(4,5,6)
        camera=NS(getInverseWorldTransformation=lambda:view,getProjectionMatrix=lambda:projection,
            getWorldPosition=lambda:eye,getCameraLightPosition=lambda:light)
        values={'u_lightOpacity':.98,'u_surfaceDetail':.7,'u_viewDirection':[0.,0.,1.],
            'u_attachedPaint[0]':[.1,.2,.3,.4]}
        def uniforms(frozen):
            self.assertIsNot(frozen,camera);self.assertEqual(frozen.getWorldPosition().x,1)
            return values
        frozen=receiver.ReceiverValues.freeze(NS(receiver_uniforms=uniforms),camera)
        view.data[:]=8;projection.data[:]=9;eye.x=10;light.z=20;values['u_attachedPaint[0]'][0]=1
        shader=Mock();frozen.apply(shader)
        shader.setUniformValue.assert_any_call('u_attachedPaint[0]',[.1,.2,.3,.4])
        np.testing.assert_array_equal(frozen.camera.getInverseWorldTransformation().getData(),np.eye(4))
        np.testing.assert_array_equal(frozen.camera.getProjectionMatrix().getData(),np.eye(4))
        self.assertEqual(frozen.camera.getWorldPosition().x,1)
        self.assertEqual(frozen.camera.getCameraLightPosition().z,6)
        frozen.camera.getProjectionMatrix().getData()[:]=0
        np.testing.assert_array_equal(frozen.camera.getProjectionMatrix().getData(),np.eye(4))


class ReceiverMapTests(unittest.TestCase):
    def setUp(self):
        self.fixture=async_fixture.AsyncEnvironmentTests('test_query_only_and_nested_queries_share_one_last_use_ticket')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        context=patch.dict(sys.modules,{'UM.Math.Matrix':NS(Matrix=_Matrix),
            'UM.Math.Vector':NS(Vector=_Vector),'UM.Math.Color':NS(Color=type('_Color',(),{}))})
        context.start();self.addCleanup(context.stop)
        self.binding,self.payload,self.geometry=self.fixture.query_binding()
        self.owner=self.fixture.owner;self.owner._selected_key=self.payload.key
        self.payload.descriptor=ProbeDescriptor((0.,1.,2.),(-10.,-10.,-10.),(10.,10.,10.))

    def test_pin_uses_real_consumer_ticket_and_original_map_after_owner_changes(self):
        frozen=receiver.ReceiverMap.freeze(self.owner);self.assertTrue(frozen.current())
        with frozen.query_scope() as geometry:
            self.assertIs(geometry.geometry,self.geometry);self.assertEqual(self.geometry.reads,1)
            self.assertEqual(geometry.key,frozen.source_key)
            self.assertIsNotNone(self.binding._use)
            shader=Mock();frozen.apply(shader)
            shader.setTexture.assert_any_call(7,self.binding)
            shader.setTexture.assert_any_call(6,self.binding.depth)
            shader.setUniformValue.assert_any_call('u_probe',[0.,1.,2.])
            self.assertIn(shader,self.owner._shaders)
        self.assertEqual(self.geometry.reads,0);self.assertIsNone(self.binding._use)
        newer=Mock();self.owner._storage=newer
        frozen.release_bindings();newer.release.assert_not_called()
        with frozen.query_scope() as geometry:self.assertIsNone(geometry)
        with self.assertRaisesRegex(RuntimeError,'publication changed'):frozen.apply(Mock())

    def test_selection_and_context_retirement_refuse_pin_and_query(self):
        frozen=receiver.ReceiverMap.freeze(self.owner)
        self.owner._selected_key=('new scene',)
        self.assertIsNone(receiver.ReceiverMap.freeze(self.owner))
        with frozen.query_scope() as geometry:self.assertIsNone(geometry)
        self.owner._selected_key=self.payload.key
        self.owner._main_retired=True;self.assertFalse(frozen.current())
        self.assertIsNone(receiver.ReceiverMap.freeze(self.owner))
        self.owner._main_retired=False;self.owner._quarantine=True
        self.assertFalse(frozen.current())

    def test_refusal_between_initial_check_and_admitted_scope_returns_no_source(self):
        frozen=receiver.ReceiverMap.freeze(self.owner)
        original=self.binding.query
        @contextmanager
        def changed(*args):
            with original(*args) as geometry:
                self.owner._selected_key=('superseded',);yield geometry
        with patch.object(self.binding,'query',changed):
            with frozen.query_scope() as geometry:self.assertIsNone(geometry)
        self.assertEqual(self.geometry.reads,0);self.assertIsNone(self.binding._use)

    def test_both_binding_restores_attempted_if_colour_restore_fails(self):
        frozen=receiver.ReceiverMap.freeze(self.owner)
        with patch.object(self.binding,'release',side_effect=[RuntimeError('colour restore'),None]) as release:
            with self.assertRaisesRegex(RuntimeError,'colour restore'):frozen.release_bindings()
            self.assertEqual([call.args[0] for call in release.call_args_list],[7,6])

    def test_owner_close_withdraws_receiver_texture_registrations(self):
        frozen=receiver.ReceiverMap.freeze(self.owner);shader=Mock()
        frozen.apply(shader);shader.setTexture.reset_mock()
        self.owner.close()
        shader.setTexture.assert_any_call(7,None)
        shader.setTexture.assert_any_call(6,None)
        self.assertFalse(frozen.current())


if __name__=='__main__':unittest.main()
