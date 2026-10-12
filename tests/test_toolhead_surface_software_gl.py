"""Real deferred lighting through the software-scissor compatibility path."""
import configparser
import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PyQt6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject)
from tests import test_toolhead_environment_gl as fixture
from mpf.toolhead.ToolheadGLState import procedure, preserved_state
from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache


class SurfaceSoftwareGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.EnvironmentGLTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass.__func__(cls)

    def test_real_shade_software_viewport_matches_full_reference_with_depth(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl; width,height=64,48; origin=(7,11); clip=(21,9,31,27)
        class Shader:
            def __init__(self): self.program=QOpenGLShaderProgram();self.values={}
            def setVertexShader(self,text):return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,text)
            def setFragmentShader(self,text):return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,text)
            def build(self):
                if not self.program.link():raise RuntimeError(self.program.log())
            def setUniformValue(self,name,value):self.values[name]=value
            def bind(self):
                if not self.program.bind():return False
                for name,value in self.values.items():
                    if isinstance(value,(list,tuple)):self.program.setUniformValue(name,*value)
                    else:self.program.setUniformValue(name,value)
                return True
            def release(self):self.program.release()
        class Renderer:
            def __init__(self,software):self.software=software
            def glGetString(self,key):return 'Apple Software Renderer' if self.software else 'Hardware reference'
            def __getattr__(self,name):return getattr(gl,name)
        config=configparser.ConfigParser(interpolation=None)
        config.read(Path(__file__).parents[1]/'mpf/toolhead/scene-lighting.shader')
        caches=[ToolheadSurfaceCache('#version 410\n'+config['shaders']['fragment41core'],depth_only=True) for _ in range(2)]
        vertex='#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);}'
        def program(fragment):
            result=QOpenGLShaderProgram()
            self.assertTrue(result.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,vertex))
            self.assertTrue(result.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,fragment))
            self.assertTrue(result.link(),result.log());return result
        capture=program('#version 410\nlayout(location=0)out vec4 p;layout(location=1)out vec4 n;layout(location=2)out vec4 c;'
            'void main(){p=vec4(gl_FragCoord.xy/32.,0,.5);n=vec4(0,0,1,2);c=vec4(.25,.5,.75,1);gl_FragDepth=.5;}')
        seed=program('#version 410\nout vec4 c;void main(){c=vec4(.125,.25,.375,0);gl_FragDepth=gl_FragCoord.x<39.? .25:.75;}')
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        host=QOpenGLFramebufferObject(width+14,height+22,fmt)
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        matrix=SimpleNamespace(getData=lambda:np.eye(4))
        camera=SimpleNamespace(getProjectionMatrix=lambda:matrix,getInverseWorldTransformation=lambda:matrix,
            getWorldPosition=lambda:[0.,0.,5.])
        def lights(shader):
            for name,value in {'u_attachedCount':1,'u_lightOpacity':1.,
                    'u_attachedPosition[0]':[1.,1.,4.],'u_attachedDirection[0]':[0.,0.,-1.],
                    'u_attachedColour[0]':[.3,.4,.5],'u_attachedRange[0]':10.}.items():shader.setUniformValue(name,value)
        node=SimpleNamespace(apply_attached_lights=lights,scene_lighting_effects=lambda:(True,True),
            scene_light_bounds=lambda:(((1,1,4),10),))
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        def images():
            rgba=np.empty((height+22,width+14,4),np.uint8);depth=np.empty(rgba.shape[:2],np.float32)
            read(0,0,rgba.shape[1],rgba.shape[0],0x1908,0x1401,rgba.ctypes.data)
            read(0,0,rgba.shape[1],rgba.shape[0],0x1902,0x1406,depth.ctypes.data)
            return rgba,depth
        def draw(program):
            gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_ALWAYS);gl.glDepthMask(True)
            gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
            program.bind();vao.bind();gl.glDrawArrays(4,0,3);vao.release();program.release()
        dither=bool(gl.glIsEnabled(0x0BD0))
        try:
            with preserved_state(gl,self.context),patch.dict('sys.modules',{'UM.View.GL.ShaderProgram':SimpleNamespace(ShaderProgram=Shader)}):
                gl.glDisable(0x0BD0);results=[]
                for software,cache in enumerate(caches):
                    host.bind();gl.glViewport(*origin,width,height);gl.glClearDepth(1.)
                    cache.prepare(Renderer(bool(software)),'same',(),lambda:draw(capture),lambda *_:None)
                    self.assertEqual(cache._software_scissor,bool(software))
                    host.bind();gl.glViewport(*origin,width,height);gl.glDisable(gl.GL_SCISSOR_TEST)
                    gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
                    gl.glClearColor(.125,.25,.375,0);gl.glClearDepth(.75)
                    gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT);draw(seed)
                    initial,initial_depth=images()
                    # Full reference is independent of the driver's faulty small
                    # scissor; compare its illuminated pixels against the ROI path.
                    rect=clip if software else (0,0,width,height)
                    with patch('mpf.toolhead.ToolheadSurfaceCache.light_scissor',return_value=rect):
                        cache.shade(gl,camera,node)
                    self.assertEqual(tuple(gl.glGetIntegerv(0x0BA2)),(*origin,width,height))
                    rgba,depth=images();np.testing.assert_array_equal(depth,initial_depth)
                    results.append(rgba)
                    if software:
                        x,y,w,h=clip;x+=origin[0];y+=origin[1]
                        outside=np.ones(rgba.shape[:2],bool);outside[y:y+h,x:x+w]=False
                        np.testing.assert_array_equal(rgba[outside],initial[outside])
                        np.testing.assert_array_equal(rgba[y:y+h,x:x+w],results[0][y:y+h,x:x+w])
                        self.assertTrue(np.any(rgba[y:y+h,x:x+w,:3]>initial[y:y+h,x:x+w,:3]))
                        np.testing.assert_array_equal(rgba[y:y+h,x:39],initial[y:y+h,x:39])
                self.assertEqual(int(gl.glGetError()),0)
        finally:
            (gl.glEnable if dither else gl.glDisable)(0x0BD0)
            host.bind();vao.destroy();capture.removeAllShaders();seed.removeAllShaders()
            for cache in caches:cache._fbo=None;cache._programs.clear();cache._vao=None
