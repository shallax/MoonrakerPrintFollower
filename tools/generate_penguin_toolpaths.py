"""Generate the packaged Easter egg strokes from the capture penguin G-code."""
from __future__ import annotations

import json
from pathlib import Path

from capture_penguin import make_gcode

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "mpf" / "plate" / "PenguinToolpaths.js"


def strokes():
    tool, x, y = 0, 0.0, 0.0
    result = []
    for line in make_gcode().splitlines():
        if line.startswith("T") and line[1:].isdigit():
            tool = int(line[1:])
        elif line.startswith(("G0 ", "G1 ")):
            words = {part[0]: float(part[1:]) for part in line.split()[1:]}
            next_x, next_y = words.get("X", x), words.get("Y", y)
            if line.startswith("G1 ") and "E" in words and (next_x, next_y) != (x, y):
                result.append([x, y, next_x, next_y, tool])
            x, y = next_x, next_y
    return result


def main():
    lines = [".pragma library", "// Generated from tools/capture_penguin.py by tools/generate_penguin_toolpaths.py.",
             "// G-code extrusion order; tools 0, 1 and 2 are black, white and yellow.",
             "var strokes = ["]
    path = strokes()
    for start in range(0, len(path), 20):
        lines.append("    " + ",".join(json.dumps(row, separators=(",", ":")) for row in path[start:start + 20])
                     + ("," if start + 20 < len(path) else ""))
    lines.append("];")
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {len(path)} penguin G-code strokes in {OUTPUT}")


if __name__ == "__main__":
    main()
