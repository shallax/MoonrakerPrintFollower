# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Prove temperature intent ordering across both real command lanes.

"""A pending one-shot must not undo a newer heater adjustment."""
import unittest
from types import SimpleNamespace
from tests.test_monitor_controls import QT_AVAILABLE, record

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, pyqtSignal
    from tests.test_monitor_controls import Data, BedMesh
    from mpf.monitor.controls.MonitorControls import MonitorControls
    from mpf.monitor.controls.MonitorCommands import MonitorCommands
    from mpf.monitor.controls.MonitorTuning import MonitorTuning

    class TransportData(Data):
        commandChanged = pyqtSignal(object)
        connected = True

        def __init__(self):
            super().__init__(observation=record())
            self.requests = []

        def set_commands_busy(self, busy): pass
        def later(self, *args): pass
        def refresh_all(self): pass
        def request(self, channel, method, path, callback, **kwargs):
            self.requests.append(SimpleNamespace(channel=channel, path=path, callback=callback, **kwargs))
            return True


@unittest.skipUnless(QT_AVAILABLE, 'Qt required')
class HeaterIntentOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.data = TransportData()
        self.commands = MonitorCommands(self.data)
        self.tuning = MonitorTuning(self.data, self.commands)
        self.controls = MonitorControls(self.data, self.commands, self.tuning, BedMesh(), {})
        self.addCleanup(self.tuning.reset)
        self.addCleanup(self.commands.reset)
        self.data.rebuild(auxiliary={
            'configfile': {'settings': {'extruder': {'min_temp': 5, 'max_temp': 285}}},
            'extruder': {'target': 200}}, presets={'presets': {
                'pla': {'name': 'PLA', 'values': {'extruder': {'bool': True, 'value': 215}}}}})
        self.commands.script('Home', 'G28')

    def finish_home(self):
        self.data.requests[0].callback({'result': 'ok'}, None)

    def scripts(self):
        return [r.body['script'] for r in self.data.requests]

    def test_queued_profile_cannot_reheat_after_newer_individual_off(self):
        self.controls.apply_preset(0)
        self.controls.set_heater_temperature('extruder', 0)
        self.tuning._send('heater:extruder')
        self.finish_home()
        self.assertEqual(self.scripts(), ['G28', 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0'])
        self.assertIn('superseded', self.commands.status)

    def test_queued_cooldown_cannot_undo_newer_individual_target(self):
        self.controls.heaters_off()
        self.controls.set_heater_temperature('extruder', 230)
        self.finish_home()
        self.tuning._send('heater:extruder')
        self.assertEqual(self.scripts(), ['G28', 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=230'])

    def test_newer_group_replaces_older_group_and_pending_bump(self):
        self.controls.apply_preset(0)
        self.controls.set_heater_temperature('extruder', 225)
        self.controls.heaters_off()
        self.tuning._send('heater:extruder')
        self.finish_home()
        self.assertEqual(self.scripts(), ['G28', 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0'])

    def test_newer_profile_replaces_queued_cooldown(self):
        self.controls.heaters_off()
        self.controls.apply_preset(0)
        self.finish_home()
        self.assertEqual(self.scripts(), ['G28', 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=215'])

    def test_invalid_individual_intent_does_not_cancel_valid_profile(self):
        self.controls.apply_preset(0)
        self.controls.set_heater_temperature('extruder', 999)
        self.controls.set_heater_temperature('missing', 0)
        self.finish_home()
        self.assertEqual(self.scripts(), ['G28', 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=215'])

    def test_queued_profile_rechecks_lock(self):
        self.controls.apply_preset(0)
        self.data.observation = record(controls_locked=True)
        self.finish_home()
        self.assertEqual(self.scripts(), ['G28'])

    def test_new_profile_clears_pending_adjustment_but_preserves_other_tuning(self):
        self.controls.set_heater_temperature('extruder', 225)
        self.controls.factor('speed', 125)
        self.controls.apply_preset(0)
        self.tuning._send('heater:extruder')
        self.tuning._send('speed-factor')
        self.finish_home()
        self.assertEqual(self.scripts(), ['G28', 'M220 S125', 'SET_HEATER_TEMPERATURE HEATER=extruder TARGET=215'])
