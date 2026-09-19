#!/bin/sh
# Pre-commit checks for MoonrakerPrintFollower.
#
# Installed as the git pre-commit hook by tools/install_hooks.sh; the
# same checks run in CI (.github/workflows/ci.yml, lint job). Tools
# that are not installed (ruff, qmlformat) are skipped with a note, so
# the hook never blocks work on a machine without them.
#
# The independent checks run IN PARALLEL (the 2026-09-18 ruling — a
# serial gate idles the build host's cores): each check writes its own
# log in a scratch dir, the verdicts print as they land, and any
# failure fails the commit with that check's log printed.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

status_dir="$(mktemp -d "${TMPDIR:-/tmp/mpf}/hook.XXXXXX")"
trap 'rm -rf "$status_dir"' EXIT

check() {
    label="$1"
    shift
    if "$@" >"$status_dir/$label.log" 2>&1; then
        echo "pre-commit: $label ok"
    else
        echo "pre-commit: $label FAILED"
        touch "$status_dir/failed-$label"
    fi
}

have_docker=0
command -v docker >/dev/null 2>&1 && have_docker=1
qmlformat="$(command -v qmlformat 2>/dev/null || true)"
[ -z "$qmlformat" ] && [ -x /usr/lib/qt6/bin/qmlformat ] && qmlformat=/usr/lib/qt6/bin/qmlformat
# tests/theme_assets is vendored upstream Cura QML, verbatim: it must
# never be reformatted, so the format gate skips it.
changed="$(git diff --cached --name-only --diff-filter=ACM | grep '\.qml$' \
    | grep -v '^tests/theme_assets/' || true)"

check compile python3 -m compileall -q plugins tools tests &
check qml-structure python3 tools/check_qml.py plugins &
if [ "$have_docker" = "1" ]; then
    check qml-engine tools/docker_dev.sh python3 tools/check_qml_engine.py &
fi
if command -v gitleaks >/dev/null 2>&1; then
    check gitleaks gitleaks protect --staged --no-banner &
elif [ "$have_docker" = "1" ]; then
    check gitleaks tools/docker_dev.sh gitleaks protect --staged --no-banner &
else
    echo "pre-commit: gitleaks not found — skipping (see INSTRUCTIONS.md)"
fi
if command -v ruff >/dev/null 2>&1; then
    check ruff ruff check plugins tools tests &
elif [ "$have_docker" = "1" ]; then
    # No host ruff? The pinned container has the same version CI uses;
    # skipping here would hand the failure to the CI lint job instead.
    check ruff tools/docker_dev.sh ruff check plugins tools tests &
else
    echo "pre-commit: ruff not found — skipping (pip install ruff)"
fi
if [ -n "$changed" ]; then
    if [ -n "$qmlformat" ]; then
        # $changed is a deliberate word-split list of staged file names.
        # shellcheck disable=SC2086
        check qmlformat tools/check_qml_format.sh $changed &
    elif [ "$have_docker" = "1" ]; then
        # $changed is a deliberate word-split list of staged file names.
        # shellcheck disable=SC2086
        check qmlformat tools/docker_dev.sh check_qml_format $changed &
    else
        echo "pre-commit: qmlformat not found — skipping (see INSTRUCTIONS.md)"
    fi
fi
check unit-tests env LEGS=host tools/run_tests.sh &

wait

failed="$(find "$status_dir" -maxdepth 1 -name 'failed-*' -printf '%f\n' | sed 's/^failed-//')"
if [ -n "$failed" ]; then
    echo "pre-commit: checks failed —" >&2
    for label in $failed; do
        echo "pre-commit: $label log:" >&2
        tail -40 "$status_dir/$label.log" >&2 || true
    done
    exit 1
fi
echo "pre-commit: all checks passed"
