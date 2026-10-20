#!/usr/bin/env python3
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-28 [af21c55] Add native macOS development toolchain and optional Docker backend
# 2026-09-27 [0501e01] Isolate Qt coverage and release tests and provision shader runtime
# 2026-09-26 [2bdf753] Add a native Windows development leg

"""Compatibility entry point for the shared native Windows/macOS driver."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native.dev import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
