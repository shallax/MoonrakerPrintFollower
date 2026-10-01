#!/bin/sh
# Run every gate inside the pinned dev container.
#
# The invocation details (build-if-needed, host-user mapping, bind
# mount) live in tools/docker_dev.sh; this wrapper adds the editor-
# backup cleanup around the run.
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
tools/docker_dev.sh "$@"
status=$?
# Formatters and capture runs can leave editor-style backups behind.
find "$root" -name "*~" -not -path "*/.git/*" -delete 2>/dev/null || true
exit $status
