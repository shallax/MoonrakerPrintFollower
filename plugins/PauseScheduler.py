from __future__ import annotations

from typing import Iterable, MutableSet

from .Core import due_end_of_layer_pauses


class PauseScheduler:
    """Print-local end-of-layer PAUSE ownership with deterministic consumption."""

    def __init__(self, layers: MutableSet[int]) -> None:
        self._layers = layers

    @property
    def layers(self) -> set[int]:
        return set(self._layers)

    def schedule(self, layer: int) -> bool:
        layer = int(layer)
        if layer < 0 or layer in self._layers:
            return False
        self._layers.add(layer)
        return True

    def remove(self, layer: int) -> bool:
        layer = int(layer)
        if layer not in self._layers:
            return False
        self._layers.remove(layer)
        return True

    def clear(self) -> int:
        count = len(self._layers)
        self._layers.clear()
        return count

    def consume_due(self, current_layer: int) -> list[int]:
        due = list(due_end_of_layer_pauses(self._layers, int(current_layer)))
        for layer in due:
            self._layers.discard(layer)
        return due

    def is_imminent(self, current_layer: int, *, lookahead_layers: int = 1) -> bool:
        """Return True while a scheduled end-of-layer pause needs tight polling.

        A pause for layer N is sent after the observed layer advances beyond N.
        Tight polling starts no later than N-1 and remains active on N itself,
        minimizing the distance travelled into N+1 before PAUSE reaches Klipper.
        """
        try:
            current = int(current_layer)
            lookahead = max(0, int(lookahead_layers))
        except (TypeError, ValueError):
            return False
        return any(current <= int(layer) <= current + lookahead for layer in self._layers)

    def replace(self, layers: Iterable[int]) -> None:
        self._layers.clear()
        self._layers.update(max(0, int(layer)) for layer in layers)
