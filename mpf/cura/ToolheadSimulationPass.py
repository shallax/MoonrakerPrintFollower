"""Opt-in public simulation-pass adapter with immutable path buffer storage.

Native current/fractional shaders and lower/current/fractional draw order remain
unchanged; qualified shadow lower layers may use equivalent instanced tubes.
Completed paths retain colour and depth; new complete lines append, while the
fractional segment stays transient. Native view, pass and mesh stay unchanged.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from ..toolhead.ToolheadPathGeometry import ToolheadPathGeometry
from ..toolhead.ToolheadSimulationCache import ToolheadSimulationCache
from ..toolhead.ToolheadInstancedShadow import ToolheadInstancedShadow


def value_key(value):
    if hasattr(value, "getData"): return np.asarray(value.getData()).tobytes()
    if isinstance(value, np.ndarray): return value.shape, value.dtype.str, value.tobytes()
    if isinstance(value, (tuple, list)): return tuple(value_key(item) for item in value)
    return repr(value)


def path_ranges(data, view):
    current, minimum, path = int(view.getCurrentLayer()), int(view.getMinimumLayer()), float(view.getCurrentPath())
    counts = data.getElementCounts()
    if current not in counts or minimum > current or not math.isfinite(path) or path < 0:
        return None
    start = int(sum(count for layer, count in counts.items() if layer < minimum))
    top = int(sum(count for layer, count in counts.items() if layer < current))
    end = top + int(path) * 2
    if end > top + int(counts[current]): return None
    partial = None
    layer = data.getLayer(current)
    if layer is None: return None
    index = int(path)
    if path % 1 > .0001:
        for polygon in layer.polygons:
            if index >= len(polygon.data):
                index -= len(polygon.data)
                continue
            if index + 1 < len(polygon.data):
                partial = polygon.data[index], polygon.data[index + 1], path % 1
            break
    if partial is not None and end + 2 > top + int(counts[current]): return None
    return start, top, end, partial


def _colour_bytes(data, count):
    """View the working public float32 colour API without another array copy."""
    raw = data.getColorsAsByteArray()
    if not isinstance(raw, (bytes, bytearray, memoryview)) or len(raw) != count * 16:
        raise ValueError("native float32 colour bytes have wrong size")
    return np.frombuffer(raw, dtype=np.float32).reshape(count, 4)


def path_mesh(data):
    """Share native immutable arrays; add missing previous-type values once."""
    if data.hasAttribute("prev_line_types"): return data
    from UM.Mesh.MeshData import MeshData
    from cura.LayerPolygon import LayerPolygon
    attributes = {name: data.getAttribute(name) for name in data.attributeNames()}
    types = np.asarray(attributes["line_types"]["value"], dtype=np.float32)
    previous = np.empty_like(types)
    if len(previous):
        previous[0] = LayerPolygon.MoveUnretractedType
        previous[1:] = types[:-1]
    previous.flags.writeable = False
    attributes["prev_line_types"] = dict(value=previous, opengl_type="float", opengl_name="a_prev_line_type")
    vertices = data.getVertices()
    try: colours = data.getColors()
    except (TypeError, ValueError): colours = _colour_bytes(data, len(vertices))
    return MeshData(vertices=vertices, normals=data.getNormals(), indices=data.getIndices(),
                    colors=colours, uvs=data.getUVCoordinates(), attributes=attributes)


class ToolheadSimulationPass:
    def __init__(self, original, renderer, view, root, eligible_callback, *,
                 active_extruder=None, starts_colour=None, timing=None):
        self.original, self.renderer = original, renderer
        self._view, self._root, self._eligible = view, root, eligible_callback
        self._active_extruder, self._starts_colour = active_extruder, starts_colour
        self._context = self._failed_context = self._fbo = self._size = None
        self._shaders = self._geometry = None
        self._cache = ToolheadSimulationCache()
        self._data = None
        self._owned_output = self._closed = False
        self._shadow = None
        self._previous = None
        self._last_source = None
        self._logged = False
        self._depth_snapshot = self._depth_risks = None
        self._depth_starts_alpha = None
        self._depth_logged = False
        self._depth_rejections = set()
        self._depth_proof_failure = None
        if timing is None:
            from ..diagnostics.RenderTiming import RenderTiming
            timing = RenderTiming()
        self._timing = timing
        self._instanced_shadow = ToolheadInstancedShadow(diagnostic=getattr(timing, "enabled", False))
        self._shadow_source = None
        self._handle_shader = None

    def getName(self): return self.original.getName()
    def getSize(self): return self.original.getSize()
    def setSize(self, width, height): self.original.setSize(width, height)
    def getPriority(self): return self.original.getPriority()
    def isEnabled(self): return self.original.isEnabled()
    def setEnabled(self, value): self.original.setEnabled(value)
    def getTextureId(self): return self._fbo.texture() if self._owned_output else self.original.getTextureId()
    def getOutput(self): return self._fbo.toImage() if self._owned_output else self.original.getOutput()
    def getCompletedLayerShadowMode(self):
        """Observed native material mode; unknown before a valid source transition."""
        return self._shadow if not self._closed and self._last_source is not None else None
    def bind(self): self.original.bind()
    def release(self): self.original.release()

    def get_visible_depth_revision(self, camera, viewport):
        """Identity of our complete rendered depth, including fractional paths."""
        snapshot = self._depth_snapshot
        if not self._owned_output or snapshot is None or self._closed:
            return None
        from PyQt6.QtGui import QOpenGLContext
        try:
            if QOpenGLContext.currentContext() is not self._context or viewport != (0, 0, *self._size):
                return None
            if (value_key(camera.getInverseWorldTransformation()) != snapshot["key"][3]
                    or value_key(camera.getProjectionMatrix()) != snapshot["key"][4]
                    or self._depth_view_key(self._view) != snapshot["settings"]
                    or float(self._view.getCurrentPath()) != snapshot["path"]
                    or int(self._view.getCurrentLayer()) != snapshot["key"][5]
                    or int(self._view.getMinimumLayer()) != snapshot["key"][6]):
                return None
            source = self._source()
            if source is None or source[0] is not snapshot["child"] or source[1] is not snapshot["data"]:
                return None
            if value_key(source[0].getWorldTransformation()) != snapshot["key"][2]:
                return None
            return snapshot["key"], snapshot["path"], snapshot["settings"], int(self._fbo.handle())
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
            return None

    def copy_visible_depth(self, gl, output, camera, viewport, crop, revision):
        """Copy only our owned full-output depth into a matching head crop."""
        if revision is None or self.get_visible_depth_revision(camera, viewport) != revision:
            return False
        from PyQt6.QtCore import QRect
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject
        left, bottom, width, height = crop
        try:
            if (output.format().samples() or self._fbo.format().samples()
                    or int(gl.glGetIntegerv(0x80A9))
                    or (output.size().width(), output.size().height()) != (width, height)
                    or int(gl.glGetIntegerv(0x8CA6)) != int(output.handle())
                    or left < 0 or bottom < 0 or width <= 0 or height <= 0
                    or left + width > viewport[2] or bottom + height > viewport[3]):
                return False
            queries = self._cache._depth_functions(self._context)
            if queries is None:
                return False
            source_format = self._cache._depth_format(gl, self._fbo.handle(), queries)
            if source_format is None or source_format != self._cache._depth_format(gl, output.handle(), queries):
                return False
            QOpenGLFramebufferObject.blitFramebuffer(output, QRect(0, 0, width, height),
                self._fbo, QRect(left, bottom, width, height), gl.GL_DEPTH_BUFFER_BIT, 0x2600)
            return not gl.glGetError()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return False
        finally:
            output.bind()
            gl.glViewport(0, 0, width, height)

    def set_scene(self, view, root):
        if view is not self._view or root is not self._root:
            self._view, self._root = view, root
            self._instanced_shadow.close()
            self._data = self._geometry = self._shadow = self._previous = None
            self._last_source = None
            self._owned_output = False
            self._depth_snapshot = self._depth_risks = None

    def close(self):
        if self.renderer.getRenderPass(self.getName()) is self:
            self.renderer.removeRenderPass(self)
            self.renderer.addRenderPass(self.original)
        self._instanced_shadow.close()
        self._closed = True
        self._owned_output = False
        self._fbo = self._geometry = self._shaders = None
        self._cache = None
        self._depth_snapshot = self._depth_risks = None

    def _source(self):
        from UM.Scene.SceneNode import SceneNode
        from UM.Scene.ToolHandle import ToolHandle
        if self._closed or not self._eligible() or self._view.getCompatibilityMode(): return None
        if self._view.getCurrentLayerMesh() is not None or self._view.getCurrentLayerJumps() is not None: return None
        sources = []
        for child in self._root.getAllChildren():
            if isinstance(child, ToolHandle): continue
            if not isinstance(child, SceneNode) or not child.isVisible(): continue
            if (getattr(child, "isOutsideBuildArea", lambda: False)()
                    or child.callDecoration("isAssignedToDisabledExtruder")): return None
            data = child.callDecoration("getLayerData")
            if data is not None:
                if not child.getMeshData() and not child.callDecoration("isBlockSlicing"): continue
                ranges = path_ranges(data, self._view)
                if ranges is None: return None
                sources.append((child, data, ranges))
        return sources[0] if len(sources) == 1 else None

    def _uniforms(self, camera):
        from UM.Math.Color import Color
        if self._active_extruder is None:
            from cura.Settings.ExtruderManager import ExtruderManager
            active = ExtruderManager.getInstance().activeExtruderIndex
        else: active = self._active_extruder()
        if self._starts_colour is None:
            from UM.Application import Application
            starts = Color(*Application.getInstance().getTheme().getColor("layerview_starts").getRgb())
        else: starts = self._starts_colour()
        alpha = getattr(starts, "a", None)
        if alpha is None and isinstance(starts, (tuple, list)) and len(starts) == 4: alpha = starts[3]
        self._depth_starts_alpha = float(alpha) if alpha is not None else None
        signature = []
        def upload(shader, name, value):
            shader.setUniformValue(name, value)
            signature.append((name, value_key(value)))
        normal, shadow = self._shaders
        upload(normal, "u_active_extruder", float(max(0, active)))
        upload(normal, "u_starts_color", starts)
        for shader in (normal, shadow):
            upload(shader, "u_lightPosition", camera.getCameraLightPosition())
            for uniform, getter in (("u_layer_view_type", "getSimulationViewType"), ("u_extruder_opacity", "getExtruderOpacities"),
                    ("u_show_travel_moves", "getShowTravelMoves"), ("u_show_helpers", "getShowHelpers"),
                    ("u_show_skin", "getShowSkin"), ("u_show_infill", "getShowInfill"), ("u_show_starts", "getShowStarts")):
                value = getattr(self._view, getter)()
                shader.setUniformValue(uniform, value)
                signature.append((uniform, value_key(value)))
            for metric, getter in (("feedrate", "Feedrate"), ("thickness", "Thickness"),
                                   ("line_width", "LineWidth"), ("flow_rate", "FlowRate")):
                for limit in ("min", "max"):
                    name = "u_" + limit + "_" + metric
                    value = getattr(self._view, "get" + limit.title() + getter)()
                    shader.setUniformValue(name, value)
                    signature.append((name, value_key(value)))
        return tuple(signature)

    def _draw(self, context, source):
        from PyQt6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        from UM.View.GL.OpenGL import OpenGL
        from UM.PluginRegistry import PluginRegistry
        from UM.Application import Application
        child, data, ranges = source
        gl = OpenGL.getInstance().getBindingsObject()
        camera = Application.getInstance().getController().getScene().getActiveCamera()
        if camera is None: raise RuntimeError("Simulation camera unavailable")
        if self._shaders is None:
            directory = Path(PluginRegistry.getInstance().getPluginPath("SimulationView"))
            self._shadow_source = directory / "layers3d_shadow.shader"
            self._shaders = tuple(OpenGL.getInstance().createShaderProgram(str(directory / filename))
                                  for filename in ("layers3d.shader", "layers3d_shadow.shader"))
            if any(shader is None for shader in self._shaders): raise RuntimeError("Native simulation shaders unavailable")
        if data is not self._data:
            self._geometry = ToolheadPathGeometry(path_mesh(data))
            self._data = data
        width, height = self.getSize()
        if width <= 0 or height <= 0: raise RuntimeError("Simulation target size unavailable")
        if self._size != (width, height):
            format_ = QOpenGLFramebufferObjectFormat()
            format_.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
            self._fbo = QOpenGLFramebufferObject(width, height, format_)
            if not self._fbo.isValid(): raise RuntimeError("Simulation framebuffer unavailable")
            self._size = width, height
        signature = self._uniforms(camera)
        bound = False
        try:
            bound = True
            if not self._fbo.bind(): raise RuntimeError("Simulation framebuffer could not be bound")
            gl.glViewport(0, 0, width, height)
            gl.glDisable(0x0C11)
            gl.glColorMask(True, True, True, True)
            gl.glDepthMask(True)
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glDepthFunc(gl.GL_LESS)
            gl.glDisable(gl.GL_BLEND)
            normal, shadow = self._shaders
            transform = child.getWorldTransformation()
            start, top, end, partial = ranges
            normal.setUniformValue("u_last_vertex", [math.nan] * 3)
            normal.setUniformValue("u_next_vertex", [math.nan] * 3)
            normal.setUniformValue("u_last_line_ratio", 1.)
            def completed(first, last):
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glDepthFunc(gl.GL_LESS)
                gl.glDepthMask(True)
                gl.glDisable(gl.GL_BLEND)
                gl.glDisable(gl.GL_CULL_FACE)
                gl.glColorMask(True, True, True, True)
                if last > first: self._geometry.render(normal, camera, transform, [(first, last)], gl)
            def rebuild():
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glDepthFunc(gl.GL_LESS)
                gl.glDisable(gl.GL_BLEND)
                gl.glDepthMask(True)
                gl.glColorMask(True, True, True, True)
                gl.glEnable(gl.GL_CULL_FACE)
                if top > start:
                    instanced = self._shadow and self._instanced_shadow.render(self._geometry.mesh, self._shadow_source,
                        camera, transform, start, top, self._view, gl)
                    if not instanced: self._geometry.render(shadow if self._shadow else normal, camera, transform, [(start, top)], gl)
                completed(top, end)
            key = (id(data), id(child), value_key(transform), value_key(camera.getInverseWorldTransformation()),
                   value_key(camera.getProjectionMatrix()), int(self._view.getCurrentLayer()),
                   int(self._view.getMinimumLayer()), self._shadow, signature)
            self._cache.restore(gl, self._fbo, key, end, rebuild, completed)
            if partial is not None:
                gl.glEnable(gl.GL_DEPTH_TEST)
                gl.glDepthFunc(gl.GL_LESS)
                gl.glDepthMask(True)
                gl.glDisable(gl.GL_BLEND)
                gl.glDisable(gl.GL_CULL_FACE)
                gl.glColorMask(True, True, True, True)
                normal.setUniformValue("u_last_vertex", list(map(float, partial[0])))
                normal.setUniformValue("u_next_vertex", list(map(float, partial[1])))
                normal.setUniformValue("u_last_line_ratio", float(partial[2]))
                self._geometry.render(normal, camera, transform, [(end, end + 2)], gl)
            try:
                raster = (int(gl.glGetIntegerv(0x0B46)), int(gl.glGetIntegerv(0x0B45)),
                    tuple(map(float, gl.glGetFloatv(0x0B70))),
                    bool(gl.glIsEnabled(0x8037)), bool(gl.glIsEnabled(0x864F)))
            except (TypeError, ValueError): raster = None
            self._depth_snapshot = dict(data=data, child=child, key=key, start=start, top=top, end=end,
                settings=self._depth_view_key(self._view), shadow=self._shadow,
                starts_alpha=self._depth_starts_alpha, raster=raster, path=float(self._view.getCurrentPath()))
            self._draw_handles(camera)
        finally:
            if bound: self._fbo.release()

    def _draw_handles(self, camera):
        """Selection handles must not retire cached G-code depth or material mode."""
        from UM.Scene.ToolHandle import ToolHandle
        handles = [child for child in self._root.getAllChildren()
            if isinstance(child, ToolHandle) and child.getSolidMesh() is not None]
        if not handles: return
        from UM.Resources import Resources
        from UM.View.GL.OpenGL import OpenGL
        from UM.View.RenderBatch import RenderBatch
        if self._handle_shader is None:
            self._handle_shader = OpenGL.getInstance().createShaderProgram(Resources.getPath(Resources.Shaders, "toolhandle.shader"))
        batch = RenderBatch(self._handle_shader, type=RenderBatch.RenderType.Overlay, backface_cull=True)
        for handle in handles:
            batch.addItem(handle.getWorldTransformation(), mesh=handle.getSolidMesh())
        batch.render(camera)

    @staticmethod
    def _depth_view_key(view):
        return tuple(value_key(getattr(view, "get" + getter)()) for getter in
            ("SimulationViewType", "ExtruderOpacities", "ShowTravelMoves", "ShowHelpers",
             "ShowSkin", "ShowInfill", "ShowStarts"))

    def _depth_geometry_risks(self, data, transform):
        """One immutable mesh proof, reused across camera/progress requests."""
        matrix = np.asarray(transform.getData())
        identity = data, matrix.tobytes()
        if self._depth_risks is not None and self._depth_risks[:2] == identity:
            return self._depth_risks[2]
        self._depth_risks = data, identity[1], None
        self._depth_proof_failure = None
        try:
            risk = self._depth_geometry_proof(data, matrix)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
            self._depth_proof_failure = "geometry metadata invalid (%s: %s)" % (type(error).__name__, str(error)[:160])
            return None
        self._depth_risks = data, identity[1], risk
        return risk

    def _depth_geometry_proof(self, data, matrix):
        def unsupported(reason):
            self._depth_proof_failure = reason
            return None
        if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
                or not np.allclose(matrix[3], [0, 0, 0, 1]) or np.linalg.det(matrix[:3, :3]) <= 0):
            return unsupported("model transform is not finite affine positive-winding")
        vertices = np.asarray(data.getVertices())
        pairs = np.asarray(data.getIndices()).reshape(-1, 2)
        types = np.asarray(data.getAttribute("line_types")["value"]).reshape(-1)
        dimensions = np.asarray(data.getAttribute("line_dimensions")["value"])
        material = np.asarray(data.getAttribute("colors")["value"])
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices)
                or dimensions.shape != (len(vertices), 2) or types.size != len(vertices)
                or material.shape != (len(vertices), 4) or not np.issubdtype(pairs.dtype, np.integer)):
            return unsupported("native vertex/attribute/index layout invalid")
        # Bound proof temporaries independently of print size. Native arrays
        # remain shared; only sparse support/prime element offsets are retained.
        chunk_lines = 65536
        low, high = np.full(3, np.inf), np.full(3, -np.inf)
        maximum_dimension = 0.
        material_alpha = material[:, 3]
        material_opaque = True
        for first in range(0, len(vertices), chunk_lines * 2):
            last = first + chunk_lines * 2
            block, dims = vertices[first:last], dimensions[first:last]
            if not np.isfinite(block).all() or not np.isfinite(dims).all():
                return unsupported("nonfinite native vertex or line dimensions")
            low = np.minimum(low, block.min(axis=0))
            high = np.maximum(high, block.max(axis=0))
            maximum_dimension = max(maximum_dimension, float(np.max(np.abs(dims))))
            material = material_alpha[first:last]
            material_opaque = material_opaque and bool(np.all(np.isfinite(material) & (material > 0)))
        support, prime = [], []
        for first in range(0, len(pairs), chunk_lines):
            block = pairs[first:first + chunk_lines]
            line_types = types[block[:, 0]]
            # Preserve native float32 endpoint rounding before world transform.
            left, right = vertices[block[:, 0]].copy(), vertices[block[:, 1]].copy()
            left[:, 1] -= dimensions[block[:, 0], 1] / 2
            right[:, 1] -= dimensions[block[:, 1], 1] / 2
            # Large Nx3 matmul can hang in Cura's bundled OpenBLAS worker
            # pool. Direct contraction keeps this bounded proof single-threaded.
            delta = np.einsum('ij,kj->ik', right - left, matrix[:3, :3], optimize=False)
            # Culling equivalence for oblique nontravel 3D tubes has not been
            # qualified; retain the original depth pass for those sources.
            travel = np.isin(line_types, [8, 9, 12, 13])
            oblique = (delta[:, 1] != 0) & np.any(delta[:, [0, 2]] != 0, axis=1) & ~travel
            if np.any(oblique): return unsupported("oblique nontravel tube culling equivalence unqualified")
            support.append((np.flatnonzero(line_types == 4) + first) * 2)
            prime.append((np.flatnonzero(line_types == 11) + first) * 2)
        center = (low + high) / 2 @ matrix[:3, :3].T + matrix[:3, 3]
        extent = (high - low) / 2 @ np.abs(matrix[:3, :3]).T
        padding = .2 + 2 * maximum_dimension * (1 + float(np.linalg.norm(matrix[:3, :3])))
        risk = dict(bounds=(center - extent - padding, center + extent + padding),
            support=np.concatenate(support) if support else np.empty(0, dtype=np.int64),
            prime=np.concatenate(prime) if prime else np.empty(0, dtype=np.int64),
            colour_opaque=None, colour_failure=None, material_opaque=material_opaque, vertex_count=len(vertices))
        return risk

    @staticmethod
    def _depth_line_alpha(data, risk):
        """Lazy alpha proof; Cura 5.13's ndarray colour getter raises ValueError.

        Its public byte getter works. Scan that float32 view once and release
        the temporary bytes; material/metric colour modes never need the copy.
        """
        if risk["colour_opaque"] is not None: return risk["colour_opaque"]
        try:
            try: values = np.asarray(data.getColors())
            except (TypeError, ValueError): values = np.empty(0)
            count = risk["vertex_count"]
            if values.shape != (count, 4):
                values = _colour_bytes(data, count)
            valid = True
            for first in range(0, count, 131072):
                alpha = values[first:first + 131072, 3]
                if not np.all(np.isfinite(alpha) & (alpha > 0)):
                    valid = False
                    break
            risk["colour_opaque"] = valid
        except (AttributeError, IndexError, TypeError, ValueError) as error:
            risk["colour_opaque"] = False
            risk["colour_failure"] = "line colour alpha proof failed (%s: %s)" % (type(error).__name__, str(error)[:160])
        return risk["colour_opaque"]

    def try_copy_completed_depth(self, gl, output, camera, paths, view):
        """Public, optional depth capability; stock/private FBOs are never read."""
        def rejected(reason):
            # Profiling diagnostics never issue GL calls or repeat a reason.
            # A fixed cap also bounds logging if a malformed view keeps changing.
            if (getattr(self._timing, "enabled", False) and reason not in self._depth_rejections
                    and len(self._depth_rejections) < 12):
                self._depth_rejections.add(reason)
                try:
                    from UM.Logger import Logger
                    Logger.log("i", "toolhead shared simulation depth rejected: %s", reason)
                except Exception: pass
            return False
        snapshot = self._depth_snapshot
        if (snapshot is None or not self._owned_output or self._closed or not self._eligible()
                or self._failed_context is self._context or view is not self._view or len(paths) != 1):
            return rejected("owned source/view unavailable or multiple paths")
        from PyQt6.QtGui import QOpenGLContext
        if QOpenGLContext.currentContext() is not self._context: return rejected("graphics context changed")
        try:
            source = self._source()
            if source is None or source[0] is not snapshot["child"] or source[1] is not snapshot["data"]:
                return rejected("native source eligibility or identity changed")
            geometry, transform, bounds, _lit, uniforms, top = paths[0]
            end = int(bounds[1]) - (2 if math.isfinite(uniforms["u_last_vertex"][0]) else 0)
            if (geometry.mesh is not snapshot["data"] or (bounds[0], top, end) !=
                    (snapshot["start"], snapshot["top"], snapshot["end"])):
                return rejected("path identity or completed prefix changed")
            if value_key(transform) != snapshot["key"][2]: return rejected("model matrix differs")
            if value_key(camera.getInverseWorldTransformation()) != snapshot["key"][3]:
                return rejected("view matrix differs")
            if value_key(camera.getProjectionMatrix()) != snapshot["key"][4]:
                return rejected("projection matrix differs")
            if self._depth_view_key(view) != snapshot["settings"]: return rejected("view settings changed")
            if bool(view.getShowTravelMoves()): return rejected("travel moves visible (open strips)")
            raster = (int(gl.glGetIntegerv(0x0B46)), int(gl.glGetIntegerv(0x0B45)),
                tuple(map(float, gl.glGetFloatv(0x0B70))),
                bool(gl.glIsEnabled(0x8037)), bool(gl.glIsEnabled(0x864F)))
            if raster != snapshot["raster"] or raster != (0x0901, 0x0405, (0., 1.), False, False):
                return rejected("cull/winding/depth-range raster state differs")
            risks = self._depth_geometry_risks(snapshot["data"], transform)
            if risks is None: return rejected(self._depth_proof_failure or "geometry proof unavailable")
            position = camera.getWorldPosition()
            position = np.asarray([position.x, position.y, position.z])
            if not np.isfinite(position).all() or np.all((position >= risks["bounds"][0]) & (position <= risks["bounds"][1])):
                return rejected("camera inside padded source bounds or nonfinite")
            mode = int(view.getSimulationViewType())
            if mode not in range(6): return rejected("unsupported colour view mode")
            if (mode == 0 and not risks["material_opaque"]) or (mode == 1 and not self._depth_line_alpha(snapshot["data"], risks)):
                return rejected(risks["colour_failure"] or "source colour alpha is zero/nonfinite (whole source proof)")
            if bool(view.getShowStarts()) and (snapshot["starts_alpha"] is None or snapshot["starts_alpha"] <= 0):
                return rejected("visible start marker alpha unavailable or zero")
            def older_present(indices):
                return np.searchsorted(indices, top) > np.searchsorted(indices, bounds[0])
            if snapshot["shadow"]:
                # Inset0 start markers are restored by the receiver pass. Only
                # old support starts are absent from shadow depth, while prime
                # towers are an extra shadow occluder when helpers are hidden.
                if bool(view.getShowStarts()) and older_present(risks["support"]):
                    return rejected("older support starts absent from native shadow depth")
                if not bool(view.getShowHelpers()) and older_present(risks["prime"]):
                    return rejected("hidden older prime tower remains in native shadow depth")
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return rejected("malformed geometry/view metadata")
        copied = self._cache.try_copy_depth(gl, output, snapshot["key"], end)
        if not copied: return rejected("completed depth cache/storage/destination unavailable")
        if copied and not self._depth_logged and getattr(self._timing, "enabled", False):
            self._depth_logged = True
            try:
                from UM.Logger import Logger
                Logger.log("i", "toolhead shared simulation depth admitted: %sx%s, completed elements %s",
                    output.size().width(), output.size().height(), end)
            except Exception: pass
        return copied

    def render(self):
        source = context = None
        try:
            from PyQt6.QtGui import QOpenGLContext
            from UM.View.GL.OpenGLContext import OpenGLContext
            context = QOpenGLContext.currentContext()
            if context is not None and not OpenGLContext.isLegacyOpenGL(): source = self._source()
            current = int(self._view.getCurrentLayer()), float(self._view.getCurrentPath())
            if context is not self._context:
                self._handle_shader = None
                self._instanced_shadow.close()
                self._context = context
                self._failed_context = self._fbo = self._size = self._shaders = self._geometry = self._data = None
                self._cache = ToolheadSimulationCache()
                self._depth_snapshot = self._depth_risks = None
                self._depth_logged = False
                self._depth_rejections.clear()
                self._shadow = self._previous = None
                self._last_source = None
                self._logged = False
            identity = (source[0], source[1]) if source is not None else None
            if identity != self._last_source:
                self._shadow = self._previous = None
                self._last_source = identity
            previous_shadow = self._shadow
            previous_layer = self._previous[0] if self._previous is not None else None
            if source is not None and self._previous is not None:
                if current[1] != self._previous[1]: self._shadow = True
                if not self._view.isSimulationRunning() and current[0] != self._previous[0]: self._shadow = False
            # Keep the original's private transition state synchronized through
            # its public render(), never by reading or mutating its internals.
            warm = previous_shadow != self._shadow or previous_layer != current[0]
            self._previous = current
            if source is not None and self._shadow is not None and not warm and self._failed_context is not context:
                self._timing.measure("native simulation", lambda: self._draw(context, source))
                self._owned_output = True
                if not self._logged:
                    try:
                        from UM.Logger import Logger
                        Logger.log("i", "Cached native simulation active: retained completed paths, stable EBO/VAO, native current/fractional shaders")
                    except Exception: pass
                    self._logged = True
                return
            if source is None: self._shadow = None
        except Exception as error:
            if source is None:
                self._shadow = self._previous = self._last_source = None
            self._failed_context = context
            try:
                from UM.Logger import Logger
                Logger.log("w", "Cached simulation unavailable; using native rendering: %s", str(error))
            except Exception: pass
        self._owned_output = False
        self.original.render()
