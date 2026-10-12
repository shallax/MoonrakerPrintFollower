"""Real buffer-texture borrowing preserves pixels, depth and owner storage."""
import ctypes
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
from PyQt6.QtGui import QMatrix4x4, QVector3D
from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLShaderProgram, QOpenGLShader, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLVertexArrayObject
from tests import test_toolhead_environment_gl as gl_fixture
from tests.test_toolhead_instanced_shadow import Mesh, FIXTURE
from mpf.toolhead.ToolheadInstancedShadow import ToolheadInstancedShadow
from mpf.toolhead.ToolheadPathGeometry import ToolheadPathGeometry
from mpf.toolhead.ToolheadGLState import procedure


class Shader:
    def __init__(self):self.program=QOpenGLShaderProgram();self.values={};self.bindings={}
    def setVertexShader(self,source):return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,source)
    def setFragmentShader(self,source):return self.program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,source)
    def build(self):
        if not self.program.link():raise RuntimeError(self.program.log())
    def addBinding(self,name,key):self.bindings[name]=key
    def setUniformValue(self,name,value):self.values[name]=value
    def bind(self):
        result=self.program.bind()
        for name,value in self.values.items():self.program.setUniformValue(name,value)
        return result
    def updateBindings(self,**values):
        for name,key in self.bindings.items():self.program.setUniformValue(name,values[key])
    def release(self):self.program.release()


class SharedIndexGLTests(unittest.TestCase):
    setUpClass=classmethod(gl_fixture.EnvironmentGLTests.setUpClass.__func__)
    tearDownClass=classmethod(gl_fixture.EnvironmentGLTests.tearDownClass.__func__)

    def test_owned_and_borrowed_draws_match_and_close_preserves_indexed_draw(self):
        if not self.available:self.skipTest('Offscreen Qt OpenGL unavailable')
        gl=self.gl;mesh=Mesh();mesh.indices.flags.writeable=False
        mesh.vertices[:]=[[-.7,0,0],[.7,0,0]]
        mesh.attributes['line_dimensions']['value'][:]=[.2,.2]
        body=mesh.vertices.tobytes()+mesh.colors.tobytes()+b''.join(mesh.getAttribute(name)['value'].tobytes() for name in mesh.attributeNames())
        vertex=QOpenGLBuffer();self.assertTrue(vertex.create());self.assertTrue(vertex.bind());vertex.allocate(body,len(body));vertex.release()
        vao=QOpenGLVertexArrayObject();self.assertTrue(vao.create());vao.bind()
        shared=QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer);self.assertTrue(shared.create());self.assertTrue(shared.bind());shared.allocate(mesh.indices.tobytes(),8)
        geometry=ToolheadPathGeometry(mesh);geometry._context=self.context;geometry._vertex_mesh=mesh
        geometry._index_buffer=shared;geometry._index_bytes=8;geometry._index_generation=1
        fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        fbo=QOpenGLFramebufferObject(64,64,fmt);self.assertTrue(fbo.isValid());fbo.bind()
        gl.glViewport(0,0,64,64);gl.glEnable(0x0B71);gl.glDepthFunc(0x0201);gl.glEnable(0x0B44);gl.glCullFace(0x0405);gl.glFrontFace(0x0901)
        gl.glDisable(0x0BE2);gl.glDisable(0x0C11);gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
        matrix=QMatrix4x4();camera=NS(getInverseWorldTransformation=lambda:matrix,getProjectionMatrix=lambda:matrix,getCameraLightPosition=lambda:QVector3D(0,2,2))
        opacity=QMatrix4x4(*([1.]*16));opacity.getData=lambda:np.ones((4,4),np.float32)
        view=NS(getShowTravelMoves=lambda:False,getShowSkin=lambda:True,getShowInfill=lambda:True,getShowHelpers=lambda:True,getExtruderOpacities=lambda:opacity)
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        def pixels():
            rgba=(ctypes.c_ubyte*(64*64*4))();depth=(ctypes.c_float*(64*64))()
            read(0,0,64,64,0x1908,0x1401,rgba);read(0,0,64,64,0x1902,0x1406,depth)
            return bytes(rgba),bytes(depth)
        def clear():gl.glClearColor(0,0,0,0);gl.glClearDepth(1.);gl.glClear(0x4100)
        helper=ToolheadInstancedShadow()
        try:
            with patch.dict(sys.modules,{'UM.View.GL.OpenGL':NS(OpenGL=NS(getInstance=lambda:NS(createVertexBuffer=lambda source:vertex))),
                    'UM.View.GL.ShaderProgram':NS(ShaderProgram=Shader)}):
                clear();self.assertTrue(helper.render(mesh,FIXTURE,camera,matrix,0,2,view,gl));expected=pixels()
                self.assertTrue(any(expected[0]));old_buffer=helper._storage['lines']
                clear();self.assertTrue(helper.render(mesh,FIXTURE,camera,matrix,0,2,view,gl,index_owner=geometry))
                self.assertEqual(pixels(),expected);self.assertIs(helper._storage['lines'],shared)
                self.assertEqual(old_buffer.bufferId(),0);self.assertTrue(gl.glIsBuffer(shared.bufferId()))
                self.assertEqual(int(gl.glGetIntegerv(0x8895)),shared.bufferId())
                helper.close();self.assertTrue(gl.glIsBuffer(shared.bufferId()))
                # The ordinary indexed path remains usable after borrower retirement.
                program=QOpenGLShaderProgram()
                self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,'#version 410\nvoid main(){gl_Position=vec4(gl_VertexID==0?-.7:.7,0,0,1);}'))
                self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,'#version 410\nout vec4 color;void main(){color=vec4(1);}'))
                self.assertTrue(program.link());program.bind();clear()
                procedure(self.context,'glDrawElements',None,ctypes.c_uint,ctypes.c_int,ctypes.c_uint,ctypes.c_void_p)(1,2,0x1405,None)
                self.assertTrue(any(pixels()[0]));program.release();self.assertEqual(gl.glGetError(),0)
        finally:
            helper.close();shared.destroy();vertex.destroy();vao.destroy();fbo.release()
