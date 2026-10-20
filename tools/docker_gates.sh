#!/bin/sh
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-10-01 [1d8bd42] Capitalise the remaining top-level directories
# 2026-09-08 [7341f82] Development tooling: pinned container, quality gates and the Makefile

# Run every gate inside the pinned dev container.
#
# The invocation details (build-if-needed, host-user mapping, bind
# mount) live in tools/docker_dev.sh; this wrapper adds the editor-
# backup cleanup around the run.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
tools/docker_dev.sh "$@"
status=$?
# Formatters and capture runs can leave editor-style backups behind.
find "$root" -name "*~" -not -path "*/.git/*" -delete 2>/dev/null || true
exit $status
