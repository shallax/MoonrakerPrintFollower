#!/bin/sh
# Run the full test suite locally and fail loudly on any failure.
#
# The stdlib suite runs on the host (Python 3.10+); the real-Qt suite
# runs inside the dev container, whose pinned PyQt6 and fonts make the
# Qt tests deterministic on any machine — no venv or LD_LIBRARY_PATH
# shims to remember. Failures print their tracebacks and the script
# exits non-zero.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

echo "== stdlib suite =="
python3 -m unittest discover -s tests

echo "== real-Qt suite (dev container) =="
tools/docker_dev.sh python3 -m unittest discover -s tests

echo "all test suites passed"
