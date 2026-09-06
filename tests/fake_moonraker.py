from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Deque, Dict, Iterable, Optional


@dataclass(frozen=True)
class FakeCommand:
    name: str
    expected_states: tuple[str, ...]


@dataclass(frozen=True)
class FakeRequest:
    method: str
    path: str
    body: Any = None


class FakeMoonraker:
    """Deterministic in-process Moonraker used by architecture tests.

    No sockets, threads or wall clock are involved. Tests can script status
    transitions, endpoint failures and command responses while inspecting every
    request that would have been issued.
    """

    def __init__(
        self,
        statuses: Iterable[Dict[str, Any]] = (),
        *,
        objects: Iterable[str] = ("print_stats", "virtual_sdcard", "gcode_move", "motion_report"),
    ) -> None:
        self._statuses: Deque[Dict[str, Any]] = deque(deepcopy(list(statuses)))
        self._errors: Dict[str, Deque[str]] = {}
        self.objects = list(objects)
        self.commands: list[FakeCommand] = []
        self.requests: list[FakeRequest] = []
        self.server_info: Dict[str, Any] = {
            "moonraker_version": "fake",
            "klippy_state": "ready",
        }
        self.metadata: Dict[str, Dict[str, Any]] = {}

    def enqueue_status(self, status: Dict[str, Any]) -> None:
        self._statuses.append(deepcopy(status))

    def enqueue_error(self, path_fragment: str, detail: str) -> None:
        self._errors.setdefault(str(path_fragment), deque()).append(str(detail))

    def set_metadata(self, filename: str, **values: Any) -> None:
        self.metadata[str(filename)] = deepcopy(values)

    def next_status(self) -> Dict[str, Any]:
        if not self._statuses:
            raise AssertionError("fake Moonraker script exhausted")
        return deepcopy(self._statuses.popleft())

    def issue(self, name: str, expected_states: Iterable[str]) -> FakeCommand:
        command = FakeCommand(str(name), tuple(str(item) for item in expected_states))
        self.commands.append(command)
        return command

    def request(self, method: str, path: str, body: Any = None) -> Dict[str, Any]:
        method = str(method or "GET").upper()
        path = str(path or "")
        self.requests.append(FakeRequest(method, path, deepcopy(body)))

        for fragment, errors in self._errors.items():
            if fragment in path and errors:
                raise RuntimeError(errors.popleft())

        if "printer/objects/query" in path:
            return {"result": {"status": self.next_status()}}
        if "printer/objects/list" in path:
            return {"result": {"objects": list(self.objects)}}
        if path.endswith("server/info") or "server/info" in path:
            return {"result": deepcopy(self.server_info)}
        if "server/files/metadata" in path:
            filename = path.split("filename=", 1)[-1]
            return {"result": deepcopy(self.metadata.get(filename, {}))}
        if "printer/gcode/script" in path:
            script = ""
            if isinstance(body, dict):
                script = str(body.get("script") or "")
            self.commands.append(FakeCommand(script or "gcode", ()))
            return {"result": "ok"}
        if "printer/print/pause" in path:
            self.commands.append(FakeCommand("Pause", ("paused",)))
            return {"result": {}}
        if "printer/print/resume" in path:
            self.commands.append(FakeCommand("Resume", ("printing",)))
            return {"result": {}}
        if "printer/print/cancel" in path:
            self.commands.append(FakeCommand("Cancel", ("cancelled", "complete", "standby")))
            return {"result": {}}
        return {"result": {}}

    def poll_session(self, session: Any, *, now: Optional[float] = None):
        payload = self.request("GET", "/printer/objects/query")
        status = payload["result"]["status"]
        return session.merge_status(status, now=now)

    @property
    def remaining(self) -> int:
        return len(self._statuses)
