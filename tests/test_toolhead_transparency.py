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

    def test_resize_retires_both_old_targets_before_first_new_allocation(self):
        self.assertTrue(self.draw())
        self.head.size().width.return_value = 128
        def allocate(*args):
            if self.fbos.call_count == 3:
                for name in ('_front', '_merged', '_size'):
                    self.assertIsNone(getattr(self.helper, name), name)
            return self.front if len(args) == 3 else self.merged
        self.fbos.side_effect = allocate
        self.assertTrue(self.draw())

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


class SampledTransparencyGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_toolhead_environment_gl import EnvironmentGLTests
        EnvironmentGLTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from tests.test_toolhead_environment_gl import EnvironmentGLTests
        EnvironmentGLTests.tearDownClass.__func__(cls)

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
