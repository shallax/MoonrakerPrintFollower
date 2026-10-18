# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead
# 2026-10-10 [80c4b26] Complete directional reflections and reduce rendering overhead
# 2026-10-08 [e2f777d] Add toolhead materials, scene reflections and fan animation

"""Fixed-cost shutter samples, each starting from the same static colour/depth."""
from __future__ import annotations
from contextlib import contextmanager, nullcontext
from weakref import ref
from collections import OrderedDict
from .ToolheadGLState import preserved_state, preserved_samples
from .ToolheadSampleTarget import ToolheadSampleTarget, sample_blit


class ToolheadRotorRender:
    def __init__(self):
        self._context = self._size = None
        self._work = self._resolve = self._result = self._blitter = None
        self._key = None
        self._background = self._update_region = None
        self._generation = self._retirement = None
        self._pool = OrderedDict()
        self._pool_bytes = self._pool_hits = 0

    def _clear_pool(self):
        self._pool.clear()
        self._pool_bytes = 0

    def retire_for_ordinary(self, width, height, samples):
        """Keep only bounded, exact-format single-sample scratch pairs."""
        if isinstance(self._work, ToolheadSampleTarget):
            self.release_targets()
            return
        if self._size == (width, height, samples): return
        if self._work is not None and self._size is not None and self._size[2] == 0:
            w, h, _ = self._size
            charge = w*h*(12 + (8 if self._result is not None else 0))
            if charge <= 48 << 20:
                key = self._size, self._result is None, self._work.attachment()
                replaced = self._pool.pop(key, None)
                if replaced is not None: self._pool_bytes -= replaced[2]
                self._pool[key] = self._work, self._result, charge
                self._pool_bytes += charge
                while len(self._pool) > 4 or self._pool_bytes > 48 << 20:
                    _, (_, _, old_charge) = self._pool.popitem(last=False)
                    self._pool_bytes -= old_charge
        self._work = self._resolve = self._result = None
        self._size = self._key = self._background = None

    def _take_targets(self, size, sharp, attachment):
        pair = self._pool.pop((size, sharp, attachment), None)
        if pair is None: return None
        self._pool_bytes -= pair[2]
        if not pair[0].isValid() or pair[1] is not None and not pair[1].isValid(): return None
        self._pool_hits += 1
        return pair[:2]

    def release_targets(self):
        """Retire obsolete shutter storage before another crop is admitted."""
        previous = self._work
        if isinstance(previous, ToolheadSampleTarget): previous.close()
        self._work = self._resolve = self._result = self._blitter = None
        self._size = self._key = self._background = None
        self._clear_pool()

    def sharp(self, gl, static, camera, size, render, *, cache_key=None):
        """Keep the static image immutable when the full shutter is too large."""
        return self.combine(gl, static, camera, size, render, blurred=False,
            cache_key=('sharp', cache_key) if cache_key is not None else None, _sharp_only=True)

    @contextmanager
    def _region_scope(self, region):
        self._update_region = region
        try: yield
        finally: self._update_region = None

    def _apply_region(self, gl):
        if self._update_region is None:
            gl.glDisable(gl.GL_SCISSOR_TEST)
        else:
            gl.glEnable(gl.GL_SCISSOR_TEST)
            gl.glScissor(*self._update_region)

    def combine(self, gl, static, camera, size, render, *, blurred, cache_key=None,
                region=None, background_key=None, _sharp_only=False):
        """Reuse the averaged exterior only for a complete swept-motion region.

        The caller's background key includes every non-motion input. A changed
        region or shutter sample count first rebuilds the entire result.
        """
        from PyQt6.QtCore import QRect, QRectF
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLTextureBlitter
        context = QOpenGLContext.currentContext()
        width, height, samples = size
        # Bound NEW shutter storage before allocating; large viewports retain
        # sharp single-pose animation using the existing static target.
        explicit = isinstance(static, ToolheadSampleTarget)
        storage = 36 if explicit else max(1,samples)*12
        if width*height*(storage + (4 if samples else 0) + (0 if _sharp_only else 8)) > 128*1024*1024:
            raise RuntimeError("Fan blur exceeds its graphics memory budget")
        if context is None: raise RuntimeError('Rotor graphics context unavailable')
        if explicit and (samples != 4 or static._context is not context or not static.isValid()):
            raise RuntimeError('Rotor samples require the exact live owned target')
        if self._context is not context:
            self._clear_pool()
            self._generation=object();token=self._generation;owner=ref(self)
            def retired():
                instance=owner()
                if instance is not None and instance._generation is token:
                    instance._context=instance._size=instance._work=instance._resolve=instance._result=instance._blitter=instance._key=None
                    instance._generation=None
                    instance._background=None
                    instance._clear_pool()
            self._retirement=retired
            signal=getattr(context,'aboutToBeDestroyed',None)
            if signal is not None:
                from PyQt6.QtCore import Qt
                signal.connect(retired,Qt.ConnectionType.DirectConnection)
        if cache_key is not None and self._context is context and self._size == size and self._key == cache_key and (self._result is None) == _sharp_only and isinstance(self._work,ToolheadSampleTarget) == explicit and (not explicit or self._work.isValid()):
            return (self._resolve or self._work) if _sharp_only else self._result
        background = eligible_region = None
        if not samples and not _sharp_only and region is not None:
            x,y,w,h = region
            if 0 <= x < x+w <= width and 0 <= y < y+h <= height:
                eligible_region = tuple(region)
                if background_key is not None:
                    background = (background_key, id(static), eligible_region, bool(blurred))
        partial = (background is not None and self._background == background and
                   self._context is context and self._size == size and self._result is not None)
        self._key = self._background = None
        with self._region_scope(tuple(region) if partial else None), preserved_state(gl, context), (preserved_samples(gl, context) if explicit else nullcontext()):
            if context is not self._context or size != self._size or (self._result is None) != _sharp_only or isinstance(self._work,ToolheadSampleTarget) != explicit or (explicit and not self._work.isValid()):
                if not explicit and self._context is context:
                    self.retire_for_ordinary(width, height, samples)
                pooled = self._take_targets(size, _sharp_only, static.attachment()) if not explicit and not samples else None
                previous_size = self._size
                previous_context = self._context
                self._context = self._size = None
                previous = self._work
                self._work = self._resolve = self._result = None
                if previous_context is not context: self._blitter = None
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
                              else pooled[0] if pooled is not None else QOpenGLFramebufferObject(width, height, format_))
                self._resolve = QOpenGLFramebufferObject(width, height) if samples else None
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setInternalTextureFormat(0x881A)  # RGBA16F: average premultiplied samples.
                self._result = pooled[1] if pooled is not None else None if _sharp_only else QOpenGLFramebufferObject(width, height, format_)
                if not self._work.isValid() or (self._result is not None and not self._result.isValid()) or (self._resolve is not None and not self._resolve.isValid()):
                    raise RuntimeError('Rotor shutter targets unavailable')
                if not _sharp_only and self._blitter is None:
                    self._blitter = QOpenGLTextureBlitter()
                    if not self._blitter.create(): raise RuntimeError('Rotor shutter composition unavailable')
                self._context, self._size = context, size
            if explicit:
                if self._work.positions != static.positions:
                    raise RuntimeError('Rotor work sample pattern differs from its seed')
                gl.glEnable(0x809D)
                for flag in (0x8E51,0x80A0,0x809E,0x809F,0x8C36):gl.glDisable(flag)
            self._apply_region(gl)
            gl.glColorMask(True, True, True, True)
            gl.glDepthMask(True)
            if not _sharp_only:
                if not self._result.bind(): raise RuntimeError('Rotor result target unavailable')
                gl.glViewport(0,0,width,height); gl.glClearColor(0,0,0,0); gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            positions = (-.5,0.,.5) if blurred else (0.,)
            accumulation_region = self._update_region
            for index, fraction in enumerate(positions):
                # The first pose establishes this frame's complete exterior.
                # All later poses have identical pixels outside swept rotor
                # bounds, even when the camera/head/background changed.
                self._update_region = eligible_region if partial or index else None
                # Copy BOTH colour and depth before EACH pose. One sample must
                # never occlude the following sample or stack translucent alpha.
                self._apply_region(gl)
                if explicit:
                    sample_blit(gl,self._work,static,buffers=gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                else:
                    QOpenGLFramebufferObject.blitFramebuffer(self._work, static,
                        buffers=gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                if int(gl.glGetError()): raise RuntimeError('Rotor static colour/depth copy failed')
                if not self._work.bind(): raise RuntimeError('Rotor pose target unavailable')
                gl.glViewport(0,0,width,height)
                self._apply_region(gl)
                render(camera, fraction)
                if self._resolve is not None:
                    if explicit:sample_blit(gl,self._resolve,self._work,buffers=gl.GL_COLOR_BUFFER_BIT)
                    else:QOpenGLFramebufferObject.blitFramebuffer(self._resolve, self._work)
                texture = self._resolve or self._work
                if _sharp_only: continue
                # A fresh frame still averages the FULL work image three
                # times, preserving the original RGBA16F addition rounding.
                self._update_region = accumulation_region
                self._apply_region(gl)
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
        self._background = background
        return (self._resolve or self._work) if _sharp_only else self._result
