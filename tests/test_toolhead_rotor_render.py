"""Real offscreen shutter composition: fresh depth and one premultiplied average."""
import ctypes
from contextlib import contextmanager
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
from tests import test_toolhead_environment_gl as fixture
from mpf.toolhead.ToolheadRotorRender import ToolheadRotorRender
from mpf.toolhead.ToolheadGLState import procedure


class RotorRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.EnvironmentGLTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass.__func__(cls)

    def test_large_viewport_refuses_shutter_allocation_before_creating_storage(self):
        owner=ToolheadRotorRender()
        with self.assertRaisesRegex(RuntimeError,'memory budget'):
            owner.combine(None,None,None,(7680,4320,4),None,blurred=True)
        self.assertIsNone(owner._work)
        self.assertIsNone(owner._result)

    def test_identical_stopped_pose_reuses_pixels_and_changed_scene_or_pose_rebuilds(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl
        format_=QOpenGLFramebufferObjectFormat();format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        static=QOpenGLFramebufferObject(16,16,format_);static.bind()
        gl.glDisable(gl.GL_SCISSOR_TEST);gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
        gl.glClearColor(.1,.2,.3,1);gl.glClearDepth(.25)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
        frames=[];colour=[.2,.4,.6,.5]
        def render(camera,fraction):
            frames.append(fraction)
            gl.glClearColor(*colour);gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        owner=ToolheadRotorRender();camera=SimpleNamespace()
        first=owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('scene',0))
        image=first.toImage()
        with patch('mpf.toolhead.ToolheadRotorRender.preserved_state',side_effect=AssertionError('cached pose touched GL')):
            for _ in range(20):
                self.assertIs(owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('scene',0)),first)
        self.assertEqual(first.toImage(),image)
        self.assertEqual(frames,[0.])
        colour[:]=[.6,.4,.2,.5]
        changed=owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('scene',1))
        self.assertNotEqual(changed.toImage(),image)
        owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('new depth',1))
        # A failed draw must never stamp its partially rendered output as ready.
        with self.assertRaisesRegex(RuntimeError,'pose failed'):
            owner.combine(gl,static,camera,(16,16,0),lambda *_:(_ for _ in ()).throw(RuntimeError('pose failed')),
                          blurred=False,cache_key=('failure',1))
        self.assertIsNone(owner._key)
        owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('failure',1))
        self.assertEqual(len(frames),4)
        from mpf.toolhead.ToolheadGLState import preserved_state
        @contextmanager
        def restoration_failure(*args):
            with preserved_state(*args): yield
            raise RuntimeError('restoration failed')
        with patch('mpf.toolhead.ToolheadRotorRender.preserved_state',restoration_failure), self.assertRaisesRegex(RuntimeError,'restoration failed'):
            owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('restore',1))
        self.assertIsNone(owner._key)
        owner.combine(gl,static,camera,(16,16,0),render,blurred=False,cache_key=('restore',1))
        self.assertEqual(len(frames),6)
        self.assertEqual(int(gl.glGetError()),0)

    def test_each_pose_starts_from_static_depth_and_shutter_does_not_stack_alpha(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl=self.gl
        format_=QOpenGLFramebufferObjectFormat(); format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        static=QOpenGLFramebufferObject(16,16,format_); self.assertTrue(static.isValid()); static.bind()
        gl.glDisable(gl.GL_SCISSOR_TEST); gl.glColorMask(True,True,True,True); gl.glDepthMask(True)
        gl.glClearDepth(.25); gl.glClearColor(.25,.5,.75,1.)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
        original=static.toImage(); static.bind()
        read=procedure(self.context,'glReadPixels',None,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        samples=[]
        gl.glBlendEquationSeparate(0x8007,0x800B)
        def render(camera,fraction):
            self.assertIs(camera,cam)
            depth=ctypes.c_float(); read(0,0,1,1,0x1902,0x1406,ctypes.byref(depth))
            self.assertAlmostEqual(depth.value,.25,places=5)
            samples.append(fraction)
            colour={-.5:(.5,0,0),0.:(0,.5,0),.5:(0,0,.5)}[fraction]
            gl.glClearColor(*colour,.5); gl.glClearDepth(.75)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
        owner=ToolheadRotorRender(); cam=SimpleNamespace()
        result=owner.combine(gl,static,cam,(16,16,0),render,blurred=True)
        colour=result.toImage().pixelColor(8,8)
        self.assertEqual(samples,[-.5,0.,.5])
        self.assertTrue(all(abs(v-85)<=1 for v in (colour.red(),colour.green(),colour.blue())))
        self.assertTrue(abs(colour.alpha()-128)<=1)
        self.assertEqual(static.toImage(),original)
        result=owner.combine(gl,static,cam,(16,16,0),render,blurred=False)
        self.assertTrue(abs(result.toImage().pixelColor(8,8).green()-255)<=1)
        self.assertEqual(int(gl.glGetIntegerv(0x8009)),0x8007)
        self.assertEqual(int(gl.glGetIntegerv(0x883D)),0x800B)
        self.assertEqual(int(gl.glGetError()),0)
