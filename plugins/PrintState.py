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
    # End-of-print remaining seconds anchored to the index's per-layer
    # timing and the observed speed ratio, when the coordinator can
    # compute it; None means "use the plain blend".
    layer_eta: Optional[float] = None
    # Within-layer progress from the index's byte ranges: the file
    # position between the layer's first and last byte. None without an
    # index (the nozzle's Z never moves within a layer, so Z cannot
    # express this).
    layer_progress: Optional[float] = None
    # The index view is built and usable (the Improve-ETA state).
    index_ready: bool = False
    # The monitor-only download's byte fraction (None while nothing is
    # downloading) and whether the index build is running.
    download_fraction: Optional[float] = None
    indexing: bool = False
    # ONE load state shared by the Preview and the Monitor: true from
    # any load request (either view's) until the terminal state.
    load_active: bool = False

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
        self._z_below = 0
        self._z_deltas = []
        self._prev_z = None
        self._z_ascent = 0.0
        self._z_layer_provisional = False
        self._step = None
        self._first = None
        self._z_rise_observations = 0
        self._z_rise_retired = False

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
            elif raw <= 0 and config.moonraker_layer_is_one_based:
                # Klipper reports current_layer=0 before the start-gcode runs
                # the first SET_PRINT_STATS_INFO. In one-based mode that is a
                # pre-print value, not layer zero; fall through to the
                # file-position/geometry paths instead of clamping to layer 0.
                layer, source = None, ""
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
        sd_progress = self._number(sd.get("progress"))
        if config.z_fallback and isinstance(position, (list, tuple)) and len(position) >= 4:
            z, extrusion = self._number(position[2]), self._number(position[3])
            # The extrusion baseline is recorded even while the file is
            # still queued, so the first post-start observation computes
            # immediately instead of waiting one more poll.
            previous = self._extrusion
            self._extrusion = extrusion
            # Z motion is tracked on EVERY observation — the baseline
            # observation has no previous extrusion but still records
            # the first Z. The provisional seed below additionally
            # requires a real extrusion advance, so z-hops can never
            # seed a layer.
            if self._prev_z is not None and z is not None:
                delta = z - self._prev_z
                if delta > 0.005:
                    self._z_ascent += delta
                    self._z_rise_observations += 1
                    # Provisional immediate seed: the author attaches
                    # mid-print and cannot wait for a full layer (some
                    # layers are huge). The FIRST Z increment seeds a
                    # step and a layer; a z-hop misread self-heals when
                    # its descent cancels the ascent below, and the
                    # plateau commit refines the step as more layers
                    # land. The seed step prefers the slicer header:
                    # the canonical branch below uses (z - first)/step,
                    # so seeding with the measured ascent as BOTH would
                    # disagree exactly on 150%-first-layer profiles —
                    # and a fragment of a partial ascent as the step
                    # seeds an absurd layer number (vase-mode rise).
                    if self._z_layer is None and not self._z_rise_retired \
                            and self._z_ascent >= 0.02 \
                            and (sd_progress is None or sd_progress >= 0.03) \
                            and extrusion is not None and previous is not None \
                            and extrusion > previous + 0.0001:
                        step = self._number(metadata.get("layer_height")) or self._z_ascent
                        first = self._number(metadata.get("first_layer_height")) or step
                        # Floor semantics, not round(): round() reads
                        # one layer AHEAD from ~60% through the layer
                        # and the one-at-a-time extrapolation can never
                        # pull it back — the whole readout (and every
                        # end-of-layer pause) fired early.
                        candidate = max(0, int((z - first) / step + 1e-6))
                        total_height = self._number(metadata.get("object_height"))
                        if total_height and total_height > 0:
                            candidate = min(candidate, int(total_height / step + 1e-6))
                        self._z_layer = candidate
                        self._z_layer_provisional = True  # provisional until confirmed
                        self._step = step
                        self._first = first
                    if self._z_layer_provisional and self._step \
                            and self._z_rise_observations >= 6 \
                            and self._z_ascent > 1.5 * self._step \
                            and extrusion is not None and previous is not None \
                            and extrusion > previous + 0.0001:
                        # The seeded step was a partial ascent, not a
                        # layer boundary: the rise continues poll after
                        # poll with no plateau (vase-mode continuous
                        # rise). Re-anchor on the best known step;
                        # without one, retire the guess — an inflated
                        # layer number is worse than the honest "—".
                        better = self._number(metadata.get("layer_height"))
                        if not better and self._z_deltas:
                            ordered = sorted(self._z_deltas)
                            better = ordered[len(ordered) // 2]
                        if better and better > 0:
                            first = self._number(metadata.get("first_layer_height")) or better
                            self._step = better
                            self._first = first
                            self._z_layer = max(0, int((z - first) / better + 1e-6))
                            self._z_layer_provisional = False
                        else:
                            self._z_layer = None
                            self._z_layer_provisional = False
                            self._step = None
                            self._first = None
                            self._z_rise_retired = True
                        self._z_rise_observations = 0
                elif delta < -0.005:
                    self._z_ascent = max(0.0, self._z_ascent + delta)
                    self._z_rise_observations = 0
                    self._z_rise_retired = False
                    if self._z_ascent <= 0.005 and self._z_layer_provisional:
                        # The ascent cancelled out: it was a z-hop,
                        # not a layer change. Clear the provisional.
                        self._z_layer = None
                        self._z_layer_provisional = False
                        self._step = None
                        self._first = None
                elif self._z_ascent > 0.01:
                    self._z_deltas.append(self._z_ascent)
                    if len(self._z_deltas) > 24:
                        self._z_deltas.pop(0)
                    self._z_ascent = 0.0
                    self._z_rise_observations = 0
                    self._z_rise_retired = False
            if z is not None:
                self._prev_z = z
            # The estimate itself only runs once the virtual SD has
            # actually started: while heating the nozzle parks at a
            # height that has nothing to do with the first layer, and
            # seeding the estimate from it read as "Layer 50" through
            # the whole print. A MISSING progress keeps the legacy path
            # (minimal or paused statuses); only the explicit 0.0 of a
            # queued file blocks. Before the file starts, the
            # print-start fallback below covers the honest "Layer 1".
            if (sd_progress is None or sd_progress > 0) and z is not None and extrusion is not None \
                    and previous is not None and extrusion > previous + 0.0001:
                resolved_heights = False
                if heights:
                    matches = [(abs(height - z), n) for n, height in enumerate(heights)
                               if height > 0 and abs(height - z) <= config.z_tolerance]
                    if matches:
                        self._z_layer = min(matches)[1]
                        self._z_layer_provisional = False
                        resolved_heights = True
                    else:
                        # Mid-layer positions match no layer-START height
                        # (the tolerance is tighter than the layer
                        # thickness): the layer is the last start below z.
                        below = [n for n, height in enumerate(heights)
                                 if height > 0 and height <= z + config.z_tolerance]
                        if below and below[-1] < len(heights) - 1:
                            self._z_layer = below[-1]
                            self._z_layer_provisional = False
                            resolved_heights = True
                        elif below and z <= heights[-1] + config.z_tolerance:
                            # Past the last known start but not above the
                            # model: the top layer.
                            self._z_layer = below[-1]
                            self._z_layer_provisional = False
                            resolved_heights = True
                        # Unresolved otherwise: a pause Z-lift (keep the
                        # last known layer) or heights from a DIFFERENT
                        # file in the Cura scene (fall through below).
                if not resolved_heights and (not heights or self._z_layer is None):
                    step = self._number(metadata.get("layer_height"))
                    if not step:
                        # Some slicer headers never declare a layer
                        # height: estimate it from the observed Z
                        # increments instead of showing "—" forever.
                        deltas = sorted(self._z_deltas)
                        if deltas:
                            step = deltas[len(deltas) // 2]
                    first = self._number(metadata.get("first_layer_height")) or step
                    if step and step > 0 and first is not None:
                        # Remember the resolved step: the height and
                        # thickness anchors at the end of resolve() need
                        # it even for never-downloaded prints.
                        self._step = step
                        self._first = first
                        candidate = max(0, int((z - first) / step + 1e-6))
                        total_height = self._number(metadata.get("object_height"))
                        if total_height and total_height > 0:
                            # The header's total print height bounds the
                            # estimate: an underestimated step must never
                            # claim a layer above the object itself.
                            candidate = min(candidate, int(total_height / step + 1e-6))
                        if self._z_layer is None:
                            # Two seed rules. Before ~3% progress the
                            # start-gcode parks at wipe heights (z=10+)
                            # while the file streams — seed only near
                            # the bed there. At significant progress the
                            # position IS the real print height (a
                            # mid-print connect, or a genuinely tall
                            # print), so seed freely; a wipe above it
                            # self-heals via the descent correction.
                            if sd_progress is not None and sd_progress >= 0.03:
                                self._z_layer = candidate
                            elif z <= max(first, step) * 2:
                                self._z_layer = candidate
                        elif candidate > self._z_layer:
                            # Without geometry the extrapolation can only move
                            # one layer at a time; a pause Z-lift would
                            # otherwise jump the physical layer by dozens.
                            self._z_layer += 1
                            self._z_layer_provisional = False
                            self._z_below = 0
                        elif candidate < self._z_layer:
                            # Printing never descends: a LOWER candidate
                            # is a return from a lift or a bad high seed.
                            # Correct only after three consecutive
                            # below-observations so the rounding jitter
                            # at a layer boundary cannot oscillate.
                            self._z_below += 1
                            if self._z_below >= 3:
                                self._z_layer = candidate
                                self._z_layer_provisional = False
                                self._z_below = 0
                        else:
                            self._z_below = 0
            if layer is None and self._z_layer is not None:
                layer, source = self._z_layer, "extrusion-guarded Z height"
        if layer is None and raw is not None and raw <= 0 and config.moonraker_layer_is_one_based \
                and str(stats.get("state") or "") in {"printing", "paused"} \
                and (sd_progress is None or sd_progress < 0.03):
            # An ACTIVE print at current_layer=0 and negligible progress
            # is at the very start (Klipper reports 0 until the first
            # SET_PRINT_STATS_INFO), so "Layer 1" is the honest reading.
            # Past ~3% progress the same signal means the G-code never
            # emits per-layer stats — claiming "Layer 1" then would be
            # a stuck lie, and "—" is the honest value.
            layer, source = 0, "print start"
        if layer is not None:
            layer = max(0, layer)
        height = heights[layer] if layer is not None and layer < len(heights) else None
        # The resolved step (header or measured) anchors the height and
        # thickness, so the layer progress bar works without a download.
        step = self._step or self._number(metadata.get("layer_height"))
        first = self._number(metadata.get("first_layer_height")) or step
        if height is None and layer is not None and step and first:
            height = first + layer * step

        thickness = None
        if layer is not None:
            # "Layer height" means this layer's material thickness, not its
            # cumulative Z coordinate. Prefer exact geometry so adaptive layer
            # heights remain accurate, then fall back to nominal metadata.
            if layer < len(heights):
                current_height = self._number(heights[layer])
                previous_height = self._number(heights[layer - 1]) if layer > 0 else 0.0
                if current_height is not None and previous_height is not None:
                    delta = current_height - previous_height
                    if delta > 0:
                        thickness = delta
            if thickness is None:
                thickness = first if layer == 0 and first is not None else step
        return PhysicalLayer(layer, total, height, source, thickness)
