"""Coverage for the what's-new overlay's window owner, the upload
controller and the camera bridge.

The rest of the suite pins these three modules' happy paths; these
tests drive the branches that carry the failures: the overlay's
once-per-version offer (retry, give-up, the mount on Cura's stored
engine), the upload's folder walk, print gate, readiness ladder and
upload-verdict reads, and the bridge's relay lifecycle (request
parsing guards, upstream refusals, teardown).

No line of the three modules is left over. One thing is deliberately
not instantiated: the overlay's Popup. This process owns a
QCoreApplication, and a Popup cannot live without a window (a QWindow
qFatal's without a QGuiApplication; a windowless Popup segfaults), so
the document is compiled on the real engine and its mount is driven
through a component double. The capture and QML gates mount the real
document in a real window.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, ScriptedTransport, runtime

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
QML_STUBS = ROOT / "tests" / "qml_stubs"

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtQml import QQmlComponent

    class MonitorStub(QObject):
        """The monitor model's what's-new surface (signal + QML reads)."""

        whatsNewRequested = pyqtSignal()

        def __init__(self):
            super().__init__()
            from plugins.WhatsNew import entries
            self.checks = 0
            self.dismissals = 0
            self._content = entries()

        @pyqtProperty(QVariant, constant=True)
        def whatsNewContent(self):
            return self._content

        def checkWhatsNew(self):
            self.checks += 1

        @pyqtSlot()
        def dismissWhatsNew(self):
            self.dismissals += 1

    class RecordingTimer:
        """Captures the overlay's deferred work; nothing ever fires."""

        def __init__(self):
            self.calls = []

        def singleShot(self, delay, callback):
            self.calls.append((delay, callback))

    class RecordingMetaObject:
        def __init__(self):
            self.calls = []

        def invokeMethod(self, target, name, *args):
            self.calls.append((target, name))

    class FakeWindow:
        """A window as the overlay sees one: geometry, visibility, and
        the content item that marks it as a QML window (a plain QWindow
        has none)."""

        def __init__(self, width, height, visible=True, content=True):
            self._width, self._height, self._visible = width, height, visible
            if content:
                self.contentItem = lambda: object()

        def width(self):
            return self._width

        def height(self):
            return self._height

        def isVisible(self):
            return self._visible

    class FakeReply(QObject):
        """A QNetworkReply double: the signals both modules connect and
        the reads they make off a landed reply."""

        uploadProgress = pyqtSignal(int, int)
        readyRead = pyqtSignal()
        finished = pyqtSignal()

        def __init__(self, body=b"", error=None, error_string="simulated"):
            super().__init__()
            self._body, self._error, self._error_string = body, error, error_string
            self.deleted = 0
            self.aborts = 0
            self.buffer_size = None

        def setReadBufferSize(self, size):
            self.buffer_size = size

        def error(self):
            from PyQt6.QtNetwork import QNetworkReply
            return self._error or QNetworkReply.NetworkError.NoError

        def errorString(self):
            return self._error_string

        def readAll(self):
            return self._body

        def isRunning(self):
            return False

        def abort(self):
            self.aborts += 1

        def deleteLater(self):
            self.deleted += 1

    class DeadReply:
        """A reply whose Qt core is gone: releasing it raises, like the
        real object does once its owner (the NAM) has destroyed it."""

        def abort(self):
            raise RuntimeError("wrapped C/C++ object has been deleted")

        def deleteLater(self):
            raise RuntimeError("wrapped C/C++ object has been deleted")

        def error(self):
            from PyQt6.QtNetwork import QNetworkReply
            return QNetworkReply.NetworkError.NoError

        def errorString(self):
            return ""

        def attribute(self, _name):
            return None


class Source:
    """The prepared-file lease the adapter hands the controller."""

    def __init__(self, path, filename):
        self.path, self.filename, self.closes = path, filename, 0

    def close(self):
        self.closes += 1
        try:
            os.remove(self.path)
        except OSError:
            pass


def _enter(test, context):
    """TestCase.enterContext is 3.11+; the 3.10 leg needs the hand-rolled form."""
    test.addCleanup(context.__exit__, None, None, None)
    return context.__enter__()


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class WhatsNewOverlayTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.module = self.qt.load("WhatsNewOverlay")
        self.logger = sys.modules["UM.Logger"].Logger

    def _overlay(self):
        """An overlay whose offer timer is recorded, never armed."""
        timers = RecordingTimer()
        _enter(self, patch.object(self.module, "QTimer", timers))
        overlay = self.module.WhatsNewOverlay()
        overlay.timers = timers
        return overlay

    def test_close_neuters_queued_offers_and_destroys_a_live_popup(self):
        # F1 (the 2026-09-19 review): close() must make every queued
        # callback inert — no window lookup, no retry — and destroy a
        # live popup rather than merely dropping the reference.
        overlay = self._overlay()
        self.assertEqual(len(overlay.timers.calls), 1)  # the boot offer
        overlay.close()
        self._install_windows([])
        self._install_devices([])
        overlay._offer()
        self.assertEqual(overlay._attempts, 0, "the closed offer bails before any work")
        self.assertEqual(len(overlay.timers.calls), 1, "no retry is scheduled")

        class FakePopup:
            def __init__(self):
                self.deleted = False

            def deleteLater(self):
                self.deleted = True

        popup = FakePopup()
        overlay._overlay = popup
        overlay.close()
        self.assertTrue(popup.deleted)
        self.assertIsNone(overlay._overlay)

    def _install_windows(self, windows):
        _enter(self, patch.object(
            self.module, "QGuiApplication", SimpleNamespace(allWindows=lambda: list(windows))))

    def _install_devices(self, devices):
        application = SimpleNamespace(
            getOutputDeviceManager=lambda: SimpleNamespace(getOutputDevices=lambda: list(devices)))
        _enter(self, patch.object(
            sys.modules["UM.Application"], "Application",
            SimpleNamespace(getInstance=lambda: application)))

    @staticmethod
    def _device(class_name, monitor):
        device = type(class_name, (), {})()
        device.activePrinter = monitor
        return device

    def _engine(self, *, stubs):
        from PyQt6.QtQml import QQmlEngine
        engine = QQmlEngine()
        if stubs:
            engine.addImportPath(str(QML_STUBS))
        engine.addImportPath(str(PLUGINS))
        engine.rootContext().setContextProperty("screenScaleFactor", 1.0)
        self.addCleanup(engine.deleteLater)
        return engine

    def _component_double(self, width=520, height=400):
        """QQmlComponent double: records the load, yields an overlay.

        The shipped document compiles for real in its own test, but it
        is never instantiated here: a Popup needs a window, and this
        process has no QGuiApplication to build one on (QWindow
        qFatal's without one; a windowless Popup segfaults). The
        capture and QML gates mount it with a real window.
        """
        instances = []

        class ComponentDouble:
            def __init__(self, engine):
                self.engine, self.created = engine, None
                instances.append(self)

            def setData(self, source, url):
                self.source, self.url = bytes(source), url

            def createWithInitialProperties(self, properties):
                self.properties = properties
                self.created = QObject()
                self.created.setProperty("width", width)
                self.created.setProperty("height", height)
                return self.created

            def errorString(self):
                return "double"

            def errors(self):
                return []

        _enter(self, patch.object(self.module, "QQmlComponent", ComponentDouble))
        return instances

    def _qml_engine_stub(self, engine):
        package = ModuleType("UM.Qt")
        package.__path__ = []
        module = ModuleType("UM.Qt.QtApplication")
        module.QtApplication = SimpleNamespace(getInstance=lambda: SimpleNamespace(_qml_engine=engine))
        _enter(self, patch.dict(sys.modules, {"UM.Qt": package, "UM.Qt.QtApplication": module}))

    def _quiet_qt_messages(self):
        from PyQt6.QtCore import qInstallMessageHandler
        previous = qInstallMessageHandler(lambda *args: None)
        self.addCleanup(qInstallMessageHandler, previous)

    def test_the_target_window_is_the_largest_qml_window(self):
        # Cura keeps popup windows alive and native QWindows exist too;
        # only a QML window carries an engine on its content item, so a
        # larger native window must never win the offer.
        native = FakeWindow(4000, 4000, content=False)
        small, large = FakeWindow(300, 200), FakeWindow(900, 700)
        self._install_windows([native, small, large])
        self.assertIs(self._overlay()._find_main_window(), large)
        self._install_windows([])
        self.assertIsNone(self._overlay()._find_main_window())

    def test_the_monitor_is_found_only_on_a_moonraker_device(self):
        wanted = object()
        self._install_devices([
            self._device("OtherOutputDevice", object()),
            self._device("MoonrakerOutputDevicePlugin", None),
            self._device("MoonrakerOutputDevicePlugin", wanted),
        ])
        self.assertIs(self._overlay()._find_monitor(), wanted)
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", None)])
        self.assertIsNone(self._overlay()._find_monitor())

    def test_the_offer_waits_for_a_visible_window_and_a_monitor(self):
        overlay = self._overlay()
        self.assertEqual(overlay.timers.calls, [(1500, overlay._offer)])
        # Neither half exists yet: retry, and say what is missing once.
        self._install_windows([])
        self._install_devices([])
        overlay._offer()
        self.assertEqual(overlay._attempts, 1)
        self.assertEqual(overlay.timers.calls[-1], (1000, overlay._offer))
        level, message = self.logger.log.call_args[0][:2]
        self.assertEqual(level, "i")
        self.assertIn("waits", message)
        # A window that exists but is not yet visible is not a window.
        self._install_windows([FakeWindow(900, 700, visible=False)])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", object())])
        overlay._offer()
        self.assertEqual(overlay.timers.calls[-1], (1000, overlay._offer))

    def test_the_offer_gives_up_after_the_bounded_retries(self):
        # A session whose Cura never finishes booting gets no popup —
        # the bound is what ends the timer chain.
        overlay = self._overlay()
        self._install_windows([])
        self._install_devices([])
        overlay._attempts = 299
        overlay._offer()
        self.assertEqual(overlay._attempts, 300)
        self.assertEqual(len(overlay.timers.calls), 1)  # no further retry
        level, message = self.logger.log.call_args[0][:2]
        self.assertEqual(level, "w")
        self.assertIn("gave up", message)

    def test_the_offer_checks_the_monitor_once_the_window_is_up(self):
        overlay = self._overlay()
        model = MonitorStub()
        self._install_windows([FakeWindow(900, 700)])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", model)])
        shown = []
        overlay._show = lambda: shown.append(1)

        overlay._offer()

        self.assertEqual(model.checks, 1)
        # The connection is what wires the monitor's request to the popup.
        model.whatsNewRequested.emit()
        self.assertEqual(shown, [1])

    def test_the_offer_never_raises_out_of_its_timer(self):
        # An unhandled raise in a timer callback takes Cura down.
        overlay = self._overlay()

        def explode():
            raise RuntimeError("a stub application")

        overlay._find_main_window = explode

        overlay._offer()

        self.assertEqual(self.logger.log.call_args[0][0], "e")

    def test_the_overlay_document_compiles_on_the_real_engine(self):
        # The shipped document against the real engine and the stub UM
        # surface: a dropped import, a renamed property or a binding to
        # a name the theme does not export fails here, not at offer
        # time. Instantiation is the capture and QML gates' job.
        engine = self._engine(stubs=True)
        path = os.path.join(str(PLUGINS), "WhatsNewOverlay.qml")
        component = QQmlComponent(engine)
        with open(path, encoding="utf-8") as handle:
            component.setData(handle.read().encode("utf-8"), QUrl.fromLocalFile(path))

        self.assertFalse(component.isError(), [error.toString() for error in component.errors()])

    def test_the_overlay_mounts_on_cura_stored_engine(self):
        engine = self._engine(stubs=True)
        self._qml_engine_stub(engine)
        root = object()
        window = FakeWindow(900, 700)
        window.contentItem = lambda: root
        self._install_windows([window])
        model = MonitorStub()
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", model)])
        meta = RecordingMetaObject()
        _enter(self, patch.object(self.module, "QMetaObject", meta))
        instances = self._component_double(width=520, height=400)
        overlay = self._overlay()

        overlay._show()

        component = instances[0]
        # Cura's stored engine is used, not a lookup for this window.
        self.assertIs(component.engine, engine)
        self.assertEqual(component.url.toLocalFile(), os.path.join(str(PLUGINS), "WhatsNewOverlay.qml"))
        self.assertIn("Popup {", component.source.decode("utf-8"))
        # The monitor supplies the content through the initial property.
        self.assertIs(component.properties["model"], model)
        popup = overlay._overlay
        self.assertIs(popup, component.created)
        # Parented into the window's content item and centered on the
        # WINDOW's geometry (the item's own size lags its first layout).
        self.assertIs(popup.property("parent"), root)
        self.assertEqual(popup.property("x"), round((900 - 520) / 2))
        self.assertEqual(popup.property("y"), round((700 - 400) / 2))
        # A Popup root is a QObject: it opens through the meta-object.
        self.assertEqual([name for _target, name in meta.calls], ["open", "forceActiveFocus"])
        self.assertIs(meta.calls[0][0], popup)

    def test_the_overlay_opens_on_the_content_items_own_engine(self):
        # The qmlEngine() lookup returns null for Cura's main window on
        # some hosts, so the content item's engine is the fallback.
        engine = self._engine(stubs=True)
        _enter(self, patch.object(self.module, "qmlEngine", lambda _item: engine))
        window = FakeWindow(900, 700)
        self._install_windows([window])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", MonitorStub())])
        _enter(self, patch.object(self.module, "QMetaObject", RecordingMetaObject()))
        instances = self._component_double()
        overlay = self._overlay()

        overlay._show()

        self.assertIs(instances[0].engine, engine)
        self.assertIs(overlay._overlay, instances[0].created)

    def test_the_show_waits_when_the_window_or_the_monitor_is_missing(self):
        # The offer retries, so a half-ready host must cost nothing: no
        # component is built until both the window and the monitor are
        # there.
        self._install_windows([FakeWindow(900, 700)])
        self._install_devices([])
        instances = self._component_double()
        overlay = self._overlay()

        overlay._show()

        self.assertIsNone(overlay._overlay)
        self.assertEqual(instances, [])

    def test_the_overlay_closes_and_can_be_offered_again(self):
        # Unload drops the popup; a later session on the same host must
        # be able to mount a fresh one.
        engine = self._engine(stubs=True)
        self._qml_engine_stub(engine)
        self._install_windows([FakeWindow(900, 700)])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", MonitorStub())])
        _enter(self, patch.object(self.module, "QMetaObject", RecordingMetaObject()))
        instances = self._component_double()
        overlay = self._overlay()
        overlay._show()
        self.assertIsNotNone(overlay._overlay)

        overlay.close()
        self.assertIsNone(overlay._overlay)

        # F1's contract (the 2026-09-19 review): a closed overlay is
        # dead — a late _show mounts nothing, and no retry fires.
        overlay._show()
        self.assertEqual(len(instances), 1)
        self.assertIsNone(overlay._overlay)
        # A later session mounts a FRESH overlay instance instead.
        fresh = self._overlay()
        fresh._show()
        self.assertEqual(len(instances), 2)
        self.assertIs(fresh._overlay, instances[1].created)

    def test_the_overlay_refuses_to_mount_without_an_engine(self):
        self._quiet_qt_messages()
        _enter(self, patch.object(self.module, "qmlEngine", lambda _item: None))
        self._install_windows([FakeWindow(900, 700)])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", MonitorStub())])
        overlay = self._overlay()

        overlay._show()

        self.assertIsNone(overlay._overlay)
        level, message = self.logger.log.call_args[0][:2]
        self.assertEqual(level, "e")
        self.assertIn("no QML engine", message)

    def test_the_overlay_reports_a_document_that_fails_to_load(self):
        # An engine without the UM stubs cannot resolve the document's
        # imports; the failure must surface the component's own errors.
        self._quiet_qt_messages()
        engine = self._engine(stubs=False)
        _enter(self, patch.object(self.module, "qmlEngine", lambda _item: engine))
        self._install_windows([FakeWindow(900, 700)])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", MonitorStub())])
        overlay = self._overlay()

        overlay._show()

        self.assertIsNone(overlay._overlay)
        level, message = self.logger.log.call_args[0][:2]
        self.assertEqual(level, "e")
        self.assertIn("failed to load", message)

    def test_the_overlay_swallows_a_raising_component(self):
        # The macOS boot crashed inside the component load; the offer
        # must survive it.
        self._quiet_qt_messages()
        engine = self._engine(stubs=True)
        _enter(self, patch.object(self.module, "qmlEngine", lambda _item: engine))

        class Raising:
            def __init__(self, *_args):
                raise RuntimeError("import storm")

        _enter(self, patch.object(self.module, "QQmlComponent", Raising))
        self._install_windows([FakeWindow(900, 700)])
        self._install_devices([self._device("MoonrakerOutputDevicePlugin", MonitorStub())])
        overlay = self._overlay()

        overlay._show()

        self.assertIsNone(overlay._overlay)
        level, message = self.logger.log.call_args[0][:2]
        self.assertEqual(level, "e")
        self.assertIn("raised", message)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class UploadControllerCoverageTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.module = self.qt.load("UploadController")
        self.client_module = self.qt.load("MoonrakerClient")
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.verdict_type = self.qt.load("MonitorPermissions").Verdict
        self.directory = tempfile.mkdtemp(prefix="f45-upload-coverage-")
        self.addCleanup(shutil.rmtree, self.directory, True)

    def _client(self, url="http://printer-a"):
        client = self.client_module.MoonrakerClient(transport=ScriptedTransport())
        client.configure(url, "", 750)

        def stop():
            client.stop()
            client.transport.close()  # the pooled sockets close with the manager
        self.addCleanup(stop)
        return client

    def _controller(self, client=None, machine="A", identity=None, **config):
        client = client or self._client()
        config = self.config_type(url="http://printer-a", upload_dialog=True, **config)
        controller = self.module.UploadController(client, machine, identity or (lambda: ("A", "A")))
        self.addCleanup(controller.abort)
        controller.begin(config, "part.gcode")
        return controller

    def _prepared(self, name="part.gcode", body=b"G1 X0\n"):
        path = os.path.join(self.directory, name)
        with open(path, "wb") as handle:
            handle.write(body)
        return Source(path, name)

    def _install_upload_post(self, client, reply=None):
        """Intercept the multipart POST: what was sent, and the reply."""
        posted = []
        reply = reply or FakeReply()

        def post(request, multipart):
            posted.append(SimpleNamespace(request=request, multipart=multipart))
            return reply
        client.transport.network = SimpleNamespace(post=post)
        return posted, reply

    @staticmethod
    def _requests(transport, fragment="", method=None):
        return [request for request in transport.requests
                if fragment in request.path and (method is None or request.method == method)]

    def test_begin_refuses_a_printer_that_is_not_the_active_one(self):
        controller = self.module.UploadController(self._client(), "A", lambda: ("B", "B"))
        self.addCleanup(controller.abort)

        with self.assertRaises(ValueError) as raised:
            controller.begin(self.config_type(url="http://printer-a"), "part.gcode")

        self.assertIn("Only the active Cura printer", str(raised.exception))
        self.assertFalse(controller.busy)

    def test_prepared_releases_the_lease_of_a_retired_operation(self):
        # A lease handed to an operation that is already gone must be
        # closed here: nothing else owns it.
        controller = self.module.UploadController(self._client(), "A", lambda: ("A", "A"))
        self.addCleanup(controller.abort)
        source = self._prepared()

        controller.prepared(source)

        self.assertEqual(source.closes, 1)
        self.assertFalse(os.path.exists(source.path))
        self.assertEqual(controller.filename, "")

    def test_begin_refuses_to_ride_a_reconfigured_link(self):
        # The dialog's config may still name the printer Cura was on
        # before a reconfiguration: uploading there would send the file
        # to whatever the new endpoint is.
        controller = self.module.UploadController(self._client(), "A", lambda: ("A", "A"))
        self.addCleanup(controller.abort)

        with self.assertRaises(ValueError) as raised:
            controller.begin(self.config_type(url="http://printer-b"), "part.gcode")

        self.assertIn("being reconfigured", str(raised.exception))
        self.assertFalse(controller.busy)

    def test_prepared_refuses_a_second_lease_and_releases_the_newcomer(self):
        # The lease is exclusive: an operation owns exactly one
        # prepared file, and the loser must be closed here.
        controller = self._controller()
        first, second = self._prepared(), self._prepared("other.gcode")

        controller.prepared(first)
        with self.assertRaises(RuntimeError) as raised:
            controller.prepared(second)

        self.assertIn("already owns a prepared file", str(raised.exception))
        self.assertEqual(second.closes, 1)
        self.assertEqual(first.closes, 0)
        self.assertEqual(controller.filename, "part.gcode")

    def test_cancel_ignores_a_choice_that_is_being_applied(self):
        # The user hit cancel while the dialog's choice was still
        # landing: tearing down there would race the choice's own
        # start, so the cancel is dropped and the upload proceeds.
        client = self._client()
        controller = self._controller(client)
        controller.prepared(self._prepared())
        posted, _reply = self._install_upload_post(client)
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller.accept("gcodes", "benchy.gcode", False)
        controller.cancel()
        self.qt.events(10)

        self.assertEqual(results, [])
        self.assertEqual(len(posted), 1)

    def test_cancel_retires_the_operation_exactly_once(self):
        controller = self._controller()
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller.cancel()
        controller.cancel()  # a second click is not a second verdict
        self.qt.events(10)

        self.assertEqual(results, [(False, "")])
        self.assertTrue(controller.busy)  # the adapter owns the terminal
        controller.terminal_delivered()
        self.assertFalse(controller.busy)

    def test_start_uploads_when_the_print_was_not_requested(self):
        client = self._client()
        controller = self._controller(client, upload_start_print=False)
        controller.prepared(self._prepared())
        posted, _reply = self._install_upload_post(client)

        controller.start()

        self.assertEqual(len(posted), 1)  # no readiness detour

    def test_start_waits_for_readiness_when_no_power_device_is_configured(self):
        # Print start with no power devices: the readiness ladder runs
        # before the file is sent, since a print cannot start on a
        # Klippy that is still booting.
        client = self._client()
        transport = client.transport
        controller = self._controller(client, upload_start_print=True, power_devices="")
        controller.prepared(self._prepared())
        posted, _reply = self._install_upload_post(client)

        controller.start()

        self.assertEqual(posted, [])
        self.assertEqual(len(self._requests(transport, "server/info", "GET")), 1)

    def test_upload_ignores_a_reply_that_is_not_the_operations(self):
        # Only the reply this operation posted may report: a superseded
        # one is released without a verdict.
        client = self._client()
        controller = self._controller(client)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(client)
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))
        controller._upload()
        stranger = FakeReply(body=b'{"error": "not mine"}')

        controller._uploaded(stranger, controller._generation)

        self.assertEqual(stranger.deleted, 1)
        self.assertEqual(results, [])

    def test_upload_surfaces_the_servers_words_over_qt_error_text(self):
        # The refusal-words rule: Moonraker's own message beats Qt's
        # generic error string.
        from PyQt6.QtNetwork import QNetworkReply
        client = self._client()
        controller = self._controller(client)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(
            client, FakeReply(body=b'{"message": "Extrude below minimum temp"}',
                              error=QNetworkReply.NetworkError.ContentNotFoundError,
                              error_string="Error transferring - server replied: Not Found"))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(False, "Extrude below minimum temp")])

    def test_upload_reports_a_print_start_the_printer_refused(self):
        # A 201 upload with print_started false: the file is there, the
        # print is not, and the message must say so.
        client = self._client()
        controller = self._controller(client, upload_start_print=True)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(client, FakeReply(body=b'{"print_started": false}'))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(False, "Uploaded; the printer refused to start the print")])
        self.assertEqual(controller.print_outcome, "")

    def test_upload_reports_the_queued_outcome(self):
        # print_queued: the printer took the job but will not start it
        # until the current one ends — the adapter shows a queued print,
        # not a started one.
        client = self._client()
        controller = self._controller(client, upload_start_print=True)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(client, FakeReply(body=b'{"print_queued": true}'))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(True, "")])
        self.assertEqual(controller.print_outcome, "queued")

    def test_discover_walks_only_the_writable_visible_folders(self):
        client = self._client()
        transport = client.transport
        controller = self._controller(client)

        controller.discover()
        first = self._requests(transport, "server/files/directory?")
        self.assertEqual(len(first), 1)
        self.assertIn("path=gcodes", first[0].path)
        first[0].callback({"result": {"dirs": [
            {"dirname": "calibration", "permissions": "rw"},
            {"dirname": ".hidden", "permissions": "rw"},
            {"dirname": "read-only", "permissions": "r"},
            {"dirname": "  "},
            "not-a-dict",
            {"dirname": "calibration"},
        ]}}, None)

        self.assertEqual(controller.paths, ["<root>", "calibration"])
        # The walk descends into every folder it accepts.
        walk = self._requests(transport, "server/files/directory?")
        self.assertEqual(len(walk), 2)
        self.assertIn("gcodes%2Fcalibration", walk[1].path)
        walk[1].callback({"result": {"dirs": []}}, None)
        self.assertEqual(len(self._requests(transport, "server/files/directory?")), 2)
        # While the user is choosing, the walk stands down.
        controller._choice_pending = True
        controller._scan()
        self.assertEqual(len(self._requests(transport, "server/files/directory?")), 2)

    def test_accept_retires_a_choice_whose_connection_changed(self):
        active = ["A"]
        client = self._client()
        controller = self._controller(client, identity=lambda: (active[0], active[0]))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        active[0] = "B"  # a machine switch retires the live binding
        controller.accept("<root>", "renamed.gcode", False)
        self.qt.events(10)

        self.assertEqual(results, [(False, "Moonraker connection changed; retry the upload")])
        # The operation is retired; the adapter holds the device until
        # it acknowledges the terminal.
        self.assertTrue(controller.busy)
        controller.terminal_delivered()
        self.assertFalse(controller.busy)

    def test_accept_completes_the_extension_and_publishes_the_choice(self):
        controller = self._controller()
        accepted, closed = [], []
        controller.choicesAccepted.connect(lambda path, start: accepted.append((path, start)))
        controller.dialogClosed.connect(lambda: closed.append(1))

        controller.accept("gcodes/calibration", "benchy", False)
        self.assertEqual(controller.filename, "benchy.gcode")  # the slicer's extension carries over
        self.qt.events(10)

        self.assertEqual(accepted, [("gcodes/calibration", False)])
        self.assertEqual(closed, [1])

    def test_accept_rejects_a_hidden_folder_without_retiring_the_upload(self):
        controller = self._controller()
        accepted, closed = [], []
        controller.choicesAccepted.connect(lambda path, start: accepted.append((path, start)))
        controller.dialogClosed.connect(lambda: closed.append(1))

        controller.accept(".hidden", "benchy.gcode", False)
        self.qt.events(10)

        self.assertEqual(accepted, [])
        self.assertEqual(closed, [])
        self.assertTrue(controller.busy)  # the user may pick again

    def test_start_probes_and_powers_on_every_configured_device(self):
        client = self._client()
        transport = client.transport
        controller = self._controller(client, upload_start_print=True, power_devices="socket")
        controller.prepared(self._prepared())

        controller.start()
        probes = self._requests(transport, "machine/device_power/device?", "GET")
        self.assertEqual(len(probes), 1)
        self.assertIn("device=socket", probes[0].path)
        probes[0].callback({"result": {"socket": "off"}}, None)
        posts = self._requests(transport, "machine/device_power/device?", "POST")
        self.assertEqual(len(posts), 1)
        self.assertIn("action=on", posts[0].path)
        posts[0].callback({"result": {}}, None)

        # The chain ends in the readiness wait, not a second power-on.
        self.assertEqual(len(self._requests(transport, "server/info")), 1)
        self.assertEqual(controller._power_off, [])

    def test_ready_uploads_at_once_on_an_established_connection(self):
        client = self._client()
        controller = self._controller(client)
        controller.prepared(self._prepared())
        posted, _reply = self._install_upload_post(client)
        client.admit_status({"print_stats": {"state": "printing"}},
                            origin="sync", stamp=time.monotonic(), generation=client._generation)
        self.assertTrue(client.connected)

        controller._ready()

        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0].request.url().path(), "/server/files/upload")

    def test_ready_gives_up_when_klippy_never_arrives(self):
        controller = self._controller()
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))
        controller._attempts = controller.MAX_READY_ATTEMPTS

        controller._ready()
        self.qt.events(10)

        self.assertEqual(results, [(False, "Klippy did not become ready in time")])
        controller.terminal_delivered()
        self.assertFalse(controller.busy)

    def test_ready_waits_and_retries_while_klippy_starts(self):
        client = self._client()
        transport = client.transport
        controller = self._controller(client, ready_retry_interval_s=0.1)
        controller.prepared(self._prepared())
        posted, _reply = self._install_upload_post(client)
        waiting = []
        controller.status.connect(waiting.append)

        controller._ready()
        first = self._requests(transport, "server/info")
        self.assertEqual(len(first), 1)
        first[0].callback({"result": {"klippy_state": "startup"}}, None)
        self.assertEqual(waiting, ["Waiting for printer readiness (1/21)"])

        self.qt.events(300)  # the retry is scheduled on the configured interval
        retries = self._requests(transport, "server/info")
        self.assertEqual(len(retries), 2)
        retries[1].callback({"result": {"klippy_state": "ready"}}, None)
        self.assertEqual(len(posted), 1)

    def test_upload_refuses_to_ride_a_refused_print_gate(self):
        client = self._client()
        controller = self._controller(client, upload_start_print=True)
        controller.prepared(self._prepared())
        controller.set_print_gate(
            lambda: self.verdict_type(mode="disabled", reason="the printer is not idle"))
        posted, reply = self._install_upload_post(client)
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        self.assertEqual(len(posted), 1)  # the file still uploads

        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(False, "Uploaded; print start refused: the printer is not idle")])
        self.assertEqual(controller.print_outcome, "")

    def test_upload_reports_the_started_outcome(self):
        client = self._client()
        controller = self._controller(client, upload_start_print=True)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(
            client, FakeReply(body=b'{"result": {"item": {}}, "print_started": true}'))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(True, "")])
        self.assertEqual(controller.print_outcome, "started")

    def test_upload_accepts_a_legacy_non_json_success_body(self):
        client = self._client()
        controller = self._controller(client)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(client, FakeReply(body=b"<html>Moonraker</html>"))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(True, "")])
        self.assertEqual(controller.print_outcome, "")

    def test_upload_surfaces_a_json_error_key_without_a_transport_error(self):
        client = self._client()
        controller = self._controller(client)
        controller.prepared(self._prepared())
        _posted, reply = self._install_upload_post(
            client, FakeReply(body=b'{"error": "the job queue is full"}'))
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller._upload()
        reply.finished.emit()
        self.qt.events(10)

        self.assertEqual(results, [(False, "the job queue is full")])

    def test_upload_failure_closes_the_source_and_reports(self):
        client = self._client()
        controller = self._controller(client)
        source = self._prepared()
        controller.prepared(source)
        posted, _reply = self._install_upload_post(client)
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))
        os.remove(source.path)  # the prepared file vanished before it could open

        controller._upload()
        self.qt.events(10)

        self.assertEqual(len(results), 1)
        success, detail = results[0]
        self.assertFalse(success)
        self.assertTrue(detail)
        self.assertEqual(posted, [])  # it failed at the file, not on the wire
        self.assertEqual(source.closes, 1)
        controller.terminal_delivered()
        self.assertFalse(controller.busy)

    def test_the_terminal_timer_survives_a_deleted_owner(self):
        # The queued terminal can outlive Cura's deletion of the owner.
        from PyQt6 import sip
        client = self._client()
        controller = self.module.UploadController(client, "A", lambda: ("A", "A"))
        controller.begin(self.config_type(url="http://printer-a", upload_dialog=True), "part.gcode")
        results = []
        controller.finished.connect(lambda ok, detail: results.append((ok, detail)))

        controller.fail("the printer changed")
        sip.delete(controller)
        self.qt.events(10)

        self.assertEqual(results, [])  # nothing delivered, and nothing raised


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class CameraBridgeTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.module = self.qt.load("CameraBridge")

    def _bridge(self, base="", key=""):
        bridge = self.module.CameraBridge()
        self.addCleanup(bridge.stop)
        if base:
            bridge.configure(base, key)
        return bridge

    def _recording_nam(self, bridge):
        """Swap the manager for a recorder: what the bridge fetched."""
        class RecordingNam:
            def __init__(self):
                self.gets = []

            def get(self, request):
                self.gets.append(request)
                return FakeReply()
        nam = RecordingNam()
        bridge._nam = nam
        return nam

    def _server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _client(self, bridge, request):
        from PyQt6.QtNetwork import QTcpSocket
        socket = QTcpSocket()
        self.addCleanup(socket.abort)
        socket.connectToHost("127.0.0.1", bridge.port)
        socket.write(request)
        return socket

    def _pump(self, condition, iterations=400):
        for _ in range(iterations):
            self.qt.events(10)
            if condition():
                return True
        return False

    @staticmethod
    def _handler(recorded, body=None, status=200, headers=(), hold=None):
        """A camera endpoint: one GET, optionally an open-ended stream.

        A held response declares NO content length — that is what keeps
        the upstream reply live, exactly like a real MJPG stream.
        """

        class Handler(PipeSafeHandler):
            def do_GET(self):
                recorded.append((self.path, self.headers.get("X-Api-Key")))
                try:
                    self.send_response(status)
                    for name, value in headers:
                        self.send_header(name, value)
                    if body is not None and hold is None:
                        self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if body is not None:
                        self.wfile.write(body)
                        self.wfile.flush()
                    if hold is not None:
                        hold.wait(5)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass
        return Handler

    def test_the_bridge_listens_only_while_configured(self):
        bridge = self._bridge()
        self.assertFalse(bridge.active)
        self.assertEqual(bridge.port, 0)
        self.assertEqual(bridge.local_url("/webcam/?action=stream"), "")  # nothing to point at

        self.assertTrue(bridge.configure("http://printer/", "k"))  # the trailing slash is trimmed
        port = bridge.port
        self.assertGreater(port, 0)
        self.assertTrue(bridge.active)
        self.assertEqual(bridge.local_url("/webcam/?action=stream"),
                         f"http://127.0.0.1:{port}/webcam/?action=stream")
        self.assertEqual(bridge.local_url("webcam"), f"http://127.0.0.1:{port}/webcam")
        self.assertTrue(bridge.configure("http://printer", "k"))  # same identity keeps the listener
        self.assertEqual(bridge.port, port)

        self.assertFalse(bridge.configure("", ""))  # unconfigured closes the listener
        self.assertEqual(bridge.port, 0)
        self.assertFalse(bridge.active)
        bridge.stop()  # a second teardown is a no-op

    def test_the_bridge_releases_every_relay_on_stop(self):
        from PyQt6.QtNetwork import QTcpSocket
        recorded, hold = [], threading.Event()
        self.addCleanup(hold.set)
        server = self._server(self._handler(recorded, body=b"frame", headers=(
            ("Content-Type", "multipart/x-mixed-replace; boundary=frame"),), hold=hold))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "key")
        socket = self._client(bridge, b"GET /webcam?action=stream HTTP/1.1\r\nHost: local\r\n\r\n")
        self.assertTrue(self._pump(lambda: bool(recorded) and bridge._relays))
        self.assertTrue(self._pump(lambda: bytes(socket.readAll()) != b""))

        bridge.stop()

        self.assertEqual(bridge._relays, {})
        self.assertTrue(self._pump(lambda: not bridge._server.findChildren(QTcpSocket)))
        self.assertEqual(bridge._upstream_base, "")
        self.assertEqual(bridge._api_key, "")

    def test_the_bridge_relays_a_snapshot_with_its_content_length(self):
        recorded = []
        body = b"\xff\xd8\xff\xd9"
        server = self._server(self._handler(recorded, body=body, headers=(("Content-Type", "image/jpeg"),)))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "test-key")
        socket = self._client(bridge, b"GET /webcam/?action=snapshot HTTP/1.1\r\nHost: local\r\n\r\n")
        payload = bytearray()

        def drain():
            payload.extend(bytes(socket.readAll()))
            return payload.endswith(body)
        self.assertTrue(self._pump(drain))

        head = bytes(payload).split(b"\r\n\r\n", 1)[0]
        self.assertIn(b"HTTP/1.1 200 OK", head)
        self.assertIn(b"Content-Type: image/jpeg", head)
        self.assertIn(f"Content-Length: {len(body)}".encode(), head)
        self.assertIn(b"Connection: close", head)
        self.assertEqual(bytes(payload).split(b"\r\n\r\n", 1)[1], body)
        self.assertEqual(bridge._relayed_bytes, len(body))
        self.assertEqual([key for _path, key in recorded], ["test-key"])
        # A finished reply terminates the response and releases the relay.
        self.assertTrue(self._pump(lambda: not bridge._relays))

    def test_a_finished_response_drains_its_buffered_tail(self):
        # A finite (snapshot) response can finish with its last bytes
        # still in the reply buffer (the readyRead gate closed): the
        # bridge drains them before the close, or the loader sees a
        # body shorter than the declared Content-Length.
        bridge = self._bridge()

        class TailSocket:
            def __init__(self):
                self.writes = bytearray()
                self.disconnects = 0

            def bytesToWrite(self):
                return 0

            def write(self, chunk):
                self.writes.extend(chunk)

            def flush(self):
                pass

            def disconnectFromHost(self):
                self.disconnects += 1

        socket = TailSocket()
        reply = FakeReply(body=b"tail")
        bridge._relays[socket] = (reply, bytearray(), True)
        bridge._on_upstream_finished(socket, reply)
        self.assertIn(b"tail", socket.writes)
        self.assertEqual(bridge._relayed_bytes, 4)
        self.assertNotIn(socket, bridge._relays)

    def test_the_bridge_drains_client_bytes_after_dispatch(self):
        recorded, hold = [], threading.Event()
        self.addCleanup(hold.set)
        chunk = b"\xff\xd8\xff\xd9"
        server = self._server(self._handler(recorded, body=chunk, hold=hold))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "key")
        socket = self._client(bridge, b"GET /stream HTTP/1.1\r\nHost: local\r\n\r\n")
        payload = bytearray()

        def drain():
            payload.extend(bytes(socket.readAll()))
            return payload.endswith(chunk)
        self.assertTrue(self._pump(drain))

        socket.write(b"trailing body traffic")
        self.qt.events(50)

        self.assertEqual(len(recorded), 1)  # never a second upstream request
        self.assertEqual(bridge._relayed_bytes, len(chunk))  # the head went out exactly once
        self.assertEqual(bytes(payload).count(b"HTTP/1.1"), 1)

    def test_the_bridge_holds_a_partial_request_line(self):
        recorded = []
        server = self._server(self._handler(recorded, body=b"\xff\xd8", headers=(("Content-Type", "image/jpeg"),)))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "key")
        socket = self._client(bridge, b"GET /snap")

        self.qt.events(50)

        self.assertEqual(recorded, [])  # no request line yet: nothing to dispatch
        self.assertEqual(len(bridge._relays), 1)
        socket.write(b"shot HTTP/1.1\r\nHost: local\r\n\r\n")
        self.assertTrue(self._pump(lambda: bool(recorded)))
        self.assertEqual(recorded[0][0], "/snapshot")

    def test_the_bridge_drops_an_oversized_request_header(self):
        bridge = self._bridge("http://127.0.0.1:1", "key")
        nam = self._recording_nam(bridge)
        socket = self._client(bridge, b"GET /" + b"a" * (self.module.MAX_REQUEST_HEADER_BYTES + 512))
        disconnected = []
        socket.disconnected.connect(lambda: disconnected.append(1))

        self.assertTrue(self._pump(lambda: bool(disconnected)))

        self.assertEqual(nam.gets, [])  # the capped header is never dispatched
        self.assertEqual(bridge._relayed_bytes, 0)

    def _assert_the_client_is_dropped(self, bridge, request):
        socket = self._client(bridge, request)
        disconnected = []
        socket.disconnected.connect(lambda: disconnected.append(1))

        self.assertTrue(self._pump(lambda: bool(disconnected)))
        self.assertTrue(self._pump(lambda: not bridge._relays))

    def test_the_bridge_aborts_a_request_that_is_not_a_get(self):
        recorded = []
        server = self._server(self._handler(recorded, body=b"x"))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "key")

        for request in (b"POST /upload HTTP/1.1\r\nHost: local\r\n\r\n",
                        b"GET\r\n\r\n"):
            with self.subTest(request=request):
                self._assert_the_client_is_dropped(bridge, request)

        self.qt.events(50)
        self.assertEqual(recorded, [])
        self.assertEqual(bridge._relayed_bytes, 0)

    def test_the_bridge_ignores_ready_notifications_for_unknown_sockets(self):
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge("http://127.0.0.1:1", "key")

        bridge._on_socket_ready(QTcpSocket())  # never accepted: nothing to relay

        self.assertEqual(bridge._relays, {})
        self.assertEqual(bridge._relayed_bytes, 0)

    def test_the_bridge_refuses_a_dispatch_without_a_usable_target(self):
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge()
        nam = self._recording_nam(bridge)
        socket = QTcpSocket()
        self.addCleanup(socket.abort)

        bridge._start_upstream(socket, "/webcam")  # unconfigured
        bridge._upstream_base = "ftp://printer"
        bridge._start_upstream(socket, "/webcam")  # not a camera transport

        self.assertEqual(nam.gets, [])
        self.assertEqual(bridge._relayed_bytes, 0)

    def test_the_bridge_reads_header_text_whatever_qt_hands_it(self):
        header_text = self.module.CameraBridge._header_text
        self.assertEqual(header_text(b"OK", "fallback"), "OK")
        self.assertEqual(header_text("Uploading", "fallback"), "Uploading")
        self.assertEqual(header_text(None, "application/octet-stream"), "application/octet-stream")

    def test_the_bridge_ignores_replies_that_are_not_the_relays(self):
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge("http://127.0.0.1:1", "key")
        socket = QTcpSocket()
        self.addCleanup(socket.abort)
        own, stale = FakeReply(), FakeReply()
        bridge._relays[socket] = (own, bytearray(), False)

        bridge._on_upstream_ready(socket, stale)
        bridge._on_socket_written(socket, stale)
        unknown = QTcpSocket()
        bridge._on_upstream_finished(unknown, stale)
        # A stray reply whose Qt core is already gone — finished after
        # the NAM destroyed it — releases just as quietly.
        bridge._on_upstream_finished(socket, DeadReply())

        self.assertEqual(stale.deleted, 1)  # the stray reply still releases itself
        self.assertEqual(bridge._relays[socket], (own, bytearray(), False))
        self.assertEqual(bridge._relayed_bytes, 0)

    def test_the_bridge_releases_the_upstream_of_a_dropped_client(self):
        from PyQt6.QtNetwork import QTcpSocket
        recorded, hold = [], threading.Event()
        self.addCleanup(hold.set)
        server = self._server(self._handler(recorded, body=b"frame", hold=hold))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "key")
        socket = self._client(bridge, b"GET /stream HTTP/1.1\r\nHost: local\r\n\r\n")
        self.assertTrue(self._pump(lambda: bool(recorded) and bridge._relays))

        socket.abort()  # the loader gave up on a live stream

        self.assertTrue(self._pump(lambda: not bridge._relays))
        self.assertTrue(self._pump(lambda: not bridge._server.findChildren(QTcpSocket)))

    def test_the_bridge_aborts_the_stream_behind_an_auth_refusal(self):
        from PyQt6.QtNetwork import QTcpSocket
        recorded = []
        server = self._server(self._handler(recorded, body=b"nope", status=401))
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "wrong-key")
        failures = []
        bridge.upstreamFailed.connect(failures.append)
        self._client(bridge, b"GET /webcam HTTP/1.1\r\nHost: local\r\n\r\n")

        self.assertTrue(self._pump(lambda: bool(failures)))

        self.assertIn("camera upstream failed", failures[0])
        self.assertEqual([key for _path, key in recorded], ["wrong-key"])
        self.assertEqual(bridge._relays, {})
        self.assertTrue(self._pump(lambda: not bridge._server.findChildren(QTcpSocket)))

    def test_the_bridge_aborts_when_the_printer_is_unreachable(self):
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge("http://127.0.0.1:1", "key")  # nothing listens on port 1
        failures = []
        bridge.upstreamFailed.connect(failures.append)
        self._client(bridge, b"GET /webcam HTTP/1.1\r\nHost: local\r\n\r\n")

        self.assertTrue(self._pump(lambda: bool(failures)))

        self.assertIn("camera upstream failed", failures[0])
        self.assertEqual(bridge._relays, {})
        self.assertTrue(self._pump(lambda: not bridge._server.findChildren(QTcpSocket)))

    def test_the_bridge_closes_cleanly_on_a_truncated_stream(self):
        # A reply that ends early carries an HTTP status under 400: the
        # client still gets the tail flushed and the socket closed.
        class Truncating(PipeSafeHandler):
            def do_GET(self):
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    self.wfile.write(b"\xff\xd8")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = self._server(Truncating)
        bridge = self._bridge("http://127.0.0.1:" + str(server.server_port), "key")
        socket = self._client(bridge, b"GET /snapshot HTTP/1.1\r\nHost: local\r\n\r\n")
        payload, disconnected = bytearray(), []
        socket.disconnected.connect(lambda: disconnected.append(1))

        def drain():
            payload.extend(bytes(socket.readAll()))
            return bool(disconnected)
        self.assertTrue(self._pump(drain))

        self.assertIn(b"\xff\xd8", bytes(payload))
        self.assertEqual(bridge._relays, {})

    def test_the_bridge_skips_a_pending_connection_the_server_cannot_yield(self):
        class Vanishing:
            def __init__(self):
                self.pending = [True, False]

            def hasPendingConnections(self):
                return self.pending.pop(0) if self.pending else False

            def nextPendingConnection(self):
                return None

            def isListening(self):
                return False

            def close(self):
                pass

        bridge = self._bridge()
        server = Vanishing()
        bridge._server = server

        bridge._accept()

        self.assertEqual(bridge._relays, {})
        self.assertEqual(server.pending, [])

    def test_the_teardown_releases_objects_whose_qt_core_is_gone(self):
        # The live report's graveyard: a reply and an accepted socket
        # can outlive their C++ objects (the NAM and the server own
        # them), and a release that raised would strand every relay
        # behind the dead one.
        from PyQt6 import sip
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge()
        socket = QTcpSocket()
        bridge._relays[socket] = (DeadReply(), bytearray(), False)
        sip.delete(socket)

        bridge._close_all()

        self.assertEqual(bridge._relays, {})

    def test_a_reply_that_refuses_to_be_released_still_closes_the_stream(self):
        # The release arm is isolated from the reads that follow it:
        # whatever the reply does when it is let go, the relay is gone
        # and the client's socket still closes.
        from PyQt6 import sip
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge()
        socket, reply = QTcpSocket(), DeadReply()
        bridge._relays[socket] = (reply, bytearray(), False)
        sip.delete(socket)

        bridge._on_upstream_finished(socket, reply)

        self.assertEqual(bridge._relays, {})

    def test_a_dead_client_socket_is_released_on_close(self):
        # Same guard on the client side, where the socket is the one
        # whose Qt core is gone — and the upstream behind it with it.
        from PyQt6 import sip
        from PyQt6.QtNetwork import QTcpSocket
        bridge = self._bridge()
        socket = QTcpSocket()
        bridge._relays[socket] = (DeadReply(), bytearray(), False)
        sip.delete(socket)

        bridge._on_socket_closed(socket)

        self.assertEqual(bridge._relays, {})


if __name__ == "__main__":
    unittest.main()
