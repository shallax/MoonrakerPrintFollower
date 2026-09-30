from __future__ import annotations

from dataclasses import dataclass, field


def due_end_of_layer_pauses(scheduled_layers, current_layer: int):
    """Return scheduled zero-based layers whose *end* has been crossed.

    A target layer is due only after Moonraker has advanced to a strictly later
    layer. Reaching the target layer itself must never pause at its beginning.
    """
    try:
        current = int(current_layer)
    except (TypeError, ValueError):
        return []

    due = []
    for raw_layer in scheduled_layers or ():
        try:
            layer = int(raw_layer)
        except (TypeError, ValueError):
            continue
        if layer < current:
            due.append(layer)
    return sorted(set(due))


@dataclass
class PauseScheduleState:
    layers: set[int] = field(default_factory=set)


class PauseScheduleService:
    """Own print-local end-of-layer PAUSE scheduling."""

    def __init__(self) -> None:
        self._state = PauseScheduleState()

    @property
    def layers(self) -> frozenset[int]:
        return frozenset(self._state.layers)

    def schedule(self, layer: int) -> bool:
        layer = int(layer)
        if layer < 0 or layer in self._state.layers:
            return False
        self._state.layers.add(layer)
        return True

    def remove(self, layer: int) -> bool:
        layer = int(layer)
        if layer not in self._state.layers:
            return False
        self._state.layers.remove(layer)
        return True

    def clear(self) -> int:
        count = len(self._state.layers)
        self._state.layers.clear()
        return count

    def consume_due(self, current_layer: int) -> list[int]:
        due = list(due_end_of_layer_pauses(self._state.layers, int(current_layer)))
        for layer in due:
            self._state.layers.discard(layer)
        return due

    def is_imminent(self, current_layer: int, *, lookahead_layers: int = 1) -> bool:
        try:
            current = int(current_layer)
            lookahead = max(0, int(lookahead_layers))
        except (TypeError, ValueError):
            return False
        return any(
            current <= int(layer) <= current + lookahead
            for layer in self._state.layers
        )
