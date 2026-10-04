"""Region masks must fail closed and exclude pixels before model resizing."""
import unittest

from PyQt6.QtGui import QImage
from mpf.geometry.DetectionRegions import validate_regions, fingerprint, persisted_regions
from mpf.detection.DetectionMask import masked_image

LEFT = [[0, 0], [.5, 0], [.5, 1], [0, 1]]


class DetectionRegionTests(unittest.TestCase):
    def test_shape_identity_ignores_start_direction_and_polygon_order(self):
        right = [[.6, .1], [.9, .1], [.9, .9], [.6, .9]]
        self.assertEqual(fingerprint([LEFT, right]), fingerprint([list(reversed(right)), LEFT[2:] + LEFT[:2]]))

    def test_invalid_records_never_become_full_frame(self):
        for invalid in (None, [], {"camera": None}, {"camera": [[[10**400, 0], [0, 1], [1, 0]]]}):
            with self.subTest(value=str(invalid)[:30]):
                self.assertIn(None, persisted_regions(invalid).values())
        self.assertEqual(persisted_regions({}), {})

    def test_polygon_validation_rejects_crossings_duplicates_bounds_and_tiny_areas(self):
        for polygon in ([[0, 0], [1, 1], [0, 1], [1, 0]],
                        [[0, 0], [1, 0], [0, 0]],
                        [[True, 0], [1, 0], [0, 1]],
                        [[0, 0], [float('nan'), 0], [0, 1]],
                        [[0, 0], [.001, 0], [0, .001]],
                        [[0, 0], [1, 0], [.5, 0], [1, 1], [0, 1]]):
            with self.subTest(polygon=polygon), self.assertRaises(ValueError):
                validate_regions([polygon])

    def test_mask_union_keeps_source_pixels_and_blacks_out_excluded_pixels(self):
        image = QImage(100, 100, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        second = [[.4, .3], [.8, .3], [.8, .8], [.4, .8]]
        result = masked_image(image, validate_regions([LEFT, list(reversed(second))]))
        self.assertEqual(result.pixelColor(20, 20).red(), 255)
        self.assertEqual(result.pixelColor(45, 45).red(), 255)
        self.assertEqual(result.pixelColor(70, 70).red(), 255)
        self.assertEqual(result.pixelColor(90, 90).red(), 0)
        self.assertEqual(image.pixelColor(90, 90).red(), 255)
        self.assertEqual(masked_image(image, []).cacheKey(), image.cacheKey())

    def test_limits_reject_extra_polygons_vertices_and_camera_records(self):
        with self.assertRaises(ValueError):
            validate_regions([LEFT] * 5)
        with self.assertRaises(ValueError):
            validate_regions([[[i / 33, i / 33] for i in range(33)]])
        self.assertEqual(persisted_regions({str(i): [LEFT] for i in range(33)}), {"invalid": None})
