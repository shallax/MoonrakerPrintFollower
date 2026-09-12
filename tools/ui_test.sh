#!/bin/sh
# Phase-A harness driver: real Cura under Xvfb in the harness container.
# Usage: make ui_test        -> the phase-a scenario (passing gallery)
#        make ui_test MODE=fail       -> the deliberately failing run
#        make ui_test MODE=discover   -> dump stage-menu coordinates
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

CONTAINER="${HARNESS_CONTAINER:-mpf-cura513}"
RUN_DIR=/tmp/mpf/ui-artifacts/run-001

# Fresh-seed the run's Cura profile from the pinned config dir: the
# machine + printer record (pointed at the simulator), welcome and
# What's-New suppressed. Logs, registry state and probe debris from
# earlier runs never carry over.
rm -rf /tmp/mpf/xdg
mkdir -p /tmp/mpf/xdg
cp -r "$root/tests/harness/config/." /tmp/mpf/xdg/
COORDS=/tmp/mpf/harness_coords.json
MODE="${MODE:-scenario}"
export HARNESS_MODE="$MODE"

# Stage the production plugin and the driver into the run's Cura
# profile (the XDG data dir the spike established). A RED run stages
# the plugin built from a known-broken revision — the runner and the
# driver stay current (the scenario itself must be the same).
PLUGIN_DIR=/tmp/mpf/xdg/cura/5.13/plugins
rm -rf "$PLUGIN_DIR/Moonraker_Print_Follower" "$PLUGIN_DIR/HarnessDriver"
mkdir -p "$PLUGIN_DIR"
PACKAGE_ROOT="$root/dist"
if [ -n "${RED_REV:-}" ]; then
    RED_DIR="/tmp/mpf/red-$RED_REV"
    if [ ! -f "$RED_DIR/dist/MoonrakerPrintFollower-v4.0.0.curapackage" ]; then
        git -C "$root" worktree add --detach "$RED_DIR" "$RED_REV" >/dev/null 2>&1 || true
        (cd "$RED_DIR" && make package >/dev/null 2>&1) || true
    fi
    PACKAGE_ROOT="$RED_DIR/dist"
fi
(cd /tmp/mpf && rm -rf pkg_stage && mkdir pkg_stage && \
 unzip -q -o "$PACKAGE_ROOT/MoonrakerPrintFollower-v4.0.0.curapackage" \
   -d pkg_stage 'files/plugins/*')
cp -r /tmp/mpf/pkg_stage/files/plugins/Moonraker_Print_Follower "$PLUGIN_DIR/"
cp -r "$root/tests/harness/driver" "$PLUGIN_DIR/HarnessDriver"
cp "$root/tests/harness/runner.py" /tmp/mpf/harness_runner.py
cp "$root/tests/harness/tier2_scenarios.py" /tmp/mpf/tier2_scenarios.py
cp "$root/tests/harness/tier2_map.py" /tmp/mpf/tier2_map.py
cp "$root/tests/harness/surface_coverage.py" /tmp/mpf/coverage.py

docker exec "$CONTAINER" bash -lc 'pgrep -f "Xvfb :99" >/dev/null || \
  (Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp &)'

# The simulator: the plugin's network peer for the run. Fixed port so
# the seeded printer config points at it deterministically. Restarted
# every run — a long-lived process keeps serving stale simulator code
# (and pgrep -f patterns match the probing shell itself).
SIM_PORT=7125
docker exec "$CONTAINER" bash -lc "fuser -k $SIM_PORT/tcp 2>/dev/null; sleep 0.5; \
  cd /tmp/mpf/harness_tests/tests/harness && nohup python3 simulator_serve.py $SIM_PORT \
  >/tmp/mpf/simulator.log 2>&1 &"

rm -f "$RUN_DIR/index.html"
mkdir -p "$RUN_DIR"

case "$MODE" in
    discover)
        docker exec "$CONTAINER" bash -lc 'su ubuntu -s /bin/bash -c "cd /tmp/mpf/cura513_xt && \
            DISPLAY=:99 APPDIR=/tmp/mpf/cura513_xt \
            LD_LIBRARY_PATH=/tmp/mpf/cura513_xt:/tmp/mpf/cura513_xt/usr/lib/x86_64-linux-gnu:/tmp/mpf/cura513_xt/lib/x86_64-linux-gnu:/tmp/mpf/cura513_xt/usr/lib:/tmp/mpf/qt6wheel/PyQt6/Qt6/lib \
            PYTHONPATH=/tmp/mpf/qt6wheel:/tmp/mpf/cura513_xt \
            XDG_DATA_HOME=/tmp/mpf/xdg XDG_CONFIG_HOME=/tmp/mpf/xdg/config HOME=/tmp/mpf/fakehome \
            LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb timeout 1800 \
            /lib64/ld-linux-x86-64.so.2 ./UltiMaker-Cura" >/tmp/mpf/cura_run.log 2>&1 &'
        # wait for the driver's port, then run the discovery
        for _ in $(seq 1 120); do [ -s /tmp/mpf/harness_port.txt ] && break; sleep 1; done
        docker exec "$CONTAINER" env DISPLAY=:99 HARNESS_RUN_DIR="$RUN_DIR" \
            python3 /tmp/mpf/harness_runner.py discover
        ;;
    scenario|fail|scenario1|scenario1fail|scenario2|scenario3|scenario4|scenario5|scenario6|scenario7|scenario8|scenario9|scenario10|scenario11|tier2)
        docker exec "$CONTAINER" bash -lc 'su ubuntu -s /bin/bash -c "cd /tmp/mpf/cura513_xt && \
            DISPLAY=:99 APPDIR=/tmp/mpf/cura513_xt \
            LD_LIBRARY_PATH=/tmp/mpf/cura513_xt:/tmp/mpf/cura513_xt/usr/lib/x86_64-linux-gnu:/tmp/mpf/cura513_xt/lib/x86_64-linux-gnu:/tmp/mpf/cura513_xt/usr/lib:/tmp/mpf/qt6wheel/PyQt6/Qt6/lib \
            PYTHONPATH=/tmp/mpf/qt6wheel:/tmp/mpf/cura513_xt \
            XDG_DATA_HOME=/tmp/mpf/xdg XDG_CONFIG_HOME=/tmp/mpf/xdg/config HOME=/tmp/mpf/fakehome \
            LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb timeout 1800 \
            /lib64/ld-linux-x86-64.so.2 ./UltiMaker-Cura" >/tmp/mpf/cura_run.log 2>&1 &'
        for _ in $(seq 1 120); do [ -s /tmp/mpf/harness_port.txt ] && break; sleep 1; done
        [ -s /tmp/mpf/harness_port.txt ] || { echo "ui_test: the driver never came up"; tail -20 /tmp/mpf/cura_run.log; exit 1; }
        docker exec "$CONTAINER" env DISPLAY=:99 HARNESS_RUN_DIR="$RUN_DIR" \
            HARNESS_COORDS="$COORDS" python3 /tmp/mpf/harness_runner.py "$MODE" "${TIER2_GROUP:-}"
        ;;
esac
echo "ui_test: gallery at $RUN_DIR/index.html"
