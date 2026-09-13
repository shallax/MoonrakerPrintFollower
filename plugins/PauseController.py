"""Print-local PAUSE scheduling and command lifecycle, independent of Preview UI."""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from .PauseScheduleService import PauseScheduleService, due_end_of_layer_pauses


class PauseController(QObject):
    changed = pyqtSignal()
    message = pyqtSignal(str)
    COMMAND = "ScheduledPause"

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client
        self._schedule = PauseScheduleService()
        self._job = None
        self._generation = 0
        self._target = None
        self._current = None
        # Per-layer lifecycle: "fired" while awaiting confirmation,
        # "failed"/"timed_out" once the verification missed. Entries
        # leave the list ONLY on observation (the verified-pause-only
        # ruling) — a missed pause stays, restyled.
        self._states = {}
        client.commandChanged.connect(self._command_changed)

    @property
    def layers(self): return self._schedule.layers

    @property
    def states(self): return dict(self._states)

    def bind(self, job_key):
        if job_key == self._job: return
        self._generation += 1
        self._client.transport.cancel_owner("pause")
        self._target = self._current = None
        self._job = job_key
        self._schedule.clear()
        self._states.clear()
        self._client.set_pause_guard(False)
        self.changed.emit()

    def toggle(self, layer, current, total):
        if layer in self.layers:
            self.remove(layer)
            return True
        if self._job is None or current is None or layer < current or (total is not None and layer >= total - 1):
            return False
        self._schedule.schedule(layer)
        self._update_guard(current)
        self.message.emit(f"PAUSE scheduled for end of layer {layer + 1}")
        self.changed.emit()
        return True

    def remove(self, layer):
        self._schedule.remove(layer)
        self._states.pop(layer, None)
        self._update_guard(self._current)
        self.changed.emit()

    def clear(self):
        self._schedule.clear()
        self._states.clear()
        self._client.set_pause_guard(False)
        self.changed.emit()

    def _update_guard(self, current):
        self._client.set_pause_guard(current is not None and self._schedule.is_imminent(current))

    def observe(self, current):
        self._current = current
        self._update_guard(current)
        if current is None or self._job is None: return
        # The entries STAY in the schedule until the pause is actually
        # observed (the ruling): only layers without a lifecycle state
        # fire, so a failed pause never re-arms on a later poll.
        due = [layer for layer in due_end_of_layer_pauses(self.layers, current)
               if layer not in self._states]
        if not due: return
        self.changed.emit()
        if self._target is not None: return
        self._target = due[0]
        self._states[self._target] = "fired"
        generation, job, target = self._generation, self._job, self._target
        self._client.track_command(self.COMMAND, {"paused"}, timeout_s=10.0)
        def finished(payload, error):
            if generation != self._generation or job != self._job: return
            if error: self._client.fail_command(self.COMMAND, error)
            else: self._client.accept_command(self.COMMAND)
        started = self._client.transport.send_json("pause", "scheduled", "POST", "printer/gcode/script",
            finished, body={"script": "PAUSE"}, category="command")
        if not started: self._client.fail_command(self.COMMAND, "PAUSE request already in flight")
        else: self.message.emit(f"Requesting PAUSE after layer {target + 1}")
        self._update_guard(current)

    def _command_changed(self, event):
        if event.get("name") != self.COMMAND or self._target is None: return
        outcome = event.get("outcome")
        if outcome == "accepted":
            self.message.emit(f"PAUSE accepted after layer {self._target + 1}; waiting for printer confirmation")
        elif outcome == "confirmed":
            # The printer was OBSERVED paused: the entry leaves the list
            # now, never merely because the layer was crossed.
            self.message.emit(f"PAUSE confirmed after layer {self._target + 1}")
            self._schedule.remove(self._target)
            self._states.pop(self._target, None)
            self._target = None
            self.changed.emit()
        elif outcome in {"failed", "timed_out"}:
            # Not verified: the entry stays, restyled missed.
            self.message.emit(f"PAUSE after layer {self._target + 1}: {event.get('detail') or outcome}")
            self._states[self._target] = outcome
            self._target = None
            self.changed.emit()

    def close(self):
        self.bind(None)
        try: self._client.commandChanged.disconnect(self._command_changed)
        except Exception: pass

