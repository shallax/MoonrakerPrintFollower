"""Worker-private buffers, VAOs and single-sample reflection face target."""
from __future__ import annotations

import ctypes
from .ToolheadGLState import GeometryUncertain, procedure

_uncertain_faces = []

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
    def __init__(self, gl, context, width, height, depth_format=0x81A6, *, samples=0):
        self.context, self.gl = context, gl
        self.framebuffer = self.texture = self.renderbuffer = 0
        self.ms_framebuffer = self.ms_colour = self.ms_depth = 0
        self.samples = 0
        self.width, self.height = width, height
        self.retained_bytes = width*height*8
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
            if samples: self._multisample(depth_format, samples)
        except GeometryUncertain:
            raise
        except Exception:
            self.close(); raise
        finally:
            procedure(context, 'glBindRenderbuffer', None, U, U)(0x8D41, 0)
            gl.glBindTexture(0x0DE1, 0)
            self._bind(0x8D40, 0)

    def _close_multisample(self):
        for function, field in (('glDeleteFramebuffers','ms_framebuffer'),
                ('glDeleteRenderbuffers','ms_colour'),('glDeleteRenderbuffers','ms_depth')):
            name = getattr(self,field)
            if name:
                try:
                    value=U(name)
                    procedure(self.context,function,None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
                except Exception as error:
                    _uncertain_faces.append(self)
                    raise GeometryUncertain(self, 'Reflection multisample cleanup uncertain') from error
                setattr(self,field,0)
        self.samples=0
        self.retained_bytes=self.width*self.height*8

    def _multisample(self, depth_format, samples):
        gl,context=self.gl,self.context
        if samples != 4 or int(gl.glGetIntegerv(0x8D57)) < samples: return
        self.retained_bytes=self.width*self.height*40
        try:
            value=U()
            procedure(context,'glGenFramebuffers',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
            self.ms_framebuffer=value.value
            if not self.ms_framebuffer: raise RuntimeError('Multisample framebuffer unavailable')
            self._bind(0x8D40,self.ms_framebuffer)
            for field,format_,attachment in (('ms_colour',0x8058,0x8CE0),('ms_depth',depth_format,0x8D00)):
                value=U()
                procedure(context,'glGenRenderbuffers',None,I,ctypes.POINTER(U))(1,ctypes.byref(value))
                setattr(self,field,value.value)
                if not value.value: raise RuntimeError('Multisample renderbuffer unavailable')
                procedure(context,'glBindRenderbuffer',None,U,U)(0x8D41,value.value)
                procedure(context,'glRenderbufferStorageMultisample',None,U,I,U,I,I)(
                    0x8D41,samples,format_,self.width,self.height)
                actual=I()
                procedure(context,'glGetRenderbufferParameteriv',None,U,U,ctypes.POINTER(I))(
                    0x8D41,0x8CAB,ctypes.byref(actual))
                if actual.value != samples: raise RuntimeError('Multisample count mismatch')
                procedure(context,'glGetRenderbufferParameteriv',None,U,U,ctypes.POINTER(I))(
                    0x8D41,0x8D44,ctypes.byref(actual))
                depth_bits={0x81A5:16,0x81A6:24,0x81A7:32}
                valid_format = (actual.value == format_ if field=='ms_colour' else
                    depth_bits.get(actual.value,0) >= depth_bits[depth_format])
                # Colour-only resolve permits a driver's higher depth precision.
                if not valid_format: raise RuntimeError('Multisample format mismatch')
                procedure(context,'glFramebufferRenderbuffer',None,U,U,U,U)(0x8D40,attachment,0x8D41,value.value)
            procedure(context,'glDrawBuffer',None,U)(0x8CE0)
            procedure(context,'glReadBuffer',None,U)(0x8CE0)
            if procedure(context,'glCheckFramebufferStatus',U,U)(0x8D40)!=0x8CD5 or gl.glGetError():
                raise RuntimeError('Multisample target unavailable')
            self.samples=samples
            try:
                from UM.Logger import Logger
                Logger.log('i', 'Toolhead reflection capture: %sx%s, %s samples, colour resolve', self.width, self.height, self.samples)
            except ImportError: pass
        except Exception:
            self._close_multisample()
            # Optional allocation errors must not poison the working single target.
            for _ in range(32):
                if not gl.glGetError(): break
            else: raise RuntimeError('Multisample allocation error state did not clear')
        finally:
            self._bind(0x8D40,self.framebuffer)

    def resolve(self):
        if self.samples:
            self.gl.glDisable(0x0C11)
            self._bind(0x8CA8,self.ms_framebuffer)
            self._bind(0x8CA9,self.framebuffer)
            procedure(self.context,'glReadBuffer',None,U)(0x8CE0)
            procedure(self.context,'glDrawBuffer',None,U)(0x8CE0)
            procedure(self.context,'glBlitFramebuffer',None,*([I]*8),U,U)(
                0,0,self.width,self.height,0,0,self.width,self.height,0x4000,0x2600)
            if self.gl.glGetError(): raise RuntimeError('Reflection multisample resolve failed')
        self._bind(0x8D40,self.framebuffer)

    def isValid(self): return self.valid

    def bind(self):
        self._bind(0x8D40, self.ms_framebuffer or self.framebuffer)
        if self.samples:
            self.gl.glEnable(0x809D)
            for capability in (0x809E,0x809F,0x80A0,0x8E51): self.gl.glDisable(capability)
        return bool(self.framebuffer)

    def close(self):
        self._close_multisample()
        for function, field in (('glDeleteFramebuffers', 'framebuffer'),
                ('glDeleteTextures', 'texture'), ('glDeleteRenderbuffers', 'renderbuffer')):
            name = getattr(self, field)
            setattr(self, field, 0)
            if name:
                value = U(name)
                procedure(self.context, function, None, I, ctypes.POINTER(U))(1, ctypes.byref(value))
        self.valid = False
        self.retained_bytes = 0


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
