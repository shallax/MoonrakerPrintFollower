from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import threading
import time
from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import BinaryIO, Dict, List, Optional, Sequence, Tuple

try:
    from .Core import RemoteFileIdentity
except ImportError:  # test/import convenience
    from Core import RemoteFileIdentity


_LAYER_COMMENT = re.compile(rb"^\s*;LAYER:\s*-?\d+\s*$", re.IGNORECASE)
_CURA_LAYER_VALUE = re.compile(rb"^\s*;LAYER:\s*(-?\d+)\s*$", re.IGNORECASE)
_ORCA_LAYER = re.compile(rb"^\s*;\s*layer\s+num/total_layer_count:\s*\d+\s*/\s*\d+\s*$", re.IGNORECASE)
_ORCA_LAYER_VALUE = re.compile(rb"^\s*;\s*layer\s+num/total_layer_count:\s*(\d+)\s*/\s*\d+\s*$", re.IGNORECASE)
_PRUSA_LAYER_CHANGE = re.compile(rb"^\s*;LAYER_CHANGE\s*$", re.IGNORECASE)
_STATS_MARKER = re.compile(
    rb"^\s*SET_PRINT_STATS_INFO\b.*\bCURRENT_LAYER\s*=\s*(-?\d+)",
    re.IGNORECASE,
)
_MOTION = re.compile(rb"^\s*(?:N\d+\s*)?G(?:0|1|2|3)(?!\d)", re.IGNORECASE)
_ELAPSED = re.compile(rb"^\s*;TIME_ELAPSED:", re.IGNORECASE)
_COMMAND = re.compile(rb"^\s*(?:N\d+\s*)?([GMT]\d+)(?!\d)", re.IGNORECASE)
_AXIS = re.compile(rb"([XYZ])\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)", re.IGNORECASE)

_CACHE_MAGIC = b"MPFI110\0"
_CACHE_VERSION = 2
_LARGE_FILE_COMPACT_THRESHOLD = 128 * 1024 * 1024


@dataclass
class LayerMotionIndex:
    ranges: List[Tuple[int, int]] = field(default_factory=list)
    motion_offsets: List[array] = field(default_factory=list)
    motion_x: List[array] = field(default_factory=list)
    motion_y: List[array] = field(default_factory=list)
    motion_z: List[array] = field(default_factory=list)
    layer_start_positions: List[Tuple[float, float, float]] = field(default_factory=list)
    layer_start_absolute: List[bool] = field(default_factory=list)
    layer_start_units: List[float] = field(default_factory=list)
    current_layer_map: Dict[int, int] = field(default_factory=dict)
    compact: bool = False
    hydrated_layers: set[int] = field(default_factory=set, repr=False)
    cache_lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def __bool__(self) -> bool:
        return bool(self.ranges)

    def layer_count(self) -> int:
        return len(self.ranges)

    def motion_count(self, layer: int) -> int:
        if layer < 0 or layer >= len(self.motion_offsets):
            return 0
        return len(self.motion_offsets[layer])

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
        lag_window: int = 128,
        ahead_window: int = 8,
        max_distance_mm: float = 3.0,
        minimum_fraction: Optional[float] = None,
    ) -> Tuple[float, str]:
        """Estimate physical progress using Moonraker's live tool position.

        ``file_position`` remains the authoritative coarse locator.  We only
        search a bounded neighbourhood around that location, biased backwards
        because Klipper's parser/lookahead is normally ahead of the physical
        nozzle.  If no nearby motion segment plausibly matches the live tool
        position, the exact legacy file-position fraction is returned.
        """

        base_fraction, base_method = self.file_fraction(layer, file_position)
        floor_fraction: Optional[float] = None
        if minimum_fraction is not None:
            try:
                floor_fraction = max(0.0, min(1.0, float(minimum_fraction)))
            except (TypeError, ValueError):
                floor_fraction = None

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

        try:
            px, py, pz = float(live_position[0]), float(live_position[1]), float(live_position[2])
        except (TypeError, ValueError):
            return with_floor(base_fraction, base_method)

        coarse_completed = bisect_right(offsets, int(file_position))
        lo = max(0, coarse_completed - max(1, int(lag_window)) - 1)
        if floor_fraction is not None:
            # Live XYZ can match more than one place on a closed/repeated toolpath.
            # Once a layer has visibly progressed, never search behind that point;
            # otherwise a later visit to the same XY can make Preview rewind.
            floor_completed = max(0, min(n, int(math.floor(floor_fraction * n))))
            lo = max(lo, max(0, floor_completed - 1))
        hi = min(n - 1, coarse_completed + max(0, int(ahead_window)))
        if hi < lo:
            return with_floor(base_fraction, base_method)

        layer_start = (
            self.layer_start_positions[layer]
            if layer < len(self.layer_start_positions)
            else (float(xs[0]), float(ys[0]), float(zs[0]))
        )

        best_distance_sq = float("inf")
        best_completed = None
        for i in range(lo, hi + 1):
            if i == 0:
                ax, ay, az = layer_start
            else:
                ax, ay, az = float(xs[i - 1]), float(ys[i - 1]), float(zs[i - 1])
            bx, by, bz = float(xs[i]), float(ys[i]), float(zs[i])
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
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_completed = i + t

        if best_completed is None or math.sqrt(best_distance_sq) > max(0.1, max_distance_mm):
            return with_floor(base_fraction, base_method)

        refined = max(0.0, min(1.0, float(best_completed) / n))
        return with_floor(refined, "live position")


def _parse_axes(code: bytes) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for match in _AXIS.finditer(code):
        try:
            values[match.group(1).decode("ascii").upper()] = float(match.group(2))
        except (UnicodeDecodeError, ValueError):
            continue
    return values


def _build_pass(
    handle: BinaryIO,
    marker: re.Pattern[bytes],
    *,
    collect_stats: bool,
    collect_motions: bool = True,
    cancel_event=None,
) -> Tuple[
    List[Tuple[int, int]], List[array], List[array], List[array], List[array],
    List[Tuple[float, float, float]], List[bool], List[float], List[int],
]:
    blocks: List[dict] = []
    current: Optional[dict] = None
    stats_values: List[int] = []
    absolute_xyz = True
    units_scale = 1.0
    x = y = z = 0.0
    line_number = 0

    handle.seek(0)
    while True:
        if cancel_event is not None and (line_number & 0x3FF) == 0 and cancel_event.is_set():
            return [], [], [], [], [], [], [], [], []
        offset = handle.tell()
        line = handle.readline()
        if not line:
            break
        stripped = line.rstrip(b"\r\n")

        if collect_stats:
            stats_match = _STATS_MARKER.search(stripped)
            if stats_match is not None:
                try:
                    value = int(stats_match.group(1))
                    if not stats_values or stats_values[-1] != value:
                        stats_values.append(value)
                except (TypeError, ValueError):
                    pass

        if marker.search(stripped):
            if current is not None and current["end"] is None:
                current["end"] = offset
            current = {
                "start": offset,
                "end": None,
                "motions": array("Q"),
                "x": array("f"),
                "y": array("f"),
                "z": array("f"),
                "start_position": (x, y, z),
                "start_absolute": absolute_xyz,
                "start_units": units_scale,
            }
            blocks.append(current)
        elif current is not None and current["end"] is None and _ELAPSED.search(stripped):
            current["end"] = offset

        # Track G-code XYZ state even outside the indexed layer body. This is
        # important for Cura files that emit travel/macro motion between
        # ;TIME_ELAPSED and the following ;LAYER marker.
        code = stripped.split(b";", 1)[0]
        command_match = _COMMAND.search(code)
        command = command_match.group(1).upper() if command_match else b""
        axes = _parse_axes(code)
        if units_scale != 1.0 and axes:
            axes = {axis: value * units_scale for axis, value in axes.items()}
        if command == b"G20":
            units_scale = 25.4
        elif command == b"G21":
            units_scale = 1.0
        elif command == b"G90":
            absolute_xyz = True
        elif command == b"G91":
            absolute_xyz = False
        elif command == b"G92":
            if "X" in axes:
                x = axes["X"]
            if "Y" in axes:
                y = axes["Y"]
            if "Z" in axes:
                z = axes["Z"]
        elif _MOTION.search(stripped):
            nx, ny, nz = x, y, z
            if "X" in axes:
                nx = axes["X"] if absolute_xyz else x + axes["X"]
            if "Y" in axes:
                ny = axes["Y"] if absolute_xyz else y + axes["Y"]
            if "Z" in axes:
                nz = axes["Z"] if absolute_xyz else z + axes["Z"]
            x, y, z = nx, ny, nz
            if collect_motions and current is not None and current["end"] is None:
                current["motions"].append(offset)
                current["x"].append(x)
                current["y"].append(y)
                current["z"].append(z)

        line_number += 1

    if cancel_event is not None and cancel_event.is_set():
        return [], [], [], [], [], [], [], [], []

    file_end = handle.tell()
    if current is not None and current["end"] is None:
        current["end"] = file_end

    ranges: List[Tuple[int, int]] = []
    motions: List[array] = []
    motion_x: List[array] = []
    motion_y: List[array] = []
    motion_z: List[array] = []
    starts: List[Tuple[float, float, float]] = []
    start_absolute: List[bool] = []
    start_units: List[float] = []
    for block in blocks:
        start = int(block["start"])
        end = int(block["end"] if block["end"] is not None else file_end)
        ranges.append((start, max(start + 1, end)))
        motions.append(block["motions"])
        motion_x.append(block["x"])
        motion_y.append(block["y"])
        motion_z.append(block["z"])
        starts.append(tuple(float(v) for v in block["start_position"]))
        start_absolute.append(bool(block["start_absolute"]))
        start_units.append(float(block["start_units"]))
    return ranges, motions, motion_x, motion_y, motion_z, starts, start_absolute, start_units, stats_values


def _collect_marker_values(handle: BinaryIO, capture: Optional[re.Pattern[bytes]], cancel_event=None) -> List[int]:
    if capture is None:
        return []
    values: List[int] = []
    handle.seek(0)
    line_number = 0
    while True:
        if cancel_event is not None and (line_number & 0x3FF) == 0 and cancel_event.is_set():
            return []
        line = handle.readline()
        if not line:
            break
        match = capture.search(line.rstrip(b"\r\n"))
        if match is not None:
            try:
                values.append(int(match.group(1)))
            except (TypeError, ValueError):
                pass
        line_number += 1
    return values


def build_index_from_file(path: str, cancel_event=None, compact: Optional[bool] = None) -> LayerMotionIndex:
    if compact is None:
        try:
            compact = os.path.getsize(path) >= _LARGE_FILE_COMPACT_THRESHOLD
        except OSError:
            compact = False

    markers = (
        (_LAYER_COMMENT, _CURA_LAYER_VALUE),
        (_ORCA_LAYER, _ORCA_LAYER_VALUE),
        (_PRUSA_LAYER_CHANGE, None),
        (_STATS_MARKER, _STATS_MARKER),
    )
    ranges = motions = xs = ys = zs = starts = start_absolute = start_units = stats_values = None
    marker_values: List[int] = []
    with open(path, "rb") as handle:
        for marker, capture in markers:
            result = _build_pass(
                handle, marker, collect_stats=True, collect_motions=not compact, cancel_event=cancel_event
            )
            ranges, motions, xs, ys, zs, starts, start_absolute, start_units, stats_values = result
            if ranges:
                marker_values = _collect_marker_values(handle, capture, cancel_event)
                break
            if cancel_event is not None and cancel_event.is_set():
                break

    if cancel_event is not None and cancel_event.is_set():
        return LayerMotionIndex()
    ranges = ranges or []
    motions = motions or []
    xs = xs or []
    ys = ys or []
    zs = zs or []
    starts = starts or []
    start_absolute = start_absolute or []
    start_units = start_units or []
    stats_values = stats_values or []

    layer_map: Dict[int, int] = {}
    if ranges and len(stats_values) == len(ranges):
        for index, value in enumerate(stats_values):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index
    elif ranges and len(stats_values) >= 2:
        if all(stats_values[i] == stats_values[0] + i for i in range(len(stats_values))):
            base = stats_values[0]
            for index in range(len(ranges)):
                layer_map[base + index] = index
    elif ranges and len(marker_values) == len(ranges):
        # Cura and Orca numeric layer markers provide a useful mapping even when
        # SET_PRINT_STATS_INFO is absent. The exact values are preserved instead
        # of assuming zero/one-based numbering.
        for index, value in enumerate(marker_values):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index

    hydrated = set(range(len(ranges))) if not compact else set()
    return LayerMotionIndex(
        ranges=ranges,
        motion_offsets=motions,
        motion_x=xs,
        motion_y=ys,
        motion_z=zs,
        layer_start_positions=starts,
        layer_start_absolute=start_absolute,
        layer_start_units=start_units,
        current_layer_map=layer_map,
        compact=bool(compact),
        hydrated_layers=hydrated,
    )


def hydrate_layer_from_file(index: LayerMotionIndex, path: str, layer: int) -> bool:
    """Populate motion data for one layer of a compact large-file index.

    Boundary indexing keeps RAM bounded for huge files. Motion commands are then
    loaded only for layers actually viewed during the live print. Byte-position
    following remains available while hydration is pending.
    """
    if not index.compact or layer in index.hydrated_layers:
        return True
    if layer < 0 or layer >= len(index.ranges):
        return False
    start, end = index.ranges[layer]
    try:
        with open(path, "rb") as handle:
            handle.seek(start)
            x, y, z = index.layer_start_positions[layer] if layer < len(index.layer_start_positions) else (0.0, 0.0, 0.0)
            absolute_xyz = index.layer_start_absolute[layer] if layer < len(index.layer_start_absolute) else True
            units_scale = index.layer_start_units[layer] if layer < len(index.layer_start_units) else 1.0
            offsets = array("Q")
            xs = array("f")
            ys = array("f")
            zs = array("f")
            while handle.tell() < end:
                offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                stripped = line.rstrip(b"\r\n")
                code = stripped.split(b";", 1)[0]
                command_match = _COMMAND.search(code)
                command = command_match.group(1).upper() if command_match else b""
                axes = _parse_axes(code)
                if units_scale != 1.0 and axes:
                    axes = {axis: value * units_scale for axis, value in axes.items()}
                if command == b"G20":
                    units_scale = 25.4
                elif command == b"G21":
                    units_scale = 1.0
                elif command == b"G90":
                    absolute_xyz = True
                elif command == b"G91":
                    absolute_xyz = False
                elif command == b"G92":
                    x = axes.get("X", x); y = axes.get("Y", y); z = axes.get("Z", z)
                elif _MOTION.search(stripped):
                    if "X" in axes: x = axes["X"] if absolute_xyz else x + axes["X"]
                    if "Y" in axes: y = axes["Y"] if absolute_xyz else y + axes["Y"]
                    if "Z" in axes: z = axes["Z"] if absolute_xyz else z + axes["Z"]
                    offsets.append(offset); xs.append(x); ys.append(y); zs.append(z)
        with index.cache_lock:
            while len(index.motion_offsets) < len(index.ranges):
                index.motion_offsets.append(array("Q")); index.motion_x.append(array("f")); index.motion_y.append(array("f")); index.motion_z.append(array("f"))
            index.motion_offsets[layer] = offsets
            index.motion_x[layer] = xs
            index.motion_y[layer] = ys
            index.motion_z[layer] = zs
            index.hydrated_layers.add(layer)
        return True
    except OSError:
        return False


def build_index_from_bytes(data: bytes, cancel_event=None) -> LayerMotionIndex:
    with tempfile.NamedTemporaryFile(prefix="mpf-index-test-", suffix=".gcode", delete=False) as handle:
        path = handle.name
        handle.write(data)
    try:
        return build_index_from_file(path, cancel_event, compact=False)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    """Read exactly *size* bytes or raise EOFError.

    gzip streams are allowed to return short reads, so cache loading must not
    assume one ``read(n)`` call fills the requested buffer.
    """
    if size < 0:
        raise EOFError("negative cache read")
    chunks = bytearray()
    remaining = size
    while remaining:
        chunk = handle.read(remaining)
        if not chunk:
            raise EOFError("truncated index cache")
        chunks.extend(chunk)
        remaining -= len(chunk)
    return bytes(chunks)


class PersistentIndexCache:
    def __init__(self, directory: str, *, max_bytes: int = 128 * 1024 * 1024, max_entries: int = 16) -> None:
        self.directory = directory
        self.max_bytes = max(1 * 1024 * 1024, int(max_bytes))
        self.max_entries = max(1, int(max_entries))
        os.makedirs(self.directory, exist_ok=True)

    def _path(self, identity: RemoteFileIdentity) -> str:
        digest = hashlib.sha256(identity.stable_key().encode("utf-8")).hexdigest()
        return os.path.join(self.directory, f"{digest}.mpfi.gz")

    def load(self, identity: Optional[RemoteFileIdentity]) -> Optional[LayerMotionIndex]:
        if identity is None:
            return None
        path = self._path(identity)
        try:
            with gzip.open(path, "rb") as handle:
                if handle.read(len(_CACHE_MAGIC)) != _CACHE_MAGIC:
                    return None
                header_len_raw = _read_exact(handle, 4)
                if len(header_len_raw) != 4:
                    return None
                header_len = struct.unpack("<I", header_len_raw)[0]
                if header_len <= 0 or header_len > 16 * 1024 * 1024:
                    return None
                header = json.loads(_read_exact(handle, header_len).decode("utf-8"))
                if header.get("version") != _CACHE_VERSION:
                    return None
                if header.get("identity") != identity.stable_key():
                    return None
                if header.get("byteorder") != sys.byteorder:
                    return None
                ranges = [(int(a), int(b)) for a, b in header.get("ranges", [])]
                starts = [tuple(float(v) for v in xyz[:3]) for xyz in header.get("starts", [])]
                start_absolute = [bool(v) for v in header.get("start_absolute", [True] * len(ranges))]
                start_units = [float(v) for v in header.get("start_units", [1.0] * len(ranges))]
                layer_map = {int(k): int(v) for k, v in (header.get("layer_map") or {}).items()}
                compact = bool(header.get("compact", False))
                counts = [int(v) for v in header.get("counts", [])]
                if not (
                    len(ranges) == len(counts) == len(starts)
                    == len(start_absolute) == len(start_units)
                ):
                    return None

                offsets: List[array] = []
                xs: List[array] = []
                ys: List[array] = []
                zs: List[array] = []
                for count in counts:
                    if count < 0 or count > 100_000_000:
                        return None
                    off = array("Q")
                    xx = array("f")
                    yy = array("f")
                    zz = array("f")
                    off.frombytes(_read_exact(handle, count * off.itemsize))
                    xx.frombytes(_read_exact(handle, count * xx.itemsize))
                    yy.frombytes(_read_exact(handle, count * yy.itemsize))
                    zz.frombytes(_read_exact(handle, count * zz.itemsize))
                    if not (len(off) == len(xx) == len(yy) == len(zz) == count):
                        return None
                    offsets.append(off)
                    xs.append(xx)
                    ys.append(yy)
                    zs.append(zz)
            try:
                os.utime(path, None)
            except OSError:
                pass
            hydrated_raw = header.get("hydrated")
            if isinstance(hydrated_raw, list):
                hydrated = {int(i) for i in hydrated_raw if 0 <= int(i) < len(ranges)}
            else:
                hydrated = {i for i, values in enumerate(offsets) if len(values) > 0}
            return LayerMotionIndex(
                ranges=ranges,
                motion_offsets=offsets,
                motion_x=xs,
                motion_y=ys,
                motion_z=zs,
                layer_start_positions=starts,
                layer_start_absolute=start_absolute,
                layer_start_units=start_units,
                current_layer_map=layer_map,
                compact=compact,
                hydrated_layers=hydrated,
            )
        except (OSError, ValueError, json.JSONDecodeError, EOFError, struct.error):
            return None

    def save(self, identity: Optional[RemoteFileIdentity], index: LayerMotionIndex) -> None:
        if identity is None or not index:
            return
        with index.cache_lock:
            layer_count = len(index.ranges)
            if not (
                len(index.motion_offsets) == layer_count
                and len(index.motion_x) == layer_count
                and len(index.motion_y) == layer_count
                and len(index.motion_z) == layer_count
                and len(index.layer_start_positions) == layer_count
                and len(index.layer_start_absolute) == layer_count
                and len(index.layer_start_units) == layer_count
            ):
                return
            for i in range(layer_count):
                count = len(index.motion_offsets[i])
                if not (len(index.motion_x[i]) == len(index.motion_y[i]) == len(index.motion_z[i]) == count):
                    return
            path = self._path(identity)
            temp_path = f"{path}.tmp-{os.getpid()}-{int(time.time() * 1000)}"
            header = {
                "version": _CACHE_VERSION,
                "identity": identity.stable_key(),
                "byteorder": sys.byteorder,
                "ranges": index.ranges,
                "starts": index.layer_start_positions,
                "start_absolute": index.layer_start_absolute,
                "start_units": index.layer_start_units,
                "layer_map": {str(k): int(v) for k, v in index.current_layer_map.items()},
                "compact": bool(index.compact),
                "hydrated": sorted(index.hydrated_layers),
                "counts": [len(v) for v in index.motion_offsets],
            }
            raw_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
            try:
                with gzip.open(temp_path, "wb", compresslevel=3) as handle:
                    handle.write(_CACHE_MAGIC)
                    handle.write(struct.pack("<I", len(raw_header)))
                    handle.write(raw_header)
                    for i, offsets in enumerate(index.motion_offsets):
                        handle.write(offsets.tobytes())
                        handle.write(index.motion_x[i].tobytes())
                        handle.write(index.motion_y[i].tobytes())
                        handle.write(index.motion_z[i].tobytes())
                os.replace(temp_path, path)
                self.prune()
            except OSError:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def prune(self) -> None:
        try:
            entries = []
            for name in os.listdir(self.directory):
                if not name.endswith(".mpfi.gz"):
                    continue
                path = os.path.join(self.directory, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, path))
            entries.sort(reverse=True)
            total = 0
            for idx, (_mtime, size, path) in enumerate(entries):
                total += size
                if idx >= self.max_entries or total > self.max_bytes:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        except OSError:
            pass
