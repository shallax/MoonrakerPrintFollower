"""Hydrate one compact index layer, retaining its opening modal state."""
from __future__ import annotations


import time

from array import array
from typing import Callable, Dict, Optional

from . import ArcGeometry

from .FeatureTracker import _FeatureTracker, _TYPE_NONE, _TYPE_OTHER
from .GCodeParser import _ARC_CLOCKWISE, _ARC_COUNTER, _COMMAND, _FAST_MOTIONS, _MOTION, _TYPE_COMMENT, _TYPE_PREFIX, _fast_motion_line, _parse_arc_words, _parse_axes
from .IndexLimits import _MAX_LINE_BYTES, _MAX_MOTIONS_PER_LAYER, _MAX_TYPE_NAME_BYTES
from .IndexWork import HydrationYield, _YIELD_CHECK_MASK, passive_yield
from .MotionIndex import LayerMotionIndex

def hydrate_layer_from_file(index: LayerMotionIndex, path: str, layer: int,
                            keep_anchor: Optional[int] = None,
                            should_stop: Optional[Callable[[], bool]] = None) -> bool:
    """Populate motion data for one layer of a compact large-file index.

    Boundary indexing keeps RAM bounded for huge files. Motion commands are then
    loaded only for layers actually viewed during the live print. Byte-position
    following remains available while hydration is pending.

    keep_anchor names the FOLLOWED layer: the eviction window keeps
    [anchor-1, anchor+1] around it, so a prefetched look-ahead layer
    survives and the window follows the print rather than whichever
    layer the background worker picked last. Without keep_anchor the
    anchor is the index's followed_layer (set by the service, read
    HERE at completion so a worker finishing after an anchor change
    applies the latest policy), then the hydrated layer.

    The parse walks the layer's feature state as well as its geometry:
    the E axis for the travel boundaries and the ;TYPE: markers for the
    feature runs. Both are seeded from the scan's per-layer opening state,
    so a hydrated layer matches the full scan it stands in for.

    should_stop is consulted on the walk's own wall-clock gate, and the
    layer is abandoned with HydrationYield when it answers True. It is
    the background pass's caller that passes one: the pass runs on
    speculation and must yield to a demand, while a demand's own
    hydration is the foreground and has nothing to yield to. Callers
    that pass nothing keep the plain hand-back and no interruption.
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
            features = _FeatureTracker()
            seed_type = index.layer_start_types[layer] if layer < len(index.layer_start_types) else _TYPE_NONE
            features.last_type = seed_type
            features.open_type = seed_type
            features.e = index.layer_start_e[layer] if layer < len(index.layer_start_e) else 0.0
            features.absolute_e = index.layer_start_e_absolute[layer] if layer < len(index.layer_start_e_absolute) else True
            features.extruding = index.layer_start_extruding[layer] if layer < len(index.layer_start_extruding) else True
            features.retracted = index.layer_start_retracted[layer] if layer < len(index.layer_start_retracted) else False
            features.speed = index.layer_start_speeds[layer] if layer < len(index.layer_start_speeds) else 0.0
            features.tool = index.layer_start_tools[layer] if layer < len(index.layer_start_tools) else 0
            features.retractions = dict(index.layer_start_retractions[layer]) if layer < len(index.layer_start_retractions) else {}
            # The plane at this layer's first motion, never a default XY:
            # a G18 issued before the layer decides what its arcs mean.
            features.plane = index.layer_start_arc_plane[layer] \
                if layer < len(index.layer_start_arc_plane) else ArcGeometry.PLANE_XY
            type_lookup: Dict[str, int] = {}
            offsets = array("Q")
            xs = array("f")
            ys = array("f")
            zs = array("f")
            arcs: Dict[int, tuple] = {}
            # The walk's own hand-back. Without it a dense layer is a
            # single uninterrupted hold on the interpreter, and the
            # seek that arrives while it runs waits the layer out.
            yield_at = time.monotonic()
            checked = 0
            while handle.tell() < end:
                checked += 1
                if (checked & _YIELD_CHECK_MASK) == 0:
                    updated = passive_yield(time.monotonic(), yield_at)
                    if updated != yield_at:
                        yield_at = updated
                        if should_stop is not None and should_stop():
                            # Before the commit below: the layer keeps
                            # every array it had, which is none of them.
                            raise HydrationYield()
                offset = handle.tell()
                line = handle.readline(_MAX_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > _MAX_LINE_BYTES:
                    line = b""
                stripped = line.rstrip(b"\r\n")
                if stripped.lstrip().startswith(_TYPE_PREFIX):
                    type_match = _TYPE_COMMENT.match(stripped)
                    if type_match is not None:
                        name = type_match.group(1)[:_MAX_TYPE_NAME_BYTES].decode("ascii", "replace")
                        code = type_lookup.get(name)
                        if code is None:
                            # The vocabulary is the index's own; a name the
                            # scan never saw there is one the scan never
                            # saw either, so it reads as unknown rather
                            # than as a code this layer cannot name.
                            try:
                                code = index.type_names.index(name) + 2
                            except ValueError:
                                code = _TYPE_OTHER
                            type_lookup[name] = code
                        features.set_type(code)
                code = stripped.split(b";", 1)[0]
                fast = _fast_motion_line(code)
                if fast is not None:
                    command, axes = fast
                else:
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
                elif command == b"G17":
                    features.plane = ArcGeometry.PLANE_XY
                elif command == b"G18":
                    features.plane = ArcGeometry.PLANE_XZ
                elif command == b"G19":
                    features.plane = ArcGeometry.PLANE_YZ
                elif command == b"M82":
                    features.absolute_e = True
                elif command == b"M83":
                    features.absolute_e = False
                elif command in (b"G10", b"G11"):
                    if command == b"G10" or features.retracted:
                        features.extruder_event(command == b"G10", len(offsets) < _MAX_MOTIONS_PER_LAYER)
                elif command.startswith(b"T"):
                    tool = int(command[1:])
                    features.tool = tool if 0 <= tool < 16 else 0
                    features.retracted = features.retractions.get(features.tool, 0.0) != 0
                elif command == b"G92":
                    x = axes.get("X", x); y = axes.get("Y", y); z = axes.get("Z", z)
                    if "E" in axes: features.e = axes["E"]
                elif command in _FAST_MOTIONS or _MOTION.search(stripped):
                    arc = None
                    if command in _ARC_CLOCKWISE or command in _ARC_COUNTER:
                        arc_words = _parse_arc_words(code)
                        if units_scale != 1.0 and arc_words:
                            arc_words = {word: value * units_scale for word, value in arc_words.items()}
                        arc = ArcGeometry.descriptor(
                            features.plane, command in _ARC_CLOCKWISE, arc_words,
                            absolute_xyz=absolute_xyz)
                    if "X" in axes: x = axes["X"] if absolute_xyz else x + axes["X"]
                    if "Y" in axes: y = axes["Y"] if absolute_xyz else y + axes["Y"]
                    if "Z" in axes: z = axes["Z"] if absolute_xyz else z + axes["Z"]
                    collect_here = len(offsets) < _MAX_MOTIONS_PER_LAYER
                    features.add(axes, collect_here)
                    if collect_here:
                        if arc is not None:
                            arcs[len(offsets)] = arc
                        offsets.append(offset); xs.append(x); ys.append(y); zs.append(z)
        with index.cache_lock:
            while len(index.motion_offsets) < len(index.ranges):
                index.motion_offsets.append(array("Q")); index.motion_x.append(array("f")); index.motion_y.append(array("f")); index.motion_z.append(array("f"))
                index.motion_arcs.append({})
                index.motion_types.append([]); index.travel_starts.append([]); index.travel_ends.append([])
                index.layer_start_types.append(_TYPE_NONE); index.layer_start_e.append(0.0)
                index.layer_start_e_absolute.append(True); index.layer_start_extruding.append(True)
                index.layer_start_arc_plane.append(ArcGeometry.PLANE_XY)
                if len(index.layer_motion_counts) < len(index.ranges):
                    index.layer_motion_counts.append(0)
            while len(index.extruder_events) < len(index.ranges):
                index.extruder_events.append([])
            index.extruder_events[layer] = features.events
            while len(index.firmware_retractions) < len(index.ranges):
                index.firmware_retractions.append([])
            index.firmware_retractions[layer] = features.firmware_events
            while len(index.motion_extrusion) < len(index.ranges):
                index.motion_extrusion.append(array("f"))
            index.motion_extrusion[layer] = features.extrusions
            while len(index.motion_speeds) < len(index.ranges):
                index.motion_speeds.append(array("f"))
                index.motion_tools.append(array("H"))
            index.motion_speeds[layer], index.motion_tools[layer] = features.speeds, features.tools
            index.motion_offsets[layer] = offsets
            index.layer_motion_counts[layer] = len(offsets)
            index.motion_x[layer] = xs
            index.motion_y[layer] = ys
            index.motion_z[layer] = zs
            index.motion_arcs[layer] = arcs
            hydrated_runs, hydrated_starts, hydrated_ends = features.payload()
            index.motion_types[layer] = hydrated_runs
            index.travel_starts[layer] = hydrated_starts
            index.travel_ends[layer] = hydrated_ends
            index.hydrated_layers.add(layer)
            # The retention bound (the live report's progress-driven
            # growth): hydration was demand-driven as the print
            # advanced and nothing ever dropped an old layer, so a long
            # print accumulated motion arrays for every layer it
            # crossed. The window keeps the previous, current and
            # look-ahead layers around the anchor; any reader of an
            # evicted layer sees an empty array (the same degraded
            # fallback as a never-hydrated one).
            anchor = keep_anchor
            if anchor is None:
                anchor = index.followed_layer
            if anchor is None:
                anchor = layer
            # A frozen follower layer is a second anchor: its window
            # survives the live one's advance (the pop-over's detach
            # would otherwise evict the very layer it is showing).
            manual = index.manual_anchor
            # The freshly hydrated layer's own window is a THIRD
            # survivor: the background full-cache pass hydrates layers
            # far outside both anchors and must hold the arrays valid
            # until its prepare and encode complete — the next pass
            # hydrate evicts this layer's window, so the union stays
            # bounded at the live, manual and in-flight windows.
            for old in sorted(index.hydrated_layers):
                if (old < anchor - 1 or old > anchor + 1) \
                        and (manual is None or old < manual - 1 or old > manual + 1) \
                        and (old < layer - 1 or old > layer + 1):
                    index.motion_offsets[old] = array("Q")
                    index.motion_x[old] = array("f")
                    index.motion_y[old] = array("f")
                    index.motion_z[old] = array("f")
                    # The feature columns travel with the geometry, or an
                    # evicted layer would hand back a full set of runs for
                    # an empty motion list. The arc descriptors are keyed
                    # by motion index and go with them.
                    index.motion_arcs[old] = {}
                    index.motion_types[old] = []
                    index.travel_starts[old] = []
                    index.travel_ends[old] = []
                    if old < len(index.extruder_events):
                        index.extruder_events[old] = []
                    if old < len(index.firmware_retractions):
                        index.firmware_retractions[old] = []
                    if old < len(index.motion_extrusion):
                        index.motion_extrusion[old] = array("f")
                    if old < len(index.motion_speeds):
                        index.motion_speeds[old] = array("f")
                        index.motion_tools[old] = array("H")
                    index.hydrated_layers.remove(old)
        return True
    except OSError:
        return False
