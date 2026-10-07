"""Owned native path colour/depth accumulation, excluding fractional segments."""
from __future__ import annotations

import ctypes


class ToolheadSimulationCache:
    # One RGBA8 + 32-bit depth target; the adapter already owns its output.
    # 3840x2190 requires 64.2 MiB here. Larger allocations fail safely.
    MAX_BYTES = 128 * 1024 * 1024

    def __init__(self):
        self._context = self._fbo = self._size = None
        self._key = self._completed = None
        self._bind = self._blit = None
        self._copy_checked = False
        self._copy_target = None

    def _retire(self):
        self._fbo = self._size = self._key = self._completed = None
        self._copy_checked = False
        self._copy_target = None

    def _functions(self, context):
        bind = context.getProcAddress(b"glBindFramebuffer")
        blit = context.getProcAddress(b"glBlitFramebuffer")
        if not bind or not blit: raise RuntimeError("Simulation framebuffer copying unavailable")
        self._bind = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint)(int(bind))
        self._blit = ctypes.CFUNCTYPE(None, *([ctypes.c_int] * 8), ctypes.c_uint, ctypes.c_uint)(int(blit))

    def restore(self, gl, output, key, completed, rebuild, append):
        """Restore retained paths into the explicitly owned, already bound output.

        ``key`` includes mesh/transform, native shaders, camera/light, layer,
        minimum layer and every native view/theme/extruder setting. ``completed``
        excludes the fractional segment. Callbacks render into this cache with
        native GL_LESS/no blending; append receives the old/new element ends.
        The caller draws the fractional segment into output after this returns.
        """
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        context = QOpenGLContext.currentContext()
        if self._context is not context:
            self._retire()
            self._bind = self._blit = None
        self._context = context
        if context is None: raise RuntimeError("Simulation graphics context unavailable")
        if self._bind is None: self._functions(context)
        target = int(output.handle())
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        read_target = int(gl.glGetIntegerv(0x8CAA))
        width, height = output.size().width(), output.size().height()
        if target <= 0 or int(gl.glGetIntegerv(0x8CA6)) != target:
            raise RuntimeError("Simulation cache requires its bound owned output")
        if viewport != (0, 0, width, height) or width <= 0 or height <= 0:
            raise RuntimeError("Simulation cache output viewport unavailable")
        if int(gl.glGetIntegerv(0x80A9)) or output.format().samples():
            raise RuntimeError("Simulation cache requires a single-sample output")
        if output.format().internalTextureFormat() not in (0x1908, 0x8058):
            raise RuntimeError("Simulation cache requires RGBA8 output")
        if width * height * 8 > self.MAX_BYTES:
            raise RuntimeError("Simulation cache exceeds its retained memory budget")
        completed = int(completed)
        if completed < 0: raise RuntimeError("Simulation completed prefix is invalid")
        # Native RenderPass clears using the current GL clear colour. Retain
        # that exact background; this helper never changes glClearColor.
        clear_colour = tuple(map(float, gl.glGetFloatv(0x0C22)))
        key = key, clear_colour
        try:
            if self._size != (width, height):
                self._retire()
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
                format_.setInternalTextureFormat(0x8058)
                self._fbo = QOpenGLFramebufferObject(width, height, format_)
                if not self._fbo.isValid(): raise RuntimeError("Simulation retained framebuffer unavailable")
                self._size = width, height
            if self._copy_target != target: self._copy_checked = False
            changed = self._key != key or self._completed != completed
            if changed:
                if not self._fbo.bind(): raise RuntimeError("Simulation retained framebuffer could not be bound")
                gl.glViewport(0, 0, width, height)
                gl.glDisable(0x0C11)
                gl.glColorMask(True, True, True, True)
                gl.glDepthMask(True)
                if self._key != key or self._completed is None or completed < self._completed:
                    gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                    rebuild()
                else:
                    append(self._completed, completed)
                error = int(gl.glGetError())
                if error: raise RuntimeError("Simulation retained draw failed: 0x%04x" % error)
            # A copy failure must never publish an incomplete retained image.
            # Validate destination changes independently from geometry changes.
            if not self._copy_checked:
                error = int(gl.glGetError())
                if error: raise RuntimeError("Simulation copy found existing GL error: 0x%04x" % error)
            self._bind(0x8CA8, self._fbo.handle())
            self._bind(0x8CA9, target)
            gl.glDisable(0x0C11)  # Blit is affected by the destination scissor.
            self._blit(0, 0, width, height, 0, 0, width, height,
                       gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            if not self._copy_checked:
                error = int(gl.glGetError())
                if error: raise RuntimeError("Simulation colour/depth copy failed: 0x%04x" % error)
                self._copy_checked = True
                self._copy_target = target
            self._key, self._completed = key, completed
        except Exception:
            self._retire()
            raise
        finally:
            try:
                try:
                    self._bind(0x8CA8, read_target)
                finally:
                    self._bind(0x8CA9, target)
            finally:
                gl.glViewport(*viewport)

    def _depth_functions(self, context):
        attachment = context.getProcAddress(b"glGetFramebufferAttachmentParameteriv")
        renderbuffer = context.getProcAddress(b"glGetRenderbufferParameteriv")
        if not attachment or not renderbuffer: return None
        getter = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                  ctypes.POINTER(ctypes.c_int))
        storage = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
        return getter(int(attachment)), storage(int(renderbuffer))

    def _depth_format(self, gl, handle, queries):
        """Query the owned depth attachment without knowing Qt's private RBO."""
        self._bind(0x8CA8, handle)
        kind, name = ctypes.c_int(), ctypes.c_int()
        queries[0](0x8CA8, 0x8D00, 0x8CD0, ctypes.byref(kind))
        queries[0](0x8CA8, 0x8D00, 0x8CD1, ctypes.byref(name))
        if kind.value != 0x8D41 or name.value <= 0: return None
        previous = int(gl.glGetIntegerv(0x8CA7))
        try:
            gl.glBindRenderbuffer(0x8D41, name.value)
            values = []
            for parameter in (0x8D44, 0x8D54, 0x8D55, 0x8CAB):
                value = ctypes.c_int()
                queries[1](0x8D41, parameter, ctypes.byref(value))
                values.append(value.value)
            return tuple(values)  # Internal format, depth/stencil bits, samples.
        finally:
            gl.glBindRenderbuffer(0x8D41, previous)

    def try_copy_depth(self, gl, output, expected_key, completed):
        """Copy completed depth only after exact source and storage agreement.

        This narrow capability accepts only the caller's explicitly owned,
        currently bound destination. Unsupported storage returns False before
        any pixel copy. Command failures raise, so the caller clears/rebuilds
        its image instead of trusting a possibly incomplete destination.
        """
        from PyQt6.QtGui import QOpenGLContext
        context = QOpenGLContext.currentContext()
        if (context is None or context is not self._context or self._fbo is None
                or self._key is None or self._key[0] != expected_key or self._completed != completed):
            return False
        target = int(output.handle())
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        width, height = self._size
        if (target <= 0 or int(gl.glGetIntegerv(0x8CA6)) != target
                or viewport != (0, 0, width, height)
                or (output.size().width(), output.size().height()) != self._size
                or output.format().samples() or int(gl.glGetIntegerv(0x80A9))):
            return False
        queries = self._depth_functions(context)
        if queries is None: return False
        read_target = int(gl.glGetIntegerv(0x8CAA))
        try:
            error = int(gl.glGetError())
            if error: raise RuntimeError("Shared simulation depth found existing GL error: 0x%04x" % error)
            source_format = self._depth_format(gl, self._fbo.handle(), queries)
            destination_format = self._depth_format(gl, target, queries)
            error = int(gl.glGetError())
            if error: raise RuntimeError("Shared simulation depth format query failed: 0x%04x" % error)
            if (source_format is None or source_format != destination_format
                    or source_format[1] <= 0 or source_format[3] != 0):
                return False
            self._bind(0x8CA8, self._fbo.handle())
            self._bind(0x8CA9, target)
            gl.glDisable(0x0C11)
            self._blit(0, 0, width, height, 0, 0, width, height, gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            error = int(gl.glGetError())
            if error: raise RuntimeError("Shared simulation depth copy failed: 0x%04x" % error)
            return True
        finally:
            try:
                try: self._bind(0x8CA8, read_target)
                finally: self._bind(0x8CA9, target)
            finally:
                gl.glViewport(*viewport)
