#!/bin/sh
# Two full capture runs in the pinned container must be byte-identical.
# The harness freezes every live input (the wall clock included), so any
# difference between two runs means a new unpinned source leaked in —
# a blinking caret, a ticking clock, anything time- or phase-dependent.
# Run this after changing anything the captures render; CI's Screenshot
# sync compares ONE run against the committed copies, which only has a
# chance of catching drift — this check catches it deterministically.
#
# Unlike refresh_screenshots.sh this never touches screenshots/ or
# dist/screenshots: the runs land in .determinism-check/, which is
# removed on success and kept for diagnosis on failure.
#
# The four legs (two light, two dark) run side by side (the 2026-09-18
# parallelism ruling) but share no mutable state: each owns its output
# directory, its materialised theme tree (CAPTURE_THEME_TREE), its QML
# disk cache and its log. The theme overlay used to be the single
# dist/.capture-theme for all four — a leg rewriting it mid-render is
# what made this check fail intermittently. They run inside ONE
# container entry for the same reason run_tests.sh does: four parallel
# docker_dev.sh calls race on the image build.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
# Overridable so parallel determinism runs (the CI-load stress) can
# each keep their own evidence dir instead of racing on one tree.
tmp="${DETERMINISM_DIR:-.determinism-check}"
rm -rf "$tmp"
mkdir -p "$tmp"
# Pull fresh, and fail loudly: a silently stale image would render
# drifted bytes that the pinned CI image never reproduces. Parallel
# stress runs (the CI-load experiments) pre-build once and skip the
# concurrent builds — ten builds over a mutating context raced the
# tar layer and died with "unexpected EOF", pure stress-harness noise.
if [ "${SKIP_BUILD:-0}" != "1" ]; then
    "$(dirname "$0")/build_image.sh" moonraker-print-follower-dev . --pull >/dev/null 2>&1 || {
        echo "determinism check: the capture image failed to build — aborting rather than comparing a stale image" >&2
        exit 1
    }
fi
# The leg script is generated (not inlined in a sh -c string) so the
# quoting stays readable; the bind mount makes it visible in the
# container at the same path. $tmp is expanded here, the leg internals
# are not.
cat >"$tmp/legs.sh" <<LEGS
set -eu
capture_leg() {
    leg="\$1"
    # Explicit theme names: an EMPTY CAPTURE_THEME reaches
    # os.environ.get as a value, not as the default, and the dark legs
    # must not fall back to the light assets.
    CAPTURE_THEME="\$2"
    CAPTURE_THEME_TREE="$tmp/\$leg/.theme"
    QML_DISK_CACHE_PATH="$tmp/\$leg/.qmlcache"
    export CAPTURE_THEME CAPTURE_THEME_TREE QML_DISK_CACHE_PATH
    mkdir -p "$tmp/\$leg"
    python3 tools/capture_monitor.py "$tmp/\$leg"
    python3 tools/capture_preview.py "$tmp/\$leg"
    python3 tools/capture_settings.py "$tmp/\$leg"
    python3 tools/capture_upload.py "$tmp/\$leg"
    python3 tools/capture_whatsnew.py "$tmp/\$leg"
    python3 tools/capture_filemanager.py "$tmp/\$leg"
}
capture_leg run1 cura-light >"$tmp/run1.log" 2>&1 & p1=\$!
capture_leg run2 cura-light >"$tmp/run2.log" 2>&1 & p2=\$!
capture_leg run1-dark cura-dark >"$tmp/run1-dark.log" 2>&1 & p3=\$!
capture_leg run2-dark cura-dark >"$tmp/run2-dark.log" 2>&1 & p4=\$!
failed=0
for pid in "\$p1" "\$p2" "\$p3" "\$p4"; do
    wait "\$pid" || failed=1
done
exit "\$failed"
LEGS
if ! tools/docker_dev.sh sh "$tmp/legs.sh"; then
    echo "capture determinism: a capture leg failed — its log is in $tmp/*.log" >&2
    exit 1
fi
stale=0
count=0
# ANY asymmetry between the runs fails the gate, not just a changed
# byte: a file produced by only one run — run1 missing a scene run2
# rendered, or run2 growing an extra scene run1 never made — is drift
# too, so the scene lists must agree in both directions, and then every
# common file must match byte for byte.
for generated in "$tmp/run1"/*.png; do
    [ -e "$generated" ] || break
    count=$((count + 1))
    name="$(basename "$generated")"
    if [ -e "$tmp/run2/$name" ]; then
        if ! cmp -s "$generated" "$tmp/run2/$name"; then
            echo "NON-DETERMINISTIC: $name differs between two runs in the same container" >&2
            # The mismatch diagnostics: differing-pixel count, the
            # changed bounding box and a visual diff beside the
            # images — the byte comparison alone cannot name the
            # region that raced.
            tools/docker_dev.sh sh -c "cd /work && python3 tools/image_diff.py '$tmp/run1/$name' '$tmp/run2/$name'" >&2 || true
            stale=1
        fi
    else
        echo "NON-DETERMINISTIC: $name was produced by run1 but not by run2" >&2
        stale=1
    fi
done
for generated in "$tmp/run2"/*.png; do
    [ -e "$generated" ] || break
    name="$(basename "$generated")"
    if [ ! -e "$tmp/run1/$name" ]; then
        echo "NON-DETERMINISTIC: $name was produced by run2 but not by run1" >&2
        stale=1
    fi
done
# The dark pair, compared the same way: the scene lists must agree in
# both directions and every common file must match byte for byte.
for generated in "$tmp/run1-dark"/*.png; do
    [ -e "$generated" ] || break
    name="$(basename "$generated")"
    if [ -e "$tmp/run2-dark/$name" ]; then
        if ! cmp -s "$generated" "$tmp/run2-dark/$name"; then
            echo "NON-DETERMINISTIC (dark): $name differs between two runs in the same container" >&2
            tools/docker_dev.sh sh -c "cd /work && python3 tools/image_diff.py '$tmp/run1-dark/$name' '$tmp/run2-dark/$name'" >&2 || true
            stale=1
        fi
    else
        echo "NON-DETERMINISTIC (dark): $name was produced by run1-dark but not by run2-dark" >&2
        stale=1
    fi
done
for generated in "$tmp/run2-dark"/*.png; do
    [ -e "$generated" ] || break
    name="$(basename "$generated")"
    if [ ! -e "$tmp/run1-dark/$name" ]; then
        echo "NON-DETERMINISTIC (dark): $name was produced by run2-dark but not by run1-dark" >&2
        stale=1
    fi
done
if [ "$stale" -eq 0 ]; then
    rm -rf "$tmp"
    echo "captures are deterministic: $count scenes byte-identical across two runs"
fi
exit "$stale"
