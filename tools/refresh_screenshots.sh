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
if [ "${1:-}" = "--copy-only" ]; then
    # The gates run has already produced fresh captures (make build);
    # just refresh the committed copies.
    root="$(git rev-parse --show-toplevel)"
    cd "$root"
    cp dist/screenshots/*.png screenshots/
    exit 0
fi
root="$(git rev-parse --show-toplevel)"
cd "$root"
rm -f dist/screenshots/*.png
# The CI's sync job builds the capture image FRESH from the Dockerfile;
# docker_dev.sh reuses layer-cached bases, so a moved base image made
# the local captures drift from the CI's (the settings scenes' fitted
# heights are font-metric sensitive). Pull fresh so the local renders
# always match what the sync job produces.
docker build --pull -q -t moonraker-print-follower-dev . >/dev/null 2>&1 || true
tools/docker_dev.sh sh -c "python3 tools/capture_monitor.py dist/screenshots \
    && python3 tools/capture_preview.py dist/screenshots \
    && python3 tools/capture_settings.py dist/screenshots \
    && python3 tools/capture_upload.py dist/screenshots"
cp dist/screenshots/*.png screenshots/
