"""Behavioural coverage of the assembled plugin using real Qt, not source strings."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tests.qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, Preferences, ScriptedSocket, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class QtRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.clients = []
        self.followers = []
        self.addCleanup(self.close_runtime)
        # The download flow opens the save picker; this harness process
        # has no QApplication for a real dialog, so the class-level
        # patch covers every test that drives a download.
        self.dialog_patch = patch("PyQt6.QtWidgets.QFileDialog.getSaveFileName",
                                  return_value=("/tmp/never-written.gcode", ""))
        self.dialog_patch.start()
        self.addCleanup(self.dialog_patch.stop)

    def close_runtime(self):
        for follower in self.followers:
            follower.deinitialize()
        for client in self.clients:
            client.stop()
            client.transport.close()  # the manager's pooled sockets close with it
        self.qt.events()

    def client(self):
        transport = ScriptedTransport()
        client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=transport)
        client.configure("http://printer-a", "test-key", 750)
        self.clients.append(client)
        return client, transport

    def follower(self, *, preferences=None, machine=True):
        app = self.qt.Application(preferences=preferences, machine=machine)
        module = self.qt.load("MoonrakerClient")
        real_client = module.MoonrakerClient
        transport = ScriptedTransport()
        # Inject transport through the real client's constructor; instantiate all
        # composed services/signals, including the real binding startup.
        with patch.object(self.qt.load("FollowerRuntime"), "MoonrakerClient",
                          lambda parent: real_client(parent, transport=transport, socket=ScriptedSocket())):
            follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.followers.append(follower)
        return app, follower, transport

    def output(self):
        client, transport = self.client()
        app = self.qt.Application()
        config = self.qt.load("PrinterConfig").PrinterConfig(
            url="http://printer-a", api_key="test-key", upload_dialog=True)
        from PyQt6.QtCore import QObject, pyqtSignal

        class _Presentation(QObject):
            bedMeshThresholdsRequested = pyqtSignal(float, float)
            printPauseRequested = pyqtSignal()

            def publish_pause_verdicts(self, *args):
                pass

        follower = SimpleNamespace(client=client, transport=transport, session=client.session,
            presentation=_Presentation(),
            current_printer_config=lambda: config,
            current_printer_identity=lambda: (app.stack.getId(), app.stack.getName()),
            apply_printer_config=lambda updated: None)
        device = self.qt.load("MoonrakerOutputDevice").MoonrakerOutputDevice(app, "A",
            client=client, config=follower.current_printer_config,
            apply_config=follower.apply_printer_config, active_identity=follower.current_printer_identity)
        plugin = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app, follower)
        plugin._devices["A"] = device
        plugin._current = device
        self.addCleanup(device.deactivate)
        app.createQmlComponent = Mock(return_value=Mock())
        device.requestWrite(None)
        return app, device, plugin, client, transport

    def monitor(self, *, typed=False):
        client, transport = self.client()
        config = self.qt.load("PrinterConfig").PrinterConfig(url="http://printer-a", api_key="test-key")
        from PyQt6.QtCore import QObject, pyqtSignal
        class Mesh(QObject):
            changed = pyqtSignal()
            visible = True
            def __init__(self):
                super().__init__()
                self.snapshot = {}
            def update(self, value):
                if value != self.snapshot:
                    self.snapshot = value
                    self.changed.emit()
            def set_visible(self, value):
                self.visible = value
                self.changed.emit()
        model = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel(None, 1,
            client=client, print_state=lambda: self.qt.load("PrintState").PrintSnapshot(),
            config=lambda: config, apply_config=lambda value: None, bed_mesh=Mesh())
        client._poll_timer.stop()
        self.addCleanup(model.setMonitoringActive, False)
        return model, client, transport


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class MonitorDataAuxTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=self.transport)
        self.client.configure("http://printer-a", "test-key", 750)
        self.addCleanup(self.client.stop)
        self.data = self.qt.load("MonitorData").MonitorData(self.client, None)
        self.data.set_owner_active(True)
        self.addCleanup(self.data.set_owner_active, False)

    def deliver(self, channel, payload, error=None):
        request = next((r for r in self.transport.requests if r.channel == channel), None)
        self.assertIsNotNone(request, channel)
        request.callback(payload, error)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class PreviewMotionTests(unittest.TestCase):
    def setUp(self):
        from contextlib import contextmanager

        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.view = SimpleNamespace(path=0.0, minimum=0)
        def set_path(value): self.view.path = value
        def set_minimum(value): self.view.minimum = value
        self.view.setPath = set_path
        self.view.setMinimumPath = set_minimum
        self.view.getCurrentPath = lambda: self.view.path
        self.view.getMinimumPath = lambda: self.view.minimum
        self.view.getMaxPaths = lambda: 100

        @contextmanager
        def writing_preview():
            yield self.view

        self.cura = SimpleNamespace(view=self.view, writing_preview=writing_preview)
        self.remembers = 0
        def remembered():
            self.remembers += 1
        self.motion = self.qt.load("PreviewMotion").PreviewMotion(self.cura, remembered)
        self.addCleanup(self.motion.close)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class RemoteFileServiceDownloadTests(unittest.TestCase):
    """The streamed-download state machine: failure latch and backoff retry."""

    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.transport.configure("http://printer-a", "test-key")
        module = self.qt.load("RemoteFileService")
        self.files = module.RemoteFileService(self.transport, None)
        self.addCleanup(self.files.close)
        self.files.bind(("part.gcode", 100, 1))
        self.files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 0, modified=1)
        self.files._want_file = True


    def _wait(self, predicate, timeout=5.0):
        # The writer thread reports completion through a queued signal;
        # the wait pumps the event loop until it lands.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.qt.events()
            time.sleep(0.005)
        return predicate()

    def _reply_double(self, error=False, payload=b"G1 X0 Y0\n", size=None, content_encoding=None):
        from PyQt6.QtCore import QObject, pyqtSignal
        from PyQt6.QtNetwork import QNetworkReply

        class FakeReply(QObject):
            readyRead = pyqtSignal()
            finished = pyqtSignal()
            def __init__(self, error):
                super().__init__()
                self._error = QNetworkReply.NetworkError.ContentNotFoundError if error else QNetworkReply.NetworkError.NoError
                self._buffer = payload
            def setReadBufferSize(self, size): pass
            def readAll(self):
                # A real reply drains: each readAll returns only the
                # bytes that arrived since the last read.
                data, self._buffer = self._buffer, b""
                return data
            def rawHeader(self, name):
                # The real API returns the header bytes; absent length
                # means indeterminate progress. Content-Encoding is
                # the download guard's tell — the double serves it
                # absent unless a test pins a compressed form.
                if name == b"Content-Encoding":
                    return content_encoding or b""
                return str(size).encode("ascii") if size is not None else b""
            def read(self, maxsize): return self.readAll()
            def bytesAvailable(self): return 0
            def error(self): return self._error
            def errorString(self): return "simulated"
            def abort(self): pass
            def deleteLater(self): pass
        return FakeReply(error)

    def _identity(self, name, size):
        return self.qt.load("MoonrakerProtocol").RemoteFileIdentity(name, size, modified=1)

    def _gated_target(self, gate, name="GatedTarget"):
        # A target whose writer parks on the first write until the gate
        # is set — the deterministic stand-in for a slow disk.
        DownloadTarget = self.qt.load("DownloadStream").DownloadTarget

        class GatedTarget(DownloadTarget):
            armed = True
            def write(self, data):
                if type(self).armed:
                    type(self).armed = False
                    gate.wait()
                return super().write(data)
        GatedTarget.__name__ = name
        return GatedTarget


    def _cura_double(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class CuraDouble(QObject):
            loadFailed = pyqtSignal(str)

            def __init__(self, parent=None):
                super().__init__(parent)
                self.loads = []

            def load(self, lease):
                self.loads.append(lease)

        return CuraDouble()

    def _file_download(self, **kwargs):
        """The file-manager save lane over this REAL one-shot service."""
        module = self.qt.load("FileDownload")
        download = module.FileDownload(self.files, self._cura_double(), None, **kwargs)
        self.addCleanup(download.close)
        return module, download

    def _save_target(self, name="saved.gcode", payload=None):
        directory = tempfile.mkdtemp(prefix="mpfxtest-save-")
        target = os.path.join(directory, name)
        if payload is not None:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(payload)
        return target


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class ToolheadControllerTests(unittest.TestCase):
    """The jog queue and pause-first sequencing against scripted doubles."""

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

        class Data(QObject):
            changed = pyqtSignal()
            invalidated = pyqtSignal()
            commandChanged = pyqtSignal(object)
            def __init__(self):
                super().__init__()
                self.active = True
                self.connected = True
                self.guard_calls = []
                self.snapshot = SimpleNamespace(core={}, auxiliary={})
            def set_toolhead_guard(self, active):
                self.guard_calls.append(active)
            def set_state(self, state):
                self.snapshot = SimpleNamespace(
                    core={"print_stats": {"state": state},
                          "gcode_move": {"absolute_coordinates": True},
                          "motion_report": {"live_position": [10.0, 10.0, 10.0, 0.0]}},
                    auxiliary={"toolhead": {"homed_axes": "xyz",
                                             "axis_minimum": [0, 0, 0],
                                             "axis_maximum": [200, 200, 200]}})
                # The 4.2.0 contract: the record rides the data owner.
                from mpf.monitor.MonitorPermissions import Observation
                self.observation = Observation(
                    active=self.active, connection="yes" if self.connected else "unknown",
                    state=state, homed_axes="xyz", assumed_stopped=False,
                    save_config_pending=False, controls_locked=False, busy=False)
                self.changed.emit()

        class Commands(QObject):
            changed = pyqtSignal()
            emergencyStopped = pyqtSignal()
            def __init__(self):
                super().__init__()
                self.busy = False
                self.fail_next = False
                self.sent = []
            def send(self, label, path, body=None):
                if self.busy:
                    return False
                if self.fail_next:
                    # A refused send completes synchronously: the real
                    # MonitorCommands clears busy, emits changed (re-entering
                    # the controller's pump), then the tracker emits the
                    # terminal failed event.
                    self.fail_next = False
                    self.changed.emit()
                    self.owner.commandChanged.emit(
                        {"name": label, "outcome": "failed", "terminal": True, "detail": "unavailable"})
                    return False
                self.sent.append((label, path, body))
                self.busy = True
                return True
            def complete(self):
                self.busy = False
                self.changed.emit()

        self.data = Data()
        self.commands = Commands()
        self.commands.owner = self.data
        from tests.manual_motion_support import attach
        attach(self.data, self.commands)
        module = self.qt.load("ToolheadController")
        self.controller = module.ToolheadController(self.data, self.commands)
        self.addCleanup(self.controller.close)

    def scripts(self):
        return [body["script"] for label, path, body in self.commands.sent if path == "printer/gcode/script"]

    def pauses(self):
        return [1 for label, path, body in self.commands.sent if path == "printer/print/pause"]


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class RemoteFileServiceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        self.transport.configure("http://printer-a", "test-key")
        self.service = self.qt.load("RemoteFileService").RemoteFileService(self.transport, None)
        self.addCleanup(self.service.close)
        self.service.bind(("part.gcode", 1000, 1))

    def _wait_for_the_backoff_to_expire(self, timeout=3.0):
        """Pump events until the service's own retry window has passed.

        The window is read from time.monotonic(), which on Windows is
        GetTickCount64 — a clock that only advances every ~15 ms tick, so
        a 5 ms window is not cleared by waiting 5 ms (or 15). Waiting on
        the implementation's own deadline keeps the assertion as strict
        on a coarse clock as on a fine one; the iteration bound means a
        clock that never advances fails the assertion, not the suite.
        """
        for _ in range(int(timeout * 200)):
            if time.monotonic() >= self.service._metadata_retry_at:
                return
            self.qt.events(5)


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class CuraIntegrationLoadTests(unittest.TestCase):
    """The load-into-Cura contract: preflights, the unconfirmed-load
    watchdog and the quiet late completion."""

    def setUp(self):
        from PyQt6.QtCore import QObject, pyqtSignal
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

        class FakeApp(QObject):
            fileCompleted = pyqtSignal(str)
            workspaceLoaded = pyqtSignal(str)
            def __init__(self):
                super().__init__()
                self.loaded = []
            def getController(self):
                class Stub:
                    def getScene(self): raise AttributeError()
                    def getView(self, name): return None
                return Stub()
            def getBackend(self): return None
            def readLocalFile(self, url, **kwargs): self.loaded.append(url.toLocalFile())

        self.app = FakeApp()
        module = self.qt.load("CuraIntegration")
        self.cura = module.CuraIntegration(self.app, None)
        self.addCleanup(self.cura.close)
        self.releases = []

    def _wait(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.qt.events(10)
        return predicate()

    def _lease(self, path):
        def release(lease_path):
            self.releases.append(lease_path)
            shutil.rmtree(os.path.dirname(lease_path), ignore_errors=True)
        return self.qt.load("RemoteFileService").FileLease(path, release)

    def _make_file(self, name="part.gcode"):
        directory = tempfile.mkdtemp(prefix="load-")
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(b"G1 X0\n")
        return path


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt integration suite")
class SettingsSaveRefusalTests(unittest.TestCase):
    """The settings dialog's verdict: a refused persistence write must
    reach saveConfig's caller as a failure. Cura's MachineAction flow
    keeps the dialog open on that False (the QML's `saveRefused`), so
    the user sees the refusal instead of a dialog that closed over a
    change that never landed."""

    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.fail_save = False
        self.followers = []
        self.addCleanup(self._close)

    def _close(self):
        for follower in self.followers:
            follower.deinitialize()
        self.qt.events()

    def _follower(self):
        app = self.qt.Application()
        module = self.qt.load("MoonrakerClient")
        root = self.qt.load("FollowerRuntime")
        transport = ScriptedTransport()
        real_save = root._savefile_write

        def save(path, text):
            if self.fail_save and path.endswith("settings.json"):
                return False  # the disk refuses the settings document alone
            return real_save(path, text)

        with patch.object(root, "MoonrakerClient",
                          lambda parent: module.MoonrakerClient(parent, transport=transport, socket=ScriptedSocket())), \
                patch.object(root, "_savefile_write", save):
            follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.followers.append(follower)
        return app, follower

    def _action(self, follower):
        from PyQt6.QtCore import QObject

        class _MachineActionBase(QObject):
            def __init__(self, key, label):
                super().__init__()

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
            action = self.qt.load("MoonrakerFollowerMachineAction").MoonrakerFollowerMachineAction(
                application, follower)
        self.addCleanup(action.deleteLater)
        return action

    @staticmethod
    def _params(**overrides):
        params = {
            "enabled": True,
            "url": "http://printer-a:7125",
            "api_key": "k",
            "feed_mode": "websocket",
            "poll_interval_ms": 2500,
            "aux_interval_ms": 1000,
            "console_interval_ms": 1000,
            "follow_mode": "exact",
            "z_tolerance": "0.05",
            "cache_max_mb": "512",
            "ready_retry_interval_s": "1.0",
            "filename_translate_input": "a",
            "filename_translate_output": "b",
        }
        params.update(overrides)
        return params


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['CuraIntegrationLoadTests', 'Mock', 'MonitorDataAuxTests', 'PipeSafeHandler', 'Preferences', 'PreviewMotionTests', 'QT_AVAILABLE', 'QtRuntimeTests', 'RemoteFileServiceDownloadTests', 'RemoteFileServiceMetadataTests', 'ScriptedSocket', 'ScriptedTransport', 'SettingsSaveRefusalTests', 'SimpleNamespace', 'ThreadingHTTPServer', 'ToolheadControllerTests', 'annotations', 'json', 'os', 'patch', 'runtime', 'shutil', 'sys', 'tempfile', 'threading', 'time', 'unittest']
