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
    observation: Optional[PrintObservation] = None


class RemoteJobService:
    """Own remote print observation, run identity and same-file restart detection."""

    def __init__(self, active_states: Iterable[str]) -> None:
        self._active_states = {str(item) for item in active_states}
        self._state = RemoteJobState()

    @property
    def key(self) -> Optional[JobKey]:
        return self._state.key

    @property
    def serial(self) -> int:
        return self._state.serial

    @property
    def observation(self) -> Optional[PrintObservation]:
        return self._state.observation

    @property
    def printer_state(self) -> str:
        observation = self._state.observation
        return observation.state if observation is not None else ""

    @property
    def filename(self) -> str:
        observation = self._state.observation
        return observation.filename if observation is not None else ""

    def observe(
        self,
        print_stats: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
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

        observation = PrintObservation(
            state, filename, file_size, file_position, print_duration
        )
        previous = self._state.observation
        active = observation.state in self._active_states and bool(observation.filename)
        new_job = False
        if active:
            key = self._state.key
            if key is None:
                new_job = True
            elif key[0] != observation.filename or key[1] != observation.file_size:
                new_job = True
            elif previous is None or previous.state not in self._active_states:
                new_job = True
            elif observation.file_position < previous.file_position:
                new_job = True
            elif observation.print_duration + 0.05 < previous.print_duration:
                new_job = True

            if new_job:
                self._state.serial += 1
                self._state.key = (
                    observation.filename,
                    observation.file_size,
                    self._state.serial,
                )

        self._state.observation = observation
        return PrintTransition(self._state.key, new_job, self._state.serial)

    def reset(self) -> None:
        self._state = RemoteJobState()
