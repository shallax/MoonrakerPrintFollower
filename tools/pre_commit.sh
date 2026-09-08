#!/bin/sh
# Pre-commit checks for MoonrakerPrintFollower.
#
# Installed as the git pre-commit hook by tools/install_hooks.sh; the
# same checks run in CI (.github/workflows/ci.yml, lint job). Tools
# that are not installed (ruff, qmlformat) are skipped with a note, so
# the hook never blocks work on a machine without them.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

fail=0

printf 'pre-commit: python compile... '
python3 -m compileall -q plugins tools tests || fail=1

printf 'pre-commit: QML structure... '
python3 tools/check_qml.py plugins >/dev/null || fail=1

if command -v ruff >/dev/null 2>&1; then
    printf 'pre-commit: ruff... '
    ruff check plugins tools tests || fail=1
elif command -v docker >/dev/null 2>&1; then
    # No host ruff? The pinned container has the same version CI uses;
    # skipping here would hand the failure to the CI lint job instead.
    printf 'pre-commit: ruff (container)... '
    tools/docker_dev.sh ruff check plugins tools tests || fail=1
else
    echo "pre-commit: ruff not found — skipping (pip install ruff)"
fi

qmlformat="$(command -v qmlformat 2>/dev/null || true)"
[ -z "$qmlformat" ] && [ -x /usr/lib/qt6/bin/qmlformat ] && qmlformat=/usr/lib/qt6/bin/qmlformat
changed="$(git diff --cached --name-only --diff-filter=ACM | grep '\.qml$' \
    | grep -v '^tests/theme_assets/' || true)"
# tests/theme_assets is vendored upstream Cura QML, verbatim: it must never
# be reformatted, so the format gate skips it.
if [ -n "$changed" ]; then
    printf 'pre-commit: qmlformat... '
    if [ -n "$qmlformat" ]; then
        # $changed is a deliberate word-split list of staged file names.
        # shellcheck disable=SC2086
        tools/check_qml_format.sh $changed >/dev/null || fail=1
    elif command -v docker >/dev/null 2>&1; then
        # The container's qmlformat and check_qml_format cover hosts
        # without the Qt toolchain.
        # shellcheck disable=SC2086
        tools/docker_dev.sh check_qml_format $changed >/dev/null || fail=1
    else
        echo "pre-commit: qmlformat not found — skipping (see INSTRUCTIONS.md)"
    fi
fi

printf 'pre-commit: unit tests... '
python3 -m unittest discover -s tests -p "test_*.py" >/dev/null 2>&1 || fail=1

if [ "$fail" -ne 0 ]; then
    echo "pre-commit: checks failed — fix them, or use git commit --no-verify to bypass." >&2
    exit 1
fi
echo "pre-commit: all checks passed"
