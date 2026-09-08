"""Monitor command acknowledgement and emergency-stop ownership."""
from __future__ import annotations

import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class MonitorCommands(QObject):
    changed = pyqtSignal()
    emergencyStopped = pyqtSignal()
    EXPECTED = {"Pause": {"paused"}, "Resume": {"printing"}, "Cancel": {"cancelled", "complete", "standby"}}
    # A resumed printer may re-heat before it actually resumes; the
    # confirmation must outlive a slow heat-up instead of reporting
    # failure while the printer does exactly what it was asked.
    EXPECTED_TIMEOUT_S = {"Resume": 300}
    MAX_QUEUED_COMMANDS = 16
    # The stop requires two clicks and a held third press: the hold must
    # last this long before the stop fires, so a click spasm cannot fire it
    # while a real emergency stays under a second away.
    HOLD_MS = 600
    HOLD_TICK_MS = 50

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = data
        self._busy = False
        self._status = self._tracked = ""
        self._clicks = 0
        self._queue = []
        self._hold_progress = 0.0
        self._hold_started_at = 0.0
        self._suppress_click = False
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(1000)
        self._reset_timer.timeout.connect(self._reset_clicks)
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(self.HOLD_MS)
        self._hold_timer.timeout.connect(self._fire_hold)
        self._hold_ticker = QTimer(self)
        self._hold_ticker.setInterval(self.HOLD_TICK_MS)
        self._hold_ticker.timeout.connect(self._hold_tick)
        data.invalidated.connect(self.reset)
        data.commandChanged.connect(self._command_changed)

    @property
    def busy(self): return self._busy
    @property
    def status(self): return self._status
    @property
    def clicks(self): return self._clicks
    @property
    def hold_progress(self): return self._hold_progress
    @property
    def state(self):
        if not self._data.active or not self._data.connected: return ""
        return str((self._data.snapshot.core.get("print_stats") or {}).get("state") or "")
    @property
    def print_active(self): return self.state in {"printing", "paused"}
    @property
    def setup_allowed(self):
        # Busy is deliberately not part of the gate: one-shot setup scripts
        # (Home, QGL, mesh, Save, Cooldown, macros) queue behind the
        # in-flight command, so the user can line them up in succession.
        return bool(self.state) and not self.print_active

    def reset(self):
        self._busy = False
        self._queue.clear()
        self._status = self._tracked = ""
        self._reset_clicks()
        self.changed.emit()

    def send(self, label, path, body=None):
        if self._busy or not self._data.active: return False
        self._busy = True
        self._status = f"{label} requested…"
        expected = self.EXPECTED.get(label)
        self._tracked = label if expected else ""
        if expected: self._data.track_command(label, expected,
            timeout_s=self.EXPECTED_TIMEOUT_S.get(label, 10))
        self.changed.emit()
        def finished(payload, error):
            if error:
                self._busy = False
                # A connection-level error says nothing about whether the
                # command executed: the script may already have been
                # accepted by the printer.
                self._status = f"{label} outcome unknown: {error}"
                if expected: self._data.fail_command(label, error)
                self._tracked = ""
            elif expected:
                self._data.accept_command(label)
            else:
                self._busy = False
                self._status = f"{label} accepted"
            self._data.later(150, self._data.refresh_all)
            # Pump the queued one-shots BEFORE announcing the idle lane:
            # listeners (the toolhead controller) react to "changed" by
            # sending their own command, and the queue must keep its place
            # in line ahead of that fresh send.
            self._pump_queue()
            self.changed.emit()
        # Commands get a long transfer timeout: Moonraker accepts the
        # script before responding, so a slow reply must never be
        # misread as a failed command.
        started = self._data.request("control", "POST", path, finished, body=body,
            category="command", timeout_ms=30000)
        if not started:
            finished(None, "Moonraker is unavailable")
        return started

    def script(self, label, script):
        return self.request(label, "printer/gcode/script", {"script": str(script)})

    def request(self, label, path, body=None):
        """A one-shot command: sent now, or queued behind the in-flight one.

        One-shot commands (Home, QGL, mesh, Save, Cooldown, macros, system
        restarts) queue instead of being dropped, so they can be lined up
        in quick succession. Stateful commands never queue.
        """
        if self._busy:
            if len(self._queue) >= self.MAX_QUEUED_COMMANDS:
                return False
            self._queue.append((label, path, body))
            self._status = f"{label} queued"
            self.changed.emit()
            return True
        return self.send(label, path, body)

    def _pump_queue(self):
        if not self._queue or self._busy or not self._data.active:
            return
        label, path, body = self._queue.pop(0)
        self.send(label, path, body)

    def quick(self, channel, script, callback):
        return self._data.request("quick-" + channel, "POST", "printer/gcode/script", callback,
            body={"script": script}, replace=True, category="command")

    def _command_changed(self, event):
        if event.get("name") != self._tracked: return
        outcome = event.get("outcome")
        self._status = f"{self._tracked}: {event.get('detail') or outcome}"
        if event.get("terminal"):
            self._busy = False
            self._tracked = ""
        self.changed.emit()
        self._pump_queue()

    def emergency_click(self):
        # The first two clicks arm the stop; the third press must be held
        # (see emergency_hold_started) before anything fires.
        if not self._data.active: return
        if self._suppress_click:
            # The release of a fired hold delivers a clicked event; it must
            # not count as the first click of a new arm sequence.
            self._suppress_click = False
            return
        if self._clicks >= 2: return
        self._clicks += 1
        self._reset_timer.start()
        self.changed.emit()

    def emergency_hold_started(self):
        if not self._data.active or self._clicks < 2 or self._hold_timer.isActive(): return
        self._suppress_click = False  # a new press starts a fresh sequence
        self._reset_timer.stop()  # the hold keeps the arm alive
        self._hold_started_at = time.monotonic()
        self._hold_progress = 0.0
        self._hold_ticker.start()
        self._hold_timer.start()
        self.changed.emit()

    def emergency_hold_released(self):
        # Releasing before the hold completes cancels it; the arm persists
        # for a moment so the user can try again.
        if not self._hold_timer.isActive(): return
        self._hold_ticker.stop()
        self._hold_timer.stop()
        self._hold_progress = 0.0
        self._reset_timer.start()
        self.changed.emit()

    def _hold_tick(self):
        self._hold_progress = min(1.0, (time.monotonic() - self._hold_started_at) * 1000.0 / self.HOLD_MS)
        self.changed.emit()

    def _fire_hold(self):
        self._hold_ticker.stop()
        self._hold_progress = 1.0
        self._fire_emergency()

    def _fire_emergency(self):
        def finished(payload, error):
            self._status = f"Emergency stop failed: {error}" if error else "Emergency stop issued"
            self.changed.emit()
            self._data.force_refresh()
        self._data.request("emergency-stop", "POST", "printer/emergency_stop", finished,
            replace=True, category="command")
        # Everything pending dies with the stop: queued commands, the
        # jog queue and any in-flight busy state. The busy release makes
        # gated controls (power toggles, restarts) usable immediately.
        self._suppress_click = True  # the release of this hold is not a click
        self.emergencyStopped.emit()
        self.reset()

    def _reset_clicks(self):
        self._reset_timer.stop()
        self._hold_ticker.stop()
        self._hold_timer.stop()
        self._hold_progress = 0.0
        self._clicks = 0
        self.changed.emit()

