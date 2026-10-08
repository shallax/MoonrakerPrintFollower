"""Conservative head crops and cached premultiplied texture composition."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from mpf.toolhead.ToolheadFrameCache import ToolheadFrameCache, projected_bounds


class FrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.fbo = Mock()
        self.factory = Mock(return_value=self.fbo)
        self.factory.Attachment = SimpleNamespace(CombinedDepthStencil=1, Depth=2)
        self.format = Mock()
        self.blitter = Mock()
        self.blitter_factory = Mock(return_value=self.blitter)
        self.blitter_factory.Origin = SimpleNamespace(OriginBottomLeft=1)
        self.blitter_factory.targetTransform.return_value = "quad"
        self.native_bind = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint)(lambda *_: None)
        self.current_context = SimpleNamespace(getProcAddress=lambda _: ctypes.cast(self.native_bind, ctypes.c_void_p).value)
        self.context = patch.dict("sys.modules", {
            "PyQt6.QtGui": SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda: self.current_context)),
            "PyQt6.QtOpenGL": SimpleNamespace(QOpenGLFramebufferObject=self.factory,
                QOpenGLFramebufferObjectFormat=lambda: self.format, QOpenGLTextureBlitter=self.blitter_factory),
            "UM.Math.Matrix": SimpleNamespace(Matrix=lambda data: SimpleNamespace(getData=lambda: data)),
        })
        self.context.start()
        self.addCleanup(self.context.stop)
        self.cache = ToolheadFrameCache()
        self.cache._bind = Mock()
        self.gl = Mock(GL_COLOR_BUFFER_BIT=16384, GL_DEPTH_BUFFER_BIT=256, GL_DEPTH_TEST=1,
            GL_CULL_FACE=2, GL_BLEND=3, GL_ONE=1, GL_ONE_MINUS_SRC_ALPHA=771)
        self.viewport = (0, 0, 800, 600)
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 0 if name == 0x80A9 else 17
        self.matrix = np.eye(4)
        self.transform = SimpleNamespace(getData=lambda: self.matrix)
        self.camera = SimpleNamespace(getInverseWorldTransformation=lambda: self.transform,
            getProjectionMatrix=lambda: self.transform, getWorldPosition=lambda: (0, 0, 0),
            getCameraLightPosition=lambda: (0, 1, 0))
        self.render = Mock()
        self.bounds = np.array([[-.1, -.2, -.1], [.1, .2, .1]])

    def draw(self, state="pose"):
        self.cache.draw(self.gl, self.camera, self.bounds, self.transform, state, self.render)

    def test_unrelated_compositions_reuse_shading_and_premultiplied_alpha(self):
        for _ in range(30): self.draw()
        self.render.assert_called_once()
        self.assertEqual(self.blitter.blit.call_count, 30)
        self.gl.glBlendFunc.assert_called_with(1, 771)
        self.cache._bind.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.assertLess(self.factory.call_args.args[0], 800)
        self.assertLess(self.factory.call_args.args[1], 600)

    def test_final_image_uses_additive_equations_and_restores_host_even_on_failure(self):
        original = self.gl.glGetIntegerv.side_effect
        self.gl.glGetIntegerv.side_effect = lambda key: {0x8009: 0x800A, 0x883D: 0x8007}.get(key, original(key))
        current = [None]
        self.gl.glBlendEquationSeparate.side_effect = lambda *value: current.__setitem__(0, value)
        def blit(*args):
            self.assertEqual(current[0], (0x8006, 0x8006))
            raise RuntimeError('final image failed')
        self.blitter.blit.side_effect = blit
        with self.assertRaisesRegex(RuntimeError, 'final image failed'): self.draw()
        self.assertEqual(current[0], (0x800A, 0x8007))
        self.cache._bind.assert_called_with(0x8D40, 17)

    def test_scene_depth_revision_invalidates_cached_head_and_seed_receives_native_camera_crop(self):
        seed = Mock(return_value=True)
        for revision in ("prefix-1", "prefix-1", "fraction-.5", "fraction-.75"):
            self.cache.draw(self.gl, self.camera, self.bounds, self.transform, "pose", self.render,
                            depth_seed=seed, depth_revision=revision)
        self.assertEqual(self.render.call_count, 3)
        self.assertEqual(seed.call_count, 3)
        args = seed.call_args.args
        self.assertIs(args[1], self.fbo)
        self.assertIs(args[2], self.camera)
        self.assertEqual(args[3], self.viewport)
        self.assertEqual(args[4], projected_bounds(self.bounds, self.matrix, self.matrix, self.matrix, 800, 600))

    def test_failed_depth_seed_clears_partial_depth_and_retries_next_composition(self):
        seed = Mock(side_effect=[False, RuntimeError("copy failed"), True, True])
        for _ in range(4):
            self.cache.draw(self.gl, self.camera, self.bounds, self.transform, "pose", self.render,
                            depth_seed=seed, depth_revision="same-source")
        self.assertEqual(seed.call_count, 3)
        self.assertEqual(self.render.call_count, 3)
        self.gl.glClear.assert_any_call(self.gl.GL_DEPTH_BUFFER_BIT)
        self.cache._bind.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_global_fade_reuses_image_and_scales_premultiplied_colour_and_alpha_once(self):
        self.gl.glGetFloatv.return_value = (.1, .2, .3, .4)
        for opacity in (1., .35, .8, 1.):
            self.cache.draw(self.gl, self.camera, self.bounds, self.transform, "pose", self.render,
                            opacity=opacity)
            self.blitter.setOpacity.assert_called_with(opacity)
        self.render.assert_called_once()
        self.gl.glBlendFuncSeparate.assert_any_call(0x8003, 771, 1, 771)
        self.gl.glBlendColor.assert_any_call(0., 0., 0., .35)
        self.gl.glBlendColor.assert_called_with(.1, .2, .3, .4)
        self.gl.glBlendFunc.assert_called_with(1, 771)

    def test_foreground_blending_uses_cropped_camera_after_head_and_is_cached(self):
        order = []
        self.render.side_effect = lambda camera: order.append(('head', camera))
        overlay = Mock(side_effect=lambda gl, fbo, camera: order.append(('front', camera)) or True)
        for _ in range(2):
            self.cache.draw(self.gl, self.camera, self.bounds, self.transform, 'pose', self.render,
                post_render=overlay)
        self.assertEqual([entry[0] for entry in order], ['head', 'front'])
        self.assertIs(order[0][1], order[1][1])
        self.assertIsNot(order[0][1], self.camera)
        overlay.assert_called_once()

    def test_failed_foreground_blend_is_retried_without_hiding_the_head(self):
        overlay = Mock(side_effect=[RuntimeError('overlay'), False, True])
        for _ in range(4):
            self.cache.draw(self.gl, self.camera, None, self.transform, 'pose', self.render,
                post_render=overlay)
        self.assertEqual(overlay.call_count, 3)
        self.assertEqual(self.blitter.blit.call_count, 4)

    def test_fade_composition_failure_restores_constant_colour_and_framebuffer(self):
        self.gl.glGetFloatv.return_value = (.1, .2, .3, .4)
        self.blitter.blit.side_effect = RuntimeError("composition failed")
        with self.assertRaisesRegex(RuntimeError, "composition failed"):
            self.cache.draw(self.gl, self.camera, self.bounds, self.transform, "pose", self.render, opacity=.5)
        self.gl.glBlendColor.assert_called_with(.1, .2, .3, .4)
        self.cache._bind.assert_called_with(0x8D40, 17)

    def test_depth_only_lighting_target_is_opt_in_and_multisample_keeps_original_attachment(self):
        self.draw()
        self.format.setAttachment.assert_called_once_with(1)
        self.cache = ToolheadFrameCache(depth_only=True)
        self.draw()
        self.format.setAttachment.assert_called_with(2)
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 4 if name == 0x80A9 else 17
        self.draw()
        self.format.setAttachment.assert_called_with(1)

    def test_pose_camera_opacity_and_size_changes_invalidate_pixels(self):
        self.draw()
        self.draw("opacity .35")
        self.matrix[0, 3] = .05
        self.draw("opacity .35")
        self.viewport = (0, 0, 900, 700)
        self.draw("opacity .35")
        self.assertEqual(self.render.call_count, 4)

    def test_projection_switch_invalidates_pixels_and_crop_retains_camera_mode(self):
        projection = np.eye(4)
        self.camera.getProjectionMatrix = lambda: SimpleNamespace(getData=lambda: projection)
        for orthographic in (True, True, False, False, True):
            projection[3] = [0, 0, 0, 1] if orthographic else [0, 0, -1, 0]
            self.draw()
            cropped = self.render.call_args.args[0].getProjectionMatrix().getData()
            np.testing.assert_array_equal(cropped[3], projection[3])
        self.assertEqual(self.render.call_count, 3)

    def test_crop_projection_preserves_the_original_screen_position(self):
        self.draw()
        cropped = self.render.call_args.args[0].getProjectionMatrix().getData()
        left, bottom, width, height = projected_bounds(self.bounds, np.eye(4), np.eye(4), np.eye(4), 800, 600)
        for point in ([.05, .1, 0, 1], [-.08, -.15, 0, 1]):
            projected = cropped @ point
            np.testing.assert_allclose((projected[:2] / projected[3] + 1) * [width / 2, height / 2] + [left, bottom],
                (np.array(point[:2]) + 1) * [400, 300])

    def test_hidden_or_empty_view_does_no_gpu_work(self):
        self.matrix[0, 3] = 10
        self.draw()
        self.assertIsNone(self.cache._fbo)
        self.viewport = (0, 0, 0, 600)
        self.draw()
        self.render.assert_not_called()

    def test_near_camera_bounds_use_conservative_full_view(self):
        projection = np.eye(4)
        projection[3, 3] = -1
        self.assertEqual(projected_bounds(self.bounds, np.eye(4), np.eye(4), projection, 800, 600), (0, 0, 800, 600))

    def test_draw_failure_restores_target_without_accepting_cached_image(self):
        self.render.side_effect = RuntimeError("draw failed")
        with self.assertRaisesRegex(RuntimeError, "draw failed"): self.draw()
        self.assertIsNone(self.cache._key)
        self.cache._bind.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_additive_illumination_reuses_a_full_view_without_double_alpha(self):
        for _ in range(30):
            self.cache.draw(self.gl, self.camera, None, self.transform, "lights", self.render, additive=True)
        self.render.assert_called_once()
        self.factory.assert_called_once_with(800, 600, self.format)
        self.gl.glBlendFunc.assert_called_with(1, 1)
        np.testing.assert_array_equal(self.render.call_args.args[0].getProjectionMatrix().getData(), np.eye(4))

    def test_small_head_moves_reuse_tiled_storage_but_refresh_shading(self):
        self.draw()
        self.matrix[0, 3] = .001
        self.draw()
        self.factory.assert_called_once()
        self.assertEqual(self.render.call_count, 2)

    def test_full_view_preserves_native_projection_bytes_for_shared_depth(self):
        # Native depth admission compares the exact projection. Rebuilding it
        # through a float64 identity loses float32 storage and negative zeros.
        self.matrix = np.eye(4, dtype=np.float32)
        self.matrix[0, 1] = -0.0
        original = self.matrix.tobytes()
        for bounds in (None, np.array([[-2., -2., -2.], [2., 2., 2.]])):
            with self.subTest(bounds=bounds):
                self.cache._key = None
                self.cache.draw(self.gl, self.camera, bounds, self.transform, "native", self.render)
                rendered = self.render.call_args.args[0].getProjectionMatrix().getData()
                self.assertEqual(rendered.dtype, self.matrix.dtype)
                self.assertEqual(rendered.tobytes(), original)

    def test_failed_allocation_binding_or_composition_restores_destination(self):
        for failure in ("storage", "blitter", "binding", "composition", "resolve"):
            with self.subTest(failure=failure):
                self.cache._key = self.cache._size = self.cache._blitter = None
                self.fbo.isValid.return_value = failure != "storage"
                self.blitter.create.return_value = failure != "blitter"
                self.fbo.bind.return_value = failure != "binding"
                self.blitter.blit.side_effect = RuntimeError("composition failed") if failure == "composition" else None
                self.factory.blitFramebuffer.side_effect = RuntimeError("resolve failed") if failure == "resolve" else None
                self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 4 if name == 0x80A9 else 17
                with self.assertRaises(RuntimeError): self.draw()
                self.cache._bind.assert_called_with(0x8D40, 17)
                self.gl.glViewport.assert_called_with(*self.viewport)

    def test_new_context_retires_pixels_and_function_pointers(self):
        self.draw()
        self.current_context = SimpleNamespace(getProcAddress=lambda _: ctypes.cast(self.native_bind, ctypes.c_void_p).value)
        self.draw()
        self.assertEqual(self.factory.call_count, 2)
        self.assertEqual(self.render.call_count, 2)
        self.assertEqual(self.blitter_factory.call_count, 2)
        self.assertIsInstance(self.cache._bind, ctypes._CFuncPtr)

    def test_context_and_entry_point_must_exist(self):
        self.current_context = None
        with self.assertRaisesRegex(RuntimeError, "context unavailable"): self.draw()
        self.current_context = SimpleNamespace(getProcAddress=lambda _: None)
        self.cache._bind = None
        with self.assertRaisesRegex(RuntimeError, "binding unavailable"): self.draw()
        self.factory.assert_not_called()

    def test_invalid_projection_and_offset_viewport_keep_conservative_coverage(self):
        projection = np.eye(4)
        projection[1, 1] = np.nan
        self.assertEqual(projected_bounds(self.bounds, np.eye(4), np.eye(4), projection, 800, 600), (0, 0, 800, 600))
        self.viewport = (12, 18, 800, 600)
        self.draw()
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_failed_resolve_allocation_and_failed_blitter_release_restore_target(self):
        invalid = Mock()
        invalid.isValid.return_value = False
        self.factory.side_effect = [self.fbo, invalid]
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 4 if name == 0x80A9 else 17
        with self.assertRaisesRegex(RuntimeError, "resolve unavailable"): self.draw()
        self.cache._bind.assert_called_with(0x8D40, 17)
        self.factory.side_effect = None
        self.blitter.release.side_effect = RuntimeError("release failed")
        with self.assertRaisesRegex(RuntimeError, "release failed"): self.draw()
        self.cache._bind.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)

    def test_multisample_animation_fallback_is_resolved_after_the_single_pose(self):
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 4 if name == 0x80A9 else 17
        static, resolved = Mock(), Mock()
        static.texture.return_value = 0
        resolved.texture.return_value = 321
        self.factory.side_effect = [static, resolved]
        animation = Mock(return_value=static)
        self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'pose',self.render,animate=animation)
        self.assertEqual(self.factory.blitFramebuffer.call_count,2)
        self.factory.blitFramebuffer.assert_called_with(resolved,static)
        self.blitter.blit.assert_called_once_with(321,'quad',1)
        self.assertIs(animation.call_args.args[1],static)

    def test_multisample_head_is_resolved_before_texture_composition(self):
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 4 if name == 0x80A9 else 17
        self.draw()
        self.format.setSamples.assert_called_with(4)
        self.assertEqual(self.factory.call_count, 2)
        self.factory.blitFramebuffer.assert_called_once()


if __name__ == "__main__": unittest.main()
