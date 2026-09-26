"""The simplification's cost bound, on the shapes that make it matter.

Douglas-Peucker is exact and cheap on ordinary geometry and quadratic
on adversarial geometry: a chain whose every split peels exactly one
vertex costs one full scan per kept vertex, so a 200k sawtooth is 4e10
inspections rather than a drawing. These cases hold the line the work
bound draws — each one asserts the geometry the payload must still
guarantee AND the work the walk charges against the bound.

That second assertion is a COUNT, never a clock: the walk is counted
one unit per interior vertex it asks the distance of, which is the
quantity the bound is written in, and a count reads the same on an
idle runner and on one running sixteen suites at once. A stopwatch
reads the machine's load into the result instead, which is how a
budget turns into a flake.

What is asserted of every case:

* the endpoints never move;
* the result is a subset of the input vertices, in order;
* every dropped vertex is within the tolerance of the FINITE chord
  that replaced it — the module's own error bound, measured here
  independently of the code that produces it;
* the travel channel either fits its budget or stays inside the arc
  sagitta ceiling, whichever the geometry allows.

Run it as a module (`python3 -m tests.test_plate_progress_performance`) to
print the measured table before the assertions run.
"""
from __future__ import annotations

import math
import random
import time
import unittest
from unittest.mock import patch

import plugins.PlateProgress as plate_progress
from plugins.ArcGeometry import MAX_SAGITTA_MM
from plugins.PlateProgress import (
    MAX_TRAVEL_POINTS,
    _SIMPLIFY_WORK_LIMIT,
    _budgeted,
    _douglas_peucker,
)
# The error bound's measurement lives with the correctness tests: one
# implementation, used here to hold the same promise on the stress shapes.
from tests.test_plate_progress import dropped_within

# The work ceiling: the bound promises one call no more than the limit
# plus the single interval scan it was already inside when the charge
# ran out, so twice the documented limit is that promise with room.
# Measured on the largest geometry the point budgets admit (200k
# vertices, one channel): 1.4M inspections for the long bow, 2.7M for
# the dense arc, and 3.0M/3.2M for the two shapes the bound clips —
# against the 4e10 those same shapes inspected unclamped, four orders
# past this ceiling. Counted, not timed, so no runner's load can move
# any of those numbers.
WORK_CEILING = 2 * _SIMPLIFY_WORK_LIMIT
# The largest point count the per-class budget admits without
# simplifying at all, which is where the stress cases start.
SIZE = 200000


def chain(points):
    """The payload's vertex shape: [x, y, motion]."""
    return [[float(x), float(y), float(i)] for i, (x, y) in enumerate(points)]


def randomish(count):
    """A noisy run: a seeded random walk, the reviewer's 'noisy' case."""
    rng = random.Random(20260920)
    x = y = 0.0
    points = []
    for _ in range(count):
        x += rng.uniform(-0.5, 0.5)
        y += rng.uniform(-0.5, 0.5)
        points.append((x, y))
    return points


def sawtooth(count, amplitude=0.5, pitch=1.0):
    """Alternating spikes ABOVE the tolerance: the quadratic case.

    Every split peels the spike next to the vertex just kept, so the
    recursion keeps one vertex per split with a near-full-length scan
    each — the shape the old walk could not finish.
    """
    return [(i * pitch, amplitude if i % 2 else 0.0) for i in range(count)]


def nearly_straight(count, step=0.2):
    """A long gentle bow: one smooth curve, thousands of vertices, and
    a simplification that must find its thirty-odd corners quickly."""
    return [(i * step, math.sin(i * 1.0e-4) * 0.5) for i in range(count)]


def dense_arc(count, radius=40.0, step=0.001):
    """Arc-derived geometry at full tessellation density: a spiral of
    identical curvature, which is what a layer of arcs looks like once
    ArcGeometry has drawn every subedge."""
    return [(radius * math.cos(i * step), radius * math.sin(i * step))
            for i in range(count)]


WORKLOADS = (
    ("randomish", randomish),
    ("sawtooth", sawtooth),
    ("nearly straight", nearly_straight),
    ("dense arc", dense_arc),
)


def measured(call):
    start = time.perf_counter()
    value = call()
    return time.perf_counter() - start, value


class WorkCeilingExceeded(Exception):
    """The walk inspected more vertices than the bound admits."""


def charged(call, ceiling):
    """(work, value): run *call* counting what its walk inspects.

    One unit per interior vertex the walk asks the distance of — the
    charge the module's bound is written in — counted from OUTSIDE the
    walk, so a walk that stopped charging its own counter is still
    counted. The walk is cut off at *ceiling*: without its bound the
    quadratic shapes run for minutes, and a test that hangs has said
    nothing at all.
    """
    real = plate_progress._point_segment_distance_sq
    count = [0]

    def inspected(px, py, x0, y0, dx, dy, span_sq):
        count[0] += 1
        if count[0] > ceiling:
            raise WorkCeilingExceeded(count[0])
        return real(px, py, x0, y0, dx, dy, span_sq)

    with patch.object(plate_progress, "_point_segment_distance_sq", inspected):
        value = call()
    return count[0], value


class SoundSimplification:
    """The geometry every simplification owes, whichever entry ran it."""

    def assert_sound(self, points, kept, tolerance):
        self.assertGreaterEqual(len(kept), 2)
        self.assertEqual([kept[0][0], kept[0][1]], [points[0][0], points[0][1]])
        self.assertEqual([kept[-1][0], kept[-1][1]],
                         [points[-1][0], points[-1][1]])
        motions = [int(vertex[2]) for vertex in kept]
        self.assertEqual(motions, sorted(set(motions)),
                         "the kept vertices must be the input's own, in order")
        worst = dropped_within(points, kept)
        self.assertLessEqual(worst, tolerance + 1.0e-9,
                             "a dropped vertex sits %s mm from the drawn polyline" % worst)


class WorkBounded:
    """The walk's cost, held to the count its bound is written in."""

    def assert_bounded(self, call, label):
        """The kept vertices of *call*, with its walk cut off as soon
        as it inspects past WORK_CEILING — the bound's own promise,
        counted instead of timed."""
        try:
            _, kept = charged(call, WORK_CEILING)
        except WorkCeilingExceeded as exceeded:
            self.fail("%s: the walk inspected past %d vertices (%d so far)"
                      % (label, WORK_CEILING, exceeded.args[0]))
        return kept


class SimplificationPerformanceTests(SoundSimplification, WorkBounded,
                                     unittest.TestCase):
    """The four shapes, each against the bound and the work it charges."""

    def test_the_noisy_run_is_bounded_and_still_sound(self):
        points = chain(randomish(SIZE))
        kept = self.assert_bounded(
            lambda: _douglas_peucker(points, MAX_SAGITTA_MM), "the noisy run")
        self.assert_sound(points, kept, MAX_SAGITTA_MM)

    def test_the_sawtooth_is_bounded_and_still_sound(self):
        points = chain(sawtooth(SIZE))
        kept = self.assert_bounded(
            lambda: _douglas_peucker(points, MAX_SAGITTA_MM), "the sawtooth")
        self.assert_sound(points, kept, MAX_SAGITTA_MM)
        # The spikes are above the tolerance, so a bound-respecting
        # simplification cannot reach the two-vertex answer the shape
        # would need; what matters is that it is bounded, not that it
        # is small.
        self.assertGreater(len(kept), 2)

    def test_the_long_nearly_straight_run_collapses(self):
        points = chain(nearly_straight(SIZE))
        kept = self.assert_bounded(
            lambda: _douglas_peucker(points, MAX_SAGITTA_MM), "the long bow")
        self.assert_sound(points, kept, MAX_SAGITTA_MM)
        # A bow of half a millimetre over four hundred metres of run:
        # the corners are the only things the bound has to keep.
        self.assertLess(len(kept), 200)

    def test_the_dense_arc_is_bounded_and_still_sound(self):
        points = chain(dense_arc(SIZE))
        kept = self.assert_bounded(
            lambda: _douglas_peucker(points, MAX_SAGITTA_MM), "the dense arc")
        self.assert_sound(points, kept, MAX_SAGITTA_MM)
        self.assertLess(len(kept), SIZE // 10)


class BudgetedPerformanceTests(SoundSimplification, WorkBounded,
                              unittest.TestCase):
    """The same shapes through the production entry: _budgeted at the
    travel channel's budget, which is the one a real layer exceeds."""

    def test_every_shape_is_bounded_through_the_budgeted_channel(self):
        for name, make in WORKLOADS:
            points = chain(make(SIZE))
            kept = self.assert_bounded(
                lambda points=points: _budgeted([points], MAX_TRAVEL_POINTS)[0],
                name)
            # Over budget is the documented fallback for a channel whose
            # geometry cannot be reduced inside the ceiling; the bound
            # the payload still owes is the ceiling itself.
            self.assert_sound(points, kept, MAX_SAGITTA_MM)

    def test_a_channel_the_budget_can_hold_stays_inside_it(self):
        # The straight run at the same size: the corridor the budget
        # exists to buy is still bought, so the bound did not turn a
        # fitting channel into a dense one.
        points = chain(nearly_straight(SIZE, step=1.0))
        kept = self.assert_bounded(
            lambda: _budgeted([points], MAX_TRAVEL_POINTS)[0], "the straight run")
        self.assertLessEqual(len(kept), MAX_TRAVEL_POINTS)
        self.assert_sound(points, kept, MAX_SAGITTA_MM)


if __name__ == "__main__":
    print("workload            n        _douglas_peucker  _budgeted(travel)  kept")
    for name, make in WORKLOADS:
        points = chain(make(SIZE))
        first, kept = measured(lambda points=points: _douglas_peucker(points, MAX_SAGITTA_MM))
        second, budgeted = measured(
            lambda points=points: _budgeted([points], MAX_TRAVEL_POINTS)[0])
        print("%-18s %-8d %8.3f s        %8.3f s           %d/%d"
              % (name, SIZE, first, second, len(budgeted), len(kept)))
    unittest.main()
