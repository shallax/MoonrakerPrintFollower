"""Foreground transparency preserves head coverage and retained crop resources."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mpf.toolhead.ToolheadTransparency import ToolheadTransparency


class TransparencyTests(unittest.TestCase):
    def setUp(self):
        self.gl = Mock(GL_TEXTURE0=33984)
        self.gl.glGetError.return_value = 0
        self.head = Mock()
        self.head.format().samples.return_value = 0
        self.head.size().width.return_value = 64
        self.head.size().height.return_value = 96
        self.front, self.merged = Mock(), Mock()
        self.fbos = Mock(side_effect=[self.front, self.merged, self.front, self.merged])
        self.shader, self.vao = Mock(), Mock()
        self.shader_factory = Mock(return_value=self.shader)
        self.batch = Mock()
        self.batch_factory = Mock(return_value=self.batch)
        self.batch_factory.RenderType = SimpleNamespace(Transparent=2)
        def render(camera):
            self.batch_factory.call_args.kwargs['state_setup_callback'](self.gl)
        self.batch.render.side_effect = render
        self.modules = patch.dict('sys.modules', {
            'PyQt6.QtOpenGL': SimpleNamespace(QOpenGLFramebufferObject=self.fbos,
                QOpenGLVertexArrayObject=Mock(return_value=self.vao)),
            'UM.View.GL.ShaderProgram': SimpleNamespace(ShaderProgram=self.shader_factory),
            'UM.View.RenderBatch': SimpleNamespace(RenderBatch=self.batch_factory)})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.camera = object()
        self.source = SimpleNamespace(shader=object(), renderMode=1, backfaceCull=True,
            renderRange=(1,3), items=[dict(transformation='transform', mesh='mesh',
                uniforms={'opacity': .4}, normal_transformation='normal')])
        self.helper = ToolheadTransparency()

    def draw(self):
        return self.helper.draw(self.gl, self.head, self.camera, [self.source])

    def test_native_surface_state_is_preserved_and_resources_reused_until_crop_resize(self):
        for _ in range(2): self.assertTrue(self.draw())
        self.assertEqual(self.fbos.call_count, 2)
        self.shader_factory.assert_called_once()
        self.batch.addItem.assert_called_with('transform', mesh='mesh', uniforms={'opacity':.4}, normal_transformation='normal')
        self.assertEqual(self.batch_factory.call_args.kwargs['range'], (1,3))
        self.assertTrue(self.batch_factory.call_args.kwargs['backface_cull'])
        self.gl.glBlendFuncSeparate.assert_called_with(self.gl.GL_SRC_ALPHA, self.gl.GL_ONE_MINUS_SRC_ALPHA,
            self.gl.GL_ONE, self.gl.GL_ONE_MINUS_SRC_ALPHA)
        self.fbos.blitFramebuffer.assert_any_call(self.front, self.head, self.gl.GL_DEPTH_BUFFER_BIT, 0x2600)
        self.fbos.blitFramebuffer.assert_any_call(self.head, self.merged, self.gl.GL_COLOR_BUFFER_BIT, 0x2600)
        self.head.size().width.return_value = 128
        self.assertTrue(self.draw())
        self.assertEqual(self.fbos.call_count, 4)
        self.gl.glViewport.assert_called_with(0,0,128,96)

    def test_empty_surfaces_and_multisampling_do_not_allocate(self):
        self.assertTrue(self.helper.draw(self.gl, self.head, self.camera, []))
        self.head.format().samples.return_value = 4
        self.assertFalse(self.draw())
        self.fbos.assert_not_called()

    def test_failed_depth_copy_leaves_head_colour_unchanged_and_rebinds_head(self):
        self.gl.glGetError.return_value = 1282
        self.assertFalse(self.draw())
        self.batch.render.assert_not_called()
        self.assertEqual(self.fbos.blitFramebuffer.call_count, 1)
        self.head.bind.assert_called()
        self.gl.glDepthMask.assert_called_with(True)

    def test_allocation_shader_and_array_failures_preserve_destination(self):
        failures = [(self.front.isValid, 'framebuffer'), (self.shader.setVertexShader, 'vertex'),
                    (self.shader.setFragmentShader, 'fragment'), (self.vao.create, 'array')]
        for method, label in failures:
            with self.subTest(label=label):
                self.helper = ToolheadTransparency()
                self.fbos.side_effect = [self.front, self.merged]
                method.return_value = False
                with self.assertRaises(RuntimeError): self.draw()
                self.head.bind.assert_called()
                method.return_value = True

    def test_failed_surface_or_merge_draw_releases_resources_and_does_not_replace_head(self):
        for method in (self.batch.render, self.gl.glDrawArrays):
            with self.subTest(method=method):
                self.fbos.blitFramebuffer.reset_mock()
                previous = method.side_effect
                method.side_effect = RuntimeError('draw')
                with self.assertRaisesRegex(RuntimeError, 'draw'): self.draw()
                self.assertEqual(self.fbos.blitFramebuffer.call_count, 1)
                self.head.bind.assert_called()
                method.side_effect = previous
        self.shader.release.assert_called()
        self.vao.release.assert_called()
