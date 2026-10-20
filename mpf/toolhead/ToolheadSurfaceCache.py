# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [b60b255] Qualify exact clipping on Apple software OpenGL and isolate EGL tests
# 2026-10-12 [0701dcb] Improve reflection continuity and reduce rendering overhead
# 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

"""Light-independent, owned visible surfaces for moving toolhead illumination."""
from __future__ import annotations

import numpy as np
import logging
from .ToolheadCamera import apply_camera_view


def surface_fragment():
    # Position is the native interpolated shading position, not a position
    # reconstructed from the expanded tube's depth. Store that depth separately.
    return """#version 410
in vec3 f_vertex;
in vec3 f_normal;
in vec4 f_color;
uniform int u_depthOnly;
uniform int u_surfaceKind;
layout(location=0) out vec4 surface_position;
layout(location=1) out vec4 surface_normal;
layout(location=2) out vec4 surface_colour;
void main() {
    if (f_color.a <= 0.0) discard;
    surface_position = vec4(f_vertex, gl_FragCoord.z);
    surface_normal = vec4(normalize(f_normal), float(u_surfaceKind));
    surface_colour = f_color;
}
"""


def light_extent(position, reach, padding, direction=None):
    position = np.asarray(position)
    lower, upper = np.full(3, -reach), np.full(3, reach)
    if direction is not None:
        try:
            normal = np.asarray(direction, dtype=float)
            length = float(np.linalg.norm(normal))
            if normal.shape == (3,) and np.isfinite(normal).all() and length > 1e-12:
                normal = normal/length
                rim = reach*np.sqrt(np.maximum(0., 1.-normal*normal))
                lower = np.where(normal <= 0., -reach, -rim)
                upper = np.where(normal >= 0., reach, rim)
        except (TypeError, ValueError): pass
    # Shader directions and coordinates are float32; keep their equator and
    # normalization uncertainty inside the CPU double-precision envelope.
    tolerance = 2e-5*max(1., abs(reach), float(np.max(np.abs(position))))
    return position+lower-padding-tolerance, position+upper+padding+tolerance


def light_scissor(camera, lights, width, height, padding=0.0, *, directions=None):
    """Project conservative light volumes; near-plane intersections use all pixels."""
    from .ToolheadFrameCache import projected_bounds
    matrix = np.eye(4)
    view = camera.getInverseWorldTransformation().getData()
    projection = camera.getProjectionMatrix().getData()
    lights = tuple(lights)
    try: directions = tuple(directions) if directions is not None else ()
    except TypeError: directions = ()
    if len(directions) != len(lights): directions = (None,)*len(lights)
    crops = [projected_bounds(light_extent(position, reach, padding, direction),
        matrix, view, projection, width, height) for (position, reach), direction in zip(lights, directions, strict=True)]
    crops = [crop for crop in crops if crop is not None]
    if not crops: return None
    left = min(crop[0] for crop in crops)
    bottom = min(crop[1] for crop in crops)
    right = max(crop[0] + crop[2] for crop in crops)
    top = max(crop[1] + crop[3] for crop in crops)
    return left, bottom, right - left, top - bottom


class ToolheadSurfaceCache:
    # One buffer set only: no persistent lower-layer or transient colour copies.
    MAX_BYTES = 512 * 1024 * 1024

    def __init__(self, fragment_source=None, *, depth_only=False):
        self._depth_only = bool(depth_only)
        self._context = self._fbo = self._size = self._key = None
        self._completed = None
        self._region = self._view_size = self._region_key = None
        self._programs = {}
        self._vao = None
        self._copy_checked = False
        self._software_scissor = False
        self._fragment_source = fragment_source

    def _select_region(self, width, height, rect, key):
        full = (0, 0, width, height)
        if rect is None: return full
        try:
            if len(rect) != 4 or any(type(value) is not int for value in rect): return full
            x, y, w, h = rect
            if w <= 0 or h <= 0: return full
            right, top = min(width, x+w), min(height, y+h)
            x, y = max(0, x), max(0, y)
            if right <= x or top <= y: return full
        except (TypeError, ValueError): return full
        previous = self._region if self._view_size == (width, height) and self._region_key == key else None
        if previous is not None:
            px, py, pw, ph = previous
            if px <= x and py <= y and px+pw >= right and py+ph >= top: return previous
        margin = max(128, (right-x)//4, (top-y)//4)
        left, bottom = max(0, (x-margin)//128*128), max(0, (y-margin)//128*128)
        right, top = min(width, (right+margin+127)//128*128), min(height, (top+margin+127)//128*128)
        # Pad new coverage once; padding the previous envelope again expands
        # unrelated axes each time a moving light escapes one edge.
        if previous is not None:
            left, bottom = min(left, px), min(bottom, py)
            right, top = max(right, px+pw), max(top, py+ph)
        return left, bottom, right-left, top-bottom

    def prepare(self, gl, key, completed, rebuild, append, *, rect=None):
        from PyQt6.QtGui import QOpenGLContext
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        context = QOpenGLContext.currentContext()
        if context is None or context.format().majorVersion() < 4:
            raise RuntimeError("Deferred lighting requires OpenGL 4.1")
        if context is not self._context:
            self._software_scissor = gl.glGetString(0x1F01) in ('Apple Software Renderer', b'Apple Software Renderer')
            self._context = context
            self._fbo = self._size = self._key = self._completed = self._vao = None
            self._region = self._view_size = self._region_key = None
            self._programs.clear()
            self._copy_checked = False
        target = int(gl.glGetIntegerv(0x8CA6))
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        width, height = viewport[2:]
        samples = int(gl.glGetIntegerv(0x80A9))
        region = self._select_region(width, height, rect, key)
        left, bottom, rw, rh = region
        size = rw, rh, samples
        if width <= 0 or height <= 0 or rw * rh * max(samples, 1) * 40 > self.MAX_BYTES:
            raise RuntimeError("Deferred lighting surface storage exceeds budget")
        if samples:
            # Resolving position/normal first changes edge shading; per-sample
            # reconstruction also differs from native centre interpolation.
            # Retain the established forward AA path on multisampled hosts.
            raise RuntimeError("Deferred lighting requires single-sample storage; preserving native antialiasing")
        try:
            if region != self._region or self._view_size != (width, height):
                self._key = self._completed = None
                self._copy_checked = False
            self._region, self._view_size, self._region_key = region, (width, height), key
            if self._size != size:
                self._key = self._completed = None
                self._copy_checked = False
                format_ = QOpenGLFramebufferObjectFormat()
                format_.setSamples(samples)
                format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth if self._depth_only
                                      else QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
                format_.setInternalTextureFormat(0x8814)  # RGBA32F: exact shading position and depth.
                self._fbo = QOpenGLFramebufferObject(rw, rh, format_)
                if not self._fbo.isValid(): raise RuntimeError("Deferred lighting framebuffer unavailable")
                self._fbo.addColorAttachment(rw, rh, 0x881A)
                self._fbo.addColorAttachment(rw, rh, 0x881A)
                self._size = size
                message = "Deferred lighting surfaces allocated: %dx%d, samples=%d, storage budget %.1f MiB" % (
                    rw, rh, samples, rw * rh * 40 / (1024 * 1024))
                try:
                    from UM.Logger import Logger
                except ImportError:
                    logging.getLogger(__name__).info(message)
                else:
                    Logger.log("i", message)
            if self._key == key and self._completed == completed: return
            if not self._fbo.bind(): raise RuntimeError("Deferred lighting framebuffer could not be bound")
            gl.glDrawBuffers(3, [0x8CE0, 0x8CE1, 0x8CE2])
            if gl.glCheckFramebufferStatus(0x8D40) != 0x8CD5:
                raise RuntimeError("Deferred lighting attachments incomplete")
            gl.glViewport(-left, -bottom, width, height)
            gl.glDisable(0x0C11)
            gl.glColorMask(True, True, True, True)
            gl.glDepthMask(True)
            gl.glDisable(gl.GL_BLEND)
            if self._key != key or self._completed is None or any(new < old for old, new in zip(self._completed, completed, strict=True)):
                gl.glClearColor(0, 0, 0, 0)
                gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
                rebuild()
            elif self._completed != completed:
                append(self._completed, completed)
            error = int(gl.glGetError())
            if error: raise RuntimeError("Deferred lighting surface update failed: 0x%04x" % error)
            self._key, self._completed = key, completed
        except Exception:
            self._key = self._completed = None
            raise
        finally:
            QOpenGLFramebufferObject.bindDefault() if target == 0 else gl.glBindFramebuffer(0x8D40, target)
            gl.glViewport(*viewport)

    def copy_depth(self, gl, *, rect=None):
        target = int(gl.glGetIntegerv(0x8CA6))
        x, y, width, height = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        left, bottom, right, top = 0, 0, width, height
        # Rectangle is local to the unchanged full-size source/target viewport.
        # Invalid admission falls back to the original complete depth copy.
        if rect is not None:
            try:
                if len(rect) != 4 or any(type(value) is not int for value in rect): raise ValueError('Invalid depth rectangle')
                rx, ry, rw, rh = rect
                if rw < 0 or rh < 0: raise ValueError('Invalid depth extent')
                left, bottom = max(0,rx), max(0,ry)
                right, top = min(width,rx+rw), min(height,ry+rh)
            except (TypeError, ValueError):
                left, bottom, right, top = 0, 0, width, height
        rx, ry, rw, rh = self._region or (0, 0, width, height)
        left, bottom = max(left, rx), max(bottom, ry)
        right, top = min(right, rx+rw), min(top, ry+rh)
        if right <= left or top <= bottom: return
        try:
            gl.glBindFramebuffer(0x8CA8, self._fbo.handle())
            gl.glBindFramebuffer(0x8CA9, target)
            gl.glBlitFramebuffer(left-rx, bottom-ry, right-rx, top-ry, x+left, y+bottom, x+right, y+top,
                gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            if not self._copy_checked:
                error = int(gl.glGetError())
                if error: raise RuntimeError("Deferred lighting depth copy failed: 0x%04x" % error)
                self._copy_checked = True
        finally:
            gl.glBindFramebuffer(0x8D40, target)

    def try_seed_depth(self, copy_completed_depth, *, region_copy=None):
        """Offer only this owned target to an optional completed-depth provider.

        The provider owns semantic, context and attachment-format admission.
        Rejection leaves the normal geometry rebuild available; an exception
        fails the capture so a partly changed target is cleared before fallback.
        """
        if self._fbo is None or copy_completed_depth is None: return False
        # A cropped attachment must never masquerade as a full-view target.
        if self._region is not None and self._region != (0, 0, *self._view_size):
            return False if region_copy is None else bool(region_copy(self._fbo, self._region))
        return bool(copy_completed_depth(self._fbo))

    def shade(self, gl, camera, node, *, padding=0.0):
        from PyQt6.QtOpenGL import QOpenGLVertexArrayObject
        from UM.View.GL.ShaderProgram import ShaderProgram
        if self._fbo is None: raise RuntimeError("Deferred lighting surfaces unavailable")
        sw, sh, samples = self._size
        width, height = self._view_size or (sw, sh)
        direction_provider = getattr(node, 'scene_light_directions', None)
        crop = light_scissor(camera, node.scene_light_bounds(), width, height, padding,
            directions=direction_provider() if callable(direction_provider) else None)
        if crop is None: return
        rx, ry, rw, rh = self._region or (0, 0, width, height)
        cx, cy, cw, ch = crop
        left, bottom = max(cx, rx), max(cy, ry)
        right, top = min(cx+cw, rx+rw), min(cy+ch, ry+rh)
        if right <= left or top <= bottom: return
        crop = left, bottom, right-left, top-bottom
        shader = self._programs.get(bool(samples))
        if shader is None:
            shader = ShaderProgram()
            vertex = "#version 410\nvoid main() { vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0); }"
            source = self._fragment_source
            if source is None: raise RuntimeError("Deferred lighting shader resource unavailable")
            source = source[:source.index("void main()")]
            # The same light function is used by the forward fragment.
            declarations = """
uniform SURFACE_SAMPLER u_surfacePosition;
uniform SURFACE_SAMPLER u_surfaceNormal;
uniform SURFACE_SAMPLER u_surfaceColour;
uniform vec2 u_surfaceOrigin;
uniform int u_lightBed;
uniform int u_lightModels;
void main() {
    ivec2 pixel = ivec2(gl_FragCoord.xy - u_surfaceOrigin);
    vec4 position = FETCH(u_surfacePosition);
    vec4 normal = FETCH(u_surfaceNormal);
    vec4 colour = FETCH(u_surfaceColour);
    if (normal.w < 0.5 || (normal.w < 1.5 ? u_lightBed == 0 : u_lightModels == 0)) discard;
    gl_FragDepth = position.w;
    frag_color = vec4(lightSurface(position.xyz, normal.xyz, colour.rgb), colour.a);
}
"""
            declarations = declarations.replace("SURFACE_SAMPLER", "sampler2D")
            declarations = declarations.replace("FETCH(", "texelFetch(")
            for name in ("u_surfacePosition", "u_surfaceNormal", "u_surfaceColour"):
                declarations = declarations.replace("texelFetch(" + name + ")", "texelFetch(" + name + ", pixel, 0)")
            if not shader.setVertexShader(vertex) or not shader.setFragmentShader(source + declarations):
                raise RuntimeError("Deferred lighting shader could not compile")
            shader.build()
            self._programs[bool(samples)] = shader
        if self._vao is None:
            self._vao = QOpenGLVertexArrayObject()
            if not self._vao.create(): raise RuntimeError("Deferred lighting VAO unavailable")
        node.apply_attached_lights(shader)
        apply_camera_view(shader, camera)
        shader.setUniformValue("u_viewPosition", camera.getWorldPosition())
        bed, models = node.scene_lighting_effects()
        shader.setUniformValue("u_lightBed", int(bed))
        shader.setUniformValue("u_lightModels", int(models))
        viewport = tuple(map(int, gl.glGetIntegerv(0x0BA2)))
        origin = viewport[:2]
        shader.setUniformValue("u_surfaceOrigin", [float(origin[0]+rx), float(origin[1]+ry)])
        texture_target = 0x0DE1
        textures = self._fbo.textures()
        active = int(gl.glGetIntegerv(0x84E0))
        try:
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glDisable(gl.GL_CULL_FACE)
            gl.glDepthMask(False)
            gl.glDepthFunc(gl.GL_LEQUAL)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE)
            gl.glEnable(0x0C11)
            gl.glScissor(origin[0] + crop[0], origin[1] + crop[1], crop[2], crop[3])
            if self._software_scissor:
                # The shader samples in window coordinates, so a smaller
                # viewport preserves its inputs while avoiding broken scissoring.
                gl.glDisable(0x0C11)
                gl.glViewport(origin[0]+crop[0], origin[1]+crop[1], crop[2], crop[3])
            for index, texture in enumerate(textures):
                gl.glActiveTexture(0x84C0 + index)
                gl.glBindTexture(texture_target, texture)
                shader.setUniformValue(("u_surfacePosition", "u_surfaceNormal", "u_surfaceColour")[index], index)
            self._vao.bind()
            if shader.bind() is False: raise RuntimeError("Deferred lighting shader could not be bound")
            gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
            error = int(gl.glGetError())
            if error: raise RuntimeError("Deferred lighting draw failed: 0x%04x" % error)
        finally:
            shader.release()
            self._vao.release()
            for index in range(3):
                gl.glActiveTexture(0x84C0 + index)
                gl.glBindTexture(texture_target, 0)
            gl.glActiveTexture(active)
            gl.glDisable(0x0C11)
            if self._software_scissor: gl.glViewport(*viewport)
