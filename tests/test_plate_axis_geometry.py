"""Bed-border arrow geometry used by the raster and GPU paths."""
import unittest

from mpf.plate.PlateAxisGeometry import axis_arrows


class PlateAxisGeometryTests(unittest.TestCase):
    def test_half_heads_are_open_and_entire_strokes_stay_inside_the_bed(self):
        for width, height, stroke in ((400, 300, 2), (90, 70, 1)):
            x_arrow, y_arrow = axis_arrows(2, 2, width + 2, height + 2, stroke)
            x_shaft, x_head = x_arrow
            y_shaft, y_head = y_arrow
            self.assertEqual(len(x_arrow), 2)
            self.assertEqual(len(y_arrow), 2)
            self.assertAlmostEqual(x_shaft[0] - x_shaft[2], .15 * width)
            self.assertAlmostEqual(y_shaft[3] - y_shaft[1], .15 * height)
            self.assertEqual(x_shaft[2:], x_head[:2])
            self.assertEqual(y_shaft[2:], y_head[:2])
            self.assertGreater(x_head[2], x_head[0])
            self.assertGreater(x_head[3], x_head[1])
            self.assertLess(y_head[2], y_head[0])
            self.assertLess(y_head[3], y_head[1])
            for arrow in (x_arrow, y_arrow):
                for ax, ay, bx, by in arrow:
                    for x, y in ((ax, ay), (bx, by)):
                        self.assertGreaterEqual(x - stroke / 2, 2)
                        self.assertLessEqual(x + stroke / 2, width + 2)
                        self.assertGreaterEqual(y - stroke / 2, 2)
                        self.assertLessEqual(y + stroke / 2, height + 2)
