# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify history admission, clock freshness and printer/job fences without UI.

from dataclasses import replace
import unittest
from mpf.detection.DetectionRun import DetectionRun, DetectionRunInput


class DetectionRunTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.0
        self.input = DetectionRunInput(('printer-a', 1, ('part.gcode', 100, 1), 'http://printer', True), 'part.gcode', True)
        self.requests, self.transitions = [], []
        self.started = True
        def request(*args):
            self.requests.append(args)
            return self.started
        self.owner = DetectionRun(current=lambda: self.input, request=request,
            changed=self.transitions.append, monotonic=lambda: self.now, wall_time=lambda: 1000)

    def reply(self, *, job='run-a', error=''):
        self.requests[-1][-1]({'result': {'jobs': [dict(job_id=job, start_time=1000,
            filename='part.gcode', status='in_progress', end_time=None)]}}, error)

    def test_freshness_and_metadata_cadence_do_not_refresh_on_every_frame(self):
        self.owner.ensure()
        self.assertEqual(self.transitions[-1].kind, 'reset')
        self.assertEqual(self.owner.notice_identity.filename, 'part.gcode')
        self.owner.ensure(force=True)
        self.assertEqual(len(self.requests), 1)
        self.reply()
        self.assertEqual(self.transitions[-1].kind, 'admitted')
        self.assertTrue(self.owner.fresh(2))
        self.owner.ensure()
        self.assertEqual(len(self.requests), 1)
        self.now = 13
        self.assertFalse(self.owner.fresh(2))
        self.owner.ensure(force=True)
        self.reply()
        self.assertEqual(self.transitions[-1].previous, self.owner.identity)
        self.assertTrue(self.owner.fresh(2))
        self.owner.attested_at = 14
        self.assertFalse(self.owner.fresh(2))

    def test_delayed_attestation_cannot_authorize_an_immediate_pause(self):
        self.owner.ensure()
        self.now = 13
        self.reply()
        self.assertFalse(self.owner.fresh(2))
        self.assertEqual(self.owner.retry_at, 18)
        self.owner.ensure(force=True)
        self.assertEqual(len(self.requests), 1)
        self.now = 18
        self.owner.ensure(force=True)
        self.assertEqual(len(self.requests), 2)

    def test_replaced_job_and_closed_tracker_ignore_late_replies(self):
        self.owner.ensure()
        stale = self.requests[-1][-1]
        key = ('printer-a', 1, ('other.gcode', 100, 2), 'http://printer', True)
        self.input = replace(self.input, key=key, filename='other.gcode')
        self.owner.ensure()
        count = len(self.transitions)
        stale({}, '')
        self.assertEqual(len(self.transitions), count)
        self.owner.close()
        self.requests[-1][-1]({}, '')
        self.owner.ensure()
        self.assertEqual(len(self.transitions), count)
        self.assertIsNone(self.owner.context)

    def test_retired_history_cannot_be_reused_for_a_new_local_run(self):
        self.owner.ensure()
        self.reply()
        old = self.owner.identity
        self.input = replace(self.input, key=('printer-a', 1, ('part.gcode', 100, 2), 'http://printer', True))
        self.owner.ensure()
        self.assertEqual(self.owner.retired_history, old)
        self.reply()
        self.assertIsNone(self.owner.identity)
        self.assertEqual(self.transitions[-1].kind, 'unresolved')
        self.now = 15
        self.owner.ensure(force=True)
        self.reply(job='run-b')
        self.assertEqual(self.owner.identity.job_id, 'run-b')

    def test_errors_revoke_freshness_and_explicit_missing_history_revokes_identity(self):
        self.owner.ensure()
        self.reply()
        old = self.owner.identity
        self.now = 20
        self.owner.ensure()
        self.reply(error='offline')
        self.assertEqual(self.owner.identity, old)
        self.assertIsNone(self.owner.attested_at)
        self.assertEqual(self.transitions[-1].kind, 'unresolved')
        self.now = 25
        self.owner.ensure(force=True)
        self.requests[-1][-1]({}, '')
        self.assertIsNone(self.owner.identity)
        self.assertEqual(self.transitions[-1].kind, 'lost')

    def test_ineligible_context_and_refused_request_have_no_pending_job(self):
        self.input = replace(self.input, eligible=False)
        self.owner.ensure()
        self.assertEqual(self.requests, [])
        self.input = replace(self.input, eligible=True)
        self.started = False
        self.owner.ensure()
        self.assertFalse(self.owner.pending)
        self.assertFalse(self.owner.fresh(2))
