from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable


@dataclass(frozen=True)
class FakeCommand:
    name: str
    expected_states: tuple[str, ...]


class FakeMoonraker:
    """Deterministic scripted Moonraker used by architecture tests.

    It deliberately has no sockets, threads or wall clock. Tests advance one
    response at a time and feed the same payload shape MoonrakerClient publishes
    into the transport-agnostic session state.
    """

    def __init__(self, statuses: Iterable[Dict[str, Any]] = ()) -> None:
        self._statuses: Deque[Dict[str, Any]] = deque(deepcopy(list(statuses)))
        self.commands: list[FakeCommand] = []

    def enqueue_status(self, status: Dict[str, Any]) -> None:
        self._statuses.append(deepcopy(status))

    def next_status(self) -> Dict[str, Any]:
        if not self._statuses:
            raise AssertionError("fake Moonraker script exhausted")
        return deepcopy(self._statuses.popleft())

    def issue(self, name: str, expected_states: Iterable[str]) -> FakeCommand:
        command = FakeCommand(str(name), tuple(str(item) for item in expected_states))
        self.commands.append(command)
        return command

    @property
    def remaining(self) -> int:
        return len(self._statuses)
