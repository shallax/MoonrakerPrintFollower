"""Assemble scanned layer records into the query index.

The scanner owns G-code modal state and the hot input loop. Assembly only
projects completed records, maps marker/PAUSE offsets and derives print-wide
metric ranges; it never reads a file or schedules work.
"""
from __future__ import annotations

import math
from array import array
from bisect import bisect_right
from typing import Dict, List, Optional, Tuple
from .MotionIndex import LayerMotionIndex

def _print_colour_ranges(blocks, heights, diameter, diameters):
    result = {}
    for block, height in zip(blocks, heights, strict=True):
        for tool, limits in (block.get("metric_limits") or {}).items():
            if len(limits) != 6 or not math.isfinite(limits[0]):
                continue
            d = diameters.get(tool, diameter)
            area = math.pi * d * d / 4
            values = {"speed": limits[:2], "height": (height, height),
                      "width": tuple(v * area / height for v in limits[2:4]),
                      "flow": tuple(v * area for v in limits[4:6])}
            for name, (lo, hi) in values.items():
                if not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo <= hi <= 1e9):
                    continue
                previous = result.get(name, (lo, hi))
                result[name] = (min(lo, previous[0]), max(hi, previous[1]))
    return result

def assemble_index(blocks, file_end, stats_values, marker_values, pause_offsets, type_names, filament_diameter, filament_diameters, compact, cancel_event) -> LayerMotionIndex:
    ranges: List[Tuple[int, int]] = []
    motions: List[array] = []
    xs: List[array] = []
    ys: List[array] = []
    zs: List[array] = []
    types: List[List[List[int]]] = []
    travel_starts: List[List[int]] = []
    travel_ends: List[List[int]] = []
    extrusion_columns = []
    speed_columns, tool_columns, start_speeds, start_tools = [], [], [], []
    layer_heights = []
    last_print_z = 0.0
    extruder_events = []
    start_retracted = []
    start_retractions = []
    firmware_retractions = []
    starts: List[Tuple[float, float, float]] = []
    start_absolute: List[bool] = []
    start_units: List[float] = []
    start_types: List[int] = []
    start_e: List[float] = []
    start_e_absolute: List[bool] = []
    start_extruding: List[bool] = []
    arcs: List[Dict[int, tuple]] = []
    start_arc_plane: List[int] = []
    elapsed_times: List[Optional[float]] = []
    block_stats: List[Optional[int]] = []
    layer_counts: List[int] = []
    for block in blocks:
        start = int(block["start"])
        end = int(block["end"] if block["end"] is not None else file_end)
        ranges.append((start, max(start + 1, end)))
        # The walk's per-layer motion counter, captured at the close:
        # the layer's true total — the collected arrays undercount (the
        # compact scan collects nothing; the per-layer cap truncates)
        # and a resumed session's prepared layers never hydrate, so a
        # zero count would leave the scrub slider dead forever.
        layer_counts.append(int(block.get("motion_total", 0)))
        motions.append(block["motions"])
        xs.append(block["x"])
        ys.append(block["y"])
        zs.append(block["z"])
        block_features = block["features"] or ([], [], [])
        types.append(block_features[0])
        travel_starts.append(block_features[1])
        travel_ends.append(block_features[2])
        extrusion_columns.append(block.get("extrusions", array("f")))
        speed_columns.append(block.get("speeds", array("f")))
        tool_columns.append(block.get("tools", array("H")))
        start_speeds.append(block.get("start_speed", 0.0))
        start_tools.append(block.get("start_tool", 0))
        print_z = block.get("print_z")
        height = print_z - last_print_z if print_z is not None else 0.0
        layer_heights.append(height if 0.001 <= height <= 10 else 0.2)
        if print_z is not None:
            last_print_z = print_z
        extruder_events.append(block.get("events", []))
        start_retracted.append(block.get("start_retracted", False))
        start_retractions.append(block.get("start_retractions", {}))
        firmware_retractions.append(block.get("firmware_retractions", []))
        arcs.append(block["arcs"])
        start_arc_plane.append(int(block["start_arc_plane"]))
        starts.append(tuple(float(v) for v in block["start_position"]))
        start_absolute.append(bool(block["start_absolute"]))
        start_units.append(float(block["start_units"]))
        start_types.append(int(block["start_type"]))
        start_e.append(float(block["start_e"]))
        start_e_absolute.append(bool(block["start_e_absolute"]))
        start_extruding.append(bool(block["start_extruding"]))
        elapsed = block.get("elapsed")
        elapsed_times.append(float(elapsed) if elapsed is not None else None)
        stats = block.get("stats")
        block_stats.append(int(stats) if stats is not None else None)

    # The baked pauses map to layers by block START only. A block's
    # recorded end is the ;TIME_ELAPSED line, and PauseAtHeight
    # emits its pause block AFTER that — between the elapsed marker
    # and the next layer marker (the live report: the real job's M0
    # lines sat past the recorded end and were dropped). The next
    # block's start is the true boundary, so bisect on starts alone
    # is exact. A pause before the first marker (start gcode)
    # belongs to no layer and is skipped.
    pause_layers: Tuple[int, ...] = ()
    if ranges and pause_offsets:
        baked: List[int] = []
        starts_offsets = [start for start, _end in ranges]
        for offset in pause_offsets:
            idx = bisect_right(starts_offsets, offset) - 1
            if idx < 0 or idx >= len(ranges):
                continue
            if not baked or baked[-1] != idx:
                baked.append(idx)
        pause_layers = tuple(baked)

    if cancel_event is not None and cancel_event.is_set():
        return LayerMotionIndex()
    ranges = ranges or []
    motions = motions or []
    xs = xs or []
    ys = ys or []
    zs = zs or []
    types = types or []
    travel_starts = travel_starts or []
    travel_ends = travel_ends or []
    starts = starts or []
    start_absolute = start_absolute or []
    start_units = start_units or []
    start_types = start_types or []
    start_e = start_e or []
    start_e_absolute = start_e_absolute or []
    start_extruding = start_extruding or []
    arcs = arcs or []
    start_arc_plane = start_arc_plane or []
    elapsed_times = elapsed_times or []
    stats_values = stats_values or []

    layer_map: Dict[int, int] = {}
    # Per-block values are positionally grounded: each layer's own first
    # CURRENT_LAYER, immune to leading start-gcode values and trailing extras.
    if ranges and len(block_stats) == len(ranges) and all(value is not None for value in block_stats):
        for index, value in enumerate(block_stats):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index
    if not layer_map and ranges and len(stats_values) == len(ranges):
        for index, value in enumerate(stats_values):
            if value in layer_map:
                layer_map = {}
                break
            layer_map[value] = index
    elif not layer_map and ranges and len(marker_values) == len(ranges):
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
        motion_arcs=arcs,
        motion_types=types,
        type_names=type_names,
        travel_starts=travel_starts,
        travel_ends=travel_ends,
        motion_extrusion=extrusion_columns,
        layer_heights=layer_heights,
        filament_diameter=filament_diameter, filament_diameters=filament_diameters,
        motion_speeds=speed_columns, motion_tools=tool_columns,
        layer_start_speeds=start_speeds, layer_start_tools=start_tools,
        colour_ranges=_print_colour_ranges(blocks, layer_heights, filament_diameter, filament_diameters),
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
        pauses=pause_layers,
        compact=bool(compact),
        hydrated_layers=hydrated,
        layer_motion_counts=layer_counts,
    )
