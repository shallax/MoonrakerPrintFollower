# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own run attestation, stale-reply fences and metadata cadence outside the UI bridge.

"""History attestation over explicit immutable inputs and request capabilities."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4
from ..printing.PrintRunIdentity import attest_active_run, PrintRunIdentity


@dataclass(frozen=True)
class DetectionRunInput:
    key: tuple
    filename: str
    eligible: bool


@dataclass(frozen=True)
class RunTransition:
    kind: str
    previous: object = None


class DetectionRun:
    def __init__(self, *, current, request, changed, monotonic, wall_time):
        self._current = current
        self._request = request
        self._changed = changed
        self._now = monotonic
        self._wall_time = wall_time
        self.context = None
        self.identity = None
        self.notice_identity = None
        self.pending = False
        self.retry_at = self.next_at = 0.0
        self.attested_at = None
        self.retired_history = None
        self._closed = False

    def close(self, *_args):
        self._closed = True
        self.context = None
        self.pending = False

    def fresh(self, seconds):
        return (self.identity is not None and self.attested_at is not None
                and 0 <= self._now() - self.attested_at <= seconds)

    def ensure(self, *, force=False):
        if self._closed:
            return
        value = self._current()
        key = value.key
        printer, _session, job, url, active = key
        if key != self.context:
            old = self.context
            if old is not None and old[0] == printer and old[1] == key[1] and (old[2] != job or old[4] and not active):
                self.retired_history = self.identity
            self.context = key
            self.identity = self.attested_at = None
            self.notice_identity = PrintRunIdentity(uuid4().hex, self._wall_time(), value.filename, 'session')
            self.pending = False
            self.retry_at = self.next_at = 0.0
            self._changed(RunTransition('reset'))
        clock = self._now()
        if (self.pending or not active or job is None or not value.eligible or not printer
                or clock < self.retry_at or (not force and clock < self.next_at)):
            return
        self.pending = True
        self.attested_at = None
        self.retry_at = self._now() + 5
        binding = sha256(url.encode()).hexdigest()

        def finished(payload, error):
            if self._closed or key != self.context or key != self._current().key:
                return
            self.pending = False
            identity = attest_active_run(payload, value.filename, binding) if not error else None
            if identity is not None and identity != self.retired_history:
                previous = self.identity
                self.identity = identity
                self.attested_at = clock
                self.retry_at = 0 if self.fresh(2) else self._now() + 5
                self.next_at = self._now() + 10
                self._changed(RunTransition('admitted', previous))
            else:
                self.attested_at = None
                if not error and self.identity is not None:
                    self.identity = None
                    self._changed(RunTransition('lost'))
                else:
                    self._changed(RunTransition('unresolved'))
        if not self._request('detectionHistory', 'GET', 'server/history/list?limit=1&order=desc', finished):
            self.pending = False
