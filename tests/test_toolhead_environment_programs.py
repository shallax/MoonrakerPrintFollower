"""Real host-state guards with harmless shaders; never the failed PBR query.

Geometry/query semantics have separate bounded native receipts. These tests
exercise the new owner's admission, callback failures and exact-context drain.
"""
from types import SimpleNamespace as NS
from contextlib import contextmanager
import sys
import unittest
from unittest.mock import Mock,patch

from PyQt6.QtOpenGL import QOpenGLShaderProgram,QOpenGLShader,QOpenGLFramebufferObject,QOpenGLVertexArrayObject
from mpf.toolhead import ToolheadEnvironmentPrograms as module
from mpf.toolhead.ToolheadEnvironmentGeometry import GeometryUncertain
from mpf.toolhead.ToolheadEnvironmentReceiver import ReceiverValues,ReceiverBatch
from tests import test_toolhead_environment_gl as fixture


class _Program:
    def __init__(self):
        self._shader_program=QOpenGLShaderProgram()
        assert self._shader_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}')
        assert self._shader_program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nout vec4 colour;void main(){colour=vec4(.25,.5,.75,1.);}')
        assert self._shader_program.link()
        self.setTexture=Mock();self.setUniformValue=Mock()
        self.release=Mock(wraps=self._shader_program.release)
    def bind(self):self._shader_program.bind()  # Actual UM API returns None.


class ReceiverProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EnvironmentGLTests.setUpClass()
        cls.available=fixture.EnvironmentGLTests.available
        cls.context=fixture.EnvironmentGLTests.context
        cls.surface=getattr(fixture.EnvironmentGLTests,'surface',None)
        cls.gl=getattr(fixture.EnvironmentGLTests,'gl',None)
    @classmethod
    def tearDownClass(cls):fixture.EnvironmentGLTests.tearDownClass()

    def setUp(self):
        if not self.available:self.skipTest('Core GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface));self.owners=[];self.targets=[]
        self.factory=patch.object(module,'create_program',side_effect=self.make_program)
        self.factory.start();self.addCleanup(self.factory.stop)

    def tearDown(self):
        self.context.makeCurrent(self.surface);self.gl.glFinish()
        for owner in self.owners:
            # Explicit original-context drain reclaims deliberately uncertain
            # TEST owners only. Production never clears quarantine this way.
            owner.quarantined=False;owner._busy=False;owner.close()
        self.targets.clear();self.assertEqual(self.gl.glGetError(),0)

    def make_program(self,stage,solid,retain):
        program=_Program();retain(program);return program

    def owner(self,**options):
        args=dict(existing_bytes=91,byte_budget=32<<20);args.update(options)
        owner=module.ReceiverPrograms(self.gl,self.context,**args)
        self.owners.append(owner);return owner

    def target(self):
        target=QOpenGLFramebufferObject(8,8);self.targets.append(target);return target

    def query_state(self):
        target=self.target();self.assertTrue(target.bind());self.gl.glViewport(0,0,8,8)
        self.gl.glEnable(self.gl.GL_SCISSOR_TEST);self.gl.glScissor(0,0,8,8)
        for flag in (self.gl.GL_DEPTH_TEST,self.gl.GL_BLEND,self.gl.GL_CULL_FACE):self.gl.glDisable(flag)
        return tuple(self.target().texture() for _ in range(2))

    def values_plan(self):
        values=ReceiverValues(object(),())
        opaque=NS(mesh=object());glass=NS(mesh=object())
        plan=(ReceiverBatch(opaque,None,'model',None,values),
              ReceiverBatch(None,glass,'model',None,values))
        return values,plan

    def batch_scope(self,render):
        class Batch:
            RenderType=NS(Solid='solid')
            def __init__(self,shader,**options):self.shader=shader;self.options=options
            def addItem(self,*args,**kwargs):pass
            def render(self,camera):
                self.shader.bind();self.options['state_setup_callback'](outer.gl)
                render(self)
        outer=self
        return patch.dict(sys.modules,{'UM.View.RenderBatch':NS(RenderBatch=Batch),
            'UM.View.GL.OpenGL':NS(OpenGL=NS(getInstance=lambda:NS(_context=self.context)))})

    def test_admission_precedes_compile_and_refuses_foreign_context(self):
        with patch.object(module,'create_program',side_effect=AssertionError('compile before admission')):
            with self.assertRaises(MemoryError):self.owner(byte_budget=91)
            for options in ({'existing_bytes':True},{'existing_bytes':-1},{'byte_budget':0}):
                with self.assertRaises(ValueError):self.owner(**options)
        self.context.doneCurrent()
        with self.assertRaises(RuntimeError):self.owner()

    def test_link_failure_keeps_partial_graph_and_budget(self):
        made=[]
        def failed(stage,solid,retain):
            program=_Program();retain(program);made.append(program);raise RuntimeError('compile failed')
        with patch.object(module,'create_program',side_effect=failed):
            with self.assertRaises(GeometryUncertain) as caught:self.owner()
        owner=caught.exception.owner;self.owners.append(owner)
        self.assertTrue(owner.quarantined);self.assertIs(owner.programs['depth',False],made[0])
        self.assertEqual(owner.retained_bytes,module.PROGRAM_BYTES)

    def test_completed_query_restores_actual_program_vao_active_and_2d_bindings(self):
        owner=self.owner();textures=self.query_state();external=_Program();external.bind()
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create());vao.bind()
        self.gl.glActiveTexture(0x84C0);self.gl.glBindTexture(0x0DE1,textures[1])
        self.gl.glActiveTexture(0x84C7)
        source=NS(geometry=NS(apply=lambda shader:self.assertTrue(owner._busy)))
        try:
            owner.query(source,textures,100.)
            self.assertEqual(int(self.gl.glGetIntegerv(0x8B8D)),external._shader_program.programId())
            self.assertEqual(int(self.gl.glGetIntegerv(0x85B5)),vao.objectId())
            self.assertEqual(int(self.gl.glGetIntegerv(0x84E0)),0x84C7)
            self.gl.glActiveTexture(0x84C0)
            self.assertEqual(int(self.gl.glGetIntegerv(0x8069)),textures[1])
            self.assertFalse(owner._busy)
        finally:vao.release();vao.destroy();external.release()

    def test_query_rejects_unbounded_work_before_delivery(self):
        owner=self.owner();textures=self.query_state();source=NS(geometry=Mock())
        for pair,far in (((),100.),(textures,float('nan')),(textures,True),(textures,-1.)):
            with self.assertRaises(ValueError):owner.query(source,pair,far)
        self.gl.glScissor(0,0,17,8)
        with self.assertRaisesRegex(ValueError,'bounded sixteen'):owner.query(source,textures,100.)
        self.gl.glScissor(0,0,8,8);self.gl.glDisable(self.gl.GL_SCISSOR_TEST)
        with self.assertRaises(ValueError):owner.query(source,textures,100.)
        source.geometry.apply.assert_not_called();self.assertFalse(owner.quarantined)

    def test_failed_um_bind_cannot_draw_under_another_valid_program(self):
        owner=self.owner();textures=self.query_state();external=_Program();external.bind()
        shader=owner.programs['query',False]
        with patch.object(shader,'bind',return_value=None):
            with self.assertRaises(GeometryUncertain):owner.query(NS(geometry=Mock()),textures,100.)
        self.assertTrue(owner.quarantined);self.assertFalse(owner._busy)
        self.assertEqual(int(self.gl.glGetIntegerv(0x8B8D)),external._shader_program.programId())
        external.release()

    def test_lookup_read_is_pinned_and_reentrant_retirement_keeps_owner(self):
        owner=self.owner()
        with self.assertRaises(GeometryUncertain):
            with owner.read() as programs:
                self.assertEqual(programs,(owner.programs['lookup',False],owner.programs['lookup',True]))
                self.assertTrue(owner._busy);owner.close()
        self.assertTrue(owner.quarantined);self.assertFalse(owner._busy)
        self.assertEqual(owner.retained_bytes,module.PROGRAM_BYTES)

    def test_uniform_failure_still_releases_shader_and_pinned_map(self):
        owner=self.owner();values,plan=self.values_plan();mapping=Mock()
        mapping.apply.side_effect=RuntimeError('partial texture delivery')
        shader=owner.programs['record',True]
        with self.batch_scope(lambda batch:None),patch.object(module,'thaw_uniform',side_effect=lambda value:value):
            with self.assertRaises(GeometryUncertain):owner.collect('record',NS(),plan,values,mapping)
        shader.release.assert_called();mapping.release_bindings.assert_called_once()

    def test_collection_delivers_stable_mesh_order_and_original_program(self):
        owner=self.owner();values,plan=self.values_plan();mapping=Mock();delivered=[]
        capture=NS(deliver=lambda shader,index:delivered.append((shader,index)))
        with self.batch_scope(lambda batch:self.assertTrue(owner._busy)),patch.object(module,'thaw_uniform',side_effect=lambda value:value):
            owner.collect('identity',capture,plan,values,mapping)
        self.assertEqual(delivered,[(owner.programs['identity',True]._shader_program,0),
            (owner.programs['identity',False]._shader_program,1)])
        self.assertEqual(mapping.release_bindings.call_count,2)
        with self.assertRaises(ValueError):owner.collect('lookup',capture,plan,values,mapping)
        with self.assertRaises(ValueError):owner.collect('record',capture,plan,ReceiverValues(values.camera,()),mapping)

    def test_seed_only_replays_opaque_after_exact_depth_seed(self):
        owner=self.owner();values,plan=self.values_plan();depth=Mock();depth.seed.return_value=True
        drawn=[];mapping=Mock()
        with self.batch_scope(lambda batch:drawn.append(bool(self.gl.glGetBooleanv(0x0B72)))),patch.object(module,'thaw_uniform',side_effect=lambda value:value):
            self.assertTrue(owner.seed(NS(),depth,plan,values,mapping))
        self.assertEqual(drawn,[True]);depth.seed.assert_called_once()
        depth.seed.return_value=False
        self.assertFalse(owner.seed(NS(),depth,plan,values,mapping));self.assertEqual(drawn,[True])
        self.assertEqual(owner.programs['seed',False].setUniformValue.call_args.args,('u_depthOnly',0))

    def test_foreign_context_is_not_a_drain_and_never_releases(self):
        owner=self.owner();shader=owner.programs['seed',False];shader.release.reset_mock()
        self.context.doneCurrent()
        with self.assertRaises(GeometryUncertain):owner.close()
        shader.release.assert_not_called();self.assertEqual(owner.retained_bytes,module.PROGRAM_BYTES)

    def test_idle_close_restores_external_program_and_is_idempotent(self):
        owner=self.owner();external=_Program();external.bind()
        owner.close();self.assertTrue(owner.closed);self.assertEqual(owner.retained_bytes,0)
        self.assertEqual(int(self.gl.glGetIntegerv(0x8B8D)),external._shader_program.programId())
        owner.close();external.release()

    def test_context_retirement_during_lookup_is_rooted(self):
        owner=self.owner()
        with self.assertRaises(GeometryUncertain):
            with owner.read():self.context.aboutToBeDestroyed.emit()
        self.assertTrue(owner.quarantined);self.assertFalse(owner.closed)

    def test_nested_program_work_is_refused_before_second_query(self):
        owner=self.owner();textures=self.query_state()
        def nested(shader):
            with owner.read():self.fail('Nested lookup admitted')
        with self.assertRaises(GeometryUncertain):owner.query(NS(geometry=NS(apply=nested)),textures,100.)
        self.assertTrue(owner.quarantined);self.assertFalse(owner._busy)

    def test_host_restoration_failure_cannot_publish_a_clean_program_receipt(self):
        owner=self.owner()
        @contextmanager
        def bad_guard(*args,**kwargs):
            yield
            raise RuntimeError('host restore failed')
        with patch.object(module,'preserved_state',bad_guard):
            with self.assertRaises(GeometryUncertain):
                with owner.read():pass
        self.assertTrue(owner.quarantined);self.assertEqual(owner.retained_bytes,module.PROGRAM_BYTES)

    def test_foreign_render_singleton_is_refused_before_native_batch(self):
        owner=self.owner();values,plan=self.values_plan();mapping=Mock()
        with self.batch_scope(lambda batch:self.fail('Foreign render entered')):
            with patch.dict(sys.modules,{'UM.View.GL.OpenGL':NS(OpenGL=NS(getInstance=lambda:NS(_context=object())))}):
                with self.assertRaises(GeometryUncertain):owner.collect('record',NS(),plan,values,mapping)
        mapping.apply.assert_not_called();mapping.release_bindings.assert_not_called()

    def test_callback_context_loss_never_releases_into_foreign_context(self):
        owner=self.owner();values,plan=self.values_plan();mapping=Mock()
        mapping.apply.side_effect=lambda shader:self.context.doneCurrent()
        shader=owner.programs['seed',False];shader.release.reset_mock()
        with self.batch_scope(lambda batch:self.fail('Context loss reached batch')):
            with self.assertRaises(GeometryUncertain):owner.seed(NS(),NS(seed=lambda capture:True),plan,values,mapping)
        mapping.release_bindings.assert_not_called();shader.release.assert_not_called()
        self.assertTrue(owner.quarantined)


class ProgramFactoryTests(unittest.TestCase):
    def test_each_stage_transforms_the_original_shader_and_pins_before_load(self):
        events=[];linked=[True]
        class Shader:
            def __init__(self):self._shader_program=NS(isLinked=lambda:linked[0])
            def setVertexShader(self,value):self.vertex=value
            def setFragmentShader(self,value):self.fragment=value
            def load(self,path,version):
                events.append(('load',self,path,version))
                self.setVertexShader('native vertex')
                self.setFragmentShader('if (v_color.a <= 0.0) discard;\nnative fragment')
        with (patch.dict(sys.modules,{'UM.View.GL.ShaderProgram':NS(ShaderProgram=Shader)}),
              patch.object(module,'layer_vertex',side_effect=lambda source:'vertex:'+source) as vertex,
              patch.object(module,'layer_selection_fragment',side_effect=lambda stage,**kwargs:'select:'+stage) as select,
              patch.object(module,'layer_receiver_fragment',return_value='record') as record,
              patch.object(module,'layer_lookup_fragment',return_value='lookup') as lookup,
              patch.object(module,'recovery_query_fragment',return_value='bounded query') as query):
            for stage in ('depth','identity','record','lookup','seed','query'):
                for solid in (False,True):
                    events.clear()
                    program=module.create_program(stage,solid,lambda value:events.append(('pin',value)))
                    self.assertEqual(events[0],('pin',program));self.assertEqual(events[1][0],'load')
                    self.assertEqual(events[1][3],'41core')
                    if stage=='query':
                        self.assertIn('gl_VertexID',program.vertex);self.assertEqual(program.fragment,'bounded query')
                    else:self.assertEqual(program.vertex,'vertex:native vertex')
                    if stage in ('depth','identity'):select.assert_called_with(stage,single_pass=solid,samples=4)
                    elif stage=='record':record.assert_called_with('if (v_color.a <= 0.0) discard;\nnative fragment',single_pass=solid,samples=4)
                    elif stage=='lookup':
                        source='\nnative fragment' if solid else 'if (v_color.a <= 0.0) discard;\nnative fragment'
                        lookup.assert_called_with(source,samples=4)
                    elif stage=='seed':self.assertEqual(program.fragment,'if (v_color.a <= 0.0) discard;\nnative fragment')
            linked[0]=False;events.clear()
            with self.assertRaisesRegex(RuntimeError,'did not link'):
                module.create_program('seed',False,lambda value:events.append(('pin',value)))
            self.assertEqual(events[0][0],'pin')
            for args in (('unknown',True),('record',1)):
                with self.assertRaises(ValueError):module.create_program(*args,lambda value:None)
            self.assertTrue(vertex.called);self.assertTrue(query.called)


if __name__=='__main__':unittest.main()
