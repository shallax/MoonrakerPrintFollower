# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-29 [90e8a59] Organise source by feature ownership and enforce domain boundaries

"""Locate packaged files independently of the caller's directory.

Cura installs this package under its plugin ID, not necessarily as ``mpf``.
The root comes from this package's own location; callers supply literal paths
relative to it. Resource contract tests validate every declared file against
both the source tree and the built package.
"""
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def plugin_path(*parts: str) -> str:
    """Return a local filename beneath the installed plugin root."""
    return str(PLUGIN_ROOT.joinpath(*parts))
