#!/bin/sh
# The PR-level harness check: the smoke set (one boot, the
# release-killing surfaces) against the primary pinned Cura. The full
# gate (smoke + every suite group + the secondary version) remains the
# release workflow's job — this is the fast per-PR signal.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

HARNESS_DIR="${HARNESS_DIR:-/tmp/mpf}"
CONTAINER="${HARNESS_CONTAINER:-mpf-cura-ci}"
PRIMARY="${CURA_PRIMARY:-5.13.0}"
RUN_ROOT="$HARNESS_DIR/ui-artifacts/runs/$(date +%Y-%m-%d-%H%M%S)"

mkdir -p "$HARNESS_DIR" "$RUN_ROOT"

# The harness stages the plugin from dist/, and a fresh CI checkout has
# no dist/ — the dev loop's make package doesn't exist here. Build it.
make package >/dev/null

docker build -q -t mpf-cura-harness "$root/tools/harness" >/dev/null
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
# SYS_PTRACE lets the stall diagnostics attach gdb/strace to the
# hung boot from inside the container (the host's ptrace_scope
# otherwise blocks a non-parent tracer).
docker run -d --init --name "$CONTAINER" --cap-add=SYS_PTRACE \
    -v "$HARNESS_DIR:$HARNESS_DIR" mpf-cura-harness sleep infinity >/dev/null

python3 "$root/tools/fetch_cura.py" "$PRIMARY"

if timeout 40m env CURA_VERSION="$PRIMARY" \
        HARNESS_CONTAINER="$CONTAINER" MODE=suite SCENARIO_GROUP=smoke \
        RUN_DIR_NAME="$RUN_ROOT/smoke" ./tools/ui_test.sh > "$RUN_ROOT/smoke.log" 2>&1; then
    echo "harness smoke PASSED (run root: $RUN_ROOT)"
else
    echo "harness smoke FAILED (run root: $RUN_ROOT)"
    exit 1
fi
