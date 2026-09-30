"""The webcam stream's freshness policy: when the pane must be told to
reload, and what the veil means while it has not.

A URL change is the only thing that restarts Cura's loader, so every
recovery path here is one decision — move the reload nonce — and the
published URL rides it. The paths are the connection transition, a dead
or stalled stream, the manual refresh, the stream toggle and a wake from
suspend.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt

# Once a recovery cycle is running, later retries wait this long: a dead
# stream must not spin Cura's loader in a tight loop.
RETRY_SECONDS = 10.0


class CameraRecovery:
    """The reload nonce, the recovery veil and the URL bookkeeping.

    Every transition answers whether the published frame changed, so the
    facade keeps its own dispatch: this owner never publishes a frame
    itself.
    """

    def __init__(self, *, active, monotonic=time.monotonic):
        # Does this monitor own the printer right now: a deposed cached
        # monitor must not reload anything on a global wake.
        self._active = active
        self._clock = monotonic
        self._stream_enabled = True
        self._nonce = 0
        self._recovering = False
        self._last_refresh_at = 0.0
        self._last_url = ""
        self._app_state = None

    @property
    def stream_enabled(self) -> bool:
        return self._stream_enabled

    @property
    def nonce(self) -> int:
        return self._nonce

    @property
    def recovering(self) -> bool:
        """The veil: a dead stream is reported, not left as a stale
        picture that reads as live. No veil for a wake — the stream may
        come back instantly, and a stuck veil reads as a failure the
        user must recover from by hand."""
        return self._recovering

    @property
    def application_state(self):
        return self._app_state

    def connection_restored(self) -> bool:
        """A reconnect restarts the stream — the e-stop's automatic
        cycle included."""
        if not self._stream_enabled:
            return False
        self._nonce += 1
        return True

    def stream_stalled(self) -> bool:
        """A stream failure, or the QML pane's render watchdog reporting a
        stream that connected but never painted a frame (it raises no
        error signal of its own).

        The FIRST failure retries immediately: a camera's first fetch can
        die on a cold-start hiccup (DNS or first contact) while the very
        next request sails. Once a retry cycle is running the cadence
        above applies."""
        if not self._stream_enabled:
            return False
        from ...diagnostics.CameraTiming import mark
        mark("T6-watchdog", "camera render stalled")
        now = self._clock()
        if not self._recovering or now - self._last_refresh_at >= RETRY_SECONDS:
            self._last_refresh_at = now
            self._nonce += 1
        self._recovering = True
        return True

    def stream_restored(self) -> bool:
        if not self._recovering:
            return False
        self._recovering = False
        return True

    def refresh_requested(self) -> bool:
        """The manual refresh: the nonce feeds a cache-busting query
        parameter so the live stream itself reloads, not just the
        webcam list."""
        if not self._stream_enabled:
            return False
        self._nonce += 1
        return True

    def set_stream_enabled(self, enabled) -> bool:
        """The stream toggle moves the nonce in BOTH directions: the pane
        re-applies whatever URL it now has, blanked or live."""
        enabled = bool(enabled)
        if enabled is self._stream_enabled:
            return False
        self._stream_enabled = enabled
        self._nonce += 1
        return True

    def seed_application_state(self, state) -> None:
        self._app_state = state

    def application_state_changed(self, state) -> bool:
        """A wake reloads the camera source once, for the ACTIVE monitor
        only: a stream that survives a suspend shows a frozen frame whose
        image size is already set, so the watchdog cannot see it."""
        previous = self._app_state
        self._app_state = state
        if state != Qt.ApplicationState.ApplicationActive:
            return False
        if previous in (None, Qt.ApplicationState.ApplicationActive):
            return False
        if not self._active():
            return False
        self._nonce += 1
        return True

    def note_url(self, url) -> tuple:
        """Record this frame's stream URL: (bumped, first_attach).

        A query-only transition is the upstream's own noise (a rotated
        nonce in the reported URL) — the live stream keeps working, so
        neither this nor the pane's guard reloads it. An origin, port or
        path transition reloads, and the first attach always does: its
        initial request dies silently in the loader."""
        last = self._last_url
        if url == last:
            return False, False
        self._last_url = url

        def stripped(value):
            cut = value.find("?")
            return value[:cut] if cut >= 0 else value
        if stripped(url) == stripped(last or ""):
            return False, False
        self._nonce += 1
        return True, not last
