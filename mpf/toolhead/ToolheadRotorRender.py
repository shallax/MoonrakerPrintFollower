"""Fixed-cost shutter samples, each starting from the same static colour/depth."""
from __future__ import annotations
from contextlib import nullcontext
from weakref import ref
from .ToolheadGLState import preserved_state, preserved_samples
from .ToolheadSampleTarget import ToolheadSampleTarget, sample_blit


class ToolheadRotorRender:
    def __init__(self):
        self._context = self._size = None
        self._work = self._resolve = self._result = self._blitter = None
        self._key = None
        self._generation = self._retirement = None

    def release_targets(self):
        """Retire obsolete shutter storage before another crop is admitted."""
        previous = self._work
        if isinstance(previous, ToolheadSampleTarget): previous.close()
        self._work = self._resolve = self._result = self._blitter = None
        self._size = self._key = None

    def combine(self, gl, static, camera, size, render, *, blurred, cache_key=None):
        from PyQt6.QtCore import QRect, QRectF
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLTextureBlitter
        context = QOpenGLContext.currentContext()
        width, height, samples = size
        # Bound NEW shutter storage before allocating; large viewports retain
        # sharp single-pose animation using the existing static target.
        explicit = isinstance(static, ToolheadSampleTarget)
        storage = 36 if explicit else max(1,samples)*12
        if width*height*(storage + (4 if samples else 0) + 8) > 128*1024*1024:
            raise RuntimeError("Fan blur exceeds its graphics memory budget")
        if context is None: raise RuntimeError('Rotor graphics context unavailable')
        if explicit and (samples != 4 or static._context is not context or not static.isValid()):
            raise RuntimeError('Rotor samples require the exact live owned target')
        if self._context is not context:
            self._generation=object();token=self._generation;owner=ref(self)
            def retired():
                instance=owner()
                if instance is not None and instance._generation is token:
                    instance._context=instance._size=instance._work=instance._resolve=instance._result=instance._blitter=instance._key=None
                    instance._generation=None
            self._retirement=retired
            signal=getattr(context,'aboutToBeDestroyed',None)
            if signal is not None:
                from PyQt6.QtCore import Qt
                signal.connect(retired,Qt.ConnectionType.DirectConnection)
        if cache_key is not None and self._context is context and self._size == size and self._key == cache_key and isinstance(self._work,ToolheadSampleTarget) == explicit and (not explicit or self._work.isValid()):
            return self._result
        self._key = None
        with preserved_state(gl, context), (preserved_samples(gl, context) if explicit else nullcontext()):
            if context is not self._context or size != self._size or isinstance(self._work,ToolheadSampleTarget) != explicit or (explicit and not self._work.isValid()):
                previous_size = self._size
                self._context = self._size = None
                previous = self._work
                self._work = self._resolve = self._result = self._blitter = None
                if isinstance(previous, ToolheadSampleTarget):
                    try: previous.close()
                    except Exception:
                        self._work = previous
                        self._size = previous_size
                        raise
                previous = None
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setAttachment(static.attachment())
                format_.setSamples(samples)
                self._work = (ToolheadSampleTarget(gl,width,height) if explicit
                              else QOpenGLFramebufferObject(width, height, format_))
                self._resolve = QOpenGLFramebufferObject(width, height) if samples else None
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setInternalTextureFormat(0x881A)  # RGBA16F: average premultiplied samples.
                self._result = QOpenGLFramebufferObject(width, height, format_)
                if not self._work.isValid() or not self._result.isValid() or (self._resolve is not None and not self._resolve.isValid()):
                    raise RuntimeError('Rotor shutter targets unavailable')
                self._blitter = QOpenGLTextureBlitter()
                if not self._blitter.create(): raise RuntimeError('Rotor shutter composition unavailable')
                self._context, self._size = context, size
            if explicit:
                if self._work.positions != static.positions:
                    raise RuntimeError('Rotor work sample pattern differs from its seed')
                gl.glEnable(0x809D)
                for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36):gl.glDisable(flag)
            if not self._result.bind(): raise RuntimeError('Rotor result target unavailable')
            gl.glDisable(gl.GL_SCISSOR_TEST); gl.glColorMask(True, True, True, True)
            gl.glViewport(0,0,width,height); gl.glClearColor(0,0,0,0); gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            positions = (-.5,0.,.5) if blurred else (0.,)
            for fraction in positions:
                # Copy BOTH colour and depth before EACH pose. One sample must
                # never occlude the following sample or stack translucent alpha.
                if explicit:
                    sample_blit(gl,self._work,static,buffers=gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                else:
                    QOpenGLFramebufferObject.blitFramebuffer(self._work, static,
                        buffers=gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                if int(gl.glGetError()): raise RuntimeError('Rotor static colour/depth copy failed')
                if not self._work.bind(): raise RuntimeError('Rotor pose target unavailable')
                gl.glViewport(0,0,width,height)
                render(camera, fraction)
                if self._resolve is not None:
                    if explicit:sample_blit(gl,self._resolve,self._work,buffers=gl.GL_COLOR_BUFFER_BIT)
                    else:QOpenGLFramebufferObject.blitFramebuffer(self._resolve, self._work)
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
