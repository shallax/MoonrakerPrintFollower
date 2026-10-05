"""Object banner placement and real-engine Preview overlay checks."""
from __future__ import annotations

import unittest
import time

from mpf.preview.ObjectNameProjection import place_banners
from tests import qml_engine_support as harness


class BannerPlacementTests(unittest.TestCase):
    def test_crowded_anchors_get_distinct_plates_and_bent_leaders(self):
        objects = [{"name": f"Object {index}", "position": (100, 100), "nodeId": index,
                    "progress": index / 4} for index in range(4)]
        rows = place_banners(objects, lambda point: point, 500, 400)
        self.assertEqual(len(rows), 4)
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

    def test_offscreen_and_nonfinite_anchors_do_not_make_tags(self):
        objects = [{"name": "far", "position": (800, 30)},
                   {"name": "bad", "position": (float("nan"), 10)}]
        self.assertEqual(place_banners(objects, lambda point: point, 500, 400), [])


class BannerHostTests(harness.RealEngineTestCase):
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
