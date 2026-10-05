"""Bounded per-object extrusion summaries from the index's single G-code scan."""
from __future__ import annotations

import math
import re
from bisect import bisect_left


_START = re.compile(rb"^EXCLUDE_OBJECT_START\s+NAME\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s;]+))", re.I)
_END = re.compile(rb"^EXCLUDE_OBJECT_END(?:\s|$)", re.I)
_MESH = re.compile(rb"^;MESH:(.+)$", re.I)
MAX_OBJECTS = 128
MAX_CHECKPOINTS = 100_000


def valid_object_work(objects, file_end):
    if not isinstance(objects, dict) or len(objects) > MAX_OBJECTS:
        return False
    count = 0
    for name, row in objects.items():
        if not isinstance(name, str) or not name or len(name) > 256 or not isinstance(row, dict):
            return False
        total, top, last, checkpoints = (row.get(key) for key in ("filament", "top", "last", "layers"))
        bounds = row.get("bounds")
        if (not isinstance(total, (int, float)) or not math.isfinite(total) or not 0 < total < 1e12
                or not isinstance(top, (int, float)) or not math.isfinite(top)
                or not isinstance(last, int) or not 0 <= last <= file_end
                or not isinstance(checkpoints, list)
                or not isinstance(bounds, list) or len(bounds) != 4
                or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bounds)
                or bounds[0] > bounds[2] or bounds[1] > bounds[3]):
            return False
        count += len(checkpoints)
        if count > MAX_CHECKPOINTS:
            return False
        previous = -1
        for part in checkpoints:
            if (not isinstance(part, list) or len(part) != 4
                    or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in part)):
                return False
            first, end, before, after = part
            if not (previous < first <= end <= file_end and 0 <= before <= after <= total):
                return False
            previous = end
        if not checkpoints or checkpoints[-1][1] != last:
            return False
    return True


class ObjectWorkTracker:
    def __init__(self):
        self.objects = {}
        self._explicit_seen = False
        self._explicit = None
        self._mesh = None
        self._layer = -1
        self._pending = {}
        self._checkpoints = 0
        self._overflow = False

    def marker(self, line):
        stripped = line.lstrip()
        start = _START.match(stripped)
        if start:
            self._explicit_seen = True
            self._explicit = next((part.decode("utf-8", "replace") for part in start.groups() if part), None)
        elif _END.match(stripped):
            self._explicit = None
        else:
            mesh = _MESH.match(stripped)
            if mesh:
                name = mesh.group(1).strip().decode("utf-8", "replace")
                self._mesh = None if name.upper() == "NONMESH" else name

    def open_layer(self):
        self._flush()
        self._layer += 1

    def add(self, offset, x, y, z, extrusion):
        if (self._overflow or self._layer < 0 or not 0 < extrusion < 1e6
                or not all(math.isfinite(value) for value in (x, y, z))):
            return
        name = self._explicit if self._explicit_seen else self._mesh
        if not name or len(name) > 256:
            return
        row = self.objects.get(name)
        if row is None:
            if len(self.objects) >= MAX_OBJECTS:
                self._overflow = True
                self.objects.clear()
                self._pending.clear()
                return
            row = {"filament": 0.0, "top": z, "last": offset, "layers": [],
                   "bounds": [x, y, x, y]}
            self.objects[name] = row
        before = row["filament"]
        row["filament"] += extrusion
        row["top"] = max(row["top"], z)
        row["last"] = offset
        bounds = row["bounds"]
        bounds[0] = min(bounds[0], x)
        bounds[1] = min(bounds[1], y)
        bounds[2] = max(bounds[2], x)
        bounds[3] = max(bounds[3], y)
        pending = self._pending.get(name)
        if pending is None:
            self._pending[name] = [offset, offset, before, row["filament"]]
        else:
            pending[1] = offset
            pending[3] = row["filament"]

    def _flush(self):
        if self._overflow:
            return
        for name, checkpoint in self._pending.items():
            self.objects[name]["layers"].append(checkpoint)
            self._checkpoints += 1
        self._pending.clear()
        if self._checkpoints > MAX_CHECKPOINTS:
            self._overflow = True
            self.objects.clear()

    def finish(self):
        self._flush()
        return self.objects


def object_work_fraction(row, offset):
    """Filament share at a file offset, interpolating only the active layer."""
    total = row.get("filament", 0)
    checkpoints = row.get("layers", ())
    if total <= 0 or not checkpoints:
        return None
    index = bisect_left([part[1] for part in checkpoints], offset)
    if index >= len(checkpoints):
        return 1.0
    first, last, before, after = checkpoints[index]
    if offset < first:
        done = before
    elif last <= first:
        done = after
    else:
        done = before + (after - before) * min(1.0, max(0.0, (offset - first) / (last - first)))
    return min(1.0, max(0.0, done / total))


def object_remaining(row, offset, file_end, remaining_end, ranges, elapsed_times):
    """Scale the print ETA to this object's last extrusion, using slicer time."""
    last = row.get("last", 0)
    if last <= offset:
        return 0.0
    if remaining_end is None or remaining_end < 0 or file_end <= offset:
        return None

    def slicer_time(position):
        index = bisect_left([end for _start, end in ranges], position)
        if index >= len(ranges) or index >= len(elapsed_times):
            return None
        start, end = ranges[index]
        before = 0.0 if index == 0 else elapsed_times[index - 1]
        after = elapsed_times[index]
        if before is None or after is None or after < before or end <= start:
            return None
        return before + (after - before) * min(1.0, max(0.0, (position - start) / (end - start)))

    current_time, last_time = slicer_time(offset), slicer_time(last)
    end_time = elapsed_times[-1] if elapsed_times else None
    if current_time is None or last_time is None or end_time is None or end_time <= current_time:
        return None
    share = (last_time - current_time) / (end_time - current_time)
    return max(0.0, min(float(remaining_end), float(remaining_end) * max(0.0, min(1.0, share))))
