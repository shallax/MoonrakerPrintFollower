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
# The four legs (two light, two dark) are independent and write their
# own directories: they run side by side (the 2026-09-18 parallelism
# ruling), each with its own log, and any leg's failure fails the gate.
capture_leg() {
    leg="$1"
    theme="$2"
    mkdir -p "$tmp/$leg"
    tools/docker_dev.sh sh -c "CAPTURE_THEME=$theme python3 tools/capture_monitor.py '$tmp/$leg' \
        && CAPTURE_THEME=$theme python3 tools/capture_preview.py '$tmp/$leg' \
        && CAPTURE_THEME=$theme python3 tools/capture_settings.py '$tmp/$leg' \
        && CAPTURE_THEME=$theme python3 tools/capture_upload.py '$tmp/$leg' \
        && CAPTURE_THEME=$theme python3 tools/capture_whatsnew.py '$tmp/$leg'"
}
capture_leg run1 "" >"$tmp/run1.log" 2>&1 & leg_p1=$!
capture_leg run2 "" >"$tmp/run2.log" 2>&1 & leg_p2=$!
# The dark-theme leg (the 4.5.0 ruling): the same scenes under the
# dark asset set — a wrong-coloured glyph (hardcoded black on dark
# grey, the live 4.4.0 find) must fail the e-stop contrast gate in
# capture_monitor instead of a live session.
capture_leg run1-dark cura-dark >"$tmp/run1-dark.log" 2>&1 & leg_p3=$!
capture_leg run2-dark cura-dark >"$tmp/run2-dark.log" 2>&1 & leg_p4=$!
leg_failed=0
for pid in $leg_p1 $leg_p2 $leg_p3 $leg_p4; do
    wait "$pid" || leg_failed=1
done
if [ "$leg_failed" -ne 0 ]; then
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
