# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-13 [54a31e5] Implement interrupted print recovery handover

"""Read-only recovery planning for a paused virtual-SD print.

The plan reconstructs the G-code parser state at an exact motion boundary.
It deliberately emits no printer commands: a separate fresh-state preflight
must verify the file, printer and staged return before a handover is possible.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
import re

from ..gcode import ArcGeometry
from ..gcode.GCodeParser import _COMMAND, _MOTION, _parse_arc_words, _parse_axes
from ..gcode.IndexLimits import _MAX_LINE_BYTES


class RecoveryPlanError(ValueError):
    """The source cannot support an exact recovery plan at this boundary."""


_NAMED_COMMAND = re.compile(rb"^\s*([A-Z_][A-Z_0-9]*)\b")
_S_WORD = re.compile(rb"(?:^|\s)S\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?=\s|$)", re.I)
_P_WORD = re.compile(rb"(?:^|\s)P\s*(\d+)(?=\s|$)", re.I)
_MACRO_ARG = re.compile(rb"(?:^|\s)([A-Z][A-Z0-9_]*)=([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?=\s|$)", re.I)
_IGNORED = frozenset({b"M73", b"M117", b"M118", b"M400", b"G4", b"M0", b"M25",
                      b"PAUSE", b"SET_PRINT_STATS_INFO", b"EXCLUDE_OBJECT_DEFINE",
                      b"EXCLUDE_OBJECT_START", b"EXCLUDE_OBJECT_END"})
_PAUSE_COMMANDS = frozenset({b"M0", b"M25", b"PAUSE"})
_MOTIONS = frozenset({b"G0", b"G00", b"G1", b"G01", b"G2", b"G02", b"G3", b"G03"})


def _number(value, label):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise RecoveryPlanError(f"Invalid {label}") from error
    if not math.isfinite(result):
        raise RecoveryPlanError(f"Non-finite {label}")
    return result


def _word(pattern, code, default, label):
    match = pattern.search(code)
    return _number(match.group(1), label) if match is not None else default


@dataclass(frozen=True)
class RecoveryModalState:
    position: tuple[float, float, float]
    e: float
    absolute_xyz: bool
    force_relative_e: bool
    feedrate: float | None
    speed_percent: float
    flow_percent: float
    fan_speed: int | None
    hotend_target: float | None
    bed_target: float | None
    acceleration: float | None = None

    @property
    def absolute_e(self):
        # Klipper M82 removes the forced-relative override. G91 still
        # makes E relative, regardless of the last M82.
        return self.absolute_xyz and not self.force_relative_e


@dataclass(frozen=True)
class RecoverySourcePlan:
    boundary_offset: int
    file_size: int
    sha256: str
    state: RecoveryModalState
    printed_top_z: float = 0.0
    preview_segments: tuple = ()


@dataclass(frozen=True)
class LeadInSuggestion:
    boundary: object
    replayed_motions: int
    extrusion_path_mm: float
    limited_by_seam: bool = False


def suggest_lead_in(view, suspect, minimum_mm=5.0, minimum_moves=0):
    """Find a contiguous extrusion run before the suspected stop.

    The first policy deliberately stays within the selected layer and stops
    at a travel, retraction or non-extruding move. Crossing that seam could
    replay already completed geometry after a different priming state. None
    leaves the user's estimate in place for manual adjustment.
    """
    minimum_mm = _number(minimum_mm, "lead-in distance")
    if type(minimum_moves) is not int or minimum_moves < 0 or minimum_mm <= 0 or suspect is None:
        return None
    index = view._index
    layer, split = suspect.selected_layer, suspect.completed_motions
    if split <= 0:
        return None
    with index.cache_lock:
        if not view._exact_layer(layer) or layer >= len(index.motion_extrusion):
            return None
        extrusions = index.motion_extrusion[layer]
        if len(extrusions) != index.motion_count(layer):
            return None
        distance = 0.0
        count = 0
        first_boundary = None
        start = index.layer_start_positions[layer]
        for motion in range(split - 1, -1, -1):
            if extrusions[motion] <= 0:
                break
            end = (index.motion_x[layer][motion], index.motion_y[layer][motion],
                   index.motion_z[layer][motion])
            before = start if motion == 0 else (
                index.motion_x[layer][motion - 1], index.motion_y[layer][motion - 1],
                index.motion_z[layer][motion - 1])
            arc = index.motion_arcs[layer].get(motion) if layer < len(index.motion_arcs) else None
            length = (ArcGeometry.path_length(arc, before, end, xy=True) if arc
                      else math.hypot(end[0] - before[0], end[1] - before[1]))
            if not math.isfinite(length) or length <= 0:
                break
            distance += length
            count += 1
            first_boundary = view.recovery_boundary(layer, motion)
            if first_boundary is None:
                return None
            if (count >= minimum_moves if minimum_moves else distance >= minimum_mm):
                return LeadInSuggestion(first_boundary, count, distance)
        return (LeadInSuggestion(first_boundary, count, distance, True)
                if first_boundary is not None else None)


def scan_source(path, boundary, observed_offset=None, *, setup_macros=(), transparent_macros=(),
                nozzle_parameter="EXTRUDER", bed_parameter="BED", preview_offset=None):
    """Reconstruct supported modal state immediately before a motion.

    The exact original byte offset must name a complete G0/G1/G2/G3 line.
    Unknown commands fail closed because a macro may alter coordinates,
    extrusion, heat, fans or transforms. No prefix is replayed on the printer.
    """
    if boundary is None or not isinstance(boundary.next_motion_offset, int):
        raise RecoveryPlanError("Choose an exact indexed motion boundary")
    target = boundary.next_motion_offset
    try:
        size = os.path.getsize(path)
    except OSError as error:
        raise RecoveryPlanError("The downloaded G-code is unavailable") from error
    if target < 0 or target >= size:
        raise RecoveryPlanError("The selected command is outside the source file")
    if observed_offset is None:
        observed_offset = target
    if type(observed_offset) is not int or not target <= observed_offset <= size:
        raise RecoveryPlanError("The observed print position is outside the source file")
    setup_macros = {str(name).upper().encode("ascii") for name in setup_macros}
    transparent_macros = {str(name).upper().encode("ascii") for name in transparent_macros}
    for name in (nozzle_parameter, bed_parameter):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", name):
            raise RecoveryPlanError("Invalid print-start temperature parameter name")
    if setup_macros & transparent_macros:
        raise RecoveryPlanError("A source macro has conflicting recovery contracts")
    position = [0.0, 0.0, 0.0]
    e = 0.0
    absolute_xyz = True
    force_relative_e = False
    feedrate = None
    speed_percent = flow_percent = 100.0
    fan_speed = hotend_target = bed_target = None
    acceleration = None
    printed_top_z = 0.0
    unknown_axes = set()
    unknown_e = False
    selected_state = None
    seen_target = False
    preview_limit = target if preview_offset is None else preview_offset
    preview_segments = []
    preview_count = 0
    preview_stride = 1
    plane = ArcGeometry.PLANE_XY
    def modal_state():
        if unknown_axes or unknown_e:
            raise RecoveryPlanError("A setup macro needs a full absolute XYZ move and G92 E before this point")
        return RecoveryModalState(tuple(position), e, absolute_xyz, force_relative_e,
                                  feedrate, speed_percent, flow_percent, fan_speed,
                                  hotend_target, bed_target, acceleration)
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while handle.tell() < observed_offset:
                offset = handle.tell()
                line = handle.readline(_MAX_LINE_BYTES + 1)
                if not line or len(line) > _MAX_LINE_BYTES:
                    raise RecoveryPlanError("The observed offset is not a valid command boundary")
                digest.update(line)
                if offset == target:
                    if not _MOTION.match(line.split(b";", 1)[0]):
                        raise RecoveryPlanError("The indexed offset no longer names a motion command")
                    selected_state = modal_state()
                    seen_target = True
                if handle.tell() > observed_offset:
                    break  # Moonraker may report a position inside the queued line.
                code = line.split(b";", 1)[0].strip().upper()
                if not code:
                    continue
                match = _COMMAND.match(code) or _NAMED_COMMAND.match(code)
                if match is None:
                    raise RecoveryPlanError(f"Unsupported G-code at byte {offset}")
                command = match.group(1)
                axes = _parse_axes(code)
                if command in _MOTIONS:
                    old_position = tuple(position)
                    old_e = e
                    for axis, index in (("X", 0), ("Y", 1), ("Z", 2)):
                        if axis in axes:
                            value = _number(axes[axis], axis)
                            position[index] = value if absolute_xyz else position[index] + value
                            if absolute_xyz:
                                unknown_axes.discard(axis)
                    if "E" in axes:
                        value = _number(axes["E"], "E")
                        e = e + value if force_relative_e or not absolute_xyz else value
                    if (e > old_e and (position[0] != old_position[0]
                            or position[1] != old_position[1]
                            or command in {b"G2", b"G02", b"G3", b"G03"})):
                        printed_top_z = max(printed_top_z, position[2], old_position[2])
                        if handle.tell() <= preview_limit and not unknown_axes and not unknown_e:
                            points = [old_position, tuple(position)]
                            if command in {b"G2", b"G02", b"G3", b"G03"}:
                                desc = ArcGeometry.descriptor(plane, command in {b"G2", b"G02"},
                                                              _parse_arc_words(code), absolute_xyz=absolute_xyz)
                                if desc is not None:
                                    points = [old_position, *ArcGeometry.tessellate(desc, old_position, position)]
                            for start, end in zip(points, points[1:], strict=False):
                                if preview_count % preview_stride == 0:
                                    preview_segments.append((*start, *end))
                                preview_count += 1
                                if len(preview_segments) > 12000:
                                    preview_segments = preview_segments[::2]
                                    preview_stride *= 2
                    if "F" in axes:
                        feedrate = _number(axes["F"], "feedrate")
                        if feedrate <= 0:
                            raise RecoveryPlanError("Invalid feedrate")
                elif command == b"G90":
                    absolute_xyz = True
                elif command == b"G91":
                    absolute_xyz = False
                elif command == b"M82":
                    force_relative_e = False
                elif command == b"M83":
                    force_relative_e = True
                elif command == b"G92":
                    if not axes or any(axis in axes for axis in "XYZ"):
                        raise RecoveryPlanError("G92 XYZ position resets are not supported")
                    if "E" in axes:
                        e = _number(axes["E"], "E reset")
                        unknown_e = False
                elif command in {b"G17", b"G18", b"G19"}:
                    plane = int(command[1:])
                elif command == b"G21":
                    pass
                elif command == b"G20":
                    raise RecoveryPlanError("Inch-mode G-code is not supported by Klipper")
                elif command in {b"M220", b"M221"}:
                    value = _word(_S_WORD, code, 100.0, "factor")
                    if value <= 0:
                        raise RecoveryPlanError("Invalid speed or flow factor")
                    if command == b"M220":
                        speed_percent = value
                    else:
                        flow_percent = value
                elif command == b"M204":
                    value = _word(_S_WORD, code, None, "acceleration")
                    if value is None or value <= 0:
                        raise RecoveryPlanError("Unsupported acceleration command")
                    acceleration = value
                elif command in {b"M106", b"M107"}:
                    fan = _word(_P_WORD, code, 0, "fan index")
                    if fan != 0:
                        raise RecoveryPlanError("Multiple fan channels are not supported")
                    value = 0 if command == b"M107" else _word(_S_WORD, code, 255, "fan speed")
                    if not 0 <= value <= 255:
                        raise RecoveryPlanError("Invalid fan speed")
                    fan_speed = int(value)
                elif command in {b"M104", b"M109", b"M140", b"M190"}:
                    value = _word(_S_WORD, code, None, "temperature")
                    if value is None or value < 0:
                        raise RecoveryPlanError("Unsupported temperature command")
                    if command in {b"M104", b"M109"}:
                        hotend_target = value
                    else:
                        bed_target = value
                elif command in setup_macros:
                    if offset >= target or printed_top_z > 0:
                        raise RecoveryPlanError("A setup macro cannot be crossed during a recovery rewind")
                    arguments = {name.decode("ascii"): _number(value, "macro temperature")
                                 for name, value in _MACRO_ARG.findall(code)}
                    if nozzle_parameter in arguments:
                        hotend_target = arguments[nozzle_parameter]
                    if bed_parameter in arguments:
                        bed_target = arguments[bed_parameter]
                    unknown_axes = set("XYZ")
                    unknown_e = True
                elif command in transparent_macros:
                    pass
                elif command in _PAUSE_COMMANDS and offset >= target:
                    raise RecoveryPlanError("The selected replay would cross another pause command")
                elif command == b"T0" or command in _IGNORED:
                    pass
                else:
                    raise RecoveryPlanError(
                        f"Command {command.decode('ascii', 'replace')} before the selected point needs review")
            if not seen_target:
                line = handle.readline(_MAX_LINE_BYTES + 1)
                if len(line) > _MAX_LINE_BYTES or not _MOTION.match(line.split(b";", 1)[0]):
                    raise RecoveryPlanError("The indexed offset no longer names a motion command")
                selected_state = modal_state()
                digest.update(line)
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise RecoveryPlanError("The downloaded G-code could not be read") from error
    if size != os.path.getsize(path):
        raise RecoveryPlanError("The downloaded G-code changed during planning")
    if any(abs(a - float(b)) > 0.001 for a, b in zip(selected_state.position, boundary.position, strict=True)):
        raise RecoveryPlanError("Indexed and reconstructed positions disagree")
    return RecoverySourcePlan(target, size, digest.hexdigest(), selected_state, printed_top_z,
                              tuple(preview_segments))
