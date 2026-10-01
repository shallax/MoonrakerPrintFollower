#!/bin/sh
: "${PYTHON:=python3}"
# Run a chosen list of domain files in independent processes.
# Shared QML fixtures start their application lazily in test setup.
# The seek performance budget runs alone after correctness tests.
#
#   make test_files FILES="tests.test_qml_plate_composition tests.test_monitor_qml_contracts"
#
# JOBS (default 8) processes at once. SHARDS (default 1) is an optional
# within-file split, intended only for order-independent modules.
# One log per process, a verdict per file, and a non-zero exit for failed
# or missing tests. No arguments is a usage error, never a pass.
set -eu

if [ "$#" -eq 0 ]; then
    echo "usage: run_some.sh <test.module> [<test.module> ...]" >&2
    echo "  e.g. run_some.sh tests.test_monitor_qml_contracts tests.test_plate_progress" >&2
    exit 2
fi

exec "$PYTHON" "$(dirname "$0")/run_some.py" "$@"
