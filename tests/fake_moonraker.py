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
            # History availability is discovered from the components
            # list; tests remove "history" to simulate it disabled.
            "components": ["history"],
        }
        self.metadata: Dict[str, Dict[str, Any]] = {}
        # File-manager state (round-2 E12): relpath -> entry, history
        # jobs most-recent-first, and the delete trail for assertions.
        self.files: Dict[str, Dict[str, Any]] = {}
        self.history_jobs: list[Dict[str, Any]] = []
        self.deleted: list[str] = []

    def set_file(self, relpath: str, *, name: Optional[str] = None, modified: float = 0.0,
                 size: int = 0, permissions: str = "rw", **metadata: Any) -> None:
        # Moonraker-shaped entries: basename only, NO path field —
        # the listing never echoes the directory (round-2 D3).
        entry: Dict[str, Any] = {
            "filename": str(name or relpath.rsplit("/", 1)[-1]),
            "modified": float(modified),
            "size": int(size),
            "permissions": str(permissions),
        }
        entry.update(metadata)
        self.files[str(relpath)] = entry

    def add_history_job(self, filename: str, *, status: str = "completed", exists: bool = True,
                        start_time: float = 0.0, end_time: Optional[float] = None, **extra: Any) -> None:
        job: Dict[str, Any] = {
            "filename": str(filename),
            "status": str(status),
            "exists": bool(exists),
            "start_time": float(start_time),
            "end_time": float(end_time) if end_time is not None else float(start_time),
        }
        job.update(extra)
        self.history_jobs.insert(0, job)

    @staticmethod
    def _query_args(path: str) -> Dict[str, str]:
        if "?" not in path:
            return {}
        return dict(pair.split("=", 1) for pair in path.split("?", 1)[1].split("&") if "=" in pair)

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
        if "server/files/directory" in path:
            # One directory level, Moonraker-shaped: the response does
            # NOT echo the requested path, and entries carry basenames
            # only — the walker must reconstruct (root, relpath) itself
            # (round-2 domain D3).
            args = self._query_args(path)
            requested = str(args.get("path", "gcodes") or "gcodes").strip("/")
            prefix = requested + "/" if requested else ""
            files = []
            dirs = []
            seen_dirs = set()
            for rel, entry in self.files.items():
                if not rel.startswith(prefix):
                    continue
                rest = rel[len(prefix):]
                if "/" in rest:
                    dirname = rest.split("/", 1)[0]
                    if dirname not in seen_dirs:
                        seen_dirs.add(dirname)
                        dirs.append({"dirname": dirname, "modified": 0.0, "size": 0, "permissions": "rw"})
                    continue
                files.append(entry)
            return {"result": {
                "files": deepcopy(sorted(files, key=lambda item: str(item["filename"]))),
                "dirs": dirs,
                "disk_usage": {"total": 8000000000, "used": 6800000000, "free": 1200000000},
                "root_info": {"name": requested or "gcodes", "permissions": "rw"},
            }}
        if "server/history/list" in path:
            args = self._query_args(path)
            try:
                limit = int(args.get("limit", "50"))
                start = int(args.get("start", "0"))
            except ValueError:
                limit, start = 50, 0
            window = self.history_jobs[start:start + max(0, limit)]
            return {"result": {"count": len(window), "jobs": deepcopy(window)}}
        if method == "DELETE" and "server/files/" in path:
            # DELETE /server/files/{root}/{filename} — root-inclusive,
            # and the fake keys files by the same root-inclusive
            # relpath. Moonraker refuses to delete the file being
            # printed with a 403; the fake models the refusal via
            # enqueue_error (the real-socket tests own the
            # HTTP-status truth).
            rel = path.split("server/files/", 1)[1]
            if rel in self.files:
                del self.files[rel]
                self.deleted.append(rel)
                return {"result": rel}
            return {"result": {}}
        if "server/files/move" in path:
            source = str((body or {}).get("source") or "")
            dest = str((body or {}).get("dest") or "")
            if source in self.files:
                entry = self.files.pop(source)
                self.files[dest] = entry
                return {"result": dest}
            return {"result": {}}
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
