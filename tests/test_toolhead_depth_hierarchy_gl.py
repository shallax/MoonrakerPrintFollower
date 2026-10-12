"""Actual raw worker hierarchy owner preserves host state and publishes exact ranges."""
import ctypes
import os
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np
os.environ['QT_QPA_PLATFORM']='cocoa' if sys.platform=='darwin' else 'offscreen'
from tests import test_toolhead_environment_gl as gl_fixture
from mpf.toolhead.ToolheadDepthHierarchy import DepthHierarchy, SIZE, TILE_SIZE
from mpf.toolhead.ToolheadGLState import procedure


class DepthHierarchyGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The common fixture raises when required GL is unavailable, otherwise
        # records availability so each test is honestly counted as skipped.
        gl_fixture.EnvironmentGLTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        gl_fixture.EnvironmentGLTests.tearDownClass.__func__(cls)

    def setUp(self):
        if not self.available: self.skipTest('Core4 context unavailable')

    def test_actual_owner_reduces_matching_pair_and_restores_bindings(self):
        gl,context=self.gl,self.context
        U,I,P=ctypes.c_uint,ctypes.c_int,ctypes.c_void_p
        source=U();procedure(context,'glGenTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(source))
        gl.glActiveTexture(0x84C0+5);gl.glBindTexture(0x8513,source.value)
        gl.glTexParameteri(0x8513,0x2801,0x2600);gl.glTexParameteri(0x8513,0x2800,0x2600)
        image=procedure(context,'glTexImage2D',None,U,I,I,I,I,I,U,U,P)
        faces=[];rng=np.random.default_rng(7721)
        for face in range(6):
            data=rng.uniform(-.1,1.1,(512,512)).astype('f4')
            data[:32,:32]=1.;data[32:64,:32]=np.nan
            data[64:96,:32]=np.inf;data[96:128,:32]=-.1
            data[128:160,:32]=np.nextafter(np.float32(1),np.float32(0))
            faces.append(data)
            image(0x8515+face,0,0x822E,512,512,0,0x1903,0x1406,P(data.ctypes.data))
        host_texture=U()
        procedure(context,'glGenTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(host_texture))
        gl.glBindTexture(0x0DE1,host_texture.value)
        buffer=U();procedure(context,'glGenBuffers',None,I,ctypes.POINTER(U))(1,ctypes.byref(buffer))
        gl.glBindBuffer(0x88EC,buffer.value)
        gl.glViewport(3,4,41,43);gl.glEnable(gl.GL_SCISSOR_TEST);gl.glScissor(1,2,3,4)
        owner=None
        try:
            # RawProgram only needs this unused superclass import; all its GL
            # construction, compilation and uniforms use the production code.
            with patch.dict(sys.modules,{'UM.View.GL.ShaderProgram':SimpleNamespace(ShaderProgram=object)}):
                owner=DepthHierarchy.optional(gl,context)
            self.assertIsNotNone(owner)
            self.assertEqual(gl.glGetIntegerv(0x88EF),buffer.value)
            timer=U();procedure(context,'glGenQueries',None,I,ctypes.POINTER(U))(1,ctypes.byref(timer))
            elapsed=[];walls=[]
            try:
                for _ in range(4):
                    before=time.perf_counter()
                    procedure(context,'glBeginQuery',None,U,U)(0x88BF,timer.value)
                    self.assertTrue(owner.build(gl,source.value,owner.names[1]))
                    procedure(context,'glEndQuery',None,U)(0x88BF)
                    gl.glFinish();walls.append((time.perf_counter()-before)*1000)
                    nanos=ctypes.c_uint64()
                    procedure(context,'glGetQueryObjectui64v',None,U,U,ctypes.POINTER(ctypes.c_uint64))(
                        timer.value,0x8866,ctypes.byref(nanos))
                    elapsed.append(nanos.value/1e6)
                print('Production six-face hierarchy build: warm GPU %.3f ms; inclusive wall %.3f ms' %
                    (np.median(elapsed[1:]),np.median(walls[1:])))
            finally:procedure(context,'glDeleteQueries',None,I,ctypes.POINTER(U))(1,ctypes.byref(timer))
            self.assertEqual(tuple(gl.glGetIntegerv(0x0BA2)),(3,4,41,43))
            self.assertTrue(gl.glIsEnabled(gl.GL_SCISSOR_TEST))
            self.assertEqual(gl.glGetIntegerv(0x8514),source.value)
            self.assertEqual(gl.glGetIntegerv(0x8069),host_texture.value)
            gl.glFinish();gl.glBindTexture(0x8513,owner.names[1])
            for face in range(6):
                actual=np.empty((SIZE,SIZE,2),np.float32)
                procedure(context,'glGetTexImage',None,U,I,U,U,P)(0x8515+face,0,0x8227,0x1406,P(actual.ctypes.data))
                cells=faces[face].reshape(SIZE,TILE_SIZE,SIZE,TILE_SIZE).transpose(0,2,1,3).reshape(SIZE,SIZE,TILE_SIZE*TILE_SIZE)
                valid=(cells>=0)&(cells<1)
                expected=np.stack([np.where(valid,cells,1.).min(2),np.where(valid,cells,0.).max(2)],2).astype('f4')
                np.testing.assert_array_equal(actual.view('u4'),expected.view('u4'))
            self.assertEqual(gl.glGetError(),0)
        finally:
            gl.glBindBuffer(0x88EC,0);gl.glFinish()
            if owner is not None:owner.close()
            procedure(context,'glDeleteTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(source))
            procedure(context,'glDeleteTextures',None,I,ctypes.POINTER(U))(1,ctypes.byref(host_texture))
            procedure(context,'glDeleteBuffers',None,I,ctypes.POINTER(U))(1,ctypes.byref(buffer))
