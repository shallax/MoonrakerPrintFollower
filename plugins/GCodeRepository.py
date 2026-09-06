from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


JobKey = Tuple[str, int, int]


@dataclass
class CachedGCode:
    filename: Optional[str] = None
    path: Optional[str] = None
    job_key: Optional[JobKey] = None

    def matches(self, filename: str, job_key: Optional[JobKey]) -> bool:
        return bool(self.path and self.filename == filename and self.job_key == job_key)


class GCodeRepository:
    """Single owner for the active job's downloaded G-code identity."""

    def __init__(self) -> None:
        self.cached = CachedGCode()

    def adopt(self, filename: str, path: str, job_key: Optional[JobKey]) -> Optional[str]:
        previous = self.cached.path
        self.cached = CachedGCode(str(filename), str(path), job_key)
        return previous

    def discard(self) -> Optional[str]:
        previous = self.cached.path
        self.cached = CachedGCode()
        return previous
