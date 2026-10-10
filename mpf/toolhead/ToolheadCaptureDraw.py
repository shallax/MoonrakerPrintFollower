"""Context-private draw adapter using existing path VBOs and bounded indices."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import numpy as np
from .ToolheadCaptureBuffers import CaptureBuffer, CaptureVertexArray
from .ToolheadPathGeometry import ToolheadPathGeometry
from .ToolheadGLState import procedure


def camera_bindings(shader, camera, transform, mesh, normal=None):
    from UM.Math.Matrix import Matrix
    if normal is None and mesh.hasNormals():
        normal = Matrix(transform.getData())
        normal.setRow(3, [0, 0, 0, 1]); normal.setColumn(3, [0, 0, 0, 1])
        normal.invert(); normal.transpose()
    shader.updateBindings(model_matrix=transform, normal_matrix=normal,
        view_matrix=camera.getInverseWorldTransformation(), projection_matrix=camera.getProjectionMatrix(),
        view_position=camera.getWorldPosition(), light_0_position=camera.getCameraLightPosition())


class CapturePaths(ToolheadPathGeometry):
    def __init__(self, mesh, lease, gl, context):
        super().__init__(mesh, build_bounds=False, index_chunk=32768)
        lease.validate(mesh)
        self.lease, self.gl, self.context = lease, gl, context
        self.index = CaptureBuffer(context, 0x8893)
        self.arrays = {}
        self.index.create()
        self._index_uploaded = False

    @contextmanager
    def draw_session(self, shader, camera, transform, gl):
        vao = self.arrays.get(shader)
        try:
            if vao is None:
                vao = CaptureVertexArray(self.context); vao.create()
                self.arrays[shader] = vao
                vao.bind()
                gl.glBindBuffer(0x8892, self.lease.name)
                # Recorded byte range is checked both before borrowing and in
                # this shared context. No Qt wrapper crosses the lease boundary.
                size = ctypes.c_int()
                procedure(self.context, 'glGetBufferParameteriv', None, ctypes.c_uint, ctypes.c_uint,
                    ctypes.POINTER(ctypes.c_int))(0x8892, 0x8764, ctypes.byref(size))
                if size.value != self.lease.size: raise RuntimeError('Reflection borrowed buffer changed')
                shader.bind()
                for name, kind, offset in self.lease.layout: shader.enableAttribute(name, kind, offset)
            else:
                vao.bind(); gl.glBindBuffer(0x8892, self.lease.name); shader.bind()
            self.index.bind()
            if not self._index_uploaded:
                # Frozen indices are shared by every face/pass and later capture.
                # The capture receipt already admits the complete index storage.
                self.index.upload(np.asarray(self._indices, dtype=np.uint32).tobytes())
                self._index_uploaded = True
            camera_bindings(shader, camera, transform, self.mesh)
            draw_elements = procedure(self.context, 'glDrawElements', None, ctypes.c_uint, ctypes.c_int,
                ctypes.c_uint, ctypes.c_void_p)
            def draw(ranges):
                for start, end in self._validate_ranges(ranges):
                    shader.setUniformValue('u_drawElementStart', start)
                    draw_elements(0x0001, end-start, 0x1405, ctypes.c_void_p(start*4))
            yield draw
        finally:
            try: shader.release()
            finally:
                procedure(self.context, 'glBindVertexArray', None, ctypes.c_uint)(0)
                gl.glBindBuffer(0x8892, 0); gl.glBindBuffer(0x8893, 0)

    def close(self):
        for vao in self.arrays.values(): vao.close()
        self.arrays.clear(); self.index.close()


class CapturePlates:
    def __init__(self, gl, context):
        self.gl, self.context = gl, context
        self.buffers, self.arrays = {}, {}

    def draw(self, shader, item, camera, gl, phase):
        mesh = item['mesh']
        key = mesh.identity
        buffers = self.buffers.get(key)
        try:
            vao_key = key, shader
            vao = self.arrays.get(vao_key)
            if vao is None:
                vao = CaptureVertexArray(self.context); vao.create()
                self.arrays[vao_key] = vao
            vao.bind()
            if buffers is None:
                vertex, index = CaptureBuffer(self.context, 0x8892), CaptureBuffer(self.context, 0x8893)
                self.buffers[key] = (vertex, index)
                vertex.create(); index.create()
                body = b''.join(np.asarray(part).tobytes() for part in mesh.upload_parts())
                _layout, required = mesh.layout()
                if len(body) != required: raise RuntimeError('Reflection plate vertex bytes disagree')
                vertex.upload(body)
                if mesh.indices is not None: index.upload(np.asarray(mesh.indices, dtype=np.uint32).tobytes())
                buffers = vertex, index
            buffers[0].bind(); buffers[1].bind()
            shader.bind()
            for name, kind, offset in mesh.layout()[0]: shader.enableAttribute(name, kind, offset)
            camera_bindings(shader, camera, item['transformation'], mesh, item.get('normal_transformation'))
            if phase != 'light' and item.get('uniforms') is not None: shader.updateBindings(**item['uniforms'])
            gl.glEnable(0x0B71)
            gl.glDepthMask(phase == 'depth')
            gl.glDepthFunc(0x0203 if phase == 'light' else 0x0201)
            if phase == 'light':
                gl.glEnable(0x0B44); gl.glCullFace(0x0405); gl.glFrontFace(0x0901)
                gl.glEnable(0x0BE2); gl.glBlendEquation(0x8006)
                gl.glBlendFuncSeparate(0x0302, 1, 0, 1)
            else:
                gl.glDisable(0x0B44)
                if phase == 'colour':
                    gl.glEnable(0x0BE2); gl.glBlendFunc(0x0302, 0x0303); gl.glBlendEquation(0x8006)
                else: gl.glDisable(0x0BE2)
            if phase == 'depth': gl.glColorMask(False, False, False, False)
            if mesh.indices is not None:
                procedure(self.context, 'glDrawElements', None, ctypes.c_uint, ctypes.c_int,
                    ctypes.c_uint, ctypes.c_void_p)(4, int(mesh.indices.size), 0x1405, None)
            else: gl.glDrawArrays(4, 0, mesh.getVertexCount())
        finally:
            try: shader.release()
            finally:
                if phase == 'depth': gl.glColorMask(True, True, True, True)
                procedure(self.context, 'glBindVertexArray', None, ctypes.c_uint)(0)
                gl.glBindBuffer(0x8892, 0); gl.glBindBuffer(0x8893, 0)

    def close(self):
        for array in self.arrays.values(): array.close()
        self.arrays.clear()
        for pair in self.buffers.values():
            for buffer in pair: buffer.close()
        self.buffers.clear()

    def retain(self, identities):
        for key in set(self.arrays):
            if key[0] not in identities: self.arrays.pop(key).close()
        for key in set(self.buffers)-identities:
            for buffer in self.buffers.pop(key): buffer.close()
