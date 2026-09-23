"""Coverage for MoonrakerMonitorModel's plumbing: the file-manager confirm
and print payloads with their slots, the publish value builders, the
hydration edge branches and the action tail.

Every test drives the production model through the Qt runtime harness
(real PyQt6, the scripted transport, the Cura host doubles) and builds it
the way the integration suite does, so each branch is reached through the
state the model reads in production rather than by hand-stuffing its
internals.

Leftovers, with reasons:
  - 1533: the published window's swap-back. Both thresholds are clamped
    into the same mesh window and the clamp is monotone, so
    setBedMeshThresholds cannot hand the publish an inverted pair; the
    line guards a state nothing constructs.
  - 3404: the navigation demand's hard-key guard. _nav_key_hard returns
    None only for a None key, and _schedule_navigation has already
    returned on that key two lines earlier, so the guard cannot fire.
  - 1890 (openMigrationBackupFolder): the body opens the config folder in
    the desktop's file manager. The container has no such handler, so the
    slot is driven with QDesktopServices.openUrl replaced by a recorder —
    the URL it hands over is asserted, the launch itself is not.
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from plugins.ToolheadPolicy import EXTRUDE_DISTANCE_DEFAULT, EXTRUDE_SPEED_DEFAULT, JOG_DISTANCE_DEFAULT
from qt_runtime_support import QT_AVAILABLE, ScriptedTransport, runtime

# The scratch root the container bind-mounts; anywhere else uses the
# platform default.
SCRATCH = "/tmp/mpf"


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt model suite")
class MonitorModelCase(unittest.TestCase):
    """One model per test on the scripted transport."""

    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=self.transport)
        self.client.configure("http://printer-a", "test-key", 750)
        self.client._poll_timer.stop()
        self.addCleanup(self.client.stop)
        self.applied = []
        self.print_state = self.qt.load("PrintState").PrintSnapshot()
        self.identity = lambda: ("A", "Printer A")
        self.mesh = None
        self.model = None

    # ---- construction ------------------------------------------------

    def printer_config(self, **fields):
        return self.qt.load("PrinterConfig").PrinterConfig(
            url="http://printer-a", api_key="test-key", **fields)

    def make_mesh(self, snapshot=None):
        from PyQt6.QtCore import pyqtSignal

        class Mesh(self.qt.QObject):
            changed = pyqtSignal()

            def __init__(self):
                super().__init__()
                self.snapshot = dict(snapshot or {})
                self.visible = True
                self.thresholds = []

            def set_thresholds(self, low, high):
                self.thresholds.append((low, high))

            def set_visible(self, value):
                self.visible = value
                self.changed.emit()

        return Mesh()

    def build(self, **kwargs):
        self.printer_config_value = kwargs.pop("config", None) or self.printer_config()
        self.mesh = kwargs.pop("mesh", None) or self.make_mesh()
        model = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel(
            None, 1,
            client=self.client,
            print_state=lambda: self.print_state,
            config=lambda: self.printer_config_value,
            apply_config=self.applied.append,
            bed_mesh=self.mesh,
            identity=kwargs.pop("identity", self.identity),
            **kwargs)
        self.addCleanup(model.setMonitoringActive, False)
        return model

    def persistence(self):
        """The production facade over a scratch settings/state pair."""
        root = tempfile.mkdtemp(prefix="mpfxtest-model-", dir=SCRATCH if os.path.isdir(SCRATCH) else None)
        return self.qt.load("PluginPersistence").PluginPersistence(
            settings_path=os.path.join(root, "settings.json"),
            state_global_path=os.path.join(root, "state.json"),
            state_machine_dir=os.path.join(root, "machines"))

    # ---- driving -----------------------------------------------------

    def deliver(self, fragment, payload, error=None):
        """Answer the newest request whose path carries the fragment."""
        for request in reversed(self.transport.requests):
            if fragment in request.path:
                request.callback(payload, error)
                return request
        raise AssertionError(f"no recorded request matching {fragment!r}")

    def channels(self):
        return [request.channel for request in self.transport.requests]

    def sent(self, fragment):
        """The recorded requests whose path carries the fragment — the
        lane the command components post on is uniform, the path is not.
        """
        return [request for request in self.transport.requests if fragment in request.path]

    @staticmethod
    def unwrap(value):
        """QVariant-typed properties come back wrapped; the QML payloads
        are read as Python here."""
        return value.value() if hasattr(value, "value") else value

    def value(self, name):
        return self.unwrap(getattr(self.model, name))

    def model_now(self):
        """One model per test, built on first use so the print state can
        land before the popup opens."""
        if self.model is None:
            self.model = self.build()
        return self.model

    def connect(self, status=None):
        """The HTTP status reply: the client's connect transition plus
        whatever core objects the test needs observed."""
        self.client._handle_http_status(
            {"result": {"status": dict(status or {})}}, None, self.client._generation, time.monotonic())

    def observe(self, *, server=None, auxiliary=None, objects=None):
        """Land the lanes an HTTP status reply does not carry; the lanes
        a call leaves out keep what an earlier one put there."""
        patch = {}
        if server is not None:
            patch["server"] = dict(server)
        if auxiliary is not None:
            patch["auxiliary"] = dict(auxiliary)
        if objects is not None:
            patch["objects"] = tuple(objects)
        self.model._data._update(**patch)

    def ack(self, fragment):
        """Answer the newest request on the fragment's path. The command
        tracker needs the POST's acceptance before a state frame can
        confirm the command and free the lane."""
        request = self.sent(fragment)[-1]
        request.callback({"result": "ok"}, None)
        return request

    def open_files(self, files, *, dirs=(), history=(), disk=None):
        """Open the popup the way the QML does, then answer its walk."""
        self.model.openFileManager()
        self.deliver("path=gcodes&", {"result": {
            "files": list(files),
            "dirs": [{"dirname": name} for name in dirs],
            "disk_usage": dict(disk or {"total": 800, "used": 600, "free": 200}),
        }})
        for name in dirs:
            self.deliver(f"path=gcodes/{name}", {"result": {"files": [], "dirs": []}})
        if history:
            self.deliver("server/history/list", {"result": {"jobs": list(history)}})
        return self.model._file_manager

    @staticmethod
    def file_entry(name, **meta):
        return {"filename": name, "modified": 10.0, "size": 100, **meta}

    @staticmethod
    def thumb(name):
        return [{"relative_path": f".thumbs/{name}-32x32.png", "width": 32, "height": 32}]


class PublishSentinelTests(MonitorModelCase):
    """The typed-coercion helper (the 4.5.0 debt pack): every Optional
    snapshot field must reach its typed property as the sentinel."""

    def test_the_helper_passes_values_and_replaces_none(self):
        self.model = self.build()
        self.assertEqual(self.model._coerce(None, -1.0), -1.0)
        self.assertEqual(self.model._coerce(0.5, -1.0), 0.5)
        self.assertEqual(self.model._coerce(-3, -1), -3)

    def test_none_optional_snapshot_fields_publish_the_sentinels(self):
        # A fresh snapshot carries None for every Optional field: the
        # typed C++ properties must receive the sentinels, never None
        # (the live crash this seam guards).
        self.model = self.build()
        self.model._publish()
        self.assertEqual(self.model.nextPauseLayer, -1)
        self.assertEqual(self.model.nextPauseFraction, -1.0)
        self.assertEqual(self.model.improveEtaProgress, -1.0)


class LocaleAndHydrationTests(MonitorModelCase):
    """The module-level coercions: locale spellings and stored selections."""

    def locale(self, languages):
        class Locale:
            def uiLanguages(self):
                return list(languages)

        return patch.object(self.qt.load("MoonrakerMonitorModel"), "QLocale",
                            SimpleNamespace(system=lambda: Locale()))

    def test_british_spelling_follows_the_commonwealth_locales(self):
        module = self.qt.load("MoonrakerMonitorModel")
        for language, expected in (("en-GB", True), ("en_AU", True),
                                   ("en-US", False), ("en", False), ("de-DE", False)):
            with self.locale([language]):
                self.assertIs(module._british_spelling(), expected, language)

    def test_the_published_spelling_reads_the_locale(self):
        self.model = self.build()
        with self.locale(["en-GB"]):
            self.model._publish()
        self.assertTrue(self.model.britishSpelling)

    def test_a_broken_locale_query_falls_back_to_american_spellings(self):
        # A QLocale that cannot be queried must not take the whole
        # publish down with it.
        module = self.qt.load("MoonrakerMonitorModel")

        class Broken:
            def uiLanguages(self):
                raise RuntimeError("no locale backend")

        with patch.object(module, "QLocale", SimpleNamespace(system=lambda: Broken())):
            self.assertFalse(module._british_spelling())

    def test_toolhead_state_degrades_junk_to_the_policy_defaults(self):
        module = self.qt.load("MoonrakerMonitorModel")
        state = module._toolhead_state({"jogDistance": "wide", "extrudeDistance": None,
                                        "extrudeSpeed": -5})
        self.assertEqual(state, {"jogDistance": JOG_DISTANCE_DEFAULT,
                                 "extrudeDistance": EXTRUDE_DISTANCE_DEFAULT,
                                 "extrudeSpeed": EXTRUDE_SPEED_DEFAULT})
        kept = module._toolhead_state({"jogDistance": 12.5, "extrudeDistance": 3, "extrudeSpeed": 120})
        self.assertEqual(kept, {"jogDistance": 12.5, "extrudeDistance": 3.0, "extrudeSpeed": 120.0})

    def test_machine_geometry_rejects_unparsable_values(self):
        self.model = self.build()
        self.model.setMachineGeometry("wide", 200, False)
        self.model.setMachineGeometry(200, None, False)
        self.assertEqual(self.model.bedMeshMachineWidth, 0.0)
        self.assertEqual(self.model.bedMeshMachineDepth, 0.0)
        self.model.setMachineGeometry("220", 220, True)
        self.assertEqual(self.model.bedMeshMachineWidth, 220.0)
        self.assertTrue(self.model.bedMeshCenterIsZero)

    def test_the_legacy_write_state_name_writes_the_named_file(self):
        # _write_state is the module-level name older callers import; it
        # writes the document to the file it names, merging into it.
        module = self.qt.load("MoonrakerMonitorModel")
        path = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-state-"), "sections.json")
        pathlib.Path(path).write_text(json.dumps({"foreign": 1}), encoding="utf-8")
        with patch.object(module, "_sections_path", lambda: path):
            module._write_state({"sections": {"console": False}})
        document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.assertEqual(document["sections"], {"console": False})
        self.assertEqual(document["foreign"], 1)


class StoreWiringTests(MonitorModelCase):
    """The store slots: the config-only double, and the failure notes."""

    def test_the_config_only_store_persists_the_panel_state(self):
        module = self.qt.load("MoonrakerMonitorModel")
        path = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-store-"), "state.json")
        self.model = self.build(state_store=module.StateStore(path))
        self.model.setControlsCollapsed(True)
        self.model.setJogDistance(42.0)
        document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.assertTrue(document["controlsCollapsed"])
        self.assertEqual(document["toolhead"]["jogDistance"], 42.0)

    def test_a_failed_hydration_read_is_reported_once_the_console_exists(self):
        # The read runs before the console does, so its failure note has
        # to be queued and flushed rather than dropped.
        module = self.qt.load("MoonrakerMonitorModel")
        pathlib.Path(module._sections_path()).write_text("{not json", encoding="utf-8")
        self.model = self.build()
        notes = [entry["text"] for entry in self.value("consoleLines") if entry["kind"] == "note"]
        self.assertTrue(any("could not be read" in text for text in notes), notes)
        self.assertFalse(self.model.controlsCollapsed)

    def test_a_failed_save_reaches_the_console_note_line(self):
        self.model = self.build()
        # A parent path that is a FILE can never hold the document:
        # the save fails and the note reaches the console.
        blocker = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-blocker-"), "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("not a directory")
        self.model._store._path = os.path.join(blocker, "state.json")
        self.model.setControlsCollapsed(True)
        notes = [entry["text"] for entry in self.value("consoleLines") if entry["kind"] == "note"]
        self.assertTrue(any("could not be saved" in text for text in notes), notes)

    def test_the_migration_banner_hides_once_dismissed(self):
        self.model = self.build(persistence=self.persistence())
        self.model._store.set_migration_record({
            "status": "failed", "backupWritten": True, "backupName": "cura-rollback.cfg"})
        # The production landing precedes the ready point; the model's
        # record cache re-reads there (setMonitoringActive is the
        # post-migration hook).
        self.model.setMonitoringActive(True)
        self.model._publish()
        self.assertTrue(self.model.migrationBannerVisible)
        self.assertIn("cura-rollback.cfg", self.model.migrationBannerText)
        self.assertTrue(self.model.migrationBackupAvailable)
        self.model.dismissMigrationBanner()
        self.assertFalse(self.model.migrationBannerVisible)
        self.assertTrue(self.model.migrationDiagnosticsVisible)
        self.assertIn("cura-rollback.cfg", self.model.migrationDiagnosticsText)
        self.assertTrue(self.model._store.migration_record()["bannerDismissed"])

    def test_a_store_without_a_record_lane_ignores_the_dismissal(self):
        self.model = self.build()
        self.model.dismissMigrationBanner()
        self.assertFalse(self.model.migrationBannerVisible)

    def test_the_backup_folder_slot_opens_the_config_directory(self):
        self.model = self.build()
        opened = []
        module = self.qt.load("MoonrakerMonitorModel")
        with patch.object(module, "QDesktopServices",
                          SimpleNamespace(openUrl=opened.append)):
            self.model.openMigrationBackupFolder()
        self.assertEqual(len(opened), 1)
        from UM.Resources import Resources
        self.assertEqual(opened[0].toLocalFile(), Resources.getConfigStoragePath())


class PublishBranchTests(MonitorModelCase):
    """The publish's edge branches and the camera watchdog."""

    def test_a_model_without_a_gui_application_skips_the_wake_watch(self):
        from PyQt6.QtGui import QGuiApplication
        with patch.object(QGuiApplication, "instance", staticmethod(lambda: None)):
            self.model = self.build()
        self.assertIsNone(self.model._camera_app_state)

    def test_the_model_hooks_the_application_wake_state(self):
        # The harness runs a core application, so the hook is driven
        # through the double the model queries for it.
        from PyQt6.QtGui import QGuiApplication
        hooked = []
        app = SimpleNamespace(applicationState=lambda: "inactive",
                              applicationStateChanged=SimpleNamespace(connect=hooked.append))
        with patch.object(QGuiApplication, "instance", staticmethod(lambda: app)):
            self.model = self.build()
        self.assertEqual(self.model._camera_app_state, "inactive")
        self.assertEqual(hooked, [self.model._on_app_state_changed])

    def test_waking_the_application_reloads_the_camera_once(self):
        from PyQt6.QtCore import Qt
        self.model = self.build()
        self.model._camera_app_state = Qt.ApplicationState.ApplicationInactive
        before = self.model.cameraRefreshNonce
        self.model._on_app_state_changed(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(self.model.cameraRefreshNonce, before + 1)
        # A second Active state is not a wake: no further reload.
        self.model._on_app_state_changed(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(self.model.cameraRefreshNonce, before + 1)

    def test_a_deposed_monitor_ignores_the_wake(self):
        # F3 (the 2026-09-19 review): every cached monitor hooks the
        # global wake, but only the ACTIVE one may reload its camera.
        from PyQt6.QtCore import Qt
        self.model = self.build()
        self.model._camera_app_state = Qt.ApplicationState.ApplicationInactive
        self.model.setMonitoringActive(False)
        before = self.model.cameraRefreshNonce
        self.model._on_app_state_changed(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(self.model.cameraRefreshNonce, before,
                         "a deposed monitor must not publish on wake")

    def test_a_recovered_stream_without_a_failure_keeps_the_nonce(self):
        self.model = self.build()
        before = self.model.cameraRefreshNonce
        self.model._on_stream_recovered()
        self.assertFalse(self.model.cameraRecovering)
        self.assertEqual(self.model.cameraRefreshNonce, before)

    def test_the_first_camera_discovery_publishes_one_url_and_one_nonce(self):
        # The QML coalescer's contract: the first discovery changes
        # the URL once and the nonce once — the coalescer then
        # collapses the two signals into ONE stream application (the
        # camera-delay fix's second cause drove two starts).
        from PyQt6.QtTest import QSignalSpy
        self.model = self.build()
        self.model._camera = SimpleNamespace(url="", values={})
        self.model._publish()  # no camera yet
        url_spy = QSignalSpy(self.model.cameraUrlChanged)
        nonce_spy = QSignalSpy(self.model.cameraRefreshChanged)
        self.model._camera.url = "http://cam/stream"
        self.model._publish()
        self.assertEqual(len(url_spy), 1)
        self.assertEqual(len(nonce_spy), 1)
        self.assertGreater(self.model.cameraRefreshNonce, 0)

    def test_a_camera_without_a_url_does_not_break_the_publish(self):
        self.model = self.build()
        self.model._camera = SimpleNamespace(values={})
        before = self.model.cameraRefreshNonce
        self.model._publish()
        self.assertEqual(self.model.cameraRefreshNonce, before)
        self.assertTrue(self.model.monitorState)

    def test_a_query_only_url_transition_does_not_reload(self):
        # The 2026-09-19 live-run ruling: a poll may report the same
        # stream with a rotated nonce in its query — a healthy stream
        # must not be reloaded for it. A genuine path transition
        # still bumps the nonce.
        from PyQt6.QtTest import QSignalSpy
        self.model = self.build()
        self.model._camera = SimpleNamespace(url="", values={})
        self.model._publish()  # no camera yet
        self.model._camera.url = "http://cam/stream?nonce=1"
        self.model._publish()
        before = self.model.cameraRefreshNonce
        nonce_spy = QSignalSpy(self.model.cameraRefreshChanged)
        self.model._camera.url = "http://cam/stream?nonce=2"
        self.model._publish()
        self.assertEqual(len(nonce_spy), 0)
        self.assertEqual(self.model.cameraRefreshNonce, before)
        self.model._camera.url = "http://cam/other"
        self.model._publish()
        self.assertEqual(len(nonce_spy), 1)
        self.assertEqual(self.model.cameraRefreshNonce, before + 1)

    def test_the_all_page_reports_its_own_totals(self):
        self.model = self.build()
        self.open_files([])
        self.model.setFilePageSize("all")
        self.assertEqual(self.model.fileManagerShown, "Showing 0 of 0")
        self.open_files([self.file_entry("part.gcode"), self.file_entry("second.gcode")])
        self.model.setFilePageSize("all")
        self.assertEqual(self.model.fileManagerShown, "Showing 1–2 of 2")

    def test_open_recents_fetch_the_rows_thumbnails(self):
        self.model = self.build()
        with patch.object(self.model._file_manager, "request_thumbnails") as fetch:
            fm = self.open_files([self.file_entry("part.gcode", thumbnails=self.thumb("part"))],
                                 history=[{"filename": "part.gcode", "status": "completed",
                                           "exists": True, "end_time": 5.0}])
            self.model._publish()
        self.assertTrue(fm.recents()[0]["thumb"])
        requested = [row.relpath for call in fetch.call_args_list for row in call.args[0]]
        self.assertIn("part.gcode", requested)

    def test_the_published_threshold_window_follows_a_touched_pair(self):
        mesh = self.make_mesh({"minimum": 0.0, "maximum": 1.0})
        self.model = self.build(mesh=mesh)
        self.model._publish()
        self.assertEqual(self.model.bedMeshThresholdLow, 0.0)
        self.model.setBedMeshThresholds(0.2, 0.8)
        self.assertEqual(self.model.bedMeshThresholdLow, 0.2)
        self.assertEqual(self.model.bedMeshThresholdHigh, 0.8)
        self.assertEqual(mesh.thresholds[-1], (0.2, 0.8))
        # The same pair again: nothing to send, nothing to store.
        self.model.setBedMeshThresholds(0.2, 0.8)
        self.assertEqual(len(mesh.thresholds), 1)
        # The handles dragged past each other: the window reads ordered.
        self.model.setBedMeshThresholds(0.8, 0.2)
        self.assertEqual((self.model.bedMeshThresholdLow, self.model.bedMeshThresholdHigh), (0.2, 0.8))
        self.assertEqual(len(mesh.thresholds), 1)
        # A narrower mesh clamps the stored pair into it on the next publish.
        mesh.snapshot = {"minimum": 0.5, "maximum": 0.6}
        self.model._publish()
        self.assertEqual(self.model.bedMeshThresholdLow, 0.5)
        self.assertEqual(self.model.bedMeshThresholdHigh, 0.6)

    def test_the_threshold_slot_refuses_a_mesh_without_a_range(self):
        self.model = self.build()
        # No mesh observed yet, and a flat one: nothing to clamp into.
        self.model.setBedMeshThresholds(0.2, 0.8)
        self.assertFalse(self.mesh.thresholds)
        self.mesh.snapshot = {"minimum": 1.0, "maximum": 1.0}
        self.model.setBedMeshThresholds(0.2, 0.8)
        self.assertFalse(self.mesh.thresholds)


class PrintConfirmTests(MonitorModelCase):
    """The print dialog's payload, its dispatch gate and its slots."""

    def test_the_print_payload_reports_the_printers_readiness(self):
        self.model = self.build()
        self.open_files([self.file_entry(
            "part.gcode", estimated_time=3600, filament_total=1200,
            thumbnails=self.thumb("part"))])
        self.observe(server={"klippy_state": "ready"}, auxiliary={"toolhead": {"homed_axes": "xyz"}})
        self.model.fileRequestPrint("part.gcode")
        confirm = self.value("filePrintConfirm")
        self.assertEqual(confirm["name"], "part.gcode")
        self.assertEqual(confirm["printerName"], "Printer A")
        self.assertTrue(confirm["ready"])
        self.assertEqual(confirm["readyText"], "Printer ready.")
        self.observe(auxiliary={"toolhead": {"homed_axes": ""}})
        self.model.fileRequestPrint("part.gcode")
        confirm = self.value("filePrintConfirm")
        self.assertFalse(confirm["ready"])
        self.assertEqual(confirm["readyText"], "The printer is not homed — the print may not start.")
        self.observe(server={"klippy_state": "shutdown"})
        self.model.fileRequestPrint("part.gcode")
        self.assertEqual(self.value("filePrintConfirm")["readyText"], "Printer state: Shutdown.")

    def test_the_print_payload_falls_back_to_a_generic_printer_name(self):
        self.model = self.build()
        self.open_files([self.file_entry("part.gcode")])

        def no_identity():
            raise RuntimeError("the machine's identity is not resolved yet")

        self.model._identity = no_identity
        self.model.fileRequestPrint("part.gcode")
        self.assertEqual(self.value("filePrintConfirm")["printerName"], "the printer")

    def test_requesting_a_print_of_a_missing_file_opens_nothing(self):
        self.model = self.build()
        self.open_files([self.file_entry("part.gcode")])
        self.model.fileRequestPrint("ghost.gcode")
        self.assertEqual(self.value("filePrintConfirm"), "")

    def test_confirming_a_print_dispatches_and_closes_the_popup(self):
        self.model = self.build()
        self.open_files([self.file_entry("part.gcode")])
        self.connect()
        self.model.fileRequestPrint("part.gcode")
        self.model.fileConfirmPrint()
        self.assertIn("print:part.gcode", self.channels())
        self.assertEqual(self.value("filePrintConfirm"), "")
        self.assertFalse(self.model.fileManagerOpen)

    def test_confirming_a_print_refuses_when_the_verdict_moved(self):
        self.model = self.build()
        self.open_files([self.file_entry("part.gcode")])
        self.model.fileRequestPrint("part.gcode")
        # Never connected: the dispatch gate re-checks and refuses.
        self.model.fileConfirmPrint()
        self.assertNotIn("print:part.gcode", self.channels())
        self.assertEqual(self.value("filePrintConfirm"), "")
        self.assertIn("Print start refused", self.model.actionStatus)

    def test_cancelling_the_print_dialog_clears_the_payload(self):
        self.model = self.build()
        self.open_files([self.file_entry("part.gcode")])
        self.model.fileRequestPrint("part.gcode")
        self.model.fileCancelPrint()
        self.assertEqual(self.value("filePrintConfirm"), "")

    def test_downloading_a_file_delegates_to_the_follower(self):
        requested = []
        self.model = self.build(request_file_download=requested.append)
        self.model.fileDownload("prints/part.gcode")
        self.assertEqual(requested, ["prints/part.gcode"])
        self.model._request_file_download = None
        self.model.fileDownload("prints/part.gcode")
        self.assertEqual(requested, ["prints/part.gcode"])

    def test_download_progress_publishes_and_cancel_delegates(self):
        progress = [None]
        cancelled = []
        self.model = self.build(request_download_progress=lambda: progress[0],
                                cancel_file_download=lambda: cancelled.append(True))
        self.assertEqual(self.value("fileDownloadProgress"), "")
        progress[0] = {"name": "part.gcode", "percent": 42}
        self.model._publish()
        self.assertEqual(self.value("fileDownloadProgress"), {"name": "part.gcode", "percent": 42})
        self.model.fileDownloadCancel()
        self.assertEqual(cancelled, [True])
        self.model._cancel_file_download = None
        self.model.fileDownloadCancel()
        self.assertEqual(cancelled, [True])

    def test_clearing_the_walk_error_republishes(self):
        self.model = self.build()
        self.model._file_manager._walk_error = "listing failed"
        self.model.fileClearWalkError()
        self.assertEqual(self.model.fileManagerWalkError, "")


class FileMutationTests(MonitorModelCase):
    """The delete, rename, upload and view slots over a real listing."""

    def listing(self, files=None, *, dirs=("prints",), history=()):
        self.model_now()
        return self.open_files(files or [self.file_entry("part.gcode"),
                                         self.file_entry("second.gcode",
                                                         thumbnails=self.thumb("second"))],
                               dirs=dirs, history=history)

    def printing(self, relpath="part.gcode"):
        self.model_now()
        self.connect()
        self.model.updateMoonrakerStatus({"print_stats": {"state": "printing", "filename": relpath}})

    def test_the_bulk_delete_refuses_when_every_selection_is_blocked(self):
        self.printing()
        self.listing()
        self.model.toggleFileSelection("part.gcode")
        self.model.fileRequestDelete()
        self.assertEqual(self.value("fileDeleteConfirm"), "")

    def test_the_bulk_delete_offers_the_rest_and_counts_the_blocked(self):
        self.printing()
        self.listing()
        self.model.toggleFileSelection("part.gcode")
        self.model.toggleFileSelection("second.gcode")
        self.model.fileRequestDelete()
        confirm = self.value("fileDeleteConfirm")
        self.assertEqual(confirm["relpaths"], ["second.gcode"])
        self.assertEqual(confirm["blocked"], 1)

    def test_deleting_one_file_offers_the_confirm(self):
        self.listing()
        self.model.fileRequestDeleteFile("second.gcode")
        self.assertEqual(self.value("fileDeleteConfirm")["first"], "second.gcode")
        self.model.fileConfirmDelete()
        self.assertIn("delete:second.gcode", self.channels())

    def test_deleting_one_file_refuses_the_printing_one_and_ghosts(self):
        self.printing()
        self.listing()
        self.model.fileRequestDeleteFile("part.gcode")
        self.assertEqual(self.value("fileDeleteConfirm"), "")
        self.model.fileRequestDeleteFile("ghost.gcode")
        self.assertEqual(self.value("fileDeleteConfirm"), "")

    def test_creating_a_directory_asks_the_service(self):
        self.listing()
        self.model.fileCreateDirectory("fresh")
        self.assertTrue(any(channel.startswith("mkdir:") for channel in self.channels()))

    def test_deleting_a_directory_requires_a_path(self):
        self.listing()
        self.model.fileRequestDeleteDir("/")
        self.assertEqual(self.value("fileDeleteConfirm"), "")
        self.model.fileRequestDeleteDir("prints/deep/")
        confirm = self.value("fileDeleteConfirm")
        self.assertEqual((confirm["kind"], confirm["path"], confirm["name"]), ("dir", "prints/deep", "deep"))
        self.model.fileConfirmDelete()
        self.assertIn("delete:prints/deep", self.channels())

    def test_cancelling_a_delete_clears_the_confirm(self):
        self.listing()
        self.model.fileRequestDeleteDir("prints")
        self.model.fileCancelDelete()
        self.assertEqual(self.value("fileDeleteConfirm"), "")

    def test_renaming_refuses_the_printing_file_and_ghosts(self):
        self.printing()
        self.listing()
        self.model.fileRequestRename("part.gcode")
        self.assertEqual(self.value("fileRenameTarget"), "")
        self.model.fileRequestRename("ghost.gcode")
        self.assertEqual(self.value("fileRenameTarget"), "")

    def test_renaming_a_file_offers_the_target(self):
        self.listing()
        self.model.fileRequestRename("second.gcode")
        target = self.value("fileRenameTarget")
        self.assertEqual((target["kind"], target["name"]), ("file", "second.gcode"))
        self.model.filePreviewRename("renamed.gcode")
        self.assertFalse(self.model.fileRenameConflict)
        self.model.fileConfirmRename()
        self.assertIn("move:second.gcode", self.channels())
        self.assertEqual(self.value("fileRenameTarget"), "")

    def test_a_typed_name_against_a_resident_file_previews_the_collision(self):
        self.listing()
        self.model.fileRequestRename("second.gcode")
        self.model.filePreviewRename("part.gcode")
        self.assertTrue(self.model.fileRenameConflict)
        self.model.filePreviewRename("part")
        self.assertFalse(self.model.fileRenameConflict)

    def test_a_directory_rename_previews_its_own_collisions(self):
        self.listing(dirs=("prints", "parts"))
        self.model.fileRequestRenameDir("prints")
        self.model.filePreviewRename("parts")
        self.assertTrue(self.model.fileRenameConflict)
        self.model.fileConfirmRename()
        self.assertIn("move:prints", self.channels())

    def test_a_directory_rename_refuses_an_empty_path_and_a_free_name(self):
        self.listing(dirs=("prints",))
        self.model.fileRequestRenameDir("/")
        self.assertEqual(self.value("fileRenameTarget"), "")
        self.model.fileRequestRenameDir("prints")
        self.assertEqual(self.value("fileRenameTarget")["kind"], "dir")
        self.model.filePreviewRename("fresh")
        self.assertFalse(self.model.fileRenameConflict)

    def test_previewing_a_rename_without_a_target_is_a_no_op(self):
        self.listing()
        self.model.filePreviewRename("anything.gcode")
        self.assertEqual(self.value("fileRenameTarget"), "")

    def test_cancelling_a_rename_clears_the_target_and_the_conflict(self):
        self.listing()
        self.model.fileRequestRename("second.gcode")
        self.model.fileCancelRename()
        self.assertEqual(self.value("fileRenameTarget"), "")
        self.assertFalse(self.model.fileRenameConflict)

    def test_navigation_goes_up_and_into_a_folder(self):
        self.listing()
        self.model.fileNavigateTo(["prints"], False)
        self.assertEqual(self.model.fileManagerDirectory, ["prints"])
        self.model.fileNavigateTo(["prints"], True)
        self.assertEqual(self.model.fileManagerDirectory, [])

    def test_the_selection_slots_delegate_to_the_service(self):
        fm = self.listing()
        self.model.toggleFileSelection("second.gcode")
        self.assertEqual(fm.selection, {"second.gcode"})
        self.model.toggleFilePageSelection()
        self.assertEqual(fm.selection, {"part.gcode", "second.gcode"})
        self.model.clearFileSelection()
        self.assertEqual(fm.selection, set())

    def test_history_and_metadata_slots_reach_the_service(self):
        self.listing()
        self.model.fileLoadAllHistory()
        self.assertTrue(any("start=0" in request.path for request in self.transport.requests))
        self.model.fileScanMetadata("prints/part.gcode")
        self.assertIn("metascan:prints/part.gcode", self.channels())

    def test_visible_thumbnails_answer_only_while_the_popup_is_open(self):
        self.model = self.build()
        with patch.object(self.model._file_manager, "request_thumbnails") as fetch:
            self.model.fileRequestVisibleThumbnails(["part.gcode"])
            fetch.assert_not_called()
        self.listing()
        with patch.object(self.model._file_manager, "request_thumbnails") as fetch:
            self.model.fileRequestVisibleThumbnails(["second.gcode", "ghost.gcode"])
        requested = [row.relpath for call in fetch.call_args_list for row in call.args[0]]
        self.assertEqual(requested, ["second.gcode"])

    def test_a_column_change_saves_only_when_the_service_changed(self):
        self.listing()
        saved = []
        with patch.object(self.model, "_save_state", lambda: saved.append(1)):
            self.model.setFileColumnWidth("size", 180)
            self.assertEqual(len(saved), 1)
            self.model.setFileColumnWidth("size", 180)
            self.assertEqual(len(saved), 1)
            order = list(self.value("fileManagerColumnOrder"))
            self.model.setFileColumnOrder(list(reversed(order)))
            self.assertEqual(len(saved), 2)
            self.assertEqual(self.value("fileManagerColumnOrder"), list(reversed(order)))
            self.model.setFileColumnVisible("size", False)
            self.assertEqual(len(saved), 3)
            self.assertIn("size", self.value("fileManagerColumnHidden"))

    def test_upload_progress_publishes_only_on_a_new_percentage(self):
        self.model = self.build()
        self.model._file_upload_progress = {"name": "part.gcode", "percent": 0,
                                            "state": "uploading", "error": ""}
        self.model._publish()
        original = self.model._file_upload_progress
        self.model._file_manager.uploadProgress.emit(0)
        self.assertIs(self.model._file_upload_progress, original)
        self.model._file_manager.uploadProgress.emit(40)
        self.assertIsNot(self.model._file_upload_progress, original)
        self.assertEqual(self.value("fileUploadProgress")["percent"], 40)

    def test_cancelling_an_upload_closes_the_confirmation(self):
        self.model = self.build()
        self.model._file_upload_confirm = {"path": "/tmp/part.gcode", "filename": "part.gcode"}
        self.model.fileCancelUpload()
        self.assertEqual(self.value("fileUploadConfirm"), "")
        self.model.fileUpload("")
        self.assertEqual(self.value("fileUploadConfirm"), "")

    def test_opening_and_refreshing_survive_a_dying_service_slot(self):
        # Qt swallows a slot's traceback, so the wrapper's whole job is
        # to log the failure and republish an empty popup.
        self.model = self.build()
        module = self.qt.load("MoonrakerMonitorModel")
        fm = self.model._file_manager
        with self.assertLogs(module.__name__, level="ERROR") as captured:
            with patch.object(fm, "open", side_effect=RuntimeError("the slot died")):
                self.model.openFileManager()
                self.model.refreshFileManager()
        self.assertEqual(len(captured.records), 2)
        self.assertTrue(self.model.fileManagerOpen)


class ActionTailTests(MonitorModelCase):
    """The pause/resume lanes, the what's-new overlay and the small slots."""

    def printing_lane(self, state="printing", *, paused=False):
        # The real status path, not a direct data poke: the command
        # tracker follows this stream, so a state frame is what clears
        # the lane's busy flag after a dispatch.
        self.connect({"print_stats": {"state": state}, "pause_resume": {"is_paused": paused}})
        self.model._data._update(objects=("print_stats", "pause_resume"))

    def test_pause_and_resume_refuse_without_an_allowed_verdict(self):
        self.model = self.build()
        self.model.pausePrint()
        self.assertEqual(self.model.actionStatus, "Pause refused: Printer state unknown")
        self.model.resumePrint()
        self.assertNotIn("Resume requested", self.model.actionStatus)
        self.assertNotIn("printer/print/pause", self.channels())

    def test_pause_and_resume_dispatch_on_an_allowed_verdict(self):
        self.model = self.build()
        self.printing_lane()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertTrue(self.model.canPausePrint)
        self.model.pausePrint()
        self.assertEqual([request.path for request in self.sent("printer/print/pause")],
                         ["printer/print/pause"])
        self.ack("printer/print/pause")
        self.printing_lane(state="paused", paused=True)
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertTrue(self.model.canResumePrint)
        self.model.resumePrint()
        self.assertEqual([request.path for request in self.sent("printer/print/resume")],
                         ["printer/print/resume"])

    def test_the_strip_button_dispatches_by_state(self):
        self.model = self.build()
        self.printing_lane(state="paused", paused=True)
        self.model.stripPausePrint()
        self.assertTrue(self.sent("printer/print/resume"))
        self.ack("printer/print/resume")
        self.printing_lane()
        self.model.stripPausePrint()
        self.assertTrue(self.sent("printer/print/pause"))
        # One dispatch each: the second press rode the fresh state, it
        # did not repeat the first lane's command.
        self.assertEqual(len(self.sent("printer/print/resume")), 1)

    def test_cancel_only_sends_while_a_print_is_active(self):
        self.model = self.build()
        self.model.cancelPrint()
        self.assertFalse(self.sent("printer/print/cancel"))
        self.printing_lane()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertTrue(self.model.canCancelPrint)
        self.model.cancelPrint()
        self.assertEqual([request.path for request in self.sent("printer/print/cancel")],
                         ["printer/print/cancel"])

    def test_the_whats_new_overlay_opens_and_records_its_marker(self):
        self.model = self.build()
        requested = []
        self.model.whatsNewRequested.connect(lambda: requested.append(1))
        content = list(self.model.whatsNewContent)
        self.assertTrue(content)
        self.assertTrue(content[0]["isLatest"])
        self.model._whats_new_seen = ""
        self.model.checkWhatsNew()
        self.assertEqual(len(requested), 1)
        self.model.showWhatsNew()
        self.assertEqual(len(requested), 2)
        dismissed = []
        self.model.whatsNewDismissed.connect(lambda: dismissed.append(1))
        self.model.dismissWhatsNew()
        self.assertEqual(dismissed, [1])
        self.model.checkWhatsNew()
        self.assertEqual(len(requested), 2)

    def test_the_improve_hourglass_gives_up_at_its_timeout(self):
        asked = []
        self.model = self.build(request_monitor_download=lambda: asked.append(1))
        self.model.improveEta()
        self.assertTrue(self.model.improvingEta)
        self.assertEqual(asked, [1])
        self.model._improve_eta_timeout()
        self.assertFalse(self.model.improvingEta)
        # The timeout is a one-shot: a settled model stays settled.
        self.model._improve_eta_timeout()
        self.assertFalse(self.model.improvingEta)

    def test_showing_probe_points_pushes_the_config(self):
        self.model = self.build()
        self.model.setShowProbePoints(False)
        self.assertFalse(self.applied)
        self.model.setShowProbePoints(True)
        self.assertTrue(self.model.showProbePoints)
        self.assertEqual(len(self.applied), 1)
        self.assertTrue(self.applied[0].show_probe_points)
        self.model.setShowProbePoints(True)
        self.assertEqual(len(self.applied), 1)

    def test_chart_target_and_power_toggles_save_only_on_a_real_change(self):
        # An unset key is not False, so the first switch to False is a
        # real change; the second one must not rewrite the record.
        self.model = self.build()
        self.model.setShowTemperatureTargets(False)
        self.assertFalse(self.model._chart_config["showTargets"])
        self.assertEqual(len(self.applied), 1)
        self.model.setShowTemperatureTargets(False)
        self.assertEqual(len(self.applied), 1)
        self.model.setShowTemperatureTargets(True)
        self.assertTrue(self.model._chart_config["showTargets"])
        self.assertEqual(len(self.applied), 2)
        self.model.setShowTemperaturePower(True)
        self.assertTrue(self.model._chart_config["showPower"])
        self.assertEqual(len(self.applied), 3)
        self.model.setShowTemperaturePower(True)
        self.assertEqual(len(self.applied), 3)

    def test_an_unknown_pane_is_ignored_by_the_layout_slots(self):
        self.model = self.build()
        policy = self.qt.load("SectionLayoutPolicy")
        pane = policy.PANE_NAMES[0]
        section = policy.PANE_SECTION_ORDER[pane][0]
        self.model.setSectionLayout(pane, [], [section])
        before = self.value("sectionLayout")
        self.assertEqual(before[pane]["hidden"], [section])
        self.model.setSectionLayout("not-a-pane", [], [])
        self.assertEqual(self.value("sectionLayout"), before)
        self.assertEqual(self.unwrap(self.model.sectionLayoutFor("not-a-pane")), {})
        self.assertEqual(self.unwrap(self.model.sectionLayoutFor(pane)), before[pane])

    def test_the_frontend_slot_opens_the_configured_target(self):
        self.model = self.build(config=self.printer_config(frontend_url="http://front:7125"))
        opened = []
        with patch.object(self.qt.load("MoonrakerMonitorModel"), "QDesktopServices",
                          SimpleNamespace(openUrl=opened.append)):
            self.model.openFrontend()
        self.assertEqual([url.toString() for url in opened], ["http://front:7125"])

    def test_the_position_mode_toggle_reaches_the_toolhead(self):
        self.model = self.build()
        self.model.setPositionMode(False)
        self.assertEqual(self.model._toolhead._absolute_coordinates, False)
        self.model.setPositionMode(True)
        self.assertEqual(self.model._toolhead._absolute_coordinates, True)


class ChartCadenceTests(MonitorModelCase):
    """The chart feeds from its own 1 s clock, decoupled from the
    auxiliary delivery slider (the live report: a fast slider setting
    fed the history at push rate and the count cap trimmed the
    advertised 30-minute window to minutes)."""

    def test_a_tick_feeds_one_sample_on_the_fixed_clock(self):
        self.model = self.build()
        self.assertEqual(self.model._chart_timer.interval(), 1000)
        self.connect()
        self.observe(auxiliary={"extruder": {"temperature": 200.0}})
        before = len(self.model._history.series("extruder"))
        self.model._on_chart_tick()
        self.assertEqual(len(self.model._history.series("extruder")), before + 1)

    def test_auxiliary_arrivals_no_longer_feed_the_history(self):
        self.model = self.build()
        self.connect()
        self.observe(auxiliary={"extruder": {"temperature": 200.0}})
        count = len(self.model._history.series("extruder"))
        self.observe(auxiliary={"extruder": {"temperature": 205.0}})
        self.observe(auxiliary={"extruder": {"temperature": 210.0}})
        self.assertEqual(len(self.model._history.series("extruder")), count)

    def test_a_tick_never_bridges_a_disconnected_snapshot(self):
        self.model = self.build()
        self.connect()
        self.observe(auxiliary={"extruder": {"temperature": 200.0}})
        self.model._on_chart_tick()
        self.client.stop(reset_session=False)
        count = len(self.model._history.series("extruder"))
        self.model._on_chart_tick()
        self.assertEqual(len(self.model._history.series("extruder")), count)


class PlatePayloadTests(MonitorModelCase):
    """The plate payload: the visited (passed) state from the print
    snapshot and the geometry/exclusion merge."""

    def test_the_plate_payload_reads_visited_from_the_print_snapshot(self):
        # The green-printed fix: the visited set comes from the PRINT
        # snapshot — reading it from the monitor snapshot made passed
        # always false (the live report: no green outlines, ever).
        self.model = self.build()
        self.model._data._update(auxiliary={"exclude_object": {
            "objects": [
                {"name": "PART_A", "center": [10.0, 20.0],
                 "polygon": [[5.0, 15.0], [5.0, 25.0], [15.0, 25.0], [15.0, 15.0]]},
                {"name": "PART_B", "center": [40.0, 40.0],
                 "polygon": [[35.0, 35.0], [35.0, 45.0], [45.0, 45.0], [45.0, 35.0]]},
            ],
            "excluded_objects": [],
            "current_object": "PART_B",
        }})
        self.print_state = self.qt.load("PrintState").PrintSnapshot(
            plate_visited=frozenset({"PART_A"}))
        self.model._publish()
        rows = {row["name"]: row for row in self.model._values["plateObjects"]["objects"]}
        self.assertTrue(rows["PART_A"]["passed"], "the visited object did not read as passed")
        self.assertFalse(rows["PART_B"]["passed"], "the current object read as passed")
        # The layer transition: a fresh print snapshot without the
        # visited set clears every passed flag.
        self.print_state = self.qt.load("PrintState").PrintSnapshot()
        self.model._publish()
        rows = {row["name"]: row for row in self.model._values["plateObjects"]["objects"]}
        self.assertFalse(rows["PART_A"]["passed"], "the transition kept the stale passed flag")


    def test_the_plate_payload_merges_geometry_and_state(self):
        self.model = self.build()
        self.model._data._update(auxiliary={"exclude_object": {
            "objects": [{"name": "PART_A", "center": [10.0, 20.0],
                         "polygon": [[5.0, 15.0], [5.0, 25.0], [15.0, 25.0], [15.0, 15.0]]}],
            "excluded_objects": ["PART_A"],
            "current_object": None,
        }})
        self.model._publish()
        plate = self.model._values["plateObjects"]
        self.assertEqual(plate["excludedCount"], 1)
        row = plate["objects"][0]
        self.assertEqual(row["center"], [10.0, 20.0])
        self.assertTrue(row["excluded"])


class QueuedObjectGestureTests(MonitorModelCase):
    """The object gestures ride the one-shot lane: a click while another
    command is in flight is QUEUED, so the plate predicate the click
    checked has to be revalidated when the queue drains."""

    def plate(self, objects, excluded=(), state="printing"):
        """Land a plate with the state its gesture gate reads.
        exclude_object rides the core lane."""
        self.connect({"print_stats": {"filename": "part.gcode", "state": state},
                      "exclude_object": {
                          "objects": [{"name": name, "center": [0.0, 0.0], "polygon": []} for name in objects],
                          "excluded_objects": list(excluded), "current_object": None}})

    def hold_the_lane(self):
        """A mid-print one-shot in flight: babystepping is the
        legitimate mid-print command, and it is what the object click
        then queues behind."""
        self.model.adjustZOffset(0.05)
        self.qt.events(10)
        self.assertTrue(self.model.actionBusy)

    def scripts(self):
        return [request.options["body"]["script"] for request in self.sent("printer/gcode/script")]

    def test_a_queued_exclusion_is_revalidated_against_the_plate(self):
        self.model = self.model_now()
        self.plate(["PART_A"])
        self.hold_the_lane()
        self.model.excludeObject("PART_A")
        self.qt.events(10)
        self.assertEqual(self.model.actionStatus, "Exclude PART_A queued")
        # The plate moved while the entry waited: another client
        # excluded the name.
        self.plate(["PART_A"], excluded=["PART_A"])
        self.ack("printer/gcode/script")  # the nudge completes; the pump runs
        self.qt.events(20)
        self.assertNotIn('EXCLUDE_OBJECT NAME="PART_A"', self.scripts())
        self.assertEqual(self.model.actionStatus,
                         "Exclude PART_A cancelled: 'PART_A' is already excluded")

    def test_a_queued_restore_is_revalidated_against_the_plate(self):
        self.model = self.model_now()
        self.plate(["PART_A"], excluded=["PART_A"])
        self.hold_the_lane()
        self.model.restoreObject("PART_A")
        self.qt.events(10)
        self.assertEqual(self.model.actionStatus, "Restore PART_A queued")
        # The exclusion was lifted under the entry (a print restart
        # without it): RESET on an included name would be refused now.
        self.plate(["PART_A"])
        self.ack("printer/gcode/script")
        self.qt.events(20)
        self.assertNotIn('EXCLUDE_OBJECT RESET=1 NAME="PART_A"', self.scripts())
        self.assertEqual(self.model.actionStatus,
                         "Restore PART_A cancelled: 'PART_A' is not excluded")

    def test_a_queued_exclusion_is_revalidated_against_the_print_state(self):
        self.model = self.model_now()
        self.plate(["PART_A"])
        self.hold_the_lane()
        self.model.excludeObject("PART_A")
        # The print ended while the entry waited (the lane was still
        # holding the nudge, so nothing pumped yet).
        self.connect({"print_stats": {"state": "standby"}})
        self.ack("printer/gcode/script")
        self.qt.events(20)
        self.assertNotIn('EXCLUDE_OBJECT NAME="PART_A"', self.scripts())
        self.assertEqual(self.model.actionStatus,
                         "Exclude PART_A cancelled: No print is running")

    def test_a_dropped_queued_gesture_does_not_hold_the_next_one(self):
        # The dropped entry's latch dies with it: the retry after the
        # obstacle cleared is not read as a gesture already in flight.
        self.model = self.model_now()
        self.plate(["PART_A"])
        self.hold_the_lane()
        self.model.excludeObject("PART_A")
        self.connect({"print_stats": {"state": "standby"}})
        self.ack("printer/gcode/script")
        self.qt.events(20)
        self.plate(["PART_A"])  # the print resumed
        self.model.excludeObject("PART_A")
        self.qt.events(10)
        self.assertIn('EXCLUDE_OBJECT NAME="PART_A"', self.scripts())

    def test_a_confirmed_exclusion_releases_the_latch(self):
        # The end-to-end half of the latch contract: the printer's own
        # status landing is the confirmation, and the lane idle again
        # is all the next gesture must wait for.
        self.model = self.model_now()
        self.plate(["PART_A"])
        self.model.excludeObject("PART_A")
        self.qt.events(10)
        self.assertIn('EXCLUDE_OBJECT NAME="PART_A"', self.scripts())
        self.plate(["PART_A"], excluded=["PART_A"])
        self.ack("printer/gcode/script")
        self.qt.events(20)
        self.plate(["PART_A"])
        self.model.excludeObject("PART_A")
        self.qt.events(10)
        self.assertEqual(self.scripts().count('EXCLUDE_OBJECT NAME="PART_A"'), 2)


class PlateSplitPublicationTests(MonitorModelCase):
    """The follower face's boundary: the service's refined count crosses
    to the face as the bare number it is."""

    def test_the_refined_split_publishes_as_a_number(self):
        # The face colours to plateSplit, so the count must arrive as a
        # number — the layers publish separately, with the service's
        # memoised identity.
        self.model = self.build()
        self.model.setFollowerPopoverOpen(True)
        self.print_state = self.qt.load("PrintState").PrintSnapshot(
            plate_progress={"layers": {"current": {"classes": {}}}, "split": 7,
                            "method": "motion index", "anchor": 2})
        self.model._publish()
        self.assertEqual(self.value("plateSplit"), 7)
        self.assertEqual(self.value("plateProgressAnchor"), 2)
        self.assertTrue(self.value("plateProgressAvailable"))

    def test_an_unbuilt_plate_publishes_no_split(self):
        # No index is no boundary: the face ghosts rather than colouring
        # to a count from another poll.
        self.model = self.build()
        self.print_state = self.qt.load("PrintState").PrintSnapshot()
        self.model._publish()
        self.assertIsNone(self.value("plateSplit"))


class ModuleCoercionSlotTests(MonitorModelCase):
    """The coercions the follower's slots lean on: an unparseable stored
    or reported value must never reach the published numbers."""

    def test_an_unparseable_anchor_and_scale_fall_back(self):
        module = self.qt.load("MoonrakerMonitorModel")
        self.assertEqual(module._coerce_anchor(None), -1)
        self.assertEqual(module._coerce_anchor("junk"), -1)
        self.assertEqual(module._coerce_anchor("7"), 7, "a stored digit is a layer")
        view = module._follower_view_state({"lineScale": "junk", "showNext": "yes"})
        self.assertEqual(view["lineScale"], 0.7, "an unparseable stroke fell through")
        self.assertTrue(view["showNext"], "a non-bool flag must keep its default")

    def test_a_snapshot_without_layer_info_reads_no_layer(self):
        # The read is optional all the way down: a state frame that
        # carries no layer info answers None rather than raising.
        self.model = self.build()
        self.assertIsNone(self.model._layer_index(SimpleNamespace()))
        self.assertIsNone(self.model._layer_index(SimpleNamespace(layer=None)))
        self.assertEqual(
            self.model._layer_index(SimpleNamespace(layer=SimpleNamespace(index=4))), 4)


class FollowerViewSlotTests(MonitorModelCase):
    """The follower's view toggles, the pop-over gates and the stream
    switch: a repeat is a no-op — no document write, no publish."""

    def counters(self):
        """The two side effects these setters are gated on: the state
        document's rewrite and the publish. Both still run — the count is
        what the gate's absence is read from."""
        self.saves, self.publishes = [], []

        def counted(record, original):
            def call(*args, **kwargs):
                record.append(1)
                return original(*args, **kwargs)
            return call

        for entry in (patch.object(self.model, "_save_state",
                                   counted(self.saves, self.model._save_state)),
                      patch.object(self.model, "_publish",
                                   counted(self.publishes, self.model._publish))):
            entry.start()
            self.addCleanup(entry.stop)

    def test_a_repeated_toggle_neither_saves_nor_publishes(self):
        self.model = self.build()
        self.counters()
        # The defaults ARE the first values: the repeat path runs first.
        self.model.setFollowerShowPrevious(True)
        self.model.setFollowerShowNext(True)
        self.model.setFollowerShowBase(True)
        self.model.setFollowerShowTravels(False)
        self.model.setFollowerLineScale(0.7)
        self.assertEqual((self.saves, self.publishes), ([], []),
                         "a repeat rewrote the document or republished")
        self.model.setFollowerShowPrevious(False)
        self.model.setFollowerShowNext(False)
        self.model.setFollowerShowBase(False)
        self.model.setFollowerShowTravels(True)
        self.assertEqual((len(self.saves), len(self.publishes)), (4, 4),
                         "a real change did not save and publish")
        self.assertFalse(self.model.followerShowPrevious)
        self.assertFalse(self.model.followerShowNext)
        self.assertFalse(self.model.followerShowBase)
        self.assertTrue(self.model.followerShowTravels)

    def test_the_line_scale_clamps_into_the_control_range(self):
        self.model = self.build()
        self.model.setFollowerLineScale(3.0)
        self.assertEqual(self.model.followerLineScale, 2.0)
        self.model.setFollowerLineScale(0.1)
        self.assertEqual(self.model.followerLineScale, 0.5)
        self.model.setFollowerLineScale("junk")
        self.assertEqual(self.model.followerLineScale, 0.5,
                         "an unparseable stroke moved the published scale")

    def test_the_popover_gates_only_publish_on_a_real_change(self):
        self.model = self.build()
        self.counters()
        self.model.setChartOpen(False)
        self.model.setFollowerPopoverOpen(False)
        self.model.setFollowerInteracting(False)
        self.model.setPickerPopoverOpen(False)
        self.assertEqual(self.publishes, [], "a repeat gate republished")
        self.model.setChartOpen(True)
        self.model.setFollowerInteracting(True)
        self.model.setPickerPopoverOpen(True)
        self.assertEqual(len(self.publishes), 3, "a real gate change did not publish")
        self.assertTrue(self.model._chart_open)
        self.assertTrue(self.model._follower_interacting)
        self.assertTrue(self.model._picker_popover_open)

    def test_the_stream_switch_suspends_and_resumes_the_pane(self):
        self.model = self.build()
        self.counters()
        self.model.setWebcamStreamEnabled(True)
        self.assertEqual(self.publishes, [], "a repeat switch republished")
        self.model.setWebcamStreamEnabled(False)
        self.assertFalse(self.value("webcamStreamEnabled"))
        nonce = self.model._camera_refresh_nonce
        self.model.setWebcamStreamEnabled(True)
        self.assertTrue(self.value("webcamStreamEnabled"))
        self.assertEqual(self.model._camera_refresh_nonce, nonce + 1,
                         "resuming the stream did not request a fresh one")

    def test_the_connection_and_watchdog_guards_stand_down_with_the_stream_off(self):
        self.model = self.build()
        self.model.setWebcamStreamEnabled(False)
        nonce = self.model._camera_refresh_nonce
        # No stream to reload and none to recover: both paths stand
        # down rather than bumping the nonce or retrying a dead camera.
        self.model._on_connection_state("yes")
        self.assertEqual(self.model._camera_refresh_nonce, nonce)
        self.model._on_stream_failed()
        self.assertEqual(self.model._camera_refresh_nonce, nonce)

    def test_refresh_webcams_stands_down_with_the_stream_off(self):
        self.model = self.build()
        self.model.setWebcamStreamEnabled(False)
        requests = len(self.transport.requests)
        self.model.refreshWebcams()
        self.assertEqual(len(self.transport.requests), requests,
                         "a disabled stream still fetched the webcam list")

    def test_the_pane_diagnostics_run_without_a_visible_pane(self):
        # The QML pane's cold-start trace hooks: callable with no pane
        # attached, and the ids stay process-unique for the tracing.
        self.model = self.build()
        self.model.cameraFirstFrameRendered()
        first = self.model.cameraPaneInstanceId()
        second = self.model.cameraPaneInstanceId()
        self.assertIsInstance(first, int)
        self.assertEqual(second, first + 1, "the pane ids left the shared sequence")
        self.model.cameraPaneTrace(first, "applyCamera")


class FollowerSeekSlotTests(MonitorModelCase):
    """The seek slots' refusals: junk, out-of-range and repeat values
    never reach the coordinator's anchor and split seams."""

    def anchors(self):
        self.anchors, self.splits = [], []
        self.model = self.build(request_plate_anchor=self.anchors.append,
                                request_plate_split=self.splits.append)
        return self.model

    def test_a_junk_or_negative_seek_never_reaches_the_coordinator(self):
        model = self.anchors()
        model.setFollowerLayerAnchor("junk")
        model.setFollowerLayerAnchor(None)
        model.setFollowerLayerAnchor(-1)
        model.setFollowerLayerProgress("junk")
        self.assertEqual((self.anchors, self.splits), ([], []),
                         "a refused seek still reached the coordinator")

    def test_a_repeated_seek_republishes_without_moving_the_coordinator(self):
        model = self.anchors()
        model.setFollowerLayerAnchor(3)
        self.assertEqual(model.followerLayerAnchor, 3)
        self.assertEqual((self.anchors, self.splits), ([3], [-1]))
        # The same seek again: the face still needs the publish (it may
        # have rebuilt), but the coordinator is not asked twice.
        model.setFollowerLayerAnchor(3)
        self.assertEqual((self.anchors, self.splits), ([3], [-1]))

    def test_a_progress_scrub_refuses_an_unknown_live_layer(self):
        model = self.anchors()
        # Attached with no layer the print stands on: the scrub cannot
        # freeze anything, so nothing moves.
        self.assertLess(self.value("plateProgressAnchor"), 0)
        model.setFollowerLayerProgress(5)
        self.assertEqual((self.anchors, self.splits), ([], []))
        # Detached on a frozen split: the repeat is a republish only. The
        # flag is set directly — the detach's own publish re-attaches
        # against the fresh (job-less) snapshot.
        model._follower_attached = False
        frozen = min(5, model._values.get("plateLayerMotionCount", 0) or 0)
        model._follower_layer_split = frozen
        model.setFollowerLayerProgress(frozen)
        self.assertEqual(self.splits, [], "a repeated scrub re-asked for the same split")


class SurfaceDemandSlotTests(MonitorModelCase):
    """The follower surface's own seams: the staged view/plot slots, the
    demand scheduler's skips and the navigation commit's guards."""

    def surface(self, name="popover", width=400, height=300):
        surface = self.model_now()._plate_surfaces[name]
        surface.view = {"width": width, "height": height, "scale": 1.0,
                        "lineScale": 0.7, "dpr": 1.0, "compact": False}
        surface.plot = {"offsetX": 0.0, "offsetY": 0.0, "sx": 1.0, "sy": 1.0}
        return surface

    def wrapper(self, surface, layer, motions=20, payload=None):
        wrapped = self.qt.load("PlateQt").PlateLayer(
            payload if payload is not None else {"motions": motions, "classes": {}})
        surface.layers[layer] = wrapped
        return wrapped

    @staticmethod
    def local_url(path):
        from PyQt6.QtCore import QUrl
        return QUrl.fromLocalFile(path).toString()

    def test_the_view_and_plot_slots_ignore_an_unknown_surface(self):
        self.model = self.build()
        popover = self.model._plate_surfaces["popover"]
        self.model.setFollowerView("ghost-popover", 1.0, 0.7, 400, 300, False, 0.0, 0.0)
        self.model.setFollowerPlot("ghost-popover", 0.0, 0.0, 1.0, 1.0, 0.0, 0.0)
        self.assertIsNone(popover.stage["view"])
        self.assertIsNone(popover.stage["plot"])

    def test_a_settled_burst_with_nothing_staged_flushes_nothing(self):
        # The queued zero-tick flush may find its stage already drained:
        # it must leave the surface's context alone.
        surface = self.surface()
        generation = surface.generation
        self.model._flush_surface_context(surface)
        self.assertEqual(surface.generation, generation)

    def test_the_gesture_bake_stands_down_when_detached(self):
        self.model = self.build()
        self.model.setFollowerAttached(False)
        self.model.setFollowerGestureBake()
        surface = self.model._plate_surfaces["popover"]
        self.assertIsNone(surface.nav["job"], "a detached bake scheduled a navigation job")
        # The mini never carries the interaction raster: the hook is
        # popover-only even when the press arrives from another pane.
        self.model._nav_gesture_bake("mini")
        self.assertIsNone(self.model._plate_surfaces["mini"].nav["job"])

    def test_the_window_guards_answer_an_empty_placeholder(self):
        self.model = self.build()
        # An unknown surface has no window, and a non-integer anchor
        # (the QML's undefined slider) lands on the first layer rather
        # than poisoning the desired state.
        self.assertEqual(
            self.model._qt_window("ghost-popover", {"current": 1}, 3), {})
        surface = self.surface()
        window = self.model._qt_window(
            surface, {"prev": None, "current": None, "next": None}, None)
        self.assertEqual(window, {"prev": None, "current": None, "next": None})
        self.assertEqual(surface.anchor, 0)

    def test_the_navigation_backing_yields_to_the_memory_budget(self):
        # The interaction raster's 4x backing is bounded by the safe
        # single-buffer share: a surface big enough to blow it renders
        # at a reduced backing instead of risking the double-buffered
        # peak, and a surface with no geometry keeps the full 4x.
        self.model = self.build()
        surface = self.surface()
        surface.view = {"width": 0, "height": 0}
        self.assertEqual(self.model._navigation_backing(surface), 4.0)
        surface.view = {"width": 2200, "height": 2200}
        with self.assertLogs("MoonrakerPrintFollower", level="WARNING") as captured:
            backing = self.model._navigation_backing(surface)
        self.assertLess(backing, 4.0)
        self.assertGreaterEqual(backing, 1.0)
        self.assertTrue(any("backing reduced" in line for line in captured.output))

    def test_the_wake_arm_without_a_window_is_inert(self):
        # The scheduler arms only a stamped window: a surface whose
        # throttle never stamped must not leave a wake behind.
        surface = self.surface()
        self.assertIsNone(surface.nav["wake_at"])
        self.model._nav_arm_wake(surface)
        self.assertIsNone(surface.nav["wake_at"])

    def test_a_navigation_render_cancelled_mid_flight_publishes_nothing(self):
        # A supersede that lands while the worker is painting: the
        # terminal arrives without a picture, the ready slot keeps the
        # previous raster and the identical demand latches (no retry
        # loop against a demand that moved).
        surface = self.surface()
        self.model.setFollowerAttached(False)
        self.wrapper(surface, 6)
        surface.desired = {"current": 6, "ghosts": {"prev": None, "next": None}, "split": None}
        module = self.qt.load("MoonrakerMonitorModel")
        from PyQt6.QtGui import QImage

        def superseded(window, plot, view, split=None, cancel=None, **kwargs):
            # The picture is painted and THEN found superseded: the
            # cancel lands between the render and the publication.
            if cancel is not None:
                cancel.set()
            return QImage(4, 4, QImage.Format.Format_RGB32)

        with patch.object(module, "render_navigation_layer", superseded):
            self.model._schedule_navigation(surface)
            from PyQt6.QtCore import QThreadPool
            QThreadPool.globalInstance().waitForDone(5000)
            self.qt.events(20)
        self.assertEqual(surface.nav["url"], "", "a cancelled render published a raster")
        self.assertIsNone(surface.nav["job"])
        self.assertIsInstance(surface.nav["failed"], tuple,
                              "the render raised instead of cancelling")

    def test_the_scheduler_skips_a_layer_without_a_wrapper(self):
        # The demand names the current layer, but the payload for it
        # never landed: the dispatch skips it instead of rendering it.
        surface = self.surface()
        surface.desired = {"current": 6, "ghosts": {"prev": None, "next": None}, "split": None}
        self.model._schedule_surface(surface)
        self.assertEqual(surface.render_count, {})
        self.assertIsNone(surface.job)

    def test_the_prefix_demand_answers_its_two_refusals(self):
        surface = self.surface()
        wrapped = self.wrapper(surface, 6, motions=10)
        # A split at or past the layer's end is the whole layer: the
        # base renders, never a prefix.
        self.assertFalse(self.model._prefix_wanted(surface, 6, 12))
        self.assertTrue(self.model._prefix_wanted(surface, 6, 4),
                        "an unrendered prefix was not demanded")
        # A prefix whose own split reads invalid is no prefix at all.
        from PyQt6.QtGui import QImage
        wrapped.set_prefix(QImage(2, 2, QImage.Format.Format_RGB32),
                           self.local_url("/tmp/mpf/prefix.png"), -1, ("key",))
        wrapped.set_expected_key(("key",))
        self.assertTrue(self.model._prefix_wanted(surface, 6, 4))

    def test_the_dispatch_rechecks_the_prefix_under_the_queue(self):
        # The queue is composed from one reading of _prefix_wanted and
        # the dispatch re-reads it, so a wrapper whose prefix turns
        # unusable while the queue is built starts no prefix render.
        surface = self.surface()
        self.wrapper(surface, 6, motions=10)
        surface.desired = {"current": 6, "ghosts": {"prev": None, "next": None},
                           "split": 4}
        answers = []

        def first_only(_model, _surface, _layer, split):
            answers.append(split)
            return len(answers) == 1

        with patch.object(type(self.model), "_prefix_wanted", first_only):
            self.model._schedule_surface(surface)
        self.assertEqual(len(answers), 2, "the dispatch did not re-read the prefix verdict")
        self.assertEqual(surface.job["kind"], "full",
                         "the prefix the re-check refused still reached the renderer")
        from PyQt6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(5000)
        self.qt.events(20)

    def test_an_obsolete_job_without_a_demand_is_cancelled(self):
        surface = self.surface()
        import threading
        cancel = threading.Event()
        surface.job = {"layer": 6, "token": 1, "generation": 0, "epoch": 0,
                       "serial": 1, "state": "running", "cancel": cancel}
        surface.desired = None
        self.model._cancel_obsolete_job(surface)
        self.assertTrue(cancel.is_set(), "a demand-less job kept rendering")

    def test_the_seek_trace_ignores_a_stage_outside_a_seek(self):
        # Trace enabled but no seek entry yet: the stage is dropped
        # rather than timestamped from the process start.
        self.model = self.build()
        self.model._seek_trace_enabled = True
        self.model._trace("T6 payload obtained", {"surface": "popover"})
        self.assertEqual(self.model._seek_trace, [])

    def test_the_navigation_commit_refuses_a_dead_surface_and_a_foreign_kind(self):
        surface = self.surface()
        self.model._nav_committed(("nav", None, self.local_url("/tmp/mpf/n.png"), ("k",)),
                                  ("ghost-popover", 0, 0, 0, ("k",), "nav", None, 0, 1))
        self.model._nav_committed(("full", None), ("popover", 0, 0, 0, ("k",), "full", None, 0, 1))
        self.assertEqual(surface.nav["url"], "", "a foreign ticket promoted a raster")

    def test_a_committed_navigation_raster_survives_its_retired_file(self):
        # The double buffer's replacement: the ready URL is promoted
        # atomically and the retired buffer is unlinked — a retirement
        # that cannot be unlinked must not undo the promotion.
        surface = self.surface()
        self.model.setFollowerAttached(False)
        self.wrapper(surface, 6)
        surface.desired = {"current": 6, "ghosts": {"prev": None, "next": None}, "split": None}
        key = self.model._navigation_key(surface)
        surface.job_epoch = 0
        surface.nav["serial"] = 4
        surface.nav["job"] = {"key": key, "cancel": self.cancelled_event(),
                              "epoch": 0, "serial": 4, "backing": 4.0}
        retired = tempfile.mkdtemp(prefix="mpfxtest-retired-")
        surface.nav["url"] = self.local_url(retired)
        url = self.local_url(os.path.join(tempfile.mkdtemp(prefix="mpfxtest-nav-"), "nav.png"))
        self.model._nav_committed(("nav", None, url, key),
                                  ("popover", -1, 0, 0, key, "nav", None, 0, 4))
        self.assertEqual(surface.nav["url"], url)
        self.assertEqual(surface.nav["key"], key)
        self.assertTrue(os.path.isdir(retired), "the promotion removed the live retiree")

    @staticmethod
    def cancelled_event():
        import threading
        return threading.Event()

    def test_a_commit_for_a_vanished_surface_and_layer_is_accounted(self):
        surface = self.surface("mini")
        self.wrapper(surface, 7)
        from PyQt6.QtGui import QImage
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        images = ("full", image, self.local_url("/tmp/mpf/a-c.png"),
                  image, self.local_url("/tmp/mpf/a-b.png"),
                  image, self.local_url("/tmp/mpf/a-t.png"))
        # A worker that outlived its surface's retirement: the start is
        # counted nowhere and the completion never touches another
        # surface's scheduler.
        self.model._raster_started(("ghost-popover", 7, 5, 3, None, "full", None, 2, 9))
        self.assertEqual(surface.stats["started"], 0)
        self.model._raster_committed(images, ("ghost-popover", 7, 5, 3, ("k",), "full", None, 2, 9))
        # The active job whose layer the demand has since left: the
        # picture lands on the wrapper but never counts as committed.
        surface.generation = 3
        surface.job_epoch = 2
        surface.tokens[7] = 5
        surface.job = {"layer": 7, "token": 5, "generation": 3, "epoch": 2,
                       "serial": 9, "state": "running", "cancel": self.cancelled_event()}
        surface.desired = {"current": 6, "ghosts": {"prev": None, "next": None}, "split": None}
        self.model._raster_committed(images, ("mini", 7, 5, 3, ("k",), "full", None, 2, 9))
        self.assertEqual(surface.stats["discarded"], 1)
        self.assertEqual(surface.stats["committed"], 0)

    def test_a_dead_bridge_drops_the_job_before_it_starts(self):
        # The owner's deleteLater can land before a queued job starts:
        # the job's first emit then raises, and the worker must drop it
        # rather than abort the pool thread.
        surface = self.surface()
        self.wrapper(surface, 6)
        surface.desired = {"current": 6, "ghosts": {"prev": None, "next": None}, "split": None}

        class DeadSignal:
            @staticmethod
            def emit(*args):
                raise RuntimeError("wrapped C/C++ object of type RasterBridge has been deleted")

        self.model._raster_bridge = SimpleNamespace(started=DeadSignal(), done=DeadSignal())
        self.model._schedule_surface(surface)
        from PyQt6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(5000)
        self.qt.events(20)
        self.assertEqual(surface.stats["started"], 0)
        self.assertEqual(surface.stats["committed"], 0)

    def test_a_discarded_jobs_files_are_swept(self):
        # The sweep must swallow a path that is gone or cannot be
        # unlinked: the picture is already discarded either way.
        directory = tempfile.mkdtemp(prefix="mpfxtest-sweep-")
        url = self.local_url(directory)
        self.model = self.build()
        self.model._unlink_asset_files(())
        self.model._unlink_asset_files(("full", None, url, None, url, None, url))
        self.model._unlink_asset_files(("prefix", None, url))
        self.assertTrue(os.path.isdir(directory), "the sweep removed a live directory")


class SurfaceLifecycleTests(MonitorModelCase):
    """The model's own teardown and accounting: the pin release at
    destruction, the raster directory's sweep and the memory story."""

    def test_the_destruction_paths_release_every_wrapper_pin(self):
        released = []
        stub = SimpleNamespace(unpin_decoded=released.append,
                               packed_bytes=lambda: 0,
                               decoded_resident_bytes=lambda: 0,
                               pinned_decoded_bytes=lambda: 0)
        self.model = self.build(index_service=stub)
        surface = self.model._plate_surfaces["popover"]
        for layer in (4, 5):
            surface.layers[layer] = self.qt.load("PlateQt").PlateLayer({"motions": 1, "classes": {}})
        # The follower's service outlives the model: its death hands the
        # decoded payloads back rather than pinning them forever.
        self.model._release_all_pins()
        self.assertEqual(sorted(released), [4, 5])

    def test_a_model_without_the_service_releases_nothing(self):
        # The harness and the tests mount without an index service.
        self.model = self.build()
        surface = self.model._plate_surfaces["popover"]
        surface.layers[4] = self.qt.load("PlateQt").PlateLayer({"motions": 1, "classes": {}})
        self.model._unpin_surface(surface)
        self.model._release_all_pins()

    def test_the_instance_sweeps_its_own_raster_directory(self):
        self.model = self.build()
        directory = self.model._raster_cache_dir
        pathlib.Path(directory, "leftover.png").write_bytes(b"leftover")
        self.model._cleanup_raster_dir()
        self.assertFalse(os.path.exists(directory))
        # An un-removable directory must not raise out of the model's
        # own destruction.
        with patch.object(self.model, "_raster_cache_dir", directory):
            with patch("shutil.rmtree", side_effect=OSError("no removal")):
                self.model._cleanup_raster_dir()

    def test_the_memory_accounting_survives_a_hostile_directory(self):
        self.model = self.build()
        pathlib.Path(self.model._raster_cache_dir, "kept.png").write_bytes(b"kept")
        self.assertEqual(self.model.memory_accounting()["rasterDirFiles"], 1)
        # A directory that cannot be listed reads as empty, and a file
        # that cannot be stat'ed is skipped rather than raised.
        with patch("os.stat", side_effect=OSError("no stat")):
            self.assertEqual(self.model.memory_accounting()["rasterDirFiles"], 0)
        with patch("os.listdir", side_effect=OSError("no listing")):
            self.assertEqual(self.model.memory_accounting()["rasterDirFiles"], 0)

    def test_the_prune_survives_a_hostile_directory(self):
        self.model = self.build()
        stale = os.path.join(self.model._raster_cache_dir, "r-stale.png")
        pathlib.Path(stale).write_bytes(b"stale")
        with patch("os.stat", side_effect=OSError("no stat")):
            self.model._prune_raster_cache(keep=0)
        self.assertTrue(os.path.exists(stale), "an unstat'able file was unlinked")
        with patch("os.unlink", side_effect=OSError("no unlink")):
            self.model._prune_raster_cache(keep=0)
        self.assertTrue(os.path.exists(stale))
        with patch("os.listdir", side_effect=OSError("no listing")):
            self.model._prune_raster_cache(keep=0)
        os.unlink(stale)

    def test_a_closed_picker_keeps_the_last_plate_payload(self):
        # The plate map is required nowhere while the picker is closed
        # and the section collapsed: the previous payload is carried
        # untouched rather than rebuilt per poll.
        self.model = self.build()
        self.model._data._update(auxiliary={"exclude_object": {
            "objects": [{"name": "PART_A", "center": [10.0, 20.0],
                         "polygon": [[5.0, 15.0], [5.0, 25.0], [15.0, 25.0], [15.0, 15.0]]}],
            "excluded_objects": [], "current_object": None}})
        self.model._publish()
        published = self.model._values["plateObjects"]
        self.assertEqual(len(published["objects"]), 1)
        self.model.setSectionExpanded("plate", False)
        self.model._publish()
        self.assertIs(self.model._values["plateObjects"], published,
                      "the closed picker rebuilt the plate payload")
        self.assertTrue(self.model._values["plateHasObjects"])

    def test_an_unparseable_layer_count_publishes_zero(self):
        self.model = self.build()
        self.print_state = self.qt.load("PrintState").PrintSnapshot(plate_layer_count="junk")
        self.model._publish()
        self.assertEqual(self.value("plateLayerCount"), 0,
                         "an unparseable layer count reached the slider's range")
