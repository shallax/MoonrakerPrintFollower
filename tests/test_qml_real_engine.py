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
        # every size and never filled it.
        for width in (640, 900):
            monitor = self.mount_monitor(width)
            flick = self.find(monitor, "moonrakerStatusFlick")
            content = self.find(monitor, "moonrakerStatusContent")
            self.assertGreater(flick.width(), 100, "the status pane did not lay out")
            self.assertAlmostEqual(content.width(), flick.width() - 14, delta=0.5)
        self.assertEqual(monitor.width(), 900)

    def test_the_sections_fill_the_column_once_it_is_wide(self):
        monitor = self.mount_monitor(900)
        content = self.find(monitor, "moonrakerStatusContent")
        sections = [child for child in content.childItems() if child.isVisible() and child.width() > 0]
        self.assertGreaterEqual(len(sections), 3)
        for section in sections:
            self.assertAlmostEqual(section.width(), content.width(), delta=0.5)

    def test_the_column_never_keeps_its_own_implicit_width(self):
        # The narrow viewport is the crisp case: the column is NARROWER
        # than the content it holds, which only happens when the width
        # tracks the flickable.
        monitor = self.mount_monitor(640)
        content = self.find(monitor, "moonrakerStatusContent")
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
