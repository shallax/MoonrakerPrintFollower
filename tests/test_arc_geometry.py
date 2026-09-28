"""The commanded path of one arc motion: the plane it turns in, the
direction and the length of its sweep, the helix along the third axis,
and the live position measured against it.

The cases describe geometry — the radius a point sits on, the side of a
diameter a sweep keeps, the quadrant a wrap-around crosses, the axis a
helix interpolates, the error one generated subedge is allowed — so a
change in how finely a curve is cut into points never reads as a
failure here. The other half of the contract is the descriptor: a form
Klipper would refuse has to read as no arc at all, so a malformed line
degrades to the straight edge it would have been.
"""
from __future__ import annotations

from math import atan2, cos, hypot, inf, nan, pi, radians, sin
import unittest

from plugins.ArcGeometry import (
    MAX_SAGITTA_MM,
    MAX_SEGMENT_MM,
    MAX_SEGMENTS,
    PLANES,
    PLANE_XY,
    PLANE_XZ,
    PLANE_YZ,
    closest,
    degenerate,
    descriptor,
    point_at,
    subdivisions,
    tessellate,
)

_TWO_PI = 2.0 * pi
_X, _Y, _Z = 0, 1, 2

# plane -> the two centre-offset words Klipper reads for it.
_OFFSET_WORDS = {
    PLANE_XY: ("I", "J"),
    PLANE_XZ: ("I", "K"),
    PLANE_YZ: ("J", "K"),
}


def _arc(plane, offset_a, offset_b, clockwise=False):
    """The descriptor of a *plane* arc whose centre sits *offset_a* and
    *offset_b* from the start, along that plane's own two axes."""
    word_a, word_b = _OFFSET_WORDS[plane]
    return descriptor(plane, clockwise, {word_a: offset_a, word_b: offset_b})


def _radius_from(point, centre_a, centre_b, axis_a=_X, axis_b=_Y):
    """The point's distance from the centre, inside its own plane."""
    return hypot(point[axis_a] - centre_a, point[axis_b] - centre_b)


def _sagitta(radius, first, second, centre_a=0.0, centre_b=0.0):
    """A chord's true height above its circle in XY: R * (1 - cos(theta / 2)).

    The angle is taken from the centre, so the height is the real
    deviation of a drawn subedge from the commanded curve rather than a
    difference between two radii.
    """
    angle_a = atan2(first[_Y] - centre_b, first[_X] - centre_a)
    angle_b = atan2(second[_Y] - centre_b, second[_X] - centre_a)
    span = abs((angle_b - angle_a + pi) % _TWO_PI - pi)
    return radius * (1.0 - cos(span / 2.0))


class DescriptorTests(unittest.TestCase):
    """The sparse descriptor is all an arc leaves behind, so it has to
    carry the plane and both offsets exactly, and answer None for every
    form Klipper would reject."""

    def test_the_three_g_code_planes_are_the_only_planes_accepted(self):
        self.assertEqual((PLANE_XY, PLANE_XZ, PLANE_YZ), (17, 18, 19),
                         "the modal plane codes moved away from G17/G18/G19")
        self.assertEqual(PLANES, (PLANE_XY, PLANE_XZ, PLANE_YZ))
        for plane in PLANES:
            desc = _arc(plane, -10.0, 0.0)
            self.assertIsNotNone(desc, "plane %s was not accepted as an arc plane" % plane)
            self.assertEqual(desc[0], plane, "the descriptor lost the plane it was built for")
            self.assertEqual(desc[2:], (-10.0, 0.0), "the descriptor lost the offsets it was built from")

    def test_a_missing_centre_offset_word_counts_as_zero(self):
        # Every plane has two applicable words and a line may write only
        # one of them: the absent one is a zero offset, not a rejection.
        for plane in PLANES:
            word_a, word_b = _OFFSET_WORDS[plane]
            both = _arc(plane, -10.0, 0.0)
            self.assertEqual(descriptor(plane, False, {word_a: -10.0}), both,
                             "a %s arc that wrote only %s lost its centre" % (plane, word_a))
            self.assertEqual(descriptor(plane, False, {word_b: -10.0}), _arc(plane, 0.0, -10.0),
                             "a %s arc that wrote only %s lost its centre" % (plane, word_b))

    def test_every_refused_form_reads_as_no_arc(self):
        # Each refusal is Klipper's own rule: no centre to turn about, no
        # relative arc moves, no radius form, no fourth plane.
        refused = (
            ("a pair of zero offsets leaves no centre to turn about",
             _arc(PLANE_XY, 0.0, 0.0)),
            ("a line that wrote no offset word at all leaves no centre either",
             descriptor(PLANE_XY, False, {})),
            ("the R form carries a radius, not the centre Klipper needs",
             descriptor(PLANE_XY, False, {"I": -10.0, "R": 10.0})),
            ("G91 forbids relative arc moves outright",
             descriptor(PLANE_XY, False, {"I": -10.0}, absolute_xyz=False)),
            ("plane 99 is not one of the three modal planes",
             descriptor(99, False, {"I": -10.0, "J": 0.0})),
            ("a NaN offset is not a centre",
             descriptor(PLANE_XY, False, {"I": nan, "J": 0.0})),
            ("an infinite offset is not a centre",
             descriptor(PLANE_XY, False, {"I": inf, "J": 0.0})),
        )
        for message, desc in refused:
            self.assertIsNone(desc, message)

    def test_a_small_real_offset_is_still_an_arc(self):
        # A thousandth of a millimetre is a real turn: an epsilon wide
        # enough to swallow it would erase the arc rather than coarsen it.
        self.assertEqual(_arc(PLANE_XY, -0.001, 0.0), (PLANE_XY, False, -0.001, 0.0),
                         "a 0.001 mm offset was refused as if the centre were absent")
        self.assertFalse(degenerate(0.001, 0.0))
        self.assertFalse(degenerate(0.0, -0.001))
        # Numerical zero is both offsets together, never either one alone.
        self.assertTrue(degenerate(0.0, 0.0))
        self.assertTrue(degenerate(1.0e-9, -1.0e-9))


class SweepTests(unittest.TestCase):
    """One commanded sweep: the circle it turns on, the way round it
    travels, and what the third axis does while it turns."""

    def assertOnCircle(self, points, radius, centre_a=0.0, centre_b=0.0, axis_a=_X, axis_b=_Y):
        """Every generated point lies *radius* from the centre, measured
        inside the arc's own plane."""
        self.assertTrue(points, "the arc generated no points at all")
        for point in points:
            self.assertAlmostEqual(
                _radius_from(point, centre_a, centre_b, axis_a, axis_b), radius, places=6,
                msg="a generated point left the commanded circle: %s" % (point,))

    def assertNoPointBelow(self, points, axis, floor, description):
        """No generated point has *axis* below *floor*."""
        lowest = min(point[axis] for point in points)
        self.assertGreaterEqual(lowest, floor, "%s (lowest %s)" % (description, lowest))

    def assertNoPointAbove(self, points, axis, ceiling, description):
        """No generated point has *axis* above *ceiling*."""
        highest = max(point[axis] for point in points)
        self.assertLessEqual(highest, ceiling, "%s (highest %s)" % (description, highest))

    def assertSomePointBelow(self, points, axis, limit, description):
        """Some generated point has *axis* below *limit*."""
        lowest = min(point[axis] for point in points)
        self.assertLess(lowest, limit, "%s (lowest %s)" % (description, lowest))

    def assertSomePointAbove(self, points, axis, limit, description):
        """Some generated point has *axis* above *limit*."""
        highest = max(point[axis] for point in points)
        self.assertGreater(highest, limit, "%s (highest %s)" % (description, highest))

    def test_a_counter_clockwise_quarter_circle_runs_along_its_arc(self):
        start, target = (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)
        points = tessellate(_arc(PLANE_XY, -10.0, 0.0), start, target)
        self.assertOnCircle(points, 10.0)
        self.assertNoPointBelow(points, _X, 0.0, "the counter-clockwise quarter crossed to negative x")
        self.assertNoPointBelow(points, _Y, 0.0, "the counter-clockwise quarter crossed to negative y")
        self.assertEqual(points[-1], (0.0, 10.0, 0.0), "the quarter did not end at its commanded target")

    def test_the_clockwise_quarter_circle_leaves_along_the_short_side(self):
        start, target = (0.0, 10.0, 0.0), (10.0, 0.0, 0.0)
        points = tessellate(_arc(PLANE_XY, 0.0, -10.0, clockwise=True), start, target)
        self.assertOnCircle(points, 10.0)
        self.assertNoPointBelow(points, _X, 0.0, "the clockwise quarter crossed to negative x")
        self.assertNoPointBelow(points, _Y, 0.0, "the clockwise quarter crossed to negative y")
        # The first sample sits a few degrees along the arc, not across
        # the diameter: it leaves the top of the circle toward (10, 0).
        self.assertAlmostEqual(points[0][_X], 0.98, delta=0.3,
                               msg="the clockwise quarter's first point is %s" % (points[0],))
        self.assertAlmostEqual(points[0][_Y], 9.95, delta=0.3,
                               msg="the clockwise quarter's first point is %s" % (points[0],))
        self.assertEqual(points[-1], (10.0, 0.0, 0.0), "the quarter did not end at its commanded target")

    def test_a_clockwise_quarter_the_other_way_round_takes_the_long_way(self):
        start, target = (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)
        long_way = tessellate(_arc(PLANE_XY, -10.0, 0.0, clockwise=True), start, target)
        short_way = tessellate(_arc(PLANE_XY, -10.0, 0.0), start, target)
        self.assertOnCircle(long_way, 10.0)
        self.assertOnCircle(short_way, 10.0)
        # The clockwise turn between these two points is the 270-degree
        # one, so it crosses the bottom and the left of the circle.
        self.assertSomePointBelow(long_way, _Y, -9.0, "the clockwise quarter never crossed the bottom")
        self.assertSomePointBelow(long_way, _X, -9.0, "the clockwise quarter never crossed the left")
        self.assertNoPointBelow(short_way, _Y, -0.01, "the counter-clockwise quarter dipped below the chord")
        self.assertEqual(long_way[-1], (0.0, 10.0, 0.0), "the clockwise quarter missed its target")
        self.assertEqual(short_way[-1], (0.0, 10.0, 0.0), "the counter-clockwise quarter missed its target")

    def test_a_semicircle_keeps_its_own_side_of_the_diameter(self):
        start, target = (10.0, 0.0, 0.0), (-10.0, 0.0, 0.0)
        counter = tessellate(_arc(PLANE_XY, -10.0, 0.0), start, target)
        clockwise = tessellate(_arc(PLANE_XY, -10.0, 0.0, clockwise=True), start, target)
        self.assertOnCircle(counter, 10.0)
        self.assertOnCircle(clockwise, 10.0)
        self.assertNoPointBelow(counter, _Y, -1.0e-9, "the counter-clockwise semicircle crossed its diameter")
        self.assertNoPointAbove(clockwise, _Y, 1.0e-9, "the clockwise semicircle crossed its diameter")
        self.assertSomePointAbove(counter, _Y, 9.0, "the counter-clockwise semicircle never reached the top")
        self.assertSomePointBelow(clockwise, _Y, -9.0, "the clockwise semicircle never reached the bottom")
        self.assertEqual(counter[-1], (-10.0, 0.0, 0.0), "the counter-clockwise semicircle missed its target")
        self.assertEqual(clockwise[-1], (-10.0, 0.0, 0.0), "the clockwise semicircle missed its target")

    def test_a_full_counter_clockwise_circle_returns_to_its_start(self):
        start = (10.0, 0.0, 0.0)
        points = tessellate(_arc(PLANE_XY, -10.0, 0.0), start, start)
        # A target equal to the start is a whole turn, never a no-op and
        # never a dot: the path crosses both the bottom and the top.
        self.assertSomePointBelow(points, _Y, -9.0, "the full circle never reached the bottom")
        self.assertSomePointAbove(points, _Y, 9.0, "the full circle never reached the top")
        self.assertOnCircle(points, 10.0)
        self.assertEqual(points[-1], start, "the full circle did not end on its own start")

    def test_a_full_clockwise_circle_runs_the_other_way(self):
        start = (10.0, 0.0, 0.0)
        points = tessellate(_arc(PLANE_XY, -10.0, 0.0, clockwise=True), start, start)
        # The first step leaves the start downward, which is the way the
        # clockwise turn goes; it must not set off over the top.
        self.assertLess(points[0][_Y], 0.0, "the full clockwise circle left its start upward: %s" % (points[0],))
        self.assertSomePointBelow(points, _Y, -9.0, "the full clockwise circle never reached the bottom")
        self.assertOnCircle(points, 10.0)
        self.assertEqual(points[-1], start, "the full clockwise circle did not end on its own start")

    def test_a_sweep_across_zero_degrees_goes_the_directed_way(self):
        # A start at 350 degrees and a target at 10 on a 10 mm circle: the
        # counter-clockwise sweep is the twenty degrees through zero, the
        # clockwise one the other 340 degrees, through the bottom.
        start = (10.0 * cos(radians(350.0)), 10.0 * sin(radians(350.0)), 0.0)
        target = (10.0 * cos(radians(10.0)), 10.0 * sin(radians(10.0)), 0.0)
        counter = tessellate(_arc(PLANE_XY, -start[_X], -start[_Y]), start, target)
        clockwise = tessellate(_arc(PLANE_XY, -start[_X], -start[_Y], clockwise=True), start, target)
        self.assertNoPointBelow(counter, _Y, -1.8,
                                "the counter-clockwise twenty-degree sweep dipped toward the bottom")
        self.assertSomePointBelow(clockwise, _Y, -9.9,
                                  "the clockwise sweep from 350 to 10 degrees missed the bottom")
        self.assertEqual(counter[-1], target, "the twenty-degree sweep missed its target")
        self.assertEqual(clockwise[-1], target, "the long clockwise sweep missed its target")

    def test_a_sweep_across_a_half_turn_passes_the_bottom(self):
        start, target = (-10.0, 0.0, 0.0), (10.0, 0.0, 0.0)
        points = tessellate(_arc(PLANE_XY, 10.0, 0.0), start, target)
        # From 180 degrees the counter-clockwise sweep runs through 270:
        # under the bottom of the circle, never back over the top.
        self.assertSomePointBelow(points, _Y, -9.9, "the sweep never passed through (0, -10)")
        self.assertNoPointAbove(points, _Y, 9.9, "the sweep passed over the top instead of under the bottom")
        self.assertEqual(points[-1], (10.0, 0.0, 0.0), "the half turn did not end at its commanded target")

    def test_a_g17_helix_climbs_z_along_the_sweep(self):
        start, target = (10.0, 0.0, 0.0), (0.0, 10.0, 5.0)
        points = tessellate(_arc(PLANE_XY, -10.0, 0.0), start, target)
        heights = [point[_Z] for point in points]
        for before, after in zip(heights, heights[1:], strict=False):
            self.assertLess(before, after, "the helix's Z fell back between %s and %s" % (before, after))
        self.assertGreater(heights[0], 0.0, "the helix gained its whole Z climb in the first step")
        self.assertLess(heights[0], 1.0, "the first helix step already climbed %s mm of Z" % heights[0])
        self.assertEqual(heights[-1], 5.0, "the helix did not finish at the commanded Z")
        # The climb never bends the turn: the XY part is still the quarter
        # circle about the centre the offsets named.
        self.assertOnCircle(points, 10.0)
        self.assertNoPointBelow(points, _X, 0.0, "the helix's XY part crossed to negative x")
        self.assertNoPointBelow(points, _Y, 0.0, "the helix's XY part crossed to negative y")

    def test_a_g18_arc_turns_in_the_xz_plane(self):
        start, target = (10.0, 3.0, 0.0), (0.0, 3.0, 10.0)
        points = tessellate(_arc(PLANE_XZ, -10.0, 0.0), start, target)
        # G18's offsets sit in X and K: the centre is in XZ, and Y is a
        # constant of the motion.
        self.assertOnCircle(points, 10.0, axis_a=_X, axis_b=_Z)
        for point in points:
            self.assertEqual(point[_Y], 3.0, "the G18 arc moved Y off its plane: %s" % (point,))
        self.assertNoPointBelow(points, _X, 0.0, "the G18 quarter crossed to negative x")
        self.assertNoPointBelow(points, _Z, 0.0, "the G18 quarter crossed to negative z")
        self.assertEqual(points[-1], (0.0, 3.0, 10.0), "the G18 quarter did not end at its commanded target")

    def test_a_g18_helix_climbs_y_along_the_sweep(self):
        start, target = (10.0, 0.0, 0.0), (0.0, 7.0, 10.0)
        points = tessellate(_arc(PLANE_XZ, -10.0, 0.0), start, target)
        # XZ leaves Y as the third axis, so Y is what interpolates.
        heights = [point[_Y] for point in points]
        for before, after in zip(heights, heights[1:], strict=False):
            self.assertLess(before, after, "the G18 helix's Y fell back between %s and %s" % (before, after))
        self.assertGreater(heights[0], 0.0, "the G18 helix gained its whole Y travel in the first step")
        self.assertEqual(heights[-1], 7.0, "the G18 helix did not finish at the commanded Y")
        self.assertOnCircle(points, 10.0, axis_a=_X, axis_b=_Z)
        self.assertNoPointBelow(points, _X, 0.0, "the G18 helix's XZ part crossed to negative x")
        self.assertNoPointBelow(points, _Z, 0.0, "the G18 helix's XZ part crossed to negative z")

    def test_a_g19_arc_turns_in_the_yz_plane(self):
        start, target = (1.0, 10.0, 0.0), (1.0, 0.0, 10.0)
        points = tessellate(_arc(PLANE_YZ, -10.0, 0.0), start, target)
        # G19's offsets sit in J and K: the centre is in YZ, and X is a
        # constant of the motion.
        self.assertOnCircle(points, 10.0, axis_a=_Y, axis_b=_Z)
        for point in points:
            self.assertEqual(point[_X], 1.0, "the G19 arc moved X off its plane: %s" % (point,))
        self.assertNoPointBelow(points, _Y, 0.0, "the G19 quarter crossed to negative y")
        self.assertNoPointBelow(points, _Z, 0.0, "the G19 quarter crossed to negative z")
        self.assertEqual(points[-1], (1.0, 0.0, 10.0), "the G19 quarter did not end at its commanded target")

    def test_a_g19_helix_climbs_x_along_the_sweep(self):
        start, target = (0.0, 10.0, 0.0), (5.0, 0.0, 10.0)
        points = tessellate(_arc(PLANE_YZ, -10.0, 0.0), start, target)
        # YZ leaves X as the third axis, so X is what interpolates.
        heights = [point[_X] for point in points]
        for before, after in zip(heights, heights[1:], strict=False):
            self.assertLess(before, after, "the G19 helix's X fell back between %s and %s" % (before, after))
        self.assertGreater(heights[0], 0.0, "the G19 helix gained its whole X travel in the first step")
        self.assertEqual(heights[-1], 5.0, "the G19 helix did not finish at the commanded X")
        self.assertOnCircle(points, 10.0, axis_a=_Y, axis_b=_Z)
        self.assertNoPointBelow(points, _Y, 0.0, "the G19 helix's YZ part crossed to negative y")
        self.assertNoPointBelow(points, _Z, 0.0, "the G19 helix's YZ part crossed to negative z")

    def test_the_last_generated_point_is_the_target_itself(self):
        # The endpoint is the stored position, not the last trig step: the
        # next motion's edge has to begin exactly where this one ended.
        # The awkward decimals put the two in different low bits, which is
        # where an equality check earns its keep.
        radius = 10.123456789
        quarter = _arc(PLANE_XY, -radius, 0.0)
        cases = (
            (quarter, (radius, 0.0, 0.0), (0.0, radius, 0.0)),
            (quarter, (radius, 0.0, 0.0), (-radius, 0.0, 0.0)),
            (quarter, (radius, 0.0, 0.0), (radius, 0.0, 0.0)),
            (quarter, (radius, 0.0, 0.123456789), (0.0, radius, 5.123456789)),
        )
        for desc, start, target in cases:
            points = tessellate(desc, start, target)
            self.assertEqual(points[-1], (float(target[0]), float(target[1]), float(target[2])),
                             "the arc ended at %s rather than at the commanded %s" % (points[-1], target))


class SubdivisionBudgetTests(unittest.TestCase):
    """The tessellation budget: a subedge's chord error and its length
    are both bounded whatever the radius, and the count stays finite even
    for a line damaged past any real arc."""

    def assertWithinBudget(self, desc, start, target, radius):
        """Every subedge of the sweep is inside both tolerances."""
        points = [tuple(float(value) for value in start)] + tessellate(desc, start, target)
        for first, second in zip(points, points[1:], strict=False):
            height = _sagitta(radius, first, second)
            self.assertLessEqual(height, MAX_SAGITTA_MM + 1.0e-9,
                                 "a subedge stood %s mm off the commanded circle" % height)
            length = hypot(hypot(second[_X] - first[_X], second[_Y] - first[_Y]), second[_Z] - first[_Z])
            self.assertLessEqual(length, MAX_SEGMENT_MM + 1.0e-6,
                                 "a subedge was %s mm long, past the one-millimetre cap" % length)

    def test_every_subedge_respects_the_sagitta_and_length_budgets(self):
        radius = 25.0
        start = (radius, 0.0, 0.0)
        quarter = _arc(PLANE_XY, -radius, 0.0)
        self.assertWithinBudget(quarter, start, (0.0, radius, 0.0), radius)
        self.assertWithinBudget(quarter, start, start, radius)
        # The same circle with a Z climb: the length cap has to count the
        # helical travel, not only the circular part of the subedge.
        self.assertWithinBudget(quarter, start, (radius, 0.0, 30.0), radius)

    def test_the_subdivision_count_stays_bounded_and_sane(self):
        start, target = (10.0, 0.0, 0.0), (0.0, 10.0, 0.0)
        count = subdivisions(_arc(PLANE_XY, -10.0, 0.0), start, target)
        self.assertGreaterEqual(count, 4, "a 10 mm quarter circle was cut into %s subedges" % count)
        self.assertLessEqual(count, 64, "a 10 mm quarter circle was cut into %s subedges" % count)
        # A tiny arc is nearly straight: one subedge, never a spray of
        # points that the payload then has to carry.
        tiny_start = (0.02, 0.0, 0.0)
        tiny_target = (0.02 * cos(radians(5.0)), 0.02 * sin(radians(5.0)), 0.0)
        self.assertEqual(subdivisions(_arc(PLANE_XY, -0.02, 0.0), tiny_start, tiny_target), 1,
                         "a 0.02 mm arc of five degrees was cut into several subedges")
        # A radius no budget can afford stops at the cap instead of asking
        # for an unbounded list.
        huge = _arc(PLANE_XY, -1.0e6, 0.0)
        start = target = (1.0e6, 0.0, 0.0)
        self.assertEqual(subdivisions(huge, start, target, max_segments=8), 8,
                         "a 1e6 mm full circle ignored the cap it was handed")
        self.assertEqual(subdivisions(huge, start, target), MAX_SEGMENTS,
                         "a 1e6 mm full circle did not stop at the segment cap")

    def test_a_broken_arc_never_raises_out_of_the_count(self):
        # A damaged line reaches the count as infinities and NaNs, and the
        # answer still has to be a bounded number of subedges rather than
        # an exception thrown into the parse or the payload build.
        incomplete = _arc(PLANE_XY, -10.0, 0.0)
        infinite = subdivisions(incomplete, (inf, 0.0, 0.0), (0.0, 10.0, 0.0))
        self.assertGreaterEqual(infinite, 1, "an infinite start asked for %s subedges" % infinite)
        self.assertLessEqual(infinite, MAX_SEGMENTS, "an infinite start asked for %s subedges" % infinite)
        nan_target = subdivisions(incomplete, (10.0, 0.0, 0.0), (nan, 10.0, 0.0))
        self.assertGreaterEqual(nan_target, 1, "a NaN target asked for %s subedges" % nan_target)
        self.assertLessEqual(nan_target, MAX_SEGMENTS, "a NaN target asked for %s subedges" % nan_target)


class FullCircleSeamTests(unittest.TestCase):
    """A full turn returns to its own planar angle, so the angle alone
    cannot say whether a sample at the seam is the start or the end. The
    helix separates the two in the third axis, and the ends are measured
    as candidates instead of being assumed away."""

    @staticmethod
    def _fixture(plane, clockwise=False):
        """A radius-10 full circle in *plane*, climbing its own third
        axis, as ``(descriptor, start, target)``."""
        if plane == PLANE_XY:      # sweeps XY, climbs Z
            return (_arc(plane, -10.0, 0.0, clockwise), (10.0, 0.0, 0.0), (10.0, 0.0, 5.0))
        if plane == PLANE_XZ:      # sweeps XZ, climbs Y
            return (_arc(plane, -10.0, 0.0, clockwise), (10.0, 0.0, 0.0), (10.0, 5.0, 0.0))
        # PLANE_YZ sweeps YZ and climbs X.
        return (_arc(plane, -10.0, 0.0, clockwise), (0.0, 10.0, 0.0), (5.0, 10.0, 0.0))

    def test_each_plane_and_direction_measures_its_own_ends_exactly(self):
        for plane in PLANES:
            for clockwise in (False, True):
                with self.subTest(plane=plane, clockwise=clockwise):
                    desc, start, target = self._fixture(plane, clockwise)
                    distance, t = closest(desc, start, target, target)
                    self.assertAlmostEqual(distance, 0.0, places=6,
                                           msg="the full turn's end measured %s mm off" % distance)
                    self.assertEqual(t, 1.0, "the full turn's end read at %s of the sweep" % t)
                    distance, t = closest(desc, start, target, start)
                    self.assertAlmostEqual(distance, 0.0, places=6,
                                           msg="the full turn's start measured %s mm off" % distance)
                    self.assertEqual(t, 0.0, "the full turn's start read at %s of the sweep" % t)

    def test_the_helix_seam_resolves_to_its_own_side(self):
        # A sample a thousandth of the sweep from an end: the planar
        # angle puts it on the correct side of the seam, and the helix
        # keeps it there.
        desc, start, target = self._fixture(PLANE_XY)
        for fraction, near in ((0.999, lambda t: self.assertGreater(t, 0.99)),
                               (0.001, lambda t: self.assertLess(t, 0.01))):
            distance, t = closest(desc, start, target, point_at(desc, start, target, fraction))
            self.assertAlmostEqual(distance, 0.0, places=6,
                                   msg="the sample at %s of the sweep measured %s mm off"
                                   % (fraction, distance))
            near(t)

    def test_the_full_turns_midpoint_reads_at_half_the_sweep(self):
        for plane in PLANES:
            desc, start, target = self._fixture(plane)
            middle = point_at(desc, start, target, 0.5)
            distance, t = closest(desc, start, target, middle)
            self.assertAlmostEqual(distance, 0.0, places=6,
                                   msg="plane %s: the mid-sweep point measured %s mm off"
                                   % (plane, distance))
            self.assertAlmostEqual(t, 0.5, places=4,
                                   msg="plane %s: the mid-sweep point read at %s" % (plane, t))

    def test_a_planar_full_circle_ties_deterministically_at_its_seam(self):
        # Start and target are the same XYZ point: no sample can tell
        # them apart, so the tie resolves the same way every time.
        desc = _arc(PLANE_XY, -10.0, 0.0)
        start = (10.0, 0.0, 0.0)
        distance, t = closest(desc, start, start, start)
        self.assertEqual(distance, 0.0)
        self.assertEqual(t, 0.0)

    def test_a_clamped_end_still_wins_when_it_is_the_closer_candidate(self):
        # Beyond the end, off the seam: the end stays the answer.
        desc, start, target = self._fixture(PLANE_XY)
        distance, t = closest(desc, start, target, (10.0, 0.0, 6.0))
        self.assertEqual(t, 1.0)
        self.assertAlmostEqual(distance, 1.0, places=6)


class ClosestPointTests(unittest.TestCase):
    """A position measured against the commanded sweep: how far it sits
    from the path, and where along the sweep that nearest point is."""

    START = (10.0, 0.0, 0.0)
    TARGET = (0.0, 10.0, 0.0)

    def setUp(self):
        self.quarter = _arc(PLANE_XY, -10.0, 0.0)

    def test_a_position_on_the_arc_measures_no_distance_at_its_own_fraction(self):
        middle = (10.0 * cos(radians(45.0)), 10.0 * sin(radians(45.0)), 0.0)
        distance, t = closest(self.quarter, self.START, self.TARGET, middle)
        self.assertAlmostEqual(distance, 0.0, places=6,
                               msg="a position sitting on the arc measured %s mm off it" % distance)
        self.assertAlmostEqual(t, 0.5, places=4,
                               msg="the mid-sweep position read at %s of the arc" % t)

    def test_a_position_at_the_centre_measures_one_radius(self):
        distance, t = closest(self.quarter, self.START, self.TARGET, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(distance, 10.0, places=6,
                               msg="the centre measured %s mm from the arc, not its radius" % distance)
        # Every point of the sweep is a radius away, so the nearest one is
        # the start and the fraction is the start's.
        self.assertEqual(t, 0.0, "the centre read at %s of the arc" % t)

    def test_a_position_beyond_either_end_measures_to_that_end(self):
        # The sweep is clamped: a sample beside the arc but past an end is
        # measured to that end, never to the far side of the circle.
        before, t_before = closest(self.quarter, self.START, self.TARGET, (11.0, -1.0, 0.0))
        self.assertEqual(t_before, 0.0, "a position beside the start read at %s of the arc" % t_before)
        self.assertAlmostEqual(before, hypot(1.0, 1.0), places=6,
                               msg="the distance was %s, not the distance to the start" % before)
        after, t_after = closest(self.quarter, self.START, self.TARGET, (-1.0, 11.0, 0.0))
        self.assertEqual(t_after, 1.0, "a position beside the target read at %s of the arc" % t_after)
        self.assertAlmostEqual(after, hypot(1.0, 1.0), places=6,
                               msg="the distance was %s, not the distance to the target" % after)

    def test_the_fraction_walks_forward_as_the_position_walks_the_arc(self):
        readings = []
        for degrees in range(-10, 101, 5):
            position = (10.0 * cos(radians(degrees)), 10.0 * sin(radians(degrees)), 0.0)
            _distance, t = closest(self.quarter, self.START, self.TARGET, position)
            readings.append((degrees, t))
        for (before, t_before), (after, t_after) in zip(readings, readings[1:], strict=False):
            self.assertLessEqual(t_before, t_after,
                                 "the reading at %s degrees (%s) came after the one at %s degrees (%s)"
                                 % (before, t_before, after, t_after))
        # Clamped at both ends of the walk: nothing before the start, the
        # target itself past the end.
        self.assertEqual(readings[0][1], 0.0, "the walk did not start clamped at the start")
        self.assertEqual(readings[-1][1], 1.0, "the walk did not end clamped at the target")

    def test_a_descriptor_without_a_radius_measures_nothing(self):
        # Hand-built, because the index never stores one: an arc with no
        # radius has no path to measure against, and the answer has to be
        # None rather than a division by a radius of nothing.
        empty = (PLANE_XY, False, 0.0, 0.0)
        self.assertIsNone(closest(empty, self.START, self.TARGET, (1.0, 1.0, 0.0)),
                          "an arc with no radius measured a distance anyway")

    def test_point_at_hands_back_the_exact_endpoints(self):
        middle = point_at(self.quarter, self.START, self.TARGET, 0.5)
        self.assertAlmostEqual(middle[_X], 10.0 * cos(radians(45.0)), places=6,
                               msg="the quarter's mid-sweep point is %s" % (middle,))
        self.assertAlmostEqual(middle[_Y], 10.0 * sin(radians(45.0)), places=6,
                               msg="the quarter's mid-sweep point is %s" % (middle,))
        self.assertEqual(middle[_Z], 0.0, "the quarter's mid-sweep point left its plane: %s" % (middle,))
        # Outside the sweep the endpoint itself comes back, exactly.
        for fraction in (0.0, -0.5):
            self.assertEqual(point_at(self.quarter, self.START, self.TARGET, fraction), self.START,
                             "fraction %s did not hand back the start" % fraction)
        for fraction in (1.0, 2.0):
            self.assertEqual(point_at(self.quarter, self.START, self.TARGET, fraction), self.TARGET,
                             "fraction %s did not hand back the target" % fraction)


if __name__ == "__main__":
    unittest.main()
