"""Qt-independent, immutable physical-print observations shared by all consumers."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Optional, Sequence

from .RemoteJobService import JobKey, PrintObservation


@dataclass(frozen=True)
class PhysicalLayer:
    index: Optional[int] = None  # zero-based; never the user's Preview selection
    total: Optional[int] = None
    height: Optional[float] = None  # absolute Z height, when Cura/metadata can resolve it
    source: str = ""
    thickness: Optional[float] = None  # material thickness of this layer, for Monitor/UI


@dataclass(frozen=True)
class PrintSnapshot:
    job_key: Optional[JobKey] = None
    observation: Optional[PrintObservation] = None
    layer: PhysicalLayer = PhysicalLayer()
    estimated_time: Optional[float] = None
    metadata_complete: bool = False

    @property
    def active(self) -> bool:
        return self.observation is not None and self.observation.state in {"printing", "paused"}


class LayerResolver:
    """Resolve once per observation; readers cannot advance fallback state.

    Exact G-code layer mapping/byte ranges precede geometry/metadata fallbacks.
    Z-only samples must advance extrusion, preventing Z-hop from changing layer.
    Geometry may supply a denominator but never clamps the physical observation.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self._extrusion = None
        self._z_layer = None

    @staticmethod
    def _number(value, convert=float):
        try:
            result = convert(value)
            return result if isfinite(result) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def resolve(self, status: Mapping, config, index=None, metadata: Optional[Mapping] = None,
                heights: Sequence[float] = ()) -> PhysicalLayer:
        stats = status.get("print_stats") or {}
        stats = stats if isinstance(stats, Mapping) else {}
        info = stats.get("info") or {}
        sd = status.get("virtual_sdcard") or {}
        move = status.get("gcode_move") or {}
        sd = sd if isinstance(sd, Mapping) else {}
        move = move if isinstance(move, Mapping) else {}
        info = info if isinstance(info, Mapping) else {}
        metadata = metadata or {}
        total = self._number(info.get("total_layer"), int)
        if index is not None and index.ranges:
            total = len(index.ranges)
        if total is None or total <= 0:
            total = self._number(metadata.get("layer_count"), int)
        if total is None or total <= 0:
            total = len(heights) or None

        raw = self._number(info.get("current_layer"), int)
        layer, source = None, ""
        if raw is not None:
            mapping = index.current_layer_map if index is not None else {}
            if raw in mapping:
                layer, source = int(mapping[raw]), "G-code mapped current_layer"
            else:
                layer = raw - int(bool(config.moonraker_layer_is_one_based))
                source = "Moonraker current_layer"
        if layer is None and index is not None:
            position = self._number(sd.get("file_position"), int)
            if position is not None:
                layer = index.layer_at(position)
                if layer is not None:
                    source = "G-code file position"

        position = move.get("gcode_position") or ()
        if config.z_fallback and isinstance(position, (list, tuple)) and len(position) >= 4:
            z, extrusion = self._number(position[2]), self._number(position[3])
            previous = self._extrusion
            self._extrusion = extrusion
            if z is not None and extrusion is not None and previous is not None and extrusion > previous + 0.0001:
                matches = [(abs(height - z), n) for n, height in enumerate(heights)
                           if height > 0 and abs(height - z) <= config.z_tolerance]
                if matches:
                    self._z_layer = min(matches)[1]
                else:
                    step = self._number(metadata.get("layer_height"))
                    first = self._number(metadata.get("first_layer_height")) or step
                    if step and step > 0 and first is not None:
                        self._z_layer = max(0, int(round((z - first) / step)))
            if layer is None and self._z_layer is not None:
                layer, source = self._z_layer, "extrusion-guarded Z height"
        if layer is not None:
            layer = max(0, layer)
        height = heights[layer] if layer is not None and layer < len(heights) else None
        step = self._number(metadata.get("layer_height"))
        first = self._number(metadata.get("first_layer_height")) or step
        if height is None and layer is not None and step and first:
            height = first + layer * step

        thickness = None
        if layer is not None:
            # Preserve v3.0 Monitor semantics: "Layer height" means the thickness
            # of the current layer, not its cumulative Z coordinate. Moonraker
            # metadata is authoritative when available; exact Cura/G-code Z
            # heights provide a useful adaptive-layer fallback.
            thickness = first if layer == 0 and first else step
            if thickness is None and layer < len(heights):
                current_height = self._number(heights[layer])
                previous_height = self._number(heights[layer - 1]) if layer > 0 else 0.0
                if current_height is not None and previous_height is not None:
                    delta = current_height - previous_height
                    if delta > 0:
                        thickness = delta
        return PhysicalLayer(layer, total, height, source, thickness)


