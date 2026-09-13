#!/bin/sh
# The real-Cura scenario driver: boots Cura under Xvfb in the harness
# container, runs the selected scenario mode, and leaves nothing behind.
# Usage: make ui_test        -> the skeleton demo scenario (passing gallery)
#        make ui_test MODE=fail       -> the deliberately failing run
#        make ui_test MODE=discover   -> dump stage-menu coordinates
#        make ui_test MODE=scenarioN  -> one gate scenario (1..11)
#        make ui_test MODE=suite SCENARIO_GROUP=<letter or name>
#        make ui_test MODE=real       -> read-only observation of a real
#            printer (REAL_URL + REAL_API_KEY in the environment; the
#            host and key never touch the repo — TESTING.md §2.5)
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

# The plugin package is version-named; read it from package.json.
PLUGIN_VERSION="$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])')"

CONTAINER="${HARNESS_CONTAINER:-mpf-cura513}"
RUN_DIR=/tmp/mpf/ui-artifacts/run-001

# The pinned Cura for this run: any version can be selected; prepare
# one with tools/fetch_cura.py (the manifest records the swap).
CURA_VERSION="${CURA_VERSION:-5.13.0}"
CURA_ROOT="/tmp/mpf/cura_versions/$CURA_VERSION/root"
CURA_WHEELS="/tmp/mpf/cura_versions/$CURA_VERSION/wheels"
if [ ! -d "$CURA_ROOT" ]; then
    echo "ui_test: Cura $CURA_VERSION is not prepared — run tools/fetch_cura.py $CURA_VERSION first"
    exit 1
fi

# Nothing outlives a run: Cura, its video ffmpeg and the simulator die
# with the run (the container runs docker-init, which reaps the
# zombies). The brackets keep pkill from matching its own command line.
cleanup() {
    docker exec "$CONTAINER" bash -lc \
        'pkill -9 -f "UltiMaker-Cur[a]" 2>/dev/null; \
         pkill -9 -f "ffmpe[g]" 2>/dev/null; \
         pkill -9 -f "simulator_serve[.]py" 2>/dev/null; true' || true
}
trap cleanup EXIT

# Fresh-seed the run's Cura profile from the pinned config dir: the
# machine + printer record (pointed at the simulator), welcome and
# What's-New suppressed. Logs, registry state and probe debris from
# earlier runs never carry over.
rm -rf /tmp/mpf/xdg
mkdir -p /tmp/mpf/xdg
cp -r "$root/tests/harness/config/." /tmp/mpf/xdg/
# Real mode points the seeded machine record at the real host, at
# runtime, from the environment — the host and key never touch the
# repo, the logs or any committed file.
if [ "${MODE:-scenario}" = "real" ]; then
    [ -n "${REAL_URL:-}" ] || { echo "ui_test: MODE=real needs REAL_URL (and REAL_API_KEY) in the environment"; exit 1; }
    python3 - "$REAL_URL" "${REAL_API_KEY:-}" << 'PY'
import sys
url, key = sys.argv[1], sys.argv[2]
path = "/tmp/mpf/xdg/config/cura/5.13/cura.cfg"
text = open(path).read()
text = text.replace('"url":"http://127.0.0.1:7125"',
                    '"url":"%s"' % url.replace('\\', '\\\\').replace('"', '\\"'))
text = text.replace('"api_key":""',
                    '"api_key":"%s"' % key.replace('\\', '\\\\').replace('"', '\\"'))
open(path, "w").write(text)
PY
fi
# The seed and plugin dirs live under Cura's per-version data dir;
# the seed is written for 5.13, so carry it over for another version.
SEED_VER="${CURA_VERSION%.*}"
if [ "$SEED_VER" != "5.13" ]; then
    cp -r /tmp/mpf/xdg/config/cura/5.13 /tmp/mpf/xdg/config/cura/"$SEED_VER"
    cp -r /tmp/mpf/xdg/cura/5.13 /tmp/mpf/xdg/cura/"$SEED_VER"
fi
# The driver's ready marker is per-boot: a stale one from an earlier
# run would let the wait loop pass before Cura is actually up.
rm -f /tmp/mpf/harness_port.txt
COORDS=/tmp/mpf/harness_coords.json
MODE="${MODE:-scenario}"
export HARNESS_MODE="$MODE"

# Stage the production plugin and the driver into the run's Cura
# profile (the XDG data dir the spike established). A RED run stages
# the plugin built from a known-broken revision — the runner and the
# driver stay current (the scenario itself must be the same).
PLUGIN_DIR="/tmp/mpf/xdg/cura/$SEED_VER/plugins"
rm -rf "$PLUGIN_DIR/Moonraker_Print_Follower" "$PLUGIN_DIR/HarnessDriver"
mkdir -p "$PLUGIN_DIR"
PACKAGE_ROOT="$root/dist"
if [ -n "${RED_REV:-}" ]; then
    RED_DIR="/tmp/mpf/red-$RED_REV"
    if [ ! -f "$RED_DIR/dist/MoonrakerPrintFollower-v$PLUGIN_VERSION.curapackage" ]; then
        git -C "$root" worktree add --detach "$RED_DIR" "$RED_REV" >/dev/null 2>&1 || true
        (cd "$RED_DIR" && make package >/dev/null 2>&1) || true
    fi
    PACKAGE_ROOT="$RED_DIR/dist"
fi
(cd /tmp/mpf && rm -rf pkg_stage && mkdir pkg_stage && \
 unzip -q -o "$PACKAGE_ROOT/MoonrakerPrintFollower-v$PLUGIN_VERSION.curapackage" \
   -d pkg_stage 'files/plugins/*')
cp -r /tmp/mpf/pkg_stage/files/plugins/Moonraker_Print_Follower "$PLUGIN_DIR/"
cp -r "$root/tests/harness/driver" "$PLUGIN_DIR/HarnessDriver"
cp "$root/tests/harness/runner.py" /tmp/mpf/harness_runner.py
cp "$root/tests/harness/scenarios.py" /tmp/mpf/scenarios.py
cp "$root/tests/harness/scenario_map.py" /tmp/mpf/scenario_map.py
cp "$root/tests/harness/surface_coverage.py" /tmp/mpf/coverage.py

# The bracket keeps pgrep from matching the exec shell's own command
# line (which contains the pattern) — without it the guard always
# reports a running Xvfb and a fresh container never gets its display.
# The spawn is a detached exec, the daemon form that survives: a
# shell-backgrounded Xvfb — subshell or not, nohup or not — dies with
# its exec session's teardown (proven empirically on a fresh
# container), while `docker exec -d` has no session to tear down.
docker exec "$CONTAINER" bash -lc 'pgrep -f "Xvfb :9[9]" >/dev/null' || \
    docker exec -d "$CONTAINER" Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp

# Kill anything a crashed previous run left behind (the EXIT trap
# covers clean exits; this covers ui_test.sh itself being killed):
# stale instances share the XDG seed and the display, fight over the
# per-machine config record and reconnect to the simulator — the
# sweep's intermittent model-vanishing boots traced back to the
# pile-up. The bracket in the pattern keeps the pkill from matching
# its own command line.
docker exec "$CONTAINER" bash -lc 'pkill -9 -f "UltiMaker-Cur[a]" 2>/dev/null; \
    pkill -9 -f "ffmpe[g]" 2>/dev/null; sleep 1'

# The simulator: the plugin's network peer for the run. Fixed port so
# the seeded printer config points at it deterministically. Restarted
# every run — a long-lived process keeps serving stale simulator code
# (and pgrep -f patterns match the probing shell itself). Real mode
# has no simulator — the seeded record points at the real host.
SIM_PORT=7125
if [ "$MODE" != "real" ]; then
    docker exec "$CONTAINER" bash -lc "fuser -k $SIM_PORT/tcp 2>/dev/null; sleep 0.5; \
      cd /tmp/mpf/harness_tests/tests/harness && nohup python3 simulator_serve.py $SIM_PORT \
      >/tmp/mpf/simulator.log 2>&1 &"
fi

rm -f "$RUN_DIR/index.html"
mkdir -p "$RUN_DIR"

case "$MODE" in
    discover)
        docker exec -e CURA_ROOT="$CURA_ROOT" -e CURA_WHEELS="$CURA_WHEELS" \
            "$CONTAINER" bash -lc 'su ubuntu -s /bin/bash -c "cd \$CURA_ROOT && \
            DISPLAY=:99 APPDIR=\$CURA_ROOT \
            LD_LIBRARY_PATH=\$CURA_ROOT:\$CURA_ROOT/usr/lib/x86_64-linux-gnu:\$CURA_ROOT/lib/x86_64-linux-gnu:\$CURA_ROOT/usr/lib:\$CURA_WHEELS/PyQt6/Qt6/lib \
            PYTHONPATH=\$CURA_WHEELS:\$CURA_ROOT \
            XDG_DATA_HOME=/tmp/mpf/xdg XDG_CONFIG_HOME=/tmp/mpf/xdg/config HOME=/tmp/mpf/fakehome \
            LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb timeout 1800 \
            /lib64/ld-linux-x86-64.so.2 ./UltiMaker-Cura" >/tmp/mpf/cura_run.log 2>&1 &'
        # wait for the driver's port, then run the discovery
        for _ in $(seq 1 120); do [ -s /tmp/mpf/harness_port.txt ] && break; sleep 1; done
        docker exec "$CONTAINER" env DISPLAY=:99 HARNESS_RUN_DIR="$RUN_DIR" \
            python3 /tmp/mpf/harness_runner.py discover
        ;;
    scenario|fail|scenario1|scenario1fail|scenario2|scenario3|scenario4|scenario5|scenario6|scenario7|scenario8|scenario9|scenario10|scenario11|suite|real)
        docker exec -e CURA_ROOT="$CURA_ROOT" -e CURA_WHEELS="$CURA_WHEELS" \
            "$CONTAINER" bash -lc 'su ubuntu -s /bin/bash -c "cd \$CURA_ROOT && \
            DISPLAY=:99 APPDIR=\$CURA_ROOT \
            LD_LIBRARY_PATH=\$CURA_ROOT:\$CURA_ROOT/usr/lib/x86_64-linux-gnu:\$CURA_ROOT/lib/x86_64-linux-gnu:\$CURA_ROOT/usr/lib:\$CURA_WHEELS/PyQt6/Qt6/lib \
            PYTHONPATH=\$CURA_WHEELS:\$CURA_ROOT \
            XDG_DATA_HOME=/tmp/mpf/xdg XDG_CONFIG_HOME=/tmp/mpf/xdg/config HOME=/tmp/mpf/fakehome \
            LIBGL_ALWAYS_SOFTWARE=1 QT_QPA_PLATFORM=xcb timeout 1800 \
            /lib64/ld-linux-x86-64.so.2 ./UltiMaker-Cura" >/tmp/mpf/cura_run.log 2>&1 &'
        for _ in $(seq 1 120); do [ -s /tmp/mpf/harness_port.txt ] && break; sleep 1; done
        [ -s /tmp/mpf/harness_port.txt ] || { echo "ui_test: the driver never came up"; tail -20 /tmp/mpf/cura_run.log; exit 1; }
        docker exec "$CONTAINER" env DISPLAY=:99 HARNESS_RUN_DIR="$RUN_DIR" \
            HARNESS_COORDS="$COORDS" REAL_URL="${REAL_URL:-}" \
            REAL_API_KEY="${REAL_API_KEY:-}" \
            python3 /tmp/mpf/harness_runner.py "$MODE" "${SCENARIO_GROUP:-}"
        ;;
esac
echo "ui_test: gallery at $RUN_DIR/index.html"
