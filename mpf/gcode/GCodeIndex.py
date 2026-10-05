"""Build a layer/motion index from a G-code source file in one bounded scan."""
from __future__ import annotations

from .IndexAssembly import assemble_index
from .IndexHeader import read_header


import math
import os
import tempfile
import time

from array import array
from typing import BinaryIO, Dict, List, Optional

from . import ArcGeometry

from .FeatureTracker import _FeatureTracker, _TYPE_OTHER
from .GCodeParser import _ARC_CLOCKWISE, _ARC_COUNTER, _COMMAND, _ELAPSED, _FAST_MOTIONS, _MOTION, _PAUSE_COMMAND, _STATS_MARKER, _TYPE_COMMENT, _TYPE_PREFIX, _fast_motion_line, _parse_arc_words, _parse_axes
from .IndexLimits import _LARGE_FILE_COMPACT_THRESHOLD, _MAX_LAYER_BLOCKS, _MAX_LINE_BYTES, _MAX_MOTIONS_PER_LAYER, _MAX_TYPE_NAMES, _MAX_TYPE_NAME_BYTES
from .IndexWork import _BUILD_PROGRESS_MASK, _YIELD_CHECK_MASK, passive_yield
from .MotionIndex import LayerMotionIndex
from .ObjectWork import ObjectWorkTracker

def _emit_progress(handle: BinaryIO, progress) -> None:
    # The scanner's honest precision: the file offset against the
    # size. Every 4096 lines, so the callback stays cheap.
    try:
        size = os.fstat(handle.fileno()).st_size
        progress(min(1.0, handle.tell() / max(1, size)))
    except (OSError, ValueError):
        pass


def build_index_from_file(path: str, cancel_event=None, compact: Optional[bool] = None, progress=None, stage=None) -> LayerMotionIndex:
    # ONE pass (the ruling): a single read collects the
    # layer ranges, the marker values, the block stats, the motions
    # AND the pause offsets. The old four-pass build re-read the
    # file per concern, which restarted the progress bar per pass
    # and quadrupled the I/O on large files. The markers are checked
    # per line in the sniffed order, so the sniffed format still
    # wins cheaply and the fallbacks still catch files whose markers
    # the sniff window missed.
    if stage is not None:
        stage("Scanning layers")
    if compact is None:
        try:
            compact = os.path.getsize(path) >= _LARGE_FILE_COMPACT_THRESHOLD
        except OSError:
            compact = False

    winner, captures, filament_diameter, filament_diameters = read_header(path)

    blocks: List[dict] = []
    current: Optional[dict] = None
    stats_values: List[int] = []
    marker_values: List[int] = []
    pause_offsets: List[int] = []
    absolute_xyz = True
    units_scale = 1.0
    x = y = z = 0.0
    line_number = 0
    collect_motions = not compact
    # The feature walk runs for every build, compact included: the scan
    # still sees the ;TYPE: lines and the E words, and a compact index
    # keeps only each layer's opening state for the hydrator to resume
    # from.
    features = _FeatureTracker()
    object_work = ObjectWorkTracker()
    type_lookup: Dict[str, int] = {}
    type_names: List[str] = []

    yield_at = time.monotonic()
    with open(path, "rb") as handle:
        while True:
            if cancel_event is not None and (line_number & 0x3FF) == 0 and cancel_event.is_set():
                return LayerMotionIndex()
            if (line_number & _YIELD_CHECK_MASK) == 0 and line_number:
                # Release the GIL on the workers' own wall-clock gate:
                # the parse is a tight Python loop, and what starves the
                # UI thread is the TIME between hand-backs, not the line
                # count that separates them. The count decides only how
                # often the gate can be ASKED, so it has to be fine
                # enough that the wall-clock period is what governs the
                # hand-back.
                yield_at = passive_yield(time.monotonic(), yield_at)
            if (line_number & _BUILD_PROGRESS_MASK) == 0 and line_number:
                if progress is not None:
                    _emit_progress(handle, progress)
            offset = handle.tell()
            line = handle.readline(_MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > _MAX_LINE_BYTES:
                # A hostile/corrupt file with no newlines would load
                # a giant "line" into RAM and regex-scan it;
                # truncated garbage chunks simply match nothing and
                # are skipped.
                line = b""
            stripped = line.rstrip(b"\r\n")
            if stripped[:1] in (b"E", b"e", b";", b" ", b"\t"):
                marker_head = stripped.lstrip()
                if marker_head[:1].upper() in (b"E", b";") and marker_head.upper().startswith((b"EXCLUDE_OBJECT_", b";MESH:")):
                    object_work.marker(stripped)
            # Slicer motion lines cannot be anchored metadata markers. Parse
            # this common shape once, skipping four regex probes per move.
            code = stripped.split(b";", 1)[0]
            fast = _fast_motion_line(code)
            stats_match = None if fast is not None else _STATS_MARKER.search(stripped)
            if stats_match is not None:
                try:
                    value = int(stats_match.group(1))
                    if not stats_values or stats_values[-1] != value:
                        stats_values.append(value)
                    # Record the first CURRENT_LAYER seen inside each
                    # layer block. A global consecutive-value
                    # heuristic cannot tell a leading start-gcode
                    # value (CURRENT_LAYER=0 before the first
                    # ;LAYER) from the first layer's own value.
                    if current is not None and current["end"] is None and current["stats"] is None:
                        current["stats"] = value
                except (TypeError, ValueError):
                    pass

            # The layer-marker line: the sniffed format first, the
            # fallbacks in order. Only the ACTIVE marker opens
            # blocks — the old per-pass loop broke on the first pass
            # that found ranges, so a later format's lines (e.g. the
            # stats marker) never acted once an earlier one matched
            # anywhere. Without a sniff an earlier-ordered marker
            # still takes over retroactively (its pass would have
            # found this line before any later marker's pass ran).
            boundary = False
            matched = None
            if fast is None and winner is not None and winner.search(stripped) is not None:
                boundary = True
                matched = captures[winner]
            if boundary:
                if current is not None and current["end"] is None:
                    current["end"] = offset
                    # The motion total rides the close — the count is
                    # the walk's only every-motion record, and the
                    # layer's true total must survive the compact
                    # scan's empty arrays (the dead-scrub resume
                    # report). Captured only when the block actually
                    # closes here: an elapsed-closed block keeps its
                    # own total (its trailing travel belongs to no
                    # layer).
                    current["motion_total"] = features.count
                # The marker opens a layer: hand the closing one its
                # feature arrays and seed this one's opening state.
                finished = features.payload()
                finished_events = features.events
                finished_extrusions = features.extrusions
                finished_speeds, finished_tools = features.speeds, features.tools
                finished_firmware = features.firmware_events
                finished_limits = features.metric_limits
                features.open_layer()
                object_work.open_layer()
                if current is not None:
                    current["features"] = finished
                    current["events"] = finished_events
                    current["extrusions"] = finished_extrusions
                    current["speeds"], current["tools"] = finished_speeds, finished_tools
                    current["metric_limits"] = finished_limits
                    current["firmware_retractions"] = finished_firmware
                if len(blocks) >= _MAX_LAYER_BLOCKS:
                    # Marker-dense hostile file: stop tracking further
                    # layers. The last tracked block already closed at
                    # the offset above; everything after degrades to
                    # the byte-range fraction and the last known
                    # layer.
                    current = None
                else:
                    current = {
                        "start": offset,
                        "end": None,
                        "elapsed": None,
                        "stats": None,
                        "motions": array("Q"),
                        "x": array("f"),
                        "y": array("f"),
                        "z": array("f"),
                        "arcs": {},
                        "start_position": (x, y, z),
                        "start_absolute": absolute_xyz,
                        "start_units": units_scale,
                        "start_type": features.start_type,
                        "start_e": features.start_e,
                        "start_e_absolute": features.start_e_absolute,
                        "start_extruding": features.start_extruding,
                        "start_arc_plane": features.start_plane,
                        "features": None,
                        "events": [],
                        "extrusions": array("f"),
                        "print_z": None,
                        "start_retracted": features.start_retracted,
                        "start_retractions": features.start_retractions,
                        "start_speed": features.start_speed, "start_tool": features.start_tool,
                    }
                    blocks.append(current)
                if matched is not None:
                    capture_match = matched.search(stripped)
                    if capture_match is not None:
                        try:
                            marker_values.append(int(capture_match.group(1)))
                        except (TypeError, ValueError):
                            pass
            elif current is not None and current["end"] is None:
                elapsed_match = None if fast is not None else _ELAPSED.search(stripped)
                if elapsed_match is not None:
                    current["end"] = offset
                    # The elapsed marker closes the block as surely as the
                    # next layer marker does, so the feature arrays go with
                    # it: a layer closed here would otherwise keep the runs
                    # the tracker had not yet been handed, and a layer
                    # closed at EOF would never be given any.
                    current["features"] = features.payload()
                    current["events"] = features.events
                    current["extrusions"] = features.extrusions
                    current["speeds"], current["tools"] = features.speeds, features.tools
                    current["firmware_retractions"] = features.firmware_events
                    current["metric_limits"] = features.metric_limits
                    current["motion_total"] = features.count
                    try:
                        current["elapsed"] = float(elapsed_match.group(1))
                    except (TypeError, ValueError):
                        current["elapsed"] = None

            # A baked end-of-layer pause command (the pause-offsets
            # concern, collected in the same read).
            if fast is None and _PAUSE_COMMAND.match(stripped) is not None:
                pause_offsets.append(offset)

            # The slicer's feature marker. The prefix test is the fast
            # path — the anchored regex is the confirmation, so an
            # indented or oddly-spaced marker is still read.
            if fast is None and stripped.lstrip().startswith(_TYPE_PREFIX):
                type_match = _TYPE_COMMENT.match(stripped)
                if type_match is not None:
                    name = type_match.group(1)[:_MAX_TYPE_NAME_BYTES].decode("ascii", "replace")
                    type_code = type_lookup.get(name)
                    if type_code is None:
                        if len(type_names) >= _MAX_TYPE_NAMES:
                            type_code = _TYPE_OTHER
                        else:
                            type_names.append(name)
                            type_code = len(type_names) + 1
                            type_lookup[name] = type_code
                    features.set_type(type_code)

            # Track G-code XYZ state even outside the indexed layer
            # body. This is important for Cura files that emit
            # travel/macro motion between ;TIME_ELAPSED and the
            # following ;LAYER marker.
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
                collect_event = collect_motions and current is not None and current["end"] is None and features.count < _MAX_MOTIONS_PER_LAYER
                if command == b"G10" or features.retracted:
                    features.extruder_event(command == b"G10", collect_event)
            elif command.startswith(b"T"):
                tool = int(command[1:])
                features.tool = tool if 0 <= tool < 16 else 0
                features.retracted = features.retractions.get(features.tool, 0.0) != 0
            elif command == b"G92":
                if "X" in axes:
                    x = axes["X"]
                if "Y" in axes:
                    y = axes["Y"]
                if "Z" in axes:
                    z = axes["Z"]
                # An extruder reset (Cura's per-layer G92 E0) moves E
                # without extruding: it re-bases the axis, never reads as
                # a retraction.
                if "E" in axes:
                    features.e = axes["E"]
            elif command in _FAST_MOTIONS or _MOTION.search(stripped):
                # A G2/G3 is ONE motion like any other: it takes the next
                # index, its E decides extrusion, its ;TYPE: names its
                # feature. What its line adds is where the head actually
                # travelled — a circular (or helical) path the descriptor
                # records so the payload, the live-position match and the
                # printed-object walk all read the real curve instead of
                # the chord between its ends. The endpoint below is still
                # the truth the NEXT move's edge starts from.
                motion_start = (x, y, z)
                nx, ny, nz = x, y, z
                if "X" in axes:
                    nx = axes["X"] if absolute_xyz else x + axes["X"]
                if "Y" in axes:
                    ny = axes["Y"] if absolute_xyz else y + axes["Y"]
                if "Z" in axes:
                    nz = axes["Z"] if absolute_xyz else z + axes["Z"]
                arc = None
                if command in _ARC_CLOCKWISE or command in _ARC_COUNTER:
                    arc_words = _parse_arc_words(code)
                    if units_scale != 1.0 and arc_words:
                        arc_words = {word: value * units_scale for word, value in arc_words.items()}
                    arc = ArcGeometry.descriptor(
                        features.plane, command in _ARC_CLOCKWISE, arc_words,
                        absolute_xyz=absolute_xyz)
                moved_xy = nx != x or ny != y or arc is not None
                x, y, z = nx, ny, nz
                collect_here = collect_motions and current is not None \
                    and current["end"] is None \
                    and len(current["motions"]) < _MAX_MOTIONS_PER_LAYER
                # The feature walk must see EVERY motion, collected or
                # not: the per-layer cap and the elapsed-marker boundary
                # both stop the arrays without stopping the E state, and
                # the next layer resumes from that state.
                previous_e = features.e
                length = ArcGeometry.path_length(arc, motion_start, (x, y, z), xy=True) if arc else math.hypot(x - motion_start[0], y - motion_start[1])
                features.add(axes, collect_here, length)
                if (current is not None and current["end"] is None
                        and moved_xy and features.e > previous_e):
                    object_work.add(offset, x, y, z, features.e - previous_e)
                if current is not None and current["end"] is None and current["print_z"] is None and moved_xy and features.e > previous_e:
                    current["print_z"] = z
                if collect_here:
                    # Past the cap the layer's path data truncates and
                    # the byte-range fraction covers the rest — a
                    # one-layer hostile file must not grow multi-GB
                    # motion arrays.
                    if arc is not None:
                        current["arcs"][len(current["motions"])] = arc
                    current["motions"].append(offset)
                    current["x"].append(x)
                    current["y"].append(y)
                    current["z"].append(z)

            line_number += 1

        file_end = handle.tell()
        if current is not None and current["end"] is None:
            current["end"] = file_end
            current["features"] = features.payload()
            current["events"] = features.events
            current["extrusions"] = features.extrusions
            current["speeds"], current["tools"] = features.speeds, features.tools
            current["firmware_retractions"] = features.firmware_events
            current["metric_limits"] = features.metric_limits
            current["motion_total"] = features.count

    return assemble_index(
        blocks, file_end, stats_values, marker_values, pause_offsets, type_names, filament_diameter, filament_diameters, compact, cancel_event,
        object_work.finish(),
    )


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
