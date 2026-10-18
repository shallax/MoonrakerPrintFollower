# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-13 [3f9fcb5] Begin v6 recovery with exact follower boundary mapping

"""Exact scrub-to-file mapping used by the planned print recovery flow."""
import os
import tempfile
import unittest

from mpf.gcode.GCodeIndex import build_index_from_file
from mpf.gcode.IndexHydrator import hydrate_layer_from_file
from mpf.gcode.IndexView import IndexView


GCODE = (b"G90\n;LAYER:0\n"
         b"G1 X1 Y1 Z0.2 E0.1\n"
         b"G1 X2 Y1 E0.2\n"
         b";LAYER:1\n; empty layer\n"
         b";LAYER:2\nG2 X3 Y2 I0 J1 E0.3\n")


class RecoveryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "print.gcode")
        with open(self.path, "wb") as handle:
            handle.write(GCODE)

    def view(self, *, compact=False):
        index = build_index_from_file(self.path, compact=compact)
        return IndexView(("print.gcode", len(GCODE), 1), index)

    def test_within_layer_boundary_points_to_the_next_original_command(self):
        view = self.view()
        first = view.recovery_boundary(0, 0)
        self.assertEqual(first.next_motion_offset, GCODE.index(b"G1 X1"))
        self.assertEqual(first.position, (0.0, 0.0, 0.0))
        second = view.recovery_boundary(0, 1)
        self.assertEqual(second.next_motion_offset, GCODE.index(b"G1 X2"))
        self.assertAlmostEqual(second.position[0], 1.0)
        self.assertEqual((second.next_layer, second.next_motion), (0, 1))

    def test_full_layer_skips_empty_layer_but_preserves_selected_position(self):
        view = self.view()
        boundary = view.recovery_boundary(0, -1)
        self.assertEqual((boundary.selected_layer, boundary.completed_motions), (0, 2))
        self.assertEqual((boundary.next_layer, boundary.next_motion), (2, 0))
        self.assertEqual(boundary.next_motion_offset, GCODE.index(b"G2 X3"))
        self.assertAlmostEqual(boundary.position[0], 2.0)
        self.assertIsNone(view.recovery_boundary(2, -1))

    def test_compact_or_evicted_arrays_never_fall_back_to_estimated_bytes(self):
        view = self.view(compact=True)
        view._index.followed_layer = 1  # retain the three-layer window
        self.assertIsNone(view.recovery_boundary(0, 1))
        self.assertTrue(hydrate_layer_from_file(view._index, self.path, 0))
        self.assertIsNotNone(view.recovery_boundary(0, 1))
        self.assertIsNone(view.recovery_boundary(0, -1))  # next layer unavailable
        self.assertTrue(hydrate_layer_from_file(view._index, self.path, 1))
        self.assertIsNone(view.recovery_boundary(0, -1))  # layer 2 still unavailable
        self.assertTrue(hydrate_layer_from_file(view._index, self.path, 2))
        self.assertIsNotNone(view.recovery_boundary(0, -1))
        view._index.motion_offsets[0] = view._index.motion_offsets[0][:0]
        self.assertIsNone(view.recovery_boundary(0, 1))

    def test_refuses_fractional_out_of_range_and_boolean_selections(self):
        view = self.view()
        for layer, split in ((-1, 0), (3, 0), (True, 0), (0, True),
                             (0, 1.5), (0, -2), (0, 3)):
            with self.subTest(layer=layer, split=split):
                self.assertIsNone(view.recovery_boundary(layer, split))


if __name__ == "__main__":
    unittest.main()
