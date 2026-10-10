"""Exact sample-depth snapshots remain independent of newer scene draws."""
import ctypes
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentDepth as module
from mpf.toolhead.ToolheadGLState import procedure, preserved_state, preserved_samples
from mpf.toolhead.ToolheadSampleTarget import ToolheadSampleTarget
from tests import test_toolhead_environment_gl as fixture


class ReceiverDepthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EnvironmentGLTests.setUpClass()
        cls.available = fixture.EnvironmentGLTests.available
        cls.context = fixture.EnvironmentGLTests.context
        cls.gl = getattr(fixture.EnvironmentGLTests,'gl',None)
        cls.surface = getattr(fixture.EnvironmentGLTests,'surface',None)

    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass()

    def setUp(self):
        if not self.available: self.skipTest('Core GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface))
        self.targets = []; self.owners = []

    def tearDown(self):
        self.context.makeCurrent(self.surface); self.gl.glFinish()
        for owner in self.owners:
            # Tests alone perform an explicit original-context finish before
            # reclaiming deliberately quarantined fault fixtures.
            owner.quarantined=False; owner.close()
        for target in self.targets: target.close()
        self.assertEqual(self.gl.glGetError(),0)

    def target(self, width=8):
        target = ToolheadSampleTarget(self.gl,width,8)
        self.targets.append(target); return target

    def fill(self,target,values):
        with preserved_state(self.gl,self.context),preserved_samples(self.gl,self.context):
            self.assertTrue(target.bind()); self.gl.glDisable(0x0C11)
            self.gl.glDepthMask(True); self.gl.glEnable(0x8E51)
            mask = procedure(self.context,'glSampleMaski',None,ctypes.c_uint,ctypes.c_uint)
            # Clear ignores the sample mask, so draw an exact fullscreen depth
            # writer per plane. This creates a real nonuniform MS depth oracle.
            from PyQt6.QtOpenGL import QOpenGLShaderProgram,QOpenGLShader,QOpenGLVertexArrayObject
            program = QOpenGLShaderProgram()
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
                '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'))
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
                '#version 410\nuniform float depth;out vec4 colour;void main(){gl_FragDepth=depth;colour=vec4(1.);}'))
            self.assertTrue(program.link(),program.log())
            vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create())
            try:
                self.assertTrue(program.bind()); vao.bind()
                self.gl.glViewport(0,0,target.width(),target.height())
                self.gl.glDisable(self.gl.GL_CULL_FACE); self.gl.glDisable(self.gl.GL_BLEND)
                self.gl.glEnable(self.gl.GL_DEPTH_TEST); self.gl.glDepthFunc(self.gl.GL_ALWAYS)
                for sample,value in enumerate(values):
                    mask(0,1<<sample); program.setUniformValue('depth',value)
                    self.gl.glDrawArrays(self.gl.GL_TRIANGLES,0,3)
            finally: vao.release(); program.release(); vao.destroy()

    def create(self,source,**options):
        args = dict(key=('scene',3),existing_bytes=27,byte_budget=32<<20); args.update(options)
        owner = module.ReceiverDepth(self.gl,self.context,source,**args)
        self.owners.append(owner); return owner

    def read(self,target):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject,QOpenGLFramebufferObjectFormat
        from PyQt6.QtOpenGL import QOpenGLShaderProgram,QOpenGLShader,QOpenGLVertexArrayObject
        with preserved_state(self.gl,self.context),preserved_samples(self.gl,self.context):
            fmt=QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x822E)
            staging=QOpenGLFramebufferObject(8,8,fmt); self.assertTrue(staging.bind())
            program=QOpenGLShaderProgram()
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
                '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'))
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
                '#version 410\nuniform sampler2DMS depth;uniform int plane;out float value;void main(){value=texelFetch(depth,ivec2(gl_FragCoord.xy),plane).r;}'))
            self.assertTrue(program.link(),program.log())
            vao=QOpenGLVertexArrayObject(); self.assertTrue(vao.create())
            result=np.empty((4,8,8),np.float32)
            try:
                self.assertTrue(program.bind()); vao.bind()
                self.gl.glViewport(0,0,8,8)
                for flag in (self.gl.GL_DEPTH_TEST,self.gl.GL_CULL_FACE,self.gl.GL_BLEND,0x8E51): self.gl.glDisable(flag)
                self.gl.glColorMask(True,True,True,True)
                self.gl.glActiveTexture(0x84C0); self.gl.glBindTexture(0x9100,target.depth_texture())
                procedure(self.context,'glBindSampler',None,ctypes.c_uint,ctypes.c_uint)(0,0)
                program.setUniformValue('depth',0)
                for plane in range(4):
                    program.setUniformValue('plane',plane); self.gl.glDrawArrays(self.gl.GL_TRIANGLES,0,3)
                    procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,
                        ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)(0,0,8,8,0x1903,0x1406,result[plane].ctypes.data)
            finally: vao.release(); program.release(); vao.destroy()
            return result

    def capture(self,target):
        return NS(context=self.context,samples=4,width=target.width(),height=target.height(),
            positions=target.positions,targets={'records':target})

    def test_exact_four_planes_survive_source_mutation_and_host_scissor(self):
        source=self.target(); destination=self.target()
        values=(.125,.375,.625,.875); self.fill(source,values)
        owner=self.create(source)
        self.assertEqual(owner.retained_bytes,8*8*36+module.METADATA_BYTES)
        self.assertIsNone(owner._target._retirement)
        self.fill(source,(.9,.9,.9,.9))
        self.assertTrue(destination.bind()); self.gl.glEnable(0x0C11); self.gl.glScissor(0,0,1,1)
        self.assertTrue(owner.seed(self.capture(destination)))
        self.assertTrue(self.gl.glIsEnabled(0x0C11))
        self.gl.glDisable(0x0C11)
        planes=self.read(destination)
        np.testing.assert_array_equal(planes,np.broadcast_to(np.array(values,np.float32)[:,None,None],(4,8,8)))
        owner.close(); self.assertTrue(owner.closed); self.assertEqual(owner.retained_bytes,0)
        owner.close()

    def test_admission_refuses_before_any_new_allocation(self):
        source=self.target()
        with patch.object(ToolheadSampleTarget,'__init__',side_effect=AssertionError('allocated')):
            with self.assertRaises(MemoryError): self.create(source,byte_budget=64)
        for options in ({'key':['mutable']},{'existing_bytes':True},{'byte_budget':0}):
            with self.assertRaises(ValueError): self.create(source,**options)
        with self.assertRaises(ValueError): self.create(NS())

    def test_seed_refuses_wrong_size_pattern_samples_context_and_binding(self):
        source=self.target(); target=self.target(); owner=self.create(source)
        capture=self.capture(target)
        for name,value in (('samples',1),('width',9),('positions',tuple(reversed(target.positions))),('context',None)):
            old=getattr(capture,name); setattr(capture,name,value)
            with self.assertRaises(ValueError): owner.seed(capture)
            setattr(capture,name,old)
        self.assertTrue(source.bind())
        with self.assertRaisesRegex(RuntimeError,'bound records'): owner.seed(capture)
        self.assertFalse(owner.quarantined)

    def test_foreign_context_cannot_copy_or_claim_retirement(self):
        source=self.target(); owner=self.create(source); receipt=owner.retained_bytes; target=owner._target
        with patch('PyQt6.QtGui.QOpenGLContext.currentContext',return_value=None):
            with self.assertRaises(RuntimeError): self.create(source)
            with self.assertRaises(module.GeometryUncertain): owner.close()
        self.assertTrue(owner.quarantined); self.assertIs(owner._target,target)
        self.assertEqual(owner.retained_bytes,receipt); self.assertIn(owner,module._uncertain)

    def test_failed_copy_retains_allocated_target_and_receipt(self):
        source=self.target()
        with patch.object(module,'sample_blit',side_effect=RuntimeError('copy failed')):
            with self.assertRaises(module.GeometryUncertain) as caught: self.create(source)
        owner=caught.exception.owner; self.owners.append(owner)
        self.assertTrue(owner.quarantined); self.assertIsNotNone(owner._target)
        self.assertGreater(owner.retained_bytes,0)

    def test_seed_fault_and_reentrant_context_retirement_quarantine_without_dropping_target(self):
        source=self.target(); target=self.target(); owner=self.create(source)
        target.bind()
        with patch.object(module,'sample_depth_certificate',side_effect=RuntimeError('certificate failed')):
            with self.assertRaises(module.GeometryUncertain): owner.seed(self.capture(target))
        self.assertIsNotNone(owner._target)
        other=self.create(source)
        with self.assertRaises(RuntimeError):
            with other._operation():
                other._retirement()
                with self.assertRaises(RuntimeError): other._current()

    def test_restore_only_gl_error_cannot_publish_success(self):
        source=self.target(); target=self.target(); owner=self.create(source); target.bind()
        original=module.preserved_samples
        @contextmanager
        def poisoned_restore(*args,**kwargs):
            with original(*args,**kwargs): yield
            # Invalid target is an error-only failure after all work completed.
            procedure(self.context,'glBindFramebuffer',None,ctypes.c_uint,ctypes.c_uint)(0,0)
        with patch.object(module,'preserved_samples',poisoned_restore):
            with self.assertRaises(module.GeometryUncertain): owner.seed(self.capture(target))
        self.assertTrue(owner.quarantined); self.assertIsNotNone(owner._target)

    def test_failed_finish_keeps_names_and_full_charge(self):
        source=self.target(); owner=self.create(source); target=owner._target
        with patch.object(owner.gl,'glFinish',side_effect=RuntimeError('finish failed')):
            with self.assertRaises(module.GeometryUncertain): owner.close()
        self.assertIs(owner._target,target); self.assertTrue(target.isValid())
        self.assertGreater(owner.retained_bytes,0)

    def test_changed_actual_destination_sample_order_refuses_seed(self):
        source=self.target(); target=self.target(); owner=self.create(source); target.bind()
        with patch.object(owner,'_positions',return_value=tuple(reversed(owner.positions))):
            with self.assertRaises(module.GeometryUncertain): owner.seed(self.capture(target))
        self.assertTrue(owner.quarantined)
