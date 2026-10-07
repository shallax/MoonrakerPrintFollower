"""Real Qt preview pixels, demand scheduling and visible-surface nozzle picking."""
from __future__ import annotations

import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtGui import QGuiApplication, QImage, QPainter
    from mpf.toolhead.ToolheadModelPreview import ToolheadModelPreview
    from mpf.geometry.ToolheadGeometry import mesh_from_arrays, camera_projection, pick_projected, visible_triangles
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

if QT_AVAILABLE:
    class Draft(QObject):
        changed = pyqtSignal()
        lightingPreviewChanged = pyqtSignal()

        def __init__(self, mesh):
            super().__init__()
            self.mesh = mesh
            self.tip = None
            self.hits = []
            self.lights = []
            self.light_hits = []

        def picked(self, point):
            self.hits.append(point)
            self.tip = point
            self.changed.emit()

        def pickedLight(self, position, direction, surface):
            self.light_hits.append((position, direction, surface))
            self.changed.emit()


@unittest.skipUnless(QT_AVAILABLE, "Real Qt is required")
class PreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.preview = ToolheadModelPreview()
        self.preview.setWidth(240)
        self.preview.setHeight(200)
        triangles = [[[-1, 0, 0], [1, 0, 0], [0, 0, 2]]]
        self.mesh = mesh_from_arrays(triangles, [(0.8, 0.2, 0.1, 1)])
        self.draft = Draft(self.mesh)
        self.preview.model = self.draft
        self.drain()

    def drain(self):
        deadline = time.monotonic() + 5
        while self.preview._worker is not None or self.preview._pending is not None:
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("preview worker did not retire")
            threading.Event().wait(.001)
        self.app.processEvents()

    def tearDown(self):
        self.drain()
        self.preview.model = None

    def paint(self):
        image = QImage(240, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        self.preview.paint(painter)
        painter.end()
        return image

    def test_real_model_and_tip_paint_pixels(self):
        image = self.paint()
        opaque = sum(image.pixelColor(x, y).alpha() > 0 for x in range(240) for y in range(200))
        self.assertGreater(opaque, 100)
        self.draft.tip = (0, 0, 0)
        self.draft.changed.emit()
        marked = self.paint()
        orange = sum(marked.pixelColor(x, y).red() > 240 and marked.pixelColor(x, y).green() < 130
                     for x in range(240) for y in range(200))
        self.assertGreater(orange, 10)

    def test_pick_requires_mode_and_visible_surface_then_exits(self):
        self.preview.pick(120, 100)
        self.assertEqual(self.draft.hits, [])
        self.preview.picking = True
        self.preview.picking = True
        self.preview.pick(-100, -100)
        self.assertTrue(self.preview.picking)
        projected = self.preview._projection[0].mean(axis=0)
        self.preview.pick(*map(float, projected[:2]))
        self.assertEqual(len(self.draft.hits), 1)
        self.assertFalse(self.preview.picking)
        np.testing.assert_allclose(self.draft.hits[0], self.mesh.triangles[0].mean(axis=0), atol=1e-6)

    def test_pan_keeps_geometry_and_pick_coordinates_in_sync(self):
        image = self.preview._image
        projection = self.preview._projection
        generation = self.preview._generation
        point = projection[0].mean(axis=0)
        self.preview.pan(25, -12)
        self.assertEqual(self.preview._pan, (25, -12))
        self.assertIs(self.preview._image, image)
        self.assertIs(self.preview._projection, projection)
        self.assertEqual(self.preview._generation, generation)
        self.preview.picking = True
        self.preview.pick(float(point[0]+25), float(point[1]-12))
        np.testing.assert_allclose(self.draft.hits[0], self.mesh.triangles[0].mean(axis=0), atol=1e-6)
        self.preview.resetCamera()
        self.drain()
        self.assertEqual(self.preview._pan, (0, 0))

    def test_horizontal_orbit_follows_drag_direction(self):
        self.preview.orbit(20, 0)
        self.drain()
        self.assertEqual(self.preview._yaw, 23)

    def test_camera_controls_clamp_and_reset(self):
        generation = self.preview._generation
        self.preview.model = self.draft
        self.assertEqual(generation, self.preview._generation)
        self.preview.picking = True
        self.preview.orbit(20, 1000)
        self.assertEqual(generation, self.preview._generation)
        self.preview.picking = False
        self.preview.orbit(20, 1000)
        self.assertEqual(self.preview._pitch, 89)
        self.preview.orbit(-20, -1000)
        self.assertEqual(self.preview._pitch, -89)
        for _ in range(40): self.preview.zoomBy(120)
        self.assertEqual(self.preview._zoom, 8)
        for _ in range(80): self.preview.zoomBy(-120)
        self.assertEqual(self.preview._zoom, .25)
        self.preview.resetCamera()
        self.drain()
        self.assertEqual((self.preview._yaw, self.preview._pitch, self.preview._zoom), (35, 25, 1))

    def test_new_mesh_cancels_picking_and_old_draft_disconnects(self):
        self.preview.picking = True
        self.draft.mesh = mesh_from_arrays(self.mesh.triangles * 2)
        self.draft.changed.emit()
        self.drain()
        self.assertFalse(self.preview.picking)
        self.assertIs(self.preview._mesh, self.draft.mesh)
        replacement = Draft(self.mesh)
        self.preview.model = replacement
        self.drain()
        generation = self.preview._generation
        self.draft.changed.emit()
        self.assertEqual(generation, self.preview._generation)
        self.preview.model = None
        self.preview._model_changed()
        self.preview.pick(0, 0)
        self.preview._start()

    def test_worker_failure_paints_reason_and_next_request_recovers(self):
        with patch("mpf.toolhead.ToolheadModelPreview.camera_projection", side_effect=ValueError("bad projection")):
            self.preview.resetCamera()
            self.drain()
        self.assertIn("bad projection", self.preview._error)
        self.assertIsNone(self.preview._projection)
        self.preview.picking = True
        self.preview.pick(120, 100)
        self.assertEqual(self.draft.hits, [])
        image = self.paint()
        self.assertTrue(any(image.pixelColor(x, 100).alpha() for x in range(240)))
        self.preview.resetCamera()
        self.drain()
        self.assertEqual(self.preview._error, "")
        self.assertIsNotNone(self.preview._image)

    def test_gpu_failure_switches_to_paintable_pickable_software_preview(self):
        gpu = self.preview._gpu = Mock()
        self.preview._packed = (b"packed", np.zeros(3), 1, 3)
        with patch("mpf.toolhead.ToolheadModelPreview.preview_buffer", side_effect=AssertionError("GPU retried")):
            self.preview._gpu_error("shader rejected")
            self.drain()
            self.assertIs(self.preview._gpu, gpu)
            gpu.setVisible.assert_called_once_with(False)
            gpu.update.assert_not_called()
            self.assertIsNone(self.preview._packed)
            self.assertIsNotNone(self.preview._image)
            image = self.paint()
            self.assertTrue(any(image.pixelColor(x, 100).alpha() for x in range(240)))
            self.preview.orbit(10, 5)
            self.drain()
            self.preview.pan(5, -3)
            point = self.preview._projection[0].mean(axis=0)
            self.preview.picking = True
            with patch("mpf.toolhead.ToolheadModelPreview.pick_projected", wraps=pick_projected) as pick:
                self.preview.pick(float(point[0]+5), float(point[1]-3))
                self.assertFalse(pick.call_args.kwargs["nearest"])
            np.testing.assert_allclose(self.draft.hits[0], self.mesh.triangles[0].mean(axis=0), atol=1e-6)
            replacement = Draft(mesh_from_arrays(self.mesh.triangles * 2))
            self.preview.model = replacement
            self.drain()
            self.assertIs(self.preview._mesh, replacement.mesh)
            generation = self.preview._generation
            self.preview._gpu_error("duplicate renderer failure")
            self.assertEqual(generation, self.preview._generation)

    def test_gpu_failure_retires_packing_in_flight_before_software_result(self):
        self.preview._gpu = Mock()
        entered, release = threading.Event(), threading.Event()

        def blocked(mesh):
            entered.set()
            if not release.wait(5): raise AssertionError("packing worker never released")
            return b"stale", np.zeros(3), 1, len(mesh.triangles)*3

        with patch("mpf.toolhead.ToolheadModelPreview.preview_buffer", side_effect=blocked) as pack:
            self.preview._request()
            self.assertTrue(entered.wait(2))
            self.preview._gpu_error("buffer creation failed")
            release.set()
            self.drain()
        self.assertEqual(pack.call_count, 1)
        self.assertIsNone(self.preview._packed)
        self.assertIsNotNone(self.preview._projection)
        self.assertIsNotNone(self.preview._image)

    def test_zero_sized_gpu_failure_still_retires_pending_gpu_result(self):
        self.preview._gpu = Mock()
        self.preview.setWidth(0)
        generation = self.preview._generation
        self.preview._gpu_error("context lost")
        self.preview._ready(generation, ("gpu", self.mesh, (b"stale", np.zeros(3), 1, 3)))
        self.assertIsNone(self.preview._packed)
        self.assertIsNone(self.preview._image)
        self.preview.setWidth(240)
        self.drain()
        self.assertIsNotNone(self.preview._image)

    def test_gpu_camera_changes_reuse_geometry_and_pick_frontmost_surface(self):
        gpu = Mock()
        with patch("mpf.toolhead.ToolheadModelPreview.QQuickWindow.graphicsApi") as api, \
                patch("mpf.toolhead.ToolheadModelPreview.ToolheadPreviewGL", return_value=gpu):
            from PyQt6.QtQuick import QSGRendererInterface
            api.return_value = QSGRendererInterface.GraphicsApi.OpenGL
            self.preview._attach_window(object())
            self.drain()
        packed = self.preview._packed
        self.assertIsNotNone(packed)
        with patch("mpf.toolhead.ToolheadModelPreview.preview_buffer", side_effect=AssertionError("camera repacked geometry")):
            self.preview.orbit(15, -10)
            self.preview.zoomBy(120)
            self.preview.pan(8, 9)
            self.assertIs(self.preview._packed, packed)
            image = self.paint()
            self.assertFalse(any(image.pixelColor(x, 100).alpha() for x in range(240)))
            projected = camera_projection(self.mesh, self.preview._yaw, self.preview._pitch, 240, 200, self.preview._zoom)[0]
            point = projected[0].mean(axis=0)
            self.preview.picking = True
            with patch("mpf.toolhead.ToolheadModelPreview.pick_projected", wraps=pick_projected) as pick:
                self.preview.pick(float(point[0]+8), float(point[1]+9))
                self.assertTrue(pick.call_args.kwargs["nearest"])
            np.testing.assert_allclose(self.draft.hits[0], self.mesh.triangles[0].mean(axis=0), atol=1e-6)
        gpu.setWidth.assert_called_with(240)
        gpu.setHeight.assert_called_with(200)

    def test_software_fallback_retains_light_surface_picking_and_markers(self):
        self.preview._gpu = Mock()
        self.preview._gpu_error("no shader support")
        self.drain()
        self.preview.addingLight = True
        point = self.preview._projection[0].mean(axis=0)
        self.preview.pick(*map(float, point[:2]))
        self.assertEqual(len(self.draft.light_hits), 1)
        position, direction, surface = self.draft.light_hits[0]
        self.assertEqual(surface, 0)
        np.testing.assert_allclose(position, self.mesh.triangles[0].mean(axis=0), atol=1e-6)
        self.assertGreaterEqual(np.dot(direction, self.preview._camera[1][:, 2]), 0)
        self.assertFalse(self.preview.addingLight)
        self.draft.lights = [{"position": position, "colour": "#00ff00"}]
        self.draft.lightingPreviewChanged.emit()
        image = self.paint()
        self.assertTrue(any(image.pixelColor(x, int(point[1])).green() > 240 for x in range(240)))

    def test_stale_worker_cannot_publish_over_latest_camera(self):
        entered, release = threading.Event(), threading.Event()
        original = camera_projection
        calls = []

        def blocked(*args):
            calls.append(args[1:3])
            if len(calls) == 1:
                entered.set()
                if not release.wait(5): raise AssertionError("test worker was never released")
            return original(*args)

        with patch("mpf.toolhead.ToolheadModelPreview.camera_projection", side_effect=blocked):
            self.preview.resetCamera()
            self.assertTrue(entered.wait(2))
            self.preview.orbit(50, 0)
            release.set()
            self.drain()
        self.assertGreaterEqual(len(calls), 2)
        expected = original(self.mesh, self.preview._yaw, self.preview._pitch, 240, 200, 1)[0]
        np.testing.assert_allclose(self.preview._projection, expected)

    def test_zero_sized_view_does_not_schedule(self):
        self.preview.setWidth(0)
        generation = self.preview._generation
        self.preview.resetCamera()
        self.assertEqual(generation, self.preview._generation)
        self.preview.setWidth(240)
        self.drain()

    def test_crossing_triangles_pick_last_painted_not_nearest_depth(self):
        # First face has high depth at the hit, but the second has higher
        # mean depth and is painted last by the orthographic preview.
        projected = np.array([[[0, 0, 10], [10, 0, -10], [0, 10, -10]],
                              [[0, 0, 0], [10, 0, 0], [0, 10, 0]]], dtype=float)
        mesh = mesh_from_arrays([[[0, 0, 10], [10, 0, -10], [0, 10, -10]],
                                 [[0, 0, 0], [10, 0, 0], [0, 10, 0]]])
        np.testing.assert_array_equal(visible_triangles(projected, 20, 20), [0, 1])
        np.testing.assert_allclose(pick_projected(mesh, projected, 1, 1), (1, 1, 0))

    def test_subpixel_and_alpha_zero_faces_cannot_be_picked(self):
        points = [[[0, 0, 1], [.1, 0, 1], [0, .1, 1]],
                  [[0, 0, 2], [10, 0, 2], [0, 10, 2]]]
        mesh = mesh_from_arrays(points, [(1, 0, 0, 1), (0, 1, 0, 0)])
        projected = np.array(points, dtype=float)
        self.assertEqual(len(visible_triangles(projected, 20, 20, mesh.colours)), 0)
        self.assertIsNone(pick_projected(mesh, projected, .01, .01))
