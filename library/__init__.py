# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Provide the standalone toolpath namespace without Cura entry points.

"""Standalone G-code inspection and rendering, using the plugin's shared sources."""
from .gcode.GCodeDocument import GCodeDocument, LayerInfo

__all__ = ["GCodeDocument", "LayerInfo"]
