#!/bin/sh
# Run the full test suite locally and fail loudly on any failure.
#
# The stdlib suite runs on the host (Python 3.10+); the real-Qt suite
# runs inside the dev container, whose pinned PyQt6 and fonts make the
# Qt tests deterministic on any machine — no venv or LD_LIBRARY_PATH
# shims to remember. Each leg runs ONCE: its output lands in a scratch
# log, the verdict prints, and on failure the failure blocks follow
# and the full log is kept for inspection — a failing run is never
# re-run just to read what happened.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
log_dir="${TMPDIR:-/tmp/mpf}"
mkdir -p "$log_dir"
log="$(mktemp "$log_dir/mpf-tests.XXXXXX")"

run_once() {
    name="$1"
    shift
    echo "== $name =="
    if "$@" >"$log" 2>&1; then
        grep -E "^(Ran|OK)" "$log" || true
        rm -f "$log"
        return 0
    fi
    echo "FAILED — the full output is at $log"
    grep -B2 -A12 "FAIL:\|ERROR:" "$log" || true
    grep -E "^(Ran|FAILED)" "$log" || true
    return 1
}

run_once "stdlib suite" python3 -m unittest discover -s tests
# The harness structural and spine checks (probe strings compile, ids
# unique, every op exists; the evidence record and the PNG dimension
# check) — not discovered on the host (no package init), so run
# directly.
run_once "harness specs" python3 tests/harness/test_harness_specs.py
run_once "harness runner" python3 tests/harness/test_harness_runner.py
run_once "real-Qt suite (dev container)" tools/docker_dev.sh python3 -m unittest discover -s tests

echo "all test suites passed"
