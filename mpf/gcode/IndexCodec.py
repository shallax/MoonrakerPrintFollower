"""Bounded validation of the on-disk index columns and cache-format metadata."""
from __future__ import annotations


import math

from typing import BinaryIO, Dict, List, Optional, Sequence

from . import ArcGeometry

from .IndexLimits import _MAX_TYPE_NAMES, _MAX_TYPE_RUNS_PER_LAYER

_CACHE_MAGIC = b"MPFI110\0"
# The header is a length-prefixed JSON blob inside the container, so the
# reader's bound is also the writer's: a longer header is a blob the
# loader refuses, and writing one would only spend the cache's budget on
# a file that can never be read back.
_MAX_CACHE_HEADER_BYTES = 16 * 1024 * 1024
# The feature columns' serialization budget (a run or a marker costs
# ~10 bytes of JSON): a hostile file can fragment every layer into a run
# list of its own, so the columns are bounded on top of the per-layer
# caps. Past the budget they are dropped WHOLE — a truncated run list
# would restore a layer's colours wrong, while an absent one only reads
# as "not recorded".
_MAX_CACHE_FEATURE_ENTRIES = 500_000
# The arc columns' serialization budget, in descriptors: the JSON header
# itself is the bound (one descriptor is ~25 bytes and the header may not
# exceed _MAX_CACHE_HEADER_BYTES). It is ALL OR NOTHING: a blob past it
# is not published, and one offered to the loader is refused, because a
# cache missing descriptors restores an index that draws those arcs as
# chords — geometry the file never commanded, silently, for the life of
# the cache entry. The G0/G1 path pays nothing for the budget: the
# column is sparse, one entry per arc, and a file without arcs keeps
# caching with no arc budget at all.
_MAX_CACHE_ARC_ENTRIES = 200_000
# 10: the arc columns are all-or-nothing. 9 could publish an index whose
# descriptors the entry budget had dropped, and a dropped column is
# indistinguishable from a file that never had arcs, so every 9 blob is
# refused rather than read back as an arc-free one.
# 9: the sparse per-motion arc descriptors and the layer-start arc plane.
# 8 restored feature columns but indexed G2/G3 by endpoint, so a 8 blob
# would draw every arc as its chord — the version refuses it outright.
# 11: the per-layer motion counts changed semantics (born from the
# build walk, not the hydrated arrays) — every older cache's
# counts may read zero for never-hydrated layers, which is exactly
# the resumed-session dead-slider report; refusing them rebuilds.
_CACHE_VERSION = 14


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


def _event_columns(counts, events, seeds):
    if not events:
        events = [[] for _ in counts]
    if not seeds:
        seeds = [False] * len(counts)
    if len(events) != len(counts) or len(seeds) != len(counts) or not all(isinstance(v, bool) for v in seeds):
        return None
    clean = []
    for count, rows in zip(counts, events, strict=True):
        layer = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                return None
            motion, retract = row
            if not isinstance(motion, int) or not isinstance(retract, bool) or not 0 <= motion <= count or layer and motion < layer[-1][0]:
                return None
            layer.append([motion, retract])
        clean.append(layer)
    return {"extruder_events": clean, "start_retracted": list(seeds)}


def _feature_columns(counts: Sequence[int], type_names: Sequence[str], columns) -> Optional[Dict]:
    """Validate the per-motion feature columns, or None when they are ragged.

    The writer and the reader both come through here, because the columns
    that draw a layer's colours and its travel boundaries are only worth
    restoring if they agree with the geometry they belong to: a layer
    whose runs do not total its motion count, a marker outside its layer,
    or a code past the vocabulary would restore a *different* index than
    the one saved. Validation returning None means the blob must not be
    published — or not be trusted. An index carrying no feature data at
    all (a hand-built one, or a blob predating the columns) answers an
    empty mapping, so they are simply absent from the header.
    """
    runs, starts, ends, start_types, start_e, start_e_absolute, start_extruding = columns
    try:
        if all(len(column) == 0 for column in columns):
            return {}
    except TypeError:
        return None
    if not isinstance(type_names, list) or len(type_names) > _MAX_TYPE_NAMES:
        return None
    if not all(isinstance(name, str) for name in type_names):
        return None
    layer_count = len(counts)
    if any(not isinstance(column, list) or len(column) != layer_count for column in columns):
        return None

    def markers_or_none(values, count):
        cleaned = []
        previous = -1
        for marker in values:
            if not isinstance(marker, int) or isinstance(marker, bool) or not previous < marker < count:
                return None
            previous = marker
            cleaned.append(marker)
        return cleaned

    codes = len(type_names) + 2
    if not all(isinstance(code, int) and 0 <= code < codes for code in start_types):
        return None
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and math.isfinite(value) for value in start_e):
        return None
    if not all(isinstance(flag, bool) for flag in (*start_e_absolute, *start_extruding)):
        return None

    entries = 0
    clean_runs: List[List[List[int]]] = []
    clean_starts: List[List[int]] = []
    clean_ends: List[List[int]] = []
    for layer, count in enumerate(counts):
        layer_runs: List[List[int]] = []
        total = 0
        if len(runs[layer]) > _MAX_TYPE_RUNS_PER_LAYER:
            return None
        for run in runs[layer]:
            if not isinstance(run, list) or len(run) != 2:
                return None
            span, code = run
            if not isinstance(span, int) or isinstance(span, bool) or span < 1:
                return None
            if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code < codes:
                return None
            total += span
            layer_runs.append([span, code])
        # The runs must cover the layer's motions exactly: a layer that
        # restores a shorter path than its offsets describe is the
        # "different index" this validation exists to stop.
        if total != count:
            return None
        layer_starts = markers_or_none(starts[layer], count)
        layer_ends = markers_or_none(ends[layer], count)
        if layer_starts is None or layer_ends is None:
            return None
        entries += len(layer_runs) + len(layer_starts) + len(layer_ends)
        clean_runs.append(layer_runs)
        clean_starts.append(layer_starts)
        clean_ends.append(layer_ends)
    if entries > _MAX_CACHE_FEATURE_ENTRIES:
        return {}
    return {
        "type_names": list(type_names),
        "type_runs": clean_runs,
        "travel_starts": clean_starts,
        "travel_ends": clean_ends,
        "start_types": list(start_types),
        "start_e": [float(value) for value in start_e],
        "start_e_absolute": list(start_e_absolute),
        "start_extruding": list(start_extruding),
    }


def _arc_entries(arcs) -> List[List[list]]:
    """The in-memory descriptors as the sorted entry lists the blob holds.

    The live index keys its descriptors by motion (a sparse mapping, so
    the no-arc path allocates nothing per motion); the blob stores them
    as ordered entries, which is also the shape both sides validate.
    """
    entries: List[List[list]] = []
    for layer_arcs in arcs:
        if isinstance(layer_arcs, dict):
            entries.append([[int(motion), plane, bool(clockwise), float(offset_a), float(offset_b)]
                            for motion, (plane, clockwise, offset_a, offset_b)
                            in sorted(layer_arcs.items())])
        else:
            entries.append(list(layer_arcs))
    return entries


def _arc_columns(counts: Sequence[int], arcs, start_planes) -> Optional[Dict]:
    """Validate the arc descriptors and the layer-start planes, or None.

    The writer and the reader both come through here, for the same reason
    the feature columns do: a descriptor naming a motion its layer does
    not have, a plane that is not a plane, or offsets that draw no circle
    would restore an index whose arcs are not the arcs that were saved.
    Returning None means the blob must not be published — or not be
    trusted. An index with no arc data at all (every G0/G1 file) answers
    an empty mapping, so its header carries no arc keys and pays nothing
    for the feature.

    Past the entry budget the answer is None, never a header without the
    descriptors: the layer-start planes alone would restore an index
    whose arcs draw as chords — geometry the file never commanded —
    stored durably and indistinguishable from a file that has no arcs.
    A cache is faithful or it is not written.
    """
    try:
        if not any(arcs) and all(plane == ArcGeometry.PLANE_XY for plane in start_planes):
            return {}
    except TypeError:
        return None
    layer_count = len(counts)
    if not isinstance(arcs, (list, tuple)) or not isinstance(start_planes, (list, tuple)):
        return None
    if len(arcs) != layer_count or len(start_planes) != layer_count:
        return None
    if not all(isinstance(plane, int) and not isinstance(plane, bool)
               and plane in ArcGeometry.PLANES for plane in start_planes):
        return None
    entries = 0
    clean: List[List[list]] = []
    for layer, count in enumerate(counts):
        layer_arcs = arcs[layer]
        if not isinstance(layer_arcs, (list, tuple)):
            return None
        cleaned: List[list] = []
        previous = -1
        for entry in layer_arcs:
            if not isinstance(entry, (list, tuple)) or len(entry) != 5:
                return None
            motion, plane, clockwise, offset_a, offset_b = entry
            if not isinstance(motion, int) or isinstance(motion, bool) or not previous < motion < count:
                return None
            if not isinstance(plane, int) or isinstance(plane, bool) or plane not in ArcGeometry.PLANES:
                return None
            if not isinstance(clockwise, bool):
                return None
            if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                       and math.isfinite(value) for value in (offset_a, offset_b)):
                return None
            if ArcGeometry.degenerate(offset_a, offset_b):
                return None
            previous = motion
            cleaned.append([motion, plane, clockwise, float(offset_a), float(offset_b)])
        entries += len(cleaned)
        clean.append(cleaned)
    planes = [int(plane) for plane in start_planes]
    if entries > _MAX_CACHE_ARC_ENTRIES:
        return None
    return {"arcs": clean, "start_arc_plane": planes}
