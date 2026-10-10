"""Private draw admission, exact index bytes and balanced failure cleanup."""
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import numpy as np

from mpf.toolhead import ToolheadCaptureDraw as module
from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, BufferLease


class Matrix:
    def __init__(self, data=None): self.data = np.eye(4) if data is None else np.array(data, copy=True)
    def getData(self): return self.data
    def setRow(self, index, value): self.data[index] = value
    def setColumn(self, index, value): self.data[:, index] = value
    def invert(self): self.data = np.linalg.inv(self.data)
    def transpose(self): self.data = self.data.T


class Buffer:
    def __init__(self, *_args): self.uploads, self.closed = [], 0
    def create(self): pass
    def bind(self): pass
    def upload(self, value): self.uploads.append(value)
    def close(self): self.closed += 1


class CaptureDrawTests(unittest.TestCase):
    def setUp(self):
        self.gl, self.context, self.shader = Mock(), object(), Mock()
        self.camera = Mock()
        self.draws, self.binds = [], []
        self.size = 48
        def procedure(_context, name, *_types):
            if name == 'glGetBufferParameteriv':
                return lambda _target, _property, out: setattr(out._obj, 'value', self.size)
            if name == 'glDrawElements': return lambda *args: self.draws.append(args)
            if name == 'glBindVertexArray': return lambda value: self.binds.append(value)
            raise AssertionError(name)
        for patcher in (patch.object(module, 'CaptureBuffer', Buffer),
                patch.object(module, 'CaptureVertexArray', Buffer),
                patch.object(module, 'procedure', side_effect=procedure),
                patch.dict(sys.modules, {'UM.Math.Matrix': NS(Matrix=Matrix)})):
            patcher.start(); self.addCleanup(patcher.stop)

    @staticmethod
    def mesh(indices=True, normals=False):
        return CaptureMesh(11, np.arange(12, dtype=np.float32).reshape(4, 3),
            np.array([0, 1, 2, 3], np.int32) if indices else None,
            np.tile([0., 1., 0.], (4, 1)).astype(np.float32) if normals else None,
            None, None, ())

    def paths(self):
        mesh = self.mesh()
        return module.CapturePaths(mesh, BufferLease(31, 48, mesh.layout()[0]), self.gl, self.context)

    def test_path_sessions_preserve_exact_offsets_bytes_and_cached_vao(self):
        paths = self.paths()
        for _ in range(2):
            with paths.draw_session(self.shader, self.camera, Matrix(), self.gl) as draw:
                draw([(0, 2)]); draw([(2, 4)])
        self.assertEqual(paths.index.uploads, [np.array([0, 1, 2, 3], np.uint32).tobytes()])
        self.assertEqual([(mode, count, kind, offset.value) for mode, count, kind, offset in self.draws],
            [(1, 2, 0x1405, None), (1, 2, 0x1405, 8)] * 2)
        self.assertEqual([call.args for call in self.shader.setUniformValue.call_args_list],
            [('u_drawElementStart', 0), ('u_drawElementStart', 2)] * 2)
        self.assertEqual(self.shader.enableAttribute.call_count, 1)
        self.assertEqual(self.shader.release.call_count, 2)
        self.assertEqual(self.binds, [0, 0])
        vao = next(iter(paths.arrays.values()))
        paths.close(); self.assertEqual(vao.closed, 1)
        self.assertEqual(paths.arrays, {})

    def test_failed_full_index_upload_retries_before_any_draw(self):
        paths = self.paths()
        paths.index.upload = Mock(side_effect=[RuntimeError('upload failed'), None])
        with self.assertRaisesRegex(RuntimeError, 'upload failed'):
            with paths.draw_session(self.shader, self.camera, Matrix(), self.gl): pass
        self.assertFalse(paths._index_uploaded)
        self.assertEqual(self.draws, [])
        for _ in range(2):
            with paths.draw_session(self.shader, self.camera, Matrix(), self.gl) as draw:
                draw([(2, 4)])
        self.assertEqual(paths.index.upload.call_count, 2)
        self.assertTrue(paths._index_uploaded)
        self.assertEqual([args[3].value for args in self.draws], [8, 8])

    def test_retained_index_storage_still_rejects_out_of_range_draws(self):
        paths = self.paths()
        with paths.draw_session(self.shader, self.camera, Matrix(), self.gl) as draw:
            for invalid in ((-1, 2), (2, 5), (3, 2)):
                with self.assertRaisesRegex(RuntimeError, 'index range'):
                    draw([invalid])
            self.assertEqual(self.draws, [])
            draw([(0, 2)])  # A shortened prefix never submits the future pair.
        self.assertEqual(self.draws[0][1], 2)

    def test_borrowed_size_change_and_shader_fault_always_unbind(self):
        paths = self.paths(); self.size = 47
        with self.assertRaisesRegex(RuntimeError, 'buffer changed'):
            with paths.draw_session(self.shader, self.camera, Matrix(), self.gl): pass
        self.shader.release.assert_called_once()
        self.assertEqual(self.binds, [0])
        self.assertEqual(self.draws, [])
        self.shader.release.side_effect = RuntimeError('release fault')
        with self.assertRaisesRegex(RuntimeError, 'release fault'):
            with paths.draw_session(self.shader, self.camera, Matrix(), self.gl): pass
        self.assertEqual(self.binds, [0, 0])
        self.assertEqual(self.gl.glBindBuffer.call_args_list[-1].args, (0x8893, 0))

    def test_normal_matrix_is_inverse_transpose_without_translation(self):
        transform = Matrix(); transform.data[:3, :3] = np.diag([2., 3., 4.])
        transform.data[:3, 3] = [9., 8., 7.]
        module.camera_bindings(self.shader, self.camera, transform, self.mesh(normals=True))
        values = self.shader.updateBindings.call_args.kwargs
        np.testing.assert_allclose(values['normal_matrix'].getData(), np.diag([.5, 1/3, .25, 1.]))
        supplied = Matrix()
        module.camera_bindings(self.shader, self.camera, transform, self.mesh(normals=True), supplied)
        self.assertIs(self.shader.updateBindings.call_args.kwargs['normal_matrix'], supplied)

    def test_plate_phases_keep_depth_alpha_and_upload_once(self):
        plates = module.CapturePlates(self.gl, self.context)
        mesh = self.mesh()
        item = dict(mesh=mesh, transformation=Matrix(), uniforms={'opacity': .7})
        for phase in ('depth', 'colour', 'light'): plates.draw(self.shader, item, self.camera, self.gl, phase)
        vertex, index = plates.buffers[11]
        self.assertEqual(vertex.uploads, [mesh.vertices.tobytes()])
        self.assertEqual(index.uploads, [mesh.indices.view(np.uint32).tobytes()])
        self.assertEqual(self.draws, [(4, 4, 0x1405, None)] * 3)
        self.assertEqual([call.args for call in self.gl.glDepthMask.call_args_list], [(True,), (False,), (False,)])
        self.assertEqual([call.args for call in self.gl.glColorMask.call_args_list],
            [(False, False, False, False), (True, True, True, True)])
        self.gl.glBlendFuncSeparate.assert_called_once_with(0x0302, 1, 0, 1)
        self.assertEqual(self.shader.release.call_count, 3)
        arrays = list(plates.arrays.values())
        plates.retain(set())
        self.assertEqual(plates.buffers, {}); self.assertEqual(plates.arrays, {})
        self.assertTrue(all(array.closed == 1 for array in arrays))
        self.assertEqual((vertex.closed, index.closed), (1, 1))

    def test_unindexed_draw_and_failed_plate_restore_write_masks(self):
        plates = module.CapturePlates(self.gl, self.context)
        mesh = self.mesh(indices=False)
        item = dict(mesh=mesh, transformation=Matrix())
        plates.draw(self.shader, item, self.camera, self.gl, 'colour')
        self.gl.glDrawArrays.assert_called_once_with(4, 0, 4)
        plates.close(); plates.close()
        invalid = Mock(identity=55, upload_parts=Mock(return_value=[np.zeros(1)]),
            layout=Mock(return_value=((), 48)))
        with self.assertRaisesRegex(RuntimeError, 'vertex bytes disagree'):
            plates.draw(self.shader, dict(mesh=invalid), self.camera, self.gl, 'depth')
        self.assertEqual(self.gl.glColorMask.call_args.args, (True, True, True, True))
        self.assertEqual(self.gl.glBindBuffer.call_args_list[-1].args, (0x8893, 0))
        plates.close()


if __name__ == '__main__': unittest.main()
