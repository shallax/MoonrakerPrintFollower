"""Sequential handover and ambiguous-result behavior without a printer."""
import copy
from dataclasses import replace
import re
import unittest

from mpf.printing.RecoveryExecution import RecoveryExecution
from tests.test_recovery_handover import source, status


class FakePrinter:
    def __init__(self):
        self.state = status()
        self.file_size = 1000
        self.scripts = []
        self.query_count = 0
        self.fail_script = None

    def query(self, callback):
        self.query_count += 1
        callback(copy.deepcopy(self.state), None)
        return True

    def send(self, script, callback):
        self.scripts.append(script)
        if self.fail_script and self.fail_script in script:
            callback("connection lost")
            return True
        if script == "PAUSE":
            self.state["print_stats"]["state"] = "paused"
            self.state["virtual_sdcard"]["is_active"] = False
            self.state["pause_resume"]["is_paused"] = True
        if script.startswith("M23 "):
            self.state["print_stats"]["filename"] = script[4:]
            self.state["print_stats"]["state"] = "standby"
            self.state["virtual_sdcard"]["file_size"] = self.file_size
            self.state["virtual_sdcard"]["file_position"] = 0
        if "M109 S" in script:
            self.state["extruder"]["target"] = 210
            self.state["extruder"]["temperature"] = 210
            self.state["heater_bed"]["target"] = 60
            self.state["heater_bed"]["temperature"] = 60
        if "G1 X40 Y50" in script:
            self.state["gcode_move"]["gcode_position"] = [40, 50, 2, 0]
            self.state["gcode_move"]["position"] = [40, 50, 2]
            self.state["toolhead"]["position"] = [40, 50, 2]
        if "G1 X10 Y15" in script:
            self.state["gcode_move"]["gcode_position"] = [10, 15, 3, 0]
            self.state["gcode_move"]["position"] = [10, 15, 3]
            self.state["toolhead"]["position"] = [10, 15, 3]
        if script.startswith("M26 S"):
            self.state["virtual_sdcard"]["file_position"] = int(script[5:])
        if "G92 E15.25" in script:
            self.state["gcode_move"]["gcode_position"][3] = 15.25
            self.state["gcode_move"]["absolute_coordinates"] = True
            self.state["gcode_move"]["absolute_extrude"] = True
            self.state["gcode_move"]["speed_factor"] = float(re.search(r"M220 S([0-9.]+)", script)[1]) / 100
            self.state["gcode_move"]["extrude_factor"] = float(re.search(r"M221 S([0-9.]+)", script)[1]) / 100
        if "M24" in script:
            self.state["print_stats"]["state"] = "printing"
            self.state["pause_resume"]["is_paused"] = False
            self.state["virtual_sdcard"]["is_active"] = True
        callback(None)
        return True


class RecoveryExecutionTests(unittest.TestCase):
    def test_handover_seeks_only_after_verified_return_and_resumes_last(self):
        printer = FakePrinter()
        events = []
        execution = RecoveryExecution(printer.query, printer.send, events.append)
        self.assertTrue(execution.start(source=source(), filename="part.gcode", printed_top_z=8))
        self.assertEqual(execution.phase, "complete")
        self.assertFalse(execution.active)
        self.assertEqual([item.phase for item in events if item.terminal], ["complete"])
        self.assertEqual(len(printer.scripts), 6)
        self.assertIn("M190 S60", printer.scripts[0])
        self.assertIn("M109 S210", printer.scripts[0])
        self.assertTrue(printer.scripts[1].startswith("RESTORE_GCODE_STATE"))
        self.assertIn("G1 X40 Y50", printer.scripts[2])
        self.assertEqual(printer.scripts[3], "M26 S300")
        self.assertIn("G92 E15.25", printer.scripts[4])
        self.assertEqual(printer.scripts[5], "CLEAR_PAUSE\nM24")

    def test_ambiguous_return_does_not_seek_or_retry(self):
        printer = FakePrinter()
        printer.fail_script = "G1 X40 Y50"
        events = []
        execution = RecoveryExecution(printer.query, printer.send, events.append)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "uncertain")
        self.assertEqual(len(printer.scripts), 3)
        self.assertNotIn("M26", "\n".join(printer.scripts))

    def test_stale_or_unhomed_state_refuses_before_mutation(self):
        printer = FakePrinter()
        printer.state["toolhead"]["homed_axes"] = "xy"
        events = []
        execution = RecoveryExecution(printer.query, printer.send, events.append)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "refused")
        self.assertEqual(printer.scripts, [])

    def test_wrong_accumulated_e_blocks_resume_after_seek(self):
        printer = FakePrinter()
        original = printer.send
        def wrong_e(script, callback):
            if "G92 E15.25" in script:
                def changed(error):
                    printer.state["gcode_move"]["gcode_position"][3] = 2.0
                    callback(error)
                return original(script, changed)
            return original(script, callback)
        printer.send = wrong_e
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "refused")
        self.assertNotIn("M24", "\n".join(printer.scripts))

    def test_active_print_is_paused_before_heating_and_rewind(self):
        printer = FakePrinter()
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "complete")
        self.assertEqual(printer.scripts[0], "PAUSE")
        self.assertEqual(printer.scripts[1], "M400")
        self.assertIn("M109 S210", printer.scripts[2])

    def test_prime_waits_for_heat_then_returns_from_checked_coordinate(self):
        printer = FakePrinter()
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                        prime_enabled=True, prime_xyz=(10, 15, 3),
                        prime_amount_mm=20, prime_speed_mm_s=2)
        self.assertEqual(execution.phase, "complete")
        self.assertIn("M109 S210", printer.scripts[0])
        self.assertIn("G1 E20 F120", printer.scripts[2])
        self.assertIn("G1 X40 Y50", printer.scripts[3])
        self.assertEqual(printer.scripts[4], "M26 S300")
        self.assertNotIn("G1 E20", printer.scripts[3])

    def test_unloaded_file_is_selected_without_running_start_macro(self):
        printer = FakePrinter()
        printer.state["print_stats"].update(state="standby", filename="")
        printer.state["virtual_sdcard"].update(file_size=0, file_position=0)
        printer.state["pause_resume"]["is_paused"] = False
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "complete")
        self.assertEqual(printer.scripts[0], "M23 part.gcode")
        self.assertEqual(printer.scripts[-1], "M24")
        self.assertLess(printer.scripts.index("M26 S300"), len(printer.scripts) - 1)
        self.assertNotIn("PRINT_START", "\n".join(printer.scripts))

    def test_unloaded_file_refuses_overridden_m23_before_selection(self):
        printer = FakePrinter()
        printer.state["print_stats"].update(state="standby", filename="")
        printer.state["pause_resume"]["is_paused"] = False
        printer.state["configfile"]["config"]["gcode_macro M23"] = {"gcode": "M24"}
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "refused")
        self.assertEqual(printer.scripts, [])

    def test_command_failures_stop_before_resume_without_retry(self):
        # A Moonraker script response can be ambiguous after the printer has
        # already acted. Every stage must stop instead of sending the next one.
        for phase, fragment in (("heating_and_waiting", "M109 S"),
                                ("restore_state", "RESTORE_GCODE_STATE"),
                                ("priming_nozzle", "G1 E20"),
                                ("returning", "G1 X40 Y50"),
                                ("seeking", "M26 S"),
                                ("restoring_modal_state", "G92 E15.25"),
                                ("resuming", "M24")):
            with self.subTest(phase=phase):
                printer = FakePrinter()
                def changed(outcome, phase=phase, printer=printer, fragment=fragment):
                    if outcome.phase == phase:
                        printer.fail_script = fragment
                execution = RecoveryExecution(printer.query, printer.send, changed)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                                prime_enabled=True, prime_xyz=(10, 15, 3))
                self.assertEqual(execution.phase, "uncertain")
                self.assertEqual(sum(fragment in script for script in printer.scripts), 1)
                if phase != "resuming":
                    self.assertNotIn("M24", "\n".join(printer.scripts))

    def test_each_fresh_telemetry_gate_refuses_a_changed_printer(self):
        for phase, mutate in (
            ("checking_prime", lambda s: s["gcode_move"].update(gcode_position=[11, 15, 3, 0])),
            ("checking_return", lambda s: s["gcode_move"].update(gcode_position=[41, 50, 2, 0])),
            ("checking_seek", lambda s: s["virtual_sdcard"].update(file_position=299)),
            ("checking_modal_state", lambda s: s["gcode_move"].update(absolute_extrude=False)),
        ):
            with self.subTest(phase=phase):
                printer = FakePrinter()
                def changed(outcome, phase=phase, printer=printer, mutate=mutate):
                    if outcome.phase == phase:
                        mutate(printer.state)
                execution = RecoveryExecution(printer.query, printer.send, changed)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                                prime_enabled=True, prime_xyz=(10, 15, 3))
                self.assertEqual(execution.phase, "refused")
                self.assertNotIn("M24", "\n".join(printer.scripts))

    def test_active_pause_failure_and_missing_confirmation_stop_before_heating(self):
        for fault in ("command", "state"):
            with self.subTest(fault=fault):
                printer = FakePrinter()
                printer.state["print_stats"]["state"] = "printing"
                printer.state["virtual_sdcard"]["is_active"] = True
                printer.state["pause_resume"]["is_paused"] = False
                if fault == "command":
                    printer.fail_script = "PAUSE"
                else:
                    original = printer.send
                    def unconfirmed(script, callback, original=original, printer=printer):
                        if script == "PAUSE":
                            def changed(error, printer=printer):
                                printer.state["pause_resume"]["is_paused"] = False
                                callback(error)
                            return original(script, changed)
                        return original(script, callback)
                    printer.send = unconfirmed
                execution = RecoveryExecution(printer.query, printer.send,
                                              lambda _outcome: None)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8)
                self.assertIn(execution.phase, {"uncertain", "refused"})
                self.assertNotIn("M109", "\n".join(printer.scripts))

    def test_final_pause_offset_is_rescanned_before_heating(self):
        printer = FakePrinter()
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        reviewed = source()
        refreshed = replace(reviewed, printed_top_z=12)
        rescans = []
        def refresh(fresh, callback):
            rescans.append(fresh["virtual_sdcard"]["file_position"])
            callback(refreshed, 12, None)
            return True
        execution = RecoveryExecution(printer.query, printer.send,
                                      lambda _outcome: None, refresh)
        execution.start(source=reviewed, filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "complete")
        self.assertEqual(rescans, [600])
        self.assertIn("G1 Z17", "\n".join(printer.scripts))

    def test_changed_source_after_pause_refuses_handover(self):
        for mutation in (lambda s: replace(s, boundary_offset=s.boundary_offset + 1),
                         lambda s: replace(s, sha256="b" * 64)):
            with self.subTest(mutation=mutation):
                printer = FakePrinter()
                printer.state["print_stats"]["state"] = "printing"
                printer.state["virtual_sdcard"]["is_active"] = True
                printer.state["pause_resume"]["is_paused"] = False
                def refresh(_fresh, callback, mutation=mutation):
                    callback(mutation(source()), 8, None)
                    return True
                execution = RecoveryExecution(printer.query, printer.send,
                                              lambda _outcome: None, refresh)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8)
                self.assertEqual(execution.phase, "refused")
                self.assertNotIn("M109", "\n".join(printer.scripts))

    def test_late_replan_callback_does_not_overwrite_transport_uncertainty(self):
        printer = FakePrinter()
        printer.state["print_stats"]["state"] = "printing"
        printer.state["virtual_sdcard"]["is_active"] = True
        printer.state["pause_resume"]["is_paused"] = False
        saved = []
        def refresh(_fresh, callback):
            saved.append(callback)
            return True
        execution = RecoveryExecution(printer.query, printer.send,
                                      lambda _outcome: None, refresh)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "rechecking_source")
        execution.abort_uncertain("Disconnected")
        saved[0](source(), 8, None)
        self.assertEqual(execution.phase, "uncertain")
        self.assertNotIn("M109", "\n".join(printer.scripts))

    def test_lost_fresh_status_at_each_stage_never_dispatches_resume(self):
        for phase in ("checking", "checking_temperature", "checking_restored_state",
                      "checking_prime", "checking_return", "checking_seek",
                      "checking_modal_state", "checking_resume"):
            with self.subTest(phase=phase):
                printer = FakePrinter()
                holder = [None]
                def query(callback, holder=holder, phase=phase, printer=printer):
                    if holder[0].phase == phase:
                        callback(None, "connection lost")
                        return True
                    return printer.query(callback)
                execution = holder[0] = RecoveryExecution(query, printer.send,
                                                          lambda _outcome: None)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                                prime_enabled=True, prime_xyz=(10, 15, 3))
                self.assertEqual(execution.phase, "uncertain")
                if phase != "checking_resume":
                    self.assertNotIn("M24", "\n".join(printer.scripts))

    def test_transport_refusal_and_cancel_stop_before_next_command(self):
        printer = FakePrinter()
        execution = RecoveryExecution(lambda _callback: False, printer.send,
                                      lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "uncertain")
        self.assertEqual(printer.scripts, [])

        execution = RecoveryExecution(printer.query, lambda _script, _callback: False,
                                      lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "uncertain")
        self.assertEqual(printer.scripts, [])

        pending = []
        execution = RecoveryExecution(lambda callback: pending.append(callback) or True,
                                      printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertFalse(execution.start(source=source(), filename="part.gcode",
                                         printed_top_z=8))
        execution.cancel()
        pending[0](copy.deepcopy(printer.state), None)
        self.assertEqual(execution.phase, "cancelled")
        self.assertEqual(printer.scripts, [])

    def test_rejects_print_owner_and_unloaded_filename_before_mutation(self):
        for mode, filename, paused in (("printing", "other.gcode", False),
                                       ("standby", "folder/part.gcode", False),
                                       ("standby", "part.gcode", True)):
            with self.subTest(mode=mode, filename=filename, paused=paused):
                printer = FakePrinter()
                printer.state["print_stats"]["state"] = mode
                printer.state["pause_resume"]["is_paused"] = paused
                printer.state["virtual_sdcard"]["is_active"] = mode == "printing"
                execution = RecoveryExecution(printer.query, printer.send,
                                              lambda _outcome: None)
                execution.start(source=source(), filename=filename, printed_top_z=8)
                self.assertEqual(execution.phase, "refused")
                self.assertEqual(printer.scripts, [])

    def test_pause_settle_and_file_load_failures_stop_before_heat(self):
        for active, failed in ((True, "M400"), (False, "M23")):
            with self.subTest(active=active):
                printer = FakePrinter()
                printer.fail_script = failed
                printer.state["print_stats"]["state"] = "printing" if active else "standby"
                printer.state["pause_resume"]["is_paused"] = False
                printer.state["virtual_sdcard"]["is_active"] = active
                execution = RecoveryExecution(printer.query, printer.send,
                                              lambda _outcome: None)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8)
                self.assertEqual(execution.phase, "uncertain")
                self.assertNotIn("M109", "\n".join(printer.scripts))

    def test_active_replan_failure_stops_before_heat(self):
        for outcome in ("dispatch", "scan"):
            with self.subTest(outcome=outcome):
                printer = FakePrinter()
                printer.state["print_stats"]["state"] = "printing"
                printer.state["virtual_sdcard"]["is_active"] = True
                printer.state["pause_resume"]["is_paused"] = False
                def refresh(_status, callback, outcome=outcome):
                    if outcome == "scan":
                        callback(None, None, "invalid source")
                        return True
                    return False
                execution = RecoveryExecution(printer.query, printer.send,
                                              lambda _outcome: None, refresh)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8)
                self.assertIn(execution.phase, {"refused", "uncertain"})
                self.assertNotIn("M109", "\n".join(printer.scripts))

    def test_custom_pause_cleanup_is_verified_before_starting_sd_worker(self):
        printer = FakePrinter()
        config = printer.state["configfile"]["config"]
        config["gcode_macro PAUSE"] = {
            "gcode": "SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
                     "SET_FILAMENT_SENSOR SENSOR=inlet ENABLE=0\n"
                     "SET_IDLE_TIMEOUT TIMEOUT=9999"}
        config["idle_timeout"] = {"timeout": 600}
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                        saved_state="PAUSE")
        self.assertEqual(execution.phase, "complete")
        self.assertIn("SET_FILAMENT_SENSOR SENSOR=inlet ENABLE=1", printer.scripts[-2])
        self.assertIn("SET_IDLE_TIMEOUT TIMEOUT=600", printer.scripts[-2])
        self.assertEqual(printer.scripts[-1], "CLEAR_PAUSE\nM24")

    def test_cancel_before_dispatch_does_not_send_a_script(self):
        printer = FakePrinter()
        execution = None
        def changed(outcome):
            if outcome.phase == "heating_and_waiting":
                execution.cancel()
        execution = RecoveryExecution(printer.query, printer.send, changed)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "cancelled")
        self.assertEqual(printer.scripts, [])
        queries = []
        execution = None
        def query(callback):
            queries.append(callback)
            return True
        def cancel_check(outcome):
            if outcome.phase == "checking":
                execution.cancel()
        execution = RecoveryExecution(query, printer.send, cancel_check)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "cancelled")
        self.assertEqual(queries, [])

    def test_heating_refuses_targets_outside_the_nozzle_limit(self):
        printer = FakePrinter()
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                        hotend_target=300)
        self.assertEqual(execution.phase, "refused")
        self.assertEqual(printer.scripts, [])

    def test_temperature_and_final_resume_are_confirmed_from_fresh_status(self):
        for phase in ("checking_temperature", "checking_resume"):
            with self.subTest(phase=phase):
                printer = FakePrinter()
                def changed(outcome, phase=phase, printer=printer):
                    if outcome.phase == phase:
                        if phase == "checking_temperature":
                            printer.state["extruder"]["temperature"] = 100
                        else:
                            printer.state["print_stats"]["state"] = "paused"
                execution = RecoveryExecution(printer.query, printer.send, changed)
                execution.start(source=source(), filename="part.gcode", printed_top_z=8)
                self.assertIn(execution.phase, {"refused", "uncertain"})

    def test_cleanup_command_error_stops_before_resuming(self):
        printer = FakePrinter()
        config = printer.state["configfile"]["config"]
        config["gcode_macro PAUSE"] = {
            "gcode": "SAVE_GCODE_STATE NAME=PAUSE\nBASE_PAUSE\n"
                     "SET_FILAMENT_SENSOR SENSOR=inlet ENABLE=0"}
        printer.fail_script = "SET_FILAMENT_SENSOR SENSOR=inlet ENABLE=1"
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=source(), filename="part.gcode", printed_top_z=8,
                        saved_state="PAUSE")
        self.assertEqual(execution.phase, "uncertain")
        self.assertNotIn("M24", "\n".join(printer.scripts))

    def test_unconfirmed_acceleration_blocks_the_sd_worker(self):
        printer = FakePrinter()
        printer.state["toolhead"]["max_accel"] = 600
        planned = source()
        planned = replace(planned, state=replace(planned.state, acceleration=700))
        execution = RecoveryExecution(printer.query, printer.send, lambda _outcome: None)
        execution.start(source=planned, filename="part.gcode", printed_top_z=8)
        self.assertEqual(execution.phase, "refused")
        self.assertNotIn("M24", "\n".join(printer.scripts))


if __name__ == "__main__":
    unittest.main()
