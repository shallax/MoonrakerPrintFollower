from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, Iterable, Optional, Set


class RequestCategory(str, Enum):
    CORE = "core"
    AUXILIARY = "auxiliary"
    POWER = "power"
    SYSTEM = "system"
    ENDSTOPS = "endstops"
    CONSOLE = "console"
    DISCOVERY = "discovery"
    COMMAND = "command"
    STATIC = "static"


@dataclass(frozen=True)
class PollPolicy:
    """Category-aware HTTP polling policy."""

    paused_floor_ms: int = 1500
    idle_floor_ms: int = 5000
    # The auxiliary objects query while printing/paused: 2.5 s, down
    # from 1 s — the author's live report (2026-09-11): prints stall
    # at points while the plugin is connected, and two full state
    # queries per second are the prime suspect. Temperatures change
    # slowly enough that the chart loses nothing.
    auxiliary_active_ms: int = 2500
    auxiliary_idle_ms: int = 2500
    power_ms: int = 5000
    system_ms: int = 10000
    endstops_ms: int = 10000
    console_ms: int = 1000
    console_idle_ms: int = 5000
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
            # Urgent covers both guards: the pause guard (printer is
            # pausing, state still "printing") and the toolhead guard
            # (moves running while idle/paused) — the floors must not
            # apply while either is latched.
            if urgent:
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
        if category == RequestCategory.ENDSTOPS:
            return self.endstops_ms
        if category == RequestCategory.CONSOLE:
            # Live output matters while a print runs; an idle printer
            # does not need a store fetch every second (the domain
            # panel's idle-floor point: ~86k requests/day otherwise).
            return self.console_ms if (active or paused) else max(self.console_ms, self.console_idle_ms)
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


@dataclass(frozen=True)
class BindingIdentity:
    """What a session is bound to: URL, key and the status-feed mode.

    The mode is part of the identity — a change rebinds (the author's
    ruling) — but it never enters ``MoonrakerHttpTransport.identity``:
    HTTP lanes have no reason to be invalidated by a status-feed change.
    """

    url: str = ""
    api_key: str = ""
    feed_mode: str = "http"


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
                merged.update(copy.deepcopy(value))
                self.status[object_name] = merged
            else:
                # Store a defensive copy so callers cannot mutate the snapshot
                # through objects they still hold.
                self.status[object_name] = copy.deepcopy(value)
        self.revision += 1
        self.updated_at = time.monotonic() if now is None else float(now)
        return self.copy_status()

    def copy_status(self) -> Dict[str, Any]:
        """Return a fully detached copy; nested mutation cannot reach internals."""
        return copy.deepcopy(self.status)

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
                command.detail = (
                    "Moonraker accepted the command, but the expected printer state was not observed"
                    if command.http_accepted else "Moonraker did not acknowledge the command in time"
                )
                changed.append(command)
        return changed

    def expire(self, *, now: Optional[float] = None) -> list[CommandAcknowledgement]:
        """Advance deadlines without treating a cached snapshot as a new observation."""
        return self.observe("", now=now)

    @property
    def has_pending(self) -> bool:
        return any(not command.terminal for command in self._commands.values())

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
        self.feed_mode = "http"
        self.connected = False
        self.pause_guard = False
        self.toolhead_guard = False
        # The e-stop's assumption (the author's ruling): session-level
        # storage so a stale stream can never re-assert an e-stopped
        # print; the rewrite stays at the client's single admission site.
        self.assume_print_stopped = False

    def reset(self) -> None:
        self.generation += 1
        self.connected = False
        self.pause_guard = False
        self.toolhead_guard = False
        self.feed_mode = "http"
        self.assume_print_stopped = False
        self.snapshot = SessionSnapshot()
        self.commands.clear()
        self.coalescer.clear()

    def set_pause_guard(self, active: bool) -> bool:
        active = bool(active)
        if active == self.pause_guard:
            return False
        self.pause_guard = active
        return True

    def set_toolhead_guard(self, active: bool) -> bool:
        # While the toolhead is moving (or just moved), the core poll floor
        # drops to the urgent rate so the position readout tracks the head.
        active = bool(active)
        if active == self.toolhead_guard:
            return False
        self.toolhead_guard = active
        return True

    def merge_status(self, patch: Dict[str, Any], *, now: Optional[float] = None) -> tuple[Dict[str, Any], list[CommandAcknowledgement]]:
        status = self.snapshot.merge_status(patch, now=now)
        # Only a fresh print_stats.state can confirm a command. An unrelated
        # partial patch must not confirm it using an old merged state.
        stats = patch.get("print_stats") if isinstance(patch, dict) else None
        state = stats.get("state", "") if isinstance(stats, dict) else ""
        changed = self.commands.observe(state, now=now)
        return status, changed


class MoonrakerSession:
    """One active-printer Moonraker session: identity, transport, state and policy.

    The pure state class above remains importable without Qt for deterministic
    tests. The Qt transport is imported lazily only when a live session is built.
    """

    def __init__(self, parent=None, *, state: Optional[MoonrakerSessionState] = None, transport=None, socket=None) -> None:
        self._state = state or MoonrakerSessionState()
        if transport is None:
            from .MoonrakerTransport import MoonrakerHttpTransport
            transport = MoonrakerHttpTransport(parent)
        self.transport = transport
        if socket is None:
            from .MoonrakerSocket import MoonrakerSocket
            socket = MoonrakerSocket(parent)
        self.socket = socket
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

    @property
    def toolhead_guard(self) -> bool:
        return self._state.toolhead_guard

    def configure(self, base_url: str, api_key: str, feed_mode: Optional[str] = None) -> bool:
        """Rebind when the URL, the key OR the feed mode changed.

        ``feed_mode=None`` keeps the current mode (the frozen seam's
        sentinel). A mode-only change rebinds the session — the author's
        ruling — but never reconfigures the HTTP transport: its lanes
        have no reason to be invalidated by a status-feed change.
        """
        target_url = str(base_url or "").rstrip("/")
        target_key = str(api_key or "")
        target_mode = str(feed_mode or self._state.feed_mode).strip().lower() or "http"
        changed = (target_url, target_key, target_mode) != (
            self._state.base_url, self._api_key, self._state.feed_mode,
        )
        if not changed:
            return False
        if (target_url, target_key) != (self._state.base_url, self._api_key):
            # Transport configure cancels every owner before identity changes.
            self.transport.configure(target_url, target_key)
        # Reset the socket and the shared state in the same transaction so
        # stale authenticated data cannot survive any kind of rebind.
        self.socket.stop()
        self._state.reset()
        self._state.base_url = target_url
        self._api_key = target_key
        self._state.feed_mode = target_mode
        return True

    @property
    def identity(self) -> BindingIdentity:
        return BindingIdentity(self._state.base_url, self._api_key, self._state.feed_mode)

    @property
    def feed_mode(self) -> str:
        return self._state.feed_mode

    def reset(self) -> None:
        self.transport.cancel_all()
        self.socket.stop()
        self._state.reset()

    def set_pause_guard(self, active: bool) -> bool:
        return self._state.set_pause_guard(active)

    def set_toolhead_guard(self, active: bool) -> bool:
        return self._state.set_toolhead_guard(active)

    def merge_status(self, patch: Dict[str, Any], *, now: Optional[float] = None):
        return self._state.merge_status(patch, now=now)
