"""Context-owned four-sample colour and explicit floating-point scene depth."""
from __future__ import annotations

import ctypes
from weakref import ref

import numpy as np

from .ToolheadGLState import preserved_state, preserved_samples, procedure, retire_textures

U, I = ctypes.c_uint, ctypes.c_int


def validate_sample_positions(positions):
    """Require the exact measured, ordered four-sample GL pattern."""
    values = np.asarray(positions)
    if (values.shape != (4, 2) or values.dtype.kind not in 'fiu' or not np.isfinite(values).all()
            or np.any(values[:, 0] < 0) or np.any(values[:, 0] >= 1)
            or np.any(values[:, 1] <= 0) or np.any(values[:, 1] >= 1)
            or np.any(values * 16 != np.floor(values * 16))
            or len({tuple(row) for row in values}) != 4):
        raise ValueError("Four-sample positions require the exact ordered GL pattern")


class SampleTargetUnavailable(RuntimeError):
    """Target refused after successful cleanup and host-state restoration."""


class ToolheadSampleTarget:
    """A Qt-owned FBO name with certified RGBA8/Depth32F MS attachments.

    Qt owns its original, detached single-sample colour texture as well as the
    context-local framebuffer. The extra texture is explicitly budgeted; its
    lifetime/retirement remains Qt's responsibility. Shared MS textures are
    separately retired in their share group, never from a foreign context.
    """
    def __init__(self, gl, width, height):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        context = QOpenGLContext.currentContext()
        if context is None or context.format().majorVersion() < 4:
            raise RuntimeError('Four-sample target requires an owned core4 context')
        if not (0 < width <= 8192 and 0 < height <= 8192 and width*height*36 <= 128*1024*1024):
            raise ValueError('Four-sample target dimensions exceed its budget')
        self._context, self._group = context, context.shareGroup()
        self._fbo = None
        self._names = ()
        self._valid = False
        self.positions = ()
        self.allocation_bytes = width*height*36  # MS RGBA8 + D32F + detached Qt RGBA8.
        self._retirement = None
        self._read_fbo = self._read_shader = self._read_vao = None
        self._format = QOpenGLFramebufferObjectFormat()
        self._format.setSamples(4)
        self._format.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        self._format.setInternalTextureFormat(0x8058)
        refusal = None
        try:
            with preserved_state(gl, context), preserved_samples(gl, context):
                try:
                    fmt = QOpenGLFramebufferObjectFormat()
                    fmt.setInternalTextureFormat(0x8058)
                    self._fbo = QOpenGLFramebufferObject(width, height, fmt)
                    if not self._fbo.isValid() or not self._fbo.bind():
                        raise RuntimeError('Four-sample framebuffer allocation failed')
                    names = (U*2)()
                    procedure(context, 'glGenTextures', None, I, ctypes.POINTER(U))(2, names)
                    self._names = tuple(names)
                    if not all(self._names): raise RuntimeError('Four-sample texture allocation failed')
                    image = procedure(context, 'glTexImage2DMultisample', None, U, I, U, I, I, ctypes.c_ubyte)
                    attach = procedure(context, 'glFramebufferTexture2D', None, U, U, U, U, I)
                    query = procedure(context, 'glGetTexLevelParameteriv', None, U, I, U, ctypes.POINTER(I))
                    gl.glActiveTexture(0x84C0)
                    for name, kind, slot in ((names[0], 0x8058, 0x8CE0), (names[1], 0x8CAC, 0x8D00)):
                        gl.glBindTexture(0x9100, name)
                        image(0x9100, 4, kind, width, height, 1)
                        for parameter, expected in ((0x9106, 4), (0x1003, kind), (0x1000, width), (0x1001, height)):
                            actual = I()
                            query(0x9100, 0, parameter, ctypes.byref(actual))
                            if actual.value != expected: raise RuntimeError('Four-sample storage differs from its descriptor')
                        attach(0x8D40, slot, 0x9100, name, 0)
                    complete = procedure(context, 'glCheckFramebufferStatus', U, U)(0x8D40)
                    if complete != 0x8CD5 or int(gl.glGetIntegerv(0x80A9)) != 4:
                        raise RuntimeError('Four-sample attachments are incomplete')
                    get = procedure(context, 'glGetMultisamplefv', None, U, U, ctypes.POINTER(ctypes.c_float))
                    positions = []
                    for sample in range(4):
                        value = (ctypes.c_float*2)()
                        get(0x8E50, sample, value)
                        positions.append(tuple(value))
                    validate_sample_positions(positions)
                    self.positions = tuple(positions)
                    if gl.glGetError(): raise RuntimeError('Four-sample target preparation failed')
                except (RuntimeError, ValueError) as error:
                    self.close()
                    refusal = error
            if refusal is not None:
                raise SampleTargetUnavailable(str(refusal)) from refusal
            self._valid = True
        except Exception:
            self.close()
            raise
        owner = ref(self)
        def destroyed():
            instance = owner()
            if instance is not None:
                instance._valid = False
                try: instance.close()
                except Exception: pass  # Keep names leased if a dying driver cannot retire them.
        self._retirement = destroyed
        context.aboutToBeDestroyed.connect(destroyed, Qt.ConnectionType.DirectConnection)

    def __del__(self):
        try: self.close()
        except Exception: pass

    def isValid(self): return self._valid
    def format(self):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObjectFormat
        return QOpenGLFramebufferObjectFormat(self._format)
    def attachment(self): return self._format.attachment()
    def size(self): return self._fbo.size()
    def width(self): return self._fbo.width()
    def height(self): return self._fbo.height()
    def handle(self): return self._fbo.handle()
    def texture(self): return self._names[0] if self._names else 0
    def depth_texture(self): return self._names[1] if self._names else 0

    def bind(self):
        from PyQt6.QtGui import QOpenGLContext
        return bool(self._valid and QOpenGLContext.currentContext() is self._context and self._fbo.bind())

    def read_depth(self, gl):
        """Return original top-down sample IDs, never resolved/replicated depth."""
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLVertexArrayObject
        from UM.View.GL.ShaderProgram import ShaderProgram
        if not self._valid or QOpenGLContext.currentContext() is not self._context:
            raise RuntimeError('Sample depth readback requires its live original context')
        # A warmed staging target must never return stale finite pixels when
        # inherited raster state suppresses this fullscreen extraction.
        if (tuple(map(int, gl.glGetIntegerv(0x0B40))) != (0x1B02, 0x1B02)
                or any(gl.glIsEnabled(flag) for flag in
                       (0x8C89, 0x0B90, 0x0BF2, *range(0x3000, 0x3008)))):
            raise RuntimeError('Sample depth readback requires unrestricted filled raster coverage')
        width, height = self.width(), self.height()
        if width*height*40 > 128 << 20:
            raise RuntimeError('Sample depth staging exceeds its graphics memory budget')
        with preserved_state(gl, self._context), preserved_samples(gl, self._context):
            if self._read_fbo is None:
                fmt = QOpenGLFramebufferObjectFormat(); fmt.setInternalTextureFormat(0x822E)
                staging = QOpenGLFramebufferObject(width, height, fmt)
                if not staging.isValid(): raise RuntimeError('Sample depth staging unavailable')
                gl.glActiveTexture(0x84C0); gl.glBindTexture(0x0DE1, staging.texture())
                query = procedure(self._context, 'glGetTexLevelParameteriv', None, U, I, U, ctypes.POINTER(I))
                for parameter, expected in ((0x1003,0x822E),(0x1000,width),(0x1001,height)):
                    actual = I(); query(0x0DE1,0,parameter,ctypes.byref(actual))
                    if actual.value != expected:
                        raise RuntimeError('Sample depth staging storage differs from its descriptor')
                self._read_fbo = staging
                self.allocation_bytes += width*height*4
            if self._read_shader is None:
                shader = ShaderProgram()
                if not shader.setVertexShader("#version 410\nvoid main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.0-1.0,0.0,1.0);}") or not shader.setFragmentShader("""#version 410
uniform sampler2DMS u_depth;
uniform int u_sample;
out float frag_color;
void main(){frag_color=texelFetch(u_depth,ivec2(gl_FragCoord.xy),u_sample).r;}
"""):
                    raise RuntimeError('Sample depth extraction shader unavailable')
                shader.build(); shader.setUniformValue('u_depth', 0)
                self._read_shader = shader
            if self._read_vao is None:
                self._read_vao = QOpenGLVertexArrayObject()
                if not self._read_vao.create():
                    self._read_vao = None
                    raise RuntimeError('Sample depth extraction vertex array unavailable')
            packed = {key:int(gl.glGetIntegerv(key)) for key in (0x0D00,0x0D02,0x0D03,0x0D04,0x0D05)}
            pack_buffer = int(gl.glGetIntegerv(0x88ED))
            read = procedure(self._context,'glReadPixels',None,I,I,I,I,U,U,ctypes.c_void_p)
            sampler = procedure(self._context,'glBindSampler',None,U,U)
            result = np.empty((4,height,width),np.float32)
            plane = np.empty((height,width),np.float32)
            gl.glUseProgram(0)
            try:
                gl.glBindBuffer(0x88EB,0)
                for key in packed: gl.glPixelStorei(key,1 if key==0x0D05 else 0)
                if not self._read_fbo.bind(): raise RuntimeError('Sample depth staging bind failed')
                gl.glViewport(0,0,width,height)
                for flag in (0x0B71,0x0BE2,0x0B44,0x0C11,0x8037,0x8DB9,0x8E51,0x80A0,0x809E,0x809F,0x8C36):
                    gl.glDisable(flag)
                gl.glColorMask(True,True,True,True)
                self._read_shader.bind()
                if not int(gl.glGetIntegerv(0x8B8D)): raise RuntimeError('Sample depth extraction shader did not bind')
                self._read_vao.bind()
                gl.glActiveTexture(0x84C0); gl.glBindTexture(0x9100,self.depth_texture()); sampler(0,0)
                for sample in range(4):
                    self._read_shader.setUniformValue('u_sample',sample)
                    gl.glDrawArrays(0x0004,0,3)
                    read(0,0,width,height,0x1903,0x1406,plane.ctypes.data)
                    if gl.glGetError(): raise RuntimeError('Sample depth extraction failed')
                    result[sample] = plane[::-1]
                if not np.isfinite(result).all() or np.any((result<0)|(result>1)):
                    raise RuntimeError('Sample depth extraction contains invalid values')
                result.flags.writeable = False
            finally:
                actions = [lambda:gl.glBindBuffer(0x88EB,pack_buffer)]
                actions.extend(lambda key=key,value=value:gl.glPixelStorei(key,value) for key,value in packed.items())
                actions.extend((self._read_vao.release,self._read_shader.release))
                failure = None
                for action in actions:
                    try: action()
                    except Exception as error:
                        if failure is None: failure = error
                if failure is not None: raise RuntimeError('Sample depth readback restoration failed') from failure
        return result

    def close(self):
        self._valid = False
        if self._names:
            retire_textures(self._group, tuple(name for name in self._names if name))
            self._names = ()
        if self._retirement is not None:
            try: self._context.aboutToBeDestroyed.disconnect(self._retirement)
            except (RuntimeError, TypeError): pass
            self._retirement = None
        self._read_fbo = self._read_shader = self._read_vao = None
        self._fbo = None  # Qt's context-local FBO guard owns deferred native deletion.


def sample_blit(gl, target, source, *, buffers=None):
    """Copy certified same-size planes or resolve colour, never resolve depth."""
    from PyQt6.QtGui import QOpenGLContext
    from PyQt6.QtOpenGL import QOpenGLFramebufferObject
    context = QOpenGLContext.currentContext()
    if context is None or any(isinstance(owner, ToolheadSampleTarget) and owner._context is not context
                              for owner in (target, source)):
        raise RuntimeError('Sample copy requires its original current context')
    buffers = gl.GL_COLOR_BUFFER_BIT if buffers is None else buffers
    if (not target.isValid() or not source.isValid()
            or (target.width(), target.height()) != (source.width(), source.height())):
        raise ValueError('Sample copy requires complete same-size owned targets')
    source_samples, target_samples = source.format().samples(), target.format().samples()
    if buffers & gl.GL_DEPTH_BUFFER_BIT:
        if (not isinstance(source, ToolheadSampleTarget) or not isinstance(target, ToolheadSampleTarget)
                or source_samples != target_samples or source.positions != target.positions):
            raise ValueError('Sample depth copy requires identical Depth32F sample storage')
    elif target_samples != source_samples and not (source_samples == 4 and target_samples == 0):
        raise ValueError('Sample colour copy cannot synthesize missing samples')
    with preserved_state(gl, context):
        gl.glDisable(0x0C11)  # Blits obey inherited scissor even without an error.
        QOpenGLFramebufferObject.blitFramebuffer(
            target._fbo if isinstance(target, ToolheadSampleTarget) else target,
            source._fbo if isinstance(source, ToolheadSampleTarget) else source,
            buffers=buffers, filter=0x2600)
        if gl.glGetError(): raise RuntimeError('Sample colour/depth copy failed')
