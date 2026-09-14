#!/bin/sh
# The PR-level harness check: the smoke set against the primary pinned
# Cura, twice in a row — the second unit proves the boot survives the
# first unit's debris (Cura's owner-only writes broke the next unit's
# cleanup on CI). The full gate (smoke + every suite group + the
# secondary version) remains the release workflow's job.
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

# Prefer the published image (the release workflow pushes it; the
# package is private, so CI logs in first). Build locally only when
# the registry copy is unreachable — and say so, loudly, when the
# build itself fails (a swallowed build once left docker run without
# an image and the whole job died on a usage error).
echo "harness smoke: pulling the harness image (building if it is unreachable)"
if docker pull ghcr.io/shallax/mpf-cura-harness:latest >/dev/null 2>&1; then
    # The run uses the short local name; the registry image carries
    # the full ghcr ref and must be re-tagged first (an untagged
    # pull once left docker run pulling the bare name from Hub).
    docker tag ghcr.io/shallax/mpf-cura-harness:latest mpf-cura-harness
else
    echo "harness smoke: the pull failed — building the image locally"
    if ! docker build -q -t mpf-cura-harness "$root/tools/harness" > "$RUN_ROOT/build.log" 2>&1; then
        echo "harness smoke: the image build failed:"
        tail -30 "$RUN_ROOT/build.log"
        exit 1
    fi
fi
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
# SYS_PTRACE lets the stall diagnostics attach gdb/strace to the
# hung boot from inside the container (the host's ptrace_scope
# otherwise blocks a non-parent tracer).
docker run -d --init --name "$CONTAINER" --cap-add=SYS_PTRACE \
    -v "$HARNESS_DIR:$HARNESS_DIR" mpf-cura-harness sleep infinity >/dev/null

python3 "$root/tools/fetch_cura.py" "$PRIMARY"

# Two units, one after the other: the second boot must survive the
# first unit's debris — Cura's owner-only config writes once killed
# the next unit's cleanup on CI (the cross-uid lesson).
fail=0
for run in smoke-1 smoke-2; do
    echo "harness smoke: unit $run starts (boot + the smoke scenarios)"
    if timeout 40m env CURA_VERSION="$PRIMARY" \
            HARNESS_CONTAINER="$CONTAINER" MODE=suite SCENARIO_GROUP=smoke \
            RUN_DIR_NAME="$RUN_ROOT/$run" ./tools/ui_test.sh > "$RUN_ROOT/$run.log" 2>&1; then
        echo "harness smoke $run PASSED"
    else
        echo "harness smoke $run FAILED"
        fail=1
    fi
done
if [ "$fail" = 0 ]; then
    echo "harness smoke PASSED (run root: $RUN_ROOT)"
else
    echo "harness smoke FAILED (run root: $RUN_ROOT)"
    exit 1
fi
