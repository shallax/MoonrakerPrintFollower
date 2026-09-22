"""Direct tests for MonitorControls: the control projections and the
policy gates guarding every dispatch.

The five collaborators are hand-rolled QObject doubles over real Qt
signals, so each dispatch is asserted at the seam the model consumes;
the policy rows are the production MonitorPermissions table and only
the frozen observation record is synthesised per case. One case composes
the real MonitorTuning to pin the queued-value display contract the
brightness/channel rulings depend on.

No line of the module is left uncovered: the container's real Qt
carries the QObject/signal seam, so nothing here needs an exclusion.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.qt_runtime_support import QT_AVAILABLE

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

    from plugins import MonitorControls as controls_module
    from plugins.MonitorControls import MonitorControls
    from plugins.MonitorPermissions import (Observation, R_NOT_PRINTING, R_UNKNOWN, can_macro,
                                            can_restart, can_z_offset)
    from plugins.MonitorTuning import MonitorTuning


def record(**overrides):
    """A synthesised policy record; the production rows rule on it."""
    fields = dict(active=True, connection="yes", state="standby", homed_axes="xyz",
                  assumed_stopped=False, save_config_pending=False, controls_locked=False, busy=False)
    fields.update(overrides)
    return Observation(**fields)


if QT_AVAILABLE:
    class Data(QObject):
        """The data capability: the snapshot plus the policy record."""

        changed = pyqtSignal()
        invalidated = pyqtSignal()

        def __init__(self, observation=None, **fields):
            super().__init__()
            self.active = True
            self.observation = observation
            self._fields = dict(core={}, auxiliary={}, objects=(), presets={}, power=())
            self._fields.update(fields)
            self.snapshot = self._namespace()

        def _namespace(self):
            return SimpleNamespace(server={}, printer={}, webcams=(), endstops=(), **self._fields)

        def rebuild(self, **fields):
            """A poll: patch the snapshot and publish it like the owner."""
            self._fields.update(fields)
            self.snapshot = self._namespace()
            self.changed.emit()

    class Commands(QObject):
        """The command capability: records every dispatch with its rule.

        ``started`` mirrors the lane's verdict from ``script``: False is
        a dispatch that never reached the wire (a dead transport, a full
        queue), and the gesture's latch must follow it.
        """

        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.setup_allowed = True
            self.calls = []
            self.started = True

        def script(self, label, script, rule=None):
            self.calls.append(("script", label, script, rule))
            return self.started

        def request(self, label, path, payload, rule=None):
            self.calls.append(("request", label, path, payload, rule))
            return self.started

        def send(self, label, path, payload):
            self.calls.append(("send", label, path, payload))

        def report_status(self, message):
            self.calls.append(("status", message))

    class Tuning(QObject):
        """The tuning capability: records the debounced display contract."""

        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.observed = []
            self.displayed = {}
            self.previews = []
            self.queued = []

        def observe(self, key, actual):
            self.observed.append((key, actual))

        def value(self, key, actual):
            return self.displayed.get(key, actual)

        def preview(self, key, value):
            self.previews.append((key, value))

        def queue(self, key, value, channel, script):
            self.queued.append((key, value, channel, script))

    class BedMesh(QObject):
        changed = pyqtSignal()

        def __init__(self, snapshot=None):
            super().__init__()
            self.snapshot = snapshot


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ControlsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.data = Data(observation=record())
        self.commands = Commands()
        self.tuning = Tuning()
        self.mesh = BedMesh()
        self.controls = MonitorControls(self.data, self.commands, self.tuning, self.mesh, {})

    def scripts(self):
        return [call for call in self.commands.calls if call[0] == "script"]


class ProjectionTests(ControlsCase):
    def test_values_are_a_deep_copy(self):
        self.data.rebuild(presets={"presets": {"pla": {"name": "PLA", "values": {}}}})
        values = self.controls.values
        values["temperaturePresetItems"].append({"index": 99})
        values["fanControlItems"].append("junk")
        # The copy-on-change contract (the 2026-09-19 review's I):
        # the served copy is stable until the next observation, so a
        # caller's mutation cannot reach the controller's canonical
        # projection — the next rebuild restores it.
        self.data.rebuild(presets={"presets": {}})
        self.data.changed.emit()
        self.assertNotIn(99, [item["index"] for item in self.controls.values["temperaturePresetItems"]])
        self.assertEqual(self.controls.values["fanControlItems"], [])

    def test_every_capability_change_republishes(self):
        # The publish storm's suppression (the 2026-09-19 review):
        # each capability whose PROJECTION changes must emit, and an
        # input that changes nothing outward must stay silent.
        seen = []
        self.controls.changed.connect(lambda: seen.append(len(seen)))
        self.data.rebuild(objects=("gcode_macro TEST",))
        self.data.changed.emit()  # the macro list appears
        self.tuning.displayed["speed-factor"] = 137
        self.tuning.changed.emit()  # the speed slider changes
        self.data.rebuild(objects=())
        self.data.changed.emit()  # the macro list disappears again
        self.mesh.changed.emit()  # the mesh passthrough emits
        self.assertEqual(len(seen), 4)
        # The suppression's own half: an unchanged projection never
        # re-emits — this input would have published a full model
        # rebuild on every heartbeat before.
        self.commands.changed.emit()
        self.assertEqual(len(seen), 4)

    def test_the_printer_surface_projects_into_the_values(self):
        self.data.rebuild(
            objects=("gcode_macro TEST", "quad_gantry_level", "bed_mesh"),
            auxiliary={"configfile": {"config": {}}},
            core={"gcode_move": {"speed_factor": 1.5, "extrude_factor": 0.9, "homing_origin": [0, 0, 0.05]}})
        values = self.controls.values
        self.assertEqual(values["macroNames"], ["TEST"])
        self.assertTrue(values["hasQuadGantryLevel"])
        self.assertTrue(values["hasBedMesh"])
        self.assertEqual(values["speedFactorPercent"], 150)
        self.assertEqual(values["flowFactorPercent"], 90)
        self.assertEqual(values["zOffset"], 0.05)
        self.assertEqual(values["zOffsetText"], "+0.050 mm")
        self.assertEqual(self.tuning.observed[-1], ("flow-factor", 90))

    def test_a_short_origin_reports_no_z_offset(self):
        self.data.rebuild(core={"gcode_move": {"homing_origin": [0]}})
        self.assertEqual(self.controls.values["zOffset"], 0)
        self.assertEqual(self.controls.values["zOffsetText"], "+0.000 mm")

    def test_macro_names_sort_case_insensitively_and_hide_underscored(self):
        self.data.rebuild(objects=("gcode_macro zebra", "gcode_macro Alpha", "gcode_macro _internal", "fan"))
        self.assertEqual(self.controls.values["macroNames"], ["Alpha", "zebra"])

    def test_the_pending_tuning_value_wins_over_the_printer_report(self):
        # The published projection reads tuning.value(), so a held slider
        # never snaps back to the printer's stale poll.
        self.tuning.displayed["fan:fan"] = 42
        self.data.rebuild(auxiliary={"fan": {"speed": 1}})
        self.assertEqual(self.controls.values["fanControlItems"][0]["percent"], 42)
        self.assertIn(("fan:fan", 100), self.tuning.observed)

    def test_the_setup_projection_follows_the_restart_row(self):
        self.data.rebuild(objects=("quad_gantry_level", "bed_mesh"),
                          auxiliary={"configfile": {"config": {}, "save_config_pending": True}},
                          presets={"presets": {"pla": {"name": "PLA", "values": {}}}})
        values = self.controls.values
        self.assertTrue(values["canRunSetup"])
        self.assertTrue(values["canApplyTemperaturePreset"])
        self.assertTrue(values["canSaveConfig"])
        self.data.observation = record(state="printing")
        self.data.rebuild()
        values = self.controls.values
        self.assertFalse(values["canRunSetup"])
        self.assertFalse(values["canApplyTemperaturePreset"])
        self.assertFalse(values["canSaveConfig"])

    def test_the_save_config_summary_is_quiet_on_a_clean_printer(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {}}})
        self.assertEqual(self.controls.values["saveConfigSummary"], "")
        self.assertFalse(self.controls.values["canSaveConfig"])

    def test_the_save_config_summary_names_the_pending_items(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {}, "save_config_pending": True,
                                                    "save_config_pending_items": {"mcu": {}, "extruder": {}}}})
        self.assertEqual(self.controls.values["saveConfigSummary"], "Unsaved: extruder, mcu")
        self.assertTrue(self.controls.values["saveConfigPending"])

    def test_a_pending_save_without_items_falls_back_to_the_summary_line(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {}, "save_config_pending": True}})
        self.assertEqual(self.controls.values["saveConfigSummary"], "Unsaved Klipper configuration changes")

    def test_the_invalidated_signal_resets_the_remembered_gain(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        self.controls.output("led-brightness", "neopixel strip", 20)
        self.controls.led_color("neopixel strip", 100, 0, 0, 0)
        self.assertIn("RED=0.2000", self.tuning.queued[-1][3])
        self.data.invalidated.emit()
        # The gain is re-seeded from the printer's own colour, not kept.
        self.controls.led_color("neopixel strip", 100, 0, 0, 0)
        self.assertIn("RED=1.0000", self.tuning.queued[-1][3])

    def test_section_matches_case_insensitively_and_ignores_non_mappings(self):
        config = {"NeoPixel Strip": {"white_pin": "PA1"}, "other": "not-a-mapping"}
        self.assertEqual(MonitorControls.section(config, "neopixel strip"), {"white_pin": "PA1"})
        self.assertEqual(MonitorControls.section(config, "absent"), {})


class MacroTests(ControlsCase):
    @staticmethod
    def macro_config():
        """A fresh config object, as every SAVE_CONFIG reload delivers."""
        return {"gcode_macro TEST": {"gcode": "{{ params.COUNT|default(2)|int }}"}}

    def test_macro_parameters_are_inferred_once_per_config_object(self):
        self.data.rebuild(objects=("gcode_macro TEST",),
                          auxiliary={"configfile": {"config": self.macro_config()}})
        with patch.object(controls_module, "infer_macro_parameters",
                          wraps=controls_module.infer_macro_parameters) as infer:
            first = self.controls.macro_parameters("TEST")
            self.controls.macro_parameters("TEST")
            self.assertEqual(infer.call_count, 1)
            # A new config object (the SAVE_CONFIG reload) re-infers.
            self.data.rebuild(auxiliary={"configfile": {"config": self.macro_config()}})
            second = self.controls.macro_parameters("TEST")
        self.assertEqual(infer.call_count, 2)
        self.assertEqual(first, second)
        self.assertEqual(first, [{"name": "COUNT", "type": "int", "default": "2",
                                  "required": False, "hasDefault": True}])

    def test_macro_parameters_return_a_copy_and_unknown_names_are_empty(self):
        self.data.rebuild(objects=("gcode_macro TEST",),
                          auxiliary={"configfile": {"config": {"gcode_macro TEST": {"gcode": "{{ params.COUNT }}"}}}})
        parameters = self.controls.macro_parameters("TEST")
        parameters.append("junk")
        self.assertEqual(self.controls.macro_parameters("TEST"),
                         [{"name": "COUNT", "type": "string", "default": "", "required": True, "hasDefault": False}])
        self.assertEqual(self.controls.macro_parameters("ABSENT"), [])

    def test_run_macro_flattens_the_arguments_and_rides_the_macro_row(self):
        self.data.rebuild(objects=("gcode_macro TEST",))
        self.controls.run_macro("TEST", " A=1\nB=2\rC=3 ")
        self.assertEqual(self.commands.calls[-1], ("script", "Macro TEST", "TEST A=1 B=2 C=3", can_macro))
        self.controls.run_macro("TEST")
        self.assertEqual(self.commands.calls[-1], ("script", "Macro TEST", "TEST", can_macro))
        self.controls.run_macro("ABSENT")
        self.assertEqual(len(self.commands.calls), 2)

    def test_run_macro_refuses_while_a_print_runs(self):
        self.data.rebuild(objects=("gcode_macro TEST",))
        self.data.observation = record(state="printing")
        self.controls.run_macro("TEST")
        self.assertEqual(self.commands.calls, [])


class OutputRowTests(ControlsCase):
    def test_fan_rows_project_the_percent_and_the_writability(self):
        self.data.rebuild(auxiliary={"fan": {"speed": 0.5}, "fan_generic aux": {"speed": 1.4},
                                     "controller_fan hotend": {"speed": 0.25}})
        rows = {row["object"]: row for row in self.controls.values["fanControlItems"]}
        self.assertEqual(set(rows), {"fan", "fan_generic aux", "controller_fan hotend"})
        self.assertEqual((rows["fan"]["name"], rows["fan"]["percent"], rows["fan"]["writable"]),
                         ("Part fan", 50, True))
        self.assertEqual((rows["fan_generic aux"]["name"], rows["fan_generic aux"]["percent"]),
                         ("Aux", 100))
        self.assertEqual((rows["controller_fan hotend"]["name"], rows["controller_fan hotend"]["writable"]),
                         ("Hotend", False))

    def test_a_fan_without_a_reported_speed_reads_zero(self):
        self.data.rebuild(auxiliary={"fan": {}})
        self.assertEqual(self.controls.values["fanControlItems"][0]["percent"], 0)

    def test_led_rows_seed_the_gain_and_the_channels_once(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0.5, 0, 0]]}})
        item = self.controls.values["ledItems"][0]
        self.assertEqual(item["object"], "neopixel strip")
        self.assertEqual(item["name"], "Strip")
        self.assertEqual(item["percent"], 100)
        self.assertEqual((item["redPercent"], item["greenPercent"], item["bluePercent"], item["whitePercent"]),
                         (100, 50, 0, 0))
        # The rulings: a dimmer poll and a colour change must not
        # move the sliders the user holds — the gain acts on the SEND.
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[0.2, 0.1, 0, 0]]}})
        item = self.controls.values["ledItems"][0]
        self.assertEqual(item["percent"], 100)
        self.assertEqual((item["redPercent"], item["greenPercent"]), (100, 50))

    def test_led_rows_without_colour_data_are_dropped(self):
        self.data.rebuild(auxiliary={"neopixel strip": {}, "dotstar other": {"color_data": ["junk", {"a": 1}]}})
        self.assertEqual(self.controls.values["ledItems"], [])

    def test_led_white_channel_reads_the_section(self):
        self.data.rebuild(auxiliary={
            "configfile": {"config": {"neopixel pin": {"white_pin": "PA1"},
                                      "neopixel order": {"color_order": "grbw"},
                                      "neopixel plain": {"color_order": "RGB"}}},
            "neopixel pin": {"color_data": [[1, 0, 0, 0]]},
            "neopixel order": {"color_data": [[1, 0, 0, 0]]},
            "neopixel plain": {"color_data": [[1, 0, 0, 0]]}})
        has_white = {item["object"]: item["hasWhite"] for item in self.controls.values["ledItems"]}
        self.assertEqual(has_white, {"neopixel pin": True, "neopixel order": True, "neopixel plain": False})

    def test_pwm_rows_need_the_flag_and_read_the_section_scale(self):
        self.data.rebuild(auxiliary={
            "configfile": {"config": {"output_pin case_light": {"pwm": "True", "scale": "2"},
                                      "output_pin relay": {"pwm": "False"},
                                      "output_pin dim": {"pwm": "on", "scale": "0"},
                                      "output_pin unset": {"pwm": "yes"}}},
            "output_pin case_light": {"value": 1},
            "output_pin relay": {"value": 1},
            "output_pin dim": {"value": 0.25},
            "output_pin unset": {"value": 0.5}})
        rows = {row["object"]: row for row in self.controls.values["pwmOutputItems"]}
        self.assertEqual(set(rows), {"output_pin case_light", "output_pin dim", "output_pin unset"})
        self.assertEqual((rows["output_pin case_light"]["pin"], rows["output_pin case_light"]["name"],
                          rows["output_pin case_light"]["scale"], rows["output_pin case_light"]["percent"]),
                         ("case_light", "Case light", 2, 50))
        # A non-positive scale cannot divide: it falls back to 1.
        self.assertEqual((rows["output_pin dim"]["scale"], rows["output_pin dim"]["percent"]), (1, 25))
        self.assertEqual(rows["output_pin unset"]["percent"], 50)

    def test_a_pwm_pin_with_no_reported_value_is_not_rendered(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {"output_pin lamp": {"pwm": "1"}}},
                                     "output_pin lamp": {}})
        self.assertEqual(self.controls.values["pwmOutputItems"], [])

    def test_non_mapping_auxiliary_values_are_skipped(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {}}, "fan": {"speed": 1},
                                     "gcode_move": "junk", "toolhead": 7})
        self.assertEqual(len(self.controls.values["fanControlItems"]), 1)
        self.assertEqual(self.controls.values["pwmOutputItems"], [])


class OutputDispatchTests(ControlsCase):
    def test_a_regulated_fan_never_gets_a_set_fan_speed(self):
        # Fail closed for the firmware-regulated fans: the command either
        # never sticks or is not registered at all.
        self.data.rebuild(auxiliary={"controller_fan hotend": {"speed": 0.5},
                                     "temperature_fan chamber": {"speed": 0.5}})
        self.controls.output("fan", "controller_fan hotend", 80)
        self.controls.output("fan", "temperature_fan chamber", 80)
        self.assertEqual(self.tuning.queued, [])

    def test_the_part_fan_uses_m106_and_generic_fans_set_fan_speed(self):
        self.data.rebuild(auxiliary={"fan": {"speed": 0.5}, "fan_generic aux": {"speed": 0.5}})
        self.controls.output("fan", "fan", 50)
        self.assertEqual(self.tuning.queued[-1], ("fan:fan", 50, "fan:fan", "M106 S128"))
        self.controls.output("fan", "fan_generic aux", 25)
        self.assertEqual(self.tuning.queued[-1], ("fan:fan_generic aux", 25, "fan:fan_generic aux",
                                                  "SET_FAN_SPEED FAN=aux SPEED=0.250"))

    def test_a_pwm_output_scales_the_section_scale(self):
        self.data.rebuild(auxiliary={"configfile": {"config": {"output_pin case_light": {"pwm": "1", "scale": "2"}}},
                                     "output_pin case_light": {"value": 0.5}})
        self.controls.output("pwm-output", "output_pin case_light", 75)
        self.assertEqual(self.tuning.queued[-1][3], "SET_PIN PIN=case_light VALUE=1.5")

    def test_output_clamps_the_percent_previews_locally_and_skips_strangers(self):
        self.data.rebuild(auxiliary={"fan": {"speed": 0.5}})
        self.controls.output("fan", "fan", 500, preview=True)
        self.assertEqual(self.tuning.previews, [("fan:fan", 100)])
        self.assertEqual(self.tuning.queued, [])
        self.controls.output("fan", "fan", -20)
        self.assertEqual(self.tuning.queued[-1], ("fan:fan", 0, "fan:fan", "M106 S0"))
        self.controls.output("fan", "absent", 50)
        self.controls.output("led-brightness", "absent", 50)
        self.assertEqual(len(self.tuning.queued), 1)

    def test_the_brightness_slider_scales_the_reported_colour(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0, 0, 0], [0, 0, 1, 0]]}})
        self.controls.output("led-brightness", "neopixel strip", 25)
        key, value, channel, script = self.tuning.queued[-1]
        self.assertEqual((key, value, channel), ("led-brightness:neopixel strip", 25, "led-brightness:neopixel strip"))
        self.assertEqual(script.splitlines(), [
            "SET_LED LED=strip INDEX=1 RED=0.2500 GREEN=0.0000 BLUE=0.0000 WHITE=0.0000 TRANSMIT=0",
            "SET_LED LED=strip INDEX=2 RED=0.0000 GREEN=0.0000 BLUE=0.2500 WHITE=0.0000 TRANSMIT=1"])

    def test_the_brightness_falls_back_to_the_last_lit_colour(self):
        # The printer stopped reporting colour (a mid-transition pair of
        # polls): the send must reuse the last colour it actually showed.
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[0, 0, 0, 0]]}})
        self.controls.output("led-brightness", "neopixel strip", 50)
        self.assertEqual(self.tuning.queued[-1][3],
                         "SET_LED LED=strip INDEX=1 RED=0.5000 GREEN=0.0000 BLUE=0.0000 WHITE=0.0000 TRANSMIT=1")

    def test_the_brightness_fallback_without_a_remembered_colour(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[0, 0, 0, 0]]}})
        self.controls.output("led-brightness", "neopixel strip", 50)
        self.assertEqual(self.tuning.queued[-1][3],
                         "SET_LED LED=strip INDEX=1 RED=0.5000 GREEN=0.5000 BLUE=0.5000 WHITE=0.0000 TRANSMIT=1")

    def test_led_color_composes_the_gain_into_the_send(self):
        self.data.rebuild(auxiliary={
            "configfile": {"config": {"neopixel strip": {"color_order": "GRBW"}}},
            "neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        self.controls.led_color("neopixel strip", 0, 100, 0, 100, 50)
        key, value, channel, script = self.tuning.queued[-1]
        self.assertEqual((key, channel), ("led-colour:neopixel strip", "led-colour:neopixel strip"))
        self.assertEqual(value, (0, 100, 0, 100))
        self.assertEqual(script, "SET_LED LED=strip RED=0.0000 GREEN=0.5000 BLUE=0.0000 WHITE=0.5000 TRANSMIT=1")
        # The channels the user set are what the sliders keep showing.
        self.data.rebuild(auxiliary={
            "configfile": {"config": {"neopixel strip": {"color_order": "GRBW"}}},
            "neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        item = self.controls.values["ledItems"][0]
        self.assertEqual((item["redPercent"], item["greenPercent"], item["bluePercent"], item["whitePercent"]),
                         (0, 100, 0, 100))

    def test_led_color_zeroes_white_without_the_white_channel(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        self.controls.led_color("neopixel strip", 100, 0, 0, 100, 50)
        self.assertEqual(self.tuning.queued[-1][1], (100, 0, 0, 0))
        self.assertEqual(self.tuning.queued[-1][3],
                         "SET_LED LED=strip RED=0.5000 GREEN=0.0000 BLUE=0.0000 WHITE=0.0000 TRANSMIT=1")

    def test_led_color_uses_the_remembered_gain_and_clamps(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        self.controls.output("led-brightness", "neopixel strip", 20)
        self.controls.led_color("neopixel strip", 100, 0, 0, 0)
        self.assertIn("RED=0.2000", self.tuning.queued[-1][3])
        self.controls.led_color("neopixel strip", 100, 0, 0, 0, 250)
        self.assertEqual(self.tuning.queued[-1][3],
                         "SET_LED LED=strip RED=1.0000 GREEN=0.0000 BLUE=0.0000 WHITE=0.0000 TRANSMIT=1")

    def test_led_color_defaults_to_full_without_a_remembered_gain(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[0, 0, 0, 0]]}})
        self.controls.led_color("neopixel strip", 100, 0, 0, 0)
        self.assertEqual(self.tuning.queued[-1][3],
                         "SET_LED LED=strip RED=1.0000 GREEN=0.0000 BLUE=0.0000 WHITE=0.0000 TRANSMIT=1")

    def test_led_color_previews_and_skips_strangers(self):
        self.data.rebuild(auxiliary={"neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        self.controls.led_color("neopixel absent", 100, 0, 0, 0)
        self.assertEqual(self.tuning.queued, [])
        self.assertEqual(self.tuning.previews, [])
        self.controls.led_color("neopixel strip", 0, 100, 0, 0, preview=True)
        self.assertEqual(self.tuning.previews, [("led-colour:neopixel strip", (0, 100, 0, 0))])
        self.assertEqual(self.tuning.queued, [])


class FactorAndOffsetTests(ControlsCase):
    def test_factor_clamps_to_the_minimums_and_names_the_gcode(self):
        self.controls.factor("speed", 5)
        self.assertEqual(self.tuning.queued[-1], ("speed-factor", 10, "speed-factor", "M220 S10"))
        self.controls.factor("flow", 20)
        self.assertEqual(self.tuning.queued[-1], ("flow-factor", 50, "flow-factor", "M221 S50"))
        self.controls.factor("speed", 250)
        self.assertEqual(self.tuning.queued[-1], ("speed-factor", 250, "speed-factor", "M220 S250"))

    def test_a_previewed_factor_never_reaches_the_queue(self):
        self.controls.factor("flow", 80, preview=True)
        self.assertEqual(self.tuning.previews, [("flow-factor", 80)])
        self.assertEqual(self.tuning.queued, [])

    def test_z_offset_moves_only_when_the_axes_are_homed(self):
        self.data.rebuild(auxiliary={"toolhead": {"homed_axes": "xyz"}})
        self.controls.z_offset(0.1)
        self.assertEqual(self.commands.calls[-1],
                         ("script", "Z offset", "SET_GCODE_OFFSET Z_ADJUST=+0.1 MOVE=1", can_z_offset))
        self.controls.z_offset()
        self.assertEqual(self.commands.calls[-1][2], "SET_GCODE_OFFSET Z=0 MOVE=1")
        # The MOVE gate reads the snapshot's homed axes; the row itself
        # never consults homing (the H3 ruling), so the nudge still lands.
        self.data.rebuild(auxiliary={"toolhead": {"homed_axes": "x"}})
        self.controls.z_offset(0.5)
        self.assertEqual(self.commands.calls[-1][2], "SET_GCODE_OFFSET Z_ADJUST=+0.5")

    def test_z_offset_rejects_a_zero_or_oversized_nudge(self):
        for amount in (0, 0.00005, 5.5, -6):
            self.controls.z_offset(amount)
        self.assertEqual(self.commands.calls, [])

    def test_z_offset_allows_mid_print_but_not_while_busy(self):
        self.data.observation = record(state="printing")
        self.controls.z_offset(0.1)
        self.assertEqual(len(self.commands.calls), 1)
        self.data.observation = record(state="printing", busy=True)
        self.controls.z_offset(0.1)
        self.assertEqual(len(self.commands.calls), 1)


class PresetTests(ControlsCase):
    def test_presets_sort_by_name_and_fall_back_to_the_key(self):
        self.data.rebuild(presets={"presets": {"zzz": {"name": "Beta", "values": {}},
                                               "aaa": {"values": {}}, "bad": "junk"}})
        values = self.controls.values
        self.assertEqual(values["temperaturePresetNames"], ["aaa", "Beta"])
        self.assertEqual([item["index"] for item in values["temperaturePresetItems"]], [0, 1])

    def test_the_cooldown_gcode_appends_a_cooldown_entry(self):
        self.data.rebuild(presets={"presets": {}, "cooldownGcode": "M104 S0"})
        self.assertEqual(self.controls.values["temperaturePresetNames"], ["Cooldown"])
        self.controls.apply_preset(0)
        self.assertEqual(self.commands.calls[-1], ("script", "Cooldown", "M104 S0", can_restart))

    def test_a_non_mapping_preset_payload_is_ignored(self):
        self.data.rebuild(presets={"presets": ["junk"]})
        self.assertEqual(self.controls.values["temperaturePresetItems"], [])
        self.assertEqual(self.controls.values["temperaturePresetNames"], [])

    def test_preset_active_compares_the_reported_targets_tolerantly(self):
        self.data.rebuild(presets={"presets": {"pla": {"name": "PLA", "values": {
            "extruder": {"bool": True, "value": 200}}}}}, auxiliary={"extruder": {"target": 200}})
        self.assertTrue(self.controls.values["temperaturePresetItems"][0]["active"])
        self.data.rebuild(auxiliary={"extruder": {"target": 199.6}})
        self.assertTrue(self.controls.values["temperaturePresetItems"][0]["active"])
        self.data.rebuild(auxiliary={"extruder": {"target": 180}})
        self.assertFalse(self.controls.values["temperaturePresetItems"][0]["active"])
        self.data.rebuild(auxiliary={"toolhead": {}})
        self.assertFalse(self.controls.values["temperaturePresetItems"][0]["active"])

    def test_preset_active_needs_a_boolean_row_and_a_matching_target(self):
        self.assertFalse(MonitorControls.preset_active({"preset": {"values": "junk"}}, {}))
        self.assertFalse(MonitorControls.preset_active({"preset": {}}, {}))
        # Entries without the bool flag (a sensor pair) compare nothing.
        self.assertFalse(MonitorControls.preset_active(
            {"preset": {"values": {"extruder": {"value": 200}}}}, {"extruder": {"target": 200}}))
        # The reported name match is caseless.
        self.assertTrue(MonitorControls.preset_active(
            {"preset": {"values": {"EXTRUDER": {"bool": True, "value": 200}}}}, {"extruder": {"target": 200}}))

    def test_apply_preset_dispatches_the_heater_commands_and_gcode(self):
        self.data.rebuild(presets={"presets": {"pla": {"name": "PLA", "gcode": "M117 PLA", "values": {
            "extruder": {"bool": True, "value": 200},
            "temperature_fan chamber": {"bool": True, "value": 45},
            "heater_bed": {"bool": False, "value": 60},
            "sensor": {"value": 20},
            "extruder1": {"bool": True},
            "heater_generic broken": "junk"}}}})
        self.controls.apply_preset(0)
        self.assertEqual(self.commands.calls[-1], ("script", "PLA", "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=200"
                                                   "\nSET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=chamber TARGET=45"
                                                   "\nM117 PLA", can_restart))

    def test_apply_preset_honours_the_bounds_and_the_setup_gate(self):
        self.controls.apply_preset(0)
        self.controls.apply_preset(-1)
        self.data.rebuild(presets={"presets": {"pla": {"name": "PLA", "values": {
            "extruder": {"bool": True, "value": 200}}}}})
        self.commands.setup_allowed = False
        self.controls.apply_preset(0)
        self.controls.apply_preset(7)
        self.assertEqual(self.commands.calls, [])

    def test_a_preset_with_nothing_to_send_stays_silent(self):
        self.data.rebuild(presets={"presets": {"off": {"name": "Off", "values": {
            "sensor": {"bool": False}, "extruder": {"value": 200}}}}})
        self.controls.apply_preset(0)
        self.assertEqual(self.commands.calls, [])

    def test_cooldown_zeroes_every_heater_across_the_profiles(self):
        self.data.rebuild(presets={"presets": {
            "pla": {"name": "PLA", "values": {"extruder": {"bool": True, "value": 200},
                                              "temperature_fan chamber": {"bool": True, "value": 45},
                                              "sensor": {"bool": False}}},
            "abs": {"name": "ABS", "values": {"extruder": {"bool": True, "value": 250}}}}})
        self.controls.heaters_off()
        self.assertEqual(self.commands.calls[-1], ("script", "Cooldown",
                                                   "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0"
                                                   "\nSET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=chamber TARGET=0",
                                                   can_restart))

    def test_cooldown_needs_commands_and_the_setup_gate(self):
        self.data.rebuild(presets={"presets": {"pla": {"name": "PLA", "values": {
            "extruder": {"bool": False, "value": 200}}}}})
        self.controls.heaters_off()
        self.commands.setup_allowed = False
        self.controls.heaters_off()
        self.assertEqual(self.commands.calls, [])


class SetupAndRestartTests(ControlsCase):
    def test_each_setup_row_dispatches_its_script(self):
        self.data.rebuild(objects=("quad_gantry_level", "bed_mesh"),
                          auxiliary={"configfile": {"config": {}, "save_config_pending": True}})
        rows = (("home", "Home", "G28"), ("qgl", "QGL", "QUAD_GANTRY_LEVEL"),
                ("mesh", "Bed mesh", "BED_MESH_CALIBRATE"), ("save", "Save configuration", "SAVE_CONFIG"))
        for name, label, script in rows:
            before = len(self.commands.calls)
            self.controls.setup(name)
            self.assertEqual(self.commands.calls[before:], [("script", label, script, can_restart)], name)

    def test_setup_rows_the_printer_lacks_stay_silent(self):
        self.data.rebuild()
        self.controls.setup("qgl")
        self.controls.setup("mesh")
        self.controls.setup("save")
        self.assertEqual(self.commands.calls, [])
        self.controls.setup("home")
        self.assertEqual(len(self.commands.calls), 1)

    def test_setup_needs_the_command_lane_to_allow_setup(self):
        self.commands.setup_allowed = False
        self.controls.setup("home")
        self.assertEqual(self.commands.calls, [])

    def test_restarts_dispatch_the_host_endpoints_under_the_restart_row(self):
        self.controls.firmware_restart()
        self.controls.klipper_restart()
        self.controls.host_restart()
        self.assertEqual(self.commands.calls, [
            ("request", "Firmware restart", "printer/firmware_restart", {}, can_restart),
            ("request", "Klipper restart", "printer/restart", {}, can_restart),
            ("request", "Host restart", "machine/reboot", {}, can_restart)])

    def test_a_running_print_refuses_every_restart(self):
        self.data.observation = record(state="printing")
        self.controls.firmware_restart()
        self.controls.klipper_restart()
        self.controls.host_restart()
        self.assertEqual(self.commands.calls, [])

    def test_mesh_profile_quotes_the_name_and_rides_the_restart_row(self):
        self.data.rebuild(auxiliary={"bed_mesh": {"profiles": {"default": {}, "rough bed": {}}}})
        self.controls.mesh_profile("rough bed")
        self.assertEqual(self.commands.calls[-1],
                         ("script", "Load mesh rough bed", "BED_MESH_PROFILE LOAD='rough bed'", can_restart))
        self.controls.mesh_profile("absent")
        self.commands.setup_allowed = False
        self.controls.mesh_profile("default")
        self.assertEqual(len(self.commands.calls), 1)

    def test_clear_mesh_needs_the_row_and_a_loaded_mesh(self):
        self.controls.clear_mesh()
        self.assertEqual(self.commands.calls, [])
        self.mesh.snapshot = {"rows": 3}
        self.controls.clear_mesh()
        self.assertEqual(self.commands.calls[-1], ("script", "Clear bed mesh", "BED_MESH_CLEAR", can_restart))
        self.commands.setup_allowed = False
        self.controls.clear_mesh()
        self.assertEqual(len(self.commands.calls), 1)


class PowerAndExclusionTests(ControlsCase):
    def test_power_rows_render_every_reported_device(self):
        self.data.rebuild(power=[{"device": "printer", "status": "on", "locked_while_printing": False},
                                 {"device": "plain"},
                                 {"device": "dfu", "status": "off", "locked_while_printing": True},
                                 {"status": "orphan"}])
        rows = {row["name"]: row for row in self.controls.power_devices()}
        self.assertEqual(set(rows), {"printer", "plain", "dfu"})
        self.assertEqual(rows["printer"], {"name": "printer", "status": "on", "locked": False, "can_toggle": True})
        self.assertEqual(rows["plain"], {"name": "plain", "status": "unknown", "locked": False, "can_toggle": True})

    def test_a_locked_device_refuses_mid_print_and_an_unlocked_one_does_not(self):
        self.data.rebuild(power=[{"device": "printer", "status": "on"},
                                 {"device": "dfu", "status": "off", "locked_while_printing": True}])
        self.data.observation = record(state="printing")
        rows = {row["name"]: row for row in self.controls.power_devices()}
        self.assertTrue(rows["printer"]["can_toggle"])
        self.assertFalse(rows["dfu"]["can_toggle"])

    def test_power_rows_fail_closed_without_a_policy_record(self):
        # The record is absent, not merely stale: getattr must default to
        # None rather than the attribute existing with a permissive value.
        self.data.rebuild(power=[{"device": "printer", "status": "on"}])
        del self.data.observation
        self.assertEqual([row["can_toggle"] for row in self.controls.power_devices()], [False])

    def test_set_power_dispatches_only_for_a_toggleable_device(self):
        self.data.rebuild(power=[{"device": "printer", "status": "on"},
                                 {"device": "dfu", "status": "off", "locked_while_printing": True}])
        self.data.observation = record(state="printing")
        self.controls.set_power("dfu", True)
        self.assertEqual(self.commands.calls, [])
        self.controls.set_power("printer", False)
        self.assertEqual(self.commands.calls[-1],
                         ("send", "Power printer", "machine/device_power/device",
                          {"device": "printer", "action": "off"}))
        self.controls.set_power("printer", True)
        self.assertEqual(self.commands.calls[-1][3]["action"], "on")
        self.controls.set_power("absent", True)
        self.assertEqual(len(self.commands.calls), 2)

    def test_exclude_dispatches_the_escaped_name_mid_print(self):
        self.data.observation = record(state="printing")
        self.data.rebuild(auxiliary={"exclude_object": {"objects": [{"name": "part"}, {"name": 'a\\b"c\n'}]}})
        self.controls.exclude("part")
        self.assertEqual(self.commands.calls[-1][:3],
                         ("script", "Exclude part", 'EXCLUDE_OBJECT NAME="part"'))
        # The rule it rides is the lane's dispatch revalidation: the
        # mid-print row AND the name's own plate predicate.
        rule = self.commands.calls[-1][3]
        self.assertEqual(rule(record(state="printing")).mode, "allowed")
        self.assertEqual(rule(record(state="printing")).reason, "")
        self.assertEqual(rule(record(state="standby")).reason, R_NOT_PRINTING)
        # A quote, a backslash and a newline must not break out of the
        # quoted NAME — a newline would otherwise start a second command.
        self.controls.exclude('a\\b"c\n')
        self.assertEqual(self.commands.calls[-1][2], 'EXCLUDE_OBJECT NAME="a\\\\b\\"c "')

    def test_exclude_refuses_loudly_when_the_row_disallows(self):
        self.data.rebuild(auxiliary={"exclude_object": {"objects": [{"name": "part"}]}})
        self.controls.exclude("part")
        kind, message = self.commands.calls[-1]
        self.assertEqual(kind, "status")
        self.assertTrue(message.startswith("Exclude refused: "))
        self.assertIn("print", message)

    def test_exclude_without_a_record_names_the_unknown_state(self):
        self.data.rebuild(auxiliary={"exclude_object": {"objects": [{"name": "part"}]}})
        del self.data.observation
        self.controls.exclude("part")
        self.assertEqual(self.commands.calls, [("status", "Exclude refused: " + R_UNKNOWN)])

    def test_exclude_receipts_unknown_and_already_excluded_objects(self):
        # The no-confirm ruling: every gesture receipts, no-ops
        # included — silence would re-trigger the gesture.
        self.data.observation = record(state="printing")
        self.data.rebuild(auxiliary={"exclude_object": {"objects": [{"name": "part"}, {"name": "gone"}],
                                                        "excluded_objects": ["gone"]}})
        self.controls.exclude("absent")
        self.assertEqual(self.commands.calls[-1], ("status", "Exclude refused: 'absent' is not on the plate"))
        self.controls.exclude("gone")
        self.assertEqual(self.commands.calls[-1], ("status", "Exclude refused: 'gone' is already excluded"))

    def test_restore_dispatches_the_reset_line_with_the_name(self):
        self.data.observation = record(state="printing")
        self.data.rebuild(core={"exclude_object": {"objects": [{"name": "PART_A"}],
                                                   "excluded_objects": ["PART_A"]}})
        self.controls.restore("PART_A")
        self.assertEqual(self.commands.calls[-1][:3],
                         ("script", "Restore PART_A", 'EXCLUDE_OBJECT RESET=1 NAME="PART_A"'))

    def test_restore_refuses_an_empty_name_without_dispatch(self):
        self.data.observation = record(state="printing")
        self.data.rebuild(core={"exclude_object": {"objects": [{"name": "PART_A"}],
                                                   "excluded_objects": ["PART_A"]}})
        self.controls.restore("")
        self.assertEqual(self.commands.calls[-1], ("status", "Restore refused: no object named"))
        self.assertEqual(self.scripts(), [])

    def test_restore_never_emits_a_bare_reset(self):
        # The review's blocker: a RESET=1 without NAME clears every
        # exclusion on the plate — the line must always carry one.
        self.data.observation = record(state="printing")
        for name in ("PART_A", 'a\\b"c\n'):
            self.data.rebuild(core={"exclude_object": {"objects": [{"name": "PART_A"}],
                                                       "excluded_objects": [name]}})
            self.controls.restore(name)
            self.assertIn('RESET=1 NAME="', self.commands.calls[-1][2])

    def test_restore_refuses_a_not_excluded_name_with_words(self):
        self.data.observation = record(state="printing")
        self.data.rebuild(core={"exclude_object": {"objects": [{"name": "PART_A"}],
                                                   "excluded_objects": []}})
        self.controls.restore("PART_A")
        self.assertEqual(self.commands.calls[-1], ("status", "Restore refused: 'PART_A' is not excluded"))

    def test_the_latch_blocks_the_same_gesture_until_the_status_confirms(self):
        # The double tap inside the status lag stays one dispatch. The
        # release is the PLATE, not a manual confirmation: the status
        # that shows the exclusion landing is what frees the gesture.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 1)
        self.assertEqual(self.commands.calls[-1][0], "status")
        self.assertIn("already in flight", self.commands.calls[-1][1])
        self.plate(["PART_A"], excluded=["PART_A"])
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 2)

    def test_the_latch_never_wedges_the_rescue_direction(self):
        self.data.observation = record(state="printing")
        self.data.rebuild(core={"exclude_object": {"objects": [{"name": "PART_A"}, {"name": "PART_B"}],
                                                   "excluded_objects": ["PART_B"]}})
        self.controls.exclude("PART_A")
        # A restore of the other object dispatches while the exclude
        # is still in flight — the rescue path is never gated.
        self.controls.restore("PART_B")
        self.assertEqual(len(self.scripts()), 2)

    def plate(self, objects, excluded=()):
        self.data.rebuild(core={"exclude_object": {"objects": [{"name": name} for name in objects],
                                                   "excluded_objects": list(excluded)}})

    def test_a_confirmed_exclusion_releases_the_latch_for_the_next_gesture(self):
        # The exclude → restore → exclude cycle is a normal correction,
        # and it fits inside the latch's ceiling. The plate status
        # confirming the first exclusion IS the delivery — holding the
        # gesture until the ten-second backstop expires wedges the
        # third click on a command that finished long ago.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 1)
        self.plate(["PART_A"], excluded=["PART_A"])
        self.controls.restore("PART_A")
        self.assertEqual(len(self.scripts()), 2)
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 3)
        self.assertEqual(self.commands.calls[-1][0], "script")

    def test_a_confirmed_restore_releases_the_latch_for_the_next_gesture(self):
        self.data.observation = record(state="printing")
        self.plate(["PART_A"], excluded=["PART_A"])
        self.controls.restore("PART_A")
        self.assertEqual(len(self.scripts()), 1)
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 2)
        self.plate(["PART_A"], excluded=["PART_A"])
        self.controls.restore("PART_A")
        self.assertEqual(len(self.scripts()), 3)
        self.assertEqual(self.commands.calls[-1][0], "script")

    def test_the_latch_follows_the_plate_and_not_the_clock(self):
        # The plate turned over inside the ceiling (a finished print,
        # a new one): the armed gesture can never land on the plate
        # that does not list the name, so the name's next exclusion —
        # on the plate it is actually on — is not refused by it.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.plate(["PART_B"])
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 2)

    def test_pending_latches_do_not_survive_a_session_invalidation(self):
        # A printer switch (or a reconnect cycle) resets the lane and
        # the plate with it: a latch from the dead session must not
        # refuse the same gesture on the new one.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.data.invalidated.emit()
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 2)

    def test_a_dispatch_that_never_started_leaves_no_latch(self):
        # The transport was down: nothing is in flight, so the retry
        # must not be refused as one.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"])
        self.commands.started = False
        self.controls.exclude("PART_A")
        self.commands.started = True
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 2)
        self.assertEqual(self.commands.calls[-1][0], "script")

    def test_a_restore_dispatch_that_never_started_leaves_no_latch(self):
        self.data.observation = record(state="printing")
        self.plate(["PART_A"], excluded=["PART_A"])
        self.commands.started = False
        self.controls.restore("PART_A")
        self.commands.started = True
        self.controls.restore("PART_A")
        self.assertEqual(len(self.scripts()), 2)

    def test_the_latch_blocks_a_rapid_restore_until_the_plate_confirms(self):
        # The exclusion and restoration directions latch independently
        # and both release on their own plate status.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"], excluded=["PART_A"])
        self.controls.restore("PART_A")
        self.controls.restore("PART_A")
        self.assertEqual(len(self.scripts()), 1)
        self.assertIn("already in flight", self.commands.calls[-1][1])
        self.plate(["PART_A"])
        self.plate(["PART_A"], excluded=["PART_A"])
        self.controls.restore("PART_A")
        self.assertEqual(len(self.scripts()), 2)

    def test_a_refused_gesture_arms_no_latch(self):
        # Every refusal happens before the arm — a refused click must
        # never cost the user the next, legitimate one.
        self.data.observation = record(state="printing")
        self.plate(["PART_A"], excluded=["PART_A"])
        self.controls.exclude("PART_A")  # already excluded: refused
        self.plate(["PART_A"])
        self.controls.exclude("PART_A")
        self.assertEqual(len(self.scripts()), 1)


class FailClosedTests(ControlsCase):
    def test_every_gate_fails_closed_without_a_policy_record(self):
        self.data.rebuild(objects=("gcode_macro TEST",))
        del self.data.observation
        self.data.changed.emit()
        self.controls.run_macro("TEST")
        self.controls.z_offset(0.1)
        self.controls.firmware_restart()
        self.assertEqual(self.commands.calls, [])
        self.assertFalse(self.controls.values["canRunSetup"])


class RealTuningTests(ControlsCase):
    def test_the_real_tuning_keeps_a_released_value_across_polls(self):
        # The composition with the production debouncer: the value the
        # user released must survive the next poll's rebuild, or the
        # Slider jumps back to the printer's stale report mid-gesture.
        tuning = MonitorTuning(self.data, self.commands)
        self.addCleanup(tuning.reset)
        controls = MonitorControls(self.data, self.commands, tuning, self.mesh, {})
        self.data.rebuild(auxiliary={"fan": {"speed": 1}})
        controls.output("fan", "fan", 40)
        self.data.rebuild(auxiliary={"fan": {"speed": 1}})
        self.assertEqual(controls.values["fanControlItems"][0]["percent"], 40)
        self.data.invalidated.emit()
        self.assertEqual(controls.values["fanControlItems"][0]["percent"], 100)


if __name__ == "__main__":
    unittest.main()
