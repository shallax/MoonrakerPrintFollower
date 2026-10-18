# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead

"""Exact completed-depth crops using actual Qt framebuffer storage."""
import ctypes
import unittest

import numpy as np
from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
from tests import test_toolhead_environment_gl as fixture
from mpf.toolhead.ToolheadGLState import procedure
from mpf.toolhead.ToolheadSimulationCache import ToolheadSimulationCache


class SimulationCropGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture.EnvironmentGLTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls): fixture.EnvironmentGLTests.tearDownClass.__func__(cls)

    def test_integer_crop_matches_full_depth_and_preserves_target_colour(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl = self.gl
        fmt = QOpenGLFramebufferObjectFormat()
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        source = QOpenGLFramebufferObject(64,48,fmt)
        cache = ToolheadSimulationCache()
        cache._context, cache._fbo, cache._size = self.context, source, (64,48)
        cache._key, cache._completed = ('scene', (0.,0.,0.,0.)), 12
        cache._functions(self.context)
        read = procedure(self.context, 'glReadPixels', None, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        source.bind(); gl.glViewport(0,0,64,48); gl.glDisable(0x0C11); gl.glDepthMask(True)
        gl.glClearDepth(.23); gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        gl.glEnable(0x0C11); gl.glScissor(20,15,13,11)
        gl.glClearDepth(.67); gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        reference = np.empty((48,64),np.float32)
        read(0,0,64,48,0x1902,0x1406,reference.ctypes.data)
        try:
            for region in ((0,0,64,48), (16,12,24,20), (51,37,13,11)):
                x,y,w,h = region
                target = QOpenGLFramebufferObject(w,h,fmt)
                target.bind(); gl.glDisable(0x0C11); gl.glViewport(-x,-y,64,48)
                gl.glClearColor(.2,.4,.6,.8); gl.glColorMask(True,True,True,True)
                gl.glClearDepth(1.); gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
                colour = np.empty((h,w,4),np.uint8)
                read(0,0,w,h,0x1908,0x1401,colour.ctypes.data)
                gl.glEnable(0x0C11); gl.glScissor(1,2,3,4)
                self.assertTrue(cache.try_copy_depth(gl,target,'scene',12,source_region=region))
                self.assertTrue(gl.glIsEnabled(0x0C11))
                self.assertEqual(tuple(gl.glGetIntegerv(0x0C10)), (1,2,3,4))
                self.assertEqual(tuple(gl.glGetIntegerv(0x0BA2)), (-x,-y,64,48))
                actual = np.empty((h,w),np.float32)
                read(0,0,w,h,0x1902,0x1406,actual.ctypes.data)
                np.testing.assert_array_equal(actual,reference[y:y+h,x:x+w])
                actual_colour = np.empty_like(colour)
                read(0,0,w,h,0x1908,0x1401,actual_colour.ctypes.data)
                np.testing.assert_array_equal(actual_colour,colour)
            self.assertEqual(int(gl.glGetError()),0)
        finally:
            gl.glDisable(0x0C11)
            cache._fbo = None
            source.release()
