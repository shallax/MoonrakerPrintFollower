# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-29 [90e8a59] Organise source by feature ownership and enforce domain boundaries

"""Validation of print-wide numerical motion ranges stored in the index."""

import math

RANGE_KEYS = frozenset({"speed", "height", "width", "flow"})


def valid_ranges(value):
    return (isinstance(value, dict) and set(value) <= RANGE_KEYS
            and all(isinstance(bounds, (list, tuple)) and len(bounds) == 2
                    and all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1e9 for v in bounds)
                    and bounds[0] <= bounds[1] for bounds in value.values()))
