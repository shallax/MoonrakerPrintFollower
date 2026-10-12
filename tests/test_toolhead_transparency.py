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
        self.head.format().attachment.return_value = 2
        self.head.format().internalTextureFormat.return_value = 0x8058
        self.head.format().mipmap.return_value = False
        self.head.size().width.return_value = 64
        self.head.size().height.return_value = 96
        self.front, self.merged = Mock(), Mock()
        self.front.format.return_value = self.head.format()
        self.fbos = Mock(return_value=self.front)
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
        self.assertEqual(self.fbos.call_count, 1)
        self.shader_factory.assert_called_once()
        self.batch.addItem.assert_called_with('transform', mesh='mesh', uniforms={'opacity':.4}, normal_transformation='normal')
        self.assertEqual(self.batch_factory.call_args.kwargs['range'], (1,3))
        self.assertTrue(self.batch_factory.call_args.kwargs['backface_cull'])
        self.gl.glBlendFuncSeparate.assert_any_call(self.gl.GL_SRC_ALPHA, self.gl.GL_ONE_MINUS_SRC_ALPHA,
            self.gl.GL_ONE, self.gl.GL_ONE_MINUS_SRC_ALPHA)
        self.fbos.blitFramebuffer.assert_any_call(self.front, self.head, self.gl.GL_DEPTH_BUFFER_BIT, 0x2600)
        self.assertEqual(self.fbos.blitFramebuffer.call_count, 2)
        self.head.texture.assert_not_called()
        self.assertIsNone(self.helper._merged)
        self.gl.glBlendFuncSeparate.assert_any_call(0x0304, 0x0303, 0, 1)
        self.head.size().width.return_value = 128
        self.assertTrue(self.draw())
        self.assertEqual(self.fbos.call_count, 2)
        self.gl.glViewport.assert_called_with(0,0,128,96)

    def test_failed_destination_bind_never_samples_or_draws_into_foreground(self):
        self.head.bind.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'destination could not bind'):
            self.draw()
        self.gl.glDrawArrays.assert_not_called()
        self.shader.bind.assert_not_called()
        self.gl.glBindTexture.assert_not_called()
        self.head.texture.assert_not_called()
        self.assertEqual(self.fbos.blitFramebuffer.call_count, 1)
        self.head.bind.assert_called()

    def test_clipped_foreground_restricts_every_stage_and_restores_scissor_on_failure(self):
        previous = (3, 4, 10, 11)
        self.gl.glGetIntegerv.side_effect = lambda key: previous if key == 0x0C10 else 1
        self.gl.glIsEnabled.return_value = False
        clip = (8, 12, 16, 24)
        def copied(destination, destination_rect, source, source_rect, buffers, filter_):
            self.assertIs(destination,self.front);self.assertIs(source,self.head)
            self.assertEqual(destination_rect.getRect(),clip);self.assertEqual(source_rect.getRect(),clip)
            self.assertEqual((buffers,filter_),(self.gl.GL_DEPTH_BUFFER_BIT,0x2600))
            self.gl.glDisable.assert_called_with(0x0C11)
        self.fbos.blitFramebuffer.side_effect=copied
        self.assertTrue(self.helper.draw(self.gl, self.head, self.camera, [self.source], clip=clip))
        self.assertEqual([call.args for call in self.gl.glScissor.call_args_list],
            [clip, clip, clip, clip, previous])
        self.gl.glDisable.assert_called_with(0x0C11)
        self.gl.glIsEnabled.return_value = True
        self.batch.render.side_effect = RuntimeError('foreground failed')
        with self.assertRaisesRegex(RuntimeError, 'foreground failed'):
            self.helper.draw(self.gl, self.head, self.camera, [self.source], clip=clip)
        self.gl.glScissor.assert_called_with(*previous)
        self.gl.glEnable.assert_called_with(0x0C11)

    def test_clipped_foreground_rejects_invalid_rectangles_and_multisample_targets(self):
        for clip in ((-1, 0, 10, 10), (0, 0, 0, 1), (0, 0, 65, 96), (0, 0, 1.), (0, 0, 1., 2)):
            with self.subTest(clip=clip), self.assertRaisesRegex(ValueError, 'exact target'):
                self.helper.draw(self.gl, self.head, self.camera, [self.source], clip=clip)
        self.gl.glScissor.assert_not_called()
        self.head.format().samples.return_value = 4
        self.assertFalse(self.helper.draw(self.gl, self.head, self.camera, [self.source], clip=(0, 0, 64, 96)))

    def test_direct_blend_restores_factors_equations_even_when_draw_fails(self):
        values = {0x80C9: 0x0302, 0x80C8: 0x0303, 0x80CB: 1, 0x80CA: 0x0303,
                  0x8009: 0x800A, 0x883D: 0x800B}
        self.gl.glGetIntegerv.side_effect = values.__getitem__
        self.gl.glDrawArrays.side_effect = RuntimeError('draw failed')
        with self.assertRaisesRegex(RuntimeError, 'draw failed'): self.draw()
        self.gl.glBlendFuncSeparate.assert_called_with(0x0302, 0x0303, 1, 0x0303)
        self.gl.glBlendEquationSeparate.assert_called_with(0x800A, 0x800B)
        self.gl.glDisable.assert_any_call(self.gl.GL_BLEND)
        self.head.texture.assert_not_called()
        self.head.bind.assert_called()

    def test_empty_surfaces_and_multisampling_do_not_allocate(self):
        self.assertTrue(self.helper.draw(self.gl, self.head, self.camera, []))
        self.head.format().samples.return_value = 4
        self.assertFalse(self.draw())
        self.fbos.assert_not_called()

    def test_resize_retires_both_old_targets_before_first_new_allocation(self):
        self.assertTrue(self.draw())
        self.head.size().width.return_value = 128
        def allocate(*args):
            if self.fbos.call_count == 2:
                for name in ('_front', '_merged', '_size'):
                    self.assertIsNone(getattr(self.helper, name), name)
            return self.front if len(args) == 3 else self.merged
        self.fbos.side_effect = allocate
        self.assertTrue(self.draw())

    def test_exact_foreground_targets_reuse_storage_but_clear_and_reseed_each_draw(self):
        first, second = self.front, Mock()
        second.format.return_value = self.head.format()
        self.fbos.side_effect = [first, second]
        self.assertTrue(self.draw())
        self.helper.retire_for_ordinary(128, 96)
        self.helper.retire_for_ordinary(128, 96)
        self.assertEqual(self.helper._pool_bytes, 64*96*12)
        self.head.size().width.return_value = 128
        self.assertTrue(self.draw())
        self.head.size().width.return_value = 64
        self.assertTrue(self.draw())
        self.assertIs(self.helper._front, first)
        self.assertEqual(self.fbos.call_count, 2)
        self.assertEqual(self.helper._pool_hits, 1)
        self.assertEqual(self.helper._pool_bytes, 128*96*12)
        self.assertEqual(self.fbos.blitFramebuffer.call_count, 3)
        self.assertEqual(self.gl.glClear.call_count, 3)
        self.helper.release_targets()
        self.assertFalse(self.helper._pool)
        self.assertEqual(self.helper._pool_bytes, 0)
        self.assertIsNone(self.helper._front)

    def test_foreground_pool_checks_full_format_and_rejects_invalid_storage(self):
        self.assertTrue(self.draw())
        original = self.helper._ordinary_key
        self.head.format().attachment.return_value = 1
        self.assertTrue(self.draw())
        self.assertEqual(self.fbos.call_count, 2)
        self.assertIn(original, self.helper._pool)
        self.helper._pool[original][0].isValid.return_value = False
        self.assertIsNone(self.helper._take_front(original))
        self.assertEqual(self.helper._pool_bytes, 0)
        self.front.isValid.return_value = True
        self.head.format().attachment.return_value = 2
        self.head.format().internalTextureFormat.return_value = 0x881A
        self.assertTrue(self.draw())
        self.assertEqual(self.fbos.call_count, 3)
        self.head.format().mipmap.return_value = True
        self.assertTrue(self.draw())
        self.assertEqual(self.fbos.call_count, 4)

    def test_foreground_pool_bounds_and_unknown_formats(self):
        for width in (64, 65, 66, 67, 68, 69):
            self.head.size().width.return_value = width
            self.assertTrue(self.draw())
        self.assertEqual(len(self.helper._pool), 4)
        self.assertEqual(self.helper._pool_bytes, sum(w*96*12 for w in (65,66,67,68)))
        self.helper._size = (4096, 4096)
        self.helper._ordinary_key = (4096, 4096, 0, 2, 0x8058, False)
        previous = self.helper._pool_bytes
        self.helper._stash_front()
        self.assertEqual(self.helper._pool_bytes, previous)
        for internal, mipmap, samples in ((0x881A, False, 0), (0x8058, True, 0), (0x8058, False, 4)):
            self.helper._front = Mock()
            self.helper._ordinary_key = (64, 96, samples, 2, internal, mipmap)
            self.helper._stash_front()
            self.assertEqual(self.helper._pool_bytes, previous)

    def test_failed_new_foreground_allocation_keeps_idle_storage_without_publishing_old_pixels(self):
        self.assertTrue(self.draw())
        self.head.size().width.return_value = 128
        invalid = Mock()
        invalid.isValid.return_value = False
        self.fbos.return_value = invalid
        self.gl.glDrawArrays.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'composition unavailable'): self.draw()
        self.assertIsNone(self.helper._front)
        self.assertEqual(self.helper._pool_bytes, 64*96*12)
        self.gl.glDrawArrays.assert_not_called()
        self.head.size().width.return_value = 64
        self.assertTrue(self.draw())
        self.assertIs(self.helper._front, self.front)
        self.assertEqual(self.fbos.call_count, 2)

    def test_foreground_pool_byte_budget_evicts_and_sample_transition_drains_ordinary(self):
        self.head.size().height.return_value = 1024
        for width in (1024, 1025, 1026, 1027):
            self.head.size().width.return_value = width
            self.assertTrue(self.draw())
            self.assertLessEqual(self.helper._pool_bytes, 24 << 20)
        self.assertEqual(len(self.helper._pool), 1)
        self.helper._clear_ordinary_targets()
        self.assertEqual(self.helper._pool_bytes, 0)
        self.assertIsNone(self.helper._ordinary_key)

    def test_sample_target_retirement_failure_retains_the_unretired_owner(self):
        first, second = Mock(), Mock()
        self.helper._sample_front, self.helper._sample_merged = first, second
        self.helper._sample_size = (64, 96, 4)
        first.close.side_effect = RuntimeError('retirement failed')
        with self.assertRaisesRegex(RuntimeError, 'retirement failed'):
            self.helper._retire_sample_targets()
        self.assertIs(self.helper._sample_front, first)
        self.assertIs(self.helper._sample_merged, second)
        second.close.assert_not_called()
        first.close.side_effect = None
        self.helper._retire_sample_targets()
        self.assertIsNone(self.helper._sample_front)
        self.assertIsNone(self.helper._sample_merged)
        self.assertIsNone(self.helper._sample_size)

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

    def test_failed_surface_or_composition_draw_releases_resources_and_rebinds_head(self):
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


class SampledTransparencyGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_toolhead_environment_gl import EnvironmentGLTests
        EnvironmentGLTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from tests.test_toolhead_environment_gl import EnvironmentGLTests
        EnvironmentGLTests.tearDownClass.__func__(cls)

    def test_ordinary_clip_matches_full_foreground_inside_and_preserves_exterior_and_depth(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        import ctypes
        import numpy as np
        from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        from mpf.toolhead.ToolheadGLState import procedure
        gl, context = self.gl, self.context
        width, height = 19, 13
        vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
        class Shader:
            def __init__(self): self.program = QOpenGLShaderProgram(); self.values = {}
            def setVertexShader(self, text): return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, text)
            def setFragmentShader(self, text): return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, text)
            def build(self):
                if not self.program.link(): raise RuntimeError(self.program.log())
            def setUniformValue(self, name, value): self.values[name] = value
            def bind(self):
                if not self.program.bind(): raise RuntimeError(self.program.log())
                for name, value in self.values.items(): self.program.setUniformValue(name, value)
            def release(self): self.program.release()
        front = Shader(); self.assertTrue(front.setVertexShader(vertex))
        self.assertTrue(front.setFragmentShader('#version 410\nout vec4 colour;void main(){colour=vec4(.15,.3,.05,.5);gl_FragDepth=.4;}'))
        front.build()
        vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create())
        depth_before_draw=[]
        class Batch:
            RenderType = SimpleNamespace(Transparent=2)
            def __init__(self, *args, **kwargs): self.options = kwargs
            def addItem(self, *args, **kwargs): pass
            def render(self, camera):
                depth_before_draw.append(pixels(helper._front,True))
                gl.glEnable(gl.GL_DEPTH_TEST); gl.glDepthMask(False); gl.glEnable(gl.GL_BLEND)
                self.options['state_setup_callback'](gl)
                front.bind(); vao.bind()
                try: gl.glDrawArrays(4, 0, 3)
                finally: vao.release(); front.release()
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        targets = [QOpenGLFramebufferObject(width, height, fmt) for _ in range(3)]
        def seed(target):
            self.assertTrue(target.bind()); gl.glViewport(0, 0, width, height)
            gl.glDisable(0x0C11); gl.glColorMask(True, True, True, True); gl.glDepthMask(True)
            gl.glClearColor(.3, .2, .1, .7); gl.glClearDepth(.6)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        read = procedure(context, 'glReadPixels', None, *([ctypes.c_int]*4), ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        def pixels(target, depth=False):
            self.assertTrue(target.bind())
            result = np.empty((height, width) if depth else (height, width, 4), np.float32 if depth else np.uint8)
            read(0, 0, width, height, 0x1902 if depth else 0x1908, 0x1406 if depth else 0x1401, result.ctypes.data)
            return result
        batch = SimpleNamespace(shader=front, renderMode=4, backfaceCull=False, renderRange=None,
            items=[dict(transformation=None, mesh=None)])
        helper = ToolheadTransparency()
        try:
            for target in targets: seed(target)
            original, original_depth = pixels(targets[0]), pixels(targets[0], True)
            with patch.dict('sys.modules', {'UM.View.GL.ShaderProgram': SimpleNamespace(ShaderProgram=Shader),
                    'UM.View.RenderBatch': SimpleNamespace(RenderBatch=Batch)}):
                self.assertTrue(helper.draw(gl, targets[1], None, [batch]))
                expected = pixels(targets[1])
                first_front = helper._front
                larger = QOpenGLFramebufferObject(width+3, height+2, fmt)
                seed(larger)
                self.assertTrue(helper.draw(gl, larger, None, [batch]))
                self.assertEqual(helper._pool_bytes, width*height*12)
                seed(targets[1])
                self.assertTrue(helper.draw(gl, targets[1], None, [batch]))
                self.assertIs(helper._front, first_front)
                self.assertEqual(helper._pool_hits, 1)
                np.testing.assert_array_equal(pixels(targets[1]), expected)
                np.testing.assert_array_equal(pixels(targets[1], True).view(np.uint32), original_depth.view(np.uint32))
                for clip in ((0, 0, 7, 5), (12, 8, 7, 5), (3, 2, 13, 9)):
                    # Poison every previous foreground depth cell. A failed or
                    # wrongly positioned copy must not pass through stale depth.
                    helper._front.bind();gl.glDisable(0x0C11);gl.glDepthMask(True)
                    gl.glClearDepth(.1);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                    seed(targets[2]); gl.glEnable(0x0C11); gl.glScissor(1, 1, 2, 2)
                    self.assertTrue(helper.draw(gl, targets[2], None, [batch], clip=clip))
                    self.assertTrue(gl.glIsEnabled(0x0C11))
                    self.assertEqual(tuple(gl.glGetIntegerv(0x0C10)), (1, 1, 2, 2))
                    x, y, w, h = clip; reference = original.copy()
                    np.testing.assert_array_equal(depth_before_draw[-1][y:y+h,x:x+w].view(np.uint32),
                        original_depth[y:y+h,x:x+w].view(np.uint32))
                    reference[y:y+h, x:x+w] = expected[y:y+h, x:x+w]
                    np.testing.assert_array_equal(pixels(targets[2]), reference)
                    np.testing.assert_array_equal(pixels(targets[2], True).view(np.uint32), original_depth.view(np.uint32))
            self.assertEqual(int(gl.glGetError()), 0)
            helper.release_targets()
        finally:
            gl.glDisable(0x0C11); vao.destroy(); front.program.removeAllShaders()

    def test_each_foreground_depth_sample_precedes_shutter_resolve_and_average(self):
        if not self.available:self.skipTest('Offscreen OpenGL unavailable')
        import ctypes
        import numpy as np
        from PyQt6.QtOpenGL import QOpenGLShader,QOpenGLShaderProgram,QOpenGLVertexArrayObject,QOpenGLFramebufferObject,QOpenGLFramebufferObjectFormat
        from mpf.toolhead.ToolheadSampleTarget import ToolheadSampleTarget,sample_blit
        from PyQt6.QtGui import QVector4D
        from mpf.toolhead.ToolheadRotorRender import ToolheadRotorRender
        from mpf.toolhead.ToolheadGLState import procedure
        gl,context=self.gl,self.context
        width,height=19,13
        def shader(fragment):
            program=QOpenGLShaderProgram()
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
                '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID==1)?3:-1,(gl_VertexID==2)?3:-1);gl_Position=vec4(p,0,1);}'))
            self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment))
            self.assertTrue(program.link(),program.log())
            return program
        class QtShader:
            def __init__(self):self.program=QOpenGLShaderProgram();self.values={};self.bound=False
            def setVertexShader(self,value):return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,value)
            def setFragmentShader(self,value):return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,value)
            def build(self):
                if not self.program.link():raise RuntimeError(self.program.log())
            def setUniformValue(self,name,value):
                self.values[name]=value
                if self.bound:self.program.setUniformValue(name,value)
            def bind(self):
                if not self.program.bind():return False
                self.bound=True
                for name,value in self.values.items():self.program.setUniformValue(name,value)
                return True
            def release(self):self.bound=False;self.program.release()
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        read=procedure(context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        front_shader=shader('#version 410\nout vec4 colour;void main(){colour=vec4(0,.5,0,.5);gl_FragDepth=.5;}')
        export=shader('#version 410\nuniform sampler2DMS source;uniform int sampleIndex;out vec4 colour;void main(){colour=texelFetch(source,ivec2(gl_FragCoord.xy),sampleIndex);}')
        fmt=QOpenGLFramebufferObjectFormat();fmt.setInternalTextureFormat(0x8814)
        output=QOpenGLFramebufferObject(width,height,fmt)
        class Batch:
            RenderType=SimpleNamespace(Transparent=2)
            def __init__(self,*args,**kwargs):self.options=kwargs
            def addItem(self,*args,**kwargs):pass
            def render(self,camera):
                gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthMask(False);gl.glEnable(gl.GL_BLEND)
                self.options['state_setup_callback'](gl)
                front_shader.bind();vao.bind()
                try:gl.glDrawArrays(4,0,3)
                finally:vao.release();front_shader.release()
        batch=SimpleNamespace(shader=front_shader,renderMode=4,backfaceCull=False,renderRange=None,
            items=[dict(transformation=None,mesh=None)])
        head=ToolheadSampleTarget(gl,width,height);reference=ToolheadSampleTarget(gl,width,height)
        helper=ToolheadTransparency();shutter=ToolheadRotorRender()
        seed_shader=shader('#version 410\nuniform vec4 colourValue;uniform float depthValue;out vec4 colour;void main(){colour=colourValue;gl_FragDepth=depthValue;}')
        mask=procedure(context,'glSampleMaski',None,ctypes.c_uint,ctypes.c_uint)
        colour=np.array([np.full((height,width,4),[40+30*s,24,16,255],np.uint8) for s in range(4)])
        depth=np.broadcast_to(np.array([.2,.4,.6,.8],np.float32)[:,None,None],(4,height,width)).copy()
        def seed(target,planes=depth):
            self.assertTrue(target.bind());gl.glViewport(0,0,width,height)
            gl.glColorMask(True,True,True,True);gl.glDisable(gl.GL_SCISSOR_TEST)
            gl.glDisable(0x8E51);gl.glDisable(0x8C36)
            gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthMask(True);gl.glDepthFunc(gl.GL_ALWAYS)
            gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE);gl.glEnable(0x8E51)
            seed_shader.bind();vao.bind()
            try:
                for sample in range(4):
                    seed_shader.setUniformValue('colourValue',QVector4D(*(float(v)/255 for v in colour[sample,0,0])))
                    seed_shader.setUniformValue('depthValue',float(planes[sample,0,0]))
                    mask(0,1<<sample);gl.glDrawArrays(4,0,3)
            finally:
                vao.release();seed_shader.release();gl.glDisable(0x8E51);gl.glDepthFunc(gl.GL_LESS)
        def extract(target):
            output.bind();gl.glViewport(0,0,width,height)
            gl.glDisable(gl.GL_DEPTH_TEST);gl.glDisable(gl.GL_BLEND);gl.glDisable(0x8E51)
            export.bind();vao.bind();export.setUniformValue('source',0);gl.glActiveTexture(0x84C0)
            planes=[]
            try:
                for name in (target.texture(),target.depth_texture()):
                    gl.glBindTexture(0x9100,name);values=[]
                    for sample in range(4):
                        export.setUniformValue('sampleIndex',sample);gl.glDrawArrays(4,0,3)
                        result=np.empty((height,width,4),np.float32)
                        read(0,0,width,height,0x1908,0x1406,result.ctypes.data)
                        values.append(result[::-1].copy())
                    planes.append(np.asarray(values))
            finally:vao.release();export.release();gl.glBindTexture(0x9100,0)
            self.assertEqual(int(gl.glGetError()),0)
            return np.rint(planes[0]*255).astype(np.uint8),planes[1][...,0].copy()
        scopes=patch.dict('sys.modules',{'UM.View.GL.ShaderProgram':SimpleNamespace(ShaderProgram=QtShader),
                                        'UM.View.RenderBatch':SimpleNamespace(RenderBatch=Batch)})
        scopes.start()
        try:
            seed(head);sample_blit(gl,reference,head,buffers=0x4100)
            reference.bind();gl.glViewport(0,0,width,height)
            Batch(None,state_setup_callback=lambda b:(b.glDepthFunc(b.GL_LESS),b.glBlendEquationSeparate(0x8006,0x8006),
                b.glBlendFuncSeparate(b.GL_SRC_ALPHA,b.GL_ONE_MINUS_SRC_ALPHA,b.GL_ONE,b.GL_ONE_MINUS_SRC_ALPHA))).render(None)
            expected,expected_depth=extract(reference)
            head.bind();gl.glViewport(0,0,width,height)
            self.assertTrue(helper.draw(gl,head,None,[batch]))
            actual,returned_depth=extract(head)
            np.testing.assert_array_equal(actual,expected)
            np.testing.assert_array_equal(returned_depth.view(np.uint32),depth.view(np.uint32))
            np.testing.assert_array_equal(returned_depth,expected_depth)
            np.testing.assert_array_equal(actual[:2],colour[:2])
            self.assertTrue(np.any(actual[2:]!=colour[2:]))
            retired=helper._sample_retirement;retired()
            self.assertIsNone(helper._sample_shader);self.assertIsNone(helper._sample_size)
            head.bind();gl.glViewport(0,0,width,height)
            self.assertTrue(helper.draw(gl,head,None,[batch]))
            current=helper._sample_front;retired();self.assertIs(helper._sample_front,current)
            with patch.object(ToolheadSampleTarget,'width',return_value=1536), \
                    patch.object(ToolheadSampleTarget,'height',return_value=2048), \
                    patch.object(ToolheadSampleTarget,'__init__',side_effect=AssertionError('unbounded allocation')):
                with self.assertRaisesRegex(RuntimeError,'combined graphics memory budget'):helper.draw(gl,head,None,[batch])
            resolved=[]
            def pose(camera,fraction):
                planes=np.roll(depth,(-.5,0,.5).index(fraction),axis=0).copy()
                seed(shutter._work,planes)
                self.assertTrue(helper.draw(gl,shutter._work,None,[batch]))
                np.testing.assert_array_equal(shutter._work.read_depth(gl).view(np.uint32),planes.view(np.uint32))
                resolve=QOpenGLFramebufferObject(width,height);sample_blit(gl,resolve,shutter._work)
                resolve.bind();pixels=np.empty((height,width,4),np.float32)
                read(0,0,width,height,0x1908,0x1406,pixels.ctypes.data);resolved.append(pixels.copy())
            seed(head)
            result=shutter.combine(gl,head,None,(width,height,4),pose,blurred=True,cache_key=('complete',))
            result.bind();actual=np.empty((height,width,4),np.float32)
            read(0,0,width,height,0x1908,0x1406,actual.ctypes.data)
            expected=np.float16(sum(np.float16(row/3).astype(np.float32) for row in resolved)).astype(np.float32)
            # Half-float addition is certified to one representable ulp; raw
            # foreground RGBA8/depth above retains exact byte/word assertions.
            self.assertLessEqual(np.max(np.abs(actual-expected)),np.spacing(np.float16(1)))
            self.assertEqual(int(gl.glGetError()),0)
        finally:
            head.close();reference.close();seed_shader.removeAllShaders()
            for target in (helper._sample_front,helper._sample_merged,shutter._work):
                if target is not None:target.close()
            scopes.stop();vao.destroy();front_shader.removeAllShaders();export.removeAllShaders()


class OrdinaryTransparencyBlendGLTests(unittest.TestCase):
    def test_direct_blend_matches_original_rgba_for_every_alpha_pair(self):
        import ast
        import inspect
        import numpy as np
        try:
            import moderngl
        except ImportError:
            self.skipTest('The capture OpenGL runtime is required')
        from tools.capture_toolhead import create_context
        context, dll = create_context()
        resources = []
        try:
            width = height = 512
            rng = np.random.default_rng(7531)
            head = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
            front = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
            head[:, :, 3] = np.tile(np.arange(256, dtype=np.uint8), 1024).reshape(height, width)
            front[:, :, 3] = np.repeat(np.arange(256, dtype=np.uint8), 1024).reshape(height, width)
            for values in (head, front):
                values[:, :, :3] = np.rint(values[:, :, :3].astype(float) * values[:, :, 3:4] / 255).astype(np.uint8)
            tree = ast.parse(inspect.getsource(inspect.getmodule(ToolheadTransparency)))
            fragment = next(call.args[0].value for call in ast.walk(tree)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'setFragmentShader')
            vertex = '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}'
            reference = '''#version 410
uniform sampler2D u_head, u_front;
out vec4 frag_color;
void main(){ivec2 p=ivec2(gl_FragCoord.xy);vec4 h=texelFetch(u_head,p,0),f=texelFetch(u_front,p,0);
frag_color=vec4(h.rgb*(1.-f.a)+f.rgb*h.a,h.a);}'''
            # Dithering may deliberately perturb normalized integer storage.
            # This isolated context is destroyed below, so no host flag changes.
            context.disable_direct(0x0BD0)
            for dtype in ('f1','f4'):
                values = (head,front) if dtype=='f1' else tuple(v.astype(np.float32)/255 for v in (head,front))
                inputs=[]
                for value in values:
                    texture=context.texture((width,height),4,value.tobytes(),dtype=dtype)
                    resources.append(texture);inputs.append(texture)
                outputs=[]
                for source in (reference,fragment):
                    program=context.program(vertex_shader=vertex,fragment_shader=source)
                    resources.append(program);program['u_front']=1
                    if 'u_head' in program:program['u_head']=0
                    texture=context.texture((width,height),4,values[0].tobytes(),dtype=dtype);resources.append(texture)
                    target=context.framebuffer([texture]);resources.append(target)
                    target.use();context.viewport=(0,0,width,height)
                    context.disable(moderngl.DEPTH_TEST|moderngl.CULL_FACE|moderngl.BLEND)
                    for unit,texture in enumerate(inputs):texture.use(unit)
                    if source==fragment:
                        context.enable(moderngl.BLEND);context.blend_equation=moderngl.FUNC_ADD
                        context.blend_func=(moderngl.DST_ALPHA,moderngl.ONE_MINUS_SRC_ALPHA,moderngl.ZERO,moderngl.ONE)
                    vao=context.vertex_array(program,[]);resources.append(vao);vao.render(vertices=3)
                    outputs.append(np.frombuffer(target.read(components=4,alignment=1,dtype=dtype),
                        np.uint8 if dtype=='f1' else np.float32).reshape(height,width,4).copy())
                np.testing.assert_array_equal(outputs[0][...,3],outputs[1][...,3])
                np.testing.assert_array_equal(outputs[1][...,3],values[0][...,3])
                if dtype=='f1':
                    delta=np.abs(outputs[1][...,:3].astype(int)-outputs[0][...,:3].astype(int))
                    self.assertLessEqual(int(delta.max()),1,'RGBA8 RGB may differ by only one quantization step')
                    np.testing.assert_array_equal(outputs[1][head[...,3]==0],0)
                    print('Direct blend RGB8 max LSB:',int(delta.max()))
                else:
                    h,f=values;expected=h[...,:3].astype(float)*(1-f[...,3:4])+f[...,:3].astype(float)*h[...,3:4]
                    for output in outputs:np.testing.assert_allclose(output[...,:3],expected,rtol=0,atol=1e-6)
                    np.testing.assert_allclose(outputs[0],outputs[1],rtol=0,atol=1e-6)
                    print('Direct blend RGBA32F max:',float(np.max(np.abs(outputs[0]-outputs[1]))))
        finally:
            for resource in reversed(resources): resource.release()
            context.release()
            if dll is not None: dll.close()
