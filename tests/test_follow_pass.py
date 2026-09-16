"""The follow pass: the layer/line attribute baking over Cura's flat
LayerData, and the pass state. numpy-gated — the host test env may
lack it; the container gate has it.
"""
import unittest

try:
    import numpy
    NUMPY = True
except ImportError:
    NUMPY = False


class _FakeLayerData:
    def __init__(self, vertices, indices, colors, counts, extra=None):
        self._vertices = numpy.asarray(vertices, dtype=numpy.float32)
        self._indices = numpy.asarray(indices, dtype=numpy.int32)
        self._colors = numpy.asarray(colors, dtype=numpy.float32)
        self._counts = counts
        self._extra = extra or {}

    def getVertices(self):
        return self._vertices

    def getIndices(self):
        return self._indices

    def getColors(self):
        return self._colors

    def getElementCounts(self):
        return self._counts

    def attributeNames(self):
        return sorted(self._extra)

    def getAttribute(self, name):
        return self._extra[name]


@unittest.skipUnless(NUMPY, "numpy not available")
class FollowMeshBakeTests(unittest.TestCase):
    def _bake(self, counts):
        from plugins.FollowPass import build_follow_mesh
        # 8 vertices, 8 indices: layers 1 and 3 with two lines each.
        vertices = numpy.zeros((8, 3), numpy.float32)
        indices = numpy.arange(8, dtype=numpy.int32)
        colors = numpy.ones((8, 4), numpy.float32)
        extra = {
            "extruder": {"opengl_type": "float", "opengl_name": "a_extruder",
                         "value": numpy.zeros(8, numpy.float32)},
            "line_type": {"opengl_type": "float", "opengl_name": "a_line_type",
                          "value": numpy.ones(8, numpy.float32)},
        }
        layer_data = _FakeLayerData(vertices, indices, colors, counts, extra)
        return build_follow_mesh(layer_data), layer_data

    def test_bakes_layer_and_line_attributes(self):
        mesh, layer_data = self._bake({1: 4, 3: 4})
        self.assertIsNotNone(mesh)
        layer_attr = mesh.getAttribute("layer")
        line_attr = mesh.getAttribute("line")
        self.assertEqual(layer_attr["opengl_name"], "a_layer")
        self.assertEqual(line_attr["opengl_name"], "a_line")
        self.assertEqual(list(layer_attr["value"]), [1, 1, 1, 1, 3, 3, 3, 3])
        self.assertEqual(list(line_attr["value"]), [0, 0, 1, 1, 0, 0, 1, 1])

    def test_references_the_source_arrays(self):
        mesh, layer_data = self._bake({1: 4, 3: 4})
        self.assertTrue(numpy.shares_memory(mesh.getVertices(), layer_data.getVertices()))
        self.assertTrue(numpy.shares_memory(mesh.getIndices(), layer_data.getIndices()))
        self.assertTrue(numpy.shares_memory(mesh.getAttribute("extruder")["value"],
                                            layer_data.getAttribute("extruder")["value"]))

    def test_vertices_beyond_the_counts_table_never_render(self):
        mesh, layer_data = self._bake({1: 4})
        layer_attr = mesh.getAttribute("layer")
        self.assertEqual(list(layer_attr["value"]), [1, 1, 1, 1, 1 << 30, 1 << 30, 1 << 30, 1 << 30])

    def test_bake_fails_cleanly_without_geometry(self):
        from plugins.FollowPass import build_follow_mesh
        layer_data = _FakeLayerData([], [], [], {})
        self.assertIsNone(build_follow_mesh(layer_data))
        layer_data = _FakeLayerData(numpy.zeros((4, 3)), numpy.arange(4), numpy.ones((4, 4)), {})
        self.assertIsNone(build_follow_mesh(layer_data))


if __name__ == "__main__":
    unittest.main()
