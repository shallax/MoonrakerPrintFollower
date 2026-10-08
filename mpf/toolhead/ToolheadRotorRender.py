"""Fixed-cost shutter samples, each starting from the same static colour/depth."""
from __future__ import annotations
from .ToolheadGLState import preserved_state


class ToolheadRotorRender:
    def __init__(self):
        self._context = self._size = None
        self._work = self._resolve = self._result = self._blitter = None
        self._key = None

    def combine(self, gl, static, camera, size, render, *, blurred, cache_key=None):
        from PyQt6.QtCore import QRect, QRectF
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLTextureBlitter
        context = QOpenGLContext.currentContext()
        width, height, samples = size
        # Bound NEW shutter storage before allocating; large viewports retain
        # sharp single-pose animation using the existing static target.
        if width*height*(max(1,samples)*12 + (4 if samples else 0) + 8) > 128*1024*1024:
            raise RuntimeError("Fan blur exceeds its graphics memory budget")
        if context is None: raise RuntimeError('Rotor graphics context unavailable')
        if cache_key is not None and self._context is context and self._size == size and self._key == cache_key:
            return self._result
        self._key = None
        with preserved_state(gl, context):
            if context is not self._context or size != self._size:
                self._context = self._size = None
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setAttachment(static.attachment())
                format_.setSamples(samples)
                self._work = QOpenGLFramebufferObject(width, height, format_)
                self._resolve = QOpenGLFramebufferObject(width, height) if samples else None
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setInternalTextureFormat(0x881A)  # RGBA16F: average premultiplied samples.
                self._result = QOpenGLFramebufferObject(width, height, format_)
                if not self._work.isValid() or not self._result.isValid() or (self._resolve is not None and not self._resolve.isValid()):
                    raise RuntimeError('Rotor shutter targets unavailable')
                self._blitter = QOpenGLTextureBlitter()
                if not self._blitter.create(): raise RuntimeError('Rotor shutter composition unavailable')
                self._context, self._size = context, size
            if not self._result.bind(): raise RuntimeError('Rotor result target unavailable')
            gl.glDisable(gl.GL_SCISSOR_TEST); gl.glColorMask(True, True, True, True)
            gl.glViewport(0,0,width,height); gl.glClearColor(0,0,0,0); gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            positions = (-.5,0.,.5) if blurred else (0.,)
            for fraction in positions:
                # Copy BOTH colour and depth before EACH pose. One sample must
                # never occlude the following sample or stack translucent alpha.
                QOpenGLFramebufferObject.blitFramebuffer(self._work, static,
                    buffers=gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                if int(gl.glGetError()): raise RuntimeError('Rotor static colour/depth copy failed')
                if not self._work.bind(): raise RuntimeError('Rotor pose target unavailable')
                gl.glViewport(0,0,width,height)
                render(camera, fraction)
                if self._resolve is not None: QOpenGLFramebufferObject.blitFramebuffer(self._resolve, self._work)
                texture = self._resolve or self._work
                self._result.bind(); gl.glViewport(0,0,width,height)
                gl.glDisable(gl.GL_DEPTH_TEST); gl.glDisable(gl.GL_CULL_FACE); gl.glEnable(gl.GL_BLEND)
                weight = 1./len(positions)
                gl.glBlendColor(0,0,0,weight)
                gl.glBlendFuncSeparate(0x8003,gl.GL_ONE,gl.GL_ONE,gl.GL_ONE)
                gl.glBlendEquationSeparate(0x8006,0x8006)
                self._blitter.bind(); self._blitter.setOpacity(weight)
                try:
                    matrix = QOpenGLTextureBlitter.targetTransform(QRectF(0,0,width,height),QRect(0,0,width,height))
                    self._blitter.blit(texture.texture(),matrix,QOpenGLTextureBlitter.Origin.OriginBottomLeft)
                finally: self._blitter.release()
        # Restoration is part of a successful composition. A failed guard
        # exit must never mark partially delivered graphics state as reusable.
        self._key = cache_key
        return self._result
