"""Line coverage for the Moonraker output-device adapter pair.

Both modules are driven as the running host drives them: real Qt through the
harness in tests/qt_runtime_support.py, a real MoonrakerClient on a scripted
transport, the real MoonrakerMonitorModel built by the plugin exactly as
production builds it, and the composed upload controller behind a QML-dialog
double. Nothing in either module is stubbed out to make a line reachable.

Uncovered lines: none — every statement in both modules executes here, so no
line is left to a Qt- or Cura-bound path that cannot run in the container. The
handful of seams that do need a double are the ones with no in-container
substitute: application.createQmlComponent (the QML upload dialog), the
multipart QNetworkReply, UM.Message (inert under the harness, and the popup
text/duration is the behaviour under test) and QDesktopServices (which would
otherwise launch a browser on the test host). Each seam's failure mode is
exercised as well, so the guarded lines inside the modules still run.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, ScriptedTransport, runtime

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal


if QT_AVAILABLE:

    class DialogDouble:
        """The upload dialog the adapter creates from QML."""

        def __init__(self):
            self.visible = True
            self.shown = 0
            self.deleted = 0

        def isVisible(self): return self.visible
        def show(self): self.shown += 1
        def deleteLater(self): self.deleted += 1

    class DialogFactory:
        """Stands in for application.createQmlComponent."""

        def __init__(self, app):
            self.created = []
            app.createQmlComponent = self._create

        def _create(self, path, context):
            dialog = DialogDouble()
            dialog.path = path
            self.created.append(dialog)
            return dialog

    class UploadReply(QObject):
        """The multipart reply: the test owns the body and the terminal."""

        uploadProgress = pyqtSignal(int, int)
        finished = pyqtSignal()

        def __init__(self, body=None):
            super().__init__()
            self._body = body or {}
            self.deleted = 0

        def error(self):
            from PyQt6.QtNetwork import QNetworkReply
            return QNetworkReply.NetworkError.NoError

        def errorString(self): return ""
        def readAll(self): return json.dumps(self._body).encode()
        def abort(self): pass
        def isRunning(self): return False
        def deleteLater(self): self.deleted += 1

    class Signal:
        """The one slot-registering signal shape UM.Message needs."""

        def __init__(self): self.slots = []

        def connect(self, slot): self.slots.append(slot)

        def emit(self, *args):
            for slot in list(self.slots):
                slot(*args)

    class Popup:
        """Stands in for UM.Message: records what the adapter told the user."""

        instances = []

        def __init__(self, text="", lifetime=0, is_visible=False):
            self.text, self.lifetime, self.is_visible = text, lifetime, is_visible
            self.title, self.progress, self.actions = "", None, []
            self.shown = self.hidden = 0
            self.actionTriggered = Signal()
            Popup.instances.append(self)

        def setTitle(self, value): self.title = value
        def setText(self, value): self.text = value
        def setProgress(self, value): self.progress = value
        def addAction(self, *args): self.actions.append(args)
        def show(self): self.shown += 1
        def hide(self): self.hidden += 1

    class Presentation(QObject):
        """The two request signals the output plugin mirrors into the Monitor."""

        bedMeshThresholdsRequested = pyqtSignal(float, float)
        printPauseRequested = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.verdict_pushes = []

        def publish_pause_verdicts(self, *args):
            # The real PreviewPresentation's surface (the debt pack's
            # single-authority push); the double records it.
            self.verdict_pushes.append(args)

    class BedMesh(QObject):
        """The Monitor's mesh capability, including the threshold sink."""

        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.snapshot = {}
            self.visible = True
            self.thresholds = None

        def update(self, value):
            if value != self.snapshot:
                self.snapshot = value
                self.changed.emit()

        def set_visible(self, value):
            self.visible = bool(value)
            self.changed.emit()

        def set_thresholds(self, low, high):
            self.thresholds = (low, high)

    class FollowerDouble(QObject):
        """The follower facade slice the output plugin reads, wired as production."""

        download_failed = pyqtSignal(str)

        def __init__(self, client, config, identity, print_state):
            super().__init__()
            self.client = client
            self.presentation = Presentation()
            self.bed_mesh = BedMesh()
            self.print_state = print_state
            self.persistence = None
            self.notice = None  # a callable once a notice exists
            self.blocks = []
            self.loads = []
            self.applied = []
            self._config = config
            self._identity = identity

        def set_config(self, config): self._config = config
        def current_printer_config(self): return self._config
        def current_printer_identity(self): return self._identity()
        def apply_printer_config(self, config): self.applied.append(config)
        def confirmForceLoadCurrentPrint(self): self.loads.append("load")
        def confirmDownloadForMonitor(self): self.loads.append("monitor")
        def request_file_download(self, relpath): self.loads.append(relpath)
        def receive_preview_block(self, block): self.blocks.append(block)
        def has_toolpath(self): return True


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt runtime suite")
class OutputDeviceTestCase(unittest.TestCase):
    """Shared harness: the production modules imported under the fake Cura host."""

    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.clients = []
        self.devices = []
        self.applied = []
        Popup.instances.clear()
        self.addCleanup(self.close_runtime)

    def close_runtime(self):
        for device in self.devices:
            device.deactivate()
        for client in self.clients:
            client.stop()
            client.transport.close()  # the manager's pooled sockets close with it
        self.qt.events()

    def printer_config(self, **overrides):
        values = {"url": "http://printer-a", "api_key": "test-key"}
        values.update(overrides)
        return self.qt.load("PrinterConfig").PrinterConfig(**values)

    def client(self, url="http://printer-a", api_key="test-key"):
        client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=ScriptedTransport())
        client.configure(url, api_key, 750)
        client._poll_timer.stop()
        self.clients.append(client)
        return client

    def follower(self, client, config, *, machine_id="A", name="Printer A"):
        return FollowerDouble(client, config, lambda: (machine_id, name),
                              self.qt.load("PrintState").PrintSnapshot())

    def device(self, *, app=None, client=None, machine_id="A", config=None, identity=None,
               apply_config=None, has_slice=None):
        """A device wired the way the output-device plugin wires it."""
        client = client if client is not None else self.client()
        app = app if app is not None else self.qt.Application()
        config = config if config is not None else self.printer_config()
        device = self.qt.load("MoonrakerOutputDevice").MoonrakerOutputDevice(
            app, machine_id, client=client, config=lambda: config,
            apply_config=self.applied.append if apply_config is None else apply_config,
            active_identity=(lambda: (machine_id, "Printer " + machine_id)) if identity is None else identity,
            has_slice=has_slice)
        self.devices.append(device)
        return device

    def plugin(self, app, follower):
        plugin = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        self.addCleanup(plugin.stop)
        return plugin

    def capture_popups(self):
        """Route the adapter's UM.Message through the recording double."""
        patcher = patch.object(self.qt.load("MoonrakerOutputDevice"), "Message", Popup)
        patcher.start()
        self.addCleanup(patcher.stop)
        return Popup.instances

    @staticmethod
    def log_texts(logger, level):
        return [call.args[1] for call in logger.log.call_args_list
                if call.args and call.args[0] == level]


class OutputDevicePluginTests(OutputDeviceTestCase):
    """The plugin: device ownership across refreshes, machines and sessions."""

    def setUp(self):
        super().setUp()
        self.module = self.qt.load("MoonrakerOutputDevicePlugin")
        self.device_module = self.qt.load("MoonrakerOutputDevice")
        self.monitor_module = self.qt.load("MoonrakerMonitorModel")
        self.logger = self.module.Logger

    def test_an_application_without_the_stack_signal_starts_idle(self):
        follower = self.follower(self.client(), self.printer_config())
        # A host that does not publish stack changes: construction must survive it.
        app = SimpleNamespace(getGlobalContainerStack=lambda: None)
        plugin = self.module.MoonrakerOutputDevicePlugin(app, follower)

        plugin.start()

        self.assertIsNone(plugin._current)
        self.assertEqual(plugin._devices, {})

    def test_stop_gates_the_stack_handler_and_restart_recovers(self):
        # E (the 2026-09-19 review): stop must really stop — a stack
        # change after stop reinstalls nothing; start again registers
        # exactly once.
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()
        self.assertIsNotNone(plugin._current)
        plugin.stop()
        self.assertIsNone(plugin._current)
        plugin.refresh()  # the stack handler firing after stop
        self.assertIsNone(plugin._current, "a post-stop refresh must not reinstall")
        # The cached devices stay CACHED but inactive (F3's
        # distinction: inactive and destroyed are different states).
        for device in plugin._devices.values():
            monitor = getattr(device, "activePrinter", None)
            self.assertIsNotNone(monitor)
            self.assertFalse(monitor._data._active)
        plugin.start()
        self.assertIsNotNone(plugin._current, "restart registers again")

    def test_refresh_installs_one_real_monitor_per_machine(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        attach = Mock()
        follower.notice = lambda: SimpleNamespace(attach_model=attach)
        plugin = self.plugin(app, follower)

        plugin.refresh()

        device = plugin._current
        manager = plugin.getOutputDeviceManager()
        self.assertEqual(device.getId(), "MoonrakerPrintFollower@A")
        self.assertEqual(plugin._devices, {"A": device})
        manager.addOutputDevice.assert_called_once_with(device)
        monitor = device.activePrinter
        self.assertIsInstance(monitor, self.monitor_module.MoonrakerMonitorModel)
        self.assertEqual((monitor.name, monitor.uniqueName, monitor.buildplate), ("A", "A", "glass"))
        self.assertEqual(device.getName(), "Printer A")
        self.assertTrue(device._monitor_view_qml_path.endswith("MoonrakerMonitorBedMesh.qml"))
        attach.assert_called_once_with(monitor)

        plugin.refresh()  # the same machine reuses the served instance and its monitor
        self.assertIs(plugin._current, device)
        self.assertIs(device.activePrinter, monitor)
        manager.addOutputDevice.assert_called_once_with(device)

    def test_the_monitor_is_wired_to_the_follower_capabilities(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        monitor = plugin._current.activePrinter
        follower.bed_mesh.snapshot = {"minimum": 0.0, "maximum": 10.0}

        follower.presentation.bedMeshThresholdsRequested.emit(0.2, 0.8)
        self.assertEqual(follower.bed_mesh.thresholds, (0.2, 0.8))

        follower.presentation.printPauseRequested.emit()
        # The strip's control runs the pause lane's revalidation; an unknown
        # state refuses with the policy's words rather than a raw command.
        self.assertTrue(monitor._commands.status.startswith("Pause refused:"))

        monitor.previewBlockChanged.emit({"remainder": 12})
        self.assertEqual(follower.blocks, [{"remainder": 12}])

    def test_the_pause_verdicts_are_wired_to_the_presentation(self):
        # The single authority (the debt pack's two-clock
        # unification): the model's verdict change pushes the strip's
        # six properties through the presentation to every card.
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        monitor = plugin._current.activePrinter
        pushes = []
        with patch.object(follower.presentation, "publish_pause_verdicts",
                          side_effect=lambda *args: pushes.append(args)):
            monitor.actionChanged.emit()
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0][:2], (monitor.canPausePrint, monitor.canResumePrint))
        self.assertEqual(pushes[0][2:4], (monitor.pauseReason, monitor.resumeReason))
        self.assertEqual(pushes[0][4:], (monitor.pauseReasonDetail, monitor.resumeReasonDetail))

    def test_session_invalidation_deactivates_every_installed_device(self):
        app = self.qt.Application()
        client = self.client()
        follower = self.follower(client, self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.refresh()
        device = plugin._current
        monitor = device.activePrinter
        dialogs = DialogFactory(app)
        finished = []
        device.writeFinished.connect(finished.append)

        device.requestWrite(None, "part.gcode")  # an upload is in flight
        self.assertTrue(device._upload.busy)

        client.configure("http://printer-b", "new-key", 750)
        self.qt.events(10)

        self.assertFalse(device._upload.busy)
        self.assertEqual(finished, [device])  # retired, and not as an error
        self.assertEqual(dialogs.created[0].deleted, 1)
        self.assertFalse(monitor._data._active)  # the monitor stopped polling

    def test_stop_retires_the_device_it_registered(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        device = plugin._current
        manager = plugin.getOutputDeviceManager()

        plugin.stop()

        self.assertIsNone(plugin._current)
        manager.removeOutputDevice.assert_called_once_with(device.getId())
        self.assertFalse(device._upload.busy)

    def test_stop_survives_a_manager_that_already_dropped_the_device(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        plugin.getOutputDeviceManager().removeOutputDevice.side_effect = RuntimeError("unknown device")

        plugin.stop()

        self.assertIsNone(plugin._current)

    def test_a_vanished_stack_retires_the_device(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        device = plugin._current
        manager = plugin.getOutputDeviceManager()
        manager.removeOutputDevice.side_effect = RuntimeError("already gone")

        app.stack = None
        plugin.refresh()

        self.assertIsNone(plugin._current)
        manager.removeOutputDevice.assert_called_once_with(device.getId())

    def test_only_http_urls_with_a_host_are_servable(self):
        usable = self.module.MoonrakerOutputDevicePlugin._usable_url
        for value in ("http://printer-a", "https://printer-a:80/ms"):
            self.assertTrue(usable(value), value)
        for value in ("", "http://", "ftp://printer-a", "printer-a"):
            self.assertFalse(usable(value), value)

    def test_an_unusable_url_retires_the_device_until_the_config_is_fixed(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        device = plugin._current
        manager = plugin.getOutputDeviceManager()

        follower.set_config(self.printer_config(url="http://"))  # no host: nothing to serve
        plugin.refresh()

        self.assertIsNone(plugin._current)
        manager.removeOutputDevice.assert_called_once_with(device.getId())

    def test_a_machine_switch_replaces_the_device_and_its_monitor(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        first = plugin._current
        monitor = first.activePrinter
        manager = plugin.getOutputDeviceManager()

        app.stack = self.qt.Machine("B")
        follower._identity = lambda: ("B", "Printer B")
        plugin.refresh()

        second = plugin._current
        self.assertIsNot(second, first)
        self.assertEqual(second.getId(), "MoonrakerPrintFollower@B")
        self.assertEqual(plugin._devices, {"A": first, "B": second})
        self.assertEqual(second.getName(), "Printer B")
        # The outgoing device released the transport before the incoming one claimed it.
        self.assertFalse(monitor._data._active)
        manager.removeOutputDevice.assert_called_once_with(first.getId())

    def test_an_outgoing_instance_is_retired_even_when_the_cache_lost_it(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        outgoing = plugin._current
        manager = plugin.getOutputDeviceManager()
        manager.removeOutputDevice.side_effect = RuntimeError("already gone")
        plugin._devices.clear()  # the plugin no longer maps the instance it serves

        plugin.refresh()

        self.assertIsNot(plugin._current, outgoing)
        self.assertEqual(plugin._current.getId(), "MoonrakerPrintFollower@A")
        self.assertEqual(manager.addOutputDevice.call_count, 2)
        manager.removeOutputDevice.assert_called_once_with(outgoing.getId())
        self.assertFalse(outgoing.activePrinter._data._active)

    def test_a_broken_binding_never_escapes_refresh(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        follower.current_printer_config = Mock(side_effect=RuntimeError("no binding"))
        plugin = self.module.MoonrakerOutputDevicePlugin(app, follower)
        self.addCleanup(plugin.stop)
        plugin.start()  # armed; the broken binding makes the refresh fail

        plugin.refresh()

        self.assertIsNone(plugin._current)
        self.assertTrue(any("output-device refresh failed" in text
                            for text in self.log_texts(self.logger, "e")))

    def test_monitor_activation_never_raises_out_of_the_plugin(self):
        activate = self.module.MoonrakerOutputDevicePlugin._set_monitor_active
        activate(SimpleNamespace(activePrinter=SimpleNamespace()), True)  # no such capability

        setter = Mock(side_effect=RuntimeError("wrapped object deleted"))
        activate(SimpleNamespace(activePrinter=SimpleNamespace(setMonitoringActive=setter)), True)
        setter.assert_called_once_with(True)

    def test_a_device_that_cannot_deactivate_is_logged_not_fatal(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)

        plugin._deactivate_device(SimpleNamespace())  # nothing to release
        broken = SimpleNamespace(deactivate=Mock(side_effect=RuntimeError("gone")))
        plugin._deactivate_device(broken)

        broken.deactivate.assert_called_once_with()
        self.assertEqual(self.logger.logException.call_args.args[0], "e")
        self.assertIn("deactivation failed", self.logger.logException.call_args.args[1])

    def test_a_stack_without_an_extruder_count_still_yields_a_monitor(self):
        app = self.qt.Application()
        stack = app.stack

        class Picky:
            def getId(self): return stack.getId()
            def getName(self): return stack.getName()
            def getProperty(self, name, role):
                if name == "machine_extruder_count":
                    raise RuntimeError("not a container stack")
                return stack.getProperty(name, role)

        app.stack = Picky()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)

        plugin.refresh()

        monitor = plugin._current.activePrinter
        self.assertIsInstance(monitor, self.monitor_module.MoonrakerMonitorModel)
        self.assertEqual(monitor.name, "A")
        self.assertTrue(plugin.getOutputDeviceManager().addOutputDevice.called)

    def test_a_failing_identity_refresh_leaves_the_monitor_serving(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        device = plugin._current
        monitor = device.activePrinter

        class Hostile:
            def getName(self): raise RuntimeError("stack went away")
            def getId(self): return "A"
            def getProperty(self, name, role): return None

        plugin._install_monitor(device, Hostile())

        self.assertIs(device.activePrinter, monitor)  # the cached monitor is untouched
        self.assertTrue(device._monitor_view_qml_path.endswith(".qml"))

    def test_a_monitor_that_cannot_refresh_is_still_installed(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        plugin = self.plugin(app, follower)
        plugin.start()  # the production lifecycle arms the running gate
        plugin.refresh()
        device = plugin._current
        device.activePrinter.refreshAll = Mock(side_effect=RuntimeError("no data"))
        self.logger.log.reset_mock()

        plugin._install_monitor(device, app.stack)

        self.assertTrue(any("Monitor refresh failed" in text
                            for text in self.log_texts(self.logger, "w")))

    def test_a_follower_without_a_notice_is_served_anyway(self):
        app = self.qt.Application()
        follower = self.follower(self.client(), self.printer_config())
        self.assertIsNone(follower.notice)  # production: no notice to attach to

        plugin = self.plugin(app, follower)
        plugin.refresh()

        self.assertIsNotNone(plugin._current)


class OutputDeviceAdapterTests(OutputDeviceTestCase):
    """The device: metadata, the upload lifecycle and its user-facing feedback."""

    def setUp(self):
        super().setUp()
        self.module = self.qt.load("MoonrakerOutputDevice")
        self.logger = self.module.Logger

    def test_device_metadata_follows_the_binding_identity(self):
        device = self.device()
        for name in ("setName", "setDescription", "setShortDescription", "setIconName",
                     "setConnectionText", "setPriority"):
            setattr(device, name, Mock())

        device.updateConfig(lambda: ("A", "Kappa"))

        device.setName.assert_called_once_with("Kappa")
        device.setDescription.assert_called_once_with("Upload to Kappa")
        device.setShortDescription.assert_called_once_with("Upload to Kappa")
        device.setIconName.assert_called_once_with("print")
        device.setConnectionText.assert_called_once_with("Connected via Moonraker")
        device.setPriority.assert_called_once_with(5)

        device.updateConfig(Mock(side_effect=RuntimeError("binding lost")))
        device.setName.assert_called_with("Printer")

    def test_the_qml_upload_properties_track_the_upload_controller(self):
        device = self.device()

        self.assertEqual(device.initialUploadPath, "<root>")
        self.assertEqual(device.initialUploadFilename, "")
        self.assertFalse(device.initialStartPrint)
        self.assertEqual(list(device.uploadPathOptions.value()), ["<root>"])

    def test_a_stack_that_cannot_report_extruders_still_builds_the_device(self):
        app = self.qt.Application()
        app.stack = SimpleNamespace(getProperty=Mock(side_effect=RuntimeError("no definition")))

        device = self.device(app=app)

        self.assertEqual(len(device._printers), 1)
        self.assertIsNotNone(device.activePrinter)

    def test_the_dispatch_gate_reads_the_installed_monitor(self):
        Observation = self.qt.load("MonitorPermissions").Observation
        device = self.device()
        # No monitor page opened yet: the upload-and-print flow must still work.
        self.assertIsNone(device._print_verdict())

        def observation(**overrides):
            values = dict(active=False, connection="yes", state="idle", homed_axes="xyz",
                          assumed_stopped=False, save_config_pending=False, controls_locked=False,
                          busy=False)
            values.update(overrides)
            return Observation(**values)

        device._printers = [SimpleNamespace(_data=SimpleNamespace(observation=observation()))]
        self.assertEqual(device._print_verdict().mode, "allowed")

        device._printers = [SimpleNamespace(
            _data=SimpleNamespace(observation=observation(connection="no")))]
        self.assertEqual(device._print_verdict().mode, "disabled")

    def test_leaving_the_monitor_stage_returns_to_the_sliced_view(self):
        app = self.qt.Application()
        unsliced = self.device(app=app)  # no slice capability at all
        unsliced.leaveMonitorStage()
        self.assertEqual(app.controller.stage, "PrepareStage")

        empty_plate = self.device(app=app, has_slice=lambda: False)
        empty_plate.leaveMonitorStage()
        self.assertEqual(app.controller.stage, "PrepareStage")

        sliced = self.device(app=app, has_slice=lambda: True)
        sliced.leaveMonitorStage()
        self.assertEqual(app.controller.stage, "PreviewStage")

    def test_leaving_the_monitor_stage_collapses_the_console(self):
        device = self.device()
        monitor = SimpleNamespace(expanded=None,
                                  setConsoleExpanded=lambda value: setattr(monitor, "expanded", value))
        device._printers = [monitor]

        device.leaveMonitorStage()
        self.assertIs(monitor.expanded, False)

        device._printers = [SimpleNamespace(setConsoleExpanded=Mock(side_effect=RuntimeError("deleted")))]
        device.leaveMonitorStage()  # logged, never raised
        self.assertTrue(any("console expansion" in text
                            for text in self.log_texts(self.logger, "w")))

    def test_a_failing_stage_switch_is_logged_not_raised(self):
        device = self.device()
        device._application = SimpleNamespace(getController=Mock(side_effect=RuntimeError("no controller")))

        device.leaveMonitorStage()

        self.assertTrue(any("could not leave the monitor stage" in text
                            for text in self.log_texts(self.logger, "w")))

    def readiness_poll(self, client, klippy_state):
        """Answer the next readiness poll; the ladder re-arms on a timer."""
        seen = len([r for r in client.transport.requests if r.path.startswith("server/info")])
        for _ in range(40):
            self.qt.events(20)
            polls = [r for r in client.transport.requests if r.path.startswith("server/info")]
            if len(polls) > seen:
                polls[-1].callback({"result": {"klippy_state": klippy_state}}, "")
                return
        raise AssertionError("the upload controller never polled for readiness")

    def run_start_upload(self, body, **config_overrides):
        """The dialog -> readiness -> multipart ladder of an upload-with-start."""
        app = self.qt.Application()
        client = self.client()
        config = self.printer_config(upload_dialog=True, upload_start_print=True,
                                     ready_retry_interval_s=0.05, **config_overrides)
        device = self.device(app=app, client=client, config=config)
        DialogFactory(app)
        reply = UploadReply(body=body)
        client.transport.network = SimpleNamespace(post=lambda request, multipart: reply)
        statuses = []
        device._upload.status.connect(statuses.append)

        device.requestWrite(None, "part.gcode")
        device.acceptUpload("<root>", "part.gcode", True)
        self.readiness_poll(client, "startup")  # not ready yet: the ladder reports and retries
        self.readiness_poll(client, "ready")
        reply.uploadProgress.emit(50, 100)
        reply.finished.emit()
        self.qt.events(20)
        self.assertFalse(device._upload.busy)
        return device, statuses

    def test_an_upload_that_starts_a_print_names_the_outcome(self):
        self.capture_popups()
        for body, outcome, suffix in (
            ({"result": {}, "print_started": True}, "started", " and started the print"),
            ({"result": {}, "print_started": False, "print_queued": True}, "queued", " and queued the print"),
        ):
            Popup.instances.clear()
            device, statuses = self.run_start_upload(body)

            self.assertEqual(device._upload.print_outcome, outcome)
            self.assertEqual(statuses, ["Waiting for printer readiness (1/21)", "Uploading part.gcode"])
            self.assertEqual(len(Popup.instances), 2)  # one status popup, reused; one completion
            status = Popup.instances[0]
            self.assertEqual(status.title, "Moonraker")
            self.assertEqual((status.shown, status.hidden, status.progress), (1, 1, 50))
            message = Popup.instances[-1]
            self.assertEqual(message.text, f"Uploaded 'part.gcode' to Printer A{suffix}.")
            self.assertEqual(message.lifetime, 0)  # autohide is off by default
            self.assertEqual([action[0] for action in message.actions], ["open_browser"])

    def test_the_completion_button_opens_the_configured_frontend(self):
        self.capture_popups()
        device, _statuses = self.run_start_upload({"result": {}, "print_started": True})
        message = Popup.instances[-1]
        desktop = Mock(openUrl=Mock(return_value=True))

        with patch.object(self.module, "QDesktopServices", desktop):
            message.actionTriggered.emit(message, "open_browser")

        self.assertEqual(desktop.openUrl.call_args.args[0].toString(),
                         device._upload.config.frontend_target)

    def test_the_completion_popup_autohides_only_when_configured(self):
        self.capture_popups()

        self.run_start_upload({"result": {}, "print_started": True}, upload_autohide_message=True)

        self.assertEqual(Popup.instances[-1].lifetime, 30)

    def test_only_the_browser_action_is_answered(self):
        device = self.device()
        desktop = Mock(openUrl=Mock(return_value=True))

        with patch.object(self.module, "QDesktopServices", desktop):
            device._on_message_action(None, "something_else")
            desktop.openUrl.assert_not_called()

            device._browser_target = "http://printer-a/#/files"
            device._on_message_action(None, "open_browser")
            self.assertEqual(desktop.openUrl.call_args.args[0].toString(), "http://printer-a/#/files")

            desktop.openUrl.return_value = False
            device._on_message_action(None, "open_browser")
            self.assertTrue(any(text.endswith("browser for %s")
                                for text in self.log_texts(self.logger, "w")))

            desktop.openUrl.side_effect = RuntimeError("no desktop")
            device._on_message_action(None, "open_browser")
            self.assertTrue(any(text.endswith("browser: %s")
                                for text in self.log_texts(self.logger, "w")))

    def test_the_browser_action_falls_back_to_the_configured_frontend(self):
        app = self.qt.Application()
        device = self.device(app=app, config=self.printer_config(frontend_url="http://printer-a/#/files"))
        DialogFactory(app)
        desktop = Mock(openUrl=Mock(return_value=True))

        device.requestWrite(None, "part.gcode")  # no target captured on the message yet
        with patch.object(self.module, "QDesktopServices", desktop):
            device._on_message_action(None, "open_browser")

        self.assertEqual(desktop.openUrl.call_args.args[0].toString(), "http://printer-a/#/files")

    def test_a_dialog_upload_reports_success_and_offers_the_browser(self):
        app = self.qt.Application()
        client = self.client()
        popups = self.capture_popups()
        device = self.device(app=app, client=client)
        dialogs = DialogFactory(app)
        reply = UploadReply(body={"result": {"item": {"path": "part.gcode"}}})
        client.transport.network = SimpleNamespace(post=lambda request, multipart: reply)
        written, finished, succeeded, progress = [], [], [], []
        device.writeStarted.connect(written.append)
        device.writeFinished.connect(finished.append)
        device.writeSuccess.connect(succeeded.append)
        device.writeProgress.connect(lambda sender, percent: progress.append(percent))

        device.requestWrite(None, "part.gcode")

        self.assertEqual(written, [device])  # Cura is told before the file is prepared
        self.assertEqual(len(dialogs.created), 1)
        self.assertTrue(dialogs.created[0].shown)
        self.assertTrue(device._upload.busy)

        device.acceptUpload("<root>", "part.gcode", False)
        self.qt.events(10)
        self.assertEqual(popups[-1].text, "Uploading part.gcode")
        reply.uploadProgress.emit(40, 100)
        reply.finished.emit()
        self.qt.events(10)

        self.assertFalse(device._upload.busy)
        self.assertEqual(finished, [device])
        self.assertEqual(succeeded, [device])
        self.assertEqual(progress, [40])
        self.assertEqual(popups[-1].text, "Uploaded 'part.gcode' to Printer A.")
        self.assertEqual(popups[-1].shown, 1)
        self.assertEqual(dialogs.created[0].deleted, 1)
        self.assertEqual(self.applied[-1].upload_path, "")

    def test_a_dialog_that_cannot_be_created_uploads_anyway(self):
        app = self.qt.Application()
        client = self.client()
        app.createQmlComponent = Mock(side_effect=RuntimeError("no QML engine"))
        device = self.device(app=app, client=client)
        reply = UploadReply(body={"result": {}})
        client.transport.network = SimpleNamespace(post=lambda request, multipart: reply)

        device.requestWrite(None, "part.gcode")

        self.assertIsNone(device._dialog)
        self.assertTrue(device._upload.busy)  # the upload went ahead without the dialog
        reply.finished.emit()
        self.qt.events(10)
        self.assertFalse(device._upload.busy)

    def test_a_silent_configuration_uploads_without_a_dialog(self):
        app = self.qt.Application()
        app.createQmlComponent = Mock()
        device = self.device(app=app, config=self.printer_config(upload_dialog=False))

        device.requestWrite(None, "part.gcode")

        app.createQmlComponent.assert_not_called()
        self.assertIsNone(device._dialog)
        self.assertTrue(device._upload.busy)

    def test_a_second_write_while_the_dialog_is_open_is_refused(self):
        app = self.qt.Application()
        device = self.device(app=app)
        dialogs = DialogFactory(app)
        device.requestWrite(None, "part.gcode")

        with self.assertRaises(self.module.OutputDeviceError.DeviceBusyError):
            device.requestWrite(None, "part.gcode")

        self.assertEqual(len(dialogs.created), 1)  # no second dialog, no second upload

    def test_a_closed_dialog_releases_the_device_for_the_next_attempt(self):
        app = self.qt.Application()
        device = self.device(app=app)
        DialogFactory(app)
        device.requestWrite(None, "first.gcode")
        dialog = device._dialog
        dialog.visible = False  # the user dismissed it without choosing

        with self.assertRaises(self.module.OutputDeviceError.DeviceBusyError):
            device.requestWrite(None, "second.gcode")  # the deferred cancel has not landed yet

        self.qt.events(10)
        self.assertFalse(device._upload.busy)
        self.assertIsNone(device._dialog)
        self.assertEqual(dialog.deleted, 1)
        device.requestWrite(None, "second.gcode")  # the wedge is broken
        self.assertTrue(device._upload.busy)

    def test_a_destroyed_dialog_still_refuses_rather_than_crashing(self):
        app = self.qt.Application()
        device = self.device(app=app)
        DialogFactory(app)
        device.requestWrite(None, "part.gcode")

        class Destroyed:
            # A deleted QML object: every call raises, as Qt's wrapper does.
            def isVisible(self): raise RuntimeError("wrapped C/C++ object deleted")
            def deleteLater(self): raise RuntimeError("wrapped C/C++ object deleted")

        device._dialog = Destroyed()
        with self.assertRaises(self.module.OutputDeviceError.DeviceBusyError):
            device.requestWrite(None, "part.gcode")

    def test_a_write_for_an_inactive_printer_is_refused_before_it_starts(self):
        self.capture_popups()
        device = self.device(identity=lambda: ("B", "Printer B"))
        written = []
        device.writeStarted.connect(written.append)

        device.requestWrite(None, "part.gcode")

        self.assertEqual(written, [])  # Cura never saw a start
        self.assertFalse(device._upload.busy)
        self.assertEqual(Popup.instances[-1].title, "Moonraker - Error")
        self.assertIn("Only the active Cura printer", Popup.instances[-1].text)

    def test_an_unpreparable_file_reports_a_write_error(self):
        self.capture_popups()
        device = self.device()
        errors, finished, succeeded = [], [], []
        device.writeError.connect(errors.append)
        device.writeFinished.connect(finished.append)
        device.writeSuccess.connect(succeeded.append)

        device.requestWrite(None, "bad:name.gcode")
        self.qt.events(10)

        self.assertEqual(errors, [device])
        self.assertEqual(finished, [device])
        self.assertEqual(succeeded, [])
        self.assertFalse(device._upload.busy)
        self.assertIn("Could not prepare the Moonraker upload", Popup.instances[-1].text)

    def test_a_cancel_finishes_without_an_error_popup(self):
        popups = self.capture_popups()
        app = self.qt.Application()
        device = self.device(app=app)
        DialogFactory(app)
        finished = []
        device.writeFinished.connect(finished.append)

        device.requestWrite(None, "part.gcode")
        device.cancelUpload()
        self.qt.events(10)

        self.assertEqual(finished, [device])
        self.assertFalse(device._upload.busy)
        self.assertEqual([popup.title for popup in popups if popup.title == "Moonraker - Error"], [])

    def test_status_lines_reuse_one_popup_and_progress_reaches_cura(self):
        popups = self.capture_popups()
        device = self.device()
        progress = []
        device.writeProgress.connect(lambda sender, percent: progress.append(percent))

        device._status("Uploading part.gcode")
        self.assertEqual(len(popups), 1)
        self.assertEqual((popups[0].title, popups[0].text, popups[0].shown), ("Moonraker", "Uploading part.gcode", 1))

        device._status("Waiting for printer readiness (1/21)")
        self.assertEqual(len(popups), 1)  # one popup, not a stack
        self.assertEqual(popups[0].text, "Waiting for printer readiness (1/21)")

        device._progress(60)
        self.assertEqual(progress, [60])
        self.assertEqual(popups[0].progress, 60)

        device._finished(False, "")  # the cancel terminal clears the popup
        self.assertIsNone(device._message)
        self.assertEqual(popups[0].hidden, 1)
        device._progress(70)  # nothing left to update: the signal still rides
        self.assertEqual(progress, [60, 70])

    def test_a_failed_write_shows_the_words_and_reports_the_error(self):
        popups = self.capture_popups()
        device = self.device()
        errors, finished = [], []
        device.writeError.connect(errors.append)
        device.writeFinished.connect(finished.append)

        device._finished(False, "No space left on device")

        self.assertEqual(errors, [device])
        self.assertEqual(finished, [device])
        self.assertEqual(popups[-1].title, "Moonraker - Error")
        self.assertEqual(popups[-1].text, "No space left on device")

    def test_accepted_choices_are_remembered_in_the_printer_config(self):
        def build(config):
            """A device whose config follows what the host last applied."""
            box = {"config": config}

            def apply(updated):
                box["config"] = updated
                self.applied.append(updated)

            device = self.module.MoonrakerOutputDevice(
                self.qt.Application(), "A", client=self.client(), config=lambda: box["config"],
                apply_config=apply, active_identity=lambda: ("A", "Printer A"))
            self.devices.append(device)
            return device

        device = build(self.printer_config(upload_remember_state=True, upload_paths=["existing"]))

        device._remember("newfolder", True)

        updated = self.applied[-1]
        self.assertEqual(updated.upload_paths, ["existing", "newfolder"])
        self.assertEqual(updated.upload_path, "newfolder")
        self.assertTrue(updated.upload_start_print)

        device._remember("newfolder", False)  # already listed: not duplicated
        self.assertEqual(self.applied[-1].upload_paths, ["existing", "newfolder"])

        plain = build(self.printer_config(upload_paths=[]))
        plain._remember("newfolder", True)  # remembering the choice is not opted in
        self.assertEqual(self.applied[-1].upload_paths, ["newfolder"])
        self.assertEqual(self.applied[-1].upload_path, "")
        self.assertFalse(self.applied[-1].upload_start_print)

    def test_a_config_that_cannot_be_saved_never_blocks_the_upload(self):
        device = self.device(apply_config=Mock(side_effect=RuntimeError("prefs locked")))

        device._remember("newfolder", False)

        self.assertTrue(any("could not remember upload choices" in text
                            for text in self.log_texts(self.logger, "w")))

    def test_a_destroyed_dialog_is_forgotten_without_raising(self):
        device = self.device()

        class Destroyed:
            def deleteLater(self): raise RuntimeError("deleted")

        device._dialog = Destroyed()
        device._release_dialog()
        self.assertIsNone(device._dialog)
        device._release_dialog()  # nothing left to release


if __name__ == "__main__":
    unittest.main()
