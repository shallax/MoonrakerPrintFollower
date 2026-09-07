# Development instructions

How changes are made in this repository. The architecture itself is described
in `ARCHITECTURE.md`; release history lives in `CHANGELOG.md`.

## Version bump checklist

When bumping the version (for example 3.1.0 → 3.2.0), every one of these must
change together:

1. `package.json` — `package_version`
2. `plugins/plugin.json` — `version`
3. `CHANGELOG.md` — a new section at the top, following the existing format
4. Git tag — `v<version>`; the release workflow validates the tag against both
   version fields and fails on mismatch

The version test asserts `package_version` and `plugin` `version` stay in
sync; the release workflow asserts both equal the git tag, so no test edit is
needed per release.

`sdk_version` in `package.json` is a compatibility floor, not a release number:
change it only when the Cura SDK floor moves (see `tests/test_sdk_compatibility.py`).

## Test suite organisation

- One file per domain (`test_architecture.py`, `test_session.py`,
  `test_print_state.py`, `test_monitor.py`, …).
- Never name test files or classes after versions, releases, or individual
  fixes (no `test_v31_*`, no `*_regressions`).
- New tests join the existing domain file; a new domain gets a new file.
- Tests that need real Qt are guarded with
  `@unittest.skipUnless(QT_AVAILABLE, ...)`. They skip in stdlib-only local
  runs and run in CI, where `tests.yml` installs PyQt6.

## Architecture contract

- `ARCHITECTURE.md` is the implementation contract, not a roadmap. Code
  changes that alter a boundary must update the doc and its tests together —
  never rename source strings just to make tests pass.
- Component import rules live in the allowlist in
  `tests/test_architecture.py`; changing a component's dependencies updates
  that allowlist.
- New components are constructed in `FollowerRuntime.py` and wired by
  `PrintCoordinator.py`; add them to the ownership map in `ARCHITECTURE.md`.

## Release gates

Local:

    python -m compileall -q plugins tools tests
    python tools/check_qml.py plugins
    python -m unittest discover -s tests -p "test_*.py"

CI on tag push runs the release workflow (reproducible archives, source/package
byte parity, Marketplace layout). Before tagging, run the smoke checks the
harness cannot cover: real QML rendering, native nozzle/bed-mesh integration,
Cura file-writer compatibility, multi-printer interaction and large files.
