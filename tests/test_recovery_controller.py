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
        self.context = replace(self.context, unloaded=True,
                               observed_offset=self.context.boundary.next_motion_offset)
        printer = self.transport.printer
        printer.file_size = self.context.job[1]
        printer.state["print_stats"].update(state="standby", filename="")
        printer.state["virtual_sdcard"].update(file_size=0, file_position=0,
                                                is_active=False)
        printer.state["pause_resume"]["is_paused"] = False
        self.ready()
        self.assertTrue(self.controller.start({"printedTopZ": 12}))
        deadline = time.monotonic() + 5
        while self.controller.state["phase"] != "complete" and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.controller.state["phase"], "complete", self.controller.state)
        self.assertEqual(self.transport.scripts[0], "M23 part.gcode")
        self.assertIn("M109 S210", self.transport.scripts[1])
        self.assertIn("G1 Z17", self.transport.scripts[2])
        self.assertEqual(self.transport.scripts[-1], "M24")
        self.assertNotIn("PRINT_START", "\n".join(self.transport.scripts))


if __name__ == "__main__":
    unittest.main()
