#!/bin/sh
# Pre-commit checks for MoonrakerPrintFollower.
#
# Installed as the git pre-commit hook by Tools/install_hooks.sh; the
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
# Tests/theme_assets is vendored upstream Cura QML, verbatim: it must
# never be reformatted, so the format gate skips it.
# R is in the filter because a move is a rename: the documents a
# restructure relocates are exactly the ones whose import and asset
# lines it rewrites, and ACM alone left them unchecked.
changed="$(git diff --cached --name-only --diff-filter=ACMR | grep '\.qml$' \
    | grep -v '^Tests/ThemeAssets/' || true)"

check compile python3 -m compileall -q mpf tools tests &
check qml-structure python3 Tools/check_qml.py mpf &
if [ "$have_docker" = "1" ]; then
    check qml-engine Tools/docker_dev.sh python3 Tools/check_qml_engine.py &
fi
if command -v gitleaks >/dev/null 2>&1; then
    check gitleaks gitleaks protect --staged --no-banner &
elif [ "$have_docker" = "1" ]; then
    check gitleaks Tools/docker_dev.sh gitleaks protect --staged --no-banner &
else
    echo "pre-commit: gitleaks not found — skipping (see INSTRUCTIONS.md)"
fi
if command -v ruff >/dev/null 2>&1; then
    check ruff ruff check mpf tools tests &
elif [ "$have_docker" = "1" ]; then
    # No host ruff? The pinned container has the same version CI uses;
    # skipping here would hand the failure to the CI lint job instead.
    check ruff Tools/docker_dev.sh ruff check mpf tools tests &
else
    echo "pre-commit: ruff not found — skipping (pip install ruff)"
fi
if [ -n "$changed" ]; then
    if [ -n "$qmlformat" ]; then
        # $changed is a deliberate word-split list of staged file names.
        # shellcheck disable=SC2086
        check qmlformat Tools/check_qml_format.sh $changed &
    elif [ "$have_docker" = "1" ]; then
        # $changed is a deliberate word-split list of staged file names.
        # shellcheck disable=SC2086
        check qmlformat Tools/docker_dev.sh check_qml_format $changed &
    else
        echo "pre-commit: qmlformat not found — skipping (see INSTRUCTIONS.md)"
    fi
fi
if python3 -c "import PyQt6" >/dev/null 2>&1; then
    check unit-tests env LEGS=host Tools/run_tests.sh &
elif [ "$have_docker" = "1" ]; then
    # No host PyQt6: the module-guarded QML files discover no tests
    # there, and unittest exits 5 on an empty discovery — a failure the
    # host cannot tell from a real one. The pinned container has the
    # runtime the suite expects, and the other legs already fall back
    # to it for the same reason.
    check unit-tests Tools/docker_dev.sh sh -c "cd /work && LEGS=host Tools/run_tests.sh" &
else
    echo "pre-commit: PyQt6 not found and no docker — the unit leg cannot run (see INSTRUCTIONS.md)" >&2
fi

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
