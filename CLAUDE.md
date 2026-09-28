# Working in this repository

How changes are made here is documented in `INSTRUCTIONS.md`; the
architecture is in `ARCHITECTURE.md`; the UI test harness in
`TESTING.md`. Read `INSTRUCTIONS.md` before making changes.

On macOS and Windows, Makefile procedures default to the shared native
driver (`tools/native/dev.py`), with no Docker required: start with
`make dev_install`. Use `BACKEND=docker` to opt into the pinned Linux
image on either host. Linux defaults to Docker. See `INSTRUCTIONS.md`
for the toolchain, parity report and platform details.

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
