"""Persistent index file lifecycle: load, atomic save and eviction."""
from __future__ import annotations


import gzip
import hashlib
import json
import math
import os
import struct
import sys
import threading
import time

from .CachePolicy import evict_to_budget, sweep_index_temps
from array import array
from typing import Dict, List, Optional

from . import ArcGeometry
from .MotionRanges import valid_ranges
from ..Moonraker.MoonrakerProtocol import RemoteFileIdentity

from .FeatureTracker import _TYPE_NONE
from .IndexCodec import _CACHE_MAGIC, _CACHE_VERSION, _MAX_CACHE_HEADER_BYTES, _arc_columns, _arc_entries, _event_columns, _feature_columns, _read_exact
from .IndexLimits import _MAX_MOTIONS_PER_LAYER
from .MotionIndex import LayerMotionIndex

try:
    from UM.Logger import Logger as _Logger
except ImportError:
    _Logger = None  # the host stdlib suite has no UM


def _log(message, *args):
    """The persistence diagnostics, at the DECISION points only
    (never per layer): an INFO line names the reason a restore or an
    eviction happened. The host stdlib suite runs without UM — the
    log no-ops there."""
    if _Logger is not None:
        _Logger.log("i", message, *args)


class PersistentIndexCache:
    def __init__(self, directory: str, *, max_bytes: int = 128 * 1024 * 1024,
                 max_entries: Optional[int] = 16) -> None:
        self.directory = directory
        self.max_bytes = max(1 * 1024 * 1024, int(max_bytes))
        # None disables the entry-count bound (the unified runtime
        # cache's choice — the configured byte budget governs the
        # whole print-folder cache; a hidden folder-count cap would
        # silently override a 4096 MiB selection for small prints).
        self.max_entries = None if max_entries is None else max(1, int(max_entries))
        os.makedirs(self.directory, exist_ok=True)
        sweep_index_temps(self.directory)

    def _path(self, identity: RemoteFileIdentity) -> str:
        digest = hashlib.sha256(identity.stable_key().encode("utf-8")).hexdigest()
        # The per-print folder (the review's unified-persistence
        # finding): the index AND the prepared store live as siblings
        # under one print's own directory — an entire print's cache
        # is one folder to delete, and the eviction drops the whole
        # folder (never an orphaned half).
        print_dir = os.path.join(self.directory, f"p-{digest[:24]}")
        os.makedirs(print_dir, exist_ok=True)
        return os.path.join(print_dir, "index.mpfi.gz")

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
                if header_len <= 0 or header_len > _MAX_CACHE_HEADER_BYTES:
                    return None
                header = json.loads(_read_exact(handle, header_len).decode("utf-8"))
                if header.get("version") != _CACHE_VERSION:
                    return None
                if header.get("identity") != identity.stable_key():
                    return None
                # The uuid is Moonraker's per-extraction token, never a
                # content identity; the header's own fields vouch for
                # size/modified (the surviving discriminators). Validate
                # every known field.
                fields = header.get("identity_fields")
                if fields and isinstance(fields, list) and len(fields) == 4:
                    if identity.size > 0 and int(fields[1]) > 0 and int(fields[1]) != identity.size:
                        return None
                    if identity.modified > 0 and float(fields[2]) > 0 and float(fields[2]) != identity.modified:
                        return None
                    # The uuid is Moonraker's per-extraction token: with
                    # RELIABLE metadata (a real modified timestamp) a
                    # re-extraction rolls it without the gcode changing,
                    # so it must never invalidate an otherwise-valid
                    # entry (the review's UUID-policy finding — the
                    # stable key already ignores it, and the load may
                    # not contradict the key). WITHOUT a modified
                    # timestamp the weak identity (name+size alone) is
                    # the only thing standing, and a rolled uuid then
                    # marks a re-extraction whose content may have
                    # changed: the stale entry is refused rather than
                    # trusted (the review's weak-metadata rule — a
                    # field ignored by the lookup must never vouch for
                    # a restore).
                    if identity.modified <= 0 and identity.size > 0 \
                            and str(fields[3]) != str(identity.uuid):
                        return None
                    # The per-machine namespace resolves cross-printer
                    # collisions instead.
                if header.get("byteorder") != sys.byteorder:
                    return None
                ranges = [(int(a), int(b)) for a, b in header.get("ranges", [])]
                starts = [tuple(float(v) for v in xyz[:3]) for xyz in header.get("starts", [])]
                start_absolute = [bool(v) for v in header.get("start_absolute", [True] * len(ranges))]
                start_units = [float(v) for v in header.get("start_units", [1.0] * len(ranges))]
                layer_map = {int(k): int(v) for k, v in (header.get("layer_map") or {}).items()}
                elapsed_times = [float(v) if v is not None else None for v in header.get("elapsed_times", [])]
                compact = bool(header.get("compact", False))
                counts = [int(v) for v in header.get("counts", [])]
                if not (
                    len(ranges) == len(counts) == len(starts)
                    == len(start_absolute) == len(start_units) == len(elapsed_times)
                ):
                    return None
                # The feature columns are optional as a whole — a blob
                # written before they existed still restores its geometry
                # — but a present one that disagrees with the geometry is
                # refused rather than trusted. An absent run list restores
                # as one untyped run per motion: the motion is there, its
                # colour never was, and the columns must still come back a
                # per-layer entry long or the hydrator could not fill them.
                empty_markers = [[] for _ in counts]
                untyped_runs = [[[count, _TYPE_NONE]] if count else [] for count in counts]
                blank_seeds = [_TYPE_NONE] * len(counts)
                zero_seeds = [0.0] * len(counts)
                true_seeds = [True] * len(counts)
                feature_header = _feature_columns(
                    counts,
                    header.get("type_names", []),
                    (
                        header.get("type_runs", untyped_runs),
                        header.get("travel_starts", empty_markers),
                        header.get("travel_ends", empty_markers),
                        header.get("start_types", blank_seeds),
                        header.get("start_e", zero_seeds),
                        header.get("start_e_absolute", true_seeds),
                        header.get("start_extruding", true_seeds),
                    ),
                )
                if feature_header is None:
                    return None
                event_header = _event_columns(counts, header.get("extruder_events", []), header.get("start_retracted", []))
                if event_header is None:
                    return None
                extruder_events = [[tuple(row) for row in rows] for rows in event_header["extruder_events"]]
                start_retracted = event_header["start_retracted"]
                retraction_seeds = header.get("start_retractions", [])
                if not isinstance(retraction_seeds, list) or (retraction_seeds and len(retraction_seeds) != len(counts)) or not all(isinstance(row, dict) and all(str(k).isdigit() and 0 <= int(k) < 16 and isinstance(v, (int, float)) and math.isfinite(v) and (v == -1 or 0 <= v <= 1e6) for k,v in row.items()) for row in retraction_seeds):
                    return None
                start_retractions = [{int(k):v for k,v in row.items()} for row in retraction_seeds]
                firmware_retractions = header.get("firmware_retractions", [])
                if not isinstance(firmware_retractions, list) or (firmware_retractions and len(firmware_retractions) != len(counts)) or not all(isinstance(rows, list) and len(rows) <= _MAX_MOTIONS_PER_LAYER and all(isinstance(row, list) and len(row) == 3 and isinstance(row[0], int) and 0 <= row[0] <= counts[i] and isinstance(row[1], int) and 0 <= row[1] < 16 and isinstance(row[2], bool) for row in rows) and all(a[0] <= b[0] for a,b in zip(rows, rows[1:], strict=False)) for i,rows in enumerate(firmware_retractions)):
                    return None
                types = feature_header.get("type_runs", untyped_runs)
                travel_starts = feature_header.get("travel_starts", empty_markers)
                travel_ends = feature_header.get("travel_ends", empty_markers)
                start_types = feature_header.get("start_types", blank_seeds)
                start_e = feature_header.get("start_e", zero_seeds)
                start_e_absolute = feature_header.get("start_e_absolute", true_seeds)
                start_extruding = feature_header.get("start_extruding", true_seeds)
                # A budget-dropped (or absent) vocabulary leaves no code to
                # name, so the names go with the runs.
                type_names = list(feature_header.get("type_names", []))
                # The arc columns are optional as a whole: a file with
                # no arcs carries no arc keys and restores every motion
                # without one. A present column is validated against the
                # geometry it claims, and one past the entry budget is
                # refused outright — reading it back arc-free would draw
                # a chord where the file commanded a curve, for as long
                # as the entry lives.
                arc_header = _arc_columns(
                    counts,
                    header.get("arcs", [{} for _ in counts]),
                    header.get("start_arc_plane", [ArcGeometry.PLANE_XY] * len(counts)),
                )
                if arc_header is None:
                    return None
                arcs: List[Dict[int, tuple]] = []
                for layer_arcs in arc_header.get("arcs", [{} for _ in counts]):
                    arcs.append({int(entry[0]): (int(entry[1]), bool(entry[2]),
                                                 float(entry[3]), float(entry[4]))
                                 for entry in layer_arcs})
                start_arc_plane = list(arc_header.get(
                    "start_arc_plane", [ArcGeometry.PLANE_XY] * len(counts)))

                heights = header.get("layer_heights", [])
                diameter = header.get("filament_diameter", 1.75)
                if not isinstance(heights, list) or (heights and len(heights) != len(counts)) or not all(isinstance(v, (int, float)) and math.isfinite(v) and 0.001 <= v <= 10 for v in heights):
                    return None
                if not isinstance(diameter, (int, float)) or not math.isfinite(diameter) or not 0.1 <= diameter <= 10:
                    return None
                diameters = header.get("filament_diameters", {})
                if not isinstance(diameters, dict) or not all(str(k).isdigit() and 0 <= int(k) < 16 and isinstance(v, (int, float)) and math.isfinite(v) and .1 <= v <= 10 for k,v in diameters.items()):
                    return None
                if not valid_ranges(header.get("colour_ranges", {})):
                    return None
                for key, upper, integer in (("start_speeds", 1e6, False), ("start_tools", 15, True)):
                    values = header.get(key, [])
                    if not isinstance(values, list) or (values and len(values) != len(counts)) or not all(isinstance(v, int if integer else (int, float)) and math.isfinite(v) and 0 <= v <= upper for v in values):
                        return None
                if not isinstance(header.get("motion_attributes", False), bool):
                    return None
                if not isinstance(header.get("extrusion_column", False), bool):
                    return None
                offsets: List[array] = []
                xs: List[array] = []
                ys: List[array] = []
                zs: List[array] = []
                extrusion_columns = []
                speed_columns, tool_columns = [], []
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
                    ee = array("f")
                    if header.get("extrusion_column"):
                        ee.frombytes(_read_exact(handle, count * ee.itemsize))
                        if not all(math.isfinite(v) for v in ee):
                            return None
                    extrusion_columns.append(ee)
                    speed, tool = array("f"), array("H")
                    if header.get("motion_attributes"):
                        speed.frombytes(_read_exact(handle, count * 4))
                        tool.frombytes(_read_exact(handle, count * 2))
                        if not all(math.isfinite(v) and 0 <= v <= 1e6 for v in speed) or any(v >= 16 for v in tool):
                            return None
                    speed_columns.append(speed)
                    tool_columns.append(tool)
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
                # The motion arrays are the evidence. The feature columns
                # deliberately are NOT: their runs must total the layer's
                # motion count, so they can only exist where those arrays
                # do and could never name a layer this misses.
                hydrated = {i for i, values in enumerate(offsets) if len(values) > 0}
            pauses = []
            for value in header.get("pauses", []):
                try:
                    layer = int(value)
                except (TypeError, ValueError):
                    continue
                if 0 <= layer < len(ranges) and (not pauses or layer > pauses[-1]):
                    pauses.append(layer)
            motion_counts = header.get("motion_counts")
            if not (isinstance(motion_counts, list) and len(motion_counts) == len(ranges)
                    and all(isinstance(v, int) and 0 <= v for v in motion_counts)):
                # Legacy caches carry no eviction-proof counts; the
                # array lengths (the evicted state) are the fallback.
                motion_counts = list(counts)
            return LayerMotionIndex(
                ranges=ranges,
                motion_offsets=offsets,
                motion_x=xs,
                motion_y=ys,
                motion_z=zs,
                motion_arcs=arcs,
                motion_types=types,
                type_names=type_names,
                travel_starts=travel_starts,
                travel_ends=travel_ends,
                motion_extrusion=extrusion_columns,
                layer_heights=header.get("layer_heights", []),
                filament_diameter=header.get("filament_diameter", 1.75),
                filament_diameters={int(k):v for k,v in header.get("filament_diameters", {}).items()},
                motion_speeds=speed_columns, motion_tools=tool_columns,
                layer_start_speeds=header.get("start_speeds", []),
                layer_start_tools=header.get("start_tools", []),
                colour_ranges={k:tuple(v) for k,v in header.get("colour_ranges", {}).items()},
                extruder_events=extruder_events,
                layer_start_retracted=start_retracted,
                layer_start_retractions=start_retractions,
                firmware_retractions=firmware_retractions,
                layer_start_positions=starts,
                layer_start_absolute=start_absolute,
                layer_start_units=start_units,
                layer_start_types=start_types,
                layer_start_e=start_e,
                layer_start_e_absolute=start_e_absolute,
                layer_start_extruding=start_extruding,
                layer_start_arc_plane=start_arc_plane,
                current_layer_map=layer_map,
                layer_elapsed_times=elapsed_times,
                pauses=tuple(pauses),
                compact=compact,
                hydrated_layers=hydrated,
                layer_motion_counts=motion_counts,
            )
        except (OSError, ValueError, json.JSONDecodeError, EOFError, struct.error):
            return None

    def save(self, identity: Optional[RemoteFileIdentity], index: LayerMotionIndex) -> None:
        if identity is None or not index:
            return
        # The hold is the SNAPSHOT, never the encode. The GUI thread
        # takes this same lock to read the plate (the layers, the
        # split, the progress and the followed layer's own writes), so
        # a hold across the gzip of a whole print's motion data is a
        # stall on the thread that draws the UI, sized by the dataset.
        #
        # The snapshot copies, it does not collect references: a
        # compact index is hydrated — its per-layer arrays replaced —
        # under this same lock by the background pass, so encoding the
        # live arrays outside the hold would publish a layer the
        # header's own counts disagree with.
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
                and len(index.layer_elapsed_times) == layer_count
                and len(index.motion_arcs) == layer_count
                and len(index.layer_start_arc_plane) == layer_count
            ):
                return
            counts = [len(v) for v in index.motion_offsets]
            for i in range(layer_count):
                count = counts[i]
                if not (len(index.motion_x[i]) == len(index.motion_y[i]) == len(index.motion_z[i]) == count):
                    return
            features = _feature_columns(counts, index.type_names, (
                index.motion_types, index.travel_starts, index.travel_ends,
                index.layer_start_types, index.layer_start_e,
                index.layer_start_e_absolute, index.layer_start_extruding,
            ))
            if features is None:
                # Ragged feature columns restore a different index than the
                # one saved; publishing the blob would be worse than not
                # caching at all.
                return
            arc_columns = _arc_columns(counts, _arc_entries(index.motion_arcs),
                                       index.layer_start_arc_plane)
            if arc_columns is None:
                return
            header = {
                "version": _CACHE_VERSION,
                "identity": identity.stable_key(),
                "identity_fields": [identity.filename, identity.size, identity.modified, identity.uuid],
                "byteorder": sys.byteorder,
                "ranges": index.ranges,
                "starts": index.layer_start_positions,
                "start_absolute": index.layer_start_absolute,
                "start_units": index.layer_start_units,
                "layer_map": {str(k): int(v) for k, v in index.current_layer_map.items()},
                "elapsed_times": index.layer_elapsed_times,
                "pauses": list(index.pauses),
                "compact": bool(index.compact),
                "hydrated": sorted(index.hydrated_layers),
                "counts": counts,
                # The eviction-proof counts ride beside the array lengths
                # (which reflect the evicted state for a compact save):
                # a restored index knows a far layer's total before its
                # first re-hydration.
                "motion_counts": list(index.layer_motion_counts),
            }
            has_extrusions = len(index.motion_extrusion) == layer_count and all(len(index.motion_extrusion[i]) == counts[i] for i in range(layer_count))
            if index.motion_extrusion and not has_extrusions:
                return
            header.update(extrusion_column=has_extrusions, layer_heights=index.layer_heights, filament_diameter=index.filament_diameter)
            has_attributes = len(index.motion_speeds) == len(index.motion_tools) == layer_count and all(len(index.motion_speeds[i]) == len(index.motion_tools[i]) == counts[i] for i in range(layer_count))
            if (index.motion_speeds or index.motion_tools) and not has_attributes:
                return
            header.update(filament_diameters=index.filament_diameters, motion_attributes=has_attributes, start_speeds=index.layer_start_speeds,
                          start_tools=index.layer_start_tools, colour_ranges=index.colour_ranges, start_retractions=index.layer_start_retractions, firmware_retractions=index.firmware_retractions)
            event_columns = _event_columns(counts, index.extruder_events, index.layer_start_retracted)
            if event_columns is None:
                return
            header.update(event_columns)
            header.update(features)
            header.update(arc_columns)
            raw_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
            if len(raw_header) > _MAX_CACHE_HEADER_BYTES:
                # A very fragmented (or hostile) file can still push the
                # geometry columns past the readable bound. Writing that
                # blob would only spend the byte budget on a file the
                # loader always refuses, so it is not written at all.
                return
            # tobytes is what makes the encode lock-free: header and
            # geometry leave the hold as one consistent view.
            snapshot = [(offsets.tobytes(), index.motion_x[i].tobytes(),
                         index.motion_y[i].tobytes(), index.motion_z[i].tobytes(),
                         index.motion_extrusion[i].tobytes() if has_extrusions else b"",
                         index.motion_speeds[i].tobytes() if has_attributes else b"",
                         index.motion_tools[i].tobytes() if has_attributes else b"")
                        for i, offsets in enumerate(index.motion_offsets)]
        path = self._path(identity)
        # Unique per CONCURRENT writer, not merely per instant: the
        # encode runs outside the lock now, and the lock is per index
        # while the path is per identity, so two writers overlapping in
        # the same millisecond must not share one temp file.
        temp_path = (f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
                     f"-{int(time.time() * 1000)}")
        try:
            with gzip.open(temp_path, "wb", compresslevel=3) as handle:
                handle.write(_CACHE_MAGIC)
                handle.write(struct.pack("<I", len(raw_header)))
                handle.write(raw_header)
                for offsets, xs, ys, zs, ee, speed, tool in snapshot:
                    handle.write(offsets)
                    handle.write(xs)
                    handle.write(ys)
                    handle.write(zs)
                    handle.write(ee)
                    handle.write(speed)
                    handle.write(tool)
            os.replace(temp_path, path)
            self.prune(keep=path)
        except OSError:
            try:
                os.remove(temp_path)
            except OSError:
                pass

    def prune(self, keep: Optional[str] = None) -> None:
        """The print-level policy (the review's unified-lifecycle
        finding): one print folder's total cost is the index AND the
        prepared representation together, and an evicted print loses
        the WHOLE folder — never an orphaned half. The walk is the
        SHARED eviction policy (CachePolicy.evict_to_budget): true
        LRU — the least recently used unprotected folders go first,
        and the eviction never finishes over budget while an
        unprotected folder remains. The protected path (the
        just-written or currently used entry, passed explicitly —
        never an mtime guess) always survives; if it alone exceeds a
        budget, that is the only acceptable overage."""
        try:
            sweep_index_temps(self.directory)
            # The print-level totals: one entry per print folder, its
            # size the sum of every representation inside it.
            totals = {}
            for root, _dirs, names in os.walk(self.directory):
                folder = os.path.basename(root)
                if not folder.startswith("p-"):
                    continue
                try:
                    stats = [os.stat(os.path.join(root, name))
                             for name in names
                             if name.endswith((".mpfi.gz", ".mpfp"))]
                    if not stats:
                        continue  # an empty leftover folder counts nothing
                    size = sum(stat.st_size for stat in stats)
                    mtime = max(stat.st_mtime for stat in stats)
                except OSError:
                    continue
                totals[root] = (mtime, size)
            keep_dir = os.path.dirname(keep) if keep else None
            evict_to_budget(totals, self.max_bytes, self.max_entries,
                            keep_dir)
        except OSError:
            pass
