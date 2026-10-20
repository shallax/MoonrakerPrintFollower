# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Own live index observation independently of build and hydration scheduling.

"""Live motion matching over read-only index and prepared geometry capabilities."""
from ...gcode.PlateSplitTracker import PlateSplitTracker
from ...gcode.MotionRefinement import refine_payload
from ...gcode.PlateProgress import split_index as _split_index
from ...printing.PrintState import MotionProgress


class IndexMotionTracking:
    def __init__(self):
        self.tracker = PlateSplitTracker()

    def reset(self):
        self.tracker.reset()

    def observe(self, view, payload, anchor, file_position, live_position=None, paused=False, extruding=None):
        """Accept one live observation independently of presentation readiness.

        The coordinator calls this once per frame and publishes the immutable
        result to both renderers. Manual plate scrubbing never enters here.
        """
        if view is None or not isinstance(anchor, int) \
                or not 0 <= anchor < len(view.ranges):
            return MotionProgress(anchor, None, 0, "unavailable")
        index = view._index
        with index.cache_lock:
            total = index.motion_count(anchor)
            split = self._observe_split(view, payload, anchor, file_position, live_position, paused, extruding)
            partial = 0.0 if self.tracker.awaiting_layer_entry else index.partial_motion(anchor, split, live_position)
        return MotionProgress(anchor, split, total,
                              "motion index" if split is not None else "unavailable", partial)


    def _observe_split(self, view, payload, anchor, file_position, live_position, paused, extruding):
        if file_position is None:
            return None
        index = view._index
        with index.cache_lock:
            coarse = _split_index(index, anchor, file_position)
            if coarse is None:
                return None
            # Resolve geometry in the index; the pure tracker is the ONLY
            # owner of accepted progress, continuity and overshoot evidence.
            tracker = self.tracker
            advanced = tracker.begin(view.job_key, anchor)
            floor = tracker.floor
            if paused and floor is not None:
                return tracker.accept(coarse, None, None, live_position is not None, paused=True)
            refined = None
            raw = None
            if live_position is not None:
                # On a monotonic spiral, Z itself orders the motions.
                # XY repeats around the vase and can match an earlier
                # seam, holding the first few percent at zero and
                # truncating the last few percent. Flat layers still
                # use the geometry search and its overshoot evidence.
                raw = view.spiral_z_split(anchor, live_position[2]) \
                    if extruding is True else None
                if raw is None:
                    # The index uses the previous boundary as a search
                    # anchor and returns an UNCLAMPED geometric match.
                    # The tracker needs behind-floor evidence to recover
                    # an erroneously advanced repeated path.
                    raw, _method = index.refined_split(
                        anchor, file_position, live_position,
                        minimum_split=None, floor_split=floor,
                        stall=tracker.stall_polls, extruding=extruding)
                refined = raw if floor is None or raw is None \
                    else max(raw, floor)
                if refined is None:
                    offsets = index.motion_offsets[anchor] \
                        if anchor < len(index.motion_offsets) else ()
                    if not len(offsets):
                        # Compact layers search the prepared payload's geometry;
                        # they use the same boundary policy as hydrated layers.
                        refined = refine_payload(
                            payload, coarse, live_position,
                            floor, ahead=tracker.payload_ahead_window,
                            stall=tracker.stall_polls,
                            extruding=extruding) if payload is not None else None
                        raw = refined
                        tracker.observe_payload_advance(refined)
            spiral = view.continuous_z_at(anchor)
            entry_confirmed = extruding is not False and index.layer_entry_confirmed(
                anchor, raw, live_position, previous_z=tracker.entry_previous_z,
                continuous_z=spiral) \
                if tracker.awaiting_layer_entry else True
            # The ordinary first poll is deliberately held: the parser
            # can claim a flat layer before the nozzle gets there. For a
            # continuously rising layer, a geometric match at its actual
            # Z is already physical entry evidence. Discarding it leaves
            # a visible gap at the start of every short spiral turn.
            spiral_entry = advanced and raw is not None and entry_confirmed \
                and spiral
            result = tracker.accept(
                coarse, refined, raw, live_position is not None,
                advanced=advanced and not spiral_entry, entry_confirmed=entry_confirmed)
            if raw is not None and live_position is not None and not tracker.awaiting_layer_entry:
                tracker.last_confirmed_z = float(live_position[2])
            return result

