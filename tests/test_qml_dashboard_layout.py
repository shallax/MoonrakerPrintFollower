"""Executable qml dashboard layout contracts."""
from tests import qml_engine_support as harness


class ZOffsetApplyTests(harness.RealEngineTestCase):
    def test_apply_button_follows_model_eligibility_and_target(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        class ModelDouble(harness.QObject):
            changed = harness.pyqtSignal()

            def __init__(self):
                super().__init__()
                self.can_apply = False
                self.calls = 0

            @harness.pyqtProperty(str, notify=changed)
            def zOffsetText(self):
                return "+0.025 mm"

            @harness.pyqtProperty(str, notify=changed)
            def zOffsetApplyTarget(self):
                return "probe"

            @harness.pyqtProperty(bool, notify=changed)
            def canApplyZOffset(self):
                return self.can_apply

            @harness.pyqtProperty(bool, notify=changed)
            def actionBusy(self):
                return False

            @harness.pyqtProperty(str, notify=changed)
            def sectionReason(self):
                return ""

            @harness.pyqtSlot()
            def applyZOffset(self):
                self.calls += 1

        section = self.mount("ZOffsetControls.qml")
        window = harness.QQuickWindow()
        window.resize(480, 330)
        section.setParentItem(window.contentItem())
        section.setWidth(460)
        window.show()
        self.addCleanup(window.deleteLater)
        model = ModelDouble()
        section.setProperty("printerModel", model)
        self.pump()
        apply = self.find(section, "applyZOffsetButton")
        self.assertEqual(apply.property("text"), "Apply Z offset")
        self.assertFalse(apply.property("enabled"))
        model.can_apply = True
        model.changed.emit()
        self.pump()
        self.assertTrue(apply.property("enabled"))
        center = apply.mapToScene(harness.QPointF(apply.width() / 2, apply.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump()
        self.assertEqual(model.calls, 1)


class FailureDetectionSectionTests(harness.RealEngineTestCase):
    def test_controls_require_setup_and_follow_the_selected_printer(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest

        class ModelDouble(harness.QObject):
            detectionChanged = harness.pyqtSignal()
            sectionChanged = harness.pyqtSignal()

            def __init__(self, notify=False, pause=False, pending=False,
                         ready=False, camera=False, warning=38, failure=78, safe=300):
                super().__init__()
                self._notify = notify
                self._pause = pause
                self._pending = pending
                self._ready = ready
                self._global_enabled = True
                self._camera = camera
                self._enabled = False
                self._warning = warning
                self._failure = failure
                self._safe = safe
                self._rearmable = False
                self._alert_level = "warning"
                self.expanded = True
                self.calls = []

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionReady(self):
                return self._ready

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionGlobalEnabled(self):
                return self._global_enabled

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionCameraReady(self):
                return self._camera

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionEnabled(self):
                return self._enabled

            @harness.pyqtProperty(int, notify=detectionChanged)
            def detectionWarningThreshold(self):
                return self._warning

            @harness.pyqtProperty(int, notify=detectionChanged)
            def detectionFailureThreshold(self):
                return self._failure

            @harness.pyqtProperty(int, notify=detectionChanged)
            def detectionSafeSeconds(self):
                return self._safe

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionNotifyEnabled(self):
                return self._notify

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionPauseEnabled(self):
                return self._pause

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionAlertPending(self):
                return self._pending

            @harness.pyqtProperty(str, notify=detectionChanged)
            def detectionAlertLevel(self):
                return self._alert_level if self._pending else ""

            @harness.pyqtProperty(bool, notify=detectionChanged)
            def detectionPauseRearmable(self):
                return self._rearmable

            @harness.pyqtProperty("QVariant", notify=sectionChanged)
            def sectionExpandedMap(self):
                return {"failureDetection": self.expanded}

            @harness.pyqtSlot(bool)
            def setDetectionEnabled(self, value):
                self.calls.append(("enabled", value))
                self._enabled = value
                self.detectionChanged.emit()

            @harness.pyqtSlot(int, int)
            def setDetectionThresholds(self, warning, failure):
                self.calls.append(("thresholds", warning, failure))
                self._warning, self._failure = warning, failure
                self.detectionChanged.emit()

            @harness.pyqtSlot(int)
            def setDetectionSafeSeconds(self, seconds):
                self.calls.append(("safe", seconds))
                self._safe = seconds
                self.detectionChanged.emit()

            @harness.pyqtSlot(bool)
            def setDetectionNotifyEnabled(self, value):
                self.calls.append(("notify", value))
                self._notify = value
                self.detectionChanged.emit()

            @harness.pyqtSlot(bool)
            def setDetectionPauseEnabled(self, value):
                self.calls.append(("pause", value))
                self._pause = value
                self.detectionChanged.emit()

            @harness.pyqtSlot()
            def acknowledgeDetectionAlert(self):
                self.calls.append(("acknowledge",))
                self._pending = False
                self.detectionChanged.emit()

            @harness.pyqtSlot()
            def rearmDetectionPause(self):
                self.calls.append(("rearm",))
                self._rearmable = False
                self.detectionChanged.emit()

        section = self.mount("FailureDetectionSection.qml")
        window = harness.QQuickWindow()
        window.resize(480, 600)
        section.setParentItem(window.contentItem())
        section.setWidth(460)
        window.show()
        self.addCleanup(window.deleteLater)
        notify = self.find(section, "detectionNotifyCheckbox")
        pause = self.find(section, "detectionPauseCheckbox")
        acknowledge = self.find(section, "detectionAcknowledgeButton")
        rearm = self.find(section, "detectionRearmPauseButton")
        enabled = self.find(section, "detectionMainEnabledCheckbox")
        slider = self.find(section, "detectionControlsThresholdSlider")
        safe_slider = self.find(section, "detectionSafePeriodSlider")
        safe_value = self.find(section, "detectionSafePeriodValue")
        self.assertEqual(enabled.property("text"), "Enable")
        self.assertFalse(notify.property("checked"))
        self.assertFalse(pause.property("checked"))
        self.assertFalse(notify.property("enabled"))
        self.assertFalse(acknowledge.property("enabled"))
        self.assertFalse(enabled.property("enabled"))
        self.assertFalse(slider.property("enabled"))
        self.assertFalse(safe_slider.property("enabled"))
        self.assertEqual(slider.property("minimum"), 0)
        self.assertEqual(slider.property("maximum"), 1)

        first = ModelDouble()
        second = ModelDouble(notify=True, pending=True, warning=25, failure=65, safe=900)
        section.setProperty("printerModel", first)
        self.pump(30)
        self.assertEqual(first.calls, [], "binding the model must not write configuration")
        self.assertEqual(safe_slider.property("value"), 300)
        self.assertEqual(safe_value.property("text"), "5m")
        for control in (enabled, slider, safe_slider, notify, pause, acknowledge, rearm):
            self.assertFalse(control.property("enabled"))

        first._ready = True
        first.detectionChanged.emit()
        self.pump()
        self.assertFalse(enabled.property("enabled"), "a printer with no camera cannot be enabled")
        for control in (slider, safe_slider, notify, pause, acknowledge, rearm):
            self.assertFalse(control.property("enabled"))

        first._camera = True
        first.detectionChanged.emit()
        self.pump()
        self.assertTrue(enabled.property("enabled"))
        for control in (slider, safe_slider, notify, pause, acknowledge, rearm):
            self.assertFalse(control.property("enabled"))
        center = enabled.mapToScene(harness.QPointF(enabled.width() / 2, enabled.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump()
        self.assertEqual(first.calls, [("enabled", True)])
        for control in (slider, safe_slider, notify, pause):
            self.assertTrue(control.property("enabled"))
        self.assertFalse(acknowledge.property("enabled"))

        handle = safe_slider.property("handle")
        origin = handle.mapToScene(harness.QPointF(handle.width() / 2, handle.height() / 2)).toPoint()
        destination = safe_slider.mapToScene(
            harness.QPointF(safe_slider.width() * .8, safe_slider.height() / 2)).toPoint()
        QTest.mousePress(window, Qt.MouseButton.LeftButton, pos=origin)
        QTest.mouseMove(window, pos=destination)
        self.pump()
        self.assertNotEqual(safe_value.property("text"), "5m")
        preview = safe_value.property("text")
        self.assertFalse(any(call[0] == "safe" for call in first.calls),
                         "preview must not save before release")
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, pos=destination)
        self.pump()
        self.assertEqual(safe_value.property("text"), preview)

        for control in (pause, notify):
            center = control.mapToScene(harness.QPointF(control.width() / 2, control.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
            self.pump()
        self.assertEqual(first.calls, [("enabled", True), ("safe", first._safe),
                                       ("pause", True), ("notify", True)])
        low_target = slider.mapToScene(harness.QPointF(slider.width() * 0.15, slider.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=low_target)
        self.pump()
        threshold_calls = [call for call in first.calls if call[0] == "thresholds"]
        self.assertTrue(threshold_calls, "a slider gesture must reach the model")
        self.assertLess(threshold_calls[-1][1], threshold_calls[-1][2])
        self.assertAlmostEqual(slider.property("low"), first._warning / 100)
        self.assertAlmostEqual(slider.property("high"), first._failure / 100)
        labels = [item.property("text") for item in section.findChildren(harness.QQuickItem)]
        self.assertIn("Warning at %.2f" % (first._warning / 100), labels)
        self.assertIn("Failure at %.2f" % (first._failure / 100), labels)
        self.assertTrue(notify.property("checked"))
        self.assertTrue(pause.property("checked"))

        target = safe_slider.mapToScene(harness.QPointF(safe_slider.width() * 0.4,
                                                      safe_slider.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=target)
        self.pump()
        safe_calls = [call for call in first.calls if call[0] == "safe"]
        self.assertTrue(safe_calls, "safe-period gesture must reach the printer model")
        self.assertEqual(safe_calls[-1][1] % 10, 0)
        self.assertEqual(safe_slider.property("value"), first._safe)

        first._pending = True
        first.detectionChanged.emit()
        self.pump()
        self.assertTrue(acknowledge.property("enabled"))
        # The standing alert is marked on the section header too, so a
        # collapsed section still shows something is waiting — and its
        # colour follows the alert's level.
        dot = self.find(section, "sectionAlertDot")
        self.assertTrue(dot.property("visible"))
        warning = harness.QColor(0xfb, 0x8c, 0x00)
        self.assertEqual(dot.property("color"), warning)
        first._alert_level = "failure"
        first.detectionChanged.emit()
        self.pump()
        self.assertEqual(dot.property("color"), harness.QColor(0xd3, 0x2f, 0x2f))
        center = acknowledge.mapToScene(harness.QPointF(acknowledge.width() / 2, acknowledge.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump()
        self.assertEqual(first.calls[-1], ("acknowledge",))
        self.assertFalse(acknowledge.property("enabled"))

        first._rearmable = True
        first.detectionChanged.emit()
        self.pump()
        self.assertTrue(rearm.property("enabled"))
        rearm_center = rearm.mapToScene(harness.QPointF(rearm.width() / 2, rearm.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=rearm_center)
        self.pump()
        self.assertEqual(first.calls[-1], ("rearm",))
        self.assertFalse(rearm.property("enabled"))

        center = enabled.mapToScene(harness.QPointF(enabled.width() / 2, enabled.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump()
        self.assertEqual(first.calls[-1], ("enabled", False))
        self.assertTrue(enabled.property("enabled"))
        for control in (slider, safe_slider, notify, pause, acknowledge, rearm):
            self.assertFalse(control.property("enabled"))
        self.assertTrue(notify.property("checked"))
        self.assertTrue(pause.property("checked"))
        self.assertAlmostEqual(slider.property("low"), first._warning / 100)
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump()
        self.assertEqual(first.calls[-1], ("enabled", True))
        for control in (slider, safe_slider, notify, pause):
            self.assertTrue(control.property("enabled"))

        section.setProperty("printerModel", second)
        self.pump()
        self.assertTrue(notify.property("checked"))
        self.assertFalse(pause.property("checked"))
        self.assertFalse(enabled.property("enabled"))
        self.assertFalse(slider.property("enabled"))
        self.assertFalse(safe_slider.property("enabled"))
        self.assertFalse(notify.property("enabled"))
        self.assertFalse(pause.property("enabled"))
        self.assertFalse(acknowledge.property("enabled"), "a pending alert cannot bypass setup")
        self.assertAlmostEqual(slider.property("low"), .25)
        self.assertAlmostEqual(slider.property("high"), .65)
        self.assertEqual(safe_slider.property("value"), 900)
        self.assertEqual(second.calls, [])
        second._ready = True
        second._camera = True
        second.detectionChanged.emit()
        self.pump()
        self.assertFalse(acknowledge.property("enabled"))
        self.assertFalse(notify.property("enabled"))
        self.assertFalse(rearm.property("enabled"))
        second._enabled = True
        second.detectionChanged.emit()
        self.pump()
        self.assertTrue(acknowledge.property("enabled"))
        second.expanded = False
        second.sectionChanged.emit()
        self.pump()
        self.assertFalse(notify.isVisible())
        self.assertFalse(pause.isVisible())
        self.assertFalse(enabled.isVisible())
        self.assertFalse(slider.isVisible())
        self.assertFalse(safe_slider.isVisible())
        self.assertFalse(acknowledge.isVisible())
        self.assertFalse(rearm.isVisible())
        self.assertEqual(second.calls, [])


class CaptureShellStartupTests(harness.RealEngineTestCase):
    def test_preloaded_dashboard_is_ready_when_the_capture_shell_opens(self):
        from tools.capture_monitor import _preload_dashboard

        dashboard = _preload_dashboard(self.engine)
        self.assertTrue(dashboard.isReady())
        shell, window = self.mount_window("MoonrakerMonitorBedMesh.qml", 900, 760)
        self._wait_until(
            window,
            lambda _image: shell.findChild(harness.QQuickItem, "moonrakerControlsPane") is not None,
            timeout=2.0)
        self.assertIsNotNone(shell.findChild(harness.QQuickItem, "moonrakerControlsPane"))


class StatusColumnGeometryTests(harness.StatusColumnGeometryTests):
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
            # A positioner derives implicitWidth from its explicitly sized
            # children; the contract is that none escape the viewport.
            for section in content.childItems():
                if section.width() > 0:
                    self.assertAlmostEqual(section.width(), content.width(), delta=0.5)


class ConsoleInputRowTests(harness.ConsoleInputRowTests):
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


class PaneGutterTests(harness.PaneGutterTests):
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


class PauseRowRoleTests(harness.PauseRowRoleTests):
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


class StripVerdictRefreshTests(harness.StripVerdictRefreshTests):
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


class TuningResetTests(harness.TuningResetTests):
    def test_each_reset_button_commands_its_factor_to_100(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt

        class ModelDouble(harness.QObject):
            def __init__(self):
                super().__init__()
                self.calls = []

            @harness.pyqtSlot(int)
            def setSpeedFactor(self, percent):
                self.calls.append(("speed", percent))

            @harness.pyqtSlot(int)
            def setFlowFactor(self, percent):
                self.calls.append(("flow", percent))

            @harness.pyqtSlot(int)
            def previewSpeedFactor(self, percent):
                pass

            @harness.pyqtSlot(int)
            def previewFlowFactor(self, percent):
                pass

            @harness.pyqtProperty("QVariant")
            def sectionExpandedMap(self):
                return {}

            @harness.pyqtProperty(bool)
            def controlsLocked(self):
                return False

            @harness.pyqtProperty(bool)
            def monitorConnected(self):
                return True

        section = self.mount("TuningSection.qml")
        window = harness.QQuickWindow()
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
            center = button.mapToScene(harness.QPointF(button.width() / 2, button.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump(30)
        self.assertIn(("speed", 100), model.calls)
        self.assertIn(("flow", 100), model.calls)


class TuningResetConvergenceTests(harness.TuningResetConvergenceTests):
    def test_the_flow_slider_reads_100_after_the_reset_converges(self):
        from PyQt6.QtTest import QTest
        from PyQt6.QtCore import Qt

        class ModelDouble(harness.QObject):
            flowFactorPercentChanged = harness.pyqtSignal()

            def __init__(self):
                super().__init__()
                self._flow = 137
                self.calls = []

            @harness.pyqtProperty(int)
            def speedFactorPercent(self):
                return 100

            @harness.pyqtProperty(int, notify=flowFactorPercentChanged)
            def flowFactorPercent(self):
                return self._flow

            def confirm(self, value):
                self._flow = value
                self.flowFactorPercentChanged.emit()

            @harness.pyqtSlot(int)
            def setFlowFactor(self, percent):
                self.calls.append(("flow", percent))

            @harness.pyqtSlot(int)
            def previewFlowFactor(self, percent):
                pass

            @harness.pyqtProperty("QVariant")
            def sectionExpandedMap(self):
                return {}

            @harness.pyqtProperty(bool)
            def controlsLocked(self):
                return False

            @harness.pyqtProperty(bool)
            def monitorConnected(self):
                return True

        section = self.mount("TuningSection.qml")
        window = harness.QQuickWindow()
        window.resize(520, 400)
        section.setParentItem(window.contentItem())
        window.show()
        self.addCleanup(window.deleteLater)
        model = ModelDouble()
        section.setProperty("printerModel", model)
        self.pump(30)
        button = self.find(section, "moonrakerTuningFlowReset")
        slider = None
        for item in button.parentItem().childItems():
            if "OutlineSlider" in item.metaObject().className():
                slider = item
                break
        self.assertIsNotNone(slider, "the flow slider did not build")
        self.assertEqual(slider.property("value"), 137)
        # A prior user interaction writes the slider's value directly
        # (the drag path) — under the old binding that destroyed the
        # model link and the reset's 100 could never reach the handle.
        slider.setProperty("value", 200)
        self.pump(30)
        center = button.mapToScene(harness.QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=center)
        self.pump(30)
        self.assertIn(("flow", 100), model.calls)
        model.confirm(100)  # the printer's polled confirmation
        self.pump(30)
        self.assertEqual(slider.property("value"), 100,
                         "the slider must read the confirmed 100, not the to-clamp")






class DynamicPaneStackTests(harness.RealEngineTestCase):
    def test_section_visibility_and_height_changes_settle_without_layout_feedback(self):
        dashboard, window = self.mount_window("MoonrakerMonitorDashboard.qml", 1840, 900)
        self._pump_ms(100)
        columns = [self.find(dashboard, name) for name in
                   ("moonrakerStatusContent", "moonrakerControlsContent")]
        start = len(harness._APPLICATION["messages"])
        for column in columns:
            sections = [child for child in column.childItems() if child.width() > 0]
            for section in sections:
                # Section reordering temporarily removes the visual parent.
                # Width must follow the pane, not dereference that null parent.
                section.setParentItem(None)
                self.pump(3)
                self.assertAlmostEqual(section.width(), column.width(), delta=.5)
                section.setParentItem(column)
                section.setVisible(False)
                self._pump_ms(20)
                section.setVisible(True)
                self._pump_ms(20)
                self.assertAlmostEqual(section.width(), column.width(), delta=0.5)
            self._pump_ms(50)
            shown = sorted((child for child in sections if child.isVisible()), key=lambda child: child.y())
            for previous, following in zip(shown, shown[1:], strict=False):
                self.assertGreaterEqual(following.y() + .5, previous.y() + previous.height())
        from tests.harness.scenarios import CONTROLS_PROBE
        def walk(item, depth=64):
            yield item
            if depth > 0:
                for child in item.childItems():
                    yield from walk(child, depth - 1)
        scope = {"_lookup_windows": lambda: [window], "_walk": walk, "QPointF": harness.QPointF}
        exec(CONTROLS_PROBE, scope)
        self.assertTrue(scope["result"]["clear"], scope["result"])
        self.assertFalse([line for line in harness._APPLICATION["messages"][start:]
                          if "polish loop" in line.lower() or "binding loop" in line.lower()
                          or "TypeError" in line])


class FileManagerOpenBindingTests(harness.RealEngineTestCase):
    def test_fetch_publish_does_not_reenter_the_open_binding(self):
        from tools.capture_filemanager import FileManagerModelStub

        class PublishingModel(FileManagerModelStub):
            fileManagerChanged = harness.pyqtSignal()

            def __init__(self):
                super().__init__()
                self.opened = False
                self.fetches = 0

            @harness.pyqtProperty(bool, notify=fileManagerChanged)
            def fileManagerOpen(self):
                return self.opened

            @harness.pyqtSlot()
            def openFileManager(self):
                self.fetches += 1
                self.fileManagerChanged.emit()

            def publish(self, opened):
                self.opened = opened
                self.fileManagerChanged.emit()

        model = PublishingModel()
        context = self.engine.rootContext()
        context.setContextProperty("openingPrinter", model)
        self.addCleanup(context.setContextProperty, "openingPrinter", None)
        component = harness.QQmlComponent(self.engine)
        component.setData(b"import QtQuick 2.15; Item { width: 900; height: 600; "
                          b"property bool fileManagerOpen: openingPrinter.fileManagerOpen; "
                          b"FileManager { anchors.fill: parent; open: parent.fileManagerOpen; "
                          b"printerModel: openingPrinter } }",
                          harness.QUrl.fromLocalFile(str(harness.qml_source("FileManager.qml").with_name("OpenProbe.qml"))))
        document = component.create()
        self.assertIsNotNone(document, harness.qml_error_report(component))
        window = harness.QQuickWindow()
        window.resize(900, 600)
        document.setParentItem(window.contentItem())
        self.addCleanup(self._destroy_window, document, window)
        window.show()
        self.pump(30)
        start = len(harness._APPLICATION["messages"])
        model.publish(True)
        self._pump_ms(50)
        self.assertEqual(model.fetches, 1)
        model.publish(False)
        self._pump_ms(30)
        model.publish(True)
        self._pump_ms(50)
        self.assertEqual(model.fetches, 2)
        self.assertFalse([line for line in harness._APPLICATION["messages"][start:]
                          if "binding loop" in line.lower()])


class SectionContentSizingTests(harness.RealEngineTestCase):
    def check_section(self, filename, section_id, models):
        section = self.mount(filename)
        window = harness.QQuickWindow()
        window.resize(500, 900)
        section.setParentItem(window.contentItem())
        self.addCleanup(window.deleteLater)
        window.show()
        header = section.childItems()[0]
        start = len(harness._APPLICATION["messages"])
        idle_grid_heights = {}
        for width in (383, 240, 359, 399):
            section.setWidth(width)
            for model in models:
                for expanded in (True, False, True):
                    section.setProperty("printerModel", dict(model, sectionExpandedMap={section_id: expanded}))
                    # A visibility change queues a polish pass. Pumping for
                    # 35 ms left the old download row in macOS's grid height
                    # even though the model was already idle. Measure drawn,
                    # stable geometry before recording or comparing heights.
                    previous = None
                    stable = 0
                    def section_ready(image, section=section):
                        nonlocal previous, stable
                        # Only layout participants: progress-bar ink animates
                        # continuously inside these rows and is not a size cue.
                        items = [section, *section.childItems()]
                        for child in section.childItems():
                            items.extend(child.childItems())
                        grid = section.findChild(harness.QQuickItem, "jobTelemetryGrid")
                        if grid is not None:
                            items.extend(grid.childItems())
                        geometry = tuple((item.x(), item.y(), item.width(), item.height(),
                                          item.isVisible()) for item in items)
                        stable = stable + 1 if geometry == previous else 0
                        previous = geometry
                        return not image.isNull() and stable >= 2
                    frame = self._wait_until(window, section_ready, timeout=3.0)
                    self.assertFalse(frame.isNull(), "section never rendered")
                    self.assertGreaterEqual(stable, 2, "section geometry never settled")
                    self.assertAlmostEqual(header.width(), width, delta=.5)
                    if not expanded:
                        self.assertAlmostEqual(section.height(), header.height(), delta=.5)
                    else:
                        body = section.childItems()[1]
                        if body.isVisible():
                            self.assertGreater(section.height(), header.height())
                            self.assertAlmostEqual(section.height(), body.y() + body.height()
                                                   + section.property("verticalMargin"), delta=.5)
                            self.assertLessEqual(body.x() + body.width(), width)
                            if section_id == "job":
                                grid = self.find(section, "jobTelemetryGrid")
                                if not model.get("improvingEta"):
                                    baseline = idle_grid_heights.setdefault(tuple(sorted(model.items())), grid.height())
                                    self.assertAlmostEqual(grid.height(), baseline, delta=.5,
                                                           msg="available width must not wrap telemetry rows")
                                from PyQt6.QtQml import QQmlExpression
                                for label in grid.findChildren(harness.QQuickItem):
                                    if label.metaObject().indexOfProperty("wrapMode") < 0:
                                        continue
                                    expression = QQmlExpression(harness.QQmlEngine.contextForObject(label),
                                                                label, "Number(wrapMode)")
                                    self.assertEqual(expression.evaluate()[0], 0,
                                                     "telemetry cells must not inherit UM.Label wrapping")
                            if section_id == "job" and model.get("improvingEta"):
                                phase = model["improveEtaPhase"]
                                progress = model["improveEtaProgress"]
                                if progress >= 0:
                                    phase += " %d%%" % round(progress * 100)
                                labels = [item for item in section.findChildren(harness.QObject)
                                          if item.property("text") == phase]
                                self.assertTrue(labels, "the busy phase must be displayed")
                                for label in labels:
                                    from PyQt6.QtQml import QQmlExpression
                                    expression = QQmlExpression(harness.QQmlEngine.contextForObject(label),
                                                                label, "Number(wrapMode)")
                                    self.assertEqual(expression.evaluate()[0], 0,
                                                     "progress must elide, never wrap into another row")
                            if section_id == "job" and model.get("plateSourceStatus"):
                                source = self.find(section, "moonrakerJobSourceProgress")
                                busy = bool(model.get("plateSourceBusy"))
                                self.assertEqual(bool(source.property("busy")), busy)
                                self.assertEqual(source.isVisible(), busy)
                                if busy:
                                    self.assertAlmostEqual(source.property("progress"),
                                                           model["plateSourceProgress"])
                                    percent = (round(model["plateSourceProgress"] * 100)
                                               if model["plateSourceProgress"] >= 0 else None)
                                    phase = ("G-code metadata" if model.get("plateSourceResolving")
                                             else "G-code download")
                                    phase += f" {percent}%" if percent is not None else ""
                                    self.assertTrue([item for item in source.findChildren(harness.QObject)
                                                     if item.property("text") == phase])
        self.assertFalse([line for line in harness._APPLICATION["messages"][start:]
                          if "polish loop" in line.lower() or "binding loop" in line.lower()])

    def test_profiles_arrive_change_and_disappear_without_height_feedback(self):
        base = dict(controlsLocked=False, monitorConnected=True, canApplyTemperaturePreset=True,
                    sectionReason="", sectionReasonDetail="", printActive=False)
        models = [dict(base, temperaturePresetItems=rows) for rows in
                  ([], [{"active": False, "name": "PLA", "index": 0}],
                   [{"active": True, "name": "A longer named profile", "index": 0},
                    {"active": False, "name": "ABS", "index": 1}], [])]
        self.check_section("ProfilesSection.qml", "profiles", models)

    def test_job_telemetry_and_download_rows_keep_the_section_height_content_driven(self):
        base = dict(actionStatus="", actionTimestamp="", filamentRemaining="—", filamentUsed="—",
                    improveEtaPhase="", improveEtaProgress=0, improvingEta=False,
                    monitorAccelLimit="—", monitorConnected=True, monitorElapsed="00:00:01",
                    monitorEta="—", monitorEtaBasis="", monitorFilename="test.gcode", monitorFinish="—",
                    monitorFlow="100%", monitorFlowDiameter="1.75 mm", monitorFlowRate="—",
                    monitorLayer="—", monitorLayerProgress=-1, monitorLayerSource="", monitorMessage="",
                    monitorPositionX="—", monitorPositionY="—", monitorPositionZ="—", monitorProgress=0,
                    monitorSpeed="100%", monitorState="Printing", monitorVelocity="—", nextPauseBaked=False,
                    nextPauseEta="", nextPauseFraction=-1, platePassFraction=-1, printActive=True,
                    printIndexReady=False)
        downloading = [dict(base, improvingEta=True, improveEtaPhase=phase,
                            improveEtaProgress=progress)
                       for phase, progress in (("Resolving", -1), ("Downloading", 0),
                                               ("Downloading", .09), ("Downloading", .99),
                                               ("Downloading", 1), ("Indexing", -1))]
        cached_source = [dict(base, printIndexReady=True,
                              plateSourceStatus="Downloading G-code for precise tracking",
                              plateSourceBusy=True,
                              plateSourceProgress=progress)
                         for progress in (-1, 0, .42, 1)]
        resolving_source = dict(base, printIndexReady=True,
                                plateSourceStatus="Resolving G-code for precise tracking",
                                plateSourceBusy=True, plateSourceResolving=True,
                                plateSourceProgress=-1)
        failed_source = dict(base, printIndexReady=True,
                             plateSourceStatus="G-code download failed; precise tracking unavailable",
                             plateSourceBusy=False, plateSourceProgress=-1)
        paused = dict(base, monitorState="Paused", monitorEta="Paused", monitorElapsed="123:45:56",
                      monitorFinish="Wednesday 23:59 + 12 days", monitorLayer="12345 / 50000",
                      monitorSpeed="50000%", monitorFlow="50000%", filamentUsed="123456.78 m",
                      filamentRemaining="999999.99 m", monitorAccelLimit="100000 mm/s²",
                      monitorVelocity="12345.6 mm/s", monitorFlowRate="-12345.6 mm³/s")
        self.check_section("JobSection.qml", "job", [base, *downloading, resolving_source,
                           *cached_source,
                           failed_source, paused, base,
                           dict(base, printIndexReady=True, monitorLayer="2 / 100",
                           monitorLayerProgress=.3, monitorEta="00:10:00", monitorPositionX="10.0",
                           monitorPositionY="20.0", monitorPositionZ=".4")])


class PreviewLoadingSizeTests(harness.RealEngineTestCase):
    def test_busy_phase_changes_preserve_the_row_and_fill_its_width(self):
        indicator = self.mount("LoadProgressIndicator.qml")
        window = harness.QQuickWindow()
        window.resize(500, 100)
        indicator.setParentItem(window.contentItem())
        self.addCleanup(window.deleteLater)
        window.show()
        content = self.find(indicator, "loadIndicatorContent")
        start = len(harness._APPLICATION["messages"])
        for width in (300, 220, 344):
            indicator.setWidth(width)
            self._pump_ms(30)
            row_height = indicator.height()
            for busy, progress, phase in ((True, -1, "Resolving"), (True, .25, "Downloading"),
                                          (True, -1, "Indexing"), (True, 1, "Rendering"),
                                          (False, -1, ""), (True, .8, "Downloading")):
                indicator.setProperty("phase", phase)
                indicator.setProperty("progress", progress)
                indicator.setProperty("busy", busy)
                # Visibility and text changes schedule Qt Quick polish for
                # the next frame. A fixed 30 ms sleep can inspect old child
                # coordinates on a loaded macOS runner; observe a frame with
                # the complete row contract instead, retaining the deadline.
                def row_ready(_image, busy=busy, width=width):
                    items = [item for item in content.childItems() if item.isVisible() and item.width() > 0]
                    return (content.isVisible() == busy and abs(content.width() - width) <= .5
                            and (not busy or (len(items) == 3 and items[1].width() > 30
                                 and items[-1].x() + items[-1].width() <= width + .5)))
                self._wait_until(window, row_ready, timeout=3.0)
                self.assertAlmostEqual(indicator.height(), row_height, delta=.5)
                self.assertEqual(content.isVisible(), busy)
                self.assertAlmostEqual(content.width(), width, delta=.5)
                if busy:
                    items = [item for item in content.childItems() if item.isVisible() and item.width() > 0]
                    self.assertEqual(len(items), 3)
                    self.assertGreater(items[1].width(), 30, "the progress bar must have usable width")
                    self.assertLessEqual(items[-1].x() + items[-1].width(), width + .5)
        self.assertFalse([line for line in harness._APPLICATION["messages"][start:]
                          if "polish loop" in line.lower() or "binding loop" in line.lower()])
