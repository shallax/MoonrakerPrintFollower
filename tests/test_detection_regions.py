"""Region masks must fail closed and exclude pixels before model resizing."""
import unittest

from PyQt6.QtGui import QImage
from mpf.geometry.DetectionRegions import validate_regions, fingerprint, persisted_regions
from mpf.detection.DetectionMask import masked_image, cropped_masked_image, RegionResolutionError

LEFT = [[0, 0], [.5, 0], [.5, 1], [0, 1]]


class DetectionRegionTests(unittest.TestCase):
    def test_crop_encloses_all_polygons_and_preserves_concave_mask(self):
        image = QImage(100, 80, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        regions = validate_regions([
            [[.105, .125], [.4, .125], [.4, .25], [.2, .25], [.2, .5], [.105, .5]],
            [[.7, .5], [.805, .5], [.805, .875], [.7, .875]]])
        cropped, bounds = cropped_masked_image(image, regions)
        self.assertEqual(bounds.getRect(), (10, 10, 71, 60))
        self.assertEqual((cropped.width(), cropped.height()), (71, 60))
        self.assertEqual(cropped.pixelColor(5, 20).red(), 255)
        self.assertEqual(cropped.pixelColor(20, 20).red(), 0)
        self.assertEqual(cropped.pixelColor(65, 50).red(), 255)
        self.assertEqual(image.pixelColor(30, 30).red(), 255)

    def test_crop_handles_full_frame_no_regions_and_tiny_resolution(self):
        image = QImage(100, 80, QImage.Format.Format_RGB888)
        image.fill(0xffffff)
        unchanged, bounds = cropped_masked_image(image, [])
        self.assertEqual(unchanged.cacheKey(), image.cacheKey())
        self.assertEqual(bounds, image.rect())
        full, bounds = cropped_masked_image(image, [[[0, 0], [1, 0], [1, 1], [0, 1]]])
        self.assertEqual(bounds, image.rect())
        self.assertEqual(full.pixelColor(99, 79).red(), 255)
        with self.assertRaises(RegionResolutionError):
            cropped_masked_image(image, [[[.1, .1], [.11, .1], [.11, .11], [.1, .11]]])

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
