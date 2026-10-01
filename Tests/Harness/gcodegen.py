"""The harness's deterministic generated gcode (pure — no tornado),
shared by the simulator and the unit tests.
"""
from bisect import bisect_left
from math import dist
import re


def penguin_playback():
    """A test-only linear-motion timeline from the README's penguin toolpaths.

    Kept independent of the plugin's indexer so following is tested against
    telemetry from the file, not answers supplied by the tracker under test.
    This parser only handles the generator's absolute G0/G1/G92 vocabulary.
    """
    try:
        from Tools.capture_penguin import make_gcode as penguin
    except ModuleNotFoundError:
        from capture_penguin import make_gcode as penguin
    # The native seeds and offscreen capture printer share a 250 mm bed.
    data = penguin().encode("ascii")
    rows = []
    position = [0.0, 0.0, 0.0, 0.0]
    layer, tool, offset, clock, feed = -1, 0, 0, 0.0, 6000.0
    for raw in data.splitlines(keepends=True):
        line = raw.decode("ascii").strip()
        offset += len(raw)
        if line.startswith(";LAYER:"):
            layer = int(line.partition(":")[2])
        elif re.fullmatch(r"T\d+", line):
            tool = int(line[1:])
        elif line.startswith(("G0 ", "G1 ", "G92 ")):
            values = {key: float(value) for key, value in re.findall(
                r"([XYZEF])(-?\d+(?:\.\d+)?)", line)}
            before = list(position)
            for axis, key in enumerate("XYZE"):
                position[axis] = values.get(key, position[axis])
            feed = values.get("F", feed)
            if line.startswith("G92") or layer < 0:
                continue
            clock += max(0.002, dist(before[:3], position[:3]) / (feed / 60))
            rows.append({"at": clock, "start": before, "end": list(position),
                         "layer": layer, "tool": tool, "offset": offset})
    first = next(i for i, row in enumerate(rows) if row["end"][3] > row["start"][3])
    before_print = rows[first - 1]["at"] if first else 0.0
    rows = [dict(row, at=row["at"] - before_print) for row in rows[first:]]
    return data, rows


def playback_sample(rows, fraction):
    """Interpolate along a commanded segment, never across a corner."""
    target = min(1.0, max(0.0, fraction)) * rows[-1]["at"]
    at = min(len(rows) - 1, bisect_left(rows, target, key=lambda row: row["at"]))
    row = rows[at]
    previous = rows[at - 1]["at"] if at else 0.0
    duration = row["at"] - previous
    blend = min(1.0, max(0.0, (target - previous) / duration))
    moving = 0 < fraction < 1
    return dict(row, position=[a + (b - a) * blend
                              for a, b in zip(row["start"], row["end"], strict=True)],
                velocity=dist(row["start"][:3], row["end"][:3]) / duration if moving else 0.0,
                extruder_velocity=(row["end"][3] - row["start"][3]) / duration if moving else 0.0)


def make_gcode(layers: int = 40) -> str:
    """A deterministic, Cura-parseable gcode: a square perimeter per
    layer with extrusion moves and M73 progress. Real parse/render
    targets for the load pipeline (A25). ONE baked pause (the PAUSE
    command at layer 20) so the index scenarios exercise the baked
    pause path too (by request) — no scenario asserts the
    pause list's exact contents, so the extra row is free coverage."""
    lines = [";FLAVOR:Marlin", ";LAYER_COUNT:%d" % layers, "M73 P0", "G90", "M82"]
    size = 50.0
    layer_height = 0.2
    z = layer_height
    extruded = 0.0
    for layer in range(layers):
        lines.append(";LAYER:%d" % layer)
        if layer == 20:
            lines.append("PAUSE")
        corners = [(100.0, 100.0), (100.0 + size, 100.0),
                   (100.0 + size, 100.0 + size), (100.0, 100.0 + size)]
        # Travel to the first corner, then the perimeter.
        lines.append("G0 X%.2f Y%.2f Z%.2f F6000" % (corners[0][0], corners[0][1], z))
        for x, y in corners[1:]:
            extruded += 0.12
            lines.append("G1 X%.2f Y%.2f E%.4f F1800" % (x, y, extruded))
        extruded += 0.12
        lines.append("G1 X%.2f Y%.2f E%.4f F1800" % (corners[0][0], corners[0][1], extruded))
        # A diagonal infill line for visible geometry.
        extruded += 0.08
        lines.append("G1 X%.2f Y%.2f E%.4f F2400" % (100.0 + size, 100.0 + size, extruded))
        z += layer_height
        lines.append("M73 P%d" % min(100, int((layer + 1) * 100 / layers)))
        # The per-layer elapsed marker, as real slicers emit it: the
        # index's remaining-time math reads the layer's END from this
        # line (without it every layer's elapsed is None and the
        # next-pause ETA stays "unavailable" — the x10 report).
        lines.append(";TIME_ELAPSED:%d" % ((layer + 1) * 30))
    lines.append("M73 P100")
    lines.append(";TIME_ELAPSED:1234")
    return "\n".join(lines) + "\n"
