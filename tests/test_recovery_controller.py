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
        self.addCleanup(self.controller.close)

    def ready(self):
        self.assertTrue(self.controller.plan())
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] == "planning" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "ready", self.controller.state)

    def wait_terminal(self):
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] in {"verifying_source", "checking_remote_file"} and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)

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
        self.assertEqual(self.transport.scripts[-1], "CLEAR_PAUSE\nM24")
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
        self.assertIn("G1 Z17", self.transport.scripts[3])
        self.assertEqual(self.transport.scripts[-1], "M24")
        self.assertNotIn("PRINT_START", "\n".join(self.transport.scripts))

    def test_missing_or_changed_selection_refuses_before_handover(self):
        original = self.context
        self.context = None
        self.assertFalse(self.controller.plan())
        self.assertEqual(self.controller.state["phase"], "refused")
        self.context = replace(original, path="missing.gcode")
        self.assertTrue(self.controller.plan())
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] == "planning" and time.monotonic() < deadline:
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
