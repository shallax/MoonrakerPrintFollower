#!/usr/bin/env bash
set -euo pipefail

REPO="shallax/MoonrakerPrintFollower"
OUT="/tmp/moonraker-print-follower-releases"
rm -rf "$OUT"
mkdir -p "$OUT"

git fetch --all --tags --prune

build_historical_package() {
  local version="$1"
  local sha="$2"
  local work="$OUT/src-$version"
  local asset="$OUT/MoonrakerPrintFollower-v${version}.curapackage"

  rm -rf "$work"
  mkdir -p "$work"
  git archive "$sha" | tar -x -C "$work"

  if [[ "$version" == "2.0.0" && -f "$work/MoonrakerPrintFollower-v2.0.0.curapackage" ]]; then
    cp "$work/MoonrakerPrintFollower-v2.0.0.curapackage" "$asset"
    echo "$asset"
    return
  fi

  python - "$work" "$version" "$asset" <<'PY'
import json
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
version = sys.argv[2]
asset = pathlib.Path(sys.argv[3])

package_path = root / "package.json"
package = json.loads(package_path.read_text(encoding="utf-8"))
# The requested release hashes are authoritative.  v1.0.0's historical tree
# carried later version strings in metadata, so normalise only release-version
# metadata while preserving the exact source content from the requested hash.
package["package_version"] = version
package_bytes = (json.dumps(package, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
package_id = str(package["package_id"])

entries = []
legacy_files = root / "files"
plugins = root / "plugins"

if legacy_files.is_dir():
    for path in sorted(legacy_files.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if path.name == "plugin.json":
            try:
                meta = json.loads(data.decode("utf-8"))
                meta["version"] = version
                data = (json.dumps(meta, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
            except Exception:
                pass
        entries.append((rel, data))
elif plugins.is_dir():
    for path in sorted(plugins.rglob("*")):
        if not path.is_file():
            continue
        rel_src = path.relative_to(plugins)
        if "__pycache__" in rel_src.parts or any(part.startswith(".") for part in rel_src.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo", ".orig", ".rej", ".swp", ".swo", ".tmp", ".bak"}:
            continue
        data = path.read_bytes()
        if path.name == "plugin.json":
            try:
                meta = json.loads(data.decode("utf-8"))
                meta["version"] = version
                data = (json.dumps(meta, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
            except Exception:
                pass
        arc = f"files/plugins/{package_id}/{rel_src.as_posix()}"
        entries.append((arc, data))
else:
    raise SystemExit(f"No plugin payload found in {root}")

asset.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(asset, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
    zf.writestr("package.json", package_bytes)
    for arc, data in entries:
        zf.writestr(arc, data)

# Sanity-check the two version-bearing metadata files.
with zipfile.ZipFile(asset, "r") as zf:
    pkg = json.loads(zf.read("package.json"))
    assert pkg["package_version"] == version
    plugin_entries = [n for n in zf.namelist() if n.endswith("/plugin.json")]
    assert len(plugin_entries) == 1, plugin_entries
    plugin = json.loads(zf.read(plugin_entries[0]))
    assert plugin["version"] == version
print(asset)
PY
}

write_notes() {
  local version="$1"
  local file="$OUT/notes-$version.md"
  case "$version" in
    1.0.0)
      cat > "$file" <<'EOF'
## First public release

Moonraker Print Follower brings a live Klipper/Moonraker print into Cura Preview.

### Highlights
- Follow the printer's current layer and progress through the active layer in Cura Preview.
- Load the G-code currently printing on Moonraker into Cura on demand.
- Pause and resume Preview following without pausing the printer itself.
- Configure Moonraker connection details, polling, layer handling and Preview behaviour.

This is the original 1.0 release of the plugin.
EOF
      ;;
    1.0.1)
      cat > "$file" <<'EOF'
## Better manual Preview control

This release makes it much easier to inspect a print without fighting the follower.

### Highlights
- Moving Cura's layer or toolpath slider manually now suspends automatic following.
- Resuming following catches Preview back up to the live print.
- Plugin-driven Preview movement is distinguished from user interaction, avoiding false pauses.

A small release, but a substantial usability improvement for live inspection.
EOF
      ;;
    1.0.2)
      cat > "$file" <<'EOF'
## Reliability and lifecycle hardening

Version 1.0.2 focuses on making following behave predictably while Cura is loading, slicing or changing scenes.

### Highlights
- Safer handling of Cura scene changes and slicing, reducing stale or out-of-order Preview updates.
- More reliable manual-override detection when Cura rebuilds its Preview components.
- Improved cleanup and cancellation of downloads, network requests and background indexing.
- Better protection against reusing stale data when the same G-code filename is printed again.
- Lower memory overhead while indexing large G-code files.

The user-facing workflow stays familiar, but the follower is considerably more robust underneath.
EOF
      ;;
    1.0.3)
      cat > "$file" <<'EOF'
## Faster large-print handling and more accurate following

Version 1.0.3 is a performance and accuracy release aimed particularly at larger G-code files and long-running prints.

### Highlights
- Streams G-code downloads and indexing instead of holding the complete file in memory.
- Adds persistent, validated path indexes so repeated loads can be much faster.
- Uses Moonraker motion data when available to better match Cura's nozzle position to the physical printer.
- Improves layer mapping using information embedded in the G-code itself.
- Strengthens manual Preview override detection and stale-work protection.

This is the hardened final release of the 1.0.x line.
EOF
      ;;
    1.1.0)
      cat > "$file" <<'EOF'
## Per-printer configuration and follow modes

Version 1.1.0 moves Moonraker Print Follower from a single global setup to a proper per-printer Cura workflow.

### Highlights
- Separate Moonraker connection and following settings for each Cura printer.
- Automatic migration of existing 1.0.x settings.
- New follow modes: exact current layer, last completed layer, one-layer look-ahead and a layer window around the live layer.
- More resilient Moonraker polling with automatic retry backoff.
- Built-in connection testing and capability detection.
- Better handling of very large G-code files through compact indexing and on-demand detail loading.
- Refined Cura-styled Preview controls and clearer live status.

Only the currently selected Cura printer owns the active follower session.
EOF
      ;;
    2.0.0)
      cat > "$file" <<'EOF'
## Native Cura printer integration

Version 2.0.0 makes Moonraker Print Follower feel like part of Cura rather than a separate utility.

### Highlights
- Configuration moves into **Settings → Printer → Manage Printers → Configure Moonraker Follower**.
- Full per-printer settings and single-active-printer behaviour.
- Targets the complete Cura 5.x / SDK 8.x family from Cura 5.0 through 5.13.
- Improved live nozzle handling in Preview using Cura's native nozzle model.
- Smoother monotonic within-layer following, avoiding visible rewind/retrace behaviour around ambiguous motion and layer changes.
- Retains multiple follow modes, resilient Moonraker polling and scalable large-G-code indexing.
- Existing 1.x settings are migrated automatically.

This release also establishes the canonical Marketplace package identity used by later releases.
EOF
      ;;
    3.0.0)
      cat > "$file" <<'EOF'
## Unified Moonraker control and live Preview

Version 3.0.0 turns Moonraker Print Follower into a much more complete Cura-side companion for Klipper/Moonraker while preserving the core live Preview follower.

### Highlights
- Integrates Moonraker connection/output functionality into one plugin, including G-code upload and printer-aware file handling.
- Adds a live printer dashboard with temperatures, print state, macros, power controls, Z offset, speed/flow tuning, fans, LEDs, PWM outputs and a permanently accessible emergency stop.
- Adds rich bed-mesh support: 3D Preview overlay, mesh statistics, calibration, loading saved profiles and clearing the active mesh.
- Adds end-of-layer PAUSE scheduling directly from Cura Preview, including multiple scheduled pauses, individual removal, clear-all and live ETA for every scheduled pause.
- Restores and improves selected-layer ETA while inspecting future layers.
- Renames Preview following controls to **Detach / Attach** so they cannot be confused with pausing the printer.
- Adds presentation/following refinements and smoothing while keeping user interaction responsive.
- Improves multi-printer behaviour, large-print performance, polling efficiency and stale-response protection.
- Adds extensive release hardening and automated compatibility/regression coverage.

3.0.0 is the new major release and supersedes the separate Moonraker connection workflow for this plugin.
EOF
      ;;
    *) echo "Unknown version $version" >&2; exit 1 ;;
  esac
  echo "$file"
}

publish_release() {
  local version="$1"
  local sha="$2"
  local asset="$3"
  local notes
  notes=$(write_notes "$version")
  local tag="v$version"

  if gh release view "$tag" --repo "$REPO" >/dev/null 2>&1; then
    echo "Release $tag already exists; refusing to overwrite it" >&2
    exit 1
  fi

  gh release create "$tag" "$asset" \
    --repo "$REPO" \
    --target "$sha" \
    --title "Moonraker Print Follower $tag" \
    --notes-file "$notes"
}

# Build and publish the historical releases at the exact hashes supplied by the maintainer.
declare -A HASHES=(
  [1.0.0]="ae39118e0bcdfb170bc2ba81c6fdc25db5879c8c"
  [1.0.1]="fc4180cf88ee2a5b37c12647dcc5ba8c80454888"
  [1.0.2]="72f53231bda022693b75f585780af5eefd6a50b9"
  [1.0.3]="265b4046e8cc7a61b6eaf3553057e62e64258c17"
  [1.1.0]="5092cdc0cae21fd4b40764a66e8adb779eb9a169"
  [2.0.0]="9060236d83701ca8fab8fea7afbf1f49fcd91266"
)

for version in 1.0.0 1.0.1 1.0.2 1.0.3 1.1.0 2.0.0; do
  asset=$(build_historical_package "$version" "${HASHES[$version]}")
  publish_release "$version" "${HASHES[$version]}" "$asset"
done

# Build v3 from the release candidate before removing this one-shot publication machinery.
python tools/build_curapackage.py --output "$OUT/MoonrakerPrintFollower-v3.0.0.curapackage"
python tools/verify_curapackage.py "$OUT/MoonrakerPrintFollower-v3.0.0.curapackage"

# Remove one-shot release machinery, leaving the production source tree clean.
rm -f tools/publish_github_releases.sh
rm -f tools/.publish_github_releases
rm -f .github/workflows/publish-final-releases.yml

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -A
if ! git diff --cached --quiet; then
  git commit -m "Finalize v3.0.0 release tree"
fi
FINAL_SHA=$(git rev-parse HEAD)

# Publish the clean release tree to both the development branch and main.
git push origin HEAD:v3-unified-moonraker
git push origin HEAD:main

# v3.0.0 is tagged at exactly the commit now on main.
publish_release "3.0.0" "$FINAL_SHA" "$OUT/MoonrakerPrintFollower-v3.0.0.curapackage"

# Remove temporary development branches now that main contains the final v3 tree.
mapfile -t TMP_BRANCHES < <(git for-each-ref --format='%(refname:strip=3)' 'refs/remotes/origin/tmp-*' | sort -u)
for branch in "${TMP_BRANCHES[@]}"; do
  [[ -n "$branch" ]] || continue
  echo "Deleting temporary branch $branch"
  git push origin --delete "$branch" || true
done

# The v3 integration branch has served its purpose; main is now authoritative.
git push origin --delete v3-unified-moonraker

echo "FINAL_SHA=$FINAL_SHA"
echo "All releases published and temporary branches removed."
