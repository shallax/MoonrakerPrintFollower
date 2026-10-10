"""Explicit MS attachments, context lifetime and refusal to synthesize samples."""
import ctypes
from contextlib import nullcontext
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import numpy as np

from mpf.toolhead import ToolheadSampleTarget as module


class _Format:
    """CPU value stand-in: these tests never create a Qt graphics context."""
    def __init__(self, other=None):
        self._samples = other.samples() if other else 0
        self._attachment = other.attachment() if other else 0
        self._internal = other.internalTextureFormat() if other else 0

    def setSamples(self, value): self._samples = value
    def samples(self): return self._samples
    def setAttachment(self, value): self._attachment = value
    def attachment(self): return self._attachment
    def setInternalTextureFormat(self, value): self._internal = value
    def internalTextureFormat(self): return self._internal


class SampleTargetTests(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.context.format.return_value = NS(majorVersion=lambda: 4)
        self.context.shareGroup.return_value = self.context
        self.gl = Mock()
        self.gl.glGetIntegerv.return_value = 4
        self.gl.glGetError.return_value = 0
        self.fbo = Mock()
        self.fbo.isValid.return_value = self.fbo.bind.return_value = True
        self.fbo.width.return_value = 19; self.fbo.height.return_value = 13
        self.fbo.handle.return_value = 29
        self.positions = ((.375,.125),(.875,.375),(.125,.625),(.625,.875))
        self.kind = 0
        self.bad_parameter = None
        self.no_names = False
        self.status = 0x8CD5
        self.calls = []
        self.factory = Mock(return_value=self.fbo)
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject
        self.factory.Attachment = QOpenGLFramebufferObject.Attachment
        def procedure(context, name, *types):
            self.calls.append(name)
            if name == 'glGenTextures':
                def generate(count, names): names[0], names[1] = (0,0) if self.no_names else (17,18)
                return generate
            if name == 'glTexImage2DMultisample':
                return lambda target, samples, kind, width, height, fixed: setattr(self, 'kind', kind)
            if name == 'glGetTexLevelParameteriv':
                def query(target, level, parameter, pointer):
                    value = {0x9106:4,0x1003:0x822E if target == 0x0DE1 else self.kind,0x1000:19,0x1001:13}[parameter]
                    ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int))[0] = 0 if self.bad_parameter == parameter else value
                return query
            if name == 'glCheckFramebufferStatus': return lambda target: self.status
            if name == 'glGetMultisamplefv':
                def get(kind, sample, value): value[:] = self.positions[sample]
                return get
            if name == 'glBindSampler': return Mock()
            if name == 'glReadPixels':
                def read(x,y,width,height,kind,scalar,pointer):
                    self.assertEqual((x,y,width,height,kind,scalar),(0,0,19,13,0x1903,0x1406))
                    view = np.ctypeslib.as_array(ctypes.cast(pointer,ctypes.POINTER(ctypes.c_float)),shape=(13,19))
                    view[:] = self.read_planes[self.sample]
                return read
            self.assertEqual(name, 'glFramebufferTexture2D')
            return Mock()
        for scope in (patch('PyQt6.QtGui.QOpenGLContext.currentContext', return_value=self.context),
                      patch('PyQt6.QtOpenGL.QOpenGLFramebufferObject', self.factory),
                      patch('PyQt6.QtOpenGL.QOpenGLFramebufferObjectFormat', _Format),
                      patch.object(module, 'preserved_state', side_effect=lambda *args: nullcontext()),
                      patch.object(module, 'preserved_samples', side_effect=lambda *args: nullcontext()),
                      patch.object(module, 'retire_textures'),
                      patch.object(module, 'procedure', side_effect=procedure)):
            value = scope.start(); self.addCleanup(scope.stop)
            if scope.attribute == 'retire_textures': self.retire = value

    def target(self): return module.ToolheadSampleTarget(self.gl,19,13)

    def test_only_clean_allocation_refusal_can_trigger_ordinary_fallback(self):
        from contextlib import contextmanager
        self.bad_parameter=0x1003
        with self.assertRaises(module.SampleTargetUnavailable):self.target()
        self.retire.assert_called()
        @contextmanager
        def broken_restore(*args):
            try:yield
            finally:raise RuntimeError('host restoration failed')
        for guard in ('preserved_state','preserved_samples'):
            with patch.object(module,guard,broken_restore):
                with self.assertRaisesRegex(RuntimeError,'host restoration failed') as caught:self.target()
                self.assertNotIsInstance(caught.exception,module.SampleTargetUnavailable)

    def extraction(self):
        target = self.target()
        self.pack = {0x0D00:1,0x0D02:29,0x0D03:2,0x0D04:3,0x0D05:8}
        self.gl.glGetIntegerv.side_effect = lambda key: {
            **self.pack,0x0B40:(0x1B02,0x1B02),0x88ED:83,0x8B8D:71}[key]
        self.gl.glIsEnabled.return_value = False
        self.sample = 0
        self.read_planes = np.array([np.full((13,19),sample/5,np.float32) +
                                     np.arange(13,dtype=np.float32)[:,None]/100
                                     for sample in range(4)])
        self.shader = Mock()
        self.shader.setVertexShader.return_value = self.shader.setFragmentShader.return_value = True
        self.shader.setUniformValue.side_effect = lambda name,value: setattr(self,'sample',value) if name == 'u_sample' else None
        self.vao = Mock(); self.vao.create.return_value = True
        for scope in (patch.dict('sys.modules',{'UM.View.GL.ShaderProgram':NS(ShaderProgram=lambda:self.shader)}),
                      patch('PyQt6.QtOpenGL.QOpenGLVertexArrayObject',return_value=self.vao)):
            scope.start(); self.addCleanup(scope.stop)
        return target

    def assert_pack_restored(self):
        self.gl.glBindBuffer.assert_any_call(0x88EB,83)
        for key,value in self.pack.items(): self.gl.glPixelStorei.assert_any_call(key,value)

    def test_original_four_depth_planes_row_order_readonly_and_cached_staging(self):
        target = self.extraction()
        for _ in range(2):
            result = target.read_depth(self.gl)
            np.testing.assert_array_equal(result,self.read_planes[:,::-1])
            self.assertFalse(result.flags.writeable)
        self.assertEqual(target.allocation_bytes,19*13*40)
        self.assertEqual(self.factory.call_count,2)
        self.assertEqual([c.args[1] for c in self.shader.setUniformValue.call_args_list if c.args[0]=='u_sample'],[0,1,2,3]*2)
        self.assertEqual(self.gl.glDrawArrays.call_count,8)
        self.assert_pack_restored()
        target.close(); self.assertIsNone(target._read_fbo)

    def test_warmed_staging_rejects_suppressed_raster_and_foreign_or_retired_context(self):
        target = self.extraction(); target.read_depth(self.gl)
        original = self.gl.glGetIntegerv.side_effect
        self.gl.glGetIntegerv.side_effect = lambda key:(0x1B01,0x1B01) if key==0x0B40 else original(key)
        with self.assertRaisesRegex(RuntimeError,'raster coverage'): target.read_depth(self.gl)
        self.gl.glGetIntegerv.side_effect = original
        for flag in (0x8C89,0x0B90,0x0BF2,*range(0x3000,0x3008)):
            self.gl.glIsEnabled.side_effect = lambda key,flag=flag:key==flag
            with self.assertRaisesRegex(RuntimeError,'raster coverage'): target.read_depth(self.gl)
        self.assertEqual(self.gl.glDrawArrays.call_count,4)
        self.gl.glIsEnabled.side_effect = None
        with patch('PyQt6.QtGui.QOpenGLContext.currentContext',return_value=Mock()):
            with self.assertRaisesRegex(RuntimeError,'live original context'): target.read_depth(self.gl)
        target.close()
        with self.assertRaisesRegex(RuntimeError,'live original context'): target.read_depth(self.gl)

    def test_staging_budget_allocation_and_storage_refusal_before_publication(self):
        target = self.extraction()
        self.fbo.width.return_value = self.fbo.height.return_value = 8192
        with self.assertRaisesRegex(RuntimeError,'memory budget'): target.read_depth(self.gl)
        self.fbo.width.return_value = 19; self.fbo.height.return_value = 13
        self.fbo.isValid.return_value = False
        with self.assertRaisesRegex(RuntimeError,'staging unavailable'): target.read_depth(self.gl)
        self.fbo.isValid.return_value = True
        for parameter in (0x1003,0x1000,0x1001):
            self.bad_parameter = parameter
            with self.assertRaisesRegex(RuntimeError,'storage differs'): target.read_depth(self.gl)
            self.assertIsNone(target._read_fbo)
            self.assertEqual(target.allocation_bytes,19*13*36)
        target.close()

    def test_shader_and_vao_failures_do_not_read_or_recharge_staging(self):
        target = self.extraction()
        for stage in ('setVertexShader','setFragmentShader'):
            getattr(self.shader,stage).return_value = False
            with self.assertRaisesRegex(RuntimeError,'shader unavailable'): target.read_depth(self.gl)
            getattr(self.shader,stage).return_value = True
        self.vao.create.return_value = False
        with self.assertRaisesRegex(RuntimeError,'vertex array unavailable'): target.read_depth(self.gl)
        self.assertIsNone(target._read_vao)
        self.assertEqual(target.allocation_bytes,19*13*40)
        self.vao.create.return_value = True
        self.fbo.bind.return_value = False
        with self.assertRaisesRegex(RuntimeError,'staging bind failed'): target.read_depth(self.gl)
        self.fbo.bind.return_value = True
        original = self.gl.glGetIntegerv.side_effect
        self.gl.glGetIntegerv.side_effect = lambda key:0 if key==0x8B8D else original(key)
        with self.assertRaisesRegex(RuntimeError,'shader did not bind'): target.read_depth(self.gl)
        self.gl.glDrawArrays.assert_not_called()
        self.assert_pack_restored(); target.close()

    def test_failed_or_invalid_readback_never_returns_partial_sample_seed(self):
        target = self.extraction()
        self.gl.glGetError.return_value = 0x502
        with self.assertRaisesRegex(RuntimeError,'extraction failed'): target.read_depth(self.gl)
        self.gl.glGetError.return_value = 0
        for value in (np.nan,-.1,1.1):
            self.read_planes[3,12,18] = value
            with self.assertRaisesRegex(RuntimeError,'invalid values'): target.read_depth(self.gl)
        self.assert_pack_restored()
        self.assertEqual(self.vao.release.call_count,4)
        self.assertEqual(self.shader.release.call_count,4)
        target.close()

    def test_cleanup_fault_still_restores_independent_pack_vao_and_shader_state(self):
        target = self.extraction()
        def store(key,value):
            if (key,value)==(0x0D00,1): raise RuntimeError('pack restore fault')
        self.gl.glPixelStorei.side_effect = store
        with self.assertRaisesRegex(RuntimeError,'restoration failed') as error: target.read_depth(self.gl)
        self.assertEqual(str(error.exception.__cause__),'pack restore fault')
        self.assert_pack_restored()
        self.vao.release.assert_called_once(); self.shader.release.assert_called_once()
        target.close()

    def test_explicit_storage_budget_pattern_and_retirement(self):
        target = self.target()
        self.assertTrue(target.isValid()); self.assertTrue(target.bind())
        self.assertEqual((target.width(),target.height(),target.handle()), (19,13,29))
        self.assertEqual((target.texture(),target.depth_texture()), (17,18))
        self.assertEqual(target.positions, self.positions)
        self.assertEqual(target.allocation_bytes,19*13*36)
        self.assertEqual(target.format().samples(),4)
        self.assertEqual(target.attachment(),self.factory.Attachment.Depth)
        target.size(); self.fbo.size.assert_called_once()
        callback = self.context.aboutToBeDestroyed.connect.call_args.args[0]
        callback()
        self.assertFalse(target.isValid()); self.assertFalse(target.bind())
        self.assertEqual((target.texture(),target.depth_texture()), (0,0))
        self.retire.assert_called_once_with(self.context,(17,18))
        target.close(); self.assertEqual(self.retire.call_count,1)

    def test_foreign_context_never_binds_or_blits_owned_names(self):
        target = self.target()
        with patch('PyQt6.QtGui.QOpenGLContext.currentContext', return_value=Mock()):
            self.assertFalse(target.bind())
            with self.assertRaisesRegex(RuntimeError,'original current context'):
                module.sample_blit(self.gl,target,target)
        target.close()

    def test_bad_dimensions_or_context_fail_before_allocations(self):
        with patch('PyQt6.QtGui.QOpenGLContext.currentContext', return_value=None):
            with self.assertRaisesRegex(RuntimeError,'core4'): self.target()
        self.context.format.return_value = NS(majorVersion=lambda: 3)
        with self.assertRaisesRegex(RuntimeError,'core4'): self.target()
        self.context.format.return_value = NS(majorVersion=lambda: 4)
        for width,height in ((0,13),(8193,1),(8192,8192)):
            with self.assertRaisesRegex(ValueError,'budget'): module.ToolheadSampleTarget(self.gl,width,height)
        self.factory.assert_not_called()

    def test_incomplete_allocations_storage_and_pattern_retire_textures(self):
        self.fbo.isValid.return_value = False
        with self.assertRaisesRegex(RuntimeError,'framebuffer allocation'): self.target()
        self.fbo.isValid.return_value = True
        self.no_names = True
        with self.assertRaisesRegex(RuntimeError,'texture allocation'): self.target()
        self.no_names = False
        for parameter in (0x9106,0x1003,0x1000,0x1001):
            self.bad_parameter = parameter
            with self.assertRaisesRegex(RuntimeError,'storage differs'): self.target()
        self.bad_parameter = None; self.status = 0
        with self.assertRaisesRegex(RuntimeError,'incomplete'): self.target()
        self.status = 0x8CD5; self.positions = ((.5,.5),)*4
        with self.assertRaisesRegex(module.SampleTargetUnavailable,'ordered GL pattern'): self.target()
        self.positions = ((.375,.125),(.875,.375),(.125,.625),(.625,.875))
        self.gl.glGetError.return_value = 0x0502
        with self.assertRaisesRegex(RuntimeError,'preparation failed'): self.target()
        self.retire.assert_any_call(self.context,(17,18))

    def test_failed_guard_exit_retires_prepared_objects(self):
        class Fault:
            def __enter__(self): pass
            def __exit__(self,*args): raise RuntimeError('guard cleanup')
        with patch.object(module,'preserved_samples',return_value=Fault()):
            with self.assertRaisesRegex(RuntimeError,'guard cleanup'): self.target()
        self.retire.assert_called_once_with(self.context,(17,18))

    def test_dying_driver_keeps_texture_names_leased_and_destructor_is_safe(self):
        target = self.target()
        callback = self.context.aboutToBeDestroyed.connect.call_args.args[0]
        self.retire.side_effect = RuntimeError('driver fault')
        callback(); self.assertFalse(target.isValid())
        self.assertEqual(target._names,(17,18))
        target.__del__()
        self.retire.side_effect = None
        target.close(); self.assertFalse(target._names)
        self.context.aboutToBeDestroyed.disconnect.side_effect = RuntimeError('dying wrapper')
        other = self.target(); other.close()

    def test_copy_requires_complete_matching_depth_and_only_resolves_colour(self):
        self.gl.GL_COLOR_BUFFER_BIT = 0x4000; self.gl.GL_DEPTH_BUFFER_BIT = 0x100
        a,b = self.target(),self.target()
        module.sample_blit(self.gl,b,a,buffers=0x4100)
        self.factory.blitFramebuffer.assert_called_with(self.fbo,self.fbo,buffers=0x4100,filter=0x2600)
        self.gl.glDisable.assert_any_call(0x0C11)
        b.positions = tuple(reversed(b.positions))
        with self.assertRaisesRegex(ValueError,'identical Depth32F'): module.sample_blit(self.gl,b,a,buffers=0x100)
        resolved = NS(isValid=lambda:True,width=lambda:19,height=lambda:13,format=lambda:NS(samples=lambda:0))
        module.sample_blit(self.gl,resolved,a)
        with self.assertRaisesRegex(ValueError,'identical Depth32F'): module.sample_blit(self.gl,resolved,a,buffers=0x100)
        with self.assertRaisesRegex(ValueError,'synthesize'): module.sample_blit(self.gl,a,resolved)
        self.gl.glGetError.return_value = 0x0502
        with self.assertRaisesRegex(RuntimeError,'copy failed'): module.sample_blit(self.gl,resolved,a)
        resolved.width = lambda:18
        with self.assertRaisesRegex(ValueError,'same-size'): module.sample_blit(self.gl,resolved,a)
        a.close(); b.close()


    def test_blit_failure_exits_its_independent_host_state_guard(self):
        self.gl.GL_COLOR_BUFFER_BIT = 0x4000; self.gl.GL_DEPTH_BUFFER_BIT = 0x100
        a,b = self.target(),self.target()
        events = []
        class Guard:
            def __enter__(self): events.append('enter')
            def __exit__(self,kind,error,tb): events.append(str(error))
        self.factory.blitFramebuffer.side_effect = RuntimeError('copy driver failed')
        with patch.object(module,'preserved_state',return_value=Guard()):
            with self.assertRaisesRegex(RuntimeError,'copy driver failed'): module.sample_blit(self.gl,b,a,buffers=0x4100)
        self.assertEqual(events,['enter','copy driver failed'])
        a.close(); b.close()
