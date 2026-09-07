from __future__ import annotations

from dataclasses import dataclass, field
import threading
from typing import Any, Dict, Optional, Set, Tuple

from .GCodeIndex import LayerMotionIndex


@dataclass
class GCodeIndexState:
    generation: int = 0
    filename: Optional[str] = None
    job_key: Optional[Tuple[str, int, int]] = None
    ranges: list = field(default_factory=list)
    motion_offsets: list = field(default_factory=list)
    current_layer_map: Dict[int, int] = field(default_factory=dict)
    data: Optional[LayerMotionIndex] = None
    build_filename: Optional[str] = None
    build_job_key: Optional[Tuple[str, int, int]] = None
    cancel_event: Optional[threading.Event] = None
    thread: Optional[threading.Thread] = None
    hydrating_layers: Set[int] = field(default_factory=set)
    hydration_threads: Set[threading.Thread] = field(default_factory=set)


class GCodeIndexService:
    """Authoritative owner of active G-code index/build/hydration state."""

    def __init__(self) -> None:
        self.state = GCodeIndexState()

    def invalidate_build(self) -> int:
        event = self.state.cancel_event
        if event is not None:
            event.set()
        self.state.cancel_event = None
        self.state.generation += 1
        self.state.build_filename = None
        self.state.build_job_key = None
        return self.state.generation

    def clear_index(self) -> None:
        self.state.filename = None
        self.state.job_key = None
        self.state.ranges = []
        self.state.motion_offsets = []
        self.state.current_layer_map = {}
        self.state.data = None
        self.state.hydrating_layers.clear()

    def install(
        self,
        filename: str,
        index: LayerMotionIndex,
        job_key: Optional[Tuple[str, int, int]],
    ) -> bool:
        if not index:
            return False
        self.state.filename = str(filename)
        self.state.job_key = job_key
        self.state.data = index
        self.state.ranges = list(index.ranges)
        self.state.motion_offsets = list(index.motion_offsets)
        self.state.current_layer_map = dict(index.current_layer_map)
        return True

    def begin_build(
        self,
        filename: str,
        job_key: Optional[Tuple[str, int, int]],
        cancel_event: threading.Event,
        thread: threading.Thread,
    ) -> int:
        self.state.cancel_event = cancel_event
        self.state.thread = thread
        self.state.build_filename = str(filename)
        self.state.build_job_key = job_key
        return self.state.generation

    def finish_build(self) -> None:
        self.state.build_filename = None
        self.state.build_job_key = None
        self.state.cancel_event = None
        self.state.thread = None

    def begin_hydration(self, layer: int) -> bool:
        layer = int(layer)
        if layer in self.state.hydrating_layers:
            return False
        self.state.hydrating_layers.add(layer)
        return True

    def finish_hydration(self, layer: int) -> None:
        self.state.hydrating_layers.discard(int(layer))
        self.state.hydration_threads = {t for t in self.state.hydration_threads if t.is_alive()}

    def add_hydration_thread(self, thread: threading.Thread) -> None:
        self.state.hydration_threads = {t for t in self.state.hydration_threads if t.is_alive()}
        self.state.hydration_threads.add(thread)
