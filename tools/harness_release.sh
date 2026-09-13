#!/bin/sh
# The release gate's real-Cura scenario runs: the 11 gates and the
# suite on the primary pinned Cura, then the gates again on the
# secondary version — the version-swap proof (TESTING.md §5).
#
# Budgets are declared per layer; the soak group stays out of the
# release path. Every unit retries up to 3 times — the documented
# flake policy for the boot-time discovery intermittency — and the
# galleries carry the evidence either way.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

HARNESS_DIR="${HARNESS_DIR:-/tmp/mpf}"
CONTAINER="${HARNESS_CONTAINER:-mpf-cura513}"
PRIMARY="${CURA_PRIMARY:-5.13.0}"
SECONDARY="${CURA_SECONDARY:-5.12.0}"

mkdir -p "$HARNESS_DIR"

docker build -q -t mpf-cura-harness "$root/tools/harness" >/dev/null
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --init --name "$CONTAINER" \
    -v "$HARNESS_DIR:$HARNESS_DIR" mpf-cura-harness sleep infinity >/dev/null

python3 "$root/tools/fetch_cura.py" "$PRIMARY"
python3 "$root/tools/fetch_cura.py" "$SECONDARY"

fail=0
unit() {  # unit <minutes-budget> <cura-version> <description> <mode> [group]
    budget=$1; version=$2; desc=$3; mode=$4; group=${5:-}
    echo "== $desc (budget ${budget}m per attempt) =="
    green=0
    for attempt in 1 2 3; do
        if [ -n "$group" ]; then
            if timeout "${budget}m" env CURA_VERSION="$version" \
                HARNESS_CONTAINER="$CONTAINER" MODE="$mode" \
                SCENARIO_GROUP="$group" ./tools/ui_test.sh; then
                green=1; break
            fi
        elif timeout "${budget}m" env CURA_VERSION="$version" \
            HARNESS_CONTAINER="$CONTAINER" MODE="$mode" \
            ./tools/ui_test.sh; then
            green=1; break
        fi
        echo "    attempt $attempt failed"
    done
    if [ "$green" != 1 ]; then
        echo "FAILED: $desc"
        fail=1
    fi
}

# The primary version: the gates, then the suite groups a–i.
for n in 1 2 3 4 5 6 7 8 9 10 11; do
    unit 10 "$PRIMARY" "gate $n on $PRIMARY" "scenario$n"
done
for g in a b c d e f g h i; do
    unit 15 "$PRIMARY" "suite group $g on $PRIMARY" suite "$g"
done
# The secondary version: the swap proof — the gates again under it.
for n in 1 2 3 4 5 6 7 8 9 10 11; do
    unit 10 "$SECONDARY" "gate $n on $SECONDARY" "scenario$n"
done

docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
if [ "$fail" != 0 ]; then
    echo "ui release gate FAILED"
    exit 1
fi
echo "ui release gate PASSED"
