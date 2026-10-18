# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation

"""Mouse-driven temperature controls connected to real control and tuning owners."""
import unittest
from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtCore import QPoint, QPointF, Qt, pyqtProperty, pyqtSignal, pyqtSlot
    from PyQt6.QtTest import QTest
    from tests.test_monitor_controls import Data, Commands, BedMesh, record
    from mpf.monitor.controls.MonitorControls import MonitorControls
    from mpf.monitor.controls.MonitorTuning import MonitorTuning

    class HeaterPrinter(harness.PrinterModelDouble):
        changed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.data = Data(observation=record(state="printing"))
            self.data.refresh_all = lambda: None
            self.data.later = lambda *args: None
            self.commands = Commands()
            self.sent = []
            self.commands.quick = lambda channel, script, callback: self.sent.append(script) or True
            self.tuning = MonitorTuning(self.data, self.commands)
            self.mesh = BedMesh()
            self.controls = MonitorControls(self.data, self.commands, self.tuning, self.mesh, {})
            self.controls.changed.connect(self.changed.emit)
            self.data.rebuild(auxiliary={
                "configfile": {"settings": {
                    "extruder": {"min_temp": 5, "max_temp": 285},
                    "heater_bed": {"min_temp": 5, "max_temp": 120}}},
                "extruder": {"target": 210}, "heater_bed": {"target": 60}})
        @pyqtProperty("QVariant", notify=changed)
        def heaterControlItems(self): return self.controls.values["heaterControlItems"]
        @pyqtProperty(bool, notify=changed)
        def canSetHeaterTemperature(self): return self.controls.values["canSetHeaterTemperature"]
        @pyqtProperty(bool, notify=changed)
        def controlsLocked(self): return self.data.observation.controls_locked
        @pyqtProperty("QVariant", notify=changed)
        def temperaturePresetItems(self): return self.controls.values["temperaturePresetItems"]
        @pyqtProperty(bool, notify=changed)
        def canApplyTemperaturePreset(self): return self.controls.values["canApplyTemperaturePreset"]
        @pyqtSlot(int)
        def applyTemperaturePreset(self, index): self.controls.apply_preset(index)
        @pyqtSlot()
        def heatersOff(self): self.controls.heaters_off()
        @pyqtSlot(str, float)
        def setHeaterTemperature(self, name, target):
            self.controls.set_heater_temperature(name, target)


@unittest.skipUnless(harness.QT_AVAILABLE, "Qt required")
class HeaterUiTests(harness.RealEngineTestCase):
    def find(self, document, name):
        pending = [document]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        self.fail("Missing visual control: " + name)

    def setup_panel(self, width=460):
        printer = HeaterPrinter()
        self.addCleanup(printer.tuning.reset)
        document, window = self.mount_window("ProfilesSection.qml", width, 650)
        document.setProperty("printerModel", printer)
        self.pump(20)
        window.grabWindow()
        return printer, document, window

    def click(self, window, item, fraction=.5):
        window.grabWindow()
        self.assertTrue(item.isEnabled())
        point = item.mapToItem(window.contentItem(), QPointF(item.width()*fraction, item.height()/2))
        self.assertTrue(0 <= point.x() < window.width() and 0 <= point.y() < window.height())
        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         QPoint(round(point.x()), round(point.y())))
        self.pump(20)
        window.grabWindow()

    def test_steps_rapid_bumps_off_and_limits_reach_the_command_owner(self):
        printer, document, window = self.setup_panel()
        picker = self.find(document, "heaterStepPicker")
        for index, step in enumerate((1, 5, 10, 25)):
            picker.setProperty("currentIndex", index)
            printer.data.rebuild(auxiliary={**printer.data.snapshot.auxiliary,
                                           "extruder": {"target": 210}})
            printer.tuning.reset()
            self.pump(20)
            self.click(window, self.find(document, "heaterPlus_extruder"))
            self.click(window, self.find(document, "heaterPlus_extruder"))
            self.assertEqual(printer.tuning.value("heater:extruder", 0), 210 + 2*step)
            self.click(window, self.find(document, "heaterMinus_extruder"))
            self.assertEqual(printer.tuning.value("heater:extruder", 0), 210 + step)
            printer.tuning._send("heater:extruder")
            self.assertEqual(printer.sent[-1], f"SET_HEATER_TEMPERATURE HEATER=extruder TARGET={210+step}")
        self.click(window, self.find(document, "heaterOff_extruder"))
        self.assertEqual(printer.tuning.value("heater:extruder", -1), 0)
        self.click(window, self.find(document, "heaterPlus_extruder"))
        self.assertEqual(printer.tuning.value("heater:extruder", -1), 25)
        printer.controls.set_heater_temperature("extruder", 280)
        self.pump(20)
        self.click(window, self.find(document, "heaterPlus_extruder"))
        self.assertEqual(printer.tuning.value("heater:extruder", -1), 285)
        self.assertFalse(self.find(document, "heaterPlus_extruder").isEnabled())

    def test_slider_track_click_at_narrow_width_and_lock_before_dispatch(self):
        printer, document, window = self.setup_panel(340)
        slider = self.find(document, "heaterSlider_heater_bed")
        self.click(window, slider, .8)
        target = printer.tuning.value("heater:heater_bed", -1)
        self.assertGreater(target, 60)
        self.assertLessEqual(target, 120)
        printer.data.observation = record(controls_locked=True)
        printer.data.changed.emit()
        printer.tuning._send("heater:heater_bed")
        self.assertEqual(printer.sent, [])
        self.pump(20)
        self.assertFalse(self.find(document, "heaterSlider_heater_bed").isEnabled())

    def test_poll_during_drag_does_not_destroy_the_pressed_slider(self):
        printer, document, window = self.setup_panel()
        slider = self.find(document, "heaterSlider_extruder")
        point = slider.mapToItem(window.contentItem(), QPointF(slider.width()*.7, slider.height()/2))
        pos = QPoint(round(point.x()), round(point.y()))
        QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
        self.pump(10)
        printer.data.rebuild(auxiliary={**printer.data.snapshot.auxiliary,
                                       "heater_bed": {"target": 65}})
        self.pump(10)
        self.assertIs(self.find(document, "heaterSlider_extruder"), slider)
        QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           QPoint(pos.x()-40, pos.y()))
        self.pump(20)
        self.assertFalse(document.property("adjustingHeater"))
        self.assertIn("heater:extruder", printer.tuning._pending)

    def test_changed_limits_reject_a_queued_target_before_it_reaches_printer(self):
        printer, document, window = self.setup_panel()
        self.click(window, self.find(document, "heaterPlus_extruder"))
        auxiliary = dict(printer.data.snapshot.auxiliary)
        auxiliary["configfile"] = {"settings": {
            "extruder": {"min_temp": 5, "max_temp": 200}}}
        printer.data.rebuild(auxiliary=auxiliary)
        printer.tuning._send("heater:extruder")
        self.assertEqual(printer.sent, [])
        self.assertNotIn("heater:extruder", printer.tuning._pending)

    def test_minimum_and_off_are_distinct_and_changing_step_sends_nothing(self):
        printer, document, window = self.setup_panel()
        picker = self.find(document, "heaterStepPicker")
        picker.forceActiveFocus()
        QTest.keyClick(window, Qt.Key.Key_End)
        self.pump(20)
        self.assertEqual(document.property("temperatureStep"), 25)
        self.assertEqual(printer.tuning._pending, {})
        picker.setProperty("currentIndex", 0)
        self.click(window, self.find(document, "heaterOff_extruder"))
        self.click(window, self.find(document, "heaterPlus_extruder"))
        self.assertEqual(printer.tuning.value("heater:extruder", -1), 5)
        self.click(window, self.find(document, "heaterMinus_extruder"))
        self.assertEqual(printer.tuning.value("heater:extruder", -1), 0)

    def test_profile_mouse_click_dispatches_during_printing(self):
        printer, document, window = self.setup_panel()
        printer.data.rebuild(presets={"presets": {"pla": {
            "name": "PLA", "values": {"extruder": {"bool": True, "value": 215},
                                    "heater_bed": {"bool": True, "value": 60}}}}})
        self.pump(20)
        window.grabWindow()
        pending = [document]
        buttons = []
        while pending:
            item = pending.pop()
            if item.property("text") == "PLA":
                buttons.append(item)
            pending.extend(item.childItems())
        button = next(item for item in buttons if item.metaObject().indexOfSignal(b"clicked()") >= 0)
        self.click(window, button)
        self.assertEqual(printer.commands.calls[-1][2],
                         "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=215"
                         "\nSET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=60")
