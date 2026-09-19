"""Coverage for the settings, formatting and persistence surfaces.

Targets the branches the existing suites leave untouched rather than
re-asserting covered ground: the pure formatting helpers, the config
record's coercion, the one-shot migration's control flow, and the
Qt-side owners' branch behaviour. The doubles follow the shapes the
existing suites already establish (qt_runtime_support's runtime,
Preferences and Application; the monitor-controls data double's
surface), so nothing here invents a second dialect of fake.

Leftovers that need a live Cura/UM host are named in the docstrings of
the classes that skip on Qt availability.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
import pathlib
from types import ModuleType, SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

import plugins

from plugins.MonitorFormatting import (chart_label, chart_temperature_objects, core_values,
                                       day_offset_suffix, duration, endstop_values,
                                       estimate_remaining, factor_percent, fan_writable,
                                       file_disk_text, file_duration_short, file_filament,
                                       file_row_payload, file_size, file_temperature,
                                       file_timestamp, filament_diameter,
                                       filament_total_mm_from_file, friendly, layer_readout,
                                       height_readout, literal_default, mesh_profiles, number,
                                       object_kind, parse_bed_mesh, parse_mcu_stats,
                                       peripheral_values, preview_eta_text, print_job_caption,
                                       result, wanted_object, format_bytes, normalise_mesh,
                                       filament_total_mm_from_gcode, infer_macro_parameters,
                                       preview_block, preview_temperature_pair)
from plugins.MonitorPermissions import (Observation, R_ALREADY_PAUSED, R_ALREADY_PRINTING,
                                        R_BUSY, R_CLEARED_PAUSE, R_DISCONNECTED, R_ESTOPPED,
                                        R_LOCKED, R_NO_PAUSED_PRINT, R_NOTHING_TO_PAUSE,
                                        R_NOT_PRINTING, R_PAUSE_FIRST, R_PAUSED_NOTE,
                                        R_PRINTING, R_UNKNOWN, R_UNSUPPORTED, REASON_DETAIL,
                                        can_exclude, can_jog, can_macro, can_pause, can_power,
                                        can_restart, can_resume, can_set_absolute,
                                        can_start_print, can_z_offset, jog_caption,
                                        section_reason)
from plugins.MonitorTemperatureHistory import (PALETTE, TemperatureHistory, _segments,
                                               chart_payload, series_metadata)
from plugins.PauseScheduleService import PauseScheduleService, due_end_of_layer_pauses
from plugins.PersistenceMigration import (MigrationOutcome, _clean_preferences, _read_old_chrome,
                                          _record, _remove_old_state_file, _verify_new_files,
                                          _write_new_files, read_source, run_migration,
                                          split_record, write_backup)
from plugins.PluginPersistence import PluginPersistence
from plugins.PreviewFormatting import (pause_can_toggle, pause_eta, pause_items, pause_summary,
                                       pause_unavailable, status_icon, status_text)
from plugins.PrinterConfig import (FeedMode, PrinterConfig, PrinterConfigStore, normalise_url,
                                   normalise_temperature_chart, upload_path_safe)
from plugins.StateStore import StateStore
from qt_runtime_support import QT_AVAILABLE, Preferences, runtime

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins"


def _pretty_save(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal

    # One runtime for the whole file: the Cura/UM stubs the Qt-side
    # modules import must be resident before those modules load.
    _RUNTIME = runtime()
    _QT = _RUNTIME.__enter__()

    from plugins.CameraBridge import CameraBridge
    from plugins.GCodeIndexService import GCodeIndexService, IndexView
    from plugins.GCodeIndex import LayerMotionIndex
    from plugins.MonitorCamera import MonitorCamera
    from plugins.MonitorCommands import MonitorCommands
    from plugins.MonitorTuning import MonitorTuning
    from plugins.PauseController import PauseController
    from plugins.PrinterBinding import PrinterBinding, _REMOVAL_WIPE_FIELDS
    from plugins.UiStateStore import UiStateStore


def observation(**overrides):
    fields = dict(active=True, connection="yes", state="standby", homed_axes="xyz",
                  assumed_stopped=False, save_config_pending=False, controls_locked=False,
                  busy=False)
    fields.update(overrides)
    return Observation(**fields)


# --------------------------------------------------------------------------
# MonitorFormatting: the pure projections the monitor panes read.
# --------------------------------------------------------------------------

class MonitorFormattingCoverageTests(unittest.TestCase):
    def test_result_unwraps_the_moonraker_envelope(self):
        self.assertEqual(result({"result": {"status": {}}}), {"status": {}})
        self.assertEqual(result({"status": {}}), {"status": {}})
        self.assertEqual(result("not a mapping"), {})
        self.assertEqual(result(None), {})

    def test_number_rejects_non_finite_and_unparseable_values(self):
        self.assertEqual(number("1.5"), 1.5)
        self.assertEqual(number(float("inf")), 0.0)
        self.assertEqual(number(float("nan"), -1.0), -1.0)
        self.assertEqual(number("abc"), 0.0)
        self.assertEqual(number(None), 0.0)

    def test_factor_percent_reads_an_absent_factor_honestly(self):
        self.assertEqual(factor_percent(1.0), "100%")
        self.assertEqual(factor_percent(0.425), "42%")
        self.assertEqual(factor_percent(None), "—")
        self.assertEqual(factor_percent("nonsense"), "—")

    def test_friendly_names_the_known_object_families(self):
        self.assertEqual(friendly("extruder"), "Hotend")
        self.assertEqual(friendly("extruder2"), "Hotend 3")
        self.assertEqual(friendly("heater_bed"), "Bed")
        self.assertEqual(friendly("fan"), "Part fan")
        self.assertEqual(friendly("temperature_fan chamber"), "Chamber")
        self.assertEqual(friendly("mcu_extra"), "Mcu extra")

    def test_chart_label_keeps_the_family_and_the_host_marker(self):
        # A temperature_fan's reading must not read as a heater of the
        # same suffix, and the host sensor must not collide with a
        # temperature_sensor of the same suffix.
        self.assertEqual(chart_label("temperature_fan chamber"), "Chamber (fan)")
        self.assertEqual(chart_label("temperature_host raspberry_pi"), "Raspberry pi (host)")
        self.assertEqual(chart_label("heater_bed"), "Bed")

    def test_fan_writable_only_for_the_generic_and_part_fan(self):
        self.assertTrue(fan_writable("fan"))
        self.assertTrue(fan_writable("Fan"))
        self.assertTrue(fan_writable("fan_generic exhaust"))
        self.assertFalse(fan_writable("heater_fan hotend"))
        self.assertFalse(fan_writable("controller_fan1"))
        self.assertFalse(fan_writable("temperature_fan chamber"))

    def test_object_kind_classifies_every_family(self):
        for name, kind in (("heater_bed", "system"), ("extruder", "system"),
                           ("extruder12", "system"), ("quad_gantry_level", "system"),
                           ("gcode_macro PAUSE", "macro"), ("fan_generic x", "fan"),
                           ("neopixel led", "led"), ("output_pin fan2", "pwm"),
                           ("temperature_sensor chamber", "temperature"),
                           ("filament_switch_sensor runout", "filament"), ("mcu host", "mcu")):
            self.assertEqual(object_kind(name), kind, name)
        # An unknown family classifies as nothing and is never queried.
        self.assertEqual(object_kind("some_future_object"), "")
        self.assertEqual(object_kind(None), "")
        self.assertFalse(wanted_object("some_future_object"))
        self.assertTrue(wanted_object("fan_generic x"))
        self.assertFalse(wanted_object("gcode_macro PAUSE"))

    def test_chart_temperature_objects_skips_non_readings_and_system_names(self):
        # Only heater_bed and extruders of the system family chart; a
        # charted name with no finite reading is dropped for this tick.
        self.assertEqual(chart_temperature_objects({
            "toolhead": {"position": [0, 0, 0]},
            "system_stats": {"memavail": 1},
            "fan": "not a mapping",
            "heater_generic chamber": {"temperature": None},
        }), {})
        self.assertEqual(set(chart_temperature_objects({
            "extruder": {"temperature": 200.0},
            "gcode_macro X": {"temperature": 20.0},
        })), {"extruder"})

    def test_chart_temperature_objects_drops_a_fan_mirroring_a_heater(self):
        # A temperature_fan reports its heater's own reading; charting it
        # would draw the same curve twice. A fan that reads differently
        # stays.
        self.assertEqual(set(chart_temperature_objects({
            "temperature_fan chamber": {"temperature": 60.0},
            "heater_bed": {"temperature": 60.0},
            "extruder": {"temperature": 200.0},
        })), {"heater_bed", "extruder"})
        self.assertEqual(set(chart_temperature_objects({
            "temperature_fan chamber": {"temperature": 21.0},
            "heater_bed": {"temperature": 60.0},
        })), {"heater_bed", "temperature_fan chamber"})

    def test_preview_temperature_pair_reads_the_hotend_and_the_bed(self):
        self.assertEqual(preview_temperature_pair({
            "extruder": {"temperature": 200.4, "target": 200.0},
            "extruder1": {"temperature": 21.0, "target": 0.0},
            "heater_bed": {"temperature": 60.0, "target": 65.0},
        }), ("200.4 → 200.0 °C", "60.0 → 65.0 °C"))
        # The first extruder wins; a heater with no setpoint and a
        # pre-first-sample 0.0 reading are both "—".
        self.assertEqual(preview_temperature_pair({
            "extruder": {"temperature": 0.0},
            "extruder1": {"temperature": 21.0},
        }), ("—", "—"))
        self.assertEqual(preview_temperature_pair({
            "heater_bed": "bad", "temperature_fan chamber": {"temperature": 21.0},
        }), ("—", "—"))

    def test_preview_block_publishes_the_pair_and_the_policy_verdicts(self):
        block = preview_block({"extruder": {"temperature": 200.4, "target": 205.0},
                               "heater_bed": {"temperature": 60.0}},
                              observation(state="printing", pause_resume_supported=True),
                              stamp=12)
        self.assertEqual(block["stamp"], 12.0)
        self.assertEqual(block["hotend"], "200.4 → 205.0 °C")
        self.assertEqual(block["bed"], "60.0 °C")
        self.assertFalse(block["inactive"])
        self.assertEqual(block["state"], "printing")
        self.assertTrue(block["canPause"])
        self.assertEqual(block["pauseReason"], "")
        self.assertFalse(block["canResume"])
        self.assertEqual(block["resumeReason"], R_ALREADY_PRINTING)
        self.assertTrue(block["resumeReasonDetail"])
        self.assertFalse(block["busy"])
        # An unobserved printer fails both rows closed rather than
        # omitting keys.
        closed = preview_block({}, None, stamp=0, inactive=True)
        self.assertEqual((closed["hotend"], closed["bed"]), ("—", "—"))
        self.assertEqual(closed["state"], "")
        self.assertTrue(closed["inactive"])
        self.assertFalse(closed["canPause"])
        self.assertFalse(closed["canResume"])
        self.assertEqual(closed["pauseReason"], R_UNKNOWN)
        self.assertTrue(closed["pauseReasonDetail"])

    def test_filament_total_sums_every_value_on_the_declaration_line(self):
        self.assertEqual(filament_total_mm_from_gcode(
            b"G28\n;Filament used: 2.157m, 1.5m\n"), 3657.0)
        self.assertIsNone(filament_total_mm_from_gcode(b"G28\n"))
        # A marker line with no value carries no total.
        self.assertIsNone(filament_total_mm_from_gcode(b";Filament used:\n"))
        # A hostile digit run makes the whole line untrustworthy, and a
        # sum that overflows is no total either.
        self.assertIsNone(filament_total_mm_from_gcode(b";Filament used: " + b"9" * 400 + b"\n"))
        huge = b"1" + b"0" * 308
        self.assertIsNone(filament_total_mm_from_gcode(
            b";Filament used: " + huge + b", " + huge + b"\n"))

    def test_infer_macro_parameters_reads_every_declaration_shape(self):
        found = {item["name"]: item for item in infer_macro_parameters(
            "{% set SPEED = params.SPEED|default(100)|int %}\n"
            "{% set NAME = params['NAME'] %}\n"
            "{% set FLAG = params.FLAG|default('true')|lower %}\n"
            "{% set TEMP = params.TEMP|default(210.5)|float %}\n"
            "{% set SPEED = params.SPEED|default(50)|int %}\n"
            "{% set N = params.N %}\n{% set N = params.N|default(3)|int %}\n"
            "{% set TOOL = params.TOOL %}\n{% set TOOL = params.TOOL|default('t0') %}\n")}
        self.assertEqual(found["SPEED"], {"name": "SPEED", "type": "int", "default": "100",
                                          "required": False, "hasDefault": True})
        self.assertEqual(found["NAME"], {"name": "NAME", "type": "string", "default": "",
                                         "required": True, "hasDefault": False})
        self.assertEqual(found["FLAG"]["type"], "bool")
        self.assertEqual(found["FLAG"]["default"], "True")
        self.assertEqual(found["TEMP"], {"name": "TEMP", "type": "float", "default": "210.5",
                                         "required": False, "hasDefault": True})
        # A later typed declaration upgrades the row, and a later
        # default settles a row that had none.
        self.assertEqual(found["N"]["type"], "int")
        self.assertEqual(found["N"]["default"], "3")
        self.assertEqual(found["TOOL"]["default"], "t0")
        self.assertFalse(found["TOOL"]["required"])

    def test_preview_eta_text_reads_the_layer_anchor_first(self):
        physical = SimpleNamespace(layer_eta=3661.0, estimated_time=None, metadata_complete=True)
        text = preview_eta_text({"print_stats": {"state": "printing"}, "virtual_sdcard": {}}, physical)
        self.assertTrue(text.startswith("01:01:01 · ≈"), text)
        # A paused print and an idle printer say what they know.
        self.assertEqual(preview_eta_text({"print_stats": {"state": "paused"}}, physical), "Paused")
        self.assertEqual(preview_eta_text({"print_stats": {"state": "standby"}}, physical), "—")
        # No anchor and no estimate: nothing to say.
        bare = SimpleNamespace(layer_eta=None, estimated_time=None, metadata_complete=False)
        self.assertEqual(preview_eta_text({"print_stats": {"state": "printing"},
                                           "virtual_sdcard": {}}, bare), "—")

    def test_preview_eta_text_blends_when_the_anchor_is_missing(self):
        physical = SimpleNamespace(layer_eta=None, estimated_time=7200.0, metadata_complete=True)
        text = preview_eta_text({"print_stats": {"state": "printing", "print_duration": 3600},
                                 "virtual_sdcard": {"progress": 0.5}}, physical)
        self.assertTrue(text.startswith("01:00:00"), text)

    def test_day_offset_suffix_only_marks_a_later_day(self):
        now = datetime.now().astimezone()
        self.assertEqual(day_offset_suffix(now), "")
        self.assertEqual(day_offset_suffix(now + timedelta(days=2)), " +2")

    def test_duration_clamps_negatives_and_unparseable_input(self):
        self.assertEqual(duration(3661), "01:01:01")
        self.assertEqual(duration(-50), "00:00:00")
        self.assertEqual(duration("nonsense"), "00:00:00")

    def test_file_size_picks_the_unit(self):
        self.assertEqual(file_size(2_500_000_000), "2.5 GB")
        self.assertEqual(file_size(2_500_000), "2.5 MB")
        self.assertEqual(file_size(2600), "3 KB")
        self.assertEqual(file_size(None), "0 KB")

    def test_file_timestamp_falls_back_for_absent_or_corrupt_instants(self):
        now = datetime.now().timestamp()
        self.assertEqual(file_timestamp(0, now), "—")
        self.assertEqual(file_timestamp(-5, now), "—")
        self.assertEqual(file_timestamp(1e20, now), "—")
        stamp = datetime(2024, 9, 10, 14, 32).timestamp()
        self.assertEqual(file_timestamp(stamp, stamp), "10 Sep 14:32")
        self.assertEqual(file_timestamp(stamp, datetime(2026, 1, 1).timestamp()), "10 Sep 2024")

    def test_file_duration_short_covers_each_magnitude(self):
        self.assertEqual(file_duration_short(0), "—")
        self.assertEqual(file_duration_short(-10), "—")
        self.assertEqual(file_duration_short(5400), "1 h 30 min")
        self.assertEqual(file_duration_short(7200), "2 h")
        self.assertEqual(file_duration_short(600), "10 min")

    def test_file_disk_text_and_unit_formatters(self):
        self.assertEqual(file_disk_text({}), "—")
        self.assertEqual(file_disk_text({"total": 0, "free": 0}), "—")
        self.assertIn("free of", file_disk_text({"total": 8_000_000_000, "free": 4_000_000_000}))
        self.assertEqual(file_temperature(205.4), "205 °C")
        self.assertEqual(file_temperature(0), "—")
        self.assertEqual(file_temperature(-3), "—")
        self.assertEqual(file_filament(1500.0), "1.50 m")
        self.assertEqual(file_filament(0), "—")

    def test_file_row_payload_projects_every_cell_and_the_status_fallback(self):
        row = SimpleNamespace(filename="part.gcode", relpath="projects/part.gcode", modified=0,
                              size=None, attempts=0, last_status="", print_start_time=None,
                              object_height=None, layer_height=None, estimated_time=0,
                              last_print=0, slicer="", extruder=0, bed=0, filament=0)
        payload = file_row_payload(row, 0)
        self.assertEqual(payload["folder"], "projects")
        self.assertEqual(payload["size"], "—")
        self.assertEqual(payload["attempts"], "—")
        self.assertEqual(payload["status"], "Never printed")
        self.assertEqual(payload["statusColour"], "text_inactive")
        self.assertEqual(payload["objH"], "—")
        self.assertTrue(payload["unparsed"])
        self.assertFalse(payload["hasThumb"])
        # A file printed beyond the loaded history window is told apart
        # from one never printed at all.
        row.print_start_time = 100.0
        self.assertEqual(file_row_payload(row, 0)["status"], "Missing history")
        row.last_status = "completed"
        self.assertEqual(file_row_payload(row, 0)["statusColour"], "#43a047")
        row.last_status = ""  # the payload falls back per call
        row.relpath = "part.gcode"
        row.size = 2_500_000
        row.attempts = 3
        row.object_height = 12.0
        row.layer_height = 0.2
        row.estimated_time = 5400
        row.slicer = "Cura"
        row.extruder = 205.4
        row.bed = 60.0
        row.filament = 1500.0
        payload = file_row_payload(row, 0)
        self.assertEqual(payload["folder"], "")
        self.assertEqual(payload["size"], "2.5 MB")
        self.assertEqual(payload["attempts"], "3")
        self.assertEqual(payload["objH"], "12.00 mm")
        self.assertEqual(payload["layerH"], "0.20 mm")
        self.assertEqual(payload["est"], "1 h 30 min")
        self.assertEqual(payload["extr"], "205 °C")
        self.assertEqual(payload["filament"], "1.50 m")
        self.assertFalse(payload["unparsed"])

    def test_estimate_remaining_prefers_the_file_blend_only_in_band(self):
        # A file-only estimate (no metadata total) needs progress and a
        # little elapsed time before it is trusted.
        self.assertIsNone(estimate_remaining(5, 0.01, None, True))
        self.assertIsNone(estimate_remaining(120, 0.5, None, False))
        self.assertAlmostEqual(estimate_remaining(15, 0.01, None, True), 1485, delta=2)
        # A metadata total with a by-file blend inside the plausibility
        # band blends the two; outside it the metadata read wins.
        self.assertAlmostEqual(estimate_remaining(3600, 0.5, 7200, True), 3600, delta=2)
        blended = estimate_remaining(3600, 0.5, 7300, True)
        self.assertLess(blended, 3700)
        # Elapsed beyond the estimate: the by-file read carries on, and
        # nothing at all is left when there is no blend either.
        self.assertAlmostEqual(estimate_remaining(8000, 0.99, 7200, True), 80.8, delta=1.0)
        self.assertEqual(estimate_remaining(8000, 0.0001, 7200, False), 0.0)
        # A metadata total far beyond the by-file read keeps its own
        # value: the blend only applies inside the plausibility band.
        self.assertEqual(estimate_remaining(100, 0.5, 100000, True), 99900)

    def test_filament_diameter_follows_the_active_extruder(self):
        auxiliary = {
            "toolhead": {"extruder": "extruder1"},
            "configfile": {"settings": {"extruder": {"filament_diameter": 1.75},
                                        "extruder1": {"filament_diameter": 2.85}}},
        }
        self.assertEqual(filament_diameter(auxiliary), 2.85)
        # An unconventional tool or a non-mapping section yields None.
        self.assertIsNone(filament_diameter({"toolhead": {"extruder": "extruder_stepper e0"},
                                             "configfile": {"settings": {}}}))
        self.assertIsNone(filament_diameter({"toolhead": {"extruder": "extruder"},
                                             "configfile": {"settings": "bad"}}))
        self.assertIsNone(filament_diameter({"toolhead": {"extruder": "extruder"},
                                             "configfile": {"settings": {"extruder": 1.75}}}))
        self.assertIsNone(filament_diameter({}))

    def _core_snapshot(self, **print_stats):
        stats = {"state": "printing", "print_duration": 30}
        stats.update(print_stats)
        return SimpleNamespace(core={"print_stats": stats, "virtual_sdcard": {}, "gcode_move": {},
                                     "motion_report": {}},
                               auxiliary={}, server={})

    def test_core_values_reads_every_layer_text_shape(self):
        physical = SimpleNamespace(layer=SimpleNamespace(index=None, total=None, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=None)
        self.assertEqual(core_values(self._core_snapshot(), physical, True)["monitorLayer"], "—")
        physical.layer = SimpleNamespace(index=None, total=5, thickness=None)
        self.assertEqual(core_values(self._core_snapshot(), physical, True)["monitorLayer"], "— / 5")
        physical.layer = SimpleNamespace(index=2, total=None, thickness=None)
        self.assertEqual(core_values(self._core_snapshot(), physical, True)["monitorLayer"], "3")

    def test_core_values_reports_the_motion_rows_and_their_absences(self):
        snapshot = self._core_snapshot()
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=10, thickness=0.2),
                                   estimated_time=None, metadata_complete=True, layer_eta=None)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorPosition"], "—")
        self.assertEqual(values["monitorVelocity"], "—")
        self.assertEqual(values["monitorFlowRate"], "—")
        self.assertEqual(values["monitorFlowDiameter"], "—")
        self.assertEqual(values["monitorAccelLimit"], "—")
        self.assertEqual(values["monitorLayerProgress"], -1.0)
        snapshot.core["motion_report"] = {"live_position": [1.0, 2.0, 3.0], "live_velocity": 45.6,
                                          "live_extruder_velocity": 5.0}
        snapshot.auxiliary = {
            "toolhead": {"max_accel": 3000},
            "configfile": {"settings": {"extruder": {"filament_diameter": 1.75}}},
        }
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorPosition"], "X 1.0   Y 2.0   Z 3.00")
        self.assertEqual(values["monitorPositionCompact"], "X 1.0 Y 2.0 Z 3.00")
        self.assertEqual((values["monitorPositionX"], values["monitorPositionY"],
                          values["monitorPositionZ"]), ("X 1.0", "Y 2.0", "Z 3.00"))
        self.assertEqual(values["monitorVelocity"], "45.6 mm/s")
        self.assertEqual(values["monitorFlowRate"], "12.0 mm³/s")
        self.assertEqual(values["monitorFlowDiameter"], "1.75 mm")
        self.assertEqual(values["monitorAccelLimit"], "3000 mm/s²")

    def test_core_values_reads_the_snapshot_filament_total_as_a_fallback(self):
        snapshot = self._core_snapshot(filament_used=3500.0)
        snapshot.filament_total = 42000.0
        physical = SimpleNamespace(layer=SimpleNamespace(index=1, total=2, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=None,
                                   filament_total=None, layer_progress=0.5)
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["filamentUsed"], "3.50 m")
        self.assertEqual(values["filamentRemaining"], "38.50 m")
        self.assertEqual(values["monitorLayerProgress"], 0.5)
        self.assertEqual(values["monitorLayerSource"], "")

    def test_core_values_names_disconnection_and_an_overtaken_total(self):
        snapshot = self._core_snapshot(filament_used=50000.0)
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=None, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=None,
                                   filament_total=42000.0)
        values = core_values(snapshot, physical, False)
        self.assertEqual(values["monitorState"], "Disconnected")
        # Used overtaking the declared total means the total is
        # untrustworthy: "—", never a confident clamped zero.
        self.assertEqual(values["filamentRemaining"], "—")

    def test_core_values_reads_the_printing_eta_and_its_basis(self):
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=None, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=3661)
        values = core_values(self._core_snapshot(), physical, True)
        self.assertEqual(values["monitorEta"], "01:01:01")
        self.assertEqual(values["monitorEtaBasis"], "index")
        self.assertRegex(values["monitorFinish"], r"^\d{2}:\d{2}( \+\d)?$")
        # Without the anchor the metadata blend takes over, and the
        # basis says so.
        snapshot = self._core_snapshot(print_duration=3600)
        snapshot.core["virtual_sdcard"] = {"progress": 0.5}
        physical.layer_eta = None
        physical.estimated_time = 7200
        values = core_values(snapshot, physical, True)
        self.assertEqual(values["monitorEta"], "01:00:00")
        self.assertEqual(values["monitorEtaBasis"], "blend")
        # A far estimate names the weekday it lands on.
        physical.layer_eta = 72000
        values = core_values(self._core_snapshot(), physical, True)
        self.assertEqual(values["monitorEta"], "20:00:00")
        self.assertRegex(values["monitorFinish"], r"^[A-Z][a-z]{2} \d{2}:\d{2}( \+\d+)?$")

    def test_core_values_reads_the_paused_and_idle_eta_words(self):
        physical = SimpleNamespace(layer=SimpleNamespace(index=0, total=None, thickness=None),
                                   estimated_time=None, metadata_complete=True, layer_eta=None)
        paused = self._core_snapshot(state="paused")
        self.assertEqual(core_values(paused, physical, True)["monitorEta"], "Paused")
        self.assertEqual(core_values(paused, physical, True)["monitorEtaBasis"], "")

    def test_endstop_values_skips_blank_states_and_silences_a_disconnected_summary(self):
        snapshot = SimpleNamespace(endstops={"x": "  ", "y": "open"})
        values = endstop_values(snapshot)
        self.assertEqual([item["name"] for item in values["endstopItems"]], ["Y"])
        self.assertEqual(values["endstopSummary"], "")
        self.assertEqual(endstop_values(SimpleNamespace(endstops={}), connected=False)["endstopSummary"], "")

    def test_parse_mcu_stats_accepts_maps_and_klipper_strings(self):
        self.assertEqual(parse_mcu_stats({"freq": "40000000", "mcu_awake": "bad"}), {"freq": 40000000.0})
        self.assertEqual(parse_mcu_stats("freq=40000000, mcu_awake=0.05"), {"freq": 40000000.0,
                                                                          "mcu_awake": 0.05})
        self.assertEqual(parse_mcu_stats(None), {})
        self.assertEqual(parse_mcu_stats("no equals signs here"), {})

    def test_format_bytes_marks_absent_and_negative_values(self):
        self.assertEqual(format_bytes(None), "—")
        self.assertEqual(format_bytes(-1), "—")
        self.assertEqual(format_bytes(2_500_000), "2.50 MB")
        self.assertEqual(format_bytes(2500), "2.5 kB")
        self.assertEqual(format_bytes(512), "512 B")

    def test_peripheral_values_projects_the_auxiliary_status(self):
        snapshot = SimpleNamespace(
            auxiliary={
                "heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.4},
                "temperature_host raspberry_pi": {"temperature": 45.5},
                "fan": {"speed": 0.5, "rpm": 1234},
                "heater_fan hotend": {"speed": 1.0},
                "filament_switch_sensor runout": {"filament_detected": False},
                "filament_motion_sensor move": {"filament_detected": True, "enabled": False},
                "mcu": {"last_stats": "freq=40000000 mcu_awake=0.05 mcu_task_avg=0.00002 "
                                      "bytes_write=2048 bytes_read=1024",
                        "memory_free": 2_500_000, "mcu_version": "v0.12",
                        "mcu_constants": {"CLOCK_FREQ": 40000000}},
                "mcu extra": {"last_stats": {"freq": 0}, "mcu_version": ""},
                "exclude_object": {"objects": [{"name": "cube"}, {"name": "sphere"}, "junk"],
                                   "excluded_objects": ["sphere"], "current_object": "cube"},
                "system_stats": {"memavail": 2_097_152, "sysload": 1.5},
                "webhooks": {"state": "ready"},
            },
            server={"moonraker_version": "v1.3", "klippy_state": "ready"},
            printer={"software_version": "v0.12"})
        values = peripheral_values(snapshot)
        by_name = {item["name"]: item for item in values["temperatureItems"]}
        self.assertIn("Bed", by_name)
        self.assertIn("→ 60 °C", by_name["Bed"]["detail"])
        self.assertIn("· 40%", by_name["Bed"]["detail"])
        self.assertIn("45.5 °C", by_name["Raspberry pi (host)"]["detail"])
        self.assertEqual(values["cpuTemperature"], "45.5 °C")
        fans = {item["name"]: item for item in values["fanItems"]}
        self.assertEqual(fans["Part fan"]["speed"], 0.5)
        self.assertIn("1,234 RPM", fans["Part fan"]["detail"])
        sensors = {item["name"]: item for item in values["filamentSensorItems"]}
        self.assertEqual(sensors["Runout"]["state"], "Runout / not detected")
        self.assertEqual(sensors["Move"]["state"], "Disabled")
        mcus = {item["name"]: item for item in values["mcuItems"]}
        self.assertEqual(mcus["Main MCU"]["version"], "v0.12")
        self.assertEqual(mcus["Main MCU"]["task"], "avg 20.0 µs")
        self.assertEqual(mcus["Main MCU"]["frequency"], "40.000 MHz")
        self.assertIn("TX 2.0 kB", mcus["Main MCU"]["transport"])
        self.assertEqual(mcus["Extra"]["frequency"], "—")
        self.assertEqual([item["name"] for item in values["excludeObjectItems"]], ["cube", "sphere"])
        self.assertTrue(values["excludeObjectItems"][1]["excluded"])
        self.assertTrue(values["excludeObjectItems"][0]["current"])
        self.assertEqual(values["memoryAvailable"], "2.00 GB")
        self.assertEqual(values["hostLoad"], "1.50")
        self.assertEqual(values["klippyState"], "Ready")
        self.assertEqual(values["moonrakerVersion"], "v1.3")
        self.assertEqual(values["klipperVersion"], "v0.12")
        self.assertEqual(values["cpuTemperature"], "45.5 °C")

    def test_peripheral_values_survives_an_empty_auxiliary(self):
        snapshot = SimpleNamespace(auxiliary={}, server={}, printer={})
        values = peripheral_values(snapshot)
        self.assertEqual(values["temperatureItems"], [])
        self.assertEqual(values["mcuSummary"], "—")
        self.assertEqual(values["memoryAvailable"], "—")
        self.assertEqual(values["cpuTemperature"], "—")
        self.assertEqual(values["klippyState"], "Unknown")
        self.assertEqual(values["hostLoad"], "—")
        self.assertEqual(values["memoryAvailable"], "—")

    def test_peripheral_values_reports_a_small_memory_reading_in_megabytes(self):
        snapshot = SimpleNamespace(auxiliary={"system_stats": {"memavail": 524288}},
                                   server={}, printer={})
        self.assertEqual(peripheral_values(snapshot)["memoryAvailable"], "512 MB")

    def test_normalise_mesh_rejects_malformed_matrices(self):
        self.assertIsNone(normalise_mesh("nope"))
        self.assertIsNone(normalise_mesh([[1, 2]]))                    # a single row
        self.assertIsNone(normalise_mesh([[1, 2], [1]]))               # a short row
        self.assertIsNone(normalise_mesh([[1, 2], [1, 2, 3]]))         # ragged
        self.assertIsNone(normalise_mesh([[1, 2], [1, "x"]]))          # non-numeric
        self.assertEqual(normalise_mesh([["1", 2], [3, 4]]), [[1.0, 2.0], [3.0, 4.0]])

    def test_parse_bed_mesh_validates_the_source_and_the_bounds(self):
        self.assertEqual(parse_bed_mesh("nope"), {})
        self.assertEqual(parse_bed_mesh({}), {})
        base = {"mesh_min": [0, 0], "mesh_max": [100, 100], "profile_name": " default "}
        mesh = parse_bed_mesh({**base, "probed_matrix": [[1, 2], [3, 4]]})
        self.assertEqual(mesh["source"], "probed_matrix")
        self.assertEqual(mesh["profile"], "default")
        self.assertEqual((mesh["rows"], mesh["columns"]), (2, 2))
        self.assertEqual((mesh["minimum"], mesh["maximum"], mesh["range"]), (1, 4, 3))
        self.assertEqual(parse_bed_mesh({**base, "mesh_matrix": [[1, 2], [3, 4]]})["source"],
                         "mesh_matrix")
        # Bounds must be numeric, complete and non-degenerate.
        self.assertEqual(parse_bed_mesh({"mesh_matrix": [[1, 2], [3, 4]], "mesh_min": [0],
                                         "mesh_max": [100, 100]}), {})
        self.assertEqual(parse_bed_mesh({"mesh_matrix": [[1, 2], [3, 4]], "mesh_min": [0, 0],
                                         "mesh_max": [0, 100]}), {})
        self.assertEqual(parse_bed_mesh({"mesh_matrix": [[1, 2], [3, 4]], "mesh_min": ["a", 0],
                                         "mesh_max": [100, 100]}), {})

    def test_mesh_profiles_reads_every_listing_shape(self):
        self.assertEqual(mesh_profiles("nope"), [])
        self.assertEqual(mesh_profiles({}), [])
        self.assertEqual(mesh_profiles({"profiles": {"B": {}, "A": {}}}), ["A", "B"])
        self.assertEqual(mesh_profiles({"profiles": ["b", {"name": "a"}, {"profile": "c"}, 7, None]}),
                         ["a", "b", "c"])
        # The active profile leads, then "default", then the sorted rest.
        self.assertEqual(mesh_profiles({"profiles": ["a", "b", "default"], "profile_name": "b"}),
                         ["b", "a", "default"])
        self.assertEqual(mesh_profiles({"profiles": ["b", "a", "default"]}),
                         ["default", "a", "b"])

    def test_literal_default_types_wall_defaults(self):
        self.assertEqual(literal_default(None), (None, False))
        self.assertEqual(literal_default(" true "), (True, True))
        self.assertEqual(literal_default("FALSE"), (False, True))
        self.assertEqual(literal_default("none"), ("", True))
        self.assertEqual(literal_default("null"), ("", True))
        self.assertEqual(literal_default("1.5"), (1.5, True))
        self.assertEqual(literal_default("'label'"), ("label", True))
        self.assertEqual(literal_default("1 +"), (None, False))

    def test_layer_and_height_readouts_are_the_printer_side_only(self):
        self.assertEqual(layer_readout(SimpleNamespace(index=None)), "—")
        self.assertEqual(layer_readout(SimpleNamespace(index=4, total=None)), "5")
        self.assertEqual(layer_readout(SimpleNamespace(index=4, total=9)), "5/9")
        self.assertEqual(height_readout(SimpleNamespace(height=None)), "—")
        self.assertEqual(height_readout(SimpleNamespace(height=1.5)), "1.50 mm")

    def test_print_job_caption_maps_the_state_word_once(self):
        self.assertEqual(print_job_caption(None), "")
        self.assertEqual(print_job_caption(observation(connection="no")), "Disconnected")
        self.assertEqual(print_job_caption(observation(connection="unknown")),
                         "Printer state unknown")
        self.assertEqual(print_job_caption(observation(controls_locked=True, state="printing")),
                         "Locked")
        self.assertEqual(print_job_caption(observation(state="printing")), "Printing")
        self.assertEqual(print_job_caption(observation(state="paused")), "Paused")
        self.assertEqual(print_job_caption(observation(state="standby")), "Idle")

    def test_filament_total_falls_back_when_the_header_cannot_be_read(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = os.path.join(directory, "gone.gcode")
            self.assertIsNone(filament_total_mm_from_file(missing))
            # A window that cuts the declaration's line must not accept
            # the truncated value as the total.
            path = os.path.join(directory, "part.gcode")
            with open(path, "wb") as handle:
                handle.write(b"G28\n;Filament used: 2.157m\n")
            self.assertIsNone(filament_total_mm_from_file(path, limit=8))


# --------------------------------------------------------------------------
# PrinterConfig: the record's coercion and the store's legacy chain.
# --------------------------------------------------------------------------

class PrinterConfigCoverageTests(unittest.TestCase):
    def test_normalise_url_canonicalises_and_strips_credentials(self):
        for placeholder in ("", "   ", "http:", "HTTPS://"):
            self.assertEqual(normalise_url(placeholder), "http://")
        self.assertEqual(normalise_url("bad\x01host"), "http://")
        self.assertEqual(normalise_url("printer.local:7125"), "http://printer.local:7125")
        self.assertEqual(normalise_url("http://printer.local///"), "http://printer.local")
        self.assertEqual(normalise_url("http://printer.local/"), "http://printer.local")
        self.assertEqual(normalise_url("http://user:pass@printer.local:7125/api"), "http://printer.local:7125/api")
        self.assertEqual(normalise_url("http://user:pass@[::1]:7125"), "http://[::1]:7125")

    def test_upload_path_safe_refuses_traversal_and_the_root_marker(self):
        self.assertEqual(upload_path_safe("<root>"), "")
        self.assertEqual(upload_path_safe(".."), "")
        self.assertEqual(upload_path_safe("a/../b"), "")
        self.assertEqual(upload_path_safe("a/.hidden"), "")
        # A backslash separator is normalised for the check: it must
        # not smuggle a traversal segment through.
        self.assertEqual(upload_path_safe("projects\\..\\etc"), "")
        self.assertEqual(upload_path_safe("projects/parts"), "projects/parts")
        self.assertEqual(upload_path_safe(None), "")

    def test_normalise_temperature_chart_coerces_hand_edited_values(self):
        self.assertEqual(normalise_temperature_chart(None), {})
        self.assertEqual(normalise_temperature_chart({}), {})
        chart = normalise_temperature_chart({"visible": {"extruder": "yes", "bed": 0},
                                             "colors": {"extruder": 1},
                                             "showTargets": "false"})
        self.assertEqual(chart["visible"], {"extruder": True, "bed": False})
        self.assertEqual(chart["colors"], {"extruder": "1"})
        self.assertFalse(chart["showTargets"])
        self.assertTrue(chart["showPower"])
        defaults = normalise_temperature_chart({"visible": "nope", "colors": "nope"})
        self.assertEqual(defaults["visible"], {})
        self.assertEqual(defaults["colors"], {})

    def test_feed_mode_coercion_covers_direct_construction(self):
        self.assertEqual(PrinterConfig(feed_mode="http").feed_mode, FeedMode.HTTP)
        self.assertEqual(PrinterConfig(feed_mode="WEBSOCKET").feed_mode, FeedMode.WEBSOCKET)
        self.assertEqual(PrinterConfig(feed_mode=None).feed_mode, FeedMode.WEBSOCKET)
        self.assertEqual(PrinterConfig(feed_mode="nonsense").feed_mode, FeedMode.WEBSOCKET)

    def test_frontend_target_prefers_the_dedicated_frontend(self):
        self.assertEqual(PrinterConfig(url="http://p:7125").frontend_target, "http://p:7125")
        self.assertEqual(PrinterConfig(url="http://p:7125", frontend_url="http://f").frontend_target,
                         "http://f")

    def test_from_dict_repairs_every_out_of_range_numeric(self):
        config = PrinterConfig.from_dict({
            "poll_interval_ms": "nonsense",
            "z_tolerance": "nonsense",
            "ready_retry_interval_s": "nonsense",
            "camera_rotation": "nonsense",
            "aux_interval_ms": "nonsense",
            "console_interval_ms": "nonsense",
        })
        self.assertEqual(config.poll_interval_ms, 750)
        self.assertEqual(config.z_tolerance, 0.04)
        self.assertEqual(config.ready_retry_interval_s, 0.5)
        self.assertEqual(config.camera_rotation, 0)
        self.assertEqual(config.aux_interval_ms, 2500)
        self.assertEqual(config.console_interval_ms, 1000)

        clamped = PrinterConfig.from_dict({
            "poll_interval_ms": 10 ** 9, "z_tolerance": 0.5, "ready_retry_interval_s": 120.0,
            "camera_rotation": 45, "aux_interval_ms": 10, "console_interval_ms": 10 ** 6,
        })
        self.assertEqual(clamped.poll_interval_ms, 3_600_000)
        self.assertEqual(clamped.z_tolerance, 0.04)          # above the 0.250 ceiling
        self.assertEqual(clamped.ready_retry_interval_s, 60.0)
        self.assertEqual(clamped.camera_rotation, 0)         # not a quarter turn
        self.assertEqual(clamped.aux_interval_ms, 250)
        self.assertEqual(clamped.console_interval_ms, 60_000)
        self.assertEqual(PrinterConfig.from_dict({"z_tolerance": float("nan")}).z_tolerance, 0.04)
        self.assertEqual(PrinterConfig.from_dict({"poll_interval_ms": 0}).poll_interval_ms, 1)
        self.assertEqual(PrinterConfig.from_dict({"ready_retry_interval_s": 0.0}).ready_retry_interval_s,
                         0.1)
        self.assertEqual(PrinterConfig.from_dict({"camera_rotation": 270}).camera_rotation, 270)
        self.assertEqual(PrinterConfig.from_dict({"camera_rotation": 180}).camera_rotation, 180)
        self.assertEqual(PrinterConfig.from_dict({"camera_rotation": 90}).camera_rotation, 90)
        self.assertIsInstance(PrinterConfig.from_dict(None), PrinterConfig)

    def test_from_dict_cleans_the_console_transcript(self):
        config = PrinterConfig.from_dict({"console_transcript": [
            "junk",
            {"kind": "unknown", "text": "x"},
            {"kind": "command", "text": "G28", "error": 1, "success": 0},
            {"kind": "response", "text": "ok", "success": True},
        ]})
        self.assertEqual([entry["text"] for entry in config.console_transcript], ["G28", "ok"])
        self.assertTrue(config.console_transcript[0]["error"])
        self.assertFalse(config.console_transcript[0]["success"])
        self.assertTrue(config.console_transcript[1]["success"])
        # An oversized line is capped, and a corrupt history list is
        # trimmed rather than loaded whole.
        long_line = PrinterConfig.from_dict({"console_transcript": [
            {"kind": "command", "text": "x" * 9000}]})
        self.assertEqual(len(long_line.console_transcript[0]["text"]), 8 * 1024)
        self.assertEqual(PrinterConfig.from_dict({"console_transcript": "nope"}).console_transcript, [])
        history = PrinterConfig.from_dict({"console_history": [f"line-{i}" for i in range(300)]})
        self.assertEqual(len(history.console_history), 200)
        self.assertEqual(history.console_history[0], "line-100")
        self.assertEqual(PrinterConfig.from_dict({"console_history": 5}).console_history, [])

    def test_from_dict_resets_a_corrupt_console_store_time(self):
        for value in (float("inf"), "nonsense", -5.0, 4_102_444_801.0):
            self.assertEqual(PrinterConfig.from_dict({"console_store_time": value}).console_store_time,
                             0.0, value)
        self.assertEqual(PrinterConfig.from_dict({"console_store_time": 12.5}).console_store_time, 12.5)
        self.assertEqual(PrinterConfig.from_dict({"console_store_time": None}).console_store_time, 0.0)

    def test_from_dict_coerces_loose_booleans_and_unknown_enums(self):
        config = PrinterConfig.from_dict({"enabled": "yes", "upload_dialog": "false",
                                          "camera_mirror": 1, "eta_learn": "on",
                                          "follow_mode": "sideways", "output_format": "UFP"})
        self.assertTrue(config.enabled)
        self.assertFalse(config.upload_dialog)
        self.assertTrue(config.camera_mirror)
        self.assertTrue(config.eta_learn)
        self.assertEqual(config.follow_mode, "exact")
        self.assertEqual(config.output_format, "ufp")
        self.assertEqual(PrinterConfig.from_dict({"output_format": "STL"}).output_format, "gcode")
        self.assertEqual(PrinterConfig.from_dict({"feed_mode": "http"}).feed_mode, FeedMode.HTTP)
        self.assertEqual(PrinterConfig.from_dict({"feed_mode": "bogus"}).feed_mode, FeedMode.WEBSOCKET)
        self.assertEqual(PrinterConfig.from_dict({}).feed_mode, FeedMode.WEBSOCKET)

    def test_from_dict_filters_the_upload_paths(self):
        config = PrinterConfig.from_dict({"upload_paths": ["projects", "..", "<root>", ".git", "  /parts/  "],
                                          "upload_path": "/projects/parts/"})
        self.assertEqual(config.upload_paths, ["projects", "parts"])
        self.assertEqual(config.upload_path, "projects/parts")
        self.assertEqual(PrinterConfig.from_dict({"upload_paths": "nope"}).upload_paths, [])

    def test_store_decodes_and_coerces_loose_preference_values(self):
        self.assertTrue(PrinterConfigStore._truthy(True))
        self.assertTrue(PrinterConfigStore._truthy(" YES "))
        self.assertFalse(PrinterConfigStore._truthy("nope"))
        self.assertEqual(PrinterConfigStore._decode_mapping({"a": 1}), {"a": 1})
        self.assertEqual(PrinterConfigStore._decode_mapping('{"a": 1}'), {"a": 1})
        self.assertEqual(PrinterConfigStore._decode_mapping("not json"), {})
        self.assertEqual(PrinterConfigStore._decode_mapping("[1, 2]"), {})
        self.assertEqual(PrinterConfigStore._decode_mapping(None), {})

    def test_store_identity_falls_back_when_the_provider_raises(self):
        def broken():
            raise RuntimeError("no machine yet")
        store = PrinterConfigStore(Preferences({}), broken)
        self.assertEqual(store.identity(), ("unknown", "Unknown Cura printer"))
        store = PrinterConfigStore(Preferences({}), lambda: (None, None))
        self.assertEqual(store.identity(), ("unknown", "unknown"))

    def test_store_carries_settings_saved_under_the_pre_rename_keys(self):
        # The 4.3.0 identity rename: the morning's underscore keys must
        # survive the rename once.
        prefs = _DefaultingPreferences({
            "moonraker_print_follower/url": "http://pre-rename:7125",
            "moonraker_print_follower/poll_interval_ms": 1234,
            "moonraker_print_follower/printer_configs_v1": '{"A": {"url": "http://old:7125"}}',
            "moonraker_print_follower/bed_mesh_visible": False,
        })
        PrinterConfigStore(prefs, lambda: ("A", "A"))
        self.assertEqual(prefs.getValue(PrinterConfigStore.LEGACY_MAP["url"]), "http://pre-rename:7125")
        self.assertEqual(prefs.getValue(PrinterConfigStore.LEGACY_MAP["poll_interval_ms"]), 1234)
        self.assertEqual(prefs.getValue(PrinterConfigStore.PREF_KEY),
                         '{"A": {"url": "http://old:7125"}}')
        # The blob carry is gated on a truthy old value; the falsy
        # flag is left to its registered default.
        self.assertIsNone(prefs.getValue("moonrakerprintfollower/bed_mesh_visible"))

    def test_store_constructs_under_a_preferences_store_without_defaults(self):
        # The carry reads with an explicit default; a host whose
        # getValue takes only the key must not take construction down.
        store = PrinterConfigStore(Preferences({}), lambda: ("A", "A"))
        self.assertEqual(store.identity(), ("A", "A"))

    def test_store_migrates_the_flat_keys_into_the_active_machine(self):
        prefs = Preferences({})
        store = PrinterConfigStore(prefs, lambda: ("A", "Printer A"))
        prefs.setValue(PrinterConfigStore.LEGACY_MAP["url"], "http://legacy:7125")
        self.assertTrue(store.migrate_legacy_to_current_machine())
        self.assertEqual(store.get("A").url, "http://legacy:7125")
        # The marker makes it a one-shot.
        self.assertFalse(store.migrate_legacy_to_current_machine())

    def test_store_defers_migration_until_an_identity_exists(self):
        prefs = Preferences({})
        store = PrinterConfigStore(prefs, lambda: ("unknown", "Unknown Cura printer"))
        prefs.setValue(PrinterConfigStore.LEGACY_MAP["url"], "http://legacy:7125")
        self.assertFalse(store.migrate_legacy_to_current_machine())
        # Deferred, not migrated: the blob is left for the real machine
        # and the one-shot marker is untouched.
        self.assertEqual(printer_configs_blob(prefs), "{}")
        self.assertFalse(prefs.getValue(PrinterConfigStore.MIGRATED_KEY))

    def test_store_imports_the_moonraker_connection_plugin(self):
        prefs = Preferences({})
        store = PrinterConfigStore(prefs, lambda: ("A", "Printer A"))
        store.set(PrinterConfig(url="http://own:7125"), "A")
        prefs.setValue(PrinterConfigStore.MOONRAKER_CONNECTION_PREF_KEY, json.dumps({
            "A": {"url": "http://legacy:7125", "api_key": "KEY", "upload_pathes": ["projects", ".."],
                  "trans_input": "in", "output_format": "ufp", "camera_image_rotation": 90,
                  "camera_image_mirror": True, "upload_start_print_job": True,
                  "retry_interval": 5.0, "camera_url": "http://cam", "power_device": "psu"},
            "B": {"url": "http://b:7125"},
            "C": "not a mapping",
        }))
        self.assertEqual(store.migrate_moonraker_connection(), 2)
        imported = store.get("A")
        # The already-configured endpoint wins; the fields the follower
        # had no equivalent for are imported.
        self.assertEqual(imported.url, "http://own:7125")
        self.assertEqual(imported.api_key, "KEY")
        self.assertEqual(imported.upload_paths, ["projects"])
        self.assertEqual(imported.filename_translate_input, "in")
        self.assertEqual(imported.output_format, "ufp")
        self.assertEqual(imported.camera_rotation, 90)
        self.assertTrue(imported.camera_mirror)
        self.assertTrue(imported.upload_start_print)
        self.assertEqual(imported.ready_retry_interval_s, 5.0)
        self.assertEqual(imported.power_devices, "psu")
        self.assertEqual(store.get("B").url, "http://b:7125")
        # The marker makes it a one-shot.
        self.assertEqual(store.migrate_moonraker_connection(), 0)

    def test_store_connection_import_is_a_no_op_without_the_old_plugin(self):
        # Checking is not importing: the empty source returns 0 and
        # leaves NO migration marker (the reviewer's first-install
        # invariant — a marker means an import happened).
        prefs = Preferences({})
        store = PrinterConfigStore(prefs, lambda: ("A", "A"))
        self.assertEqual(store.migrate_moonraker_connection(), 0)
        self.assertFalse(prefs.getValue(PrinterConfigStore.MOONRAKER_CONNECTION_MIGRATED_KEY))

    def test_store_set_preserves_foreign_keys_and_update_round_trips(self):
        prefs = Preferences({})
        store = PrinterConfigStore(prefs, lambda: ("A", "A"))
        store.set(PrinterConfig(url="http://a:7125"))
        blob = json.loads(prefs.getValue(PrinterConfigStore.PREF_KEY))
        blob["A"]["aFutureKey"] = 42
        prefs.setValue(PrinterConfigStore.PREF_KEY, json.dumps(blob))
        store.set(PrinterConfig(url="http://b:7125"))
        saved = json.loads(prefs.getValue(PrinterConfigStore.PREF_KEY))
        self.assertEqual(saved["A"]["aFutureKey"], 42)
        self.assertEqual(saved["A"]["url"], "http://b:7125")
        updated = store.update(follow_mode="window", enabled=False)
        self.assertEqual(updated.follow_mode, "window")
        self.assertFalse(store.get().enabled)


def printer_configs_blob(prefs):
    return prefs.getValue(PrinterConfigStore.PREF_KEY)


class _DefaultingPreferences(Preferences):
    """Uranium's getValue takes an optional default; the shared double
    takes only the key, which would make every rename carry raise."""

    def getValue(self, key, default=None):
        return self.values.get(key, default)


# --------------------------------------------------------------------------
# PauseScheduleService: the print-local schedule's edge coercion.
# --------------------------------------------------------------------------

class PauseScheduleCoverageTests(unittest.TestCase):
    def test_due_layers_skip_unparseable_input(self):
        self.assertEqual(due_end_of_layer_pauses({3}, "nonsense"), [])
        self.assertEqual(due_end_of_layer_pauses({3, "x", None}, 5), [3])
        self.assertEqual(due_end_of_layer_pauses(None, 5), [])

    def test_schedule_rejects_negative_and_duplicate_layers(self):
        schedule = PauseScheduleService()
        self.assertFalse(schedule.schedule(-1))
        self.assertTrue(schedule.schedule(2))
        self.assertFalse(schedule.schedule(2))

    def test_remove_and_clear_report_what_changed(self):
        schedule = PauseScheduleService()
        self.assertFalse(schedule.remove(2))
        schedule.schedule(2)
        schedule.schedule(4)
        self.assertTrue(schedule.remove(2))
        self.assertFalse(schedule.remove(2))
        self.assertEqual(schedule.clear(), 1)
        self.assertEqual(schedule.layers, frozenset())

    def test_consume_due_drains_only_the_crossed_layers(self):
        schedule = PauseScheduleService()
        schedule.schedule(1)
        schedule.schedule(7)
        self.assertEqual(schedule.consume_due(5), [1])
        self.assertEqual(schedule.layers, frozenset({7}))

    def test_imminent_tightens_around_the_target(self):
        schedule = PauseScheduleService()
        schedule.schedule(10)
        self.assertFalse(schedule.is_imminent(5))
        self.assertTrue(schedule.is_imminent(10, lookahead_layers=0))
        self.assertFalse(schedule.is_imminent(None))
        self.assertEqual(due_end_of_layer_pauses({}, 5), [])


# --------------------------------------------------------------------------
# MonitorPermissions: the policy table's every row.
# --------------------------------------------------------------------------

class MonitorPermissionsCoverageTests(unittest.TestCase):
    def test_every_shared_prelude_refusal_names_its_own_cause(self):
        self.assertEqual(section_reason(observation(connection="unknown")), R_UNKNOWN)
        self.assertEqual(section_reason(observation(connection="no")), R_DISCONNECTED)
        self.assertEqual(section_reason(observation(active=False)), R_UNKNOWN)
        self.assertEqual(section_reason(observation(controls_locked=True)), R_LOCKED)
        self.assertEqual(section_reason(observation()), "")
        # The e-stop assumption is carried, not a prelude refusal.
        self.assertEqual(section_reason(observation(assumed_stopped=True)), "")

    def test_can_jog_treats_printing_as_a_mode_and_unknown_as_a_refusal(self):
        self.assertEqual(can_jog(observation(state="printing")).mode, "pause-first")
        self.assertEqual(can_jog(observation(state="printing")).reason, R_PAUSE_FIRST)
        self.assertEqual(can_jog(observation(state="error")).mode, "allowed")
        self.assertEqual(can_jog(observation(state="paused")).mode, "allowed")
        # Not homed is deliberately still allowed.
        self.assertEqual(can_jog(observation(state="standby", homed_axes="")).mode, "allowed")
        self.assertEqual(can_jog(observation(state="")).reason, R_UNKNOWN)
        self.assertEqual(can_jog(observation(state="")).mode, "disabled")
        self.assertEqual(can_set_absolute(observation(state="")).mode, "disabled")

    def test_jog_caption_names_every_state(self):
        self.assertEqual(jog_caption(observation(controls_locked=True)), R_LOCKED)
        self.assertEqual(jog_caption(observation(state="printing")), R_PAUSE_FIRST)
        self.assertEqual(jog_caption(observation(state="paused")), R_PAUSED_NOTE)
        self.assertEqual(jog_caption(observation(state="standby")), "")

    def test_restart_and_macro_share_the_one_shot_row(self):
        self.assertEqual(can_restart(observation(state="printing")).reason, R_PRINTING)
        self.assertEqual(can_restart(observation(state="paused")).reason, R_PRINTING)
        self.assertEqual(can_restart(observation(state="")).reason, R_UNKNOWN)
        self.assertEqual(can_restart(observation(state="standby")).mode, "allowed")
        self.assertEqual(can_macro(observation(state="printing")).reason, R_PRINTING)

    def test_power_rows_gate_on_the_device_lock(self):
        self.assertEqual(can_power(observation(state="printing"), True).reason, R_PRINTING)
        self.assertEqual(can_power(observation(state="printing"), False).mode, "allowed")
        self.assertEqual(can_power(observation(state=""), False).reason, R_UNKNOWN)
        self.assertEqual(can_power(observation(), True).mode, "allowed")

    def test_start_print_keeps_the_connection_clause(self):
        self.assertEqual(can_start_print(observation(connection="unknown")).reason, R_UNKNOWN)
        self.assertEqual(can_start_print(observation(connection="no")).reason, R_DISCONNECTED)
        self.assertEqual(can_start_print(observation(state="printing")).reason, R_PRINTING)
        self.assertEqual(can_start_print(observation(connection="no", active=False)).reason,
                         R_DISCONNECTED)
        # Not homed and not ready stay allowed: the confirmation warns.
        self.assertEqual(can_start_print(observation(state="")).mode, "allowed")
        self.assertEqual(can_start_print(observation(state="error")).mode, "allowed")

    def test_exclude_fires_only_mid_print(self):
        self.assertEqual(can_exclude(observation(state="standby")).reason, R_NOT_PRINTING)
        self.assertEqual(can_exclude(observation(state="printing")).mode, "allowed")
        self.assertEqual(can_exclude(observation(state="paused")).mode, "allowed")

    def test_z_offset_gates_on_the_lane_not_the_print(self):
        self.assertEqual(can_z_offset(observation(busy=True)).reason, R_BUSY)
        self.assertEqual(can_z_offset(observation(state="")).reason, R_UNKNOWN)
        self.assertEqual(can_z_offset(observation(state="printing")).mode, "allowed")

    def test_pause_refuses_on_the_assumption_the_capability_and_the_bit(self):
        self.assertEqual(can_pause(observation(assumed_stopped=True)).reason, R_ESTOPPED)
        self.assertEqual(can_pause(observation(busy=True)).reason, R_BUSY)
        self.assertEqual(can_pause(observation(pause_resume_supported=False)).reason, R_UNSUPPORTED)
        self.assertEqual(can_pause(observation(pause_resume_supported=None)).reason, R_UNKNOWN)
        # The capability gate precedes the state checks: an
        # unobserved object list refuses before the bit is consulted.
        self.assertEqual(can_pause(observation(state="printing", is_paused=True)).reason, R_UNKNOWN)
        self.assertEqual(can_pause(observation(state="printing", is_paused=True,
                                               pause_resume_supported=True)).reason,
                         R_ALREADY_PAUSED)
        self.assertEqual(can_pause(observation(state="paused", is_paused=False,
                                               pause_resume_supported=True)).reason,
                         R_ALREADY_PAUSED)
        self.assertEqual(can_pause(observation(state="standby",
                                               pause_resume_supported=True)).reason,
                         R_NOTHING_TO_PAUSE)
        # The state word is the fallback while the bit is unobserved.
        self.assertEqual(can_pause(observation(state="printing", is_paused=None,
                                               pause_resume_supported=True)).mode, "allowed")
        self.assertEqual(can_pause(observation(state="paused", is_paused=None,
                                               pause_resume_supported=True)).reason,
                         R_ALREADY_PAUSED)
        self.assertEqual(can_pause(observation(state="printing", is_paused=False,
                                               pause_resume_supported=True)).mode, "allowed")

    def test_resume_reads_the_authoritative_bit_before_the_state_word(self):
        self.assertEqual(can_resume(observation(assumed_stopped=True)).reason, R_ESTOPPED)
        self.assertEqual(can_resume(observation(busy=True)).reason, R_BUSY)
        self.assertEqual(can_resume(observation(pause_resume_supported=False)).reason, R_UNSUPPORTED)
        self.assertEqual(can_resume(observation(pause_resume_supported=None)).reason, R_UNKNOWN)
        # A CLEAR_PAUSE leaves the state word "paused" while the
        # authoritative bit says resume can never succeed.
        self.assertEqual(can_resume(observation(state="paused", is_paused=False,
                                                pause_resume_supported=True)).reason,
                         R_CLEARED_PAUSE)
        self.assertEqual(can_resume(observation(state="printing", is_paused=False,
                                                pause_resume_supported=True)).reason,
                         R_ALREADY_PRINTING)
        # A stale bit surviving SDCARD_RESET_FILE must not offer RESUME.
        self.assertEqual(can_resume(observation(state="standby", is_paused=True,
                                                pause_resume_supported=True)).reason,
                         R_NO_PAUSED_PRINT)
        self.assertEqual(can_resume(observation(state="printing", is_paused=True,
                                                pause_resume_supported=True)).mode, "allowed")
        # The state word stands in while the bit is unobserved.
        self.assertEqual(can_resume(observation(state="paused", is_paused=None,
                                                pause_resume_supported=True)).mode, "allowed")
        self.assertEqual(can_resume(observation(state="paused", is_paused=True,
                                                pause_resume_supported=True)).mode, "allowed")

    def test_every_reason_constant_has_its_tooltip_sentence(self):
        for reason in (R_UNKNOWN, R_DISCONNECTED, R_ESTOPPED, R_LOCKED, R_PAUSE_FIRST, R_PAUSED_NOTE,
                       R_PRINTING, R_BUSY, R_NOT_PRINTING, R_NOTHING_TO_PAUSE, R_ALREADY_PAUSED,
                       R_ALREADY_PRINTING, R_UNSUPPORTED, R_CLEARED_PAUSE, R_NO_PAUSED_PRINT):
            self.assertTrue(REASON_DETAIL[reason].strip(), reason)


# --------------------------------------------------------------------------
# MonitorTemperatureHistory: the ring buffers and the chart payload.
# --------------------------------------------------------------------------

class MonitorTemperatureHistoryCoverageTests(unittest.TestCase):
    """The bounded window. The cap guard on a series the poll did not
    feed (MonitorTemperatureHistory.py 136-137) is a defensive copy of
    the per-reading cap: a series is only ever created and appended in
    that loop, so it can never be over the cap at the sweep, and no
    public call reaches it."""

    def test_a_gap_restarts_the_window(self):
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": 60.0}}, 0.0, wall=1000.0)
        history.observe({"heater_bed": {"temperature": 61.0}}, 5.0)
        self.assertEqual(len(history.series("heater_bed")), 2)
        history.observe({"heater_bed": {"temperature": 20.0}}, 60.0)
        # The long gap is a session break: the stale 60 °C curve is gone.
        samples = history.series("heater_bed")
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].temperature, 20.0)
        self.assertEqual(samples[0].elapsed, 0.0)

    def test_a_backwards_clock_re_anchors_instead_of_trimming(self):
        # A backwards monotonic clock restarts the window: the old
        # timebase's samples leave and the new reading anchors the
        # fresh window (the chart's nearest-sample search needs one
        # ordered timebase).
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": 60.0}}, 100.0)
        history.observe({"heater_bed": {"temperature": 61.0}}, 50.0)
        samples = history.series("heater_bed")
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[-1].elapsed, 0.0)
        self.assertEqual(samples[-1].temperature, 61.0)

    def test_a_vanished_sensor_is_pruned_from_the_legend(self):
        history = TemperatureHistory(window_seconds=1.0)
        history.observe({"heater_bed": {"temperature": 60.0},
                         "temperature_sensor chamber": {"temperature": 30.0}}, 0.0)
        self.assertEqual(history.names(), ["heater_bed", "temperature_sensor chamber"])
        history.observe({"heater_bed": {"temperature": 61.0}}, 5.0)
        self.assertEqual(history.names(), ["heater_bed"])

    def test_the_window_and_the_sample_cap_both_bound_the_memory(self):
        history = TemperatureHistory(window_seconds=1_000_000.0)
        for step in range(1900):
            history.observe({"heater_bed": {"temperature": 60.0}}, step * 0.5)
        self.assertEqual(len(history.series("heater_bed")), 1800)

    def test_filling_reports_a_window_that_is_still_collecting(self):
        history = TemperatureHistory()
        self.assertFalse(history.filling)
        history.observe({"heater_bed": {"temperature": 60.0}}, 0.0)
        self.assertTrue(history.filling)
        history.observe({"heater_bed": {"temperature": 60.0}}, 20.0)
        self.assertFalse(history.filling)

    def test_target_and_power_segments_split_at_real_gaps(self):
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": 20.0}}, 0.0)
        history.observe({"heater_bed": {"temperature": 21.0, "target": 60.0, "power": 0.5}}, 2.5)
        history.observe({"heater_bed": {"temperature": 30.0, "target": 60.0, "power": 0.6}}, 5.0)
        history.observe({"heater_bed": {"temperature": 59.0, "target": 60.0}}, 20.0)
        history.observe({"heater_bed": {"temperature": 60.0}}, 22.0)
        # The 15 s feed gap between the two target stretches splits the
        # polyline; the lone closing sample is dropped.
        self.assertEqual(history.target_segments("heater_bed"), [[[2.5, 60.0], [5.0, 60.0]]])
        self.assertEqual(history.power_segments("heater_bed"), [[[2.5, 0.5], [5.0, 0.6]]])
        self.assertEqual(history.points("heater_bed")[0], [0.0, 20.0])
        self.assertEqual(history.series("absent"), [])
        self.assertEqual(history.points("absent"), [])

    def test_segments_keep_at_least_two_points(self):
        self.assertEqual(_segments([[0.0, 1.0]]), [])
        self.assertEqual(_segments([]), [])
        self.assertEqual(_segments([[0.0, 1.0], [1.0, 2.0], [10.0, 3.0]]),
                         [[[0.0, 1.0], [1.0, 2.0]]])

    def test_reset_clears_the_window_and_bumps_the_revision(self):
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": 60.0}}, 0.0, wall=500.0)
        self.assertEqual(history.wall_origin, 500.0)
        before = history.revision
        history.reset()
        self.assertGreater(history.revision, before)
        self.assertEqual(history.names(), [])
        self.assertIsNone(history.wall_origin)
        self.assertFalse(history.filling)

    def test_the_revision_only_moves_when_the_payload_changes(self):
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": 60.0}}, 0.0)
        revision = history.revision
        history.observe({}, 2.5)
        self.assertEqual(history.revision, revision)
        history.observe({"heater_bed": {"temperature": 61.0}}, 5.0)
        self.assertEqual(history.revision, revision + 1)

    def test_chart_payload_composes_the_series_and_the_toggles(self):
        history = TemperatureHistory()
        history.observe({"heater_bed": {"temperature": 60.0, "target": 60.0, "power": 0.5},
                         "temperature_sensor chamber": {"temperature": 30.0}}, 0.0)
        payload = chart_payload(history, {"colors": {"heater_bed": "#123456"},
                                          "visible": {"heater_bed": False},
                                          "showTargets": False})
        # The hidden series stays out of the data payload — its
        # identity and colour ride the metadata the legend reads.
        self.assertEqual([item["name"] for item in payload["series"]], ["temperature_sensor chamber"])
        self.assertFalse(payload["series"][0]["primary"])
        self.assertTrue(payload["series"][0]["color"].startswith("#"))
        self.assertFalse(payload["showTargets"])
        self.assertTrue(payload["showPower"])
        self.assertEqual(payload["palette"], list(PALETTE))
        self.assertTrue(payload["filling"])
        metadata = {item["name"]: item for item in series_metadata(history, {
            "colors": {"heater_bed": "#123456"}, "visible": {"heater_bed": False}})}
        self.assertEqual(metadata["heater_bed"]["color"], "#123456")
        self.assertFalse(metadata["heater_bed"]["visible"])
        self.assertTrue(metadata["heater_bed"]["primary"])
        self.assertEqual(chart_payload(history, None)["series"][0]["name"], "heater_bed")


# --------------------------------------------------------------------------
# StateStore and PluginPersistence: the file semantics and the facade.
# --------------------------------------------------------------------------

class StateStoreCoverageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "state.json")

    def test_an_injected_save_primitive_owns_the_commit(self):
        calls = []
        store = StateStore(self.path, save=lambda path, text: calls.append((path, text)) or True)
        self.assertTrue(store.write({"a": 1}))
        self.assertEqual(calls[0][0], self.path)
        self.assertIn('"a": 1', calls[0][1])
        self.assertFalse(os.path.exists(self.path))
        failing = StateStore(self.path, note=lambda kind, text: None,
                             save=lambda path, text: False)
        self.assertFalse(failing.write({"a": 1}))

    def test_an_injected_lock_wraps_the_whole_cycle(self):
        events = []

        class _Lock:
            def __enter__(self):
                events.append("enter")

            def __exit__(self, *args):
                events.append("exit")
                return False

        store = StateStore(self.path, lock=_Lock)
        self.assertTrue(store.write({"a": 1}))
        self.assertEqual(events, ["enter", "exit"])

    def test_a_replace_write_drops_the_keys_the_merge_would_keep(self):
        store = StateStore(self.path, note=lambda kind, text: None)
        store.write({"a": 1, "b": 2})
        self.assertTrue(store.write({"a": 9}, merge=False))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"a": 9})

    def test_the_merge_drops_the_named_keys_deliberately(self):
        store = StateStore(self.path, note=lambda kind, text: None)
        store.write({"sections": {}, "chart": {}})
        self.assertTrue(store.write({"sections": {"a": True}}, delete=("chart",)))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"sections": {"a": True}})

    def test_an_undecodable_file_heals_on_the_next_write(self):
        store = StateStore(self.path, note=lambda kind, text: None)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write('{"partial": ')  # a truncated sync
        self.assertTrue(store.write({"a": 1}))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"a": 1})
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")  # decodable, but not a document
        self.assertTrue(store.write({"b": 2}))
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"b": 2})

    def test_a_write_failure_reports_once_until_the_session_boundary(self):
        notes = []
        store = StateStore(self.dir.name, note=lambda kind, text: notes.append((kind, text)))
        self.assertFalse(store.write({"a": 1}))
        self.assertFalse(store.write({"a": 1}))
        self.assertEqual([kind for kind, _ in notes], ["write"])
        store.reset_failures()
        self.assertFalse(store.write({"a": 1}))
        self.assertEqual(len(notes), 2)

    def test_the_failure_sink_is_optional(self):
        store = StateStore(self.dir.name)
        self.assertFalse(store.write({"a": 1}))
        self.assertIsNone(store.read())


class PluginPersistenceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.settings = os.path.join(self.dir.name, "settings.json")
        self.global_state = os.path.join(self.dir.name, "state", "global.json")
        self.machines = os.path.join(self.dir.name, "state", "machines")
        self.persistence = PluginPersistence(self.settings, self.global_state, self.machines,
                                             save=_pretty_save)

    def _document(self):
        with open(self.settings, encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_paths_are_exposed_for_the_migration_runner(self):
        self.assertEqual(self.persistence.settings_path, self.settings)
        self.assertEqual(self.persistence.state_global_path, self.global_state)
        self.assertEqual(self.persistence.state_dir, self.machines)

    def test_a_machine_config_round_trips_through_the_settings_document(self):
        config = PrinterConfig(url="http://a:7125", feed_mode="http", camera_selected="cam-1")
        self.assertTrue(self.persistence.set_machine_config("A", config))
        entry = self.persistence.get_machine("A")
        self.assertEqual(entry["url"], "http://a:7125")
        self.assertEqual(entry["feed_mode"], "http")
        self.assertEqual(entry["camera_selected"], "cam-1")
        self.assertNotIn("console_transcript", entry)
        self.assertIsNone(self.persistence.get_machine("missing"))

    def test_a_sibling_record_survives_every_key_scoped_write(self):
        self.persistence.set_machine_config("A", PrinterConfig(url="http://a:7125"))
        self.persistence.set_machine_config("B", PrinterConfig(url="http://b:7125"))
        self.persistence.set_machine("A", {"enabled": False})
        document = self._document()
        self.assertFalse(document["machines"]["A"]["enabled"])
        self.assertEqual(document["machines"]["B"]["url"], "http://b:7125")

    def test_a_foreign_section_shape_is_repaired_rather_than_trusted(self):
        self.persistence.write_settings_document({"machines": "junk", "global": "junk"})
        self.persistence.set_machine("A", {"url": "http://a:7125"})
        self.persistence.set_global({"activeMachineId": "A"})
        document = self._document()
        self.assertEqual(document["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(document["global"]["activeMachineId"], "A")
        self.assertEqual(self.persistence.settings_document()["machines"].keys(), {"A"})
        self.assertIsNone(self.persistence.migration_record())

    def test_the_migration_record_merges_rather_than_replaces(self):
        self.persistence.set_migration_record({"status": "failed", "reason": "backup-failed"})
        self.assertTrue(self.persistence.set_migration_record({"backupWritten": True}))
        record = self.persistence.migration_record()
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["backupWritten"])

    def test_removing_an_absent_machine_is_not_a_failure(self):
        self.assertTrue(self.persistence.remove_machine("NOPE"))
        self.persistence.set_machine_config("A", PrinterConfig(url="http://a:7125"))
        self.assertTrue(self.persistence.remove_machine("A"))
        self.assertIsNone(self.persistence.get_machine("A"))

    def test_the_global_document_merges_and_drops_only_on_request(self):
        self.persistence.merge_state_global({"sections": {"toolhead": True}})
        self.persistence.merge_state_global({"controlsLocked": True}, delete=("sections",))
        document = self.persistence.state_global_document()
        self.assertEqual(document, {"controlsLocked": True})

    def test_a_machine_shard_is_keyed_by_the_quoted_id(self):
        self.assertTrue(self.persistence.set_machine_state("Printer A", {"consoleStoreTime": 5.0}))
        self.assertEqual(os.listdir(self.machines), ["Printer+A.json"])
        self.assertEqual(self.persistence.get_machine_state("Printer A")["consoleStoreTime"], 5.0)
        self.assertIsNone(self.persistence.get_machine_state("absent"))

    def test_the_retired_history_key_is_dropped_on_the_first_shard_write(self):
        self.persistence.write_machine_state_document("A", {"consoleHistory": ["old"], "x": 1})
        self.assertTrue(self.persistence.set_machine_state("A", {"consoleStoreTime": 2.0}))
        shard = self.persistence.get_machine_state("A")
        self.assertNotIn("consoleHistory", shard)
        self.assertEqual(shard["x"], 1)
        self.assertEqual(shard["consoleStoreTime"], 2.0)

    def test_a_non_document_shard_reads_as_absent(self):
        os.makedirs(self.machines, exist_ok=True)
        with open(os.path.join(self.machines, "A.json"), "w", encoding="utf-8") as handle:
            handle.write("[1, 2]")
        self.assertIsNone(self.persistence.get_machine_state("A"))

    def test_reset_failures_reaches_every_live_store(self):
        # A session boundary must re-arm the reporting of every store
        # the facade owns, shards included.
        notes = []
        base = PluginPersistence(self.settings, self.global_state, self.machines,
                                 note=lambda kind, text: notes.append(kind))
        base._shard("A")
        for store in (base._settings, base._state_global):
            store._reported.add("write")
        base._shards["A"]._reported.add("write")
        base.reset_failures()
        self.assertEqual(base._settings._reported, set())
        self.assertEqual(base._state_global._reported, set())
        self.assertEqual(base._shards["A"]._reported, set())

    def test_a_non_document_settings_file_reads_as_an_empty_document(self):
        plugin = PluginPersistence(self.dir.name, self.global_state, self.machines)
        self.assertEqual(plugin.settings_document(), {})
        self.assertIsNone(plugin.get_machine("A"))


# --------------------------------------------------------------------------
# PersistenceMigration: the one-shot's control flow.
# --------------------------------------------------------------------------

class PersistenceMigrationCoverageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.cura_cfg = os.path.join(self.dir.name, "cura.cfg")
        with open(self.cura_cfg, "wb") as handle:
            handle.write(b'[moonrakerprintfollower]\nprinter_configs_v1 = "{}"\n')
        self.settings_path = os.path.join(self.dir.name, "settings.json")
        self.state_dir = os.path.join(self.dir.name, "state", "machines")
        self.old_state_path = os.path.join(self.dir.name, "old_sections.json")
        self.cleaned = []

    def _runner(self, **overrides):
        calls = {"settings": [], "record": [], "global": [], "machine": [], "prefs": []}

        def settings_write(document):
            calls["settings"].append(document)
            os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
            with open(self.settings_path, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            return overrides.get("settings_write", True)

        def record_write(update):
            calls["record"].append(update)
            return True

        def global_write(document):
            calls["global"].append(document)
            return overrides.get("global_write", True)

        def machine_write(machine_id, document):
            calls["machine"].append((machine_id, document))
            if overrides.get("machine_write") is False:
                return False
            os.makedirs(self.state_dir, exist_ok=True)
            with open(os.path.join(self.state_dir, f"{machine_id}.json"), "w",
                      encoding="utf-8") as handle:
                json.dump(document, handle)
            return True

        def set_pref(key, value):
            calls["prefs"].append((key, value))

        return calls, dict(
            settings_write=settings_write, settings_record_write=record_write,
            state_global_write=global_write, state_machine_write=machine_write, set_pref=set_pref)

    def _run(self, blob, **overrides):
        calls, writers = self._runner(**overrides)
        outcome = run_migration(blob, self.cura_cfg, self.settings_path, self.state_dir,
                                self.old_state_path, timestamp="2026-09-18-10-00-00", **writers)
        return calls, outcome

    def test_read_source_distinguishes_absent_empty_corrupt_and_records(self):
        self.assertEqual(read_source(None), ("absent", {}))
        self.assertEqual(read_source(""), ("absent", {}))
        self.assertEqual(read_source("{}"), ("empty", {}))
        self.assertEqual(read_source('{"A": {"url": "http://a:7125"}}'),
                         ("records", {"A": {"url": "http://a:7125"}}))
        self.assertEqual(read_source('{"A": {}}')[0], "records")
        self.assertEqual(read_source("{not json"), ("corrupt", {}))
        self.assertEqual(read_source("[1, 2]"), ("corrupt", {}))

    def test_split_record_sends_the_console_trio_to_the_state_side(self):
        settings, state = split_record({"url": "http://a:7125", "feed_mode": "http",
                                        "console_transcript": [{"kind": "command", "text": "G28"}],
                                        "console_store_time": 9.0})
        self.assertEqual(settings["feed_mode"], "http")
        self.assertNotIn("console_transcript", settings)
        self.assertNotIn("console_store_time", settings)
        self.assertNotIn("console_history", settings)
        self.assertEqual(state["consoleStoreTime"], 9.0)
        self.assertEqual(state["consoleTranscript"][0]["text"], "G28")

    def test_split_record_folds_a_legacy_history_into_the_transcript(self):
        # The 4.5.0 cleanup: the dead consoleHistory shard key is never
        # written; its lines become command entries.
        settings, state = split_record({"console_history": ["G28", "M104 S200"]})
        self.assertNotIn("consoleHistory", state)
        self.assertEqual([entry["text"] for entry in state["consoleTranscript"]], ["G28", "M104 S200"])
        self.assertEqual(state["consoleTranscript"][0]["kind"], "command")
        self.assertEqual(settings["url"], "http://")

    def test_write_backup_verifies_what_it_copied(self):
        target = os.path.join(self.dir.name, "backup.cfg")
        self.assertTrue(write_backup(self.cura_cfg, target))
        with open(target, "rb") as handle:
            self.assertIn(b"printer_configs_v1", handle.read())
        self.assertFalse(os.path.exists(target + ".tmp"))
        self.assertFalse(write_backup(os.path.join(self.dir.name, "missing.cfg"),
                                      os.path.join(self.dir.name, "other.cfg")))
        # Existence is not durability: a file without the plugin's blob
        # group is not a usable backup.
        empty = os.path.join(self.dir.name, "empty.cfg")
        with open(empty, "wb") as handle:
            handle.write(b"[general]\n")
        self.assertFalse(write_backup(empty, os.path.join(self.dir.name, "third.cfg")))

    def test_a_backup_that_loses_bytes_on_the_swap_is_refused(self):
        # Verify-by-re-read: existence is not durability, so a copy the
        # swap truncated must fail before the clean destroys the source.
        target = os.path.join(self.dir.name, "torn.cfg")

        def truncate(source, destination):
            with open(destination, "wb") as handle:
                handle.write(b"[general]\n")

        with patch("plugins.PersistenceMigration.os.replace", side_effect=truncate):
            self.assertFalse(write_backup(self.cura_cfg, target))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), b"[general]\n")

    def test_verify_rejects_a_document_whose_records_do_not_match(self):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump({"machines": {"A": {}, "B": {}}}, handle)
        self.assertFalse(_verify_new_files({"A": {}}, self.settings_path, self.state_dir))
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump({"machines": {"B": {}}}, handle)
        self.assertFalse(_verify_new_files({"A": {}}, self.settings_path, self.state_dir))

    def test_an_absent_blob_is_a_side_effect_free_no_op(self):
        # Schema activation is the BINDING's job (the reviewer's
        # first-install invariant): absent source writes nothing at
        # all — no document, no record, no chrome carry.
        calls, outcome = self._run(None)
        self.assertEqual((outcome.status, outcome.reason), ("ok", "nothing-to-do"))
        self.assertFalse(outcome.backup_written)
        self.assertEqual(calls["global"], [])
        self.assertEqual(calls["settings"], [])
        self.assertEqual(calls["record"], [])
        self.assertEqual(os.listdir(self.dir.name).count("cura.cfg.2026-09-18-10-00-00"), 0)

    def test_a_corrupt_blob_is_flagged_and_started_clean_behind_a_backup(self):
        calls, outcome = self._run("{not json")
        self.assertEqual((outcome.status, outcome.reason), ("failed", "corrupt-blob"))
        self.assertTrue(outcome.backup_written)
        self.assertEqual(outcome.backup_name, "cura.cfg.2026-09-18-10-00-00")
        self.assertTrue(os.path.exists(os.path.join(self.dir.name, outcome.backup_name)))
        self.assertEqual(calls["settings"][0]["machines"], {})
        self.assertTrue(calls["prefs"])
        self.assertEqual(calls["record"], [])

    def test_a_corrupt_blob_without_a_backup_leaves_cura_cfg_untouched(self):
        os.remove(self.cura_cfg)
        calls, outcome = self._run("{not json")
        self.assertEqual((outcome.status, outcome.reason), ("failed", "corrupt-blob"))
        self.assertFalse(outcome.backup_written)
        self.assertIsNone(outcome.backup_name)
        self.assertEqual(calls["prefs"], [])
        self.assertEqual(calls["settings"], [])

    def test_the_records_path_backs_up_writes_verifies_then_cleans(self):
        calls, outcome = self._run('{"A": {"url": "http://a:7125", "console_transcript": []}}')
        self.assertEqual((outcome.status, outcome.reason), ("ok", "migrated"))
        self.assertEqual(outcome.records, 1)
        self.assertTrue(outcome.backup_written)
        self.assertEqual(calls["settings"][0]["machines"]["A"]["url"], "http://a:7125")
        self.assertEqual(calls["machine"][0][0], "A")
        # The clean is last, and the record lands after it.
        self.assertTrue(calls["prefs"])
        self.assertEqual(calls["record"][0]["status"], "ok")
        self.assertTrue(os.path.exists(os.path.join(self.state_dir, "A.json")))

    def test_a_failed_backup_stops_before_anything_moves(self):
        os.remove(self.cura_cfg)
        calls, outcome = self._run('{"A": {}}')
        self.assertEqual((outcome.status, outcome.reason), ("failed", "backup-failed"))
        self.assertEqual(calls["settings"], [])
        self.assertEqual(calls["prefs"], [])
        self.assertEqual(calls["record"], [])

    def test_a_failed_new_file_write_stops_before_the_clean(self):
        calls, outcome = self._run('{"A": {}}', machine_write=False)
        self.assertEqual((outcome.status, outcome.reason), ("failed", "write-failed"))
        self.assertEqual(calls["prefs"], [])
        self.assertEqual(calls["record"], [])

    def test_a_failed_state_global_write_stops_before_the_clean(self):
        calls, outcome = self._run('{"A": {}}', global_write=False)
        self.assertEqual((outcome.status, outcome.reason), ("failed", "write-failed"))
        self.assertEqual(calls["settings"], [])
        self.assertEqual(calls["prefs"], [])

    def test_a_failed_verify_stops_before_the_clean(self):
        calls = {"prefs": []}

        def settings_write(document):
            calls["prefs"].append(("settings", document))
            return True  # the write claims success but lands nothing

        def global_write(document):
            return True

        def machine_write(machine_id, document):
            return True

        outcome = run_migration('{"A": {}}', self.cura_cfg, self.settings_path, self.state_dir,
                                self.old_state_path, settings_write=settings_write,
                                settings_record_write=lambda update: True,
                                state_global_write=global_write, state_machine_write=machine_write,
                                set_pref=lambda key, value: calls["prefs"].append((key, value)),
                                timestamp="2026-09-18-10-00-00")
        self.assertEqual((outcome.status, outcome.reason), ("failed", "verify-failed"))
        self.assertNotIn(("blob-clean", None), calls["prefs"])

    def test_verify_rejects_a_shard_that_does_not_hold_its_record(self):
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            json.dump({"machines": {"A": {}}}, handle)
        self.assertFalse(_verify_new_files({"A": {}}, self.settings_path, self.state_dir))
        with open(os.path.join(self.state_dir, "A.json"), "w", encoding="utf-8") as handle:
            handle.write("[1]")
        self.assertFalse(_verify_new_files({"A": {}}, self.settings_path, self.state_dir))
        with open(self.settings_path, "w", encoding="utf-8") as handle:
            handle.write("[1]")
        self.assertFalse(_verify_new_files({"A": {}}, self.settings_path, self.state_dir))

    def test_the_corrupt_recovery_carries_the_old_chrome_across(self):
        # The chrome carry now belongs to CORRUPT-blob recovery only:
        # the empty/absent path never touches the state files.
        with open(self.old_state_path, "w", encoding="utf-8") as handle:
            json.dump({"sections": {"toolhead": False}}, handle)
        calls, outcome = self._run("{not json")
        self.assertEqual(outcome.reason, "corrupt-blob")
        self.assertEqual(calls["global"][0]["sections"], {"toolhead": False})
        self.assertEqual(calls["global"][0]["configVersion"], 2)
        self.assertFalse(os.path.exists(self.old_state_path))

    def test_read_old_chrome_tolerates_absent_and_corrupt_files(self):
        self.assertEqual(_read_old_chrome(None), {})
        self.assertEqual(_read_old_chrome(os.path.join(self.dir.name, "missing.json")), {})
        with open(self.old_state_path, "w", encoding="utf-8") as handle:
            handle.write("[1]")
        self.assertEqual(_read_old_chrome(self.old_state_path), {})
        with open(self.old_state_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(_read_old_chrome(self.old_state_path), {})

    def test_removing_the_old_state_file_is_tolerant(self):
        _remove_old_state_file(None)
        _remove_old_state_file(os.path.join(self.dir.name, "missing.json"))
        with open(self.old_state_path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        _remove_old_state_file(self.old_state_path)
        self.assertFalse(os.path.exists(self.old_state_path))

    def test_write_new_files_skips_non_mapping_records(self):
        written = []
        ok = _write_new_files({"A": "junk", "B": {"url": "http://b:7125"}},
                              lambda document: written.append(document) or True,
                              lambda document: True,
                              lambda machine_id, document: True,
                              None, MigrationOutcome(), "stamp")
        self.assertTrue(ok)
        self.assertEqual(list(written[0]["machines"]), ["B"])

    def test_the_clean_resets_every_legacy_key_to_its_registered_default(self):
        written = {}
        _clean_preferences(lambda key, value: written.__setitem__(key, value))
        self.assertEqual(written[PrinterConfigStore.PREF_KEY], "{}")
        self.assertFalse(written[PrinterConfigStore.MIGRATED_KEY])
        self.assertFalse(written[PrinterConfigStore.MOONRAKER_CONNECTION_MIGRATED_KEY])
        self.assertTrue(written["moonrakerprintfollower/bed_mesh_visible"])
        self.assertEqual(written["moonrakerprintfollower/bed_mesh_exaggeration"], 20.0)
        for field_name, pref_key in PrinterConfigStore.LEGACY_MAP.items():
            self.assertEqual(written[pref_key], PrinterConfigStore.LEGACY_DEFAULTS[field_name])

    def test_the_record_names_the_backup_file_never_its_path(self):
        outcome = MigrationOutcome(status="ok", reason="migrated", backup_name="cura.cfg.stamp",
                                   backup_written=True, records=2)
        record = _record(outcome, "2026-09-18-10-00-00")
        self.assertEqual(record["backupName"], "cura.cfg.stamp")
        self.assertEqual(record["attemptedAt"], "2026-09-18-10-00-00")
        self.assertEqual(record["records"], 2)
        self.assertFalse(record["toastShown"])
        self.assertFalse(record["bannerDismissed"])


# --------------------------------------------------------------------------
# PreviewFormatting: the pure Preview projections.
# --------------------------------------------------------------------------

class PreviewFormattingCoverageTests(unittest.TestCase):
    def _status(self, **overrides):
        fields = dict(detail="Following", load_requested=False, loading=False, files_phase="idle",
                      index_phase="idle", attached=True, enabled=True, connected=True,
                      configured=True)
        fields.update(overrides)
        return status_text(**fields)

    def test_status_text_prefers_the_earlier_phases(self):
        self.assertEqual(self._status(load_requested=True, loading=True), "Resolving…")
        self.assertEqual(self._status(loading=True, files_phase="downloading"), "Loading print…")
        self.assertEqual(self._status(files_phase="downloading", index_phase="indexing"),
                         "Downloading…")
        self.assertEqual(self._status(index_phase="indexing"), "Indexing…")
        self.assertEqual(self._status(files_phase="error"), "Error")
        self.assertEqual(self._status(index_phase="error"), "Error")

    def test_status_text_names_detachment_and_the_connection(self):
        self.assertEqual(self._status(attached=False), "Detached")
        self.assertEqual(self._status(attached=False, enabled=False, detail="Idle"), "Idle")
        self.assertEqual(self._status(connected=False), "Disconnected")
        self.assertEqual(self._status(connected=False, configured=False), "Not configured")
        self.assertEqual(self._status(detail="Paused"), "Paused")

    def test_status_icon_marks_the_healthy_words(self):
        self.assertEqual(status_icon("Following"), "CheckCircle")
        self.assertEqual(status_icon("Connected"), "CheckCircle")
        self.assertEqual(status_icon("Indexing…"), "Information")

    def test_pause_toggle_requires_a_layer_inside_the_print(self):
        self.assertTrue(pause_can_toggle(True, 4, 4, 10))
        self.assertTrue(pause_can_toggle(True, 4, 4, None))
        self.assertFalse(pause_can_toggle(False, 4, 4, 10))
        self.assertFalse(pause_can_toggle(True, None, 4, 10))
        self.assertFalse(pause_can_toggle(True, 4, None, 10))
        self.assertFalse(pause_can_toggle(True, 3, 4, 10))
        self.assertFalse(pause_can_toggle(True, 9, 4, 10))

    def test_pause_unavailable_names_the_blocking_reason(self):
        self.assertEqual(pause_unavailable(True, False, False, None, 4),
                         "Waiting for current print layer")
        self.assertEqual(pause_unavailable(True, False, False, 5, 3), "Layer 4 already printed")
        self.assertEqual(pause_unavailable(True, False, False, 5, 5), "Final layer ends the print")
        # A usable or scheduled control has nothing to explain.
        self.assertEqual(pause_unavailable(True, True, False, 5, 6), "")
        self.assertEqual(pause_unavailable(True, False, True, 5, 3), "")
        self.assertEqual(pause_unavailable(False, False, False, 5, 3), "")

    def test_pause_eta_appends_the_wall_clock_when_one_is_supplied(self):
        self.assertEqual(pause_eta(None, lambda seconds: "x"), "ETA unavailable")
        self.assertEqual(pause_eta(600, lambda seconds: "in-10"), "in in-10")
        self.assertEqual(pause_eta(600, lambda seconds: "in-10", clock=lambda seconds: "12:00"),
                         "in in-10 · ≈12:00")

    def test_pause_summary_lists_the_scheduled_layers(self):
        self.assertEqual(pause_summary([]), "")
        self.assertEqual(pause_summary([{"layer": 4}, {"layer": 9}]),
                         "End-of-layer PAUSE: 4, 9")

    def test_pause_items_merge_the_manual_and_the_baked_rows(self):
        items = pause_items([4], {4: "passed"}, (4, 7), lambda layer: 60.0 * layer,
                            lambda seconds: f"{seconds:.0f}s", current=8,
                            clock=lambda seconds: "12:00")
        self.assertEqual([item["layer"] for item in items], [5, 8])
        self.assertEqual(items[0]["state"], "passed")
        self.assertEqual(items[0]["eta"], "in 240s · ≈12:00")
        self.assertEqual(items[1]["state"], "baked")
        self.assertTrue(items[1]["passed"])
        # Without a current layer a baked row is not marked passed.
        baked = pause_items([], {}, (7,), lambda layer: None, lambda seconds: "x")
        self.assertEqual(baked[0]["state"], "baked")
        self.assertFalse(baked[0]["passed"])


# --------------------------------------------------------------------------
# The Qt-side owners.
# --------------------------------------------------------------------------

if QT_AVAILABLE:

    class _Registry(QObject):
        containerRemoved = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.known = []

        def findContainerStacksMetadata(self, id=None):  # noqa: A002  # the registry API's own name
            return [entry for entry in self.known if entry.get("id") == id]

    class _Container:
        def __init__(self, container_id, container_type="machine"):
            self._id = container_id
            self._type = container_type

        def getId(self):
            return self._id

        def getMetaData(self):
            return {"type": self._type}

    class _BindingApp(QObject):
        globalContainerStackChanged = pyqtSignal()

        def __init__(self, preferences, machine_id="A"):
            super().__init__()
            self._preferences = preferences
            self.machine_id = machine_id
            self.registry = None

        def getPreferences(self):
            return self._preferences

        def getGlobalContainerStack(self):
            if self.machine_id is None:
                return None
            machine_id = self.machine_id
            return SimpleNamespace(getId=lambda: machine_id, getName=lambda: f"Printer {machine_id}")

        def getContainerRegistry(self):
            return self.registry

    class _BindingClient:
        def __init__(self):
            self.calls = []
            self.configures = []

        def stop(self, reset_session=True):
            self.calls.append(("stop", reset_session))

        def set_trace_http(self, enabled):
            self.calls.append(("trace", enabled))

        def configure(self, *args, **kwargs):
            self.configures.append((args, kwargs))

        def start(self):
            self.calls.append(("start", None))

    class _TempPersistence:
        def __init__(self):
            self._dir = tempfile.TemporaryDirectory()
            self.base = self._dir.name
            self.persistence = PluginPersistence(
                os.path.join(self.base, "settings.json"),
                os.path.join(self.base, "state", "global.json"),
                os.path.join(self.base, "state", "machines"),
                save=_pretty_save)

        def cleanup(self):
            self._dir.cleanup()

    class PrinterBindingCoverageTests(unittest.TestCase):
        """The binding's branch behaviour. Everything here runs in the
        container; the Cura-side mutations (removeMachine's own
        activations, the real registry service) are the caller's and
        are exercised by the harness, not reproduced here."""

        def setUp(self):
            self.temp = _TempPersistence()
            self.addCleanup(self.temp.cleanup)
            self.persistence = self.temp.persistence
            self.prefs = Preferences({})
            self.app = _BindingApp(self.prefs, "A")
            self.registry = _Registry()
            self.app.registry = self.registry
            self.client = _BindingClient()
            self.cura_cfg = os.path.join(self.temp.base, "cura.cfg")
            # The backup gate copies the whole cura.cfg and refuses a
            # copy without the plugin's blob group, so the fixture must
            # carry one.
            with open(self.cura_cfg, "wb") as handle:
                handle.write(b"[moonrakerprintfollower]\nprinter_configs_v1 = 1\n")
            self.old_state = os.path.join(self.temp.base, "sections.json")
            self.binding = PrinterBinding(self.app, self.client, self.persistence,
                                          self.cura_cfg, self.old_state)
            self.addCleanup(self.binding.close)

        def _record(self, machine_id="A", **fields):
            self.persistence.set_machine_config(machine_id, PrinterConfig(**fields))

        def test_config_reads_the_live_identity_from_either_source(self):
            self._record(url="http://a:7125")
            self.assertEqual(self.binding.config.url, "http://a:7125")
            self.assertTrue(self.binding.configured)
            # A machine with no record yet falls back to the preference
            # blob; the identity is the binding's own read, so the app's
            # machine changing without a republish is not seen here.
            self.binding._machine_id = "B"
            self.assertEqual(self.binding.config.url, "http://")
            self.assertFalse(self.binding.configured)
            self.binding._machine_id = "unknown"
            self.assertFalse(self.binding.configured)

        def test_usable_requires_a_real_http_endpoint(self):
            self.assertTrue(PrinterBinding.usable("http://printer.local:7125"))
            self.assertFalse(PrinterBinding.usable("http://"))
            self.assertFalse(PrinterBinding.usable("ftp://printer.local"))
            self.assertFalse(PrinterBinding.usable("not a url"))

        def test_the_bed_mesh_keys_move_once_and_are_never_overwritten(self):
            self.prefs.addPreference("moonrakerprintfollower/bed_mesh_visible", True)
            self.prefs.addPreference("moonrakerprintfollower/bed_mesh_exaggeration", 20.0)
            self.prefs.setValue("moonrakerprintfollower/bed_mesh_visible", False)
            self.prefs.setValue("moonrakerprintfollower/bed_mesh_exaggeration", "nonsense")
            self.binding._carry_bed_mesh_preferences(self.prefs)
            global_section = self.persistence.settings_document()["global"]
            self.assertFalse(global_section["bedMeshVisible"])
            self.assertEqual(global_section["bedMeshExaggeration"], 20.0)
            # A second run leaves the live values alone.
            self.persistence.set_global({"bedMeshExaggeration": 55.0})
            self.binding._carry_bed_mesh_preferences(self.prefs)
            self.assertEqual(self.persistence.settings_document()["global"]["bedMeshExaggeration"], 55.0)

        def test_the_migration_is_deferred_until_the_legacy_chain_completed(self):
            blob = json.dumps({"A": {"url": "http://a:7125"}})
            self.prefs.setValue(PrinterConfigStore.PREF_KEY, blob)
            self.binding.run_persistence_migration()
            # The v2 activation may land, but the one-shot is deferred:
            # no migrated machine, no record.
            self.assertIsNone(self.persistence.get_machine("A"))
            self.assertIsNone(self.persistence.migration_record())
            self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
            self.binding.run_persistence_migration()
            self.assertEqual(self.persistence.get_machine("A")["url"], "http://a:7125")
            self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY), "{}")
            self.assertFalse(self.prefs.getValue(PrinterConfigStore.MIGRATED_KEY))

        def test_an_ok_record_replays_only_the_idempotent_post_conditions(self):
            with open(self.old_state, "w", encoding="utf-8") as handle:
                json.dump({"sections": {}}, handle)
            self.persistence.set_global({"migration": {"status": "ok"}, "bedMeshVisible": True,
                                         "bedMeshExaggeration": 20.0})
            self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
            self.binding.run_persistence_migration()
            self.assertFalse(os.path.exists(self.old_state))
            # The blob is cleaned, never re-migrated: the machine record
            # stays absent.
            self.assertIsNone(self.persistence.get_machine("A"))

        def test_a_terminal_failure_record_is_not_replayed(self):
            self.persistence.set_global({"migration": {"status": "failed", "reason": "corrupt-blob",
                                                       "backupWritten": True}})
            self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
            self.prefs.setValue(PrinterConfigStore.PREF_KEY, json.dumps({"A": {"url": "http://a:7125"}}))
            self.binding.run_persistence_migration()
            self.assertIsNone(self.persistence.get_machine("A"))
            # A retryable one replays instead.
            self.persistence.set_global({"migration": {"status": "failed", "reason": "backup-failed"}})
            with open(self.cura_cfg, "wb") as handle:
                handle.write(b"[moonrakerprintfollower]\nprinter_configs_v1 = 1\n")
            self.binding.run_persistence_migration()
            self.assertIsNotNone(self.persistence.get_machine("A"))

        def test_a_camera_only_change_never_reconfigures_the_client(self):
            self._record(url="http://a:7125", camera_selected="cam-1")
            self.binding.apply(replace_config(self.binding.config, camera_selected="cam-2"))
            self.assertEqual(self.client.configures, [])
            self.assertEqual(self.persistence.get_machine("A")["camera_selected"], "cam-2")
            # Only the trace mirror moved: no stop, no start, no rebind.
            self.assertEqual([call for call in self.client.calls if call[0] in ("stop", "start")], [])

        def test_an_endpoint_change_tears_the_poller_down_without_emitting(self):
            self._record(url="http://a:7125")
            emitted = []
            self.binding.changed.connect(lambda: emitted.append(1))
            self.binding.apply(replace_config(self.binding.config, url="http://b:7125"))
            self.assertIn(("stop", False), self.client.calls)
            self.assertEqual(self.client.calls[-1], ("start", None))
            self.assertTrue(emitted)
            # The leak instrument's legacy mirror tracks the save.
            self.assertTrue(self.prefs.getValue(PrinterConfigStore.LEGACY_MAP["enabled"]))

        def test_an_unconfigured_endpoint_stops_rather_than_starts(self):
            self.binding.apply(PrinterConfig(url="http://"))
            self.assertEqual([call for call in self.client.calls if call[0] == "start"], [])
            self.assertEqual(self.client.calls[-1][0], "stop")
            self.assertEqual(self.client.configures[-1][1]["feed_mode"], "websocket")

        def test_a_closed_binding_ignores_further_saves(self):
            self.binding.close()
            self.binding.close()  # idempotent
            before = list(self.client.calls)
            self.binding.apply(PrinterConfig(url="http://a:7125"))
            self.assertEqual(self.client.calls, before)

        def test_a_machine_switch_invalidates_the_session(self):
            emitted = []
            self.binding.changed.connect(lambda: emitted.append(1))
            self.app.machine_id = "B"
            self.app.globalContainerStackChanged.emit()
            # The switch tears the old session down, and the new machine
            # has no record yet so the apply leaves the lane stopped.
            self.assertEqual(self.client.calls, [("stop", True), ("stop", True)])
            self.assertEqual(self.binding.identity[0], "B")
            self.assertTrue(emitted)

        def test_a_name_only_change_republishes_without_tearing_down(self):
            emitted = []
            self.binding.changed.connect(lambda: emitted.append(1))
            self.app.globalContainerStackChanged.emit()
            self.assertEqual(self.binding.identity, ("A", "Printer A"))
            self.assertEqual(self.client.calls, [])
            self.assertTrue(emitted)

        def test_the_removal_filter_ignores_non_machines_and_unknown_ids(self):
            self.binding._container_removed(SimpleNamespace(getMetaData=lambda: {"type": "quality"}))
            self.binding._container_removed(_Container("A"))
            self.assertEqual(self.persistence.get_machine("A"), None)
            self._record(url="http://a:7125")
            self.binding._container_removed(SimpleNamespace(getMetaData=lambda: {
                "type": "machine"}, getId=lambda: "A"))
            self.assertEqual(self.persistence.get_machine("A")["url"], "http://a:7125")

        def test_the_removal_filter_ignores_a_container_that_raises(self):
            class _Broken:
                def getMetaData(self):
                    raise RuntimeError("gone")

            self.binding._container_removed(_Broken())
            self.assertEqual(self.persistence.get_machine("A"), None)

        def test_the_rename_emission_is_filtered_by_the_registry(self):
            self._record(url="http://a:7125")
            self.registry.known = [{"id": "A"}]
            self.binding._container_removed(_Container("A"))
            self.assertEqual(self.persistence.get_machine("A")["url"], "http://a:7125")

        def test_the_removal_wipes_the_removed_machines_credentials(self):
            self._record(url="http://a:7125", api_key="secret")
            self.app.machine_id = "B"
            self.binding._machine_id = "B"
            with patch.object(QTimer, "singleShot", lambda *args: args[1]() if callable(args[1]) else None):
                self.binding._container_removed(_Container("A"))
            wiped = self.persistence.get_machine("A")
            for field in _REMOVAL_WIPE_FIELDS:
                self.assertIn(field, wiped, field)
            self.assertEqual(wiped["url"], "http://")
            self.assertEqual(wiped["api_key"], "")
            self.assertIn("removed_at", wiped)
            # The removed id is not the active one: nothing tears down.
            self.assertEqual(self.client.calls, [])

        def test_removing_the_active_machine_stops_the_poller(self):
            self._record(url="http://a:7125")
            with patch.object(QTimer, "singleShot", lambda *args: args[1]() if callable(args[1]) else None):
                self.binding._container_removed(_Container("A"))
            self.assertEqual(self.client.calls, [("stop", True)])

        def test_a_failed_legacy_migration_is_logged_not_fatal(self):
            # The chain carries on past a raising step: the failure is
            # logged, and the next migration still runs.
            ran = []
            with patch.object(PrinterConfigStore, "migrate_legacy_to_current_machine",
                              side_effect=RuntimeError("old data")), \
                    patch.object(PrinterConfigStore, "migrate_moonraker_connection",
                                 side_effect=lambda: ran.append("connection")):
                self.binding._migrate()
            self.assertEqual(ran, ["connection"])

        def test_close_survives_a_machine_signal_that_cannot_disconnect(self):
            class _Stubborn:
                def disconnect(self, slot):
                    raise TypeError("not connected")

            self.binding._machine_signal = _Stubborn()
            self.binding.close()
            self.assertEqual(self.client.calls, [("stop", True)])

        def test_a_failed_one_shot_is_logged_rather_than_fatal(self):
            self.prefs.setValue(PrinterConfigStore.MIGRATED_KEY, True)
            self.prefs.setValue(PrinterConfigStore.PREF_KEY, '{"A": {"url": "http://a:7125"}}')
            os.remove(self.cura_cfg)  # the backup's source is gone
            self.binding.run_persistence_migration()
            # The failure is terminal for this run: nothing cleaned the
            # preference blob that the one-shot failed to land.
            self.assertEqual(self.prefs.getValue(PrinterConfigStore.PREF_KEY),
                             '{"A": {"url": "http://a:7125"}}')

        def test_a_recorded_migration_never_replays_the_legacy_chain(self):
            self.persistence.set_global({"migration": {"status": "ok"}})
            with patch.object(PrinterConfigStore, "migrate_legacy_to_current_machine") as legacy:
                self.binding._migrate()
            self.assertEqual(legacy.call_count, 0)

        def test_start_applies_after_migrating(self):
            self.binding.start()
            self.assertTrue(self.client.configures)

    class MonitorTuningCoverageTests(unittest.TestCase):
        """The debouncer's revision guard. The QML Slider gesture itself
        is the harness's; the value arbitration is entirely here."""

        def setUp(self):
            self.qt = _QT
            self.debounce = []
            self.commands = SimpleNamespace(quick=self._quick, quick_results=[])
            self.data = _TuningData()
            self.tuning = MonitorTuning(self.data, self.commands)
            self.addCleanup(self.tuning.reset)

        def _quick(self, channel, script, callback):
            self.debounce.append((channel, script, callback))
            if self.commands.quick_results:
                return self.commands.quick_results.pop(0)
            return True

        def test_reset_drops_every_pending_value_and_timer(self):
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            emitted = []
            self.tuning.changed.connect(lambda: emitted.append(1))
            self.tuning.reset()
            # Nothing is pending: the model publishes the printer's value.
            self.assertEqual(self.tuning.value("speed", 100), 100)
            self.assertEqual(self.tuning._pending, {})
            self.assertTrue(emitted)
            self.assertEqual(self.tuning._revision, 2)

        def test_a_re_grab_keeps_the_released_value_authoritative(self):
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            revision = self.tuning._revision
            self.tuning.preview("speed", 80)
            # The preview must not displace the released value, and the
            # queued send keeps the newer revision.
            self.assertEqual(self.tuning.value("speed", 100), 50)
            self.assertGreater(self.tuning._revision, revision)
            pending = self.tuning._pending["speed"]
            self.assertEqual(pending.value, 50)
            self.assertEqual(pending.revision, self.tuning._revision)

        def test_a_pending_slider_never_publishes_its_preview(self):
            self.tuning.preview("speed", 80)
            self.assertEqual(self.tuning.value("speed", 100), 100)
            self.tuning.preview("speed", 90)
            self.assertEqual(self.tuning.value("speed", 100), 100)

        def test_a_queue_is_ignored_while_the_monitor_is_inactive(self):
            self.data.active = False
            emitted = []
            self.tuning.changed.connect(lambda: emitted.append(1))
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            self.assertEqual(self.tuning.value("speed", 100), 100)
            self.assertEqual(emitted, [])

        def test_observation_clears_a_sent_value_once_the_printer_agrees(self):
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            self.tuning._send("speed")
            self.assertEqual(self.debounce[0][0], "speed")
            self.tuning.observe("speed", 100)
            self.assertEqual(self.tuning.value("speed", 100), 50)
            self.tuning.observe("speed", 50.5)
            self.assertEqual(self.tuning.value("speed", 100), 100)

        def test_a_confirm_timeout_expires_the_row(self):
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            self.tuning._send("speed")
            self.tuning._expire("speed")
            self.assertEqual(self.data.refreshes, 1)
            self.assertEqual(self.tuning._pending, {})

        def test_a_rejected_command_expires_immediately(self):
            self.commands.quick_results = [False]
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            self.tuning._send("speed")
            self.assertEqual(self.tuning._pending, {})
            self.assertEqual(self.data.refreshes, 1)

        def test_the_completion_callback_ignores_a_superseded_revision(self):
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            self.tuning._send("speed")
            superseded = self.debounce[-1][2]
            self.tuning.queue("speed", 60, "speed", "M220 S60")  # a newer revision
            self.tuning._send("speed")
            superseded({"result": {}}, None)
            # The older revision's completion is dropped: no refresh, and
            # the released value stands.
            self.assertEqual(self.tuning.value("speed", 100), 60)
            self.assertEqual(self.data.later_calls, [])
            # The live revision's failure path expires it.
            self.debounce[-1][2](None, "refused")
            self.assertEqual(self.tuning._pending, {})

        def test_a_successful_completion_refreshes_late(self):
            self.tuning.queue("speed", 50, "speed", "M220 S50")
            self.tuning._send("speed")
            self.debounce[0][2]({"result": {}}, None)
            self.assertEqual(self.data.refreshes, 0)
            self.assertEqual(self.data.later_calls, [(150, self.data.refresh_all)])

        def test_sending_without_a_script_is_a_no_op(self):
            self.tuning.preview("speed", 80)
            self.tuning._send("speed")
            self.assertEqual(self.debounce, [])

        def test_matches_compares_sequences_elementwise(self):
            self.assertTrue(self.tuning.matches([1, 2], [1.5, 2.4], tolerance=0.5))
            self.assertFalse(self.tuning.matches([1, 2], [1, 9], tolerance=0.5))
            self.assertFalse(self.tuning.matches([1, 2], [1, 2, 3]))
            self.assertFalse(self.tuning.matches([1, 2], 1))
            self.assertTrue(self.tuning.matches("off", "off"))
            self.assertFalse(self.tuning.matches("off", "on"))

    class PauseControllerCoverageTests(unittest.TestCase):
        """The print-local pause lane. The polling cadence and the
        printer's own confirmation are the session's; this asserts the
        lane's arbitration over them."""

        def setUp(self):
            self.client = _PauseClient()
            self.controller = PauseController(self.client)
            self.addCleanup(self.controller.close)
            self.messages = []
            self.controller.message.connect(self.messages.append)

        def test_bind_is_a_no_op_for_the_same_job_and_resets_on_a_new_one(self):
            self.controller.bind(("part", 100, 1))
            self.assertTrue(self.controller.toggle(4, 0, 10))
            self.controller.bind(("part", 100, 1))
            self.assertEqual(self.controller.layers, frozenset({4}))
            self.controller.bind(("part", 200, 2))
            self.assertEqual(self.controller.layers, frozenset())
            self.assertEqual(self.controller.states, {})
            self.assertIn(("pause", None), self.client.transport.cancelled)

        def test_toggle_refuses_layers_outside_the_live_print(self):
            self.assertFalse(self.controller.toggle(4, 0, 10))          # no job bound
            self.controller.bind(("part", 100, 1))
            self.assertFalse(self.controller.toggle(4, None, 10))
            self.assertFalse(self.controller.toggle(2, 4, 10))
            self.assertFalse(self.controller.toggle(9, 4, 10))          # the final layer ends it
            self.assertTrue(self.controller.toggle(9, 4, None))          # no total: no final-layer rule
            self.assertTrue(self.controller.toggle(9, 4, None))          # toggling removes it again

        def test_toggle_removes_an_existing_entry(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.assertTrue(self.controller.toggle(4, 0, 10))
            self.assertEqual(self.controller.layers, frozenset())
            self.controller.remove(4)  # removing an absent layer is harmless

        def test_clear_empties_the_schedule_and_the_states(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller.clear()
            self.assertEqual(self.controller.layers, frozenset())
            self.assertEqual(self.controller.states, {})
            self.assertIn(False, self.client.guards)

        def test_observe_arms_the_guard_only_near_the_target(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(10, 0, 100)
            self.controller.observe(5)
            self.assertEqual(self.client.guards[-1], False)
            self.controller.observe(9)
            self.assertEqual(self.client.guards[-1], True)
            self.controller.observe(None)
            self.assertEqual(self.client.guards[-1], False)

        def test_observe_dispatches_one_pause_and_never_re_arms(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller.observe(5)
            self.assertEqual(self.controller.states, {4: "fired"})
            self.assertEqual(self.client.tracked[-1][0], "ScheduledPause")
            self.assertEqual(self.client.transport.requests[-1].path, "printer/gcode/script")
            self.assertIn("Requesting PAUSE after layer 5", self.messages)
            # A second poll with the pause still unconfirmed must not
            # send a second PAUSE, and no new entry fires.
            sent = len(self.client.transport.requests)
            self.controller.observe(6)
            self.assertEqual(len(self.client.transport.requests), sent)

        def test_observe_stands_down_for_an_observed_terminal_state(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.client.status = {"print_stats": {"state": "complete"}}
            self.controller.observe(5)
            self.assertEqual(self.controller.states, {})
            self.assertEqual(self.client.transport.requests, [])

        def test_observe_accepts_an_unobserved_state(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.client.status = {}
            self.controller.observe(5)
            self.assertEqual(self.controller.states, {4: "fired"})

        def test_an_in_flight_refusal_fails_the_command(self):
            self.client.transport.started = False
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller.observe(5)
            self.assertEqual(self.client.failed[-1][0], "ScheduledPause")

        def test_the_transport_reply_accepts_a_lost_or_superseded_pause(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller.observe(5)
            reply = self.client.transport.requests[-1].callback
            reply(None, "offline")
            self.assertEqual(self.client.failed[-1], ("ScheduledPause", "offline"))
            # A reply from a superseded job must not touch the new one.
            self.controller.bind(("part", 200, 2))
            reply({"result": {}}, None)
            self.assertEqual(self.client.accepted, [])
            # The live job's own reply reaches the command record.
            self.controller.toggle(4, 0, 10)
            self.controller.observe(5)
            self.client.transport.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.client.accepted, ["ScheduledPause"])

        def test_close_swallows_a_client_that_refuses_to_disconnect(self):
            class _Signal:
                def connect(self, slot):
                    self.slot = slot

                def disconnect(self, slot):
                    raise TypeError("not connected")

            controller = PauseController(SimpleNamespace(commandChanged=_Signal()))
            controller.close()
            self.assertIsNone(controller._job)

        def test_a_confirmed_pause_stays_listed_as_passed(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller.observe(5)
            self.controller._command_changed({"name": "ScheduledPause", "outcome": "accepted"})
            self.assertIn("waiting for printer confirmation", self.messages[-1])
            self.assertEqual(self.controller.states, {4: "fired"})
            self.controller._command_changed({"name": "ScheduledPause", "outcome": "confirmed"})
            self.assertEqual(self.controller.states.get(4), "passed")
            self.assertIn(4, self.controller.layers)
            self.assertIsNone(self.controller._target)

        def test_a_missed_pause_is_restyled_and_never_re_arms(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller.observe(5)
            self.controller._command_changed({"name": "ScheduledPause", "outcome": "timed_out",
                                              "detail": "no pause observed"})
            self.assertEqual(self.controller.states.get(4), "timed_out")
            self.assertIn("no pause observed", self.messages[-1])
            self.controller._command_changed({"name": "ScheduledPause", "outcome": "failed"})
            self.assertEqual(self.controller.states.get(4), "timed_out")

        def test_an_unrelated_command_event_is_ignored(self):
            self.controller.bind(("part", 100, 1))
            self.controller.toggle(4, 0, 10)
            self.controller._command_changed({"name": "Pause"})
            self.controller._command_changed({"name": "ScheduledPause", "outcome": "confirmed"})
            self.assertEqual(self.controller.states, {})

    class GCodeIndexServiceCoverageTests(unittest.TestCase):
        """The index lifecycle. The workers run inline through an
        injected executor — the thread hop is the harness's contract,
        not a behaviour of this owner."""

        def setUp(self):
            self.root = tempfile.TemporaryDirectory()
            self.addCleanup(self.root.cleanup)
            self.files = _IndexFiles()
            self.cache = _IndexCache()
            self.service = GCodeIndexService(self.files, self.cache)
            self.addCleanup(self.service.close)
            self.service._executor = _InlineExecutor()
            self.job = ("part.gcode", 100, 1)
            # The files owner publishes the live job key; _advance trusts
            # no other source.
            self.files.job_key = self.job
            self.path = os.path.join(self.root.name, "part.gcode")
            with open(self.path, "w", encoding="utf-8") as handle:
                handle.write("G1 X0 Y0\n;LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n")

        def _index(self, layers=3):
            return LayerMotionIndex(ranges=[(index * 10, index * 10 + 10) for index in range(layers)],
                                    current_layer_map={index: index for index in range(layers)})

        def test_the_phase_names_the_lifecycle(self):
            self.assertEqual(self.service.phase, "idle")
            self.service._view = IndexView(self.job, self._index())
            self.assertEqual(self.service.phase, "ready")
            self.service._busy = "build"
            self.assertEqual(self.service.phase, "indexing")
            self.service._error = "boom"
            self.assertEqual(self.service.phase, "error")

        def test_bind_resets_the_progress_of_a_previous_print(self):
            self.service._progress = 1.0
            self.service._error = "boom"
            self.service._failed_hydrate.add(1)
            self.service.bind(self.job)
            self.assertIsNone(self.service._progress)
            self.assertEqual(self.service._error, "")
            self.assertEqual(self.service._failed_hydrate, set())
            generation = self.service.generation
            self.service.bind(self.job)  # same job: no new generation
            self.assertEqual(self.service.generation, generation)

        def test_a_request_without_an_identity_asks_for_metadata(self):
            self.service.bind(self.job)
            self.service.request()
            self.assertEqual(self.files.metadata_requests, 1)
            self.assertEqual(self.files.file_requests, 0)

        def test_a_strong_identity_restores_the_cache_off_the_ui_thread(self):
            self.files.identity = SimpleNamespace(uuid="u", modified=1)
            self.cache.load_result = self._index()
            self.service.bind(self.job)
            self.service.request()
            self.assertEqual(self.cache.loads, [self.files.identity])
            self.assertIsNotNone(self.service.view)

        def test_a_missing_lease_asks_for_the_file_before_building(self):
            self.files.identity = SimpleNamespace(uuid="", modified=0)
            self.service.bind(self.job)
            self.service.request()
            self.assertEqual(self.files.file_requests, 1)
            self.assertIsNone(self.service.view)

        def test_a_weak_identity_still_restores_once_then_builds(self):
            self.files.identity = SimpleNamespace(uuid="", modified=0)
            self.files.path = self.path
            self.service.bind(self.job)
            self.service.request()
            self.assertIsNotNone(self.service.view)
            self.assertEqual(self.service._save, True)

        def test_a_finished_build_reports_its_progress_and_persists(self):
            self.files.identity = SimpleNamespace(uuid="u", modified=1)
            self.files.path = self.path
            self.service.bind(self.job)
            self.service.request()
            self.assertIsNotNone(self.service.view)
            self.assertIs(self.service.view._index.__class__, LayerMotionIndex)
            self.assertEqual(self.cache.saves[0][0], self.files.identity)

        def test_a_build_with_no_layer_markers_fails_loudly(self):
            self.files.identity = SimpleNamespace(uuid="u", modified=1)
            self.files.path = os.path.join(self.root.name, "empty.gcode")
            with open(self.files.path, "w", encoding="utf-8") as handle:
                handle.write("G28\n")
            failures = []
            self.service.failed.connect(failures.append)
            self.service.bind(self.job)
            self.service.request()
            self.assertEqual(self.service.phase, "error")
            self.assertEqual(len(failures), 1)
            self.assertIsNone(self.service.view)

        def test_a_hydration_request_outside_the_index_is_ignored(self):
            self.service.bind(self.job)
            self.service.request_hydration(0)  # no view yet
            self.assertEqual(self.service._hydrate, set())
            self.service._view = IndexView(self.job, self._index())
            self.service.request_hydration(9)
            self.assertEqual(self.service._hydrate, set())

        def test_a_hydration_request_prefetches_the_current_and_next_layer(self):
            index = self._index()
            index.compact = True  # a compact index reports layers as unhydrated
            self.service.bind(self.job)
            self.service._view = IndexView(self.job, index)
            self.service.request_hydration(0)
            self.assertEqual(self.service._hydrate, {0, 1})

        def test_a_hydration_failure_is_latched_until_the_file_changes(self):
            index = self._index()
            index.compact = True
            self.service.bind(self.job)
            self.service._view = IndexView(self.job, index)
            self.service._hydrating = 1
            self.service._finish(self.service.generation, "hydrate", False, None, None)
            self.assertEqual(self.service._failed_hydrate, {1})
            self.assertIsNone(self.service._hydrating)
            # The latch spares the next poll a full re-read of the file.
            self.files.identity = SimpleNamespace(uuid="u", modified=1)
            self.service._restored = True
            self.service._wanted = True
            self.service._hydrate = {1}
            self.service._advance()
            self.assertEqual(self.service._hydrate, set())

        def test_a_successful_hydration_marks_the_index_worth_saving(self):
            self.service.bind(self.job)
            self.service._view = IndexView(self.job, self._index())
            self.service._finish(self.service.generation, "hydrate", True, None, None)
            self.assertTrue(self.service._save)

        def test_the_followed_layer_anchors_the_retention_window(self):
            index = self._index()
            self.service.bind(self.job)
            self.service._view = IndexView(self.job, index)
            self.service._hydrate = {0, 4, 9}
            self.service.set_followed_layer(4)
            self.assertEqual(index.followed_layer, 4)
            self.assertEqual(self.service._hydrate, {4})
            # A non-integer anchor and a missing view are no-ops.
            self.service.set_followed_layer("4")
            self.assertEqual(index.followed_layer, 4)
            self.service._view = None
            self.service.set_followed_layer(2)

        def test_a_new_file_invalidates_failed_hydrations(self):
            self.service._failed_hydrate.add(1)
            self.files.changed.emit()
            self.assertEqual(self.service._failed_hydrate, set())

        def test_a_stale_worker_is_dropped_but_its_lease_is_returned(self):
            self.service.bind(self.job)
            generation = self.service.generation
            self.service.bind(("other.gcode", 200, 2))
            lease = _IndexLease(self.path)
            self.service._finish(generation, "build", self._index(), None, lease)
            self.assertTrue(lease.closed)
            self.assertIsNone(self.service.view)

        def test_a_worker_whose_qt_owner_died_still_closes_its_lease(self):
            self.service.bind(self.job)
            lease = _IndexLease(self.path)
            with patch.object(type(self.service), "_completed", new=_dead_signal()):
                self.service._submit("build", lambda: self._index(), lease)
            self.assertTrue(lease.closed)
            # With no reply channel left the lane stays busy — the lease
            # must still go home.
            self.assertEqual(self.service._busy, "build")

        def test_the_view_exposes_only_the_read_only_query_surface(self):
            index = self._index()
            index.motion_offsets = [[], [], []]
            index.layer_elapsed_times = [0.0, 12.5, 25.0]
            index.pauses = (1,)
            view = IndexView(self.job, index)
            self.assertEqual(len(view.ranges), 3)
            self.assertEqual(view.current_layer_map[2], 2)
            self.assertEqual(view.elapsed_times, (0.0, 12.5, 25.0))
            self.assertFalse(view.compact)
            self.assertEqual(view.pause_layers, (1,))
            self.assertTrue(view.hydrated(0))  # not compact: every layer is hydrated
            # Ranges are half-open: 100 lands on the last layer, and a
            # position before the first layer has no answer.
            self.assertEqual(view.layer_at(5), 0)
            self.assertEqual(view.layer_at(100), 2)
            self.assertIsNone(view.layer_at(-5))
            self.assertEqual(view.fraction(0, 5, None), (0.5, "byte position"))
            self.assertEqual(view.fraction(0, 5, None, minimum=0.75),
                             (0.75, "byte position (monotonic)"))
            index.compact = True
            index.hydrated_layers.add(1)
            self.assertEqual(view.compact, True)
            self.assertFalse(view.hydrated(0))
            self.assertTrue(view.hydrated(1))

        def test_a_pending_hydration_takes_the_lease_and_drains(self):
            index = self._index()
            index.compact = True  # only the followed window is hydrated
            self.service.bind(self.job)
            self.service._view = IndexView(self.job, index)
            self.files.identity = SimpleNamespace(uuid="u", modified=1)
            self.files.path = self.path
            self.service._restored = True
            self.service._wanted = True
            self.service._hydrate = {2, 1}
            hydrations = []
            with patch("plugins.GCodeIndexService.hydrate_layer_from_file",
                       side_effect=lambda index, path, layer: hydrations.append(layer) or True):
                self.service._advance()
            # The lowest pending layer goes first, and each completion
            # re-advances until the window is drained.
            self.assertEqual(hydrations, [1, 2])
            self.assertEqual(self.service._hydrate, set())
            self.assertIsNone(self.service._hydrating)

        def test_a_pending_hydration_without_a_lease_asks_for_the_file(self):
            index = self._index()
            index.compact = True
            self.service.bind(self.job)
            self.service._view = IndexView(self.job, index)
            self.files.identity = SimpleNamespace(uuid="u", modified=1)
            self.service._restored = True
            self.service._wanted = True
            self.service._hydrate = {1}
            self.service._advance()
            # The layer stays queued until the bytes are local.
            self.assertEqual(self.files.file_requests, 1)
            self.assertEqual(self.service._hydrate, {1})

        def test_a_worker_that_raises_reports_the_error_instead_of_crashing(self):
            def unreadable():
                raise RuntimeError("unreadable")

            self.service.bind(self.job)
            self.service._restored = True
            self.service._wanted = True
            self.service._submit("build", unreadable)
            self.assertEqual(self.service.phase, "error")
            self.assertEqual(self.service._error, "unreadable")

        def test_close_is_idempotent_and_cancels_the_worker(self):
            self.service.bind(self.job)
            self.service.close()
            generation = self.service.generation
            self.service.close()
            self.assertEqual(self.service.generation, generation)
            self.assertTrue(self.service._cancel.is_set())

    class _IndexFiles(QObject):
        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.job_key = None
            self.identity = None
            self.path = None
            self.metadata_requests = 0
            self.file_requests = 0

        def lease(self):
            return _IndexLease(self.path) if self.path else None

        def request_metadata(self):
            self.metadata_requests += 1

        def request_file(self):
            self.file_requests += 1

    class _IndexLease:
        def __init__(self, path):
            self.path = path
            self.closed = False

        def close(self):
            self.closed = True

    class _IndexCache:
        def __init__(self):
            self.loads = []
            self.saves = []
            self.load_result = None

        def load(self, identity):
            self.loads.append(identity)
            return self.load_result

        def save(self, identity, index):
            self.saves.append((identity, index))
            return True

    class _InlineExecutor:
        def __init__(self):
            self.submissions = []

        def submit(self, work):
            self.submissions.append(work)
            return _InlineFuture(work)

        def shutdown(self, **kwargs):
            self.shutdown_kwargs = kwargs

    class _InlineFuture:
        def __init__(self, work):
            try:
                self._value, self._error = work(), None
            except Exception as exc:  # the worker's own failure surface
                self._value, self._error = None, exc

        def result(self):
            if self._error is not None:
                raise self._error
            return self._value

        def add_done_callback(self, callback):
            callback(self)

    class _DeadSignal:
        """A signal whose owner was destroyed with the C++ object."""

        def emit(self, *args):
            raise RuntimeError("wrapped C/C++ object has been deleted")

    def _dead_signal():
        return _DeadSignal()

    class MonitorCameraCoverageTests(unittest.TestCase):
        """Webcam selection and the bridge decision. The QML ComboBox's
        model consumption is the harness's; the value shape is here."""

        def setUp(self):
            self.data = _CameraData()
            self.config = PrinterConfig(url="http://printer.local:7125")
            self.applied = []
            self.camera = MonitorCamera(self.data, lambda: self.config, self.applied.append)
            self.addCleanup(self._close_bridge)

        def _close_bridge(self):
            if self.camera._camera_bridge is not None:
                self.camera._camera_bridge.stop()
                self.camera._camera_bridge = None

        def _snapshot(self, *cameras):
            self.data.set_webcams(tuple(cameras))

        def test_identity_prefers_the_stable_identifiers(self):
            self.assertEqual(MonitorCamera.identity({"uid": "u", "id": "i", "name": "n"}), "u")
            self.assertEqual(MonitorCamera.identity({"id": "i", "name": "n"}), "i")
            self.assertEqual(MonitorCamera.identity({"name": "n"}), "n")
            self.assertEqual(MonitorCamera.identity({}, 3), "camera-3")
            self.assertEqual(MonitorCamera.identity_aliases({"uid": "u", "name": "n"}, 1),
                             {"u", "n", "camera-1"})

        def test_a_foreign_stream_url_is_served_direct_without_the_key(self):
            # C (the 2026-09-19 review): the Moonraker key may only
            # ride requests to the printer's own origin — a webcam on
            # another host is served direct and keyless, and no
            # bridge is ever created for it.
            self.config = PrinterConfig(url="http://printer.local:7125", api_key="SECRET")
            self.camera = MonitorCamera(self.data, lambda: self.config, self.applied.append)
            self._snapshot({"uid": "u1", "name": "Box",
                            "stream_url": "http://camera-box:8080/stream"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.url, "http://camera-box:8080/stream")
            self.assertIsNone(self.camera._camera_bridge)

        def test_a_same_origin_stream_keeps_the_bridged_key_carry(self):
            self.config = PrinterConfig(url="http://printer.local:7125", api_key="SECRET")
            self.camera = MonitorCamera(self.data, lambda: self.config, self.applied.append)
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/webcam?action=stream"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertTrue(self.camera.url.startswith("http://127.0.0.1:"))
            self.assertEqual(self.camera._camera_bridge._upstream_base, "http://printer.local:7125")
            self.assertEqual(self.camera._camera_bridge._api_key, "SECRET")

        def test_a_deposed_camera_retires_its_bridge_and_reconfigures_again(self):
            # D: A -> B leaves no listener and no key behind; B -> A
            # configures the cached bridge again normally.
            self.config = PrinterConfig(url="http://printer.local:7125", api_key="SECRET")
            self.camera = MonitorCamera(self.data, lambda: self.config, self.applied.append)
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/webcam?action=stream"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertGreater(self.camera._camera_bridge.port, 0)
            # The switch: the same machine loses its key (B).
            self.config = PrinterConfig(url="http://printer.local:7125")
            self.camera._key = None
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.url, "http://printer.local:7125/webcam?action=stream")
            self.assertEqual(self.camera._camera_bridge.port, 0)
            self.assertEqual(self.camera._camera_bridge._upstream_base, "")
            self.assertEqual(self.camera._camera_bridge._api_key, "")
            # Back to A: the cached bridge listens again with the key.
            self.config = PrinterConfig(url="http://printer.local:7125", api_key="SECRET")
            self.camera._key = None
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertGreater(self.camera._camera_bridge.port, 0)
            self.assertEqual(self.camera._camera_bridge._api_key, "SECRET")

        def test_the_signature_ignores_unrelated_poll_churn(self):
            first = MonitorCamera.camera_signature([{"uid": "u", "name": "Cam"}])
            second = MonitorCamera.camera_signature([{"uid": "u", "name": "Cam", "extra": 1}])
            self.assertEqual(first, second)
            self.assertNotEqual(first, MonitorCamera.camera_signature([{"uid": "u", "name": "Other"}]))

        def test_a_new_listing_publishes_the_model_then_restores_the_selection(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/webcam?action=stream"})
            self.camera.observe()
            self.assertEqual(self.camera.values["webcamNames"], ["Front"])
            self.assertEqual(self.camera.values["activeWebcamIndex"], -1)
            self.assertTrue(self.camera._restore_pending)
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.values["activeWebcamIndex"], 0)
            self.assertEqual(self.camera.url, "http://printer.local:7125/webcam?action=stream")
            self.assertEqual(self.camera.values["cameraName"], "Front")

        def test_a_second_poll_waits_for_the_pending_restore(self):
            self._snapshot({"uid": "u1", "name": "Front"})
            self.camera.observe()
            self.assertTrue(self.camera._restore_pending)
            # The same listing on the next poll must not restore the
            # selection either: QML has not consumed the model yet.
            self.camera.observe()
            self.assertEqual(self.camera.values["activeWebcamIndex"], -1)
            self.assertTrue(self.camera._restore_pending)

        def test_a_pending_restore_is_dropped_when_the_scheduled_listing_is_stale(self):
            self._snapshot({"uid": "u1", "name": "Front"})
            self.camera.observe()
            self.camera._restore_after_population(("stale",))
            self.assertTrue(self.camera._restore_pending)
            self.camera._restore_pending = False
            self.camera._restore_after_population(("stale",))

        def test_an_empty_listing_clears_the_stream(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/cam"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self._snapshot()
            self.camera.observe()
            self.assertEqual(self.camera.values["webcamNames"], [])
            self.assertEqual(self.camera.values["activeWebcamIndex"], -1)
            self.assertEqual(self.camera.url, "")

        def test_an_unchanged_selection_key_republishes_nothing(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/cam"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            emitted = []
            self.camera.changed.connect(lambda: emitted.append(1))
            self.camera.observe()
            self.assertEqual(emitted, [])

        def test_a_remembered_alias_survives_the_upgrade_to_uid_selection(self):
            # An older config stored the friendly name; the restore
            # accepts it rather than falling back to the first camera.
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/one"},
                           {"uid": "u2", "name": "Back", "stream_url": "/two"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.config = replace_config(self.config, camera_selected="Back")
            self.camera.observe()
            self.assertEqual(self.camera.values["activeWebcamIndex"], 1)
            self.assertTrue(self.camera.url.endswith("/two"))

        def test_a_hostile_stream_url_is_refused(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "//evil.example/x"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.url, "")
            self._snapshot({"uid": "u2", "name": "Front", "stream_url": "javascript:alert(1)"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.url, "")

        def test_the_diagnostics_kill_switch_publishes_no_stream_url(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/cam"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertTrue(self.camera.url)
            self.config = replace_config(self.config, camera_disabled=True)
            self.camera.observe()
            self.assertEqual(self.camera.url, "")

        def test_an_inactive_monitor_never_resolves_a_stream(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/cam"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.data.active = False
            self.config = replace_config(self.config, camera_rotation=45)
            self.camera.observe()
            self.assertEqual(self.camera.url, "")
            self.assertEqual(self.camera.values["cameraRotation"], 0)
            self.assertEqual(self.camera.values["cameraName"], "Front")

        def test_an_unnamed_camera_gets_the_configured_label(self):
            self._snapshot({"uid": "u1", "stream_url": "/cam"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.values["webcamNames"], ["Camera 1"])
            self.assertEqual(self.camera.values["cameraName"], "Configured camera")

        def test_a_relative_stream_url_joins_the_configured_printer(self):
            self.assertEqual(self.camera._remote_stream("http://printer.local/cam"), True)
            self.assertFalse(self.camera._remote_stream("http://127.0.0.1:8080/cam"))
            self.assertEqual(self.camera._bridge_url(self.config, ""), "")
            # A loopback or relative stream needs no bridge.
            self.assertEqual(self.camera._bridge_url(self.config, "/cam"), "/cam")

        def test_a_key_carrying_remote_stream_rides_the_loopback_bridge(self):
            self.config = replace_config(self.config, api_key="SECRET")
            url = self.camera._bridge_url(self.config, "http://printer.local:7125/webcam?action=stream")
            self.assertIsNotNone(self.camera._camera_bridge)
            self.assertTrue(url.startswith("http://127.0.0.1:"), url)
            with patch.object(CameraBridge, "configure", return_value=False):
                self.assertEqual(self.camera._bridge_url(self.config, "http://printer.local/cam"),
                                 "http://printer.local/cam")

        def test_select_publishes_the_choice_and_ignores_out_of_range(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/one"},
                           {"uid": "u2", "name": "Back", "stream_url": "/two"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.camera.select(5)
            self.assertEqual(self.applied, [])
            self.camera.select(1)
            self.assertEqual(self.applied[-1].camera_selected, "u2")
            self.config = self.applied[-1]
            self.camera.observe()
            self.assertEqual(self.camera.values["activeWebcamIndex"], 1)

        def test_an_unknown_rotation_falls_back_to_upright(self):
            self._snapshot({"uid": "u1", "name": "Front", "stream_url": "/cam", "rotation": 45})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.values["cameraRotation"], 0)
            self._snapshot({"uid": "u2", "name": "Front", "stream_url": "/cam", "rotation": "nonsense"})
            self.camera.observe()
            self.camera._restore_after_population(self.camera._camera_signature)
            self.assertEqual(self.camera.values["cameraRotation"], 0)

    class _CameraData(QObject):
        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.active = True
            self._webcams = ()
            self.snapshot = SimpleNamespace(webcams=self._webcams)

        def set_webcams(self, webcams):
            self._webcams = webcams
            self.snapshot = SimpleNamespace(webcams=webcams)

    def replace_config(config, **changes):
        from dataclasses import replace as _dataclass_replace
        return _dataclass_replace(config, **changes)

    class MonitorCommandsCoverageTests(unittest.TestCase):
        """The command lane's display channels and the emergency-stop
        hold. The transport's own timeout/flush behaviour is the
        session's; this asserts the lane over it."""

        def setUp(self):
            self.data = _CommandsData()
            self.commands = MonitorCommands(self.data)
            self.addCleanup(self.commands.reset)

        def test_the_display_channels_layer_receipt_lifecycle_status(self):
            self.data.snapshot.core = {"print_stats": {"state": "printing"}}
            self.assertEqual(self.commands.state, "printing")
            self.assertTrue(self.commands.print_active)
            self.assertFalse(self.commands.setup_allowed)
            self.data.snapshot.core = {"print_stats": {"state": "standby"}}
            self.assertTrue(self.commands.setup_allowed)
            self.data.active = False
            self.assertEqual(self.commands.state, "")
            self.data.active = True
            self.data.connected = False
            self.assertEqual(self.commands.state, "")

        def test_a_dispatch_tracks_expected_states_and_a_live_verdict(self):
            self.commands.send("Pause", "printer/print/pause")
            self.assertEqual(self.commands.status, "Pause requested…")
            self.assertTrue(self.commands.busy)
            self.assertEqual(self.data.tracked[-1][:2], ("Pause", {"paused"}))
            self.assertTrue(self.data.active_busy)

        def test_a_server_refusal_reads_the_servers_own_words(self):
            self.commands.send("Extrude", "printer/gcode/script")
            self.data.requests[-1].callback({"message": "bad"}, "Extrude below minimum temp")
            self.assertEqual(self.commands.status, "Extrude refused: Extrude below minimum temp")
            self.assertFalse(self.commands.busy)

        def test_a_connection_error_is_reported_as_an_unknown_outcome(self):
            self.commands.send("Home", "printer/gcode/script")
            self.data.requests[-1].callback(None, "Moonraker is unavailable")
            self.assertEqual(self.commands.status, "Home outcome unknown: Moonraker is unavailable")

        def test_an_unacknowledged_one_shot_gets_a_transient_receipt(self):
            self.commands.send("Home", "printer/gcode/script")
            self.data.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.commands.status, "Home sent")
            self.assertFalse(self.commands.busy)
            # The receipt ages out to "—", never back to a stale claim.
            self.commands._expire_receipt()
            self.assertEqual(self.commands.status, "")
            self.commands._expire_receipt()  # nothing showing: no-op

        def test_a_tracked_confirmation_supersedes_the_receipt(self):
            self.commands.send("Pause", "printer/print/pause")
            self.data.requests[-1].callback({"result": {}}, None)
            self.commands._command_changed({"name": "Pause", "outcome": "paused",
                                            "detail": "paused", "terminal": True})
            self.assertEqual(self.commands.status, "Pause: paused")
            self.assertFalse(self.commands.busy)
            self.assertEqual(self.commands._tracked, "")

        def test_a_stale_reply_after_the_emergency_stop_is_dropped(self):
            self.commands.send("Pause", "printer/print/pause")
            callback = self.data.requests[-1].callback
            self.commands._fire_emergency()
            callback({"result": {}}, None)
            self.assertEqual(self.commands.status, "")
            self.assertEqual(self.commands._tracked, "")

        def test_an_unavailable_lane_refuses_a_dispatch(self):
            self.data.active = False
            self.assertFalse(self.commands.send("Home", "printer/gcode/script"))
            self.data.active = True
            self.data.request_result = False
            self.assertFalse(self.commands.send("Home", "printer/gcode/script"))
            self.assertEqual(self.commands.status, "Home outcome unknown: Moonraker is unavailable")

        def test_one_shots_queue_behind_the_in_flight_command(self):
            self.commands.send("Home", "printer/gcode/script")
            self.assertTrue(self.commands.request("Mesh", "printer/gcode/script"))
            self.assertEqual(self.commands.status, "Mesh queued")
            self.assertEqual(len(self.commands._queue), 1)
            # The completing one-shot releases the lane, and the queue
            # keeps its place: the entry dispatches ahead of any listener
            # that reacts to the release.
            self.data.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.commands._queue, [])
            self.assertEqual(self.commands.status, "Mesh requested…")

        def test_the_queue_is_bounded(self):
            self.commands.send("Pause", "printer/print/pause")
            for index in range(MonitorCommands.MAX_QUEUED_COMMANDS):
                self.assertTrue(self.commands.request(f"Macro {index}", "printer/gcode/script"))
            self.assertFalse(self.commands.request("One more", "printer/gcode/script"))

        def test_a_queued_one_shot_is_revalidated_before_it_runs(self):
            # The click-time predicate passed; a print started by another
            # client before the queue drained must still stop the entry.
            self.commands.send("Home", "printer/gcode/script")
            self.data.observation = observation(state="printing")
            self.commands.request("Macro X", "printer/gcode/script", rule=can_macro)
            self.data.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.commands.status, f"Macro X cancelled: {R_PRINTING}")
            self.assertEqual(self.data.requests.count_for("Macro X"), [])

        def test_a_queued_one_shot_denied_without_an_observation_fails_closed(self):
            self.commands.send("Home", "printer/gcode/script")
            del self.data.observation
            self.commands.request("Macro X", "printer/gcode/script", rule=can_macro)
            self.data.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.commands.status, f"Macro X cancelled: {R_UNKNOWN}")

        def test_a_fully_allowed_queue_entry_still_dispatches(self):
            self.commands.send("Home", "printer/gcode/script")
            self.data.observation = observation(state="standby")
            self.commands.request("Macro X", "printer/gcode/script", rule=can_macro)
            self.data.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.commands.status, "Macro X requested…")

        def test_a_script_and_a_quick_send_ride_their_own_lanes(self):
            # The one-shot lane posts under the shared control label and
            # carries its identity in the live text; the slider's quick
            # send rides its own channel so it never occupies that lane.
            self.commands.script("Macro X", "TEST")
            self.assertEqual(self.data.requests[-1].label, "control")
            self.assertEqual(self.data.requests[-1].path, "printer/gcode/script")
            self.assertEqual(self.data.requests[-1].options["body"], {"script": "TEST"})
            self.assertEqual(self.data.requests[-1].options["timeout_ms"], 30000)
            self.assertEqual(self.commands.status, "Macro X requested…")
            self.commands.quick("slider", "M220 S50", lambda payload, error: None)
            self.assertEqual(self.data.requests[-1].label, "quick-slider")
            self.assertEqual(self.data.requests[-1].options["body"], {"script": "M220 S50"})
            self.assertTrue(self.data.requests[-1].options["replace"])

        def test_the_report_status_line_is_the_watchdogs_verdict(self):
            self.commands.report_status("Print start never happened")
            self.assertEqual(self.commands.status, "Print start never happened")

        def test_the_emergency_stop_arms_then_requires_a_held_press(self):
            self.data.active = False
            self.commands.emergency_click()
            self.assertEqual(self.commands.clicks, 0)
            self.data.active = True
            self.commands.emergency_click()
            self.commands.emergency_click()
            self.assertEqual(self.commands.clicks, 2)
            self.commands.emergency_click()  # a third click is not a further arm
            self.assertEqual(self.commands.clicks, 2)
            # The hold needs the armed clicks.
            self.commands.emergency_hold_started()
            self.assertAlmostEqual(self.commands.hold_progress, 0.0)
            self.assertTrue(self.commands._hold_timer.isActive())
            self.commands._hold_tick()
            self.assertGreaterEqual(self.commands.hold_progress, 0.0)
            # Releasing early cancels the hold and keeps the arm briefly.
            self.commands.emergency_hold_released()
            self.assertFalse(self.commands._hold_timer.isActive())
            self.assertEqual(self.commands.hold_progress, 0.0)

        def test_a_hold_before_the_arm_is_refused(self):
            self.commands.emergency_hold_started()
            self.assertFalse(self.commands._hold_timer.isActive())
            self.commands.emergency_hold_released()
            self.commands.emergency_click()
            self.commands.emergency_hold_started()
            self.assertFalse(self.commands._hold_timer.isActive())

        def test_the_release_of_a_fired_hold_is_not_a_new_click(self):
            self.commands.emergency_click()
            self.commands.emergency_click()
            with patch.object(QTimer, "singleShot", lambda *args: None):
                self.commands._fire_hold()
            # The stop resets the arm and the progress bar; the release
            # that follows it is suppressed, not counted as a new click.
            self.assertEqual(self.commands.clicks, 0)
            self.assertEqual(self.commands.hold_progress, 0.0)
            self.commands.emergency_click()
            self.assertEqual(self.commands.clicks, 0)

        def test_the_stop_clears_every_pending_lane(self):
            self.commands.send("Pause", "printer/print/pause")
            self.commands.request("Home", "printer/gcode/script")
            emitted = []
            self.commands.emergencyStopped.connect(lambda: emitted.append(1))
            with patch.object(QTimer, "singleShot", lambda *args: None):
                self.commands._fire_hold()
            self.assertEqual(self.commands._queue, [])
            self.assertFalse(self.commands.busy)
            self.assertTrue(self.data.assumed_stopped)
            self.assertTrue(emitted)
            self.assertEqual(self.commands.status, "")

        def test_the_stop_result_reports_the_transport_verdict(self):
            with patch.object(QTimer, "singleShot", lambda *args: None):
                self.commands._fire_hold()
            self.data.requests[-1].callback({"result": {}}, None)
            self.assertEqual(self.commands.status, "Emergency stop issued")
            with patch.object(QTimer, "singleShot", lambda *args: None):
                self.commands._fire_emergency()
            self.data.requests[-1].callback(None, "offline")
            self.assertEqual(self.commands.status, "Emergency stop failed: offline")

        def test_the_click_reset_rearms_the_sequence(self):
            self.commands.emergency_click()
            self.assertEqual(self.commands.clicks, 1)
            self.commands._reset_clicks()
            self.assertEqual(self.commands.clicks, 0)
            self.assertEqual(self.commands.hold_progress, 0.0)

        def test_reset_clears_the_lane_and_pushes_idleness(self):
            self.commands.send("Pause", "printer/print/pause")
            self.commands.reset()
            self.assertEqual(self.commands.status, "")
            self.assertFalse(self.commands.busy)
            self.assertFalse(self.data.active_busy)

    class MonitorCommandsDataDouble(SimpleNamespace):
        """Marker: the double's surface is the real MonitorData's."""

    class _TrackedRequest(SimpleNamespace):
        pass

    class _Requests(list):
        def count_for(self, label):
            return [request for request in self if request.label == label]

    class _CommandsData(QObject):
        invalidated = pyqtSignal()
        commandChanged = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.active = True
            self.connected = True
            self.snapshot = SimpleNamespace(core={"print_stats": {"state": "standby"}})
            self.observation = observation(state="standby")
            self.requests = _Requests()
            self.tracked = []
            self.fails = []
            self.accepts = []
            self.later_calls = []
            self.refreshes = 0
            self.force_refreshes = 0
            self.active_busy = False
            self.assumed_stopped = False
            self.request_result = True

        def set_commands_busy(self, busy):
            self.active_busy = busy

        def track_command(self, name, expected_states=(), *, timeout_s=10.0):
            self.tracked.append((name, set(expected_states), timeout_s))

        def accept_command(self, name):
            self.accepts.append(name)

        def fail_command(self, name, detail):
            self.fails.append((name, detail))

        def later(self, delay_ms, callback):
            self.later_calls.append((delay_ms, callback))

        def refresh_all(self):
            self.refreshes += 1

        def force_refresh(self):
            self.force_refreshes += 1

        def assume_print_stopped(self):
            self.assumed_stopped = True

        def reconnect_after_emergency(self):
            self.reconnected = True

        def request(self, label, method, path, callback, **options):
            request = _TrackedRequest(label=label, method=method, path=path, callback=callback,
                                      options=options)
            self.requests.append(request)
            return self.request_result

    class _PauseRequest(SimpleNamespace):
        pass

    class _PauseTransport:
        def __init__(self):
            self.requests = []
            self.cancelled = []
            self.started = True

        def send_json(self, owner, channel, method, path, callback, **kwargs):
            self.requests.append(_PauseRequest(owner=owner, channel=channel, method=method, path=path,
                                               callback=callback, options=kwargs))
            return self.started

        def cancel_owner(self, owner):
            self.cancelled.append((owner, None))

    class _PauseClient(QObject):
        commandChanged = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.transport = _PauseTransport()
            self.status = {"print_stats": {"state": "printing"}}
            self.tracked = []
            self.accepted = []
            self.failed = []
            self.guards = []

        def track_command(self, name, expected_states=(), *, timeout_s=10.0):
            self.tracked.append((name, set(expected_states), timeout_s))

        def accept_command(self, name):
            self.accepted.append(name)

        def fail_command(self, name, detail):
            self.failed.append((name, detail))

        def set_pause_guard(self, active):
            self.guards.append(active)

    class _TuningData(QObject):
        invalidated = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.active = True
            self.refreshes = 0
            self.later_calls = []

        def refresh_all(self):
            self.refreshes += 1

        def later(self, delay_ms, callback):
            self.later_calls.append((delay_ms, callback))


class UiStateStoreCoverageTests(unittest.TestCase):
    """The boundary guard and the two write paths. Qt is needed only
    for the UM.Logger import; the store itself is pure.

    set_sections carried a round-trip guard that was unreachable by
    construction (every value is coerced to bool, so the payload always
    round-trips) — the dead branch was removed from the module, so the
    refusal path is exercised for real through set_section_layout,
    whose caller normalises rather than coerces.
    """

    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    def test_the_sections_map_writes_through_the_facade_when_it_exists(self):
        calls = []

        class _Facade:
            def merge_state_global(self, update):
                calls.append(update)
                return True

        self.assertTrue(UiStateStore(_Facade()).set_sections({"toolhead": 1, "console": None}))
        self.assertEqual(calls[0], {"sections": {"toolhead": True, "console": False}})

    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    def test_a_plain_store_double_falls_back_to_its_own_write(self):
        calls = []

        class _Double:
            def write(self, update):
                calls.append(update)
                return True

        self.assertTrue(UiStateStore(_Double()).set_sections({}))
        self.assertTrue(UiStateStore(_Double()).set_section_layout({"order": ["a"]}))
        self.assertEqual(calls, [{"sections": {}}, {"sectionLayout": {"order": ["a"]}}])

    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    def test_a_payload_that_cannot_round_trip_fails_here(self):
        class _Double:
            def __init__(self):
                self.writes = 0

            def write(self, update):
                self.writes += 1
                return True

        store = _Double()
        self.assertFalse(UiStateStore(store).set_section_layout({"bad": object()}))
        self.assertEqual(store.writes, 0)


class SettingsPageMigrationMirrorTests(unittest.TestCase):
    """The settings page reads its migration surface off the ACTION (the
    live find: the page's bindings pointed at the wrong manager and a
    broken binding left the banner visible with dead buttons)."""

    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def _action(self, follower):
        from PyQt6.QtCore import QObject

        class _MachineActionBase(QObject):
            def __init__(self, key, label):
                super().__init__()
                self._key = key
                self._label = label

        application = SimpleNamespace(
            getContainerRegistry=lambda: SimpleNamespace(
                containerAdded=SimpleNamespace(connect=lambda _fn: None)),
        )
        with patch.dict(sys.modules, {
                "cura.MachineAction": SimpleNamespace(MachineAction=_MachineActionBase),
                "UM.Settings": SimpleNamespace(DefinitionContainer=SimpleNamespace(
                    DefinitionContainer=type("DefinitionContainer", (), {}))),
                "UM.Settings.DefinitionContainer": SimpleNamespace(
                    DefinitionContainer=type("DefinitionContainer", (), {})),
            }):
            from plugins.MoonrakerFollowerMachineAction import MoonrakerFollowerMachineAction
            action = MoonrakerFollowerMachineAction(application, follower)
        self.addCleanup(action.deleteLater)
        return action

    class _Facade:
        def __init__(self, record=None):
            self.record = dict(record or {})
            self.writes = []

        def migration_record(self):
            return dict(self.record)

        def set_migration_record(self, update):
            self.record.update(update)
            self.writes.append(dict(update))

    class _Follower:
        def __init__(self, persistence):
            self.persistence = persistence

        def current_printer_config(self):
            return None

        def current_printer_identity(self):
            return ("A", "Printer A")

    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    def test_the_action_mirrors_the_record_states(self):
        facade = self._Facade()
        action = self._action(self._Follower(facade))

        # No record: nothing shows.
        self.assertFalse(action.migrationBannerVisible)
        self.assertEqual(action.migrationBannerText, "")
        self.assertFalse(action.migrationBackupAvailable)
        self.assertFalse(action.migrationDiagnosticsVisible)

        # A failed record with a backup: the banner and the backup
        # button, the diagnostics row after dismissal.
        facade.record = {"status": "failed", "backupWritten": True, "backupName": "cura.cfg.stamp"}
        self.assertTrue(action.migrationBannerVisible)
        self.assertTrue(action.migrationBackupAvailable)
        self.assertFalse(action.migrationDiagnosticsVisible)
        self.assertIn("cura.cfg.stamp", action.migrationBannerText)
        action.dismissMigrationBanner()
        self.assertEqual(facade.writes, [{"bannerDismissed": True}])
        self.assertFalse(action.migrationBannerVisible)
        self.assertTrue(action.migrationDiagnosticsVisible)
        # A backup existed: the diagnostics row carries flavour A's
        # rollback recipe (flavour B is the no-backup case).
        self.assertIn("cura.cfg.stamp", action.migrationDiagnosticsText)

    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    def test_the_backup_folder_opens_cura_s_config_folder(self):
        from PyQt6.QtGui import QDesktopServices

        opened = []

        def fake_open(url):
            opened.append(url.toLocalFile())

        action = self._action(self._Follower(None))
        with patch.object(QDesktopServices, "openUrl", staticmethod(fake_open)):
            action.openMigrationBackupFolder()
        from UM.Resources import Resources
        self.assertEqual(opened, [Resources.getConfigStoragePath()])


class PluginPackageCoverageTests(unittest.TestCase):
    """The package entry point. The plugin classes are stand-ins: the
    wiring is what register() owns, not the extensions themselves."""

    def test_metadata_is_empty_and_register_wires_the_components(self):
        self.assertEqual(plugins.getMetaData(), {})
        probed = []
        app = SimpleNamespace(name="app")
        follower = SimpleNamespace(_runtime="runtime")
        output = SimpleNamespace(name="output")
        action = SimpleNamespace(name="action")
        fakes = {name: ModuleType(name) for name in (
            "plugins.MoonrakerPrintFollower", "plugins.MoonrakerOutputDevicePlugin",
            "plugins.MoonrakerFollowerMachineAction", "plugins.LeakProbe")}
        fakes["plugins.MoonrakerPrintFollower"].MoonrakerPrintFollower = lambda app: follower
        fakes["plugins.MoonrakerOutputDevicePlugin"].MoonrakerOutputDevicePlugin = (
            lambda app, owner: output)
        fakes["plugins.MoonrakerFollowerMachineAction"].MoonrakerFollowerMachineAction = (
            lambda app, owner, output_plugin: action)
        fakes["plugins.LeakProbe"].start_leak_probe = (
            lambda runtime, application: probed.append((runtime, application)))
        with patch.dict(sys.modules, fakes):
            wired = plugins.register(app)
        self.assertEqual(wired, {"extension": follower, "output_device": output,
                                 "machine_action": action})
        # The leak probe watches the follower's own runtime.
        self.assertEqual(probed, [("runtime", app)])


if __name__ == "__main__":
    unittest.main()
