"""Bounded live-position matching against immutable prepared geometry."""
from __future__ import annotations

import math
from .ArcGeometry import MAX_SAGITTA_MM
from .MotionIndex import better_candidate, candidate_distance_limit_sq


def refine_payload(payload, coarse, live_position, floor=None,
                         ahead=4096, stall=0, max_distance_mm=3.0,
                         extruding=None):
    """The live-position refinement over a PAYLOAD's geometry (the
    unhydrated compact layer): the index's own bounded search, run
    against the decoded polylines the plate already draws. Each
    segment contributes only its points inside the window —
    bisected, never walked. The seed is the monotonic FLOOR, not
    the byte-fraction coarse: the fraction drifts anywhere
    relative to the true motion, and a window centred on it
    missed the toolhead's geometry for stretches, stalling the
    fill until the drift slid the window over it (the live
    staircase report). The nozzle sits within a bounded advance
    of the floor; only the floor-less first poll of a layer
    searches wide around the coarse. Returns the RAW refined
    motion count, or None when the geometry or the match is
    absent (the caller holds the floor) — a below-floor result
    is the caller's overshoot-lock evidence, never silently
    clamped here."""
    if payload is None or live_position is None or len(live_position) < 3:
        return None
    try:
        px, py = float(live_position[0]), float(live_position[1])
    except (TypeError, ValueError):
        return None
    # The progressive windows: floor-seeded windows first (the
    # ahead side capped by the observed per-poll advance — a
    # fresh floor means the nozzle is a bounded step past it),
    # a wider one when the floor held through a travel, and the
    # coarse-centred wide pair for the layer's first observation.
    # CONSECUTIVE STALLED polls expand the ahead exponentially:
    # a clamped match (the earlier pass of repeated geometry)
    # never advanced the floor, so the window must reach the
    # true pass the nozzle has since moved on to — without the
    # expansion the stall self-perpetuates (the live report: a
    # 28 s stick that only a lucky tie-break ever ended).
    if floor is not None:
        ahead = max(512, int(ahead))
        # The window ladder pairs the AHEAD and the BEHIND sides:
        # the ahead side chases the nozzle past a held floor, and
        # the behind side is the overshoot-lock's EVIDENCE — the
        # truth behind an overshot floor sits hundreds of motions
        # back, and a 64-motion behind reach never found it, so
        # the correction never fired (the live report: huge
        # overshoots that never corrected).
        multipliers = [(1, 1), (8, 1)]
        if stall >= 2:
            multipliers += [(64, 16), (512, 128)]
        motions = int(payload.get("motions") or 0)
        windows = []
        for ahead_mult, behind_mult in multipliers:
            hi = int(floor) + ahead * ahead_mult
            if motions > 0:
                hi = min(hi, motions)
            windows.append((max(0, int(floor) - 64 * behind_mult), hi))
    else:
        windows = ((max(0, int(coarse) - 8192), int(coarse) + 8192),)
    best_distance_sq = float("inf")
    best_motion = None
    left = bottom = -float("inf")
    right = top = float("inf")

    def offer(distance_sq, motion):
        # The shared candidate comparison (the hydrated search runs
        # the same one): strict distance wins, a near tie resolves
        # by the floor instead of by scan order — the pass the
        # nozzle is on, never a future pass that merely lies inside
        # the window and never an earlier one that stalls the fill.
        nonlocal best_distance_sq, best_motion, left, right, bottom, top
        previous_distance_sq = best_distance_sq
        best_distance_sq, best_motion = better_candidate(
            distance_sq, motion, best_distance_sq, best_motion, floor)
        if best_distance_sq != previous_distance_sq:
            # Include the shared comparator's near-tie tolerance.
            # A candidate outside this box can neither improve nor
            # tie the current best; rejected candidates have no effect
            # on subsequent comparisons, even near the distance limit.
            reach = math.sqrt(candidate_distance_limit_sq(best_distance_sq))
            left, right = px - reach, px + reach
            bottom, top = py - reach, py + reach

    for lo, hi in windows:
        for segments in (payload.get("classes") or {}).values():
            for points in segments:
                if len(points) == 1 and lo <= points[0][2] <= hi:
                    # A travel split can leave an isolated vertex
                    # — the motion's only geometry. Skipping it
                    # made the TRUE motion invisible and the
                    # search picked a future pass instead (the
                    # replay's +403 overshoot on the live file).
                    vertex = points[0]
                    offer((px - vertex[0]) ** 2 + (py - vertex[1]) ** 2,
                          float(vertex[2]))
                if len(points) < 2:
                    continue
                # Polylines carry increasing motion numbers. Most runs
                # are outside this bounded window; reject them before
                # bisecting every run on every status publication.
                if points[-1][2] < lo or points[1][2] > hi:
                    continue
                # The manual bisect: the bundled engine's bisect
                # key compares the unkeyed needle, and an int <
                # list TypeError is what that yields (the live
                # traceback).
                low2, high2 = 0, len(points)
                while low2 < high2:
                    mid2 = (low2 + high2) // 2
                    if points[mid2][2] < lo:
                        low2 = mid2 + 1
                    else:
                        high2 = mid2
                begin = low2
                if begin >= len(points):
                    continue
                begin = max(0, begin - 1)
                for i in range(begin + 1, len(points)):
                    if points[i][2] > hi:
                        break
                    ax, ay = points[i - 1][0], points[i - 1][1]
                    bx, by = points[i][0], points[i][1]
                    # An edge whose bounding box misses the best match's
                    # neighbourhood cannot improve or tie that match.
                    # Keep the motion window and tie policy unchanged,
                    # but avoid projecting distant geometry on the UI thread.
                    if (ax < left and bx < left) or (ax > right and bx > right) \
                            or (ay < bottom and by < bottom) or (ay > top and by > top):
                        continue
                    dx, dy = bx - ax, by - ay
                    length_sq = dx * dx + dy * dy
                    if length_sq <= 1e-12:
                        t = 1.0
                        qx, qy = bx, by
                    else:
                        t = ((px - ax) * dx + (py - ay) * dy) / length_sq
                        t = max(0.0, min(1.0, t))
                        qx, qy = ax + t * dx, ay + t * dy
                    distance_sq = (px - qx) ** 2 + (py - qy) ** 2
                    # The edge i spans points[i-1] -> points[i]
                    # and belongs to motion points[i][2]: the
                    # completed count is that motion, fraction t.
                    offer(distance_sq, points[i][2] - 1 + t)
        # A match below the floor is NOT satisfying: it clamps,
        # stalls the fill, and — critically — must not stop the
        # wider windows from running. Only a match AT OR ABOVE
        # the floor (or any match on a floor-less first search)
        # settles the window.
        if best_motion is not None \
                and math.sqrt(best_distance_sq) <= max(0.1, max_distance_mm) \
                and (floor is None or best_motion >= floor):
            break
    if best_motion is None or math.sqrt(best_distance_sq) > max(0.1, max_distance_mm):
        return None
    # Prepared chords may sit a sagitta away from physical extrusion
    # while a travel intersects its XY exactly. Flow identifies the
    # deposited path, but cannot justify a distant future pass.
    if extruding is True and best_distance_sq <= (MAX_SAGITTA_MM + 0.001) ** 2:
        return int(best_motion)
    # The travel gate: the nozzle on a travel is off the
    # extrusion geometry — the nearest extrusion line can be a
    # FUTURE pass close enough to win the match, jumping the
    # split ahead and locking the overshoot into the floor (the
    # replay's +403 on the live file: the truth's own geometry
    # was the travel, absent from the classes). Nothing prints
    # during a travel: the honest answer is to HOLD, and the
    # nearest travel segment at ~0 is the tell.
    best_travel_sq = None
    for lo, hi in windows:
        for points in (payload.get("travels") or []):
            if len(points) < 2:
                continue
            if points[-1][2] < lo or points[1][2] > hi:
                continue
            low2, high2 = 0, len(points)
            while low2 < high2:
                mid2 = (low2 + high2) // 2
                if points[mid2][2] < lo:
                    low2 = mid2 + 1
                else:
                    high2 = mid2
            begin = low2
            if begin >= len(points):
                continue
            begin = max(0, begin - 1)
            for i in range(begin + 1, len(points)):
                if points[i][2] > hi:
                    break
                ax, ay = points[i - 1][0], points[i - 1][1]
                bx, by = points[i][0], points[i][1]
                if (ax < left and bx < left) or (ax > right and bx > right) \
                        or (ay < bottom and by < bottom) or (ay > top and by > top):
                    continue
                dx, dy = bx - ax, by - ay
                length_sq = dx * dx + dy * dy
                if length_sq <= 1e-12:
                    t = 1.0
                    qx, qy = bx, by
                else:
                    t = ((px - ax) * dx + (py - ay) * dy) / length_sq
                    t = max(0.0, min(1.0, t))
                    qx, qy = ax + t * dx, ay + t * dy
                distance_sq = (px - qx) ** 2 + (py - qy) ** 2
                if best_travel_sq is None or distance_sq < best_travel_sq:
                    best_travel_sq = distance_sq
    # The old 0.2 mm margin admitted future skin only 0.024 mm from
    # a live travel on the fine-layer print. Float-coordinate noise
    # needs a small tolerance, not half a normal extrusion width.
    if best_travel_sq is not None and math.sqrt(best_travel_sq) + 0.01 \
            < math.sqrt(best_distance_sq):
        return None
    # The RAW result: the caller clamps for publication — a
    # below-floor match is the overshoot-lock's evidence, and the
    # caller's correction path needs it unclamped (the live
    # replay: the refinement found the truth at distance 0 below
    # an overshot floor every poll, and the clamp stalled the
    # fill until the nozzle caught up).
    return int(best_motion)
