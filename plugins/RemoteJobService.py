from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from .PrintTracker import PrintObservation, PrintTracker, PrintTransition


@dataclass
class RemoteJobState:
    key: Optional[Tuple[str, int, int]] = None


class RemoteJobService:
    """Authoritative owner of remote print-run identity and restart detection."""

    def __init__(self, active_states: Iterable[str]) -> None:
        self._tracker = PrintTracker(active_states)
        self.state = RemoteJobState()

    @property
    def serial(self) -> int:
        return self._tracker.serial

    @serial.setter
    def serial(self, value: int) -> None:
        self._tracker.serial = max(0, int(value or 0))

    @property
    def last_file_position(self) -> Optional[int]:
        return self._tracker.last_file_position

    @last_file_position.setter
    def last_file_position(self, value: Optional[int]) -> None:
        self._tracker.last_file_position = None if value is None else int(value)

    @property
    def last_print_duration(self) -> Optional[float]:
        return self._tracker.last_print_duration

    @last_print_duration.setter
    def last_print_duration(self, value: Optional[float]) -> None:
        self._tracker.last_print_duration = None if value is None else float(value)

    def observe(
        self,
        print_stats: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
        *,
        previous_state: str = "",
    ) -> PrintTransition:
        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")
        try:
            file_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        try:
            file_position = int(virtual_sdcard.get("file_position") or 0)
        except (TypeError, ValueError):
            file_position = 0
        try:
            print_duration = float(print_stats.get("print_duration") or 0.0)
        except (TypeError, ValueError):
            print_duration = 0.0

        transition = self._tracker.observe(
            PrintObservation(state, filename, file_size, file_position, print_duration),
            previous_state=str(previous_state or ""),
        )
        self.state.key = transition.key
        return transition

    def set_key(self, key: Optional[Tuple[str, int, int]]) -> None:
        self.state.key = key

    def reset(self) -> None:
        self._tracker.reset()
        self.state = RemoteJobState()
