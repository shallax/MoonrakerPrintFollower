# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [48fdc0f] Finish shared path indices and cropped lighting output
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead

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

    def test_two_main_owners_and_fenced_worker_share_indices_until_lease_retirement(self):
        import gc
        import weakref
        from tests.test_toolhead_capture_draw import Matrix
        from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, BufferLease, IndexLease
        from mpf.toolhead.ToolheadCaptureDraw import CapturePaths
        from PyQt6.QtGui import QOpenGLContext, QOffscreenSurface
        if not self.available:self.skipTest('Offscreen Qt OpenGL unavailable')
        gl=self.gl;mesh=Mesh();mesh.attributes={};mesh.hasColors=lambda:False
        mesh.vertices[:]=[[-.7,0,0],[.7,0,0]];mesh.indices.flags.writeable=False
        vertex=QOpenGLBuffer();self.assertTrue(vertex.create());self.assertTrue(vertex.bind())
        vertex.allocate(mesh.vertices.tobytes(),mesh.vertices.nbytes);vertex.release()
        pointer=procedure(self.context,'glVertexAttribPointer',None,ctypes.c_uint,ctypes.c_int,ctypes.c_uint,ctypes.c_ubyte,ctypes.c_int,ctypes.c_void_p)
        shader=Shader()
        self.assertTrue(shader.setVertexShader('#version 410\nin vec3 a_vertex;void main(){gl_Position=vec4(a_vertex,1);}'))
        self.assertTrue(shader.setFragmentShader('#version 410\nout vec4 color;void main(){color=vec4(.3,.6,.9,1);}'));shader.build()
        def attribute(name,kind,offset):
            location=shader.program.attributeLocation(name)
            if location>=0:gl.glEnableVertexAttribArray(location);pointer(location,3,0x1406,0,0,ctypes.c_void_p(offset))
        shader.enableAttribute=attribute
        camera=NS(getInverseWorldTransformation=lambda:Matrix(),getProjectionMatrix=lambda:Matrix(),getWorldPosition=lambda:None,getCameraLightPosition=lambda:None)
        transform=Matrix();fmt=QOpenGLFramebufferObjectFormat();fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        target=QOpenGLFramebufferObject(32,32,fmt);target.bind();gl.glViewport(0,0,32,32)
        gl.glDisable(0x0C11);gl.glDisable(0x0BE2);gl.glEnable(0x0B71);gl.glDepthFunc(0x0201);gl.glDepthMask(True)
        gl.glColorMask(True,True,True,True)
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        def pixels():
            colour=(ctypes.c_ubyte*(32*32*4))();depth=(ctypes.c_float*(32*32))()
            read(0,0,32,32,0x1908,0x1401,colour);read(0,0,32,32,0x1902,0x1406,depth)
            return bytes(colour),bytes(depth)
        def clear():gl.glClearColor(0,0,0,0);gl.glClearDepth(1);gl.glClear(0x4100)
        one=ToolheadPathGeometry(mesh,build_bounds=False);two=ToolheadPathGeometry(mesh,build_bounds=False)
        with patch.dict(sys.modules,{'UM.View.GL.OpenGL':NS(OpenGL=NS(getInstance=lambda:NS(createVertexBuffer=lambda source:vertex))),
                'UM.Math.Matrix':NS(Matrix=Matrix)}):
            clear();one.render(shader,camera,transform,[(0,2)],gl);expected=pixels();self.assertTrue(any(expected[0]))
            clear();two.render(shader,camera,transform,[(0,2)],gl);self.assertEqual(pixels(),expected)
            self.assertIs(one._index_buffer,two._index_buffer)
            allocation=one.shared_index_allocation(self.context);self.assertIs(allocation,two.shared_index_allocation(self.context))
            capture_geometry=ToolheadPathGeometry(mesh,build_bounds=False,index_chunk=32768)
            capture_geometry._element_base=2
            self.assertIsNone(capture_geometry.shared_index_allocation(self.context))
            self.assertIs(capture_geometry.full_source_index_allocation(self.context),allocation)
            self.assertIsNone(capture_geometry.full_source_index_allocation(NS(shareGroup=lambda:object())))
            receipt=IndexLease(allocation.name,allocation.size,2,id(allocation.group),allocation.generation)
            frozen=CaptureMesh(456,mesh.vertices,mesh.indices,None,None,None,())
            lease=BufferLease(vertex.bufferId(),mesh.vertices.nbytes,frozen.layout()[0],receipt)
            # These retained objects stand in for the main-thread input cohort.
            cohort=[allocation,vertex];reference=weakref.ref(allocation)
            one._vaos.clear();two._vaos.clear();del one,two,capture_geometry,allocation;gc.collect()
            self.assertIsNotNone(reference())
            fence=procedure(self.context,'glFenceSync',ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint)(0x9117,0)
            self.assertTrue(fence);gl.glFlush()
            worker=QOpenGLContext();worker.setFormat(self.context.format());worker.setShareContext(self.context)
            self.assertTrue(worker.create());surface=QOffscreenSurface();surface.setFormat(worker.format());surface.create()
            self.assertTrue(worker.makeCurrent(surface));self.assertEqual(id(worker.shareGroup()),receipt.group)
            procedure(worker,'glWaitSync',None,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint64)(fence,0,0xffffffffffffffff)
            procedure(worker,'glDeleteSync',None,ctypes.c_void_p)(fence)
            worker_target=QOpenGLFramebufferObject(32,32,fmt);worker_target.bind();gl.glViewport(0,0,32,32)
            gl.glEnable(0x0B71);gl.glDepthFunc(0x0201);gl.glDepthMask(True)
            paths=CapturePaths(frozen,lease,gl,worker)
            clear();paths.render(shader,camera,transform,[(0,2)],gl);self.assertEqual(pixels(),expected)
            self.assertEqual(paths.index.name,receipt.name);paths.close();self.assertTrue(gl.glIsBuffer(receipt.name))
            worker_target.release();del paths,worker_target;worker.doneCurrent()
            self.assertTrue(self.context.makeCurrent(self.surface))
            foreign=QOpenGLContext();foreign.setFormat(self.context.format());self.assertTrue(foreign.create())
            foreign_surface=QOffscreenSurface();foreign_surface.setFormat(foreign.format());foreign_surface.create()
            self.assertTrue(foreign.makeCurrent(foreign_surface))
            unrelated=QOpenGLBuffer();self.assertTrue(unrelated.create());self.assertTrue(unrelated.bind())
            unrelated.allocate(mesh.indices.tobytes(),8)
            from dataclasses import replace
            colliding=replace(lease,index=replace(receipt,name=unrelated.bufferId()))
            with self.assertRaisesRegex(RuntimeError,'foreign'):CapturePaths(frozen,colliding,gl,foreign)
            self.assertTrue(gl.glIsBuffer(unrelated.bufferId()));unrelated.destroy();foreign.doneCurrent()
            self.assertTrue(self.context.makeCurrent(self.surface))
            cohort.clear();gc.collect();self.assertIsNone(reference())
        vertex.destroy();target.release()
