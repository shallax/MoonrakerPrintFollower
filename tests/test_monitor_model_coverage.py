"""Coverage for MoonrakerMonitorModel's plumbing: the file-manager confirm
and print payloads with their slots, the publish value builders, the
hydration edge branches and the action tail.

Every test drives the production model through the Qt runtime harness
(real PyQt6, the scripted transport, the Cura host doubles) and builds it
the way the integration suite does, so each branch is reached through the
state the model reads in production rather than by hand-stuffing its
internals.

Leftovers, with reasons:
  - 935: the published window's swap-back. Both thresholds are clamped
    into the same mesh window and the clamp is monotone, so
    setBedMeshThresholds cannot hand the publish an inverted pair; the
    line guards a state nothing constructs.
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
        root = tempfile.mkdtemp(prefix="mpf-model-", dir=SCRATCH if os.path.isdir(SCRATCH) else None)
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
        path = os.path.join(tempfile.mkdtemp(prefix="mpf-state-"), "sections.json")
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
        path = os.path.join(tempfile.mkdtemp(prefix="mpf-store-"), "state.json")
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
        blocker = os.path.join(tempfile.mkdtemp(prefix="mpf-blocker-"), "blocker")
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
