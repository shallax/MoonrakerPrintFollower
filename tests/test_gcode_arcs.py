"""G2/G3 arcs from literal G-code to drawn geometry.

Every case here starts from the G-code a slicer or a hand-written file
would carry, goes through the real parser, and asserts XY COORDINATES —
never vertex counts, never motion indices used as a proxy for geometry.
The invariant under test is the one a chord cannot satisfy: a vertex
sitting only on the commanded circle, a direction, a plane, a true
endpoint, and the ownership that keeps a whole arc inside one printed
motion.
"""
from __future__ import annotations

from array import array
from math import cos, hypot, radians, sin
import gzip
import json
import os
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from plugins import ArcGeometry, GCodeIndex
from plugins.GCodeIndex import (
    LayerMotionIndex,
    PersistentIndexCache,
    build_index_from_bytes,
    build_index_from_file,
    hydrate_layer_from_file,
)
from plugins.MoonrakerProtocol import RemoteFileIdentity
from plugins.PlateProgress import _budgeted, layer_polylines, motion_edges, split_index

try:  # the host stdlib suite has no PyQt6: the service cases skip there
    from PyQt6.QtCore import QObject, pyqtSignal
    from qt_runtime_support import QT_AVAILABLE, runtime
except ImportError:
    QT_AVAILABLE = False


def _index(gcode: str) -> LayerMotionIndex:
    return build_index_from_bytes(gcode.encode("ascii"))


def _chain(index: LayerMotionIndex, name: str, layer: int = 0):
    """The one class segment the tested file draws, or a clear failure."""
    segments = layer_polylines(index, layer)["classes"].get(name, [])
    assert len(segments) == 1, "the file drew %d %s segments, not one" % (len(segments), name)
    return segments[0]


def _motions(chain):
    """The motion index each vertex carries — the payload's own field."""
    return [int(vertex[2]) for vertex in chain]


def _deviation(vertices, centre_x, centre_y, radius):
    return max(abs(hypot(vertex[0] - centre_x, vertex[1] - centre_y) - radius)
               for vertex in vertices)


def _chord_error(points, centre_x, centre_y, radius):
    """The worst distance between a drawn chord's own midpoint and the
    circle it stands in for: what the eye sees if a curve is flattened."""
    worst = 0.0
    for position in range(1, len(points)):
        mid_x = (points[position - 1][0] + points[position][0]) / 2.0
        mid_y = (points[position - 1][1] + points[position][1]) / 2.0
        worst = max(worst, abs(hypot(mid_x - centre_x, mid_y - centre_y) - radius))
    return worst


def _write(gcode: str, suffix=".gcode") -> str:
    handle = tempfile.NamedTemporaryFile(prefix="mpf-arc-", suffix=suffix, delete=False)
    handle.write(gcode.encode("ascii"))
    handle.close()
    return handle.name


# Cache fixtures: a file carrying a real arc, one carrying nothing but
# G0/G1, and one that selects a modal plane without ever commanding an
# arc (its geometry is linear, but the plane still has to survive).
ARC_SOURCE = "M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\n"
PLAIN_SOURCE = "M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y0\nG1 X10 Y0 E1\nG1 X10 Y10 E2\n"
PLANE_ONLY_SOURCE = "M82\nG18\n;LAYER:0\n;TYPE:SKIN\nG1 X1 Y2 Z3 E1\nG0 X10 Y0 Z0\n"


class ArcWindowTests(unittest.TestCase):
    """The centre offsets are a centre, and the sweep is a direction.

    The chord is the failure this whole file exists to catch: it is the
    geometry the index would produce by ignoring I/J/K, and every case
    below is one a chord gets visibly wrong.
    """

    def test_a_counter_clockwise_quarter_circle_sweeps_its_quadrant(self):
        # G3 from (10,0) about (0,0) to (0,10): the short way, through
        # +x/+y, never near the origin or the chord's midpoint.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G3 X0 Y10 I-10 J0 E1\n")
        self.assertEqual(index.motion_count(0), 2)
        self.assertEqual(index.motion_arcs[0].get(1), (17, False, -10.0, 0.0))
        chain = _chain(index, "SKIN")
        self.assertGreater(len(chain), 4, "the arc arrived as its chord")
        self.assertLess(_deviation(chain, 0.0, 0.0, 10.0), 1.5e-3,
                        "a vertex left the circle the centre offset describes")
        self.assertTrue(all(vertex[0] >= -1e-9 and vertex[1] >= -1e-9 for vertex in chain),
                        "a counter-clockwise quarter went outside its quadrant")
        self.assertEqual(_motions(chain), [1] * len(chain))
        self.assertAlmostEqual(chain[-1][0], 0.0, places=6)
        self.assertAlmostEqual(chain[-1][1], 10.0, places=6)

    def test_a_clockwise_quarter_circle_sweeps_the_other_way(self):
        # The mirror case: G2 from (0,10) about (0,0) back to (10,0).
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X0 Y10\n"
                       "G2 X10 Y0 I0 J-10 E1\n")
        self.assertEqual(index.motion_arcs[0].get(1), (17, True, 0.0, -10.0))
        chain = _chain(index, "SKIN")
        self.assertGreater(len(chain), 4)
        self.assertLess(_deviation(chain, 0.0, 0.0, 10.0), 1.5e-3)
        self.assertTrue(all(vertex[0] >= -1e-9 and vertex[1] >= -1e-9 for vertex in chain))
        self.assertEqual(chain[-1][0], 10.0)

    def test_a_clockwise_arc_takes_the_long_way_when_that_is_the_command(self):
        # The same endpoints as the counter-clockwise quarter, commanded
        # clockwise: 270 degrees through -y and -x. A shortest-angle
        # reading of the sweep would draw the counter-clockwise quarter.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G2 X0 Y10 I-10 J0 E1\n")
        chain = _chain(index, "SKIN")
        self.assertTrue(any(vertex[1] < -9.0 for vertex in chain),
                        "the clockwise sweep cut the short way round")
        self.assertTrue(any(vertex[0] < -9.0 for vertex in chain))
        self.assertLess(_deviation(chain, 0.0, 0.0, 10.0), 1.5e-3)

    def test_the_spelling_variants_are_the_same_arc(self):
        # G02/G03 are the same command words to the parser, so they must
        # be the same geometry to the index.
        long_form = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG03 X0 Y10 I-10 J0 E1\n")
        short_form = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\n")
        self.assertEqual(long_form.motion_arcs[0], short_form.motion_arcs[0])
        self.assertEqual(layer_polylines(long_form, 0), layer_polylines(short_form, 0))
        clockwise = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y10\nG02 X10 Y0 I0 J-10 E1\n")
        self.assertEqual(clockwise.motion_arcs[0].get(1), (17, True, 0.0, -10.0))

    def test_a_full_circle_is_a_circle_and_not_a_dot(self):
        # The planar target equals the planar start, so the command is a
        # whole turn: every vertex is on the circle, the path returns to
        # the endpoint, and the motion count stays the command count.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G3 X10 Y0 I-10 J0 E1\n")
        self.assertEqual(index.motion_count(0), 2)
        chain = _chain(index, "SKIN")
        self.assertGreater(len(chain), 8)
        self.assertLess(_deviation(chain, 0.0, 0.0, 10.0), 1.5e-3)
        self.assertLess(min(vertex[1] for vertex in chain), -9.0)
        self.assertGreater(max(vertex[1] for vertex in chain), 9.0)
        self.assertLess(min(vertex[0] for vertex in chain), -9.0)
        self.assertAlmostEqual(chain[-1][0], 10.0, places=5)
        self.assertAlmostEqual(chain[-1][1], 0.0, places=5)

    def test_a_full_circle_without_endpoint_words_is_still_a_circle(self):
        # G2/G3 may omit the planar endpoint entirely, which Klipper reads
        # as "the target is the start" — the same full circle, and the
        # motion still has an endpoint (the position it never left).
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G2 I-10 J0 E1\n")
        chain = _chain(index, "SKIN")
        self.assertLess(min(vertex[1] for vertex in chain), -9.0,
                        "a clockwise turn from (10,0) must first go through -y")
        self.assertLess(_deviation(chain, 0.0, 0.0, 10.0), 1.5e-3)
        self.assertEqual(index.motion_count(0), 2)


class ArcExtrusionStateTests(unittest.TestCase):
    """An arc is classified by its E like any other motion: the whole
    curve is one extrusion or one travel, never a mixture."""

    @staticmethod
    def _arc_edges(index, motion, layer=0):
        return [(x0, y0, x1, y1, extruding) for owner, x0, y0, x1, y1, _f, extruding
                in motion_edges(index, layer) if owner == motion]

    def test_an_arc_with_absolute_e_extrudes_along_its_whole_curve(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G1 X10 Y0 E1\n"
                       "G3 X0 Y10 I-10 J0 E2\n")
        edges = self._arc_edges(index, 2)
        self.assertGreater(len(edges), 4)
        self.assertTrue(all(edge[4] for edge in edges), "part of the arc deposited nothing")
        for _x0, _y0, x1, y1, _e in edges:
            self.assertAlmostEqual(hypot(x1, y1), 10.0, places=3)
        self.assertEqual(_motions(_chain(index, "SKIN")), [1] + [2] * len(edges))

    def test_an_arc_with_relative_e_extrudes_the_same_way(self):
        absolute = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                          "G0 X10 Y0\nG1 X10 Y0 E1\nG3 X0 Y10 I-10 J0 E2\n")
        relative = _index("M83\n;LAYER:0\n;TYPE:SKIN\n"
                          "G0 X10 Y0\nG1 X10 Y0 E1\nG3 X0 Y10 I-10 J0 E1\n")
        self.assertEqual(layer_polylines(absolute, 0), layer_polylines(relative, 0))

    def test_an_arc_without_e_is_a_curved_travel(self):
        # A travel arc draws as the path it took, with its glyphs at the
        # TRUE ends of the movement — A where the head stood, B where it
        # arrived — never at an intermediate tessellation vertex.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G1 X10 Y0 E1\n"
                       "G3 X0 Y10 I-10 J0\n"
                       "G1 X0 Y10 E2\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(len(layer["travels"]), 1)
        travel = layer["travels"][0]
        self.assertEqual(travel[0][:2], [10.0, 0.0])
        self.assertEqual(travel[-1][:2], [0.0, 10.0])
        self.assertLess(_deviation(travel, 0.0, 0.0, 10.0), 1.5e-3,
                        "the travel arc was drawn as its chord")
        self.assertEqual(layer["travelStarts"], [[10.0, 0.0, 1.0]])
        self.assertEqual(layer["travelEnds"], [[0.0, 10.0, 1.0]])

    def test_a_retracting_arc_is_not_geometry_in_a_feature_run(self):
        # Negative E is a retraction: the arc deposits nothing, so it is
        # a travel — and the class run breaks around it.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G1 X10 Y0 E1\n"
                       "G3 X0 Y10 I-10 J0 E-1\n"
                       "G1 X0 Y10 E2\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(len(layer["travels"]), 1)
        self.assertLess(_deviation(layer["travels"][0], 0.0, 0.0, 10.0), 1.5e-3)
        self.assertEqual([list(vertex[:2]) for vertex in layer["classes"]["SKIN"][0]],
                         [[0.0, 0.0], [10.0, 0.0]])


class ArcFeatureTopologyTests(unittest.TestCase):
    """An arc's subedges inherit its feature and its travel state, and
    they never reconnect two runs the G-code kept apart."""

    def test_arc_subedges_stay_in_the_arcs_feature_run(self):
        index = _index("M82\n;LAYER:0\n"
                       ";TYPE:WALL-OUTER\nG1 X10 Y0 E1\n"
                       ";TYPE:SKIN\nG3 X0 Y10 I-10 J0 E2\n"
                       ";TYPE:WALL-OUTER\nG1 X-10 Y10 E3\n")
        layer = layer_polylines(index, 0)
        self.assertEqual(sorted(layer["classes"]), ["SKIN", "WALL-OUTER"])
        self.assertEqual(len(layer["classes"]["WALL-OUTER"]), 2,
                         "the two wall runs were joined across the skin arc")
        skin = layer["classes"]["SKIN"][0]
        self.assertEqual(_motions(skin), [1] * len(skin))
        self.assertLess(_deviation(skin, 0.0, 0.0, 10.0), 1.5e-3)
        self.assertEqual([list(vertex[:2]) for vertex in layer["classes"]["WALL-OUTER"][0]],
                         [[0.0, 0.0], [10.0, 0.0]])
        self.assertEqual([list(vertex[:2]) for vertex in layer["classes"]["WALL-OUTER"][1]],
                         [[0.0, 10.0], [-10.0, 10.0]])

    def test_an_arc_between_two_linear_moves_joins_the_path_exactly(self):
        # The next move's edge starts where the arc ended: no gap, no
        # overlap, whatever the tessellation did in between.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G1 X10 Y0 E1\n"
                       "G3 X0 Y10 I-10 J0 E2\n"
                       "G1 X-10 Y10 E3\n")
        chain = _chain(index, "SKIN")
        self.assertEqual(chain[-1][:2], [-10.0, 10.0])
        self.assertEqual(_motions(chain)[-2:], [1, 2])
        endpoints = [(vertex[0], vertex[1]) for vertex in chain]
        self.assertEqual(len(endpoints), len(set(endpoints)),
                         "the chain repeats a vertex, so an edge has no length")

    def test_every_arc_subedge_belongs_to_the_arcs_motion_index(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\nG1 X-10 Y10 E2\n")
        seen = [motion for motion, *_rest in motion_edges(index, 0)]
        self.assertEqual(seen[0], 0)
        self.assertEqual(seen[-1], 2)
        self.assertEqual(set(seen), {0, 1, 2})
        self.assertGreater(seen.count(1), 4, "the arc contributed one edge")


class ArcPlaneTests(unittest.TestCase):
    """G17/G18/G19 are modal across commands and across layers, and the
    plane decides which two of I/J/K are the centre offsets.

    An off-plane arc's 3D path is asserted through the LIVE POSITION
    match, the one consumer that reads the full XYZ: the XY payload can
    only project it, and a projected XZ arc is a straight line in X.
    """

    def _assert_arc_midpoint_matches(self, index, label, live, chord):
        offsets = list(index.motion_offsets[0])
        fraction, method = index.refined_fraction(0, offsets[1], live)
        self.assertEqual(method, "live position",
                         "%s: the arc's own midpoint was not matched" % label)
        self.assertAlmostEqual(fraction, 0.75, places=4)
        _other, other_method = index.refined_fraction(0, offsets[1], chord)
        self.assertNotEqual(other_method, "live position",
                            "%s: the chord's midpoint was accepted as on-path" % label)

    def test_a_g18_arc_sweeps_x_and_z_about_the_offsets(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G18\nG0 X20 Y3 Z0\n"
                       "G3 X0 Z20 I-20 K0 E1\n")
        self.assertEqual(index.motion_arcs[0].get(1), (18, False, -20.0, 0.0))
        # Read as XY the same words draw a line in X and nothing in Z;
        # the plane's own axes are X and Z, and Y never moves.
        chain = _chain(index, "SKIN")
        self.assertTrue(all(abs(vertex[1] - 3.0) < 1e-9 for vertex in chain),
                        "a G18 arc moved Y, which is its helical axis here")
        self.assertLess(chain[-1][0], 1e-6)
        self._assert_arc_midpoint_matches(
            index, "G18",
            (20.0 * cos(radians(45)), 3.0, 20.0 * sin(radians(45))),
            (10.0, 3.0, 10.0))

    def test_a_g19_arc_sweeps_y_and_z_about_the_offsets(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G19\nG0 X1 Y20 Z0\n"
                       "G3 Y0 Z20 J-20 K0 E1\n")
        self.assertEqual(index.motion_arcs[0].get(1), (19, False, -20.0, 0.0))
        chain = _chain(index, "SKIN")
        self.assertTrue(all(abs(vertex[0] - 1.0) < 1e-9 for vertex in chain),
                        "a G19 arc moved X, which is its helical axis here")
        self._assert_arc_midpoint_matches(
            index, "G19",
            (1.0, 20.0 * cos(radians(45)), 20.0 * sin(radians(45))),
            (1.0, 10.0, 10.0))

    def test_the_plane_stays_modal_across_ordinary_commands(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G18\nG0 X10 Y0 Z0\n"
                       "G1 X10 Y0 Z0 F1200\n"
                       "G92 E0\n"
                       "M106 S255\n"
                       "G21\n"
                       "G3 X0 Z10 I-10 K0 E1\n")
        self.assertEqual(index.motion_arcs[0].get(2), (18, False, -10.0, 0.0),
                         "a command between the plane word and the arc reset the plane")

    def test_the_plane_selected_in_an_earlier_layer_governs_a_later_one(self):
        index = _index("M82\nG18\n"
                       ";LAYER:0\nG1 X10 Y0 Z0 E1\n"
                       ";LAYER:1\nG0 X10 Z0\n"
                       "G3 X0 Z10 I-10 K0 E1\n")
        self.assertEqual(index.layer_start_arc_plane, [18, 18])
        self.assertEqual(index.motion_arcs[0], {})
        self.assertEqual(index.motion_arcs[1].get(1), (18, False, -10.0, 0.0))

    def test_the_default_plane_is_xy(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\n")
        self.assertEqual(index.layer_start_arc_plane, [17])
        self.assertEqual(index.motion_arcs[0].get(1), (17, False, -10.0, 0.0))

    def test_a_helical_arc_advances_the_off_plane_axis_linearly(self):
        # Under G17 the remaining axis is Z: the head climbs while it
        # sweeps, and the live position matches the climb as well as the
        # curve — the helix's own midpoint, not the plane's.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X20 Y0 Z0\n"
                       "G3 X-20 Y0 Z10 I-20 J0 E1\n")
        offsets = list(index.motion_offsets[0])
        fraction, method = index.refined_fraction(0, offsets[1], (0.0, 20.0, 5.0))
        self.assertEqual(method, "live position")
        self.assertAlmostEqual(fraction, 0.75, places=4)


class ArcUnitConversionTests(unittest.TestCase):
    """G20 scales an arc's centre offsets exactly as it scales the axes:
    an inch command is the millimetre curve it stands for, not a curve
    whose centre was read in millimetres."""

    def test_an_inches_arc_draws_the_same_curve_as_its_millimetre_twin(self):
        # One inch of I beside one inch of travel: 25.4 mm both ways, so
        # the curve is the quarter circle of radius 25.4 about the origin.
        inches = _index("M82\nG20\n;LAYER:0\n;TYPE:SKIN\n"
                        "G0 X1 Y0\nG3 X0 Y1 I-1 J0 E1\n")
        millimetres = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                             "G0 X25.4 Y0\nG3 X0 Y25.4 I-25.4 J0 E1\n")
        self.assertEqual(inches.motion_arcs[0], {1: (17, False, -25.4, 0.0)})
        self.assertLess(_deviation(_chain(inches, "SKIN"), 0.0, 0.0, 25.4), 1e-4)
        self.assertEqual(layer_polylines(inches, 0), layer_polylines(millimetres, 0))


class ArcDegradationTests(unittest.TestCase):
    """Klipper-invalid arcs keep the index usable: the motion keeps its
    endpoint, its E, its feature and its place, and draws as the straight
    edge it would have been without the offsets."""

    def test_a_radius_form_arc_is_not_drawn_as_a_curve(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG2 X0 Y10 R10 E1\n")
        self.assertEqual(index.motion_arcs[0], {})
        self.assertEqual([list(vertex[:2]) for vertex in _chain(index, "SKIN")],
                         [[10.0, 0.0], [0.0, 10.0]])

    def test_an_arc_under_relative_xyz_is_not_drawn_as_a_curve(self):
        # Klipper refuses G2/G3 in relative mode; the position still
        # advances, so the following geometry stays in the file's frame.
        index = _index("M82\nG91\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\n"
                       "G3 X-10 Y10 I-10 J0 E1\n"
                       "G1 X-10 Y0 E2\n")
        self.assertEqual(index.motion_arcs[0], {})
        self.assertEqual([list(vertex[:2]) for vertex in _chain(index, "SKIN")],
                         [[10.0, 0.0], [0.0, 10.0], [-10.0, 10.0]])

    def test_zero_centre_offsets_are_not_an_arc(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I0 J0 E1\n")
        self.assertEqual(index.motion_arcs[0], {})

    def test_missing_offsets_are_not_an_arc(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 E1\n")
        self.assertEqual(index.motion_arcs[0], {})

    def test_the_offsets_of_the_other_planes_do_not_form_an_arc(self):
        # Under G17 the K word is not a centre offset: I and J are both
        # absent and zero, so there is no arc to draw.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 K10 E1\n")
        self.assertEqual(index.motion_arcs[0], {})
        self.assertEqual([list(vertex[:2]) for vertex in _chain(index, "SKIN")],
                         [[10.0, 0.0], [0.0, 10.0]])

    def test_a_non_finite_offset_keeps_the_endpoint_it_can_read(self):
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 Iinf J0 E1\n")
        self.assertEqual(index.motion_arcs[0], {})
        self.assertEqual([list(vertex[:2]) for vertex in _chain(index, "SKIN")],
                         [[10.0, 0.0], [0.0, 10.0]])

    def test_malformed_arc_lines_never_crash_the_index(self):
        # Whatever the words say, the motion keeps its place, its E and
        # the endpoint the linear reading of the same words gives — and
        # the tessellation stays bounded. A degenerate arc is drawn, not
        # raised on.
        for words in ("X Y10 I-10 J0",      # an axis word without a value
                      "X1e999 Y0 I-10 J0",  # an endpoint that is not finite
                      "X0 Y10 Inan J0",
                      "X0 Y10 Iinf J0",
                      "X0 Y10 I-10 J0 R5",  # radius form: Klipper refuses it
                      "X0 Y10"):
            arc = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 %s E1\n" % words)
            linear = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG1 %s E1\n" % words)
            self.assertEqual(arc.motion_count(0), 2, words)
            self.assertEqual(float(arc.motion_x[0][-1]), float(linear.motion_x[0][-1]), words)
            self.assertEqual(float(arc.motion_y[0][-1]), float(linear.motion_y[0][-1]), words)
            self.assertLessEqual(len(_chain(arc, "SKIN")), 4097, words)

class ArcSplitOwnershipTests(unittest.TestCase):
    """The printed boundary counts motions, not tessellation vertices."""

    @staticmethod
    def _fixture():
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\nG1 X-10 Y10 E2\n")
        return index, list(index.motion_offsets[0])

    def test_the_split_counts_the_motion_that_owns_the_arc(self):
        index, offsets = self._fixture()
        # The positions the reader sees: the start of the layer, the arc's
        # own line, and the line after it. The count is the number of
        # motion lines the position has passed, so a position sitting ON
        # the arc's line has not passed it.
        self.assertEqual(split_index(index, 0, offsets[0]), 0)
        self.assertEqual(split_index(index, 0, offsets[1]), 1)
        self.assertEqual(split_index(index, 0, offsets[2]), 2)

    def test_the_arcs_vertices_share_one_motion_so_they_print_together(self):
        index, _offsets = self._fixture()
        chain = _chain(index, "SKIN")
        # Split 2 counts motions 0 and 1, so the whole arc prints — and
        # motion 2, the line after it, does not.
        drawn = [vertex for vertex in chain if int(vertex[2]) < 2]
        self.assertGreater(len(drawn), 4)
        self.assertEqual({int(vertex[2]) for vertex in drawn} - {0}, {1})
        self.assertAlmostEqual(drawn[-1][0], 0.0, places=6)
        self.assertAlmostEqual(drawn[-1][1], 10.0, places=6)
        self.assertEqual(_motions(chain), sorted(_motions(chain)))

    def test_no_split_count_falls_inside_the_arc(self):
        # The boundary is a motion count, so no count can cut between two
        # of the arc's subedges: every subedge carries the same motion.
        index, _offsets = self._fixture()
        chain = _chain(index, "SKIN")
        arc_vertices = [vertex for vertex in chain if int(vertex[2]) == 1]
        self.assertGreater(len(arc_vertices), 4)
        for count in range(0, 4):
            painted = len([vertex for vertex in chain
                           if int(vertex[2]) == 1 and int(vertex[2]) < count])
            self.assertIn(painted, (0, len(arc_vertices)),
                          "split %d painted part of the arc" % count)


class ArcLivePositionTests(unittest.TestCase):
    """The live toolhead is matched against the curve it is on, not
    against the chord between the curve's ends."""

    @staticmethod
    def _semicircle():
        # A 40 mm semicircle from (20,30) to (-20,30) about (0,30): its
        # apex is 20 mm above the chord's midpoint, and the layer's own
        # travel passes nowhere near either.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X20 Y30\n"
                       "G3 X-20 Y30 I-20 J0 E1\n")
        return index, list(index.motion_offsets[0])

    def test_a_live_position_on_the_arc_matches_the_arc(self):
        index, offsets = self._semicircle()
        fraction, method = index.refined_fraction(0, offsets[1], (0.0, 50.0, 0.0))
        self.assertEqual(method, "live position")
        self.assertAlmostEqual(fraction, 0.75, places=4)

    def test_a_live_position_on_the_chord_is_not_an_on_path_match(self):
        # The chord's midpoint is 20 mm from the arc: a chord-based
        # reading would call this a perfect match and report progress.
        index, offsets = self._semicircle()
        fraction, method = index.refined_fraction(0, offsets[1], (0.0, 30.0, 0.0))
        self.assertNotEqual(method, "live position")
        self.assertNotAlmostEqual(fraction, 0.75, places=3)

    def test_the_end_of_a_full_circle_helix_is_a_live_position_match(self):
        # The helix's start and its target share an XY position: the
        # live toolhead sitting on the exact endpoint is on the path,
        # and the seam must not measure it against the far end instead.
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\n"
                       "G0 X10 Y0 Z0\n"
                       "G3 X10 Y0 Z5 I-10 J0 E1\n")
        offsets = list(index.motion_offsets[0])
        fraction, method = index.refined_fraction(0, offsets[1], (10.0, 0.0, 5.0))
        self.assertEqual(method, "live position",
                         "the exact end of the helix was read as off-model")
        self.assertAlmostEqual(fraction, 1.0, places=6)

    def test_the_arcs_progress_is_monotonic_along_the_curve(self):
        index, offsets = self._semicircle()
        fractions = []
        for degrees in (20, 45, 90, 135, 160):
            live = (20.0 * cos(radians(degrees)), 30.0 + 20.0 * sin(radians(degrees)), 0.0)
            fraction, method = index.refined_fraction(0, offsets[1], live)
            self.assertEqual(method, "live position", "at %d degrees" % degrees)
            fractions.append(fraction)
        self.assertEqual(fractions, sorted(fractions), "progress went backwards on the curve")
        self.assertAlmostEqual(fractions[-1], (1.0 + 160.0 / 180.0) / 2.0, places=3)


class ArcCacheTests(unittest.TestCase):
    """The persistent index carries the arcs, and an old blob cannot
    masquerade as one that does."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="mpf-arc-cache-")
        self.addCleanup(self._cleanup)
        self.identity = RemoteFileIdentity("arcs.gcode", 4096, 1.0, "id-arcs")
        self.cache = PersistentIndexCache(self.directory)

    def _cleanup(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def _round_trip(self, gcode: str):
        path = _write(gcode)
        self.addCleanup(os.remove, path)
        original = build_index_from_file(path)
        self.cache.save(self.identity, original)
        return original, self.cache.load(self.identity)

    def _blob(self, gcode: str) -> str:
        path = _write(gcode)
        self.addCleanup(os.remove, path)
        self.cache.save(self.identity, build_index_from_file(path))
        # The production cache's own path (the per-print subdirectory
        # layout) — the blob to rewrite is the file the loader reads.
        blob = self.cache._path(self.identity)
        self.assertTrue(os.path.exists(blob), "the cache wrote nothing to inspect")
        return blob

    def _built(self, gcode: str, *, compact: bool = False):
        """The index for *gcode*, and the file it came from (still on disk
        for a hydration pass)."""
        path = _write(gcode)
        self.addCleanup(os.remove, path)
        return build_index_from_file(path, compact=compact), path

    @staticmethod
    def _header_of(blob: str) -> dict:
        with gzip.open(blob, "rb") as handle:
            handle.read(8)
            length = struct.unpack("<I", handle.read(4))[0]
            return json.loads(handle.read(length).decode("utf-8"))

    def _rewrite_header(self, blob: str, header: dict) -> None:
        with gzip.open(blob, "rb") as handle:
            body = handle.read()
        length = struct.unpack("<I", body[8:12])[0]
        raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
        with gzip.open(blob, "wb", compresslevel=3) as handle:
            handle.write(b"MPFI110\0")
            handle.write(struct.pack("<I", len(raw)))
            handle.write(raw)
            handle.write(body[8 + length:])

    def test_the_arc_descriptors_survive_the_round_trip(self):
        original, restored = self._round_trip(
            "M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\nG2 X0 Y0 I0 J-10 E2\n")
        self.assertIsNotNone(restored, "the cache refused an arc-bearing index")
        self.assertEqual(restored.motion_arcs, original.motion_arcs)
        self.assertEqual(restored.layer_start_arc_plane, original.layer_start_arc_plane)
        self.assertEqual(layer_polylines(restored, 0), layer_polylines(original, 0))

    def test_every_plane_and_both_directions_round_trip(self):
        gcode = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
                 "G17\nG0 X20 Y0\nG3 X0 Y20 I-20 J0 E1\n"
                 "G18\nG0 X20 Y0 Z0\nG2 X0 Z20 I-20 K0 E2\n"
                 "G19\nG0 X1 Y20 Z0\nG3 Y0 Z20 J-20 K0 E3\n")
        original, restored = self._round_trip(gcode)
        self.assertEqual(restored.motion_arcs, original.motion_arcs)
        self.assertEqual([17, 18, 19],
                         [original.motion_arcs[0][index][0] for index in (1, 3, 5)])
        self.assertEqual([False, True, False],
                         [original.motion_arcs[0][index][1] for index in (1, 3, 5)])
        self.assertEqual(restored.layer_start_arc_plane, [17])

    def test_a_full_circle_descriptor_round_trips_as_one(self):
        original, restored = self._round_trip(
            "M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 I-10 J0 E1\n")
        self.assertEqual(restored.motion_arcs, original.motion_arcs)
        chain = _chain(restored, "SKIN")
        self.assertLess(min(vertex[1] for vertex in chain), -9.0)
        self.assertGreater(max(vertex[1] for vertex in chain), 9.0)

    def test_only_arc_motions_carry_a_descriptor(self):
        body = "".join("G1 X%d Y50 E%d\n" % (step, step + 1) for step in range(40))
        original, restored = self._round_trip(
            "M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y0\n" + body + "G2 X40 Y60 I0 J-10 E99\n")
        self.assertEqual(list(original.motion_arcs[0]), [41])
        self.assertEqual(list(restored.motion_arcs[0]), [41])

    def test_the_layer_start_plane_survives_the_round_trip(self):
        original, restored = self._round_trip(
            "M82\nG18\n;LAYER:0\nG1 X1 Y2 Z3 E1\n;LAYER:1\nG2 X0 Z10 I-10 K0 E2\n")
        self.assertEqual(restored.layer_start_arc_plane, [18, 18])
        self.assertEqual(restored.motion_arcs[1], original.motion_arcs[1])

    def test_a_version_9_blob_is_refused_because_it_may_be_lossy(self):
        # v9 could legally publish a cache whose arc descriptors were
        # dropped by the entry budget, so an absent arc column proves
        # nothing about the file it came from: every v9 blob is refused
        # rather than read as an arc-free one.
        blob = self._blob(ARC_SOURCE)
        self.assertEqual(GCodeIndex._CACHE_VERSION, 10,
                         "the cache version must move past the era that could drop arcs")
        self._rewrite_header(blob, dict(self._header_of(blob), version=9))
        self.assertIsNone(self.cache.load(self.identity),
                          "a v9 blob (possibly lossy) was accepted")

    def test_an_arc_bearing_index_is_never_cached_without_its_arcs(self):
        # Arc descriptors are physical geometry, not presentation: when
        # the entry budget cannot hold them the entry is not published,
        # rather than published as a file that restores as chords.
        original, _path = self._built(ARC_SOURCE)
        with patch.object(GCodeIndex, "_MAX_CACHE_ARC_ENTRIES", 0):
            self.cache.save(self.identity, original)
        self.assertEqual(os.listdir(self.directory), [],
                         "a cache without the arcs was published anyway")
        self.assertIsNone(self.cache.load(self.identity))

    def test_a_g0_g1_file_still_caches_with_no_arc_budget(self):
        # Nothing to lose, nothing to refuse: the budget only ever
        # refuses files that actually carry arc descriptors.
        original, _path = self._built(PLAIN_SOURCE)
        with patch.object(GCodeIndex, "_MAX_CACHE_ARC_ENTRIES", 0):
            self.cache.save(self.identity, original)
        restored = self.cache.load(self.identity)
        self.assertIsNotNone(restored, "a file with no arcs to lose was not cached")
        self.assertEqual(layer_polylines(restored, 0), layer_polylines(original, 0))

    def test_a_plane_only_file_keeps_its_modal_plane_with_no_arc_budget(self):
        # A file that selects G18 without commanding any arc still needs
        # the layer-start plane the hydrator seeds from.
        original, _path = self._built(PLANE_ONLY_SOURCE)
        with patch.object(GCodeIndex, "_MAX_CACHE_ARC_ENTRIES", 0):
            self.cache.save(self.identity, original)
        restored = self.cache.load(self.identity)
        self.assertIsNotNone(restored, "a file with no arcs to lose was not cached")
        self.assertEqual(restored.layer_start_arc_plane,
                         original.layer_start_arc_plane)

    def test_an_over_budget_arc_column_is_refused_on_load(self):
        self.assertTrue(self._blob(ARC_SOURCE), "no blob to load")
        with patch.object(GCodeIndex, "_MAX_CACHE_ARC_ENTRIES", 0):
            self.assertIsNone(self.cache.load(self.identity),
                              "an over-budget arc column was read as arc-free")

    def test_a_cached_arc_file_cannot_come_back_as_chords(self):
        # The only two allowed states are "cached faithfully" and "not
        # cached": what comes back is compared as drawn geometry.
        for gcode in (ARC_SOURCE, PLANE_ONLY_SOURCE):
            original, _path = self._built(gcode)
            self.cache.save(self.identity, original)
            restored = self.cache.load(self.identity)
            self.assertIsNotNone(restored, "an arc-bearing index was cached lossily: %s" % gcode)
            for layer in range(len(original.ranges)):
                self.assertEqual(layer_polylines(restored, layer),
                                 layer_polylines(original, layer), (gcode, layer))

    def test_a_restored_compact_index_hydrates_to_the_full_geometry(self):
        # The restored compact index must not merely agree on a count of
        # descriptors: it hydrates its layers from the file and has to
        # come out drawing what a full scan draws, arcs included.
        gcode = ("M82\nG18\n"
                 ";LAYER:0\nG1 X1 Y2 Z3 E1\n"
                 ";LAYER:1\nG2 X0 Z10 I-10 K0 E2\n"
                 ";LAYER:2\nG1 X5 Y5 Z5 E3\n")
        compact, path = self._built(gcode, compact=True)
        self.cache.save(self.identity, compact)
        restored = self.cache.load(self.identity)
        self.assertIsNotNone(restored, "a compact arc-bearing index was not cached")
        full = build_index_from_file(path)
        self.assertEqual(restored.layer_start_arc_plane, full.layer_start_arc_plane)
        for layer in (1, 2):
            self.assertTrue(hydrate_layer_from_file(restored, path, layer, keep_anchor=layer),
                            "layer %d did not hydrate" % layer)
            self.assertEqual(restored.motion_arcs[layer], full.motion_arcs[layer])
            self.assertEqual(layer_polylines(restored, layer), layer_polylines(full, layer))

    def test_a_blob_written_before_arcs_were_indexed_is_refused(self):
        # A blob from the version before arcs draws every arc as its
        # chord. Accepting one would silently regress the geometry, so it
        # is refused and the file is read again.
        blob = self._blob(ARC_SOURCE)
        self.assertEqual(self._header_of(blob)["version"], 10)
        self._rewrite_header(blob, dict(self._header_of(blob), version=8))
        self.assertIsNone(self.cache.load(self.identity),
                          "a pre-arc cache blob was accepted as arc-aware")

    def test_a_corrupt_arc_column_is_refused_rather_than_trusted(self):
        blob = self._blob("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\n")
        for broken in (
            {"arcs": [[[99, 17, False, -10.0, 0.0]]]},       # a motion the layer lacks
            {"arcs": [[[1, 99, False, -10.0, 0.0]]]},        # not a plane
            {"arcs": [[[1, 17, False, 0.0, 0.0]]]},          # no radius at all
            {"arcs": [[[1, 17, True, float("inf"), 0.0]]]},  # not a number
            {"arcs": [[[1, 17]]]},                           # truncated entry
            {"start_arc_plane": [99]},                       # not a plane
            {"start_arc_plane": [17, 17]},                   # not one per layer
        ):
            self._rewrite_header(blob, dict(self._header_of(blob), **broken))
            self.assertIsNone(self.cache.load(self.identity),
                              "a corrupt arc column was trusted: %s" % sorted(broken))

    def test_a_g0_g1_file_pays_nothing_for_the_arc_columns(self):
        # The columns are absent from the header entirely, so a file with
        # no arcs writes the header it always did.
        blob = self._blob("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y0\nG1 X10 Y0 E1\nG1 X10 Y10 E2\n")
        with gzip.open(blob, "rb") as handle:
            raw = handle.read(65536)
        self.assertNotIn(b'"arcs"', raw)
        self.assertNotIn(b'"start_arc_plane"', raw)
        header = self._header_of(blob)
        self.assertNotIn("arcs", header)
        self.assertNotIn("start_arc_plane", header)

    def test_the_arc_column_stays_sparse_in_the_blob(self):
        body = "".join("G1 X%d Y50 E%d\n" % (step, step + 1) for step in range(200))
        blob = self._blob("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y0\n" + body
                          + "G2 X200 Y60 I0 J-10 E201\n")
        header = self._header_of(blob)
        self.assertEqual(header["arcs"], [[[201, 17, True, 0.0, -10.0]]])
        self.assertEqual(header["counts"], [202])
        self.assertEqual(header["start_arc_plane"], [17])


class ArcCompactHydrationTests(unittest.TestCase):
    """A hydrated layer parses its arcs from the plane the full scan
    recorded, so the two agree vertex for vertex."""

    def _hydrated(self, gcode: str, layer: int):
        path = _write(gcode)
        self.addCleanup(os.remove, path)
        compact = build_index_from_file(path, compact=True)
        self.assertTrue(hydrate_layer_from_file(compact, path, layer, keep_anchor=layer))
        return build_index_from_file(path), compact

    def test_a_hydrated_plane_arc_matches_the_full_scan(self):
        gcode = ("M82\nG18\n"
                 ";LAYER:0\nG1 X1 Y2 Z3 E1\n"
                 ";LAYER:1\nG2 X0 Z10 I-10 K0 E2\n"
                 ";LAYER:2\nG1 X5 Y5 Z5 E3\n")
        full, compact = self._hydrated(gcode, 1)
        self.assertEqual(compact.motion_arcs[1], full.motion_arcs[1])
        self.assertEqual(compact.motion_arcs[1], {0: (18, True, -10.0, 0.0)})
        self.assertEqual(compact.layer_start_arc_plane, full.layer_start_arc_plane)
        self.assertEqual(layer_polylines(compact, 1), layer_polylines(full, 1))

    def test_a_hydrated_g19_arc_matches_the_full_scan(self):
        gcode = ("M82\nG19\n"
                 ";LAYER:0\nG1 X1 Y2 Z3 E1\n"
                 ";LAYER:1\nG3 Y0 Z10 J-10 K0 E2\n")
        full, compact = self._hydrated(gcode, 1)
        self.assertEqual(compact.motion_arcs[1], full.motion_arcs[1])
        self.assertEqual(compact.motion_arcs[1], {0: (19, False, -10.0, 0.0)})
        self.assertEqual(layer_polylines(compact, 1), layer_polylines(full, 1))

    def test_a_plane_selected_inside_the_hydrated_layer_is_carried(self):
        # The plane word sits in the hydrated layer's own body, after the
        # seed: the layer's arcs must read the same both ways.
        gcode = ("M82\n"
                 ";LAYER:0\nG1 X1 Y2 Z3 E1\n"
                 ";LAYER:1\nG18\nG0 X20 Y0 Z0\nG3 X0 Z20 I-20 K0 E2\n")
        full, compact = self._hydrated(gcode, 1)
        self.assertEqual(compact.motion_arcs[1], full.motion_arcs[1])
        self.assertEqual(layer_polylines(compact, 1), layer_polylines(full, 1))

    def test_a_hydrated_inches_arc_matches_the_full_scan(self):
        # The units factor is applied by the compact scan and again by the
        # hydration parse: the hydrated layer must carry the same offsets.
        gcode = ("M82\nG20\n"
                 ";LAYER:0\n;TYPE:SKIN\nG0 X1 Y0\nG3 X0 Y1 I-1 J0 E1\n"
                 ";LAYER:1\nG1 X0.5 Y0.5 E2\n")
        full, compact = self._hydrated(gcode, 0)
        self.assertEqual(compact.motion_arcs[0], {1: (17, False, -25.4, 0.0)})
        self.assertEqual(compact.motion_arcs[0], full.motion_arcs[0])
        self.assertEqual(layer_polylines(compact, 0), layer_polylines(full, 0))

    def test_a_hydrated_arc_keeps_its_feature_and_travel_state(self):
        gcode = ("M83\n"
                 ";LAYER:0\n;TYPE:WALL-OUTER\nG1 X10 Y0 E1\n"
                 ";LAYER:1\n;TYPE:SKIN\nG0 X10 Y0\nG3 X0 Y10 I-10 J0 E1\nG1 X-10 Y10 E1\n")
        full, compact = self._hydrated(gcode, 1)
        self.assertEqual(compact.hydrated_layers, {1})
        self.assertEqual(layer_polylines(compact, 1), layer_polylines(full, 1))
        self.assertTrue(layer_polylines(compact, 1)["classes"]["SKIN"])


class ArcSimplificationTests(unittest.TestCase):
    """The budget's tolerance may never exceed the error the arcs were
    tessellated to: simplifying a curve back into a chord is the bug the
    arc geometry exists to remove."""

    @staticmethod
    def _circle():
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X50 Y0\n"
                       "G3 I-50 J0 E1\n")  # a full 50 mm circle
        return _chain(index, "SKIN")

    def test_the_drawn_payload_keeps_the_circle_it_was_given(self):
        chain = self._circle()
        self.assertGreater(len(chain), 100)
        self.assertLessEqual(_deviation(chain, 0.0, 0.0, 50.0), 1e-6)
        self.assertLessEqual(_chord_error(chain, 0.0, 0.0, 50.0),
                             ArcGeometry.MAX_SAGITTA_MM)

    def test_a_forced_simplification_stays_inside_the_sagitta(self):
        chain = self._circle()
        simplified = _budgeted([list(chain)], 20)[0]
        self.assertLess(len(simplified), len(chain), "the budget was not enforced at all")
        # The ceiling is what keeps this true: the raw budget tolerance on
        # this path is several millimetres, which flattens the circle into
        # a handful of chords the eye reads as a polygon.
        self.assertLessEqual(_chord_error(simplified, 0.0, 0.0, 50.0),
                             ArcGeometry.MAX_SAGITTA_MM + 1e-9,
                             "the simplification flattened the arc past its own tolerance")
        self.assertEqual(simplified[0][:2], chain[0][:2])
        self.assertEqual(simplified[-1][:2], chain[-1][:2])


class ArcIndexScaleTests(unittest.TestCase):
    """A file with no arcs is unchanged: no descriptors, no per-motion
    cost, and the whole geometry still drawn."""

    def test_a_long_straight_file_indexes_with_no_arcs(self):
        body = "".join("G1 X%d Y%d E%d\n" % (step, step % 50, step + 1) for step in range(5000))
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y0\n" + body)
        self.assertEqual(index.motion_count(0), 5001)
        self.assertEqual([len(arcs) for arcs in index.motion_arcs], [0])
        self.assertEqual(index.layer_start_arc_plane, [17])
        layer = layer_polylines(index, 0)
        self.assertEqual(layer["motions"], 5001)
        # Motion 1 is a zero-XY prime (X0 Y0), which moves nothing and so
        # contributes no vertex: the chain holds the opening position and
        # one endpoint per move that actually moved.
        self.assertEqual(len(layer["classes"]["SKIN"][0]), 5000)
        self.assertEqual(layer["travels"], [])

    def test_the_descriptors_are_keyed_by_motion_not_stored_per_motion(self):
        body = "".join("G1 X%d Y%d E%d\n" % (step, step % 50, step + 1) for step in range(5000))
        index = _index("M82\n;LAYER:0\n;TYPE:SKIN\nG0 X0 Y0\n" + body
                       + "G3 X0 Y0 I-5 J0 E5001\n")
        self.assertEqual(list(index.motion_arcs[0]), [5001])
        self.assertEqual(len(index.motion_arcs[0][5001]), 4)
        self.assertEqual(layer_polylines(index, 0)["motions"], 5002)


class ArcPayloadGuardTests(unittest.TestCase):
    """Shapes a hand-built index can produce: the arc columns may be
    absent, or name a motion the layer does not have, and neither may
    raise."""

    def test_a_layer_without_a_z_column_ignores_its_descriptors(self):
        index = LayerMotionIndex(
            ranges=[(0, 100)],
            motion_offsets=[array("Q", [10, 20])],
            motion_x=[array("f", [10.0, 0.0])],
            motion_y=[array("f", [0.0, 10.0])],
            motion_z=[],
            motion_arcs=[{1: (17, False, -10.0, 0.0)}],
            layer_start_positions=[(0.0, 0.0, 0.0)],
            layer_start_extruding=[True],
        )
        self.assertEqual(layer_polylines(index, 0),
                         {"classes": {"unknown": [[[0.0, 0.0, 0.0], [10.0, 0.0, 0.0],
                                                   [0.0, 10.0, 1.0]]]},
                          "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2})

    def test_descriptors_for_motions_the_layer_lacks_are_ignored(self):
        index = LayerMotionIndex(
            ranges=[(0, 100)],
            motion_offsets=[array("Q", [10, 20])],
            motion_x=[array("f", [10.0, 0.0])],
            motion_y=[array("f", [0.0, 10.0])],
            motion_z=[array("f", [0.0, 0.0])],
            motion_arcs=[{7: (17, False, -10.0, 0.0)}],
            layer_start_positions=[(0.0, 0.0, 0.0)],
            layer_start_extruding=[True],
        )
        self.assertEqual(len(next(motion_edges(index, 0))), 7)
        self.assertEqual([list(vertex[:2])
                          for vertex in layer_polylines(index, 0)["classes"]["unknown"][0]],
                         [[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]])


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the plate visit suite")
class ArcPrintedObjectTests(unittest.TestCase):
    """Printed-object visitation follows the material: an extrusion arc
    that reaches a polygon marks it, the same arc as a travel does not."""

    POLYGON = [[10.0, 20.0], [60.0, 20.0], [60.0, 40.0], [10.0, 40.0]]
    ROWS = [{"name": "Widget", "polygon": POLYGON}]

    def setUp(self):
        class Files(QObject):
            changed = pyqtSignal()

        self.files = Files()
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.service = self.qt.load("GCodeIndexService").GCodeIndexService(self.files, object())
        self.addCleanup(self.service.close)
        self.job = ("arcs.gcode", 100, 1)
        self.service.bind(self.job)

    def _bind(self, gcode: str):
        path = _write(gcode)
        self.addCleanup(os.remove, path)
        index = build_index_from_file(path)
        view = self.qt.load("GCodeIndexService").IndexView(self.job, index)
        self.service._view = view
        return index

    # A 25 mm arc whose chord rides above the polygon: both endpoints and
    # the whole straight line between them sit outside y = 20 .. 40, and
    # only the curve dips through it.
    CLIPPING = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
                "G0 X10 Y55\n"
                "G3 X60 Y55 I25 J0 E1\n")

    def test_an_extrusion_arc_that_only_dips_into_the_polygon_marks_it(self):
        index = self._bind(self.CLIPPING)
        split = index.motion_count(0)
        self.assertEqual(split, 2)
        self.assertEqual(self.service.plate_visited(0, split, self.ROWS), frozenset({"Widget"}))

    def test_the_same_arc_as_a_travel_deposits_nothing(self):
        # The same curve with no E: it reaches into the polygon, but it
        # deposits nothing, so the object is not printed.
        index = self._bind(self.CLIPPING.replace(" E1", ""))
        split = index.motion_count(0)
        self.assertEqual(split, 2)
        self.assertEqual(self.service.plate_visited(0, split, self.ROWS), frozenset())

    # A layer that opens mid-travel: its first extrusion does not start
    # until motion 2, so the two travels before it — the second of which
    # runs straight through the polygon — deposit nothing.
    CROSSING = ("M83\n"
                ";LAYER:0\n;TYPE:SKIN\n"
                "G1 X5 Y5 E1\n"
                "G1 X5 Y50\n"
                ";LAYER:1\n;TYPE:SKIN\n"
                "G1 X30 Y30\n"
                "G1 X80 Y30\n"
                "G1 X90 Y60 E1.5\n"
                "G1 X95 Y60 E0.5\n"
                "G1 X40 Y30 E1\n"
                "G1 X45 Y25 E0.5\n")

    def test_a_cross_layer_travel_is_never_read_as_material(self):
        # The cursor polls: after the first poll the walk resumes at a
        # non-zero motion, which must not turn the opening travels of a
        # mid-travel layer into extrusions and mark the polygon.
        index = self._bind(self.CROSSING)
        self.assertFalse(index.layer_start_extruding[1],
                         "the fixture no longer opens layer 1 mid-travel")
        self.assertEqual(index.travel_starts[1], [])
        self.assertEqual(index.travel_ends[1], [2])
        self.assertEqual(self.service.plate_visited(1, 1, self.ROWS), frozenset(),
                         "the opening travel marked the polygon")
        self.assertEqual(self.service.plate_visited(1, 4, self.ROWS), frozenset(),
                         "the travel crossing the polygon marked it")
        # The extrusion that does cross it still marks, from the same
        # resuming cursor: the fix must not blind the walk.
        self.assertEqual(self.service.plate_visited(1, 5, self.ROWS), frozenset({"Widget"}))


if __name__ == "__main__":
    unittest.main()
