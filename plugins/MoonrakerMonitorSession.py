from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtCore import QTimer

from .MoonrakerMonitorModel import MoonrakerMonitorModel as _BaseMoonrakerMonitorModel
from .MoonrakerSession import RequestCategory


class MoonrakerMonitorModel(_BaseMoonrakerMonitorModel):
    """Monitor adapter backed by the follower's shared core Moonraker session."""

    _PRINT_COMMAND_STATES = {
        "Pause": {"paused"},
        "Resume": {"printing"},
        "Cancel": {"cancelled", "complete", "standby"},
    }

    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        self._tracked_control_label = ""
        super().__init__(output_controller, number_of_extruders, follower)
        client = getattr(follower, "_client", None)
        signal = getattr(client, "commandChanged", None)
        if signal is not None:
            try:
                signal.connect(self._on_shared_command_changed)
            except Exception:
                pass

    def _shared_client(self):
        return getattr(self._follower, "_client", None)

    def _start_background_timers(self) -> None:
        self._core_timer.stop()
        for timer in (self._aux_timer, self._power_timer, self._system_timer, self._discovery_timer):
            if not timer.isActive():
                timer.start()

    def refreshTransport(self) -> None:
        if not self._monitoring_active:
            self._core_timer.stop()
            self._cancel_channel("core")
            return
        self._ensure_request_session()
        self._core_timer.stop()
        self._cancel_channel("core")
        client = self._shared_client()
        if client is None:
            return
        try:
            client.start()
            if not getattr(client, "status", {}):
                client.force_refresh()
        except Exception:
            pass

    def _poll_core_fallback(self) -> None:
        client = self._shared_client()
        refresh = getattr(client, "force_refresh", None)
        if callable(refresh):
            refresh()

    def _refresh_core_now(self) -> None:
        self._poll_core_fallback()

    def _after_core_status(self, status: Any) -> None:
        super()._after_core_status(status)
        client = self._shared_client()
        session = getattr(client, "session", None)
        policy = getattr(session, "poll_policy", None)
        if policy is None:
            return
        state = self._monitor_state_raw
        configured = 1000
        try:
            configured = int(self._follower.current_printer_config().poll_interval_ms)
        except Exception:
            pass
        self._aux_timer.setInterval(policy.interval_ms(RequestCategory.AUXILIARY, configured, state))
        self._power_timer.setInterval(policy.interval_ms(RequestCategory.POWER, configured, state))
        self._system_timer.setInterval(policy.interval_ms(RequestCategory.SYSTEM, configured, state))
        self._discovery_timer.setInterval(policy.interval_ms(RequestCategory.DISCOVERY, configured, state))

    def _send_print_action(self, label: str, path: str) -> None:
        expected = self._PRINT_COMMAND_STATES.get(str(label))
        client = self._shared_client()
        if expected and client is not None:
            self._tracked_control_label = str(label)
            try:
                client.track_command(label, expected, timeout_s=10.0)
            except Exception:
                self._tracked_control_label = ""
        super()._send_print_action(label, path)

    def _on_control_finished(
        self,
        label: str,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if label != self._tracked_control_label:
            super()._on_control_finished(label, payload, error)
            return
        client = self._shared_client()
        if error:
            self._action_busy = False
            self._action_status = f"{label} failed: {error}"
            if client is not None:
                try:
                    client.fail_command(label, error)
                except Exception:
                    pass
            self._tracked_control_label = ""
            self.actionChanged.emit()
            return
        self._action_status = f"{label} accepted; waiting for printer confirmation…"
        self.actionChanged.emit()
        if client is not None:
            try:
                client.accept_command(label)
            except Exception:
                pass
            QTimer.singleShot(10250, getattr(client, "expire_commands", lambda: None))
        QTimer.singleShot(150, self._refresh_core_now)

    def _on_shared_command_changed(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        label = str(event.get("name") or "")
        if not label or label != self._tracked_control_label:
            return
        outcome = str(event.get("outcome") or "")
        if outcome == "accepted":
            self._action_status = f"{label} accepted; waiting for printer confirmation…"
        elif outcome == "confirmed":
            self._action_busy = False
            self._action_status = f"{label} confirmed"
            self._tracked_control_label = ""
        elif outcome in {"failed", "timed_out"}:
            self._action_busy = False
            detail = str(event.get("detail") or outcome.replace("_", " "))
            self._action_status = f"{label}: {detail}"
            self._tracked_control_label = ""
        self.actionChanged.emit()
