#!/bin/sh
# Build an image with a bounded retry.
#
# These Dockerfiles install their toolchain from apt, and that step is
# the flakiest thing in CI: one unreachable mirror in one attempt fails
# the whole build with apt's exit 100, and the very next attempt on the
# same runner succeeds. Unretried, that reads as a red leg on a build
# nobody changed (the screenshot-sync job's failure was this and
# nothing else). Three attempts with a widening pause turn it into a
# slow step instead.
#
# Usage: build_image.sh <image> <context> [extra docker build args...]
set -eu
image="$1"
context="$2"
shift 2
attempt=1
while [ "$attempt" -le 3 ]; do
    # shellcheck disable=SC2086
    if docker build -q -t "$image" "$@" "$context"; then
        exit 0
    fi
    echo "the $image image build failed (attempt $attempt of 3) - retrying" >&2
    attempt=$((attempt + 1))
    sleep $((attempt * 10))
done
exit 1
