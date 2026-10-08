"""Retain the shaded head between unrelated Qt Quick composition frames."""
from __future__ import annotations

import ctypes
import itertools
from types import SimpleNamespace

import numpy as np


def projected_bounds(bounds, transform, view, projection, width, height):
    """Conservative pixel crop of a box; near-camera boxes use the full view."""
    corners = np.array([(*point, 1.) for point in itertools.product(*zip(*bounds, strict=True))])
    clip = corners @ (projection @ view @ transform).T
    if not np.isfinite(clip).all() or np.any(clip[:, 3] <= 1e-6):
        return 0, 0, width, height
    pixels = (clip[:, :2] / clip[:, 3:4] + 1) * [width / 2, height / 2]
    left, bottom = np.maximum(0, np.floor(pixels.min(axis=0)) - 2).astype(int)
    right, top = np.minimum([width, height], np.ceil(pixels.max(axis=0)) + 2).astype(int)
    if right <= left or top <= bottom: return None
    # Small projected-size changes should not churn GPU allocations on every
    # telemetry move. Tile the conservative crop, never shrink its coverage.
    left, bottom = (int(left) // 32) * 32, (int(bottom) // 32) * 32
    right, top = min(width, ((int(right) + 31) // 32) * 32), min(height, ((int(top) + 31) // 32) * 32)
    return left, bottom, right - left, top - bottom


class ToolheadFrameCache:
    def __init__(self, *, depth_only=False):
        self._depth_only = bool(depth_only)
        self._key = self._size = None
        self._fbo = self._resolved = self._blitter = None
        self._bind = None
        self._context = None

    def draw(self, gl, camera, bounds, transform, state, render, *, additive=False, opacity=1.0,
             depth_seed=None, depth_revision=None, post_render=None, animate=None):
        from PyQt6.QtCore import QRect, QRectF
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat, QOpenGLTextureBlitter
        from UM.Math.Matrix import Matrix
        context = QOpenGLContext.currentContext()
        if context is None: raise RuntimeError("Toolhead graphics context unavailable")
        if self._context is not None and self._context is not context:
            # VAOs, programs and FBO names belong to a context generation.
            self._key = self._size = self._fbo = self._resolved = self._blitter = self._bind = None
        self._context = context
        target = int(gl.glGetIntegerv(0x8CA6))
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        vx, vy, width, height = viewport
        if width <= 0 or height <= 0: return
        view = camera.getInverseWorldTransformation().getData()
        projection = camera.getProjectionMatrix().getData()
        model = transform.getData()
        crop = (0, 0, width, height) if bounds is None else projected_bounds(bounds, model, view, projection, width, height)
        if crop is None: return
        left, bottom, cw, ch = crop
        samples = int(gl.glGetIntegerv(0x80A9))
        if self._bind is None:
            address = context.getProcAddress(b"glBindFramebuffer")
            if not address: raise RuntimeError("Toolhead framebuffer binding unavailable")
            self._bind = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_uint)(int(address))
        blend_colour = None
        blend_equations = (int(gl.glGetIntegerv(0x8009)), int(gl.glGetIntegerv(0x883D)))
        try:
            if self._size != (cw, ch, samples):
                self._key = None
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth if self._depth_only and not samples
                                      else QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                format_.setSamples(samples)
                self._fbo = QOpenGLFramebufferObject(cw, ch, format_)
                if not self._fbo.isValid(): raise RuntimeError("Toolhead framebuffer unavailable")
                self._resolved = QOpenGLFramebufferObject(cw, ch) if samples else None
                if self._resolved is not None and not self._resolved.isValid():
                    raise RuntimeError("Toolhead antialiasing resolve unavailable")
                self._size = cw, ch, samples
            if self._blitter is None:
                self._blitter = QOpenGLTextureBlitter()
                if not self._blitter.create(): raise RuntimeError("Toolhead texture composition unavailable")
            key = state, model.tobytes(), view.tobytes(), projection.tobytes(), crop, viewport, depth_revision
            if self._key != key:
                seeded = depth_seed is None
                if not self._fbo.bind(): raise RuntimeError("Toolhead framebuffer could not be bound")
                gl.glViewport(0, 0, cw, ch)
                gl.glDisable(0x0C11)  # Scissor: own crop is the complete target.
                gl.glColorMask(True, True, True, True)
                gl.glDepthMask(True)
                gl.glClearColor(0, 0, 0, 0)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                if depth_seed is not None:
                    try:
                        seeded = depth_seed(gl, self._fbo, camera, viewport, crop)
                    except Exception:
                        seeded = False
                    # A failed partial copy must not leave stale occluders.
                    self._fbo.bind()
                    gl.glViewport(0, 0, cw, ch)
                    gl.glColorMask(True, True, True, True)
                    gl.glDepthMask(True)
                    if not seeded: gl.glClear(gl.GL_DEPTH_BUFFER_BIT)
                if crop == (0, 0, width, height):
                    # Shared depth requires exactly the native projection.
                    # Even identity multiplication can change signed zeros or
                    # promote its dtype; a full-viewport image needs no proxy.
                    cropped_camera = camera
                    render(camera)
                else:
                    crop_matrix = np.eye(4)
                    crop_matrix[0, 0], crop_matrix[1, 1] = width / cw, height / ch
                    crop_matrix[0, 3] = (width - 2 * left) / cw - 1
                    crop_matrix[1, 3] = (height - 2 * bottom) / ch - 1
                    cropped_projection = Matrix(crop_matrix @ projection)
                    cropped_camera = SimpleNamespace(getProjectionMatrix=lambda: cropped_projection,
                        getInverseWorldTransformation=camera.getInverseWorldTransformation,
                        getWorldPosition=camera.getWorldPosition,
                        getCameraLightPosition=camera.getCameraLightPosition)
                    render(cropped_camera)
                self._cropped_camera = cropped_camera
                if post_render is not None:
                    try:
                        seeded = bool(post_render(gl, self._fbo, cropped_camera)) and seeded
                    except Exception:
                        seeded = False
                if self._resolved is not None:
                    QOpenGLFramebufferObject.blitFramebuffer(self._resolved, self._fbo)
                self._key = key if seeded else None
            texture = self._resolved if self._resolved is not None else self._fbo
            if animate is not None:
                texture = animate(gl, self._fbo, self._cropped_camera, self._size)
                if texture is self._fbo and self._resolved is not None:
                    # Optional animation may return the single-pose MSAA
                    # fallback. A multisample FBO has no sampleable texture.
                    QOpenGLFramebufferObject.blitFramebuffer(self._resolved, self._fbo)
                    texture = self._resolved
            self._bind(0x8D40, target)
            gl.glViewport(*viewport)
            gl.glDisable(gl.GL_DEPTH_TEST)
            gl.glDisable(gl.GL_CULL_FACE)
            gl.glDisable(0x0C11)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendEquationSeparate(0x8006, 0x8006)  # GL_FUNC_ADD, including final shutter composition
            # Fade the completed premultiplied image once, never overlapping
            # CAD faces individually. Qt's blitter scales alpha only, so the
            # constant source factor supplies the matching RGB scaling.
            if opacity != 1.0:
                blend_colour = tuple(map(float, gl.glGetFloatv(0x8005)))  # GL_BLEND_COLOR
                gl.glBlendColor(0., 0., 0., opacity)
                gl.glBlendFuncSeparate(0x8003, gl.GL_ONE if additive else gl.GL_ONE_MINUS_SRC_ALPHA,
                                      gl.GL_ONE, gl.GL_ONE if additive else gl.GL_ONE_MINUS_SRC_ALPHA)
            else:
                gl.glBlendFunc(gl.GL_ONE, gl.GL_ONE if additive else gl.GL_ONE_MINUS_SRC_ALPHA)
            self._blitter.bind()
            self._blitter.setOpacity(opacity)
            destination = QRectF(vx + left, vy + height - bottom - ch, cw, ch)
            matrix = QOpenGLTextureBlitter.targetTransform(destination, QRect(vx, vy, width, height))
            self._blitter.blit(texture.texture(), matrix, QOpenGLTextureBlitter.Origin.OriginBottomLeft)
        finally:
            try:
                if self._blitter is not None: self._blitter.release()
            finally:
                try:
                    try:
                        if blend_colour is not None: gl.glBlendColor(*blend_colour)
                    finally: gl.glBlendEquationSeparate(*blend_equations)
                finally:
                    try: self._bind(0x8D40, target)
                    finally: gl.glViewport(*viewport)
