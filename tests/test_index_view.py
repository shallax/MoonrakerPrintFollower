"""Read-only query fallbacks for unavailable or invalid continuous-Z evidence."""
import unittest

from mpf.GCode.GCodeIndex import build_index_from_bytes
from mpf.GCode.IndexView import IndexView


class ContinuousZInputTests(unittest.TestCase):
    def test_invalid_height_has_no_motion_verdict(self):
        index = build_index_from_bytes(b";LAYER:0\nG1 X1 Y1 Z0.2\n")
        view = IndexView(("file", 1), index)
        before = tuple(index.motion_z[0])
        for z in (None, "bad", float("nan"), float("inf"), -float("inf")):
            with self.subTest(z=z):
                self.assertIsNone(view.spiral_z_split(0, z))
        self.assertFalse(view.continuous_z_at(0), "a single edge cannot prove a spiral")
        self.assertEqual(tuple(index.motion_z[0]), before)
