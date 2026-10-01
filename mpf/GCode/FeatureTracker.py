"""One layer walk's extrusion, feature, travel and tool state."""
from __future__ import annotations

from .TravelStates import advance

import math

from array import array
from typing import Dict, List, Tuple

from . import ArcGeometry

from .IndexLimits import _MAX_MOTIONS_PER_LAYER, _MAX_TYPE_RUNS_PER_LAYER

_TYPE_NONE = 0
_TYPE_OTHER = 1


class _FeatureTracker:
    """The per-motion feature state, shared by the scan and the hydrator.

    The E axis decides what a motion is: a rise above the noise floor is an
    extrusion, anything else deposits nothing and is travel — a *falling* E
    is the same rule's retraction case, not a separate class. Each change
    in that state is a travel boundary, recorded as the motion index it
    happened on: a start where the extrusion stopped, an end where it
    resumed.

    The two parse loops must classify a motion identically — a hydrated
    layer that disagreed with the full scan it stands in for would draw a
    different preview — so the rule lives here once. The modal state (E,
    its absolute/relative mode, the open travel, the feature type, the arc
    plane) survives the layer boundaries, and ``open_layer`` hands the
    layer's own seed on to the compact hydrator, which starts mid-file with
    nothing else. The arc plane belongs here for the same reason as E: G17
    /G18/G19 is modal across the whole file, so a plane chosen long before
    a layer still decides what that layer's G2/G3 means.

    The layer's type runs are built here as well, but only a ``;TYPE:``
    marker writes one: the per-motion walk ticks a counter, and
    ``payload`` turns it into a run when the layer is read out.
    """

    __slots__ = ("runs", "starts", "ends", "count", "last_type", "span",
                 "open_type", "e", "absolute_e", "extruding", "start_type",
                 "start_e", "start_e_absolute", "start_extruding", "plane",
                 "start_plane", "events", "retracted", "start_retracted", "extrusions",
                 "speed", "tool", "start_speed", "start_tool", "speeds", "Tools", "metric_limits", "retractions", "start_retractions", "firmware_events")

    def __init__(self) -> None:
        self.runs: List[List[int]] = []
        self.starts: List[int] = []
        self.ends: List[int] = []
        self.count = 0
        self.last_type = _TYPE_NONE
        # The open run, held as (count, code) slots rather than as the
        # last element of ``runs``.
        self.span = 0
        self.open_type = _TYPE_NONE
        self.e = 0.0
        self.absolute_e = True
        # Nothing has been deposited before the first motion, but no
        # travel is open either: the first rise is a motion, not a
        # boundary.
        self.extruding = True
        self.start_type = _TYPE_NONE
        self.start_e = 0.0
        self.start_e_absolute = True
        self.start_extruding = True
        # G17 is the default plane: a file that never selects one is XY.
        self.plane = ArcGeometry.PLANE_XY
        self.start_plane = ArcGeometry.PLANE_XY
        self.events = []
        self.extrusions = array("f")
        self.retracted = self.start_retracted = False
        self.retractions, self.start_retractions = {}, {}
        self.firmware_events = []
        self.speed = self.start_speed = 0.0
        self.tool = self.start_tool = 0
        self.speeds, self.tools = array("f"), array("H")
        self.metric_limits = {}

    def open_layer(self) -> None:
        """Seed a new layer from the modal state and reset the counters."""
        self._flush()
        self.start_type = self.last_type
        self.start_e = self.e
        self.start_e_absolute = self.absolute_e
        self.start_extruding = self.extruding
        self.start_plane = self.plane
        self.start_retracted = self.retracted
        self.start_retractions = dict(self.retractions)
        self.firmware_events = []
        self.start_speed, self.start_tool = self.speed, self.tool
        self.speeds, self.tools = array("f"), array("H")
        self.metric_limits = {}
        self.events = []
        self.extrusions = array("f")
        self.runs = []
        self.starts = []
        self.ends = []
        self.count = 0
        self.span = 0
        self.open_type = self.last_type

    def set_type(self, code: int) -> None:
        """Adopt a ;TYPE: value, closing the run a different one opened.

        The type changes only on these markers, so this is the only place
        a run boundary can fall — the motion walk never writes the run
        list itself. A repeat of the current value (two names that both
        overflow the vocabulary share a code, and a slicer may re-state
        one) continues the open run instead of splitting it.
        """
        if code == self.last_type:
            return
        self._flush()
        self.last_type = code
        self.open_type = code

    def payload(self) -> Tuple[List[List[int]], List[int], List[int]]:
        """The layer's feature arrays, with the open run closed."""
        self._flush()
        return (self.runs, self.starts, self.ends)

    def _flush(self) -> None:
        """Materialize the open run — the count side of the RLE.

        Deferred on purpose: reaching into ``runs[-1]`` on every motion
        was the largest cost the feature walk added to the scan, while a
        slot increment is small. Only a type change or a layer boundary
        pays for the list, and those are per feature block, not per move.
        """
        span = self.span
        if not span:
            return
        self.span = 0
        runs = self.runs
        if len(runs) < _MAX_TYPE_RUNS_PER_LAYER:
            runs.append([span, self.open_type])
        else:
            # Run-capped: the tail's feature reads unknown rather than as
            # whatever block happened to be last.
            runs[-1][1] = _TYPE_OTHER
            runs[-1][0] += span

    def extruder_event(self, retract, collect, *, firmware=True):
        if collect and len(self.events) < _MAX_MOTIONS_PER_LAYER and (not self.events or self.events[-1] != (self.count, retract)):
            self.events.append((self.count, retract))
        self.retracted = retract
        if firmware:
            if collect and len(self.firmware_events) < _MAX_MOTIONS_PER_LAYER:
                self.firmware_events.append((self.count, self.tool, retract))
            self.retractions[self.tool] = -1.0 if retract else 0.0

    def add(self, axes: Dict[str, float], collect: bool, length=0.0) -> None:
        """Advance one motion's E and feature state.

        *collect* is False when the motion is not being recorded in the
        layer's arrays — a compact scan, or past the per-layer motion cap.
        The modal state advances either way, because the next layer's seed
        is read from it; only the appends stand down.
        """
        if "F" in axes and math.isfinite(axes["F"]) and 0 < axes["F"] <= 60e6:
            self.speed = axes["F"] / 60.0
        delta = 0.0
        if "E" in axes:
            value = axes["E"] if self.absolute_e else self.e + axes["E"]
            delta = value - self.e
            self.e = value
        if collect:
            self.extrusions.append(delta if math.isfinite(delta) and abs(delta) < 1e6 else 0.0)
            self.speeds.append(self.speed)
            self.tools.append(self.tool)
        if math.isfinite(delta) and delta > 0 and length > 1e-9:
            ratio = delta / length
            if math.isfinite(ratio) and 0 < ratio <= 1e6:
                limits = self.metric_limits.setdefault(self.tool, [math.inf, -math.inf] * 3)
                for i, value in enumerate((self.speed, ratio, ratio * self.speed)):
                    limits[i * 2] = min(limits[i * 2], value)
                    limits[i * 2 + 1] = max(limits[i * 2 + 1], value)
        advance(self.retractions, self.tool, delta if math.isfinite(delta) else 0.0)
        if delta < -1e-9:
            self.extruder_event(True, collect, firmware=False)
        elif delta > 1e-9 and self.retracted:
            self.extruder_event(False, collect, firmware=False)
        self.retracted = self.retractions.get(self.tool, 0.0) != 0
        rolling = delta > 0.0
        if rolling != self.extruding:
            if collect:
                (self.ends if rolling else self.starts).append(self.count)
            self.extruding = rolling
        if collect:
            self.span += 1
        self.count += 1
