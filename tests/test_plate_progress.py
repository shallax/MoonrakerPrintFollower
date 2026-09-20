"""The follower payload's prep: the motion-edge geometry, the
per-class grouping, the travel markers, the split count and the honest
unavailable shape.

The end-to-end cases run LITERAL G-code through the real index
(build_index_from_bytes) into layer_polylines and assert the rendered
XY coordinates — the payload's contract is the G-code's own segments,
never a point count or a motion index. The synthetic-index cases stay
for the surfaces a literal file cannot reach cheaply (the point budget,
the eviction states).
"""
from __future__ import annotations

from array import array
import gc
from math import hypot
import time
import unittest

from plugins.GCodeIndex import LayerMotionIndex, build_index_from_bytes
from plugins.PlateProgress import (
    MAX_TRAVEL_POINTS,
    _PREPARED_LIMIT,
    _budgeted,
    _douglas_peucker,
    _prepared_layers,
    _simplify,
    layer_polylines,
    motion_edges,
    plate_layers,
    plate_progress,
    prepare_layer,
    split_index,
)


def _index(gcode: str) -> LayerMotionIndex:
    return build_index_from_bytes(gcode.encode("ascii"))


def _classes(index: LayerMotionIndex, name: str, layer: int = 0):
    return layer_polylines(index, layer)["classes"].get(name, [])


def _xy(points):
    return [[point[0], point[1]] for point in points]


def _indices(points):
    return [int(point[2]) for point in points]


def dropped_within(points, kept):
    """The greatest distance from any dropped vertex to the polyline
    the kept vertices draw — the module's own error bound, measured
    from the outside.

    The walk is linear: the kept vertices partition the chain, so a
    dropped vertex belongs to the interval of the two consecutive kept
    vertices that bracket it, and the FINITE distance to that chord is
    the distance the payload promises. The bracket only moves forward.
    """
    worst = 0.0
    bracket = 0
    for point in points:
        while bracket + 1 < len(kept) and kept[bracket + 1][2] <= point[2]:
            bracket += 1
        if kept[bracket][2] == point[2]:
            continue
        distance = _point_to_segment(point, kept[bracket], kept[bracket + 1])
        if distance > worst:
            worst = distance
    return worst


def _painted(segment, split, start=-1):
    """The painter's clip as the payload defines it: edge i runs from
    vertex i-1 to vertex i, it belongs to the motion vertex i names, and
    the split is a COUNT of printed motions — the edge is drawn exactly
    when its own motion is below it. Delta paints start at *start*."""
    edges = []
    for i in range(1, len(segment)):
        motion = segment[i][2]
        if motion < start:
            continue
        if split >= 0 and motion >= split:
            break
        edges.append((tuple(segment[i - 1][:2]), tuple(segment[i][:2])))
    return edges


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


class EdgeGeometryTests(unittest.TestCase):
    """Literal G-code in, the G-code's own XY segments out. A motion's
    drawable primitive is start_of_motion -> end_of_motion: the
    endpoint-only reading lost the first edge of every run (the live
    report's straight-then-diagonal lines arriving as one diagonal)."""

    def assertChain(self, segment, expected):
        """*segment* draws *expected*, one [x, y] per vertex, in order."""
        self.assertEqual(len(segment), len(expected),
                         "the segment draws %s, not %s" % (_xy(segment), expected))
        for point, (x, y) in zip(segment, expected, strict=True):
            self.assertAlmostEqual(point[0], x, places=5)
            self.assertAlmostEqual(point[1], y, places=5)

    def test_the_critical_repro_draws_its_first_horizontal_edge(self):
        # The reported repro, verbatim: the extrusion used to start at
        # (20, 10) — the first horizontal edge was missing entirely.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            "G0 X10 Y10\n"
            "G1 X20 Y10 E1\n"
            "G1 X20 Y20 E2\n")
        segments = _classes(index, "unknown")
        self.assertEqual(len(segments), 1)
        self.assertChain(segments[0], [[10.0, 10.0], [20.0, 10.0], [20.0, 20.0]])
        # The vertices carry the edges' motions: the first horizontal
        # edge belongs to motion 1, the vertical to motion 2, so the
        # split clip draws the horizontal as soon as motion 1 is passed.
        self.assertEqual(_indices(segments[0]), [1, 1, 2])

    def test_the_first_travel_keeps_its_true_start_position(self):
        # The same repro's travel channel: the G0 runs from the layer's
        # start position to (10, 10) — both the segment and its glyphs
        # used to start at the travel's own endpoint.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            "G0 X10 Y10\n"
            "G1 X20 Y10 E1\n"
            "G1 X20 Y20 E2\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(len(layer["travels"]), 1)
        self.assertChain(layer["travels"][0], [[0.0, 0.0], [10.0, 10.0]])
        self.assertEqual(_indices(layer["travels"][0]), [0, 0])
        self.assertChain(layer["travelStarts"], [[0.0, 0.0]])
        self.assertChain(layer["travelEnds"], [[10.0, 10.0]])
        self.assertEqual(_indices(layer["travelStarts"]), [0])
        self.assertEqual(_indices(layer["travelEnds"]), [0])

    def test_a_one_motion_extrusion_draws_its_whole_line(self):
        # A single extrusion move IS a line segment: it used to reach
        # the painter as one point and draw as a dot.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n")
        segments = _classes(index, "SKIN")
        self.assertEqual(len(segments), 1)
        self.assertChain(segments[0], [[0.0, 0.0], [10.0, 0.0]])
        # The layer-start edge carries index 0: it is drawn from the
        # first motion on, and it never paints before that.
        self.assertEqual(_indices(segments[0]), [0, 0])

    def test_a_one_motion_extrusion_after_a_travel_keeps_its_start(self):
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X5 Y5\n"
            "G1 X5 Y10 E1\n")
        segments = _classes(index, "SKIN")
        self.assertEqual(len(segments), 1)
        self.assertChain(segments[0], [[5.0, 5.0], [5.0, 10.0]])
        self.assertEqual(_indices(segments[0]), [1, 1])

    def test_parallel_skin_lines_stay_parallel_with_a_retract_between_them(self):
        # Three diagonals of the same pitch, each separated by a
        # retract, a travel and a prime: every run draws its own start
        # position, so the lines stay parallel instead of fanning.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X1 Y1\n"
            "G1 X6 Y6 E1\n"
            "G1 E0\n"
            "G0 X3 Y4\n"
            "G1 E1\n"
            "G1 X8 Y9 E2\n"
            "G1 E0\n"
            "G0 X5 Y7\n"
            "G1 E1\n"
            "G1 X10 Y12 E3\n")
        segments = _classes(index, "SKIN")
        self.assertEqual(len(segments), 3)
        self.assertChain(segments[0], [[1.0, 1.0], [6.0, 6.0]])
        self.assertChain(segments[1], [[3.0, 4.0], [8.0, 9.0]])
        self.assertChain(segments[2], [[5.0, 7.0], [10.0, 12.0]])
        # The pure-E prime opens its run without a vertex of its own:
        # the run's start vertex is the first edge's start, owned by the
        # motion that draws that edge.
        self.assertEqual(_indices(segments[0]), [1, 1])
        self.assertEqual(_indices(segments[1]), [4, 5])
        self.assertEqual(_indices(segments[2]), [8, 9])
        # Every drawn edge is the same direction: the skin lines are
        # parallel, and none of them absorbed its neighbour's offset.
        self.assertEqual(self.directions(segments), {(5.0, 5.0)})

    def test_parallel_skin_lines_stay_parallel_without_any_retraction(self):
        # The same hatch with no retraction at all: E holds flat across
        # the travel, the travel is real movement all the same, and the
        # lines must stay as parallel as the retracted version.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X1 Y1\n"
            "G1 X6 Y6 E1\n"
            "G0 X3 Y4\n"
            "G1 X8 Y9 E2\n"
            "G0 X5 Y7\n"
            "G1 X10 Y12 E3\n")
        layer = layer_polylines(index, 0)
        segments = layer["classes"]["SKIN"]
        self.assertEqual(len(segments), 3)
        self.assertChain(segments[0], [[1.0, 1.0], [6.0, 6.0]])
        self.assertChain(segments[1], [[3.0, 4.0], [8.0, 9.0]])
        self.assertChain(segments[2], [[5.0, 7.0], [10.0, 12.0]])
        self.assertEqual(self.directions(segments), {(5.0, 5.0)})
        # The un-retracted travel keeps its own first edge too: it runs
        # from where extrusion stopped to where it resumed. (The leading
        # G0 is a third span, from the layer's start position.)
        self.assertChain(layer["travels"][1], [[6.0, 6.0], [3.0, 4.0]])
        self.assertChain(layer["travels"][2], [[8.0, 9.0], [5.0, 7.0]])
        self.assertEqual(layer["travels"][1][0][2], 2)

    def directions(self, segments):
        found = set()
        for segment in segments:
            for i in range(len(segment) - 1):
                edge = (round(segment[i + 1][0] - segment[i][0], 5),
                        round(segment[i + 1][1] - segment[i][1], 5))
                if edge != (0.0, 0.0):
                    found.add(edge)
        return found

    def test_a_short_horizontal_segment_keeps_its_corner(self):
        # A 0.2 mm horizontal move then a diagonal: the distance filter
        # erased the short edge and drew one diagonal — the corner must
        # survive exactly as the G-code wrote it.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X10 Y10\n"
            "G1 X10.2 Y10 E1\n"
            "G1 X15 Y15 E2\n")
        segments = _classes(index, "SKIN")
        self.assertEqual(len(segments), 1)
        self.assertChain(segments[0], [[10.0, 10.0], [10.2, 10.0], [15.0, 15.0]])
        self.assertEqual(_indices(segments[0]), [1, 1, 2])
        # The short edge is exactly horizontal: no fan, no slope leak.
        self.assertEqual(segments[0][1][1], segments[0][0][1])
        self.assertAlmostEqual(segments[0][1][0] - segments[0][0][0], 0.2, places=5)

    def test_a_feature_change_splits_the_run_without_a_travel(self):
        # WALL -> SKIN -> WALL with no travel: two independent WALL
        # runs, never one run chording back across the SKIN section.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:WALL-OUTER\n"
            "G1 X10 Y0 E1\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y10 E2\n"
            ";TYPE:WALL-OUTER\n"
            "G1 X0 Y10 E3\n")
        walls = _classes(index, "WALL-OUTER")
        skin = _classes(index, "SKIN")
        self.assertEqual(len(walls), 2)
        self.assertChain(walls[0], [[0.0, 0.0], [10.0, 0.0]])
        self.assertChain(walls[1], [[10.0, 10.0], [0.0, 10.0]])
        self.assertChain(skin[0], [[10.0, 0.0], [10.0, 10.0]])
        self.assertEqual(_indices(walls[1]), [2, 2])
        # Nothing joins the two WALL runs: the return leg is its own
        # segment from the SKIN's end, not a chord back to the first.
        self.assertEqual(_xy(walls[0][-1:]), [[10.0, 0.0]])
        self.assertEqual(_xy(walls[1][:1]), [[10.0, 10.0]])

    def test_a_feature_change_away_and_back_keeps_both_runs_apart(self):
        # A -> B -> A along the same line: the second A run must not
        # reconnect to the first across the B excursion.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:WALL-OUTER\n"
            "G1 X10 Y0 E1\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y5 E2\n"
            ";TYPE:WALL-OUTER\n"
            "G1 X20 Y0 E3\n")
        walls = _classes(index, "WALL-OUTER")
        self.assertEqual(len(walls), 2)
        self.assertChain(walls[0], [[0.0, 0.0], [10.0, 0.0]])
        self.assertChain(walls[1], [[10.0, 5.0], [20.0, 0.0]])
        self.assertEqual(_classes(index, "SKIN")[0][-1][:2], [10.0, 5.0])

    def test_a_moving_travel_with_a_retract_and_prime_draws_a_to_b(self):
        # The reviewer's travel case: extrusion stops at A, a pure-E
        # retract holds at A, the XY travel goes A -> B, a pure-E prime
        # holds at B, and extrusion resumes there.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G1 E0\n"
            "G0 X20 Y5\n"
            "G1 E1\n"
            "G1 X30 Y5 E2\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(len(layer["travels"]), 1)
        self.assertChain(layer["travels"][0], [[10.0, 0.0], [20.0, 5.0]])
        self.assertChain(layer["travelStarts"], [[10.0, 0.0]])
        self.assertChain(layer["travelEnds"], [[20.0, 5.0]])
        # The two pure-E moves are state changes: no vertex, no edge, no
        # glyph — and the travel's own edges carry its real motions.
        self.assertEqual(_indices(layer["travels"][0]), [1, 2])
        segments = layer["classes"]["SKIN"]
        self.assertEqual(len(segments), 2)
        self.assertChain(segments[0], [[0.0, 0.0], [10.0, 0.0]])
        self.assertChain(segments[1], [[20.0, 5.0], [30.0, 5.0]])

    def test_a_short_real_travel_below_the_old_floor_stays_a_travel(self):
        # 0.3 mm: below the removed 0.5 mm heuristic, and still a real
        # repositioning — the span, its edges and its glyphs all stay.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G0 X10.3 Y0\n"
            "G1 X20 Y0 E2\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(len(layer["travels"]), 1)
        self.assertChain(layer["travels"][0], [[10.0, 0.0], [10.3, 0.0]])
        self.assertChain(layer["travelStarts"], [[10.0, 0.0]])
        self.assertChain(layer["travelEnds"], [[10.3, 0.0]])
        self.assertChain(layer["classes"]["SKIN"][1], [[10.3, 0.0], [20.0, 0.0]])

    def test_a_pure_e_seam_is_no_travel_and_no_geometry(self):
        # E falls and rises with the toolhead standing still: no travel
        # (the live file's skin seams), no line, no dot — and the
        # extrusion either side still meets exactly.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G1 E0.5\n"
            "G1 E1.5\n"
            "G1 X20 Y0 E2\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(layer["travels"], [])
        self.assertEqual(layer["travelStarts"], [])
        self.assertEqual(layer["travelEnds"], [])
        segments = layer["classes"]["SKIN"]
        self.assertEqual(len(segments), 2)
        self.assertChain(segments[0], [[0.0, 0.0], [10.0, 0.0]])
        # The prime opens the second run where the retract stopped it:
        # its own zero-XY edge contributes the run's start vertex only.
        self.assertChain(segments[1], [[10.0, 0.0], [20.0, 0.0]])
        self.assertEqual(_indices(segments[1]), [2, 3])
        self.assertEqual(segments[0][-1][:2], segments[1][0][:2])
        self.assertEqual({point[1] for point in segments[0] + segments[1]}, {0.0})

    def test_both_travel_forms_of_the_hatch_draw_the_same_geometry(self):
        # The live orange-skin repro pair: the same three diagonals with
        # and without a retraction must reach the painter identically.
        retracted = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X1 Y1\n"
            "G1 X6 Y6 E1\n"
            "G1 E0\n"
            "G0 X3 Y4\n"
            "G1 E1\n"
            "G1 X8 Y9 E2\n")
        plain = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X1 Y1\n"
            "G1 X6 Y6 E1\n"
            "G0 X3 Y4\n"
            "G1 X8 Y9 E2\n")
        self.assertEqual(_xy(_classes(retracted, "SKIN")[0]),
                         _xy(_classes(plain, "SKIN")[0]))
        self.assertEqual(_xy(_classes(retracted, "SKIN")[1]),
                         _xy(_classes(plain, "SKIN")[1]))
        self.assertEqual(self.directions(_classes(plain, "SKIN")), {(5.0, 5.0)})

    def test_multiple_travel_spans_keep_each_true_start(self):
        # Two spans whose FIRST move carries the repositioning: the
        # travel edge and its start glyph begin where extrusion stopped.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G1 X20 Y0 E0.5\n"
            "G1 X30 Y0 E1.5\n"
            "G1 X40 Y5 E1.0\n"
            "G1 X50 Y5 E2.0\n")
        layer = layer_polylines(index, 0)
        travels = layer["travels"]
        self.assertEqual(len(travels), 2)
        self.assertChain(travels[0], [[10.0, 0.0], [20.0, 0.0]])
        self.assertChain(travels[1], [[30.0, 0.0], [40.0, 5.0]])
        self.assertEqual(_indices(travels[0]), [1, 1])
        self.assertEqual(_indices(travels[1]), [3, 3])
        self.assertChain(layer["travelStarts"], [[10.0, 0.0], [30.0, 0.0]])
        self.assertChain(layer["travelEnds"], [[20.0, 0.0], [40.0, 5.0]])
        # The extrusions either side resume from the travel's own end.
        segments = layer["classes"]["SKIN"]
        self.assertEqual(len(segments), 3)
        self.assertChain(segments[0], [[0.0, 0.0], [10.0, 0.0]])
        self.assertChain(segments[1], [[20.0, 0.0], [30.0, 0.0]])
        self.assertChain(segments[2], [[40.0, 5.0], [50.0, 5.0]])

    def test_a_layer_opens_at_its_own_start_position(self):
        # The second layer's first extrusion starts where the head stood
        # at the layer marker, not at the origin.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G1 X10 Y10 E2\n"
            ";LAYER:1\n"
            "G1 X20 Y10 E3\n")
        self.assertChain(_classes(index, "SKIN", 1)[0],
                         [[10.0, 10.0], [20.0, 10.0]])
        self.assertChain(_classes(index, "SKIN", 0)[0],
                         [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0]])

    def test_a_travel_across_a_layer_boundary_draws_honest_halves(self):
        # Extrusion stops at (10, 0) in layer 0, the travel carries on
        # into layer 1 where E resumes: layer 0 holds the span and the
        # start glyph, layer 1 resumes from the boundary position and
        # invents neither a travel nor a glyph.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G1 X20 Y0 E0.5\n"
            ";LAYER:1\n"
            "G1 X30 Y0 E1.5\n"
            "G1 X40 Y0 E2\n")
        first = layer_polylines(index, 0)
        self.assertChain(first["classes"]["SKIN"][0], [[0.0, 0.0], [10.0, 0.0]])
        self.assertChain(first["travels"][0], [[10.0, 0.0], [20.0, 0.0]])
        self.assertChain(first["travelStarts"], [[10.0, 0.0]])
        # The travel ends in the next layer's file, so this layer has no
        # end glyph for it.
        self.assertEqual(first["travelEnds"], [])
        second = layer_polylines(index, 1)
        self.assertEqual(second["travels"], [])
        self.assertEqual(second["travelStarts"], [])
        self.assertEqual(second["travelEnds"], [])
        # The resuming move is an extruding one: its own whole edge is
        # drawn, from the boundary position onward.
        self.assertChain(second["classes"]["SKIN"][0],
                         [[20.0, 0.0], [30.0, 0.0], [40.0, 0.0]])

    def test_relative_and_absolute_e_draw_the_same_geometry(self):
        # The E rule is modal: the same moves written relatively and
        # absolutely must produce one payload.
        absolute = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X1 Y1\n"
            "G1 X6 Y6 E1\n"
            "G1 E0\n"
            "G0 X3 Y4\n"
            "G1 E1\n"
            "G1 X8 Y9 E2\n")
        relative = _index(
            "M83\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G0 X1 Y1\n"
            "G1 X6 Y6 E1\n"
            "G1 E-1\n"
            "G0 X3 Y4\n"
            "G1 E1\n"
            "G1 X8 Y9 E1\n")
        self.assertEqual(layer_polylines(relative, 0), layer_polylines(absolute, 0))

    def test_a_g92_rebase_rebases_the_frame_and_keeps_the_e_rule(self):
        # G92 rebases the coordinates the file speaks in: the E baseline
        # moves with it (no phantom retraction, no invented travel), and
        # the following motions are drawn in the rebased frame — the
        # frame the rest of the file speaks. KNOWN LIMITATION: the index
        # stores one position per motion in the file's own frame, so an
        # edge that spans the rebase joins the two frames. Sliced output
        # rebases E alone (geometry-free); this pins the file-frame rule
        # so a rebase-aware renderer is a deliberate change.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G0 X20 Y0\n"
            "G92 X0 Y0 E0\n"
            "G1 X5 Y0 E1\n")
        layer = layer_polylines(index, 0)
        self.assertChain(layer["travels"][0], [[10.0, 0.0], [20.0, 0.0]])
        self.assertChain(layer["travelStarts"], [[10.0, 0.0]])
        self.assertChain(layer["travelEnds"], [[20.0, 0.0]])
        segments = layer["classes"]["SKIN"]
        self.assertChain(segments[0], [[0.0, 0.0], [10.0, 0.0]])
        self.assertChain(segments[1], [[20.0, 0.0], [5.0, 0.0]])

    def test_feature_markers_never_move_a_coordinate(self):
        # The colouring partitions the motions; it must not alter one
        # vertex. The same moves under different TYPE markers draw the
        # same coordinates, and an unmarked file draws them too.
        body = "G1 X10 Y0 E1\nG1 X10 Y10 E2\nG1 X0 Y10 E3\n"
        outer = _index("M82\n;LAYER:0\n;TYPE:WALL-OUTER\n" + body)
        inner = _index("M82\n;LAYER:0\n;TYPE:WALL-INNER\n" + body)
        plain = _index("M82\n;LAYER:0\n" + body)
        self.assertEqual(_xy(_classes(outer, "WALL-OUTER")[0]),
                         _xy(_classes(inner, "WALL-INNER")[0]))
        self.assertEqual(_xy(_classes(plain, "unknown")[0]),
                         _xy(_classes(outer, "WALL-OUTER")[0]))
        # The partition moved, the coordinates did not: the other class
        # is empty in each file.
        self.assertEqual(_classes(outer, "WALL-INNER"), [])
        self.assertEqual(_classes(inner, "WALL-OUTER"), [])

    def test_an_arc_move_is_drawn_as_the_path_it_commanded(self):
        # A G2 is one motion but not one line: the payload carries the
        # circular path the head took, as subedges that all belong to
        # that same motion. Here the move runs clockwise from (10,0)
        # about (0,0), so it sweeps the long way round through negative
        # Y rather than cutting the chord across the quadrant.
        index = _index(
            "M82\n"
            ";LAYER:0\n"
            ";TYPE:SKIN\n"
            "G1 X10 Y0 E1\n"
            "G2 X0 Y10 I-10 J0 E2\n")
        segments = _classes(index, "SKIN")
        self.assertEqual(len(segments), 1)
        chain = segments[0]
        self.assertEqual([chain[0][0], chain[0][1]], [0.0, 0.0])
        self.assertEqual([chain[-1][0], chain[-1][1]], [0.0, 10.0])
        self.assertGreater(len(chain), 4, "the arc arrived as its chord")
        # The chain opens where the run did — the head's position before
        # the arc — so the arc's own vertices are the ones it owns.
        arc_vertices = [vertex for vertex in chain if vertex[2] == 1.0]
        self.assertEqual({vertex[2] for vertex in chain if vertex[2] > 0.0}, {1.0})
        self.assertEqual([chain[0][0], chain[0][1]], [0.0, 0.0],
                         "the run must still open at the head's position")
        for vertex in arc_vertices:
            self.assertAlmostEqual(hypot(vertex[0], vertex[1]), 10.0, places=4,
                                   msg="a vertex left the commanded circle")
        self.assertTrue(any(vertex[1] < -9.0 for vertex in arc_vertices),
                        "the clockwise sweep cut the short way round")


class SplitCountTests(unittest.TestCase):
    """The split is ONE number with ONE meaning: a count of printed
    motions. Edge m is drawn exactly when m < split, the full repaint
    and the delta obey the same rule, and a restart (a backwards split)
    resets to nothing rather than repainting forwards."""

    GCODE = ("M82\n"
             ";LAYER:0\n"
             ";TYPE:SKIN\n"
             "G1 X10 Y0 E1\n"
             "G1 X10 Y10 E2\n"
             "G1 X0 Y10 E3\n")

    def test_the_split_counts_the_motions_the_position_has_passed(self):
        index = _index(self.GCODE)
        offsets = list(index.motion_offsets[0])
        self.assertEqual(len(offsets), 3)
        # A position before the layer's first motion: nothing printed.
        self.assertEqual(split_index(index, 0, 0), 0)
        self.assertEqual(split_index(index, 0, offsets[0]), 0)
        # Inside motion k's own line: motions 0 .. k-1 are done.
        self.assertEqual(split_index(index, 0, offsets[0] + 1), 1)
        self.assertEqual(split_index(index, 0, offsets[1]), 1)
        self.assertEqual(split_index(index, 0, offsets[1] + 1), 2)
        self.assertEqual(split_index(index, 0, offsets[2]), 2)
        # Past them all: the whole layer.
        self.assertEqual(split_index(index, 0, offsets[2] + 1), 3)
        self.assertEqual(split_index(index, 0, 10 ** 9), 3)

    def test_split_zero_one_and_n_clip_exactly_the_printed_edges(self):
        index = _index(self.GCODE)
        segment = _classes(index, "SKIN")[0]
        self.assertEqual(_painted(segment, 0), [])
        self.assertEqual(_painted(segment, 1), [((0.0, 0.0), (10.0, 0.0))])
        self.assertEqual(_painted(segment, 2),
                         [((0.0, 0.0), (10.0, 0.0)), ((10.0, 0.0), (10.0, 10.0))])
        self.assertEqual(len(_painted(segment, 3)), 3)
        # The next unexecuted edge is never painted early: at split 1
        # the vertical's motion index (1) is not below the count.
        self.assertNotIn(((10.0, 0.0), (10.0, 10.0)), _painted(segment, 1))

    def test_a_one_step_delta_paints_only_the_new_edge(self):
        index = _index(self.GCODE)
        segment = _classes(index, "SKIN")[0]
        self.assertEqual(_painted(segment, 1, 0), [((0.0, 0.0), (10.0, 0.0))])
        self.assertEqual(_painted(segment, 2, 1), [((10.0, 0.0), (10.0, 10.0))])
        self.assertEqual(_painted(segment, 3, 2), [((10.0, 10.0), (0.0, 10.0))])
        self.assertEqual(_painted(segment, 2, 2), [])

    def test_the_accumulated_deltas_paint_what_a_full_repaint_paints(self):
        index = _index(self.GCODE)
        for segment in _classes(index, "SKIN"):
            for split in range(0, 5):
                full = _painted(segment, split)
                accumulated = []
                for boundary in range(0, split + 1):
                    accumulated.extend(_painted(segment, boundary, boundary - 1))
                self.assertEqual(accumulated, full)

    def test_a_backwards_split_paints_nothing_and_a_forward_one_resumes(self):
        # A restart rewinds the count: the painter's rule holds at 0
        # (nothing), and the forward count paints again from the start —
        # it never fills in the edges it skipped on the way down.
        index = _index(self.GCODE)
        segment = _classes(index, "SKIN")[0]
        self.assertEqual(_painted(segment, 3), _painted(segment, -1))
        self.assertEqual(_painted(segment, 0), [])
        self.assertEqual(_painted(segment, 1), [((0.0, 0.0), (10.0, 0.0))])

    def test_a_layer_without_motions_reads_no_split(self):
        index = LayerMotionIndex(ranges=[(0, 10)], motion_offsets=[array("Q")])
        self.assertIsNone(split_index(index, 0, 5))


class SyntheticIndexTests(unittest.TestCase):
    """The surfaces a literal file cannot reach cheaply: the budget's
    simplification and the evicted/unhydrated states."""

    def test_a_row_of_motions_draws_its_edges(self):
        index = make_index()
        layer = layer_polylines(index, 0)
        self.assertIn("WALL-OUTER", layer["classes"])
        segments = layer["classes"]["WALL-OUTER"]
        self.assertEqual(len(segments), 1)
        points = segments[0]
        # One vertex per motion that actually moved: motion 0's edge is
        # the zero-length step from the layer start, so the chain opens
        # at its start vertex and adds one per later motion.
        self.assertEqual(len(points), 20)
        self.assertEqual(points[0][:2], [0.0, 0.0])
        self.assertEqual(points[1][:2], [1.0, 0.0])
        self.assertEqual(points[-1][:2], [19.0, 0.0])
        self.assertEqual([int(point[2]) for point in points[:2]], [0, 1])
        self.assertEqual(int(points[-1][2]), 19)
        # Every drawn edge is a real motion edge, one per motion.
        self.assertEqual(_painted(points, 20),
                         [((float(m - 1), 0.0), (float(m), 0.0)) for m in range(1, 20)])

    def test_a_class_below_the_budget_keeps_every_vertex(self):
        # 0.01 mm steps against the old 0.35 mm floor: the floor's
        # distance filter used to delete 97% of a real class's
        # vertices. Below the budget the G-code's own vertices stay.
        index = make_index(motions=300)
        index.motion_x[0] = array("f", [m * 0.01 for m in range(300)])
        segments = layer_polylines(index, 0)["classes"]["WALL-OUTER"]
        kept = sum(len(segment) for segment in segments)
        self.assertEqual(kept, 300)
        self.assertAlmostEqual(segments[0][-1][0], 2.99, places=5)

    def test_a_class_above_the_budget_simplifies_without_moving_its_corners(self):
        # Half a million points over an L: the class is above the
        # budget, so each already-separated segment simplifies with
        # Douglas-Peucker — endpoints kept, the corner kept, and every
        # surviving vertex one of the G-code's own.
        count = 250000
        half = count // 2
        index = make_index(motions=count)
        index.motion_x[0] = array(
            "f", [step * 0.01 for step in range(half)]
            + [half * 0.01] * (count - half))
        index.motion_y[0] = array(
            "f", [0.0] * half
            + [step * 0.01 for step in range(count - half)])
        segments = layer_polylines(index, 0)["classes"]["WALL-OUTER"]
        kept = sum(len(segment) for segment in segments)
        self.assertLess(kept, 200000)
        self.assertEqual(len(segments), 1)
        points = segments[0]
        self.assertEqual(points[0][:2], [0.0, 0.0])
        # The corner: the last horizontal point, then the vertical run.
        self.assertEqual([point[:2] for point in points].count([1250.0, 0.0]), 1)
        self.assertAlmostEqual(points[-1][0], 1250.0, places=5)
        self.assertAlmostEqual(points[-1][1], (count - half - 1) * 0.01, places=4)
        # Douglas-Peucker returns a subset of the input vertices, so
        # the motion indices ride their own vertices.
        self.assertEqual(int(points[0][2]), 0)
        self.assertEqual(int(points[-1][2]), count - 1)

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
        # The closing run ends at its true end (motion 9's endpoint),
        # the next opens at the position extrusion resumed from.
        self.assertEqual(first[-1][:2], [9.0, 0.0])
        self.assertEqual(int(first[-1][2]), 9)
        self.assertEqual(second[0][:2], [13.0, 0.0])
        self.assertEqual(int(second[0][2]), 14)
        for segment in segments:
            for point in segment:
                self.assertNotIn(point[2], range(10, 14))
        # The travel channel mirrors the break: one segment per span,
        # never a concatenated polyline bridging between spans.
        travel_segments = layer["travels"]
        self.assertEqual(len(travel_segments), 1)
        # The span's first vertex is its first edge's start, owned by
        # that edge's own motion.
        self.assertEqual([point[2] for point in travel_segments[0]], [10, 10, 11, 12, 13])

    def test_each_travel_span_gets_its_own_segment(self):
        index = make_index(motions=20)
        index.travel_starts[0] = [3, 12]
        index.travel_ends[0] = [6, 15]
        travel_segments = layer_polylines(index, 0)["travels"]
        self.assertEqual(len(travel_segments), 2)
        self.assertEqual([point[2] for point in travel_segments[0]], [3, 3, 4, 5])
        self.assertEqual([point[2] for point in travel_segments[1]], [12, 12, 13, 14])

    def test_a_stationary_retract_pulse_is_not_a_travel(self):
        # The live file's skin seams: E pauses at every patch boundary
        # while the toolhead stays put. No movement, no travel — and
        # no glyphs (the live report: every skin seam drew a start/
        # stop pair).
        index = make_index(motions=20)
        index.travel_starts[0] = [10]
        index.travel_ends[0] = [14]
        index.motion_x[0] = array("f", [float(m) for m in range(10)] + [9.0] * 10)
        index.motion_y[0] = array("f", [0.0] * 20)
        layer = layer_polylines(index, 0)
        self.assertEqual(layer["travels"], [])
        self.assertEqual(layer["travelStarts"], [])
        self.assertEqual(layer["travelEnds"], [])
        # The pulse IS a seam: the class polyline breaks there too —
        # a stationary retract-prime is a real feature boundary, and
        # merging across it chords the corner where the next line
        # starts (the live report: straight lines read as slight
        # diagonals). The pulse's motions leave the polyline, and the
        # stationary tail it opens carries no geometry at all.
        segments = layer["classes"]["WALL-OUTER"]
        self.assertEqual(len(segments), 1)
        self.assertAlmostEqual(segments[0][-1][0], 9.0, places=5)
        for segment in segments:
            for point in segment:
                self.assertNotIn(point[2], range(10, 14))

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

    def test_a_run_code_outside_the_vocabulary_reads_other(self):
        # The RLE's overflow code: the run is real, its name is not in
        # the vocabulary, and the class is still one the painter draws.
        index = make_index(motions=4)
        index.motion_types[0] = [[4, 1]]
        self.assertEqual(list(layer_polylines(index, 0)["classes"]), ["other"])

    def test_a_layer_without_a_recorded_start_opens_at_its_first_endpoint(self):
        # A hand-built or partly-read index: with no recorded start the
        # run opens at the first motion's own endpoint, so the first
        # edge is the zero-length step it actually was and no earlier
        # position is invented.
        index = make_index(motions=3)
        index.motion_x[0] = array("f", [5.0, 6.0, 7.0])
        index.motion_y[0] = array("f", [1.0, 1.0, 1.0])
        index.layer_start_positions = []
        edges = list(motion_edges(index, 0))
        self.assertEqual(edges[0][1:5], (5.0, 1.0, 5.0, 1.0))
        self.assertEqual(edges[1][1:5], (5.0, 1.0, 6.0, 1.0))
        self.assertEqual(_classes(index, "WALL-OUTER")[0][0][:2], [5.0, 1.0])

    def test_an_arc_at_a_layer_without_a_recorded_start_keeps_its_helix(self):
        # The arc maths needs the layer's opening Z. A recorded start
        # that carries no Z falls back to the layer's first Z, so the
        # helix is still walked and the layer after it still draws.
        index = make_index(motions=3)
        index.motion_x[0] = array("f", [0.0, 1.0, 2.0])
        index.motion_y[0] = array("f", [5.0, 5.0, 5.0])
        index.motion_z[0] = array("f", [0.4, 0.4, 0.4])
        index.layer_start_positions = [(0.0, 0.0)]
        index.motion_arcs = [{0: (17, True, -1.0, 0.0)}]
        edges = list(motion_edges(index, 0))
        self.assertEqual({edge[0] for edge in edges}, {0, 1, 2})
        self.assertGreater(len([edge for edge in edges if edge[0] == 0]), 1)
        self.assertEqual(edges[-1][0], 2)
        self.assertEqual(edges[-1][3:5], (2.0, 5.0))

    def test_a_layer_with_no_motions_reads_an_empty_payload(self):
        # A hydrated layer the file gave no motions: the payload says
        # empty with zero motions, never None — the painter tells
        # "nothing here" apart from "not loaded".
        self.assertEqual(layer_polylines(make_index(motions=0), 0),
                         {"classes": {}, "travels": [], "travelStarts": [],
                          "travelEnds": [], "motions": 0})


class CompactCodecTests(unittest.TestCase):
    """The full cache's binary form: the round trip preserves every
    channel the painter reads."""

    PAYLOAD = {
        "classes": {
            "WALL-OUTER": [[[0.0, 0.0, 0.0], [10.0, 0.0, 1.0], [10.0, 10.0, 2.0]],
                           [[10.0, 10.0, 2.0], [0.0, 10.0, 3.0]]],
            "SKIN": [],
        },
        "travels": [[[5.0, 5.0, 4.0], [6.0, 6.0, 4.0]]],
        "travelStarts": [[5.0, 5.0, 4.0]],
        "travelEnds": [[6.0, 6.0, 4.0]],
        "motions": 5,
    }

    def test_the_round_trip_preserves_every_channel(self):
        from plugins.PlateProgress import decode_layer, encode_layer
        raw = encode_layer(self.PAYLOAD)
        self.assertIsInstance(raw, bytes)
        self.assertEqual(decode_layer(raw), self.PAYLOAD)
        # The compact form is the point count times three f32s, plus
        # the headers: 14 points * 12 bytes.
        self.assertLessEqual(len(raw), 14 * 12 + 256)

    def test_an_empty_payload_round_trips(self):
        from plugins.PlateProgress import decode_layer, encode_layer
        empty = {"classes": {}, "travels": [], "travelStarts": [],
                 "travelEnds": [], "motions": 0}
        self.assertEqual(decode_layer(encode_layer(empty)), empty)

    def test_garbage_decodes_to_the_empty_payload(self):
        from plugins.PlateProgress import decode_layer
        self.assertEqual(decode_layer(b"not a payload"), {})
        self.assertEqual(decode_layer(None), {})


class SimplificationErrorTests(unittest.TestCase):
    """The distance a kept-away vertex is measured by: the FINITE
    candidate segment the simplified polyline would draw, never the
    infinite line through its ends. The line metric calls a vertex that
    doubles back on itself zero-distance from a shortcut it would
    invent."""

    def test_a_collinear_overshoot_survives_the_simplification(self):
        # (0,0) -> (10,0) -> (1,0): the middle vertex sits ON the
        # infinite line through the ends but 9 mm from the segment a
        # two-point simplification would draw, so the out-and-back is
        # geometry the tolerance must keep.
        points = [[0.0, 0.0, 0.0], [10.0, 0.0, 1.0], [1.0, 0.0, 2.0]]
        self.assertEqual(_douglas_peucker(points, 0.03), points)

    def test_an_overshoot_is_not_budgeted_away(self):
        # The budget cannot be met without drawing that false shortcut,
        # so fidelity wins and the channel stays above the budget.
        points = [[0.0, 0.0, 0.0], [10.0, 0.0, 1.0], [1.0, 0.0, 2.0]]
        self.assertEqual(_budgeted([list(points)], 2), [points])

    def test_a_collinear_run_forward_collapses_to_its_ends(self):
        points = [[0.0, 0.0, 0.0], [1.0, 0.0, 1.0], [2.0, 0.0, 2.0]]
        self.assertEqual(_douglas_peucker(points, 0.03),
                         [[0.0, 0.0, 0.0], [2.0, 0.0, 2.0]])

    def test_an_exact_reversal_keeps_its_turning_point(self):
        points = [[0.0, 0.0, 0.0], [10.0, 0.0, 1.0], [0.0, 0.0, 2.0]]
        self.assertEqual(_douglas_peucker(points, 0.03), points)

    def test_a_nearly_collinear_overshoot_keeps_its_turning_point(self):
        # One hundredth of a millimetre off the candidate's line: the
        # line metric deletes the overshoot, the segment metric keeps it.
        points = [[0.0, 0.0, 0.0], [10.0, 0.01, 1.0], [1.0, 0.02, 2.0]]
        self.assertEqual(_douglas_peucker(points, 0.03), points)

    def test_a_zero_length_candidate_span_measures_from_its_point(self):
        # The candidate's two ends coincide, so there is no direction to
        # project onto and the distance is the plain point distance.
        points = [[5.0, 5.0, 0.0], [9.0, 5.0, 1.0], [5.0, 5.0, 2.0]]
        self.assertEqual(_douglas_peucker(points, 0.03), points)

    def test_a_kept_vertex_is_never_further_from_the_drawn_polyline(self):
        # The stated bound, measured: every vertex the simplification
        # dropped sits within the tolerance of the polyline it kept.
        points = [[step * 0.5, (step % 3) * 0.02, float(step)] for step in range(60)]
        tolerance = 0.05
        kept = _douglas_peucker(points, tolerance)
        for point in points:
            best = min(_point_to_segment(point, kept[i], kept[i + 1])
                       for i in range(len(kept) - 1))
            self.assertLessEqual(best, tolerance + 1e-9,
                                 "a dropped vertex sits %s mm from the drawn polyline" % best)


def _point_to_segment(point, first, second):
    """The distance from *point* to the finite segment *first*->*second*."""
    dx = second[0] - first[0]
    dy = second[1] - first[1]
    span_sq = dx * dx + dy * dy
    if span_sq <= 0.0:
        return hypot(point[0] - first[0], point[1] - first[1])
    t = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / span_sq
    t = max(0.0, min(1.0, t))
    return hypot(point[0] - (first[0] + t * dx), point[1] - (first[1] + t * dy))


class MotionEdgeSeekStateTests(unittest.TestCase):
    """A seek establishes the travel/extrusion state from the LAYER's
    own opening state plus every boundary strictly before it — a layer
    that began mid-travel must not read its opening motions as
    material merely because the cursor is not at zero."""

    def _state(self, index, first, layer=0):
        return [edge[6] for edge in motion_edges(index, layer, first=first)]

    def test_case_a_a_layer_that_began_travelling_keeps_travelling(self):
        index = make_index(motions=8)
        index.layer_start_extruding[0] = False
        index.travel_starts[0] = []
        index.travel_ends[0] = [5]
        self.assertEqual(self._state(index, 0)[0], False)
        self.assertEqual(self._state(index, 2)[0], False)
        # The boundary AT the sought motion belongs to that motion: the
        # loop applies it before yielding motion 5.
        self.assertEqual(self._state(index, 5)[0], True)
        self.assertEqual(self._state(index, 6)[0], True)

    def test_case_b_a_layer_that_began_extruding_reads_its_boundaries(self):
        index = make_index(motions=8)
        index.layer_start_extruding[0] = True
        index.travel_starts[0] = [2]
        index.travel_ends[0] = [5]
        for first, expected in ((0, True), (1, True), (2, False),
                                (4, False), (5, True), (6, True)):
            self.assertEqual(self._state(index, first)[0], expected, first)

    def test_case_c_every_seek_matches_the_suffix_of_the_full_walk(self):
        # The invariant the incremental cursor relies on, over both
        # opening states and several transitions.
        for opening, starts, ends in ((True, [3, 12], [6, 15]),
                                      (False, [0, 7], [4, 11]),
                                      (False, [], [9]),
                                      (True, [2], [])):
            index = make_index(motions=20)
            index.layer_start_extruding[0] = opening
            index.travel_starts[0] = list(starts)
            index.travel_ends[0] = list(ends)
            full = list(motion_edges(index, 0))
            for first in range(21):
                self.assertEqual(list(motion_edges(index, 0, first=first)),
                                 [edge for edge in full if edge[0] >= first],
                                 (opening, starts, ends, first))

    def test_a_seek_past_an_arc_matches_the_suffix_of_its_full_walk(self):
        # An arc yields several physical subedges for ONE logical
        # motion: the suffix is taken by logical motion, so every
        # subedge of the arc is in it exactly once.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G3 X0 Y10 I-10 J0 E1\n"
                       "G1 X0 Y20 E2\n"
                       "G1 X10 Y20 E0.5\n"
                       "G1 X10 Y30 E1.5\n")
        full = list(motion_edges(index, 0))
        for first in range(index.motion_count(0) + 1):
            self.assertEqual(list(motion_edges(index, 0, first=first)),
                             [edge for edge in full if edge[0] >= first], first)


class MotionEdgeSeekTests(unittest.TestCase):
    """The printed-object cursor's seek and the feature RLE it walks
    past. A run that lost its shape costs the layer its feature NAME,
    never a coordinate and never a raise."""

    def test_a_seek_at_or_past_the_layer_reads_nothing(self):
        index = make_index(motions=5)
        self.assertEqual(list(motion_edges(index, 0, first=5)), [])
        self.assertEqual(list(motion_edges(index, 4)), [])
        self.assertEqual(list(motion_edges(index, -1)), [])

    def test_a_negative_seek_starts_at_the_first_motion(self):
        index = make_index(motions=3)
        self.assertEqual([edge[0] for edge in motion_edges(index, 0, first=-2)],
                         [0, 1, 2])

    def test_a_malformed_run_list_never_moves_a_coordinate(self):
        # An empty run, a non-numeric span, a zero span and a real run:
        # the walk reads past the first three to the last one.
        index = make_index(motions=6)
        index.motion_types[0] = [[], ["x", 2], [0, 2], [6, 2]]
        edges = list(motion_edges(index, 0))
        self.assertEqual([edge[0] for edge in edges], [0, 1, 2, 3, 4, 5])
        self.assertEqual([edge[5] for edge in edges], [2] * 6)
        self.assertEqual([edge[1:5] for edge in edges[1:]],
                         [(float(m - 1), 0.0, float(m), 0.0) for m in range(1, 6)])

    def test_a_seek_skips_the_runs_before_it_without_trusting_them(self):
        # The seek walks the RLE to motion 3 over the same bad runs and
        # lands on the run that covers it: motion 3's edge still opens
        # where motion 2 ended.
        index = make_index(motions=6)
        index.motion_types[0] = [[2, 2], [], ["x", 2], [0, 2], [4, 2]]
        edges = list(motion_edges(index, 0, first=3))
        self.assertEqual([edge[0] for edge in edges], [3, 4, 5])
        self.assertEqual([edge[5] for edge in edges], [2] * 3)
        self.assertEqual(edges[0][1:3], (2.0, 0.0))


class SimplificationBudgetTests(unittest.TestCase):
    """The point budget's written fallbacks: segments already minimal,
    and a channel the passes cannot fit below the ceiling."""

    def test_a_segment_of_two_vertices_is_already_minimal(self):
        # Every segment is its own endpoints: no pass can shrink one,
        # so the channel is returned with both ends where they were.
        segments = [[[0.0, 0.0, 0.0], [1.0, 0.0, 1.0]] for _ in range(400)]
        simplified = _budgeted(segments, 8)
        self.assertEqual(simplified, segments)

    def test_a_corner_is_kept_and_a_straight_run_collapses(self):
        # A split leaves one side a two-vertex interval: the corner is
        # the vertex that stays, the collinear one behind it goes.
        points = [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0],
                  [2.0, 0.0, 2.0], [3.0, 0.0, 3.0]]
        self.assertEqual(_douglas_peucker(points, 0.5),
                         [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [3.0, 0.0, 3.0]])

    def test_a_channel_that_cannot_fit_still_returns_its_own_vertices(self):
        # A budget no subset can meet: the passes coarsen until the
        # tolerance reaches the fidelity ceiling and stop there, so
        # the channel is still a subset with both ends intact — the
        # payload never widens past the budget's promise.
        points = [[0.0, 0.0, 0.0], [0.001, 0.001, 1.0], [0.002, 0.0, 2.0]]
        self.assertEqual(_budgeted([list(points)], 1),
                         [[[0.0, 0.0, 0.0], [0.002, 0.0, 2.0]]])


class SimplificationWorkBoundTests(unittest.TestCase):
    """The channel's work counter: the recursion's quadratic case is
    clamped, and the clamp limits refinement, never accuracy. The
    counter is injectable so the exhaustion path is exercised without
    paying a pathological chain's full cost."""

    _SPIKES = [[float(index), 0.5 if index % 2 else 0.0, float(index)]
               for index in range(64)]

    def test_a_counter_that_covers_the_walk_changes_nothing(self):
        # The charge is generous, so the walk is the exact recursion and
        # the spikes all stay: the bound is a limit on cost, never a
        # filter on the geometry.
        spent = [10 ** 9]
        self.assertEqual(_simplify(self._SPIKES, 0.03, spent), self._SPIKES)
        self.assertGreaterEqual(spent[0], 0)

    def test_an_exhausted_counter_completes_instead_of_lying(self):
        # A charge that cannot cover the second scan: the walk stops
        # refining, keeps the vertices it could not prove a chord for,
        # and still returns the input's own vertices within the
        # tolerance — the unrefined interval costs accuracy nothing.
        spent = [80]
        kept = _simplify(self._SPIKES, 0.03, spent)
        self.assertLess(spent[0], 0)
        self.assertEqual(kept[0], self._SPIKES[0])
        self.assertEqual(kept[-1], self._SPIKES[-1])
        self.assertEqual([point[2] for point in kept],
                         sorted({point[2] for point in kept}))
        self.assertLessEqual(dropped_within(self._SPIKES, kept), 0.03 + 1e-9)

    def test_an_exhausted_counter_is_not_a_refusal_to_simplify(self):
        # A run the walk can prove before its charge runs out still
        # collapses: exhaustion completes what is left, it does not
        # undo what was already decided.
        points = [[float(index), 0.0, float(index)] for index in range(9)]
        spent = [8]
        self.assertEqual(_simplify(points, 0.03, spent),
                         [[0.0, 0.0, 0.0], [8.0, 0.0, 8.0]])

    def test_a_budgeted_channel_spends_one_counter_across_its_passes(self):
        # Geometry dense enough that the tolerance starts far below the
        # arc ceiling, so the pass escalates instead of stopping at the
        # first miss — and each pass weighs a chain whose exact walk is
        # quadratic. One charge per CHANNEL is what keeps the call
        # bounded; one per pass would pay the quadratic cost up to six
        # times over.
        points = [[index * 0.0005, 0.05 if index % 2 else 0.0, float(index)]
                  for index in range(20000)]
        started = time.perf_counter()
        kept = _budgeted([points], MAX_TRAVEL_POINTS)[0]
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 5.0)
        self.assertGreater(len(kept), 2)
        self.assertEqual([point[2] for point in kept],
                         sorted({point[2] for point in kept}))
        self.assertLessEqual(dropped_within(points, kept), 0.03 + 1e-9)


class PreparedLayerStoreTests(unittest.TestCase):
    """The prepared-layer store: the build is a pure function of the
    index and the layer, so a repeat must not pay for it twice — and a
    layer whose arrays have moved on must never read a stale payload."""

    def test_a_second_read_returns_the_payload_the_first_one_built(self):
        index = make_index(motions=6)
        self.assertIs(layer_polylines(index, 0), layer_polylines(index, 0))
        self.assertEqual(layer_polylines(index, 0), prepare_layer(index, 0))

    def test_the_store_serves_two_consumers_the_same_layer(self):
        # The follower's window and the bundle build read the same
        # layer object, so the walk runs once per layer however many
        # times the anchor moves across it.
        index = make_index(layers=3, motions=6)
        first = layer_polylines(index, 1)
        self.assertIs(layer_polylines(index, 1), first)
        self.assertEqual(plate_layers(index, 1)["current"], first)

    def test_an_unhydrated_or_absent_layer_reads_none_and_is_not_stored(self):
        index = make_index(layers=2, motions=6, compact=True)
        index.hydrated_layers = {0}
        self.assertIsNone(layer_polylines(index, 1))
        self.assertIsNone(prepare_layer(index, 9))
        self.assertIsNone(layer_polylines(index, -1))
        self.assertIsNone(prepare_layer(index, 9))

    def test_a_layer_that_was_hydrated_later_does_not_read_the_empty_payload(self):
        # A compact index fills in place: the layer's first read is an
        # empty payload, and the arrays that arrive after it must be
        # what the next read builds from.
        index = make_index(layers=1, motions=0, compact=True)
        index.hydrated_layers = {0}
        self.assertEqual(layer_polylines(index, 0)["motions"], 0)
        index.motion_offsets = [array("Q", [10, 20])]
        index.motion_x = [array("f", [0.0, 4.0])]
        index.motion_y = [array("f", [0.0, 3.0])]
        index.motion_types = [[[2, 2]]]  # runs are [length, feature code]
        self.assertEqual(layer_polylines(index, 0)["motions"], 2)
        self.assertEqual(_xy(layer_polylines(index, 0)["classes"]["WALL-OUTER"][0]),
                         [[0.0, 0.0], [4.0, 3.0]])

    def test_a_collected_index_never_serves_a_new_one_its_payload(self):
        first = make_index(motions=6)
        payload = layer_polylines(first, 0)
        del first
        gc.collect()
        second = make_index(motions=6)
        # Whatever the allocator reuses, the new index reads as its own:
        # a dead owner's entry is a miss, never another layer's answer.
        self.assertEqual(layer_polylines(second, 0), payload)
        self.assertIsNotNone(payload)

    def test_the_store_does_not_grow_without_bound(self):
        index = make_index(layers=24, motions=4)
        for layer in range(24):
            layer_polylines(index, layer)
        self.assertLessEqual(len(_prepared_layers), _PREPARED_LIMIT)


class SplitAndBundleTests(unittest.TestCase):
    def test_the_split_bisects_the_motion_offsets(self):
        index = make_index(motions=20)
        self.assertEqual(split_index(index, 0, 95), 10)
        # The boundary is exclusive: at the motion's own offset the
        # count does not yet include it, which is the off-by-one the
        # painter used to paint early.
        self.assertEqual(split_index(index, 0, 100), 10)
        self.assertEqual(split_index(index, 0, 101), 11)

    def test_out_of_range_layers_read_no_split(self):
        index = make_index()
        self.assertIsNone(split_index(index, -1, 0))
        self.assertIsNone(split_index(index, 3, 0))

    def test_a_missing_anchor_builds_no_layer_bundle(self):
        self.assertEqual(plate_layers(make_index(), None), {})

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


class RefinedSplitTests(unittest.TestCase):
    """The followed boundary from the LIVE tool position: the paint
    follows the nozzle, not the dispatcher.

    ``refined_split`` is the Preview's own refinement (the same
    ``refined_fraction`` search, arcs included) floored onto the layer's
    motion grid, so the count names the motions the head has FINISHED
    while ``file_position`` stays the coarse anchor. Klipper's parser
    runs ahead of the nozzle by its lookahead, which is what made the
    painted fill reach past the head.
    """

    def test_a_queued_move_does_not_paint_ahead_of_the_nozzle(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G1 X10 Y0 E1\n"
                       "G1 X110 Y0 E2\n"
                       "G1 X120 Y0 E3\n")
        offsets = list(index.motion_offsets[0])
        # The dispatcher has reached the layer's last motion, so the
        # coarse boundary paints the 100 mm move whole...
        self.assertEqual(split_index(index, 0, offsets[2]), 2)
        # ...while the nozzle is only half way along it.
        split, method = index.refined_split(0, offsets[2], (60.0, 0.0, 0.0))
        self.assertEqual(method, "live position")
        self.assertEqual(split, 1, "the queued move was painted before the nozzle reached it")

    def test_the_midpoint_of_a_motion_counts_only_the_finished_ones(self):
        # A synthetic row's motion m runs from x = m - 1 to x = m, so a
        # live x names the motion under the head by construction.
        index = make_index()
        queued = list(index.motion_offsets[0])[19]  # the dispatcher is at the layer's end
        self.assertEqual(index.refined_split(0, queued, (5.0, 0.0, 0.2))[0], 6)
        # A tenth of the way into motion 6: under way, so never counted.
        self.assertEqual(index.refined_split(0, queued, (5.1, 0.0, 0.2))[0], 6)
        # At its endpoint the motion is finished, and it counts.
        self.assertEqual(index.refined_split(0, queued, (6.0, 0.0, 0.2))[0], 7)

    def test_an_arcs_midpoint_refines_against_the_curve(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X20 Y30\n"
                       "G3 X-20 Y30 I-20 J0 E1\n"
                       "G0 X50 Y50\n")
        offsets = list(index.motion_offsets[0])
        # The dispatcher has reached the closing travel while the nozzle
        # sits at the semicircle's apex, 20 mm off its chord: the coarse
        # boundary would paint the whole arc.
        self.assertEqual(split_index(index, 0, offsets[2]), 2)
        split, method = index.refined_split(0, offsets[2], (0.0, 50.0, 0.0))
        self.assertEqual(method, "live position")
        self.assertEqual(split, 1, "the arc was painted while the head was on it")
        # The chord's midpoint is 20 mm from the curve, so a chord
        # reading refuses the match and the boundary jumps ahead.
        self.assertIsNone(index.refined_split(0, offsets[2], (0.0, 30.0, 0.0))[0])

    def test_telemetry_beside_the_path_still_refines(self):
        index = make_index()
        queued = list(index.motion_offsets[0])[19]
        # 0.4 mm off the row: the nearest motion is still the head's own.
        self.assertEqual(index.refined_split(0, queued, (5.0, 0.4, 0.2)),
                         (6, "live position"))

    def test_an_off_path_toolhead_keeps_the_callers_own_boundary(self):
        index = make_index()
        queued = list(index.motion_offsets[0])[19]
        # A park, a probe, a lifted head: nothing on the layer is near,
        # so no boundary is invented — the caller's coarse one stands.
        split, method = index.refined_split(0, queued, (5.0, 40.0, 0.2))
        self.assertIsNone(split)
        self.assertEqual(method, "motion index")

    def test_an_off_path_read_holds_the_boundary_it_was_given(self):
        # With a boundary already painted, an off-path head is a HOLD:
        # the floor comes back, never the parser's position.
        index = make_index()
        queued = list(index.motion_offsets[0])[19]
        self.assertEqual(index.refined_split(0, queued, (5.0, 40.0, 0.2), minimum_split=11),
                         (11, "held (refined unavailable)"))

    def test_a_layer_the_index_cannot_measure_refines_nothing(self):
        index = make_index()
        queued = list(index.motion_offsets[0])[19]
        self.assertEqual(index.refined_split(4, queued, (5.0, 0.0, 0.2)), (None, "no motions"))

    def test_the_refinement_never_falls_below_the_painted_boundary(self):
        index = make_index()
        queued = list(index.motion_offsets[0])[19]
        painted = 0
        for x in (4.0, 9.0, 9.0, 6.0, 12.0, 11.0, 18.5):
            split, _method = index.refined_split(
                0, queued, (x, 0.0, 0.2), minimum_split=painted)
            self.assertGreaterEqual(split, painted, "the boundary walked backwards at x=%s" % x)
            painted = split
        self.assertEqual(painted, 19, "the refinement did not reach the end of the layer")
        # A read the floor had to lift is reported as such, never as a
        # fresh match of its own.
        self.assertEqual(
            index.refined_split(0, queued, (6.0, 0.0, 0.2), minimum_split=12)[1],
            "live position (monotonic)")


if __name__ == "__main__":
    unittest.main()
