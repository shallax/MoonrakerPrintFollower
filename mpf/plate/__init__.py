# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-18 — 6.0.0: Export the supported Qt-free geometry preparation API.
# 2026-09-08 [0f363ac] Deterministic captures: vendored Cura assets, capture harness, committed screenshots

"""Qt-free toolpath preparation; optional renderers live in :mod:`mpf.plate.qt`."""
from .ToolpathGeometry import prepare, stroke_geometry, marker_counts, marker_geometry, pack_shader

__all__ = ["prepare", "stroke_geometry", "marker_counts", "marker_geometry", "pack_shader"]
