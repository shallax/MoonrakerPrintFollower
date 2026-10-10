"""Real small GL targets: seeded admission, complete tiles and fenced retirement."""
import ctypes
from contextlib import contextmanager
from types import SimpleNamespace as NS
from unittest.mock import patch
import unittest

import numpy as np

from mpf.toolhead import ToolheadEnvironmentRecovery as recovery
from mpf.toolhead.ToolheadGLState import procedure
from tests import test_toolhead_environment_gl as fixture


class EnvironmentTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.EnvironmentGLTests.setUpClass()
        cls.available = fixture.EnvironmentGLTests.available
        cls.context = fixture.EnvironmentGLTests.context
        cls.gl = getattr(fixture.EnvironmentGLTests, 'gl', None)
        cls.surface = getattr(fixture.EnvironmentGLTests, 'surface', None)

    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass()

    def setUp(self):
        if not self.available: self.skipTest('Core GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface)); self.owners = []

    def tearDown(self):
        self.context.makeCurrent(self.surface); self.gl.glFinish()
        for owner in self.owners:
            if not owner.quarantined: owner.close()
        self.assertEqual(self.gl.glGetError(), 0)

    def create(self, **options):
        kwargs = dict(retained_bytes=0, byte_budget=128*1024); kwargs.update(options)
        owner = recovery.RecoveryTargets(self.gl, self.context, 17, 19, **kwargs)
        self.owners.append(owner); return owner

    def seed(self, *_args): return True

    def capture(self, owner, key='A', seed=None):
        return owner.capture(key, 'original-cohort', 'camera', (0, 0, 17, 19), (0, 0, 17, 19),
            'cropped-camera', seed=self.seed if seed is None else seed,
            draw=lambda camera: self.assertEqual(camera, 'cropped-camera'))

    @contextmanager
    def scope(self, key='original-cohort'):
        yield NS(key=key)

    def pixels(self, owner):
        result = np.empty((19, 17, 4), np.float32)
        owner.image.bind()
        procedure(self.context, 'glReadPixels', None, *([ctypes.c_int]*4), ctypes.c_uint,
            ctypes.c_uint, ctypes.c_void_p)(0, 0, 17, 19, 0x1908, 0x1406, result.ctypes.data)
        return result

    def test_complete_tiles_publish_only_after_final_gpu_completion_and_retire_on_last_read(self):
        owner = self.create(); self.assertEqual(owner.retained_bytes, 17*19*52)
        self.assertTrue(self.capture(owner)); self.assertEqual(len(owner.tiles), 4)
        with owner.read('A') as texture: self.assertIsNone(texture)
        submitted = []
        def draw(geometry, textures):
            self.assertEqual(geometry.key, 'original-cohort'); self.assertEqual(len(textures), 2)
            submitted.append(owner.tile_rect(owner.next_tile))
            self.gl.glClearColor(owner.next_tile/4, .7, .2, 1.); self.gl.glClear(self.gl.GL_COLOR_BUFFER_BIT)
        self.gl.glFinish()
        for _ in owner.tiles:
            self.assertFalse(owner.step('A', query_scope=self.scope, draw=draw))
            self.assertIsNone(owner.ready_key); self.gl.glFinish()
        self.assertEqual(submitted, [owner.tile_rect(index) for index in owner.tiles])
        self.assertIsInstance(owner.tiles, range)
        self.assertTrue(owner.step('A', query_scope=self.scope, draw=draw))
        self.assertEqual(len(submitted), 4)
        actual = self.pixels(owner)
        for index, (x, y, w, h) in enumerate(submitted):
            np.testing.assert_array_equal(actual[y:y+h, x:x+w], np.broadcast_to(
                np.array((index/4, .7, .2, 1.), np.float32), (h, w, 4)))
        with owner.read('B') as texture: self.assertIsNone(texture)
        with owner.read('A') as texture: self.assertEqual(texture, owner.image.texture())
        self.assertIsNotNone(owner._read); owner.close(); owner.close()
        self.assertTrue(owner.closed); self.assertIsNone(owner.records); self.assertIsNone(owner.image)

    def test_budget_and_invalid_dimensions_refuse_before_any_qt_allocation(self):
        with patch('PyQt6.QtOpenGL.QOpenGLFramebufferObject') as allocate:
            for kwargs in (dict(retained_bytes=128*1024), dict(byte_budget=17*19*52-1)):
                with self.assertRaises(MemoryError): self.create(**kwargs)
            for width, height in ((True, 19), (0, 19), (17, 8193)):
                with self.assertRaises(ValueError):
                    recovery.RecoveryTargets(self.gl, self.context, width, height,
                        retained_bytes=0, byte_budget=128*1024)
            allocate.assert_not_called()

    def test_failed_partial_depth_seed_withdraws_entire_generation_and_restores_host_state(self):
        owner = self.create(); self.assertTrue(self.capture(owner)); self.gl.glFinish()
        before = tuple(self.gl.glGetIntegerv(0x0BA2))
        for refusal in (False, None):
            def seed(gl, target, camera, viewport, crop, cropped, refusal=refusal):
                self.assertEqual((target, camera, viewport, crop, cropped),
                    (owner.records, 'camera', (0, 0, 17, 19), (0, 0, 17, 19), 'cropped-camera'))
                gl.glClearDepth(.2); gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                gl.glViewport(3, 4, 5, 6); gl.glColorMask(False, False, False, False)
                return refusal
            self.assertFalse(self.capture(owner, 'B', seed)); self.assertIsNone(owner.key)
            self.assertIsNone(owner.ready_key); self.assertEqual(tuple(self.gl.glGetIntegerv(0x0BA2)), before)
            with owner.read('A') as texture: self.assertIsNone(texture)
        self.assertTrue(self.capture(owner, 'C'))

    def test_selected_key_or_cohort_change_cannot_publish_partial_old_pixels(self):
        owner = self.create(); self.assertTrue(self.capture(owner)); self.gl.glFinish()
        self.assertFalse(owner.step('A', query_scope=self.scope, draw=lambda *args: None))
        self.assertFalse(owner.step('B', query_scope=self.scope, draw=lambda *args: self.fail('stale draw')))
        self.gl.glFinish()
        self.assertFalse(owner.step('A', query_scope=self.scope, draw=lambda *args: self.fail('ABA publication')))
        self.assertTrue(self.capture(owner)); self.gl.glFinish()
        self.assertFalse(owner.step('A', query_scope=lambda: self.scope('other-cohort'),
            draw=lambda *args: self.fail('wrong source draw')))
        self.assertIsNone(owner.ready_key); self.assertIsNone(owner.key)

    def test_wait_failed_retains_live_targets_and_sync_in_uncertain_owner(self):
        owner = self.create(); self.assertTrue(self.capture(owner)); fence = owner._write
        original = recovery.procedure
        def resolve(context, name, *types):
            return (lambda *args: 0x911D) if name == 'glClientWaitSync' else original(context, name, *types)
        with patch.object(recovery, 'procedure', side_effect=resolve):
            with self.assertRaises(recovery.GeometryUncertain) as caught:
                owner.step('A', query_scope=self.scope, draw=lambda *args: self.fail('unverified draw'))
        self.assertIs(caught.exception.owner, owner); self.assertTrue(owner.quarantined)
        self.assertIsNotNone(owner.records); self.assertIsNotNone(owner.image); self.assertEqual(owner._write, fence)
        self.assertTrue(procedure(self.context, 'glIsSync', ctypes.c_ubyte, ctypes.c_void_p)(ctypes.c_void_p(fence)))
        self.assertTrue(any(value is owner for value in recovery._uncertain_targets))

    def test_read_exit_with_no_current_context_cannot_fence_another_command_stream(self):
        owner = self.create(); self.assertTrue(self.capture(owner)); self.gl.glFinish()
        for _ in owner.tiles:
            owner.step('A', query_scope=self.scope, draw=lambda *args: None); self.gl.glFinish()
        self.assertTrue(owner.step('A', query_scope=self.scope, draw=lambda *args: None))
        original = recovery.procedure
        def resolve(context, name, *types):
            if name == 'glFenceSync': self.fail('No foreign-context completion fence')
            return original(context, name, *types)
        try:
            with patch.object(recovery, 'procedure', side_effect=resolve):
                with self.assertRaises(recovery.GeometryUncertain):
                    with owner.read('A') as texture:
                        self.assertIsNotNone(texture); self.context.doneCurrent()
        finally: self.assertTrue(self.context.makeCurrent(self.surface))
        self.assertTrue(owner.quarantined); self.assertIsNone(owner._read)
        self.assertIsNotNone(owner.records); self.assertIsNotNone(owner.image)

    def test_callback_retirement_cannot_drop_targets_during_capture_or_query(self):
        for operation in ('seed', 'draw', 'scope'):
            owner = self.create()
            if operation != 'seed':
                self.assertTrue(self.capture(owner)); self.gl.glFinish()
            def seed(*args, owner=owner):
                owner.close(); self.fail('Capture resumed after retirement refusal')
            def draw(*args, owner=owner):
                owner.close(); self.fail('Query resumed after retirement refusal')
            @contextmanager
            def scope(owner=owner):
                owner.close(); yield NS(key='original-cohort')
            with self.assertRaises(recovery.GeometryUncertain):
                if operation == 'seed': self.capture(owner, seed=seed)
                else:
                    owner.step('A', query_scope=scope if operation == 'scope' else self.scope,
                        draw=draw if operation == 'draw' else lambda *args: self.fail('Deleted-target draw'))
            self.assertTrue(owner.quarantined); self.assertFalse(owner.closed)
            self.assertEqual(owner._submissions, 0)
            self.assertIsNotNone(owner.records); self.assertIsNotNone(owner.image)

    def test_nested_submission_cannot_skip_a_tile_or_overwrite_a_completion_fence(self):
        owner = self.create(); self.assertTrue(self.capture(owner)); self.gl.glFinish()
        submitted = []
        def draw(*args):
            selected = owner.key
            submitted.append(owner.tile_rect(owner.next_tile))
            self.assertFalse(owner.step('B', query_scope=self.scope,
                draw=lambda *args: self.fail('Nested tile submitted')))
            self.assertFalse(self.capture(owner, 'B'))
            self.assertEqual(owner.key, selected); self.assertEqual(owner.next_tile, len(submitted)-1)
        for _ in owner.tiles:
            self.assertFalse(owner.step('A', query_scope=self.scope, draw=draw)); self.gl.glFinish()
        self.assertTrue(owner.step('A', query_scope=self.scope, draw=draw))
        self.assertEqual(submitted, [owner.tile_rect(index) for index in owner.tiles])

    def test_layered_csr_lookup_selects_original_ids_preserves_black_and_refuses_bad_addresses(self):
        from PyQt6.QtOpenGL import (QOpenGLShaderProgram, QOpenGLShader,
            QOpenGLVertexArrayObject, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat)
        gl = self.gl
        shader = QOpenGLShaderProgram()
        self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, '''#version 410
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}
'''), shader.log())
        fragment = '''#version 410
out vec4 colour;
vec3 environmentReflection(vec3 direction,float roughness,out float confidence){
 confidence=.125;return vec3(.2,.3,.4);
}
float grainHash(vec3 cell){return cell.x;}
void main(){float confidence;vec3 value=environmentReflection(vec3(0.,0.,1.),.4,confidence);
 colour=vec4(value,confidence);}
'''
        self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            recovery.layer_lookup_fragment(fragment)), shader.log())
        self.assertTrue(shader.link(), shader.log())
        for bad in ('#version 120', fragment.replace('float grainHash(', 'float changed(')):
            with self.assertRaises(ValueError): recovery.layer_lookup_fragment(bad)
        vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create())
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x8814)
        target = QOpenGLFramebufferObject(1, 1, fmt)
        self.assertTrue(target.isValid())
        buffers, textures = (ctypes.c_uint*3)(), (ctypes.c_uint*3)()
        procedure(self.context, 'glGenBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(3, buffers)
        procedure(self.context, 'glGenTextures', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(3, textures)
        upload = procedure(self.context, 'glBufferData', None, ctypes.c_uint, ctypes.c_ssize_t,
                           ctypes.c_void_p, ctypes.c_uint)
        texbuffer = procedure(self.context, 'glTexBuffer', None, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint)
        bind_sampler = procedure(self.context, 'glBindSampler', None, ctypes.c_uint, ctypes.c_uint)
        try:
            def store(index, values, dtype, internal_format):
                data = np.asarray(values, dtype)
                gl.glBindBuffer(0x8C2A, buffers[index]); upload(0x8C2A, data.nbytes, data.ctypes.data, 0x88E4)
                gl.glActiveTexture(0x84C0+index); bind_sampler(index, 0)
                gl.glBindTexture(0x8C2A, textures[index]); texbuffer(0x8C2A, internal_format, buffers[index])
            store(0, ((0, 3),), np.uint32, 0x823C)
            store(1, (2, 6, 9), np.uint32, 0x8236)
            store(2, ((0., 0., 0., 1.), (0., 0., 0., 0.), (.123, .456, .789, 1.)), np.float32, 0x8814)
            self.assertTrue(shader.bind()); vao.bind(); target.bind()
            for name, value in dict(mpf_layerRanges=0, mpf_layerIdentities=1, mpf_layerColours=2,
                    mpf_layerLookupEnabled=1, mpf_layerPixelCount=1, mpf_layerRecordCount=3,
                    mpf_layerSampleCount=1,mpf_layerLookupPlane=0,
                    mpf_layerDrawBase=2, mpf_layerDrawCount=1).items(): shader.setUniformValue(name, value)
            uniform2 = procedure(self.context, 'glUniform2i', None, ctypes.c_int, ctypes.c_int, ctypes.c_int)
            uniform2(shader.uniformLocation('mpf_layerLookupOrigin'), 0, 0)
            uniform2(shader.uniformLocation('mpf_layerLookupSize'), 1, 1)
            gl.glViewport(0, 0, 1, 1)
            for flag in (gl.GL_BLEND, gl.GL_DEPTH_TEST, gl.GL_CULL_FACE, gl.GL_SCISSOR_TEST): gl.glDisable(flag)
            gl.glColorMask(True, True, True, True)
            def draw(identity, enabled=1):
                shader.setUniformValue('mpf_layerDrawBase', identity)
                shader.setUniformValue('mpf_layerLookupEnabled', enabled)
                gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
                result = np.empty(4, np.float32)
                procedure(self.context, 'glReadPixels', None, *([ctypes.c_int]*4), ctypes.c_uint,
                    ctypes.c_uint, ctypes.c_void_p)(0, 0, 1, 1, 0x1908, 0x1406, result.ctypes.data)
                self.assertEqual(gl.glGetError(), 0); return result
            fallback = np.array((.2, .3, .4, .125), np.float32)
            np.testing.assert_array_equal(draw(2), (0., 0., 0., 1.))
            np.testing.assert_array_equal(draw(9), np.array((.123, .456, .789, 1.), np.float32))
            for identity, enabled in ((6, 1), (8, 1), (2, 0), (-1, 1), (16777216, 1)):
                np.testing.assert_array_equal(draw(identity, enabled), fallback)
            store(0, ((0xFFFFFFFF, 3),), np.uint32, 0x823C)
            np.testing.assert_array_equal(draw(9), fallback)
            store(0, ((0, 4),), np.uint32, 0x823C)
            np.testing.assert_array_equal(draw(9), fallback)
            store(0, ((0, 3),), np.uint32, 0x823C)
            store(1, (2,), np.uint32, 0x8236)
            np.testing.assert_array_equal(draw(9), fallback)
        finally:
            gl.glFinish(); shader.release(); vao.release(); vao.destroy()
            procedure(self.context, 'glDeleteTextures', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(3, textures)
            procedure(self.context, 'glDeleteBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(3, buffers)


if __name__ == '__main__': unittest.main()
