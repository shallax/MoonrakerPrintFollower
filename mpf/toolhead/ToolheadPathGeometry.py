"""Cached path indices and conservative spatial bounds for owned lighting draws."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from weakref import WeakValueDictionary
import numpy as np


class UploadArray(np.ndarray):
    # Bundled Uranium still calls this removed NumPy spelling.
    def tostring(self): return self.tobytes()


class PathVertexMesh:
    """Owned upload identity; never populate Cura's mutable LayerData VBO cache."""
    def __init__(self, mesh):
        self.source = mesh
        self._attributes = {name: dict(mesh.getAttribute(name)) for name in mesh.attributeNames()}
        for attribute in self._attributes.values():
            if "value" in attribute: attribute["value"] = np.asarray(attribute["value"]).view(UploadArray)
        if "line_types" in self._attributes:
            types = np.asarray(self._attributes["line_types"]["value"], dtype=np.float32)
            previous = np.empty_like(types).view(UploadArray)
            if len(previous):
                previous[0] = 8  # Public LayerPolygon.MoveUnretractedType.
                previous[1:] = types[:-1]
            previous.flags.writeable = False
            self._attributes["prev_line_types"] = dict(value=previous, opengl_type="float", opengl_name="a_prev_line_type")

    # Explicit upload API: no delegated host cache properties or broken native
    # getColors() getter. Geometry/bytes remain shared until the one GPU upload.
    def getVertexCount(self): return self.source.getVertexCount()
    def getVertices(self): return self.source.getVertices()
    def getIndices(self): return self.source.getIndices()
    def getNormals(self): return self.source.getNormals()
    def getUVCoordinates(self): return self.source.getUVCoordinates()
    def hasNormals(self): return self.source.hasNormals()
    def hasColors(self): return self.source.hasColors()
    def hasUVCoordinates(self): return self.source.hasUVCoordinates()
    def getVerticesAsByteArray(self): return self.source.getVerticesAsByteArray()
    def getNormalsAsByteArray(self): return self.source.getNormalsAsByteArray()
    def getColorsAsByteArray(self): return self.source.getColorsAsByteArray()
    def getUVCoordinatesAsByteArray(self): return self.source.getUVCoordinatesAsByteArray()
    def attributeNames(self): return sorted(self._attributes)
    def getAttribute(self, name): return self._attributes[name]


_vertex_meshes = WeakValueDictionary()


class ToolheadPathGeometry:
    """Share immutable native arrays; own vertex/index buffers and VAOs.

    RenderBatch's range path recreates/uploads the index buffer every draw.
    Our full immutable index buffer is uploaded once and ranges use offsets.
    Bounds are built once in blocks of 512 lines, retaining crossing segments.
    """
    BLOCK = 1024  # Two index elements per line.

    def __init__(self, mesh, indices=None, *, build_bounds=True, index_chunk=None):
        self.mesh = mesh
        self._index_chunk = index_chunk
        source_indices = np.asarray(mesh.getIndices() if indices is None else indices).reshape(-1)
        # Native signed 32-bit indices already have the exact EBO bit layout.
        # Viewing those bytes avoids a second whole-print CPU allocation.
        self._indices = (source_indices if index_chunk else source_indices.view(np.uint32)
            if source_indices.dtype == np.int32 else np.asarray(source_indices, dtype=np.uint32))
        self._element_base = 0
        self._exterior = self._exterior_offsets = None
        self._index_buffer = self._draw_elements = None
        self._context = None
        self._vertex_mesh = None
        self._vaos = {}
        self._normal_key = self._normal_matrix = None
        self._world_key = None
        self._world_bounds = None
        # Most callers use retained deferred surfaces or native simulation,
        # neither of which needs per-block forward-light bounds.
        self._bounds = None if build_bounds else np.empty((0, 2, 3), dtype=np.float32)

    def _ensure_bounds(self):
        if self._bounds is not None: return
        mesh = self.mesh
        vertices = mesh.getVertices()
        bounds = []
        try: dimensions = mesh.getAttribute("line_dimensions")
        except KeyError: dimensions = None
        padding = max(0.1, float(np.max(dimensions["value"])) * 2) if dimensions is not None else 2.0
        for start in range(0, len(self._indices), self.BLOCK):
            points = vertices[self._indices[start:start + self.BLOCK]]
            bounds.append((points.min(axis=0) - padding, points.max(axis=0) + padding))
        self._bounds = np.asarray(bounds, dtype=np.float32).reshape(-1, 2, 3)

    def exterior_geometry(self):
        """Compact immutable EBO for Inset0 boundaries, including hole walls.

        Skip interior vertices before even running the vertex shader. The VBO
        retains Cura's immutable arrays, so category/end caps/dimensions agree.
        """
        if self._exterior is None:
            try: attribute = self.mesh.getAttribute("line_types")
            except KeyError: attribute = None
            if attribute is None: return None
            types = np.asarray(attribute["value"]).reshape(-1)
            pairs = self._indices.reshape(-1, 2)
            selected = types[pairs[:, 0]] == 1  # Public LayerPolygon.Inset0Type.
            self._exterior_offsets = np.flatnonzero(selected) * 2
            self._exterior = ToolheadPathGeometry(self.mesh, pairs[selected])
        return self._exterior

    def exterior_range(self, start, end):
        """Map an original element prefix to the compact boundary EBO."""
        return tuple(int(np.searchsorted(self._exterior_offsets, point)) * 2 for point in (start, end))

    def ranges(self, transform, visible, lights):
        """Conservative cube/sphere overlap, in world space; never sample lines."""
        if not lights: return []
        self._ensure_bounds()
        if not len(self._bounds): return []
        matrix = np.asarray(transform.getData())
        key = matrix.tobytes()
        if key != self._world_key:
            center = self._bounds.mean(axis=1)
            extent = (self._bounds[:, 1] - self._bounds[:, 0]) / 2
            # Avoid the bundled BLAS worker pool for print-sized Nx3 arrays.
            center = np.einsum('ij,kj->ik', center, matrix[:3, :3], optimize=False) + matrix[:3, 3]
            extent = np.einsum('ij,kj->ik', extent, np.abs(matrix[:3, :3]), optimize=False)
            self._world_bounds = (center - extent, center + extent)
            self._world_key = key
        # Unprinted or hidden blocks cannot contribute to this prefix. Keep
        # the entire boundary block, including long crossing segments.
        first = max(0, int(visible[0]) // self.BLOCK)
        last = min(len(self._bounds), (int(visible[1]) + self.BLOCK - 1) // self.BLOCK)
        if first >= last: return []
        low, high = (bound[first:last] for bound in self._world_bounds)
        keep = np.zeros(len(low), dtype=bool)
        for position, reach in lights:
            delta = np.maximum(np.maximum(low - position, position - high), 0)
            keep |= np.einsum('ij,ij->i', delta, delta) <= reach * reach
        result = []
        for local_index in np.flatnonzero(keep):
            index = int(local_index) + first
            start = max(visible[0], int(index) * self.BLOCK)
            end = min(visible[1], (int(index) + 1) * self.BLOCK, len(self._indices))
            if start >= end: continue
            if result and result[-1][1] == start:
                result[-1] = (result[-1][0], end)
            else:
                result.append((start, end))
        return result

    def render(self, shader, camera, transform, ranges, gl):
        if not ranges: return
        ranges = self._validate_ranges(ranges)
        with self.draw_session(shader, camera, transform, gl) as draw:
            draw(ranges)

    def _validate_ranges(self, ranges):
        ranges = [(int(start), int(end)) for start, end in ranges]
        if any(start < 0 or end < start or end > len(self._indices) or end-start > 0x7fffffff
               for start, end in ranges):
            raise RuntimeError("Lighting index range exceeds owned buffer")
        if self._index_chunk and (len(ranges) != 1 or ranges[0][1]-ranges[0][0] > self._index_chunk):
            raise RuntimeError("Reflection index chunk exceeds budget")
        return ranges

    @contextmanager
    def draw_session(self, shader, camera, transform, gl):
        """Keep frozen bindings only inside one bounded environment turn."""
        from PyQt6.QtOpenGL import QOpenGLBuffer, QOpenGLVertexArrayObject
        from PyQt6.QtGui import QOpenGLContext
        from UM.View.GL.OpenGL import OpenGL
        from UM.Math.Matrix import Matrix
        vertex_buffer = vao = index_buffer = failure = None
        vao_bound = vertex_bound = index_bound = shader_bound = False
        try:
            context = QOpenGLContext.currentContext()
            if context is None: raise RuntimeError("Lighting path OpenGL context unavailable")
            if context is not self._context:
                # Qt owns share-group retirement. Do not destroy/bind an old
                # context's VAO or buffer names in its replacement context.
                self._context = context
                self._index_buffer = self._draw_elements = None
                self._vaos.clear()
            if self._draw_elements is None:
                address = context.getProcAddress(b"glDrawElements")
                if not address: raise RuntimeError("Lighting index draw unavailable")
                # PyQt's wrapper accepts client arrays, not EBO byte offsets.
                self._draw_elements = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.c_int,
                    ctypes.c_uint, ctypes.c_void_p)(int(address))
            self.vertex_mesh(context)
            vertex_buffer = OpenGL.getInstance().createVertexBuffer(self._vertex_mesh)
            if vertex_buffer is None or int(vertex_buffer.bufferId()) <= 0:
                raise RuntimeError("Lighting vertex buffer unavailable")
            vao_key = (shader, id(vertex_buffer), vertex_buffer.bufferId())
            cached = self._vaos.get(vao_key)
            vao = cached[0] if cached is not None else QOpenGLVertexArrayObject()
            if cached is None and not vao.create(): raise RuntimeError("Lighting path VAO could not be created")
            vao_bound = True
            vao.bind()
            vertex_bound = True
            if not vertex_buffer.bind(): raise RuntimeError("Lighting vertex buffer could not be bound")
            index_buffer = self._index_buffer
            if index_buffer is None:
                index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
                if not index_buffer.create(): raise RuntimeError("Lighting path index buffer could not be created")
            if int(index_buffer.bufferId()) <= 0: raise RuntimeError("Lighting path index buffer unavailable")
            index_bound = True
            if not index_buffer.bind(): raise RuntimeError("Lighting path index buffer could not be bound")
            shader_bound = True
            if shader.bind() is False: raise RuntimeError("Lighting path shader could not be bound")
            normal_key = transform.getData().tobytes()
            if self._normal_key != normal_key:
                normal = Matrix(transform.getData())
                normal.setRow(3, [0, 0, 0, 1])
                normal.setColumn(3, [0, 0, 0, 1])
                normal.invert()
                normal.transpose()
                self._normal_key, self._normal_matrix = normal_key, normal
            shader.updateBindings(model_matrix=transform, normal_matrix=self._normal_matrix,
                view_matrix=camera.getInverseWorldTransformation(), projection_matrix=camera.getProjectionMatrix(),
                view_position=camera.getWorldPosition(), light_0_position=camera.getCameraLightPosition())
            if cached is None:
                required = self._configure_attributes(shader)
                if vertex_buffer.size() < required: raise RuntimeError("Lighting vertex buffer storage incomplete")
                # Retaining the wrapper also prevents buffer identity reuse
                # while this VAO still references its vertex attributes.
                self._vaos[vao_key] = (vao, vertex_buffer)
            def draw(ranges):
                ranges = self._validate_ranges(ranges)
                if not ranges: return
                if self._index_buffer is None or self._index_chunk:
                    self._element_base = ranges[0][0] if self._index_chunk else 0
                    elements = self._indices[ranges[0][0]:ranges[0][1]] if self._index_chunk else self._indices
                    body = np.asarray(elements, dtype=np.uint32).tobytes()
                    index_buffer.allocate(body, len(body))
                    if index_buffer.size() != len(body): raise RuntimeError("Lighting path index storage incomplete")
                    self._index_buffer = index_buffer
                for start, end in ranges:
                    shader.setUniformValue("u_drawElementStart", start)
                    self._draw_range(gl, start, end)
            yield draw
        except Exception as error:
            failure = error
        finally:
            # A failed acquisition or release must not leave later resources
            # bound. Preserve the original draw error if cleanup also fails.
            for resource, attempted in ((shader, shader_bound), (index_buffer, index_bound),
                                        (vao, vao_bound), (vertex_buffer, vertex_bound)):
                if attempted:
                    try: resource.release()
                    except Exception as error:
                        if failure is None: failure = error
        if failure is not None:
            self._vaos.clear()
            self._index_buffer = None
            raise RuntimeError("Lighting path draw failed: " + str(failure)) from failure

    def vertex_mesh(self, context):
        # One upload shared by reflection/lighting/simulation owners in this
        # share group. Weak values retire it with the last owner. Retaining the
        # group wrapper prevents its identity being reused while names survive.
        group = context.shareGroup()
        key = id(self.mesh), id(group)
        upload = _vertex_meshes.get(key)
        if upload is None or upload.source is not self.mesh:
            upload = _vertex_meshes[key] = PathVertexMesh(self.mesh)
            upload.group = group
        self._vertex_mesh = upload
        return upload

    def _configure_attributes(self, shader):
        offset = 0
        mesh = self._vertex_mesh
        count = mesh.getVertexCount()
        for name, present, kind, size in (("a_vertex", True, "vector3f", 3),
                ("a_normal", mesh.hasNormals(), "vector3f", 3),
                ("a_color", mesh.hasColors(), "vector4f", 4),
                ("a_uvs", mesh.hasUVCoordinates(), "vector2f", 2)):
            if present:
                shader.enableAttribute(name, kind, offset)
                offset += count * size * 4
        sizes = {"float": 1, "int": 1, "vector2f": 2, "vector3f": 3, "vector4f": 4}
        for name in mesh.attributeNames():
            attribute = mesh.getAttribute(name)
            kind = attribute["opengl_type"]
            shader.enableAttribute(attribute["opengl_name"], kind, offset)
            offset += count * sizes[kind] * 4
        return offset

    def _draw_range(self, gl, start, end):
        self._draw_elements(gl.GL_LINES, int(end) - int(start), gl.GL_UNSIGNED_INT,
            ctypes.c_void_p((int(start)-self._element_base) * 4))
