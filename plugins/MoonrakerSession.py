from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, Iterable, Optional, Set


class RequestCategory(str, Enum):
    CORE = "core"
    AUXILIARY = "auxiliary"
    POWER = "power"
    SYSTEM = "system"
    DISCOVERY = "discovery"
    COMMAND = "command"
    STATIC = "static"


@dataclass(frozen=True)
class PollPolicy:
    """Category-aware HTTP polling policy."""

    paused_floor_ms: int = 1500
    idle_floor_ms: int = 5000
    auxiliary_active_ms: int = 1000
    auxiliary_idle_ms: int = 2500
    power_ms: int = 5000
    system_ms: int = 10000
    discovery_ms: int = 30000
    pause_guard_ms: int = 250

    def interval_ms(
        self,
        category: RequestCategory | str,
        configured_ms: int,
        printer_state: str = "",
        *,
        urgent: bool = False,
    ) -> int:
        category = RequestCategory(category)
        configured = max(1, int(configured_ms or 1))
        state = str(printer_state or "").strip().lower()
        active = state == "printing"
        paused = state == "paused"
        if category == RequestCategory.CORE:
            if urgent and active:
                return min(configured, self.pause_guard_ms)
            if active:
                return configured
            if paused:
                return max(configured, self.paused_floor_ms)
            return max(configured, self.idle_floor_ms)
        if category == RequestCategory.AUXILIARY:
            return self.auxiliary_active_ms if active or paused else self.auxiliary_idle_ms
        if category == RequestCategory.POWER:
            return self.power_ms
        if category == RequestCategory.SYSTEM:
            return self.system_ms
        if category == RequestCategory.DISCOVERY:
            return self.discovery_ms
        return configured


@dataclass
class _RequestSlot:
    in_flight: bool = False
    pending: bool = False


class RequestCoalescer:
    """Collapse overlapping refreshes into at most one queued follow-up."""

    def __init__(self) -> None:
        self._slots: Dict[str, _RequestSlot] = {}

    def begin(self, key: str, *, force: bool = False) -> bool:
        slot = self._slots.setdefault(str(key), _RequestSlot())
        if slot.in_flight:
            if force:
                slot.pending = True
            return False
        slot.in_flight = True
        return True

    def complete(self, key: str) -> bool:
        slot = self._slots.setdefault(str(key), _RequestSlot())
        follow_up = slot.pending
        slot.in_flight = False
        slot.pending = False
        return follow_up

    def cancel(self, key: str) -> None:
        self._slots.pop(str(key), None)

    def clear(self) -> None:
        self._slots.clear()

    def is_in_flight(self, key: str) -> bool:
        return bool(self._slots.get(str(key), _RequestSlot()).in_flight)


@dataclass
class SessionSnapshot:
    status: Dict[str, Any] = field(default_factory=dict)
    revision: int = 0
    updated_at: float = 0.0

    def merge_status(self, patch: Dict[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
        if not isinstance(patch, dict):
            return self.copy_status()
        for object_name, value in patch.items():
            if isinstance(value, dict):
                current = self.status.get(object_name)
                if not isinstance(current, dict):
                    current = {}
                merged = dict(current)
                merged.update(value)
                self.status[object_name] = merged
            else:
                self.status[object_name] = value
        self.revision += 1
        self.updated_at = time.monotonic() if now is None else float(now)
        return self.copy_status()

    def copy_status(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name, value in self.status.items():
            result[name] = dict(value) if isinstance(value, dict) else value
        return result

    @property
    def printer_state(self) -> str:
        stats = self.status.get("print_stats")
        if not isinstance(stats, dict):
            return ""
        return str(stats.get("state") or "").strip().lower()


@dataclass
class CommandAcknowledgement:
    name: str
    expected_states: Set[str] = field(default_factory=set)
    issued_at: float = 0.0
    timeout_s: float = 10.0
    http_accepted: bool = False
    terminal: bool = False
    outcome: str = "pending"
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "expected_states": sorted(self.expected_states),
            "http_accepted": self.http_accepted,
            "terminal": self.terminal,
            "outcome": self.outcome,
            "detail": self.detail,
        }


class CommandTracker:
    """Track HTTP acceptance separately from observable printer state."""

    def __init__(self) -> None:
        self._commands: Dict[str, CommandAcknowledgement] = {}

    def issue(self, name: str, expected_states: Iterable[str] = (), *, timeout_s: float = 10.0, now: Optional[float] = None) -> CommandAcknowledgement:
        command = CommandAcknowledgement(
            name=str(name),
            expected_states={str(item).strip().lower() for item in expected_states if str(item).strip()},
            issued_at=time.monotonic() if now is None else float(now),
            timeout_s=max(0.1, float(timeout_s)),
        )
        self._commands[command.name] = command
        return command

    def accepted(self, name: str) -> Optional[CommandAcknowledgement]:
        command = self._commands.get(str(name))
        if command is None or command.terminal:
            return command
        command.http_accepted = True
        command.outcome = "accepted"
        command.detail = "Moonraker accepted the command; waiting for printer state"
        if not command.expected_states:
            command.terminal = True
            command.outcome = "confirmed"
            command.detail = "Moonraker accepted the command"
        return command

    def failed(self, name: str, detail: str) -> Optional[CommandAcknowledgement]:
        command = self._commands.get(str(name))
        if command is None:
            command = self.issue(str(name))
        command.terminal = True
        command.outcome = "failed"
        command.detail = str(detail or "Command failed")
        return command

    def observe(self, printer_state: str, *, now: Optional[float] = None) -> list[CommandAcknowledgement]:
        state = str(printer_state or "").strip().lower()
        timestamp = time.monotonic() if now is None else float(now)
        changed: list[CommandAcknowledgement] = []
        for command in self._commands.values():
            if command.terminal:
                continue
            if command.http_accepted and state and state in command.expected_states:
                command.terminal = True
                command.outcome = "confirmed"
                command.detail = f"Printer state is {state}"
                changed.append(command)
            elif timestamp - command.issued_at >= command.timeout_s:
                command.terminal = True
                command.outcome = "timed_out"
                command.detail = "Moonraker accepted the command, but the expected printer state was not observed"
                changed.append(command)
        return changed

    def get(self, name: str) -> Optional[CommandAcknowledgement]:
        return self._commands.get(str(name))

    def clear(self) -> None:
        self._commands.clear()


class MoonrakerSessionState:
    """Pure state/policy core for one active Cura/Moonraker binding."""

    def __init__(self, poll_policy: Optional[PollPolicy] = None) -> None:
        self.poll_policy = poll_policy or PollPolicy()
        self.coalescer = RequestCoalescer()
        self.snapshot = SessionSnapshot()
        self.commands = CommandTracker()
        self.generation = 0
        self.base_url = ""
        self.connected = False
        self.pause_guard = False

    def rebind(self, base_url: str) -> bool:
        target = str(base_url or "").rstrip("/")
        if target == self.base_url:
            return False
        self.generation += 1
        self.base_url = target
        self.connected = False
        self.pause_guard = False
        self.snapshot = SessionSnapshot()
        self.commands.clear()
        self.coalescer.clear()
        return True

    def reset(self) -> None:
        self.generation += 1
        self.connected = False
        self.pause_guard = False
        self.snapshot = SessionSnapshot()
        self.commands.clear()
        self.coalescer.clear()

    def set_pause_guard(self, active: bool) -> bool:
        active = bool(active)
        if active == self.pause_guard:
            return False
        self.pause_guard = active
        return True

    def merge_status(self, patch: Dict[str, Any], *, now: Optional[float] = None) -> tuple[Dict[str, Any], list[CommandAcknowledgement]]:
        status = self.snapshot.merge_status(patch, now=now)
        changed = self.commands.observe(self.snapshot.printer_state, now=now)
        return status, changed


class MoonrakerSession:
    """One active-printer Moonraker session: identity, transport, state and policy.

    The pure state class above remains importable without Qt for deterministic
    tests. The Qt transport is imported lazily only when a live session is built.
    """

    def __init__(self, parent=None, *, state: Optional[MoonrakerSessionState] = None, transport=None) -> None:
        self._state = state or MoonrakerSessionState()
        if transport is None:
            from .MoonrakerTransport import MoonrakerHttpTransport
            transport = MoonrakerHttpTransport(parent)
        self.transport = transport
        self._api_key = ""

    @property
    def state(self) -> MoonrakerSessionState:
        return self._state

    @property
    def poll_policy(self) -> PollPolicy:
        return self._state.poll_policy

    @property
    def coalescer(self) -> RequestCoalescer:
        return self._state.coalescer

    @property
    def snapshot(self) -> SessionSnapshot:
        return self._state.snapshot

    @property
    def commands(self) -> CommandTracker:
        return self._state.commands

    @property
    def generation(self) -> int:
        return self._state.generation

    @property
    def base_url(self) -> str:
        return self._state.base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def connected(self) -> bool:
        return self._state.connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._state.connected = bool(value)

    @property
    def pause_guard(self) -> bool:
        return self._state.pause_guard

    def configure(self, base_url: str, api_key: str) -> bool:
        target_url = str(base_url or "").rstrip("/")
        target_key = str(api_key or "")
        changed = (target_url, target_key) != (self._state.base_url, self._api_key)
        if not changed:
            return False
        # Transport configure cancels every owner before identity changes. Reset
        # state in the same transaction so stale authenticated data cannot survive.
        self.transport.configure(target_url, target_key)
        self._state.reset()
        self._state.base_url = target_url
        self._api_key = target_key
        return True

    def reset(self) -> None:
        self._state.reset()

    def set_pause_guard(self, active: bool) -> bool:
        return self._state.set_pause_guard(active)

    def merge_status(self, patch: Dict[str, Any], *, now: Optional[float] = None):
        return self._state.merge_status(patch, now=now)
