#!/bin/sh
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-04 [7981ac6] Harden local detection and add per-print mute and monitored regions
# 2026-09-26 [c9839b9] Keep scrub frames stable and prepare current-layer rewind checkpoints

# Run the pinned workflow linter natively inside the dev container.
set -eu
case "$(uname -m)" in
    aarch64|arm64) architecture=arm64 ;;
    x86_64) architecture=amd64 ;;
    *) echo "unsupported actionlint architecture" >&2; exit 1 ;;
esac
mkdir -p /tmp/mpf
scratch="$(mktemp -d /tmp/mpf/actionlint.XXXXXX)"
trap 'rm -rf "$scratch"' EXIT
wget -qO "$scratch/archive.tar.gz" "https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_linux_${architecture}.tar.gz"
tar --no-same-owner -xzf "$scratch/archive.tar.gz" -C "$scratch" actionlint
"$scratch/actionlint" .github/workflows/*.yml
