"""Owned depth storage: unchanged lower layers need not be re-extruded per move."""
from __future__ import annotations

import ctypes
import logging


class ToolheadDepthCache:
    def __init__(self):
        self._fbo = None
        self._key = None
        self._size = None
        self._bind = self._blit = None
        self._copy_checked = False
        self._context = None

    def _functions(self, context):
        bind = context.getProcAddress(b"glBindFramebuffer")
        blit = context.getProcAddress(b"glBlitFramebuffer")
        if not bind or not blit: raise RuntimeError("Depth framebuffer copying unavailable")
        self._bind = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint)(int(bind))
        self._blit = ctypes.CFUNCTYPE(None, *([ctypes.c_int] * 8), ctypes.c_uint, ctypes.c_uint)(int(blit))

    def restore(self, gl, key, draw, *, append=None):
        """Refresh static depth on camera/layer/filter changes, copy it otherwise.

        The current layer's moving prefix is drawn after this copy, so backward
        scrubs and partial moves cannot leave stale depth in the cached layers.
        This FBO belongs to the plugin; no native render-pass storage is accessed.
        """
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        context = QOpenGLContext.currentContext()
        if self._context is not context:
            # Owned framebuffer names and function pointers belong to the
            # current Qt context generation, even when the view is unchanged.
            self._fbo = self._key = self._size = self._bind = self._blit = None
            self._copy_checked = False
        self._context = context
        if context is None: raise RuntimeError("Lighting graphics context unavailable")
        if self._bind is None: self._functions(context)
        target = int(gl.glGetIntegerv(0x8CA6))
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        x, y, width, height = viewport
        if width <= 0 or height <= 0: raise RuntimeError("Lighting viewport unavailable")
        samples = int(gl.glGetIntegerv(0x80A9))
        try:
            if self._size != (width, height, samples):
                self._key = None
                self._copy_checked = False
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setSamples(samples)
                format_.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                self._fbo = QOpenGLFramebufferObject(width, height, format_)
                self._size = width, height, samples
                if not self._fbo.isValid(): raise RuntimeError("Lighting depth storage unavailable")
            if self._key != key:
                if not self._fbo.bind(): raise RuntimeError("Lighting depth storage could not be bound")
                gl.glViewport(0, 0, width, height)
                gl.glDepthMask(True)
                # A growing completed prefix can append depth into the
                # existing storage. Backward scrubs and scene changes return
                # False and rebuild; fractional segments are never retained.
                if append is None or self._key is None or not append(self._key, key):
                    gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                    draw()
            # A valid source FBO does not guarantee compatible destination
            # depth storage. In particular, depth formats must match for a
            # blit. ctypes GL calls report failure through GL's error flag,
            # rather than raising the RuntimeError used by our caller's
            # uncached fallback. Validate once per storage allocation to avoid
            # querying the driver on every cached-composition frame.
            if not self._copy_checked:
                existing_error = int(gl.glGetError())
                if existing_error:
                    message = "Lighting depth copy validation found existing GL error 0x%04x" % existing_error
                    logging.getLogger(__name__).warning(message)
                    raise RuntimeError(message)
            self._bind(0x8CA8, self._fbo.handle())  # Read framebuffer.
            self._bind(0x8CA9, target)  # Draw framebuffer.
            self._blit(0, 0, width, height, x, y, x + width, y + height, gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            if not self._copy_checked:
                copy_error = int(gl.glGetError())
                if copy_error:
                    raise RuntimeError("Lighting depth copy failed with GL error 0x%04x" % copy_error)
                self._copy_checked = True
            self._key = key
        except RuntimeError:
            self._key = None
            self._copy_checked = False
            self._fbo = self._size = None
            raise
        finally:
            try:
                self._bind(0x8D40, target)
            finally:
                gl.glViewport(*viewport)
