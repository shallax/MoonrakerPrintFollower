#!/bin/sh
: "${PYTHON:=python3}"
# Run a CHOSEN list of test files, parallelised two ways.
#
# Passing several files to a single `python3 -m unittest` runs them
# SERIALLY inside one process — that is where the minutes go — and the
# real-Qt files cannot share a process anyway (test_qml_real_engine owns
# its QGuiApplication and skips when one already exists).
#
# This is the per-file fan-out tools/run_tests.sh uses for the whole
# discovery, scoped to the files a change touches — and, because one
# large file is otherwise a serial bottleneck (test_qml_real_engine is
# ~180 tests and minutes on its own), each file is also SHARDED across
# processes. The work lives in tools/run_some.py; this is the entry
# point the Makefile and the docs name.
#
#   make test_files FILES="tests.test_qml_real_engine tests.test_monitor"
#   JOBS=8 SHARDS=4 tools/run_some.sh tests.test_qml_real_engine
#
# JOBS   (default 8) processes at once.
# SHARDS (default 1) chunks per file — sharding is OPT-IN and NOT yet
# trusted: test_qml_real_engine runs 182/182 at SHARDS=2 (129 s vs
# 247 s serial) but shards die with no verdict at 4 and 6, reproduced
# on a clean log dir, and the remaining shards report fewer tests than
# discovery found. Until that is understood, the default stays 1.
#
# One log per shard under the scratch dir; a verdict per file; a
# non-zero exit if any shard fails or ran fewer tests than discovery
# found. No arguments is a usage error, never a pass.
set -eu

if [ "$#" -eq 0 ]; then
    echo "usage: run_some.sh <test.module> [<test.module> ...]" >&2
    echo "  e.g. run_some.sh tests.test_monitor tests.test_plate_progress" >&2
    exit 2
fi

exec "$PYTHON" "$(dirname "$0")/run_some.py" "$@"
