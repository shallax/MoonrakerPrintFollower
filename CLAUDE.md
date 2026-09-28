# Working in this repository

How changes are made here is documented in `INSTRUCTIONS.md`; the
architecture is in `ARCHITECTURE.md`; the UI test harness in
`TESTING.md`. Read `INSTRUCTIONS.md` before making changes.

On Windows, every procedure runs natively through the same `make`
targets (`tools/windows/dev.py` behind them, no container, no POSIX
shell): start with `make dev_install`. See `INSTRUCTIONS.md`,
"Windows development", for the pinned toolchain and the differences.

## Running a subset of the tests — do this in parallel

    make test_files FILES="tests.test_monitor_qml_contracts tests.test_index"

One process per file, `JOBS` (default 8) at a time, a verdict per file,
non-zero exit if any file fails.

**Never** pass several files to a single `python3 -m unittest` call.
That runs them serially inside one process — which is where the minutes
go — and the real-Qt files cannot share a process at all:
each QML domain file owns its `QGuiApplication` and rejects a foreign
application. Shared fixtures create no application at import time.
`tools/run_some.sh` (behind the target) applies the
same per-file fan-out `tools/run_tests.sh` uses for the whole discovery.

For everything, `make run_tests`. For the list of procedures,
`make help`.
