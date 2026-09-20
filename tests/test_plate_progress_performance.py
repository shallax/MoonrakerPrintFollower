"""The simplification's cost bound, on the shapes that make it matter.

Douglas-Peucker is exact and cheap on ordinary geometry and quadratic
on adversarial geometry: a chain whose every split peels exactly one
vertex costs one full scan per kept vertex, so a 200k sawtooth is 4e10
inspections rather than a drawing. These cases hold the line the work
bound draws — each one asserts the geometry the payload must still
guarantee AND a runtime ceiling loose enough that only a catastrophic
regression can cross it.

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

from plugins.ArcGeometry import MAX_SAGITTA_MM
from plugins.PlateProgress import (
    MAX_TRAVEL_POINTS,
    _budgeted,
    _douglas_peucker,
)
# The error bound's measurement lives with the correctness tests: one
# implementation, used here to hold the same promise on the stress shapes.
from tests.test_plate_progress import dropped_within

# The ceiling: a pathological 200k channel took minutes before the work
# bound (the sawtooth case ran past 120s in the measurement that
# prompted the clamp) and takes well under a second after it. Ten
# seconds is far above the measured cost on any machine and far below
# the unclamped one, so the test fails loudly on a regression and never
# on a slow runner.
RUNTIME_CEILING_S = 10.0
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


class SimplificationPerformanceTests(SoundSimplification, unittest.TestCase):
    """The four shapes, each against the bound and the clock."""

    def test_the_noisy_run_is_bounded_and_still_sound(self):
        points = chain(randomish(SIZE))
        elapsed, kept = measured(lambda: _douglas_peucker(points, MAX_SAGITTA_MM))
        self.assertLess(elapsed, RUNTIME_CEILING_S)
        self.assert_sound(points, kept, MAX_SAGITTA_MM)

    def test_the_sawtooth_is_bounded_and_still_sound(self):
        points = chain(sawtooth(SIZE))
        elapsed, kept = measured(lambda: _douglas_peucker(points, MAX_SAGITTA_MM))
        self.assertLess(elapsed, RUNTIME_CEILING_S)
        self.assert_sound(points, kept, MAX_SAGITTA_MM)
        # The spikes are above the tolerance, so a bound-respecting
        # simplification cannot reach the two-vertex answer the shape
        # would need; what matters is that it is bounded, not that it
        # is small.
        self.assertGreater(len(kept), 2)

    def test_the_long_nearly_straight_run_collapses(self):
        points = chain(nearly_straight(SIZE))
        elapsed, kept = measured(lambda: _douglas_peucker(points, MAX_SAGITTA_MM))
        self.assertLess(elapsed, RUNTIME_CEILING_S)
        self.assert_sound(points, kept, MAX_SAGITTA_MM)
        # A bow of half a millimetre over four hundred metres of run:
        # the corners are the only things the bound has to keep.
        self.assertLess(len(kept), 200)

    def test_the_dense_arc_is_bounded_and_still_sound(self):
        points = chain(dense_arc(SIZE))
        elapsed, kept = measured(lambda: _douglas_peucker(points, MAX_SAGITTA_MM))
        self.assertLess(elapsed, RUNTIME_CEILING_S)
        self.assert_sound(points, kept, MAX_SAGITTA_MM)
        self.assertLess(len(kept), SIZE // 10)


class BudgetedPerformanceTests(SoundSimplification, unittest.TestCase):
    """The same shapes through the production entry: _budgeted at the
    travel channel's budget, which is the one a real layer exceeds."""

    def test_every_shape_is_bounded_through_the_budgeted_channel(self):
        for name, make in WORKLOADS:
            points = chain(make(SIZE))
            elapsed, kept = measured(
                lambda points=points: _budgeted([points], MAX_TRAVEL_POINTS)[0])
            self.assertLess(elapsed, RUNTIME_CEILING_S, name)
            # Over budget is the documented fallback for a channel whose
            # geometry cannot be reduced inside the ceiling; the bound
            # the payload still owes is the ceiling itself.
            self.assert_sound(points, kept, MAX_SAGITTA_MM)

    def test_a_channel_the_budget_can_hold_stays_inside_it(self):
        # The straight run at the same size: the corridor the budget
        # exists to buy is still bought, so the bound did not turn a
        # fitting channel into a dense one.
        points = chain(nearly_straight(SIZE, step=1.0))
        elapsed, kept = measured(
            lambda: _budgeted([points], MAX_TRAVEL_POINTS)[0])
        self.assertLess(elapsed, RUNTIME_CEILING_S)
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
