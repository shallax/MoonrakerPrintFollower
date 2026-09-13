# The scenario-harness container: real Cura boots here under Xvfb.
# The Cura bundles (AppImages + PyQt wheels) live on the bind-mounted
# harness dir, prepared by tools/fetch_cura.py — this image is just
# the X environment and tooling Cura needs.
#
# Run it with `docker run --init` (see ui_test.sh): PID 1 here is
# sleep, which never reaps — docker-init reaps the killed children.
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl dbus dbus-user-session ffmpeg fontconfig \
        fonts-dejavu-core fonts-dejavu-mono libegl1 libgl1 libgl1-mesa-dri \
        libglx0 libgtk-3-0 libxkbcommon-x11-0 libxtst6 mesa-libgallium \
        mesa-va-drivers mesa-vdpau-drivers mesa-vulkan-drivers python3-dbus \
        python3-tornado unzip x11-xkb-utils xdotool xvfb \
    && rm -rf /var/lib/apt/lists/*

# The ubuntu:24.04 base already ships the harness user (ubuntu,
# uid/gid 1000) — Cura boots as it; no extra user setup needed.

CMD ["sleep", "infinity"]
