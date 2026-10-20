# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-10-01 [1d8bd42] Capitalise the remaining top-level directories
# 2026-09-13 [433ee88] harness: Cura version selection, swap proven, release-gate wiring

# The scenario-harness container: real Cura boots here under Xvfb.
# The Cura bundles (AppImages + PyQt wheels) live on the bind-mounted
# harness dir, prepared by tools/fetch_cura.py — this image is just
# the X environment and tooling Cura needs.
#
# Run it with `docker run --init` (see ui_test.sh): PID 1 here is
# sleep, which never reaps — docker-init reaps the killed children.
FROM ubuntu:26.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl dbus dbus-user-session ffmpeg fontconfig \
        fonts-dejavu-core fonts-dejavu-mono gdb libegl1 libgl1 libgl1-mesa-dri \
        libglx0 libgtk-3-0 libxkbcommon-x11-0 libxtst6 mesa-libgallium \
        mesa-va-drivers mesa-vdpau-drivers mesa-vulkan-drivers psmisc \
        python3-dbus python3-tornado strace unzip x11-xkb-utils xdotool xvfb \
    && rm -rf /var/lib/apt/lists/*

# The ubuntu:26.04 base already ships the harness user (ubuntu,
# uid/gid 1000) — Cura boots as it; no extra user setup needed.

CMD ["sleep", "infinity"]
