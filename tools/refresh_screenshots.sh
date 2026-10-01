#!/bin/sh
# Regenerate the canonical screenshots inside the dev container and
# refresh the committed copies under screenshots/.
#
# The container's pinned toolchain (Qt, fonts) makes the renders
# byte-stable, so the committed copies always match what the CI
# Screenshot sync job would produce. Run this after any change that
# affects the QML the captures render; the sync job fails on push
# until the copies are fresh.
set -eu
# The architecture is part of the capture toolchain too. Native ARM
# rasterisation differs slightly from the x86 CI baseline.
DOCKER_DEFAULT_PLATFORM=linux/amd64
export DOCKER_DEFAULT_PLATFORM
root="$(git rev-parse --show-toplevel)"
cd "$root"
rm -f dist/screenshots/*.png
# The CI's sync job builds the capture image FRESH from the Dockerfile;
# docker_dev.sh reuses layer-cached bases, so a moved base image made
# the local captures drift from the CI's (the settings scenes' fitted
# heights are font-metric sensitive). Pull fresh so the local renders
# always match what the sync job produces — and fail loudly if the
# pull/build fails: silently falling back to the stale image commits
# captures CI will reject.
"$(dirname "$0")/build_image.sh" moonraker-print-follower-dev . --pull
tools/docker_dev.sh sh tools/run_captures.sh dist/screenshots
cp dist/screenshots/*.png screenshots/
