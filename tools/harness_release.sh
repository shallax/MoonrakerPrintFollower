#!/bin/sh
# The release gate's real-Cura scenario runs: the smoke set on the
# primary pinned Cura, the full suite groups on the primary, and the
# smoke set again on the secondary version — the version-swap proof
# (TESTING.md §5).
#
# One attempt per unit (a retried unit suggests
# flakiness, and a flaky unit must read as a failure).
#
# Every unit's gallery and log land under one timestamped, immutable
# root (never overwritten, never deleted):
#   /tmp/mpf/ui-artifacts/runs/<yyyy-MM-dd-HHmmss>/<curaVersion>/<unit>[/.log]
# Re-runs accumulate side by side; the CI upload publishes the whole
# ui-artifacts tree.
#
# Usage:  harness_release.sh [-j N]
#
# The default run is SERIAL: one container, one shared /tmp/mpf, so
# the smoke units' shared boot proves the second unit survives the
# first unit's debris — a property parallel mode does not claim.
# With -j N each unit gets its OWN container AND its own working
# directory (the isolation ruling: /tmp/mpf/slot-<n> mounted as that
# slot's /tmp/mpf), so the port/token files, the XDG seed and the
# sim state can never collide; the evidence still lands in the shared
# run root. CI never uses -j — its matrix already parallelizes by
# machine, and the timing budgets assume an unloaded host.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

JOBS=1
if [ "${1:-}" = "-j" ]; then
    JOBS="${2:?usage: harness_release.sh [-j N]}"
    shift 2
fi

HARNESS_DIR="${HARNESS_DIR:-/tmp/mpf}"
# The gate owns its container(s) (built and removed here) under its
# own name(s) — never the dev harness container, which an ad-hoc run
# may be using (the gate used to remove it out from under them).
CONTAINER="${HARNESS_CONTAINER:-mpf-cura-gate}"
PRIMARY="${CURA_PRIMARY:-5.13.0}"
SECONDARY="${CURA_SECONDARY:-5.12.0}"
RUN_ROOT="$HARNESS_DIR/ui-artifacts/runs/$(date +%Y-%m-%d-%H%M%S)"

mkdir -p "$HARNESS_DIR" "$RUN_ROOT"

# The harness stages the plugin from dist/, which a fresh checkout (or
# a future CI job calling this directly) may not have — the release
# workflow's artifact steps build it, but this script must not depend
# on which job it runs inside.
make package >/dev/null

docker build -q -t mpf-cura-harness "$root/tools/harness" >/dev/null

# The Cura fetches happen ONCE into the shared tree; parallel slots
# copy their version's tree into their own work dir (the extraction
# must be writable — the plugin stages into it — and a hardlink
# would mutate the shared tree).
export CURA_VERSIONS_DIR="$HARNESS_DIR/cura_versions"
python3 "$root/tools/fetch_cura.py" "$PRIMARY"
python3 "$root/tools/fetch_cura.py" "$SECONDARY"

fail=0
hang=0

start_container() {  # start_container <name> <slot_dir> [clear]
    docker rm -f "$1" >/dev/null 2>&1 || true
    if [ "${3:-}" = "clear" ]; then
        # A slot from a previous run holds root-owned debris the host
        # cannot remove (Cura's owner-only writes) — the container
        # clears it, then the dir is created USER-OWNED before the
        # mount (docker would create a missing mount source as root,
        # and every later host-side write into the slot then dies
        # with EACCES). The clear is SLOT-ONLY: the serial run passes
        # the shared tree as the mount, and clearing it would delete
        # the fetched Cura trees and every prior run's evidence.
        docker run --rm -v "$(dirname "$2"):/host" mpf-cura-harness \
            rm -rf "/host/$(basename "$2")" >/dev/null 2>&1 || true
    fi
    mkdir -p "$2"
    # SYS_PTRACE lets the stall diagnostics attach gdb/strace to the
    # hung boot from inside the container (the host's ptrace_scope
    # otherwise blocks a non-parent tracer). The slot dir mounts as
    # the container's /tmp/mpf — ONE mount: the shared tree is never
    # mounted into a slot container (a second mount at the same
    # destination is what Docker rejects, and the evidence joins the
    # shared run root host-side after each unit).
    docker run -d --init --name "$1" --cap-add=SYS_PTRACE \
        -v "$2:/tmp/mpf" mpf-cura-harness sleep infinity >/dev/null
}

prepare_slot() {  # prepare_slot <slot_dir> <cura-version>
    mkdir -p "$1/cura_versions"
    if [ ! -d "$1/cura_versions/$2/root" ]; then
        cp -a "$HARNESS_DIR/cura_versions/$2" "$1/cura_versions/"
    fi
}

run_unit() {  # run_unit <budget> <version> <name> <mode> [group] [slot]
    budget=$1; version=$2; name=$3; mode=$4; group=${5:-}; slot=${6:-}
    echo "== $name on $version (budget ${budget}m, one attempt) =="
    wall_start=$(date +%s)
    unit_dir="$RUN_ROOT/$version/$name"
    mkdir -p "$RUN_ROOT/$version"
    if [ -n "$slot" ]; then
        container="mpf-cura-gate-$slot"
        work="$HARNESS_DIR/slot-$slot"
        prepare_slot "$work" "$version"
        # The slot's evidence dir lives in the SLOT's tree (its
        # /tmp/mpf); it joins the shared run root after the unit.
        slot_unit="$work/ui-artifacts/$name"
        mkdir -p "$slot_unit"
        status=0
        if [ -n "$group" ]; then
            timeout "${budget}m" env HARNESS_CONTAINER="$container" MPF_WORK_DIR="$work" \
                CURA_VERSION="$version" MODE="$mode" SCENARIO_GROUP="$group" \
                RUN_DIR_NAME="$slot_unit" ./tools/ui_test.sh > "$unit_dir.log" 2>&1 \
                || status=$?
        else
            timeout "${budget}m" env HARNESS_CONTAINER="$container" MPF_WORK_DIR="$work" \
                CURA_VERSION="$version" MODE="$mode" \
                RUN_DIR_NAME="$slot_unit" ./tools/ui_test.sh > "$unit_dir.log" 2>&1 \
                || status=$?
        fi
        if [ -d "$slot_unit" ]; then
            cp -a "$slot_unit" "$unit_dir"
        fi
    else
        status=0
        if [ -n "$group" ]; then
            timeout "${budget}m" env HARNESS_CONTAINER="$CONTAINER" \
                CURA_VERSION="$version" MODE="$mode" SCENARIO_GROUP="$group" \
                RUN_DIR_NAME="$unit_dir" ./tools/ui_test.sh > "$unit_dir.log" 2>&1 \
                || status=$?
        else
            timeout "${budget}m" env HARNESS_CONTAINER="$CONTAINER" \
                CURA_VERSION="$version" MODE="$mode" \
                RUN_DIR_NAME="$unit_dir" ./tools/ui_test.sh > "$unit_dir.log" 2>&1 \
                || status=$?
        fi
    fi
    if [ "$status" = 124 ]; then
        echo "HANG: $name on $version (budget overrun — the unit never finished)"
    elif [ "$status" != 0 ]; then
        echo "FAILED: $name on $version"
    fi
    # The per-unit wall clock: the measured performance record (the
    # acceptance's per-group timings), in the unit log beside the
    # verdict.
    echo "wall: $(( $(date +%s) - wall_start ))s $name on $version (status $status)"
    # The unit's status is the FUNCTION's status — the callers derive
    # the gate verdict from it (a backgrounded call runs in a
    # subshell, so flag mutation would die with the job).
    return "$status"
}

# The smoke set (the sanity layer — the release-killing surfaces in
# one boot) runs on both versions; the full suite groups run on the
# primary as the deep regression.
UNITS="20 $PRIMARY smoke suite smoke"
for g in connection status temperatures console webcams files motion printing settings visual preview probe configure stress; do
    UNITS="$UNITS
15 $PRIMARY group-$g suite $g"
done
# The first-install leg (TESTING.md §3): one clean profile booted
# twice — the journey the pre-migrated fixture cannot cover, and the
# reason a config-losing first install once shipped green.
UNITS="$UNITS
10 $PRIMARY firstinstall firstinstall"
UNITS="$UNITS
20 $SECONDARY smoke suite smoke"

if [ "$JOBS" -le 1 ]; then
    start_container "$CONTAINER" "$HARNESS_DIR"
    while read -r budget version name mode group; do
        [ -n "$budget" ] || continue
        st=0
        run_unit "$budget" "$version" "$name" "$mode" "$group" || st=$?
        if [ "$st" = 124 ]; then hang=1
        elif [ "$st" != 0 ]; then fail=1; fi
    done <<UNITS_LIST
$UNITS
UNITS_LIST
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
else
    # Parallel mode: one slot per job, units drained in batches of
    # JOBS. Every slot's container and work dir are its own (the
    # isolation ruling); the shared run root collects the evidence.
    slot_num=1
    while [ "$slot_num" -le "$JOBS" ]; do
        start_container "mpf-cura-gate-$slot_num" "$HARNESS_DIR/slot-$slot_num" clear
        slot_num=$((slot_num + 1))
    done
    count=0
    pids=""
    drain() {  # drain: wait for the batch and fold the statuses in
        for entry in $pids; do
            slot=${entry%%:*}
            pid=${entry##*:}
            st=0
            wait "$pid" || st=$?
            if [ "$st" = 124 ]; then hang=1
            elif [ "$st" != 0 ]; then fail=1; fi
            # A unit that failed or hung may have left debris (a half-
            # seeded XDG tree, a wedged Cura) in its slot — the next
            # batch's unit gets a fresh container and a cleared work
            # dir rather than inheriting it (the isolation claim).
            if [ "$st" != 0 ]; then
                start_container "mpf-cura-gate-$slot" "$HARNESS_DIR/slot-$slot" clear
            fi
        done
        pids=""
    }
    while read -r budget version name mode group; do
        [ -n "$budget" ] || continue
        slot=$((count % JOBS + 1))
        count=$((count + 1))
        run_unit "$budget" "$version" "$name" "$mode" "$group" "$slot" &
        pids="$pids $slot:$!"
        if [ $((count % JOBS)) -eq 0 ]; then
            drain
        fi
    done <<UNITS_LIST
$UNITS
UNITS_LIST
    drain
    slot_num=1
    while [ "$slot_num" -le "$JOBS" ]; do
        docker rm -f "mpf-cura-gate-$slot_num" >/dev/null 2>&1 || true
        slot_num=$((slot_num + 1))
    done
fi

# The coverage EXECUTION check (workstream 4): the map's membership
# is not enough — every mapped surface's scenario must have run in
# this matrix's evidence, and every objectName'd item mapped to a
# scenario must be addressed by one of its steps. A matrix whose
# evidence never names a mapped control is an overclaim, not
# coverage.
coverage_failures=$(python3 - "$RUN_ROOT" <<'PYEOF'
import glob
import json
import os
import sys

root = sys.argv[1]
sys.path.insert(0, os.path.join(os.getcwd(), "tests", "harness"))
from surface_coverage import check_evidence  # noqa: E402
from scenario_map import PREFIX_RULES, SCENARIO_MAP  # noqa: E402

steps = []
for path in glob.glob(os.path.join(root, "*", "*", "evidence.json")):
    with open(path, encoding="utf-8") as handle:
        steps.extend(json.load(handle).get("steps", ()))
for failure in check_evidence(SCENARIO_MAP, PREFIX_RULES, steps):
    print("ui_test: COVERAGE", failure)
PYEOF
)
if [ -n "$coverage_failures" ]; then
    echo "$coverage_failures"
fi
if [ "$fail" != 0 ] || [ "$hang" != 0 ] || [ -n "$coverage_failures" ]; then
    echo "ui release gate FAILED (run root: $RUN_ROOT)"
    exit 1
fi
echo "ui release gate PASSED (run root: $RUN_ROOT)"
