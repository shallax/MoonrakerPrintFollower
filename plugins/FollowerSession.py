from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FollowerSession:
    """Small coordinator state that does not belong to a domain service."""

    machine_id: str = "unknown"
    machine_name: str = ""
    following_paused: bool = False

    def bind_machine(self, machine_id: str, machine_name: str) -> None:
        self.machine_id = str(machine_id or "unknown")
        self.machine_name = str(machine_name or "")
