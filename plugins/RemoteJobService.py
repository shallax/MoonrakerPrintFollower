from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple


JobKey = Tuple[str, int, int]


@dataclass(frozen=True)
class PrintObservation:
    state: str
    filename: str
    file_size: int
    file_position: int
    print_duration: float


@dataclass(frozen=True)
class PrintTransition:
    key: Optional[JobKey]
    new_job: bool
    serial: int


@dataclass
class RemoteJobState:
    key: Optional[JobKey] = None
    serial: int = 0
    last_file_position: Optional[int] = None
    last_print_duration: Optional[float] = None


class RemoteJobService:
    """Authoritative owner of remote print-run identity and restart detection."""

    def __init__(self, active_states: Iterable[str]) -> None:
        self.active_states = {str(item) for item in active_states}
        self.state = RemoteJobState()

    @property
    def serial(self) -> int:
        return self.state.serial

    @serial.setter
    def serial(self, value: int) -> None:
        self.state.serial = max(0, int(value or 0))

    @property
    def last_file_position(self) -> Optional[int]:
        return self.state.last_file_position

    @last_file_position.setter
    def last_file_position(self, value: Optional[int]) -> None:
        self.state.last_file_position = None if value is None else int(value)

    @property
    def last_print_duration(self) -> Optional[float]:
        return self.state.last_print_duration

    @last_print_duration.setter
    def last_print_duration(self, value: Optional[float]) -> None:
        self.state.last_print_duration = None if value is None else float(value)

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

        observation = PrintObservation(state, filename, file_size, file_position, print_duration)
        active = observation.state in self.active_states and bool(observation.filename)
        new_job = False
        if active:
            key = self.state.key
            if key is None:
                new_job = True
            elif key[0] != observation.filename or key[1] != observation.file_size:
                new_job = True
            elif previous_state not in self.active_states:
                new_job = True
            elif (
                self.state.last_file_position is not None
                and observation.file_position < self.state.last_file_position
            ):
                new_job = True
            elif (
                self.state.last_print_duration is not None
                and observation.print_duration + 0.05 < self.state.last_print_duration
            ):
                new_job = True
            if new_job:
                self.state.serial += 1
                self.state.key = (observation.filename, observation.file_size, self.state.serial)
            self.state.last_file_position = observation.file_position
            self.state.last_print_duration = observation.print_duration
        else:
            self.state.last_file_position = None
            self.state.last_print_duration = None

        return PrintTransition(self.state.key, new_job, self.state.serial)

    def set_key(self, key: Optional[JobKey]) -> None:
        self.state.key = key

    def reset(self) -> None:
        self.state = RemoteJobState()
