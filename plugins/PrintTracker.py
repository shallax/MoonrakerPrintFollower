from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


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


class PrintTracker:
    """Own print-run identity independently from Cura/UI state."""

    def __init__(self, active_states=("printing", "paused")) -> None:
        self.active_states = {str(item) for item in active_states}
        self.serial = 0
        self.key: Optional[JobKey] = None
        self.last_file_position: Optional[int] = None
        self.last_print_duration: Optional[float] = None

    def observe(self, observation: PrintObservation, *, previous_state: str = "") -> PrintTransition:
        active = observation.state in self.active_states and bool(observation.filename)
        new_job = False
        if active:
            if self.key is None:
                new_job = True
            elif self.key[0] != observation.filename or self.key[1] != observation.file_size:
                new_job = True
            elif previous_state not in self.active_states:
                new_job = True
            elif self.last_file_position is not None and observation.file_position < self.last_file_position:
                new_job = True
            elif self.last_print_duration is not None and observation.print_duration + 0.05 < self.last_print_duration:
                new_job = True
            if new_job:
                self.serial += 1
                self.key = (observation.filename, observation.file_size, self.serial)
            self.last_file_position = observation.file_position
            self.last_print_duration = observation.print_duration
        else:
            self.last_file_position = None
            self.last_print_duration = None
        return PrintTransition(self.key, new_job, self.serial)

    def reset(self) -> None:
        self.key = None
        self.last_file_position = None
        self.last_print_duration = None
