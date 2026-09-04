from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class FollowState(str, Enum):
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    FOLLOWING = "following"
    USER_OVERRIDE = "user_override"
    REMOTE_PAUSED = "remote_paused"
    CURA_SUSPENDED = "cura_suspended"
    ERROR = "error"


class FollowMode(str, Enum):
    EXACT = "exact"
    COMPLETED = "completed"
    LOOKAHEAD = "lookahead"
    WINDOW = "window"


@dataclass
class FollowDecision:
    current_layer: int
    minimum_layer: Optional[int] = None
    follow_path: bool = True


def decide_layers(remote_layer: int, max_layer: int, mode: str, window_radius: int = 2) -> FollowDecision:
    max_layer = max(0, int(max_layer))
    remote_layer = max(0, min(int(remote_layer), max_layer))
    try:
        parsed = FollowMode(mode)
    except ValueError:
        parsed = FollowMode.EXACT

    if parsed == FollowMode.COMPLETED:
        return FollowDecision(max(0, remote_layer - 1), minimum_layer=0, follow_path=False)
    if parsed == FollowMode.LOOKAHEAD:
        return FollowDecision(min(max_layer, remote_layer + 1), minimum_layer=0, follow_path=False)
    if parsed == FollowMode.WINDOW:
        radius = max(1, int(window_radius))
        return FollowDecision(
            min(max_layer, remote_layer + radius),
            max(0, remote_layer - radius),
            follow_path=False,
        )
    return FollowDecision(remote_layer, minimum_layer=0, follow_path=True)


class FollowController:
    """Small explicit state machine for follower intent and remote lifecycle."""

    ACTIVE_REMOTE = {"printing", "paused"}

    def __init__(self) -> None:
        self.enabled = False
        self.connected = False
        self.connecting = False
        self.remote_state = ""
        self.user_paused = False
        self.user_pause_reason = ""
        self.cura_suspended = False
        self.error = ""
        self.state = FollowState.DISABLED

    def _recompute(self) -> FollowState:
        if not self.enabled:
            self.state = FollowState.DISABLED
        elif self.error:
            self.state = FollowState.ERROR
        elif self.connecting and not self.connected:
            self.state = FollowState.CONNECTING
        elif not self.connected:
            self.state = FollowState.DISCONNECTED
        elif self.user_paused:
            self.state = FollowState.USER_OVERRIDE
        elif self.cura_suspended:
            self.state = FollowState.CURA_SUSPENDED
        elif self.remote_state == "paused":
            self.state = FollowState.REMOTE_PAUSED
        elif self.remote_state == "printing":
            self.state = FollowState.FOLLOWING
        else:
            self.state = FollowState.IDLE
        return self.state

    def set_enabled(self, enabled: bool) -> FollowState:
        self.enabled = bool(enabled)
        if not self.enabled:
            self.user_paused = False
            self.user_pause_reason = ""
            self.error = ""
        return self._recompute()

    def set_connection(self, connected: bool, connecting: bool = False, error: str = "") -> FollowState:
        self.connected = bool(connected)
        self.connecting = bool(connecting)
        self.error = str(error or "")
        return self._recompute()

    def set_remote_state(self, state: str) -> FollowState:
        self.remote_state = str(state or "")
        return self._recompute()

    def set_cura_suspended(self, suspended: bool) -> FollowState:
        self.cura_suspended = bool(suspended)
        return self._recompute()

    def pause_by_user(self, reason: str = "manual") -> FollowState:
        self.user_paused = True
        self.user_pause_reason = str(reason or "manual")
        return self._recompute()

    def resume(self) -> FollowState:
        self.user_paused = False
        self.user_pause_reason = ""
        self.error = ""
        return self._recompute()

    @property
    def may_write_preview(self) -> bool:
        return self.state in {FollowState.FOLLOWING, FollowState.REMOTE_PAUSED}
