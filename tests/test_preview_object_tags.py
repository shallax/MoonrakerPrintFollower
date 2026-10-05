"""Object banner placement and real-engine Preview overlay checks."""
from __future__ import annotations

import unittest
import time

from mpf.preview.ObjectNameProjection import footprint_hits, place_banners
from tests import qml_engine_support as harness


class BannerPlacementTests(unittest.TestCase):
    def test_crowded_anchors_get_distinct_plates_and_bent_leaders(self):
        objects = [{"name": f"Object {index}", "position": (100, 100), "nodeId": index,
                    "progress": index / 4} for index in range(4)]
        rows = place_banners(objects, lambda point: point, 500, 400)
        self.assertEqual(len(rows), 4)
        self.assertTrue(any(abs(row["labelX"] + row["labelWidth"] / 2 - row["anchorX"]) > 0.5
                            for row in rows))
        for index, left in enumerate(rows):
            self.assertEqual((left["anchorX"], left["anchorY"]), (100, 100))
            self.assertLess(left["labelY"] + left["labelHeight"], left["anchorY"])
            for right in rows[index + 1:]:
                separated = (left["labelX"] + left["labelWidth"] + 5 < right["labelX"]
                             or right["labelX"] + right["labelWidth"] + 5 < left["labelX"]
                             or left["labelY"] + left["labelHeight"] + 5 < right["labelY"]
                             or right["labelY"] + right["labelHeight"] + 5 < left["labelY"])
                self.assertTrue(separated)

    def test_eta_plate_stays_above_its_anchor(self):
        row = {"name": "tower", "position": (200, 120), "deadline": time.time() + 90}
        plate = place_banners([row], lambda point: point, 500, 400)[0]
        self.assertLess(plate["labelY"] + plate["labelHeight"], plate["anchorY"])

    def test_offscreen_centres_keep_visible_tags_but_nonfinite_anchors_do_not(self):
        objects = [{"name": "far", "position": (800, 30)},
                   {"name": "bad", "position": (float("nan"), 10)}]
        rows = place_banners(objects, lambda point: point, 500, 400)
        self.assertEqual([row["name"] for row in rows], ["far"])
        self.assertEqual(rows[0]["anchorX"], 800)
        self.assertLessEqual(rows[0]["labelX"] + rows[0]["labelWidth"], 496)

    def test_hover_only_uses_only_hovered_objects_and_straight_leaders(self):
        objects = [{"name": "first", "position": (100, 140), "nodeId": 1},
                   {"name": "second", "position": (100, 140), "nodeId": 2},
                   {"name": "third", "position": (100, 140), "nodeId": 3}]
        rows = place_banners(objects, lambda point: point, 500, 400,
                             hover_only=True, hovered_node=3)
        self.assertEqual([row["name"] for row in rows], ["third"])
        self.assertAlmostEqual(rows[0]["labelX"] + rows[0]["labelWidth"] / 2,
                               rows[0]["anchorX"])
        self.assertLess(rows[0]["labelY"] + rows[0]["labelHeight"], rows[0]["anchorY"])

    def test_hover_only_keeps_every_shared_footprint_banner(self):
        objects = [{"name": name, "position": (150, 180), "nodeId": 0}
                   for name in ("first", "second", "third")]
        rows = place_banners(objects, lambda point: point, 500, 400,
                             hover_only=True, hovered_names=("first", "third"))
        self.assertEqual({row["name"] for row in rows}, {"first", "third"})
        self.assertNotEqual(rows[0]["labelY"], rows[1]["labelY"])
        self.assertTrue(all(row["anchorX"] == 150 and row["anchorY"] == 180 for row in rows))

    def test_crowding_and_top_edge_never_drop_finite_banners(self):
        objects = [{"name": f"Object {index}", "position": (30, 4)} for index in range(12)]
        rows = place_banners(objects, lambda point: point, 100, 65)
        self.assertEqual(len(rows), len(objects))
        for row in rows:
            self.assertGreaterEqual(row["labelX"], 4)
            self.assertGreaterEqual(row["labelY"], 4)
            self.assertLessEqual(row["labelX"] + row["labelWidth"], 96)
            self.assertLessEqual(row["labelY"] + row["labelHeight"], 61)

    def test_footprint_hover_returns_every_overlapping_object(self):
        square = [(-2, -2), (2, -2), (2, 2), (-2, 2)]
        objects = [{"name": "front", "footprint": square, "footprintHeight": 2},
                   {"name": "back", "footprint": square, "footprintHeight": 0},
                   {"name": "elsewhere", "footprint": [(10, 10), (12, 10), (12, 12)],
                    "footprintHeight": 0}]
        self.assertEqual(footprint_hits(objects, (0, 10, 0), (0, -1, 0)), ["front", "back"])
        self.assertEqual(footprint_hits(objects, (0, 10, 0), (0, 1, 0)), [])
        self.assertEqual(footprint_hits(objects, (0, 10, 0), (1, 0, 0)), [])

    def test_unprojectable_objects_and_unbounded_footprints_are_ignored(self):
        objects = [{"name": "no polygon", "footprint": [(0, 0), (1, 1)],
                    "footprintHeight": 0},
                   {"name": "valid", "footprint": [(-1, -1), (1, -1), (1, 1)],
                    "footprintHeight": 0}]
        self.assertEqual(footprint_hits(objects, (0, 10, 0), (0, -1, 0)), ["valid"])
        self.assertEqual(place_banners(objects, lambda point: (10, 10), 8, 100), [])
        self.assertEqual(place_banners([{"name": "unprojectable", "position": (0, 0)}],
                                       lambda point: None, 100, 100), [])


class BannerHostTests(harness.RealEngineTestCase):
    def test_hover_only_tooltip_explains_why_the_option_is_disabled(self):
        host = self.mount("PreviewObjectTagsHost.qml")
        tooltip = self.find(host, "moonrakerPreviewObjectTagsHoverOnlyTooltip")
        host.setProperty("projectionAvailable", True)
        host.setProperty("tagsEnabled", False)
        host.setProperty("pickAvailable", False)
        self.pump(10)
        self.assertEqual(tooltip.property("text"),
                         "Enable Object name banners to use this option.")
        host.setProperty("tagsEnabled", True)
        self.pump(10)
        self.assertEqual(tooltip.property("text"),
                         "No object footprints are available for this toolpath.")
        host.setProperty("pickAvailable", True)
        host.setProperty("pickMode", "footprint")
        self.pump(10)
        self.assertIn("G-code picking is approximate.", tooltip.property("text"))
        host.setProperty("projectionAvailable", False)
        self.pump(10)
        self.assertEqual(tooltip.property("text"),
                         "This Cura version does not provide camera projection to plugins.")

    def test_bottom_bar_expands_upward_when_its_handle_is_clicked(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest

        host = self.mount("PreviewObjectTagsHost.qml")
        window = harness.QQuickWindow()
        window.resize(1400, 800)
        host.setParentItem(window.contentItem())
        host.setProperty("dockVisible", True)
        host.setProperty("previewCardLeft", 1060)
        host.setProperty("previewCardBottom", 620)
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(30)
        dock = self.find(host, "moonrakerPreviewObjectTagsDock")
        self.assertLessEqual(dock.x() + dock.width(), 1060 - 12)
        self.assertAlmostEqual(dock.y() + dock.height(), 620, delta=1)
        self.assertAlmostEqual(dock.height(), 34, delta=1)
        collapsed_bottom = dock.y() + dock.height()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=QPoint(round(dock.x() + dock.width() / 2), round(dock.y() + 17)))
        self.pump(20)
        self.assertTrue(host.property("controlsExpanded"))
        self.assertGreater(dock.height(), 100)
        self.assertAlmostEqual(dock.y() + dock.height(), collapsed_bottom, delta=1)
        title = self.find(host, "moonrakerPreviewObjectTagsTitle")
        controls = self.find(host, "moonrakerPreviewObjectTagsControls")
        padding = host.property("dockVerticalPadding")
        self.assertAlmostEqual(title.mapToItem(dock, harness.QPointF(0, 0)).y(),
                               padding, delta=1)
        self.assertAlmostEqual(dock.height() - controls.mapToItem(
            dock, harness.QPointF(0, controls.implicitHeight())).y(), padding, delta=1)

    def test_controls_and_hover_mode_render(self):
        host = self.mount("PreviewObjectTagsHost.qml")
        window = harness.QQuickWindow()
        window.resize(800, 600)
        host.setParentItem(window.contentItem())
        host.setProperty("dockVisible", True)
        host.setProperty("projectionAvailable", True)
        host.setProperty("pickAvailable", True)
        host.setProperty("tagsEnabled", True)
        host.setProperty("tagRows", [
            {"name": "Left", "nodeId": 11, "anchorX": 210, "anchorY": 260,
             "labelX": 170, "labelY": 205, "labelWidth": 120, "labelHeight": 44, "progress": 0.5,
             "deadline": time.time() + 85},
            {"name": "Right", "nodeId": 12, "anchorX": 330, "anchorY": 260,
             "labelX": 290, "labelY": 205, "labelWidth": 90, "labelHeight": 24, "progress": None,
             "deadline": None},
        ])
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(30)
        self.assertIsNotNone(self.find(host, "moonrakerPreviewObjectTagsEnabled"))
        banners = [item for item in host.childItems()
                   if item.objectName() == "moonrakerObjectNameBanner"]
        self.assertEqual(len(banners), 2)
        host.setProperty("hoverOnly", True)
        host.setProperty("hoveredNode", 11)
        self.pump(10)
        self.assertEqual(sum(item.isVisible() for item in banners), 1)
        selected = next(item for item in banners if item.property("modelData")["nodeId"] == 11)
        other = next(item for item in banners if item.property("modelData")["nodeId"] == 12)
        self.assertGreater(selected.z(), other.z())
        host.setProperty("hoverOnly", False)
        self.pump(10)
        self.assertLess(other.opacity(), selected.opacity())
        host.setProperty("hoveredNode", 0)
        host.setProperty("hoveredNames", ["Left", "Right"])
        host.setProperty("hoverOnly", True)
        self.pump(10)
        self.assertEqual(sum(item.isVisible() for item in banners), 2)
        self.assertEqual(selected.z(), other.z())

    def test_any_hover_fades_even_distant_unselected_banners(self):
        host = self.mount("PreviewObjectTagsHost.qml")
        window = harness.QQuickWindow()
        window.resize(900, 600)
        host.setParentItem(window.contentItem())
        host.setProperty("dockVisible", True)
        host.setProperty("tagsEnabled", True)
        host.setProperty("tagRows", [
            {"name": name, "nodeId": node_id, "anchorX": x, "anchorY": 260,
             "labelX": x - 45, "labelY": 200, "labelWidth": 90, "labelHeight": 24,
             "progress": None, "deadline": None}
            for name, node_id, x in (("Left", 1, 100), ("Right", 2, 800))
        ])
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(20)
        banners = [item for item in host.childItems()
                   if item.objectName() == "moonrakerObjectNameBanner"]
        host.setProperty("hoveredNode", 1)
        self.pump(10)
        selected = next(item for item in banners if item.property("modelData")["nodeId"] == 1)
        distant = next(item for item in banners if item.property("modelData")["nodeId"] == 2)
        self.assertEqual(selected.opacity(), 1)
        self.assertAlmostEqual(distant.opacity(), 0.25)

    def test_long_name_moves_inside_a_clipped_viewport(self):
        host = self.mount("PreviewObjectTagsHost.qml")
        window = harness.QQuickWindow()
        window.resize(800, 600)
        host.setParentItem(window.contentItem())
        host.setProperty("dockVisible", True)
        host.setProperty("tagsEnabled", True)
        host.setProperty("tagRows", [
            {"name": "A very long object name that should scroll both ways", "nodeId": 0,
             "anchorX": 210, "anchorY": 260, "labelX": 170, "labelY": 205,
             "labelWidth": 120, "labelHeight": 30, "progress": 0.5, "deadline": None},
        ])
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(20)
        banner = next(item for item in host.childItems()
                      if item.objectName() == "moonrakerObjectNameBanner")
        viewport = next(item for item in banner.childItems()
                        if item.property("clip") is True)
        label = viewport.childItems()[0]
        self.assertTrue(viewport.property("clip"))
        self.assertGreater(label.property("implicitWidth"), viewport.width())
        travel = label.property("implicitWidth") - viewport.width()
        duration = max(1800, round(travel * 22))
        host.setProperty("marqueeClock", 550 + duration / 2)
        self.pump(5)
        self.assertAlmostEqual(label.x(), -travel / 2, delta=2)
        host.setProperty("marqueeClock", 550 + duration)
        self.pump(5)
        self.assertAlmostEqual(label.x(), -travel, delta=2)
        host.setProperty("marqueeClock", 550 + duration + 550 + duration / 2)
        self.pump(5)
        self.assertAlmostEqual(label.x(), -travel / 2, delta=2)
