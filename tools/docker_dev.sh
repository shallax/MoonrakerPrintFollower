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
# A worktree's .git is a FILE pointing at the main repository's gitdir
# — an absolute path that does not exist inside the container. Remember
# it so the container can mount the real gitdir at that path; otherwise
# git-backed tests (the package timestamp, the tracked-file census)
# fail with exit 128 inside the mounted tree.
gitdir=""
common_gitdir=""
if [ -f "$root/.git" ]; then
    gitdir="$(awk '/^gitdir:/ {print $2; exit}' "$root/.git")"
    # The worktree gitdir chains to the main repository's .git via its
    # commondir file — git needs that path inside the container too.
    common_gitdir="$(git rev-parse --git-common-dir)"
fi
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
# docker exec needs an explicit command: a running container has no
# default command to fall back on (the image's CMD exists only at run
# time). Zero-argument invocations therefore always take the run path,
# which starts the gate suite via that CMD.
#
# The warm container must also mount the CURRENT tree at /work (a
# worktree session must never run another checkout's tests) and, for
# worktrees, the real gitdir — a stale mount is the same drift class
# the image check above guards, so it forces the run path too.
warm_ok="false"
if [ "$#" -gt 0 ] && [ -n "$container_image" ] && [ "$container_image" = "$image_id" ]; then
    work_src="$(docker inspect "$name" --format '{{range .Mounts}}{{if eq .Destination "/work"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
    mounts_src="$(docker inspect "$name" --format '{{range .Mounts}}{{println .Source}}{{end}}' 2>/dev/null || true)"
    if [ "$work_src" = "$root" ] && { [ -z "$gitdir" ] || { printf '%s\n' "$mounts_src" | grep -q "^${gitdir}$" && printf '%s\n' "$mounts_src" | grep -q "^${common_gitdir}$"; }; }; then
        warm_ok="true"
    fi
fi
if [ "$warm_ok" = "true" ]; then
    docker exec -i -w /work "$name" "$@"
else
    # The stale container must not be torn down while another invocation
    # is still executing inside it (parallel make targets can race this
    # path): docker rm -f would kill that run mid-command. When it is
    # running, leave it alone and fall through to the docker run path —
    # the fresh container is unnamed, so it cannot collide with it. The
    # stale one is removed the next time this path runs and finds it idle.
    if [ "$(docker inspect "$name" --format '{{.State.Running}}' 2>/dev/null || true)" != "true" ]; then
        docker rm -f "$name" >/dev/null 2>&1 || true
    fi
    # /tmp/mpf is the deterministic scratch dir (the ruling:
    # probes, packages and transient files live there, never in the
    # source tree) — bind-mounted so the container sees the same
    # files as the host.
    mkdir -p /tmp/mpf
    if [ -n "$gitdir" ]; then
        docker run --rm --user "$(id -u):$(id -g)" -v "$root":/work -v "$gitdir:$gitdir:ro" -v "$common_gitdir:$common_gitdir:ro" -v /tmp/mpf:/tmp/mpf moonraker-print-follower-dev "$@"
    else
        docker run --rm --user "$(id -u):$(id -g)" -v "$root":/work -v /tmp/mpf:/tmp/mpf moonraker-print-follower-dev "$@"
    fi
fi
