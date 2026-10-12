"""Real Qt offscreen targets: complete cube pixels and independent host FBOs."""
import ctypes
import os
import sys
import unittest
# Cocoa supports windowless QOffscreenSurface; Qt's offscreen plugin does not.
os.environ["QT_QPA_PLATFORM"] = "cocoa" if sys.platform == "darwin" else "offscreen"
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
        if not cls.available:
            if os.environ.get("MPF_REQUIRE_OFFSCREEN_GL") == "1":
                raise RuntimeError("Required offscreen OpenGL context could not be created")
            return
        cls.surface = QOffscreenSurface(); cls.surface.setFormat(cls.context.format()); cls.surface.create()
        cls.available = cls.context.makeCurrent(cls.surface)
        if not cls.available:
            if os.environ.get("MPF_REQUIRE_OFFSCREEN_GL") == "1":
                raise RuntimeError("Required offscreen OpenGL context could not become current")
            return
        profile = QOpenGLVersionProfile(); profile.setVersion(4, 1); profile.setProfile(format_.profile())
        cls.gl = QOpenGLVersionFunctionsFactory.get(profile, cls.context)
        cls.available = cls.gl is not None
        if not cls.available:
            if os.environ.get("MPF_REQUIRE_OFFSCREEN_GL") == "1":
                raise RuntimeError("Required OpenGL 4.1 functions are unavailable")
            return
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

    def test_private_capture_resources_roundtrip_and_retire_without_host_wrappers(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from mpf.toolhead.ToolheadCaptureBuffers import CaptureBuffer, CaptureVertexArray, CaptureFace, CaptureTexture
        gl, context = self.gl, self.context
        vao, buffer = CaptureVertexArray(context), CaptureBuffer(context, 0x8892)
        vao.create(); vao.bind(); buffer.create()
        body = b'\x12\x34\x56\x78' * 16
        buffer.upload(body)
        self.assertEqual(buffer.size, len(body))
        result = ctypes.create_string_buffer(len(body))
        procedure(context, 'glGetBufferSubData', None, ctypes.c_uint, ctypes.c_ssize_t,
                  ctypes.c_ssize_t, ctypes.c_void_p)(0x8892, 0, len(body), result)
        self.assertEqual(result.raw, body)
        names = vao.name, buffer.name
        buffer.close(); buffer.close(); vao.close(); vao.close()
        self.assertFalse(gl.glIsBuffer(names[1]))
        self.assertFalse(procedure(context, 'glIsVertexArray', ctypes.c_ubyte, ctypes.c_uint)(names[0]))
        face = CaptureFace(gl, context, 8, 8)
        self.assertTrue(face.isValid()); self.assertTrue(face.bind())
        gl.glClearColor(.25, .5, .75, 1.); gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        pixel = (ctypes.c_ubyte * 4)()
        procedure(context, 'glReadPixels', None, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                  ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)(0, 0, 1, 1, 0x1908, 0x1401, pixel)
        self.assertEqual(tuple(pixel), (64, 128, 191, 255))
        face.close(); face.close(); self.assertFalse(face.isValid())
        for image in (None, (1, 1, b'\x12\x34\x56\xff')):
            texture = CaptureTexture(gl, context, image)
            texture.bind(3)
            self.assertEqual(int(gl.glGetIntegerv(0x8069)), texture.name)
            texture.release(3)
            self.assertEqual(int(gl.glGetIntegerv(0x8069)), 0)
            name = texture.name
            texture.close(); texture.close(); self.assertFalse(gl.glIsTexture(name))
        gl.glActiveTexture(0x84C0)
        self.assertEqual(int(gl.glGetError()), 0)

    def test_multisample_face_preserves_partial_turns_and_nearest_coverage(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
        from mpf.toolhead.ToolheadCaptureBuffers import CaptureFace
        gl=self.gl;face=CaptureFace(gl,self.context,32,32,samples=4)
        if face.samples != 4:
            face.close();self.skipTest('Four sample renderbuffers unavailable')
        program=QOpenGLShaderProgram();vao=QOpenGLVertexArrayObject()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float z;void main(){vec2 p[3]=vec2[3](vec2(-1.,-1.),vec2(1.,-1.),vec2(-1.,.97));gl_Position=vec4(p[gl_VertexID],z,1.);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nuniform float red;out vec4 colour;void main(){colour=vec4(red,1.-red,0.,1.);}'))
        self.assertTrue(program.link());self.assertTrue(vao.create())
        pixels=(ctypes.c_ubyte*(32*32*4))()
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        try:
            with preserved_state(gl,self.context):
                face.bind();gl.glViewport(0,0,32,32);gl.glDisable(gl.GL_SCISSOR_TEST)
                gl.glColorMask(True,True,True,True);gl.glDepthMask(True)
                gl.glClearColor(0,0,0,0);gl.glClearDepth(1.)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT|gl.GL_DEPTH_BUFFER_BIT)
                gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LESS)
                gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
                program.bind();vao.bind()
                program.setUniformValue('z',-.5);program.setUniformValue('red',1.)
                gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                face.resolve();read(0,0,32,32,0x1908,0x1401,pixels)
                first=bytes(pixels)
                self.assertTrue(any(0<value<255 for value in first[0::4]))
                self.assertEqual(set(first[1::4]),{0})
                # A later command resumes the same MS depth/colour, without a clear.
                face.bind();program.setUniformValue('z',.5);program.setUniformValue('red',0.)
                gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                face.resolve();read(0,0,32,32,0x1908,0x1401,pixels)
                self.assertEqual(bytes(pixels),first)
                self.assertEqual(face.retained_bytes,32*32*40)
                vao.release();program.release()
                self.assertEqual(int(gl.glGetError()),0)
        finally:face.close();vao.destroy();program.removeAllShaders()
        self.assertEqual((face.ms_framebuffer,face.ms_colour,face.ms_depth),(0,0,0))

    def test_multisample_reduces_translated_subpixel_line_coverage_variation(self):
        """A 0.375px line must vary less across 16 phases than 1x 0/1 snapping."""
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
        from mpf.toolhead.ToolheadCaptureBuffers import CaptureFace
        gl=self.gl
        faces=[CaptureFace(gl,self.context,32,32,samples=count) for count in (0,4)]
        if faces[1].samples!=4:
            for face in faces:face.close()
            self.skipTest('Four sample renderbuffers unavailable')
        program=QOpenGLShaderProgram();vao=QOpenGLVertexArrayObject()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float phase;void main(){vec2 p[6]=vec2[6](vec2(0,0),vec2(1,0),vec2(0,1),vec2(0,1),vec2(1,0),vec2(1,1));vec2 q=p[gl_VertexID];gl_Position=vec4((15.+phase+q.x*.375)/16.-1.,q.y*2.-1.,0.,1.);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nout vec4 colour;void main(){colour=vec4(1.);}'))
        self.assertTrue(program.link());self.assertTrue(vao.create())
        pixels=(ctypes.c_ubyte*(32*32*4))()
        read=procedure(self.context,'glReadPixels',None,*([ctypes.c_int]*4),ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        coverage=[]
        try:
            with preserved_state(gl,self.context):
                gl.glViewport(0,0,32,32);gl.glDisable(gl.GL_SCISSOR_TEST)
                gl.glDisable(gl.GL_DEPTH_TEST);gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
                gl.glColorMask(True,True,True,True);gl.glClearColor(0,0,0,0)
                program.bind();vao.bind()
                for face in faces:
                    phases=[]
                    for phase in range(16):
                        face.bind();gl.glClear(gl.GL_COLOR_BUFFER_BIT)
                        program.setUniformValue('phase',phase/16.)
                        gl.glDrawArrays(gl.GL_TRIANGLES,0,6)
                        face.resolve();read(0,0,32,32,0x1908,0x1401,pixels)
                        phases.append(sum(bytes(pixels)[0::4])/(255.*32))
                    coverage.append(phases)
                vao.release();program.release()
                self.assertEqual(int(gl.glGetError()),0)
            single,multi=coverage
            self.assertEqual((min(single),max(single)),(0.,1.))
            self.assertLess(max(multi)-min(multi),.8*(max(single)-min(single)),coverage)
        finally:
            for face in faces:face.close()
            vao.destroy();program.removeAllShaders()

    def test_multisample_allocation_failure_retains_single_target(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import patch
        from mpf.toolhead import ToolheadCaptureBuffers as module
        original=module.procedure
        def fail(ctx,name,*signature):
            if name=='glRenderbufferStorageMultisample':
                return lambda *args: (_ for _ in ()).throw(RuntimeError('allocation failed'))
            return original(ctx,name,*signature)
        with patch.object(module,'procedure',side_effect=fail):
            face=module.CaptureFace(self.gl,self.context,8,8,samples=4)
        try:
            self.assertTrue(face.isValid());self.assertTrue(face.bind())
            self.assertEqual((face.samples,face.ms_framebuffer,face.ms_colour,face.ms_depth),(0,0,0,0))
            self.assertEqual(face.retained_bytes,8*8*8)
            face.resolve()
            self.assertEqual(int(self.gl.glGetError()),0)
        finally:face.close()

    def test_multisample_caps_mismatch_and_uncertain_cleanup(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import Mock, patch
        from mpf.toolhead import ToolheadCaptureBuffers as module
        from mpf.toolhead.ToolheadGLState import GeometryUncertain
        gl=Mock(wraps=self.gl)
        gl.glGetIntegerv.side_effect=lambda key: 2 if key==0x8D57 else self.gl.glGetIntegerv(key)
        face=module.CaptureFace(gl,self.context,8,8,samples=4)
        self.assertEqual(face.samples,0);face.close()
        if int(self.gl.glGetIntegerv(0x8D57))<4:return
        original=module.procedure
        def mismatch(ctx,name,*signature):
            if name=='glGetRenderbufferParameteriv':
                return lambda target,key,value: setattr(value._obj,'value',2)
            return original(ctx,name,*signature)
        with patch.object(module,'procedure',side_effect=mismatch):
            face=module.CaptureFace(self.gl,self.context,8,8,samples=4)
        self.assertTrue(face.isValid());self.assertEqual(face.samples,0)
        self.assertEqual((face.ms_framebuffer,face.ms_colour,face.ms_depth),(0,0,0));face.close()
        def uncertain(ctx,name,*signature):
            if name=='glRenderbufferStorageMultisample' or name=='glDeleteRenderbuffers':
                return lambda *args: (_ for _ in ()).throw(RuntimeError('injected failure'))
            return original(ctx,name,*signature)
        try:
            with patch.object(module,'procedure',side_effect=uncertain):
                with self.assertRaises(GeometryUncertain):
                    module.CaptureFace(self.gl,self.context,8,8,samples=4)
            retained=module._uncertain_faces[-1]
            self.assertNotEqual(retained.ms_colour,0)
            self.assertNotEqual(retained.framebuffer,0)
            self.assertEqual(retained.retained_bytes,8*8*40)
        finally:
            for retained in module._uncertain_faces[:]:
                retained.close();module._uncertain_faces.remove(retained)
        self.assertEqual(int(self.gl.glGetError()),0)

    def test_private_capture_allocation_failures_clean_partial_resources(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import patch
        from mpf.toolhead import ToolheadCaptureBuffers as module
        gl, context = self.gl, self.context
        original = module.procedure
        for kind in ('glGenBuffers', 'glGenVertexArrays', 'glGenTextures'):
            def resolve(ctx, name, *signature, kind=kind):
                return (lambda *args: None) if name == kind else original(ctx, name, *signature)
            with patch.object(module, 'procedure', side_effect=resolve):
                with self.assertRaisesRegex(RuntimeError, 'allocation failed'):
                    if kind == 'glGenBuffers': module.CaptureBuffer(context, 0x8892).create()
                    elif kind == 'glGenVertexArrays': module.CaptureVertexArray(context).create()
                    else: module.CaptureTexture(gl, context, None)
        with self.assertRaisesRegex(RuntimeError, 'depth format'):
            module.CaptureFace(gl, context, 8, 8, 0)
        def incomplete(ctx, name, *signature):
            return (lambda *args: 0) if name == 'glCheckFramebufferStatus' else original(ctx, name, *signature)
        with patch.object(module, 'procedure', side_effect=incomplete):
            with self.assertRaisesRegex(RuntimeError, 'target unavailable'):
                module.CaptureFace(gl, context, 8, 8)
        for image in ((0, 1, b''), (1, 1, b'bad'), (16777217, 1, b'')):
            with self.assertRaisesRegex(RuntimeError, 'layout is invalid'):
                module.CaptureTexture(gl, context, image)
        buffer = module.CaptureBuffer(context, 0x8892); buffer.create()
        try:
            def wrong_size(ctx, name, *signature):
                return (lambda *args: None) if name == 'glGetBufferParameteriv' else original(ctx, name, *signature)
            with patch.object(module, 'procedure', side_effect=wrong_size):
                with self.assertRaisesRegex(RuntimeError, 'storage incomplete'): buffer.upload(b'abcd')
        finally: buffer.close()
        self.assertEqual(int(gl.glGetError()), 0)

    def test_worker_raw_program_transfers_uniforms_and_reports_native_compile_failures(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import patch
        from PyQt6.QtGui import QMatrix4x4, QVector2D, QVector3D, QVector4D, QColor
        from PyQt6.QtOpenGL import QOpenGLShader
        with patch.dict(sys.modules, {'UM.View.GL.ShaderProgram': SimpleNamespace(ShaderProgram=object)}):
            from mpf.toolhead import ToolheadCaptureGL as module
        raw = module.RawBindings(self.context, {'GL_DEPTH_TEST': 0x0B71})
        self.assertIs(raw._resolve('glFlush'), raw.glFlush)
        with self.assertRaisesRegex(RuntimeError, 'Unqualified'): raw._resolve('glUnknown')
        with self.assertRaises(ValueError): module.RawBindings(self.context, {'not_a_constant': 1})
        raw.glViewport(0, 0, 16, 16)
        self.assertEqual(raw.glGetIntegerv(0x0BA2), (0, 0, 16, 16))
        raw.glClearColor(.25, .5, .75, 1.)
        self.assertEqual(raw.glGetFloatv(0x0C22), (.25, .5, .75, 1.))
        raw.glDepthMask(True)
        self.assertEqual(raw.glGetBooleanv(0x0B72), 1)
        raw.glClearDepth(.5); self.assertEqual(raw.glGetDoublev(0x0B73), .5)
        program = module.RawProgram()
        try:
            self.assertFalse(program.isLinked())
            program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, '''#version 410
                in vec3 position; uniform mat4 transform;
                void main() { gl_Position = transform * vec4(position, 1.); }''')
            program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, '''#version 410
                uniform vec2 two; uniform vec3 three; uniform vec4 four, tint;
                uniform float scalar, floats[2]; uniform int integer;
                uniform vec2 pairs[2]; uniform vec3 triples[2]; out vec4 colour;
                void main() { colour = four + tint + vec4(two, scalar, float(integer))
                    + vec4(three, 0.) + vec4(pairs[0] + pairs[1], floats[0], floats[1])
                    + vec4(triples[0] + triples[1], 0.); }''')
            program.link(); self.assertTrue(program.isLinked()); program.bind()
            self.assertGreaterEqual(program.attributeLocation('position'), 0)
            values = {'two': QVector2D(2., 3.), 'three': QVector3D(4., 5., 6.),
                      'four': QVector4D(7., 8., 9., 10.), 'tint': QColor.fromRgbF(.2, .4, .6, 1.),
                      'scalar': .75, 'integer': 3, 'transform': QMatrix4x4()}
            for name, value in values.items(): program.setUniformValue(program.uniformLocation(name), value)
            program.setUniformValueArray(program.uniformLocation('floats'), [.2, .3])
            program.setUniformValueArray(program.uniformLocation('pairs'), [QVector2D(1., 2.), QVector2D(3., 4.)])
            program.setUniformValueArray(program.uniformLocation('triples'), [QVector3D(1., 2., 3.), QVector3D(4., 5., 6.)])
            get = procedure(self.context, 'glGetUniformfv', None, ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_float))
            actual = (ctypes.c_float * 16)()
            for name, expected in (('two', (2., 3.)), ('three', (4., 5., 6.)), ('four', (7., 8., 9., 10.)),
                                   ('scalar', (.75,)), ('pairs[1]', (3., 4.)), ('triples[1]', (4., 5., 6.))):
                get(program.programId(), program.uniformLocation(name), actual)
                self.assertEqual(tuple(actual)[:len(expected)], expected)
            # Rebinding the private program does not erase GPU uniform values.
            from unittest.mock import Mock
            scalar_location = program.uniformLocation('scalar')
            scalar_upload = Mock(wraps=program.functions['glUniform1f'])
            program.functions['glUniform1f'] = scalar_upload
            program.release(); program.bind()
            program.setUniformValue(scalar_location, .75)
            program.setUniformValue(scalar_location, .75)
            # Earlier bulk array writes invalidated the cache once.
            self.assertEqual(scalar_upload.call_count, 1)
            program.setUniformValue(scalar_location, -.0)
            program.setUniformValue(scalar_location, .0)
            self.assertEqual(scalar_upload.call_count, 3)
            vector = values['three']; vector.setX(9.)
            program.setUniformValue(program.uniformLocation('three'), vector)
            get(program.programId(), program.uniformLocation('three'), actual)
            self.assertEqual(tuple(actual)[:3], (9., 5., 6.))
            element = program.uniformLocation('floats[1]')
            program.setUniformValue(element, .4)
            program.setUniformValueArray(program.uniformLocation('floats'), [.2, .8])
            program.setUniformValue(element, .4)
            get(program.programId(), element, actual)
            self.assertAlmostEqual(actual[0], .4)
            program.setUniformValue(scalar_location, .75)
            program.release(); program.link(); program.bind()
            program.setUniformValue(program.uniformLocation('scalar'), .75)
            get(program.programId(), program.uniformLocation('scalar'), actual)
            self.assertEqual(actual[0], .75)
            with self.assertRaisesRegex(RuntimeError, 'Unqualified uniform'):
                program.setUniformValue(-1, object())
            program.release()
        finally: program.close(); program.close()
        bad = module.RawProgram()
        try:
            with self.assertRaises(RuntimeError):
                bad.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, '#version 410\nnot valid GLSL')
            bad.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
                '#version 410\nout vec3 value; void main(){value=vec3(1.);gl_Position=vec4(0.);}')
            bad.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
                '#version 410\nin vec4 value; out vec4 colour; void main(){colour=value;}')
            with self.assertRaises(RuntimeError): bad.link()
        finally: bad.close()
        self.assertEqual(raw.glGetError(), 0)

    def test_async_depth_format_matches_real_native_attachment_and_restores_binding(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import Mock, patch
        with patch.dict(sys.modules, {
            'mpf.toolhead.ToolheadEnvironmentWorker': SimpleNamespace(EnvironmentWorker=object, CaptureJob=object),
            'mpf.toolhead.ToolheadCaptureRecipe': SimpleNamespace(CaptureFreezer=object),
        }):
            from mpf.toolhead import ToolheadAsyncEnvironment as async_module
        gl, context = self.gl, self.context
        previous = int(gl.glGetIntegerv(0x8CA7))
        native = async_module.native_depth_format(gl, context)
        self.assertIn(native, (0x81A5, 0x81A6, 0x81A7, 0x8CAC))
        self.assertEqual(int(gl.glGetIntegerv(0x8CA7)), previous)
        self.assertEqual(int(gl.glGetError()), 0)
        invalid = Mock(return_value=Mock(isValid=lambda: False))
        invalid.Attachment = QOpenGLFramebufferObject.Attachment
        with patch('PyQt6.QtOpenGL.QOpenGLFramebufferObject', invalid):
            with self.assertRaisesRegex(RuntimeError, 'format unavailable'):
                async_module.native_depth_format(gl, context)
        def unexpected(_ctx, name, *_args):
            if name == 'glGetFramebufferAttachmentParameteriv':
                return lambda _target, _attachment, _parameter, output: setattr(output._obj, 'value', 0)
            return procedure(_ctx, name, *_args)
        with patch.object(async_module, 'procedure', side_effect=unexpected):
            with self.assertRaisesRegex(RuntimeError, 'attachment unsupported'):
                async_module.native_depth_format(gl, context)
        self.assertEqual(int(gl.glGetError()), 0)

    def test_unsized_native_depth_uses_measured_precision_and_restores_binding_on_refusal(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import patch
        with patch.dict(sys.modules, {
            'mpf.toolhead.ToolheadEnvironmentWorker': SimpleNamespace(EnvironmentWorker=object, CaptureJob=object),
            'mpf.toolhead.ToolheadCaptureRecipe': SimpleNamespace(CaptureFreezer=object),
        }):
            from mpf.toolhead import ToolheadAsyncEnvironment as async_module
        gl, context = self.gl, self.context
        previous = int(gl.glGetIntegerv(0x8CA7))
        for bits, expected in ((16, 0x81A5), (24, 0x81A6), (32, 0x81A7), (0, None), (12, None)):
            queries = []
            def resolve(ctx, name, *signature, bits=bits, queries=queries):
                if name == 'glGetRenderbufferParameteriv':
                    def query(_target, parameter, output):
                        queries.append(parameter)
                        output._obj.value = 0x1902 if parameter == 0x8D44 else bits
                    return query
                return procedure(ctx, name, *signature)
            with self.subTest(bits=bits), patch.object(async_module, 'procedure', side_effect=resolve):
                if expected is None:
                    with self.assertRaisesRegex(RuntimeError, 'depth precision unsupported'):
                        async_module.native_depth_format(gl, context)
                else:
                    self.assertEqual(async_module.native_depth_format(gl, context), expected)
            self.assertEqual(queries, [0x8D44, 0x8D54])
            self.assertEqual(int(gl.glGetIntegerv(0x8CA7)), previous)
            self.assertEqual(int(gl.glGetError()), 0)

    def test_colour_only_capture_still_selects_nearest_surface_colour(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from PyQt6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
        gl=self.gl;storage=CubeStorage(gl,self.context,export_depth=False)
        program=QOpenGLShaderProgram();vao=QOpenGLVertexArrayObject()
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex,
            '#version 410\nuniform float z;void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,z,1.);}'))
        self.assertTrue(program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment,
            '#version 410\nuniform float red;out vec4 colour;void main(){colour=vec4(red,1.-red,0.,1.);}'))
        self.assertTrue(program.link());self.assertTrue(vao.create())
        get=procedure(self.context,'glGetTexImage',None,ctypes.c_uint,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p)
        pixels=(ctypes.c_ubyte*(SIZE*SIZE*4))()
        try:
            with preserved_state(gl,self.context):
                for face in range(6):
                    storage.begin(gl,True);gl.glEnable(gl.GL_DEPTH_TEST);gl.glDepthFunc(gl.GL_LESS)
                    gl.glDisable(gl.GL_BLEND);gl.glDisable(gl.GL_CULL_FACE)
                    program.bind();vao.bind()
                    for z,red in ((-.5,1.),(.5,0.)):
                        program.setUniformValue('z',z);program.setUniformValue('red',red)
                        gl.glDrawArrays(gl.GL_TRIANGLES,0,3)
                    vao.release();program.release();storage.copy(gl,face)
                storage.publish(gl)
                self.assertEqual((len(storage.names),storage.front_depth),(2,0))
                gl.glBindTexture(0x8513,storage.front)
                for face in range(6):
                    get(0x8515+face,0,0x1908,0x1401,pixels)
                    self.assertEqual(bytes(pixels),bytes((255,0,0,255))*(SIZE*SIZE))
                self.assertEqual(int(gl.glGetError()),0)
        finally:storage.close();vao.destroy();program.removeAllShaders()

    def test_worker_storage_selects_physical_pair_and_retires_all_names(self):
        if not self.available: self.skipTest('Offscreen Qt OpenGL unavailable')
        from unittest.mock import patch
        with patch.dict(sys.modules, {
            'mpf.toolhead.ToolheadCaptureGL': SimpleNamespace(RawBindings=object),
            'mpf.toolhead.ToolheadCaptureRecipe': SimpleNamespace(CaptureScene=object),
        }):
            from mpf.toolhead.ToolheadEnvironmentWorker import WorkerStorage
        storage = WorkerStorage(self.gl, self.context, 0x81A6)
        names = tuple(storage.names)
        try:
            for target in (1, 0, 1):
                storage.select(target)
                self.assertEqual((storage.back, storage.back_depth), (names[target], names[target+2]))
            with self.assertRaisesRegex(RuntimeError, 'pair target'): storage.select(2)
        finally: storage.close(); storage.close()
        self.assertTrue(all(not self.gl.glIsTexture(name) for name in names))
        self.assertEqual(int(self.gl.glGetError()), 0)

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
        environment = ToolheadEnvironment(commands_per_turn=1, storage_factory=lambda gl, context: CubeStorage(gl, context, export_depth=True))
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
        # Re-slice while the old paired map is visible. Every intermediate
        # face must keep its old colours/depth AND matching probe descriptor.
        old_names=environment._storage.front,environment._storage.front_depth
        old_descriptor=environment.descriptor
        replacement=SimpleNamespace(commands=commands,descriptor=ProbeDescriptor((1,2,3),(-20,-20,-20),(20,20,20)))
        colours[:]=colours[1:]+colours[:1]
        for turn in range(12):
            environment.step(gl,self.context,'new slice','new pose',lambda:replacement)
            self.assertTrue(environment.available,environment.failure)
            if turn<11:
                self.assertEqual((environment._storage.front,environment._storage.front_depth),old_names)
                self.assertIs(environment.descriptor,old_descriptor)
                gl.glBindTexture(0x8513,environment._storage.front)
                for face in range(6):
                    get(0x8515+face,0,0x1908,0x1401,pixels)
                    old_colour=colours[(face-1)%6]
                    self.assertEqual(tuple(pixels[:4]),(*[round(v*255) for v in old_colour],255))
                gl.glBindTexture(0x8513,environment._storage.front_depth)
                for face in range(6):
                    get(0x8515+face,0,0x1902,0x1406,depths)
                    self.assertAlmostEqual(depths[0],(face+1)/8.,places=6)
        self.assertEqual(environment.descriptor,replacement.descriptor)
        self.assertNotEqual((environment._storage.front,environment._storage.front_depth),old_names)
        self.assertEqual(int(gl.glGetError()),0)

class CaptureFaceFailureTests(unittest.TestCase):
    def test_optional_accounting_tracks_fallback_and_uncertain_native_owners(self):
        from unittest.mock import Mock, patch
        from mpf.toolhead import ToolheadCaptureBuffers as module
        from mpf.toolhead.ToolheadGLState import GeometryUncertain
        gl=Mock();gl.glGetIntegerv.return_value=4;gl.glGetError.return_value=0
        for uncertain in (False,True):
            next_name=[0]
            def resolve(context,name,*signature,uncertain=uncertain,next_name=next_name):
                if name.startswith('glGen'):
                    def generate(count,pointer):
                        next_name[0]+=1;pointer._obj.value=next_name[0]
                    return generate
                if name=='glCheckFramebufferStatus':return lambda *args:0x8CD5
                if name=='glRenderbufferStorageMultisample' or (uncertain and name=='glDeleteRenderbuffers'):
                    def fail(*args):raise RuntimeError('injected allocation/cleanup failure')
                    return fail
                return lambda *args:None
            with patch.object(module,'procedure',side_effect=resolve):
                if uncertain:
                    with self.assertRaises(GeometryUncertain):module.CaptureFace(gl,object(),512,512,samples=4)
                    face=module._uncertain_faces.pop()
                    self.assertEqual(face.retained_bytes,10*1024*1024)
                    self.assertNotEqual(face.ms_colour,0)
                else:
                    face=module.CaptureFace(gl,object(),512,512,samples=4)
                    self.assertEqual(face.retained_bytes,2*1024*1024)
                    self.assertEqual(face.samples,0)
            with patch.object(module,'procedure',return_value=lambda *args:None):face.close()
            self.assertEqual(face.retained_bytes,0)
