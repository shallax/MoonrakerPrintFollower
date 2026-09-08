#!/bin/sh
# Run a command inside the pinned dev container as the host user.
#
# The image is rebuilt automatically when the Dockerfile changes (layer
# caching makes unchanged rebuilds instant) and the repository is
# bind-mounted at /work, so the container stays disposable. The
# --user "$(id -u):$(id -g)" incantation lives here, not in anyone's
# muscle memory: bind-mount writes are always owned by the host user.
#
# With no arguments the container's default command runs the full gate
# suite (compile, QML structure/format, ruff, shellcheck, hadolint,
# coverage and the deterministic captures).
#
# Usage:  tools/docker_dev.sh <command...>
#         tools/docker_dev.sh qmlformat -i plugins/Monitor.qml
#         tools/docker_dev.sh python3 -m unittest discover -s tests
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"
docker build -q -t moonraker-print-follower-dev . >/dev/null
docker run --rm --user "$(id -u):$(id -g)" -v "$root":/work moonraker-print-follower-dev "$@"
