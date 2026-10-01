# Working in this repository

How changes are made here is documented in `INSTRUCTIONS.md`; the
architecture is in `ARCHITECTURE.md`; the UI test harness in
`TESTING.md`. Read `INSTRUCTIONS.md` before making changes.

On macOS and Windows, Makefile procedures default to the shared native
driver (`Tools/Native/dev.py`), with no Docker required: start with
`make dev_install`. Use `BACKEND=docker` to opt into the pinned Linux
image on either host. Linux defaults to Docker. See `INSTRUCTIONS.md`
for the toolchain, parity report and platform details.

## Running a subset of the tests — do this in parallel

    make test_files FILES="Tests.test_monitor_qml_contracts Tests.test_index"

One process per file, up to `JOBS` workers at a time, a verdict per file,
non-zero exit if any file fails.
The native default is the host's core count capped at 16; Linux's
container runner defaults to 8 for `test_files`. Use `JOBS=2` on small
CI runners or when wall-clock-sensitive tests share the host.

**Never** pass several files to a single `python3 -m unittest` call.
That runs them serially inside one process — which is where the minutes
go — and the real-Qt files cannot share a process at all:
each QML domain file owns its `QGuiApplication` and rejects a foreign
application. Shared fixtures create no application at import time.
`Tools/run_some.sh` (behind the target) applies the
same per-file fan-out `Tools/run_tests.sh` uses for the whole discovery.

For everything, `make run_tests`. For the list of procedures,
`make help`.
