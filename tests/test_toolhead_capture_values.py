"""Native optional channel admission and validated borrowed buffer ranges."""
import unittest
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch
import numpy as np
from mpf.toolhead.ToolheadCaptureValues import CaptureMesh, BufferLease, frozen_array


class CaptureValueTests(unittest.TestCase):
    def mesh(self, *, empty=False):
        vertices = np.zeros((12, 3), np.float32)
        normals = np.zeros((0 if empty else 12, 3), np.float32)
        uvs = np.zeros((0 if empty else 12, 2), np.float32)
        indices = np.zeros((0 if empty else 4, 3), np.int32)
        return NS(getVertices=lambda: vertices, getVertexCount=lambda: 12,
            getNormals=lambda: normals, hasNormals=lambda: len(normals) > 0,
            getUVCoordinates=lambda: uvs, hasUVCoordinates=lambda: len(uvs) > 0,
            getIndices=lambda: indices, hasColors=lambda: False, attributeNames=lambda: [])

    def test_untextured_native_bed_empty_uv_and_normals_are_absent(self):
        mesh = CaptureMesh.freeze(self.mesh(empty=True))
        self.assertIsNone(mesh.uvs)
        self.assertIsNone(mesh.normals)
        self.assertIsNone(mesh.indices)
        self.assertEqual(mesh.layout(), ((('a_vertex', 'vector3f', 0),), 144))
        self.assertEqual(sum(part.nbytes for part in mesh.upload_parts()), 144)

    def test_populated_channels_keep_native_packed_offsets_and_readonly_views(self):
        source = self.mesh()
        mesh = CaptureMesh.freeze(source)
        self.assertEqual(mesh.layout(), ((('a_vertex', 'vector3f', 0),
            ('a_normal', 'vector3f', 144), ('a_uvs', 'vector2f', 288)), 384))
        self.assertTrue(source.getVertices().flags.writeable)
        self.assertFalse(mesh.vertices.flags.writeable)
        self.assertTrue(np.shares_memory(mesh.vertices, source.getVertices()))
        BufferLease(12, 384, mesh.layout()[0]).validate(mesh)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            BufferLease(12, 383, mesh.layout()[0]).validate(mesh)

    def test_malformed_populated_channel_remains_rejected_with_field_details(self):
        source = self.mesh()
        source.getUVCoordinates = lambda: np.zeros((3, 2), np.float32)
        with self.assertRaisesRegex(RuntimeError, 'a_uvs vector2f'):
            CaptureMesh.freeze(source).layout()

    def test_object_arrays_cannot_cross_thread_boundary(self):
        with self.assertRaisesRegex(RuntimeError, 'live objects'):
            frozen_array(np.array([object()], dtype=object))

    def test_colours_and_custom_channels_preserve_publication_and_packed_layout(self):
        source = self.mesh()
        colours = np.tile([.2, .3, .4, .5], (12, 1)).astype(np.float32)
        source.hasColors = lambda: True
        source._colors = colours
        data = np.ones((12, 2), np.float32)
        attribute = dict(value=data, opengl_name='a_finish', opengl_type='vector2f')
        source.attributeNames = lambda: ['finish']
        source.getAttribute = lambda name: attribute
        mesh = CaptureMesh.freeze(source)
        self.assertTrue(np.shares_memory(mesh.colours, colours))
        self.assertEqual(mesh.attributeNames(), ['finish'])
        self.assertIsNone(mesh.getAttribute('absent'))
        np.testing.assert_array_equal(mesh.getAttribute('finish')['value'], data)
        self.assertTrue(mesh.hasColors()); self.assertTrue(mesh.hasNormals()); self.assertTrue(mesh.hasUVCoordinates())
        self.assertIs(mesh.getVertices(), mesh.vertices)
        self.assertIs(mesh.getIndices(), mesh.indices)
        self.assertIs(mesh.getNormals(), mesh.normals)
        self.assertIs(mesh.getUVCoordinates(), mesh.uvs)
        self.assertEqual(sum(part.nbytes for part in mesh.upload_parts()), mesh.layout()[1])
        del source._colors
        source.getColorsAsByteArray = lambda: colours.tobytes()
        np.testing.assert_array_equal(CaptureMesh.freeze(source).colours, colours)
        source.getVertexCount = lambda: 250001
        with self.assertRaisesRegex(RuntimeError, 'colour publication'):
            CaptureMesh.freeze(source)

    def test_uniform_freezing_copies_native_values_and_preserves_types(self):
        from mpf.toolhead.ToolheadCaptureValues import freeze_uniform, thaw_uniform, UniformValue
        class Matrix:
            def __init__(self, data): self.data = np.array(data)
            def getData(self): return self.data
        class Vector:
            def __init__(self, x, y, z): self.x, self.y, self.z = x, y, z
        class Color:
            def __init__(self, r, g, b, a): self.r, self.g, self.b, self.a = r, g, b, a
        with patch.dict(sys.modules, {'UM.Math.Matrix': NS(Matrix=Matrix),
                'UM.Math.Vector': NS(Vector=Vector), 'UM.Math.Color': NS(Color=Color)}):
            original = Matrix(np.arange(16).reshape(4, 4))
            frozen = freeze_uniform(original)
            original.data[:] = 0
            np.testing.assert_array_equal(thaw_uniform(frozen).getData(), np.arange(16).reshape(4, 4))
            vector = thaw_uniform(freeze_uniform(Vector(1, 2, 3)))
            self.assertEqual((vector.x, vector.y, vector.z), (1., 2., 3.))
            colour = thaw_uniform(freeze_uniform(Color(.1, .2, .3, .4)))
            self.assertEqual((colour.r, colour.g, colour.b, colour.a), (.1, .2, .3, .4))
            for value in (True, 3, .5, np.int32(7), np.float32(.25)):
                restored = thaw_uniform(freeze_uniform(value))
                self.assertEqual(restored, value)
                self.assertIn(type(restored), (bool, int, float))
            self.assertEqual(thaw_uniform(freeze_uniform([1, (2., False)])), [1, [2., False]])
            with self.assertRaisesRegex(RuntimeError, 'Unsupported'): freeze_uniform(object())
            with self.assertRaisesRegex(RuntimeError, 'Unknown'): thaw_uniform(UniformValue('invalid', None))


if __name__ == '__main__': unittest.main()
