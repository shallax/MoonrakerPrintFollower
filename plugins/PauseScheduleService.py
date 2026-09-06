from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .Core import due_end_of_layer_pauses


@dataclass
class PauseScheduleState:
    layers: set[int] = field(default_factory=set)


class PauseScheduleService:
    """Authoritative owner of print-local end-of-layer PAUSE scheduling."""

    def __init__(self) -> None:
        self.state = PauseScheduleState()

    @property
    def layers(self) -> set[int]:
        return self.state.layers

    def schedule(self, layer: int) -> bool:
        layer = int(layer)
        if layer < 0 or layer in self.state.layers:
            return False
        self.state.layers.add(layer)
        return True

    def remove(self, layer: int) -> bool:
        layer = int(layer)
        if layer not in self.state.layers:
            return False
        self.state.layers.remove(layer)
        return True

    def clear(self) -> int:
        count = len(self.state.layers)
        self.state.layers.clear()
        return count

    def consume_due(self, current_layer: int) -> list[int]:
        due = list(due_end_of_layer_pauses(self.state.layers, int(current_layer)))
        for layer in due:
            self.state.layers.discard(layer)
        return due

    def is_imminent(self, current_layer: int, *, lookahead_layers: int = 1) -> bool:
        try:
            current = int(current_layer)
            lookahead = max(0, int(lookahead_layers))
        except (TypeError, ValueError):
            return False
        return any(current <= int(layer) <= current + lookahead for layer in self.state.layers)

    def replace(self, layers: Iterable[int]) -> None:
        self.state.layers.clear()
        self.state.layers.update(max(0, int(layer)) for layer in layers)
