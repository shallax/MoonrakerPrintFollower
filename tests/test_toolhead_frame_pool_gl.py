"""Real GL parity of freshly rendered crops backed by recycled private FBOs."""
import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PyQt6.QtOpenGL import (QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject)
from tests import test_toolhead_environment_gl as fixture
from mpf.toolhead.ToolheadFrameCache import ToolheadFrameCache
from mpf.toolhead.ToolheadGLState import procedure


class FramePoolGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.EnvironmentGLTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass.__func__(cls)

    def test_alternating_crops_reuse_names_with_exact_fresh_colour_and_depth(self):
        self._alternating_crops(0)

    def test_multisample_pairs_preserve_sample_positions_resolved_colour_and_depth(self):
        self._alternating_crops(4)

    def test_additive_composition_rectangle_matches_full_quad_exactly(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        host=QOpenGLFramebufferObject(128,96,fmt)
        matrix=lambda data:SimpleNamespace(getData=lambda:data)
        identity=matrix(np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:identity,getProjectionMatrix=lambda:identity,
            getWorldPosition=lambda:(0,0,0),getCameraLightPosition=lambda:(0,1,0))
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        cache=ToolheadFrameCache(depth_only=True)
        def render(_camera):
            gl.glEnable(0x0C11);gl.glScissor(20,20,20,12)
            gl.glClearColor(.17,.31,.43,0.);gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        def deliver(rect,enabled):
            host.bind();gl.glViewport(20,10,80,60);gl.glDisable(0x0C11)
            gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
            gl.glClearColor(.11,.23,.37,.61);gl.glClearDepth(.73)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
            gl.glScissor(45,25,30,25)
            (gl.glEnable if enabled else gl.glDisable)(0x0C11)
            cache.draw(gl,camera,None,identity,'same',render,additive=True,composition_rect=rect)
            self.assertEqual(tuple(gl.glGetIntegerv(0x0C10)),(45,25,30,25))
            self.assertEqual(bool(gl.glIsEnabled(0x0C11)),enabled)
            host.bind()
            colour=(ctypes.c_ubyte*(128*96*4))();depth=(ctypes.c_float*(128*96))()
            read(0,0,128,96,0x1908,0x1401,colour);read(0,0,128,96,0x1902,0x1406,depth)
            return bytes(colour),bytes(depth)
        try:
            with patch.dict('sys.modules',{'UM.Math.Matrix':SimpleNamespace(Matrix=matrix)}):
                for enabled in (False,True):
                    self.assertEqual(deliver((0,0,80,60),enabled),deliver((16,16,32,24),enabled))
                self.assertEqual(cache._size[:2],(80,60))
                self.assertEqual(int(gl.glGetError()),0)
        finally:
            host.bind();cache.release_targets();gl.glDisable(0x0C11)

    def test_hemisphere_scissor_preserves_actual_light_shader_rgb(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        import configparser
        from pathlib import Path
        from PyQt6.QtGui import QVector3D
        from mpf.toolhead.ToolheadSurfaceCache import light_scissor
        gl=self.gl; width,height=256,192
        host=QOpenGLFramebufferObject(width,height)
        config=configparser.ConfigParser(interpolation=None)
        config.read(Path(__file__).parents[1]/'mpf/toolhead/scene-lighting.shader')
        source='#version 410\n'+config['shaders']['fragment41core']
        program=QOpenGLShaderProgram();vao=QOpenGLVertexArrayObject()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float plane;out vec3 f_vertex;out vec3 f_normal;out vec4 f_color;'
            'void main(){vec2 p=gl_VertexID==0?vec2(-1,-1):gl_VertexID==1?vec2(3,-1):vec2(-1,3);'
            'gl_Position=vec4(p,0,1);f_vertex=vec3(p,plane);f_normal=vec3(0,0,1);f_color=vec4(.5,.6,.7,1);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,source))
        self.assertTrue(program.link());self.assertTrue(vao.create())
        matrix=SimpleNamespace(getData=lambda:np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:matrix,getProjectionMatrix=lambda:matrix)
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        pixels=np.empty((height,width,4),dtype=np.uint8)
        from mpf.toolhead.ToolheadGLState import preserved_state
        try:
            with preserved_state(gl,self.context):
                host.bind();gl.glViewport(0,0,width,height);gl.glDisable(gl.GL_DEPTH_TEST)
                gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE);gl.glColorMask(True,True,True,True)
                program.bind();vao.bind()
                for name,value in (('u_depthOnly',0),('u_attachedCount',1),('u_orthographic',1)):
                    program.setUniformValue(name,value)
                program.setUniformValue('u_lightOpacity',1.)
                program.setUniformValue('u_viewDirection',QVector3D(0,0,1))
                program.setUniformValue('u_attachedColour[0]',QVector3D(1,.8,.6))
                program.setUniformValue('u_attachedRange[0]',.65)
                for position,direction in (((.1,.2,.2),(0,-1,0)),((-.2,.1,.2),(.6,-.8,0)),((.3,-.2,.2),(0,0,-1))):
                    program.setUniformValue('u_attachedPosition[0]',QVector3D(*position))
                    program.setUniformValue('u_attachedDirection[0]',QVector3D(*direction))
                    rect=light_scissor(camera,[(position,.65)],width,height,directions=[direction])
                    self.assertLess(rect[2]*rect[3],width*height)
                    for plane in (-.3,0.,.19):
                        program.setUniformValue('plane',plane)
                        images=[]
                        for cropped in (False,True):
                            gl.glDisable(gl.GL_SCISSOR_TEST);gl.glClearColor(0,0,0,0);gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                            if cropped: gl.glEnable(gl.GL_SCISSOR_TEST);gl.glScissor(*rect)
                            gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                            read(0,0,width,height,0x1908,0x1401,pixels.ctypes.data);images.append(pixels[:,:,:3].copy())
                        np.testing.assert_array_equal(*images)
                vao.release();program.release()
                self.assertEqual(int(gl.glGetError()),0)
        finally: vao.destroy();program.removeAllShaders()

    def test_surface_region_preserves_full_resolution_attributes_and_depth_on_escape(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache, surface_fragment
        gl = self.gl
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        host = QOpenGLFramebufferObject(1040,800,fmt)
        full, cropped = ToolheadSurfaceCache(depth_only=True), ToolheadSurfaceCache(depth_only=True)
        program = QOpenGLShaderProgram()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float phase;out vec3 f_vertex;out vec3 f_normal;out vec4 f_color;'
            'void main(){vec2 p=gl_VertexID==0?vec2(-1.,-1.):gl_VertexID==1?vec2(3.,-1.):vec2(-1.,3.);'
            'f_vertex=vec3(p*123.,phase);f_normal=vec3(.2,.3,1.);f_color=vec4(p*.12+.4,.7,1.);'
            'gl_Position=vec4(p, .5-phase*.1,1.);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,surface_fragment()))
        self.assertTrue(program.link())
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        read_buffer=procedure(self.context,'glReadBuffer',None,ctypes.c_uint)
        calls={id(full):0,id(cropped):0}
        def draw(owner, phase):
            calls[id(owner)]+=1
            gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LESS);gl.glDisable(gl.GL_CULL_FACE)
            program.bind();program.setUniformValue('phase',float(phase));program.setUniformValue('u_surfaceKind',2)
            vao.bind();gl.glDrawArrays(gl.GL_TRIANGLES,0,3);vao.release();program.release()
        def image(owner,rect,attachment):
            x,y,w,h=rect;rx,ry,_,_=owner._region
            owner._fbo.bind()
            channels=1 if attachment is None else 4
            values=np.empty((h,w,channels),dtype=np.float32)
            if attachment is not None: read_buffer(0x8CE0+attachment)
            read(x-rx,y-ry,w,h,0x1902 if attachment is None else 0x1908,0x1406,values.ctypes.data)
            return values
        try:
            for phase,rect in enumerate(((350,300,80,90),(370,310,80,90),(800,550,100,100),(50,40,100,100))):
                for owner in (full,cropped):
                    host.bind();gl.glViewport(8,12,1024,768)
                    owner.prepare(gl,'camera',(phase,),lambda owner=owner,phase=phase:draw(owner,phase),
                        lambda _old,_new,owner=owner,phase=phase:draw(owner,phase),rect=rect if owner is cropped else None)
                    self.assertEqual(tuple(map(int,gl.glGetIntegerv(0x0BA2))),(8,12,1024,768))
                if phase==0:self.assertLess(cropped._size[0]*cropped._size[1],1024*768//2)
                for attachment in (0,1,2,None):
                    np.testing.assert_allclose(image(full,rect,attachment),image(cropped,rect,attachment),rtol=0,atol=2e-5)
                # Translation of the cropped source must preserve host depth.
                host.bind();gl.glViewport(8,12,1024,768);gl.glDisable(0x0C11);gl.glDepthMask(True)
                gl.glClearDepth(1.);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                cropped.copy_depth(gl,rect=rect)
                x,y,w,h=rect;actual=np.empty((h,w,1),dtype=np.float32)
                read(x+8,y+12,w,h,0x1902,0x1406,actual.ctypes.data)
                np.testing.assert_array_equal(actual,image(full,rect,None))
            self.assertEqual(calls[id(full)],4)
            self.assertEqual(calls[id(cropped)],4)
            self.assertEqual(int(gl.glGetError()),0)
        finally:
            full._fbo=cropped._fbo=None;vao.destroy();program.removeAllShaders();host.release()

    def test_surface_depth_region_matches_full_copy_with_offset_viewport(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSurfaceCache import ToolheadSurfaceCache
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        source=QOpenGLFramebufferObject(64,48,fmt);host=QOpenGLFramebufferObject(128,96,fmt)
        cache=ToolheadSurfaceCache(depth_only=True);cache._fbo=source
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        source.bind();gl.glDisable(0x0C11);gl.glDepthMask(True);gl.glClearDepth(.23);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        gl.glEnable(0x0C11);gl.glScissor(20,20,10,10);gl.glClearDepth(.67);gl.glClear(gl.GL_DEPTH_BUFFER_BIT);gl.glDisable(0x0C11)
        def deliver(rect):
            host.bind();gl.glViewport(13,17,64,48);gl.glClearDepth(1.);gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
            cache.copy_depth(gl,rect=rect)
            data=(ctypes.c_float*(128*96))();read(0,0,128,96,0x1902,0x1406,data)
            return np.ctypeslib.as_array(data).reshape(96,128).copy()
        full=deliver(None);clipped=deliver((16,16,24,20))
        np.testing.assert_array_equal(full[33:53,29:53],clipped[33:53,29:53])
        outside=np.ones((96,128),bool);outside[33:53,29:53]=False
        self.assertTrue(np.all(clipped[outside]==1.))
        self.assertEqual(int(gl.glGetError()),0)
        host.bind();cache._fbo=None

    def _alternating_crops(self,samples):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil if samples else QOpenGLFramebufferObject.Attachment.Depth)
        fmt.setSamples(samples)
        host=QOpenGLFramebufferObject(128,96,fmt)
        if samples and host.format().samples()!=samples:self.skipTest("Requested host multisampling unavailable")
        program=QOpenGLShaderProgram()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float phase;void main(){vec2 p=gl_VertexID==0?vec2(-.9,-.7):gl_VertexID==1?vec2(.8,-.6):vec2(-.2,.8);gl_Position=vec4(p+vec2(phase*.03,0.),phase*.05,1.);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nuniform float phase;out vec4 colour;void main(){colour=vec4(gl_FragCoord.xy/128.,.1+phase*.07,1.);}'))
        self.assertTrue(program.link())
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create())
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        matrix=lambda data:SimpleNamespace(getData=lambda:data)
        identity=matrix(np.eye(4))
        camera=SimpleNamespace(getInverseWorldTransformation=lambda:identity,
            getProjectionMatrix=lambda:identity,getWorldPosition=lambda:(0,0,0),getCameraLightPosition=lambda:(0,1,0))
        cache=ToolheadFrameCache(depth_only=True);handles={};reference=None
        sample_position=procedure(self.context,'glGetMultisamplefv',None,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p) if samples else None
        try:
            with patch.dict('sys.modules',{'UM.Math.Matrix':SimpleNamespace(Matrix=matrix)}):
                for phase,crop in enumerate(((0,0,64,48),(16,8,96,64),(32,16,64,48),(0,0,96,64),(0,0,64,48))):
                    def render(_camera, phase=phase):
                        gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
                        gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LEQUAL)
                        program.bind();program.setUniformValue('phase',float(phase));vao.bind()
                        try:gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                        finally:vao.release();program.release()
                    def deliver(owner, phase=phase, crop=crop, render=render):
                        host.bind();gl.glViewport(0,0,128,96);gl.glClearDepth(1.)
                        owner.draw(gl,camera,None,identity,phase,render,_crop=crop,_compose=False)
                        owner._fbo.bind();w,h=crop[2:]
                        positions=[]
                        if samples:
                            for index in range(samples):
                                position=(ctypes.c_float*2)();sample_position(0x8E50,index,position)
                                positions.append(tuple(position))
                        colour=(ctypes.c_ubyte*(w*h*4))();depth=(ctypes.c_float*(w*h))()
                        (owner._resolved or owner._fbo).bind()
                        read(0,0,w,h,0x1908,0x1401,colour)
                        if samples:
                            depth_format=QOpenGLFramebufferObjectFormat()
                            depth_format.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                            depth_resolve=QOpenGLFramebufferObject(w,h,depth_format)
                            QOpenGLFramebufferObject.blitFramebuffer(depth_resolve,owner._fbo,buffers=gl.GL_DEPTH_BUFFER_BIT)
                            depth_resolve.bind()
                        else:owner._fbo.bind()
                        read(0,0,w,h,0x1902,0x1406,depth)
                        return bytes(colour),bytes(depth),positions

                    actual=deliver(cache)
                    handle=(cache._fbo.handle(),cache._resolved.handle() if cache._resolved else None)
                    if crop[2:] in handles:self.assertEqual(handle,handles[crop[2:]])
                    else:handles[crop[2:]]=handle
                    reference=ToolheadFrameCache(depth_only=True)
                    self.assertEqual(actual,deliver(reference))
                    reference.release_targets();reference=None
                self.assertEqual(cache._pool_hits,3)
                self.assertEqual(len(cache._pool),1)
                self.assertEqual(int(gl.glGetError()),0)
        finally:
            host.bind();cache.release_targets()
            if reference is not None:reference.release_targets()
            vao.destroy();program.removeAllShaders()
