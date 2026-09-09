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
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
tmp=".determinism-check"
rm -rf "$tmp"
mkdir -p "$tmp/run1" "$tmp/run2"
# Pull fresh, and fail loudly: a silently stale image would render
# drifted bytes that the pinned CI image never reproduces.
docker build --pull -q -t moonraker-print-follower-dev . >/dev/null 2>&1 || {
    echo "determinism check: the capture image failed to build — aborting rather than comparing a stale image" >&2
    exit 1
}
for run in run1 run2; do
    tools/docker_dev.sh sh -c "python3 tools/capture_monitor.py '$tmp/$run' \
        && python3 tools/capture_preview.py '$tmp/$run' \
        && python3 tools/capture_settings.py '$tmp/$run' \
        && python3 tools/capture_upload.py '$tmp/$run'"
done
stale=0
for generated in "$tmp/run1"/*.png; do
    name="$(basename "$generated")"
    if ! cmp -s "$generated" "$tmp/run2/$name"; then
        echo "NON-DETERMINISTIC: $name differs between two runs in the same container" >&2
        stale=1
    fi
done
if [ "$stale" -eq 0 ]; then
    set -- "$tmp/run1"/*.png
    count=$#
    rm -rf "$tmp"
    echo "captures are deterministic: $count scenes byte-identical across two runs"
fi
exit "$stale"
