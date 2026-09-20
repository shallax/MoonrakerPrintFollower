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
    from PyQt6.QtCore import QCoreApplication, QObject, QPointF, QRectF, QUrl, qInstallMessageHandler
    from PyQt6.QtCore import pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtQml import QQmlComponent, QQmlEngine
    from PyQt6.QtQuick import QQuickItem, QQuickWindow

    class CuraApplicationDouble(QObject):
        """The one context property both documents read for idleness."""

        platformActivityChanged = pyqtSignal()

        @pyqtProperty(bool, notify=platformActivityChanged)
        def platformActivity(self):
            return True


    class CameraModelDouble(QObject):
        """The webcam surface the camera card reads — enough for its
        title row to build with the selector and refresh button
        showing, which is the widest that row ever gets."""

        monitorConnectedChanged = pyqtSignal()
        cameraRecoveringChanged = pyqtSignal()
        cameraRotationChanged = pyqtSignal()
        cameraFlipChanged = pyqtSignal()
        activeWebcamChanged = pyqtSignal()
        webcamNamesChanged = pyqtSignal()

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

        def __init__(self):
            super().__init__()
            self.info_calls = []
            self.status_calls = []
            self.controls_calls = []
            self.section_calls = []
            self._info_collapsed = False
            self._status_collapsed = False
            self._controls_collapsed = False
            self._eta = "—"
            self._finish = "—"
            self._print_active = False

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
        self.pump(30)
        return document, window

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

    def _mount(self, width=1100, document="MoonrakerMonitor.qml"):
        class OutputDouble(QObject):
            activePrinterChanged = pyqtSignal()

            def __init__(self, printer):
                super().__init__()
                self._printer = printer

            @pyqtProperty(QObject, notify=activePrinterChanged)
            def activePrinter(self):
                return self._printer

        printer = PrinterModelDouble()
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
        # The review's catch: the strip watched the block, the
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
    """The camera start/stop ownership (the 2026-09-19 cold-start
    review): one logical desired state, one application, at most one
    start/stop transition. The forked renderer's start() used to
    be destructive — it stopped the live reply first — so a duplicate
    application must never touch the image."""

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

        def __init__(self):
            super().__init__()
            self._dot = {"x": 125.0, "y": 125.0, "valid": True}
            self._plate = {"objects": [
                {"name": "Widget", "center": [125.0, 125.0],
                 "polygon": [[0.0, 0.0], [250.0, 0.0], [250.0, 250.0], [0.0, 250.0]],
                 "current": False, "excluded": False, "restoreAllowed": True},
            ]}

        @pyqtProperty(float, constant=True)
        def bedMeshMachineWidth(self):
            return 250.0

        @pyqtProperty(float, constant=True)
        def bedMeshMachineDepth(self):
            return 250.0

        @pyqtProperty(bool, constant=True)
        def bedMeshCenterIsZero(self):
            return False

        @pyqtProperty("QVariant", constant=True)
        def plateProgress(self):
            return PlateFaceRenderTests.CORNER_PAYLOAD

        @pyqtProperty("QVariant", constant=True)
        def plateDot(self):
            return self._dot

        @pyqtProperty("QVariant", constant=True)
        def plateObjects(self):
            return self._plate

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

    @staticmethod
    def _printer():
        return PlatePrinterDouble()

    def _open(self, monitor, popover):
        monitor.setProperty("printer", self._printer())
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

    def _grab_when_inked(self, window, face, timeout=2.5):
        deadline = time.monotonic() + timeout
        image = window.grabWindow()
        while time.monotonic() < deadline and not self._ink_rows(image, face, window):
            self.app.processEvents()
            time.sleep(0.05)
            image = window.grabWindow()
        return image

    def test_the_follower_fills_its_plot_edge_to_edge(self):
        monitor, window = self.mount_window("MoonrakerMonitor.qml", 900, 760)
        self._open(monitor, "plateprogress")
        faces = self._popover_faces(monitor, "moonrakerPlateProgressFace")
        self.assertEqual(len(faces), 1)
        face = faces[0]
        plot = face.findChild(QQuickItem, "moonrakerPlateCanvas").property("_plot")
        bed = plot.property("bed")
        top = bed.property("offsetY").toNumber()
        bottom = top + bed.property("plotHeight").toNumber()
        rows = self._ink_rows(self._grab_when_inked(window, face), face, window)
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
