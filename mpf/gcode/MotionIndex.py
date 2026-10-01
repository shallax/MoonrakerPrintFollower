"""Indexed motion data and read-only matching queries. No files, workers or presentation."""
from __future__ import annotations


import math
import threading

from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import ArcGeometry


# How far below the monotonic floor the refinement search may start, in
# motions. Generous enough to cover a parser-chunk lead and any earlier
# floor overshoot; the monotonic clamp is applied to the result. A
# boundary that consecutive polls fail to confirm widens the reach — a
# fixed one leaves an overshoot larger than it permanently uncorrectable,
# because the nozzle's own geometry never re-enters the window.
FLOOR_LOOKBACK = 256
# How far behind the coarse file position a layer's FIRST observation may
# look, in motions. Without an accepted boundary the parser's own lead is
# the only handle on where the head is, and a read burst can put it many
# hundreds of motions ahead of the nozzle — past the ordinary lag window
# entirely. The search then sees only motions the head has not reached,
# and on repeated geometry picks whichever later pass shares its XY. Such
# a match seeds the floor above the head and every later poll confirms it,
# so the fill cannot recover. The bound mirrors the payload fallback's own
# first-decode window.
FIRST_SEARCH_LOOKBACK = 8192
# How near a rival candidate must be to count as the SAME physical place.
# A repeated toolpath, a shared seam vertex or a retraced stroke puts the
# nozzle's coordinate on more than one motion of one layer, and the two
# distances then differ only by float noise — square units, so 1.02 is a
# ~1% distance tolerance.
_CANDIDATE_TIE = 1.02


def candidate_distance_limit_sq(best_distance_sq):
    """An upper bound beyond which a candidate cannot improve or tie."""
    return best_distance_sq + max(1e-8, best_distance_sq * (_CANDIDATE_TIE - 1.0))


def better_candidate(distance_sq, motion, best_distance_sq, best_motion, floor):
    """Resolve a search candidate against the best found so far.

    Strictly closer geometry always wins. A near TIE — one physical place
    reached by more than one motion, which is what repeated geometry looks
    like — is resolved by the boundary the search already trusts instead
    of by scan order: the candidate AT OR ABOVE ``floor``, and among those
    the smallest. A stroke the nozzle has not reached must never win a tie
    merely for lying inside the window, and an earlier pass the nozzle has
    already left must never win one either — it clamps the boundary below
    the paint and stalls the fill. When every tied candidate is below the
    floor, the largest wins: the one closest behind it is the overshoot
    lock's evidence that the floor itself has run ahead of the nozzle.

    ``floor`` is the accepted motion count, or None on a layer's first
    observation, where the earliest tied candidate is the honest choice —
    the dispatcher leads the nozzle, so an earlier pass is likelier than a
    later one and neither carries any evidence to prefer.

    Returns the surviving (distance_sq, motion) pair.
    """
    if best_motion is None:
        return distance_sq, motion
    # Symmetric ties: a float-noise improvement is no more evidence of a
    # later pass than a float-noise worsening. The absolute term covers
    # single-precision coordinate error near an exact zero-distance match.
    tolerance = max(1e-8, min(distance_sq, best_distance_sq) * (_CANDIDATE_TIE - 1.0))
    if distance_sq < best_distance_sq - tolerance:
        return distance_sq, motion
    if distance_sq > best_distance_sq + tolerance:
        return best_distance_sq, best_motion
    new_ok = floor is None or motion >= floor
    old_ok = floor is None or best_motion >= floor
    if new_ok != old_ok:
        return (distance_sq, motion) if new_ok else (best_distance_sq, best_motion)
    if new_ok:
        if motion < best_motion:
            return distance_sq, motion
    elif motion > best_motion:
        return distance_sq, motion
    return best_distance_sq, best_motion


def behind_reach(stall: int) -> int:
    """How far below the accepted boundary one search may look, in
    motions. The base reach covers a parser chunk plus a small overshoot;
    consecutive polls that failed to confirm the boundary widen it in
    steps, so a floor that ran far ahead still gets to see the geometry
    the nozzle is actually on. Without the ladder the correction can never
    fire and the fill waits for the nozzle to catch up."""
    if stall >= 4:
        return FLOOR_LOOKBACK * 128
    if stall >= 2:
        return FLOOR_LOOKBACK * 16
    return FLOOR_LOOKBACK
# The fraction-to-motion-count projection's tolerance. A boundary held
# in COUNT units round-trips through the fraction as a division and can
# land a hair under its own integer (1/3 * 3 == 0.9999999999999999);
# truncating that would drop a motion the boundary already counted. Far
# below one motion's share of the layer, so it can never round a
# half-finished motion up.
_SPLIT_EPSILON = 1e-9


@dataclass
class LayerMotionIndex:
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    motion_offsets: List[array] = field(default_factory=list)
    motion_x: List[array] = field(default_factory=list)
    motion_y: List[array] = field(default_factory=list)
    motion_z: List[array] = field(default_factory=list)
    # The sparse arc descriptors, one mapping per layer: motion index ->
    # (plane, clockwise, offset-a, offset-b). A G2/G3 stays ONE motion —
    # its index, its E, its feature and its ownership are the same as any
    # G1's — and the descriptor is what says the head curved on the way.
    # Everything else (a real file's motions, overwhelmingly) is absent
    # from the mapping, which is why it is a mapping and not three more
    # per-motion arrays. The physical geometry is derived from it on
    # demand by ArcGeometry, never stored here.
    motion_arcs: List[Dict[int, tuple]] = field(default_factory=list)
    # The modal arc plane (17/18/19) at each layer's first motion. The
    # plane is modal across the whole file, so a G18 in the start g-code
    # still governs layer 50, and a compact hydration that began at that
    # layer would otherwise parse its arcs as XY.
    layer_start_arc_plane: List[int] = field(default_factory=list)
    # The per-motion feature type, one RLE run list per layer: runs of
    # [motion_count, code] in motion order, so a layer costs one run per
    # ;TYPE: block instead of a byte per motion (~3 KB against ~0.44 MB
    # on a 444k-motion file) and the cache never widens its byte body.
    # Code _TYPE_NONE is "no ;TYPE: seen yet", _TYPE_OTHER the vocabulary
    # overflow, and code n + 2 names type_names[n].
    motion_types: List[List[List[int]]] = field(default_factory=list)
    # The ;TYPE: values in first-seen order. Shared by every layer, so one
    # feature keeps one code (and one colour) for the whole print. The
    # per-motion code defaults to the last value seen — a slicer emits a
    # marker when the feature changes, not per move.
    type_names: List[str] = field(default_factory=list)
    # Travel boundaries from the E axis, as motion indices per layer: a
    # start is the motion where E left the extruding state (holding flat,
    # or falling for a retraction) and an end the motion where E resumed
    # rising. The glyphs are exactly these two lists; the travel segment
    # between a start and its end is theirs to pair (a travel that crosses
    # a layer boundary leaves its start in the previous layer, which is
    # what layer_start_extruding tells apart).
    travel_starts: List[List[int]] = field(default_factory=list)
    travel_ends: List[List[int]] = field(default_factory=list)
    motion_extrusion: List[array] = field(default_factory=list)
    layer_heights: List[float] = field(default_factory=list)
    filament_diameter: float = 1.75
    filament_diameters: dict = field(default_factory=dict)
    motion_speeds: List[array] = field(default_factory=list)
    motion_tools: List[array] = field(default_factory=list)
    layer_start_speeds: List[float] = field(default_factory=list)
    layer_start_tools: List[int] = field(default_factory=list)
    colour_ranges: dict = field(default_factory=dict)
    extruder_events: List[List[Tuple[int, bool]]] = field(default_factory=list)
    layer_start_retracted: List[bool] = field(default_factory=list)
    layer_start_retractions: List[dict] = field(default_factory=list)
    firmware_retractions: List[list] = field(default_factory=list)
    layer_start_positions: List[Tuple[float, float, float]] = field(default_factory=list)
    layer_start_absolute: List[bool] = field(default_factory=list)
    layer_start_units: List[float] = field(default_factory=list)
    # The feature state at each layer's first motion, the compact
    # hydration's seed: without it a hydrated layer parses its opening
    # moves from a cold start and disagrees with the full scan it stands
    # in for (an absolute-E file reads its first move as a huge extrusion,
    # and a travel crossing the boundary loses its end marker).
    layer_start_types: List[int] = field(default_factory=list)
    layer_start_e: List[float] = field(default_factory=list)
    layer_start_e_absolute: List[bool] = field(default_factory=list)
    layer_start_extruding: List[bool] = field(default_factory=list)
    current_layer_map: Dict[int, int] = field(default_factory=dict)
    layer_elapsed_times: List[Optional[float]] = field(default_factory=list)
    # The layers whose gcode carries a baked pause command (PAUSE / M0 /
    # M25 as the line's command word) — 0-based layer indices, in
    # ascending order.
    pauses: Tuple[int, ...] = ()
    compact: bool = False
    hydrated_layers: set[int] = field(default_factory=set, repr=False)
    # One int per layer: the motion count as of the last hydration or
    # build. Eviction wipes the motion arrays (the retention bound's
    # whole point) but never this — a seek to an evicted layer still
    # resolves its FULL split and slider total instantly.
    layer_motion_counts: List[int] = field(default_factory=list)
    # The LIVE print's layer — the retention window's anchor, updated
    # by the service every poll even when that layer is already
    # hydrated. Runtime state: never saved to or restored from the
    # cache.
    followed_layer: Optional[int] = field(default=None, repr=False)
    # The follower's FROZEN layer (the pop-over's detach): a second
    # retention anchor, kept beside the live one. Runtime state, like
    # followed_layer — never saved to or restored from the cache.
    manual_anchor: Optional[int] = field(default=None, repr=False)
    cache_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __bool__(self) -> bool:
        return bool(self.ranges)

    def layer_count(self) -> int:
        return len(self.ranges)

    def motion_count(self, layer: int) -> int:
        if layer < 0:
            return 0
        if layer < len(self.layer_motion_counts):
            return self.layer_motion_counts[layer]
        if layer < len(self.motion_offsets):
            return len(self.motion_offsets[layer])
        return 0

    def file_fraction(self, layer: int, file_position: int) -> Tuple[float, str]:
        if layer < 0 or layer >= len(self.ranges):
            return 0.0, "no index"
        start, end = self.ranges[layer]
        if end <= start:
            return 0.0, "invalid range"
        position = max(start, min(int(file_position), end))
        motions = self.motion_offsets[layer]
        if motions:
            return max(0.0, min(1.0, bisect_right(motions, position) / len(motions))), "motion index"
        return max(0.0, min(1.0, (position - start) / (end - start))), "byte position"

    def refined_fraction(
        self,
        layer: int,
        file_position: int,
        live_position: Optional[Sequence[float]],
        *,
        lag_window: int = 1024,
        ahead_window: int = 8,
        max_distance_mm: float = 3.0,
        minimum_fraction: Optional[float] = None,
        floor_motion: Optional[int] = None,
        stall: int = 0,
        extruding: Optional[bool] = None,
    ) -> Tuple[float, str]:
        """Estimate physical progress using Moonraker's live tool position.

        ``file_position`` remains the authoritative coarse locator.  We only
        search a bounded neighbourhood around that location, biased backwards
        because Klipper's parser/lookahead is normally ahead of the physical
        nozzle.  If no nearby motion segment plausibly matches the live tool
        position, the last refined value is held when one exists, so the
        monotonic floor is never inflated by the parser-position fraction;
        on the first observation of a layer there is nothing to hold and the
        parser-position fraction is the only estimate available.

        ``floor_motion`` is the boundary the caller has already accepted,
        and it does two jobs the returned value must not be confused with.
        It resolves a near tie between candidates at the same physical
        place (see ``better_candidate``), and it bounds how far behind
        itself the search reaches, widening with ``stall`` — the count of
        consecutive polls that failed to confirm it. ``minimum_fraction``
        is the separate, optional clamp on the RETURNED value; a caller
        that wants the unclamped truth reads it unfloored and floors only
        for publication.
        """

        base_fraction, base_method = self.file_fraction(layer, file_position)
        floor_fraction: Optional[float] = None
        if minimum_fraction is not None:
            try:
                floor_fraction = max(0.0, min(1.0, float(minimum_fraction)))
            except (TypeError, ValueError):
                floor_fraction = None
        accepted: Optional[int] = None
        if floor_motion is not None:
            try:
                accepted = max(0, int(floor_motion))
            except (TypeError, ValueError):
                accepted = None
        try:
            stall = max(0, int(stall))
        except (TypeError, ValueError):
            stall = 0

        def with_floor(fraction: float, method: str) -> Tuple[float, str]:
            if floor_fraction is not None and fraction < floor_fraction:
                return floor_fraction, f"{method} (monotonic)"
            return fraction, method

        if live_position is None or len(live_position) < 3:
            return with_floor(base_fraction, base_method)
        if layer < 0 or layer >= len(self.motion_offsets):
            return with_floor(base_fraction, base_method)

        offsets = self.motion_offsets[layer]
        xs = self.motion_x[layer] if layer < len(self.motion_x) else array("f")
        ys = self.motion_y[layer] if layer < len(self.motion_y) else array("f")
        zs = self.motion_z[layer] if layer < len(self.motion_z) else array("f")
        n = len(offsets)
        if n == 0 or len(xs) != n or len(ys) != n or len(zs) != n:
            return with_floor(base_fraction, base_method)
        extrusion = self.motion_extrusion[layer] if layer < len(self.motion_extrusion) else ()
        match_extrusion = extruding is True and len(extrusion) == n
        if accepted is None and floor_fraction is not None:
            # A caller naming only the clamp is naming its accepted
            # boundary too — the two were one argument until the
            # overshoot lock needed them apart.
            accepted = max(0, min(n, int(math.floor(floor_fraction * n))))

        try:
            px, py, pz = float(live_position[0]), float(live_position[1]), float(live_position[2])
        except (TypeError, ValueError):
            return with_floor(base_fraction, base_method)

        coarse_completed = bisect_right(offsets, int(file_position))
        if accepted is None:
            # The first observation of a layer: reach back far enough that
            # the geometry under the head is in the window at all, whatever
            # the parser's lead. A bare lag_window lets the search pick a
            # later pass of repeated geometry, which seeds the floor above
            # the head — the one place a wrong answer cannot be corrected
            # from, because every later poll confirms it.
            lo = max(0, coarse_completed - max(max(1, int(lag_window)),
                                               FIRST_SEARCH_LOOKBACK) - 1)
        else:
            # Live XYZ can match more than one place on a closed/repeated toolpath.
            # The monotonic clamp is applied to the *result* below, which is what
            # prevents rewind; the search may dip a bounded distance below the
            # floor so an inflated floor sample can never exclude the true
            # segment (which previously cascaded into permanent coarse fallback).
            # The accepted boundary is the anchor here, not the parser: a read
            # burst puts the coarse position past the nozzle, and a lower bound
            # derived from it excludes the geometry the head is actually on.
            lo = max(0, accepted - behind_reach(stall) - 1)
        hi = min(n - 1, coarse_completed + max(0, int(ahead_window)))
        if hi < lo:
            return with_floor(base_fraction, base_method)

        layer_start = (
            self.layer_start_positions[layer]
            if layer < len(self.layer_start_positions)
            else (float(xs[0]), float(ys[0]), float(zs[0]))
        )
        # A motion that commanded an arc is matched against the arc it
        # commanded, not against its endpoint chord: the nozzle is on the
        # curve for the whole move, so a chord reading would call the
        # true path "off-model" and hold the coarse fraction instead.
        # The lookup is skipped entirely for a layer with no arcs.
        arcs = self.motion_arcs[layer] if layer < len(self.motion_arcs) else None

        best_distance_sq = float("inf")
        best_completed = None
        for i in range(lo, hi + 1):
            if match_extrusion and extrusion[i] <= 1e-9:
                continue
            if i == 0:
                ax, ay, az = layer_start
            else:
                ax, ay, az = float(xs[i - 1]), float(ys[i - 1]), float(zs[i - 1])
            bx, by, bz = float(xs[i]), float(ys[i]), float(zs[i])
            arc = arcs.get(i) if arcs else None
            if arc is not None:
                hit = ArcGeometry.closest(arc, (ax, ay, az), (bx, by, bz), (px, py, pz))
                if hit is not None:
                    distance, t = hit
                    best_distance_sq, best_completed = better_candidate(
                        distance * distance, i + t, best_distance_sq,
                        best_completed, accepted)
                    continue
            dx, dy, dz = bx - ax, by - ay, bz - az
            length_sq = dx * dx + dy * dy + dz * dz
            if length_sq <= 1e-12:
                t = 1.0
                qx, qy, qz = bx, by, bz
            else:
                t = ((px - ax) * dx + (py - ay) * dy + (pz - az) * dz) / length_sq
                t = max(0.0, min(1.0, t))
                qx, qy, qz = ax + t * dx, ay + t * dy, az + t * dz
            ddx, ddy, ddz = px - qx, py - qy, pz - qz
            distance_sq = ddx * ddx + ddy * ddy + ddz * ddz
            best_distance_sq, best_completed = better_candidate(
                distance_sq, i + t, best_distance_sq, best_completed, accepted)

        if best_completed is None or math.sqrt(best_distance_sq) > max(0.1, max_distance_mm):
            # The live position is off-model (Z-lift, off-path movement) or
            # ambiguous. Hold the last refined value instead of jumping to the
            # parser-position fraction, which sits ahead of the nozzle and
            # inflates the monotonic floor into a cm-apart staircase. Without
            # a prior value (the first observation of a layer) the coarse
            # fraction is the only estimate available and seeds the floor.
            if floor_fraction is not None:
                return floor_fraction, "held (refined unavailable)"
            return with_floor(base_fraction, base_method)

        refined = max(0.0, min(1.0, float(best_completed) / n))
        return with_floor(refined, "live position")

    def refined_split(
        self,
        layer: int,
        file_position: int,
        live_position: Optional[Sequence[float]],
        *,
        minimum_split: Optional[int] = None,
        floor_split: Optional[int] = None,
        **kwargs,
    ) -> Tuple[Optional[int], str]:
        """The follower's printed/unprinted boundary from the live tool
        position: ``refined_fraction``'s answer, floored onto this
        layer's motion grid.

        Edge m is printed exactly when m < the returned count, so the
        boundary is the number of motions the NOZZLE has finished, not
        the number the parser has dispatched — the two differ by the
        lookahead, which is what made the painted fill run ahead of the
        head. The search itself is ``refined_fraction``'s (one
        algorithm: the window, the arc geometry, the off-model hold),
        so the plate and the Preview agree about where the head is.

        ``None`` means the live position could not refine — no
        telemetry, an off-model head with nothing yet to hold, or a
        layer the index cannot measure — and the caller keeps its own
        coarse boundary. The refinement corrects a boundary; it never
        invents one. ``minimum_split`` is the boundary already painted,
        and no result ever falls below it.

        ``floor_split`` is the boundary the caller has ACCEPTED, which is
        what the search resolves ties against and how far behind itself
        it reaches. It defaults to ``minimum_split``; a caller that wants
        the honest truth reads with ``minimum_split=None`` — no clamp on
        the answer — while still naming its accepted boundary here, so
        the search keeps the continuity it needs to reject a repeated
        stroke instead of losing it with the clamp.
        """
        n = self.motion_count(layer)
        if n <= 0:
            return None, "no motions"
        floor_fraction = None
        if minimum_split is not None:
            try:
                floor_fraction = max(0.0, min(1.0, max(0, int(minimum_split)) / n))
            except (TypeError, ValueError):
                floor_fraction = None
        accepted = floor_split if floor_split is not None else minimum_split
        fraction, method = self.refined_fraction(
            layer, file_position, live_position, minimum_fraction=floor_fraction,
            floor_motion=accepted, **kwargs
        )
        if not (method.startswith("live position") or method.startswith("held")):
            # The coarse estimate (the parser's own position) or a
            # refusal: the caller's boundary is at least as good.
            return None, method
        # Flooring the fraction is what keeps the fill behind the head:
        # a motion counted as complete is one the nozzle has finished.
        split = int(math.floor(fraction * n + _SPLIT_EPSILON))
        if minimum_split is not None:
            split = max(split, int(minimum_split))
        return max(0, min(n, split)), method

    def partial_motion(self, layer, split, live_position):
        """Project onto only the accepted unfinished motion; never search ahead."""
        if live_position is None or split is None or split < 0 or layer < 0:
            return 0.0
        if layer >= len(self.motion_x) or split >= len(self.motion_x[layer]):
            return 0.0
        xs, ys, zs = self.motion_x[layer], self.motion_y[layer], self.motion_z[layer]
        if len(ys) != len(xs) or len(zs) != len(xs):
            return 0.0
        start = self.layer_start_positions[layer] if split == 0 and layer < len(self.layer_start_positions) \
            else (xs[split - 1], ys[split - 1], zs[split - 1]) if split > 0 else (xs[0], ys[0], zs[0])
        end = (xs[split], ys[split], zs[split])
        try:
            point = tuple(float(value) for value in live_position[:3])
            if len(point) != 3 or not all(math.isfinite(value) for value in point):
                return 0.0
            arcs = self.motion_arcs[layer] if layer < len(self.motion_arcs) else {}
            if split in arcs:
                hit = ArcGeometry.closest(arcs[split], start, end, point)
                return hit[1] if hit is not None and hit[0] <= 3.0 else 0.0
            delta = tuple(b - a for a, b in zip(start, end, strict=True))
            length = sum(value * value for value in delta)
            if length <= 1e-12:
                return 0.0
            t = max(0.0, min(1.0, sum((p - a) * d for p, a, d in zip(point, start, delta, strict=True)) / length))
            distance = sum((p - a - t * d) ** 2 for p, a, d in zip(point, start, delta, strict=True))
            return t if distance <= 9.0 else 0.0
        except (TypeError, ValueError, IndexError):
            return 0.0

    def layer_entry_confirmed(self, layer, candidate, live_position, previous_z=None,
                              continuous_z=False):
        """Whether physical Z distinguishes this match from the previous layer.

        XY can repeat exactly on successive layers while the parser has
        already entered the next one. Use the matched motion's height when
        hydrated; compact indices compare modal starts against the last
        confirmed physical height because layer markers may precede or
        follow their Z move. Equal-height/nonplanar or
        missing metadata cannot settle that ambiguity and defer to ordinary
        geometric matching rather than inventing physical evidence.
        """
        if layer <= 0 or candidate is None or live_position is None:
            return True
        observed_previous_z = previous_z
        try:
            previous_z = float(self.layer_start_positions[layer][2])
            heights = self.motion_z[layer] if layer < len(self.motion_z) else ()
            if len(heights):
                target_z = float(heights[min(len(heights) - 1, max(0, candidate - 1))])
                final_z = float(heights[-1])
            else:
                # A marker may occur before OR after its layer's Z move.
                # Compact modal starts alone cannot distinguish these.
                # The last physically confirmed height settles which start
                # describes the new layer; absent that evidence, XY owns it.
                if observed_previous_z is None:
                    return True
                start_z = float(observed_previous_z)
                target_z = float(self.layer_start_positions[layer][2])
                if abs(target_z - start_z) < 1e-4:
                    target_z = float(self.layer_start_positions[layer + 1][2])
                previous_z = start_z
            live_z = float(live_position[2])
        except (IndexError, TypeError, ValueError):
            return True
        if continuous_z and len(heights) and abs(final_z - previous_z) > 1e-4 \
                and abs(final_z - target_z) > 1e-4:
            # Spiralized layers climb throughout the path. The completed
            # motion's Z is behind the nozzle even for a correct XY match;
            # comparing the two heights rejects the first half of each
            # layer. Confirm entry from the layer's Z interval instead.
            tolerance = max(1e-3, abs(final_z - previous_z) * 0.02)
            if observed_previous_z is not None \
                    and abs(live_z - float(observed_previous_z)) <= tolerance:
                return False
            return min(previous_z, final_z) - tolerance <= live_z <= max(previous_z, final_z) + tolerance
        step = abs(target_z - previous_z)
        if step < 1e-4:
            return True
        return abs(live_z - target_z) <= max(1e-4, step * 0.2)
