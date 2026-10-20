# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-15 [ad303eb] the hourglass clear: skip only the improve's own publish, settle the clear before the
#   values build, and end the hourglass outright on a printer switch — the binding-reset and download-failure
#   semantics preserved with their tests

"""Print-identity helpers shared by the state and the coordinator."""
from __future__ import annotations
def index_view_for_print(view, filename):
    """The index view counts as the CURRENT print's evidence only when
    its job key names that print — a view left over from loading a
    DIFFERENT file must not read as index_ready (the red run: the ETA
    improve silently no-opped for a fresh print after any preview
    load, because the stale view and the stale files-service job
    agreed with each other)."""
    if view is not None and not (view.job_key and view.job_key[0] == filename):
        return None
    return view

