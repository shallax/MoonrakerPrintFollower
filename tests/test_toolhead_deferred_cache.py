"""Deferred receiver storage stays independent of light motion and bounded in memory."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache


class DeferredSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.context.format.return_value.majorVersion.return_value = 4
        self.fbo = Mock()
        self.fbo.isValid.return_value = self.fbo.bind.return_value = True
        self.factory = Mock(return_value=self.fbo)
        self.factory.Attachment = SimpleNamespace(CombinedDepthStencil=1, Depth=2)
        self.format = Mock()
        self.modules = patch.dict('sys.modules', {
            'PyQt6.QtGui': SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda: self.context)),
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLFramebufferObject=self.factory,
                QOpenGLFramebufferObjectFormat=lambda: self.format, QOpenGLTexture=Mock())})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.cache = ToolheadSurfaceCache()
        self.viewport = (8, 12, 800, 600)
        self.samples = 0
        self.gl = Mock(GL_BLEND=1, GL_COLOR_BUFFER_BIT=2, GL_DEPTH_BUFFER_BIT=4)
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else self.samples if name == 0x80A9 else 17
        self.gl.glCheckFramebufferStatus.return_value = 0x8CD5
        self.gl.glGetError.return_value = 0
        self.rebuild, self.append = Mock(), Mock()

    def prepare(self, completed=(10, 20), key='camera'):
        self.cache.prepare(self.gl, key, completed, self.rebuild, self.append)

    def test_region_reuses_motion_grows_on_escape_and_rebuilds_camera_changes(self):
        self.viewport = (8, 12, 2400, 1600)
        def capture(rect, key='camera', completed=(10, 20)):
            self.cache.prepare(self.gl, key, completed, self.rebuild, self.append, rect=rect)
        capture((800, 600, 200, 200))
        first = self.cache._region
        self.assertLess(self.cache._size[0]*self.cache._size[1], 2400*1600//2)
        self.gl.glViewport.assert_any_call(-first[0], -first[1], 2400, 1600)
        capture((820, 620, 200, 200))
        self.assertEqual(self.cache._region, first)
        self.rebuild.assert_called_once()
        capture((820, 620, 200, 200), completed=(12, 20))
        self.append.assert_called_once_with((10, 20), (12, 20))
        capture((1800, 1000, 200, 200))
        self.assertEqual(self.rebuild.call_count, 2)
        self.assertNotEqual(self.cache._region, first)
        capture((800, 600, 200, 200), key='moved camera')
        self.assertEqual(self.cache._region, first)
        self.assertEqual(self.rebuild.call_count, 3)
        provider = Mock()
        self.assertFalse(self.cache.try_seed_depth(provider))
        provider.assert_not_called()
        cropped_provider = Mock(return_value=True)
        self.assertTrue(self.cache.try_seed_depth(provider, region_copy=cropped_provider))
        cropped_provider.assert_called_once_with(self.fbo, first)
        provider.assert_not_called()
        rx, ry, _, _ = first
        self.cache.copy_depth(self.gl, rect=(800, 600, 200, 200))
        self.gl.glBlitFramebuffer.assert_called_with(800-rx,600-ry,1000-rx,800-ry,808,612,1008,812,4,0x2600)
        capture(None)
        self.assertEqual(self.cache._region, (0,0,2400,1600))

    def test_horizontal_escape_does_not_repad_previous_vertical_envelope(self):
        self.viewport = (0, 0, 2400, 1600)
        self.cache.prepare(self.gl, 'camera', (10,20), self.rebuild, self.append, rect=(800,600,200,200))
        first = self.cache._region
        for x in (1100,1400,1700,2000):
            self.cache.prepare(self.gl, 'camera', (10,20), self.rebuild, self.append, rect=(x,600,200,200))
            region = self.cache._region
            self.assertEqual((region[1],region[3]), (first[1],first[3]))
            self.assertLessEqual(region[0],x)
            self.assertGreaterEqual(region[0]+region[2],x+200)
        self.assertLess(self.cache._size[0]*self.cache._size[1],2400*1600//2)

    def test_depth_copy_rectangle_offsets_destination_only_and_empty_skips(self):
        self.prepare()
        self.cache.copy_depth(self.gl,rect=(10,20,30,40))
        self.gl.glBlitFramebuffer.assert_called_with(10,20,40,60,18,32,48,72,4,0x2600)
        self.gl.glBlitFramebuffer.reset_mock()
        self.cache.copy_depth(self.gl,rect=(0,0,0,0))
        self.gl.glBlitFramebuffer.assert_not_called()
        self.cache.copy_depth(self.gl,rect=(float('nan'),0,2,3))
        self.gl.glBlitFramebuffer.assert_called_with(0,0,800,600,8,12,808,612,4,0x2600)

    def test_light_frames_reuse_receivers_append_progress_and_rebuild_backwards(self):
        self.prepare()
        for _ in range(20): self.prepare()
        self.rebuild.assert_called_once()
        self.append.assert_not_called()
        self.prepare((12, 25))
        self.append.assert_called_once_with((10, 20), (12, 25))
        self.prepare((12, 24))
        self.assertEqual(self.rebuild.call_count, 2)
        self.prepare((12, 24), key='new camera')
        self.assertEqual(self.rebuild.call_count, 3)
        self.gl.glBindFramebuffer.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_four_k_single_sample_fits_but_multisample_is_rejected_before_allocation(self):
        self.viewport = (0, 0, 3840, 2160)
        self.prepare()
        self.assertEqual(self.cache._size, (3840, 2160, 0))
        self.samples = 4
        with self.assertRaisesRegex(RuntimeError, 'budget'): self.prepare()
        self.factory.assert_called_once()
        self.assertLess(3840 * 2160 * 40, self.cache.MAX_BYTES)
        self.assertGreater(3840 * 2160 * 4 * 40, self.cache.MAX_BYTES)

    def test_incomplete_capture_never_commits_and_restores_target(self):
        self.rebuild.side_effect = RuntimeError('receiver upload interrupted')
        with self.assertRaisesRegex(RuntimeError, 'interrupted'): self.prepare()
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._completed)
        self.gl.glBindFramebuffer.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.rebuild.side_effect = None
        self.prepare()
        self.assertEqual(self.rebuild.call_count, 2)

    def test_invalid_attachment_and_gl_error_cannot_commit_receivers(self):
        self.gl.glCheckFramebufferStatus.return_value = 0
        with self.assertRaisesRegex(RuntimeError, 'incomplete'): self.prepare()
        self.rebuild.assert_not_called()
        self.gl.glCheckFramebufferStatus.return_value = 0x8CD5
        self.gl.glGetError.return_value = 0x502
        with self.assertRaisesRegex(RuntimeError, '0502'): self.prepare()
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._completed)

    def test_resize_and_context_replacement_retire_old_receivers(self):
        self.prepare()
        self.viewport = (0, 0, 900, 700)
        self.prepare()
        self.context = Mock()
        self.context.format.return_value.majorVersion.return_value = 4
        self.prepare()
        self.assertEqual(self.factory.call_count, 3)
        self.assertEqual(self.rebuild.call_count, 3)

    def test_failed_depth_transfer_restores_target_and_remains_unvalidated(self):
        self.prepare()
        self.gl.glGetError.return_value = 0x502
        with self.assertRaisesRegex(RuntimeError, 'depth copy failed'): self.cache.copy_depth(self.gl)
        self.assertFalse(self.cache._copy_checked)
        self.gl.glBindFramebuffer.assert_called_with(0x8D40, 17)
        self.gl.glGetError.return_value = 0
        self.cache.copy_depth(self.gl)
        self.assertTrue(self.cache._copy_checked)
        self.gl.glBlitFramebuffer.assert_called_with(0, 0, 800, 600, 8, 12, 808, 612, 4, 0x2600)

    def test_completed_depth_seed_offers_only_owned_target_and_does_not_hide_partial_failure(self):
        provider = Mock(return_value=True)
        self.assertFalse(self.cache.try_seed_depth(provider))
        provider.assert_not_called()
        self.prepare()
        self.assertFalse(self.cache.try_seed_depth(None))
        self.assertTrue(self.cache.try_seed_depth(provider))
        provider.assert_called_once_with(self.fbo)
        provider.return_value = False
        self.assertFalse(self.cache.try_seed_depth(provider))
        provider.side_effect = RuntimeError('possibly partial transfer')
        self.rebuild.side_effect = lambda: self.cache.try_seed_depth(provider)
        with self.assertRaisesRegex(RuntimeError, 'possibly partial'):
            self.prepare(key='changed camera')
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._completed)

    def test_matching_native_depth_attachment_is_explicitly_opt_in(self):
        self.prepare()
        self.format.setAttachment.assert_called_once_with(1)
        self.cache = ToolheadSurfaceCache(depth_only=True)
        self.prepare()
        self.format.setAttachment.assert_called_with(2)


class DeferredCropTests(unittest.TestCase):
    def test_hemisphere_envelope_contains_every_lit_sample_and_raster_padding(self):
        import numpy as np
        from mpf.toolhead.ToolheadSurfaceCache import light_extent
        rng = np.random.default_rng(9843)
        samples = rng.normal(size=(30000,3))
        samples /= np.linalg.norm(samples,axis=1)[:,None]
        position = np.array([130.,10.,-50.])
        for direction in ([0,-1,0],[1,0,0],[.1,.2,.97],[-.9,.1,.01]):
            normal = np.asarray(direction)/np.linalg.norm(direction)
            lit = position+samples[samples@normal >= 0]*40.
            lo,hi = light_extent(position,40.,.7,direction)
            self.assertTrue(np.all(lit-.7 >= lo))
            self.assertTrue(np.all(lit+.7 <= hi))
        lo,hi = light_extent(position,40.,.7,[0,-1,0])
        self.assertLess(hi[1],11.)
        for direction in ([0,0,0],[float('nan'),0,0],None):
            lo,hi=light_extent(position,40.,.7,direction)
            self.assertTrue(np.all(lo <= position-40.7))
            self.assertTrue(np.all(hi >= position+40.7))

    def test_moving_spheres_retain_new_receivers_and_padding_expands_the_crop(self):
        import numpy as np
        from mpf.toolhead.ToolheadSurfaceCache import light_scissor
        matrix = SimpleNamespace(getData=lambda: np.eye(4))
        camera = SimpleNamespace(getInverseWorldTransformation=lambda: matrix,
            getProjectionMatrix=lambda: matrix)
        left = light_scissor(camera, [((-.6, 0, 0), .1)], 1000, 1000)
        right = light_scissor(camera, [((.6, 0, 0), .1)], 1000, 1000)
        both = light_scissor(camera, [((-.6, 0, 0), .1), ((.6, 0, 0), .1)], 1000, 1000)
        padded = light_scissor(camera, [((.6, 0, 0), .1)], 1000, 1000, .1)
        self.assertLess(left[0] + left[2], right[0])
        self.assertLessEqual(both[0], left[0])
        self.assertGreaterEqual(both[0] + both[2], right[0] + right[2])
        self.assertLess(padded[0], right[0])
        self.assertGreater(padded[2], right[2])
        self.assertIsNone(light_scissor(camera, [((3, 0, 0), .1)], 1000, 1000))


class DeferredShadeTests(unittest.TestCase):
    def test_each_motion_refreshes_light_uniforms_and_failed_bind_releases_owned_state(self):
        cache = ToolheadSurfaceCache("#version 410\nout vec4 frag_color;\nvoid main() {}")
        cache._fbo = Mock()
        cache._fbo.textures.return_value = [31, 32, 33]
        cache._size = (800, 600, 0)
        shader, vao = Mock(), Mock()
        shader.setVertexShader.return_value = shader.setFragmentShader.return_value = True
        shader.bind.return_value = True
        vao.create.return_value = True
        gl = Mock(GL_DEPTH_TEST=1, GL_CULL_FACE=2, GL_LEQUAL=3, GL_BLEND=4,
            GL_SRC_ALPHA=5, GL_ONE=6, GL_TRIANGLES=7)
        gl.glGetError.return_value = 0
        gl.glGetIntegerv.side_effect = lambda name: (8, 12, 800, 600) if name == 0x0BA2 else 0x84C5
        node = Mock()
        node.scene_light_bounds.return_value = [((0, 0, 0), 1)]
        node.scene_lighting_effects.return_value = (True, False)
        camera = Mock()
        import numpy as np
        camera.getProjectionMatrix.return_value.getData.return_value = np.eye(4)
        camera.getInverseWorldTransformation.return_value.getData.return_value = np.eye(4)
        modules = {
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLVertexArrayObject=lambda: vao),
            'UM.View.GL.ShaderProgram': SimpleNamespace(ShaderProgram=lambda: shader),
            'mpf.toolhead.ToolheadSceneLighting': SimpleNamespace(lighting_fragment=lambda _: '#version 410\nout vec4 frag_color;\nvoid main() {}')}
        with patch.dict('sys.modules', modules), patch('mpf.toolhead.ToolheadSurfaceCache.light_scissor', return_value=(10, 20, 30, 40)):
            cache.shade(gl, camera, node)
            cache.shade(gl, camera, node)
            self.assertEqual(node.apply_attached_lights.call_count, 2)
            shader.setFragmentShader.assert_called_once()
            shader.setUniformValue.assert_any_call('u_orthographic', 1)
            shader.setUniformValue.assert_any_call('u_viewDirection', [0.,0.,1.])
            shader.setUniformValue.assert_any_call('u_lightBed', 1)
            shader.setUniformValue.assert_any_call('u_lightModels', 0)
            gl.glScissor.assert_called_with(18, 32, 30, 40)
            shader.bind.return_value = False
            with self.assertRaisesRegex(RuntimeError, 'bound'): cache.shade(gl, camera, node)
            self.assertEqual(shader.release.call_count, 3)
            self.assertEqual(vao.release.call_count, 3)
            gl.glActiveTexture.assert_called_with(0x84C5)
            for index in range(3): gl.glActiveTexture.assert_any_call(0x84C0 + index)
            gl.glBindTexture.assert_any_call(0x0DE1, 0)
            gl.glDisable.assert_called_with(0x0C11)
