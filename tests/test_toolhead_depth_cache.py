"""Static-depth cache invalidation and restoration of Qt's destination framebuffer."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mpf.toolhead.ToolheadDepthCache import ToolheadDepthCache


class DepthCacheTests(unittest.TestCase):
    def setUp(self):
        self.fbo = Mock()
        self.fbo.handle.return_value = 17
        self.fbo.bind.return_value = self.fbo.isValid.return_value = True
        self.factory = Mock(return_value=self.fbo)
        self.factory.Attachment = SimpleNamespace(CombinedDepthStencil=1)
        self.format = Mock()
        self.context = Mock()
        self.current_context = Mock(return_value=self.context)
        self.patches = patch.dict('sys.modules', {
            'PyQt6.QtGui': SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=self.current_context)),
            'PyQt6.QtOpenGL': SimpleNamespace(
                QOpenGLFramebufferObject=self.factory, QOpenGLFramebufferObjectFormat=lambda: self.format)})
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.cache = ToolheadDepthCache()
        self.cache._context = self.context
        self.cache._bind, self.cache._blit = Mock(), Mock()
        self.viewport = (8, 12, 800, 600)
        self.gl = Mock(GL_DEPTH_BUFFER_BIT=256)
        self.gl.glGetError.return_value = 0
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 3 if name == 0x8CA6 else 4
        self.draw = Mock()

    def test_cached_frames_skip_static_geometry_but_always_copy_depth(self):
        for _ in range(10): self.cache.restore(self.gl, ('camera', 100), self.draw)
        self.draw.assert_called_once()
        self.assertEqual(self.cache._blit.call_count, 10)
        self.cache._blit.assert_called_with(0, 0, 800, 600, 8, 12, 808, 612, 256, 0x2600)
        self.cache._bind.assert_called_with(0x8D40, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.format.setSamples.assert_called_with(4)
        self.assertEqual(self.gl.glGetError.call_count, 2)
        self.cache.restore(self.gl, ('rotated camera', 100), self.draw)
        self.cache.restore(self.gl, ('rotated camera', 101), self.draw)
        self.assertEqual(self.draw.call_count, 3)

    def test_growing_prefix_appends_without_clearing_but_backwards_rebuilds(self):
        append = Mock(side_effect=lambda old, new: new > old)
        self.cache.restore(self.gl, 10, self.draw, append=append)
        self.cache.restore(self.gl, 20, self.draw, append=append)
        self.cache.restore(self.gl, 20, self.draw, append=append)
        self.assertEqual(self.draw.call_count, 1)
        self.gl.glClear.assert_called_once_with(256)
        append.assert_called_once_with(10, 20)
        self.cache.restore(self.gl, 5, self.draw, append=append)
        self.assertEqual(self.draw.call_count, 2)
        self.assertEqual(self.gl.glClear.call_count, 2)
        self.viewport = (0, 0, 900, 700)
        self.cache.restore(self.gl, 8, self.draw, append=append)
        self.assertEqual(self.draw.call_count, 3)
        self.assertEqual(append.call_count, 2)  # Resizing retires old depth.

    def test_resize_invalidates_depth_and_recreates_storage(self):
        self.cache.restore(self.gl, 'same', self.draw)
        self.viewport = (0, 0, 900, 700)
        self.cache.restore(self.gl, 'same', self.draw)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.draw.call_count, 2)
        self.assertEqual(self.gl.glGetError.call_count, 4)

    def test_failed_depth_blit_invalidates_storage_and_requests_uncached_fallback(self):
        self.gl.glGetError.side_effect = [0, 0x0502]  # GL_INVALID_OPERATION.
        with self.assertRaisesRegex(RuntimeError, 'depth copy failed.*0502'):
            self.cache.restore(self.gl, 'new', self.draw)
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._fbo)
        self.assertIsNone(self.cache._size)
        self.assertFalse(self.cache._copy_checked)
        self.cache._bind.assert_called_with(0x8D40, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_successful_copy_is_revalidated_after_resize_and_sample_change(self):
        self.cache.restore(self.gl, 'same', self.draw)
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 3 if name == 0x8CA6 else 2
        self.cache.restore(self.gl, 'same', self.draw)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.gl.glGetError.call_count, 4)
        self.viewport = (0, 0, 900, 700)
        self.gl.glGetError.side_effect = [0, 0x0502]
        with self.assertRaisesRegex(RuntimeError, 'depth copy failed.*0502'):
            self.cache.restore(self.gl, 'same', self.draw)
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._fbo)
        self.cache._bind.assert_called_with(0x8D40, 3)

    def test_existing_error_is_reported_without_attributing_it_to_or_attempting_blit(self):
        self.gl.glGetError.return_value = 0x0500
        with self.assertLogs('mpf.toolhead.ToolheadDepthCache', level='WARNING') as logs:
            with self.assertRaisesRegex(RuntimeError, 'existing GL error.*0500'):
                self.cache.restore(self.gl, 'new', self.draw)
        self.assertIn('existing GL error', logs.output[0])
        self.cache._blit.assert_not_called()
        self.gl.glGetError.assert_called_once()
        self.assertIsNone(self.cache._key)
        self.cache._bind.assert_called_with(0x8D40, 3)

    def test_failed_draw_restores_destination_and_never_accepts_incomplete_depth(self):
        self.draw.side_effect = RuntimeError('failed draw')
        with self.assertRaises(RuntimeError): self.cache.restore(self.gl, 'new', self.draw)
        self.assertIsNone(self.cache._key)
        self.cache._bind.assert_called_with(0x8D40, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.draw.side_effect = None
        self.cache.restore(self.gl, 'new', self.draw)
        self.assertEqual(self.draw.call_count, 2)

    def test_invalid_viewport_and_failed_storage_do_not_draw(self):
        self.viewport = (0, 0, 0, 0)
        with self.assertRaises(RuntimeError): self.cache.restore(self.gl, 'new', self.draw)
        self.viewport = (0, 0, 900, 700)
        self.fbo.isValid.return_value = False
        with self.assertRaises(RuntimeError): self.cache.restore(self.gl, 'new', self.draw)
        self.draw.assert_not_called()

    def test_failed_append_discards_partially_written_storage_and_rebuilds_next_frame(self):
        self.cache.restore(self.gl, 10, self.draw)
        append = Mock(side_effect=RuntimeError('append draw interrupted'))
        with self.assertRaisesRegex(RuntimeError, 'append draw interrupted'):
            self.cache.restore(self.gl, 20, self.draw, append=append)
        append.assert_called_once_with(10, 20)
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._fbo)
        self.assertIsNone(self.cache._size)
        self.assertFalse(self.cache._copy_checked)
        self.cache._bind.assert_called_with(0x8D40, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.cache.restore(self.gl, 20, self.draw, append=append)
        self.assertEqual(self.draw.call_count, 2)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(append.call_count, 1)  # Retired storage cannot append.

    def test_cached_depth_copies_into_the_current_outer_framebuffer_and_viewport_origin(self):
        self.cache.restore(self.gl, 'static', self.draw)
        self.viewport = (16, 32, 800, 600)
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 29 if name == 0x8CA6 else 4
        self.cache.restore(self.gl, 'static', self.draw)
        self.draw.assert_called_once()
        self.factory.assert_called_once()
        self.cache._bind.assert_any_call(0x8CA9, 29)
        self.cache._bind.assert_called_with(0x8D40, 29)
        self.cache._blit.assert_called_with(0, 0, 800, 600, 16, 32, 816, 632, 256, 0x2600)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_context_generation_retires_storage_functions_and_copy_validation(self):
        self.cache.restore(self.gl, 'unchanged', self.draw)
        old_fbo, old_bind, old_blit = self.fbo, self.cache._bind, self.cache._blit
        replacement = Mock()
        replacement.handle.return_value = 23
        replacement.isValid.return_value = replacement.bind.return_value = True
        self.factory.return_value = replacement
        new_context = Mock()
        self.current_context.return_value = new_context
        new_bind, new_blit = Mock(), Mock()

        def load_functions(context):
            self.assertIs(context, new_context)
            for name in ('_fbo', '_key', '_size', '_bind', '_blit'):
                self.assertIsNone(getattr(self.cache, name))
            self.assertFalse(self.cache._copy_checked)
            self.cache._bind, self.cache._blit = new_bind, new_blit

        with patch.object(self.cache, '_functions', side_effect=load_functions) as functions:
            self.cache.restore(self.gl, 'unchanged', self.draw)
            self.cache.restore(self.gl, 'unchanged', self.draw)
        functions.assert_called_once_with(new_context)
        self.assertIs(self.cache._context, new_context)
        self.assertIs(self.cache._fbo, replacement)
        self.assertEqual(self.draw.call_count, 2)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.gl.glGetError.call_count, 4)
        old_fbo.bind.assert_called_once()
        old_blit.assert_called_once()
        self.assertEqual(old_bind.call_count, 3)
        self.assertEqual(new_blit.call_count, 2)
        new_bind.assert_any_call(0x8CA8, 23)

    def test_missing_current_context_retires_old_depth_without_gl_work(self):
        self.cache.restore(self.gl, 'same', self.draw)
        self.current_context.return_value = None
        self.gl.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'graphics context unavailable'):
            self.cache.restore(self.gl, 'same', self.draw)
        for name in ('_context', '_fbo', '_key', '_size', '_bind', '_blit'):
            self.assertIsNone(getattr(self.cache, name))
        self.assertFalse(self.cache._copy_checked)
        self.assertEqual(self.gl.mock_calls, [])
        self.draw.assert_called_once()

    def test_missing_either_copy_function_fails_before_allocation_or_draw(self):
        for addresses in ((0, 12), (11, 0)):
            with self.subTest(addresses=addresses):
                cache = ToolheadDepthCache()
                self.context.getProcAddress.side_effect = addresses
                with self.assertRaisesRegex(RuntimeError, 'copying unavailable'):
                    cache.restore(self.gl, 'same', self.draw)
                self.assertIsNone(cache._bind)
                self.assertIsNone(cache._blit)
                self.assertIsNone(cache._fbo)
                self.assertIsNone(cache._size)
        self.factory.assert_not_called()
        self.draw.assert_not_called()
        self.gl.glGetIntegerv.assert_not_called()

    def test_functions_use_current_context_addresses_and_exact_blit_signature(self):
        self.context.getProcAddress.side_effect = [11, 12]
        bind, blit = Mock(), Mock()
        bind_type, blit_type = Mock(return_value=bind), Mock(return_value=blit)
        with patch('mpf.toolhead.ToolheadDepthCache.ctypes.CFUNCTYPE', side_effect=[bind_type, blit_type]) as factory:
            self.cache._bind = self.cache._blit = None
            self.cache.restore(self.gl, 'same', self.draw)
        self.context.getProcAddress.assert_any_call(b'glBindFramebuffer')
        self.context.getProcAddress.assert_any_call(b'glBlitFramebuffer')
        bind_type.assert_called_once_with(11)
        blit_type.assert_called_once_with(12)
        self.assertEqual(len(factory.call_args_list[0].args), 3)
        self.assertEqual(len(factory.call_args_list[1].args), 11)
        blit.assert_called_once_with(0, 0, 800, 600, 8, 12, 808, 612, 256, 0x2600)
        bind.assert_called_with(0x8D40, 3)

    def test_failed_fbo_bind_retires_storage_and_restores_destination(self):
        self.fbo.bind.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'could not be bound'):
            self.cache.restore(self.gl, 'new', self.draw)
        self.draw.assert_not_called()
        self.cache._blit.assert_not_called()
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._fbo)
        self.assertIsNone(self.cache._size)
        self.assertFalse(self.cache._copy_checked)
        self.cache._bind.assert_called_once_with(0x8D40, 3)
        self.gl.glViewport.assert_called_once_with(*self.viewport)

    def test_invalid_allocation_and_constructor_failure_restore_destination(self):
        self.fbo.isValid.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
            self.cache.restore(self.gl, 'new', self.draw)
        self.assertIsNone(self.cache._fbo)
        self.assertIsNone(self.cache._size)
        self.cache._bind.assert_called_with(0x8D40, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.factory.side_effect = RuntimeError('allocation interrupted')
        with self.assertRaisesRegex(RuntimeError, 'allocation interrupted'):
            self.cache.restore(self.gl, 'new', self.draw)
        self.assertIsNone(self.cache._key)
        self.assertIsNone(self.cache._size)
        self.assertEqual(self.gl.glViewport.call_count, 2)
        self.draw.assert_not_called()

    def test_viewport_is_restored_even_if_destination_rebinding_raises(self):
        self.cache._bind.side_effect = [None, None, RuntimeError('destination unavailable')]
        with self.assertRaisesRegex(RuntimeError, 'destination unavailable'):
            self.cache.restore(self.gl, 'new', self.draw)
        self.gl.glViewport.assert_called_with(*self.viewport)
