#!/bin/sh
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
python3 tools/capture_settings.py "$output"
python3 tools/capture_upload.py "$output"
python3 tools/capture_whatsnew.py "$output"
python3 tools/capture_filemanager.py "$output"
