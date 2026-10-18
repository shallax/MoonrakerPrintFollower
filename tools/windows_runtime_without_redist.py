# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-12 [5a32b35] Prepare 5.4.0 runtime mirror and follower controls
# 2026-10-12 [abf18f3] Mirror optional runtime assets on plugin releases

"""Release staging entry point for the packaged Windows DLL-free transformation."""

from mpf.toolhead.WindowsRuntimeWithoutRedist import (
    strip_cad_wheel,
    strip_python_helper,
)

__all__ = ("strip_cad_wheel", "strip_python_helper")
