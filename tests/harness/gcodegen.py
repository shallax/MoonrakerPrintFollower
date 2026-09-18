"""The harness's deterministic generated gcode (pure — no tornado),
shared by the simulator and the unit tests.
"""


def make_gcode(layers: int = 40) -> str:
    """A deterministic, Cura-parseable gcode: a square perimeter per
    layer with extrusion moves and M73 progress. Real parse/render
    targets for the load pipeline (A25). ONE baked pause (the PAUSE
    command at layer 20) so the index scenarios exercise the baked
    pause path too (the author's request) — no scenario asserts the
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
