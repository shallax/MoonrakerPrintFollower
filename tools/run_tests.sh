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
#
# JOBS (default 16 — the build host's 32 cores) fans each leg out over
# the test FILES: one worker per file in parallel (the 2026-09-18
# ruling — the files are independent: each owns its temp dirs, and
# the coverage wave proved the container tolerates parallel
# discoveries). JOBS=1 restores the serial run.
#
# COVERAGE=1 turns the container leg into a parallel coverage run:
# each worker measures its own file (COVERAGE_FILE per worker), the
# results combine, and the plugins/ report prints with the 95%
# per-project bar enforced.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
jobs="${JOBS:-16}"
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

files="$(find tests -maxdepth 1 -name 'test_*.py' -printf '%f\n' | sort | tr '\n' ' ')"

run_files() {
    # One worker per test file; any worker's failure fails the leg
    # (xargs exits 123, and the tracebacks land in the shared log).
    printf '%s\n' $files | xargs -P "$jobs" -n1 python3 -m unittest discover -s tests -p
}

run_files_container() {
    # ONE container entry (a warm-container reuse; parallel docker_dev
    # invocations would race on the image build) with the fan-out
    # inside: each worker runs its own file's discovery.
    tools/docker_dev.sh sh -c "cd /work && printf '%s\n' $files | xargs -P $jobs -n1 python3 -m unittest discover -s tests -p"
}

run_coverage_container() {
    tools/docker_dev.sh sh -c "cd /work && \
        rm -f /tmp/mpf/cov.*.coverage && \
        printf '%s\n' $files | xargs -P $jobs -n1 sh -c 'f=\"\$1\"; COVERAGE_FILE=/tmp/mpf/cov.\${f%.py}.coverage python3 -m coverage run -m unittest discover -s tests -p \"\$f\"' _ && \
        python3 -m coverage combine /tmp/mpf/cov.*.coverage && \
        python3 -m coverage report --include='plugins/*' --fail-under=95"
}

if [ "${COVERAGE:-0}" = "1" ]; then
    # The report IS the deliverable here — it passes through whole.
    echo "== parallel coverage (dev container, $jobs workers) =="
    run_coverage_container
    exit 0
fi

run_once "stdlib suite" run_files
# The harness structural and spine checks (probe strings compile, ids
# unique, every op exists; the evidence record and the PNG dimension
# check) — not discovered on the host (no package init), so run
# directly.
run_once "harness specs" python3 tests/harness/test_harness_specs.py
run_once "harness runner" python3 tests/harness/test_harness_runner.py
run_once "real-Qt suite (dev container, $jobs workers)" run_files_container

echo "all test suites passed"
