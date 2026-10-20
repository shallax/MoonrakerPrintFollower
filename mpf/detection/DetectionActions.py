# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own per-run detection actions and emit explicit persistence/command/UI effects.

"""Per-run failure-detection actions; no inference, Qt or host notifications."""
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from uuid import uuid4

ALERT_REPEAT_SECONDS = 300.0
ALERT_MAX_PER_PRINT = 3


@dataclass(frozen=True)
class DetectionEffect:
    kind: str
    message: str = ''
    context: object = None
    level: str = ''
    command_id: str = ''
    record: object = None


@dataclass
class ActionState:
    context: object = None
    saved: object = None
    muted: bool = False
    pause_attempt: object = None
    alerted_at: object = None
    acknowledged_at: object = None
    alert_count: int = 0
    alert_level: str = ''
    paused: bool = False
    pause_pending: bool = False
    pause_uncertain: bool = False


class DetectionActions:
    def __init__(self, emit, *, attempt_id=lambda: uuid4().hex):
        self._emit = emit
        self._attempt_id = attempt_id
        self.state = ActionState(saved={})

    def load_printer(self, saved):
        s = self.state
        s.context = None
        s.alerted_at = s.acknowledged_at = None
        s.paused = s.pause_pending = s.pause_uncertain = False
        s.saved = deepcopy(saved)

    def run_changed(self, *, active=True, potential_saved_mute=False):
        self.state.context = None
        self.state.muted = bool(active and potential_saved_mute)

    def _effect(self, kind, **values):
        return self._emit(DetectionEffect(kind, **values))

    def pending_alert(self, *, print_state, identity):
        s = self.state
        if (s.context is None or print_state not in ('printing', 'paused')
                or identity != s.context[1] or s.muted):
            return False
        return s.alerted_at is not None and (s.acknowledged_at is None or s.acknowledged_at < s.alerted_at)

    def sync(self, print_key, now):
        s = self.state
        if print_key == s.context:
            return
        self._effect('hide')
        s.context = print_key
        s.pause_pending = s.pause_uncertain = False
        saved = s.saved
        if isinstance(saved, dict) and saved.get('run') == print_key[1].as_dict():
            alerted, acknowledged, count = saved.get('alertedAt'), saved.get('acknowledgedAt'), saved.get('alertCount')
            s.alerted_at = alerted if type(alerted) in (int, float) and 0 <= alerted <= now and isfinite(alerted) else None
            s.acknowledged_at = acknowledged if type(acknowledged) in (int, float) and 0 <= acknowledged <= now and isfinite(acknowledged) else None
            s.alert_count = count if type(count) is int and 0 <= count <= ALERT_MAX_PER_PRINT else 0
            s.alert_level = saved.get('alertLevel') if saved.get('alertLevel') in ('warning', 'failure') else ''
            s.paused = saved.get('paused') is True
            s.pause_uncertain = saved.get('pausePending') is True or saved.get('pauseUncertain') is True
            s.pause_attempt = saved.get('pauseAttempt')
            s.muted = saved.get('muted') is True
        else:
            s.alerted_at = s.acknowledged_at = None
            s.alert_count = 0
            s.alert_level = ''
            s.paused = s.muted = False

    def save(self, run_identity):
        s = self.state
        if s.context is None or run_identity is None:
            return True
        record = dict(run=s.context[1].as_dict(), muted=s.muted, pausePending=s.pause_pending,
                      pauseUncertain=s.pause_uncertain, pauseAttempt=s.pause_attempt,
                      alertedAt=s.alerted_at, acknowledgedAt=s.acknowledged_at,
                      alertCount=s.alert_count, alertLevel=s.alert_level, paused=s.paused)
        if not self._effect('persist', context=s.context, record=record):
            return False
        s.saved = record
        return True

    def consider(self, *, context, current_context, run_identity, notice_identity,
                 potential_saved_mute, level, now, pause_enabled, notify_enabled,
                 attestation_fresh, print_state):
        if context is None or context != current_context:
            return
        identity = run_identity or notice_identity
        if identity is None or (run_identity is None and potential_saved_mute):
            return
        self.sync((context[0], identity), now)
        s = self.state
        if s.muted or level not in ('warning', 'failure'):
            return
        escalated = level == 'failure' and s.alert_level == 'warning'
        pause_candidate = (run_identity is not None and level == 'failure' and pause_enabled
                           and not s.paused and not s.pause_pending and not s.pause_uncertain)
        if pause_candidate and not attestation_fresh:
            context_current, attestation_fresh = self._effect('refresh_run', context=context)
            if not context_current:
                return
            # Refresh may synchronously admit a result and request its pause.
            # Re-read the latch before this caller can dispatch the same action.
            pause_candidate = not s.paused and not s.pause_pending and not s.pause_uncertain
        if pause_candidate and attestation_fresh:
            if not escalated and s.acknowledged_at is not None and now - s.acknowledged_at < 90:
                return
            mode, reason = self._effect('pause_gate')
            if mode == 'allowed':
                s.pause_pending = True
                s.pause_attempt = self._attempt_id()
                if not self.save(run_identity):
                    s.pause_pending = False
                    self._effect('status', message='Automatic pause blocked: its durable safety record could not be saved')
                    return
                if not self._effect('pause', command_id=s.pause_attempt):
                    s.pause_pending = False
                    s.pause_uncertain = True
                    self.save(run_identity)
                    self._effect('status', message='Automatic pause was not sent; check the printer and pause manually if needed')
            else:
                self._effect('status', message=f'Automatic pause was not sent: {reason}')
        if not notify_enabled:
            return
        if not escalated and s.acknowledged_at is not None and now - s.acknowledged_at < 90:
            return
        if self.pending_alert(print_state=print_state, identity=identity) and s.alert_level == 'failure':
            level = 'failure'
        if s.alerted_at is not None and not escalated:
            unacknowledged = s.acknowledged_at is None or s.acknowledged_at < s.alerted_at
            if unacknowledged:
                if now - s.alerted_at < ALERT_REPEAT_SECONDS or s.alert_count >= ALERT_MAX_PER_PRINT:
                    return
            elif now - s.alerted_at < 90:
                return
        if s.acknowledged_at is not None and s.alerted_at is not None and s.acknowledged_at >= s.alerted_at:
            s.alert_count = 0
        s.alerted_at, s.alert_level = now, level
        s.alert_count = min(ALERT_MAX_PER_PRINT, s.alert_count + 1)
        self.save(run_identity)
        self._effect('changed')
        self._effect('retain', level=level, context=context)
        self._effect('notify', level=level)

    def pause_result(self, event, *, run_identity, run_job, current_job):
        s = self.state
        if (not s.pause_pending or event.get('name') != 'Pause' or not event.get('terminal')
                or event.get('commandId') != s.pause_attempt or s.context is None
                or run_identity != s.context[1] or run_job is None or current_job != run_job):
            return
        if event.get('outcome') == 'confirmed':
            s.pause_pending = False
            s.paused = True
            if not self.save(run_identity):
                self._effect('status', message='Print paused, but the one-pause-per-print record could not be saved')
        elif event.get('outcome') == 'timed_out' or str(event.get('detail', '')).startswith('outcome unknown:'):
            s.pause_uncertain = True
            self.save(run_identity)
            self._effect('status', message='Automatic pause unconfirmed; check the printer and pause manually if needed')
        else:
            s.pause_pending = False
            self.save(run_identity)
        self._effect('changed')

    def acknowledge(self, now, run_identity):
        previous = self.state.acknowledged_at
        self.state.acknowledged_at = now
        if not self.save(run_identity):
            self.state.acknowledged_at = previous
            self._effect('status', message='Could not save acknowledgement for this print')
            return False
        self._effect('hide')
        self._effect('changed')
        return True

    def rearm(self, run_identity):
        previous = deepcopy(self.state)
        s = self.state
        s.paused = s.pause_pending = s.pause_uncertain = False
        s.alerted_at = s.acknowledged_at = None
        s.alert_count = 0
        s.alert_level = ''
        if not self.save(run_identity):
            self.state = previous
            self._effect('status', message='Could not save automatic pause re-arm; the pause latch is unchanged')
            return False
        return True

    def mute(self, muted, run_identity):
        previous = self.state.muted
        self.state.muted = muted
        if not self.save(run_identity):
            self.state.muted = previous
            return False
        return True
