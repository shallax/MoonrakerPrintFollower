#!/bin/sh
# Verify that QML files are qmlformat-canonical.
#
# qmlformat before Qt 6.5 has no --check, so this formats to stdout and
# diffs against the file — equivalent behaviour across Qt versions.
# Used by the pre-commit hook, the CI lint job and the dev Docker image.
#
# Arguments are files, or directories meaning every .qml under them at
# any depth. The tree nests by domain, so naming each subdirectory's
# glob no longer works, and POSIX sh cannot express "any depth" — the
# expansion belongs here, where it cannot silently match nothing. A
# quoted pattern once reached qmlformat as a literal filename and the
# gate reported a missing file as a formatting failure.
set -eu

qmlformat="$(command -v qmlformat 2>/dev/null || true)"
[ -z "$qmlformat" ] && [ -x /usr/lib/qt6/bin/qmlformat ] && qmlformat=/usr/lib/qt6/bin/qmlformat
if [ -z "$qmlformat" ]; then
    echo "check_qml_format: qmlformat not found — skipping (see INSTRUCTIONS.md)" >&2
    exit 0
fi

tmp="$(mktemp)"
list="$(mktemp)"
trap 'rm -f "$tmp" "$list"' EXIT
: > "$list"

for target in "$@"; do
    if [ -d "$target" ]; then
        find "$target" -name '*.qml' -type f | sort >> "$list"
    else
        printf '%s\n' "$target" >> "$list"
    fi
done

if [ ! -s "$list" ]; then
    echo "check_qml_format: nothing to check — refusing to pass vacuously" >&2
    exit 1
fi

status=0
# Read from a file rather than a pipe: a piped loop is a subshell, and
# a failed status set inside one never reaches the exit below.
while IFS= read -r file; do
    if ! "$qmlformat" "$file" > "$tmp" || ! diff -u "$file" "$tmp" >&2; then
        echo "check_qml_format: $file is not qmlformat-canonical; run qmlformat -i" >&2
        status=1
    fi
done < "$list"
exit "$status"
