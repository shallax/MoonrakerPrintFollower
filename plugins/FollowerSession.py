from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class FollowerSession:
    """Small coordinator state separated from Cura widgets and transport."""

    machine_id: str = "unknown"
    machine_name: str = ""
    lifecycle_generation: int = 0
    last_invalidation: str = ""
    following_paused: bool = False
    remote_job_key: Optional[Tuple[str, int, int]] = None

    def bind_machine(self, machine_id: str, machine_name: str) -> None:
        self.machine_id = str(machine_id or "unknown")
        self.machine_name = str(machine_name or "")

    def invalidate(self, reason: str, generation: int) -> None:
        self.lifecycle_generation = int(generation)
        self.last_invalidation = str(reason or "")

    def set_job(self, key: Optional[Tuple[str, int, int]]) -> None:
        self.remote_job_key = key
