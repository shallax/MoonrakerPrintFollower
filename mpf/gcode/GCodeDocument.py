# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Own standalone indexes and publish immutable, complete layer snapshots.

"""An in-memory document independent of printer jobs and application caches."""
from collections.abc import Mapping
from concurrent.futures import CancelledError
from dataclasses import dataclass, field
from os import PathLike
from types import MappingProxyType

from .GCodeIndex import build_index_from_bytes, build_index_from_file
from .IndexView import IndexView, RecoveryBoundary
from .MotionIndex import LayerMotionIndex
from .PlateProgress import prepare_layer


@dataclass(frozen=True)
class LayerInfo:
    """Zero-based layer metadata; thickness is not absolute nozzle Z."""
    layer: int
    byte_range: tuple[int, int]
    motion_count: int
    indexed_motion_count: int
    layer_height_mm: float | None

    @property
    def complete(self) -> bool:
        return self.motion_count == self.indexed_motion_count


def _snapshot(value):
    # Prepared point/polyline tuples are already immutable. Copy their mutable
    # containers without walking or duplicating every vertex in a large layer.
    if isinstance(value, dict):
        return MappingProxyType({key: _snapshot(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_snapshot(item) for item in value)
    return value


@dataclass(frozen=True, eq=False)
class GCodeDocument:
    """Load through from_file/from_bytes; the private index never leaves this API.

    All retained motions live in memory. No source file is needed after loading.
    Large-file clients can instead opt into the advanced compact index API.
    """
    _index: LayerMotionIndex = field(repr=False, compare=False)

    @classmethod
    def from_file(cls, path: str | PathLike, *, cancel_event=None) -> "GCodeDocument":
        index = build_index_from_file(path, cancel_event, compact=False)
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("G-code loading cancelled")
        return cls(index)

    @classmethod
    def from_bytes(cls, data: bytes, *, cancel_event=None) -> "GCodeDocument":
        index = build_index_from_bytes(data, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError("G-code loading cancelled")
        return cls(index)

    @property
    def layer_count(self) -> int:
        return self._index.layer_count()

    def layer_info(self, layer: int) -> LayerInfo:
        if isinstance(layer, bool) or not isinstance(layer, int) or not 0 <= layer < self.layer_count:
            raise IndexError("layer must be an integer in the document's zero-based layer range")
        index = self._index
        return LayerInfo(layer, index.ranges[layer], index.motion_count(layer),
                         len(index.motion_offsets[layer]),
                         index.layer_heights[layer] if layer < len(index.layer_heights) else None)

    def geometry(self, layer: int) -> Mapping:
        """Return an immutable renderer payload; refuse scanner-truncated layers."""
        info = self.layer_info(layer)
        if not info.complete:
            raise ValueError("layer exceeds the indexer's retained-motion limit")
        with self._index.cache_lock:
            return _snapshot(prepare_layer(self._index, layer))

    def boundary(self, layer: int, completed_motions: int) -> RecoveryBoundary | None:
        """Inspect an exact boundary; None means no complete next motion exists.

        This does not reconstruct printer state or authorise a recovery move.
        """
        info = self.layer_info(layer)
        if isinstance(completed_motions, bool) or not isinstance(completed_motions, int) \
                or not 0 <= completed_motions <= info.motion_count:
            raise ValueError("completed_motions must be an integer within the layer")
        return IndexView((), self._index).recovery_boundary(layer, completed_motions)
