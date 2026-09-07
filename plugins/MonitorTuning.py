"""Debounced, revision-guarded live tuning independent of QML and printer discovery."""
from __future__ import annotations
from dataclasses import dataclass
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


@dataclass
class PendingValue:
    value: object
    revision: int
    script: str = ""
    channel: str = ""
    sent: bool = False


class MonitorTuning(QObject):
    changed = pyqtSignal()
    DEBOUNCE_MS = 2000
    CONFIRM_MS = 5000

    def __init__(self, data, commands, parent=None):
        super().__init__(parent)
        self._data, self._commands = data, commands
        self._pending = {}
        self._debounce, self._confirm = {}, {}
        self._revision = 0
        data.invalidated.connect(self.reset)

    def reset(self):
        self._revision += 1
        self._pending.clear()
        for timer in list(self._debounce.values()) + list(self._confirm.values()): timer.stop()
        self.changed.emit()

    def preview(self, key, value):
        self._drop(key)
        self._revision += 1
        self._pending[key] = PendingValue(value, self._revision)
        self.changed.emit()

    def queue(self, key, value, channel, script):
        if not self._data.active: return
        self.preview(key, value)
        pending = self._pending[key]
        pending.script, pending.channel = script, channel
        timer = self._timer(self._debounce, key, self.DEBOUNCE_MS, self._send)
        timer.start()

    def value(self, key, actual):
        pending = self._pending.get(key)
        return pending.value if pending else actual

    def observe(self, key, actual, tolerance=1.0):
        pending = self._pending.get(key)
        if pending is not None and pending.sent and self.matches(actual, pending.value, tolerance):
            self._drop(key)

    @staticmethod
    def matches(actual, desired, tolerance=1.0):
        if isinstance(actual, (list, tuple)) or isinstance(desired, (list, tuple)):
            if not isinstance(actual, (list, tuple)) or not isinstance(desired, (list, tuple)) or len(actual) != len(desired): return False
            return all(MonitorTuning.matches(a, b, tolerance) for a, b in zip(actual, desired))
        try: return abs(float(actual) - float(desired)) <= tolerance
        except (TypeError, ValueError): return actual == desired

    def _drop(self, key):
        self._pending.pop(key, None)
        for timers in (self._debounce, self._confirm):
            if key in timers: timers[key].stop()

    def _timer(self, timers, key, interval, callback):
        if key not in timers:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: callback(key))
            timers[key] = timer
        timer = timers[key]
        timer.setInterval(interval)
        return timer

    def _send(self, key):
        pending = self._pending.get(key)
        if pending is None or not pending.script: return
        pending.sent = True
        revision = pending.revision
        def finished(payload, error):
            current = self._pending.get(key)
            if current is None or current.revision != revision: return
            if error: self._expire(key)
            else: self._data.later(150, self._data.refresh_all)
        if self._commands.quick(pending.channel, pending.script, finished):
            self._timer(self._confirm, key, self.CONFIRM_MS, self._expire).start()
        else:
            self._expire(key)

    def _expire(self, key):
        self._drop(key)
        self.changed.emit()
        self._data.refresh_all()

