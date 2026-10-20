# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead
# 2026-10-10 [80c4b26] Complete directional reflections and reduce rendering overhead
# 2026-10-08 [e2f777d] Add toolhead materials, scene reflections and fan animation

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

    def test_clipped_shutter_matches_full_half_float_pixels_and_rebuilds_after_failure(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl = self.gl
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        static = QOpenGLFramebufferObject(96, 64, fmt); static.bind()
        gl.glDisable(gl.GL_SCISSOR_TEST); gl.glColorMask(True, True, True, True); gl.glDepthMask(True)
        gl.glClearColor(.13,.27,.39,.7); gl.glClearDepth(.4)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        read = procedure(self.context, 'glReadPixels', None, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        def pixels(target):
            target.bind(); data = (ctypes.c_ubyte * (96*64*8))()
            read(0,0,96,64,0x1908,0x140B,data)
            return bytes(data)
        def depth_pixels(target):
            target.bind(); data = (ctypes.c_float * (96*64))()
            read(0,0,96,64,0x1902,0x1406,data)
            return bytes(data)
        clipped, full = ToolheadRotorRender(), ToolheadRotorRender()
        visits = []; phase = [0]; region = (32, 16, 32, 32)
        def renderer(owner):
            def render(camera, fraction):
                visits.append((owner is clipped, owner._update_region))
                # Static glass/foreground outside the rotor is deliberately
                # different from the immutable seed, and must remain averaged.
                gl.glClearColor(.17+max(0,phase[0]-3)*.01,.11,.29,.6); gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                gl.glEnable(gl.GL_SCISSOR_TEST)
                gl.glScissor(36+phase[0],20,12,16)
                gl.glClearColor(.2+fraction*.1,.3+phase[0]*.01,.4,.5)
                gl.glClearDepth(.8); gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            return render
        for step in range(8):
            phase[0] = step
            gl.glEnable(gl.GL_SCISSOR_TEST); gl.glScissor(3,4,5,6)
            actual = clipped.combine(gl,static,None,(96,64,0),renderer(clipped),blurred=True,
                cache_key=('pose',step), background_key='scene' if step<4 else ('moving scene',step),region=region)
            self.assertTrue(gl.glIsEnabled(gl.GL_SCISSOR_TEST))
            self.assertEqual(tuple(gl.glGetIntegerv(0x0C10)),(3,4,5,6))
            expected = full.combine(gl,static,None,(96,64,0),renderer(full),blurred=True)
            self.assertEqual(pixels(actual),pixels(expected))
            self.assertEqual(depth_pixels(clipped._work),depth_pixels(full._work))
        self.assertEqual([r for owned,r in visits if owned], [None,region,region]+[region]*9+[None,region,region]*4)
        self.assertIsNone(clipped._update_region)
        # Nine later pose passes cover 1/6 of the original target each.
        self.assertEqual(region[2]*region[3]*6,96*64)
        def failure(*args): raise RuntimeError('clipped failure')
        with self.assertRaisesRegex(RuntimeError,'clipped failure'):
            clipped.combine(gl,static,None,(96,64,0),failure,blurred=True,
                cache_key='failed',background_key=('moving scene',7),region=region)
        self.assertIsNone(clipped._background); self.assertIsNone(clipped._update_region)
        for background in ('scene','new scene'):
            visits.clear()
            clipped.combine(gl,static,None,(96,64,0),renderer(clipped),blurred=True,
                background_key=background,region=region)
            self.assertEqual([r for owned,r in visits if owned],[None,region,region])
        visits.clear()
        clipped.combine(gl,static,None,(96,64,0),renderer(clipped),blurred=False,
            background_key='new scene',region=region)
        self.assertEqual([r for owned,r in visits if owned],[None])
        visits.clear()
        clipped.combine(gl,static,None,(96,64,0),renderer(clipped),blurred=False,
            background_key='new scene',region=(16,16,48,32))
        self.assertEqual([r for owned,r in visits if owned],[None])
        self.assertEqual(int(gl.glGetError()),0)

    def test_recycled_scratch_pairs_match_fresh_colour_and_depth_after_resizing(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl = self.gl
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        owner = ToolheadRotorRender()
        read = procedure(self.context, 'glReadPixels', None, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        names = {}
        blitter = None
        for step, size in enumerate(((96,64), (128,96), (96,64), (128,96))):
            width, height = size
            static = QOpenGLFramebufferObject(width, height, fmt); static.bind()
            gl.glViewport(0,0,width,height); gl.glDisable(gl.GL_SCISSOR_TEST)
            gl.glColorMask(True,True,True,True); gl.glDepthMask(True)
            gl.glClearColor(.1+step*.01,.2,.3,.6); gl.glClearDepth(.3)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            def render(camera, fraction, step=step):
                gl.glClearColor(.2+step*.03,.3+fraction*.1,.4,.5)
                gl.glClearDepth(.4+step*.05)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            actual = owner.combine(gl,static,None,(*size,0),render,blurred=True,cache_key=step)
            if blitter is None: blitter = owner._blitter
            self.assertIs(owner._blitter,blitter)
            handles = owner._work.handle(), owner._result.handle()
            if size in names: self.assertEqual(handles,names[size])
            names[size] = handles
            fresh = ToolheadRotorRender()
            expected = fresh.combine(gl,static,None,(*size,0),render,blurred=True)
            def pixels(target, kind, storage, width=width, height=height):
                target.bind(); values = storage()
                read(0,0,width,height,kind[0],kind[1],values)
                return bytes(values)
            self.assertEqual(pixels(actual,(0x1908,0x140B),ctypes.c_ushort*(width*height*4)),
                pixels(expected,(0x1908,0x140B),ctypes.c_ushort*(width*height*4)))
            self.assertEqual(pixels(owner._work,(0x1902,0x1406),ctypes.c_float*(width*height)),
                pixels(fresh._work,(0x1902,0x1406),ctypes.c_float*(width*height)))
            fresh.release_targets()
        self.assertEqual(owner._pool_hits,2)
        self.assertLessEqual(owner._pool_bytes,48 << 20)
        owner._retirement()
        self.assertIsNone(owner._context)
        owner.release_targets()
        self.assertEqual(owner._pool_bytes,0)
        self.assertFalse(owner._pool)
        self.assertEqual(int(gl.glGetError()),0)

    def test_scratch_pool_is_bounded_and_rejects_invalid_storage(self):
        from unittest.mock import Mock
        owner = ToolheadRotorRender()
        for width in range(1000, 1700, 100):
            owner._size = (width, 1000, 0)
            owner._work = Mock(attachment=Mock(return_value=1), isValid=Mock(return_value=True))
            owner._result = Mock(isValid=Mock(return_value=True))
            owner.retire_for_ordinary(16,16,0)
            self.assertLessEqual(owner._pool_bytes,48 << 20)
            self.assertLessEqual(len(owner._pool),4)
        key, pair = next(iter(owner._pool.items()))
        pair[0].isValid.return_value = False
        self.assertIsNone(owner._take_targets(*key))
        self.assertNotIn(key,owner._pool)
        owner._size = (16,16,0)
        owner._work = Mock(attachment=Mock(return_value=1),isValid=Mock(return_value=True))
        owner._result = Mock(isValid=Mock(return_value=False))
        owner.retire_for_ordinary(32,32,0)
        self.assertIsNone(owner._take_targets((16,16,0),False,1))
        for _ in range(2):
            owner._size = (16,16,0)
            owner._work = Mock(attachment=Mock(return_value=1),isValid=Mock(return_value=True))
            owner._result = None
            owner.retire_for_ordinary(32,32,0)
        self.assertIsNotNone(owner._take_targets((16,16,0),True,1))
        owner._size = (4096,4096,0)
        owner._work = Mock(); owner._result = Mock()
        keys = tuple(owner._pool)
        owner.retire_for_ordinary(16,16,0)
        self.assertEqual(tuple(owner._pool),keys)
        owner.release_targets()
        self.assertFalse(owner._pool)

    def test_large_viewport_refuses_shutter_allocation_before_creating_storage(self):
        owner=ToolheadRotorRender()
        with self.assertRaisesRegex(RuntimeError,'memory budget'):
            owner.combine(None,None,None,(7680,4320,4),None,blurred=True)
        self.assertIsNone(owner._work)
        self.assertIsNone(owner._result)

    def test_retiring_explicit_shutter_preserves_unknown_close_until_verified(self):
        from mpf.toolhead import ToolheadRotorRender as module
        class Target:
            fails = True
            def close(self):
                if self.fails: raise RuntimeError('unretired shutter')
        target = Target()
        owner = ToolheadRotorRender()
        owner._work = target
        owner._size = (20, 10, 4)
        owner._result = owner._resolve = object()
        with patch.object(module, 'ToolheadSampleTarget', Target):
            with self.assertRaisesRegex(RuntimeError, 'unretired shutter'): owner.release_targets()
            self.assertIs(owner._work, target)
            self.assertEqual(owner._size, (20, 10, 4))
            target.fails = False
            owner.release_targets()
        for name in ('_work','_resolve','_result','_size','_key'):
            self.assertIsNone(getattr(owner, name))

    def test_explicit_shutter_rejects_foreign_context_and_preserves_failed_resize_lease(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSampleTarget import ToolheadSampleTarget
        target = ToolheadSampleTarget(self.gl, 19, 13)
        owner = ToolheadRotorRender()
        try:
            with patch('PyQt6.QtGui.QOpenGLContext.currentContext', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'context unavailable'):
                    owner.combine(self.gl,target,None,(19,13,4),None,blurred=False)
            with self.assertRaisesRegex(RuntimeError, 'exact live owned target'):
                owner.combine(self.gl,target,None,(19,13,0),None,blurred=False)
            owner.combine(self.gl,target,None,(19,13,4),lambda *_:None,blurred=False)
            old, size = owner._work, owner._size
            with patch.object(ToolheadSampleTarget,'close',side_effect=RuntimeError('unknown resize retirement')):
                with self.assertRaisesRegex(RuntimeError,'unknown resize retirement'):
                    owner.combine(self.gl,target,None,(20,13,4),lambda *_:None,blurred=False)
            self.assertIs(owner._work,old)
            self.assertEqual(owner._size,size)
            owner.release_targets()
            self.assertIsNone(owner._work)
        finally: target.close()

    def test_same_size_ordinary_ms_and_explicit_float_depth_shutters_never_share_work(self):
        if not self.available:self.skipTest('Offscreen OpenGL unavailable')
        from mpf.toolhead.ToolheadSampleTarget import ToolheadSampleTarget
        gl=self.gl
        fmt=QOpenGLFramebufferObjectFormat();fmt.setSamples(4)
        fmt.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        ordinary=QOpenGLFramebufferObject(19,13,fmt)
        sampled=ToolheadSampleTarget(gl,19,13)
        owner=ToolheadRotorRender()
        def render(*args):
            gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
            gl.glClearColor(.2,.3,.4,1);gl.glClearDepth(.5)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
        for static,expected in ((ordinary,False),(sampled,True),(ordinary,False)):
            static.bind();gl.glViewport(0,0,19,13);render()
            owner.combine(gl,static,None,(19,13,4),render,blurred=False,cache_key=('same',))
            self.assertEqual(isinstance(owner._work,ToolheadSampleTarget),expected)
        self.assertEqual(int(gl.glGetError()),0)
        retired=owner._retirement;retired()
        self.assertIsNone(owner._work);self.assertIsNone(owner._key)
        owner.combine(gl,ordinary,None,(19,13,4),render,blurred=False,cache_key=('same',))
        current=owner._work;retired();self.assertIs(owner._work,current)
        sampled.close()

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

    def test_sharp_rotation_preserves_static_colour_depth_and_reuses_its_target(self):
        if not self.available: self.skipTest('Offscreen OpenGL unavailable')
        gl = self.gl
        fmt = QOpenGLFramebufferObjectFormat(); fmt.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        static = QOpenGLFramebufferObject(16, 16, fmt); static.bind()
        gl.glDisable(gl.GL_SCISSOR_TEST); gl.glColorMask(True, True, True, True); gl.glDepthMask(True)
        gl.glClearColor(.25, .5, .75, 1.); gl.glClearDepth(.25)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        original = static.toImage()
        read = procedure(self.context, 'glReadPixels', None, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        owner = ToolheadRotorRender(); poses = []
        gl.glEnable(gl.GL_SCISSOR_TEST); gl.glScissor(4, 4, 1, 1)
        gl.glColorMask(False, True, False, False); gl.glDepthMask(False)
        def render(camera, fraction):
            value = ctypes.c_float(); read(0, 0, 1, 1, 0x1902, 0x1406, ctypes.byref(value))
            self.assertAlmostEqual(value.value, .25, places=5)
            poses.append(fraction)
            gl.glClearColor(.5, .25, .125, .5); gl.glClearDepth(.75)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
        first = owner.sharp(gl, static, None, (16, 16, 0), render, cache_key=('base', 0))
        work = owner._work
        self.assertTrue(gl.glIsEnabled(gl.GL_SCISSOR_TEST))
        self.assertEqual(tuple(map(bool, gl.glGetBooleanv(0x0C23))), (False, True, False, False))
        self.assertFalse(gl.glGetBooleanv(0x0B72))
        colour = first.toImage().pixelColor(0, 0)
        self.assertAlmostEqual(colour.redF(), 1., delta=.01)
        self.assertAlmostEqual(colour.blueF(), .25, delta=.01)
        self.assertIs(first, work); self.assertIsNone(owner._result); self.assertIsNone(owner._blitter)
        self.assertEqual(static.toImage(), original)
        self.assertIs(owner.sharp(gl, static, None, (16, 16, 0), render, cache_key=('base', 0)), first)
        self.assertEqual(poses, [0.])
        owner.sharp(gl, static, None, (16, 16, 0), render, cache_key=('base', 1))
        self.assertIs(owner._work, work); self.assertEqual(poses, [0., 0.])
        static.bind(); value = ctypes.c_float(); read(0, 0, 1, 1, 0x1902, 0x1406, ctypes.byref(value))
        self.assertAlmostEqual(value.value, .25, places=5)
        self.assertEqual(static.toImage(), original)
        owner.combine(gl, static, None, (16, 16, 0), render, blurred=False, cache_key=('base', 1))
        self.assertIsNotNone(owner._result)
        owner.sharp(gl, static, None, (16, 16, 0), render, cache_key=('base', 1))
        self.assertIsNone(owner._result); self.assertEqual(static.toImage(), original)
        self.assertEqual(int(gl.glGetError()), 0)
