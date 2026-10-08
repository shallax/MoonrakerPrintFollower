"""Real Qt offscreen targets: complete cube pixels and independent host FBOs."""
import ctypes
import os
import sys
import unittest
# Cocoa supports windowless QOffscreenSurface; Qt's offscreen plugin does not.
if sys.platform == "darwin": os.environ["QT_QPA_PLATFORM"] = "cocoa"
from PyQt6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile, QOpenGLFramebufferObject, QOpenGLVertexArrayObject
from mpf.toolhead.ToolheadEnvironment import CubeStorage, ToolheadEnvironment, ProbeDescriptor, SIZE
from mpf.toolhead.ToolheadGLState import procedure, preserved_state, flush_texture_deletions
from types import SimpleNamespace


class EnvironmentGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        format_ = QSurfaceFormat(); format_.setVersion(4, 1)
        format_.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        cls.context = QOpenGLContext(); cls.context.setFormat(format_)
        cls.available = cls.context.create()
        if not cls.available: return
        cls.surface = QOffscreenSurface(); cls.surface.setFormat(cls.context.format()); cls.surface.create()
        cls.available = cls.context.makeCurrent(cls.surface)
        if not cls.available: return
        profile = QOpenGLVersionProfile(); profile.setVersion(4, 1); profile.setProfile(format_.profile())
        cls.gl = QOpenGLVersionFunctionsFactory.get(profile, cls.context)
        cls.available = cls.gl is not None
        if not cls.available: return
        cls.gl.initializeOpenGLFunctions()

    @classmethod
    def tearDownClass(cls):
        cls.context.doneCurrent()

    def test_noncurrent_retirement_waits_for_own_share_group_and_never_deletes_foreign_names(self):
        if not self.available: self.skipTest("Offscreen Qt OpenGL unavailable")
        from unittest.mock import Mock
        storage=CubeStorage(self.gl,self.context)
        names=tuple(storage.names)
        window=Mock(); storage.window=window
        self.context.doneCurrent()
        storage.close()
        window.scheduleRenderJob.assert_called_once()
        job=window.scheduleRenderJob.call_args.args[0]
        other=QOpenGLContext(); other.setFormat(self.context.format()); self.assertTrue(other.create())
        other_surface=QOffscreenSurface(); other_surface.setFormat(other.format()); other_surface.create()
        self.assertTrue(other.makeCurrent(other_surface))
        foreign=CubeStorage(self.gl,other)
        job.run()
        self.assertTrue(self.gl.glIsTexture(foreign.names[0]))
        foreign.close(); other.doneCurrent()
        self.assertTrue(self.context.makeCurrent(self.surface))
        job.run()
        self.assertTrue(all(not self.gl.glIsTexture(name) for name in names))
        flush_texture_deletions()  # Idempotent on an empty current group.
        self.assertEqual(int(self.gl.glGetError()),0)

    def test_failure_restores_vao_depth_clear_offset_and_high_active_texture(self):
        if not self.available: self.skipTest("Offscreen Qt OpenGL unavailable")
        gl = self.gl
        vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create()); vao.bind()
        gl.glActiveTexture(0x84C9)
        gl.glClearDepth(.3); gl.glPolygonOffset(2., 3.)
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with preserved_state(gl, self.context):
                vao.release(); gl.glClearDepth(1.); gl.glPolygonOffset(0., 0.)
                gl.glActiveTexture(0x84C7)
                raise RuntimeError('injected')
        self.assertEqual(int(gl.glGetIntegerv(0x85B5)), vao.objectId())
        self.assertEqual(int(gl.glGetIntegerv(0x84E0)), 0x84C9)
        self.assertAlmostEqual(float(gl.glGetDoublev(0x0B73)), .3)
        self.assertEqual(float(gl.glGetFloatv(0x8038)), 2.)
        self.assertEqual(float(gl.glGetFloatv(0x2A00)), 3.)
        vao.release(); gl.glClearDepth(1.)

    def test_capture_disables_inherited_polygon_offset_and_restores_host(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        gl=self.gl;storage=CubeStorage(gl,self.context)
        try:
            gl.glEnable(0x8037);gl.glPolygonOffset(1000.,1000.)
            with preserved_state(gl,self.context):
                storage.begin(gl,True)
                self.assertFalse(gl.glIsEnabled(0x8037))
            self.assertTrue(gl.glIsEnabled(0x8037))
            self.assertEqual(float(gl.glGetFloatv(0x8038)),1000.)
            self.assertEqual(float(gl.glGetFloatv(0x2A00)),1000.)
            self.assertEqual(int(gl.glGetError()),0)
        finally:
            gl.glDisable(0x8037);gl.glPolygonOffset(0.,0.);storage.close()

    def test_host_comparison_sampler_cannot_override_raw_depth_lookup_and_is_restored(self):
        if not self.available: self.skipTest("Offscreen Qt OpenGL unavailable")
        from unittest.mock import patch
        gl,context=self.gl,self.context
        gen=procedure(context,'glGenSamplers',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))
        bind=procedure(context,'glBindSampler',None,ctypes.c_uint,ctypes.c_uint)
        param=procedure(context,'glSamplerParameteri',None,ctypes.c_uint,ctypes.c_uint,ctypes.c_int)
        delete=procedure(context,'glDeleteSamplers',None,ctypes.c_int,ctypes.POINTER(ctypes.c_uint))
        name=ctypes.c_uint();gen(1,ctypes.byref(name))
        storage=CubeStorage(gl,context)
        try:
            param(name.value,0x884C,0x884E)  # Comparison sampler deliberately conflicts.
            param(name.value,0x2801,0x2601)
            bind(6,name.value);gl.glActiveTexture(0x84C6)
            adapter=SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda:SimpleNamespace(getBindingsObject=lambda:gl)))
            with patch.dict(sys.modules,{'UM.View.GL.OpenGL':adapter}):
                storage.depth.bind(6)
                self.assertEqual(int(gl.glGetIntegerv(0x8919)),0)
                storage.depth.release(6)
                self.assertEqual(int(gl.glGetIntegerv(0x8919)),name.value)
            self.assertEqual(int(gl.glGetError()),0)
        finally:
            bind(6,0);delete(1,ctypes.byref(name));storage.close()

    def test_partial_cube_bind_failure_restores_sampler_and_active_unit(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import patch
        gl, context = self.gl, self.context
        storage = CubeStorage(gl,context)
        gl.glActiveTexture(0x84C9)
        adapter=SimpleNamespace(OpenGL=SimpleNamespace(getInstance=lambda:SimpleNamespace(getBindingsObject=lambda:gl)))
        original=storage._sampler
        called=[]
        def fault(unit,name):
            called.append((unit,name))
            if len(called)==1:
                original(unit,name)
                raise RuntimeError('injected sampler bind')
            original(unit,name)
        try:
            with patch.dict(sys.modules,{'UM.View.GL.OpenGL':adapter}), patch.object(storage,'_sampler',side_effect=fault):
                with self.assertRaisesRegex(RuntimeError,'injected sampler bind'): storage.depth.bind(6)
                storage.depth.release(6)  # Idempotent after partial-bind unwind.
            self.assertEqual(called,[(6,0),(6,0)])
            self.assertEqual(storage._bindings,{})
            self.assertEqual(int(gl.glGetIntegerv(0x84E0)),0x84C9)
            self.assertEqual(int(gl.glGetError()),0)
        finally: storage.close()

    def test_published_cube_has_all_faces_and_restores_separate_draw_read_bindings(self):
        if not self.available: self.skipTest("Offscreen Qt OpenGL unavailable; optional effect falls back")
        gl = self.gl
        draw, read = QOpenGLFramebufferObject(16, 16), QOpenGLFramebufferObject(16, 16)
        bind = procedure(self.context, 'glBindFramebuffer', None, ctypes.c_uint, ctypes.c_uint)
        bind(0x8CA9, draw.handle()); bind(0x8CA8, read.handle())
        gl.glViewport(1, 2, 8, 9)
        gl.glActiveTexture(0x84C3)
        gl.glEnable(gl.GL_SCISSOR_TEST); gl.glScissor(2, 3, 4, 5)
        environment = ToolheadEnvironment()
        self.addCleanup(environment.close)
        colours = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1)]
        def commands(face):
            def render(bindings):
                bindings.glClearColor(*colours[face], 1.)
                bindings.glClearDepth((face+1)/8.)
                bindings.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            yield render
        snapshot = SimpleNamespace(commands=commands, descriptor=ProbeDescriptor((0,0,0),(-10,-10,-10),(10,10,10)))
        for turn in range(12):
            environment.step(gl, self.context, 'file', 'pose', lambda: snapshot)
            self.assertEqual(int(gl.glGetIntegerv(0x8CA6)), draw.handle())
            self.assertEqual(int(gl.glGetIntegerv(0x8CAA)), read.handle())
            self.assertEqual(tuple(gl.glGetIntegerv(0x0BA2)), (1, 2, 8, 9))
            self.assertEqual(int(gl.glGetIntegerv(0x84E0)), 0x84C3)
            self.assertTrue(gl.glIsEnabled(gl.GL_SCISSOR_TEST))
            if turn < 11: self.assertFalse(environment.available, environment.failure)
        self.assertTrue(environment.available, environment.failure)
        gl.glBindTexture(0x8513, environment._storage.front)
        pixels = (ctypes.c_ubyte * (SIZE*SIZE*4))()
        get = procedure(self.context, 'glGetTexImage', None, ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        for face, colour in enumerate(colours):
            get(0x8515 + face, 0, 0x1908, 0x1401, pixels)
            self.assertEqual(tuple(pixels[:4]), (*[round(v*255) for v in colour], 255))
        gl.glBindTexture(0x8513, environment._storage.front_depth)
        depths = (ctypes.c_float * (SIZE*SIZE))()
        for face in range(6):
            get(0x8515 + face, 0, 0x1902, 0x1406, depths)
            self.assertAlmostEqual(depths[0], (face+1)/8., places=6)
        self.assertEqual(int(gl.glGetError()), 0)
