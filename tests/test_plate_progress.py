"""The follower payload's prep: distance decimation, per-class
grouping, the travel markers, the split index and the honest
unavailable shape — pure over hand-built index fixtures.
"""
from __future__ import annotations

from array import array
import unittest

from plugins.GCodeIndex import LayerMotionIndex
from plugins.PlateProgress import layer_polylines, plate_progress, split_index


def make_index(*, layers=1, motions=20, compact=False):
    """A small synthetic index: one straight row of motions per layer,
    all of one TYPE block, hydrated when not compact."""
    index = LayerMotionIndex(
        ranges=[(0, 100)] * layers,
        motion_offsets=[array("Q", [m * 10 for m in range(motions)])] * layers,
        motion_x=[array("f", [float(m) for m in range(motions)])] * layers,
        motion_y=[array("f", [0.0] * motions)] * layers,
        motion_z=[array("f", [0.2] * motions)] * layers,
        motion_types=[[[motions, 2]]] * layers,
        type_names=["WALL-OUTER"],
        travel_starts=[[]] * layers,
        travel_ends=[[]] * layers,
        layer_start_positions=[(0.0, 0.0, 0.0)] * layers,
        layer_start_absolute=[True] * layers,
        layer_start_units=[1.0] * layers,
        layer_start_types=[1] * layers,
        layer_start_e=[0.0] * layers,
        layer_start_e_absolute=[True] * layers,
        layer_start_extruding=[True] * layers,
        current_layer_map={0: 0},
        layer_elapsed_times=[1.0] * layers,
        compact=compact,
        hydrated_layers=set(range(layers)) if compact else set(),
    )
    return index


class LayerPolylinesTests(unittest.TestCase):
    def test_a_row_of_motions_groups_into_the_class_polyline(self):
        index = make_index()
        layer = layer_polylines(index, 0)
        self.assertIsNotNone(layer)
        self.assertIn("WALL-OUTER", layer["classes"])
        segments = layer["classes"]["WALL-OUTER"]
        # The 0.35 mm threshold keeps every mm step (19 segments).
        self.assertEqual(len(segments), 1)
        points = segments[0]
        self.assertEqual(len(points), 20)
        # Each kept point carries its motion index.
        self.assertEqual(points[-1][2], 19)

    def test_dense_motions_decimate_below_the_budget(self):
        index = make_index(motions=300)
        # 0.01 mm steps against the 0.35 mm floor: the filter keeps
        # a sparse polyline, never all 300.
        index.motion_x[0] = array("f", [m * 0.01 for m in range(300)])
        layer = layer_polylines(index, 0)
        segments = layer["classes"]["WALL-OUTER"]
        self.assertLess(sum(len(segment) for segment in segments), 60)

    def test_a_travel_splits_the_class_polyline_into_segments(self):
        index = make_index(motions=20)
        # Motions 10-13 travel: the class polyline must break there
        # and never carry the travel's motions (the live report —
        # the stroke bridged objects with phantom lines).
        index.travel_starts[0] = [10]
        index.travel_ends[0] = [14]
        layer = layer_polylines(index, 0)
        segments = layer["classes"]["WALL-OUTER"]
        self.assertEqual(len(segments), 2)
        first, second = segments
        self.assertEqual(first[-1][2], 9)
        self.assertEqual(second[0][2], 14)
        for segment in segments:
            for point in segment:
                self.assertNotIn(point[2], range(10, 14))
        # The travel channel mirrors the break: one segment per span,
        # never a concatenated polyline bridging between spans.
        travel_segments = layer["travels"]
        self.assertEqual(len(travel_segments), 1)
        self.assertEqual([point[2] for point in travel_segments[0]], [10, 11, 12, 13])

    def test_each_travel_span_gets_its_own_segment(self):
        index = make_index(motions=20)
        index.travel_starts[0] = [3, 12]
        index.travel_ends[0] = [6, 15]
        travel_segments = layer_polylines(index, 0)["travels"]
        self.assertEqual(len(travel_segments), 2)
        self.assertEqual([point[2] for point in travel_segments[0]], [3, 4, 5])
        self.assertEqual([point[2] for point in travel_segments[1]], [12, 13, 14])

    def test_a_stationary_retract_pulse_is_not_a_travel(self):
        # The live file's skin seams: E pauses at every patch boundary
        # while the toolhead stays put. No movement, no travel — and
        # no glyphs (the live report: every skin seam drew a start/
        # stop pair).
        index = make_index(motions=20)
        index.travel_starts[0] = [10]
        index.travel_ends[0] = [14]
        index.motion_x[0] = array("f", [float(m) for m in range(10)] + [10.0] * 10)
        index.motion_y[0] = array("f", [0.0] * 20)
        layer = layer_polylines(index, 0)
        self.assertEqual(layer["travels"], [])
        self.assertEqual(layer["travelStarts"], [])
        self.assertEqual(layer["travelEnds"], [])
        # The pulse IS a seam: the class polyline breaks there too —
        # a stationary retract-prime is a real feature boundary, and
        # merging across it chords the corner where the next line
        # starts (the live report: straight lines read as slight
        # diagonals). The pulse's motions leave the polyline.
        segments = layer["classes"]["WALL-OUTER"]
        self.assertEqual(len(segments), 2)
        for segment in segments:
            for point in segment:
                self.assertNotIn(point[2], range(10, 14))

    def test_a_dense_layer_holds_the_point_budget(self):
        # A dense straight run: the per-class path-length threshold
        # caps the kept points near the budget, so the paint stays
        # bounded even with the much higher fidelity budget.
        count = 250000
        xs = array("f", [step * 0.5 for step in range(count)])
        ys = array("f", [0.0] * count)
        index = make_index(motions=count)
        index.motion_x[0] = xs
        index.motion_y[0] = ys
        layer = layer_polylines(index, 0)
        segments = layer["classes"]["WALL-OUTER"]
        kept = sum(len(segment) for segment in segments)
        # The threshold sits above the fixture's step, so the filter
        # keeps every other point — bounded, never the whole run.
        self.assertLess(kept, 140000)
        self.assertGreater(kept, 100000)

    def test_an_unhydrated_compact_layer_reads_none_never_empty(self):
        index = make_index(compact=True)
        index.hydrated_layers.clear()
        self.assertIsNone(layer_polylines(index, 0))

    def test_a_non_compact_layer_reads_even_without_hydration(self):
        index = make_index(compact=False)
        index.hydrated_layers.clear()
        self.assertIsNotNone(layer_polylines(index, 0))

    def test_out_of_range_layers_read_none(self):
        index = make_index()
        self.assertIsNone(layer_polylines(index, -1))
        self.assertIsNone(layer_polylines(index, 5))


class SplitAndBundleTests(unittest.TestCase):
    def test_the_split_bisects_the_motion_offsets(self):
        index = make_index(motions=20)
        self.assertEqual(split_index(index, 0, 95), 10)

    def test_the_bundle_carries_prev_current_and_next(self):
        index = make_index(layers=3)
        bundle = plate_progress(index, 1, 55)
        self.assertEqual(bundle["anchor"], 1)
        self.assertEqual(bundle["method"], "motion index")
        self.assertEqual(bundle["split"], 6)
        self.assertIsNotNone(bundle["layers"]["prev"])
        self.assertIsNotNone(bundle["layers"]["current"])
        self.assertIsNotNone(bundle["layers"]["next"])

    def test_the_bundle_edges_trim_the_ghosts(self):
        index = make_index(layers=1)
        bundle = plate_progress(index, 0, 10)
        self.assertIsNone(bundle["layers"]["prev"])
        self.assertIsNone(bundle["layers"]["next"])
        self.assertIsNotNone(bundle["layers"]["current"])

    def test_a_missing_anchor_reads_unavailable(self):
        bundle = plate_progress(make_index(), None, None)
        self.assertEqual(bundle["method"], "unavailable")
        self.assertEqual(bundle["layers"], {})

    def test_a_missing_position_reads_unavailable_for_the_split(self):
        bundle = plate_progress(make_index(), 0, None)
        self.assertEqual(bundle["method"], "unavailable")
        self.assertIsNone(bundle["split"])


if __name__ == "__main__":
    unittest.main()
