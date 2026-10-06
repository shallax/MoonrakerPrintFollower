"""Camera motion invalidates projection/rays; telemetry and object movement do not."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from mpf.preview.CameraProjection import CameraProjection


class CameraProjectionTests(unittest.TestCase):
    def setUp(self):
        self.world, self.projection = np.eye(4), np.eye(4)
        self.camera = SimpleNamespace(
            getWorldTransformation=lambda: SimpleNamespace(getData=lambda: self.world),
            getProjectionMatrix=lambda: SimpleNamespace(getData=lambda: self.projection),
            getViewportWidth=lambda: 800, getViewportHeight=lambda: 600,
            isPerspective=lambda: True, getProjectToViewMatrix=Mock(return_value=object()),
            getRay=Mock(side_effect=lambda x, y: (x, y)))
        self.cache = CameraProjection()

    def test_many_points_and_telemetry_updates_share_one_camera_inverse(self):
        class Point:
            def __init__(self): self.x, self.y, self.z = 2., 3., 2.
            def __itruediv__(self, value):
                self.x /= value; self.y /= value; self.z /= value
                return self
        position = SimpleNamespace(preMultiply=lambda matrix: Point())
        for _ in range(40):
            project = self.cache.projector(self.camera)
            self.assertEqual(project(position), (200., 225.))
        self.assertEqual(self.camera.getProjectToViewMatrix.call_count, 1)
        self.world[0, 3] = 1
        self.cache.projector(self.camera)
        self.assertEqual(self.camera.getProjectToViewMatrix.call_count, 2)
        self.projection[0, 0] = 2
        self.cache.projector(self.camera)
        self.assertEqual(self.camera.getProjectToViewMatrix.call_count, 3)
        self.camera.isPerspective = lambda: False
        self.assertEqual(self.cache.projector(self.camera)(position), (800., 900.))

    def test_hover_ray_reuses_inverse_but_changes_with_pointer_camera_or_window(self):
        for _ in range(10): self.assertEqual(self.cache.ray(self.camera, 400, 300, 800, 600), (0, 0))
        self.assertEqual(self.camera.getRay.call_count, 1)
        self.cache.ray(self.camera, 500, 300, 800, 600)
        self.world[2, 3] = 2
        self.cache.ray(self.camera, 500, 300, 800, 600)
        self.cache.ray(self.camera, 500, 300, 1000, 600)
        self.assertEqual(self.camera.getRay.call_count, 4)

    def test_older_camera_keeps_public_projection_and_ray_fallback(self):
        camera = SimpleNamespace(projectToViewport=Mock(return_value=(1, 2)), getRay=Mock(return_value='ray'))
        self.assertIs(self.cache.projector(camera), camera.projectToViewport)
        self.assertEqual(self.cache.ray(camera, 1, 2, 10, 10), 'ray')
        self.assertEqual(self.cache.ray(camera, 1, 2, 10, 10), 'ray')
        self.assertEqual(camera.getRay.call_count, 2)
