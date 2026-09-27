"""Geometry invariants shared by the retained follower and object picker."""
from array import array
import importlib.util
import os
import threading
import unittest
from unittest.mock import patch

QT_AVAILABLE = importlib.util.find_spec("PyQt6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to test retained geometry")
class RetainedGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_arriving_ghost_reuses_cached_roles_and_keeps_the_current_channel(self):
        from concurrent.futures import Future
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        from plugins import GpuFollower as module
        from plugins.GpuStrokeMaterial import pack_shader

        payload = {"classes": {"SKIN": [[(0, 0, 0), (20, 0, 1)]]}, "travels": []}
        class Layer:
            def geometry_payload(self):
                return payload
        current, ghost, replacement = Layer(), Layer(), Layer()
        with module._CACHE_LOCK:
            module._CACHE.clear()
        prior = module.prepare((("current", payload), ("next", payload)))
        with patch.object(module, "dashed_edges", side_effect=AssertionError("cached next was rebuilt")):
            again = module.prepare((("prev", payload), ("current", payload), ("next", payload)))
        self.assertIs(next(row for row in prior if row[0] == "next"),
                      next(row for row in again if row[0] == "next"))
        window = QQuickWindow()
        item = module.GpuFollower(window.contentItem())
        item._layers = {"current": current}
        item._data = pack_shader(module.prepare((("current", payload),)))
        retained = item._data[0]
        with patch.object(module._POOL, "submit", return_value=Future()):
            item.layers = {"current": current, "next": ghost}
            self.assertIs(item._data[0], retained)
            item.layers = {"current": replacement}
            self.assertFalse(item._data)
        sip.delete(window)

    def test_layer_replacement_retires_the_previous_native_tree(self):
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        from plugins.GpuFollower import GpuFollower
        from plugins.GpuStrokeMaterial import pack_shader, _pack_stdlib
        window = QQuickWindow()
        item = GpuFollower(window.contentItem())
        edge = array('f', (0, 0, 10, 0)).tobytes()
        raw = (('current', 'SKIN', (1,), edge),)
        self.assertEqual(pack_shader(raw), _pack_stdlib(raw))
        item.settings = {'lineWidth': 1, 'split': 2}
        item._data = pack_shader(raw)
        first = item.updatePaintNode(None, None)
        self.assertEqual(first.childCount(), 2)
        # A new layer may have identical counts; its buffers still replace
        # the old batches. Empty loading intervals must retire them too.
        item._data = pack_shader(raw)
        second = item.updatePaintNode(first, None)
        self.assertTrue(sip.isdeleted(first))
        item._data = ()
        empty = item.updatePaintNode(second, None)
        self.assertTrue(sip.isdeleted(second))
        self.assertEqual(empty.childCount(), 0)
        sip.delete(empty)

    def test_next_dashes_continue_through_short_polyline_edges(self):
        from plugins.GpuFollower import dashed_edges, prepare
        points = [[i * .25, 0, i] for i in range(41)]
        edges = list(dashed_edges(points))
        self.assertEqual(sum(edge[3] - edge[1] for edge in edges), 5)
        self.assertTrue(all(edge[1] % 1.0 < .5 for edge in edges))
        payload = {"classes": {"SKIN": [points]}, "travels": []}
        result = prepare((("prev", payload), ("next", payload)))
        self.assertEqual(len(result[0][2]), 40)
        self.assertEqual(len(result[1][2]), 20)

    def test_every_width_is_constant_in_pixels_across_zoom_and_has_round_caps(self):
        from plugins.GpuFollower import stroke_geometry, WIDE_VERTICES
        edge = array('f', (0, 0, 10, 0)).tobytes()
        for scale in (1, 5, 20):
            for width in range(1, 9):
                packed = stroke_geometry((('current', 'SKIN', (1,), edge),), width, scale, scale)[0][3]
                vertices = array('f')
                vertices.frombytes(packed)
                self.assertEqual(len(vertices), WIDE_VERTICES * 2)
                self.assertAlmostEqual((max(vertices[1::2]) - min(vertices[1::2])) * scale,
                                       width, places=5)
                self.assertAlmostEqual(min(vertices[::2]) * scale, -width / 2, places=5)
                self.assertAlmostEqual(max(vertices[::2]) * scale, 10 * scale + width / 2, places=4)

    def test_wide_next_dashes_keep_flat_ends_and_shared_vertex_stride(self):
        from plugins.GpuFollower import stroke_geometry, WIDE_VERTICES
        edge = array('f', (0, 0, 3, 0)).tobytes()
        packed = stroke_geometry((('next', 'SKIN', (1,), edge),), 8, 1, 1)[0][3]
        vertices = array('f')
        vertices.frombytes(packed)
        self.assertEqual(len(vertices), WIDE_VERTICES * 2)
        self.assertEqual((min(vertices[::2]), max(vertices[::2])), (0, 3))

    def test_dense_preparation_cancels_before_publishing_or_caching(self):
        from plugins.GpuFollower import prepare
        cancel = threading.Event()
        cancel.set()
        payload = {"classes": {"SKIN": [[[i, 0, i] for i in range(100000)]]}}
        self.assertEqual(prepare((("current", payload),), cancel), ())

    def test_picker_preserves_state_precedence_halo_widths_and_hover(self):
        from plugins.GpuObjectPicker import object_strokes
        polygon = [[0, 0], [10, 0], [10, 10], [0, 10]]
        colours = {'excludedInk': '#ff0000', 'currentInk': '#0000ff',
                   'passedInk': '#00ff00', 'includedInk': '#ffffff', 'halo': '#000000'}
        for compact in (False, True):
            for state, ink, width in (({}, '#ffffff', 1.5), ({'passed': True}, '#00ff00', 1.5),
                                      ({'current': True, 'passed': True}, '#0000ff', 2.5),
                                      ({'excluded': True, 'current': True}, '#ff0000', 2.5)):
                scene = dict(colours, objects=[dict(state, name='part', polygon=polygon)],
                             plot={'sx': 2, 'sy': 2}, compact=compact)
                for hover in ('', 'part'):
                    scene['hoveredName'] = hover
                    rows = object_strokes(scene)
                    self.assertEqual([colour.name() for _, colour in rows], ['#000000', ink])
                    expected = (3 if hover else width) * (.5 if compact else 1)
                    for (packed, _), actual_width in zip(rows, (expected + (2 if compact else 4), expected), strict=True):
                        points = array('f')
                        points.frombytes(packed)
                        self.assertAlmostEqual(-min(points[::2]) * 4, actual_width, places=5)


if __name__ == "__main__":
    unittest.main()
