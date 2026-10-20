# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify read-only extrusion-run queries for independent consumers.

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from mpf.gcode import IndexView, build_index_from_bytes, build_index_from_file


class ExtrusionRunTests(unittest.TestCase):
    def setUp(self):
        self.source = b'G90\nM83\n;LAYER:0\nG1 X0 Z0.2 F1800\nG1 X10 E1\nG1 X20 E1\n'
        self.view = IndexView(('part.gcode', len(self.source), 1), build_index_from_bytes(self.source))

    def test_distance_move_budget_and_seam_are_read_only_evidence(self):
        run = self.view.extrusion_run_before(0, 2, 5)
        self.assertEqual((run.motions, run.distance_mm, run.limited_by_seam), (1, 10, False))
        self.assertEqual(run.boundary.completed_motions, 1)
        run = self.view.extrusion_run_before(0, 3, 100, 2)
        self.assertEqual((run.motions, run.distance_mm, run.limited_by_seam), (2, 20, False))
        run = self.view.extrusion_run_before(0, 3, 100)
        self.assertEqual((run.motions, run.distance_mm, run.limited_by_seam), (2, 20, True))
        self.assertEqual(self.view.extrusion_run_before(0, 1), None)
        self.assertEqual(self.view.recovery_boundary(0, 1).next_motion_offset, self.source.index(b'G1 X10'))

    def test_unretained_or_invalid_requests_never_estimate_a_boundary(self):
        for layer, split, distance, moves in ((-1, 2, 5, 0), (True, 2, 5, 0), (0, -1, 5, 0),
                (0, 99, 5, 0), (0, True, 5, 0), (0, 2, 'bad', 0), (0, 2, float('inf'), 0),
                (0, 2, 0, 0), (0, 2, 5, True), (0, 2, 5, -1)):
            with self.subTest(layer=layer, split=split, distance=distance, moves=moves):
                self.assertIsNone(self.view.extrusion_run_before(layer, split, distance, moves))
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'part.gcode'
            path.write_bytes(self.source)
            compact = IndexView(self.view.job_key, build_index_from_file(str(path), compact=True))
            self.assertIsNone(compact.extrusion_run_before(0, 2))
