# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Extract reusable CPU toolpath and vertex preparation from the Qt renderer.
# 2026-09-27 [43ab36d] Retained GPU shader geometry and stroke generation.

"""Qt-free toolpath preparation shared by the plugin and library consumers."""
from array import array
from collections import OrderedDict
import bisect
import math
import threading
import time

from ..gcode.TravelStates import is_travel

_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 48 * 1024 * 1024

WIDE_VERTICES = 30  # Rectangle + two four-triangle semicircular caps.
CAP_POINTS = tuple(
    (math.cos(-math.pi / 2 + i * math.pi / 4), math.sin(-math.pi / 2 + i * math.pi / 4)) for i in range(5)
)


def dashed_edges(points):
    """Bed-space 0.5 mm dashes / gaps, continuous across short edges."""
    phase = 0.0
    for a, b in zip(points, points[1:], strict=False):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        travelled = 0.0
        while travelled < length:
            on = phase < 0.5
            amount = min(length - travelled, (0.5 if on else 1.0) - phase)
            if on and amount > 1e-9:
                lo, hi = travelled / length, (travelled + amount) / length
                yield (b[2], a[0] + dx * lo, a[1] + dy * lo, a[0] + dx * hi, a[1] + dy * hi)
            travelled += amount
            phase = (phase + amount) % 1.0


def prepare(payloads, cancel=None):
    """Sort each class once; every later progress update is a binary search."""
    payloads = tuple(sorted(payloads, key=lambda row: {"prev": 0, "next": 1, "current": 2}.get(row[0], 3)))
    if cancel is not None and cancel.is_set():
        return ()
    if len(payloads) > 1:
        # Ghosts arrive independently. Cache each role/layer so adding one
        # never walks the unchanged current and other ghost again.
        result = tuple(row for payload in payloads for row in prepare((payload,), cancel))
        return () if cancel is not None and cancel.is_set() else result
    key = tuple((role, id(payload)) for role, payload in payloads)
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is not None:
            _CACHE.move_to_end(key)
            return entry[1]
    prepared = []
    deadline = time.perf_counter() + 0.008
    for role, payload in payloads:
        if not payload:
            continue
        if role == "current":
            for event_key, name in (("retractions", "RETRACTION"), ("unretractions", "UNRETRACTION")):
                marks = payload.get(event_key) or ()
                if marks:
                    packed = array("f")
                    for x, y, _motion in marks:
                        packed.extend((x, y, x, y))
                    prepared.append((role, name, tuple(point[2] for point in marks), packed.tobytes()))
        groups = dict(payload.get("classes") or {})
        if "travelClasses" in payload:
            groups.update(payload["travelClasses"])
        else:
            groups["TRAVEL"] = payload.get("travels") or []
        for name, segments in groups.items():
            edges = []
            for points in segments:
                source = (
                    dashed_edges(points)
                    if role == "next"
                    else ((b[2], a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:], strict=False))
                )
                for edge in source:
                    edges.append(edge)
                    if len(edges) % 256 == 0 and time.perf_counter() > deadline:
                        if cancel is not None and cancel.is_set():
                            return ()
                        time.sleep(0.001)
                        deadline = time.perf_counter() + 0.008
                if time.perf_counter() > deadline:
                    if cancel is not None and cancel.is_set():
                        return ()
                    time.sleep(0.001)
                    deadline = time.perf_counter() + 0.008
            edges.sort(key=lambda edge: edge[0])
            if edges:
                packed = array("f")
                for edge in edges:
                    packed.extend(edge[1:])
                    if time.perf_counter() > deadline:
                        if cancel is not None and cancel.is_set():
                            return ()
                        time.sleep(0.001)
                        deadline = time.perf_counter() + 0.008
                profile = payload.get("widths") or ()
                widths = array("f", (max(0.001, profile[int(edge[0])]) if 0 <= int(edge[0]) < len(profile) and profile[int(edge[0])] > 0 else 0.4 for edge in edges))
                speeds, tools = payload.get("speeds") or (), payload.get("tools") or ()
                metrics = array("f")
                for edge in edges:
                    motion = int(edge[0])
                    metrics.extend((speeds[motion] if motion < len(speeds) else 0., tools[motion] if motion < len(tools) else 0))
                prepared.append((role, name, tuple(edge[0] for edge in edges), packed.tobytes(), widths.tobytes(), metrics.tobytes()))
    result = tuple(prepared)
    # Cache keys hold source payloads to prevent id reuse. Account for their
    # retained Python points as well as packed vertices / motion integers.
    # A long next-layer edge expands into many dashes, but retains its source
    # points only once. Charging a source point per dash refused dense windows
    # even when their actual storage comfortably fitted the cache.
    source_points = sum(len(segment) for _role, payload in payloads if payload
                        for segments in (tuple((payload.get("classes") or {}).values())
                                         + (payload.get("travels") or (),) + tuple((payload.get("travelClasses") or {}).values()))
                        for segment in segments)
    source_points += sum(len(payload.get(key) or ()) for _role, payload in payloads if payload
                         for key in ("retractions", "unretractions"))
    profile_bytes = sum(sum(len(payload.get(key) or ()) * cost for key,cost in (("widths",32),("speeds",32),("tools",28))) for _role, payload in payloads if payload)
    charge = profile_bytes + sum(len(row[3]) + (sum(len(column) for column in row[4:])) + len(row[2]) * 36 for row in result) + source_points * 192
    if (cancel is None or not cancel.is_set()) and charge <= _CACHE_BYTES:
        with _CACHE_LOCK:
            _CACHE[key] = (payloads, result, charge)
            _CACHE.move_to_end(key)
            while sum(entry[2] for entry in _CACHE.values()) > _CACHE_BYTES:
                _CACHE.popitem(last=False)
    return result


def stroke_geometry(data, width, sx, sy, travel_ratio=0.7, rounded=True, cancel=None):
    """Expand wide strokes off the render thread; no driver-wide-line reliance."""
    result = []
    deadline = time.perf_counter() + 0.008
    for row in data:
        role, name, motions, packed = row[:4]
        stroke_width = width * travel_ratio if is_travel(name) else width
        points, triangles = array("f"), array("f")
        points.frombytes(packed)
        for i in range(0, len(points), 4):
            ax, ay, bx, by = points[i : i + 4]
            dx, dy = (bx - ax) * sx, (by - ay) * sy
            length = math.hypot(dx, dy)
            nx, ny = (-dy / length, dx / length) if length else (0, 1)
            ox, oy = nx * stroke_width / (2 * sx), ny * stroke_width / (2 * sy)
            triangles.extend(
                (
                    ax + ox,
                    ay + oy,
                    ax - ox,
                    ay - oy,
                    bx + ox,
                    by + oy,
                    bx + ox,
                    by + oy,
                    ax - ox,
                    ay - oy,
                    bx - ox,
                    by - oy,
                )
            )
            if rounded and role != "next":
                ux, uy = (dx / length, dy / length) if length else (1, 0)
                for cx, cy, direction in ((ax, ay, -1), (bx, by, 1)):
                    arc = tuple(
                        (
                            cx + (ux * c * direction + nx * s) * stroke_width / (2 * sx),
                            cy + (uy * c * direction + ny * s) * stroke_width / (2 * sy),
                        )
                        for c, s in CAP_POINTS
                    )
                    for a, b in zip(arc, arc[1:], strict=False):
                        triangles.extend((cx, cy, *a, *b))
            elif rounded:
                # Flat dash ends keep wide strokes from filling the gaps.
                # Match the shared vertex stride with degenerate cap triangles.
                triangles.extend((ax, ay) * (WIDE_VERTICES - 6))
            if time.perf_counter() > deadline:
                if cancel is not None and cancel.is_set():
                    return ()
                time.sleep(0.001)
                deadline = time.perf_counter() + 0.008
        result.append((role, name, motions, triangles.tobytes()))
    return tuple(result)


def marker_counts(data, progress, total=0):
    """Completed event prefixes; partial moves must not reveal their events."""
    completed = max(0, math.floor(progress)) if math.isfinite(progress) else (0 if math.isnan(progress) else float("inf"))
    # Firmware retracts can sit after the last indexed motion. They appear
    # at full playback, without requiring a nonexistent extra motion.
    search = bisect.bisect_right if total > 0 and completed >= total else bisect.bisect_left
    return tuple((name, search(motions, completed)) for role, name, motions, _raw in data
                 if role == "current" and name in ("RETRACTION", "UNRETRACTION"))


def marker_geometry(data, sx, sy, scale, compact, retractions, unretractions, limits=None):
    """Small screen-size hollow arrows, at most one of each kind per 12px cell.

    World-aligned cells keep the selection stable during pan; zoom reveals
    individual events. Glyph geometry is independent of toolpath line width.
    """
    if not (retractions or unretractions) or abs(sx) < 1e-9 or abs(sy) < 1e-9:
        return b""
    half = (3 if compact else min(8, max(4, 4 * math.sqrt(max(1, scale))))) / 2
    outline = ((0, -half), (half, 0), (half * .35, 0), (half * .35, half),
               (-half * .35, half), (-half * .35, 0), (-half, 0), (0, -half))
    vertices = array("f")
    cells = set()
    limits = dict(limits) if limits is not None else None
    for role, name, _motions, raw in data:
        enabled = retractions if name == "RETRACTION" else unretractions if name == "UNRETRACTION" else False
        if role != "current" or not enabled:
            continue
        points = array("f")
        points.frombytes(raw)
        direction = 1 if name == "RETRACTION" else -1
        count = limits.get(name, 0) if limits is not None else len(points) // 4
        for i in range(0, min(len(points), count * 4), 4):
            x, y = points[i], points[i + 1]
            cell = (name, math.floor(x * abs(sx) / 12), math.floor(y * abs(sy) / 12))
            if cell in cells:
                continue
            cells.add(cell)
            for a, b in zip(outline, outline[1:], strict=False):
                vertices.extend((x + a[0] / sx, y - direction * a[1] / sy,
                                 x + b[0] / sx, y - direction * b[1] / sy))
    return vertices.tobytes()


def pack_shader(data, cancel=None):
    try:
        import numpy as np
    except ImportError:
        return _pack_stdlib(data, cancel)
    result = []
    corners = np.array([[-1, 1], [-1, -1], [1, 1], [1, 1], [-1, -1], [1, -1]], dtype=np.float32)
    for row in data:
        role, name, motions, packed = row[:4]
        if name in ("RETRACTION", "UNRETRACTION"):
            result.append((role, name, motions, packed))
            continue
        if cancel is not None and cancel.is_set():
            return ()
        points = np.frombuffer(packed, dtype=np.float32).reshape(-1, 4)
        vertices = np.empty((len(points), 6, 10), dtype=np.float32)
        for i, corner in enumerate(corners):
            vertices[:, i, :2] = points[:, :2] if corner[0] < 0 else points[:, 2:]
        vertices[:, :, 2:4] = (points[:, 2:] - points[:, :2])[:, None, :]
        vertices[:, :, 4:6] = corners[None, :, :]
        if len(row) > 4:
            vertices[:, :, 4] *= np.frombuffer(row[4], dtype=np.float32)[:, None]
        vertices[:, :, 8:10] = np.frombuffer(row[5], dtype=np.float32).reshape(-1, 2)[:, None, :] if len(row) > 5 else 0
        motion = np.asarray(motions, dtype=np.float32)
        starts = np.zeros(len(points), dtype=np.float32)
        spans = np.ones(len(points), dtype=np.float32)
        if len(motion) > 1 and np.any(motion[1:] == motion[:-1]):
            boundaries = np.r_[0, np.flatnonzero(motion[1:] != motion[:-1]) + 1]
            lengths = np.linalg.norm(points[:, 2:] - points[:, :2], axis=1)
            cumulative = np.r_[0.0, np.cumsum(lengths, dtype=np.float64)]
            counts = np.diff(np.r_[boundaries, len(points)])
            totals = np.repeat(np.add.reduceat(lengths, boundaries), counts)
            totals = np.maximum(totals, 1e-12)
            starts = (cumulative[:-1] - np.repeat(cumulative[boundaries], counts)) / totals
            spans = lengths / totals
        vertices[:, :, 6] = (motion + starts)[:, None]
        vertices[:, :, 7] = spans[:, None]
        result.append((role, name, motions, vertices.tobytes()))
    return tuple(result)


def _pack_stdlib(data, cancel=None):
    """Development fallback; Cura itself supplies NumPy."""
    result = []
    corners = ((-1, 1), (-1, -1), (1, 1), (1, 1), (-1, -1), (1, -1))
    for row in data:
        role, name, motions, packed = row[:4]
        if name in ("RETRACTION", "UNRETRACTION"):
            result.append((role, name, motions, packed))
            continue
        widths = array("f")
        if len(row) > 4:
            widths.frombytes(row[4])
        metrics = array("f")
        if len(row) > 5:
            metrics.frombytes(row[5])
        points, vertices = array("f"), array("f")
        points.frombytes(packed)
        ranges = []
        group = 0
        while group < len(motions):
            end = group + 1
            while end < len(motions) and motions[end] == motions[group]:
                end += 1
            lengths = [((points[i * 4 + 2] - points[i * 4]) ** 2 + (points[i * 4 + 3] - points[i * 4 + 1]) ** 2) ** .5 for i in range(group, end)]
            total = max(sum(lengths), 1e-12)
            walked = 0.0
            for length in lengths:
                ranges.append((motions[group] + walked / total, length / total))
                walked += length
            group = end
        for offset in range(0, len(points), 4):
            if offset % 4096 == 0 and cancel is not None and cancel.is_set():
                return ()
            x, y, ex, ey = points[offset : offset + 4]
            for cx, cy in corners:
                vertices.extend((x if cx < 0 else ex, y if cx < 0 else ey, ex - x, ey - y, cx * (widths[offset // 4] if widths else 1.0), cy, *ranges[offset // 4], *(metrics[offset // 2:offset // 2 + 2] if metrics else (0., 0.))))
        result.append((role, name, motions, vertices.tobytes()))
    return tuple(result)
