# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — Demonstrate standalone layer rendering from the installed library.

"""Render a layer to PNG using the installed library, without Cura."""
import argparse
import math
from PyQt6.QtGui import QGuiApplication
from mpf.gcode import build_index_from_file, hydrate_layer_from_file, prepare_layer
from mpf.plate.qt import render_layer_raster


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gcode")
    parser.add_argument("output")
    parser.add_argument("--layer", type=int, default=0)
    parser.add_argument("--bed", type=float, default=250)
    parser.add_argument("--size", type=int, default=640)
    args = parser.parse_args()
    if not math.isfinite(args.bed) or args.bed <= 0 or args.size <= 20:
        parser.error("bed must be finite and positive; size must exceed the 20 pixel margin")
    app = QGuiApplication.instance() or QGuiApplication([])
    index = build_index_from_file(args.gcode, compact=True)
    if not hydrate_layer_from_file(index, args.gcode, args.layer):
        parser.error("the requested layer is unavailable")
    payload = prepare_layer(index, args.layer)
    scale = (args.size - 20) / args.bed
    plot = dict(sx=scale, sy=scale, offsetX=10, offsetY=10, bedXMin=0, bedYMax=args.bed)
    coloured, _, _ = render_layer_raster(payload, plot, dict(width=args.size, height=args.size, dpr=1))
    if not coloured.save(args.output):
        parser.error("the output image could not be saved")
    app.processEvents()


if __name__ == "__main__":
    main()
