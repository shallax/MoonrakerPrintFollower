# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-20 — 6.0.0: Separate raster painting, Qt assets and worker publication.
# 2026-10-18 — 6.0.0: Convert empty immutable geometry and event markers to Qt-compatible values.
# 2026-10-01 [02e4bd5] Draw switchable bed-axis arrows across plate views
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-09-21 [f130718] 4.6.0: the native render path — dense toolpath geometry rasterises off the QML JavaScript
#   path (QPainterPath per segment on a worker, the measured winner at ~120 ms for 500k motions), the face
#   composes blitted QImages with role-free reuse, the scrub keeps its vector delta, and the whole pipeline is
#   generation-gated and latest-wins coalesced


"""Standalone image rendering; create a QGuiApplication before painting."""
from .PlateRaster import render_layer_raster, render_layer_prefix, render_navigation_layer

__all__ = ["render_layer_raster", "render_layer_prefix", "render_navigation_layer"]
