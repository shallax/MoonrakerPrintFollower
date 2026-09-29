"""Read slicer layer markers and filament metadata before the motion scan."""
from __future__ import annotations

import re
from .GCodeParser import (
    _LAYER_COMMENT, _CURA_LAYER_VALUE, _ORCA_LAYER, _ORCA_LAYER_VALUE,
    _PRUSA_LAYER_CHANGE, _STATS_MARKER, _MARKER_SNIFF_BYTES,
)


def read_header(path):
    markers = (
        (_LAYER_COMMENT, _CURA_LAYER_VALUE),
        (_ORCA_LAYER, _ORCA_LAYER_VALUE),
        (_PRUSA_LAYER_CHANGE, None),
        (_STATS_MARKER, _STATS_MARKER),
    )
    # Sniff the layer-change format from the file head so the matching
    # marker is checked first per line; the rest stay in the fallback
    # order.
    try:
        with open(path, "rb") as probe:
            # The scan matches per line (the markers are
            # line-anchored), so the sniff must too: a raw blob search
            # would miss the layer markers and hand the first check to
            # the stats marker.
            head_lines = probe.read(_MARKER_SNIFF_BYTES).splitlines()
        sniffed = next((marker for marker, _capture in markers
                        if any(marker.match(line) for line in head_lines)), None)
    except OSError:
        sniffed = None
    filament_diameter = 1.75
    filament_diameters = {}
    for line in head_lines if "head_lines" in locals() else ():
        tool_diameter = re.match(rb";EXTRUDER_TRAIN\.(\d+)\.MATERIAL\.DIAMETER:\s*([0-9.]+)", line, re.IGNORECASE)
        if tool_diameter:
            tool, value = int(tool_diameter.group(1)), float(tool_diameter.group(2))
            if 0 <= tool < 16 and .1 <= value <= 10:
                filament_diameters[tool] = value
        if b"filament_diameter" in line.lower() or line.upper().startswith(b";MATERIAL.DIAMETER:"):
            match = re.search(rb"[:=]\s*([0-9.]+)", line)
            if match:
                try:
                    value = float(match.group(1))
                    if 0.1 <= value <= 10:
                        filament_diameter = value
                except ValueError:
                    pass
    captures = dict(markers)
    # Which marker opens layer blocks — decided ONCE per file (the
    # critic's catch): the sniffed marker, else the earliest-ordered
    # marker that matches ANY line. A fast census pass reads only the
    # marker regexes (no block state), so the scan can react to the
    # single winner — the old per-line take-over was not retroactive
    # and mis-attributed an earlier-ordered marker's lines to a
    # later one's blocks when the file primed past the sniff window.
    winner = sniffed
    if winner is None:
        try:
            with open(path, "rb") as probe:
                for line in probe:
                    for marker, _capture in markers:
                        if marker.match(line.rstrip(b"\r\n")):
                            winner = marker
                            break
                    if winner is not None:
                        break
        except OSError:
            winner = None

    return winner, captures, filament_diameter, filament_diameters
