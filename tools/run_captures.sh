#!/bin/sh
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation
# 2026-10-07 [6b3d8e9] Add Preview toolhead controls for 5.3.0
# 2026-09-26 [81a59eb] Stabilize capture parity and asynchronous validation across hosts

# Canonical screenshot entry point, called inside the pinned amd64 image.
# Qt selects CPU-specific raster routines at startup. Disable optional
# AVX/FMA paths so an emulated x86 image and a native CI CPU use the same
# rounding for SVG icons. This affects captures only, never plugin rendering.
set -eu
QT_NO_CPU_FEATURE="avx2 fma"
PYTHONFAULTHANDLER=1
export QT_NO_CPU_FEATURE PYTHONFAULTHANDLER
output="$1"
python3 tools/capture_monitor.py "$output"
python3 tools/capture_preview.py "$output"
python3 tools/capture_preview_toolhead.py "$output"
python3 tools/capture_recovery.py "$output"
python3 tools/capture_settings.py "$output"
python3 tools/capture_upload.py "$output"
python3 tools/capture_whatsnew.py "$output"
python3 tools/capture_filemanager.py "$output"
python3 tools/capture_toolhead.py "$output"
