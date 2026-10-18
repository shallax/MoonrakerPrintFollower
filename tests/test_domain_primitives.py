# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
# 2026-09-30 [f425547] mpf's subpackages are capitalised
# 2026-09-29 [90e8a59] Organise source by feature ownership and enforce domain boundaries

"""Shared extracted primitives retain their previous numerical behaviour."""
import math
import unittest

from mpf.gcode.MotionRanges import valid_ranges
from mpf.geometry.Polygons import point_in_polygon, polygon_bounds, segment_in_polygon


class DomainPrimitiveTests(unittest.TestCase):
    def test_range_validation(self):
        for value in ({}, {"speed": [0, 100]}, {"height": (0.2, 0.2), "flow": [0, 1e9]}):
            with self.subTest(value=value):
                self.assertTrue(valid_ranges(value))
        for value in (None, [], {"colour": [0, 1]}, {"speed": [1, 0]}, {"speed": [0]},
                      {"width": "01"}, {"height": [-1, 1]}, {"flow": [0, 1e9 + 1]},
                      {"speed": [0, math.inf]}, {"speed": [math.nan, 1]}, {"speed": ["0", 1]}):
            with self.subTest(value=value):
                self.assertFalse(valid_ranges(value))

    def test_polygon_intersections_and_bounds(self):
        square = [[0, 0], [10, 0], [10, 10], [0, 10]]
        self.assertEqual(polygon_bounds(square), (0, 0, 10, 10))
        self.assertTrue(point_in_polygon(5, 5, square))
        self.assertFalse(point_in_polygon(20, 5, square))
        self.assertFalse(point_in_polygon(0, 0, []))
        for line in ((5, 5, 20, 20), (-5, 5, 5, 5), (-5, 5, 15, 5),
                     (-5, 0, 15, 0), (-5, -5, 0, 0), (10, 10, 15, 15), (2, 0, 8, 0)):
            with self.subTest(line=line):
                self.assertTrue(segment_in_polygon(*line, square))
        for line in ((-5, -5, -1, -1), (11, 0, 20, 0), (0, 11, 0, 20), (-5, 11, 15, 11)):
            with self.subTest(line=line):
                self.assertFalse(segment_in_polygon(*line, square))


if __name__ == "__main__":
    unittest.main()
