"""Monitor command acknowledgement and emergency-stop ownership."""
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class MonitorCommands(QObject):
    changed = pyqtSignal()
    EXPECTED = {"Pause": {"paused"}, "Resume": {"printing"}, "Cancel": {"cancelled", "complete", "standby"}}

    def __init__(self, data, parent=None):
        super().__init__(parent)
        self._data = data
        self._busy = False
        self._status = self._tracked = ""
        self._clicks = 0
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(1000)
        self._reset_timer.timeout.connect(self._reset_clicks)
        data.invalidated.connect(self.reset)
        data.commandChanged.connect(self._command_changed)

    @property
    def busy(self): return self._busy
    @property
    def status(self): return self._status
    @property
    def clicks(self): return self._clicks
    @property
    def state(self):
        if not self._data.active or not self._data.connected: return ""
        return str((self._data.snapshot.core.get("print_stats") or {}).get("state") or "")
    @property
    def print_active(self): return self.state in {"printing", "paused"}
    @property
    def setup_allowed(self): return bool(self.state) and not self.print_active and not self.busy

    def reset(self):
        self._busy = False
        self._status = self._tracked = ""
        self._reset_clicks()
        self.changed.emit()

    def send(self, label, path, body=None):
        if self._busy or not self._data.active: return False
        self._busy = True
        self._status = f"{label} requested…"
        expected = self.EXPECTED.get(label)
        self._tracked = label if expected else ""
        if expected: self._data.track_command(label, expected)
        self.changed.emit()
        def finished(payload, error):
            if error:
                self._busy = False
                self._status = f"{label} failed: {error}"
                if expected: self._data.fail_command(label, error)
                self._tracked = ""
            elif expected:
                self._data.accept_command(label)
            else:
                self._busy = False
                self._status = f"{label} accepted"
            self._data.later(150, self._data.refresh_all)
            self.changed.emit()
        started = self._data.request("control", "POST", path, finished, body=body, category="command")
        if not started:
            finished(None, "Moonraker is unavailable")
        return started

    def script(self, label, script):
        return self.send(label, "printer/gcode/script", {"script": str(script)})

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

    def emergency_click(self):
        if not self._data.active or self._clicks >= 3: return
        self._clicks += 1
        self._reset_timer.start()
        self.changed.emit()
        if self._clicks == 3:
            def finished(payload, error):
                self._status = f"Emergency stop failed: {error}" if error else "Emergency stop issued"
                self.changed.emit()
                self._data.force_refresh()
            self._data.request("emergency-stop", "POST", "printer/emergency_stop", finished,
                replace=True, category="command")

    def _reset_clicks(self):
        self._reset_timer.stop()
        self._clicks = 0
        self.changed.emit()

