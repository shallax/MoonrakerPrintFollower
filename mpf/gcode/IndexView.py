# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-20 — 6.0.0: Expose bounded extrusion-run evidence through the read-only query interface.
# 2026-10-13 [a2da858] Derive recovery clearance and support object-top Z homing
# 2026-10-13 [3f9fcb5] Begin v6 recovery with exact follower boundary mapping
# 2026-09-29 [2cd649b] Decompose indexing into explicit parser, cache and worker components

"""Read-only index queries, including continuous-Z layer evidence."""
from __future__ import annotations


from dataclasses import dataclass, field
from bisect import bisect_left
from itertools import pairwise
import math
from .ObjectWork import object_remaining, object_work_fraction
from types import MappingProxyType


from .MotionIndex import LayerMotionIndex
from . import ArcGeometry


@dataclass(frozen=True)
class RecoveryBoundary:
    """A scrubbed motion boundary, for inspection only.

    ``next_motion_offset`` names the original command that follows the
    selected completed-motion count. It is not an executable resume offset:
    intervening modal commands and printer state still need reconstruction.
    """
    selected_layer: int
    completed_motions: int
    next_layer: int
    next_motion: int
    next_motion_offset: int
    position: tuple[float, float, float]


@dataclass(frozen=True)
class ExtrusionRun:
    boundary: RecoveryBoundary
    motions: int
    distance_mm: float
    limited_by_seam: bool = False


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
    def printed_top_z(self): return self._index.printed_top_z
    @property
    def pause_layers(self): return tuple(self._index.pauses)

    def extrusion_run_before(self, layer, split, minimum_mm=5.0, minimum_moves=0):
        """Inspect a same-layer positive-extrusion run, with no mutable-array access.

        The result stops at non-extrusion, an unusable motion, or the requested
        distance/count. None means an exact retained run cannot be inspected.
        This is geometry evidence, not permission to execute a recovery.
        """
        try:
            minimum_mm = float(minimum_mm)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(minimum_mm) or minimum_mm <= 0 or type(minimum_moves) is not int or minimum_moves < 0:
            return None
        index = self._index
        if type(layer) is not int or not 0 <= layer < len(index.ranges):
            return None
        if type(split) is not int or not 0 < split <= index.motion_count(layer):
            return None
        with index.cache_lock:
            if not self._exact_layer(layer) or layer >= len(index.motion_extrusion):
                return None
            extrusions = index.motion_extrusion[layer]
            if len(extrusions) != index.motion_count(layer):
                return None
            distance = 0.0
            count = 0
            first_boundary = None
            start = index.layer_start_positions[layer]
            for motion in range(split - 1, -1, -1):
                if extrusions[motion] <= 0:
                    break
                end = (index.motion_x[layer][motion], index.motion_y[layer][motion],
                       index.motion_z[layer][motion])
                before = start if motion == 0 else (
                    index.motion_x[layer][motion - 1], index.motion_y[layer][motion - 1],
                    index.motion_z[layer][motion - 1])
                arc = index.motion_arcs[layer].get(motion) if layer < len(index.motion_arcs) else None
                length = (ArcGeometry.path_length(arc, before, end, xy=True) if arc
                          else math.hypot(end[0] - before[0], end[1] - before[1]))
                if not math.isfinite(length) or length <= 0:
                    break
                distance += length
                count += 1
                first_boundary = self.recovery_boundary(layer, motion)
                if first_boundary is None:
                    return None
                if (count >= minimum_moves if minimum_moves else distance >= minimum_mm):
                    return ExtrusionRun(first_boundary, count, distance)
            return (ExtrusionRun(first_boundary, count, distance, True)
                    if first_boundary is not None else None)

    def recovery_boundary(self, layer, completed_motions):
        """Map an integer scrub split to the next original motion.

        A compact or evicted layer has a motion count but no exact offsets;
        returning a byte-range estimate here would be unsafe. A selection at
        the end of a layer advances to the first motion of the next indexed
        layer, skipping empty layers. None means the exact mapping is not
        currently available or no motion follows the selection.
        """
        index = self._index
        if isinstance(layer, bool) or not isinstance(layer, int) or not 0 <= layer < len(index.ranges):
            return None
        total = index.motion_count(layer)
        if completed_motions == -1 and not isinstance(completed_motions, bool):
            completed_motions = total  # the follower's full-layer sentinel
        if isinstance(completed_motions, bool) or not isinstance(completed_motions, int) \
                or not 0 <= completed_motions <= total:
            return None
        with index.cache_lock:
            # The start position of a selected boundary needs the complete
            # preceding motion array, even if the next command is in a
            # different layer. A retained count alone cannot supply it.
            if not self._exact_layer(layer):
                return None
            position = (index.layer_start_positions[layer] if completed_motions == 0 else
                        (index.motion_x[layer][completed_motions - 1],
                         index.motion_y[layer][completed_motions - 1],
                         index.motion_z[layer][completed_motions - 1]))
            for next_layer in range(layer, len(index.ranges)):
                next_motion = completed_motions if next_layer == layer else 0
                if not self._exact_layer(next_layer):
                    return None
                offsets = index.motion_offsets[next_layer]
                if next_motion < len(offsets):
                    offset = int(offsets[next_motion])
                    start, end = index.ranges[next_layer]
                    if start <= offset < end:
                        return RecoveryBoundary(layer, completed_motions, next_layer,
                                                next_motion, offset, tuple(position))
                    return None
            return None

    def _exact_layer(self, layer):
        index = self._index
        return (layer < len(index.motion_offsets)
                and layer < len(index.layer_start_positions)
                and layer < len(index.motion_x)
                and layer < len(index.motion_y)
                and layer < len(index.motion_z)
                and len(index.motion_offsets[layer]) == index.motion_count(layer)
                and len(index.motion_x[layer]) == index.motion_count(layer)
                and len(index.motion_y[layer]) == index.motion_count(layer)
                and len(index.motion_z[layer]) == index.motion_count(layer)
                and (not index.compact or layer in index.hydrated_layers))

    def physical_file_offset(self, progress):
        """Map the accepted nozzle motion boundary to an indexed file offset."""
        if progress is None or progress.split is None or not 0 <= progress.layer < len(self._index.ranges):
            return None
        layer = progress.layer
        start, end = self._index.ranges[layer]
        split = max(0, int(progress.split))
        if split == 0:
            return start
        with self._index.cache_lock:
            offsets = self._index.motion_offsets[layer]
            if offsets and split <= len(offsets):
                return int(offsets[split - 1])
        if progress.motion_total > 0:
            fraction = min(1.0, (split + progress.partial) / progress.motion_total)
            return int(start + (end - start) * fraction)
        return None

    def object_metrics(self, offset, remaining_end=None):
        """Read-only per-object filament progress and projected finish seconds."""
        if not self._index.ranges:
            return {}
        offset = max(0, int(offset)) if offset is not None else None
        file_end = self._index.ranges[-1][1]
        return {name: {"top": row["top"],
                       "center": [(row["bounds"][0] + row["bounds"][2]) / 2,
                                  (row["bounds"][1] + row["bounds"][3]) / 2],
                       "bounds": list(row["bounds"]),
                       "progress": object_work_fraction(row, offset) if offset is not None else None,
                       "remaining": object_remaining(row, offset, file_end, remaining_end,
                                                      self._index.ranges, self._index.layer_elapsed_times)
                       if offset is not None else None}
                for name, row in self._index.object_work.items()}

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
