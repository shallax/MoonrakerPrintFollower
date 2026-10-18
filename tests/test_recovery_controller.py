# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-18 — 6.0.0: Drain cancelled planning workers before deleting test source files.
# 2026-10-18 — 6.0.0: Cover recovery modes, telemetry validation and restart lifecycle regressions.
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-13 [368df57] Wait for unloaded recovery test before cleanup on Windows
# 2026-10-13 [54a31e5] Implement interrupted print recovery handover

"""Asynchronous source planning and guarded transport handover."""
import copy
import os
import tempfile
import time
import unittest
from dataclasses import replace

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from mpf.gcode.GCodeIndex import build_index_from_file
from mpf.gcode.IndexView import IndexView
from mpf.moonraker.MoonrakerProtocol import RemoteFileIdentity
from mpf.application.RecoveryController import RecoveryContext, RecoveryController
from tests.test_recovery_execution import FakePrinter


class FakeTransport:
    def __init__(self, printer, identity):
        self.printer = printer
        self.identity = identity
        self.scripts = []
        self.script_timeouts = []
        self.remote_changed = False

    def send_json(self, _owner, _channel, _method, path, callback, *, body=None, **_kwargs):
        if path.startswith("server/files/metadata"):
            modified = self.identity.modified + (1 if self.remote_changed else 0)
            callback({"result": {"filename": self.identity.filename,
                                  "size": self.identity.size, "modified": modified}}, None)
        elif path == "printer/objects/query":
            callback({"result": {"status": copy.deepcopy(self.printer.state)}}, None)
        elif path == "printer/gcode/script":
            script = body["script"]
            self.scripts.append(script)
            self.script_timeouts.append(_kwargs.get("timeout_ms"))
            self.printer.send(script, lambda error: callback({}, error))
        else:
            raise AssertionError(path)
        return True


class FakeClient(QObject):
    sessionInvalidated = pyqtSignal()

    def __init__(self, transport):
        super().__init__()
        self.transport = transport


class RecoveryControllerTests(unittest.TestCase):
    def test_malformed_profile_positions_are_refused_without_motion(self):
        for unloaded in (False, True):
            for field in ("position", "gcode_position"):
                for position in (None, [], [1], "123", [1, 2, float("nan")], [1, 2, True], [1, 2, "bad"]):
                    with self.subTest(unloaded=unloaded, field=field, position=position):
                        self.context = replace(self.context, unloaded=unloaded)
                        self.transport.printer.state["gcode_move"].update(position=[1, 2, 3], gcode_position=[1, 2, 3, 0])
                        self.transport.printer.state["gcode_move"][field] = position
                        self.assertTrue(self.controller.plan())
                        self.wait_for(lambda: self.controller.state["phase"] == "refused")
                        self.assertFalse(self.controller.state["ready"])
                        self.assertEqual(self.transport.scripts, [])

    def test_unloaded_preview_retains_file_xy_and_uses_physical_z(self):
        self.context = replace(self.context, unloaded=True)
        self.transport.printer.state["gcode_move"].update(position=[100, 100, 10], gcode_position=[90, 80, 8, 0])
        self.ready()
        state = self.controller.state
        self.assertEqual((state["currentX"], state["currentY"], state["currentZ"]), (90, 80, 10))
        self.assertEqual(state["bedBounds"], [-10, -20, 0, 240, 230])

    def setUp(self):
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        data = (b"G90\nM82\nG92 E0\nM104 S210\nM140 S60\n"
                b";LAYER:0\nG1 X40 Y50 Z2 E15.25 F1800\nG1 X41 E16\n")
        path = os.path.join(self.directory.name, "part.gcode")
        with open(path, "wb") as handle:
            handle.write(data)
        view = IndexView(("part.gcode", len(data), 1), build_index_from_file(path))
        boundary = view.recovery_boundary(0, 1)
        identity = RemoteFileIdentity("part.gcode", len(data), 123.0)
        self.context = RecoveryContext(("part.gcode", len(data), 1), "part.gcode",
            path, identity, boundary, 8.0, "PAUSE_STATE", "", 5.0, 50.0, 5.0, 120)
        printer = FakePrinter()
        printer.state["virtual_sdcard"].update(file_size=len(data), file_position=len(data))
        self.transport = FakeTransport(printer, identity)
        self.client = FakeClient(self.transport)
        self.records = []
        self.controller = RecoveryController(self.client, lambda: self.context,
            record=lambda context, phase, detail: self.records.append((context, phase, detail)))
        # Production close invalidates callbacks without blocking the UI.
        # Test teardown must then join readers before deleting their source;
        # Windows cannot unlink a G-code file while a cancelled plan scans it.
        self.addCleanup(self.controller._executor.shutdown, wait=True, cancel_futures=True)
        self.addCleanup(self.controller.close)

    def test_preview_bed_bounds_follow_printer_limits_and_coordinate_offsets(self):
        self.transport.printer.state["toolhead"].update(axis_minimum=[-5, -10, 0], axis_maximum=[250, 240, 300])
        self.transport.printer.state["gcode_move"].update(position=[100, 100, 10], gcode_position=[90, 80, 8, 0])
        self.ready()
        self.assertEqual(self.controller.state["bedBounds"], [-15, -30, -2, 240, 220])
        self.assertEqual(self.controller.state["maximumZ"], 298)

    def test_prime_dwell_reaches_transport_with_sufficient_timeout(self):
        self.context = replace(self.context, prime_enabled=True, prime_xyz=(10, 15, 3),
                               prime_amount_mm=20, prime_speed_mm_s=2, prime_dwell_s=300)
        self.ready()
        self.assertEqual(self.controller.state["primeDwell"], 300)
        self.assertEqual(self.controller.state["clearance"], 5)
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        index = next(i for i, script in enumerate(self.transport.scripts) if "G4 P300000" in script)
        self.assertGreaterEqual(self.transport.script_timeouts[index], 430000)

    def ready(self):
        self.assertTrue(self.controller.plan())
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] in {"planning", "checking_printer"} and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "ready", self.controller.state)

    def wait_terminal(self):
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] in {"verifying_source", "checking_remote_file"} and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.assertTrue(predicate(), self.controller.state)

    def test_profile_reply_after_cancellation_cannot_restore_readiness(self):
        pending = []
        self.transport.send_json = lambda *args, **kwargs: pending.append(args[4]) or True
        self.assertTrue(self.controller.plan())
        self.wait_for(lambda pending=pending: bool(pending))
        self.controller.cancel()
        pending.pop()({"result": {"status": self.transport.printer.state}}, None)
        self.assertEqual(self.controller.state["phase"], "cancelled")
        self.assertFalse(self.controller.start())
        self.assertEqual(self.transport.scripts, [])

    def test_profile_dispatch_and_reply_failures_are_read_only(self):
        for dispatch, error in ((False, None), (True, "offline")):
            with self.subTest(dispatch=dispatch, error=error):
                def fail(*args, dispatch=dispatch, error=error, **kwargs):
                    if dispatch:
                        args[4]({}, error)
                    return dispatch
                self.transport.send_json = fail
                self.assertTrue(self.controller.plan())
                self.wait_for(lambda: self.controller.state["phase"] == "refused")
                self.assertEqual(self.transport.scripts, [])
                self.assertFalse(self.controller.start())

    def test_missing_preview_limits_are_not_invented_and_motion_is_refused(self):
        del self.transport.printer.state["toolhead"]["axis_maximum"]
        self.ready()
        self.assertIsNone(self.controller.state["maximumZ"])
        self.assertEqual(self.controller.state["bedBounds"], [])
        self.assertTrue(self.controller.start())
        self.wait_for(lambda: self.controller.state["phase"] in {"refused", "uncertain"})
        self.assertFalse(any("G1 " in script for script in self.transport.scripts))

    def test_cancel_during_a_script_blocks_next_stage_and_replanning(self):
        self.ready()
        original = self.transport.send_json
        pending = []
        def hold(*args, **kwargs):
            if args[3] == "printer/gcode/script":
                pending.append(args[4])
                return True
            return original(*args, **kwargs)
        self.transport.send_json = hold
        self.assertTrue(self.controller.start())
        self.wait_for(lambda pending=pending: bool(pending))
        self.assertFalse(self.controller.plan())
        self.controller.cancel()
        self.assertEqual(self.controller.state["phase"], "cancelling")
        pending.pop()({}, None)
        self.assertFalse(self.controller.state["active"])
        self.assertEqual(self.transport.scripts, [])

    def test_session_change_discards_pending_metadata_and_script_replies(self):
        for held_path in ("server/files/metadata", "printer/gcode/script"):
            with self.subTest(path=held_path):
                self.transport.send_json = FakeTransport.send_json.__get__(self.transport)
                self.ready()
                original = self.transport.send_json
                pending = []
                def hold(*args, held_path=held_path, pending=pending, original=original, **kwargs):
                    if args[3].startswith(held_path):
                        pending.append(args[4])
                        return True
                    return original(*args, **kwargs)
                self.transport.send_json = hold
                self.assertTrue(self.controller.start())
                self.wait_for(lambda pending=pending: bool(pending))
                self.client.sessionInvalidated.emit()
                phase = self.controller.state["phase"]
                pending.pop()({}, None)
                self.assertEqual(self.controller.state["phase"], phase)
                self.assertFalse(self.controller.state["active"])
                self.assertEqual(self.transport.scripts, [])

    def test_closed_controller_ignores_verification_and_session_notifications(self):
        self.ready()
        generation = self.controller._generation
        self.controller.close()
        self.controller._verification_done(generation, True)
        self.controller.invalidate()
        self.assertFalse(self.controller.plan())
        self.assertFalse(self.controller.start())
        self.assertEqual(self.transport.scripts, [])

    def test_custom_pause_profile_is_reviewed_and_uses_pre_park_save(self):
        config = self.transport.printer.state["configfile"]["config"]
        config["gcode_macro PAUSE"] = {"gcode": "SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
            "G1 X200\nSAVE_GCODE_STATE NAME=PAUSEPARK\n"
            "SET_FILAMENT_SENSOR SENSOR=upper ENABLE=0\nSET_IDLE_TIMEOUT TIMEOUT=43200"}
        config["gcode_macro RESUME"] = {"gcode": "BASE_RESUME"}
        config["idle_timeout"] = {"timeout": "600"}
        self.ready()
        self.assertEqual(self.controller.state["pauseState"], "PAUSE")
        self.assertEqual(self.transport.scripts, [])
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)
        self.assertIn("RESTORE_GCODE_STATE NAME=PAUSE MOVE=0", self.transport.scripts)
        self.assertFalse(any("PAUSEPARK" in script for script in self.transport.scripts))

    def test_ambiguous_pause_profile_refuses_during_read_only_preparation(self):
        self.transport.printer.state["configfile"]["config"]["gcode_macro PAUSE"] = {
            "gcode": "SAVE_GCODE_STATE NAME=ONE\nSAVE_GCODE_STATE NAME=TWO\nBASE_PAUSE"}
        self.assertTrue(self.controller.plan())
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] in {"planning", "checking_printer"} and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "refused")
        self.assertIn("pre-park PAUSE state", self.controller.state["detail"])
        self.assertEqual(self.transport.scripts, [])

    def test_only_expected_new_run_is_accepted_during_unloaded_resume(self):
        for phase, filename, size_delta, position_delta, accepted in (
            ("resuming", "part.gcode", 0, 0, True),
            ("checking_resume", "part.gcode", 0, 1, True),
            ("returning", "part.gcode", 0, 0, False),
            ("resuming", "other.gcode", 0, 0, False),
            ("resuming", "part.gcode", 1, 0, False),
            ("resuming", "part.gcode", 0, -1, False),
        ):
            with self.subTest(phase=phase, filename=filename, size_delta=size_delta, position_delta=position_delta):
                self.context = replace(self.context, unloaded=True)
                self.ready()
                self.controller._phase = phase
                generation = self.controller._generation
                self.controller.job_changed(
                    {"state": "printing", "filename": filename},
                    {"file_size": self.context.identity.size + size_delta,
                     "file_position": self.context.boundary.next_motion_offset + position_delta,
                     "is_active": True})
                self.assertEqual(self.controller._generation == generation, accepted)

    def test_read_only_plan_and_confirmed_restart_accept_a_selected_point_ahead(self):
        self.context = replace(self.context, observed_offset=1)
        self.transport.printer.state["virtual_sdcard"]["file_position"] = 1
        self.ready()
        self.assertTrue(self.controller.state["skipsForward"])
        self.assertEqual(self.controller.state["resumeLayer"], self.context.boundary.next_layer + 1)
        self.assertEqual(self.controller.state["resumeMove"], self.context.boundary.next_motion + 1)
        self.assertEqual(self.transport.scripts, [])
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)
        self.assertIn(f"M26 S{self.context.boundary.next_motion_offset}", self.transport.scripts)

    def test_resume_description_uses_next_layer_when_selection_finishes_a_layer(self):
        self.ready()
        context = self.controller._planned_context
        self.controller._planned_context = replace(context,
            boundary=replace(context.boundary, selected_layer=2, completed_motions=879,
                             next_layer=3, next_motion=0))
        self.assertEqual(self.controller.state["resumeLayer"], 4)
        self.assertEqual(self.controller.state["resumeMove"], 1)

    def test_remote_mutation_refuses_before_printer_commands(self):
        self.ready()
        self.transport.remote_changed = True
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "refused")
        self.assertEqual(self.transport.scripts, [])

    def test_guarded_handover_reaches_complete(self):
        self.ready()
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)
        self.assertTrue(self.transport.scripts[-1].endswith("CLEAR_PAUSE\nM24"))
        self.assertEqual(self.records[-1][1], "complete")

    def test_active_print_is_paused_and_source_rechecked_before_heating(self):
        self.ready()
        printer = self.transport.printer
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        self.assertTrue(self.controller.start())
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] != "complete" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)
        self.assertEqual(self.transport.scripts[:2], ["PAUSE", "M400"])
        self.assertIn("M109 S210", self.transport.scripts[2])

    def test_unloaded_recovery_selects_file_then_heats_and_skips_print_start(self):
        self.context = replace(self.context, unloaded=True, file_printed_top_z=12.0,
                               observed_offset=self.context.boundary.next_motion_offset)
        printer = self.transport.printer
        printer.file_size = self.context.job[1]
        printer.state["print_stats"].update(state="standby", filename="")
        printer.state["virtual_sdcard"].update(file_size=0, file_position=0,
                                                is_active=False)
        printer.state["pause_resume"]["is_paused"] = False
        self.ready()
        self.assertEqual(self.controller.state["suggestedTopZ"], 12.0)
        self.assertTrue(self.controller.start({"zReference": 2}))
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] != "complete" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)
        self.assertEqual(self.transport.scripts[0], "M23 part.gcode")
        self.assertIn("M109 S210", self.transport.scripts[1])
        self.assertEqual(self.transport.scripts[2], "G92 Z12")
        self.assertIn("G1 Z7", self.transport.scripts[3])
        self.assertTrue(self.transport.scripts[-1].endswith("M24"))
        self.assertNotIn("PRINT_START", "\n".join(self.transport.scripts))

    def test_missing_or_changed_selection_refuses_before_handover(self):
        original = self.context
        self.context = None
        self.assertFalse(self.controller.plan())
        self.assertEqual(self.controller.state["phase"], "refused")
        self.context = replace(original, path="missing.gcode")
        self.assertTrue(self.controller.plan())
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] in {"planning", "checking_printer"} and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "refused")
        self.assertFalse(self.controller.start())

    def test_settings_change_and_local_file_change_refuse_before_printer_commands(self):
        self.ready()
        original = self.context
        self.context = None
        self.assertFalse(self.controller.start())
        self.assertEqual(self.controller.state["phase"], "refused")
        self.context = original
        self.ready()
        self.context = replace(original, clearance_mm=10)
        self.assertFalse(self.controller.start())
        self.assertEqual(self.controller.state["phase"], "refused")
        self.context = original
        self.ready()
        with open(original.path, "ab") as handle:
            handle.write(b";changed\n")
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "refused")
        self.assertEqual(self.transport.scripts, [])

    def test_missing_heater_targets_require_manual_inputs(self):
        self.ready()
        old = self.controller._source
        self.controller._source = replace(old, state=replace(old.state,
            hotend_target=None, bed_target=None))
        self.assertFalse(self.controller.start({}))
        self.assertIn("nozzle and bed targets", self.controller.state["detail"])
        # Replanning restores a ready state after a refused manual entry.
        self.ready()
        old = self.controller._source
        self.controller._source = replace(old, state=replace(old.state,
            hotend_target=None, bed_target=None))
        self.assertTrue(self.controller.start({"hotend": 210, "bed": 60}))
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "complete")
        self.assertIn("M109 S210", "\n".join(self.transport.scripts))

    def test_unloaded_print_uses_indexed_height_without_manual_entry(self):
        self.context = replace(self.context, unloaded=True, file_printed_top_z=12.0)
        printer = self.transport.printer
        printer.file_size = self.context.job[1]
        printer.state["print_stats"].update(state="standby", filename="")
        printer.state["virtual_sdcard"].update(file_size=0, file_position=0,
                                                is_active=False)
        printer.state["pause_resume"]["is_paused"] = False
        self.ready()
        self.assertEqual(self.controller.state["suggestedTopZ"], 12.0)
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)

    def test_z_reference_must_not_put_restart_below_homed_surface(self):
        self.context = replace(self.context, unloaded=True, file_printed_top_z=12.0)
        self.ready()
        self.assertFalse(self.controller.start({"zReference": 3}))
        self.assertIn("Z reference", self.controller.state["detail"])
        self.assertEqual(self.transport.scripts, [])

    def test_lost_session_invalidates_planning_and_execution(self):
        self.ready()
        pending = []
        original = self.transport.send_json
        def hold(_owner, _channel, _method, path, callback, **kwargs):
            if path == "printer/objects/query":
                pending.append(callback)
                return True
            return original(_owner, _channel, _method, path, callback, **kwargs)
        self.transport.send_json = hold
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertTrue(pending)
        self.client.sessionInvalidated.emit()
        self.assertEqual(self.controller.state["phase"], "uncertain")
        pending[0]({"result": {"status": copy.deepcopy(self.transport.printer.state)}}, None)
        self.assertEqual(self.transport.scripts, [])

    def test_cancel_and_session_change_discard_inflight_planning(self):
        self.assertTrue(self.controller.plan())
        self.controller.cancel()
        self.assertEqual(self.controller.state["phase"], "cancelled")
        self.app.processEvents()
        self.assertEqual(self.controller.state["phase"], "cancelled")
        self.assertTrue(self.controller.plan())
        self.client.sessionInvalidated.emit()
        self.assertEqual(self.controller.state["phase"], "refused")

    def test_missing_local_source_and_failed_metadata_dispatch_refuse(self):
        self.ready()
        os.unlink(self.context.path)
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "refused")
        self.assertEqual(self.transport.scripts, [])
        # Recreate the file and fail the read-only identity request.
        data = (b"G90\nM82\nG92 E0\nM104 S210\nM140 S60\n"
                b";LAYER:0\nG1 X40 Y50 Z2 E15.25 F1800\nG1 X41 E16\n")
        with open(self.context.path, "wb") as handle:
            handle.write(data)
        self.ready()
        original = self.transport.send_json
        def fail_metadata(owner, channel, method, path, callback, **kwargs):
            if path.startswith("server/files/metadata"):
                return False
            return original(owner, channel, method, path, callback, **kwargs)
        self.transport.send_json = fail_metadata
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "refused")

    def test_invalid_manual_target_and_changed_context_refuse(self):
        self.ready()
        old = self.controller._source
        self.controller._source = replace(old, state=replace(old.state, bed_target=None))
        self.assertFalse(self.controller.start({"bed": -1}))
        self.ready()
        self.assertTrue(self.controller.start())
        self.context = replace(self.context, clearance_mm=9)
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "refused")
        self.assertEqual(self.transport.scripts, [])

    def test_missing_final_paused_offset_refuses_before_heat(self):
        self.ready()
        printer = self.transport.printer
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        original = printer.send
        def changed_offset(script, callback):
            if script == "M400":
                def changed(error):
                    printer.state["virtual_sdcard"]["file_position"] = None
                    callback(error)
                return original(script, changed)
            return original(script, callback)
        printer.send = changed_offset
        self.assertTrue(self.controller.start())
        self.wait_for(lambda: self.controller.state["phase"] == "uncertain")
        self.assertNotIn("M109", "\n".join(self.transport.scripts))

    def test_source_becoming_unparseable_during_pause_refuses_before_heat(self):
        self.ready()
        printer = self.transport.printer
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        original = printer.send
        def corrupt_after_pause(script, callback):
            if script == "M400":
                def changed(error):
                    with open(self.context.path, "r+b") as handle:
                        data = handle.read()
                        handle.seek(0)
                        handle.write(data.replace(b"G1 X41 E16", b"BADCODE   "))
                    callback(error)
                return original(script, changed)
            return original(script, callback)
        printer.send = corrupt_after_pause
        self.assertTrue(self.controller.start())
        self.wait_for(lambda: self.controller.state["phase"] == "refused")
        self.assertNotIn("M109", "\n".join(self.transport.scripts))

    def test_bad_final_paused_offset_refuses_before_heat(self):
        self.ready()
        printer = self.transport.printer
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        original = printer.send
        def bad_offset(script, callback):
            if script == "M400":
                def changed(error):
                    printer.state["virtual_sdcard"]["file_position"] = -1
                    callback(error)
                return original(script, changed)
            return original(script, callback)
        printer.send = bad_offset
        self.assertTrue(self.controller.start())
        self.wait_terminal()
        self.assertEqual(self.controller.state["phase"], "uncertain")
        self.assertNotIn("M109", "\n".join(self.transport.scripts))


if __name__ == "__main__":
    unittest.main()
