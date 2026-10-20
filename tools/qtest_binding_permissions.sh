#!/bin/sh
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Make root-extracted QtTest bindings readable by Cura.

# PyQt6 wheels can store this shared library as 0600. The macOS harness
# extracts it with sudo, but Cura runs as the ordinary runner account.
# Shared libraries need read access, not an executable or writable file.
set -eu
[ "$#" -eq 1 ] && [ -f "$1" ] && [ ! -L "$1" ] || {
    echo 'expected one regular QtTest binding file' >&2
    exit 1
}
chmod 0644 "$1"
test -r "$1"
