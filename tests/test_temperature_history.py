"""Pure domain tests for the Monitor's temperature history buffers."""
from __future__ import annotations

import unittest

from plugins.MonitorFormatting import chart_temperature_objects
from plugins.MonitorTemperatureHistory import (
    FILLING_SECONDS,
    GAP_RESET_SECONDS,
    MAX_SAMPLES,
    WINDOW_SECONDS,
    TemperatureHistory,
    chart_payload,
)


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

    def test_default_window_matches_the_auxiliary_cadence(self):
        self.assertEqual(WINDOW_SECONDS, 1800)
        self.assertEqual(MAX_SAMPLES, 1800)


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

    def test_persisted_config_overrides_flow_through(self):
        history = TemperatureHistory()
        history.observe({"extruder": {"temperature": 200.0}, "heater_bed": {"temperature": 60.0}}, 1000.0)
        payload = chart_payload(history, {
            "visible": {"extruder": False},
            "colors": {"heater_bed": "#123456"},
            "showTargets": False,
            "showPower": False,
        })
        by_name = {series["name"]: series for series in payload["series"]}
        self.assertFalse(by_name["extruder"]["visible"])
        self.assertEqual(by_name["heater_bed"]["color"], "#123456")
        self.assertFalse(payload["showTargets"])
        self.assertFalse(payload["showPower"])


if __name__ == "__main__":
    unittest.main()
