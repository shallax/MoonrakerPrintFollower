from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class CuraLifecycleState:
    generation: int = 0
    reason: str = "startup"


class CuraLifecycleBridge:
    """Own Cura-scene generation tokens used to reject stale async work."""

    def __init__(self) -> None:
        self.state = CuraLifecycleState()

    @property
    def generation(self) -> int:
        return self.state.generation

    @generation.setter
    def generation(self, value: int) -> None:
        self.state.generation = max(0, int(value or 0))

    def invalidate(self, reason: str) -> int:
        self.state.generation += 1
        self.state.reason = str(reason or "Cura lifecycle changed")
        return self.state.generation

    def token(self) -> int:
        return self.state.generation

    def is_current(self, token: int) -> bool:
        return int(token) == self.state.generation

    def guarded(self, token: int, callback: Callable[[], None]) -> bool:
        if not self.is_current(token):
            return False
        callback()
        return True
