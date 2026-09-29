"""Read-only index queries, including continuous-Z layer evidence."""
from __future__ import annotations


from dataclasses import dataclass, field
from bisect import bisect_left
from itertools import pairwise
import math
from types import MappingProxyType


from .MotionIndex import LayerMotionIndex


@dataclass(frozen=True)
class IndexView:
    """Read-only query capability, never mutable arrays or worker state."""
    job_key: tuple
    _index: LayerMotionIndex
    _spiral_cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def ranges(self): return tuple(self._index.ranges)
    @property
    def current_layer_map(self): return MappingProxyType(self._index.current_layer_map)
    @property
    def elapsed_times(self): return tuple(self._index.layer_elapsed_times)
    @property
    def compact(self): return self._index.compact
    @property
    def pause_layers(self): return tuple(self._index.pauses)

    def hydrated(self, layer):
        return not self.compact or layer in self._index.hydrated_layers

    def fraction(self, layer, position, live, minimum=None):
        """Stateless index query; live renderers consume snapshot motion progress."""
        return self._index.refined_fraction(layer, position, live, minimum_fraction=minimum)

    def _z_pattern(self, layer):
        """Return (whole-layer spiral, distributed late rise).

        A transition layer may print a flat region before winding upward.
        Its own progress still needs XY matching, but its *next* boundary
        must wait for the nozzle to reach the rising path's final height.
        """
        with self._index.cache_lock:
            if not 0 <= layer < len(self._index.motion_z) \
                    or layer >= len(self._index.layer_start_positions):
                return False, False
            heights = self._index.motion_z[layer]
            if len(heights) < 8:
                return False, False
            start = self._index.layer_start_positions[layer][2]
            finish = heights[-1]
            signature = (id(heights), len(heights), start, finish)
            cached = self._spiral_cache.get(layer)
            if cached is not None and cached[0] == signature:
                return cached[1]
            rise = finish - start
            quarter = heights[(len(heights) - 1) // 4]
            halfway_index = (len(heights) - 1) // 2
            halfway = heights[halfway_index]
            three_quarters = heights[(len(heights) - 1) * 3 // 4]
            late_rise = (rise >= 0.02
                         and three_quarters < finish - rise * 0.05
                         and three_quarters - halfway >= rise * 0.1
                         and sum(right > left + 1e-5 for left, right
                                 in pairwise(heights[halfway_index:])) >= 4
                         and all(left <= right + 1e-5 for left, right in pairwise(heights)))
            spiral = (late_rise
                      and start + max(0.01, rise * 0.1) < three_quarters
                      and halfway - quarter >= rise * 0.1)
            pattern = spiral, late_rise
            self._spiral_cache[layer] = signature, pattern
            return pattern

    def continuous_z_at(self, layer):
        """Whether Z climbs through the layer, allowing Z ordered progress."""
        return self._z_pattern(layer)[0]

    def continuous_z_boundary(self, layer):
        """The physical start of a layer following a continuous Z rise."""
        if layer <= 0 or layer >= len(self._index.layer_start_positions) \
                or not self._z_pattern(layer - 1)[1]:
            return None
        return self._index.layer_start_positions[layer][2]

    def spiral_z_split(self, layer, z):
        """Fallback motion count when XY matching misses a rising spiral."""
        try:
            z = float(z)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(z):
            return None
        with self._index.cache_lock:
            if not self.continuous_z_at(layer):
                return None
            heights = self._index.motion_z[layer]
            start = self._index.layer_start_positions[layer][2]
            if z < start - 0.001 or z > heights[-1] + 0.001:
                return None
            # Equal-Z edges may still be in flight; only strictly lower
            # endpoints are certainly complete.
            return bisect_left(heights, z)

    def layer_at(self, position):
        low, high = 0, len(self._index.ranges) - 1
        while low <= high:
            middle = (low + high) // 2
            start, end = self._index.ranges[middle]
            if position < start: high = middle - 1
            elif position >= end: low = middle + 1
            else: return middle
        return min(low - 1, len(self._index.ranges) - 1) if low else None
