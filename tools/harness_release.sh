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
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

HARNESS_DIR="${HARNESS_DIR:-/tmp/mpf}"
# The gate owns its container (built and removed here) under its own
# name — never the dev harness container, which an ad-hoc run may be
# using (the gate used to remove it out from under them).
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
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
# SYS_PTRACE lets the stall diagnostics attach gdb/strace to the
# hung boot from inside the container (the host's ptrace_scope
# otherwise blocks a non-parent tracer).
docker run -d --init --name "$CONTAINER" --cap-add=SYS_PTRACE \
    -v "$HARNESS_DIR:$HARNESS_DIR" mpf-cura-harness sleep infinity >/dev/null

python3 "$root/tools/fetch_cura.py" "$PRIMARY"
python3 "$root/tools/fetch_cura.py" "$SECONDARY"

fail=0
unit() {  # unit <minutes-budget> <cura-version> <unit-name> <mode> [group]
    budget=$1; version=$2; name=$3; mode=$4; group=${5:-}
    echo "== $name on $version (budget ${budget}m, one attempt) =="
    unit_dir="$RUN_ROOT/$version/$name"
    mkdir -p "$RUN_ROOT/$version"
    if [ -n "$group" ]; then
        timeout "${budget}m" env CURA_VERSION="$version" \
            HARNESS_CONTAINER="$CONTAINER" MODE="$mode" \
            SCENARIO_GROUP="$group" RUN_DIR_NAME="$unit_dir" ./tools/ui_test.sh \
            > "$unit_dir.log" 2>&1 \
            || { echo "FAILED: $name on $version"; fail=1; }
    else
        timeout "${budget}m" env CURA_VERSION="$version" \
            HARNESS_CONTAINER="$CONTAINER" MODE="$mode" \
            RUN_DIR_NAME="$unit_dir" ./tools/ui_test.sh \
            > "$unit_dir.log" 2>&1 \
            || { echo "FAILED: $name on $version"; fail=1; }
    fi
}

# The smoke set (the sanity layer — the release-killing surfaces in
# one boot) runs on both versions; the full suite groups run on the
# primary as the deep regression.
unit 20 "$PRIMARY" "smoke" suite smoke
for g in connection status temperatures console webcams files motion printing settings visual preview probe; do
    unit 15 "$PRIMARY" "group-$g" suite "$g"
done
# The secondary version: the swap proof — the smoke set again under it.
unit 20 "$SECONDARY" "smoke" suite smoke

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
if [ "$fail" != 0 ]; then
    echo "ui release gate FAILED (run root: $RUN_ROOT)"
    exit 1
fi
echo "ui release gate PASSED (run root: $RUN_ROOT)"
