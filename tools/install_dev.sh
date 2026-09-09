#!/bin/sh
# Point Cura at this checkout directly: symlink the plugin directory
# into Cura's user plugin folder, so edits here show up on the next
# Cura restart — no package download, unzip, drag or reinstall.
#
# Undo:  rm ~/.local/share/cura/<version>/plugins/Moonraker_Print_Follower
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
cura_dir="$(find "$HOME/.local/share/cura" -maxdepth 1 -mindepth 1 -type d -name '[0-9]*' 2>/dev/null | sort -V | tail -1 || true)"
if [ -z "$cura_dir" ]; then
    echo "No Cura version directory under ~/.local/share/cura — start Cura once first." >&2
    exit 1
fi
target="$cura_dir/plugins/Moonraker_Print_Follower"
mkdir -p "$(dirname "$target")"
rm -rf "$target"
ln -s "$root/plugins" "$target"
echo "Linked $target -> $root/plugins"
echo "Restart Cura to load the checkout; rebuild the QML cache is not needed for plugin QML."
