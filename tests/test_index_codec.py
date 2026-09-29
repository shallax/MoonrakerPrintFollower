"""Corrupt optional columns must never be accepted as faithful motion data."""
from copy import deepcopy
import unittest

from mpf.gcode import ArcGeometry, IndexCodec


class IndexColumnValidationTests(unittest.TestCase):
    def test_event_columns_reject_ragged_or_untyped_events(self):
        for events, seeds in (([[[0, True]]], [False, False]),
                              ([[[0]]], [False]),
                              ([[[0, 1]]], [False]),
                              ([[[2, False]]], [False]),
                              ([[[0, False]]], [0])):
            with self.subTest(events=events, seeds=seeds):
                self.assertIsNone(IndexCodec._event_columns([1], events, seeds))
        self.assertEqual(IndexCodec._event_columns([1], [[[0, True]]], [False]),
                         {"extruder_events": [[[0, True]]], "start_retracted": [False]})

    def test_arc_columns_reject_wrong_container_types_and_nonfinite_offsets(self):
        xy = ArcGeometry.PLANE_XY
        valid = [[[0, xy, True, 1.0, 0.0]]]
        for arcs, planes in ((None, [xy]), ({1: valid[0]}, [xy]),
                             ([{0: valid[0][0]}], [xy]),
                             (valid, {0: xy})):
            with self.subTest(arcs=arcs, planes=planes):
                self.assertIsNone(IndexCodec._arc_columns([1], arcs, planes))
        for offset in (True, float("nan"), float("inf"), "1"):
            arcs = deepcopy(valid)
            arcs[0][0][3] = offset
            with self.subTest(offset=offset):
                self.assertIsNone(IndexCodec._arc_columns([1], arcs, [xy]))
        self.assertEqual(IndexCodec._arc_entries(valid), valid)
        self.assertEqual(IndexCodec._arc_columns([1], valid, [xy])["arcs"], valid)

    def test_feature_seeds_must_be_finite(self):
        for seed in (float("nan"), float("inf"), -float("inf")):
            columns = ([[[1, 0]]], [[]], [[]], [0], [seed], [True], [True])
            with self.subTest(seed=seed):
                self.assertIsNone(IndexCodec._feature_columns([1], ["WALL"], columns))
