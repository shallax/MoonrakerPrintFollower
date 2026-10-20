# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Exercise recovery workflows without Qt or a Monitor facade.

from copy import deepcopy
from types import SimpleNamespace as Values
import unittest

from mpf.application.FollowerSelection import FollowerSelection, coerce_anchor
from mpf.application.RecoverySources import RecoverySources
from mpf.application.RunoutPause import RunoutPause


class FollowerSelectionTests(unittest.TestCase):
    def setUp(self):
        self.anchors, self.splits = [], []
        self.selection = FollowerSelection(anchor=self.anchors.append, split=self.splits.append)

    def test_detach_on_zero_retains_live_split_and_new_job_reattaches(self):
        owner = self.selection
        self.assertTrue(owner.set_attached(False, live_anchor=0, live_split=3))
        self.assertEqual((owner.anchor, owner.split, owner.attached), (0, 3, False))
        self.assertEqual((self.anchors, self.splits), ([0], [3]))
        owner.observe_job(('part.gcode', 100, 1))
        self.assertTrue(owner.attached)
        self.assertEqual((owner.anchor, owner.split), (-1, None))
        self.assertEqual((self.anchors[-1], self.splits[-1]), (None, None))
        owner.observe_job(owner.job)
        self.assertEqual(len(self.anchors), 2)

    def test_detach_fallbacks_and_missing_index_refusal(self):
        owner = self.selection
        self.assertFalse(owner.set_attached(True))
        self.assertFalse(owner.set_attached(False))
        self.assertTrue(owner.attached)
        self.assertTrue(owner.set_attached(False, presented=2, live_split=4))
        self.assertIsNone(owner.split)
        owner.set_attached(True)
        self.assertTrue(owner.set_attached(False, index_ready=True))
        self.assertEqual(owner.anchor, 0)
        owner.set_attached(True)
        self.assertTrue(owner.set_attached(False, layer_count=1))
        self.assertEqual(owner.anchor, 0)
        owner.anchor = 4
        owner.set_attached(True)
        owner.anchor = 4
        self.assertTrue(owner.set_attached(False, live_anchor=2))
        self.assertEqual(owner.anchor, 4)

    def test_seek_and_scrub_have_distinct_full_layer_and_motion_boundaries(self):
        owner = self.selection
        for invalid in (-1, None, 'bad'):
            self.assertFalse(owner.seek(invalid))
        self.assertTrue(owner.seek(5))
        self.assertEqual((owner.anchor, owner.split), (5, -1))
        self.assertTrue(owner.seek(5))
        self.assertEqual(len(self.anchors), 1)
        self.assertTrue(owner.scrub(100, total=12, live_anchor=2))
        self.assertEqual((owner.anchor, owner.split), (5, 12))
        self.assertTrue(owner.scrub(12, total=12, live_anchor=2))
        self.assertFalse(owner.scrub('bad', total=12, live_anchor=2))
        owner.set_attached(True)
        self.assertFalse(owner.scrub(2, total=12, live_anchor=-1))
        self.assertTrue(owner.scrub(-1, total=12, live_anchor=0))
        self.assertEqual((owner.anchor, owner.split), (0, 0))
        self.assertEqual([coerce_anchor(v) for v in (None, 'bad', '0', 3)], [-1, -1, 0, 3])


class Requests:
    def __init__(self):
        self.calls = []
        self.accept = True
        self.notifications = []

    def send(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.accept

    def reply(self, payload=None, error=None):
        self.calls[-1][0][4](payload, error)

    def changed(self):
        self.notifications.append(True)


class RecoverySourcesTests(unittest.TestCase):
    def setUp(self):
        self.requests = Requests()
        self.owner = RecoverySources(send=self.requests.send, max_bytes=lambda: 100,
                                     changed=self.requests.changed)

    def test_filter_sort_selection_and_copied_presentation(self):
        owner = self.owner
        self.assertFalse(owner.refresh(active=True, configured=True))
        self.assertFalse(owner.refresh(active=False, configured=False))
        self.assertTrue(owner.refresh(active=False, configured=True))
        rows = [None, {}, {'filename': 'bad.gcode', 'size': 'bad'},
                {'filename': 'sub/a.gcode', 'size': 1}, {'filename': 'a;bad.gcode', 'size': 1},
                {'filename': 'oversize.gcode', 'size': 101}, {'filename': 'a.stl', 'size': 1},
                {'filename': 'old.gcode', 'size': 10, 'modified': 20},
                {'filename': 'recent.gcode', 'size': 20, 'print_start_time': 10},
                {'filename': 'new.gcode', 'size': 20, 'modified': 30, 'print_start_time': 'bad'},
                {'filename': 'nan.gcode', 'size': 20, 'modified': float('nan')}]
        self.requests.reply({'result': {'files': rows}})
        self.assertEqual([r['name'] for r in owner.options],
                         ['recent.gcode', 'new.gcode', 'old.gcode', 'nan.gcode'])
        self.assertIsNone(owner.select('recent.gcode', active=True, recovering=False))
        self.assertIsNone(owner.select('recent.gcode', active=False, recovering=True))
        self.assertIsNone(owner.select('missing.gcode', active=False, recovering=False))
        self.assertEqual(owner.select('recent.gcode', active=False, recovering=False), ('recent.gcode', 20, 0))
        state = owner.state(active=False)
        state['options'][0]['name'] = 'changed'
        self.assertEqual(owner.options[0]['name'], 'recent.gcode')
        owner.observe_active(False)
        self.assertEqual(owner.job[0], 'recent.gcode')
        owner.observe_active(True)
        self.assertIsNone(owner.job)

    def test_stale_error_failed_send_and_shutdown(self):
        owner = self.owner
        owner.refresh(active=False, configured=True)
        old = self.requests.calls[-1][0][4]
        owner.reset()
        old({'result': {'files': [{'filename': 'old.gcode', 'size': 1}]}}, None)
        self.assertEqual(owner.options, [])
        self.assertFalse(owner.loading)
        owner.refresh(active=False, configured=True)
        self.requests.reply({}, 'offline')
        self.assertIn('Could not list', owner.error)
        owner.refresh(active=False, configured=True)
        self.requests.reply({'result': {'files': []}})
        self.assertIn('No root G-code', owner.error)
        self.requests.accept = False
        self.assertFalse(owner.refresh(active=False, configured=True))
        self.assertIn('Could not request', owner.error)
        self.requests.accept = True
        owner.refresh(active=False, configured=True)
        owner.close()
        before = owner.state(active=False)
        self.requests.reply({'result': {'files': [{'filename': 'late.gcode', 'size': 1}]}})
        self.assertEqual(owner.state(active=False), before)


class RunoutPauseTests(unittest.TestCase):
    def setUp(self):
        self.requests = Requests()
        self.job = ('part.gcode', 100, 1)
        self.sensor = 'filament_switch_sensor runout'
        self.snapshot = Values(job_key=self.job, observation=Values(state='printing'))
        self.config = Values(recovery_auto_pause=True)
        self.connected = True
        self.owner = RunoutPause(send=self.requests.send, connected=lambda: self.connected,
                                 config=lambda: self.config, print_state=lambda: self.snapshot,
                                 changed=self.requests.changed)
        self.event = Values(job_key=self.job, sensor=self.sensor)
        self.status = {'print_stats': {'state': 'printing', 'filename': self.job[0]},
                       'virtual_sdcard': {'file_size': 100}, 'pause_resume': {'is_paused': False},
                       'configfile': {'config': {self.sensor: {'pause_on_runout': 'false'}}},
                       self.sensor: {'filament_detected': False, 'enabled': True}}

    def request(self):
        self.owner.request(self.event, self.snapshot)

    def checked(self, status=None):
        self.requests.reply({'result': {'status': status if status is not None else self.status}})

    def test_verified_pause_uncertainty_and_job_fences(self):
        self.assertTrue(self.owner.bind(self.job))
        self.assertFalse(self.owner.bind(self.job))
        self.request()
        self.assertTrue(self.owner.pending)
        self.checked()
        args, kwargs = self.requests.calls[-1]
        self.assertEqual(args[3], 'printer/gcode/script')
        self.assertEqual(kwargs['body'], {'script': 'PAUSE'})
        self.requests.reply({}, 'timeout')
        self.assertIn('uncertain', self.owner.note)
        self.request()
        stale = self.requests.calls[-1][0][4]
        self.owner.bind(('other.gcode', 100, 2))
        stale({'result': {'status': self.status}}, None)
        self.assertEqual(len(self.requests.calls), 3)
        self.request()
        self.checked()
        command = self.requests.calls[-1][0][4]
        self.owner.invalidate()
        note = self.owner.note
        command({}, None)
        self.assertEqual(self.owner.note, note)
        self.request()
        self.checked()
        self.requests.reply({}, None)
        self.assertIn('waiting for Klipper', self.owner.note)

    def test_disabled_disconnected_and_pending_do_not_send(self):
        self.config.recovery_auto_pause = False
        self.request()
        self.assertIn('off', self.owner.note)
        self.config.recovery_auto_pause = True
        self.connected = False
        self.request()
        self.connected = True
        self.snapshot.observation.state = 'paused'
        self.request()
        self.snapshot.observation.state = 'printing'
        self.owner.pending = True
        self.request()
        self.assertEqual(self.requests.calls, [])

    def test_each_fresh_status_refusal_and_failed_transports(self):
        for key, value, note in [
            ('configfile', {'config': {self.sensor: {'pause_on_runout': 'true'}}}, 'Klipper owns'),
            ('configfile', {}, 'unavailable'),
            (self.sensor, {'filament_detected': True, 'enabled': True}, 'Runout changed'),
            (self.sensor, [], 'Runout changed'),
            ('print_stats', {'state': 'paused', 'filename': self.job[0]}, 'print changed'),
            ('virtual_sdcard', {'file_size': 101}, 'print changed'),
            ('pause_resume', {'is_paused': True}, 'print changed'),
        ]:
            with self.subTest(key=key, value=value):
                self.request()
                status = deepcopy(self.status)
                status[key] = value
                self.checked(status)
                self.assertIn(note, self.owner.note)
                self.assertEqual(self.requests.calls[-1][0][3], 'printer/objects/query')
        self.request()
        self.requests.reply({}, 'network error')
        self.assertIn('check failed', self.owner.note)
        self.requests.accept = False
        self.request()
        self.assertIn('could not start', self.owner.note)
        self.requests.accept = True
        self.request()
        self.requests.accept = False
        self.checked()
        self.assertIn('could not be submitted', self.owner.note)
