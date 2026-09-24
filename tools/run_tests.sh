#!/bin/sh
: "${PYTHON:=python3}"
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
# results combine, and the plugins/ report prints with the 95% bars
# enforced — the project total AND per file
# (tools/check_per_file_coverage.py, whose justified exclusions carry
# the scenario map's reason/evidence/date/recheck schema).
#
# Discovery is POSIX-find only and never empty: the old GNU-only
# -printf made BSD find (the macOS leg) fail, which left the list
# empty, and an empty list runs zero workers and reports a green leg
# having run nothing. An empty discovery is now fatal, and no leg may
# pass having run zero tests. TESTS_DIR (default tests) points
# discovery elsewhere — it exists so the empty case can be exercised.
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
        # A green exit that ran nothing is a false pass: an empty worker
        # list or a -p pattern matching no file both exit 0 before
        # 3.12 (which added the "NO TESTS RAN" failure). Demand a count.
        if ! grep -qE "^Ran [1-9][0-9]* tests? in " "$log"; then
            echo "FAILED — the leg exited 0 but ran no tests; the output is at $log"
            return 1
        fi
        grep -E "^(Ran|OK)" "$log" || true
        rm -f "$log"
        return 0
    fi
    echo "FAILED — the full output is at $log"
    # 40 lines, not 12: a shared-helper traceback (mount_window ->
    # mount, _probe_rig -> _mount_line) spends a dozen lines on frames
    # alone, and the dropped tail is the exception line itself — the
    # one thing a leg we cannot re-run locally is read for.
    grep -B2 -A40 "FAIL:\|ERROR:" "$log" || true
    grep -E "^(Ran|FAILED)" "$log" || true
    return 1
}

tests_dir="${TESTS_DIR:-tests}"
# sed rather than find -printf: -printf is GNU-only, and BSD find
# (macOS) rejects it outright — the failure the sort|tr pipeline hid.
files="$(find "$tests_dir" -maxdepth 1 -name 'test_*.py' | sed 's|^.*/||' | sort | tr '\n' ' ')"
if [ -z "${files% }" ]; then
    echo "discovery found no test files under $tests_dir/ — refusing to report a green leg for an empty suite" >&2
    exit 1
fi
# shellcheck disable=SC2086  # $files is a deliberate word-split list
echo "discovered $(printf '%s\n' $files | wc -l | tr -d '[:space:]') test file(s) under $tests_dir/"

run_files() {
    # One worker per test file; any worker's failure fails the leg
    # (xargs exits 123, and the tracebacks land in the shared log).
    # shellcheck disable=SC2086  # $files is a deliberate word-split list
    printf '%s\n' $files | xargs -P "$jobs" -n1 "$PYTHON" -m unittest discover -s $tests_dir -p
}

run_files_container() {
    # ONE container entry (a warm-container reuse; parallel docker_dev
    # invocations would race on the image build) with the fan-out
    # inside: each worker runs its own file's discovery.
    # shellcheck disable=SC2086  # $files is a deliberate word-split list
    tools/docker_dev.sh sh -c "cd /work && printf '%s\n' $files | xargs -P $jobs -n1 $PYTHON -m unittest discover -s $tests_dir -p"
}

run_coverage_container() {
    # shellcheck disable=SC2086  # $files is a deliberate word-split list
    tools/docker_dev.sh sh -c "cd /work && \
        rm -f /tmp/mpf/cov.*.coverage && \
        printf '%s\n' $files | xargs -P $jobs -n1 sh -c 'f=\"\$1\"; COVERAGE_FILE=/tmp/mpf/cov.\${f%.py}.coverage $PYTHON -m coverage run -m unittest discover -s $tests_dir -p \"\$f\"' _ && \
        $PYTHON -m coverage combine /tmp/mpf/cov.*.coverage && \
        $PYTHON -m coverage report --include='plugins/*' --fail-under=95 && \
        $PYTHON -m coverage json -o /tmp/mpf/coverage.json && \
        $PYTHON tools/check_per_file_coverage.py /tmp/mpf/coverage.json"
}

if [ "${COVERAGE:-0}" = "1" ]; then
    # The report IS the deliverable here — it passes through whole.
    echo "== parallel coverage (dev container, $jobs workers) =="
    run_coverage_container
    exit 0
fi

if [ "${LEGS:-all}" = "host" ]; then
    # The pre-commit hook's slice: the host suite fanned out, no
    # container legs (CI owns the container run).
    run_once "stdlib suite" run_files
    run_once "harness specs" "$PYTHON" tests/harness/test_harness_specs.py
    run_once "harness runner" "$PYTHON" tests/harness/test_harness_runner.py
    run_once "harness native dispatch" "$PYTHON" tests/harness/test_harness_native.py
    echo "host legs passed"
    exit 0
fi

run_once "stdlib suite" run_files
# The harness structural and spine checks (probe strings compile, ids
# unique, every op exists; the evidence record and the PNG dimension
# check) — not discovered on the host (no package init), so run
# directly.
run_once "harness specs" "$PYTHON" tests/harness/test_harness_specs.py
run_once "harness runner" "$PYTHON" tests/harness/test_harness_runner.py
run_once "harness native dispatch" "$PYTHON" tests/harness/test_harness_native.py
run_once "real-Qt suite (dev container, $jobs workers)" run_files_container

echo "all test suites passed"
