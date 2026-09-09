"""Console state owner: the bounded, per-printer command history and
the send lane.

Commands go through MonitorCommands' untracked one-shot path — HTTP
acknowledgement means *queued at the Klipper boundary*, never executed,
and Klipper's replies are websocket-only (logged as 4.0.0 debt in the
roadmap). The UI must say exactly that, so this controller publishes
honest status strings instead of pretending completion.

The history persists per printer (sensor/command vocabulary differs
between machines); it is bounded by ConsolePolicy and never lands in
the global chrome file.
"""
from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, pyqtSignal

from .ConsolePolicy import MAX_PENDING, normalise_line, trim_history


class ConsoleController(QObject):
    changed = pyqtSignal()

    def __init__(self, data, commands, config, apply_config, parent=None):
        super().__init__(parent)
        self._data, self._commands = data, commands
        self._config, self._apply_config = config, apply_config
        self._history = trim_history(getattr(self._config(), "console_history", ()))
        self._status = ""
        # Accepted-but-unacknowledged sends, counted per completion of a
        # console-labelled lane cycle. Per-idle-epoch decrements drifted
        # under bursts (an intermediate completion pumps the next queued
        # command and the lane never looks idle), accumulating phantom
        # pending toward the cap.
        self._pending = 0
        commands.completed.connect(self._lane_completed)
        commands.emergencyStopped.connect(self._emergency_stopped)
        # A printer switch must not leave phantom pending sends or a
        # "sent" status bleeding across sessions; the history is
        # per-printer and persists, so it stays.
        data.invalidated.connect(self._session_invalidated)

    @property
    def values(self):
        return {
            "consoleHistory": list(self._history),
            "consolePending": self._pending,
            "consoleStatus": self._status,
        }

    def send(self, text) -> bool:
        """Accept a console line; True only when it actually entered the
        lane, so the UI can keep the draft on a refusal."""
        line = normalise_line(text)
        if not line:
            self._status = "Empty command ignored."
            self.changed.emit()
            return False
        if self._pending >= MAX_PENDING:
            self._status = "Too many commands waiting — try again in a moment."
            self.changed.emit()
            return False
        started = self._commands.request("Console", "printer/gcode/script", {"script": line})
        if not started:
            self._status = "Command queue full or Moonraker unavailable — try again."
            self.changed.emit()
            return False
        self._history = trim_history(self._history + [line])
        self._pending += 1
        self._status = "Sent to Klipper's queue — HTTP gives no output or errors."
        self._persist()
        self.changed.emit()
        return True

    def clear(self) -> None:
        if not self._history:
            return
        self._history = []
        self._persist()
        self.changed.emit()

    def _lane_completed(self, label) -> None:
        # Each completion of a console-labelled lane cycle drains one
        # pending line. Macro sends share the lane but carry their own
        # labels, so they never touch the console counter, and the
        # guard keeps the counter from going negative when an emergency
        # stop already zeroed it.
        if label == "Console" and self._pending:
            self._pending -= 1
            self.changed.emit()

    def _session_invalidated(self) -> None:
        if self._pending or self._status:
            self._pending = 0
            self._status = ""
            self.changed.emit()

    def _emergency_stopped(self) -> None:
        if self._pending:
            self._pending = 0
            self._status = "Pending console commands dropped by the emergency stop."
            self.changed.emit()

    def _persist(self) -> None:
        config = self._config()
        if getattr(config, "console_history", None) != self._history:
            self._apply_config(replace(config, console_history=self._history))
