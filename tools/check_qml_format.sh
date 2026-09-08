#!/bin/sh
# Verify that QML files are qmlformat-canonical.
#
# qmlformat before Qt 6.5 has no --check, so this formats to stdout and
# diffs against the file — equivalent behaviour across Qt versions.
# Used by the pre-commit hook, the CI lint job and the dev Docker image.
set -eu

qmlformat="$(command -v qmlformat 2>/dev/null || true)"
[ -z "$qmlformat" ] && [ -x /usr/lib/qt6/bin/qmlformat ] && qmlformat=/usr/lib/qt6/bin/qmlformat
if [ -z "$qmlformat" ]; then
    echo "check_qml_format: qmlformat not found — skipping (see INSTRUCTIONS.md)" >&2
    exit 0
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
status=0
for file in "$@"; do
    if ! "$qmlformat" "$file" > "$tmp" || ! diff -u "$file" "$tmp" >&2; then
        echo "check_qml_format: $file is not qmlformat-canonical; run qmlformat -i" >&2
        status=1
    fi
done
exit "$status"
