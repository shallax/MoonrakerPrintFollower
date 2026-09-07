from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Dict, Optional, Set, Tuple

from .GCodeIndex import LayerMotionIndex


JobKey = Tuple[str, int, int]


@dataclass
class GCodeIndexState:
    generation: int = 0
    filename: Optional[str] = None
    job_key: Optional[JobKey] = None
    ranges: list = field(default_factory=list)
    motion_offsets: list = field(default_factory=list)
    current_layer_map: Dict[int, int] = field(default_factory=dict)
    data: Optional[LayerMotionIndex] = None
    build_filename: Optional[str] = None
    build_job_key: Optional[JobKey] = None
    cancel_event: Optional[threading.Event] = None
    thread: Optional[threading.Thread] = None
    hydrating_layers: Set[int] = field(default_factory=set)
    hydration_threads: Set[threading.Thread] = field(default_factory=set)


class GCodeIndexService:
    """Own active G-code index, build and hydration lifecycle state."""

    def __init__(self) -> None:
        self._state = GCodeIndexState()

    @property
    def generation(self) -> int:
        return self._state.generation

    @property
    def filename(self) -> Optional[str]:
        return self._state.filename

    @property
    def job_key(self) -> Optional[JobKey]:
        return self._state.job_key

    @property
    def ranges(self):
        return self._state.ranges

    @property
    def motion_offsets(self):
        return self._state.motion_offsets

    @property
    def current_layer_map(self):
        return self._state.current_layer_map

    @property
    def data(self) -> Optional[LayerMotionIndex]:
        return self._state.data

    @property
    def build_filename(self) -> Optional[str]:
        return self._state.build_filename

    @property
    def build_job_key(self) -> Optional[JobKey]:
        return self._state.build_job_key

    @property
    def thread(self) -> Optional[threading.Thread]:
        return self._state.thread

    @property
    def hydrating_layers(self) -> frozenset[int]:
        return frozenset(self._state.hydrating_layers)

    @property
    def hydration_threads(self) -> frozenset[threading.Thread]:
        return frozenset(self._state.hydration_threads)

    def invalidate_build(self) -> int:
        event = self._state.cancel_event
        if event is not None:
            event.set()
        self._state.cancel_event = None
        self._state.generation += 1
        self._state.build_filename = None
        self._state.build_job_key = None
        return self._state.generation

    def clear_index(self) -> None:
        self._state.filename = None
        self._state.job_key = None
        self._state.ranges = []
        self._state.motion_offsets = []
        self._state.current_layer_map = {}
        self._state.data = None
        self._state.hydrating_layers.clear()

    def install(
        self,
        filename: str,
        index: LayerMotionIndex,
        job_key: Optional[JobKey],
    ) -> bool:
        if not index:
            return False
        self._state.filename = str(filename)
        self._state.job_key = job_key
        self._state.data = index
        self._state.ranges = list(index.ranges)
        self._state.motion_offsets = list(index.motion_offsets)
        self._state.current_layer_map = dict(index.current_layer_map)
        return True

    def update_motion_offsets(self, index: LayerMotionIndex) -> None:
        if index is self._state.data:
            self._state.motion_offsets = list(index.motion_offsets)

    def begin_build(
        self,
        filename: str,
        job_key: Optional[JobKey],
        cancel_event: threading.Event,
        thread: threading.Thread,
    ) -> int:
        self._state.cancel_event = cancel_event
        self._state.thread = thread
        self._state.build_filename = str(filename)
        self._state.build_job_key = job_key
        return self._state.generation

    def finish_build(self) -> None:
        self._state.build_filename = None
        self._state.build_job_key = None
        self._state.cancel_event = None
        self._state.thread = None

    def clear_finished_thread(self, thread: Optional[threading.Thread]) -> None:
        if thread is not None and self._state.thread is thread and not thread.is_alive():
            self._state.thread = None

    def begin_hydration(self, layer: int) -> bool:
        layer = int(layer)
        if layer in self._state.hydrating_layers:
            return False
        self._state.hydrating_layers.add(layer)
        return True

    def finish_hydration(self, layer: int) -> None:
        self._state.hydrating_layers.discard(int(layer))
        self._state.hydration_threads = {
            thread for thread in self._state.hydration_threads if thread.is_alive()
        }

    def add_hydration_thread(self, thread: threading.Thread) -> None:
        self._state.hydration_threads = {
            item for item in self._state.hydration_threads if item.is_alive()
        }
        self._state.hydration_threads.add(thread)

    def clear_hydrations(self) -> None:
        self._state.hydrating_layers.clear()

    def clear_hydration_threads(self) -> None:
        self._state.hydration_threads.clear()
