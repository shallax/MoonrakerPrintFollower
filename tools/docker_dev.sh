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
# Prefer BuildKit where the host has buildx (the modern builder);
# fall back to the legacy builder elsewhere, dropping its deprecation
# chatter — the build works fine either way, and a real failure still
# surfaces either way.
if docker buildx version >/dev/null 2>&1; then
    docker buildx build --load -q -t moonraker-print-follower-dev . >/dev/null 2>&1 || {
        echo "docker buildx build failed" >&2
        exit 1
    }
else
    build_log="$(docker build -q -t moonraker-print-follower-dev . 2>&1 >/dev/null)" || {
        printf '%s\n' "$build_log" >&2
        exit 1
    }
    printf '%s\n' "$build_log" \
        | grep -vE "legacy builder is deprecated|Install the buildx component|docs.docker.com/go/buildx" || true
fi
# Warm-container reuse: a named container (make dev_up) survives between
# commands, so repeated gates skip the create/teardown churn. The reuse
# is image-aware — after a rebuild (a --pull'd base, a Dockerfile
# change) the stale container is recreated, never reused, so a warmed
# container can never serve drifted results.
name="mpf-dev"
image_id="$(docker image inspect moonraker-print-follower-dev --format '{{.Id}}' 2>/dev/null || true)"
container_image="$(docker inspect "$name" --format '{{.Image}}' 2>/dev/null || true)"
if [ -n "$container_image" ] && [ "$container_image" = "$image_id" ]; then
    docker exec -i -w /work "$name" "$@"
else
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run --rm --user "$(id -u):$(id -g)" -v "$root":/work moonraker-print-follower-dev "$@"
fi
