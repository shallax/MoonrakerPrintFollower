"""Pure domain tests for the Monitor's temperature history buffers."""
from __future__ import annotations

import random
import unittest

from plugins.MonitorFormatting import chart_temperature_objects
from plugins.MonitorTemperatureHistory import (
    DORMANT_CHART,
    FILLING_SECONDS,
    GAP_RESET_SECONDS,
    MAX_SAMPLES,
    MINI_RENDER_BUDGET,
    WINDOW_SECONDS,
    TemperatureHistory,
    _mini_points,
    chart_payload,
    latest_values,
    mini_chart_payload,
    mini_names,
    series_metadata,
)


def reference_bounds(points, targets):
    """The render domain the chart scanned for itself before the payload
    carried it: every sample, plus every setpoint above zero."""
    bounds = {}
    for index, key in ((1, "temp"), (0, "elapsed")):
        values = [point[index] for point in points]
        if values:
            bounds[key + "Min"] = min(values)
            bounds[key + "Max"] = max(values)
    lit = [point[1] for segment in targets for point in segment if point[1] > 0]
    if lit:
        bounds["targetMin"] = min(lit)
        bounds["targetMax"] = max(lit)
    return bounds


class ChartableObjectTests(unittest.TestCase):
    """The single classification predicate: heaters and temperature
    sensors chart; fans chart only when their reading is unique."""

    def test_heaters_and_temperature_sensors_chart(self):
        auxiliary = {
            "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.2},
            "extruder": {"temperature": 210.0, "target": 210.0, "power": 0.5},
            "extruder1": {"temperature": 30.0},
            "heater_generic chamber": {"temperature": 40.0, "target": 45.0},
            "temperature_sensor enclosure": {"temperature": 22.0},
            "bme280 chamber": {"temperature": 21.5},
        }
        self.assertEqual(set(chart_temperature_objects(auxiliary)), set(auxiliary))

    def test_non_temperature_names_are_excluded(self):
        auxiliary = {
            "fan": {"speed": 0.5},
            "heater_fan hotend": {"speed": 0.8},
            "output_pin psu": {"value": 1.0},
            "filament_switch_sensor runout": {"filament_detected": True},
            "mcu": {"mcu_temp": 34.0},
            "webhooks": {"state": "ready"},
            "extruder_stepper my_stepper": {"temperature": 12.0},  # a stepper, not a heater
            "temperature_sensor broken": {"temperature": None},
        }
        self.assertEqual(chart_temperature_objects(auxiliary), {})

    def test_temperature_fan_charts_only_when_its_reading_is_unique(self):
        # The fan's own thermistor: no other object reads it.
        unique = {"temperature_fan chamber_exhaust": {"temperature": 41.0, "target": 40.0, "speed": 0.3}}
        self.assertEqual(set(chart_temperature_objects(unique)), {"temperature_fan chamber_exhaust"})
        # The same physical sensor enumerated separately: the fan is a
        # duplicate window and drops out.
        duplicated = {
            "temperature_sensor chamber": {"temperature": 41.0},
            "temperature_fan chamber_exhaust": {"temperature": 41.0, "target": 40.0},
        }
        self.assertEqual(set(chart_temperature_objects(duplicated)), {"temperature_sensor chamber"})
        # A fan mirroring a heater's temperature drops too.
        mirrored = {
            "extruder": {"temperature": 210.0},
            "heater_fan hotend": {"temperature": 210.0, "speed": 0.4},
        }
        self.assertEqual(set(chart_temperature_objects(mirrored)), {"extruder"})


class TemperatureHistoryTests(unittest.TestCase):
    def observe(self, history, elapsed, temperatures):
        auxiliary = {}
        for name, (temp, target, power) in temperatures.items():
            value = {"temperature": temp}
            if target is not None:
                value["target"] = target
            if power is not None:
                value["power"] = power
            auxiliary[name] = value
        history.observe(auxiliary, 1000.0 + elapsed)

    def test_series_accumulate_and_trim_to_the_window(self):
        history = TemperatureHistory(window_seconds=100)
        for tick in range(151):
            self.observe(history, tick, {"heater_bed": (20 + tick * 0.1, 60, 0.5)})
        points = history.points("heater_bed")
        self.assertEqual(len(points), 101)  # window of 100 s + the boundary sample
        self.assertAlmostEqual(points[0][0], 50.0)  # oldest kept: 150 - 100
        self.assertAlmostEqual(points[0][1], 25.0)
        self.assertAlmostEqual(points[-1][1], 35.0)

    def test_vanished_sensors_leave_the_series_once_the_window_slides_past(self):
        # Panel UX P3: a sensor that stops reporting must not linger in
        # the legend as a "—" row for the rest of the session.
        history = TemperatureHistory(window_seconds=100)
        self.observe(history, 0, {"extruder": (200, 210, 0.8), "heater_bed": (60, 60, 0.5)})
        self.assertIn("heater_bed", history.names())
        # Only the extruder reports for the next full window: the bed's
        # points age past the cutoff and the deque empties — pruned.
        for tick in range(1, 12):
            self.observe(history, tick * 10, {"extruder": (200 + tick, 210, 0.8)})
        self.assertIn("extruder", history.names())
        self.assertNotIn("heater_bed", history.names())

    def test_target_and_power_segments_split_at_gaps(self):
        history = TemperatureHistory()
        self.observe(history, 0, {"extruder": (200, 210, 0.8)})
        self.observe(history, 1, {"extruder": (201, 210, 0.8)})
        self.observe(history, 2, {"extruder": (202, None, None)})
        self.observe(history, 3, {"extruder": (203, None, None)})
        self.observe(history, 10, {"extruder": (204, 150, 0.3)})
        self.observe(history, 11, {"extruder": (205, 150, 0.3)})
        targets = history.target_segments("extruder")
        self.assertEqual(targets, [[[0.0, 210.0], [1.0, 210.0]], [[10.0, 150.0], [11.0, 150.0]]])
        powers = history.power_segments("extruder")
        self.assertEqual(powers, [[[0.0, 0.8], [1.0, 0.8]], [[10.0, 0.3], [11.0, 0.3]]])

    def test_non_numeric_temperatures_are_skipped(self):
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": "warm"}}, 1000.0)
        history.observe({"heater_bed": {"temperature": None}}, 1001.0)
        history.observe({"heater_bed": {"temperature": 55.0}}, 1002.0)
        self.assertEqual(history.points("heater_bed"), [[2.0, 55.0]])

    def test_sample_cap_bounds_memory(self):
        history = TemperatureHistory(window_seconds=10 ** 9)
        for tick in range(MAX_SAMPLES + 50):
            self.observe(history, tick, {"extruder": (float(tick), None, None)})
        self.assertEqual(len(history.series("extruder")), MAX_SAMPLES)
        self.assertEqual(history.series("extruder")[0].temperature, 50.0)

    def test_a_backwards_clock_step_starts_a_new_window(self):
        # A suspend/wake can hand back a smaller monotonic reading. The
        # window restarts (the samples kept belong to the old timebase),
        # so a series is never a mix of two clocks — the chart's
        # nearest-sample search reads elapsed as ascending.
        history = TemperatureHistory()
        self.observe(history, 0, {"heater_bed": (60, None, None)})
        self.observe(history, 10, {"heater_bed": (61, None, None)})
        self.observe(history, -10, {"heater_bed": (62, None, None)})
        self.assertEqual(history.points("heater_bed"), [[0.0, 62.0]])
        self.observe(history, -7.5, {"heater_bed": (63, None, None)})
        self.assertEqual(history.points("heater_bed"), [[0.0, 62.0], [2.5, 63.0]])

    def test_reset_clears_series_and_time_origin_and_bumps_revision(self):
        history = TemperatureHistory()
        self.observe(history, 0, {"heater_bed": (60, None, None)})
        revision = history.revision
        history.reset()
        self.assertEqual(history.names(), [])
        self.assertGreater(history.revision, revision)
        self.observe(history, 5, {"heater_bed": (61, None, None)})
        self.assertEqual(history.points("heater_bed"), [[0.0, 61.0]])

    def test_a_long_feed_gap_starts_a_new_window(self):
        history = TemperatureHistory()
        self.observe(history, 0, {"heater_bed": (60, None, None)})
        self.observe(history, 10, {"heater_bed": (60, None, None)})
        self.observe(history, 10 + GAP_RESET_SECONDS + 1, {"heater_bed": (61, None, None)})
        points = history.points("heater_bed")
        self.assertEqual(len(points), 1)  # the pre-gap samples are gone
        self.assertAlmostEqual(points[0][1], 61.0)

    def test_fresh_windows_report_filling(self):
        history = TemperatureHistory()
        self.observe(history, 0, {"heater_bed": (60, None, None)})
        self.assertTrue(history.filling)
        self.observe(history, FILLING_SECONDS + 1, {"heater_bed": (60, None, None)})
        self.assertFalse(history.filling)

    def test_revision_only_bumps_when_samples_land(self):
        history = TemperatureHistory()
        self.observe(history, 0, {"heater_bed": (60, None, None)})
        revision = history.revision
        history.observe({"fan": {"speed": 0.5}}, 1001.0)  # nothing chartable
        self.assertEqual(history.revision, revision)

    def test_names_are_sorted_and_stable(self):
        history = TemperatureHistory()
        self.observe(history, 0, {"extruder": (200, None, None), "heater_bed": (60, None, None),
                                  "heater_generic chamber": (40, None, None)})
        self.assertEqual(history.names(), ["extruder", "heater_bed", "heater_generic chamber"])

    def test_default_window_matches_the_chart_cadence(self):
        self.assertEqual(WINDOW_SECONDS, 1800)
        self.assertEqual(MAX_SAMPLES, 2 * WINDOW_SECONDS)

    def test_the_window_holds_the_full_span_at_the_fixed_chart_cadence(self):
        # Seeds the whole advertised window at the fixed 1 s chart
        # cadence (the full-30-minute gap the slider-coupled sampling
        # exposed: at fast slider settings the count cap trimmed the
        # window to minutes — the live report).
        history = TemperatureHistory()
        ticks = int(WINDOW_SECONDS / 1.0) + 100
        for tick in range(ticks):
            self.observe(history, tick * 1.0, {"extruder": (200.0 + (tick % 50) * 0.1, None, None)})
        points = history.points("extruder")
        self.assertEqual(points[-1][0] - points[0][0], WINDOW_SECONDS)

    def test_a_fast_chart_clock_still_holds_the_full_window(self):
        # The chart's 1 s clock is a coarse Qt timer, which may legally
        # fire 5% early (measured on the host: a 0.95 s stretch). The
        # elapsed trim owns the domain, so a fast clock costs at most
        # the sample on the cutoff — a count cap at the window boundary
        # would trim live samples and shrink the drawn window instead.
        period = 0.95
        history = TemperatureHistory()
        for tick in range(int(WINDOW_SECONDS / period) + 8):
            self.observe(history, tick * period, {"extruder": (200.0, None, None)})
        points = history.points("extruder")
        span = points[-1][0] - points[0][0]
        self.assertGreater(span, WINDOW_SECONDS - period, "the count cap trimmed live samples")
        self.assertLessEqual(span, WINDOW_SECONDS)


class TargetCompressionTests(unittest.TestCase):
    """Setpoint tracks are published without the samples that sit inside
    a constant run: the chart draws the same step shape — the dropped
    samples lay on the line between their neighbours — with a fraction
    of the points to map on every paint."""

    def observe(self, history, elapsed, target):
        history.observe({"extruder": {"temperature": 200.0, "target": target}}, 1000.0 + elapsed)

    def test_a_holding_setpoint_keeps_only_the_run_ends(self):
        history = TemperatureHistory()
        for tick in range(20):
            self.observe(history, tick, 210.0)
        self.assertEqual(history.target_segments("extruder"), [[[0.0, 210.0], [19.0, 210.0]]])

    def test_a_target_change_keeps_both_sides_of_the_step(self):
        history = TemperatureHistory()
        for tick in range(6):
            self.observe(history, tick, 210.0)
        for tick in range(6, 12):
            self.observe(history, tick, 240.0)
        self.assertEqual(history.target_segments("extruder"),
                         [[[0.0, 210.0], [5.0, 210.0], [6.0, 240.0], [11.0, 240.0]]])
        # A change that returns to the old value keeps all three runs.
        for tick in range(12, 15):
            self.observe(history, tick, 210.0)
        self.assertEqual(history.target_segments("extruder"),
                         [[[0.0, 210.0], [5.0, 210.0], [6.0, 240.0], [11.0, 240.0],
                           [12.0, 210.0], [14.0, 210.0]]])

    def test_every_dropped_sample_lay_on_the_drawn_line(self):
        # The rendering-equivalence proof, over a deliberately noisy
        # track: dropping a sample may never move the polyline.
        history = TemperatureHistory()
        rng = random.Random(20260919)
        raw = [rng.choice([200.0, 200.0, 200.0, 215.0, 230.0]) for _ in range(600)]
        for index, value in enumerate(raw):
            self.observe(history, index * 2.5, value)
        segments = history.target_segments("extruder")
        self.assertEqual(len(segments), 1)
        kept = {round(point[0] / 2.5) for point in segments[0]}
        self.assertIn(0, kept)
        self.assertIn(len(raw) - 1, kept)
        self.assertEqual({point[1] for point in segments[0]}, set(raw))
        for index in range(1, len(raw) - 1):
            if index in kept:
                continue
            # Dropped: its value equals both neighbours', so the drawn
            # line passes exactly through where it was.
            self.assertEqual(raw[index], raw[index - 1])
            self.assertEqual(raw[index], raw[index + 1])

    def test_a_sample_off_gap_splits_segments_and_compresses_each(self):
        history = TemperatureHistory()
        for tick in range(5):
            self.observe(history, tick, 210.0)
        for tick in range(5, 10):  # target reported off: no samples at all
            self.observe(history, tick, None)
        for tick in range(10, 15):
            self.observe(history, tick, 210.0)
        self.assertEqual(history.target_segments("extruder"),
                         [[[0.0, 210.0], [4.0, 210.0]], [[10.0, 210.0], [14.0, 210.0]]])

    def test_a_lone_setpoint_is_still_dropped(self):
        # The >= 2 samples rule predates the compression and still holds:
        # a one-sample blip draws nothing.
        history = TemperatureHistory()
        self.observe(history, 0, None)
        self.observe(history, 10, 210.0)
        self.observe(history, 20, None)
        self.assertEqual(history.target_segments("extruder"), [])

    def test_heater_power_keeps_every_sample(self):
        # Power is left uncompressed on purpose: it moves sample to
        # sample while a heater holds, so run compression would buy
        # nothing and the drawn area must stay literally the feed.
        history = TemperatureHistory()
        for tick in range(12):
            history.observe({"heater_bed": {"temperature": 60.0, "power": 0.5}}, 1000.0 + tick)
        self.assertEqual(len(history.power_segments("heater_bed")[0]), 12)


class ChartBoundsTests(unittest.TestCase):
    """The payload carries each series' render domain; the chart scans
    it instead of walking the samples it draws."""

    def history(self):
        history = TemperatureHistory()
        for tick in range(30):
            history.observe({
                "extruder": {"temperature": 200.0 + tick, "target": 210.0 if tick < 20 else 240.0},
                "heater_bed": {"temperature": 60.0 - tick * 0.5, "target": 0.0},
                "temperature_sensor chamber": {"temperature": 25.0},
            }, 1000.0 + tick * 2.5)
        return history

    def test_bounds_match_the_scan_the_chart_used_to_run(self):
        payload = chart_payload(self.history(), {})
        for series in payload["series"]:
            self.assertEqual(series["bounds"], reference_bounds(series["points"], series["targets"]),
                             series["name"])

    def test_bounds_carry_the_setpoint_change_that_compression_dropped(self):
        # The domain still spans both setpoints even though the samples
        # between them are gone from the payload.
        series = {entry["name"]: entry for entry in chart_payload(self.history(), {})["series"]}
        self.assertEqual(series["extruder"]["bounds"]["targetMin"], 210.0)
        self.assertEqual(series["extruder"]["bounds"]["targetMax"], 240.0)
        self.assertEqual(series["extruder"]["bounds"]["tempMin"], 200.0)
        self.assertEqual(series["extruder"]["bounds"]["tempMax"], 229.0)
        self.assertEqual(series["extruder"]["bounds"]["elapsedMin"], 0.0)
        self.assertEqual(series["extruder"]["bounds"]["elapsedMax"], 72.5)

    def test_a_heater_off_target_never_joins_the_domain(self):
        # target > 0 means lit: a zero setpoint is an off marker, and the
        # chart keeps its domain off it (it decides whether to use the
        # setpoint extremes at all).
        series = {entry["name"]: entry for entry in chart_payload(self.history(), {})["series"]}
        self.assertNotIn("targetMin", series["heater_bed"]["bounds"])
        self.assertEqual(series["heater_bed"]["bounds"]["tempMax"], 60.0)

    def test_a_series_with_no_targets_has_no_setpoint_extremes(self):
        series = {entry["name"]: entry for entry in chart_payload(self.history(), {})["series"]}
        self.assertNotIn("targetMin", series["temperature_sensor chamber"]["bounds"])

    def test_an_empty_series_reports_no_domain(self):
        history = TemperatureHistory()
        points, bounds = history.points_and_bounds("missing")
        self.assertEqual(points, [])
        self.assertEqual(bounds, {})


class ChartPayloadTests(unittest.TestCase):
    def test_primary_flag_and_palette_and_labels(self):
        history = TemperatureHistory()
        history.observe({"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0},
                         "heater_generic chamber": {"temperature": 40.0},
                         "temperature_fan part": {"temperature": 30.0, "target": 35.0},
                         "temperature_sensor enclosure": {"temperature": 22.0}}, 1000.0)
        payload = chart_payload(history, {})
        by_name = {series["name"]: series for series in payload["series"]}
        self.assertTrue(by_name["extruder"]["primary"])
        self.assertTrue(by_name["heater_bed"]["primary"])
        self.assertTrue(by_name["heater_generic chamber"]["primary"])
        self.assertFalse(by_name["temperature_sensor enclosure"]["primary"])
        self.assertFalse(by_name["temperature_fan part"]["primary"])
        self.assertEqual(by_name["temperature_fan part"]["label"], "Part (fan)")
        self.assertEqual(len(payload["palette"]), 10)
        self.assertTrue(payload["filling"])
        # A temperature_host and a temperature_sensor with the same
        # suffix (a raspberry_pi pair) render distinct
        # labels — the host reading carries "(host)".
        pair = TemperatureHistory()
        pair.observe({"temperature_host raspberry_pi": {"temperature": 50.0},
                      "temperature_sensor raspberry_pi": {"temperature": 50.0}}, 1000.0)
        by_pair = {series["name"]: series for series in chart_payload(pair, {})["series"]}
        self.assertEqual(by_pair["temperature_host raspberry_pi"]["label"], "Raspberry pi (host)")
        self.assertEqual(by_pair["temperature_sensor raspberry_pi"]["label"], "Raspberry pi")

    def test_persisted_config_overrides_flow_through(self):
        history = TemperatureHistory()
        history.observe({"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}}, 1000.0)
        payload = chart_payload(history, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        # The hidden series stays OUT of the data payload; its metadata
        # (and its re-enable path) lives in the legend's source.
        self.assertEqual([series["name"] for series in payload["series"]], ["heater_bed"])
        self.assertEqual(payload["series"][0]["color"], "#123456")
        self.assertFalse(payload["showTargets"])
        self.assertFalse(payload["showPower"])
        by_name = {series["name"]: series for series in series_metadata(history, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })}
        self.assertFalse(by_name["extruder"]["visible"])
        self.assertEqual(by_name["heater_bed"]["color"], "#123456")

    def test_a_hidden_series_carries_no_data_in_the_full_payload(self):
        history = TemperatureHistory()
        history.observe({"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5},
                         "heater_bed": {"temperature": 60.0}}, 1000.0)
        payload = chart_payload(history, {"visible": {"heater_bed": False}})
        self.assertEqual([series["name"] for series in payload["series"]], ["extruder"])

    def test_toggled_off_tracks_are_not_built(self):
        history = TemperatureHistory()
        for tick in range(10):
            history.observe({"extruder": {"temperature": 200.0, "target": 210.0, "power": 0.5}},
                            1000.0 + tick * 2.5)
        with_targets = chart_payload(history, {"showTargets": True, "showPower": True})
        self.assertTrue(with_targets["series"][0]["targets"])
        self.assertTrue(with_targets["series"][0]["powers"])
        no_targets = chart_payload(history, {"showTargets": False, "showPower": True})
        self.assertEqual(no_targets["series"][0]["targets"], [])
        self.assertTrue(no_targets["series"][0]["powers"])
        no_power = chart_payload(history, {"showTargets": True, "showPower": False})
        self.assertEqual(no_power["series"][0]["powers"], [])
        self.assertTrue(no_power["series"][0]["targets"])

    def test_latest_values_projects_one_scalar_per_series(self):
        history = TemperatureHistory()
        history.observe({"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}}, 1000.0)
        history.observe({"extruder": {"temperature": 203.5}, "heater_bed": {"temperature": 61.0}}, 1002.5)
        self.assertEqual(latest_values(history), {"extruder": 203.5, "heater_bed": 61.0})

    def test_the_dormant_chart_is_empty_and_stable(self):
        self.assertEqual(DORMANT_CHART["series"], [])
        self.assertIsInstance(DORMANT_CHART["wallOrigin"], type(None))


class MiniReductionTests(unittest.TestCase):
    """The sparkline's bounded render reduction: a single pass over the
    raw window that keeps every extreme, in order, within the budget —
    while the raw history stays untouched at full resolution."""

    def mature(self, ticks=MAX_SAMPLES, with_spike=True):
        history = TemperatureHistory(window_seconds=10 ** 9)
        for tick in range(ticks):
            value = 60.0 + (tick % 200) * 0.5
            if with_spike and tick == ticks - 100:
                value = 95.0  # a one-sample spike the reduction must keep
            history.observe({"heater_bed": {"temperature": value}}, 1000.0 + tick * 2.5)
        return history

    def test_the_reduction_stays_within_the_render_budget(self):
        history = self.mature()
        raw = history.series("heater_bed")
        self.assertEqual(len(raw), MAX_SAMPLES)
        points, _ = _mini_points(raw)
        self.assertLessEqual(len(points), MINI_RENDER_BUDGET)
        self.assertGreater(len(points), MINI_RENDER_BUDGET / 2)

    def test_the_reduction_keeps_the_first_and_last_samples(self):
        history = self.mature()
        raw = history.series("heater_bed")
        points, _ = _mini_points(raw)
        self.assertEqual(points[0], [raw[0].elapsed, raw[0].temperature])
        self.assertEqual(points[-1], [raw[-1].elapsed, raw[-1].temperature])

    def test_the_reduction_preserves_elapsed_order(self):
        history = self.mature()
        points, _ = _mini_points(history.series("heater_bed"))
        for earlier, later in zip(points[:-1], points[1:], strict=True):
            self.assertLess(earlier[0], later[0])

    def test_the_reduction_keeps_the_minima_maxima_and_the_spike(self):
        history = self.mature()
        raw = history.series("heater_bed")
        temperatures = [sample.temperature for sample in raw]
        points, _ = _mini_points(raw)
        kept = [point[1] for point in points]
        self.assertEqual(min(kept), min(temperatures))
        self.assertEqual(max(kept), max(temperatures))
        self.assertIn(95.0, kept, "the one-sample spike was erased")

    def test_the_reduction_bounds_match_the_raw_domain(self):
        history = self.mature()
        raw = history.series("heater_bed")
        _, bounds = _mini_points(raw)
        temperatures = [sample.temperature for sample in raw]
        self.assertEqual(bounds["tempMin"], min(temperatures))
        self.assertEqual(bounds["tempMax"], max(temperatures))
        self.assertEqual(bounds["elapsedMin"], raw[0].elapsed)
        self.assertEqual(bounds["elapsedMax"], raw[-1].elapsed)

    def test_a_short_window_passes_through_unchanged(self):
        history = self.mature(ticks=40, with_spike=False)
        raw = history.series("heater_bed")
        points, _ = _mini_points(raw)
        self.assertEqual(points, [[sample.elapsed, sample.temperature] for sample in raw])

    def test_an_empty_series_reduces_to_nothing(self):
        self.assertEqual(_mini_points(()), ([], {}))

    def test_the_raw_history_keeps_full_resolution_after_the_reduction(self):
        history = self.mature()
        mini_chart_payload(history, {})
        raw = history.series("heater_bed")
        self.assertEqual(len(raw), MAX_SAMPLES)


class MiniPayloadTests(unittest.TestCase):
    """The compact payload carries ONLY what the sparkline draws —
    bounded points for the selected series, no targets, no power —
    and never data for a hidden sensor."""

    def history(self):
        history = TemperatureHistory(window_seconds=10 ** 9)
        for tick in range(500):
            history.observe({
                "extruder": {"temperature": 200.0 + tick * 0.05, "target": 210.0, "power": 0.5},
                "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.2},
                "temperature_sensor chamber": {"temperature": 25.0},
            }, 1000.0 + tick * 2.5)
        return history

    def test_the_mini_payload_carries_no_target_or_power_data(self):
        payload = mini_chart_payload(self.history(), {})
        for series in payload["series"]:
            self.assertNotIn("targets", series)
            self.assertNotIn("powers", series)
        self.assertFalse(payload["showTargets"])
        self.assertFalse(payload["showPower"])

    def test_the_mini_payload_selection_matches_the_shared_policy(self):
        history = self.history()
        payload = mini_chart_payload(history, {})
        self.assertEqual([series["name"] for series in payload["series"]],
                         mini_names(history.names(), {}))
        # Both primaries ride along — the policy is never less than two
        # while two primaries exist.
        self.assertEqual(len(payload["series"]), 2)

    def test_the_mini_payload_keeps_only_bounded_points(self):
        payload = mini_chart_payload(self.history(), {})
        for series in payload["series"]:
            self.assertLessEqual(len(series["points"]), MINI_RENDER_BUDGET)

    def test_the_mini_payload_drops_hidden_sensors_entirely(self):
        payload = mini_chart_payload(self.history(), {"visible": {"extruder": False}})
        self.assertNotIn("extruder", [series["name"] for series in payload["series"]])
        # The bed is still a visible primary: the preview survives.
        self.assertIn("heater_bed", [series["name"] for series in payload["series"]])

    def test_the_mini_payload_metadata_stays_complete_for_its_series(self):
        payload = mini_chart_payload(self.history(), {})
        bed = next(series for series in payload["series"] if series["name"] == "heater_bed")
        self.assertTrue(bed["primary"])
        self.assertTrue(bed["visible"])
        self.assertTrue(bed["color"].startswith("#"))
        self.assertGreaterEqual(bed["label"], "")


if __name__ == "__main__":
    unittest.main()
