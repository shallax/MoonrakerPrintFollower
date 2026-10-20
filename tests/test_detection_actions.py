# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify action effects, durable barriers and per-run acknowledgement without UI.

from copy import deepcopy
import unittest
from mpf.detection.DetectionActions import DetectionActions
from mpf.printing.PrintRunIdentity import PrintRunIdentity


class DetectionActionTests(unittest.TestCase):
    def setUp(self):
        self.effects = []
        self.persist = self.send = True
        self.refreshed = (True, True)
        self.gate = ('allowed', '')
        def emit(effect):
            self.effects.append(effect)
            return {'persist': self.persist, 'pause': self.send,
                    'refresh_run': self.refreshed, 'pause_gate': self.gate}.get(effect.kind)
        self.owner = DetectionActions(emit, attempt_id=lambda: 'attempt-1')
        self.identity = PrintRunIdentity('run-a', 1, 'part.gcode', 'binding')
        self.context = ('printer-a', 1, ('part.gcode', 100, 1))

    def consider(self, **kwargs):
        inputs = dict(context=self.context, current_context=self.context, run_identity=self.identity,
                      notice_identity=None, potential_saved_mute=False, level='warning', now=100,
                      pause_enabled=False, notify_enabled=True, attestation_fresh=True, print_state='printing')
        inputs.update(kwargs)
        self.owner.consider(**inputs)

    def kinds(self): return [e.kind for e in self.effects]

    def test_alert_repeat_budget_and_escalation_preserve_unacknowledged_severity(self):
        self.consider()
        self.consider(now=399)
        self.assertEqual(self.kinds().count('notify'), 1)
        self.consider(level='failure', now=101)
        self.assertEqual(self.kinds().count('notify'), 2)
        self.consider(level='warning', now=402)
        self.assertEqual([e.level for e in self.effects if e.kind == 'notify'], ['warning', 'failure', 'failure'])
        self.consider(now=800)
        self.assertEqual(self.kinds().count('notify'), 3)
        self.assertTrue(self.owner.pending_alert(print_state='paused', identity=self.identity))
        self.assertFalse(self.owner.pending_alert(print_state='standby', identity=self.identity))
        self.assertFalse(self.owner.pending_alert(print_state='printing', identity=None))

    def test_acknowledgement_mute_and_rearm_are_durable_transactions(self):
        self.consider()
        self.persist = False
        self.assertFalse(self.owner.acknowledge(101, self.identity))
        self.assertIsNone(self.owner.state.acknowledged_at)
        self.assertFalse(self.owner.mute(True, self.identity))
        self.assertFalse(self.owner.state.muted)
        self.owner.state.paused = True
        previous = deepcopy(self.owner.state)
        self.assertFalse(self.owner.rearm(self.identity))
        self.assertEqual(self.owner.state, previous)
        self.persist = True
        self.assertTrue(self.owner.rearm(self.identity))
        self.assertFalse(self.owner.state.paused)
        self.consider(now=200)
        self.assertTrue(self.owner.acknowledge(201, self.identity))
        self.assertFalse(self.owner.pending_alert(print_state='printing', identity=self.identity))
        before = self.kinds().count('notify')
        self.consider(now=250)
        self.assertEqual(self.kinds().count('notify'), before)
        self.consider(now=291)
        self.assertEqual(self.owner.state.alert_count, 1)
        self.assertTrue(self.owner.mute(True, self.identity))
        self.consider(now=1000)
        self.assertEqual(self.kinds().count('notify'), before + 1)

    def test_pause_is_preceded_by_a_durable_record_and_uncertainty_latches(self):
        self.consider(level='failure', pause_enabled=True, notify_enabled=False)
        self.assertEqual(self.kinds(), ['hide', 'pause_gate', 'persist', 'pause'])
        self.assertTrue(self.effects[-2].record['pausePending'])
        self.assertEqual(self.effects[-1].command_id, 'attempt-1')
        self.consider(level='failure', pause_enabled=True, notify_enabled=False, now=110)
        self.assertEqual(self.kinds().count('pause'), 1)
        event = dict(name='Pause', terminal=True, commandId='attempt-1', outcome='timed_out')
        self.owner.pause_result(event, run_identity=self.identity, run_job=('part', 1), current_job=('part', 2))
        self.assertFalse(self.owner.state.pause_uncertain)
        self.owner.pause_result(event, run_identity=self.identity, run_job=('part', 1), current_job=('part', 1))
        self.assertTrue(self.owner.state.pause_uncertain)
        self.assertTrue(self.owner.state.pause_pending)
        event['outcome'] = 'confirmed'
        self.persist = False
        self.owner.pause_result(event, run_identity=self.identity, run_job=1, current_job=1)
        self.assertTrue(self.owner.state.paused)
        self.assertFalse(self.owner.state.pause_pending)
        self.assertIn('record could not be saved', self.effects[-2].message)

    def test_pause_refusals_and_failed_dispatch_emit_specific_effects(self):
        self.persist = False
        self.consider(level='failure', pause_enabled=True)
        self.assertNotIn('pause', self.kinds())
        self.assertFalse(self.owner.state.pause_pending)
        self.assertIn('durable safety record', self.effects[-1].message)
        self.persist, self.send = True, False
        self.consider(level='failure', pause_enabled=True, notify_enabled=False)
        self.assertTrue(self.owner.state.pause_uncertain)
        self.assertFalse(self.owner.state.pause_pending)
        self.owner.rearm(self.identity)
        self.gate = ('disabled', 'not printing')
        self.consider(level='failure', pause_enabled=True, notify_enabled=False)
        self.assertIn('not printing', self.effects[-1].message)

    def test_stale_attestation_and_context_never_dispatch_pause(self):
        self.refreshed = (False, False)
        self.consider(level='failure', pause_enabled=True, attestation_fresh=False)
        self.assertNotIn('pause_gate', self.kinds())
        self.refreshed = (True, False)
        self.consider(level='failure', pause_enabled=True, attestation_fresh=False)
        self.assertNotIn('pause_gate', self.kinds())
        self.refreshed = (True, True)
        self.consider(level='failure', pause_enabled=True, attestation_fresh=False)
        self.assertIn('pause', self.kinds())
        count = len(self.effects)
        for changes in (dict(context=None), dict(current_context=None),
                        dict(run_identity=None, notice_identity=None),
                        dict(run_identity=None, notice_identity=self.identity, potential_saved_mute=True)):
            self.consider(**changes)
        self.assertEqual(len(self.effects), count)

    def test_saved_latches_are_restored_only_for_the_same_server_run(self):
        self.owner.state.saved = dict(run=self.identity.as_dict(), pausePending=True,
            pauseUncertain=False, muted=True, paused=True, alertedAt=float('nan'),
            acknowledgedAt=100000, alertCount=True, alertLevel='unknown')
        self.owner.sync(('printer-a', self.identity), 100)
        s = self.owner.state
        self.assertTrue(s.pause_uncertain)
        self.assertFalse(s.pause_pending)
        self.assertIsNone(s.alerted_at)
        self.assertIsNone(s.acknowledged_at)
        self.assertEqual((s.alert_count, s.alert_level), (0, ''))
        self.assertTrue(s.muted)
        other = PrintRunIdentity('run-b', 2, 'part.gcode', 'binding')
        self.owner.sync(('printer-a', other), 101)
        self.assertFalse(s.muted)
        self.assertFalse(s.paused)
        self.assertTrue(self.owner.save(None))
        self.owner.state.context = None
        self.assertTrue(self.owner.save(self.identity))

    def test_recent_acknowledgement_blocks_pause_and_nonconfirmed_failure_clears_pending(self):
        self.consider()
        self.owner.acknowledge(101, self.identity)
        self.consider(level='warning', pause_enabled=True, now=102)
        self.assertNotIn('pause', self.kinds())
        self.owner.state.alert_level = 'failure'
        self.consider(level='failure', pause_enabled=True, now=103)
        self.assertNotIn('pause', self.kinds())
        self.owner.rearm(self.identity)
        self.consider(level='failure', pause_enabled=True, notify_enabled=False)
        self.owner.pause_result(dict(name='Pause', terminal=True, commandId='attempt-1', outcome='refused'),
                                run_identity=self.identity, run_job=1, current_job=1)
        self.assertFalse(self.owner.state.pause_pending)

    def test_printer_and_run_changes_own_transient_state_and_copy_saved_values(self):
        self.consider(level='failure', pause_enabled=True)
        saved = {'run': self.identity.as_dict(), 'pausePending': True}
        self.owner.load_printer(saved)
        saved['pausePending'] = False
        self.assertTrue(self.owner.state.saved['pausePending'])
        self.assertIsNone(self.owner.state.context)
        self.assertFalse(self.owner.state.pause_pending)
        self.owner.run_changed(active=False, potential_saved_mute=True)
        self.assertFalse(self.owner.state.muted)
        self.owner.run_changed(potential_saved_mute=True)
        self.assertTrue(self.owner.state.muted)

    def test_reentrant_attestation_refresh_cannot_dispatch_two_pause_requests(self):
        emit = self.owner._emit
        def refreshing(effect):
            if effect.kind == 'refresh_run':
                self.consider(level='failure', pause_enabled=True, notify_enabled=False)
                return True, True
            return emit(effect)
        self.owner._emit = refreshing
        self.consider(level='failure', pause_enabled=True, notify_enabled=False, attestation_fresh=False)
        self.assertEqual(self.kinds().count('pause'), 1)
