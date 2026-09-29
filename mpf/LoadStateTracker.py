"""The load request's lifecycle: pending requests, the handoff, the busy term."""
from __future__ import annotations

import time


class LoadStateTracker:
    """The load-state block: the preview's load request, the monitor-only
    download, the lease handoff to Cura and the busy term the panel reads.

    The request windows are the stuck "Resolving…" report's bound: a
    request against a standby printer never resolves through observe()
    — no status frame arrives to clear the flag — so a known-idle
    printer clears once the refresh the request kicked off has had a
    moment to land; the longer window catches a stale snapshot.
    """
    IDLE_WINDOW_S = 2.0
    ACTIVE_WINDOW_S = 5.0

    def __init__(self, *, files, index, cura):
        self._files, self._index, self._cura = files, index, cura
        self._load_job = None
        self._load_requested = False
        self._load_requested_at = 0.0
        self._monitor_requested = False
        self._monitor_requested_at = 0.0

    @property
    def load_requested(self):
        return self._load_requested

    @property
    def monitor_requested(self):
        return self._monitor_requested

    @property
    def active(self):
        """The preview's busy term: anything in flight from the request
        to the render."""
        return (self._load_requested or self._load_job is not None
                or self._monitor_requested
                or self._files.phase in ("resolving", "downloading")
                or self._index.phase == "indexing"
                or self._cura.loading)

    def request_load(self):
        self._load_requested = True
        self._load_requested_at = time.monotonic()

    def request_monitor(self):
        self._monitor_requested = True
        self._monitor_requested_at = time.monotonic()

    def abandon(self):
        """A failed render leaves no pending load behind."""
        self._load_job = None

    def reset(self):
        # A request in flight when the binding changes (machine switch,
        # job change) must not leak its busy flag into the next session
        # — panel finding P1-1.
        self._load_job = None
        self._load_requested = False
        self._monitor_requested = False

    def resolve(self, job, active):
        """observe()'s step: a request against an active print becomes
        the pending load, against a standby printer it only explains
        itself. Returns a status detail, or None."""
        if not self._load_requested:
            return None
        if not active:
            self._load_requested = False
            return "No active Moonraker print to load"
        self._load_job = job
        self._load_requested = False
        self._files.request_file(retry=True)
        return None

    def settle(self, now, active):
        """The refresh-side age-out of both request flags against the
        snapshot's own print state. Returns a status detail, or None."""
        detail = None
        window = self.ACTIVE_WINDOW_S if active else self.IDLE_WINDOW_S
        if self._load_requested and now - self._load_requested_at > window:
            self._load_requested = False
            if not active:
                detail = "No active Moonraker print to load"
        if self._monitor_requested and now - self._monitor_requested_at > window:
            self._monitor_requested = False
            if not active:
                detail = "No active Moonraker print to load"
        return detail

    def retire_monitor(self, index_ready):
        """The monitor-only download's terminal conditions: the index
        landed, the build failed, OR the download failed. Without the
        download-failure branch the flag wedges True forever — the
        retry ladder only re-fires when a consumer re-requests, and the
        one consumer in monitor-only mode is gated behind the toolpath
        (panel finding P1-1)."""
        if self._monitor_requested and (index_ready
                                        or self._index.phase == "error"
                                        or self._files.phase == "error"):
            self._monitor_requested = False

    def advance(self, job):
        """The lease handoff: a pending load whose file has landed loads
        into Cura. Returns a status detail, or None."""
        if self._load_job is None:
            return None
        if self._load_job != job:
            self._load_job = None
            return "Print changed before it could be loaded"
        if self._files.path and not self._cura.loading:
            lease = self._files.lease()
            self._load_job = None
            if lease is not None:
                self._cura.load(lease)
        return None
