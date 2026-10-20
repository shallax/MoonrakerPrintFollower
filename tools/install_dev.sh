#!/bin/sh
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-29 [beca85a] The plugin directory becomes mpf/, and its refs follow
# 2026-09-17 [4d4f57a] 4.3.0: the plugin identity becomes MoonrakerPrintFollower
# 2026-09-10 [4990f07] Monitor readouts: layer-anchored ETA, endstops and the first live-pass fixes

# Point Cura at this checkout directly: symlink the plugin directory
# into Cura's user plugin folder, so edits here show up on the next
# Cura restart — no package download, unzip, drag or reinstall.
#
# Undo:  rm ~/.local/share/cura/<version>/plugins/MoonrakerPrintFollower
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
cura_dir="$(find "$HOME/.local/share/cura" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*' 2>/dev/null | sort -V | tail -1 || true)"
if [ -z "$cura_dir" ]; then
    echo "No Cura version directory under ~/.local/share/cura — start Cura once first." >&2
    exit 1
fi
target="$cura_dir/plugins/MoonrakerPrintFollower"
mkdir -p "$(dirname "$target")"
rm -rf "$target"
ln -s "$root/mpf" "$target"
echo "Linked $target -> $root/mpf"
echo "Restart Cura to load the checkout; rebuild the QML cache is not needed for plugin QML."
