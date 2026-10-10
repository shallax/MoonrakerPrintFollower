"""Actual private depth cubes: holes, whole-set publication and host restoration."""
import ctypes
import os
import sys
import unittest
from unittest.mock import Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'cocoa' if sys.platform == 'darwin' else 'offscreen'
from PyQt6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PyQt6.QtOpenGL import QOpenGLVersionFunctionsFactory, QOpenGLVersionProfile, QOpenGLVertexArrayObject

from mpf.toolhead.ToolheadGLState import procedure, preserved_state
from mpf.toolhead.ToolheadShadowStorage import ShadowStorage
from mpf.toolhead import ToolheadShadowStorage as module
from mpf.toolhead.ToolheadShadowValues import ShadowLight, ShadowMapPlan, ShadowProjection


class ShadowStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        fmt = QSurfaceFormat(); fmt.setVersion(4, 1)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        cls.context = QOpenGLContext(); cls.context.setFormat(fmt)
        cls.available = cls.context.create()
        if cls.available:
            cls.surface = QOffscreenSurface(); cls.surface.setFormat(cls.context.format()); cls.surface.create()
            cls.available = cls.context.makeCurrent(cls.surface)
        if cls.available:
            profile = QOpenGLVersionProfile(); profile.setVersion(4, 1); profile.setProfile(fmt.profile())
            cls.gl = QOpenGLVersionFunctionsFactory.get(profile, cls.context)
            cls.available = cls.gl is not None
        if not cls.available:
            if os.environ.get('MPF_REQUIRE_OFFSCREEN_GL') == '1':
                raise RuntimeError('Required shadow-map GL context unavailable')
            return
        cls.gl.initializeOpenGLFunctions()

    @classmethod
    def tearDownClass(cls):
        cls.context.doneCurrent()

    def setUp(self):
        if not self.available:
            self.skipTest('Offscreen GL unavailable')
        self.assertTrue(self.context.makeCurrent(self.surface))
        self.lights = tuple(ShadowLight('attached', i, (i*20, 3, 5), (0, -1, 0), (1, 1, 1), 40) for i in range(2))
        self.plan = ShadowMapPlan(self.lights)
        self.projections = tuple(ShadowProjection(light.position, .01, 100) for light in self.lights)
        self.storage = ShadowStorage(self.gl, self.context, self.plan)
        self.addCleanup(self.storage.close)

    def read(self, texture, face, x=10, y=10):
        gl, context = self.gl, self.context
        pack = tuple(int(gl.glGetIntegerv(key)) for key in (0x88ED, 0x0D02, 0x0D03, 0x0D04, 0x0D05))
        try:
            with preserved_state(gl, context):
                gl.glBindBuffer(0x88EB, 0)
                for key, value in ((0x0D02, 0), (0x0D03, 0), (0x0D04, 0), (0x0D05, 4)):
                    gl.glPixelStorei(key, value)
                self.storage._bind(0x8D40, self.storage.framebuffer)
                self.storage._attach(0x8D40, 0x8D00, 0x8515+face, texture, 0)
                result = ctypes.c_float()
                procedure(context, 'glReadPixels', None, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)(
                              x, y, 1, 1, 0x1902, 0x1406, ctypes.byref(result))
                return result.value
        finally:
            gl.glBindBuffer(0x88EB, pack[0])
            for key, value in zip((0x0D02, 0x0D03, 0x0D04, 0x0D05), pack[1:], strict=True):
                gl.glPixelStorei(key, value)

    def clear(self, depth):
        def draw(gl, projection, face):
            gl.glClearDepth(depth); gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        return draw

    def finish(self, key, depth=.3):
        self.storage.begin(key, self.projections)
        for light in self.lights:
            for face in range(6):
                self.storage.capture((light.kind, light.index), face, self.clear(depth + light.index*.1 + face*.01))
        self.storage.publish()

    def test_all_faces_all_lights_and_failed_replacement_preserve_complete_front(self):
        self.storage.begin('A', self.projections)
        for light in self.lights:
            for face in range(6):
                self.assertIsNone(self.storage.completed('A'))
                self.storage.capture((light.kind, light.index), face, self.clear(.2+light.index*.1+face*.01))
                if light.index != 1 or face != 5:
                    with self.assertRaisesRegex(RuntimeError, 'incomplete faces'):
                        self.storage.publish()
        self.storage.publish()
        old = self.storage.completed('A')
        for identity, texture, projection in old:
            self.assertEqual(projection.origin, self.lights[identity[1]].position)
            for face in range(6):
                self.assertAlmostEqual(self.read(texture, face), .2+identity[1]*.1+face*.01, places=6)
        self.storage.begin('B', self.projections)
        def failed(gl, projection, face):
            gl.glClearDepth(.01); gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
            raise RuntimeError('injected caster failure')
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            self.storage.capture(('attached', 0), 0, failed)
        with self.assertRaises(RuntimeError):
            self.storage.publish()
        self.assertIsNone(self.storage.completed('B'))
        self.assertEqual(self.storage.completed('A'), old)
        self.assertAlmostEqual(self.read(old[0][1], 0), .2, places=6)
        self.finish('B', .5)
        self.assertIsNone(self.storage.completed('A'))
        self.assertAlmostEqual(self.read(self.storage.completed('B')[0][1], 0), .5, places=6)
        self.assertEqual(int(self.gl.glGetError()), 0)

    def test_original_raster_hole_and_hostile_scissor_depth_state_restored(self):
        from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
        shader = QOpenGLShaderProgram()
        self.addCleanup(shader.removeAllShaders)
        self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0,1);}'))
        self.assertTrue(shader.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nvoid main(){if(all(lessThan(abs(gl_FragCoord.xy-vec2(512)),vec2(80)))) discard;gl_FragDepth=.25;}'))
        self.assertTrue(shader.link())
        vao = QOpenGLVertexArrayObject(); self.assertTrue(vao.create()); self.addCleanup(vao.destroy)
        gl = self.gl
        depth_range = procedure(self.context, 'glDepthRange', None, ctypes.c_double, ctypes.c_double)
        gl.glEnable(gl.GL_SCISSOR_TEST); gl.glScissor(0, 0, 1, 1)
        gl.glDepthMask(False); gl.glDepthFunc(gl.GL_GREATER)
        gl.glEnable(0x864F); depth_range(.2, .8)
        self.storage.begin('hole', self.projections)
        def draw(gl, projection, face):
            shader.bind(); vao.bind(); gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        try:
            self.storage.capture(('attached', 0), 0, draw)
            self.assertTrue(gl.glIsEnabled(gl.GL_SCISSOR_TEST))
            self.assertTrue(gl.glIsEnabled(0x864F))
            self.assertFalse(gl.glGetBooleanv(gl.GL_DEPTH_WRITEMASK))
            self.assertEqual(int(gl.glGetIntegerv(gl.GL_DEPTH_FUNC)), gl.GL_GREATER)
            self.assertEqual(tuple(map(int, gl.glGetIntegerv(gl.GL_SCISSOR_BOX))), (0, 0, 1, 1))
            self.assertAlmostEqual(float(gl.glGetDoublev(0x0B70)[0]), .2)
            self.assertAlmostEqual(float(gl.glGetDoublev(0x0B70)[1]), .8)
            self.assertEqual(self.read(self.storage._back[0], 0), .25)
            self.assertEqual(self.read(self.storage._back[0], 0, 512, 512), 1)
            self.assertEqual(int(gl.glGetError()), 0)
        finally:
            gl.glDisable(gl.GL_SCISSOR_TEST); gl.glDisable(0x864F)
            gl.glDepthMask(True); gl.glDepthFunc(gl.GL_LESS); depth_range(0, 1)

    def test_foreign_context_retirement_and_invalid_tickets_never_publish(self):
        for key, projections in ((None, self.projections), ('bad', self.projections[:1]),
                                 ('bad', (object(), object())),
                                 ('bad', (ShadowProjection((90, 3, 5), .01, 100), self.projections[1]))):
            with self.assertRaises(ValueError):
                self.storage.begin(key, projections)
        self.storage.begin('A', self.projections)
        for identity, face in ((('attached', 7), 0), (('attached', 0), 6), (('attached', 0), True)):
            with self.assertRaises(ValueError):
                self.storage.capture(identity, face, self.clear(.2))
        self.gl.glEnable(0x0B90)
        try:
            with self.assertRaisesRegex(RuntimeError, 'raster coverage'):
                self.storage.capture(('attached', 0), 0, self.clear(.2))
        finally:
            self.gl.glDisable(0x0B90)
        self.context.doneCurrent()
        with self.assertRaisesRegex(RuntimeError, 'live creating context'):
            self.storage.completed('A')
        with self.assertRaisesRegex(RuntimeError, 'must close'):
            self.storage.close()
        self.assertTrue(self.context.makeCurrent(self.surface))
        self.finish('A')
        names = tuple(self.storage.names)
        framebuffer = self.storage.framebuffer
        self.storage.close(); self.storage.close()
        self.assertTrue(all(not self.gl.glIsTexture(name) for name in names))
        self.assertFalse(procedure(self.context, 'glIsFramebuffer', ctypes.c_ubyte, ctypes.c_uint)(framebuffer))
        with self.assertRaisesRegex(RuntimeError, 'live creating context'):
            self.storage.begin('new', self.projections)

    def test_reentrant_publication_cannot_redirect_current_draw_into_a_new_front(self):
        self.storage.begin('A', self.projections)
        with self.assertRaisesRegex(RuntimeError, 'already active'):
            self.storage.capture(('attached', 0), 0, lambda gl, projection, face:self.finish('B'))
        self.assertFalse(self.storage._completed)
        def draw(gl, projection, face):
            for operation in (lambda:self.storage.begin('B', self.projections),
                              lambda:self.storage.capture(('attached', 0), 1, self.clear(.9)),
                              self.storage.publish, self.storage.close):
                with self.assertRaisesRegex(RuntimeError, 'already active'):
                    operation()
            gl.glClearDepth(.2); gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
        self.storage.capture(('attached', 0), 0, draw)
        self.assertEqual(self.storage._pending, 'A')
        self.assertAlmostEqual(self.read(self.storage._back[0], 0), .2, places=6)
        with self.assertRaises(RuntimeError):
            self.storage.publish()

    def test_allocation_faults_delete_independent_resources_and_restore_unpack_pbo(self):
        value = ctypes.c_uint()
        procedure(self.context, 'glGenBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(value))
        pbo = value.value
        self.gl.glBindBuffer(0x88EC, pbo)
        procedure(self.context, 'glBufferData', None, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_void_p,
                  ctypes.c_uint)(0x88EC, 16, None, 0x88E4)
        self.gl.glPixelStorei(0x0CF2, 2048)
        generated = []
        real_procedure = module.procedure
        stage = [None]
        def resolve(context, name, *signature):
            real = real_procedure(context, name, *signature)
            if name == 'glGenTextures':
                def generate(count, names):
                    if stage[0] == 'texture':
                        for i in range(count):
                            names[i] = 0
                    else:
                        real(count, names); generated.extend(names)
                return generate
            if name == 'glGenFramebuffers' and stage[0] == 'framebuffer':
                return lambda count, pointer:ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint)).__setitem__(0, 0)
            if name == 'glGetTexLevelParameteriv' and stage[0] == 'format':
                return lambda *args:ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_int)).__setitem__(0, 0)
            if name == 'glCheckFramebufferStatus' and stage[0] == 'status':
                return lambda target:0
            return real
        try:
            # A real 16-byte poisoned PBO must not be read during 4MiB-face
            # allocation. Its binding AND untouched layout survive success.
            extra = ShadowStorage(self.gl, self.context, self.plan)
            extra.close()
            self.assertEqual(int(self.gl.glGetIntegerv(0x88EF)), pbo)
            self.assertEqual(int(self.gl.glGetIntegerv(0x0CF2)), 2048)
            for failure in ('texture', 'format', 'framebuffer', 'status'):
                stage[0] = failure
                with self.subTest(stage=failure), patch.object(module, 'procedure', side_effect=resolve):
                    with self.assertRaises(RuntimeError):
                        ShadowStorage(self.gl, self.context, self.plan)
                    self.assertEqual(int(self.gl.glGetIntegerv(0x88EF)), pbo)
                    self.assertEqual(int(self.gl.glGetIntegerv(0x0CF2)), 2048)
                    self.assertTrue(all(not self.gl.glIsTexture(name) for name in generated))
            proxy = Mock(wraps=self.gl)
            proxy.glGetError.return_value = 0x0505
            with self.assertRaisesRegex(RuntimeError, 'allocation failed'):
                ShadowStorage(proxy, self.context, self.plan)
        finally:
            self.gl.glBindBuffer(0x88EC, 0); self.gl.glPixelStorei(0x0CF2, 0)
            procedure(self.context, 'glDeleteBuffers', None, ctypes.c_int, ctypes.POINTER(ctypes.c_uint))(1, ctypes.byref(value))
        self.assertEqual(int(self.gl.glGetError()), 0)

    def test_admission_refusal_precedes_allocation_and_failed_face_is_not_certified(self):
        with self.assertRaises(ValueError):
            ShadowStorage(self.gl, self.context, ShadowMapPlan(()))
        with patch('PyQt6.QtGui.QOpenGLContext.currentContext', return_value=None):
            with self.assertRaises(RuntimeError):
                ShadowStorage(self.gl, self.context, self.plan)
        proxy = Mock(wraps=self.gl)
        proxy.glGetIntegerv.side_effect = lambda key:512 if key == 0x851C else self.gl.glGetIntegerv(key)
        with self.assertRaisesRegex(RuntimeError, 'cube size'):
            ShadowStorage(proxy, self.context, self.plan)
        self.storage.begin('A', self.projections)
        original = self.storage._check
        self.storage._check = lambda target:0
        with self.assertRaisesRegex(RuntimeError, 'face is incomplete'):
            self.storage.capture(('attached', 0), 0, self.clear(.2))
        self.storage._check = original
        original_error = self.storage.gl
        bad_gl = Mock(wraps=self.gl); bad_gl.glGetError.side_effect = [0, 0x0505]
        self.storage.gl = bad_gl
        with self.assertRaisesRegex(RuntimeError, 'face draw failed'):
            self.storage.capture(('attached', 0), 0, lambda gl, projection, face:None)
        self.storage.gl = original_error
        self.assertFalse(self.storage._completed)
        self.assertFalse(self.storage._capturing)

    def test_context_retirement_is_permanent_and_queue_failure_retains_names_for_retry(self):
        # Use a separate PRIVATE shared context; destroying it never activates
        # or modifies a live Cura window/context.
        other = QOpenGLContext(); other.setFormat(self.context.format()); other.setShareContext(self.context)
        self.assertTrue(other.create())
        surface = QOffscreenSurface(); surface.setFormat(other.format()); surface.create()
        self.assertTrue(other.makeCurrent(surface))
        extra = ShadowStorage(self.gl, other, self.plan)
        names = tuple(extra.names)
        from PyQt6 import sip
        with patch.object(module, 'retire_textures', side_effect=RuntimeError('injected queue failure')):
            other.doneCurrent(); sip.delete(other)
        self.assertTrue(extra._retired)
        self.assertEqual(tuple(extra.names), names)
        self.assertIn('injected queue failure', extra.retirement_failure)
        self.assertTrue(self.context.makeCurrent(self.surface))
        with self.assertRaisesRegex(RuntimeError, 'live creating context'):
            extra.completed('A')
        extra.close()
        self.assertFalse(extra.names)
        self.assertTrue(all(not self.gl.glIsTexture(name) for name in names))


if __name__ == '__main__':
    unittest.main()
