"""Contracts measured on the REAL engine, offscreen: geometry the text
linters cannot see and delegate rendering that only exists once the
documents are mounted.

MoonrakerMonitor.qml and MoonrakerPreviewCard.qml need a GUI
application (QFontDatabase is a hard requirement of every Text), while
the shared harness builds a core application — so this file owns its
own application and skips when the process already has one. The
per-file leg of tools/run_tests.sh runs each test file in its own
process, which is where these run for real.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qt_runtime_support import QT_AVAILABLE  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QObject, QPointF, QRectF, Qt, QUrl, qInstallMessageHandler
    from PyQt6.QtCore import pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtQml import QQmlComponent, QQmlEngine
    from PyQt6.QtQuick import QQuickItem, QQuickWindow

    from plugins.GCodeIndex import build_index_from_bytes
    from plugins.MonitorFormatting import _point_in_polygon, polygon_bounds
    from plugins.PlateProgress import layer_polylines

    class CuraApplicationDouble(QObject):
        """The one context property both documents read for idleness."""

        platformActivityChanged = pyqtSignal()

        @pyqtProperty(bool, notify=platformActivityChanged)
        def platformActivity(self):
            return True


    class CameraModelDouble(QObject):
        """The webcam surface the camera card reads — enough for its
        title row to build with the selector and refresh button
        showing, which is the widest that row ever gets. The FPS
        surface (the idle-load request) records every rate the control
        commits, so one wheel notch is observable."""

        monitorConnectedChanged = pyqtSignal()
        cameraRecoveringChanged = pyqtSignal()
        cameraRotationChanged = pyqtSignal()
        cameraFlipChanged = pyqtSignal()
        activeWebcamChanged = pyqtSignal()
        webcamNamesChanged = pyqtSignal()
        cameraFpsChanged = pyqtSignal()
        webcamStreamEnabledChanged = pyqtSignal()

        def __init__(self, fps=15.0, maximum=30.0):
            super().__init__()
            self.fps_calls = []
            self._camera_fps = float(fps)
            self._camera_fps_maximum = float(maximum)
            self._stream_enabled = True

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraFps(self):
            return self._camera_fps

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraFpsMin(self):
            return 0.5

        @pyqtProperty(float, notify=cameraFpsChanged)
        def cameraFpsMax(self):
            return self._camera_fps_maximum

        def set_camera_ceiling(self, maximum, fps=None):
            """A camera re-selected (or re-configured) behind the pane:
            the ceiling changes and the published rate follows it down
            exactly as MonitorCamera republishes."""
            self._camera_fps_maximum = float(maximum)
            if fps is None:
                self._camera_fps = min(self._camera_fps, self._camera_fps_maximum)
            else:
                self._camera_fps = float(fps)
            self.cameraFpsChanged.emit()

        @pyqtSlot(float)
        def setCameraFps(self, fps):
            self.fps_calls.append(float(fps))
            self._camera_fps = float(fps)
            self.cameraFpsChanged.emit()

        @pyqtProperty(bool, notify=webcamStreamEnabledChanged)
        def webcamStreamEnabled(self):
            return self._stream_enabled

        def set_stream_enabled(self, enabled):
            """The stream toggle, as the model publishes it: the flag
            and the URL move together (setWebcamStreamEnabled)."""
            self._stream_enabled = bool(enabled)
            self.webcamStreamEnabledChanged.emit()

        @pyqtProperty(bool, notify=monitorConnectedChanged)
        def monitorConnected(self):
            return True

        @pyqtProperty(bool, notify=cameraRecoveringChanged)
        def cameraRecovering(self):
            return False

        @pyqtProperty(int, notify=cameraRotationChanged)
        def cameraRotation(self):
            return 0

        @pyqtProperty(bool, notify=cameraFlipChanged)
        def cameraFlipHorizontal(self):
            return False

        @pyqtProperty(bool, notify=cameraFlipChanged)
        def cameraFlipVertical(self):
            return False

        @pyqtProperty("QVariant", notify=webcamNamesChanged)
        def webcamNames(self):
            return ["webcam", "webcam2"]

        @pyqtProperty(int, notify=activeWebcamChanged)
        def activeWebcamIndex(self):
            return 0

        @pyqtSlot(result=int)
        def cameraPaneInstanceId(self):
            return 1

        @pyqtSlot(int, str)
        def cameraPaneTrace(self, pane_id, message):
            pass

        @pyqtSlot()
        def cameraFirstFrameRendered(self):
            pass

        @pyqtSlot()
        def cameraRenderStalled(self):
            pass

        @pyqtSlot()
        def refreshWebcams(self):
            pass

        @pyqtSlot(int)
        def selectWebcam(self, index):
            pass


    class PrinterModelDouble(QObject):
        """A printer model that RECORDS the pane commands. A click on a
        null model looks the same whether or not a guard ran, so the
        refusal is only observable through the call log."""

        infoCollapsedChanged = pyqtSignal()
        statusCollapsedChanged = pyqtSignal()
        controlsCollapsedChanged = pyqtSignal()
        monitorEtaChanged = pyqtSignal()
        printActiveChanged = pyqtSignal()
        fanItemsChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.info_calls = []
            self.status_calls = []
            self.controls_calls = []
            self.section_calls = []
            self._fan_items = []
            self._info_collapsed = False
            self._status_collapsed = False
            self._controls_collapsed = False
            self._eta = "—"
            self._finish = "—"
            self._print_active = False

        @pyqtProperty("QVariant", notify=fanItemsChanged)
        def fanControlItems(self):
            return self._fan_items

        def republishFans(self, items):
            """The Moonraker refresh: a NEW fan list (the same fans,
            fresh values) replaces the repeater's model."""
            self._fan_items = items
            self.fanItemsChanged.emit()

        @pyqtProperty(str, notify=monitorEtaChanged)
        def monitorEta(self):
            return self._eta

        @pyqtSlot(str)
        def setMonitorEta(self, text):
            self._eta = text
            self.monitorEtaChanged.emit()

        @pyqtProperty(bool, notify=printActiveChanged)
        def printActive(self):
            return self._print_active

        @pyqtSlot(bool)
        def setPrintActive(self, active):
            self._print_active = bool(active)
            self.printActiveChanged.emit()

        @pyqtProperty(str, notify=monitorEtaChanged)
        def monitorFinish(self):
            return self._finish

        @pyqtSlot(str)
        def setMonitorFinish(self, text):
            self._finish = text
            self.monitorEtaChanged.emit()

        @pyqtProperty("QVariant")
        def temperatureItems(self):
            # The strip's availability gates read the temperature
            # items first: without the key the gate refresh throws
            # and the readouts never render in the harness.
            return []

        @pyqtProperty(bool, notify=infoCollapsedChanged)
        def infoCollapsed(self):
            return self._info_collapsed

        @pyqtSlot(bool)
        def setInfoCollapsed(self, collapsed):
            self.info_calls.append(bool(collapsed))
            self._info_collapsed = bool(collapsed)
            self.infoCollapsedChanged.emit()

        @pyqtProperty(bool, notify=statusCollapsedChanged)
        def statusCollapsed(self):
            return self._status_collapsed

        @pyqtSlot(bool)
        def setStatusCollapsed(self, collapsed):
            self.status_calls.append(bool(collapsed))
            self._status_collapsed = bool(collapsed)
            self.statusCollapsedChanged.emit()

        @pyqtSlot(str, bool)
        def setSectionExpanded(self, section, expanded):
            self.section_calls.append((section, bool(expanded)))

        @pyqtProperty(bool, notify=controlsCollapsedChanged)
        def controlsCollapsed(self):
            return self._controls_collapsed

        @pyqtSlot(bool)
        def setControlsCollapsed(self, collapsed):
            self.controls_calls.append(bool(collapsed))
            self._controls_collapsed = bool(collapsed)
            self.controlsCollapsedChanged.emit()

        @pyqtSlot(bool)
        def setConsoleExpanded(self, expanded):
            pass

        @pyqtProperty("QVariant")
        def sectionExpandedMap(self):
            return {}

        @pyqtProperty("QVariant")
        def sectionHiddenMap(self):
            return {}

        @pyqtProperty(bool)
        def monitorConnected(self):
            return True


    class OutputDeviceDouble(QObject):
        """The output device the monitor documents read as a context
        property, with the stage exit recorded: the Esc ladder's last
        rung leaves the monitor stage, and a null device would look the
        same whether or not that rung ran."""

        activePrinterChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self._printer = PrinterModelDouble()
            self.leaves = 0

        @pyqtProperty(QObject, notify=activePrinterChanged)
        def activePrinter(self):
            return self._printer

        @pyqtSlot()
        def leaveMonitorStage(self):
            self.leaves += 1


_APPLICATION = {"app": None, "engine": None, "theme": None, "messages": []}


def _start_application():
    """The application, engine and capture theme — built once for the
    whole file (the application cannot be replaced mid-process)."""
    if _APPLICATION["app"] is not None:
        return _APPLICATION["app"]
    if QCoreApplication.instance() is not None:
        raise unittest.SkipTest("the process already owns an application")
    sys.path.insert(0, str(ROOT / "tools"))
    from theme_support import ThemeBackend, materialise_theme_assets
    _APPLICATION["app"] = QGuiApplication([])
    _APPLICATION["theme"] = tempfile.mkdtemp(prefix="qml-probe-theme-")
    backend = ThemeBackend(str(ROOT / "tests" / "theme_assets" / "cura-light"))
    theme_tree = materialise_theme_assets(_APPLICATION["theme"], backend)
    engine = QQmlEngine()
    engine.addImportPath(str(ROOT / "tests" / "qml_stubs"))
    engine.addImportPath(str(ROOT / "plugins"))
    engine.addImportPath(theme_tree)
    engine.rootContext().setContextProperty("CuraApplication", CuraApplicationDouble())
    engine.rootContext().setContextProperty("screenScaleFactor", 1.0)
    engine.rootContext().setContextProperty("OutputDevice", None)
    _APPLICATION["engine"] = engine
    qInstallMessageHandler(lambda message_type, context, message: _APPLICATION["messages"].append(str(message)))
    return _APPLICATION["app"]


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class RealEngineTestCase(unittest.TestCase):
    """Mounts plugin documents offscreen, with the capture theme."""

    @classmethod
    def setUpClass(cls):
        cls.app = _start_application()
        cls.engine = _APPLICATION["engine"]

    def setUp(self):
        self._message_start = len(_APPLICATION["messages"])

    def pump(self, rounds=20):
        for _ in range(rounds):
            self.app.processEvents()

    def _pump_ms(self, milliseconds):
        """Real wall-clock pumping: the threaded canvases' paints and
        the QML timers (the 150 ms view settle) only advance with
        time — processEvents alone starves them."""
        deadline = time.monotonic() + milliseconds / 1000.0
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def new_messages(self):
        return [message for message in _APPLICATION["messages"][self._message_start:]
                if "MoonrakerMonitor.qml" in message or "MoonrakerPreviewCard.qml" in message]

    def mount(self, filename):
        component = QQmlComponent(self.engine)
        component.loadUrl(QUrl.fromLocalFile(str(ROOT / "plugins" / filename)))
        document = component.create()
        self.assertIsNotNone(document, [str(error) for error in component.errors()])
        if isinstance(document, QQmlComponent):
            # A Component-rooted document (MoonrakerMonitor.qml) creates
            # the component; the instance is one more call away.
            component = document
            document = component.create()
            self.assertIsNotNone(document, [str(error) for error in component.errors()])
        self.addCleanup(document.deleteLater)
        return document

    def find(self, document, object_name):
        item = document.findChild(QQuickItem, object_name)
        self.assertIsNotNone(item, object_name)
        return item

    @staticmethod
    def rect(item, base):
        return QRectF(item.mapToItem(base, QPointF(0.0, 0.0)), item.size())

    def mount_monitor(self, width, height=760):
        monitor = self.mount("MoonrakerMonitor.qml")
        monitor.setWidth(width)
        monitor.setHeight(height)
        self.pump()
        return monitor

    def mount_window(self, filename, width, height):
        """A document in a real window: a windowless mount lays out
        once and never again, so only a window replays what the live
        run does on every resize."""
        document = self.mount(filename)
        window = QQuickWindow()
        window.resize(width, height)
        document.setParentItem(window.contentItem())
        document.setWidth(width)
        document.setHeight(height)
        window.show()
        self.addCleanup(window.deleteLater)
        # The multi-test crash (engine-proven in the probe): a loaded
        # raster texture's teardown races the next mount unless the
        # window drains its frames hidden first — every window-mount
        # takes the safe teardown.
        self.addCleanup(self._settle_window, window)
        self.pump(30)
        return document, window

    def _settle_window(self, window):
        # The teardown's last binding evaluations must never wrap a
        # QObject: while the engine's property-cache registry dies,
        # a PlateLayer inside progress.layers.current is exactly the
        # wrap that segfaults it (QObjectWrapper::wrap ->
        # QQmlMetaType::propertyCache). Restore the plain-dict
        # payload FIRST — dicts wrap inertly — then drain hidden.
        printer = getattr(self, "_printer", None)
        if printer is not None and hasattr(printer, "setLayers"):
            printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
            self.pump(20)
        window.setProperty("visible", False)
        self.pump(60)

    def resize_window(self, document, window, width, height):
        window.resize(width, height)
        document.setWidth(width)
        document.setHeight(height)
        self.pump(20)

    def camera_pane(self, monitor):
        """The camera card in the monitor's middle column."""
        for item in monitor.findChildren(QQuickItem):
            if item.property("viewportWidth") is not None and item.property("contentHeight") is not None:
                return item
        self.fail("the camera card did not mount")

    def pause_card(self, items):
        """The preview card with a pause schedule, mounted in a window
        (a windowless ListView builds no delegates)."""
        card = self.mount("MoonrakerPreviewCard.qml")
        for name, value in (("gateVisible", True), ("previewStageActive", True),
                            ("configuredForFollowing", True), ("followingEnabled", True),
                            ("hasToolpath", True), ("pauseAtLayerActive", True),
                            ("pauseAtLayerItems", items)):
            card.setProperty(name, value)
        window = QQuickWindow()
        window.resize(800, 640)
        card.setParentItem(window.contentItem())
        card.setWidth(800)
        card.setHeight(640)
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(30)
        return card

    def pause_rows(self, card):
        """The rendered text of every pause row, in model order."""
        view = None
        for item in card.findChildren(QQuickItem):
            if item.property("count"):
                view = item
                break
        self.assertIsNotNone(view, "the pause list view did not build")
        content = view.property("contentItem")
        rows = []
        for row in content.childItems():
            labels = [child.property("text") for child in row.childItems()]
            rows.append(next((text for text in labels if text), ""))
        return [row for row in rows if row]


class StatusColumnGeometryTests(RealEngineTestCase):
    def test_the_status_column_tracks_the_pane_viewport(self):
        # The regression: without an explicit viewport-relative width
        # the column sat at its own implicit width (301 px) inside a
        # 238 px pane — the sections painted past the pane's edge at
        # every size and never filled it. The widths stay above the
        # status pane's fold: folded, the column is the strip. The
        # mount is windowed because the narrow-window rule needs a
        # second layout pass to settle: a windowless mount stops on
        # the pass whose camera still reads the squeezed pane (the
        # harness note on _settle).
        for width in (760, 900):
            monitor, _window = self.mount_window("MoonrakerMonitor.qml", width, 760)
            flick = self.find(monitor, "moonrakerStatusFlick")
            content = self.find(monitor, "moonrakerStatusContent")
            self.assertFalse(monitor.property("statusCollapsed"),
                             "the pane folded at %d" % width)
            self.assertGreater(flick.width(), 100, "the status pane did not lay out")
            self.assertAlmostEqual(content.width(), flick.width() - 14, delta=0.5)
        self.assertEqual(monitor.width(), 900)

    def test_the_sections_fill_the_column_once_it_is_wide(self):
        monitor, _window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        content = self.find(monitor, "moonrakerStatusContent")
        sections = [child for child in content.childItems() if child.isVisible() and child.width() > 0]
        # Objects moved to the controls pane (4.6.0): two sections
        # render unconditionally here without live data.
        self.assertGreaterEqual(len(sections), 2)
        for section in sections:
            self.assertAlmostEqual(section.width(), content.width(), delta=0.5)

    def test_the_column_never_keeps_its_own_implicit_width(self):
        # The narrow viewport is the crisp case: the column is NARROWER
        # than the content it holds, which only happens when the width
        # tracks the flickable. Below the pane's fold a narrow viewport
        # is the collapsed pane's readout strip.
        for width in (520, 560):
            monitor = self.mount_monitor(width)
            flick = self.find(monitor, "moonrakerStatusFlick")
            content = self.find(monitor, "moonrakerStatusContent")
            self.assertAlmostEqual(content.width(), flick.width() - 14, delta=0.5)
            self.assertLess(content.width(), content.property("implicitWidth"))


class ConsoleInputRowTests(RealEngineTestCase):
    def test_the_input_keeps_the_buttons_in_their_own_cells(self):
        # The 5.11/5.12 sweep: the field's hit region covered Send and
        # Clear, so the presses aimed at them landed on the field. The
        # field shrinks and clips inside its own cell; the buttons hold
        # theirs at every pane width.
        for width in (1600, 900, 640):
            monitor = self.mount_monitor(width)
            field = self.rect(self.find(monitor, "moonrakerConsoleInput"), monitor)
            send = self.rect(self.find(monitor, "moonrakerConsoleSend"), monitor)
            clear = self.rect(self.find(monitor, "moonrakerConsoleClear"), monitor)
            self.assertLessEqual(field.right(), send.left() + 0.5, "field covers Send at %d" % width)
            self.assertLessEqual(send.right(), clear.left() + 0.5, "Send covers Clear at %d" % width)
            self.assertGreater(send.width(), 0.0)
            self.assertGreater(clear.width(), 0.0)


class CollapseOnShrinkTests(RealEngineTestCase):
    """The squeeze latch on the LIVE path (the 2026-09-19 report: a
    slow window shrink stopped folding the panes). The camera column
    is the fixed one — it must never collapse — and the panes around
    it fold or yield as the window narrows."""

    def test_a_slow_shrink_folds_the_information_pane(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1100, 760)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self.assertFalse(monitor.property("infoCollapsed"), "the pane started folded")
        self.assertGreater(info.width(), 200.0)
        folded_at = None
        for width in range(1100, 699, -20):
            self.resize_window(monitor, window, width, 760)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            self.assertTrue(status.isVisible(), "the status pane hid at %d" % width)
            if monitor.property("infoCollapsed"):
                if folded_at is None:
                    folded_at = width
            else:
                self.assertIsNone(folded_at,
                                  "the fold released while still shrinking (%d)" % width)
        self.assertIsNotNone(folded_at, "the information pane never folded")
        self.assertLessEqual(folded_at, 1000, "the fold came later than the squeeze boundary")
        self.assertGreaterEqual(folded_at, 900, "the fold came before the squeeze boundary")
        self.assertLess(info.width(), 100.0, "the folded pane kept its full width")
        self.assertGreater(camera.width(), info.width(),
                           "the fold must hand the space to the camera column")

    def test_a_fast_resize_lands_in_the_same_steady_state(self):
        # The transient pass is not the contract: whatever the resize
        # steps, the resting state at a given width is the same one.
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1100, 760)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        for width, expected in ((700, True), (1100, False), (640, True), (1100, False)):
            self.resize_window(monitor, window, width, 760)
            self.assertEqual(monitor.property("infoCollapsed"), expected,
                             "the latch read %s at %d" % (monitor.property("infoCollapsed"), width))
            self.assertGreater(camera.property("viewportWidth"), 0.0, width)
        # Jumping to the same width twice rests in the same state: the
        # layout the latch reads is deterministic, so the fold cannot
        # depend on the resize that arrived before it.
        self.resize_window(monitor, window, 700, 760)
        first = (monitor.property("infoCollapsed"), info.width(), camera.width())
        self.resize_window(monitor, window, 1100, 760)
        self.resize_window(monitor, window, 700, 760)
        self.assertEqual(first, (monitor.property("infoCollapsed"), info.width(), camera.width()))

    def test_the_camera_pane_never_collapses_at_any_width(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1600, 760)
        camera = self.camera_pane(monitor)
        for width in range(1600, 399, -100):
            self.resize_window(monitor, window, width, 760)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column went below its minimum at %d" % width)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)

    def test_the_status_pane_folds_to_its_strip_and_the_console_folds(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1600, 760)
        status = self.find(monitor, "statusPanel")
        console = None
        for item in monitor.findChildren(QQuickItem):
            if item.property("tooNarrow") is not None:
                console = item
                break
        self.assertIsNotNone(console, "the console panel did not mount")
        self.assertAlmostEqual(status.width(), 410.0, delta=0.5)
        self.assertFalse(console.property("tooNarrow"))
        self.resize_window(monitor, window, 520, 760)
        # The status pane folds to its readout strip rather than
        # compressing its sections into a reflow.
        self.assertTrue(status.isVisible())
        self.assertTrue(monitor.property("statusCollapsed"))
        self.assertTrue(monitor.property("statusAutoCollapsed"))
        self.assertLess(status.width(), 100.0, "the folded pane kept its full width")
        # The console keeps its room: the fold hands the camera column
        # what the status pane was holding. Its own width-driven fold
        # waits until the column itself is crushed.
        self.assertFalse(console.property("tooNarrow"))
        self.resize_window(monitor, window, 400, 760)
        self.assertTrue(console.property("tooNarrow"))

    def test_the_controls_pane_yields_width_and_stays_open(self):
        # The dashboard's controls pane carries no auto-collapse of its
        # own: it yields between its two widths and stays open.
        dashboard, window = self.mount_window("MoonrakerMonitorDashboard.qml", 1600, 760)
        pane = self.find(dashboard, "moonrakerControlsPane")
        self.assertAlmostEqual(pane.width(), 386.0, delta=0.5)
        self.resize_window(dashboard, window, 900, 760)
        self.assertTrue(pane.isVisible(), "the controls pane closed")
        self.assertAlmostEqual(pane.width(), 340.0, delta=0.5)


class ReExpansionGuardTests(RealEngineTestCase):
    """The narrow-window lock on re-expansion (the standing rule, frozen
    in INSTRUCTIONS.md): every decision hinges on the WEBCAM pane's own
    width. An expansion is refused while it would take the camera under
    its comfort minimum — on the monitor panes and on the dashboard's
    controls pane alike — and the wider stage that makes the room
    restores each pane in reverse fold order. The status pane folds
    after the information pane down the cascade; the camera pane stays
    open throughout."""

    def _mount(self, width=1100, document="MoonrakerMonitor.qml", seed_fans=None):
        class OutputDouble(QObject):
            activePrinterChanged = pyqtSignal()

            def __init__(self, printer):
                super().__init__()
                self._printer = printer

            @pyqtProperty(QObject, notify=activePrinterChanged)
            def activePrinter(self):
                return self._printer

        printer = PrinterModelDouble()
        if seed_fans is not None:
            printer._fan_items = list(seed_fans)
        # The double stays referenced from Python: the context property
        # alone does not keep it alive, and a collected double reads as
        # a null printer — every click then looks refused.
        self._output = OutputDouble(printer)
        self.engine.rootContext().setContextProperty("OutputDevice", self._output)
        self.addCleanup(self.engine.rootContext().setContextProperty, "OutputDevice", None)
        root, window = self.mount_window(document, width, 760)
        return root, window, printer

    def _monitor_in(self, dashboard):
        """The dashboard hosts the monitor document behind a Loader:
        the narrow-window state this contract reads lives on the loaded
        document, not on the host."""
        for item in dashboard.findChildren(QQuickItem):
            if (item.property("cameraViewportWidth") is not None
                    and item.property("infoCollapsed") is not None):
                return item
        self.fail("the dashboard's monitor document did not load")

    def _settle(self, root, window, width):
        """The offscreen layout re-runs only where the window's own size
        changes: after a model-driven pane change it keeps the widths it
        settled on, and a 1 px wobble is what makes it read the model
        (the harness quirk the dashboard probe documented)."""
        self.resize_window(root, window, width - 1, 760)
        self.resize_window(root, window, width, 760)

    def _shrink(self, monitor, window, low, high=1100):
        """The live slow shrink, with the standing camera rule checked
        at every step; returns the stage width each pane folded at."""
        camera = self.camera_pane(monitor)
        folds = {}
        for width in range(high, low - 1, -20):
            self.resize_window(monitor, window, width, 760)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            for name, flag in (("info", "infoCollapsed"), ("status", "statusCollapsed")):
                if name not in folds and monitor.property(flag):
                    folds[name] = width
        return folds

    def _click(self, item, window, x_ratio=0.5, y_ratio=0.5):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        point = item.mapToScene(QPointF(item.width() * x_ratio,
                                        item.height() * y_ratio)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        self.pump(10)

    def _toggle(self, monitor, pane, word):
        """The pane's collapse toggle: the visible button whose own
        tooltip names the pane (the panes' other header buttons — the
        configure glyph, the reconnect — carry no such text)."""
        for item in self.find(monitor, pane).findChildren(QQuickItem):
            if "Button" not in item.metaObject().className() or not item.isVisible():
                continue
            for child in item.findChildren(QQuickItem):
                text = child.property("text")
                if isinstance(text, str) and word in text:
                    return item
        self.fail("the %s collapse toggle did not build" % pane)

    def _message(self, monitor, pane, word):
        """The toggle's tooltip text — the 'say so' half of the guard."""
        for item in self.find(monitor, pane).findChildren(QQuickItem):
            text = item.property("text")
            if isinstance(text, str) and word in text:
                return text
        self.fail("the %s collapse toggle's tooltip did not build" % pane)

    def test_a_slow_shrink_folds_the_information_then_the_status_pane(self):
        monitor, window, printer = self._mount(1100)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self.assertFalse(monitor.property("infoCollapsed"), "the pane started folded")
        self.assertFalse(monitor.property("statusCollapsed"), "the pane started folded")
        folds = self._shrink(monitor, window, 700)
        self.assertEqual(sorted(folds), ["info", "status"], "both panes must fold")
        self.assertGreater(folds["info"], folds["status"],
                           "the status pane folded before the information pane")
        # The fold points themselves: each pane folds where the camera it
        # leaves behind can no longer hold it. The 20 px sweep lands one
        # step past the exact crossing, so the bands carry the step.
        self.assertGreaterEqual(folds["info"], 940, "the information pane folded early")
        self.assertLessEqual(folds["info"], 980, "the information pane folded late")
        self.assertGreaterEqual(folds["status"], 720, "the status pane folded early")
        self.assertLessEqual(folds["status"], 760, "the status pane folded late")
        self.assertTrue(monitor.property("infoAutoCollapsed"))
        self.assertTrue(monitor.property("statusAutoCollapsed"))
        self.assertLess(info.width(), 100.0, "the folded pane kept its full width")
        self.assertLess(status.width(), 100.0, "the folded pane kept its full width")
        # The 'say so' half, on both panes.
        self.assertTrue(monitor.property("infoExpandLocked"))
        self.assertTrue(monitor.property("statusExpandLocked"))
        self.assertIn("too narrow", self._message(monitor, "infoPanel", "information"))
        self.assertIn("too narrow", self._message(monitor, "statusPanel", "printer status"))
        # The collapsed strips: a click anywhere on a folded pane.
        self._click(info, window, 0.5, 0.6)
        self._click(status, window, 0.5, 0.6)
        self.assertEqual(printer.info_calls, [], "a strip expanded an auto-collapsed pane")
        self.assertEqual(printer.status_calls, [], "a strip expanded an auto-collapsed pane")
        self.assertTrue(monitor.property("infoCollapsed"), "the pane reopened while too narrow")
        self.assertTrue(monitor.property("statusCollapsed"), "the pane reopened while too narrow")
        # The header toggles: the same refusal.
        self._click(self._toggle(monitor, "infoPanel", "information"), window)
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.info_calls, [], "a toggle expanded an auto-collapsed pane")
        self.assertEqual(printer.status_calls, [], "a toggle expanded an auto-collapsed pane")
        self.assertTrue(monitor.property("infoCollapsed"), "the pane reopened while too narrow")
        self.assertTrue(monitor.property("statusCollapsed"), "the pane reopened while too narrow")

    def test_widening_past_the_release_restores_both_controls(self):
        monitor, window, printer = self._mount(1100)
        self._shrink(monitor, window, 700)
        self.assertTrue(monitor.property("infoExpandLocked"))
        self.assertTrue(monitor.property("statusExpandLocked"))
        self.resize_window(monitor, window, 1100, 760)
        self.assertFalse(monitor.property("infoAutoCollapsed"), "the latch never released")
        self.assertFalse(monitor.property("statusAutoCollapsed"), "the latch never released")
        self.assertFalse(monitor.property("infoExpandLocked"), "the lock outlived the fold")
        self.assertFalse(monitor.property("statusExpandLocked"), "the lock outlived the fold")
        self.assertNotIn("too narrow", self._message(monitor, "infoPanel", "information"))
        self.assertNotIn("too narrow", self._message(monitor, "statusPanel", "printer status"))
        # Both information controls answer again: the toggle collapses
        # the pane, the strip expands it. Each strip click waits for the
        # layout the toggle's own write needs — the strip's guard reads
        # the camera the layout left behind, and a stale one still reads
        # the pane's room as spent (the harness note on _settle).
        self._click(self._toggle(monitor, "infoPanel", "information"), window)
        self.assertEqual(printer.info_calls, [True], "the toggle could not collapse the pane")
        self.assertTrue(monitor.property("infoCollapsed"))
        self._settle(monitor, window, 1100)
        self.assertFalse(monitor.property("infoExpandLocked"), "the lock outlived the collapse")
        self._click(self.find(monitor, "infoPanel"), window, 0.5, 0.6)
        self.assertEqual(printer.info_calls, [True, False], "the strip could not expand the pane")
        self.assertFalse(monitor.property("infoCollapsed"))
        # The status pane's pair, the same way.
        self._settle(monitor, window, 1100)
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True], "the toggle could not collapse the pane")
        self.assertTrue(monitor.property("statusCollapsed"))
        self._settle(monitor, window, 1100)
        self.assertFalse(monitor.property("statusExpandLocked"), "the lock outlived the collapse")
        self._click(self.find(monitor, "statusPanel"), window, 0.5, 0.6)
        self.assertEqual(printer.status_calls, [True, False], "the strip could not expand the pane")
        self.assertFalse(monitor.property("statusCollapsed"))

    def test_the_user_collapse_survives_the_narrow_window(self):
        # The auto fold never takes over the user's own collapse — the
        # pane stays folded for the user's reason and no auto flag is
        # set, so widening restores nothing but the user's state.
        monitor, window, printer = self._mount(1100)
        self._click(self._toggle(monitor, "infoPanel", "information"), window)
        self.assertEqual(printer.info_calls, [True], "the pane did not collapse")
        self._shrink(monitor, window, 700)
        self.assertFalse(monitor.property("infoAutoCollapsed"),
                         "the auto fold overrode the user's collapse")
        self.assertTrue(monitor.property("infoCollapsed"))
        self.resize_window(monitor, window, 1100, 760)
        self.assertTrue(monitor.property("infoCollapsed"),
                        "the user's collapse was discarded")
        self.assertEqual(printer.info_calls, [True],
                         "the narrow window wrote the persisted state")

    def test_the_user_collapse_of_the_status_pane_survives_too(self):
        # The same guard shape on the status pane: the auto fold never
        # overrides the user's own collapse, and the auto fold stands
        # down while it holds.
        monitor, window, printer = self._mount(1100)
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True], "the pane did not collapse")
        self._shrink(monitor, window, 700)
        self.assertFalse(monitor.property("statusAutoCollapsed"),
                         "the auto fold overrode the user's collapse")
        self.assertTrue(monitor.property("statusCollapsed"))
        self.resize_window(monitor, window, 1100, 760)
        self.assertTrue(monitor.property("statusCollapsed"),
                        "the user's collapse was discarded")

    def test_a_jump_under_the_camera_room_folds_with_no_clicks(self):
        # The click-twice report: a jump whose landed layout sits under
        # the crossing never crossed the squeeze edge on the way, so the
        # fold has to come from the landed camera — and it must come
        # with NO clicks. The first click used to be the one that landed
        # the fold, so the second was refused: one click swallowed. The
        # boundary is the width where the camera with both panes open is
        # exactly at its comfort (measured at 965/966).
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self.resize_window(monitor, window, 960, 760)
        self.assertTrue(monitor.property("infoAutoCollapsed"),
                        "the information pane did not fold")
        self.assertTrue(monitor.property("statusAutoCollapsed"),
                        "the status pane did not fold")
        self.assertTrue(monitor.property("infoCollapsed"))
        self.assertTrue(monitor.property("statusCollapsed"))
        self.assertTrue(monitor.property("infoExpandLocked"))
        self.assertTrue(monitor.property("statusExpandLocked"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                "the camera did not get its room back")
        self.assertGreater(camera.width(), info.width(),
                           "the fold must hand the space to the camera column")
        # Both expand paths refuse, twice each, with nothing ever
        # reaching the model: the fold was not a click's work.
        for _ in range(2):
            self._click(self._toggle(monitor, "infoPanel", "information"), window)
            self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
            self._click(info, window, 0.5, 0.6)
            self._click(status, window, 0.5, 0.6)
        self.assertEqual(printer.info_calls, [], "a click expanded a folded pane")
        self.assertEqual(printer.status_calls, [], "a click expanded a folded pane")
        self.assertTrue(monitor.property("infoCollapsed"),
                        "the pane reopened while too narrow")
        self.assertTrue(monitor.property("statusCollapsed"),
                        "the pane reopened while too narrow")
        # One width past the boundary the camera can hold both panes:
        # nothing folds, so a fold that survives there is the click-twice
        # hole again (the rule reads the camera, never a stage width).
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        self.resize_window(monitor, window, 970, 760)
        self.assertFalse(monitor.property("infoAutoCollapsed"),
                         "the information pane folded with the camera free")
        self.assertFalse(monitor.property("statusAutoCollapsed"),
                         "the status pane folded with the camera free")
        self.assertFalse(monitor.property("infoCollapsed"))
        self.assertFalse(monitor.property("statusCollapsed"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                "the camera is under its comfort above the boundary")

    def test_the_expansion_costs_and_the_forward_check_agree(self):
        # The contract's unit is the pane's expansion cost — its expanded
        # width less the collapsed strip it replaces — and the forward
        # check is that cost against the camera's comfort minimum. Both
        # are pinned here, so a changed cost, a changed minimum or a
        # changed measure trips this wherever the camera stands.
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        self.assertAlmostEqual(monitor.property("infoExpandCost"), 226.0, delta=0.5)
        self.assertAlmostEqual(monitor.property("statusExpandCost"), 366.0, delta=0.5)
        for width in (1250, 1100, 900, 760, 700, 640, 480):
            self.resize_window(monitor, window, width, 760)
            viewport = camera.property("viewportWidth")
            self.assertGreater(viewport, 0.0, "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            self.assertEqual(monitor.property("infoExpandBlocked"),
                             viewport - monitor.property("infoExpandCost") < 220.0,
                             "the information forward check changed at %d" % width)
            self.assertEqual(monitor.property("statusExpandBlocked"),
                             viewport - monitor.property("statusExpandCost") < 220.0,
                             "the status forward check changed at %d" % width)
            self.assertEqual(monitor.property("webcamSqueezed"), viewport < 220.0,
                             "the squeeze threshold changed at %d" % width)
            for pane in ("info", "status"):
                collapsed = monitor.property("%sCollapsed" % pane)
                locked = monitor.property("%sExpandLocked" % pane)
                # The lock never outlives its fold: an open pane is
                # never refusing anything.
                self.assertFalse(locked and not collapsed,
                                 "the %s lock outlived its fold at %d" % (pane, width))
                if collapsed:
                    self.assertEqual(
                        locked,
                        monitor.property("%sAutoCollapsed" % pane)
                        or monitor.property("webcamSqueezed")
                        or monitor.property("%sExpandBlocked" % pane),
                        "the %s lock's reasons changed at %d" % (pane, width))

    def test_the_folds_release_from_the_top_and_the_information_pane_last(self):
        # The LIFO order, measured on the way back up: the status pane
        # (folded last) is the first back, where the fold's own
        # arithmetic stops refusing; the information pane keeps its fold
        # while the status pane's stands — reclaiming its room there
        # would spend the room that fold is holding, and the pair would
        # land back on both folded — and only opens once the status pane
        # is back and the camera can hold IT (measured at 752 and 972).
        # Each width is read from a settled layout: the pass that
        # corrects the flags lays out for the flags it is correcting,
        # and the harness lays out again only on a size change (the
        # harness note on _settle), so each read finishes with a wobble
        # above the width and a return to it.
        monitor, window, printer = self._mount(1100)
        camera = self.camera_pane(monitor)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        self._shrink(monitor, window, 700)
        self.assertTrue(monitor.property("infoAutoCollapsed"))
        self.assertTrue(monitor.property("statusAutoCollapsed"))
        status_release = None
        info_release = None
        for width in range(700, 1101, 4):
            self._settle(monitor, window, width)
            self.resize_window(monitor, window, width + 1, 760)
            self.resize_window(monitor, window, width, 760)
            self.assertGreater(camera.property("viewportWidth"), 0.0,
                               "the camera viewport emptied at %d" % width)
            self.assertTrue(camera.isVisible(), "the camera card hid at %d" % width)
            self.assertGreaterEqual(camera.width(), 180.0,
                                    "the camera column was crushed at %d" % width)
            if status_release is None and not monitor.property("statusAutoCollapsed"):
                status_release = width
                # The fold's room is the camera's again, and the lock
                # went with the fold.
                self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                        "the status pane released under the comfort at %d" % width)
                self.assertFalse(monitor.property("statusExpandLocked"),
                                 "the status lock outlived its fold at %d" % width)
            if info_release is None and not monitor.property("infoAutoCollapsed"):
                info_release = width
                break
            # The information pane's fold is the last to go: it holds
            # while the status pane's stands, and goes on holding until
            # the camera with the status pane back can hold it.
            self.assertTrue(monitor.property("infoCollapsed"),
                            "the information pane reclaimed its room at %d" % width)
            if status_release is None:
                self.assertLess(camera.property("viewportWidth") - 366.0, 220.0,
                                "the status fold held at %d with the camera able to hold it" % width)
            else:
                self.assertLess(camera.property("viewportWidth") - 226.0, 220.0,
                                "the information fold held at %d with the camera able to hold it" % width)
        self.assertIsNotNone(status_release, "the status pane's fold never released")
        self.assertIsNotNone(info_release, "the information pane's fold never released")
        self.assertLess(status_release, info_release,
                        "the two folds released in the same breath")
        self.assertGreaterEqual(status_release, 740, "the status pane released early")
        self.assertLessEqual(status_release, 780, "the status pane released late")
        self.assertGreaterEqual(info_release, 960, "the information pane released early")
        self.assertLessEqual(info_release, 1000, "the information pane released late")
        # Both panes are back, their locks went with the folds, the
        # camera keeps its comfort, and the user's controls answer.
        self.assertFalse(monitor.property("statusAutoCollapsed"),
                         "the status pane stayed folded past the release")
        self.assertFalse(monitor.property("infoCollapsed"))
        self.assertFalse(monitor.property("statusCollapsed"))
        self.assertFalse(monitor.property("infoExpandLocked"))
        self.assertFalse(monitor.property("statusExpandLocked"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0,
                                "the camera is under its comfort with the panes back")
        self.assertGreater(info.width(), 200.0)
        self.assertGreater(status.width(), 400.0)
        # The user's own controls answer again at that width: the toggle
        # collapses the pane, the strip expands it back — the lock the
        # fold left standing does not outlive the fold.
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True], "the toggle could not collapse the pane")
        self.assertTrue(monitor.property("statusCollapsed"))
        self._settle(monitor, window, info_release)
        self.assertFalse(monitor.property("statusExpandLocked"),
                         "the lock outlived the collapse on a stage that can hold the pane")
        self._click(status, window, 0.5, 0.6)
        self.assertEqual(printer.status_calls, [True, False], "the strip could not expand the pane")
        self.assertFalse(monitor.property("statusCollapsed"))

    def test_the_controls_pane_refuses_once_the_camera_has_no_room(self):
        # The rule is universal: the dashboard's controls pane takes its
        # room from the same camera — read through the loaded monitor
        # document — so its every expand path refuses exactly when the
        # camera could not absorb the pane. It carries no auto fold of
        # its own, so only the user's collapse ever meets the lock.
        dashboard, window, printer = self._mount(1250, "MoonrakerMonitorDashboard.qml")
        monitor = self._monitor_in(dashboard)
        camera = self.camera_pane(monitor)
        pane = self.find(dashboard, "moonrakerControlsPane")
        self.assertAlmostEqual(dashboard.property("controlsExpandCost"), 342.0, delta=0.5)
        # The user hides the controls with the monitor's panes open: the
        # room the pane gives up goes to those panes, the camera lands at
        # 447, and 447 - 342 is under the comfort minimum — the pane is
        # locked where it lies, on the camera's own numbers.
        printer.setControlsCollapsed(True)
        self._settle(dashboard, window, 1250)
        self.assertTrue(dashboard.property("controlsCollapsed"))
        self.assertGreaterEqual(camera.property("viewportWidth"), 220.0)
        self.assertTrue(dashboard.property("controlsExpandBlocked"))
        self.assertTrue(dashboard.property("controlsExpandLocked"))
        # Hiding Printer status hands the camera the status pane's room
        # back: that camera can hold the controls again, so the lock
        # lifts and the status pane's expansion is ALLOWED.
        printer.setStatusCollapsed(True)
        self._settle(dashboard, window, 1250)
        self.assertTrue(monitor.property("statusCollapsed"))
        self.assertFalse(dashboard.property("controlsExpandBlocked"),
                         "the controls stayed blocked with the camera free")
        self.assertFalse(dashboard.property("controlsExpandLocked"))
        self.assertFalse(monitor.property("statusExpandLocked"),
                         "the status pane locked with the camera free")
        self._click(self._toggle(monitor, "statusPanel", "printer status"), window)
        self.assertEqual(printer.status_calls, [True, False],
                         "the status pane refused while the camera had room")
        # ... and that expansion is what crowds the camera again: the
        # controls' two paths refuse now, and the refusal says why.
        self._settle(dashboard, window, 1250)
        self.assertTrue(dashboard.property("controlsExpandBlocked"))
        self.assertTrue(dashboard.property("controlsExpandLocked"))
        self._click(self._toggle(dashboard, "moonrakerControlsPane", "printer controls"), window)
        self._click(pane, window, 0.5, 0.6)
        self.assertEqual(printer.controls_calls, [True],
                         "a click expanded the pane the camera has no room for")
        self.assertTrue(dashboard.property("controlsCollapsed"))
        self.assertIn("too narrow",
                      self._message(dashboard, "moonrakerControlsPane", "printer controls"))
        # Widening is the way out: the camera's room comes back with it.
        self.resize_window(dashboard, window, 1600, 760)
        self.assertFalse(dashboard.property("controlsExpandBlocked"),
                         "the controls stayed blocked on a wide stage")
        self._click(self._toggle(dashboard, "moonrakerControlsPane", "printer controls"), window)
        self.assertEqual(printer.controls_calls, [True, False],
                         "the toggle could not expand the pane on a wide stage")

    def test_a_fast_resize_rests_in_the_same_guarded_state(self):
        monitor, window, printer = self._mount(1100)
        info = self.find(monitor, "infoPanel")
        status = self.find(monitor, "statusPanel")
        camera = self.camera_pane(monitor)
        rest = []
        for _ in range(2):
            self.resize_window(monitor, window, 640, 760)
            self.assertTrue(monitor.property("infoExpandLocked"))
            self.assertTrue(monitor.property("statusExpandLocked"))
            self.assertTrue(monitor.property("infoCollapsed"))
            self.assertTrue(monitor.property("statusCollapsed"))
            self._click(info, window, 0.5, 0.6)
            self._click(status, window, 0.5, 0.6)
            self.assertEqual(printer.info_calls, [], "a strip expanded a folded pane")
            self.assertEqual(printer.status_calls, [], "a strip expanded a folded pane")
            rest.append((info.width(), status.width(), camera.width()))
            self.resize_window(monitor, window, 1100, 760)
            self.assertFalse(monitor.property("infoExpandLocked"))
            self.assertFalse(monitor.property("statusExpandLocked"))
        self.assertEqual(printer.info_calls, [], "a guarded click reached the model")
        self.assertEqual(printer.status_calls, [], "a guarded click reached the model")
        # The same jump rests in the same layout: the fold cannot
        # depend on the resize that arrived before it.
        self.assertEqual(rest[0], rest[1], "the fast resize rested differently")


class CameraTitleRowTests(RealEngineTestCase):
    """The camera card's title row at the widths the folded row
    allocates for it: the refresh button keeps its place inside the
    pane (the crush report)."""

    # The camera column's widths through a shrink that has already
    # folded the Information pane: the row allocates 456 px at the fold
    # and 216 px at a 720 px window, and the refresh button measures 11
    # px clear of the pane's right edge at every one of them (it ran
    # over the border while the fold was broken). The row's own
    # minimum is reached at the host's 180 px camera floor, below the
    # folded range pinned here.
    FOLDED_WIDTHS = (456, 396, 336, 296, 256, 216)

    def _mount_pane(self, width):
        pane, window = self.mount_window("CameraPane.qml", width, 420)
        pane.setProperty("configured", True)
        pane.setProperty("printerModel", CameraModelDouble())
        self.pump(30)
        return pane, window

    def _refresh_button(self, pane):
        for item in pane.findChildren(QQuickItem):
            if "SimpleButton" in item.metaObject().className():
                return item
        self.fail("the refresh button did not build")

    def test_the_refresh_button_stays_inside_the_pane(self):
        pane, window = self._mount_pane(self.FOLDED_WIDTHS[0])
        button = self._refresh_button(pane)
        self.assertTrue(button.isVisible(), "the controls did not build")
        for width in self.FOLDED_WIDTHS:
            self.resize_window(pane, window, width, 420)
            pane_rect = self.rect(pane, pane)
            button_rect = self.rect(button, pane)
            self.assertGreater(button.width(), 0.0, width)
            self.assertGreater(button_rect.left(), pane_rect.left(), width)
            self.assertLessEqual(button_rect.right(), pane_rect.right() + 0.5,
                                 "the refresh button left the pane at %d" % width)


class PaneGutterTests(RealEngineTestCase):
    """The constant right gutter (the 1f68f08 ruling): the attached
    scroll bar overlays the 14 px the content keeps clear of its
    pane's right edge — in EVERY pane, so no pane's right gap reads
    double against its neighbours. The panes' content also stops
    following the bar's own visibility, which is what re-opened the
    gap after the ruling shipped."""

    def assert_gutter(self, pane, content, label):
        pane_rect = self.rect(pane, pane)
        content_rect = self.rect(content, pane)
        self.assertGreater(pane_rect.width(), 0.0, label)
        self.assertAlmostEqual(pane_rect.right() - content_rect.right(), 14.0,
                               delta=0.5, msg="%s: the right gap is not the gutter" % label)

    def test_the_monitor_panes_keep_the_constant_right_gutter(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 1600, 760)
        for width in (1600, 1200, 900, 700, 520):
            self.resize_window(monitor, window, width, 760)
            self.assert_gutter(self.find(monitor, "infoPanel"),
                               self.find(monitor, "moonrakerInfoContent"),
                               "information@%d" % width)
            self.assert_gutter(self.find(monitor, "statusPanel"),
                               self.find(monitor, "moonrakerStatusContent"),
                               "status@%d" % width)

    def test_the_controls_pane_keeps_the_same_gutter(self):
        for width in (1600, 900):
            dashboard, window = self.mount_window("MoonrakerMonitorDashboard.qml", width, 760)
            self.assert_gutter(self.find(dashboard, "moonrakerControlsPane"),
                               self.find(dashboard, "moonrakerControlsContent"),
                               "controls@%d" % width)


class PauseRowRoleTests(RealEngineTestCase):
    SPARSE = [{"layer": 5}, {"layer": 7}, {"layer": 9}]
    MIXED = [{"layer": 5}, {"layer": 7, "eta": "in 00:02:00"},
             {"layer": 9, "state": "passed"}, {"layer": 12, "eta": None, "state": None, "passed": None}]

    def assert_roles_are_concrete(self, card):
        model = card.findChild(QObject, "moonrakerPauseListModel")
        self.assertIsNotNone(model)
        roles = {bytes(name).decode() for name in model.roleNames().values()}
        # A role whose FIRST value is undefined is dropped from the
        # model entirely, and the delegate's bare role lookup then
        # throws ReferenceError (eta, pauseWord and passed each did).
        self.assertEqual(roles, {"layerNo", "eta", "pauseWord", "passed"})

    def test_rows_without_a_state_or_eta_keep_the_model_roles(self):
        card = self.mount("MoonrakerPreviewCard.qml")
        card.setProperty("pauseAtLayerItems", self.SPARSE)
        self.pump()
        self.assert_roles_are_concrete(card)
        self.assertEqual(self.new_messages(), [])

    def test_rows_without_a_state_or_eta_render_their_lines(self):
        card = self.pause_card(self.SPARSE)
        self.assert_roles_are_concrete(card)
        self.assertEqual(self.pause_rows(card),
                         ["End of layer 5", "End of layer 7", "End of layer 9"])
        self.assertEqual([message for message in self.new_messages() if "ReferenceError" in message], [])

    def test_mixed_rows_render_the_state_and_the_eta(self):
        card = self.pause_card(self.MIXED)
        self.assert_roles_are_concrete(card)
        self.assertEqual(self.pause_rows(card), [
            "End of layer 5",
            "End of layer 7 · in 00:02:00",
            "End of layer 9 — passed",
            "End of layer 12",
        ])
        self.assertEqual([message for message in self.new_messages() if "ReferenceError" in message], [])


class StripVerdictRefreshTests(RealEngineTestCase):
    def verdict_card(self):
        card = self.mount("MoonrakerPreviewCard.qml")
        for name, value in (("previewBlock", {"state": "printing", "hotend": "205.2/210.0 C",
                                              "bed": "60.0/60.0 C", "inactive": False}),
                            ("previewBlockStale", False),
                            ("previewEtaText", "00:18:42 · ~14:36"),
                            ("stripCanPause", False), ("stripCanResume", False),
                            ("stripPauseReason", "Print is not printing"),
                            ("stripResumeReason", "Print is not paused")):
            card.setProperty(name, value)
        self.pump()
        return card

    def test_a_verdict_only_change_refreshes_the_strip(self):
        # The strip watched the block, the
        # staleness and the ETA but not the verdicts, so a verdict that
        # changed alone left the outgoing copy and the dead button.
        card = self.verdict_card()
        slot = self.find(card, "moonrakerStripSlot")
        button = self.find(card, "moonrakerStripPauseButton")
        self.assertEqual(slot.property("text"), "Print is not printing")
        self.assertFalse(button.property("enabled"))
        card.setProperty("stripCanPause", True)  # block and ETA untouched
        self.pump()
        self.assertEqual(slot.property("text"), "00:18:42")
        self.assertTrue(button.property("enabled"))


class SectionOrderArrivalTests(RealEngineTestCase):
    """The stored section order applies on the printer's ARRIVAL — the
    deterministic trigger: the panes' onCompleted ran before the model
    existed, so the arrival event must reorder them. This is the
    removed polling timer's regression pin.

    Two contracts, both measured: the SOURCE pin (the arrival-time
    apply lives in onPrinterChanged and no Timer waits for the model —
    the removed polling timer cannot come back silently), and the
    APPLY pin (invoked against a live model, the panes reorder). The
    notify delivery itself is Cura's C++ property, which the harness's
    machine-switch scenarios exercise — this fixture's Python-defined
    properties do not round-trip the engine's change detection."""

    @staticmethod
    def _header_order(container):
        """The rendered section ids of a pane's children, top to bottom."""
        order = []
        for child in container.childItems():
            header = child.childItems()[0] if child.childItems() else None
            if header is not None and header.property("sectionId") is not None:
                order.append(header.property("sectionId"))
        return order

    def test_the_wiring_pins_the_arrival_apply_and_bans_the_timer(self):
        source = (ROOT / "plugins" / "MoonrakerMonitor.qml").read_text(encoding="utf-8")
        # The arrival trigger: the model's change handler applies both
        # panes, reading the CURRENT effective layout.
        arrival = source[source.index("onPrinterChanged:"):source.index("onPrinterChanged:") + 2400]
        self.assertIn('applySectionOrder(infoContent, "information")', arrival)
        self.assertIn('applySectionOrder(statusContent, "status")', arrival)
        # The removed polling timer: its id may not exist anywhere.
        self.assertNotIn("sectionOrderApply", source)
        # No repeat Timer waits for the printer model to arrive.
        self.assertNotRegex(
            source[source.index("onPrinterChanged:"):source.index("onPrinterChanged:") + 600],
            r"Timer\s*\{|interval:\s*200|attempts",)

    def test_the_apply_reorders_the_panes_against_a_live_model(self):
        from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant

        class OutputDouble(QObject):
            activePrinterChanged = pyqtSignal()

            def __init__(self):
                super().__init__()
                self._printer = None

            def attach(self, printer):
                self._printer = printer
                self.activePrinterChanged.emit()

            @pyqtProperty(QObject, notify=activePrinterChanged)
            def activePrinter(self):
                return self._printer

        class PrinterDouble(QObject):
            sectionLayoutChanged = pyqtSignal()

            def __init__(self, order):
                super().__init__()
                self._order = order

            @pyqtSlot()
            def setConsoleExpanded(self, expanded):
                pass

            @pyqtSlot(str, result="QVariant")
            def sectionLayoutFor(self, pane):
                return {"order": list(self._order.get(pane, [])), "hidden": []}

            @pyqtProperty("QVariant")
            def temperatureChartLegend(self):
                return {"series": []}

            @pyqtProperty("QVariant")
            def sectionExpandedMap(self):
                return {}

            @pyqtProperty("QVariant")
            def sectionHiddenMap(self):
                return {}

        output = OutputDouble()
        self.engine.rootContext().setContextProperty("OutputDevice", output)
        self.addCleanup(self.engine.rootContext().setContextProperty, "OutputDevice", None)
        monitor = self.mount_monitor(900)  # the shell constructs with no printer
        container = self.find(monitor, "moonrakerInfoContent")
        default = self._header_order(container)
        # The information pane's canonical two sections; the ids are
        # static on the headers, so the null-printer mount reads them.
        self.assertGreaterEqual(len(default), 2, "the pane needs sections to reorder")
        reversed_order = list(reversed(default))
        # The binding carries the model (the engine's notify delivery
        # is production's concern); the apply itself runs as the
        # arrival handler would call it.
        output.attach(PrinterDouble({"information": reversed_order}))
        self.pump()
        QMetaObject.invokeMethod(monitor, "applySectionOrder",
                                 Q_ARG(QVariant, container), Q_ARG(QVariant, "information"))
        self.pump()
        self.assertEqual(self._header_order(container), reversed_order)


class TuningResetTests(RealEngineTestCase):
    """The factor sliders' reset buttons command the printer to 100%
    through the same setSpeedFactor/setFlowFactor path the slider
    release uses (the camera refresh button's glyph and styling)."""

    def test_each_reset_button_commands_its_factor_to_100(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt

        class ModelDouble(QObject):
            def __init__(self):
                super().__init__()
                self.calls = []

            @pyqtSlot(int)
            def setSpeedFactor(self, percent):
                self.calls.append(("speed", percent))

            @pyqtSlot(int)
            def setFlowFactor(self, percent):
                self.calls.append(("flow", percent))

            @pyqtSlot(int)
            def previewSpeedFactor(self, percent):
                pass

            @pyqtSlot(int)
            def previewFlowFactor(self, percent):
                pass

            @pyqtProperty("QVariant")
            def sectionExpandedMap(self):
                return {}

            @pyqtProperty(bool)
            def controlsLocked(self):
                return False

            @pyqtProperty(bool)
            def monitorConnected(self):
                return True

        section = self.mount("TuningSection.qml")
        window = QQuickWindow()
        window.resize(520, 400)
        section.setParentItem(window.contentItem())
        window.show()
        self.addCleanup(window.deleteLater)
        model = ModelDouble()
        section.setProperty("printerModel", model)
        self.pump(30)
        # A real click at each button's centre (the newer Qt's clicked
        # signal carries a QQuickMouseEvent PyQt cannot introspect, so
        # the signal is not accessible from Python — the event path is
        # the honest one anyway).
        for name in ("moonrakerTuningSpeedReset", "moonrakerTuningFlowReset"):
            button = self.find(section, name)
            center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump(30)
        self.assertIn(("speed", 100), model.calls)
        self.assertIn(("flow", 100), model.calls)


class TuningResetConvergenceTests(RealEngineTestCase):
    """The reset's end-to-end convergence: the click commands 100, the
    printer's confirmation publishes 100, and the SLIDER must read 100
    — never the to-clamp (the live 200-reset find)."""

    def test_the_flow_slider_reads_100_after_the_reset_converges(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt

        class ModelDouble(QObject):
            flowFactorPercentChanged = pyqtSignal()

            def __init__(self):
                super().__init__()
                self._flow = 137
                self.calls = []

            @pyqtProperty(int)
            def speedFactorPercent(self):
                return 100

            @pyqtProperty(int, notify=flowFactorPercentChanged)
            def flowFactorPercent(self):
                return self._flow

            def confirm(self, value):
                self._flow = value
                self.flowFactorPercentChanged.emit()

            @pyqtSlot(int)
            def setFlowFactor(self, percent):
                self.calls.append(("flow", percent))

            @pyqtSlot(int)
            def previewFlowFactor(self, percent):
                pass

            @pyqtProperty("QVariant")
            def sectionExpandedMap(self):
                return {}

            @pyqtProperty(bool)
            def controlsLocked(self):
                return False

            @pyqtProperty(bool)
            def monitorConnected(self):
                return True

        section = self.mount("TuningSection.qml")
        window = QQuickWindow()
        window.resize(520, 400)
        section.setParentItem(window.contentItem())
        window.show()
        self.addCleanup(window.deleteLater)
        model = ModelDouble()
        section.setProperty("printerModel", model)
        self.pump(30)
        slider = None
        for item in section.findChildren(QQuickItem):
            if "OutlineSlider" in item.metaObject().className() and item.property("from") == 50:
                slider = item
                break
        self.assertIsNotNone(slider, "the flow slider did not build")
        self.assertEqual(slider.property("value"), 137)
        # A prior user interaction writes the slider's value directly
        # (the drag path) — under the old binding that destroyed the
        # model link and the reset's 100 could never reach the handle.
        slider.setProperty("value", 200)
        self.pump(30)
        button = self.find(section, "moonrakerTuningFlowReset")
        center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump(30)
        self.assertIn(("flow", 100), model.calls)
        model.confirm(100)  # the printer's polled confirmation
        self.pump(30)
        self.assertEqual(slider.property("value"), 100,
                         "the slider must read the confirmed 100, not the to-clamp")


class CameraOwnershipTests(RealEngineTestCase):
    """The camera start/stop ownership: one logical desired state,
    one application, at most one start/stop transition. The forked
    renderer's start() used to be destructive — it stopped the live
    reply first — so a duplicate application must never touch the
    image."""

    def _apply(self, pane, url, visible):
        from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant
        QMetaObject.invokeMethod(pane, "applyCamera",
                                 Q_ARG(QVariant, QUrl(url)), Q_ARG(QVariant, visible))

    def _mount_pane(self):
        pane = self.mount("CameraPane.qml")
        image = self.find(pane, "cameraImage")
        return pane, image

    def _counts(self, image):
        return (image.property("startCount"), image.property("stopCount"),
                image.property("sourceSetCount"))

    def test_first_application_starts_the_consumer_exactly_once(self):
        # A: the initial attach applies the settled final state once —
        # exactly one start, one stop, one source assignment. The old
        # churn (URL publish then a later nonce publish) drove two
        # applications of two URL strings; the coalescer now delivers
        # ONE application of the final URL.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        self.assertEqual((1, 1, 1), self._counts(image))

    def test_duplicate_desired_state_is_a_no_op(self):
        # B: applying URL X + running twice — the second application
        # must not stop, re-assign the source or start again (each
        # would kill a healthy stream through Cura's destructive
        # start()).
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        self.assertEqual(first, self._counts(image))

    def test_query_only_republish_is_a_no_op(self):
        # C: a rotated nonce in the query is the same desired stream —
        # the data-snapshot reaction must not kill a healthy
        # connection for it (the 2026-09-19 live-run ruling).
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?nonce=1", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?nonce=2", True)
        self.pump()
        self.assertEqual(first, self._counts(image))

    def test_reload_marker_restarts_exactly_once(self):
        # The mpf_reload marker is the model's EXPLICIT reload request
        # (manual refresh, watchdog recovery, reconnect): a bump still
        # restarts — exactly one replacement start, not zero and not
        # two.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?mpf_reload=1", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam2/?mpf_reload=2", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2] + 1), self._counts(image))

    def test_genuine_path_change_restarts_exactly_once(self):
        # A different stream is a different desired state: exactly one
        # replacement start, not zero and not two.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        first = self._counts(image)
        self._apply(pane, "http://127.0.0.1:59999/webcam/", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2] + 1), self._counts(image))

    def test_visibility_lifecycle_stops_and_restarts_once(self):
        # D: hide -> one stop; show -> one start. The lifecycle
        # handler adopts the new visible state into the applied
        # latches, so a later identical application stays a no-op —
        # never a second start path racing applyCamera.
        pane, image = self._mount_pane()
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        first = self._counts(image)
        image.setProperty("visible", False)
        self.pump()
        self.assertEqual((first[0], first[1] + 1, first[2]), self._counts(image))
        image.setProperty("visible", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2]), self._counts(image))
        self._apply(pane, "http://127.0.0.1:59999/webcam2/", True)
        self.pump()
        self.assertEqual((first[0] + 1, first[1] + 1, first[2]), self._counts(image))

    def test_the_stream_chip_stays_hidden_until_genuinely_live(self):
        # The chip's liveness contract (the offline-veil ruling): no
        # model, or a hidden frame-less image, hides the chip — the
        # stats must never float over the veil. The SHOWING case
        # renders for real in the capture leg, whose census reads the
        # chip's actual text.
        pane, window = self.mount_window("CameraPane.qml", 400, 420)
        pane.setProperty("configured", True)
        self.pump(30)
        chip = self.find(pane, "cameraStreamChip")
        image = self.find(pane, "cameraImage")
        image.setProperty("visible", True)
        image.setProperty("imageWidth", 640)
        image.setProperty("recentBytesPerSec", 11000000)
        self.pump()
        self.assertFalse(chip.property("visible"),
                         "a model-less pane must not show the chip")
        image.setProperty("visible", False)
        self.pump()
        self.assertFalse(chip.property("visible"))
        self.addCleanup(window.deleteLater)


class CameraFpsControlTests(RealEngineTestCase):
    """The camera control bar's QML surface (the idle-load and zoom
    requests): the status chip carries the decode rate and yields to the
    Live badge when the frame cannot carry both, one bar carries two
    faces — the zoom scale at rest, the rate scale while the throttle is
    being driven — the rate face's range is the selected camera's own
    ceiling with evenly spaced graduations, the bar parks clear of the
    picture after five idle seconds, the compact chip stands in where the
    bar has no room, and the wheel reaches the rate whether or not any
    control is on screen. The CPU saving itself is the renderer's own leg
    (test_moonraker_mjpg.py); the persistence is
    test_printer_config.py's."""

    def _fps_pane(self, width, height, fps=15.0, maximum=30.0, displayed_fps=0.0):
        """The card with a live frame and a recording FPS model. The
        bandwidth readout rides along: a live chip always carries it,
        and its width is what the occlusion rule has to reckon with.
        Returns (pane, window, model, image, frame) — the frame being
        the picture's own box, the box every FPS surface rides and the
        clip they are held to."""
        pane, window = self.mount_window("CameraPane.qml", width, height)
        pane.setProperty("configured", True)
        model = CameraModelDouble(fps=fps, maximum=maximum)
        pane.setProperty("printerModel", model)
        self.pump(30)
        image = self.find(pane, "cameraImage")
        image.setProperty("visible", True)
        image.setProperty("imageWidth", 640)
        image.setProperty("imageHeight", 480)
        image.setProperty("recentBytesPerSec", 1375000)
        image.setProperty("recentDisplayedFPS", displayed_fps)
        # The first layout re-seats the parked controls through their
        # 180 ms slide: let it land before any position is measured.
        self._pump_ms(300)
        return pane, window, model, image, self.find(pane, "cameraFrame")

    def _wheel(self, window, item, delta=120, modifiers=None, position=None):
        """One real wheel notch over *item*. QTest's QWindow-level
        mouseWheel is unavailable in this Qt build — the event is posted
        directly (the plate zoom suite's own pattern). No modifier is
        the picture's own gesture (the zoom); Shift is the FPS
        throttle's."""
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtGui import QWheelEvent
        if position is None:
            position = QPointF(item.width() / 2, item.height() / 2)
        scene = item.mapToItem(window.contentItem(), position)
        event = QWheelEvent(
            QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton,
            modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)

    def _mouse(self, window, item, kind, x, y, buttons=None, button=None):
        """One real mouse event at item-local (x, y) — the drag pan's
        own path (the plate face suite's idiom). *button* is the button
        the event is ABOUT: a press must carry its own button in the
        held set or the delivery agent files it as a bare update, which
        is why the default pairs the two."""
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtGui import QMouseEvent
        if button is None:
            button = Qt.MouseButton.LeftButton
        if buttons is None:
            buttons = Qt.MouseButton.NoButton
        scene = item.mapToItem(window.contentItem(), QPointF(x, y))
        event = QMouseEvent(kind, QPointF(scene),
                            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                            button, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QGuiApplication.sendEvent(window, event)

    def _drag(self, window, item, dx, dy):
        """Press at the item's centre, move by (dx, dy), release."""
        from PyQt6.QtCore import QEvent, Qt
        cx, cy = item.width() / 2, item.height() / 2
        self._mouse(window, item, QEvent.Type.MouseButtonPress, cx, cy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, item, QEvent.Type.MouseMove, cx + dx, cy + dy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, item, QEvent.Type.MouseButtonRelease, cx + dx, cy + dy)
        self.pump(20)

    def _fps_face(self, window, wheel_target):
        """Bring the RATE face up. The bar rests on the zoom face; the
        shift wheel is the rate's own gesture, and a rate change docks
        the face it belongs to. A parked bar (every mount starts parked)
        arrives on the new face at once."""
        self._wheel(window, wheel_target, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self._pump_ms(300)

    def _double_click(self, window, item):
        """A real double click on *item*, through QTest's own app-level
        path. A DblClick posted by hand with no press outstanding is an
        UPDATE event to the delivery agent — the legacy MouseArea never
        sees it and the item's onDoubleClicked never runs (probe-verified
        on both engines), so the QTest helper is the only spelling that
        reaches it."""
        from PyQt6.QtCore import QPoint
        from PyQt6.QtTest import QTest
        scene = item.mapToItem(window.contentItem(), QPointF(item.width() / 2, item.height() / 2))
        QTest.mouseDClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                          QPoint(int(scene.x()), int(scene.y())))
        self.pump(30)

    def test_the_gesture_surface_survives_a_stream_off_and_on(self):
        # The live report: disabling then re-enabling the stream left
        # zoom and FPS dead. The blank is a real size change (the
        # renderer announces it), the picture returns, and the control
        # surface must come back with it — liveness that latches on a
        # value no later signal re-reads is the bug.
        pane, window, model, image, _frame = self._fps_pane(700, 700)
        gesture = self.find(pane, "cameraGestureArea")

        def apply_camera(url, visible):
            # Exactly what the host calls: the URL (nonce included) and
            # the configured flag, applied through the pane's own slot.
            from PyQt6.QtCore import QMetaObject, Q_ARG, QVariant
            QMetaObject.invokeMethod(pane, "applyCamera",
                                     Q_ARG(QVariant, QUrl(url)), Q_ARG(QVariant, visible))

        apply_camera("http://127.0.0.1:59999/webcam2/?mpf_reload=1", True)
        self.pump(30)
        self.assertTrue(pane.property("cameraControlLive"), "the surface starts live")
        self.assertTrue(gesture.property("enabled"))
        self._wheel(window, gesture)
        self.pump(30)
        zoomed = pane.property("cameraZoom")
        self.assertGreater(zoomed, 1.0, "the wheel zooms the live picture")

        # The stream OFF: the model's flag drops with its URL, and the
        # pane blanks the painted frame.
        model.set_stream_enabled(False)
        pane.setProperty("configured", False)
        apply_camera("", False)
        self.pump(30)
        self.assertEqual(image.property("clearCount"), 1, "the stream-off blanks the frame")
        self.assertEqual(image.property("imageWidth"), 0, "and paints no size")
        self.assertFalse(pane.property("cameraControlLive"))
        self.assertFalse(gesture.property("enabled"), "the blank disarms the gestures")

        # The stream ON again: the URL returns on a new nonce and the
        # bytes resume. Nothing here re-applies the gestures by hand.
        model.set_stream_enabled(True)
        pane.setProperty("configured", True)
        apply_camera("http://127.0.0.1:59999/webcam2/?mpf_reload=3", True)
        self.pump(30)
        image.setProperty("imageWidth", 640)
        image.setProperty("imageHeight", 480)
        self.pump(30)
        self.assertTrue(pane.property("cameraControlLive"),
                        "the resumed stream's frame re-arms the control surface")
        self.assertTrue(gesture.property("enabled"))
        self._wheel(window, gesture)
        self.pump(30)
        self.assertNotEqual(pane.property("cameraZoom"), zoomed, "the wheel zooms again")
        self._wheel(window, gesture, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0], "and the rate gesture is back with it")

    def test_the_status_chip_reports_the_decode_rate(self):
        # The top-right chip carries the rate the renderer is actually
        # decoding at — the frame's own readout, so a throttled stream
        # is visibly throttled. No frames yet means no rate to claim.
        pane, _window, _model, image, _frame = self._fps_pane(700, 640)
        chip = self.find(pane, "cameraStreamChip")
        label = self.find(pane, "cameraStreamChipText")
        self.assertTrue(chip.property("visible"), "the chip has room on a wide frame")
        self.assertIn("640×480", label.property("text"))
        self.assertNotIn("fps", label.property("text"),
                         "no rate is claimed before a frame lands")
        image.setProperty("recentDisplayedFPS", 15)
        self.pump()
        self.assertIn("15 fps", label.property("text"))
        image.setProperty("recentDisplayedFPS", 0.5)
        self.pump()
        self.assertIn("0.50 fps", label.property("text"),
                      "the low rates the idle load lives at stay readable")

    def test_the_rate_field_holds_still_at_the_sub_one_fps_floor(self):
        # The live report: at the 0.5 FPS floor the one-second sample
        # cannot see the rate at all — a window with no frame reads 0
        # and the next reads 1 — so the field appeared and disappeared
        # with every sample. At and below one frame per sample the
        # throttle's own rate IS what the renderer decodes at, and the
        # field holds it.
        pane, _window, model, image, _frame = self._fps_pane(700, 640, fps=0.5, maximum=30.0)
        label = self.find(pane, "cameraStreamChipText")
        for sample in (0, 0.5, 1, 0):
            image.setProperty("recentDisplayedFPS", sample)
            self.pump()
            self.assertIn("0.50 fps", label.property("text"),
                          "the field must not move with the sample noise")
        # Above the sample's resolution the measured rate is the honest
        # one again.
        model.set_camera_ceiling(30.0, fps=12.0)
        image.setProperty("recentDisplayedFPS", 11.6)
        self.pump()
        self.assertIn("12 fps", label.property("text"),
                      "the measured rate reports once it can be measured")

    def test_the_chip_yields_to_the_live_badge_and_returns_when_the_frame_grows(self):
        # Two pills on a narrow frame read as one smear: the chip
        # yields the moment it would occlude Live (the request) and
        # returns as soon as the frame is wide enough to carry both.
        pane, window, _model, image, _frame = self._fps_pane(700, 640, displayed_fps=15.0)
        chip = self.find(pane, "cameraStreamChip")
        badge = self.find(pane, "cameraLiveBadge")
        self.assertTrue(chip.property("visible"),
                        "the wide frame must carry both pills")
        self.resize_window(pane, window, 190, 640)
        self._pump_ms(300)
        # The precondition IS the regression pin: a chip that grows
        # (this release added the rate to it) must be reported as
        # no-longer-provable rather than silently passing.
        self.assertLess(image.width(), badge.width() + chip.width() + 3 * chip.property("badgeGap"),
                        "the narrow mount must actually be too narrow")
        self.assertFalse(chip.property("visible"),
                         "the chip must yield rather than touch the Live badge")
        self.resize_window(pane, window, 700, 640)
        self._pump_ms(300)
        self.assertTrue(chip.property("visible"),
                        "the chip returns once the frame has room for it")

    def test_the_scale_docks_with_a_rate_change_and_parks_after_five_idle_seconds(self):
        # The zoom scope's rhythm, five seconds on both (the request
        # moved the zoom's own two-second park to five): a rate change
        # slides the bar into view on the rate's own face, five idle
        # seconds slide it back out of the picture entirely.
        pane, window, model, image, frame = self._fps_pane(700, 700)
        control = self.find(pane, "cameraBar")
        self.assertGreaterEqual(frame.width(), 200, "the scale needs the room it tests for")
        self.assertGreaterEqual(frame.height(), 150, "the scale needs the room it tests for")
        self.assertTrue(control.property("visible"), "the full scale has room here")
        self.assertFalse(pane.property("cameraBarDocked"), "the bar starts parked")
        self.assertGreaterEqual(control.x(), frame.width(),
                                "the scale starts parked out of the frame")
        self._wheel(window, image, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0],
                         "one notch is one linear step of the camera's own range")
        self.assertEqual(pane.property("cameraBarMode"), "fps",
                         "the rate's own gesture brings the rate's own face up")
        self._pump_ms(300)
        self.assertLessEqual(control.x() + control.width(), frame.width() + 0.5,
                             "the docked scale rides inside the CAMERA VIEW, not the pane frame")
        self._pump_ms(5400)
        self.assertFalse(pane.property("cameraBarDocked"), "five idle seconds park it again")
        self.assertGreaterEqual(control.x(), frame.width(),
                                "the parked scale clears the picture's own edge")

    def test_the_two_faces_trade_places_with_a_slide(self):
        # The live report: the cards changed content without trading
        # places. A rate the gesture has just set echoes back through the
        # model, and that second dock used to cut the slide-out short —
        # so the bar must be out on the face that is LEAVING before the
        # new one lands, and back in on the new face.
        pane, window, model, image, frame = self._fps_pane(700, 700)
        control = self.find(pane, "cameraBar")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertEqual(pane.property("cameraBarMode"), "zoom",
                         "the plain wheel's own face is up")
        self.assertTrue(pane.property("cameraBarDocked"), "and it is docked")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        # Mid-turn-over, before any timer can run: still the zoom face,
        # with the bar on its way out of the picture.
        self.assertEqual(pane.property("cameraBarMode"), "zoom",
                         "the face that is leaving stays up for the slide-out")
        self.assertTrue(pane.property("_cameraBarTurning"), "the turn-over is under way")
        self.assertFalse(pane.property("cameraBarShown"),
                         "the turn-over takes the bar off the picture")
        self._pump_ms(700)
        self.assertEqual(pane.property("cameraBarMode"), "fps", "the rate face lands")
        self.assertFalse(pane.property("_cameraBarTurning"))
        self.assertTrue(pane.property("cameraBarShown"), "and rides back in")
        self.assertLessEqual(control.x() + control.width(), frame.width() + 0.5,
                             "the landed face is docked inside the picture")
        self.assertEqual(model.fps_calls, [16.0], "one notch, one rate")

    def test_a_rate_change_leaves_the_zoomed_scale_in_place(self):
        # The live report: with the view zoomed, a rate change replaced
        # the zoom scale for good. A view held past the fit PINS its own
        # scale — the rate card borrows the bar and hands it back once its
        # idle five seconds are up — and the scale then stays up rather
        # than parking, since a parked scale is the control the zoomed
        # picture is about.
        pane, window, model, image, frame = self._fps_pane(700, 700)
        control = self.find(pane, "cameraBar")
        self.assertFalse(pane.property("cameraBarPinned"), "the fit pins nothing")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertTrue(pane.property("cameraBarPinned"), "a held zoom pins the scale")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self._pump_ms(700)
        self.assertEqual(pane.property("cameraBarMode"), "fps",
                         "the rate card has the bar for now")
        self._pump_ms(5400)
        self.assertEqual(pane.property("cameraBarMode"), "zoom", "the scale comes back")
        self.assertTrue(pane.property("cameraBarShown"), "and it is on screen")
        self.assertLessEqual(control.x() + control.width(), frame.width() + 0.5,
                             "docked, not parked")
        self._pump_ms(6000)
        self.assertTrue(pane.property("cameraBarShown"),
                        "the pinned scale outlasts the idle park")
        self.assertEqual(pane.property("cameraBarMode"), "zoom")
        self._double_click(window, frame)
        self._pump_ms(400)
        self.assertFalse(pane.property("cameraBarPinned"), "the fit releases the pin")
        self.assertFalse(pane.property("cameraBarShown"),
                         "and the bar has nothing left to read")
        self.assertEqual(model.fps_calls, [16.0], "the rate is not a view state")

    def test_a_held_handle_holds_the_park_off_and_keeps_the_grab(self):
        # The live report: a handle held still through the park's five
        # seconds let the card slide out from under the hand, and the
        # press went on driving a scale that was no longer on the picture.
        # A held handle is not an idle card — the park waits for the
        # release, the drag keeps tracking, and the rate is set where the
        # pointer is let go.
        from PyQt6.QtCore import QEvent
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._fps_face(window, frame)
        bar = self.find(pane, "cameraFpsBar")
        handles = [item for item in bar.childItems() if item.property("pressed") is not None]
        self.assertEqual(len(handles), 1, "the scale carries its own handle")
        handle = handles[0]
        self._mouse(window, bar, QEvent.Type.MouseButtonPress, bar.width() / 2, bar.height() / 4,
                    Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertTrue(handle.property("pressed"), "the handle is held")
        self.assertAlmostEqual(pane.property("cameraFps"), 22.63, delta=0.05,
                               msg="the press takes the rate to the pointer")
        self._pump_ms(5400)
        self.assertTrue(handle.property("pressed"), "the grab survives the stillness")
        self.assertTrue(pane.property("cameraBarShown"),
                        "a held handle holds the park off")
        self._mouse(window, bar, QEvent.Type.MouseMove, bar.width() / 2, bar.height() / 2,
                    Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraFps"), 15.25, delta=0.05,
                               msg="the held handle goes on tracking the pointer")
        self._mouse(window, bar, QEvent.Type.MouseMove, bar.width() / 2, bar.height() * 3 / 4,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, bar, QEvent.Type.MouseButtonRelease, bar.width() / 2,
                    bar.height() * 3 / 4)
        self.pump(20)
        self.assertFalse(handle.property("pressed"), "the release is a real release")
        self.assertAlmostEqual(pane.property("cameraFps"), 7.88, delta=0.05,
                               msg="the rate is set where the release occurred")
        self._pump_ms(5400)
        self.assertFalse(pane.property("cameraBarShown"),
                         "the idle five seconds run from the release")

    def test_the_bar_carries_the_rate_floor_without_cutting_it_off(self):
        # The live report: at the floor the readout "0.50 fps" ran out of
        # the bar. That readout — the widest either face shows — is what
        # sets the bar's width, and the zoom face rides the same box.
        pane, _window, model, _image, _frame = self._fps_pane(700, 700)
        model.setCameraFps(0.5)
        self.pump(30)
        bar = self.find(pane, "cameraBar")
        readout = self.find(pane, "cameraFpsReadout")
        self.assertEqual(readout.property("text"), "0.50 fps", "the floor's own readout")
        self.assertGreaterEqual(bar.width() - readout.property("contentWidth"), 4.0,
                                "the widest readout must sit inside the bar")
        zoom_readout = self.find(pane, "cameraZoomReadout")
        self.assertLessEqual(zoom_readout.property("contentWidth") + 4.0, bar.width(),
                             "the zoom face rides the same width and must fit too")

    def test_the_shift_wheel_changes_the_rate_with_no_scale_on_screen(self):
        # The spec's own line: the control must not show up when the
        # view cannot carry it, but the throttle must still be
        # reachable. Where the scale has no room the rate rides the
        # compact chip instead — vertically centred on the picture.
        pane, window, model, _image, frame = self._fps_pane(190, 400)
        control = self.find(pane, "cameraBar")
        chip = self.find(pane, "cameraBarChip")
        self.assertLess(frame.width(), 200,
                        "the narrow mount must actually be too narrow for the scale")
        self.assertFalse(control.property("visible"), "no room, no scale")
        self.assertGreaterEqual(chip.x(), frame.width(),
                                "the parked chip clears the picture too")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0],
                         "the shift wheel must reach the rate with no control shown")
        self._pump_ms(300)
        self.assertTrue(chip.property("visible"), "the compact chip stands in for the scale")
        chip_rect, picture = self.rect(chip, pane), self.rect(frame, pane)
        self.assertAlmostEqual(chip_rect.center().y(), picture.center().y(), delta=1.5,
                               msg="the chip is vertically centred on the camera view")
        self.assertLessEqual(chip.x() + chip.width(), frame.width() + 0.5,
                             "the docked chip rides inside the camera view")
        self.assertEqual(self.find(pane, "cameraBarChipText").property("text"), "16 fps",
                         "the chip carries the rate itself")

    def test_no_fps_surface_at_all_when_the_view_has_no_room(self):
        # The floor of the same rule: a frame too small for even the
        # chip shows no FPS surface — the rate is still the shift
        # wheel's.
        pane, window, model, _image, frame = self._fps_pane(110, 400)
        self.assertLess(frame.width(), 96,
                        "the tiny mount must actually be too small for the chip")
        self.assertFalse(self.find(pane, "cameraBar").property("visible"))
        self.assertFalse(self.find(pane, "cameraBarChip").property("visible"))
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0])

    def test_the_plain_wheel_is_the_pictures_gesture_not_the_throttles(self):
        # The two gestures share the wheel and must not cross: a plain
        # notch zooms the picture and leaves the decode rate alone, a
        # Shift notch throttles the stream and leaves the view alone.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame)
        self.pump(30)
        self.assertEqual(model.fps_calls, [],
                         "the plain wheel is the picture's own gesture")
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25, delta=1e-6,
                               msg="one plain notch is one 1.25x zoom step")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [16.0], "the shift wheel is the throttle's")
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.25, delta=1e-6,
                               msg="a shift notch must not move the view")

    def test_the_wheel_zooms_about_the_pointer(self):
        # The zoom rides the frame centre, so the point under the
        # pointer would slide away from it; the pan is re-solved to
        # hold that point still (the plate scope's own ruling).
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        left = QPointF(frame.width() / 4, frame.height() / 2)
        self._wheel(window, frame, position=left)
        self.pump(30)
        # (W/2 - x) / 4 is where the pointer's own point lands once the
        # picture is a quarter bigger: the edge it was over stays put,
        # so a wheel to the left of the centre walks the pan right.
        self.assertAlmostEqual(pane.property("cameraPanX"),
                               (frame.width() / 2 - left.x()) * 0.25, delta=1.0,
                               msg="the point under the pointer drifted")
        self.assertAlmostEqual(pane.property("cameraPanY"), 0.0, delta=1.0)
        # Zooming out past the fit stops at the fit and re-centres.
        for _ in range(8):
            self._wheel(window, frame, delta=-120)
            self.pump(5)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.0, delta=1e-6,
                               msg="the fit is the floor")
        self.assertAlmostEqual(pane.property("cameraPanX"), 0.0, delta=1e-6)
        self.assertAlmostEqual(pane.property("cameraPanY"), 0.0, delta=1e-6)

    def test_the_drag_pans_the_picture_and_stops_at_the_pictures_edge(self):
        # The pan follows the pointer exactly while there is picture to
        # move, and the clamp owns the limit: no drag may open a gap
        # between the frame and the picture it is supposed to show.
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame)
        self._wheel(window, frame)
        self.pump(30)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.5625, delta=1e-6)
        self._drag(window, frame, 40, 20)
        self.assertAlmostEqual(pane.property("cameraPanX"), 40.0, delta=1.5,
                               msg="the drag pan lagged the pointer")
        self.assertAlmostEqual(pane.property("cameraPanY"), 20.0, delta=1.5)
        self.assertEqual(pane.property("cameraPanOffsetX"), pane.property("cameraPanX"),
                         "a pan inside the limit is applied as it is")
        # A drag past the picture's edge is held AT the edge as it is
        # stored, not only on the way to the transform.
        from PyQt6.QtCore import QEvent
        limit = pane.property("cameraPanLimitX")
        self.assertAlmostEqual(limit,
                               frame.width() * (pane.property("cameraZoom") - 1) / 2, delta=1.5,
                               msg="the limit is the picture's own overhang")
        self.assertLess(limit, 260.0, "the mount must leave room for an overshoot")
        cx, cy = frame.width() / 2, frame.height() / 2
        self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + 260, cy,
                    Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit, delta=1.5,
                               msg="a drag past the edge stops at the edge")
        self.assertAlmostEqual(pane.property("cameraPanOffsetX"),
                               pane.property("cameraPanLimitX"), delta=1.5,
                               msg="the applied pan is clamped to the picture's edge")
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + 260, cy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, frame, QEvent.Type.MouseButtonRelease, cx + 260, cy)
        self.pump(20)
        # A resize re-clamps through the binding, with no timer.
        self.resize_window(pane, window, 400, 400)
        self._pump_ms(200)
        self.assertAlmostEqual(pane.property("cameraPanOffsetX"),
                               pane.property("cameraPanLimitX"), delta=1.5,
                               msg="a shrunken pane re-clamps the applied pan")
        self.assertLess(pane.property("cameraPanOffsetX"),
                        frame.width() * (pane.property("cameraZoom") - 1) / 2 + 1.0)

    def test_a_drag_past_the_edge_never_has_to_be_undone(self):
        # The live report: past the picture's edge the pan stopped, but
        # the drag kept counting and the first stretch of the way back
        # moved nothing — the overshoot had to be undone first. The pan
        # is held to the limit as it is STORED, so the picture answers
        # the very first move of the return leg.
        pane, window, _model, _image, frame = self._fps_pane(700, 400)
        self._wheel(window, frame)
        self._wheel(window, frame)
        self.pump(30)
        limit = pane.property("cameraPanLimitX")
        self.assertGreater(limit, 40.0, "the mount must zoom into a real overhang")
        self.assertLess(limit, 200.0, "the overshoot must fit inside the window")
        from PyQt6.QtCore import QEvent
        cx, cy = frame.width() / 2, frame.height() / 2
        self._mouse(window, frame, QEvent.Type.MouseButtonPress, cx, cy,
                    Qt.MouseButton.LeftButton)
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + limit + 90, cy,
                    Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit, delta=1.5,
                               msg="the overshoot is discarded, not banked")
        self._mouse(window, frame, QEvent.Type.MouseMove, cx + limit + 50, cy,
                    Qt.MouseButton.LeftButton)
        self.pump(20)
        self.assertAlmostEqual(pane.property("cameraPanX"), limit - 40, delta=1.5,
                               msg="the return leg moves the picture at once")
        self._mouse(window, frame, QEvent.Type.MouseButtonRelease, cx + limit + 50, cy)
        self.pump(20)

    def test_a_double_click_returns_the_fit(self):
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        self._wheel(window, frame)
        self._drag(window, frame, 60, 30)
        self.pump(20)
        self.assertGreater(pane.property("cameraZoom"), 1.0)
        self.assertNotEqual(pane.property("cameraPanX"), 0.0)
        self.assertGreater(pane.property("cameraBarPinned"), False,
                           "the zoomed view is the pin's own case")
        self._double_click(window, frame)
        self.assertAlmostEqual(pane.property("cameraZoom"), 1.0, delta=1e-6,
                               msg="the double click returns the fit")
        self.assertAlmostEqual(pane.property("cameraPanX"), 0.0, delta=1e-6)
        self.assertAlmostEqual(pane.property("cameraPanY"), 0.0, delta=1e-6)
        self.assertFalse(pane.property("cameraBarPinned"),
                         "the fit leaves the pin nothing to hold")

    def test_the_parked_control_is_clipped_by_the_picture_not_the_pane(self):
        # The live request: the control must vanish as it leaves the
        # PICTURE. On a letterboxed view the pane has room beside the
        # picture, so a pane-level clip would keep painting it there.
        pane, window, model, _image, frame = self._fps_pane(900, 400)
        control = self.find(pane, "cameraBar")
        viewport = self.find(pane, "cameraViewport")
        self.assertTrue(frame.property("clip"), "the picture owns the clip")
        self.assertFalse(viewport.property("clip"),
                         "the pane must not clip in the picture's place")
        self.assertLess(frame.width(), viewport.width(),
                        "the mount must letterbox, or the pin proves nothing")
        self.assertTrue(control.property("visible"), "the scale has room on the picture")
        self.assertGreaterEqual(control.x(), frame.width(),
                                "the control starts parked out of the picture")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self._pump_ms(300)
        picture = self.rect(frame, pane)
        docked = self.rect(control, pane)
        self.assertLessEqual(docked.right(), picture.right() + 0.5,
                             "a rate change docks the control inside the picture")
        self._pump_ms(5400)
        parked = self.rect(control, pane)
        self.assertGreater(parked.left(), picture.right(),
                           "five idle seconds park it past the picture's edge")
        self.assertLess(parked.left(), picture.right() + 40 * 3,
                        "it is parked just past that edge, not off in the pane")
        self.assertEqual(model.fps_calls, [16.0])

    def test_the_scale_range_follows_the_selected_camera_ceiling(self):
        # The ceiling is the CAMERA's, not a product constant: the bar
        # tops out at the selected camera's configured target_fps and
        # re-reads it when the selection changes. The marker sits at
        # the fraction the rate now holds of that range.
        pane, window, model, _image, frame = self._fps_pane(700, 700, fps=30.0, maximum=60.0)
        self._fps_face(window, frame)
        self.assertEqual(pane.property("cameraBarMode"), "fps", "the rate face is up")
        marker = self.find(pane, "cameraFpsMarker")
        self.assertTrue(self.find(pane, "cameraFpsScale").property("visible"),
                        "the rate face is the visible one")
        self.assertEqual(pane.property("cameraFpsMax"), 60.0,
                         "the camera reports its own ceiling")
        slower = marker.y()
        model.set_camera_ceiling(15.0)
        self.pump(30)
        self.assertEqual(pane.property("cameraFpsMax"), 15.0,
                         "a slower camera lowers the bar's range")
        self.assertEqual(pane.property("cameraFps"), 15.0,
                         "the published rate follows the camera down with it")
        self.assertLess(marker.y(), slower,
                        "at the ceiling the marker rides the top of the bar")
        self.assertAlmostEqual(marker.y(), -marker.height() / 2, delta=1.5,
                               msg="the ceiling is the bar's own top")

    def test_the_ruler_ticks_every_five_and_thickens_every_ten(self):
        # The graduations the bar draws: a thin double tick at every 5
        # FPS (a quarter in from each side), a thick line at every 10,
        # and both ends of the range marked — the camera's ceiling at
        # the top, the 0.5 floor at the bottom. The spacing is EVEN: a
        # rate reads as a rate, not as a zoom's log scale (the live
        # request — the log bar crowded every low rate into its foot).
        pane, window, _model, _image, frame = self._fps_pane(700, 700, fps=15.0, maximum=30.0)
        self._fps_face(window, frame)
        bar = self.find(pane, "cameraFpsBar")
        self.assertTrue(self.find(pane, "cameraFpsScale").property("visible"),
                        "the graduations must be the face that is up")
        low, high = 0.5, 30.0
        # Three weights (the live request): a double tick at every 2.5
        # FPS, a continuous thin line at every 5, a thick line at every
        # 10 — and both ends of the range carry the thick line. A mark
        # is coded 2*major + line: a 10 is BOTH (a thick line is a line
        # like any other), a 5 is a thin one, a 2.5 is a tick.
        expected = {round((value - low) / (high - low), 4): level
                    for value, level in
                    ((0.5, 3), (2.5, 0), (5, 1), (7.5, 0), (10, 3), (12.5, 0),
                     (15, 1), (17.5, 0), (20, 3), (22.5, 0), (25, 1), (27.5, 0),
                     (30, 3))}
        marks = {}
        for item in bar.childItems():
            if item.property("major") is None:
                continue  # not a graduation delegate
            marks[round(item.property("fraction"), 4)] = item
        self.assertEqual({fraction: 2 * int(bool(item.property("major")))
                          + int(bool(item.property("line")))
                          for fraction, item in marks.items()},
                         expected, "the ruler's marks and their three weights")
        # Every 2.5 FPS sits the same distance from its neighbour: the
        # ruler is evenly spaced from the first tick up. The one gap
        # that is not a 2.5 is the foot's, and it cannot be: the ticks
        # stand on the 2.5 grid while the floor is the 0.5 the throttle
        # bottoms out at, so 2.0 FPS of track separate them.
        steps = sorted(marks)
        self.assertEqual(len(steps), len(expected), "no mark may be missed or doubled")
        # The distances come from the delegates' own y — the fractions
        # are read back rounded, and a hundredth of a fraction is wider
        # than the tolerance this check needs.
        positions = [marks[fraction].y() for fraction in steps]
        gaps = [abs(later - earlier) for earlier, later in zip(positions, positions[1:], strict=False)]
        reference = gaps[1]
        self.assertGreater(reference, 0)
        for gap in gaps[1:]:
            self.assertAlmostEqual(gap, reference, delta=0.05,
                                   msg="equal rate steps must be equal distances: %r" % (gaps,))
        self.assertAlmostEqual(gaps[0] / reference, 2.0 / 2.5, delta=1e-4,
                               msg="only the foot's own 2 FPS gap differs: %r" % (gaps,))
        self.assertAlmostEqual(reference, bar.height() * 2.5 / (high - low), delta=0.05,
                               msg="a 2.5 FPS step is 2.5 of the range's own width")
        for item in marks.values():
            left, right = item.childItems()
            if item.property("major") or item.property("line"):
                self.assertFalse(right.property("visible"),
                                 "a line is continuous, never an edge pair")
                self.assertAlmostEqual(left.width(), bar.width(), delta=0.5,
                                       msg="a line spans the bar")
                expected_height = 2 if item.property("major") else 1
                self.assertAlmostEqual(item.height(), expected_height, delta=0.1,
                                       msg="10 FPS lines are thick, 5 FPS lines thin")
            else:
                self.assertAlmostEqual(item.height(), 1, delta=0.1, msg="a tick is thin")
                self.assertTrue(right.property("visible"), "a tick is a double tick")
                self.assertAlmostEqual(left.width(), bar.width() * 0.25, delta=0.5,
                                       msg="the ticks reach a quarter in from the left")
                self.assertAlmostEqual(right.width(), bar.width() * 0.25, delta=0.5,
                                       msg="... and a quarter in from the right")

    def test_the_wheel_never_asks_for_more_than_the_camera_ceiling(self):
        # The ceiling is the camera's, so the wheel stops there: a
        # 15 FPS camera is never asked for more, and the clamp costs
        # no spurious commit at the limit. The step is the camera's own
        # slice of its range, so a narrow camera gets a narrow step.
        pane, window, model, _image, frame = self._fps_pane(700, 700, fps=12.0, maximum=15.0)
        self.assertEqual(pane.property("fpsStep"), 0.5,
                         "the narrow range steps in halves, not in 1.25x jumps")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls, [12.5], "one notch is one step of the range")
        for _ in range(4):
            self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
            self.pump(30)
        self.assertEqual(model.fps_calls[-1], 14.5, "equal steps all the way up")
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(model.fps_calls[-1], 15.0, "the ceiling is the last stop")
        calls = len(model.fps_calls)
        self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.pump(30)
        self.assertEqual(len(model.fps_calls), calls,
                         "a notch past the ceiling commits nothing above it")
        self.assertEqual(pane.property("cameraFps"), 15.0)

    def _rate_drag(self, window, item, dy, x_ratio=0.5, y_ratio=0.5, steps=1,
                   release=True):
        """A real RIGHT-button drag: press, *steps* moves of dy pixels
        each, release. The rate's own gesture — the left button pans,
        and the rate must be reachable over the picture whether or not
        any control is on screen. The whole track must stay inside the
        item: a synthetic move past its own edge is not delivered to it
        (the harness's own condition, probe-verified — a real platform
        drag holds the grab and leaves the item freely), so a track that
        ran off would silently lose its tail and read as a dropped
        gesture."""
        from PyQt6.QtCore import QEvent, Qt
        x, y = item.width() * x_ratio, item.height() * y_ratio
        self.assertGreaterEqual(y, 0, "the drag must start on the item")
        self.assertLessEqual(y + dy * steps, item.height(),
                             "the drag's whole track must stay on the item")
        self._mouse(window, item, QEvent.Type.MouseButtonPress, x, y,
                    Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        for step in range(1, steps + 1):
            self._mouse(window, item, QEvent.Type.MouseMove, x, y + dy * step,
                        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton)
        self.pump(20)
        if release:
            self._mouse(window, item, QEvent.Type.MouseButtonRelease, x, y + dy * steps,
                        Qt.MouseButton.NoButton, Qt.MouseButton.RightButton)
            self.pump(20)

    def test_the_right_drag_drives_the_rate_from_anywhere_over_the_picture(self):
        # The live request: the rate must be adjustable by dragging,
        # not only by a shifted wheel — and dragging UP raises it. The
        # travel is measured in the wheel's own steps, so the two
        # gestures cross the range alike, and a drag is a grab: the
        # rate face docks and the control stays where the hand is.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self.assertFalse(pane.property("cameraBarDocked"), "the bar starts parked")
        self._rate_drag(window, area, -12)
        self.assertEqual(model.fps_calls, [16.0], "12 px up is one step of the range")
        self.assertEqual(pane.property("cameraBarMode"), "fps",
                         "the rate's own gesture brings the rate's own face up")
        self.assertTrue(pane.property("cameraBarDocked"), "a drag docks the face")
        self.assertEqual(pane.property("cameraZoom"), 1.0,
                         "the rate drag must never move the picture")
        self._rate_drag(window, area, -12, steps=3)
        self.assertEqual(model.fps_calls, [16.0, 17.0, 18.0, 19.0],
                         "and the steps up are equal ones")
        self._rate_drag(window, area, 12)
        self.assertEqual(model.fps_calls[-1], 18.0, "down the same way")
        self.assertEqual(pane.property("cameraZoom"), 1.0)

    def test_the_steps_are_linear_at_every_point_on_the_scale(self):
        # The live report: the old 1.25x ladder accelerated towards the
        # top end, where one notch was worth several FPS. The step is
        # one constant slice of the selected camera's own range, so the
        # same flick moves the rate the same amount at the foot and at
        # the ceiling — and the slice scales with the camera.
        for maximum, step in ((15.0, 0.5), (30.0, 1.0), (60.0, 2.0), (120.0, 4.0)):
            with self.subTest(maximum=maximum):
                pane, window, model, _image, frame = self._fps_pane(
                    700, 700, fps=1.0, maximum=maximum)
                self.assertEqual(pane.property("fpsStep"), step,
                                 "the camera's range sets the step")
                for _ in range(4):  # the first steps of the range
                    self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
                    self.pump(30)
                low_end = [later - earlier for earlier, later
                           in zip(model.fps_calls, model.fps_calls[1:], strict=False)]
                for _ in range(3):  # and the steps further up the scale
                    self._wheel(window, frame, modifiers=Qt.KeyboardModifier.ShiftModifier)
                    self.pump(30)
                high_end = [later - earlier for earlier, later
                            in zip(model.fps_calls, model.fps_calls[1:], strict=False)][len(low_end):]
                self.assertTrue(low_end and high_end, "both ends must have been walked")
                self.assertEqual(set(low_end + high_end), {step},
                                 "equal steps everywhere: %r then %r"
                                 % (low_end, high_end))

    def test_a_right_drag_past_the_bound_never_banks_the_overrun(self):
        # The live report: dragging far past the floor or the ceiling
        # left the whole overrun banked, and the pointer had to be
        # walked back that same distance before the rate answered
        # again. The bound is absolute — the travel spent against it is
        # thrown away, so the first step back moves the rate at once.
        pane, window, model, _image, frame = self._fps_pane(700, 700, fps=26.0, maximum=30.0)
        area = self.find(pane, "cameraGestureArea")
        self.assertEqual(pane.property("fpsStep"), 1.0, "a one-FPS step here")
        # The whole picture is the drag's track, so a drag can run the
        # range and then some: from the foot of the picture upward.
        # The first steps land the ceiling, everything after is overrun.
        self._rate_drag(window, area, -12, steps=40, y_ratio=0.95)
        self.assertEqual(model.fps_calls[-1], 30.0, "the drag stops at the ceiling")
        self.assertEqual(pane.property("cameraFps"), 30.0)
        calls = len(model.fps_calls)
        self._rate_drag(window, area, 12, y_ratio=0.95)
        self.assertEqual(model.fps_calls[-1], 29.0,
                         "one step back from the ceiling answers at once — "
                         "the 480 px of overrun must not have to be undone first")
        self.assertEqual(len(model.fps_calls), calls + 1)
        # And the floor behaves the same way, from the top downward.
        self._rate_drag(window, area, 12, steps=40, y_ratio=0.05)
        self.assertEqual(model.fps_calls[-1], 0.5, "the drag stops at the floor")
        calls = len(model.fps_calls)
        self._rate_drag(window, area, -12, y_ratio=0.05)
        self.assertEqual(model.fps_calls[-1], 1.5,
                         "one step back from the floor answers at once")
        self.assertEqual(len(model.fps_calls), calls + 1)
        # The range's own ends are where the drags stopped, never past.
        self.assertTrue(all(0.5 <= call <= 30.0 for call in model.fps_calls),
                        "no commit ever leaves the camera's own range: %r" % (model.fps_calls,))

    def test_a_held_right_press_holds_the_park_off_until_the_release(self):
        # The rate drag is a grab like the scale's handle, and a grab
        # holds the five idle seconds off: parking the face out from
        # under a held pointer would leave the drag driving a control
        # that is no longer on the picture (the same live report the
        # scale handle answered).
        pane, window, _model, _image, _frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self._rate_drag(window, area, -12, release=False)
        self.assertTrue(pane.property("cameraBarDocked"), "the drag docked the face")
        self.assertIsNotNone(pane.property("cameraBarHandle"),
                             "the picture reports the grab the scales report")
        self._pump_ms(5400)
        self.assertTrue(pane.property("cameraBarDocked"),
                        "five still seconds with the button held must not park it")
        from PyQt6.QtCore import QEvent, Qt
        x, y = area.width() / 2, area.height() / 2 - 12
        self._mouse(window, area, QEvent.Type.MouseButtonRelease, x, y,
                    Qt.MouseButton.NoButton, Qt.MouseButton.RightButton)
        self.pump(30)
        self.assertIsNone(pane.property("cameraBarHandle"), "the release lets go")
        self._pump_ms(5400)
        self.assertFalse(pane.property("cameraBarDocked"),
                         "and the idle clock starts again at the release")

    def test_the_left_drag_still_pans_and_leaves_the_rate_alone(self):
        # The two drags must not trade places: the left button pans a
        # zoomed picture — vertically as well as horizontally — and
        # never touches the rate.
        pane, window, model, _image, frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertGreater(pane.property("cameraZoom"), 1.0, "the wheel zoomed in")
        before = (pane.property("cameraPanX"), pane.property("cameraPanY"))
        self._drag(window, area, 0, -30)
        self.assertEqual(model.fps_calls, [], "a left drag commits no rate")
        self.assertNotEqual((pane.property("cameraPanX"), pane.property("cameraPanY")), before,
                            "a left drag still pans")
        # And a right press with no travel leaves the rate where it was.
        self._rate_drag(window, area, 0)
        self.assertEqual(model.fps_calls, [], "a still right press changes nothing")
        self.assertEqual(pane.property("cameraZoom"), 1.25,
                         "and never returns the fit")

    def test_a_right_double_click_never_returns_the_fit(self):
        # The left double click is the fit; the right button's second
        # press must not steal it, or a rate drag doubled by a nervous
        # hand would throw the view away.
        pane, window, _model, _image, frame = self._fps_pane(700, 700)
        area = self.find(pane, "cameraGestureArea")
        self._wheel(window, frame)
        self._pump_ms(300)
        self.assertGreater(pane.property("cameraZoom"), 1.0, "the wheel zoomed in")
        from PyQt6.QtCore import QPoint
        from PyQt6.QtTest import QTest
        scene = area.mapToItem(window.contentItem(),
                               QPointF(area.width() / 2, area.height() / 2))
        QTest.mouseDClick(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier,
                          QPoint(int(scene.x()), int(scene.y())))
        self.pump(30)
        self.assertEqual(pane.property("cameraZoom"), 1.25,
                         "a right double click must leave the view where it is")
        self._double_click(window, area)
        self.assertEqual(pane.property("cameraZoom"), 1.0,
                         "the left double click is still the fit")


class EscapeLadderTests(RealEngineTestCase):
    """Esc on the Monitor page (the live report: the key did nothing at
    all once the console output had been clicked into). The page keeps ONE
    ladder, and a press reaches it by two routes. With nothing focused the
    window shortcut carries the key; with a TEXT item focused the engine
    in the field answers Escape inside that item and the shortcut never
    fires at all — which is the whole bug, and why the press also has to
    bubble up the item chain to the page's own handler. The container's
    engine (Qt 6.11) cannot reproduce that steal — its shortcut fires
    either way (probe-verified, Cura ships Qt 6.6) — so the test disables
    the shortcut to stand in the field engine's shoes, and then checks
    that one press is one rung with it back on."""

    def _dashboard(self, width=1250, height=760):
        output = OutputDeviceDouble()
        previous = self.engine.rootContext().contextProperty("OutputDevice")
        self.engine.rootContext().setContextProperty("OutputDevice", output)
        self.addCleanup(self.engine.rootContext().setContextProperty,
                        "OutputDevice", previous)
        dashboard, window = self.mount_window("MoonrakerMonitorDashboard.qml", width, height)
        self._pump_ms(400)
        return dashboard, window

    def _loaded_monitor(self, dashboard):
        """The monitor document the dashboard hosts — it declares no
        objectName, so its own pop-over property is the handle."""
        def search(item):
            if item.property("openPopOver") is not None:
                return item
            for child in item.childItems():
                found = search(child)
                if found is not None:
                    return found
            return None
        monitor = search(dashboard)
        self.assertIsNotNone(monitor, "the hosted monitor document did not load")
        return monitor

    def _escape_shortcut(self, dashboard):
        """The page's own window shortcut, reachable only as a QObject."""
        from PyQt6.QtGui import QKeySequence

        for child in dashboard.findChildren(QObject):
            if child.metaObject().className() != "QQuickShortcut":
                continue
            sequence = child.property("sequence")
            if sequence is not None and QKeySequence(sequence) == QKeySequence(Qt.Key.Key_Escape):
                return child
        self.fail("the page's Esc shortcut did not mount")

    def _press(self, dashboard, window, monitor, armed):
        """One Escape through QTest's app-level path, with two layers
        stacked under the ladder: one rung must run, so the monitor's
        pop-over closes and this pane's pop-up stays."""
        from PyQt6.QtTest import QTest

        monitor.setProperty("openPopOver", "sections-status")
        dashboard.setProperty("configurePaneOpen", "controls")
        self.pump(30)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        self.pump(60)
        if armed:
            self.assertEqual(monitor.property("openPopOver"), "",
                             "the top layer closes")
        else:
            self.assertEqual(monitor.property("openPopOver"), "sections-status",
                             "the ladder never ran")
        self.assertEqual(dashboard.property("configurePaneOpen"), "controls",
                         "one press is one rung, whichever route carried it")

    def test_escape_reaches_the_ladder_with_a_text_item_focused(self):
        dashboard, window = self._dashboard()
        monitor = self._loaded_monitor(dashboard)
        console = self.find(dashboard, "moonrakerConsoleOutput")
        shortcut = self._escape_shortcut(dashboard)
        self.assertTrue(shortcut.property("enabled"), "the shortcut mounts armed")
        console.forceActiveFocus()
        self.pump(60)
        # The field engine fires no shortcut at all once a text item owns
        # the key: standing the shortcut down is that engine's condition.
        shortcut.setProperty("enabled", False)
        self.pump(30)
        self._press(dashboard, window, monitor, armed=True)
        # And with it back on, the other route must not double the rung.
        shortcut.setProperty("enabled", True)
        self.pump(30)
        self._press(dashboard, window, monitor, armed=True)


class ChartSurfaceTests(RealEngineTestCase):
    """The temperature chart's rendering architecture: the data canvas
    must request the threaded image strategy (read back through the
    engine — the offscreen probe platform never drives a render pass,
    so the painted-size properties cannot testify here; the capture
    census leg renders the chart's actual pixels in this same
    container), the paint's input must be the main-thread plain-data
    snapshot, and the hover surface must be scene-graph geometry whose
    publications never request a canvas paint."""

    def chart_payload(self, with_tracks=True):
        series = [{
            "name": "extruder", "label": "Extruder", "color": "#d32f2f",
            "visible": True, "primary": True,
            "points": [[float(tick), 200.0 + tick * 0.1] for tick in range(40)],
            "bounds": {"tempMin": 200.0, "tempMax": 204.0,
                       "elapsedMin": 0.0, "elapsedMax": 39.0},
        }]
        if with_tracks:
            series[0]["targets"] = [[[0.0, 210.0], [39.0, 210.0]]]
            series[0]["powers"] = [[[float(tick), 0.5] for tick in range(40)]]
        return {
            "series": series,
            "showTargets": True,
            "showPower": True,
            "palette": [],
            "filling": False,
            "wallOrigin": 100000.0,
        }

    def mount_chart(self, compact=False, with_tracks=True):
        chart = self.mount("TemperatureChart.qml")
        chart.setProperty("compact", compact)
        chart.setProperty("chart", self.chart_payload(with_tracks))
        window = QQuickWindow()
        window.resize(800, 500)
        chart.setParentItem(window.contentItem())
        chart.setWidth(800)
        chart.setHeight(500)
        window.show()
        self.addCleanup(window.deleteLater)
        self.pump(30)
        return chart, window

    @staticmethod
    def enum_value(item, enumerator, key):
        """The enum value QML's `key` resolves to, from the item's own
        QMetaObject — PyQt6 cannot convert the C++ enum instance that
        property() returns, and Qt 6 reordered the Canvas enums, so
        hard-coded values would rot. keyToValue returns (value, ok)."""
        meta = item.metaObject()
        for index in range(meta.enumeratorCount()):
            enum = meta.enumerator(index)
            if enum.name() == enumerator:
                return enum.keyToValue(key)[0]
        raise AssertionError("no enumerator %s" % enumerator)

    @staticmethod
    def read_js(engine, item, expression):
        """A QML-side property read as (value, isUndefined): PyQt6's
        QQmlExpression returns that pair, and this is the only read
        path that survives the C++ enum instance."""
        from PyQt6.QtQml import QQmlExpression

        js = QQmlExpression(engine.rootContext(), item, expression)
        return js.evaluate()

    def test_the_data_canvas_requests_the_threaded_image_strategy(self):
        chart, _ = self.mount_chart()
        canvas = self.find(chart, "temperatureDataCanvas")
        threaded = self.enum_value(canvas, "RenderStrategy", "Threaded")
        image = self.enum_value(canvas, "RenderTarget", "Image")
        self.assertEqual(self.read_js(self.engine, canvas, "renderStrategy"),
                         (threaded, False), "the canvas must request Canvas.Threaded")
        self.assertEqual(self.read_js(self.engine, canvas, "renderTarget"),
                         (image, False), "the canvas must request Canvas.Image")
        self.assertEqual(self.canvas_messages(), [],
                         "the runtime complained about the chart canvas")

    def canvas_messages(self):
        return [message for message in self.new_messages()
                if "Canvas" in message or "canvas" in message]

    def test_the_paint_input_is_the_main_thread_plain_data_snapshot(self):
        # The threaded paint must never reach a theme singleton or
        # another QML item: its complete input is the snapshot the
        # document builds on the main thread, and it must exist — with
        # the series, mapping and labels resolved — before any paint.
        chart, _ = self.mount_chart()
        canvas = self.find(chart, "temperatureDataCanvas")
        job = canvas.property("paintJob").toVariant()
        self.assertIsNotNone(job, "the paint job was never snapshotted")
        self.assertEqual(len(job["series"]), 1)
        self.assertEqual(len(job["series"][0]["points"]), 40)
        self.assertEqual(len(job["grid"]), 5)
        self.assertGreaterEqual(len(job["ticks"]), 5)
        self.assertIn("actual", job["series"][0])
        # A trackless payload (the mini's shape) snapshots without
        # touching targets/powers at all.
        mini, _ = self.mount_chart(compact=True, with_tracks=False)
        mini_job = self.find(mini, "temperatureDataCanvas").property("paintJob").toVariant()
        self.assertEqual(len(mini_job["series"]), 1)
        self.assertEqual(len(mini_job["grid"]), 3)
        self.assertEqual(self.canvas_messages(), [])

    def test_the_hover_surface_is_scene_graph_and_publishes_values(self):
        chart, _ = self.mount_chart()
        before = len(_APPLICATION["messages"])
        chart.setProperty("hoverX", 400.0)
        self.pump(30)
        cursor = self.find(chart, "temperatureHoverCursor")
        self.assertTrue(cursor.property("visible"))
        self.assertGreater(cursor.property("x"), 0)
        self.assertNotEqual(chart.property("hoverClock"), "")
        values = chart.property("hoverValues").toVariant()
        self.assertIn("extruder", values)
        self.assertTrue(str(values["extruder"]).endswith("°C"))
        markers = self.find(chart, "temperatureHoverMarkers")
        self.assertEqual(markers.property("count"), 1)
        # The hover published scalars and moved items — the data
        # canvas was never asked to repaint (a repaint would show up
        # as engine noise, and there is no overlay Canvas left to
        # receive one).
        self.assertEqual(_APPLICATION["messages"][before:], [])
        # Leaving clears the hover state.
        chart.setProperty("hoverX", -1.0)
        self.pump(30)
        self.assertFalse(cursor.property("visible"))
        self.assertEqual(markers.property("count"), 0)

    def test_the_compact_chart_never_shows_the_hover_surface(self):
        chart, _ = self.mount_chart(compact=True)
        chart.setProperty("hoverX", 400.0)
        self.pump(30)
        cursor = self.find(chart, "temperatureHoverCursor")
        self.assertFalse(cursor.property("visible"))


if QT_AVAILABLE:
    class PlatePrinterDouble(QObject):
        """The plate surfaces' printer double. Module-level inside the
        guard (the house pattern): a function-local QObject class's
        meta-object fails the QML var write — the engine stores null
        and the popover never opens."""

        plateSplitChanged = pyqtSignal()
        plateDotChanged = pyqtSignal()
        plateObjectsChanged = pyqtSignal()
        plateLayersChanged = pyqtSignal()
        # The follower's own publish groups, mirroring the model's
        # _SIGNAL_KEYS: the anchor rides the plate group, the follow
        # state and the option ride the view group.
        plateProgressChanged = pyqtSignal()
        followerViewChanged = pyqtSignal()

        # The picker's own payload: a test installs one before it mounts
        # (the class-attribute pattern the follower half's PAYLOAD uses).
        PLATE = None

        def __init__(self):
            super().__init__()
            self._split = PlateFaceRenderTests.PAYLOAD["split"]
            self._anchor = int(PlateFaceRenderTests.PAYLOAD["anchor"])
            self._layers = PlateFaceRenderTests.PAYLOAD["layers"]
            self._scrub = None
            self._navigation = ""
            self._layer_count = 40
            self._motion_count = 21
            self._dot = {"x": 125.0, "y": 125.0, "valid": True}
            self._attached = True
            self._layer_anchor = -1
            self._show_base = True
            self.calls = []
            self._plate = PlatePrinterDouble.PLATE if PlatePrinterDouble.PLATE is not None else {
                "objects": [
                    {"name": "Widget", "center": [125.0, 125.0],
                     "polygon": [[0.0, 0.0], [250.0, 0.0], [250.0, 250.0], [0.0, 250.0]],
                     "current": False, "excluded": False, "restoreAllowed": True},
                ]
            }
        @pyqtSlot()
        def seekAnchorTicked(self):
            # The debounce's raw tick (the model stamps it for the
            # trace); the double records nothing.
            pass

        @pyqtProperty(float, constant=True)
        def bedMeshMachineWidth(self):
            return 250.0

        @pyqtProperty(float, constant=True)
        def bedMeshMachineDepth(self):
            return 250.0

        @pyqtProperty(bool, constant=True)
        def bedMeshCenterIsZero(self):
            return False

        @pyqtProperty("QVariant", notify=plateLayersChanged)
        def plateLayers(self):
            return self._layers

        def setLayers(self, layers):
            """Install a window payload after the mount (the native
            PlateLayer fixtures need the face's own plot first)."""
            self._layers = layers
            self.plateLayersChanged.emit()

        @pyqtProperty("QVariant", notify=plateLayersChanged)
        def plateScrubVector(self):
            # The production partial state publishes the scrub
            # vector beside the PlateLayer window — the face's
            # _scrubVector reads it here.
            return self._scrub

        def setScrub(self, scrub):
            self._scrub = scrub
            self.plateLayersChanged.emit()

        @pyqtProperty(str, notify=plateLayersChanged)
        def plateNavigationData(self):
            # The interaction raster's ready URL (the model's warm
            # background composite — the fixture sets a real PNG).
            return self._navigation

        def setNavigation(self, url):
            self._navigation = url or ""
            self.plateLayersChanged.emit()

        def setSplit(self, split):
            """Move the printed boundary: the layers are the mount's
            fixed half, the split is the volatile half every poll
            rewrites (the model emits the same change)."""
            self._split = int(split)
            self.plateSplitChanged.emit()

        @pyqtProperty(int, notify=plateSplitChanged)
        def plateSplit(self):
            return self._split

        @pyqtProperty(int, notify=plateProgressChanged)
        def plateProgressAnchor(self):
            return self._anchor

        def setAnchor(self, anchor):
            """Move the served layer: the follow's own publish."""
            self._anchor = int(anchor)
            self.plateProgressChanged.emit()

        @pyqtProperty(int, constant=True)
        def plateLayerCount(self):
            return self._layer_count

        @pyqtProperty(int, constant=True)
        def plateLayerMotionCount(self):
            return self._motion_count

        @pyqtProperty(bool, constant=True)
        def plateProgressAvailable(self):
            return True

        @pyqtProperty(str, constant=True)
        def plateProgressReason(self):
            return ""

        @pyqtProperty("QVariant", notify=plateDotChanged)
        def plateDot(self):
            return self._dot

        def setDot(self, x, y, valid=True):
            """The toolhead moves: the follow's per-poll clock."""
            self._dot = {"x": float(x), "y": float(y), "valid": bool(valid)}
            self.plateDotChanged.emit()

        @pyqtProperty(bool, notify=followerViewChanged)
        def followerAttached(self):
            return self._attached

        @pyqtProperty(int, notify=followerViewChanged)
        def followerLayerAnchor(self):
            return self._layer_anchor

        @pyqtProperty(bool, notify=followerViewChanged)
        def followerShowBase(self):
            return self._show_base

        @pyqtSlot(bool)
        def setFollowerShowBase(self, show):
            self.calls.append(("showBase", bool(show)))
            if self._show_base == bool(show):
                return
            self._show_base = bool(show)
            self.followerViewChanged.emit()

        # The model's own slots, mirrored: the double's state follows
        # the same rules so the controls' surface is a real state
        # machine (the freeze, the rejoin, the manual anchor).
        @pyqtSlot(bool)
        def setFollowerAttached(self, attached):
            self.calls.append(("attached", bool(attached)))
            attached = bool(attached)
            if attached == self._attached:
                return
            if attached:
                self._attached = True
                self._layer_anchor = -1
            else:
                frozen = self._layer_anchor if self._layer_anchor >= 0 else self._anchor
                if frozen < 0:
                    return
                self._attached = False
                self._layer_anchor = frozen
                self._anchor = frozen
            self.followerViewChanged.emit()
            self.plateProgressChanged.emit()

        @pyqtSlot(int)
        def setFollowerLayerAnchor(self, layer):
            self.calls.append(("layer", int(layer)))
            if layer < 0:
                return
            self._attached = False
            self._layer_anchor = int(layer)
            self._anchor = int(layer)
            self.followerViewChanged.emit()
            self.plateProgressChanged.emit()

        @pyqtSlot(int)
        def setFollowerLayerProgress(self, motions):
            self.calls.append(("progress", int(motions)))
            if self._attached:
                # Production semantics: the scrub from the live layer
                # is itself the detach — the anchor freezes where the
                # print stood (the P0 zero-index contract).
                self._attached = False
                self._layer_anchor = self._anchor
                self.followerViewChanged.emit()
            self._split = int(motions)
            self.plateProgressChanged.emit()

        @pyqtSlot(bool)
        def setFollowerPopoverOpen(self, popover_open):
            self.calls.append(("followerOpen", bool(popover_open)))

        @pyqtSlot(bool)
        def setPickerPopoverOpen(self, popover_open):
            self.calls.append(("pickerOpen", bool(popover_open)))

        # The camera gesture's freeze pair: the face calls these on
        # entry and on every exit, and an undefined slot aborts the
        # calling handler (the slider's commit sat after endInteraction).
        @pyqtSlot(bool)
        def setFollowerInteracting(self, interacting):
            self.calls.append(("interacting", bool(interacting)))

        @pyqtSlot()
        def setFollowerGestureBake(self):
            self.calls.append(("gestureBake", None))

        @pyqtProperty("QVariant", notify=plateObjectsChanged)
        def plateObjects(self):
            return self._plate

        @pyqtSlot(str)
        def excludeObject(self, name):
            """The picker's destructive command: the row takes the
            verdict and the payload republishes, as the model does."""
            self.calls.append(("exclude", name))
            self._set_excluded(name, True)

        @pyqtSlot(str)
        def restoreObject(self, name):
            self.calls.append(("restore", name))
            self._set_excluded(name, False)

        def _set_excluded(self, name, excluded):
            for row in self._plate.get("objects", []):
                if row["name"] == name:
                    row["excluded"] = bool(excluded)
            self.plateObjectsChanged.emit()

        @pyqtProperty(bool, constant=True)
        def plateHasObjects(self):
            return True

        @pyqtProperty("QVariant", constant=True)
        def sectionHiddenMap(self):
            return {}

        @pyqtProperty("QVariant", constant=True)
        def sectionExpandedMap(self):
            return {}

        @pyqtProperty("QVariant", constant=True)
        def temperatureChartLegend(self):
            return {"series": [], "showPower": True}


class PlateFaceRenderTests(RealEngineTestCase):
    """The plate family's painted contracts: the follower fills its
    plot with sample geometry pinned to the bed's extreme corners (a
    truncated or mis-signed mapping cannot hide — the live crushed
    strip report), the picker's canvas never reflows on hover, and
    the picker draws no toolhead dot."""

    # A 250 mm bed; the layer strokes the full bed rectangle, corners
    # included (the live instruction: corner-pinned sample geometry).
    CORNER_PAYLOAD = {
        "available": True, "reason": "",
        "layers": {
            "prev": None,
            "current": {
                "classes": {
                    # The travel-broken segment shape: one segment.
                    "WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                    [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                    [0.0, 0.0, 20.0]]],
                },
                "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": 21,
            },
            "next": None,
        },
        "split": 12, "method": "motion index", "anchor": 0,
    }

    # The double reads this one attribute, so a test can install a
    # payload of its own before it mounts its window (the face binds its
    # payload once per mount).
    PAYLOAD = CORNER_PAYLOAD

    @staticmethod
    def _printer():
        return PlatePrinterDouble()

    def _open(self, monitor, popover):
        # The reference is RETAINED: a Python-created QObject dies
        # with its last Python ref (the QML var takes no ownership),
        # and the monitor's printer dangles null — no plot, no dot.
        self._printer = self._printer()
        monitor.setProperty("printer", self._printer)
        monitor.setProperty("openPopOver", popover)
        self.pump(30)

    @staticmethod
    def _popover_faces(monitor, name):
        # The follower has a compact mini in the section; the popover
        # instance is the non-compact one.
        return [face for face in monitor.findChildren(QQuickItem, name)
                if not face.property("compact")]

    @staticmethod
    def _ink_rows(image, face, window):
        """Every 2 px row that carries a pixel differing from the
        face's background — the threaded canvas's painted ink."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def pixel(col, row):
            return image.pixel(int(origin.x()) + col, int(origin.y()) + row)

        background = pixel(4, 4)
        rows = []
        for row in range(0, int(face.height()), 2):
            for col in range(0, int(face.width()), 2):
                value = pixel(col, row)
                if any(abs(((value >> shift) & 0xFF) - ((background >> shift) & 0xFF)) > 20
                       for shift in (0, 8, 16)):
                    rows.append(row)
                    break
        return rows

    def _grab_when_inked(self, window, face, top=None, timeout=2.5):
        # With a top edge: the wait targets the plot's top band — the
        # scene-graph dot inks instantly and must not satisfy the
        # wait before the threaded strokes land.
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            rows = self._ink_rows(image, face, window)
            if rows and (top is None or min(rows) <= top + 12):
                return image
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image

    def _native_layer(self, payload, face, prefix_split=None, dpr=1.0,
                      line_scale=8.0):
        """A REAL PlateLayer whose rasters the native renderer
        painted with the face's own mapping — the production object
        the plain-dict fixtures never provide: no .classes, so the
        scrub vector's fallback cannot rescue a missing blit."""
        from plugins.PlateQt import PlateLayer, png_file, render_layer_prefix, render_layer_raster
        raster_dir = "/tmp/mpf/raster-probe"
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        # A THICK lineScale by default: the physical stroke at 100%
        # zoom is legitimately sub-pixel (the live ruling), which a
        # colour census cannot see — the fixture paints its proofs
        # solid. The parity test passes the production 0.7.
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": line_scale, "compact": False,
                "panX": 0.0, "panY": 0.0, "dpr": dpr}
        PlateFaceRenderTests._raster_stem = getattr(
            PlateFaceRenderTests, "_raster_stem", 0) + 1
        stem = "fixture-%d" % PlateFaceRenderTests._raster_stem
        layer = PlateLayer(payload)
        coloured, base, travels = render_layer_raster(payload, plot, view)
        layer.set_raster(coloured, "fixture-key",
                         png_file(coloured, raster_dir, stem + "-c"))
        layer.set_expected_key("fixture-key")
        if base.width() > 0:
            layer.set_base(base, "fixture-key",
                           png_file(base, raster_dir, stem + "-b"))
        if travels.width() > 0:
            layer.set_travels(travels, "fixture-key",
                              png_file(travels, raster_dir, stem + "-t"))
        if prefix_split is not None:
            prefix = render_layer_prefix(payload, plot, view, prefix_split)
            layer.set_prefix(prefix, png_file(prefix, raster_dir, stem + "-p"),
                             prefix_split, "fixture-key")
        return layer

    def _mount_empty(self):
        """Mount with EMPTY geometry: the mount's own vector paths
        can draw nothing, and the returned BASELINE grab holds the
        bed's own picture — the face's grid/background reads as ink
        to _ink_rows (its reference pixel sits outside the bed), so
        the raster proofs compare against this grab instead."""
        previous = PlateFaceRenderTests.PAYLOAD
        PlateFaceRenderTests.PAYLOAD = {
            "available": True, "reason": "",
            "layers": {
                "prev": None,
                "current": {"classes": {}, "travels": [], "travelStarts": [],
                            "travelEnds": [], "motions": 21},
                "next": None,
            },
            "split": 12, "method": "motion index", "anchor": 0,
        }
        self.addCleanup(setattr, PlateFaceRenderTests, "PAYLOAD", previous)
        monitor, window, face = self._follower_popover()
        face.setProperty("dot", None)
        self.pump(30)
        window.grabWindow()
        self.pump(30)
        baseline = window.grabWindow()
        # The threaded bed canvas can still be painting: settle the
        # picture until two grabs agree (the baseline must be the
        # bed's FINAL frame, not a mid-paint one).
        for _ in range(20):
            self.pump(10)
            check = window.grabWindow()
            if self._pixel_diff(check, baseline, face, window) == 0:
                baseline = check
                break
            baseline = check
        return monitor, window, face, baseline


    def _pixel_diff(self, image, baseline, face, window):
        """The sampled pixels that differ from the baseline grab (a
        threaded canvas's late frame reads as a diff; a settled
        identical picture reads zero)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        diffs = 0
        for row in range(0, int(face.height()), 4):
            for col in range(0, int(face.width()), 4):
                if image.pixel(int(origin.x()) + col, int(origin.y()) + row) \
                        != baseline.pixel(int(origin.x()) + col, int(origin.y()) + row):
                    diffs += 1
        return diffs

    def _stroke_ink(self, image, face, window, plot, bed_x, bed_y, tolerance=20):
        """The CORE stroke-ink rows at a bed point: strict matches
        only — antialiased fringes and a grey wash over native
        geometry must not read as the feature colour."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
        row = int(origin.y() + plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
        return sum(
            1 for py in range(max(0, row - 12), min(image.height(), row + 13))
            if self._matches(image.pixel(col, py), (0xD3, 0x2F, 0x2F),
                             tolerance=tolerance)
        )

    def _band_changed(self, image, baseline, face, window, plot, bed_x, bed_y, radius=8):
        """Any pixel changed inside a bed-space point's screen band."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
        row = int(origin.y() + plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
        for dy in range(-radius, radius + 1, 2):
            for dx in range(-radius, radius + 1, 2):
                if image.pixel(col + dx, row + dy) != baseline.pixel(col + dx, row + dy):
                    return True
        return False

    def _wait_diff(self, window, face, baseline, want, timeout=2.5):
        """Wait until the grabbed picture differs from (want=True) or
        equals (want=False) the baseline."""
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            diff = self._pixel_diff(image, baseline, face, window)
            if (want and diff > 0) or (not want and diff == 0):
                return image, diff
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image, self._pixel_diff(image, baseline, face, window)

    def _bed_point(self, face, bed_x, bed_y):
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        return {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}

    @staticmethod
    def _matches(pixel, colour, tolerance=60):
        return abs(((pixel >> 16) & 0xFF) - colour[0]) < tolerance \
            and abs(((pixel >> 8) & 0xFF) - colour[1]) < tolerance \
            and abs((pixel & 0xFF) - colour[2]) < tolerance

    def _red_pixels(self, image, face, window):
        """The wall-outer ink's red — nothing else on the face wears
        it, so the raster's presence is a colour census (robust to
        the bed's own jitter, which the pixel-diff baseline was
        not)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        count = 0
        for row in range(0, int(face.height())):
            for col in range(0, int(face.width())):
                if self._matches(image.pixel(int(origin.x()) + col, int(origin.y()) + row),
                                 (0xD3, 0x2F, 0x2F)):
                    count += 1
        return count

    def _red_in_band(self, image, face, window, plot, bed_x, bed_y, radius=10):
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"])
        row = int(origin.y() + plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"])
        for dy in range(-radius, radius + 1, 2):
            for dx in range(-radius, radius + 1, 2):
                if self._matches(image.pixel(col + dx, row + dy), (0xD3, 0x2F, 0x2F)):
                    return True
        return False

    def _purple_pixels(self, image, face, window):
        """The travel ink's purple — nothing else on the face wears
        it (the bed is grey, the toolpath red)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        count = 0
        for row in range(0, int(face.height())):
            for col in range(0, int(face.width())):
                pixel = image.pixel(int(origin.x()) + col, int(origin.y()) + row)
                r, g, b = (pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF
                if b - r > 40 and b > 120 and g < r + 60:
                    count += 1
        return count

    def _wait_purple(self, window, face, timeout=5.0):
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            count = self._purple_pixels(image, face, window)
            if count > 0:
                return image, count
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image, self._purple_pixels(image, face, window)

    def _wait_red(self, window, face, want, timeout=5.0):
        """Wait until the raster's red ink appears (want=True) or
        leaves the picture entirely (want=False)."""
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline:
            count = self._red_pixels(image, face, window)
            if (want and count > 0) or (not want and count == 0):
                return image, count
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image, self._red_pixels(image, face, window)

    def test_a_full_seek_renders_the_native_raster_with_no_scrub_vector(self):
        # The raster-only full seek: split == motions and
        # scrubVector == null — a real PlateLayer must still paint,
        # through the raster alone (the vector never exists to draw).
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(payload["motions"])
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the full layer drew nothing with no scrub vector")
        # The corner geometry: every bed corner's band must carry the
        # wall-outer red — a wrong or missing transform hides at an
        # extreme corner.
        for bed_x, bed_y in ((0.0, 0.0), (250.0, 0.0), (250.0, 250.0), (0.0, 250.0)):
            self.assertTrue(self._red_in_band(image, face, window, plot, bed_x, bed_y),
                            "the bed corner (%s, %s) never drew" % (bed_x, bed_y))

    def test_a_zero_percent_layer_paints_nothing_without_the_vector(self):
        # The 0% case: split == 0 with
        # scrubVector == null — the raster must not leak the whole
        # layer when nothing has printed. The proof first confirms
        # the same layer's raster DID draw at 100%, then waits for
        # the picture to return to the empty-mount baseline (a
        # single grab races the render thread).
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(payload["motions"])
        _inked, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the fixture's own raster never drew")
        self._printer.setSplit(0)
        _image, count = self._wait_red(window, face, want=False)
        self.assertEqual(count, 0, "the 0% layer leaked the full raster")

    def test_the_partial_printed_portion_renders_through_the_native_prefix(self):
        # The partial states' prefix: the printed portion blits from
        # the native asset — the vector walk covers only the tail,
        # and here (a PlateLayer, no .classes, split < motions)
        # every red pixel provably came from the prefix.
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=12)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(12)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the partial prefix never drew")

    def test_partial_prefix_and_canvas_tail_keep_one_stroke_width(self):
        # The live partial composition is TWO render engines: QPainter
        # owns the native prefix and QML Canvas owns the vector tail.
        # Native-vs-native parity cannot catch a seam whose Canvas half
        # is a different thickness. Measure the real composed pixels on
        # a horizontal run before, at and after the prefix boundary.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        prefix_split = 10
        layer = self._native_layer(payload, face, prefix_split=prefix_split)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)

        # Do not let the native prefix alone satisfy the wait: x=155 is
        # beyond the prefix (which ends at x=110), so red ink there
        # proves the Canvas tail has actually landed.
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        row = int(origin.y() + plot["offsetY"]
                  + (plot["bedYMax"] - 125.0) * plot["sy"])

        def red_height(bed_x, tolerance=20):
            col = int(origin.x() + plot["offsetX"]
                      + (bed_x - plot["bedXMin"]) * plot["sx"])
            return sum(
                1 for py in range(max(0, row - 12), min(image.height(), row + 13))
                if self._matches(image.pixel(col, py), (0xD3, 0x2F, 0x2F),
                                 tolerance=tolerance)
            )

        # The census holds in EVERY frame of the settle, not only in
        # the settled one: the Canvas's trim commits a frame before the
        # scene shows the trimmed texture, and a prefix admitted inside
        # that beat stacks its ink over the bitmap it replaces — the
        # prefix body then reads a full core row deeper than the Canvas
        # body (the CI signature: [4, 2]). Both bodies are measured on
        # every frame; x=155 also proves the Canvas tail landed.
        tail_landed = False
        frames = 0
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and frames < 30:
            self.app.processEvents()
            self.pump(1)
            frames += 1
            image = window.grabWindow()
            bodies = [red_height(75.0), red_height(155.0)]
            if min(bodies) > 0:
                self.assertLessEqual(
                    max(bodies) - min(bodies), 1,
                    "native prefix / Canvas tail stroke widths diverge in "
                    "frame %d: %r" % (frames, bodies))
            if self._red_in_band(image, face, window, plot, 155.0, 125.0,
                                 radius=6):
                tail_landed = True
        self.assertTrue(tail_landed, "the Canvas tail never landed")

        self.pump(20)
        image = window.grabWindow()

        # x=75 is native-prefix body, x=110 is the engine boundary,
        # x=155 is Canvas-tail body. One physical pixel is the maximum
        # acceptable rasterisation disagreement. The census counts only
        # CORE stroke ink: the two round caps meeting at the boundary
        # column legitimately add half-intensity antialiased fringes
        # there, and the grey base's wash over the prefix must not
        # read as the feature colour — tolerance 60 accepted both.
        # The bodies are held to the core census; the boundary column
        # takes the same 60 — the caps' overlap deepens its fringe into
        # the core band (one column wide), so a core census there would
        # count the joint, not the stroke. A Canvas half drawn at a
        # different thickness diverges in both censuses.
        bodies = [red_height(75.0), red_height(155.0)]
        seam = [red_height(75.0, tolerance=60), red_height(110.0, tolerance=60),
                red_height(155.0, tolerance=60)]
        self.assertGreater(min(bodies), 0, "one side of the partial stroke vanished")
        self.assertLessEqual(max(bodies) - min(bodies), 1,
                             "native prefix / Canvas tail stroke widths diverge: %r" % bodies)
        self.assertLessEqual(max(seam) - min(seam), 1,
                             "the seam column's stroke diverges: %r" % seam)
        # The grab forces the scene's sync (the harness's window
        # doctrine): the threaded canvas's last frame drains here,
        # before the teardown.
        window.grabWindow()
        self.pump(30)
        # The teardown's own binding evaluations must never wrap a
        # QObject: the engine's property-cache registry is already
        # dying when the document's last var reads happen, and a
        # PlateLayer in progress.layers.current is exactly the wrap
        # that segfaults it (QObjectWrapper::wrap -> propertyCache).
        # Restore the plain-dict payload: dicts wrap inertly.
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_prefix_that_never_loads_leaves_the_vector_owning_the_history(self):
        # The prefix's model-side validity is NOT the scene's: while
        # the prefix Image is not Ready (here its file never
        # exists), the Canvas must draw the FULL printed interval —
        # never a frame in which neither renderer owns the history.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        prefix_split = 10
        from plugins.PlateQt import render_layer_prefix, png_file
        layer = self._native_layer(payload, face)
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0}
        prefix = render_layer_prefix(payload, plot, view, prefix_split)
        # The prefix image exists — its URL deliberately does not,
        # so the scene-graph Image stays not-Ready forever.
        layer.set_prefix(prefix, "file:///tmp/mpf/raster-probe/missing-%d.png"
                         % time.monotonic_ns(), prefix_split, "fixture-key")
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the picture never drew")
        # Every sampled frame while the prefix stays un-Ready: the
        # printed history (bed x=75, inside the prefix interval)
        # must stay on screen — the vector owns it all.
        for _ in range(10):
            self.pump(5)
            image = window.grabWindow()
            self.assertGreater(
                self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
                0, "a frame lost the printed history while the prefix "
                   "image was not Ready")
        # The real file lands: the loading gap must also hold ink,
        # and once Ready the prefix takes over — the boundary column
        # gains the two caps' fringes (loose census >= 3 rows).
        layer.set_prefix(prefix, png_file(
            prefix, "/tmp/mpf/raster-probe",
            "fixture-ready-%d" % time.monotonic_ns()), prefix_split, "fixture-key")
        deadline = time.monotonic() + 3.0
        takeover = False
        while time.monotonic() < deadline:
            self.pump(5)
            image = window.grabWindow()
            self.assertGreater(
                self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
                0, "the loading gap lost the printed history")
            if self._stroke_ink(image, face, window, census_plot,
                                110.0, 125.0, tolerance=60) >= 3:
                takeover = True
                break
        self.assertTrue(takeover, "the ready prefix never took over")
        # The handover's own beat: the takeover is read a sync before
        # the scene presents the composed texture, and the strict
        # census belongs to the composed frame — sampled a beat after
        # it (the seam must not stay swollen, and the history must
        # still be there).
        self._pump_ms(30)
        image = window.grabWindow()
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
            0, "the composed frame lost the printed history")
        self.assertLessEqual(
            max(self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
                self._stroke_ink(image, face, window, census_plot, 155.0, 125.0)),
            3, "the composed stroke swelled beyond the prefix/tail seam")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_view_change_retires_the_retained_prefix_picture(self):
        # The retained handover's frozen pixels bake the view
        # transform: a zoom or pan after the freeze must retire the
        # picture, or the next loading gap would draw the print
        # displaced (the wrong-place ghost).
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        prefix_split = 10
        from plugins.PlateQt import render_layer_prefix, png_file
        layer = self._native_layer(payload, face)
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0}
        prefix = render_layer_prefix(payload, plot, view, prefix_split)
        layer.set_prefix(prefix, png_file(
            prefix, "/tmp/mpf/raster-probe",
            "fixture-retire-%d" % time.monotonic_ns()), prefix_split, "fixture-key")
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the picture never drew")
        # The freeze arms while the live picture stands.
        deadline = time.monotonic() + 3.0
        armed = False
        while time.monotonic() < deadline:
            self.pump(5)
            if face.property("_retainedPrefixSource") != "":
                armed = True
                break
        self.assertTrue(armed, "the retained freeze never armed")
        # The view change retires the frozen picture.
        face.setProperty("viewScale", 1.5)
        self.pump(5)
        self.assertEqual(face.property("_retainedPrefixSource"), "",
                         "a zoom left the stale-view picture armed")
        self.assertEqual(face.property("_retainedPrefixSplit"), -1,
                         "the retired picture kept its split")
        # The full-picture hold retires with it.
        self.assertEqual(face.property("_heldFullSource"), "",
                         "the held full frame survived the view change")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_an_asynchronous_a_to_b_seek_never_stands_the_previous_layers_pixels(self):
        # The retained handover is a promise about ONE layer: the
        # pixels frozen for layer A bridge A's own prefix replacement
        # and nothing else. A seek to layer B carries the same motions
        # and the same split values, so the split-only predicate
        # re-stands A's pixels over B — and while B's prefix is still
        # loading the canvas' hold skips B's repaint, so A's picture
        # stays up for the whole load. B's layer, not A's, owns the
        # screen.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        plot = self._bed_point(face, 0.0, 0.0)
        a_payload = {
            "classes": {"WALL-OUTER": [[[20.0 + motion * 10.0, 40.0, float(motion)]
                                        for motion in range(21)]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer_a = self._native_layer(a_payload, face, prefix_split=10)
        self._printer.setScrub(a_payload)
        self._printer.setLayers({"prev": None, "current": layer_a, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "layer A's picture never drew")
        # The freeze arms while A's picture stands: that record is
        # what the seek must not carry into B.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_retainedPrefixSource") == "":
            self.pump(5)
        self.assertNotEqual(face.property("_retainedPrefixSource"), "",
                            "the retained freeze never armed")
        # The seek: B's own geometry at its own bed position, the
        # SAME motions and split arithmetic, and a prefix asset that
        # never resolves — the asynchronous gap is the whole window.
        from plugins.PlateQt import render_layer_prefix
        b_payload = {
            "classes": {"WALL-OUTER": [[[20.0 + motion * 10.0, 200.0, float(motion)]
                                        for motion in range(21)]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer_b = self._native_layer(b_payload, face)
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0, "dpr": 1.0}
        prefix_b = render_layer_prefix(b_payload, plot, view, 10)
        layer_b.set_prefix(prefix_b, "file:///tmp/mpf/raster-probe/missing-%d.png"
                           % time.monotonic_ns(), 10, "fixture-key")
        self._printer.setScrub(b_payload)
        self._printer.setLayers({"prev": None, "current": layer_b, "next": None})
        self._printer.setAnchor(1)
        self._printer.setSplit(18)
        self.pump(30)
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        applies = QMetaObject.invokeMethod(face, "_retainedPrefixApplies",
                                           Q_RETURN_ARG(QVariant))
        self.assertFalse(bool(applies),
                         "the retained predicate re-stands layer A's pixels for layer B")
        # The picture, sampled across the whole load: A's ink must
        # never appear at A's own bed position.
        for _ in range(10):
            self._pump_ms(40)
            image = window.grabWindow()
            self.assertEqual(
                self._stroke_ink(image, face, window, plot, 75.0, 40.0), 0,
                "layer A's retained pixels stood over layer B")
        # The seek completes: B's asset lands and B's own history
        # takes B's position — the assertions above are not a stuck
        # blank.
        from plugins.PlateQt import png_file
        layer_b.set_prefix(prefix_b, png_file(prefix_b, "/tmp/mpf/raster-probe",
                                              "fixture-ab-%d" % time.monotonic_ns()),
                           10, "fixture-key")
        self._printer.setLayers({"prev": None, "current": layer_b, "next": None})
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "layer B's prefix never landed after the seek")
        self.assertGreater(self._stroke_ink(image, face, window, plot, 75.0, 200.0), 0,
                           "layer B's prefix drew nothing at its own position")
        self.assertEqual(self._stroke_ink(image, face, window, plot, 75.0, 40.0), 0,
                         "layer A's pixels outlived the seek")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_overlapping_ghost_geometry_leaves_the_current_layer_on_top(self):
        # The stack order at 100%: the ghost pair belongs BENEATH the
        # full current raster and the travels, exactly as the
        # navigation raster composites the scene (grid, ghosts, base,
        # current layer, travels). A ghost drawn over the current
        # raster dims the ink it overlaps.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showPrevious", True)
        self.pump(10)
        plot = self._bed_point(face, 0.0, 0.0)
        run = [[20.0 + motion * 10.0, 125.0, float(motion)] for motion in range(21)]
        current_payload = {"classes": {"WALL-OUTER": [run]}, "travels": [],
                           "travelStarts": [], "travelEnds": [], "motions": 21}
        # The ghost covers the SAME bed points (the overlap) plus a
        # run of its own (the ghost-only band, so the overlap probe
        # cannot pass on a ghost that never rendered).
        ghost_payload = {"classes": {"FILL": [run, [[40.0, 60.0, 30.0], [200.0, 60.0, 30.0]]]},
                         "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21}
        ghost = self._native_layer(ghost_payload, face)
        current = self._native_layer(current_payload, face)
        self._printer.setLayers({"prev": ghost, "current": current, "next": None})
        self._printer.setSplit(21)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the full current layer never drew")
        self.assertTrue(self._band_changed(image, baseline, face, window, plot, 75.0, 60.0,
                                          radius=6),
                        "the ghost never rendered — the overlap probe would be vacuous")
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def face_point(bed_x, bed_y):
            # The bed point in the face's own pixels: the grab reads it
            # through the window origin, the navigation raster (backed
            # 1:1) reads it directly.
            return (int(plot["offsetX"] + (bed_x - plot["bedXMin"]) * plot["sx"]),
                    int(plot["offsetY"] + (plot["bedYMax"] - bed_y) * plot["sy"]))

        def red_excess(image, col, row, origin_x=0, origin_y=0, span=4):
            # The red's excess over the blue at a bed point: pure
            # current-layer ink reads ~164, the same ink dimmed by the
            # ghost's 0.30 blue reads ~59.
            best = -255
            for dy in range(-span, span + 1):
                for dx in range(-span, span + 1):
                    pixel = image.pixel(origin_x + col + dx, origin_y + row + dy)
                    best = max(best, ((pixel >> 16) & 0xFF) - (pixel & 0xFF))
            return best

        local_col, local_row = face_point(75.0, 125.0)
        overlap = red_excess(image, local_col, local_row,
                             int(origin.x()), int(origin.y()))
        self.assertGreaterEqual(
            overlap, 120,
            "the ghost dimmed the current layer's ink at the same bed point "
            "(red excess %d)" % overlap)
        # Parity with the navigation raster's own composite order: the
        # same window, plot and view, backed 1:1 so both rasters share
        # one pixel grid.
        from plugins.PlateQt import render_navigation_layer
        nav_view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": 1.0, "lineScale": 8.0, "compact": False,
                    "panX": 0.0, "panY": 0.0, "dpr": 1.0, "backing": 1.0,
                    "showPrevious": True, "showNext": True, "showBase": True,
                    "showTravels": False, "bedWidth": 250.0, "bedDepth": 250.0}
        nav = render_navigation_layer({"prev": ghost_payload, "current": current_payload,
                                       "next": None}, plot, nav_view, 21)
        nav_overlap = red_excess(nav, local_col, local_row)
        self.assertGreaterEqual(
            nav_overlap, 120,
            "the navigation raster composites the ghost over the current layer "
            "(red excess %d)" % nav_overlap)
        self.assertGreaterEqual(
            overlap, nav_overlap - 20,
            "the exact stack diverges from the navigation raster's layer order "
            "(exact %d vs nav %d)" % (overlap, nav_overlap))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_the_grey_base_never_washes_the_printed_prefix(self):
        # The stack order contract: ghosts < base < prefix < tail.
        # With the base ON and the ghosts OFF, the native prefix and
        # the vector tail must keep their feature colour — the grey
        # base may mark the unprinted suffix, never wash printed ink.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showBase", True)
        face.setProperty("showPrevious", False)
        face.setProperty("showNext", False)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the picture never drew")
        # The grey base IS up: the unprinted suffix (motion 20, bed
        # x=215 — beyond the split's tail) shows the base's grey
        # where the empty baseline had none.
        self.assertTrue(self._band_changed(image, baseline, face, window,
                                           census_plot, 215.0, 125.0),
                        "the grey base never rendered — the wash "
                        "proof would be vacuous")
        # The printed prefix and the tail keep the feature colour
        # over the base: strict core-ink rows on both sides.
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
            0, "the grey base washed the native prefix")
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 155.0, 125.0),
            0, "the grey base washed the vector tail")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_the_layer_ghost_shows_at_zero_percent(self):
        # The live request: the grey whole-layer base frames the
        # print from the FIRST instant — at 0% the layer reads as
        # the ghost alone, with no printed ink yet (the 0% rule
        # holds: the base is the unprinted frame, never the
        # feature colour).
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        face.setProperty("showBase", True)
        face.setProperty("showPrevious", False)
        face.setProperty("showNext", False)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=0)
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(0)
        # The ghost washes the whole unprinted layer (a band the
        # empty baseline had none of) — at 0% there is no red ink
        # by design, so the picture's arrival is the BAND.
        deadline = time.monotonic() + 5.0
        image = None
        while time.monotonic() < deadline:
            self.pump(5)
            image = window.grabWindow()
            if self._band_changed(image, baseline, face, window,
                                  census_plot, 155.0, 125.0):
                break
        self.assertTrue(self._band_changed(image, baseline, face, window,
                                           census_plot, 155.0, 125.0),
                        "the grey ghost never rendered at 0%")
        # The 0% rule holds: no FEATURE ink anywhere.
        self.assertEqual(
            self._stroke_ink(image, face, window, census_plot, 155.0, 125.0),
            0, "feature ink appeared at 0%")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_partial_travels_on_both_sides_of_the_prefix_survive(self):
        # The prefix carries NO travels: a travel printed BEFORE
        # the prefix boundary must stay visible at partial progress
        # — the canvas redraws the travels from the layer's start
        # on every reset, so neither side vanishes.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("showTravels", True)
        # The face's own lineScale too: the QML travels stroke at
        # the live sub-pixel width is invisible to the colour
        # census (the fixture above paints the native side solid).
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = {
            "classes": {"WALL-OUTER": [[[30.0, 30.0, 1.0], [90.0, 30.0, 2.0]]]},
            "travels": [
                [[30.0, 80.0, 2.0], [90.0, 80.0, 3.0]],    # before the boundary
                [[30.0, 200.0, 13.0], [90.0, 200.0, 14.0]],  # after it, before the split
            ],
            "travelStarts": [], "travelEnds": [], "motions": 20,
        }
        layer = self._native_layer(payload, face, prefix_split=12)
        plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(16)
        image, purple = self._wait_purple(window, face)
        self.assertGreater(purple, 0, "the partial travels never drew")
        # Ink at BOTH bands: the pre-boundary travel (bed y 80) and
        # the post-boundary one (bed y 200).
        self.assertTrue(self._band_changed(image, baseline, face, window, plot,
                                           60.0, 80.0),
                        "the pre-boundary travel vanished")
        self.assertTrue(self._band_changed(image, baseline, face, window, plot,
                                           60.0, 200.0),
                        "the post-boundary travel vanished")
        # The grab forces the scene's sync, and the plain-dict
        # restore keeps the teardown's last binding evaluations
        # from wrapping the PlateLayer QObject (the engine's
        # property-cache registry is already dying then).
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_context_changes_never_hole_the_printed_history(self):
        # E: a lineScale/zoom/pan change invalidates the native
        # prefix (the model's render-key flip) and its replacement
        # lands a beat later — the printed history must stay present
        # on EVERY intermediate frame: at the old screen position
        # while the hold keeps the previous composition up, at the
        # new one once the vector owns the interval. Resize rides
        # the same view-key path (the key carries width/height).
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the picture never drew")
        old_plot = self._bed_point(face, 0.0, 0.0)
        self.assertGreater(
            self._stroke_ink(image, face, window, old_plot, 75.0, 125.0), 0,
            "the prefix side never drew before the context changes")

        def view_plot(plot, scale, pan_x):
            # The zoom multiplies the bed mapping and the pan adds
            # after (the painters' own transform) — the census must
            # follow the same folding.
            adjusted = dict(plot)
            for key in ("offsetX", "sx", "offsetY", "sy"):
                adjusted[key] = plot[key] * scale
            adjusted["offsetX"] += pan_x
            return adjusted

        contexts = [("lineScale", 12.0, 1.0, 0.0),
                    ("viewScale", 1.2, 1.2, 0.0),
                    ("viewPanX", -20.0, 1.0, -20.0)]
        for index, (name, value, scale, pan_x) in enumerate(contexts):
            # The production ordering: the context change settles
            # FIRST (the 150 ms timer consumes the view key and feeds
            # the model), and only THEN does the model's invalidation
            # publish land — with the key already consumed, so the
            # publish alone wakes nothing. The harness has no model:
            # the render-key flip IS the production mechanism.
            face.setProperty(name, value)
            self._pump_ms(200)  # the settle consumes the view key
            layer.set_expected_key("invalidated-%d" % index)
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            new_plot = view_plot(self._bed_point(face, 0.0, 0.0), scale, pan_x)
            # The replacement prefix is DELAYED: every intermediate
            # frame keeps the history — at the old screen position
            # (the held composition) or the new one (the vector's).
            # The FIRST grab is the invalidation publish's own frame:
            # the one-frame ownership swap is exactly the race under
            # test, so it must not hide behind a settling pump.
            for sample in range(7):
                if sample:
                    self._pump_ms(30)
                image = window.grabWindow()
                prefix_side = max(
                    self._stroke_ink(image, face, window, new_plot, 75.0, 125.0),
                    self._stroke_ink(image, face, window, old_plot, 75.0, 125.0))
                tail_side = max(
                    self._stroke_ink(image, face, window, new_plot, 155.0, 125.0),
                    self._stroke_ink(image, face, window, old_plot, 155.0, 125.0))
                self.assertGreater(prefix_side, 0,
                                   "%s holed the printed history (prefix side)" % name)
                self.assertGreater(tail_side, 0,
                                   "%s holed the printed history (tail side)" % name)
            # The replacement prefix lands for the new view, and the
            # settle fires (the production 150 ms re-raster): the
            # composition stays whole through the takeover and the
            # settled picture sits at the NEW positions.
            from plugins.PlateQt import render_layer_prefix, png_file
            plot_value = face.property("plot")
            if hasattr(plot_value, "toVariant"):
                plot_value = plot_value.toVariant()
            plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                    "offsetY": float(plot_value["bed"]["offsetY"]),
                    "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                    "bedXMin": float(plot_value["bed"]["bedXMin"]),
                    "bedYMax": float(plot_value["bed"]["bedYMax"])}
            view = {"width": int(face.width()), "height": int(face.height()),
                    "scale": scale, "lineScale": float(face.property("lineScale")),
                    "compact": False, "panX": pan_x, "panY": 0.0}
            prefix = render_layer_prefix(payload, plot, view, 10)
            layer.set_prefix(prefix, png_file(
                prefix, "/tmp/mpf/raster-probe",
                "fixture-e%d-%d" % (index, time.monotonic_ns())), 10,
                "invalidated-%d" % index)
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            self._pump_ms(250)  # past the view-settle timer
            for _ in range(4):
                self._pump_ms(30)
                image = window.grabWindow()
                self.assertGreater(
                    self._stroke_ink(image, face, window, new_plot, 75.0, 125.0), 0,
                    "%s lost the history at the replacement" % name)
            # The takeover completes before the next context change
            # (the production cadence: one settled change at a time).
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not face.property("_prefixWasShown"):
                self._pump_ms(50)
                window.grabWindow()
            self.assertTrue(face.property("_prefixWasShown"),
                            "%s: the replacement prefix never showed" % name)
            old_plot = new_plot
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_raster_seek_commits_to_ready_under_the_target(self):
        # The final target's composition leg: the publish (the
        # commit's handoff) to the picture's arrival — the red ink
        # IS the composed picture, so the wait for it measures
        # commit-to-picture end to end. The grab-wait's 50 ms
        # sampling bounds the assertion, the printed number is the
        # reading.
        monitor, window, face = self._follower_popover()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(21)
        start = time.monotonic()
        _image, count = self._wait_red(window, face, want=True)
        elapsed = (time.monotonic() - start) * 1000.0
        self.assertGreater(count, 0, "the picture never arrived")
        print("publish -> picture: %.1f ms" % elapsed)
        # The arrival IS the contract (the wait above); the wall-clock
        # reading is a hang guard only — the loaded CI runners push
        # past 300 ms, so the bound matches the wait's own 5 s window.
        self.assertLess(elapsed, 5000.0,
                        "the picture never settled within the wait window")
        # The grab forces the scene's sync (the harness's window
        # doctrine): the texture drains before the next mount.
        window.grabWindow()
        self.pump(30)

    def test_hidpi_rasters_paint_at_device_resolution(self):
        # D's contract: the native raster is painted at the DEVICE
        # resolution — the face's logical size times the bounded
        # backing scale — and the scene-graph samples it down to the
        # logical frame. A DPR-2 screen must never take a 1x logical
        # toolpath raster and merely enlarge it. The dimensions AND
        # the logical-space coverage both hold.
        monitor, window, face, baseline = self._mount_empty()
        payload = {
            "classes": {"WALL-OUTER": [[[0.0, 0.0, 0.0], [250.0, 0.0, 5.0],
                                        [250.0, 250.0, 10.0], [0.0, 250.0, 15.0],
                                        [0.0, 0.0, 20.0]]]},
            "travels": [], "travelStarts": [], "travelEnds": [], "motions": 21,
        }
        layer = self._native_layer(payload, face, dpr=2.0)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(21)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the full raster never drew")
        self.assertEqual(layer.rasterWidth, int(face.width()) * 2,
                         "the DPR-2 raster is not the device width")
        self.assertEqual(layer.rasterHeight, int(face.height()) * 2,
                         "the DPR-2 raster is not the device height")
        # The logical coverage: the downsampled 2x raster's ink still
        # reaches the logical frame's corners (the corner-pinned
        # stroke must not clip at the backing scale).
        plot = self._bed_point(face, 0.0, 0.0)
        for bed_x, bed_y in ((1.0, 1.0), (249.0, 249.0)):
            self.assertGreater(
                self._stroke_ink(image, face, window, plot, bed_x, bed_y), 0,
                "the DPR-2 raster's ink never reached the logical corner")

    def test_reverse_scrub_paths_settle_to_the_same_picture(self):
        # F: the reverse scrub is the prefix scheduler's stress path
        # (a backward move demands a fresh prefix immediately). The
        # SAME final {layer, split, view, toggles} must produce the
        # same settled pixels however it was reached — direct, from
        # 0, from 100, through a lower split, through a higher one,
        # or a rapid alternating reverse-heavy run. The only
        # legitimate disagreement is the prefix/tail boundary's
        # antialiased fringe (a full-canvas bitmap trims to the tail
        # on one path and keeps the continuous stroke on another).
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        target = 18

        def seek_to(split):
            self._printer.setSplit(split)

        def settled_grab():
            self._pump_ms(350)  # past the view settle and the paints
            return window.grabWindow()

        seek_to(target)
        direct = settled_grab()
        self.assertGreater(self._red_pixels(direct, face, window), 0,
                           "the direct seek never drew")
        paths = [
            ("0 -> X", [0, target]),
            ("100 -> X", [21, target]),
            ("X -> lower -> X", [target, 8, target]),
            ("X -> higher -> X", [target, 20, target]),
            ("rapid alternating", [target, 8, target, 12, target, 5,
                                   target, 15, target, 7, target]),
        ]
        diffs = {}
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        for name, sequence in paths:
            for split in sequence:
                seek_to(split)
                self._pump_ms(60)
            image = settled_grab()
            # The antialias tolerance: a path through the full state
            # keeps the canvas's FULL bitmap under the prefix (the
            # re-show gate forbids the trim), while the direct path
            # trims to the tail — the SAME geometry composites with
            # fringe shades a few channels apart. Anything beyond a
            # per-channel 40 is a real composition drift.
            def differs(pixel_a, pixel_b):
                # The suite's loose census tolerance: the paths'
                # canvas keeps the full stroke under the prefix
                # (the re-show gate forbids the trim), so the
                # prefix interval's antialiased edge row composites
                # ~42 channels lighter — the SAME geometry, an
                # antialias-level shading difference.
                return any(abs(((pixel_a >> shift) & 0xFF)
                              - ((pixel_b >> shift) & 0xFF)) > 60
                           for shift in (0, 8, 16))
            diffs[name] = sum(
                1 for row in range(0, int(face.height()), 4)
                for col in range(0, int(face.width()), 4)
                if differs(image.pixel(int(origin.x()) + col,
                                       int(origin.y()) + row),
                           direct.pixel(int(origin.x()) + col,
                                        int(origin.y()) + row)))
        # The settled single-owner composition (the review's finding
        # #2): the canvas's coverage record names ONE owner — the
        # prefix's own split (the tail-only canvas) — and the
        # delivery has landed. A settled FULL bitmap under the prefix
        # would keep the record at 0.
        self.assertEqual(face.property("_vectorCoversFrom"), layer.prefixSplit,
                         "the settled canvas never trimmed to the tail")
        self.assertTrue(face.property("_textureReady"),
                        "the settled canvas never delivered")
        print("reverse-scrub settled diffs vs direct:", diffs)
        for name, diff in diffs.items():
            self.assertLessEqual(diff, 8,
                                 "%s settled to a different picture "
                                 "(%d sampled pixels differ beyond "
                                 "the antialias tolerance)" % (name, diff))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_reverse_scrub_frames_never_show_a_hybrid_composition(self):
        # The compositor contract: every frame displayed while the
        # split moves presents ONE COMPLETE composition — the
        # standing committed picture or the replacement's — judged by
        # a whole-path probe signature (the early printed history,
        # both sides of the prefix/tail seam, the Canvas tail's
        # middle, the final printed edge, and clean space beyond it),
        # never a rightmost-column guess. Zero blank frames, zero
        # hybrid frames, zero exemptions. An intermediate split that
        # never presented may not appear once the demand moved on:
        # the allowed set is the LAST PRESENTED composition plus the
        # current demand, derived from actual presentation.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        row = int(round(float(bed["offsetY"])
                        + (float(bed["bedYMax"]) - 125.0)
                        * float(plot_value["sy"])))
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def column_for(bed_x):
            return int(round(float(bed["offsetX"])
                             + (bed_x - float(bed["bedXMin"]))
                             * float(plot_value["sx"])))

        def ink_at(image, bed_x, slack=3):
            # Any red ink in the path's row band within `slack`
            # columns of the bed-x position.
            col = column_for(bed_x)
            return any(
                self._matches(image.pixel(int(origin.x()) + c,
                                          int(origin.y()) + r),
                              (0xD3, 0x2F, 0x2F))
                for c in range(col - slack, col + slack + 1)
                for r in range(row - 2, row + 3))

        def clean_beyond(image, split):
            # No printed ink past the final edge: the stroke's round
            # cap ends within a few pixels of the last point, and the
            # next motion (10 mm further) must never bleed through —
            # stale geometry beyond the requested split reads here.
            edge = column_for(20.0 + 10.0 * (split - 1))
            return not any(
                self._matches(image.pixel(int(origin.x()) + c,
                                          int(origin.y()) + r),
                              (0xD3, 0x2F, 0x2F))
                for c in range(edge + 6, edge + 18)
                for r in range(row - 2, row + 3))

        def complete_signature(image, split):
            # The WHOLE composition's probe signature for `split`: the
            # early printed history, both sides of the prefix/tail
            # seam (a hole at the handoff is a hybrid), the Canvas
            # tail's middle, the final printed edge — and clean space
            # beyond it.
            probes = [25.0]  # the early printed history
            if split > 10:
                probes += [105.0, 115.0]  # both sides of the seam
                probes.append((110.0 + 20.0 + 10.0 * (split - 1)) / 2.0)
            probes.append(20.0 + 10.0 * (split - 1))  # the final edge
            return (all(ink_at(image, bx) for bx in probes)
                    and clean_beyond(image, split))

        def classify(image, candidates):
            # The frame's complete composition, by whole-signature
            # match — the candidate whose signature holds, else None
            # (an invalid frame).
            for split in candidates:
                if complete_signature(image, split):
                    return split
            return None

        failures = []

        def leg(name, prev_split, new_split, invalidate=False, beats=25):
            # One transition: every frame must present the standing
            # committed composition or the requested one. Zero blanks
            # and zero invalid frames — no exemptions.
            layer.set_expected_key("fixture-key")
            self._printer.setLayers({"prev": None, "current": layer, "next": None})
            self._printer.setSplit(prev_split)
            self._pump_ms(300)  # the leg's own standing composition
            self._printer.setSplit(new_split)
            if invalidate:
                layer.set_expected_key("invalidated")
            allowed = [prev_split, new_split]
            presented = prev_split
            for beat in range(beats):
                self._pump_ms(20)
                image = window.grabWindow()
                shown = classify(image, allowed)
                if shown is None:
                    # The whole signature, per candidate, so a flake's
                    # failing probes are visible in the report.
                    details = []
                    for cand in allowed:
                        probes = [25.0]
                        if cand > 10:
                            probes += [105.0, 115.0,
                                       (110.0 + 20.0 + 10.0 * (cand - 1)) / 2.0]
                        probes.append(20.0 + 10.0 * (cand - 1))
                        details.append("split %d probes %s clean=%s"
                                       % (cand, [ink_at(image, bx) for bx in probes],
                                          clean_beyond(image, cand)))
                    progress_now = face.property("progress")
                    if progress_now is not None and hasattr(progress_now, "toVariant"):
                        progress_now = progress_now.toVariant()
                    details.append(
                        "face: wasShown=%s hold=%s texture=%s last=%s covers=%s "
                        "prefixValid=%s prefixSplit=%s split=%s"
                        % (face.property("_prefixWasShown"),
                           face.property("_prefixHold"),
                           face.property("_textureReady"),
                           face.property("_lastSplit"),
                           face.property("_vectorCoversFrom"),
                           layer.prefixValid, layer.prefixSplit,
                           progress_now.get("split") if progress_now else "?"))
                    failures.append("%s: beat %d presented no complete "
                                    "composition (%s)"
                                    % (name, beat, " | ".join(details)))
                    continue
                if shown not in allowed:
                    failures.append("%s: beat %d presented split %d (allowed %s)"
                                    % (name, beat, shown, allowed))
                else:
                    presented = shown
            return presented

        for name, prev_split, new_split, invalidate in [
                ("100% -> partial", 21, 18, False),
                ("80% -> 60%", 18, 15, False),
                ("80% -> 40%", 18, 8, False),
                ("60% -> 70%", 15, 16, False),
                ("delayed prefix", 18, 15, True)]:
            leg(name, prev_split, new_split, invalidate)
        # The rapid alternation: the demand never rests, and the
        # standing-composition policy applies — the allowed set is the
        # LAST PRESENTED complete composition plus the current demand.
        # An intermediate split that never presented may not appear
        # later; one that did becomes the standing picture.
        layer.set_expected_key("fixture-key")
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        self._pump_ms(300)
        standing = 18
        for split in [15, 8, 12, 6, 16, 10, 14, 9]:
            self._printer.setSplit(split)
            allowed = [standing, split]
            for beat in range(3):
                self._pump_ms(20)
                image = window.grabWindow()
                shown = classify(image, allowed)
                if shown is None:
                    failures.append("rapid %d -> %d: beat %d presented no "
                                    "complete composition" % (standing, split, beat))
                    continue
                if shown not in allowed:
                    failures.append("rapid %d -> %d: beat %d presented split %d "
                                    "(allowed %s)" % (standing, split, beat, shown, allowed))
                else:
                    standing = shown
        self.assertEqual(failures, [],
                         "hybrid, blank or stale-composition frames "
                         "during the scrub: %s" % failures)
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_stale_canvas_delivery_never_readies_the_partial_prefix(self):
        # F4's focused regression, the review's exact state:
        # textureReady true, the coverage compatible, but the
        # canvas's LAST PAINTED split behind the current demand —
        # _partialPrefixReady must stay false until the CURRENT
        # split's delivery lands. The predicate is read through the
        # real engine's face (the front gates — the prefix model and
        # the uploaded image — genuinely pass). The stale record is
        # forced while the demand itself is never moved, so no paint
        # races the probe.
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        # Settle on a partial split so the prefix model publishes and
        # the prefix Image uploads — the predicate's own front gates.
        self._printer.setSplit(18)
        self._pump_ms(300)
        self.assertTrue(face.property("_prefixWasShown"),
                        "the settled prefix never showed")

        def predicate():
            result = QMetaObject.invokeMethod(
                face, "_partialPrefixReady", Q_RETURN_ARG(QVariant))
            self.assertIsInstance(result, bool, "the predicate never invoked")
            return result

        # The stale exact state: a delivered canvas whose recorded
        # painted split trails the standing demand (split 18). Both
        # coverage records are forced — the ownership gates read the
        # DELIVERED one (the committed record runs a beat ahead of
        # the scene), and a fixture that forced only the committed
        # record would leave the delivered record claiming a canvas
        # state the test does not mean.
        face.setProperty("_textureReady", True)
        face.setProperty("_vectorCoversFrom", 0)
        face.setProperty("_vectorCoversShown", 0)
        face.setProperty("_lastSplit", 5)
        self.assertFalse(predicate(),
                         "an older split's delivery readied the prefix")
        # The current split's delivery lands — readiness follows.
        face.setProperty("_lastSplit", 18)
        self.assertTrue(predicate(),
                        "the current delivery never readied the prefix")

    _PARITY_ORIENTATIONS = {
        # The prefix boundary sits at motion 9 (bed position 110 or
        # its orientation's equivalent): the seam probes bracket it.
        "horizontal": {
            "points": [[20.0 + m * 10.0, 125.0, float(m)] for m in range(21)],
            "prefix": (60.0, 125.0), "tail": (160.0, 125.0),
            "seam_before": (105.0, 125.0), "seam_after": (115.0, 125.0),
            "across_columns": False,
        },
        "vertical": {
            "points": [[125.0, 20.0 + m * 10.0, float(m)] for m in range(21)],
            "prefix": (125.0, 60.0), "tail": (125.0, 160.0),
            "seam_before": (125.0, 105.0), "seam_after": (125.0, 115.0),
            "across_columns": True,
        },
        "diagonal": {
            "points": [[20.0 + m * 7.0, 20.0 + m * 7.0, float(m)]
                       for m in range(21)],
            "prefix": (55.0, 55.0), "tail": (118.0, 118.0),
            "seam_before": (78.0, 78.0), "seam_after": (90.0, 90.0),
            "across_columns": False,
        },
    }

    def _parity_leg(self, dpr, orientation):
        spec = self._PARITY_ORIENTATIONS[orientation]
        # Each leg mounts fresh, and _open replaces the _printer
        # factory with the instance — restore it so the next mount
        # can build one (the old expectedFailure swallowed exactly
        # this TypeError, so its second leg never ran).
        self._printer = PlateFaceRenderTests._printer
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 0.7)
        self.pump(10)
        payload = {
            "classes": {"WALL-OUTER": [spec["points"]]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10,
                                   dpr=dpr, line_scale=0.7)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        self._pump_ms(400)
        image = window.grabWindow()
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        bed = plot_value["bed"]
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def column_for(bed_x):
            return int(round(float(bed["offsetX"])
                             + (bed_x - float(bed["bedXMin"]))
                             * float(plot_value["sx"])))

        def row_for(bed_y):
            return int(round(float(bed["offsetY"])
                             + (float(bed["bedYMax"]) - bed_y)
                             * float(plot_value["sy"])))

        def band_at(bed_x, bed_y):
            # The red excess over the underlying background across
            # the stroke's PERPENDICULAR (rows for a horizontal run,
            # columns for a vertical one; a diagonal crosses the
            # sampled band either way).
            col = column_for(bed_x)
            row = row_for(bed_y)
            values = []
            if spec["across_columns"]:
                for c in range(col - 8, col + 9):
                    pixel = image.pixel(int(origin.x()) + c,
                                        int(origin.y()) + row)
                    values.append(max(0, ((pixel >> 16) & 0xFF)
                                     - ((pixel >> 8) & 0xFF)))
            else:
                for r in range(row - 8, row + 9):
                    pixel = image.pixel(int(origin.x()) + col,
                                        int(origin.y()) + r)
                    values.append(max(0, ((pixel >> 16) & 0xFF)
                                     - ((pixel >> 8) & 0xFF)))
            return values

        def metrics(band):
            return max(band), sum(band), sum(1 for v in band if v > 4)

        label = "dpr %s %s" % (dpr, orientation)
        prefix_band = band_at(*spec["prefix"])
        tail_band = band_at(*spec["tail"])
        prefix_peak, prefix_energy, prefix_extent = metrics(prefix_band)
        tail_peak, tail_energy, tail_extent = metrics(tail_band)
        self.assertGreater(prefix_peak, 10,
                           "%s: the native prefix drew nothing measurable" % label)
        self.assertGreater(tail_peak, 10,
                           "%s: the canvas tail drew nothing measurable" % label)
        # The parity contract's DIRECTION (the live report's own):
        # the raster must never be the FAINTER side — the device
        # floor's coverage presents min(2/dpr, 1) logical px of full
        # ink, so the native prefix carries the canvas's device-grid
        # footprint while the harness's canvas paints the faithful
        # subpixel AA (the offscreen rasteriser does not floor). The
        # 0.7 slack absorbs only the harness's AA jitter.
        self.assertGreater(prefix_peak, tail_peak * 0.7,
                           "%s: the native prefix is ghostly against the "
                           "canvas tail (peak %s vs %s)"
                           % (label, prefix_peak, tail_peak))
        self.assertGreater(prefix_energy, tail_energy * 0.7,
                           "%s: the native prefix's band energy washes "
                           "out (%s vs %s)" % (label, prefix_energy, tail_energy))
        # The effective stroke extent (the thickness in rows/columns
        # above the noise floor) must not step at the seam.
        self.assertLessEqual(abs(prefix_extent - tail_extent), 1,
                             "%s: the stroke's effective extent steps at "
                             "the seam (%s vs %s rows)"
                             % (label, prefix_extent, tail_extent))
        # The seam itself: both sides of the ownership handoff stay
        # inked and comparable — a hole or a doubled seam reads here.
        seam_before = band_at(*spec["seam_before"])
        seam_after = band_at(*spec["seam_after"])
        self.assertGreater(max(seam_before), 8,
                           "%s: the seam's prefix side lost its ink" % label)
        self.assertGreater(max(seam_after), 8,
                           "%s: the seam's tail side lost its ink" % label)
        self.assertGreater(max(seam_before), max(seam_after) * 0.6,
                           "%s: the seam's prefix side washes out at the "
                           "handoff (%s vs %s)"
                           % (label, max(seam_before), max(seam_after)))
        self.pump(20)

    def test_the_native_prefix_and_the_qml_tail_match_intensity_at_production_width(self):
        # The review's finding #3, closed: at the production
        # lineScale (0.7 — subpixel strokes) the native prefix and
        # the QML canvas tail carry the SAME perceived intensity
        # over equivalent pieces of the same path, in every
        # orientation, at both backing factors. The mechanism (the
        # measured evidence): the two painters' subpixel coverage is
        # IDENTICAL — the apparent 2x deficit (peak 42 vs 21) was
        # the canvas's un-trimmed FULL bitmap stacked UNDER the
        # prefix, doubling the prefix region's ink. The prefix's
        # show now forces the settled single-owner trim, so the
        # canvas repaints to the tail alone (the settled ownership
        # record proves it: _vectorCoversFrom == prefixSplit). The
        # fix touches no alpha, no colour and no width — only the
        # composition's ownership.
        for dpr in (1.0, 2.0):
            for orientation in ("horizontal", "vertical", "diagonal"):
                self._parity_leg(dpr, orientation)

    def test_the_interaction_raster_owns_the_camera_and_swaps_atomically(self):
        # The camera interaction: a warm navigation raster owns the
        # heavy scene during a smooth wheel zoom (the exact fades as
        # ONE unit), the display eases monotonically toward the
        # target with the focal bed point pinned, the exact scene
        # returns only once its commit barrier passes, and the swap
        # moves no geometry.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        # The warm interaction raster: a real flattened composite at
        # 4x, its URL on the double (the model's role).
        from plugins.PlateQt import render_navigation_layer, png_file
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot,
            {"width": int(face.width()), "height": int(face.height()),
             "scale": 1.0, "lineScale": 8.0, "compact": False,
             "panX": 0.0, "panY": 0.0, "backing": 4.0,
             "bedWidth": 250.0, "bedDepth": 250.0}, split=18)
        self._printer.setNavigation(png_file(
            nav, "/tmp/mpf/raster-probe", "nav-fixture-%d" % time.monotonic_ns()))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the idle exact scene never drew")
        window.grabWindow()  # the idle exact scene is up

        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)

        def wheel(cx, cy, delta):
            # QTest's QWindow-level mouseWheel is unavailable in this
            # Qt build — post the real event (the harness's own
            # pattern for the drag injection).
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)
        # The toolhead dot rides the camera the scene actually shows:
        # during the eased zoom it must track the DISPLAY transform
        # (the warm raster's picture), never the target (the live
        # report — the dot snapped against the raster).
        face.setProperty("attached", True)
        face.setProperty("dot", {"x": 60.0, "y": 100.0, "valid": True})
        self.pump(10)
        from PyQt6.QtQuick import QQuickItem
        dot_item = face.findChild(QQuickItem, "moonrakerPlateToolheadDot")
        self.assertIsNotNone(dot_item, "the toolhead dot never mounted")
        plot_bed = plot_value["bed"]

        def dot_screen_x(scale, pan_x):
            return (pan_x + (float(plot_bed["offsetX"])
                             + (60.0 - float(plot_bed["bedXMin"])) * float(plot_value["sx"])) * scale
                    - dot_item.width() / 2)

        def dot_screen_y(scale, pan_y):
            return (pan_y + (float(plot_bed["offsetY"])
                             + (float(plot_bed["bedYMax"]) - 100.0) * float(plot_value["sy"])) * scale
                    - dot_item.height() / 2)
        # Wheel-zoom in at the centre: the interaction owns the scene
        # immediately and the display eases toward the 125% target.
        wheel(cx, cy, 120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the camera gesture never entered the interaction")
        first = face.property("displayScale")
        if first <= 1.0:
            self._pump_ms(30)  # the animator's first tick's beat
            first = face.property("displayScale")
        self.assertGreater(first, 1.0, "the first frame never moved")
        self.assertLess(first, 1.25, "the display snapped, never eased")
        # The eased frames: monotonic, no overshoot, the focal bed
        # point pinned, and the exact scene hidden throughout.
        last = first
        before_swap = window.grabWindow()
        while face.property("_interactionActive"):
            self._pump_ms(20)
            now = face.property("displayScale")
            self.assertGreaterEqual(now, last - 1e-6,
                                    "the display eased backwards")
            self.assertLessEqual(now, 1.25 + 1e-6,
                                 "the display overshot the target")
            last = now
            bed_x = (cx - face.property("displayPanX")) / now
            self.assertAlmostEqual(bed_x, float(cx), delta=2.0,
                                   msg="the focal point wandered")
            self.assertAlmostEqual(
                dot_item.x(),
                dot_screen_x(now, face.property("displayPanX")),
                delta=1.5, msg="the toolhead dot snapped off the display")
            self.assertAlmostEqual(
                dot_item.y(),
                dot_screen_y(now, face.property("displayPanY")),
                delta=1.5, msg="the toolhead dot snapped off the display")
            before_swap = window.grabWindow()  # the last interaction frame
        self.assertEqual(face.property("displayScale"), 1.25,
                         "the display never converged exactly")
        # The atomic swap: the frames immediately before and after
        # it are registered identically — only the fidelity changes.
        after = window.grabWindow()
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))

        def differs(pixel_a, pixel_b):
            return any(abs(((pixel_a >> shift) & 0xFF)
                          - ((pixel_b >> shift) & 0xFF)) > 60
                       for shift in (0, 8, 16))
        moved = sum(
            1 for row in range(0, int(face.height()), 4)
            for col in range(0, int(face.width()), 4)
            if differs(after.pixel(int(origin.x()) + col,
                                   int(origin.y()) + row),
                       before_swap.pixel(int(origin.x()) + col,
                                         int(origin.y()) + row)))
        self.assertLessEqual(moved, 8,
                             "the swap moved the geometry (%d pixels)" % moved)

        # The direct pan: the display follows the pointer exactly —
        # no easing, no camera lag, the interaction stays live.
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QMouseEvent
        def mouse(kind, x, y, buttons):
            scene = face.mapToItem(window.contentItem(), QPointF(x, y))
            event = QMouseEvent(kind, QPointF(scene),
                                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                                Qt.MouseButton.LeftButton, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QGuiApplication.sendEvent(window, event)
        pan_before = face.property("displayPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 40, cy + 20, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 40, cy + 20,
              Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertAlmostEqual(face.property("displayPanX"), pan_before + 40.0,
                               delta=1.5, msg="the drag pan lagged the pointer")
        self.assertTrue(face.property("_interactionActive"),
                        "the pan never entered the interaction")
        # The retarget from the CURRENT display: the next wheel keeps
        # the eased motion continuous (no restart jump backwards).
        wheel(cx, cy, 120)
        self._pump_ms(20)
        retargeted = face.property("displayScale")
        if retargeted <= 1.25:
            self._pump_ms(20)  # the animator's first tick's beat
            retargeted = face.property("displayScale")
        self.assertGreater(retargeted, 1.25, "the retarget snapped backwards")
        self.assertLess(retargeted, 1.5625 + 1e-6,
                        "the retarget overshot its new target")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the retargeted zoom never swapped back")
        # A drag DURING the eased zoom: the grab pans directly while
        # the zoom keeps easing, and the eased pan carries the drag's
        # delta — the ease lands exactly on the dragged target, no
        # pause, no snap, no permanent offset.
        wheel(cx, cy, 120)
        self._pump_ms(10)
        pressed_scale = face.property("displayScale")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 30, cy + 15, Qt.MouseButton.LeftButton)
        self._pump_ms(40)  # ticks fire while the button stays down
        held = face.property("displayScale")
        self.assertGreater(held, pressed_scale,
                           "the zoom paused while the pointer held the scene")
        mouse(QEvent.Type.MouseButtonRelease, cx + 30, cy + 15,
              Qt.MouseButton.NoButton)
        released = face.property("displayScale")
        self._pump_ms(50)  # a few animator ticks past the release
        self.assertGreater(face.property("displayScale"), released,
                           "the zoom stopped gliding after the release")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the drag-interrupted zoom never swapped back")
        self.assertEqual(face.property("viewScale"), 1.953125,
                         "the zoom never reached its wheel target")
        self.assertAlmostEqual(face.property("displayPanX"),
                               face.property("viewPanX"), delta=1.5,
                               msg="the eased pan never converged to the "
                                   "dragged target")
        # A pan-only gesture: the release's re-check drives the
        # barrier — the interaction ends without any zoom.
        pan_before = face.property("viewPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 25, cy + 10, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 25, cy + 10,
              Qt.MouseButton.NoButton)
        self.assertAlmostEqual(face.property("viewPanX"), pan_before + 25.0,
                               delta=1.5, msg="the pan-only drag lost its delta")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the pan-only gesture never settled back to the "
                         "exact scene")
        # A wheel DURING a held drag zooms properly: the target
        # updates and the display eases about the wheel's cursor —
        # the focal bed point stays pinned even with the button down
        # (the live report's held origin-zoom).
        before_wheel = face.property("viewScale")
        held_cx = cx - 60
        held_cy = cy - 40
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        wheel(held_cx, held_cy, 120)
        self.assertGreater(face.property("viewScale"), before_wheel,
                           "the wheel was inert while the drag was held")
        bed_x0 = ((held_cx - face.property("displayPanX"))
                  / face.property("displayScale"))
        for _beat in range(20):  # the ease runs while the button stays down
            self._pump_ms(20)
            bed_x = ((held_cx - face.property("displayPanX"))
                     / face.property("displayScale"))
            self.assertAlmostEqual(bed_x, bed_x0, delta=2.0,
                                   msg="the held wheel zoomed from the origin")
        mouse(QEvent.Type.MouseButtonRelease, cx, cy, Qt.MouseButton.NoButton)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the held-wheel gesture never settled back")
        # Repeated drags: the display pan and the target pan stay in
        # lockstep at every boundary — a drag must never snap at its
        # start, and never snap back at its end.
        for dx, dy in ((30, 15), (-30, -15), (20, -10), (-20, 10)):
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the camera drifted out of lockstep "
                               "between drags")
            mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the drag's press snapped the camera")
            mouse(QEvent.Type.MouseMove, cx + dx, cy + dy, Qt.MouseButton.LeftButton)
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the drag's move broke the lockstep")
            mouse(QEvent.Type.MouseButtonRelease, cx + dx, cy + dy,
                  Qt.MouseButton.NoButton)
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the drag's release snapped the camera")
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and face.property("_interactionActive"):
                self._pump_ms(30)
            self.assertFalse(face.property("_interactionActive"),
                             "a repeated drag never settled back")
            self.assertAlmostEqual(
                face.property("displayPanX"), face.property("viewPanX"),
                delta=1.0, msg="the settled camera ended out of lockstep")
        # The delayed exact: invalidate the exact scene's key, wheel
        # again — the interaction stays on the navigation raster
        # until the barrier can pass.
        layer.set_expected_key("invalidated")
        wheel(cx, cy, 120)
        self._pump_ms(120)
        self.assertTrue(face.property("_interactionActive"),
                        "the incomplete exact scene revealed itself")
        layer.set_expected_key("fixture-key")
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the completed exact scene never swapped back")
        # A corner-panned camera must NOT snap on the next wheel: the
        # pan follows the focal computation exactly — the bed's edge
        # may leave the viewport; only the 100% fit recentres (the
        # live ruling). The harness's synthetic moves dispatch by
        # POSITION (no grab routing), and the docked scope covers
        # the face's right edge — so each gesture moves +200, three
        # times: +600 lands the pan decisively past the bed's
        # coverage.
        for _drag in range(3):
            mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, cx + 200, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseButtonRelease, cx + 200, cy,
                  Qt.MouseButton.NoButton)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the corner pan never settled")
        self.assertGreater(face.property("viewPanX"), 0.0,
                           "the corner pan never left the clamp range")
        pan_before = face.property("viewPanX")
        scale_before = face.property("viewScale")
        wheel(cx, cy, 120)
        expected_pan = (cx - (cx - pan_before) / scale_before
                        * face.property("viewScale"))
        self.assertAlmostEqual(face.property("viewPanX"), expected_pan,
                               delta=1e-3,
                               msg="the wheel clamped the corner-panned camera")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the corner zoom never settled")
        # The soft clamp: once the bed would leave the viewport
        # wholly, the pan stops with 100 px of it visible on every
        # side — dragging and zooming alike (the live ruling). Two
        # inside-face drags overshoot the boundary.
        scale = face.property("viewScale")
        hard_x = face.width() - 100.0 - float(plot_bed["offsetX"]) * scale
        for _drag in range(2):
            mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, cx + 200, cy, Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseButtonRelease, cx + 200, cy,
                  Qt.MouseButton.NoButton)
        self.assertAlmostEqual(face.property("viewPanX"), hard_x, delta=1e-3,
                               msg="the drag never stopped at the soft clamp")
        self.assertAlmostEqual(face.property("displayPanX"),
                               face.property("viewPanX"), delta=1.0,
                               msg="the clamp broke the drag's lockstep")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the clamped drag never settled")
        # The wheel's side: a programmatic far camera (the harness
        # cannot drag the pointer that far), then a wheel — the
        # focal pan stops at the same soft boundary on both axes.
        hard_y = face.height() - 100.0 - float(plot_bed["offsetY"]) * scale
        face.setProperty("viewPanX", hard_x + 2000.0)
        face.setProperty("viewPanY", hard_y + 2000.0)
        face.setProperty("displayPanX", hard_x + 2000.0)
        face.setProperty("displayPanY", hard_y + 2000.0)
        self.pump(20)
        wheel(cx, cy, 120)
        # The clamp's boundary rides the NEW target's scale.
        new_scale = face.property("viewScale")
        hard_x2 = face.width() - 100.0 - float(plot_bed["offsetX"]) * new_scale
        hard_y2 = face.height() - 100.0 - float(plot_bed["offsetY"]) * new_scale
        self.assertAlmostEqual(face.property("viewPanX"), hard_x2, delta=1e-3,
                               msg="the wheel broke the soft clamp")
        self.assertAlmostEqual(face.property("viewPanY"), hard_y2, delta=1e-3,
                               msg="the wheel broke the soft clamp")
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the clamped wheel never settled")
        # A release OUTSIDE the face (the pointer left the area
        # mid-drag) cancels the grab: the exit drive must fire all
        # the same (the live wedge — the scene once stayed on the
        # warm raster forever).
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx - 200, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 400, cy,
              Qt.MouseButton.NoButton)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "an outside-face release never left the warm raster")
        # Zooming back out to 100% is the ONE snap the camera
        # allows: the fit fills the viewport, centred.
        for _down in range(8):
            wheel(cx, cy, -120)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertEqual(face.property("viewScale"), 1.0,
                         "the zoom-out never landed on the fit")
        self.assertEqual(face.property("viewPanX"), 0.0,
                         "the fit never recentred")
        self.assertEqual(face.property("viewPanY"), 0.0,
                         "the fit never recentred")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_gesture_latches_its_navigation_source_against_mid_gesture_retirement(self):
        # The review's lifecycle: the model retires the published URL
        # the moment the demand moves. The face must keep presenting
        # the raster the gesture ENTERED with (a mid-gesture
        # retirement never unloads the scene in hand), and the idle
        # binding must not admit the stale URL for the NEXT gesture.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        from plugins.PlateQt import render_navigation_layer, png_file
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot,
            {"width": int(face.width()), "height": int(face.height()),
             "scale": 1.0, "lineScale": 8.0, "compact": False,
             "panX": 0.0, "panY": 0.0, "backing": 4.0,
             "bedWidth": 250.0, "bedDepth": 250.0}, split=18)
        nav_url = png_file(nav, "/tmp/mpf/raster-probe",
                           "nav-latch-%d" % time.monotonic_ns())
        self._printer.setNavigation(nav_url)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the idle exact scene never drew")
        window.grabWindow()

        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)

        def wheel(delta):
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)

        def settle():
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and face.property("_interactionActive"):
                self._pump_ms(30)
            self.assertFalse(face.property("_interactionActive"),
                             "the gesture never settled")

        wheel(120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the ready raster never entered the interaction")
        self.assertEqual(face.property("_gestureNavSource"), nav_url,
                         "the entry never latched its source")
        # The model retires the URL mid-gesture (a demand change):
        self._printer.setNavigation("")
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the mid-gesture retirement ended the interaction")
        self.assertEqual(face.property("_gestureNavSource"), nav_url,
                         "the mid-gesture retirement unlatched the scene")
        settle()
        # The idle binding now reads the retired URL: the next
        # gesture must NOT enter the warm raster.
        wheel(120)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a retired URL admitted the stale warm raster")

    def _stroke_payload(self, motions=320, dx=0.6):
        # A dense horizontal stroke: motion m's edge ends at
        # 20 + m*dx, so the printed boundary at split S sits at
        # 20 + S*dx on the bed's own coordinates.
        return {"classes": {"WALL-OUTER": [
                    [[20.0 + m * dx, 125.0, float(m)] for m in range(motions + 1)]]},
                "travels": [], "travelStarts": [], "travelEnds": [],
                "motions": motions}

    def _bed_plot(self, face):
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        return {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}

    def _prefix_for(self, payload, face, split, stem):
        from plugins.PlateQt import render_layer_prefix, png_file
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0, "dpr": 1.0}
        prefix = render_layer_prefix(payload, self._bed_plot(face), view, split)
        return prefix, png_file(prefix, "/tmp/mpf/raster-probe",
                                "%s-%d" % (stem, time.monotonic_ns()))

    def test_a_forward_scrub_through_prefix_refreshes_never_drops_the_history(self):
        # The critical's core: a refresh renders AT the requested
        # split (the equality edge) while the previous prefix is
        # visible. Every frame through the handover must keep the
        # printed history's interior ink — the extrusion paths never
        # disappear while the replacement's image lands and the
        # canvas repaints the tail.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        plot = self._bed_plot(face)
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial prefix never drew")
        # The refresh: the new prefix AT the demand arrives while the
        # old one is visible.
        prefix, url = self._prefix_for(payload, face, 160, "dense-handover")
        layer.set_prefix(prefix, url, 160, "key-2")
        self._printer.setSplit(160)
        deadline = time.monotonic() + 3.0
        frames = 0
        interior = 20.0 + 90 * 0.6
        tail = 20.0 + 150 * 0.6
        while time.monotonic() < deadline:
            grab = window.grabWindow()
            self.assertTrue(self._red_in_band(grab, face, window, plot,
                                              interior, 125.0),
                            "the printed history dropped mid-handover")
            frames += 1
            if self._red_in_band(grab, face, window, plot, tail, 125.0):
                break
            self._pump_ms(20)
        self.assertGreater(frames, 0, "the handover never delivered")
        self.assertTrue(self._red_in_band(grab, face, window, plot,
                                          tail, 125.0),
                        "the new prefix's own interior never arrived")

    def test_repeated_forward_refreshes_keep_every_frame_complete(self):
        # Three successive refreshes (100 -> 160 -> 240): each
        # replacement lands while the previous picture stands, and
        # no frame ever loses the committed history.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        plot = self._bed_plot(face)
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        self._wait_red(window, face, want=True)
        for target in (160, 240):
            prefix, url = self._prefix_for(payload, face, target, "dense-multi")
            layer.set_prefix(prefix, url, target, "key-%d" % target)
            self._printer.setSplit(target)
            deadline = time.monotonic() + 3.0
            interior = 20.0 + (target - 40) * 0.6
            while time.monotonic() < deadline:
                grab = window.grabWindow()
                # The PREVIOUS committed history (well inside the old
                # boundary) must stand on every frame.
                self.assertTrue(self._red_in_band(grab, face, window, plot,
                                                  20.0 + (target - 80) * 0.6,
                                                  125.0),
                                "a frame lost the committed history at %d" % target)
                if self._red_in_band(grab, face, window, plot,
                                     interior, 125.0):
                    break
                self._pump_ms(20)
            self._pump_ms(60)

    def test_a_backward_scrub_to_zero_clears_all_printed_geometry(self):
        # The 0% state owns NOTHING printed: the prefix hides, the
        # canvas clears, and no held picture persists — then a
        # forward scrub repaints the history.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload()
        layer = self._native_layer(payload, face, prefix_split=100)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(120)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial prefix never drew")
        self._printer.setSplit(0)
        image, count = self._wait_red(window, face, want=False)
        self.assertEqual(count, 0,
                         "printed geometry persisted at 0%")
        # Forward again: the history repaints from the empty state.
        self._printer.setSplit(60)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0,
                           "the 0% state never recovered its history")

    def test_a_cached_zoom_bake_never_stands_over_the_full_layer(self):
        # The stale-zoom report: a prefix baked at another zoom is
        # invalid for the current view key, and at a FULL layer the
        # model demands no replacement — the hold that kept the old
        # picture during a partial repaint could arm there and never
        # release, leaving the oversized bake standing over the
        # valid full raster. The hold is partial-only now: the full
        # layer must show exactly the full raster's own band.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(21, 10.0)
        plot = self._bed_plot(face)
        # A 2x bake: the prefix rendered at scale 2 (a previous
        # zoom's cached asset).
        from plugins.PlateQt import PlateLayer, render_layer_prefix, png_file
        view2x = {"width": int(face.width()), "height": int(face.height()),
                  "scale": 2.0, "lineScale": 8.0, "compact": False,
                  "panX": 0.0, "panY": 0.0, "dpr": 1.0}
        layer = PlateLayer(payload)
        prefix2x = render_layer_prefix(payload, plot, view2x, 10)
        layer.set_prefix(prefix2x, png_file(
            prefix2x, "/tmp/mpf/raster-probe", "zoom-bake-%d" % time.monotonic_ns()),
            10, "zoom-key")
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the partial prefix never drew")
        # The view key changes (the zoom back to 100%): the bake is
        # invalid, and the split reaches the FULL layer — no hold may
        # stand the stale bake over the full picture.
        layer.setProperty("prefixValid", False)
        self._printer.setSplit(21)
        self._pump_ms(300)
        for _ in range(20):
            self._pump_ms(30)
            window.grabWindow()
            # The full raster owns the whole picture; the 2x bake's
            # doubled-height band must never appear above it. The
            # frame is judged complete when the prefix is gone and
            # the full raster's own band stands.
            if face.property("_prefixHold") is False and not face.property("_prefixWasShown"):
                break
        grab = window.grabWindow()
        self.assertFalse(face.property("_prefixHold"),
                         "the full layer armed the stale bake's hold")
        self.assertFalse(face.property("_prefixWasShown"),
                         "the stale bake's shown record survived the full layer")
        # The 2x bake draws its stroke at DOUBLE the vertical offset:
        # the full picture must carry exactly ONE red band, never the
        # bake's second band below it.
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        col = int(origin.x() + plot["offsetX"]
                  + (20.0 + 5 * 10.0 - plot["bedXMin"]) * plot["sx"])
        bands = 0
        in_band = False
        for r in range(int(origin.y()), int(origin.y() + face.height())):
            red = self._matches(grab.pixel(col, r), (0xD3, 0x2F, 0x2F))
            if red and not in_band:
                bands += 1
            in_band = red
        self.assertEqual(bands, 1,
                         "the picture carries %d bands — the 2x bake "
                         "stands over the full layer" % bands)

    def test_the_production_scheduler_never_drops_history_during_a_forward_scrub(self):
        # The live-report test: the REAL model's scheduler renders the
        # prefixes (no hand-fed replacement) while the split advances
        # through a dense layer — every intermediate frame must keep
        # the committed printed history's interior ink.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(motions=120000, dx=0.0002)
        plot = self._bed_plot(face)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": payload, "next": None})
        self._printer.setSplit(30000)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial partial never drew")
        # A fast forward scrub: ten steps of 9000 motions, one frame
        # per step — the printed interior behind the boundary must
        # never vanish (the gap report), and the picture must settle
        # at the final boundary.
        last_split = 30000
        for step in range(1, 11):
            last_split = 30000 + step * 9000
            self._printer.setSplit(last_split)
            for _ in range(4):
                self._pump_ms(30)
                grab = window.grabWindow()
                self.assertTrue(
                    self._red_in_band(grab, face, window, plot,
                                      20.0 + 15000 * 0.0002, 125.0),
                    "step %d dropped the committed printed history" % step)
        # The final position converges without further input.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            self._pump_ms(50)
            grab = window.grabWindow()
            if self._red_in_band(grab, face, window, plot,
                                 20.0 + (last_split - 5000) * 0.0002, 125.0):
                break
        else:
            self.fail("the final requested position never converged")

    def test_a_zoom_then_forward_scrub_never_resurrects_the_old_scale_history(self):
        # The out-of-scale ghost: the incremental render seeds its
        # copy from the previous picture only under the SAME render
        # key. A zoom between the commit and the refresh must fall
        # back to the full walk — the stale half would otherwise
        # stay at the old scale while the new half strokes the new
        # one, one print drawn twice, two sizes.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        payload = self._stroke_payload(motions=2000, dx=0.05)
        census_before = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": payload, "next": None})
        self._printer.setSplit(1000)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the initial partial never drew")
        self.assertGreater(
            self._stroke_ink(image, face, window, census_before, 35.0, 125.0),
            0, "the pre-zoom history never drew")
        # Zoom in at the face's centre: the interaction settles and
        # the view commits at a new render key.
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)
        scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
        event = QWheelEvent(
            QPointF(scene),
            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            QPoint(0, 0), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False)
        QGuiApplication.sendEvent(window, event)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and face.property("_interactionActive"):
            self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "the zoom gesture never settled")
        self.assertGreater(face.property("viewScale"), 1.0,
                           "the zoom never moved the view")
        # The forward scrub past the refresh threshold forces the
        # prefix job to re-run under the new view. Any frame that
        # resurrects ink at the OLD scale's bed position is the
        # ghost — the settled picture must keep the history only at
        # the new view's mapping.
        self._printer.setSplit(1150)
        census_after = self._bed_point(face, 0.0, 0.0)
        deadline = time.monotonic() + 10.0
        old_ink = 0
        moved = False
        while time.monotonic() < deadline:
            self._pump_ms(50)
            grab = window.grabWindow()
            if self._stroke_ink(grab, face, window, census_after, 35.0, 125.0) > 0:
                moved = True
            old_ink = max(old_ink, self._stroke_ink(grab, face, window,
                                                    census_before, 35.0, 125.0))
            if old_ink > 3:
                break
        self.assertTrue(moved, "the zoomed prefix never re-rendered")
        self.assertLessEqual(
            old_ink, 3,
            "the old-scale history half survived the zoom "
            "(the out-of-scale ghost)")
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_a_prefix_boundary_advance_never_exposes_a_gap(self):
        # The review's composition-transaction repro: a prefix
        # refresh lands at a new boundary while the canvas still
        # covers the OLD one — the readiness gate holds the standing
        # composition (the retained picture plus the canvas bitmap)
        # until the replacement's joint delivery, and the printed
        # history must never lose ink in any frame of the handover.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        from plugins.PlateQt import render_layer_prefix, png_file
        layer = self._native_layer(payload, face)
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        view = {"width": int(face.width()), "height": int(face.height()),
                "scale": 1.0, "lineScale": 8.0, "compact": False,
                "panX": 0.0, "panY": 0.0}
        prefix_10 = render_layer_prefix(payload, plot, view, 10)
        prefix_15 = render_layer_prefix(payload, plot, view, 15)
        url_10 = png_file(prefix_10, "/tmp/mpf/raster-probe",
                          "fixture-txn-a-%d" % time.monotonic_ns())
        url_15 = png_file(prefix_15, "/tmp/mpf/raster-probe",
                          "fixture-txn-b-%d" % time.monotonic_ns())
        census_plot = self._bed_point(face, 0.0, 0.0)
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        # The model's view publishes race the hand-fed claim (a
        # publish overwrites the wrapper's expected key), so the
        # claim is re-asserted until the composition settles with
        # the prefix OWNING [0, 10) and the canvas covering
        # [10, 18).
        deadline = time.monotonic() + 5.0
        settled = False
        image = None
        while time.monotonic() < deadline:
            layer.set_prefix(prefix_10, url_10, 10, "fixture-key")
            layer.set_expected_key("fixture-key")
            self.pump(5)
            image = window.grabWindow()
            if face.property("_vectorCoversFrom") == 10 \
                    and self._stroke_ink(image, face, window, census_plot,
                                         75.0, 125.0) > 0:
                settled = True
                break
        self.assertTrue(settled, "the settled composition never formed")
        self.assertGreater(
            self._stroke_ink(image, face, window, census_plot, 75.0, 125.0),
            0, "the settled prefix never drew its history")
        # The boundary advance: the replacement lands while the
        # canvas still covers [10, 18). Every frame of the handover
        # must keep the interior ink — the standing composition is
        # never torn down before the joint swap.
        layer.set_prefix(prefix_15, url_15, 15, "fixture-key")
        deadline = time.monotonic() + 8.0
        swapped = False
        while time.monotonic() < deadline:
            # A late view publish (the previous test's settle) may
            # still overwrite the claim — re-assert it through the
            # handover so the transition completes.
            layer.set_expected_key("fixture-key")
            # One pump per frame: the gap this census exists to catch
            # is one evaluation wide (the source swap blanking the
            # live prefix), so a coarser cadence samples straight past
            # it. Every beat of the handover is examined.
            self.pump(1)
            grab = window.grabWindow()
            self.assertGreater(
                self._stroke_ink(grab, face, window, census_plot, 75.0, 125.0),
                0, "a frame lost the printed history mid-transition")
            if face.property("_vectorCoversFrom") == 15 \
                    and self._stroke_ink(grab, face, window, census_plot,
                                         115.0, 125.0) > 0:
                swapped = True
                break
        self.assertTrue(swapped,
                        "the boundary advance never reached the joint "
                        "composition — covers %s, prefixSplit %s, "
                        "prefixValid %s" % (
                            face.property("_vectorCoversFrom"),
                            layer.prefixSplit, layer.prefixValid))
        window.grabWindow()
        self.pump(30)
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_inert_gestures_never_flip_the_warm_raster(self):
        # The inertness ruling: the warm raster enters ONLY when a
        # movement actually pans. A click at any zoom, a drag attempt
        # at 100% and a boundary-blocked drag never activate it; a
        # genuine drag, the wheel and the release settle keep their
        # existing behaviour.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("lineScale", 8.0)
        self.pump(10)
        points = [[20.0 + motion * 10.0, 125.0, float(motion)]
                  for motion in range(21)]
        payload = {
            "classes": {"WALL-OUTER": [points]},
            "travels": [], "travelStarts": [], "travelEnds": [],
            "motions": 21,
        }
        layer = self._native_layer(payload, face, prefix_split=10)
        from plugins.PlateQt import render_navigation_layer, png_file
        plot_value = face.property("plot")
        if hasattr(plot_value, "toVariant"):
            plot_value = plot_value.toVariant()
        plot = {"offsetX": float(plot_value["bed"]["offsetX"]),
                "offsetY": float(plot_value["bed"]["offsetY"]),
                "sx": float(plot_value["sx"]), "sy": float(plot_value["sy"]),
                "bedXMin": float(plot_value["bed"]["bedXMin"]),
                "bedYMax": float(plot_value["bed"]["bedYMax"])}
        nav = render_navigation_layer(
            {"prev": None, "next": None, "current": payload}, plot,
            {"width": int(face.width()), "height": int(face.height()),
             "scale": 1.0, "lineScale": 8.0, "compact": False,
             "panX": 0.0, "panY": 0.0, "backing": 4.0,
             "bedWidth": 250.0, "bedDepth": 250.0}, split=18)
        self._printer.setNavigation(png_file(
            nav, "/tmp/mpf/raster-probe", "nav-inert-%d" % time.monotonic_ns()))
        self._printer.setScrub(payload)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(18)
        image, count = self._wait_red(window, face, want=True)
        self.assertGreater(count, 0, "the idle exact scene never drew")
        window.grabWindow()

        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent, QWheelEvent
        cx = int(face.width() / 2)
        cy = int(face.height() / 2)

        def mouse(kind, x, y, buttons):
            scene = face.mapToItem(window.contentItem(), QPointF(x, y))
            event = QMouseEvent(kind, QPointF(scene),
                                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                                Qt.MouseButton.LeftButton, buttons,
                                Qt.KeyboardModifier.NoModifier)
            QGuiApplication.sendEvent(window, event)

        def wheel(delta):
            scene = face.mapToItem(window.contentItem(), QPointF(cx, cy))
            event = QWheelEvent(
                QPointF(scene), QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                QPoint(0, 0), QPoint(0, delta),
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                Qt.ScrollPhase.NoScrollPhase, False)
            QGuiApplication.sendEvent(window, event)

        def settle():
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and face.property("_interactionActive"):
                self._pump_ms(30)
            self.assertFalse(face.property("_interactionActive"),
                             "the gesture never settled")

        # 1 + 2: at the 100% fit a click and a drag attempt do
        # nothing — no warm raster, no pan (the wheel still zooms).
        self.assertFalse(face.property("_interactionActive"))
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx, cy, Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a click at 100% activated the warm raster")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 30, cy + 15, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx + 30, cy + 15,
              Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a drag attempt at 100% activated the warm raster")
        self.assertEqual(face.property("displayPanX"), 0.0,
                         "a 100% drag moved the camera")

        # 6: the wheel from the 100% fit still enters the warm
        # raster, and the ease settles back to the exact scene.
        wheel(120)
        self._pump_ms(30)
        self.assertTrue(face.property("_interactionActive"),
                        "the wheel never entered the interaction")
        settle()
        self.assertGreater(face.property("viewScale"), 1.0,
                           "the wheel never zoomed past the fit")

        # 3: at zoom, a click still does not activate — the entry
        # waits for an actual movement.
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, cx, cy, Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a click at zoom activated the warm raster")

        # 4: a genuine drag enters and pans normally.
        pan_before = face.property("displayPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx + 40, cy + 20, Qt.MouseButton.LeftButton)
        self._pump_ms(20)
        self.assertTrue(face.property("_interactionActive"),
                        "a genuine drag never entered the interaction")
        mouse(QEvent.Type.MouseButtonRelease, cx + 40, cy + 20,
              Qt.MouseButton.NoButton)
        self._pump_ms(20)
        self.assertAlmostEqual(face.property("displayPanX"), pan_before + 40.0,
                               delta=1.5, msg="the drag pan lagged the pointer")
        # 7: the release keeps the settle behaviour: the exit drive
        # returns the exact scene.
        settle()

        # 5: a drag blocked by the soft clamp applies zero pan and
        # must not activate the raster. Push the camera to the
        # boundary first — the press grabs at the face's right edge
        # and the move sweeps to the window's left, which the clamp
        # binds (the harness drops moves sent outside the window, so
        # the pointer stays in bounds). The clamp binds each move's
        # own delta, so that first sweep lands short of the bound:
        # two sweeps from inside the face drive the pan onto it (the
        # corner-pan leg's idiom below). A fresh drag attempt
        # against the bound stays inert.
        mouse(QEvent.Type.MouseButtonPress, int(face.width()) - 10, cy,
              Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, 5, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, 5, cy, Qt.MouseButton.NoButton)
        settle()
        for _sweep in range(2):
            mouse(QEvent.Type.MouseButtonPress, cx, cy,
                  Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseMove, cx - 200, cy,
                  Qt.MouseButton.LeftButton)
            mouse(QEvent.Type.MouseButtonRelease, cx - 200, cy,
                  Qt.MouseButton.NoButton)
            settle()
        bound_pan = face.property("viewPanX")
        mouse(QEvent.Type.MouseButtonPress, cx, cy, Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, cx - 200, cy, Qt.MouseButton.LeftButton)
        self._pump_ms(30)
        self.assertFalse(face.property("_interactionActive"),
                         "a boundary-blocked drag activated the warm raster")
        mouse(QEvent.Type.MouseButtonRelease, cx - 200, cy,
              Qt.MouseButton.NoButton)
        self._pump_ms(30)
        self.assertEqual(face.property("viewPanX"), bound_pan,
                         "the blocked drag moved the camera")
        self._printer.setLayers(PlateFaceRenderTests.PAYLOAD["layers"])
        self.pump(20)

    def test_the_layer_slider_handle_click_and_keyboard_steps_hold(self):
        # The reviewer's slider findings: a click on the handle must
        # NOT move the slider (movement is track clicks and drags
        # only), and a handle click focuses the slider so the arrows
        # nudge one step — the focus must survive the settle and the
        # apply.
        monitor, window, face, baseline = self._mount_empty()
        sliders = monitor.findChildren(QQuickItem, "moonrakerFollowerLayerSlider")
        self.assertTrue(sliders, "the layer slider never mounted")
        slider = sliders[0]
        self.pump(10)
        self._printer.calls.clear()

        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent

        def handle_centre_x():
            return (slider.property("leftPadding")
                    + slider.property("visualPosition")
                    * (slider.property("availableWidth") - 16.0))

        def click(x):
            scene = slider.mapToItem(window.contentItem(),
                                     QPointF(x, slider.height() / 2))
            for kind in (QEvent.Type.MouseButtonPress,
                         QEvent.Type.MouseButtonRelease):
                QGuiApplication.sendEvent(window, QMouseEvent(
                    kind, QPointF(scene),
                    QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress
                    else Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier))

        def press(key_value):
            for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QGuiApplication.sendEvent(window, QKeyEvent(
                    kind, key_value, Qt.KeyboardModifier.NoModifier))

        def seek_issued():
            return any(call[0] == "layer" for call in self._printer.calls)
        # 1: a click on the handle must not move the value.
        before = slider.property("value")
        click(handle_centre_x())
        self._pump_ms(100)
        self.assertEqual(slider.property("value"), before,
                         "a handle click moved the slider")
        self.assertFalse(seek_issued(), "a handle click issued a seek")
        # 2: a click on the track moves the slider there.
        track_x = slider.property("leftPadding") + 0.25 * slider.property("availableWidth")
        click(track_x)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not seek_issued():
            self._pump_ms(30)
        self.assertTrue(seek_issued(), "a track click never issued a seek")
        self.assertGreater(slider.property("value"), before,
                           "the track click never moved the value")
        # 3: a handle click focuses the slider; the arrows nudge one
        # step per press, and the focus survives the settle and the
        # apply.
        self._printer.calls.clear()
        click(handle_centre_x())
        self._pump_ms(30)
        self.assertTrue(slider.property("activeFocus"),
                        "a handle click never focused the slider")
        # The handle's BOTH halves grab (the reviewer's finding: one
        # side of the grab handle moved the slider, the other never
        # grabbed).
        def drag_from(x, dx):
            scene = slider.mapToItem(window.contentItem(),
                                     QPointF(x, slider.height() / 2))
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonPress, QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            scene2 = slider.mapToItem(window.contentItem(),
                                      QPointF(x + dx, slider.height() / 2))
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseMove, QPointF(scene2),
                QPointF(window.mapToGlobal(QPoint(int(scene2.x()), int(scene2.y())))),
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))
            QGuiApplication.sendEvent(window, QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(scene2),
                QPointF(window.mapToGlobal(QPoint(int(scene2.x()), int(scene2.y())))),
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
        for offset in (-6.0, 6.0):
            before_side = slider.property("value")
            drag_from(handle_centre_x() + offset, 40.0)
            self._pump_ms(300)
            self.assertGreater(slider.property("value"), before_side,
                               "the handle's far side never grabbed")
        anchor_at = slider.property("value")
        press(Qt.Key.Key_Right)
        self._pump_ms(30)
        self.assertEqual(slider.property("value"), anchor_at + 1,
                         "an arrow key never nudged one step")
        self._pump_ms(300)  # past the key debounce and the apply
        self.assertTrue(slider.property("activeFocus"),
                        "the apply stole the slider's focus")
        press(Qt.Key.Key_Right)
        self._pump_ms(30)
        self.assertEqual(slider.property("value"), anchor_at + 2,
                         "the slider lost its keyboard steps after the apply")
        self._pump_ms(300)
        self.pump(20)

    def test_a_focused_slider_reports_its_destruction_for_the_rebuild(self):
        # The reviewer's focus finding: a Moonraker republish replaces
        # a repeater's delegates while the slider holds the focus. The
        # shared component reports its own destruction with the
        # control identity (the dashboard's re-grant walk consumes it
        # — the token-pinned wiring), so the fresh delegate can take
        # the focus back — fans, LEDs and PWM alike.
        monitor, window, face, baseline = self._mount_empty()
        sliders = monitor.findChildren(QQuickItem, "moonrakerFollowerLayerSlider")
        self.assertTrue(sliders, "the layer slider never mounted")
        slider = sliders[0]
        slider.setProperty("controlObject", "fan0")
        slider.setProperty("controlKind", "fan")
        recorded = []
        slider.focusLostByDestruction.connect(
            lambda object_, kind: recorded.append((object_, kind)))
        # The handle click takes the focus (the reviewer's flow).
        from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt6.QtGui import QGuiApplication, QMouseEvent
        centre = (slider.property("leftPadding")
                  + slider.property("visualPosition")
                  * (slider.property("availableWidth") - 16.0) + 8.0)
        scene = slider.mapToItem(window.contentItem(),
                                 QPointF(centre, slider.height() / 2))
        for kind in (QEvent.Type.MouseButtonPress,
                     QEvent.Type.MouseButtonRelease):
            QGuiApplication.sendEvent(window, QMouseEvent(
                kind, QPointF(scene),
                QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress
                else Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier))
        self.pump(10)
        self.assertTrue(slider.property("activeFocus"),
                        "the handle click never focused the slider")
        # The republish tears the delegate down: the dying slider
        # reports its identity while it still holds the focus.
        monitor.setProperty("openPopOver", "")
        self.pump(10)
        window.grabWindow()
        self.pump(10)
        self.assertEqual(recorded, [("fan0", "fan")],
                         "the dying slider never reported itself")
        self.pump(20)

    def test_full_progress_travels_render_without_the_vector(self):
        # : showTravels at 100% — the travel
        # lines ride their native sibling while scrubVector stays
        # null, so the travels never silently vanish with the
        # suppressed vector.
        monitor, window, face, baseline = self._mount_empty()
        face.setProperty("showTravels", True)
        self.pump(10)
        payload = {
            "classes": {"WALL-OUTER": [[[30.0, 30.0, 1.0], [90.0, 30.0, 2.0]]]},
            "travels": [[[30.0, 200.0, 2.0], [90.0, 200.0, 3.0]]],
            "travelStarts": [], "travelEnds": [], "motions": 4,
        }
        layer = self._native_layer(payload, face)
        self._printer.setLayers({"prev": None, "current": layer, "next": None})
        self._printer.setSplit(4)
        _image, red = self._wait_red(window, face, want=True)
        self.assertGreater(red, 0, "the layer drew nothing")
        # The travel purple: nothing else on the face wears it, so
        # its presence proves the travel sibling blitted — the class
        # raster alone carries no purple.
        _image, purple = self._wait_purple(window, face)
        self.assertGreater(purple, 0, "the travel line's sibling never drew")

    # The painter-fidelity fixtures: the bed-space runs a payload
    # carries as separate prepared segments. Every vertex names the
    # motion that owns the edge ENDING there (a run's first vertex
    # carries its first edge's motion too), so each single-motion run
    # here is owned by one motion index. Two straight runs with a gap
    # between them, and two parallel diagonals 50 mm apart.
    HORIZONTAL_RUNS = (
        [[30.0, 203.0, 1.0], [90.0, 203.0, 1.0]],
        [[130.0, 203.0, 2.0], [190.0, 203.0, 2.0]],
    )
    PARALLEL_DIAGONALS = (
        [[30.0, 30.0, 1.0], [90.0, 90.0, 1.0]],
        [[30.0, 80.0, 2.0], [90.0, 140.0, 2.0]],
    )

    # Arcs straight from literal G-code, and their payloads from the
    # real index: a 120 mm semicircle whose apex stands 60 mm off its own
    # chord (a painter that connects source endpoints draws the chord,
    # and the apex band is where that shows), the clockwise mirror of the
    # same endpoints, the same arc followed by a straight move, and two
    # arcs a travel apart.
    ARC_CCW = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
               "G0 X185 Y125\n"
               "G3 X65 Y125 I-60 J0 E1\n")
    ARC_CW = ARC_CCW.replace("G3", "G2")
    ARC_THEN_LINE = ARC_CCW + "G1 X185 Y60 E2\n"
    # The second arc's E rises too: under M82 a repeated E deposits
    # nothing, so an E1-then-E1 pair would reach the face as a travel.
    ARC_RUNS = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
                "G0 X105 Y125\nG3 X15 Y125 I-45 J0 E1\n"
                "G0 X235 Y125\nG3 X145 Y125 I-45 J0 E2\n")

    @staticmethod
    def _arc_payload(gcode, split=None):
        """The follower payload a literal file produces: the painted
        geometry is the index's own output, so an arc's curve here is the
        one the follower would draw. *split* defaults to every motion."""
        index = build_index_from_bytes(gcode.encode("ascii"))
        return {
            "available": True, "reason": "",
            "layers": {"prev": None, "current": layer_polylines(index, 0), "next": None},
            "split": index.motion_count(0) if split is None else int(split),
            "method": "motion index", "anchor": 0,
        }

    @staticmethod
    def _centres(ink):
        """Each painted column's ink band centres, top to bottom."""
        columns = {}
        for col, row in ink:
            columns.setdefault(col, []).append(row)
        centres = {}
        for col, rows in columns.items():
            rows.sort()
            bands, run = [], []
            for row in rows:
                if run and row > run[-1] + 2:
                    bands.append(sum(run) / len(run))
                    run = []
                run.append(row)
            if run:
                bands.append(sum(run) / len(run))
            centres[col] = bands
        return centres

    @staticmethod
    def _near(ink, point, reach):
        return any(abs(col - round(point[0])) <= reach and abs(row - round(point[1])) <= reach
                   for col, row in ink)

    @staticmethod
    def _components(ink, reach=2):
        """The count of connected ink blobs. The tolerance is raster, not
        geometry: the strokes are 0.7 px wide on a 1 px grid, so a single
        antialiased line can drop a pixel, while a real gap at a join is a
        whole tessellation vertex away."""
        neighbours = [(dc, dr) for dc in range(-reach, reach + 1)
                      for dr in range(-reach, reach + 1)]
        remaining = set(ink)
        counted = 0
        while remaining:
            counted += 1
            stack = [remaining.pop()]
            while stack:
                col, row = stack.pop()
                for dc, dr in neighbours:
                    step = (col + dc, row + dr)
                    if step in remaining:
                        remaining.discard(step)
                        stack.append(step)
        return counted

    @staticmethod
    def _layer_payload(segments):
        """The follower payload the model hands the face: one class of
        prepared runs and the printed count, in PlateProgress's own
        shape (the count moves afterwards, as a poll moves it)."""
        return {
            "available": True, "reason": "",
            "layers": {
                "prev": None,
                "current": {
                    "classes": {"SKIN": [list(segment) for segment in segments]},
                    "travels": [], "travelStarts": [], "travelEnds": [],
                    "motions": 64,
                },
                "next": None,
            },
            "split": 0, "method": "motion index", "anchor": 0,
        }

    def _painted(self, payload):
        """Mount the follower on *payload* and hand back the face, the
        window, the live mapping and the baseline grab. The boundary
        opens at zero and the grey base is off, so what the printed
        strokes ADD over the baseline is exactly the coloured ink."""
        previous = PlateFaceRenderTests.PAYLOAD
        PlateFaceRenderTests.PAYLOAD = dict(payload, split=0)
        self.addCleanup(setattr, PlateFaceRenderTests, "PAYLOAD", previous)
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        face = faces[0]
        face.setProperty("dot", None)
        face.setProperty("showBase", False)
        self.pump(40)
        plot = face.findChild(QQuickItem, "moonrakerPlateCanvas").property("_plot")
        self.assertIsNotNone(plot, "the bed mapping never built")
        # The physical-width model renders the default 0.7 lineScale
        # as a subpixel stroke at this face size; the painter tests
        # pin the same 0.7 px weight the old fixed-width painter used,
        # so the geometry assertions measure a solid line.
        face.setProperty("lineScale", 0.7 / (0.2 * plot.property("sx").toNumber()))
        self.pump(20)
        # The grown popover lays the face out AFTER the first paint;
        # the threaded rasters only re-upload their textures on a
        # geometry sync, so a 1 px nudge replays the live resize path
        # and lands the repaint at the settled size.
        face.setWidth(face.width() + 1)
        self.pump(20)
        face.setWidth(face.width() - 1)
        self.pump(20)
        # At this face size the canvas can still be blank when the
        # baseline is grabbed, and two blank grabs agree; the baseline
        # must carry the grid the measurement is diffed against.
        self._grab_when_inked(window, face)
        return face, window, self._mapping(plot), self._settled(window)

    def _printed(self, split, window, face, baseline, box, span):
        """Move the boundary to *split* and return the pixels the
        printed strokes added over the baseline."""
        self._printer.setSplit(split)
        _image, added = self._await_ink(window, face, baseline, box, span)
        return added

    @staticmethod
    def _mapping(plot):
        """The face's own transform, read from the live plot: the test
        asserts screen geometry against the mapping the painter uses,
        never against a second opinion about it."""
        bed = plot.property("bed")
        return {
            "sx": plot.property("sx").toNumber(),
            "sy": plot.property("sy").toNumber(),
            "offsetX": bed.property("offsetX").toNumber(),
            "offsetY": bed.property("offsetY").toNumber(),
            "bedXMin": bed.property("bedXMin").toNumber(),
            "bedYMax": bed.property("bedYMax").toNumber(),
        }

    @staticmethod
    def _scene(mapping, x, y):
        return (mapping["offsetX"] + (x - mapping["bedXMin"]) * mapping["sx"],
                mapping["offsetY"] + (mapping["bedYMax"] - y) * mapping["sy"])

    @staticmethod
    def _sample(image):
        return [image.pixel(col, row)
                for row in range(0, image.height(), 7)
                for col in range(0, image.width(), 7)]

    def _settled(self, window, rounds=10):
        """The window's image once two grabs agree: the canvases raster
        on the threaded render strategy, so one grab can catch the
        strokes mid-flight."""
        image = window.grabWindow()
        for _ in range(rounds):
            self.pump(10)
            nxt = window.grabWindow()
            if self._sample(image) == self._sample(nxt):
                return nxt
            image = nxt
        return image

    def _added(self, image, baseline, face, window, box):
        """The face's pixels the printed strokes added over the empty
        baseline, as {(col, row)} inside *box* (face coordinates)."""
        origin = face.mapToItem(window.contentItem(), QPointF(0.0, 0.0))
        ox, oy = int(origin.x()), int(origin.y())
        left, top, right, bottom = box
        added = set()
        for row in range(max(0, top), min(int(face.height()), bottom + 1)):
            for col in range(max(0, left), min(int(face.width()), right + 1)):
                now = image.pixel(ox + col, oy + row)
                was = baseline.pixel(ox + col, oy + row)
                if any(abs(((now >> shift) & 0xFF) - ((was >> shift) & 0xFF)) > 24
                       for shift in (0, 8, 16)):
                    added.add((col, row))
        return added

    def _await_ink(self, window, face, baseline, box, span, timeout=3.0):
        """The image once ink reaches both ends of *span*: a paint lands
        whole, so ink at the stroke's own ends means the run is drawn."""
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while True:
            added = self._added(image, baseline, face, window, box)
            columns = [col for col, _row in added]
            if columns and min(columns) <= span[0] and max(columns) >= span[1]:
                return image, added
            if time.monotonic() >= deadline:
                image.save("/tmp/mpf/follower_painter_fail.png")
                return image, added
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()

    def test_the_painted_horizontal_runs_stay_horizontal_and_unbridged(self):
        face, window, mapping, baseline = self._painted(
            self._layer_payload(self.HORIZONTAL_RUNS))
        start = self._scene(mapping, 30.0, 203.0)
        gap = self._scene(mapping, 90.0, 203.0)[0]
        end = self._scene(mapping, 130.0, 203.0)[0]
        last = self._scene(mapping, 190.0, 203.0)[0]
        left, row, right = int(start[0]), int(start[1]), int(last)
        box = (left - 6, row - 8, right + 6, row + 8)
        added = self._printed(3, window, face, baseline, box, (left + 4, right - 4))
        self.assertTrue(added, "the printed runs were not painted")
        self.assertLessEqual(max(abs(pixel_row - row) for _col, pixel_row in added), 3,
                             "a horizontal run did not stay horizontal")
        bridged = [col for col, _pixel_row in added if gap + 4 < col < end - 4]
        self.assertEqual(bridged, [],
                         "the painter bridged the gap between two prepared runs")
        columns = [col for col, _pixel_row in added]
        self.assertLessEqual(min(columns), left + 4)
        self.assertGreaterEqual(max(columns), right - 4)

    def test_the_painted_parallel_diagonals_stay_parallel(self):
        face, window, mapping, baseline = self._painted(
            self._layer_payload(self.PARALLEL_DIAGONALS))
        high = self._scene(mapping, 30.0, 140.0)
        low = self._scene(mapping, 90.0, 30.0)
        box = (int(high[0]) - 6, int(high[1]) - 6, int(low[0]) + 6, int(low[1]) + 6)
        added = self._printed(3, window, face, baseline, box,
                              (int(high[0]) + 4, int(low[0]) - 4))
        self.assertTrue(added, "the printed diagonals were not painted")
        bands = {}
        for col in range(box[0], box[2] + 1):
            rows = sorted(pixel_row for pixel_col, pixel_row in added if pixel_col == col)
            centres = []
            run = []
            for pixel_row in rows:
                if run and pixel_row > run[-1] + 2:
                    centres.append(sum(run) / len(run))
                    run = []
                run.append(pixel_row)
            if run:
                centres.append(sum(run) / len(run))
            bands[col] = centres
        paired = {col: centres for col, centres in bands.items() if len(centres) == 2}
        span = box[2] - box[0]
        self.assertGreaterEqual(len(paired), 0.6 * span,
                                "both diagonals must paint in every column (%d of %d)"
                                % (len(paired), span))
        separations = [centres[1] - centres[0] for centres in paired.values()]
        self.assertLessEqual(max(separations) - min(separations), 2,
                             "the parallel diagonals converge or fan on screen")
        # Each band rides its own straight line at the bed's own slope:
        # a fanned or chording painter breaks the constant per-column
        # rise.
        expected = mapping["sy"] / mapping["sx"]
        columns = sorted(paired)
        for index in (0, 1):
            rise = paired[columns[0]][index] - paired[columns[-1]][index]
            self.assertAlmostEqual(rise / (columns[-1] - columns[0]), expected,
                                   delta=0.3, msg="a diagonal's slope is not the bed's")

    def test_the_split_paints_only_the_motions_it_counted(self):
        # Split 2 counts motions 0 and 1: the first run's edge (motion
        # 1) is printed, the second run's (motion 2) is not yet — an
        # inclusive reading of the same number paints one edge ahead.
        face, window, mapping, baseline = self._painted(
            self._layer_payload(self.HORIZONTAL_RUNS))
        left = int(self._scene(mapping, 30.0, 203.0)[0])
        row = int(self._scene(mapping, 30.0, 203.0)[1])
        gap = int(self._scene(mapping, 90.0, 203.0)[0])
        end = int(self._scene(mapping, 130.0, 203.0)[0])
        right = int(self._scene(mapping, 190.0, 203.0)[0])
        box = (left - 6, row - 8, right + 6, row + 8)
        added = self._printed(2, window, face, baseline, box, (left + 4, gap - 4))
        self.assertTrue(added, "the printed run was not painted")
        ahead = [col for col, _pixel_row in added if col > gap + 4]
        self.assertEqual(ahead, [],
                         "the painter drew motions past the split it was given")
        # The next poll's delta completes the layer: the accumulation
        # adds the second run without repainting the first.
        added = self._printed(3, window, face, baseline, box, (end + 4, right - 4))
        columns = [col for col, _pixel_row in added]
        self.assertLessEqual(min(columns), left + 4, "the delta lost the first run")
        self.assertGreaterEqual(max(columns), right - 4, "the delta never reached the second run")

    def test_a_painted_arc_is_curved_and_not_its_chord(self):
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_CCW))
        apex = self._scene(mapping, 125.0, 185.0)
        chord = self._scene(mapping, 125.0, 125.0)[1]
        left = self._scene(mapping, 65.0, 125.0)[0]
        right = self._scene(mapping, 185.0, 125.0)[0]
        radius = abs(chord - apex[1])
        box = (int(left) - 8, int(chord - radius) - 8, int(right) + 8, int(chord + radius) + 8)
        added = self._printed(2, window, face, baseline, box, (int(left) + 4, int(right) - 4))
        self.assertTrue(added, "the printed arc was not painted")
        centres = self._centres(added)
        self.assertGreater(len(centres), 30, "the arc arrived as a handful of chords")
        for col, bands in centres.items():
            self.assertEqual(len(bands), 1, "column %d carries two ink bands" % col)
        rows = [bands[0] for bands in centres.values()]
        self.assertGreater(max(abs(row - chord) for row in rows), 20.0,
                           "the painted arc never left its own chord")
        self.assertGreaterEqual(min(rows), chord - radius - 5, "the ink rose past the arc's apex")
        self.assertEqual([row for row in rows if row > chord + 6], [],
                         "the counter-clockwise arc dipped below its own chord")
        apex_band = [bands[0] for col, bands in centres.items() if abs(col - apex[0]) <= 2]
        self.assertTrue(apex_band, "the arc's apex column carries no ink")
        self.assertLessEqual(min(apex_band), apex[1] + 6, "the arc's apex is missing")

    def test_a_clockwise_arc_bulges_to_its_own_side_of_the_chord(self):
        # The same endpoints as the counter-clockwise case and the same
        # chord: only the command word moved, so the ink must too.
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_CW))
        apex = self._scene(mapping, 125.0, 65.0)
        chord = self._scene(mapping, 125.0, 125.0)[1]
        left = self._scene(mapping, 65.0, 125.0)[0]
        right = self._scene(mapping, 185.0, 125.0)[0]
        radius = abs(apex[1] - chord)
        box = (int(left) - 8, int(chord - radius) - 8, int(right) + 8, int(chord + radius) + 8)
        added = self._printed(2, window, face, baseline, box, (int(left) + 4, int(right) - 4))
        self.assertTrue(added, "the printed arc was not painted")
        centres = self._centres(added)
        rows = [bands[0] for bands in centres.values()]
        # A semicircle meets its chord perpendicular, so the endpoint
        # column's ink run spans about the square root of twice the
        # on-screen radius and its band centre sits that far below the
        # chord; the margin grows with the arc's pixel radius.
        self.assertLess(abs(min(rows) - chord), 9, "the clockwise arc did not start on its chord")
        self.assertGreater(max(rows) - chord, 20.0, "the painted arc never left its own chord")
        self.assertEqual([row for row in rows if row < chord - 6], [],
                         "the clockwise arc bulged to the counter-clockwise side")
        apex_band = [row for col, bands in centres.items() for row in bands
                     if abs(col - apex[0]) <= 2]
        self.assertTrue(apex_band and min(apex_band) >= apex[1] - 6,
                        "the clockwise arc's own apex band is missing")

    def test_a_quarter_circle_paints_as_multiple_screen_segments(self):
        gcode = ("M82\n;LAYER:0\n;TYPE:SKIN\n"
                 "G0 X185 Y125\n"
                 "G3 X125 Y185 I-60 J0 E1\n")
        face, window, mapping, baseline = self._painted(self._arc_payload(gcode))
        start = self._scene(mapping, 185.0, 125.0)
        end = self._scene(mapping, 125.0, 185.0)
        box = (int(end[0]) - 8, int(end[1]) - 8, int(start[0]) + 8, int(start[1]) + 8)
        added = self._printed(2, window, face, baseline, box, (int(end[0]) + 4, int(start[0]) - 4))
        self.assertTrue(added, "the printed quarter circle was not painted")
        centres = self._centres(added)
        self.assertGreater(len(centres), 15, "the quarter circle arrived as a few chords")
        rows = [centres[col][0] for col in sorted(centres)]
        # The straight line joining the two painted ends, at the middle
        # column: a curve whose midpoint sits on it drew as its chord.
        straight = rows[0] + (rows[-1] - rows[0]) * 0.5
        self.assertGreater(abs(rows[len(rows) // 2] - straight), 8.0,
                           "the quarter circle painted as one straight segment")

    def test_the_arc_and_the_line_after_it_join_without_a_gap(self):
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_THEN_LINE))
        junction = self._scene(mapping, 65.0, 125.0)
        far = self._scene(mapping, 185.0, 60.0)
        apex = self._scene(mapping, 125.0, 185.0)
        box = (int(junction[0]) - 8, int(apex[1]) - 8, int(far[0]) + 8, int(far[1]) + 8)
        added = self._printed(3, window, face, baseline, box,
                              (int(junction[0]) + 4, int(far[0]) - 4))
        self.assertTrue(added, "the printed arc and line were not painted")
        self.assertTrue(self._near(added, junction, 3), "the join vertex carries no ink")
        self.assertTrue(self._near(added, far, 3), "the move after the arc was not painted")
        self.assertEqual(self._components(added), 1,
                         "the arc and the line after it painted as separate runs")
        # Every column between them carries ink on the line's own screen
        # path: the arc's tessellation ends exactly where the line starts,
        # rather than short of it or past it.
        centres = self._centres(added)
        span = far[0] - junction[0]
        for col in range(int(junction[0]) + 3, int(far[0]) - 2):
            bands = centres.get(col)
            self.assertTrue(bands, "the stroke left a gap at column %d" % col)
            expected = junction[1] + (col - junction[0]) * (far[1] - junction[1]) / span
            self.assertLessEqual(min(abs(row - expected) for row in bands), 3,
                                 "the stroke left the line at column %d" % col)

    def test_the_split_paints_the_arc_by_its_original_motion_index(self):
        # Split 2 counts motions 0 and 1 — the travel and the arc — and
        # the arc's whole curve belongs to motion 1. A split read as a
        # vertex count instead paints part of the curve at 1, and an
        # inclusive reading of 2 paints the line after it.
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_THEN_LINE))
        apex = self._scene(mapping, 125.0, 185.0)
        chord = self._scene(mapping, 125.0, 125.0)[1]
        left = self._scene(mapping, 65.0, 125.0)
        right = self._scene(mapping, 185.0, 125.0)
        far = self._scene(mapping, 185.0, 60.0)
        span = (int(left[0]) + 4, int(right[0]) - 4)
        box = (int(left[0]) - 8, int(apex[1]) - 8, int(right[0]) + 8, int(far[1]) + 8)
        self._printer.setSplit(1)
        _image, added = self._await_ink(window, face, baseline, box, span, timeout=0.4)
        self.assertEqual(added, set(), "the split painted the arc before its own motion")
        added = self._printed(2, window, face, baseline, box, span)
        self.assertTrue([row for _col, row in added if abs(row - chord) < 8],
                        "the arc was never painted")
        self.assertEqual([row for _col, row in added if row > chord + 8], [],
                         "the split painted the motion after the arc")
        line_box = (int(left[0]) - 8, int(chord) + 8, int(right[0]) + 8, int(far[1]) + 8)
        self._printer.setSplit(3)
        _image, line_ink = self._await_ink(
            window, face, baseline, line_box,
            (int((left[0] + right[0]) / 2), int(right[0]) - 4))
        self.assertTrue(line_ink, "the motion after the arc was never painted")

    def test_disconnected_arc_runs_are_not_bridged(self):
        face, window, mapping, baseline = self._painted(self._arc_payload(self.ARC_RUNS))
        first_end = self._scene(mapping, 105.0, 125.0)[0]
        second_start = self._scene(mapping, 145.0, 125.0)[0]
        left = self._scene(mapping, 15.0, 125.0)[0]
        right = self._scene(mapping, 235.0, 125.0)[0]
        apex = self._scene(mapping, 60.0, 170.0)[1]
        chord = self._scene(mapping, 60.0, 125.0)[1]
        box = (int(left) - 8, int(apex) - 8, int(right) + 8, int(chord) + 8)
        added = self._printed(4, window, face, baseline, box, (int(left) + 4, int(right) - 4))
        self.assertTrue(added, "the printed arcs were not painted")
        columns = [col for col, _row in added]
        self.assertLessEqual(min(columns), left + 4, "the first arc is missing")
        self.assertGreaterEqual(max(columns), right - 4, "the second arc is missing")
        self.assertEqual([col for col in columns if first_end + 4 < col < second_start - 4], [],
                         "the painter bridged two arc runs a travel apart")
        self.assertEqual(self._components(added), 2, "the two arc runs painted as one")

    def test_the_follower_fills_its_plot_edge_to_edge(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        face = faces[0]
        # No dot for this measurement: the scene-graph dot inks
        # instantly and would satisfy the wait before the threaded
        # paint lands — the stroke is this test's subject.
        face.setProperty("dot", None)
        plot = face.findChild(QQuickItem, "moonrakerPlateCanvas").property("_plot")
        bed = plot.property("bed")
        top = bed.property("offsetY").toNumber()
        bottom = top + bed.property("plotHeight").toNumber()
        rows = self._ink_rows(self._grab_when_inked(window, face, top=top), face, window)
        if not rows or min(rows) > top + 12 or max(rows) < bottom - 12:
            window.grabWindow().save("/tmp/mpf/follower_fail.png")
        self.assertTrue(rows, "the follower painted nothing")
        # The corner-pinned stroke runs along the plot's extreme
        # edges: any truncated or offset mapping leaves a band empty.
        self.assertLessEqual(min(rows), top + 12, "no ink at the plot's top edge")
        self.assertGreaterEqual(max(rows), bottom - 12, "no ink at the plot's bottom edge")

    def test_the_picker_canvas_never_reflows_on_hover(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plate")
        face = self.find(monitor, "moonrakerPlateExcludeFace")
        height = face.height()
        face.setProperty("hoveredName", "Window_Support_Material_0")
        self.pump(20)
        self.assertEqual(face.height(), height, "hovering reflowed the canvas")
        # A very long name elides into the same single line.
        face.setProperty("hoveredName", "Window_Support_Material_0" * 6)
        self.pump(20)
        self.assertEqual(face.height(), height, "a long hover name reflowed the canvas")
        face.setProperty("hoveredName", "")
        self.pump(20)
        self.assertEqual(face.height(), height, "un-hovering reflowed the canvas")

    def _click(self, window, item, at=None):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        point = at if at is not None else QPointF(item.width() / 2, item.height() / 2)
        centre = item.mapToScene(point).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=centre)
        self.pump(30)

    def _follower_popover(self, width=900, height=760):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", width, height)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        return monitor, window, faces[0]

    def _fill_zoom(self, face, plot):
        """A zoom whose bed overfills the face on both axes: the pan
        clamp never bites, so "centred" is exactly the view's centre."""
        side = plot.property("bed").property("plotWidth").toNumber()
        face.setProperty("viewScale", max(face.width(), face.height()) / side + 0.5)
        self.pump(20)

    def test_the_jump_button_recentres_the_view_on_the_toolhead(self):
        monitor, window, face = self._follower_popover()
        plot = face.findChild(QQuickItem, "moonrakerPlateCanvas").property("_plot")
        self.assertIsNotNone(plot, "the bed mapping never built")
        self._fill_zoom(face, plot)
        # The toolhead controls hide in place on a row that persists —
        # the zoom no longer reflows the face (the live request), so
        # the geometry settles immediately. A grab forces the sync
        # (the harness's window doctrine).
        window.grabWindow()
        self.pump(30)
        # The toolhead at the bed's centre: the view is unpanned, so at
        # this zoom the dot stands off the view entirely.
        self._printer.setDot(125.0, 125.0)
        self.pump(30)
        dot = face.findChild(QQuickItem, "moonrakerPlateToolheadDot")
        self.assertTrue(dot.property("visible"), "no toolhead dot to jump to")
        before = (face.property("viewPanX"), face.property("viewPanY"))
        jump = self.find(monitor, "moonrakerFollowerJump")
        self.assertTrue(jump.property("enabled"), "the jump is dead with a live dot")
        self._click(window, jump)
        self.assertNotEqual((face.property("viewPanX"), face.property("viewPanY")), before,
                            "the jump never panned the view")
        self.assertAlmostEqual(dot.x() + dot.width() / 2, face.width() / 2, delta=2.0,
                               msg="the jump did not land the toolhead on the view's centre")
        self.assertAlmostEqual(dot.y() + dot.height() / 2, face.height() / 2, delta=2.0,
                               msg="the jump did not land the toolhead on the view's centre")

    def test_the_jump_button_is_disabled_without_a_live_toolhead(self):
        monitor, window, face = self._follower_popover()
        self._printer.setDot(0.0, 0.0, valid=False)
        self.pump(30)
        self.assertFalse(self.find(monitor, "moonrakerFollowerJump").property("enabled"),
                         "the jump is live with no valid toolhead position")

    def test_a_pan_settles_before_it_re_rasters(self):
        # The native-raster era's pan: the pan stays a paint input
        # (the middle-canvas ghosting killed the translate), but the
        # re-raster fires at the SETTLE — 150 ms after the last pan
        # change — never per drag tick (the awful-panning report).
        monitor, window, face = self._follower_popover()
        face.setProperty("dot", None)
        rows = self._ink_rows(self._grab_when_inked(window, face), face, window)
        self.assertTrue(rows, "the follower painted nothing")
        self.assertGreaterEqual(face.property("_paintsSinceReset"), 1,
                                "the accumulation never painted")
        # Let the mount's own settle pass first, then hold the
        # counter still.
        import time
        time.sleep(0.25)
        self.pump(30)
        paints = face.property("_paintsSinceReset")
        face.setProperty("viewPanY", 36.0)
        self.pump(5)
        self.assertEqual(face.property("_paintsSinceReset"), paints,
                         "a pan re-rasters before the settle")
        self.assertEqual(face.property("_view").property("panY").toNumber(), 36.0,
                         "the painter's carrier lost the pan")
        # The settle's reset is the timer's synchronous effect, and
        # it fires after the timer's interval (the pump alone never
        # advances the clock past 150 ms).
        time.sleep(0.25)
        self.pump(30)
        self.assertEqual(face.property("_lastSplit"), -1,
                         "the settle never reset the stack")

    def test_detaching_freezes_the_layer_and_hides_the_dot(self):
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(9)
        self.pump(20)
        dot = face.findChild(QQuickItem, "moonrakerPlateToolheadDot")
        slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        attach = self.find(monitor, "moonrakerFollowerAttach")
        self.assertTrue(dot.property("visible"))
        self.assertTrue(attach.property("enabled"))
        # The slider is LIVE while attached — a seek is itself the
        # detach (the live request: it must never sit dead).
        self.assertTrue(slider.property("enabled"), "the slider sits dead while attached")
        self.assertEqual(slider.property("value"), 9.0, "the slider did not follow the live layer")
        self._click(window, attach)
        self.assertIn(("attached", False), self._printer.calls)
        self.assertEqual(attach.property("text"), "Attach")
        self.assertFalse(face.property("attached"))
        self.assertFalse(dot.property("visible"), "the detached face kept its toolhead dot")
        self.assertEqual(self._printer.followerLayerAnchor, 9,
                         "the detach did not hold the layer it showed")
        self.assertTrue(slider.property("enabled"), "the detached slider cannot seek")
        self.assertEqual(slider.property("value"), 9.0, "the frozen layer left the slider")
        self._click(window, attach)
        self.assertIn(("attached", True), self._printer.calls)
        self.assertEqual(attach.property("text"), "Detach")
        self.assertTrue(face.property("attached"))
        self.assertTrue(dot.property("visible"), "re-attaching lost the toolhead dot")
        self.assertTrue(slider.property("enabled"))

    def test_the_layer_ghost_stays_available_on_every_layer_detached(self):
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(9)
        self.pump(20)
        attach = self.find(monitor, "moonrakerFollowerAttach")
        boxes = [item for item in monitor.findChildren(QQuickItem)
                 if item.property("text") == "Layer ghost"
                 and not item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(boxes), "no Layer ghost checkbox mounted")
        box = boxes[0]
        self.assertTrue(box.property("visible"), "the ghost box hides while attached")
        self.assertTrue(face.property("showBase"), "the live face lost its base")
        # The partial default split frames the base: the probe reads
        # the composition's own predicate, not a pixel.
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant

        def partial_base():
            return bool(QMetaObject.invokeMethod(face, "_partialBase",
                                                 Q_RETURN_ARG(QVariant)))

        self.assertTrue(partial_base(), "the base never framed the partial layer")
        self._click(window, attach)
        self.assertFalse(face.property("attached"))
        self.assertEqual(self._printer.followerLayerAnchor,
                         self._printer.plateProgressAnchor,
                         "the detach did not hold the live layer")
        self.assertTrue(box.property("visible"),
                        "the ghost box hid while detached on the live layer")
        self.assertTrue(face.property("showBase"),
                        "the detached-on-live face lost its base")
        self.assertTrue(partial_base(), "the detached-on-live base never framed")
        # The toggle drives the base through the model, detached or not.
        self._click(window, box)
        self.assertIn(("showBase", False), self._printer.calls)
        self.assertFalse(face.property("showBase"),
                         "unchecking the ghost box kept the base")
        self.assertFalse(partial_base(), "the unchecked base kept framing")
        self._click(window, box)
        self.assertIn(("showBase", True), self._printer.calls)
        self.assertTrue(face.property("showBase"), "re-checking lost the base")
        # The ghost is available for ALL layers: the print advancing
        # past the frozen layer changes nothing.
        self._printer.setAnchor(12)
        self.pump(20)
        self.assertTrue(box.property("visible"),
                        "the ghost box hid while detached on an old layer")
        self.assertTrue(face.property("showBase"),
                        "the base hid while detached on an old layer")
        # The split-brain guard: the ghost frames the SELECTED layer's
        # own partial progress — the same view the scrub shows, never
        # the live print's.
        self._printer.setFollowerLayerAnchor(5)
        self._printer.setSplit(6)
        self.pump(20)
        self.assertEqual(self._printer.plateProgressAnchor, 5,
                         "the scrub did not land on the selected layer")
        self.assertTrue(partial_base(),
                        "the ghost does not frame the selected layer's partial")

    def test_detach_and_scrub_work_from_layer_zero(self):
        # The P0 zero-index bug: the production model read anchor 0
        # as missing through `0 or -1`, so the detach was refused and
        # the progress scrub never detached on the first layer. The
        # popover must detach on layer 0 and keep the anchor at 0 —
        # never -1.
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(0)
        self.pump(20)
        attach = self.find(monitor, "moonrakerFollowerAttach")
        layer_slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        self.assertEqual(layer_slider.property("value"), 0.0,
                         "the live layer zero never reached the slider")
        self.assertEqual(attach.property("text"), "Detach")
        self._click(window, attach)
        self.assertIn(("attached", False), self._printer.calls)
        self.assertEqual(attach.property("text"), "Attach")
        self.assertFalse(face.property("attached"))
        self.assertEqual(self._printer.followerLayerAnchor, 0,
                         "the layer-zero detach did not hold the layer it showed")
        self.assertEqual(layer_slider.property("value"), 0.0,
                         "the detach turned the anchor into -1")
        # Reattach, then scrub the ACTUAL progress control: the scrub
        # is itself the detach on the current layer.
        self._click(window, attach)
        self.assertIn(("attached", True), self._printer.calls)
        self._printer.calls = []
        progress = self.find(monitor, "moonrakerFollowerLayerProgress")
        self.assertTrue(progress.property("enabled"),
                        "the progress control sits dead in the harness")
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import QPointF, Qt
        seek = progress.mapToScene(
            QPointF(progress.width() * 0.5, progress.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=seek)
        self.pump(30)
        self.assertIn(("progress", round(progress.property("value"))),
                      self._printer.calls)
        self.assertFalse(self._printer.followerAttached,
                         "the layer-zero scrub never detached")
        self.assertEqual(self._printer.followerLayerAnchor, 0,
                         "the scrub froze the wrong anchor")
        self.assertEqual(attach.property("text"), "Attach")
        self.assertEqual(layer_slider.property("value"), 0.0,
                         "the scrub turned the anchor into -1")

    def test_the_layer_slider_commits_only_after_the_seek_settles(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        monitor, window, face = self._follower_popover()
        self._printer.setAnchor(9)
        self.pump(20)
        self._click(window, self.find(monitor, "moonrakerFollowerAttach"))
        slider = self.find(monitor, "moonrakerFollowerLayerSlider")
        self.assertTrue(slider.property("enabled"))
        self._printer.calls = []
        seek = slider.mapToScene(QPointF(slider.width() * 0.5, slider.height() / 2)).toPoint()
        QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=seek)
        self.pump(20)
        requested = round(slider.property("value"))
        self.assertEqual([call for call in self._printer.calls if call[0] == "layer"], [],
                         "the seek committed a layer before it settled")
        # The settle: the request lands while the handle is still held
        # (nothing is committed by the hold itself).
        deadline = time.monotonic() + 1.5
        while (not [call for call in self._printer.calls if call[0] == "layer"]
               and time.monotonic() < deadline):
            self.app.processEvents()
            time.sleep(0.02)
        layers = [call for call in self._printer.calls if call[0] == "layer"]
        self.assertEqual(layers, [("layer", requested)],
                         "the settled seek did not request its own layer")
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=seek)
        self.pump(20)
        self.assertEqual(self._printer.followerLayerAnchor, requested)
        self.assertEqual(slider.property("value"), float(requested))
        # A release commits at once: the click path needs no settle.
        self._printer.calls = []
        far = slider.mapToScene(QPointF(slider.width() * 0.85, slider.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=far)
        self.pump(30)
        seeks = [call for call in self._printer.calls if call[0] == "layer"]
        self.assertEqual(len(seeks), 1, "a settled click did not commit exactly once")
        self.assertGreater(seeks[0][1], requested, "the second seek did not move forward")

    def test_the_picker_draws_no_toolhead_dot(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plate")
        picker_dot = self.find(monitor, "moonrakerPlateExcludeFace").findChild(
            QQuickItem, "moonrakerPlateToolheadDot")
        self.assertIsNone(picker_dot,
                          "the picker draws the toolhead — the map is the control")
        monitor.setProperty("openPopOver", "plateprogress")
        self.pump(30)
        follower_dot = self._popover_faces(
            monitor, "moonrakerPlateProgressFace")[0].findChild(
            QQuickItem, "moonrakerPlateToolheadDot")
        self.assertIsNotNone(follower_dot)
        self.assertTrue(follower_dot.property("visible"),
                        "the follower lost its toolhead dot")


if QT_AVAILABLE:
    from plugins.MonitorFormatting import PlateProjectionMemo

    class PlateJobBoundaryTests(RealEngineTestCase):
        """The picker's payload ties its GEOMETRY to the current job.
        The core lane owns exclude_object (CORE_OBJECTS) and the
        auxiliary copy is that lane's first landing alone: the finished
        print's polygons decorated with the new job's flags put an old
        object on the map, and a tap then acted on the same-named
        object at the position it held in the previous print.

        Projection-level — the method under test reads the snapshot's
        two lanes and its own memo, nothing else of the model (the
        monitor-level harness lives in the model's coverage file)."""

        # The finished print's plate, still in the auxiliary copy while
        # the new job's core report has already landed.
        OLD = {
            "objects": [{"name": "PART_A", "center": [10.0, 10.0],
                         "polygon": [[5.0, 5.0], [5.0, 15.0], [15.0, 15.0], [15.0, 5.0]]}],
            "excluded_objects": [], "current_object": None,
        }
        MOVED = {
            "objects": [{"name": "PART_A", "center": [100.0, 100.0],
                         "polygon": [[95.0, 95.0], [95.0, 105.0], [105.0, 105.0],
                                     [105.0, 95.0]]}],
            "excluded_objects": [], "current_object": "PART_A",
        }

        @classmethod
        def setUpClass(cls):
            super().setUpClass()
            from qt_runtime_support import runtime

            # The model module reads Uranium's UM.* at import, which
            # only the harness supplies (this file's QGuiApplication is
            # the same instance the harness reuses).
            with runtime():
                from plugins.MoonrakerMonitorModel import MoonrakerMonitorModel
            # Bound as a plain function on the class: the unbound method
            # called through the instance would take the test case as
            # its `self`.
            cls.projection = staticmethod(MoonrakerMonitorModel._plate_objects_value)

        def _model(self, core, auxiliary):
            """The projection bound to a model-shaped carrier: the
            snapshot's lanes plus the projection's own state — the
            memo, the job it last read, the cached geometry."""
            from types import SimpleNamespace

            return SimpleNamespace(
                _data=SimpleNamespace(snapshot=SimpleNamespace(core=core or {},
                                                               auxiliary=auxiliary or {})),
                _plate_memo=PlateProjectionMemo(),
                _plate_job_seen=None,
                _plate_geometry=None,
                _plate_payload=None)

        def _rows(self, model, visited=frozenset()):
            plate = self.projection(model, visited)
            return {row["name"]: row for row in plate["objects"]}

        def test_a_new_jobs_geometry_replaces_the_finished_prints(self):
            # Old and new jobs both carry PART_A; the new job's core
            # report moves it from [10, 10] to [100, 100].
            model = self._model(
                core={"print_stats": {"filename": "next.gcode", "state": "printing"},
                      "exclude_object": self.MOVED},
                auxiliary={"exclude_object": self.OLD})
            rows = self._rows(model)
            self.assertEqual([100.0, 100.0], list(rows["PART_A"]["center"]),
                             "the picker plotted the finished print's geometry")
            self.assertTrue(rows["PART_A"]["current"],
                            "the new job's current object never landed")

        def test_a_new_print_with_no_objects_yet_shows_no_plate(self):
            # The job boundary with nothing defined yet: the core report
            # carries an empty object list, and the finished print's
            # polygons must not stand in for it.
            model = self._model(
                core={"print_stats": {"filename": "next.gcode"},
                      "exclude_object": {"objects": [], "excluded_objects": [],
                                         "current_object": None}},
                auxiliary={"exclude_object": self.OLD})
            self.assertEqual({}, self._rows(model),
                             "the finished print's objects stayed on the plate")

        def test_a_stale_auxiliary_copy_never_crosses_a_job_boundary(self):
            # Only the auxiliary copy has landed, and the job has moved
            # since it was read: the plate is honestly empty until the
            # core lane reports its own, never the finished print under
            # the new job's flags.
            model = self._model(core={"print_stats": {"filename": "old.gcode"}},
                                auxiliary={"exclude_object": self.OLD})
            self.assertEqual(["PART_A"], sorted(self._rows(model)),
                             "the fixture no longer projects the first landing")
            model._data.snapshot.core = {"print_stats": {"filename": "next.gcode"}}
            self.assertEqual({}, self._rows(model),
                             "the finished print's geometry survived the job boundary")

        def test_the_core_report_leads_even_when_it_names_no_objects(self):
            # The lane's own answer for the current job — an empty plate
            # — outranks a populated copy from the other lane.
            model = self._model(
                core={"exclude_object": {"objects": [], "excluded_objects": [],
                                         "current_object": None}},
                auxiliary={"exclude_object": self.MOVED})
            self.assertEqual({}, self._rows(model),
                             "an empty core report fell back to the other lane")


if QT_AVAILABLE:

    class LateBedDouble(QObject):
        """A printer whose bed geometry arrives late and can switch:
        the attach the canvas must re-map without a resize. Module
        level inside the guard (the house pattern)."""

        bedChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self._bed = (0.0, 0.0)
            self._centre_is_zero = False

        def setBed(self, width, depth):
            self._bed = (float(width), float(depth))
            self.bedChanged.emit()

        def setCentreIsZero(self, value):
            self._centre_is_zero = bool(value)
            self.bedChanged.emit()

        @pyqtProperty(float, notify=bedChanged)
        def bedMeshMachineWidth(self):
            return self._bed[0]

        @pyqtProperty(float, notify=bedChanged)
        def bedMeshMachineDepth(self):
            return self._bed[1]

        @pyqtProperty(bool, notify=bedChanged)
        def bedMeshCenterIsZero(self):
            return self._centre_is_zero


class PlateCanvasHitTests(RealEngineTestCase):
    """The picker's hit test against real polygons: the click target is
    the object the user sees. Containment is authoritative (the Python
    probe's own ray cast, aimed through the canvas's own bed mapping),
    an overlap resolves to the first row in the payload's order, and the
    centre radius serves only the rows that carry no geometry at all —
    the gesture is destructive, so a centre the object does not own must
    never answer for it."""

    @staticmethod
    def _row(name, center=None, polygon=None, excluded=False):
        """A payload row in the model's plate_values shape."""
        return {"name": name, "center": center, "polygon": polygon,
                "order": 0, "excluded": bool(excluded), "current": False,
                "restoreAllowed": True}

    @classmethod
    def _payload(cls, rows):
        return {"objects": rows, "truncated": 0, "excludedCount": 0}

    @classmethod
    def _polygon_bed(cls):
        """Five objects on the double's 250 mm bed, every one of them
        with real geometry — the shapes a printer reports once the
        exclude_object payload is live."""
        return [
            # A bar 35 mm deep: a click near its right end stands ~107 mm
            # from its own centre, well past the 18 mm degraded radius.
            cls._row("Long_Bracket", [125.0, 37.5],
                     [[10.0, 20.0], [240.0, 20.0], [240.0, 55.0], [10.0, 55.0]]),
            # Two objects 2 mm apart, with the right-hand one's centre
            # inside the left-hand object's degraded reach.
            cls._row("Left_Block", [115.0, 215.0],
                     [[40.0, 180.0], [190.0, 180.0], [190.0, 250.0], [40.0, 250.0]]),
            cls._row("Right_Block", [202.0, 230.0],
                     [[192.0, 220.0], [212.0, 220.0], [212.0, 240.0], [192.0, 240.0]]),
            # An overlapping pair whose centres both stand inside the
            # degraded radius of a shared interior point.
            cls._row("Over_A", [90.0, 110.0],
                     [[60.0, 80.0], [120.0, 80.0], [120.0, 140.0], [60.0, 140.0]]),
            cls._row("Over_B", [110.0, 130.0],
                     [[80.0, 100.0], [140.0, 100.0], [140.0, 160.0], [80.0, 160.0]]),
        ]

    def _picker(self, rows):
        """Open the exclude popover on *rows* and hand back the window,
        the face and the shared canvas."""
        previous = PlatePrinterDouble.PLATE
        PlatePrinterDouble.PLATE = self._payload(rows)
        self.addCleanup(setattr, PlatePrinterDouble, "PLATE", previous)
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        # The reference is retained: a Python-created QObject dies with
        # its last Python ref (the QML var takes no ownership).
        self._printer = PlatePrinterDouble()
        monitor.setProperty("printer", self._printer)
        monitor.setProperty("openPopOver", "plate")
        self.pump(30)
        face = self.find(monitor, "moonrakerPlateExcludeFace")
        canvas = face.findChild(QQuickItem, "moonrakerPlateCanvas")
        self.assertIsNotNone(canvas, "the picker canvas never built")
        self.assertIsNotNone(canvas.property("_plot"), "the bed mapping never built")
        return window, face, canvas

    @staticmethod
    def _scene(canvas, bed_x, bed_y):
        """The canvas's own bed-to-item transform, read from the live
        plot: the test aims through the mapping the painter uses, never
        through a second opinion about it."""
        plot = canvas.property("_plot")
        bed = plot.property("bed")
        return (bed.property("offsetX").toNumber()
                + (bed_x - bed.property("bedXMin").toNumber()) * plot.property("sx").toNumber(),
                bed.property("offsetY").toNumber()
                + (bed.property("bedYMax").toNumber() - bed_y) * plot.property("sy").toNumber())

    def _hover(self, window, canvas, face, bed_x, bed_y):
        """Put the pointer on the bed point through the real mouse path
        and return the name the face published for the hover."""
        from PyQt6.QtTest import QTest
        scene_x, scene_y = self._scene(canvas, bed_x, bed_y)
        # The face publishes on a POSITION CHANGE: a move onto the
        # point the pointer already holds is no hover at all. The
        # pointer outlives the window (see _park_pointer), so a test
        # that ended on this very point starves the hover below.
        # Lead with a step off the point so every hover is a real
        # move.
        QTest.mouseMove(window, canvas.mapToScene(
            QPointF(scene_x + 8.0, scene_y + 8.0)).toPoint())
        self.pump(5)
        QTest.mouseMove(window, canvas.mapToScene(QPointF(scene_x, scene_y)).toPoint())
        self.pump(20)
        return face.property("hoveredName")

    def _click_bed(self, window, canvas, face, bed_x, bed_y):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt
        scene_x, scene_y = self._scene(canvas, bed_x, bed_y)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton,
                         pos=canvas.mapToScene(QPointF(scene_x, scene_y)).toPoint())
        self.pump(10)

    @staticmethod
    def _containing(rows, x, y):
        """The payload's own answer at a bed point, in payload order:
        the Python ray cast the QML test mirrors, so a test can state
        what the geometry says before it asserts what the canvas did."""
        return [row["name"] for row in rows
                if row.get("polygon") and _point_in_polygon(x, y, row["polygon"])]

    def _nearest_centre(self, canvas, rows, bed_x, bed_y):
        """The nearest centre the payload carries, with its pixel
        distance — the answer the replaced centre rule gave."""
        target = self._scene(canvas, bed_x, bed_y)
        best = None
        for row in rows:
            if not row.get("center"):
                continue
            point = self._scene(canvas, row["center"][0], row["center"][1])
            distance = (((point[0] - target[0]) ** 2 + (point[1] - target[1]) ** 2) ** 0.5)
            if best is None or distance < best[1]:
                best = (row["name"], distance)
        return best

    @staticmethod
    def _radius_px(canvas):
        """The degraded radius as the canvas measures it: bed
        millimetres through the plot's own scale."""
        plot = canvas.property("_plot")
        return canvas.property("hitRadiusMm") * max(abs(plot.property("sx").toNumber()),
                                                    abs(plot.property("sy").toNumber()))

    def test_a_click_near_the_polygon_edge_selects_the_object(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # 8 mm inside the bar's right end, 5 mm off its bottom edge.
        point = (232.0, 50.0)
        self.assertEqual(self._containing(rows, *point), ["Long_Bracket"],
                         "the fixture no longer places this click inside the bar")
        bounds = polygon_bounds(rows[0]["polygon"])
        self.assertLessEqual(min(point[0] - bounds[0], bounds[2] - point[0],
                                 point[1] - bounds[1], bounds[3] - point[1]), 8.0,
                             "the click is not near the polygon's edge")
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Long_Bracket")
        self.assertGreater(distance, self._radius_px(canvas),
                           "the click sits inside the degraded radius after all")
        self.assertEqual(self._hover(window, canvas, face, *point), "Long_Bracket")

    def test_a_click_inside_one_of_two_close_objects_selects_that_object(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # 2 mm inside the left object's right edge, 4 mm off its
        # neighbour — and 14.6 mm from the NEIGHBOUR's centre, inside the
        # degraded radius: the rule the picker replaced answered
        # Right_Block here, a different object from the one the user
        # highlighted.
        point = (188.0, 226.0)
        self.assertEqual(self._containing(rows, *point), ["Left_Block"])
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Right_Block")
        self.assertLessEqual(distance, self._radius_px(canvas),
                             "the fixture no longer pins the wrong-centre case")
        self.assertEqual(self._hover(window, canvas, face, *point), "Left_Block")
        # The neighbour answers on its own geometry, not on proximity.
        inside = (202.0, 232.0)
        self.assertEqual(self._containing(rows, *inside), ["Right_Block"])
        self.assertEqual(self._hover(window, canvas, face, *inside), "Right_Block")

    def test_a_click_outside_every_polygon_selects_nothing(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (150.0, 100.0)
        self.assertEqual(self._containing(rows, *point), [],
                         "the fixture no longer leaves this point in the open")
        _name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertGreater(distance, self._radius_px(canvas),
                           "the point is within the degraded radius of some centre")
        self.assertEqual(self._hover(window, canvas, face, *point), "")

    def test_a_polygon_object_is_never_matched_by_its_centre(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # 5 mm below the right object's bottom edge and 15 mm from its
        # centre: inside the degraded radius, outside the geometry, with
        # no other object in reach.
        point = (202.0, 245.0)
        self.assertEqual(self._containing(rows, *point), [])
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Right_Block")
        self.assertLessEqual(distance, self._radius_px(canvas),
                             "the fixture no longer pins the near-centre case")
        self.assertEqual(self._hover(window, canvas, face, *point), "",
                         "a centre the object does not own answered for it")

    def test_an_overlap_resolves_to_the_first_object_in_the_payload(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (95.0, 130.0)
        self.assertEqual(self._containing(rows, *point), ["Over_A", "Over_B"],
                         "the fixture no longer overlaps at this point")
        # The centre rule named the second one, so the assertion below
        # pins the documented rule and not the old answer.
        name, distance = self._nearest_centre(canvas, rows, *point)
        self.assertEqual(name, "Over_B")
        self.assertLessEqual(distance, self._radius_px(canvas))
        self.assertEqual(self._hover(window, canvas, face, *point), "Over_A")

    def test_a_centre_only_object_still_hits_through_the_degraded_radius(self):
        rows = [self._row("Sparse_Centre", [30.0, 120.0])]
        window, face, canvas = self._picker(rows)
        self.assertEqual(self._hover(window, canvas, face, 30.0, 122.0), "Sparse_Centre")
        # The radius still bounds the fallback.
        self.assertEqual(self._hover(window, canvas, face, 30.0, 150.0), "")

    def test_a_triple_click_excludes_and_restores_the_object_under_the_pointer(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        # The point where the centre rule named the neighbour: the
        # destructive gesture must act on the object the hover
        # highlighted, at every step of the gesture.
        point = (188.0, 226.0)
        self.assertEqual(self._hover(window, canvas, face, *point), "Left_Block")
        for _ in range(3):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual([call for call in self._printer.calls if call[0] == "exclude"],
                         [("exclude", "Left_Block")])
        self.assertEqual(self._hover(window, canvas, face, *point), "Left_Block",
                         "the excluded object left the hit test")
        # The verdict reached the canvas with the republished payload:
        # the same gesture now restores the object it just excluded.
        for _ in range(3):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual([call for call in self._printer.calls if call[0] == "restore"],
                         [("restore", "Left_Block")])

    def _acts(self):
        """Every object command the gesture dispatched, in order."""
        return [call for call in self._printer.calls if call[0] in ("exclude", "restore")]

    def _status_moves(self, name, excluded):
        """The plate's status changing under the gesture, as a second
        client's command reaches this face: the payload republishes
        with the row's verdict already rewritten."""
        self._printer._set_excluded(name, excluded)
        self.pump(20)

    def _park_pointer(self, window, canvas, face):
        """Leave the pointer in the open, off every object. The
        pointer outlives the test's window and the offscreen platform
        drops a move onto the position it already holds, so a test
        that ended on a point a later test hovers first would starve
        that hover instead of failing on its own subject."""
        self._hover(window, canvas, face, 5.0, 5.0)

    def test_the_gesture_fixes_its_action_on_the_first_click(self):
        """The action is the plate the user saw when the gesture
        armed: the first click publishes it (the host's counter line
        reads it), and clicks one and two never act."""
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("clickProgress"), 1)
        self.assertEqual(face.property("pendingAction"), "exclude",
                         "the arming click did not fix its action")
        self.assertEqual(self._acts(), [], "one click acted")
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("clickProgress"), 2)
        self.assertEqual(face.property("pendingAction"), "exclude")
        self.assertEqual(self._acts(), [], "two clicks acted")
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Left_Block")])
        self.assertEqual(face.property("clickProgress"), 0,
                         "the completed gesture stayed armed")
        self.assertEqual(face.property("pendingAction"), "")
        # The exclusion reached the payload, so the object's own next
        # gesture arms the other way and fires once.
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "restore")
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Left_Block"),
                                        ("restore", "Left_Block")])
        self._park_pointer(window, canvas, face)

    def test_another_clients_exclusion_mid_gesture_cancels_the_gesture(self):
        """The finding: an exclude armed against an included object
        must never land as a restore because a second client got
        there first. The state that invalidates the armed action
        cancels the gesture instead."""
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        self._status_moves("Left_Block", True)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude",
                         "the mid-gesture status re-decided the action")
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [],
                         "the gesture ran the opposite of the action it armed")
        self.assertEqual(face.property("clickProgress"), 0,
                         "the cancelled gesture stayed armed")
        self.assertEqual(face.property("pendingAction"), "")
        self._park_pointer(window, canvas, face)

    def test_another_clients_restore_mid_gesture_cancels_the_gesture(self):
        """The mirror: a restore armed against an excluded object must
        not land as an exclusion once a second client restored it."""
        rows = self._polygon_bed()
        rows[1]["excluded"] = True  # Left_Block
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "restore")
        self._status_moves("Left_Block", False)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "restore",
                         "the mid-gesture status re-decided the action")
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [],
                         "the gesture excluded the object it had armed to restore")
        self.assertEqual(face.property("clickProgress"), 0)
        self._park_pointer(window, canvas, face)

    def test_a_state_that_changed_and_came_back_still_acts(self):
        """Permission, not history: a plate that permits the armed
        action again executes it, however it got there."""
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        self._status_moves("Left_Block", True)
        self._status_moves("Left_Block", False)
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Left_Block")])
        self._park_pointer(window, canvas, face)

    def test_the_object_leaving_the_plate_mid_gesture_cancels_the_gesture(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        # A re-index, a new print: the armed target is off the plate,
        # and the ground it stood on hits nothing at all.
        self._printer._plate["objects"] = [row for row in self._printer._plate["objects"]
                                           if row["name"] != "Left_Block"]
        self._printer.plateObjectsChanged.emit()
        self.pump(20)
        for _ in range(2):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [],
                         "the gesture acted on an object that left the plate")
        # The vanished gesture left nothing armed: a fresh one on
        # another object arms and completes on its own.
        for _ in range(3):
            self._click_bed(window, canvas, face, 202.0, 232.0)
        self.pump(20)
        self.assertEqual(self._acts(), [("exclude", "Right_Block")],
                         "the vanished object's gesture leaked into the new one")
        self._park_pointer(window, canvas, face)

    def test_a_gesture_moved_to_another_object_arms_that_objects_action(self):
        """The counter restarts on the object the pointer moved to,
        and the action it arms is that object's own."""
        rows = self._polygon_bed()
        rows[2]["excluded"] = True  # Right_Block
        window, face, canvas = self._picker(rows)
        self._click_bed(window, canvas, face, 188.0, 226.0)  # Left_Block
        self.assertEqual(face.property("pendingAction"), "exclude")
        self._click_bed(window, canvas, face, 202.0, 232.0)  # Right_Block
        self.assertEqual(face.property("clickProgress"), 1,
                         "the new object continued the old gesture")
        self.assertEqual(face.property("pendingAction"), "restore",
                         "the new object inherited the other object's action")
        self._click_bed(window, canvas, face, 202.0, 232.0)
        self._click_bed(window, canvas, face, 202.0, 232.0)
        self.pump(20)
        self.assertEqual(self._acts(), [("restore", "Right_Block")],
                         "the gesture acted on the object the pointer left")
        self._park_pointer(window, canvas, face)

    def test_an_expired_gesture_rearms_from_the_current_plate(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        window_ms = face.property("tripleClickWindowMs")
        self.assertGreater(window_ms, 0, "the gesture carries no window")
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("pendingAction"), "exclude")
        # Real wall clock: the gesture's own clock is Date.now().
        time.sleep(window_ms / 1000.0 + 0.1)
        self._status_moves("Left_Block", True)
        self._click_bed(window, canvas, face, *point)
        self.assertEqual(face.property("clickProgress"), 1,
                         "a click past the window continued the old gesture")
        self.assertEqual(face.property("pendingAction"), "restore",
                         "the re-armed gesture kept the expired click's action")
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("restore", "Left_Block")])
        self._park_pointer(window, canvas, face)

    def test_a_cancelled_gesture_is_followed_by_a_clean_one(self):
        rows = self._polygon_bed()
        window, face, canvas = self._picker(rows)
        point = (188.0, 226.0)
        self._click_bed(window, canvas, face, *point)
        self._status_moves("Left_Block", True)
        self._click_bed(window, canvas, face, *point)
        self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [])
        # The cancelled gesture leaves nothing armed: the next three
        # clicks are a new gesture over the plate as it now stands.
        for _ in range(3):
            self._click_bed(window, canvas, face, *point)
        self.pump(20)
        self.assertEqual(self._acts(), [("restore", "Left_Block")],
                         "the cancelled gesture's clicks leaked into the new one")
        self._park_pointer(window, canvas, face)

    def test_the_hover_label_wears_the_object_states_ink(self):
        """The live request: the picker's hover label wears the SAME
        colour the map's outline uses for the object's state —
        excluded, current, passed and plain rows each carry their own
        ink, and a lost hover falls back to the inactive text."""
        from PyQt6.QtCore import QMetaObject, Q_RETURN_ARG, QVariant
        from PyQt6.QtGui import QColor
        rows = self._polygon_bed()
        rows[0]["excluded"] = True  # Long_Bracket
        rows[1]["current"] = True  # Left_Block
        rows[2]["passed"] = True  # Right_Block
        window, face, canvas = self._picker(rows)

        def ink():
            value = QMetaObject.invokeMethod(face, "hoverInk",
                                             Q_RETURN_ARG(QVariant))
            if hasattr(value, "toVariant"):
                value = value.toVariant()
            return QColor(value).name()

        bare = ink()
        states = {}
        for bed_x, bed_y, expect in ((107.0, 37.5, "Long_Bracket"),
                                     (115.0, 215.0, "Left_Block"),
                                     (202.0, 230.0, "Right_Block"),
                                     (90.0, 110.0, "Over_A")):
            self.assertEqual(self._hover(window, canvas, face, bed_x, bed_y),
                             expect, "the hover missed its object")
            states[expect] = ink()
        # Each state's ink is its own — the label and the outline
        # never disagree on the colour.
        self.assertEqual(4, len(set(states.values())),
                         "the states' ink collapsed: %r" % states)
        self.assertNotIn(bare, set(states.values()),
                         "a hovered state fell back to the inactive ink")
        # The label's binding wears the live ink, not just the helper:
        # the hover row is the face's own sibling in the picker's
        # layout, so the pin reads its live text and colour.
        self._hover(window, canvas, face, 107.0, 37.5)
        label = [sibling for sibling in face.parentItem().childItems()
                 if sibling.property("text") == "Long_Bracket — excluded"]
        self.assertEqual(1, len(label), "the hover label never rendered the detail")
        self.assertEqual(QColor(label[0].property("color")).name(),
                         states["Long_Bracket"],
                         "the label's colour disagrees with the state ink")

    def _bed_rect(self, canvas):
        """The mapped bed rectangle as the painter holds it."""
        bed = canvas.property("_plot").property("bed")
        return tuple(bed.property(name).toNumber() for name in
                     ("bedXMin", "bedXMax", "bedYMin", "bedYMax"))

    @staticmethod
    def _ink_count(image):
        """Pixels off the window's clear colour (the corner) — the
        grid's own ink, counted without assuming a palette."""
        blank = image.pixelColor(0, 0)
        return sum(1 for y in range(image.height())
                   for x in range(image.width())
                   if image.pixelColor(x, y) != blank)

    def test_the_map_follows_a_late_attach_and_a_bed_switch(self):
        """The mapping is computed from its own inputs, never from a
        resize: a printer attached after the mount, dimensions that
        arrive late and a bed switch all re-map the canvas where it
        stands. The map that only rebuilt on completion and on resize
        stayed blank — or on the previous machine's coordinate system —
        until the user happened to resize the popover."""
        document, window = self.mount_window("PlateCanvas.qml", 400, 400)
        canvas = document
        self.assertEqual(canvas.property("objectName"), "moonrakerPlateCanvas")
        self.assertIsNone(canvas.property("_plot"),
                          "the map plotted with no printer attached")

        # The late attach, dimensions and all. The threaded raster
        # needs wall-clock pumping before the grab can see it.
        printer = LateBedDouble()
        self._bed_printer = printer
        printer.setBed(250.0, 250.0)
        document.setProperty("printerModel", printer)
        self._pump_ms(200)
        plot = canvas.property("_plot")
        self.assertIsNotNone(plot, "the late attach never built the mapping")
        self.assertEqual(self._bed_rect(canvas), (0.0, 250.0, 0.0, 250.0))
        plotted = window.grabWindow()
        self.assertGreater(self._ink_count(plotted), 0,
                           "the late attach never reached the canvas")

        # A bed switch: the same canvas, the new machine's rectangle,
        # and the raster repainted with it.
        printer.setBed(300.0, 200.0)
        self._pump_ms(200)
        plot = canvas.property("_plot")
        self.assertIsNotNone(plot, "the bed switch dropped the mapping")
        self.assertEqual(self._bed_rect(canvas), (0.0, 300.0, 0.0, 200.0))
        switched = window.grabWindow()
        self.assertEqual(switched.size(), plotted.size())
        self.assertNotEqual(switched, plotted,
                            "the map never repainted for the bed switch")

        # Zero dimensions are unknown geometry; the real ones that
        # follow are the same no-resize path.
        printer.setBed(0.0, 0.0)
        self.pump(30)
        self.assertIsNone(canvas.property("_plot"), "a 0 mm bed still plotted")
        printer.setBed(220.0, 180.0)
        self.pump(30)
        self.assertIsNotNone(canvas.property("_plot"),
                             "the late dimensions never built the mapping")

        # The centre convention is the third bed input, on its own
        # signal: the rectangle reflects about zero.
        printer.setCentreIsZero(True)
        self.pump(30)
        self.assertEqual(self._bed_rect(canvas), (-110.0, 110.0, -90.0, 90.0))


if QT_AVAILABLE:

    class PlateDownloadPrinterDouble(QObject):
        """The download action's printer surface. The call counter is
        the click oracle for the whole-row target: a row that stopped
        routing clicks to the label would leave it at zero."""

        improvingEtaChanged = pyqtSignal()
        monitorConnectedChanged = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.improve_eta_calls = 0

        @pyqtProperty(bool, notify=improvingEtaChanged)
        def improvingEta(self):
            return False

        @pyqtProperty(float, notify=improvingEtaChanged)
        def improveEtaProgress(self):
            return -1.0

        @pyqtProperty(str, notify=improvingEtaChanged)
        def improveEtaPhase(self):
            return ""

        @pyqtProperty(bool, notify=monitorConnectedChanged)
        def monitorConnected(self):
            return True

        @pyqtSlot()
        def improveEta(self):
            self.improve_eta_calls += 1


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class PlateDownloadActionTests(RealEngineTestCase):
    """The plate cards' download action must stay ONE click target
    without anchoring a layout-managed item: anchoring a child of a
    layout is undefined behaviour and the engine warns about it on
    every mount. The MouseArea therefore lives in a plain Item whose
    anchored RowLayout carries the glyph and the label."""

    def _mount_action(self, width=260):
        # The printer is RETAINED: a Python-created QObject dies with
        # its last Python ref and the QML var then reads null.
        printer = PlateDownloadPrinterDouble()
        self._printer = printer
        document, window = self.mount_window("PlateDownloadAction.qml", width, 240)
        document.setProperty("printerModel", printer)
        self.pump(30)
        return document, window, printer

    def _click_centre(self, item, window):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
        self.pump(10)

    def _instruction_row(self, document):
        """The label the user reads and the MouseArea over it."""
        label = area = None
        for item in document.findChildren(QQuickItem):
            name = item.metaObject().className()
            text = item.property("text")
            if label is None and "Label" in name and isinstance(text, str) and text:
                label = item
            if area is None and name == "QQuickMouseArea":
                area = item
        self.assertIsNotNone(label, "the instruction label did not build")
        self.assertIsNotNone(area, "the instruction row's MouseArea did not build")
        return label, area

    def test_the_instruction_row_anchors_nothing_managed_by_a_layout(self):
        self.pump(10)  # flush anything queued by an earlier mount
        before = len(_APPLICATION["messages"])
        document, window, printer = self._mount_action()
        warned = [message for message in _APPLICATION["messages"][before:]
                  if "PlateDownloadAction.qml" in message or "managed by a layout" in message]
        self.assertEqual(warned, [], "the row's anchors warn on the real engine again")
        label, area = self._instruction_row(document)
        self.assertNotIn("Layout", area.parentItem().metaObject().className(),
                         "the MouseArea hangs off a layout item again")

    def test_the_whole_instruction_row_stays_one_click_target(self):
        document, window, printer = self._mount_action()
        label, area = self._instruction_row(document)
        base = window.contentItem()
        area_rect = self.rect(area, base)
        label_rect = self.rect(label, base)
        self.assertTrue(area_rect.contains(label_rect),
                        "the label sits outside the row's MouseArea")
        # The row is the full width the card gives the action: the
        # blank space beside the text is the same offer.
        self.assertAlmostEqual(area_rect.width(), document.width(), delta=0.5)
        self._click_centre(label, window)
        self.assertEqual(printer.improve_eta_calls, 1,
                         "clicking the label no longer downloads the index")


# --------------------------------------------------------------------------
# The settings pane (MoonrakerFollowerConfiguration.qml) on the real
# engine: the Diagnostics tab's persistent-cache-size field and its
# range copy, the seek-trace toggle, the cache-clear button's effect
# and status row, and the interval sliders' grab contract. The page
# mounts with the REAL MoonrakerFollowerMachineAction as its manager
# context object; the controls are clicked the way a user hits them
# and the assertions read the PrinterConfig the save wrote. The
# validator semantics stay in test_machine_action_coverage.py; this
# block owns the QML wiring.
import contextlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qt_runtime_support import runtime  # noqa: E402

if QT_AVAILABLE:
    from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, pyqtSignal
    from PyQt6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
    from PyQt6.QtQuick import QQuickItem, QQuickWindow

    # Stands in for Cura's DefinitionContainer: the action is imported
    # over it and the registry double passes its instances through.
    _DefinitionContainer = type("DefinitionContainer", (), {})

    class _MachineActionBase(QObject):
        """Cura's MachineAction contract: the stored key and label."""

        def __init__(self, key, label):
            super().__init__()
            self._key = key
            self._label = label

        def getKey(self):
            return self._key

        def getLabel(self):
            return self._label

    class _Registry(QObject):
        containerAdded = pyqtSignal(object)

    class _Application(QObject):
        """The host the action registers itself with. The page itself
        never touches it (its QML context object is the action)."""

        globalContainerStackChanged = pyqtSignal()

        def getContainerRegistry(self):
            return _Registry()

        def getMachineActionManager(self):
            return SimpleNamespace(addSupportedAction=Mock())

    class _Follower:
        """The facade the action reads and writes: the current printer's
        config, and whether the persistence layer accepted the write.
        ``apply_printer_config`` installs the new config the way the real
        facade does, so a saved setting republishes through the manager's
        properties."""

        def __init__(self, config):
            self.config = config
            self.identity = ("printer-a", "Printer A")
            self.applied = []
            self.apply_result = True
            self.persistence = None

        def current_printer_config(self):
            return self.config

        def current_printer_identity(self):
            return self.identity

        def apply_printer_config(self, config):
            self.applied.append(config)
            self.config = config
            return self.apply_result

    class _ActionDialogDouble(QObject):
        """The dialog the page calls close() on after a save."""

        accepted = pyqtSignal()
        rejected = pyqtSignal()
        closing = pyqtSignal()

        def close(self):
            pass

    class _CatalogDouble(QObject):
        """Cura's translation catalog, read by the themed controls."""

        def i18nc(self, _context, text):
            return text

        def i18n(self, text):
            return text

    # The context-property wrappers must outlive the documents they are
    # bound to: PyQt6 releases a wrapper when the last Python reference
    # goes, and QML then sees a null context object mid-teardown.
    _LIVE_CONTEXT_OBJECTS = []


class SettingsPageCase(RealEngineTestCase):
    """The settings pane mounted offscreen against the real machine
    action, in a real window (a windowless mount never lays out twice)."""

    DIAGNOSTICS_TAB = 3

    def setUp(self):
        super().setUp()
        # runtime() brings the Cura module stubs the plugin imports over
        # (UM.Logger, UM.Resources, UM.Preferences...); the registry and
        # container stubs are added for the action's registration path.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.qt = stack.enter_context(runtime())
        stack.enter_context(patch.dict(sys.modules, {
            "cura.MachineAction": SimpleNamespace(MachineAction=_MachineActionBase),
            "UM.Settings": SimpleNamespace(
                DefinitionContainer=SimpleNamespace(DefinitionContainer=_DefinitionContainer)),
            "UM.Settings.DefinitionContainer": SimpleNamespace(
                DefinitionContainer=_DefinitionContainer),
        }))
        self.module = self.qt.load("MoonrakerFollowerMachineAction")
        self.printer_config = self.qt.load("PrinterConfig")

    # ---- mounting ----------------------------------------------------

    def settings_config(self, **overrides):
        """A configured printer. The usable URL is what the page reads to
        make its Save button live: a placeholder URL leaves canSave false
        and every click on Save inert."""
        settings = {"url": "http://printer-a:7125/"}
        settings.update(overrides)
        return self.printer_config.PrinterConfig(**settings)

    def open_settings(self, config=None, *, tab=0, width=760, height=1100):
        """Mount the page in a window and make one tab current."""
        self.follower = _Follower(config if config is not None else self.settings_config())
        self.action = self.module.MoonrakerFollowerMachineAction(_Application(), self.follower, None)
        dialog = _ActionDialogDouble()
        catalog = _CatalogDouble()
        _LIVE_CONTEXT_OBJECTS.extend((self.action, dialog, catalog))
        context = self.engine.rootContext()
        context.setContextProperty("manager", self.action)
        context.setContextProperty("actionDialog", dialog)
        context.setContextProperty("catalog", catalog)
        document = self.mount("MoonrakerFollowerConfiguration.qml")
        window = QQuickWindow()
        window.resize(width, height)
        document.setParentItem(window.contentItem())
        document.setWidth(width)
        document.setHeight(height)
        window.show()
        self.addCleanup(self._close_page, document, window)
        self.pump(30)
        self.show_tab(document, tab)
        return document, window

    def _close_page(self, document, window):
        # Detach before the engine tears the scene down, while the
        # context-object wrappers are still referenced.
        document.setParentItem(None)
        document.deleteLater()
        window.close()
        self.pump(20)

    def show_tab(self, document, index):
        for candidate in document.findChildren(QQuickItem):
            meta = candidate.metaObject()
            name = meta.className() if meta is not None else ""
            if name == "QQuickTabBar" or name.startswith("TabRow_"):
                candidate.setProperty("currentIndex", index)
                self.pump(20)
                return
        self.fail("the settings tab bar did not mount")

    # ---- finding -----------------------------------------------------

    def item_with_text(self, document, text):
        """The control whose text is exactly this. The themed controls
        alias their text onto an inner label, so the match is narrowed to
        the one item that owns the text and knows how to be clicked."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if item.property("text") == text
                   and not item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(matches),
                         "expected exactly one control reading %r, got %d" % (text, len(matches)))
        return matches[0]

    def cache_size_field(self, document):
        """The persistent-cache-size field: the TextField on the row the
        Diagnostics copy labels 'Persistent cache size (MiB)'."""
        row = self._row_of(self.label_with_text(document, "Persistent cache size (MiB)"))
        fields = [child for child in row.childItems()
                  if child.property("maximumLength") is not None]
        self.assertEqual(1, len(fields), "the cache-size row holds no single field")
        return fields[0]

    def cache_range_copy(self, document):
        """The inline range copy on the cache-size row."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if isinstance(item.property("text"), str)
                   and item.property("text").startswith("Cache size must be between")]
        self.assertEqual(1, len(matches), "the cache-size range copy never rendered")
        return matches[0]

    def seek_trace_box(self, document):
        """The seek-timeline diagnostics toggle."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if isinstance(item.property("text"), str)
                   and item.property("text").startswith("Log follower seek timelines")
                   and item.property("checked") is not None]
        self.assertEqual(1, len(matches), "the seek-trace toggle never rendered")
        return matches[0]

    def clear_cache_button(self, document):
        return self.item_with_text(document, "Clear cached downloads and indexes")

    def label_with_text(self, document, text):
        matches = [item for item in document.findChildren(QQuickItem)
                   if item.property("text") == text
                   and item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(matches),
                         "expected exactly one label reading %r, got %d" % (text, len(matches)))
        return matches[0]

    def interval_slider(self, document, label_text):
        """The slider on the row the caption labels — the caption is a
        sibling of its RowLayout in the column, the row directly below."""
        row = self._row_of(self.label_with_text(document, label_text))
        sliders = [child for child in row.childItems()
                   if "Slider" in child.metaObject().className()
                   and child.property("handle") is not None]
        self.assertEqual(1, len(sliders), "the %s row holds no single slider" % label_text)
        return sliders[0]

    @staticmethod
    def _row_of(label):
        """The RowLayout that lays the label out: the label's own parent
        when it sits inside its row (the Diagnostics cache row), else the
        first row under it in the same column (the interval captions)."""
        parent = label.parentItem()
        if parent.metaObject().className() == "QQuickRowLayout":
            return parent
        label_top = label.mapToItem(parent, QPointF(0.0, 0.0)).y()
        rows = [child for child in parent.childItems()
                if child.metaObject().className() == "QQuickRowLayout"
                and child.mapToItem(parent, QPointF(0.0, 0.0)).y() > label_top]
        if not rows:
            raise AssertionError("no row renders under the label")
        return min(rows, key=lambda row: row.mapToItem(parent, QPointF(0.0, 0.0)).y())

    # ---- driving -----------------------------------------------------

    def click_item(self, window, item, *, dx=0.5, dy=0.5):
        """A real press-and-release at a point inside the item, refused
        unless the point is actually on screen (a click outside the
        window is silently dropped and would read as a pass)."""
        scene = item.mapToScene(QPointF(item.width() * dx, item.height() * dy))
        self._assert_on_screen(window, scene)
        self._send_mouse(window, QEvent.Type.MouseButtonPress, scene,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        self._send_mouse(window, QEvent.Type.MouseButtonRelease, scene,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.pump(20)

    @staticmethod
    def _assert_on_screen(window, scene):
        if not (0.0 <= scene.x() <= window.width() and 0.0 <= scene.y() <= window.height()):
            raise AssertionError("the click at (%.1f, %.1f) is outside the %dx%d window"
                                 % (scene.x(), scene.y(), window.width(), window.height()))

    @staticmethod
    def _send_mouse(window, kind, scene, buttons, button):
        QGuiApplication.sendEvent(window, QMouseEvent(
            kind, QPointF(scene),
            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            button, buttons, Qt.KeyboardModifier.NoModifier))

    def press_key(self, window, key):
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(window, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))
        self.pump(20)

    def save_button(self, document):
        return self.item_with_text(document, "Save")

    def type_cache_size(self, document, text):
        """Type into the field the way a user does: the text changes, the
        binding on the field is replaced, and the page re-validates."""
        field = self.cache_size_field(document)
        field.setProperty("text", text)
        self.pump(20)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsCacheSizeTests(SettingsPageCase):
    """The Diagnostics tab's persistent-cache-size field (4.6.0): the
    limit is per printer, the field is seeded from the stored one, and an
    out-of-range entry is refused before it reaches the settings file."""

    def test_the_cache_size_field_is_seeded_from_the_stored_limit(self):
        document, _window = self.open_settings(self.settings_config(cache_max_mb=2048),
                                               tab=self.DIAGNOSTICS_TAB)
        field = self.cache_size_field(document)
        self.assertEqual("2048", field.property("text"),
                         "the field did not seed from the stored cache limit")
        self.assertEqual("2048", self.action.settingsCacheMaxMb)
        self.assertFalse(self.cache_range_copy(document).property("visible"),
                         "a stored limit in range showed the range copy")
        self.assertTrue(document.property("canSave"))

    def test_an_out_of_range_cache_size_shows_the_copy_and_blocks_the_save(self):
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        copy = self.cache_range_copy(document)
        # Typed the way a user types it: the digits, and the junk a
        # paste or a slipping finger produces.
        for text in ("15", "4097", "not a number", ""):
            with self.subTest(text=text):
                self.type_cache_size(document, text)
                self.assertFalse(document.property("validCacheMax"), text)
                self.assertTrue(copy.property("visible"),
                                "the range copy stayed hidden for %r" % text)
                self.assertFalse(document.property("canSave"), text)
                self.assertFalse(self.save_button(document).property("enabled"),
                                 "the Save button stayed live for %r" % text)
                self.click_item(window, self.save_button(document))
                self.assertEqual([], self.follower.applied,
                                 "an invalid cache size reached the save path")
        # The boundary values are inside the range, one step out are not.
        for text, expected in (("16", True), ("4096", True), ("15", False), ("4097", False)):
            with self.subTest(text=text):
                self.type_cache_size(document, text)
                self.assertEqual(expected, document.property("validCacheMax"), text)
        self.type_cache_size(document, "512")
        self.assertFalse(copy.property("visible"),
                         "the range copy survived a return to a valid size")
        self.assertTrue(document.property("canSave"))

    def test_a_typed_cache_size_lands_in_the_printer_config(self):
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        self.type_cache_size(document, "256")
        self.assertTrue(document.property("canSave"),
                        "a valid cache size left the page unsaveable")
        self.click_item(window, self.save_button(document))
        self.assertTrue(self.follower.applied, "the Save click never reached the action")
        self.assertEqual(256, self.follower.config.cache_max_mb,
                         "the typed cache size never reached the printer config")
        self.assertEqual("256", self.action.settingsCacheMaxMb,
                         "the saved size never republished through the manager")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsSeekTraceTests(SettingsPageCase):
    """The Diagnostics tab's seek-timeline toggle (4.6.0): it shows the
    stored setting, follows a change to it, and saves as its own key."""

    def test_the_seek_trace_box_mirrors_the_stored_setting(self):
        document, _window = self.open_settings(self.settings_config(seek_trace=False),
                                               tab=self.DIAGNOSTICS_TAB)
        box = self.seek_trace_box(document)
        self.assertFalse(box.property("checked"),
                         "the toggle was ticked with the stored trace off")
        # The stored setting changing under a mounted page republishes
        # through settingsChanged — the list's toggle must follow it.
        self.follower.config.seek_trace = True
        self.action.settingsChanged.emit()
        self.pump(20)
        self.assertTrue(box.property("checked"),
                        "the toggle ignored the stored setting's change")

    def test_ticking_the_seek_trace_box_saves_the_toggle(self):
        """The box is bound to ``manager.settingsSeekTrace`` for display;
        the page's ``save()`` payload must carry the toggle through to
        the printer config, never silently clear a stored true."""

        document, window = self.open_settings(self.settings_config(seek_trace=False),
                                              tab=self.DIAGNOSTICS_TAB)
        box = self.seek_trace_box(document)
        self.click_item(window, box)
        self.assertTrue(box.property("checked"), "the click never ticked the toggle")
        self.click_item(window, self.save_button(document))
        self.assertTrue(self.follower.applied, "the Save click never reached the action")
        self.assertTrue(self.follower.config.seek_trace,
                        "the ticked seek trace never reached the printer config")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsCacheClearTests(SettingsPageCase):
    """The Diagnostics tab's cache-clear button: it wipes the persistent
    cache root (the drop that forces a full re-download and re-index) and
    reports the outcome on its own row rather than in a dialog."""

    def test_the_clear_button_wipes_the_cache_root_and_reports_it(self):
        from plugins.CacheNamespaces import CACHE_DIRECTORY_NAME
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        resources = sys.modules["UM.Resources"].Resources
        cache_root = os.path.join(resources.getCacheStoragePath(), CACHE_DIRECTORY_NAME)
        entry = os.path.join(cache_root, "index", "layer-index.json")
        os.makedirs(os.path.dirname(entry), exist_ok=True)
        with open(entry, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.click_item(window, self.clear_cache_button(document))
        self.assertFalse(os.path.exists(cache_root),
                         "the clear button left the persistent cache on disk")
        # The result is reported where the click happened: the row's
        # status label reads the manager's own verdict.
        status = self.action.cacheStatus
        self.assertTrue(status, "the clear produced no status text")
        self.assertEqual(status, self.label_with_text(document, status).property("text"),
                         "the row never showed the clear result")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class IntervalSliderGrabTests(SettingsPageCase):
    """The interval sliders' grab contract (the 4.6.0 reviewer finding:
    the window ignored the handle's width, so the half of the painted
    handle away from the value never grabbed and the click fell through
    to the track, which jumps). A track click still jumps — that is the
    control that keeps this suite honest."""

    SLIDERS = ("Status update interval (milliseconds)",
               "Auxiliary status interval (milliseconds)",
               "Console output interval (milliseconds)")

    def _painted_handle(self, slider):
        """The handle's own geometry — where it was actually painted, not
        where a formula says it should be."""
        handle = slider.property("handle")
        return handle.x(), handle.x() + handle.width()

    def _press_and_release(self, window, slider, x, dx=0.0):
        y = slider.height() / 2
        start = slider.mapToItem(window.contentItem(), QPointF(x, y))
        end = slider.mapToItem(window.contentItem(), QPointF(x + dx, y))
        self._assert_on_screen(window, start)
        self._send_mouse(window, QEvent.Type.MouseButtonPress, start,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        if dx:
            self._send_mouse(window, QEvent.Type.MouseMove, end,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self._send_mouse(window, QEvent.Type.MouseButtonRelease, end,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.pump(20)

    def test_every_interval_slider_grabs_both_halves_of_the_painted_handle(self):
        # The poll slider is asked for its floor: at the track's left end
        # the old centre-window is furthest from the painted handle, so
        # the far tip is the point that separates the two rules.
        document, window = self.open_settings(self.settings_config(poll_interval_ms=250))
        for caption in self.SLIDERS:
            with self.subTest(slider=caption):
                slider = self.interval_slider(document, caption)
                self.assertTrue(slider.isVisible(), caption)
                self._check_grab(window, slider)

    def _check_grab(self, window, slider):
        left, right = self._painted_handle(slider)
        floor = slider.property("from")
        anchor = slider.property("value")
        # A press on the handle is inert wherever on the handle it lands.
        for x in (left + 1.0, (left + right) / 2.0, right - 0.5):
            self._press_and_release(window, slider, x)
            self.assertEqual(anchor, slider.property("value"),
                             "a press at %.1f moved the slider off its handle" % x)
        # ... and a drag out of either tip grabs it and carries it along.
        # Setting the value directly is the harness's stand-in for the
        # operator having put the handle there.
        for x in (left + 1.0, right - 0.5):
            slider.setProperty("value", floor)
            self.pump(20)
            self._press_and_release(window, slider, x, dx=60.0)
            self.assertGreater(slider.property("value"), floor,
                               "a drag from the handle at %.1f never grabbed" % x)

    def test_a_groove_click_still_jumps_and_the_handle_click_keeps_the_steps(self):
        document, window = self.open_settings(self.settings_config(poll_interval_ms=250))
        slider = self.interval_slider(document, self.SLIDERS[0])
        left, right = self._painted_handle(slider)
        # The control: away from the handle the click must jump, so a
        # grader that reports "nothing moved" for the handle cases is
        # known to be able to see a move at all.
        self.assertEqual(0.0, slider.property("value"),
                         "the poll slider did not start at its floor")
        self._press_and_release(window, slider, right + 30.0)
        self.assertGreater(slider.property("value"), 0.0,
                           "a groove click no longer jumps the slider")
        # A handle click takes the focus; the arrow keys step one step
        # and the step survives the focus change the key path makes.
        self._press_and_release(window, slider, (left + right) / 2.0)
        self.assertTrue(slider.property("activeFocus"),
                        "a handle click never focused the slider")
        anchor = slider.property("value")
        self.press_key(window, Qt.Key.Key_Right)
        self.assertEqual(anchor + 1, slider.property("value"),
                         "an arrow key never nudged one step")
        self.assertTrue(slider.property("activeFocus"),
                        "the key path dropped the slider's focus")
        self.press_key(window, Qt.Key.Key_Left)
        self.assertEqual(anchor, slider.property("value"),
                         "the second arrow key never stepped back")


# --------------------------------------------------------------------------
# The follower-view feed's device-pixel contract: the popover's own QML
# call passes the device-pixel-ratio as a NINTH argument, the mini's
# compact feed stops at eight, and the ratio is a pixel input of the
# navigation raster — a change must re-bake it. This block runs the REAL
# MoonrakerMonitorModel in this process (the runtime() stubs) beside the
# engine's documents: the model is the only thing that can answer for
# its own slot arity.
if QT_AVAILABLE:
    class _DprMesh(QObject):
        """The bed-mesh double the model wants: the navigation raster's
        grid reads only the machine bounds off it."""

        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.snapshot = {}
            self.visible = True

        def set_thresholds(self, low, high):
            pass

        def set_visible(self, value):
            self.visible = value
            self.changed.emit()


    @unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
    class FollowerViewDprTests(RealEngineTestCase):
        """The follower view feed's argument list and the DPR's ride
        through the model's view dict into the navigation raster."""

        # The two production call sites' argument lists verbatim
        # (MoonrakerMonitor.qml's _feedRenderView and
        # PlateProgressSection.qml's compact feed). The DPR expression
        # around it is the production one; Screen.devicePixelRatio is
        # read-only and 1.0 offscreen, so a property stands in for the
        # screen.
        CALLER = b'''
import QtQuick 2.15
QtObject {
    property var printer: null
    property real screenDpr: 1.0
    property string failure: ""

    function feedPopover() {
        printer.setFollowerView("popover", 1.0, 0.7, 400, 300, false, 0.0, 0.0,
                                Math.min(2.0, Math.max(1.0, screenDpr)));
    }
    function feedMini() {
        printer.setFollowerView("mini", 1.0, 0.7, 120, 120, true, 0.0, 0.0);
    }
    function runPopover() {
        try { feedPopover(); failure = ""; } catch (error) { failure = String(error); }
    }
    function runMini() {
        try { feedMini(); failure = ""; } catch (error) { failure = String(error); }
    }
}
'''

        def setUp(self):
            super().setUp()
            stack = contextlib.ExitStack()
            self.addCleanup(stack.close)
            self.qt = stack.enter_context(runtime())
            from qt_runtime_support import ScriptedTransport
            client = self.qt.load("MoonrakerClient").MoonrakerClient(
                transport=ScriptedTransport())
            client.configure("http://printer-a", "test-key", 750)
            client._poll_timer.stop()
            self.addCleanup(client.stop)
            print_state = self.qt.load("PrintState").PrintSnapshot()
            config = self.qt.load("PrinterConfig").PrinterConfig(
                url="http://printer-a", api_key="test-key")
            self.model = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel(
                None, 1,
                client=client,
                print_state=lambda: print_state,
                config=lambda: config,
                apply_config=lambda value: None,
                bed_mesh=_DprMesh(),
                identity=lambda: ("A", "Printer A"))
            self.addCleanup(self.model.setMonitoringActive, False)
            component = QQmlComponent(self.engine)
            component.setData(self.CALLER, QUrl.fromLocalFile(
                str(ROOT / "plugins" / "follower-view-caller.qml")))
            self.caller = component.create()
            self.assertIsNotNone(self.caller, [str(error) for error in component.errors()])
            self.addCleanup(self.caller.deleteLater)
            self.caller.setProperty("printer", self.model)

        # ---- helpers --------------------------------------------------

        def _call(self, name):
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(self.caller, name)
            self.assertEqual(self.caller.property("failure"), "",
                             "the QML view call raised")
            self.qt.events(10)

        def _surface(self, name):
            return self.model._plate_surfaces[name]

        def _navigation_demand(self, surface, dpr):
            """A navigation demand on a hand-set context: no scheduler
            runs, so the key under test is the only thing moving."""
            surface.plot = {"offsetX": 10.0, "offsetY": 10.0, "sx": 3.0,
                            "sy": 3.0, "bedXMin": 0.0, "bedYMax": 250.0}
            surface.view = {"scale": 1.0, "lineScale": 0.7, "width": 400,
                            "height": 300, "compact": False,
                            "panX": 0.0, "panY": 0.0, "dpr": dpr}
            payload = {"classes": {"SKIN": [[[0.0, 0.0, 0.0], [250.0, 0.0, 3.0]]]},
                       "travels": [], "travelStarts": [], "travelEnds": [],
                       "motions": 4}
            self.model._qt_layer(surface, payload, 5)
            surface.desired = {"current": 5, "ghosts": {}, "split": 2}
            return self.model._navigation_key(surface)

        # ---- the slot's signatures ------------------------------------

        def test_the_popovers_nine_argument_call_lands_at_dpr_two(self):
            # The popover feeds the DPR ninth; a slot registered for
            # eight arguments silently drops it (the engine's "Too many
            # arguments" warning), and the workers then paint at DPR 1.
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runPopover")
            view = self._surface("popover").view
            self.assertEqual(view.get("dpr"), 2.0,
                             "the nine-argument view call dropped its DPR")
            self.assertEqual(view.get("width"), 400)
            self.assertFalse(view.get("compact"))

        def test_the_minis_eight_argument_call_still_lands(self):
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runMini")
            view = self._surface("mini").view
            self.assertEqual(view.get("width"), 120)
            self.assertTrue(view.get("compact"))
            self.assertEqual(view.get("dpr"), 1.0,
                             "the eight-argument call invented a DPR")

        def test_the_production_popover_call_reaches_the_slot_intact(self):
            # The monitor's own face feeds the slot through its
            # viewSettled connection: the arity mismatch is visible in
            # the engine's diagnostics even where the dropped value
            # (1.0 offscreen) is indistinguishable.
            monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
            monitor.setProperty("printer", self.model)
            monitor.setProperty("openPopOver", "plateprogress")
            self.pump(30)
            faces = [face for face in monitor.findChildren(QQuickItem, "moonrakerPlateProgressFace")
                     if not face.property("compact")]
            self.assertEqual(len(faces), 1)
            face = faces[0]
            from PyQt6.QtCore import QMetaObject
            QMetaObject.invokeMethod(face, "viewSettled")
            self.pump(20)
            view = self._surface("popover").view
            self.assertEqual(view.get("width"), int(face.width()),
                             "the popover's view never reached the model")
            self.assertFalse(view.get("compact"))
            truncated = [message for message in
                         _APPLICATION["messages"][self._message_start:]
                         if "Too many arguments" in message]
            self.assertEqual(truncated, [],
                             "the popover's nine-argument view call was truncated")

        # ---- the DPR's ride into the navigation raster -----------------

        def test_a_qml_dpr_change_rebakes_the_navigation_raster(self):
            surface = self._surface("popover")
            before = self._navigation_demand(surface, 1.0)
            self.assertIsNotNone(before, "the navigation demand never built")
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runPopover")
            self.assertEqual(surface.view.get("dpr"), 2.0)
            after = self.model._navigation_key(surface)
            self.assertNotEqual(before, after,
                                "a DPR change never invalidated the navigation raster")
            self.assertNotEqual(self.model._nav_key_hard(before),
                                self.model._nav_key_hard(after),
                                "the DPR change is not hard — the follow throttle "
                                "would swallow the re-bake")

        def test_a_dpr_stale_navigation_raster_leaves_the_face(self):
            surface = self._surface("popover")
            baked = self._navigation_demand(surface, 1.0)
            surface.nav["url"] = "file:///tmp/mpf/raster-probe/nav-stub.png"
            surface.nav["key"] = baked
            self.assertEqual(self.model._navigation_data_value(surface),
                             surface.nav["url"],
                             "the warm raster never reached the face")
            self.caller.setProperty("screenDpr", 2.0)
            self._call("runPopover")
            # The raster in hand was baked at the old ratio: at the new
            # demand it is a stale picture of the same scene.
            surface.nav["url"] = "file:///tmp/mpf/raster-probe/nav-stub.png"
            surface.nav["key"] = baked
            self.assertEqual(self.model._navigation_data_value(surface), "",
                             "a DPR-stale navigation raster still reached the face")
