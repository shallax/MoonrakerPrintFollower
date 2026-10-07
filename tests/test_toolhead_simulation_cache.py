"""Retained native RGBA/depth contracts without starting Cura or a GL context."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mpf.toolhead.ToolheadSimulationCache import ToolheadSimulationCache


class SimulationCacheTests(unittest.TestCase):
    def setUp(self):
        self.context = Mock()
        self.current = Mock(return_value=self.context)
        self.fbo = Mock()
        self.fbo.handle.return_value = 17
        self.fbo.bind.return_value = self.fbo.isValid.return_value = True
        self.factory = Mock(return_value=self.fbo)
        self.factory.Attachment = SimpleNamespace(Depth=2)
        self.format = Mock()
        modules = {'PyQt6.QtGui': SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=self.current)),
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLFramebufferObject=self.factory,
                QOpenGLFramebufferObjectFormat=lambda: self.format)}
        patches = patch.dict('sys.modules', modules)
        patches.start(); self.addCleanup(patches.stop)
        self.output = Mock()
        self.output.handle.return_value = 3
        self.output.size.return_value.width.return_value = 800
        self.output.size.return_value.height.return_value = 600
        self.output.format.return_value.samples.return_value = 0
        self.output.format.return_value.internalTextureFormat.return_value = 0x8058
        self.viewport = (0, 0, 800, 600)
        self.draw_target, self.read_target, self.samples = 3, 29, 0
        self.clear = (0., 0., 0., 0.)
        self.gl = Mock(GL_COLOR_BUFFER_BIT=16384, GL_DEPTH_BUFFER_BIT=256)
        self.gl.glGetIntegerv.side_effect = lambda name: {0x0BA2:self.viewport, 0x8CA6:self.draw_target,
            0x8CAA:self.read_target, 0x80A9:self.samples}[name]
        self.gl.glGetFloatv.side_effect = lambda name: self.clear
        self.gl.glGetError.return_value = 0
        self.cache = ToolheadSimulationCache()
        self.cache._context = self.context
        self.cache._bind, self.cache._blit = Mock(), Mock()
        self.rebuild, self.append = Mock(), Mock()

    def restore(self, key='same', end=10):
        self.cache.restore(self.gl, self.output, key, end, self.rebuild, self.append)

    def test_append_only_completed_segments_and_copy_both_buffers_on_unchanged_frames(self):
        self.restore(); self.restore(end=12)
        for _ in range(10): self.restore(end=12)
        self.rebuild.assert_called_once()
        self.append.assert_called_once_with(10, 12)
        self.gl.glClear.assert_called_once_with(16640)
        self.assertEqual(self.cache._blit.call_count, 12)
        self.cache._blit.assert_called_with(0, 0, 800, 600, 0, 0, 800, 600, 16640, 0x2600)
        self.cache._bind.assert_any_call(0x8CA8, 17)
        self.cache._bind.assert_any_call(0x8CA9, 3)
        self.assertEqual(self.cache._bind.call_args_list[-2].args, (0x8CA8, 29))
        self.assertEqual(self.cache._bind.call_args_list[-1].args, (0x8CA9, 3))
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.gl.glClearColor.assert_not_called()
        self.format.setAttachment.assert_called_once_with(2)
        self.format.setInternalTextureFormat.assert_called_once_with(0x8058)

    def test_backwards_progress_and_shader_camera_layer_settings_changes_rebuild(self):
        self.restore(); self.restore(end=12); self.restore(end=8)
        for key in ('camera', 'normal to shadow', 'next layer', 'minimum layer', 'theme colour', 'extruder opacity'):
            self.restore(key, end=10)
        self.assertEqual(self.rebuild.call_count, 8)
        self.append.assert_called_once_with(10, 12)
        self.assertEqual(self.gl.glClear.call_count, 8)
        self.factory.assert_called_once()

    def test_changed_clear_colour_rebuilds_without_changing_global_clear_colour(self):
        self.restore()
        self.clear = (.1, .2, .3, .4)
        self.restore()
        self.assertEqual(self.rebuild.call_count, 2)
        self.gl.glClearColor.assert_not_called()

    def test_resize_retires_prefix_and_revalidates_copy(self):
        self.restore()
        self.viewport = (0, 0, 3840, 2190)
        self.output.size.return_value.width.return_value = 3840
        self.output.size.return_value.height.return_value = 2190
        self.restore(end=12)
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.rebuild.call_count, 2)
        self.append.assert_not_called()
        self.assertEqual(self.cache._size, (3840, 2190))
        self.assertEqual(self.gl.glGetError.call_count, 6)
        self.assertLess(3840 * 2190 * 8, self.cache.MAX_BYTES)

    def test_replaced_owned_destination_revalidates_copy_without_reextruding_geometry(self):
        self.restore()
        self.output.handle.return_value = self.draw_target = 31
        self.restore()
        self.rebuild.assert_called_once()
        self.assertEqual(self.gl.glGetError.call_count, 5)
        self.cache._bind.assert_called_with(0x8CA9, 31)

    def test_invalid_output_contracts_fail_before_storage_or_rendering(self):
        cases = [('host target', lambda: setattr(self, 'draw_target', 0)),
            ('invalid handle', lambda: self.output.handle.configure_mock(return_value=0)),
            ('zero size', lambda: setattr(self, 'viewport', (0, 0, 0, 0))),
            ('nonzero origin', lambda: setattr(self, 'viewport', (8, 12, 800, 600))),
            ('GL samples', lambda: setattr(self, 'samples', 4)),
            ('Qt samples', lambda: self.output.format.return_value.samples.configure_mock(return_value=4)),
            ('float colour', lambda: self.output.format.return_value.internalTextureFormat.configure_mock(return_value=0x8814)),
            ('negative prefix', lambda: None)]
        for name, mutate in cases:
            with self.subTest(name=name):
                self.setUp(); mutate()
                with self.assertRaises(RuntimeError): self.restore(end=-1 if name == 'negative prefix' else 10)
                self.factory.assert_not_called(); self.rebuild.assert_not_called(); self.append.assert_not_called()

    def test_retained_memory_budget_rejects_large_targets_before_allocation(self):
        self.viewport = (0, 0, 8192, 8192)
        self.output.size.return_value.width.return_value = 8192
        self.output.size.return_value.height.return_value = 8192
        with self.assertRaisesRegex(RuntimeError, 'memory budget'): self.restore()
        self.factory.assert_not_called()

    def test_callback_or_gl_failure_retires_partial_accumulation_and_restores_output(self):
        self.restore()
        self.append.side_effect = ValueError('append interrupted')
        with self.assertRaisesRegex(ValueError, 'append interrupted'): self.restore(end=12)
        self.assertIsNone(self.cache._key); self.assertIsNone(self.cache._fbo)
        self.cache._bind.assert_called_with(0x8CA9, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.append.side_effect = None
        self.restore(end=12)
        self.assertEqual(self.rebuild.call_count, 2)
        self.gl.glGetError.return_value = 0x0502
        with self.assertRaisesRegex(RuntimeError, 'retained draw failed.*0502'): self.restore(end=14)
        self.assertIsNone(self.cache._completed)

    def test_existing_or_copy_errors_are_rejected_and_copy_is_never_accepted(self):
        for errors, message, copies in (([0, 0x0500], 'existing GL error', 0),
                                       ([0, 0, 0x0502], 'colour/depth copy failed', 1)):
            with self.subTest(message=message):
                self.gl.glGetError.side_effect = errors
                with self.assertRaisesRegex(RuntimeError, message): self.restore()
                self.assertFalse(self.cache._copy_checked)
                self.assertIsNone(self.cache._key)
                self.assertEqual(self.cache._blit.call_count, copies)
                self.cache._blit.reset_mock()

    def test_invalid_allocation_binding_and_constructor_restore_output(self):
        for method, message in ((self.fbo.isValid, 'framebuffer unavailable'), (self.fbo.bind, 'could not be bound')):
            method.return_value = False
            with self.assertRaisesRegex(RuntimeError, message): self.restore()
            self.assertIsNone(self.cache._fbo)
            self.cache._bind.assert_called_with(0x8CA9, 3)
            self.gl.glViewport.assert_called_with(*self.viewport)
            method.return_value = True
        self.factory.side_effect = RuntimeError('allocation interrupted')
        with self.assertRaisesRegex(RuntimeError, 'allocation interrupted'): self.restore()
        self.rebuild.assert_not_called()

    def test_context_generation_discards_storage_and_reloads_function_pointers(self):
        self.restore()
        old_blit = self.cache._blit
        replacement_context = Mock()
        self.current.return_value = replacement_context
        bind, blit = Mock(), Mock()
        def functions(context):
            self.assertIs(context, replacement_context)
            self.assertIsNone(self.cache._fbo); self.assertIsNone(self.cache._key)
            self.cache._bind, self.cache._blit = bind, blit
        with patch.object(self.cache, '_functions', side_effect=functions) as load:
            self.restore()
        load.assert_called_once()
        self.assertEqual(self.rebuild.call_count, 2)
        old_blit.assert_called_once(); blit.assert_called_once()
        self.current.return_value = None
        self.gl.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'context unavailable'): self.restore()
        self.assertEqual(self.gl.mock_calls, [])
        self.assertIsNone(self.cache._bind); self.assertIsNone(self.cache._fbo)

    def test_missing_function_addresses_fail_without_gl_mutations(self):
        for addresses in ((0, 12), (11, 0)):
            cache = ToolheadSimulationCache()
            self.context.getProcAddress.side_effect = addresses
            with self.assertRaisesRegex(RuntimeError, 'copying unavailable'):
                cache.restore(self.gl, self.output, 'same', 10, self.rebuild, self.append)
        self.factory.assert_not_called(); self.rebuild.assert_not_called()
        self.assertEqual(self.gl.mock_calls, [])

    def test_proc_wrappers_have_explicit_colour_depth_blit_ABI(self):
        self.context.getProcAddress.side_effect = [11, 12]
        bind, blit = Mock(), Mock()
        bind_type, blit_type = Mock(return_value=bind), Mock(return_value=blit)
        with patch('mpf.toolhead.ToolheadSimulationCache.ctypes.CFUNCTYPE', side_effect=[bind_type, blit_type]) as types:
            self.cache._bind = self.cache._blit = None
            self.restore()
        self.assertEqual(len(types.call_args_list[0].args), 3)
        self.assertEqual(len(types.call_args_list[1].args), 11)
        bind_type.assert_called_once_with(11); blit_type.assert_called_once_with(12)
        blit.assert_called_once_with(0, 0, 800, 600, 0, 0, 800, 600, 16640, 0x2600)

    def test_viewport_restored_when_read_binding_restore_fails(self):
        self.cache._bind.side_effect = [None, None, RuntimeError('restore read failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'restore read failed'): self.restore()
        self.cache._bind.assert_called_with(0x8CA9, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def depth_ready(self):
        self.restore()
        self.cache._bind.reset_mock(); self.cache._blit.reset_mock()
        self.cache._depth_functions = Mock(return_value=('attachment', 'storage'))
        self.cache._depth_format = Mock(return_value=(0x81a7, 32, 0, 0))
        self.gl.glGetError.reset_mock()

    def test_optional_depth_copy_preserves_colour_and_uses_exact_source_prefix(self):
        self.depth_ready()
        self.assertTrue(self.cache.try_copy_depth(self.gl, self.output, 'same', 10))
        self.cache._blit.assert_called_once_with(0, 0, 800, 600, 0, 0, 800, 600, 256, 0x2600)
        self.cache._depth_format.assert_any_call(self.gl, 17, ('attachment', 'storage'))
        self.cache._depth_format.assert_any_call(self.gl, 3, ('attachment', 'storage'))
        self.assertEqual(self.cache._bind.call_args_list[-2].args, (0x8CA8, 29))
        self.cache._bind.assert_called_with(0x8CA9, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.assertEqual(self.cache._completed, 10)

    def test_stale_source_context_or_destination_returns_false_without_copy(self):
        cases = [lambda: self.current.configure_mock(return_value=None),
            lambda: self.current.configure_mock(return_value=Mock()),
            lambda: setattr(self.cache, '_fbo', None), lambda: setattr(self.cache, '_key', None),
            lambda: setattr(self, 'draw_target', 0),
            lambda: self.output.handle.configure_mock(return_value=0),
            lambda: setattr(self, 'viewport', (1, 0, 800, 600)),
            lambda: self.output.size.return_value.width.configure_mock(return_value=799),
            lambda: self.output.format.return_value.samples.configure_mock(return_value=4),
            lambda: setattr(self, 'samples', 4)]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.setUp(); self.depth_ready(); mutate()
                self.assertFalse(self.cache.try_copy_depth(self.gl, self.output, 'same', 10))
                self.cache._blit.assert_not_called()
                self.cache._depth_functions.assert_not_called()
        self.setUp(); self.depth_ready()
        self.assertFalse(self.cache.try_copy_depth(self.gl, self.output, 'changed camera', 10))
        self.assertFalse(self.cache.try_copy_depth(self.gl, self.output, 'same', 12))
        self.cache._blit.assert_not_called()

    def test_storage_mismatch_missing_depth_and_multisampling_reject_before_copy(self):
        for source, dest in [(None, (0x81a7,32,0,0)), ((0x81a7,32,0,0), (0x88f0,24,8,0)),
                             ((0x81a7,0,0,0), (0x81a7,0,0,0)), ((0x81a7,32,0,4), (0x81a7,32,0,4))]:
            with self.subTest(source=source, dest=dest):
                self.setUp(); self.depth_ready()
                self.cache._depth_format.side_effect = [source, dest]
                self.assertFalse(self.cache.try_copy_depth(self.gl, self.output, 'same', 10))
                self.cache._blit.assert_not_called()
                self.cache._bind.assert_called_with(0x8CA9, 3)
                self.gl.glViewport.assert_called_with(*self.viewport)
        self.cache._depth_functions.return_value = None
        self.assertFalse(self.cache.try_copy_depth(self.gl, self.output, 'same', 10))

    def test_optional_depth_command_failures_raise_and_restore_owned_destination(self):
        for errors, message, copies in [([0x0500], 'existing GL error', 0),
                                       ([0, 0x0502], 'format query failed', 0),
                                       ([0, 0, 0x0502], 'copy failed', 1)]:
            with self.subTest(message=message):
                self.setUp(); self.depth_ready(); self.gl.glGetError.side_effect = errors
                with self.assertRaisesRegex(RuntimeError, message):
                    self.cache.try_copy_depth(self.gl, self.output, 'same', 10)
                self.assertEqual(self.cache._blit.call_count, copies)
                self.cache._bind.assert_called_with(0x8CA9, 3)
                self.gl.glViewport.assert_called_with(*self.viewport)
                self.assertIsNotNone(self.cache._fbo)  # Colour cache still belongs to its native adapter.
        self.setUp(); self.depth_ready()
        self.cache._bind.side_effect = [None, None, RuntimeError('restore read failed'), None]
        with self.assertRaisesRegex(RuntimeError, 'restore read failed'):
            self.cache.try_copy_depth(self.gl, self.output, 'same', 10)
        self.cache._bind.assert_called_with(0x8CA9, 3)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_depth_query_wrappers_are_typed_and_missing_entry_points_are_optional(self):
        import ctypes
        for addresses in ((0, 12), (11, 0)):
            self.context.getProcAddress.side_effect = addresses
            self.assertIsNone(self.cache._depth_functions(self.context))
        self.context.getProcAddress.side_effect = [11, 12]
        attachment_type, storage_type = Mock(), Mock()
        with patch('mpf.toolhead.ToolheadSimulationCache.ctypes.CFUNCTYPE', side_effect=[attachment_type,storage_type]) as types:
            self.cache._depth_functions(self.context)
        self.assertEqual(types.call_args_list[0].args, (None,ctypes.c_uint,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_int)))
        self.assertEqual(types.call_args_list[1].args, (None,ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(ctypes.c_int)))
        attachment_type.assert_called_once_with(11); storage_type.assert_called_once_with(12)

    def test_semantic_attachment_query_restores_renderbuffer_and_rejects_textures(self):
        def set_pointer(pointer, value): pointer._obj.value = value
        def attachment(target, slot, parameter, pointer):
            set_pointer(pointer, 0x8D41 if parameter == 0x8CD0 else 123)
        def storage(target, parameter, pointer):
            set_pointer(pointer, {0x8D44:0x81a7,0x8D54:32,0x8D55:0,0x8CAB:0}[parameter])
        self.gl.glGetIntegerv.side_effect = lambda name: 55
        self.assertEqual(self.cache._depth_format(self.gl, 17, (attachment,storage)), (0x81a7,32,0,0))
        self.gl.glBindRenderbuffer.assert_any_call(0x8D41,123)
        self.gl.glBindRenderbuffer.assert_called_with(0x8D41,55)
        for kind, name in [(0x1702,123),(0x8D41,0)]:
            query = lambda target,slot,parameter,pointer,kind=kind,name=name: set_pointer(pointer,kind if parameter == 0x8CD0 else name)
            self.assertIsNone(self.cache._depth_format(self.gl,17,(query,storage)))
        with self.assertRaisesRegex(ValueError, 'query interrupted'):
            self.cache._depth_format(self.gl,17,(attachment,Mock(side_effect=ValueError('query interrupted'))))
        self.gl.glBindRenderbuffer.assert_called_with(0x8D41,55)
