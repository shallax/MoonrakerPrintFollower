"""Worker-private buffers, VAOs and single-sample reflection face target."""
from __future__ import annotations

import ctypes
from .ToolheadGLState import procedure

U, I, P = ctypes.c_uint, ctypes.c_int, ctypes.c_void_p


class CaptureBuffer:
    def __init__(self, context, target):
        self.context, self.target = context, target
        self.name = self.size = 0

    def create(self):
        value = U()
        procedure(self.context, 'glGenBuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
        self.name = value.value
        if not self.name: raise RuntimeError('Reflection buffer allocation failed')

    def bind(self):
        procedure(self.context, 'glBindBuffer', None, U, U)(self.target, self.name)

    def upload(self, body):
        self.bind()
        procedure(self.context, 'glBufferData', None, U, ctypes.c_ssize_t, P, U)(
            self.target, len(body), ctypes.cast(ctypes.c_char_p(body), P), 0x88E4)
        size = I()
        procedure(self.context, 'glGetBufferParameteriv', None, U, U, ctypes.POINTER(I))(
            self.target, 0x8764, ctypes.byref(size))
        if size.value != len(body): raise RuntimeError('Reflection buffer storage incomplete')
        self.size = size.value

    def close(self):
        name, self.name = self.name, 0
        if name:
            value = U(name)
            procedure(self.context, 'glDeleteBuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))


class CaptureVertexArray:
    def __init__(self, context):
        self.context, self.name = context, 0

    def create(self):
        value = U()
        procedure(self.context, 'glGenVertexArrays', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
        self.name = value.value
        if not self.name: raise RuntimeError('Reflection vertex array allocation failed')

    def bind(self): procedure(self.context, 'glBindVertexArray', None, U)(self.name)

    def close(self):
        name, self.name = self.name, 0
        if name:
            value = U(name)
            procedure(self.context, 'glDeleteVertexArrays', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))


class CaptureFace:
    """Private FBO; no Qt GL wrapper construction or host context activation."""
    def __init__(self, gl, context, width, height, depth_format=0x81A6):
        self.context, self.gl = context, gl
        self.framebuffer = self.texture = self.renderbuffer = 0
        self.valid = False

        self._bind = procedure(context, 'glBindFramebuffer', None, U, U)
        try:
            value = U()
            procedure(context, 'glGenFramebuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
            self.framebuffer = value.value
            self.bind()
            procedure(context, 'glGenTextures', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
            self.texture = value.value
            gl.glBindTexture(0x0DE1, self.texture)
            procedure(context, 'glTexImage2D', None, U, I, I, I, I, I, U, U, P)(
                0x0DE1, 0, 0x8058, width, height, 0, 0x1908, 0x1401, None)
            gl.glTexParameteri(0x0DE1, 0x2801, 0x2601)
            gl.glTexParameteri(0x0DE1, 0x2800, 0x2601)
            procedure(context, 'glFramebufferTexture2D', None, U, U, U, U, I)(
                0x8D40, 0x8CE0, 0x0DE1, self.texture, 0)
            procedure(context, 'glGenRenderbuffers', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
            self.renderbuffer = value.value
            procedure(context, 'glBindRenderbuffer', None, U, U)(0x8D41, self.renderbuffer)
            if depth_format not in (0x81A5, 0x81A6, 0x81A7): raise RuntimeError('Reflection face depth format is unsupported')
            procedure(context, 'glRenderbufferStorage', None, U, U, I, I)(0x8D41, depth_format, width, height)
            procedure(context, 'glFramebufferRenderbuffer', None, U, U, U, U)(
                0x8D40, 0x8D00, 0x8D41, self.renderbuffer)
            procedure(context, 'glDrawBuffer', None, U)(0x8CE0)
            procedure(context, 'glReadBuffer', None, U)(0x8CE0)
            self.valid = bool(self.framebuffer and self.texture and self.renderbuffer
                and procedure(context, 'glCheckFramebufferStatus', U, U)(0x8D40) == 0x8CD5)
            if not self.valid: raise RuntimeError('Reflection face target unavailable')
        except Exception:
            self.close(); raise
        finally:
            procedure(context, 'glBindRenderbuffer', None, U, U)(0x8D41, 0)
            gl.glBindTexture(0x0DE1, 0)
            self._bind(0x8D40, 0)

    def isValid(self): return self.valid

    def bind(self):
        self._bind(0x8D40, self.framebuffer)
        return bool(self.framebuffer)

    def close(self):
        for function, field in (('glDeleteFramebuffers', 'framebuffer'),
                ('glDeleteTextures', 'texture'), ('glDeleteRenderbuffers', 'renderbuffer')):
            name = getattr(self, field)
            setattr(self, field, 0)
            if name:
                value = U(name)
                procedure(self.context, function, None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
        self.valid = False


class CaptureTexture:
    """Frozen native platform pixels with its nearest/repeat sampling policy."""
    def __init__(self, gl, context, image):
        self.gl, self.context, self.name = gl, context, 0
        width, height, body = image if image is not None else (1, 1, b'\x00'*4)
        if width <= 0 or height <= 0 or width*height > 16*1024*1024 or len(body) != width*height*4:
            raise RuntimeError('Reflection platform image layout is invalid')
        try:
            value = U()
            procedure(context, 'glGenTextures', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
            self.name = value.value
            if not self.name: raise RuntimeError('Reflection platform allocation failed')
            gl.glBindTexture(0x0DE1, self.name)
            procedure(context, 'glTexImage2D', None, U, I, I, I, I, I, U, U, P)(
                0x0DE1, 0, 0x8058, width, height, 0, 0x1908, 0x1401, ctypes.cast(ctypes.c_char_p(body), P))
            for key, val in ((0x2801, 0x2600), (0x2800, 0x2600), (0x2802, 0x2901), (0x2803, 0x2901)):
                gl.glTexParameteri(0x0DE1, key, val)
        except Exception:
            self.close(); raise
        finally: gl.glBindTexture(0x0DE1, 0)

    def bind(self, unit):
        self.gl.glActiveTexture(0x84C0+unit); self.gl.glBindTexture(0x0DE1, self.name)

    def release(self, unit):
        self.gl.glActiveTexture(0x84C0+unit); self.gl.glBindTexture(0x0DE1, 0)

    def close(self):
        name, self.name = self.name, 0
        if name:
            value = U(name)
            procedure(self.context, 'glDeleteTextures', None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
