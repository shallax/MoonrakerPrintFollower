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


    def test_released_targets_reallocate_the_same_crop(self):
        self.draw()
        self.cache.release_targets()
        for name in ('_fbo', '_resolved', '_key', '_size', '_output'):
            self.assertIsNone(getattr(self.cache, name))
        self.factory.reset_mock();self.render.reset_mock()
        self.draw()
        self.factory.assert_called_once();self.render.assert_called_once()

    def test_unrelated_compositions_reuse_shading_and_premultiplied_alpha(self):
        for _ in range(30): self.draw()
        self.render.assert_called_once()
        self.assertEqual(self.blitter.blit.call_count, 30)
        self.gl.glBlendFunc.assert_called_with(1, 771)
        self.cache._bind.assert_called_with(0x8D40, 17)
        self.gl.glViewport.assert_called_with(*self.viewport)
        self.assertLess(self.factory.call_args.args[0], 800)
        self.assertLess(self.factory.call_args.args[1], 600)

    def test_receiver_progress_pumps_cached_fallback_and_redraws_only_complete_group(self):
        calls=[]; identity=[None]; original_keys=[]
        def step(key):
            calls.append('step'); original_keys.append(key); return identity[0]
        def prepare(camera,output,key,seeded,viewport,crop):
            calls.append('prepare')
            self.assertIs(output,self.fbo); self.assertTrue(seeded)
            self.assertEqual(key,original_keys[-1]); self.assertEqual(viewport,self.viewport)
            self.assertEqual(crop[2:],self.factory.call_args.args[:2])
        def draw():
            return self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'receiver',self.render,
                fallback_backend='Environment map',receiver_step=step,receiver_prepare=prepare)
        self.assertEqual(draw(),'Environment map')
        self.assertEqual(draw(),'Environment map')
        self.assertEqual(calls,['step','prepare','step']); self.render.assert_called_once()
        identity[0]=('complete',4,'all shutter poses')
        self.assertEqual(draw(),'Environment map'); self.assertEqual(self.render.call_count,2)
        completed_key=self.cache._key
        draw(); self.assertEqual(self.render.call_count,2); self.assertEqual(self.cache._key,completed_key)
        self.assertTrue(all(key==original_keys[0] for key in original_keys))
        self.matrix[0,3]=.1; identity[0]=None
        draw(); self.assertNotEqual(original_keys[-1],original_keys[0]); self.assertEqual(self.render.call_count,3)

    def test_receiver_prepare_follows_original_seed_and_keeps_foreground_and_shutter(self):
        events=[]
        seed=lambda *args:events.append('seed') or True
        prepare=lambda *args:events.append(('prepare',args[3]))
        render=lambda camera:events.append('render')
        post=lambda *args:events.append('foreground') or True
        animate=lambda *args:events.append('shutter') or self.fbo
        status=self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'receiver',render,
            depth_seed=seed,receiver_step=lambda key:None,receiver_prepare=prepare,
            post_render=post,animate=animate,fallback_backend='Environment map')
        self.assertEqual(events,['seed',('prepare',True),'render','foreground','shutter'])
        self.assertEqual(status,'Environment map')
        self.cache.invalidate(); events.clear()
        self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'receiver',render,
            depth_seed=lambda *args:False,receiver_step=lambda key:None,receiver_prepare=prepare)
        self.assertEqual(events,[('prepare',False),'render'])






    def test_obsolete_qt_siblings_retire_before_ordinary_constructor_without_a_worker(self):
        retained = [270 << 20]
        calls = []
        self.viewport = (0, 0, 2176, 1024)
        self.gl.glGetIntegerv.side_effect = lambda name: self.viewport if name == 0x0BA2 else 4 if name == 0x80A9 else 17
        def retire(*args):
            calls.append(('retire', args)); retained[0] = 0
        def allocate(*args):
            self.assertEqual(retained[0], 0)
            self.assertEqual(calls[0][0], 'retire')
            return self.fbo
        self.factory.side_effect = allocate
        self.cache.draw(self.gl,self.camera,None,self.transform,'ordinary',self.render,
                animate=Mock(return_value=self.fbo), retire_graphics=retire)
        self.factory.assert_called()
        self.assertEqual(calls[0][1], (2176, 1024, 4))

    def test_failed_external_retirement_never_constructs_an_ordinary_target(self):
        retire = Mock(side_effect=RuntimeError('unretired sample owner'))
        with self.assertRaisesRegex(RuntimeError,'unretired sample owner'):
            self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'ordinary',self.render,retire_graphics=retire)
        self.factory.assert_not_called()


    def test_same_size_host_ms_target_cannot_alias_explicit_float_depth_owner(self):
        from contextlib import nullcontext
        from mpf.toolhead import ToolheadFrameCache as module
        self.gl.glGetIntegerv.side_effect=lambda name:self.viewport if name==0x0BA2 else 4 if name==0x80A9 else 17
        class Explicit:
            def __init__(self,gl,width,height):
                self.positions=((.375,.125),(.875,.375),(.125,.625),(.625,.875))
                self.width=lambda:width;self.height=lambda:height
                self.isValid=lambda:True;self.bind=lambda:True
                self.close=lambda:None
        with patch.object(module,'ToolheadSampleTarget',Explicit), \
                patch.object(module,'preserved_samples',side_effect=lambda *args:nullcontext()), \
                patch.object(module,'sample_blit') as blit:
            self.draw();ordinary=self.cache._fbo
            self.assertNotIsInstance(ordinary,Explicit)
            self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'pose',self.render,requested_samples=4)
            explicit=self.cache._fbo
            self.assertIsInstance(explicit,Explicit)
            blit.assert_called_with(self.gl,self.cache._resolved,explicit)
            self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'pose',self.render,requested_samples=4)
            self.assertIs(self.cache._fbo,explicit);self.assertEqual(self.render.call_count,2)
            explicit.positions=tuple(reversed(explicit.positions))
            self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'pose',self.render,requested_samples=4)
            self.assertEqual(self.render.call_count,3)
            self.draw();self.assertNotIsInstance(self.cache._fbo,Explicit)
            self.assertEqual(self.render.call_count,4)
        with self.assertRaises(ValueError):self.cache.draw(requested_samples=2)

    def test_crop_resize_drops_obsolete_siblings_before_replacement_allocation(self):
        self.draw()
        self.cache._resolved = object()
        self.bounds *= 2
        def allocate(*args):
            for name in ('_fbo', '_resolved', '_size', '_key'):
                self.assertIsNone(getattr(self.cache, name), name)
            return self.fbo
        self.factory.side_effect = allocate
        self.draw('new crop')
        self.assertIs(self.cache._fbo, self.fbo)

    def test_failed_sample_retirement_keeps_lease_and_never_allocates_replacement(self):
        from mpf.toolhead import ToolheadFrameCache as module
        class Sample:
            def close(self): raise RuntimeError('retirement failed')
        old = Sample()
        self.cache._context = self.current_context
        self.cache._fbo = old
        self.cache._size = (1, 1, 4)
        self.cache._resolved = object()
        with patch.object(module, 'ToolheadSampleTarget', Sample):
            with self.assertRaisesRegex(RuntimeError, 'retirement failed'): self.draw()
        self.assertIs(self.cache._fbo, old)
        self.assertIsNone(self.cache._resolved)
        self.factory.assert_not_called()
        self.render.assert_not_called()

    def test_same_context_wrapper_retirement_invalidates_every_cached_graphics_owner(self):
        self.draw();retired=self.cache._retirement
        retired()
        for name in ('_key','_size','_context','_fbo','_resolved','_blitter','_bind'):
            self.assertIsNone(getattr(self.cache,name))
        self.cache._bind=Mock();self.draw()
        current=self.cache._fbo;key=self.cache._key
        retired()  # Old-generation signal cannot clear its replacement.
        self.assertIs(self.cache._fbo,current);self.assertEqual(self.cache._key,key)
        self.assertEqual(self.render.call_count,2)

    def test_sample_capability_refusal_composes_cached_ordinary_pixels_without_native_call(self):
        from contextlib import contextmanager
        from mpf.toolhead import ToolheadFrameCache as module
        from mpf.toolhead.ToolheadGLState import SampleStateUnavailable
        @contextmanager
        def unavailable(*args):
            raise SampleStateUnavailable('core4 unavailable')
            yield
        with patch.object(module,'preserved_samples',unavailable):
            for _ in range(20):
                status=self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'pose',self.render,
                    requested_samples=4)
                self.assertIn('core4 unavailable',status)
        self.render.assert_called_once()
        self.assertEqual(self.blitter.blit.call_count,20)
        @contextmanager
        def broken_restore(*args):
            try:yield
            finally:raise RuntimeError('restore failed')
        with patch.object(module,'preserved_samples',broken_restore), \
                patch.object(self.cache,'_draw',return_value='native') as drawing:
            with self.assertRaisesRegex(RuntimeError,'restore failed'):
                self.cache.draw(self.gl,requested_samples=4)
            drawing.assert_called_once()  # No ordinary retry after mutations.

    def test_sample_target_refusal_does_not_reallocate_on_every_unrelated_redraw(self):
        from contextlib import nullcontext
        from mpf.toolhead import ToolheadFrameCache as module
        class Refused:
            attempts=0
            def __init__(self,*args):
                type(self).attempts+=1
                raise module.SampleTargetUnavailable('D32F sample pattern unsupported')
        def draw():
            return self.cache.draw(self.gl,self.camera,self.bounds,self.transform,'pose',self.render,
                requested_samples=4)
        with patch.object(module,'ToolheadSampleTarget',Refused), \
                patch.object(module,'preserved_samples',side_effect=lambda *args:nullcontext()):
            self.render.side_effect=lambda *args:self.assertNotIn(0x8E51,[call.args[0] for call in self.gl.glDisable.call_args_list])
            for _ in range(20):self.assertIn('sample pattern unsupported',draw())
            self.assertEqual(Refused.attempts,1);self.render.assert_called_once()
            self.factory.assert_called_once()
            self.cache._retirement();self.cache._bind=Mock()
            draw();self.assertEqual(Refused.attempts,2)
            self.bounds*=2
            draw();self.assertEqual(Refused.attempts,3)
        class RestorationFault:
            def __init__(self,*args):raise RuntimeError('Host graphics state could not be restored')
        self.cache._retirement();self.cache._bind=Mock();self.render.reset_mock()
        with patch.object(module,'ToolheadSampleTarget',RestorationFault), \
                patch.object(module,'preserved_samples',side_effect=lambda *args:nullcontext()):
            with self.assertRaisesRegex(RuntimeError,'could not be restored'):draw()
        self.render.assert_not_called();self.assertIsNone(self.cache._sample_refusal)

    def test_sample_graphics_budget_refuses_before_allocating_or_invoking_native(self):
        from contextlib import nullcontext
        from mpf.toolhead import ToolheadFrameCache as module
        self.viewport=(0,0,2000,2000)
        self.render.side_effect=lambda *args:self.assertNotIn(0x809E,[call.args[0] for call in self.gl.glDisable.call_args_list])
        class Target:
            attempts=0
            def __init__(self,*args):
                type(self).attempts+=1
                raise AssertionError('Budget did not refuse before allocation')
        with patch.object(module,'ToolheadSampleTarget',Target), \
                patch.object(module,'preserved_samples',side_effect=lambda *args:nullcontext()):
            status=self.cache.draw(self.gl,self.camera,None,self.transform,'pose',self.render,
                requested_samples=4)
        self.assertIn('graphics memory budget',status)
        self.assertEqual(Target.attempts,0);self.render.assert_called_once()





    def test_missing_map_and_offscreen_crop_never_report_ray_tracing(self):
        result = self.cache.draw(self.gl, self.camera, self.bounds, self.transform, 'pose', self.render,
                                )
        self.assertEqual(result, 'Ordinary shading')
        self.viewport = (0, 0, 0, 600)
        self.assertIsNone(self.cache.draw(self.gl, self.camera, self.bounds, self.transform, 'pose', self.render))

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
        self.assertIs(args[5], self.render.call_args.args[0])


    def test_full_view_seed_keeps_original_projection_object_and_dtype(self):
        seed = Mock(return_value=True)
        self.cache.draw(self.gl,self.camera,None,self.transform,'pose',self.render,depth_seed=seed)
        self.assertIs(seed.call_args.args[5],self.camera)
        self.assertIs(self.render.call_args.args[0],self.camera)

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
