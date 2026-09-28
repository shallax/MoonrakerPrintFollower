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
    def test_data_source_rejects_cycles_and_detaches_cleanly(self):
        from PyQt6 import sip
        from plugins.GpuFollower import GpuFollower

        source, consumer, downstream = (GpuFollower() for _ in range(3))
        try:
            with self.assertRaises(ValueError):
                source.dataSource = source
            with self.assertRaises(ValueError):
                source.dataSource = object()
            consumer.dataSource = source
            consumer.dataSource = source  # Rebinding the same source is harmless.
            with self.assertRaises(ValueError):
                downstream.dataSource = consumer
            source._data = (("current", "SKIN", (), b""),)
            source.readyChanged.emit()
            self.assertIs(consumer._data, source._data)
            consumer.dataSource = None
            self.assertEqual(consumer._data, ())
            source.readyChanged.emit()
            self.assertEqual(consumer._data, (), "detached consumer still receives its old source")
        finally:
            sip.delete(downstream)
            sip.delete(consumer)
            sip.delete(source)

    def test_source_owned_layers_and_line_width_updates(self):
        from PyQt6 import sip
        from PyQt6.QtGui import QColor
        from PyQt6.QtQuick import QSGTransformNode
        from plugins.GpuFollower import GpuFollower

        source, consumer = GpuFollower(), GpuFollower()
        parent = QSGTransformNode()
        try:
            source.layers = {"current": object()}
            self.assertIn("current", source.layers)
            source.layers = source.layers  # Identical geometry needs no worker.
            self.assertEqual(source.settings, {})
            self.assertIsInstance(source.supported, bool)
            consumer.dataSource = source
            consumer.layers = {"current": object()}
            self.assertIs(consumer.layers["current"], source.layers["current"])
            child = GpuFollower._node(parent, QColor("red"))
            GpuFollower._vertices(child, b"", 0, 3.5)
            self.assertEqual(child.geometry().lineWidth(), 3.5)
            GpuFollower._vertices(child, b"", 0, 1.5)
            self.assertEqual(child.geometry().lineWidth(), 1.5)
            from plugins.GpuFollower import prepare
            self.assertEqual(prepare((("current", None),)), ())
        finally:
            sip.delete(parent)
            sip.delete(consumer)
            sip.delete(source)

    def test_worker_failure_releases_readiness_and_reaches_shared_consumers(self):
        from PyQt6 import sip
        from plugins import GpuFollower as module

        class Layer:
            def geometry_payload(self):
                return {"classes": {}}

        item = module.GpuFollower()
        consumer = module.GpuFollower()
        consumer.dataSource = item
        try:
            with patch.object(module, "prepare", side_effect=ValueError("invalid geometry")):
                item.layers = {"current": Layer()}
                item._work.futures[-1].result(timeout=5)
                self.app.processEvents()
            self.assertIsNone(item._preparing_generation)
            self.assertIn("Unable to render", item.error)
            self.assertEqual(consumer.error, item.error)
            self.assertFalse(item.ready)
            item._prepared(item._generation, ())
            self.assertEqual(item.error, "")
            item._preparation_failed(item._generation - 1, "stale")
            self.assertEqual(item.error, "")
        finally:
            sip.delete(consumer)
            sip.delete(item)

    def test_qml_geometry_source_binding_is_a_qobject_pointer(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtQml import QQmlComponent, QQmlEngine, qmlRegisterType
        from PyQt6 import sip
        from plugins.GpuFollower import GpuFollower
        qmlRegisterType(GpuFollower, "FollowerOpacityTest", 1, 0, "StrokeItem")
        engine = QQmlEngine(); component = QQmlComponent(engine)
        component.setData(b'''import QtQuick 2.15
import FollowerOpacityTest 1.0
Item {
    StrokeItem { id: source; objectName: "source" }
    StrokeItem { objectName: "consumer"; dataSource: source }
}''', QUrl())
        root = component.create()
        self.assertIsNotNone(root, [error.toString() for error in component.errors()])
        source = root.findChild(GpuFollower, "source")
        consumer = root.findChild(GpuFollower, "consumer")
        self.assertIs(consumer.dataSource, source)
        source._data = (("current", "SKIN", (), b""),)
        source.readyChanged.emit()
        self.assertIs(consumer._data, source._data)
        sip.delete(root); sip.delete(engine)

    def test_translucent_passes_share_preparation_hold_frames_and_use_texture_viewport(self):
        from PyQt6 import sip
        from PyQt6.QtCore import QObject
        from PyQt6.QtQuick import QQuickWindow
        from plugins.GpuFollower import GpuFollower, prepare
        from plugins.GpuStrokeMaterial import pack_shader
        window = QQuickWindow(); window.resize(1000, 800)
        source = GpuFollower(window.contentItem())
        consumer = GpuFollower(window.contentItem()); consumer.setWidth(400); consumer.setHeight(300)
        payload = {"classes": {"SKIN": [[(0, 0, 0), (10, 0, 0), (20, 0, 1)]]}}
        data = pack_shader(prepare((("current", payload), ("prev", payload), ("next", payload))))
        source._data = data; source._layers = {"current": QObject()}
        consumer.dataSource = source
        self.assertIs(consumer._data, source._data)
        self.assertEqual(consumer._work.futures, [])
        settings = {"isolatedRole": "ghost", "split": 0, "showGrid": False, "lineWidth": 8}
        consumer.settings = settings
        node = consumer.updatePaintNode(None, None)
        self.assertEqual(node._grid_nodes, [])
        self.assertEqual(node._marker_nodes, [])
        for role,_name,_motions,_data,base,printed in node._groups:
            self.assertEqual(base.geometry().vertexCount(), 12 if role == "current" else 0)
            self.assertEqual(base._material.colour.alphaF(), 1)
            self.assertEqual(base._material.viewport, (400, 300))
            self.assertEqual(printed.geometry().vertexCount(), 0)
        # Progress belongs to the direct prefix pass; it does not dirty the
        # fixed ghost geometry or require another worker/conversion.
        ghost = next(row[-2] for row in node._groups if row[0] == "current")
        pointer = int(ghost.geometry().vertexData())
        source.settings = {"split": 1}
        self.assertIs(consumer._data, data)
        consumer.updatePaintNode(node, None)
        self.assertEqual(pointer, int(ghost.geometry().vertexData()))
        source._generation = 1; source._preparing_generation = 1; source._data = ()
        source.readyChanged.emit()
        self.assertIs(consumer.updatePaintNode(node, None), node)
        source._prepared(1, data)
        self.assertIs(consumer._data, source._data)
        sip.delete(source)
        self.assertIsNone(consumer.dataSource)
        self.assertEqual(consumer._data, ())
        sip.delete(node); sip.delete(window)

    def test_extruder_events_follow_completed_playback_and_disappear_on_scrub_back(self):
        from plugins.GpuFollower import GpuFollower, prepare, marker_counts, marker_geometry
        from plugins.GpuStrokeMaterial import pack_shader
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        data = pack_shader(prepare((("current", {"motions": 3,
            "retractions": [(0, 0, 0), (60, 0, 3)],
            "unretractions": [(30, 0, 1)]}),)))
        expected = ((0, 0, 0), (.99, 0, 0), (1, 1, 0), (1.99, 1, 0),
                    (2, 1, 1), (3, 2, 1), (0, 0, 0))
        for split, up, down in expected:
            limits = marker_counts(data, split, 3)
            self.assertEqual(dict(limits), {"RETRACTION": up, "UNRETRACTION": down})
            raw = marker_geometry(data, 1, 1, 1, False, True, True, limits)
            self.assertEqual(len(raw), (up + down) * 28 * 4)
        window = QQuickWindow()
        item = GpuFollower(window.contentItem())
        item._data = data
        settings = {"split": 3, "displaySplit": 1, "motionCount": 3,
                    "showRetractions": True, "showUnretractions": True}
        item.settings = settings
        node = item.updatePaintNode(None, None)
        original = node._marker_nodes[0]
        item.settings = dict(settings, displaySplit=1.9)
        item.updatePaintNode(node, None)
        self.assertIs(node._marker_nodes[0], original,
                      "smoothing within a motion must not rebuild glyph geometry")
        item.settings = dict(settings, displaySplit=0)
        item.updatePaintNode(node, None)
        self.assertEqual(node._marker_nodes, [])
        sip.delete(node)
        sip.delete(window)

    def test_physical_widths_share_fixed_geometry_with_pixel_override_and_zoom(self):
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        from plugins.GpuFollower import GpuFollower, prepare
        from plugins.GpuStrokeMaterial import pack_shader, _pack_stdlib
        payload = {"classes": {"SKIN": [[(0, 0, 0.0), (10, 0, 0.0), (20, 0, 1.0)]]}, "widths": (.4, .8)}
        raw = prepare((("current", payload),))
        data = pack_shader(raw)
        self.assertEqual(data, _pack_stdlib(raw))
        values = array("f")
        values.frombytes(data[0][3])
        self.assertAlmostEqual(abs(values[4]), .4)
        self.assertAlmostEqual(abs(values[60 + 4]), .8)
        window = QQuickWindow()
        item = GpuFollower(window.contentItem())
        item._data = data
        item.settings = {"split": 2, "lineWidth": 5, "plot": {"sx": 2, "sy": 2}}
        node = item.updatePaintNode(None, None)
        child = node._groups[0][-1]
        pointer = int(child.geometry().vertexData())
        self.assertEqual(child._material.width, 5)
        for zoom in (1, 5, 20):
            item.settings = {"split": 2, "lineWidth": 5, "trueThickness": True, "scale": zoom, "plot": {"sx": 2, "sy": 2}}
            self.assertIs(item.updatePaintNode(node, None), node)
            self.assertTrue(child._material.physical)
            self.assertEqual(child._material.width, 2 * zoom)
            self.assertEqual(int(child.geometry().vertexData()), pointer)
        sip.delete(node)
        sip.delete(window)

    def test_motion_animation_changes_uniforms_without_reuploading_geometry(self):
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        from plugins.GpuFollower import GpuFollower
        from plugins.GpuStrokeMaterial import pack_shader
        window = QQuickWindow()
        item = GpuFollower(window.contentItem())
        item._data = pack_shader((("current", "SKIN", (0, 1),
                                  array("f", (0, 0, 10, 0, 10, 0, 20, 0)).tobytes()),))
        item.settings = {"split": 1.8, "displaySplit": 1.2}
        node = item.updatePaintNode(None, None)
        printed = node._groups[0][-1]
        pointer = int(printed.geometry().vertexData())
        item.settings = {"split": 1.8, "displaySplit": 1.6}
        self.assertIs(item.updatePaintNode(node, None), node)
        self.assertEqual(int(printed.geometry().vertexData()), pointer)
        self.assertEqual(printed.geometry().vertexCount(), 12)
        self.assertEqual(printed._material.split, 1.6)
        self.assertTrue(printed._material.clip)
        sip.delete(node)
        sip.delete(window)

    def test_arc_subedges_partition_one_motion_instead_of_growing_together(self):
        from plugins.GpuStrokeMaterial import pack_shader, _pack_stdlib
        raw = (("current", "SKIN", (0, 0), array("f", (0, 0, 3, 0, 3, 0, 3, 1)).tobytes()),)
        self.assertEqual(pack_shader(raw), _pack_stdlib(raw))
        vertices = array("f")
        vertices.frombytes(pack_shader(raw)[0][3])
        self.assertEqual((vertices[6], vertices[7]), (0, .75))
        self.assertEqual((vertices[6 * 10 + 6], vertices[6 * 10 + 7]), (.75, .25))

    def test_marker_tracks_every_curve_subedge_and_matches_shader_fraction(self):
        from plugins.GpuFollower import GpuFollower
        from plugins.GpuStrokeMaterial import pack_shader
        item = GpuFollower()
        item._data = pack_shader((("current", "SKIN", (0, 0, 1),
                                  array("f", (0, 0, 3, 0, 3, 0, 3, 1, 3, 1, 5, 1)).tobytes()),
                                 ("next", "SKIN", (0,), array("f", (50, 50, 60, 60)).tobytes())))
        for progress, expected in ((0, (0, 0)), (.375, (1.5, 0)), (.75, (3, 0)),
                                   (.875, (3, .5)), (1, (3, 1)), (1.5, (4, 1)), (2, (5, 1))):
            point = item.pointAtMotion(progress)
            self.assertTrue(point["valid"])
            self.assertAlmostEqual(point["x"], expected[0])
            self.assertAlmostEqual(point["y"], expected[1])
        for progress in (-1, 2.5, 20, float("nan"), float("inf")):
            self.assertFalse(item.pointAtMotion(progress)["valid"])
        item._data = ()
        self.assertFalse(item.pointAtMotion(.5)["valid"], "retired geometry must not locate the live marker")

    def test_event_glyphs_are_small_zoom_dependent_and_thinned_in_screen_cells(self):
        from plugins.GpuFollower import marker_geometry, prepare
        from plugins.GpuStrokeMaterial import pack_shader
        payload = {"retractions": [(i * .001, 0, i) for i in range(1000)],
                   "unretractions": [(0, 0, 1000)]}
        data = pack_shader(prepare((("current", payload),)))
        self.assertEqual(marker_geometry(data, 1, 1, 1, False, False, False), b"")
        vertices = array("f")
        vertices.frombytes(marker_geometry(data, 1, 1, 1, False, True, False))
        self.assertEqual(len(vertices), 28, "dense events must collapse to one hollow arrow")
        self.assertEqual(max(vertices[::2]) - min(vertices[::2]), 4)
        zoomed = array("f")
        zoomed.frombytes(marker_geometry(data, 20, 20, 20, False, True, False))
        self.assertGreater(len(zoomed), len(vertices), "zoom must reveal separate events")
        first = zoomed[:28]
        self.assertAlmostEqual((max(first[::2]) - min(first[::2])) * 20, 8, places=5)
        up, down = array("f"), array("f")
        up.frombytes(marker_geometry(data, 1, 1, 1, False, True, False))
        down.frombytes(marker_geometry(data, 1, 1, 1, False, False, True))
        self.assertEqual(list(up[::2]), list(down[::2]))
        self.assertEqual(list(up[1::2]), [-v for v in down[1::2]])

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_layer_preparation_holds_the_last_frame_without_applying_new_progress(self):
        from concurrent.futures import Future
        from PyQt6 import sip
        from PyQt6.QtQuick import QQuickWindow
        from plugins import GpuFollower as module
        from plugins.GpuStrokeMaterial import pack_shader

        class Layer:
            def geometry_payload(self):
                return {"classes": {"SKIN": [[(0, 0, 0), (10, 0, 1), (20, 0, 2)]]},
                        "travels": []}

        window = QQuickWindow()
        item = module.GpuFollower(window.contentItem())
        item._layers = {"current": Layer()}
        item._data = pack_shader(module.prepare((("current", item._layers["current"].geometry_payload()),)))
        item.settings = {"split": 2}
        old = item.updatePaintNode(None, None)
        printed = old._groups[0][-1]
        self.assertEqual(printed.geometry().vertexCount(), 6)
        with patch.object(module._POOL, "submit", return_value=Future()):
            item.layers = {"current": Layer()}
            generation = item._generation
            item.settings = {"split": 1000}  # The new manual layer lands at 100%.
            held = item.updatePaintNode(old, None)
            self.assertIs(held, old, "the preparing layer flashed an empty native tree")
            self.assertEqual(printed.geometry().vertexCount(), 6,
                             "new progress was applied to the held old layer")
            item._prepared(generation - 1, ())  # A retired worker cannot release the hold.
            self.assertIs(item.updatePaintNode(held, None), held)
            # Even an empty completed layer must retire the old frame.
            item._prepared(generation, ())
            empty = item.updatePaintNode(held, None)
            self.assertTrue(sip.isdeleted(held))
            self.assertEqual(empty.childCount(), 0)
            sip.delete(empty)
        sip.delete(window)

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

    def test_picker_retains_unaffected_geometry_for_hover_pan_and_palette(self):
        from PyQt6 import sip
        from plugins import GpuObjectPicker as module
        item = module.GpuObjectPicker()
        node = None
        scene = {
            'objects': [{'name': str(i), 'polygon': [[i, 0], [i+1, 0], [i+1, 1]]}
                        for i in range(3)],
            'plot': {'sx': 2, 'sy': 2, 'bed': {'bedXMax': 250, 'bedYMax': 250}},
            'showGrid': True, 'gridThin': '#333333', 'gridMajor': '#444444',
            'includedInk': '#ffffff', 'currentInk': '#0000ff',
            'excludedInk': '#ff0000', 'halo': '#000000',
        }
        try:
            with patch.object(module, 'object_strokes', wraps=module.object_strokes) as build:
                item.scene = scene
                node = item.updatePaintNode(None, None)
                self.assertEqual(build.call_count, 3)
                grid = node._grid_node.firstChild()
                unchanged = node._objects[2].firstChild()
                item.scene = dict(scene, hoveredName='0')
                node = item.updatePaintNode(node, None)
                self.assertEqual(build.call_count, 4)
                item.scene = dict(scene, hoveredName='1')
                node = item.updatePaintNode(node, None)
                self.assertEqual(build.call_count, 6)
                item.scene = dict(item.scene, includedInk='#00ff00',
                                  plot=dict(scene['plot'], bed=dict(scene['plot']['bed'], offsetX=25)))
                node = item.updatePaintNode(node, None)
                self.assertEqual(build.call_count, 6, 'pan/palette rebuilt geometry')
                self.assertIs(node._grid_node.firstChild(), grid)
                self.assertIs(node._objects[2].firstChild(), unchanged)
                outline = unchanged.nextSibling()
                self.assertEqual(outline.material().color().name(), '#00ff00')
                # Removing objects releases their native groups; an empty bed
                # cannot retain an excluded object's old outline.
                removed = node._objects[-1]
                item.scene = dict(item.scene, objects=scene['objects'][:1])
                node = item.updatePaintNode(node, None)
                self.assertEqual(len(node._objects), 1)
                self.assertTrue(sip.isdeleted(removed))
        finally:
            if node is not None:
                sip.delete(node)
            sip.delete(item)

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

    def test_picker_draws_center_only_objects_as_circles_and_skips_empty_rows(self):
        from plugins.GpuObjectPicker import object_strokes
        scene = {
            'objects': [{'center': [5, 7]}, {}],
            'plot': {'sx': 2, 'sy': 4}, 'screenScale': 2,
            'halo': '#000000', 'includedInk': '#ffffff',
        }
        strokes = object_strokes(scene)
        self.assertEqual(len(strokes), 2)
        # Both halo and ink use the same 32-sided, centre-only fallback.
        self.assertEqual(len(strokes[0][0]), len(strokes[1][0]))
        self.assertGreater(len(strokes[0][0]), 0)


if __name__ == "__main__":
    unittest.main()
