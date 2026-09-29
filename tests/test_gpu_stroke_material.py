"""Rendering contract for the retained GPU stroke material."""

from array import array
import importlib.util
import os
import struct
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(importlib.util.find_spec("PyQt6"), "Install PyQt6 for GPU material tests")
class StrokeMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_material_ordering_shader_lifetime_and_uniform_layout(self):
        from PyQt6.QtGui import QColor, QMatrix4x4
        from mpf import GpuStrokeMaterial as module

        first = module.FollowerStrokeMaterial(QColor(40, 80, 120, 128), width=4, aa=True)
        same = module.FollowerStrokeMaterial(QColor(40, 80, 120, 128), width=4, aa=True)
        wider = module.FollowerStrokeMaterial(QColor(40, 80, 120, 128), width=6, aa=True)
        self.assertIs(first.type(), same.type())
        self.assertEqual(first.compare(same), 0)
        self.assertLess(first.compare(wider), 0)
        self.assertGreater(wider.compare(first), 0)
        self.assertEqual(first.viewport, (556, 556))
        shader = first.createShader(None)
        self.assertIs(module.SHADERS[-1], shader)

        class UniformBuffer:
            def __init__(self):
                self.written = None

            def replace(self, offset, size, data):
                self.written = (offset, size, bytes(data))

        class State:
            def __init__(self):
                self.buffer = UniformBuffer()

            def combinedMatrix(self):
                return QMatrix4x4()

            def opacity(self):
                return 0.5

            def devicePixelRatio(self):
                return 2.0

            def uniformData(self):
                return self.buffer

        first.viewport = (400, 300)
        first.split = 2.5
        first.clip = True
        first.physical = True
        state = State()
        self.assertTrue(shader.updateUniformData(state, first, None))
        offset, size, packed = state.buffer.written
        self.assertEqual((offset, size), (0, 384))
        values = struct.unpack("<96f", packed)
        self.assertEqual(values[:16], tuple(QMatrix4x4().data()))
        self.assertAlmostEqual(values[19], first.colour.alphaF() * 0.5)
        self.assertEqual(values[20:24], (4.0, 400.0, 300.0, 1.0))
        self.assertEqual(values[24:28], (1.0, 2.5, 1.0, 1.0))

    def test_cancelled_pack_and_stdlib_optional_metrics(self):
        from mpf.GpuStrokeMaterial import _pack_stdlib, pack_shader

        cancelled = threading.Event()
        cancelled.set()
        raw = (("current", "SKIN", (0,), array("f", (0, 0, 3, 4)).tobytes(),
                array("f", (0.7,)).tobytes(), array("f", (2, 3)).tobytes()),)
        self.assertEqual(pack_shader(raw, cancelled), ())
        self.assertEqual(_pack_stdlib(raw, cancelled), ())
        cancelled.clear()
        self.assertEqual(pack_shader(raw), _pack_stdlib(raw))
        vertices = array("f")
        vertices.frombytes(_pack_stdlib(raw)[0][3])
        self.assertAlmostEqual(abs(vertices[4]), 0.7, places=5)
        self.assertEqual(tuple(vertices[8:10]), (2.0, 3.0))


if __name__ == "__main__":
    unittest.main()
