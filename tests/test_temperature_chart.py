"""The temperature chart's own search logic, evaluated in a JS engine.

Hovering the chart needs the sample nearest the snapped cursor second for
every visible series, on every mouse move; the chart finds it with a
binary search over samples that ascend by elapsed. That replacement has
to be exactly what the linear scan it replaced produced — the 1.5 s
acceptance window, the earlier-sample tie rule, duplicated sample times,
a series that started late or ended early — so these tests pull the
function out of the QML document and run it against the scan itself.
"""
from __future__ import annotations

import importlib.util
import pathlib
import random
import unittest

from plugins.MonitorTemperatureHistory import TemperatureHistory

QT_AVAILABLE = importlib.util.find_spec("PyQt6") is not None

PLUGINS = pathlib.Path(__file__).resolve().parent.parent / "plugins"
CHART_QML = (PLUGINS / "TemperatureChart.qml").read_text()

TOLERANCE_SECONDS = 1.5


def reference_nearest(points, elapsed):
    """The scan the chart used: the first sample of the smallest
    distance, and only if that distance is within the window."""
    best = -1
    best_distance = float("inf")
    for index, point in enumerate(points):
        distance = abs(point[0] - elapsed)
        if distance < best_distance:
            best_distance = distance
            best = index
    return best if best_distance <= TOLERANCE_SECONDS else -1


def extract_function(source, name):
    """The named function's source, brace-balanced by QML formatting:
    the body closes on a line holding the declaration's own indent."""
    start = source.index("function %s(" % name)
    lines = source[start:].splitlines(True)
    chunk = [lines[0]]
    for line in lines[1:]:
        chunk.append(line)
        if line.rstrip("\n") == "    }":
            return "".join(chunk)
    raise AssertionError("unterminated function %s" % name)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the chart document's own logic")
class ChartSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        from PyQt6.QtQml import QJSEngine

        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.engine = QJSEngine()
        cls.engine.evaluate(extract_function(CHART_QML, "_nearestIndex"))
        if cls.engine.hasError():
            raise AssertionError(cls.engine.evaluate("0").toString())
        cls.nearest = cls.engine.evaluate("_nearestIndex")
        cls.bounds = None
        cls.engine.evaluate(extract_function(CHART_QML, "_seriesBounds"))
        if cls.engine.hasError():
            raise AssertionError(cls.engine.evaluate("0").toString())
        cls.bounds = cls.engine.evaluate("_seriesBounds")

    def search(self, points, elapsed):
        value = self.nearest.call([self.engine.toScriptValue(points), self.engine.toScriptValue(elapsed)])
        return value.toInt()

    def series_bounds(self, series):
        return self.bounds.call([self.engine.toScriptValue(series)]).toVariant()

    def assertMatchesScan(self, points, elapsed):
        self.assertEqual(self.search(points, elapsed), reference_nearest(points, elapsed),
                         "elapsed=%r" % (elapsed,))

    def test_nothing_to_search(self):
        self.assertMatchesScan([], 0.0)
        self.assertMatchesScan([], -1000.0)

    def test_a_single_sample_is_matched_within_the_window_only(self):
        points = [[10.0, 200.0]]
        self.assertMatchesScan(points, 10.0)
        self.assertMatchesScan(points, 8.5)
        self.assertMatchesScan(points, 11.5)
        self.assertMatchesScan(points, 8.499)
        self.assertMatchesScan(points, 11.501)
        self.assertMatchesScan(points, -500.0)

    def test_the_window_holds_on_a_real_cadence(self):
        points = [[index * 2.5, float(index)] for index in range(120)]
        for elapsed in (0.0, 1.5, 1.500001, 1.25, 300.0, 299.0):
            self.assertMatchesScan(points, elapsed)
        # Snapped cursor seconds across the whole window, including the
        # ends the cursor can sit beyond.
        for elapsed in range(-5, 305):
            self.assertMatchesScan(points, float(elapsed))

    def test_a_tie_keeps_the_earlier_sample(self):
        # Two samples exactly equidistant from the cursor (halves are
        # exact in binary, so this is a real tie): the scan kept the
        # first of them.
        points = [[0.5, 200.0], [1.5, 201.0]]
        self.assertEqual(reference_nearest(points, 1.0), 0)
        self.assertMatchesScan(points, 1.0)
        self.assertMatchesScan(points, 1.000001)
        self.assertMatchesScan(points, 0.999999)

    def test_duplicated_sample_times_keep_the_first_of_the_run(self):
        points = [[0.0, 200.0], [1.0, 201.0], [1.0, 202.0], [1.0, 203.0], [2.5, 204.0]]
        self.assertMatchesScan(points, 1.0)
        self.assertMatchesScan(points, 0.5)
        self.assertMatchesScan(points, 1.5)
        self.assertEqual(reference_nearest(points, 1.0), 1)
        self.assertEqual(self.search(points, 1.0), 1)

    def test_a_late_starting_series_has_no_match_before_it_starts(self):
        points = [[300.0, 40.0], [302.5, 41.0], [305.0, 42.0]]
        self.assertMatchesScan(points, 200.0)
        self.assertMatchesScan(points, 300.0)
        self.assertMatchesScan(points, 298.6)
        self.assertMatchesScan(points, 306.6)

    def test_a_jittered_cadence_matches_the_scan(self):
        rng = random.Random(20260919)
        points = []
        clock = 0.0
        for index in range(400):
            clock += 2.5 + rng.uniform(-0.45, 0.45)
            points.append([round(clock, 4), float(index)])
        for _ in range(400):
            self.assertMatchesScan(points, rng.uniform(-5.0, clock + 5.0))
        for elapsed in range(0, int(clock), 3):
            self.assertMatchesScan(points, float(elapsed))

    def test_a_track_of_duplicated_times_matches_the_scan(self):
        rng = random.Random(4242)
        points = []
        clock = 0.0
        for index in range(200):
            clock += rng.choice([0.0, 0.0, 1.0, 2.5])
            points.append([round(clock, 4), float(index)])
        for _ in range(200):
            self.assertMatchesScan(points, rng.uniform(-1.0, clock + 1.0))

    def test_the_history_window_searches_through_the_payload(self):
        # The real payload path: a mature window with a sensor that
        # started late, searched at every snapped second the cursor can
        # reach.
        history = TemperatureHistory()
        for tick in range(600):
            auxiliary = {"extruder": {"temperature": 200.0 + tick * 0.05, "target": 210.0}}
            if tick >= 100:
                auxiliary["heater_bed"] = {"temperature": 60.0, "target": 60.0}
            history.observe(auxiliary, 1000.0 + tick * 2.5)
        for name in ("extruder", "heater_bed"):
            points = history.points(name)
            for elapsed in range(0, int(points[-1][0]) + 40, 7):
                self.assertMatchesScan(points, float(elapsed))

    def test_a_payload_bounds_scan_matches_the_one_it_replaced(self):
        # The chart reads the render domain out of the payload; a
        # payload built without it must scan to the same numbers.
        history = TemperatureHistory()
        for tick in range(40):
            history.observe({
                "extruder": {"temperature": 200.0 + tick, "target": 210.0 if tick < 25 else 240.0},
                "heater_bed": {"temperature": 60.0, "target": 0.0},
            }, 1000.0 + tick * 2.5)
        from plugins.MonitorTemperatureHistory import chart_payload

        for series in chart_payload(history, {})["series"]:
            scanned = self.series_bounds({"points": series["points"], "targets": series["targets"]})
            self.assertEqual(scanned, series["bounds"], series["name"])

    def test_a_series_without_points_reports_no_domain(self):
        scanned = self.series_bounds({"points": [], "targets": []})
        self.assertTrue(scanned["tempMin"] == float("inf"), scanned)

    def test_the_domain_takes_only_lit_setpoints_from_a_payload(self):
        series = {"points": [[0.0, 20.0], [1.0, 30.0]], "targets": [[[0.0, 0.0], [1.0, 0.0]]]}
        scanned = self.series_bounds(series)
        self.assertNotIn("targetMin", scanned)
        series["targets"] = [[[0.0, 0.0], [1.0, 40.0]]]
        scanned = self.series_bounds(series)
        self.assertEqual(scanned["targetMin"], 40.0)
        self.assertEqual(scanned["targetMax"], 40.0)
