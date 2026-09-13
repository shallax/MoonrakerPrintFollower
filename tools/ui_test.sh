#!/bin/sh
# The real-Cura scenario driver: boots Cura under Xvfb in the harness
# container, runs the selected scenario mode, and leaves nothing behind.
# Usage: make ui_test        -> the skeleton demo scenario (passing gallery)
#        make ui_test MODE=fail       -> the deliberately failing run
#        make ui_test MODE=discover   -> dump stage-menu coordinates
#        make ui_test MODE=scenarioN  -> one gate scenario (1..11)
#        make ui_test MODE=suite SCENARIO_GROUP=<group name>
#        make ui_test MODE=real       -> read-only observation of a real
#            printer (REAL_URL + REAL_API_KEY in the environment; the
#            host and key never touch the repo — TESTING.md §2.5)
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

# The plugin package is version-named; read it from package.json.
PLUGIN_VERSION="$(python3 -c 'import json; print(json.load(open("package.json"))["package_version"])')"

CONTAINER="${HARNESS_CONTAINER:-mpf-cura513}"
# Per-unit evidence dir: the release gate gives every unit its own name
# so a later unit never overwrites an earlier one's proof (the panel's
# evidence-survival finding).
# An absolute RUN_DIR_NAME passes through (the release gate's
# timestamped root); a relative one nests under ui-artifacts.
case "${RUN_DIR_NAME:-run-001}" in
    /*) RUN_DIR="$RUN_DIR_NAME" ;;
    *) RUN_DIR="/tmp/mpf/ui-artifacts/${RUN_DIR_NAME:-run-001}" ;;
esac

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
    if [ "${MODE:-scenario}" = "real" ]; then
        # The seeded profile holds the real host and key at runtime:
        # a real run's debris must not outlive the run (the author's
        # rule — the key must never sit on disk beyond the session).
        case "${RUN_DIR_NAME:-run-001}" in
            /*) rm -rf /tmp/mpf/xdg "$RUN_DIR_NAME" ;;
            *) rm -rf /tmp/mpf/xdg /tmp/mpf/ui-artifacts/"${RUN_DIR_NAME:-run-001}" ;;
        esac
    fi
}
trap cleanup EXIT INT TERM

# Fresh-seed the run's Cura profile from the pinned config dir: the
# machine + printer record (pointed at the simulator), welcome and
# What's-New suppressed. Logs, registry state and probe debris from
# earlier runs never carry over.
# Cura's own writes land with owner-only modes (settings files go
# 0600, its dirs 0775): on CI the next unit's host-side rm hits them
# as a different uid. The container's root does the destructive pass.
docker exec "$CONTAINER" rm -rf /tmp/mpf/xdg
mkdir -p /tmp/mpf/xdg
cp -r "$root/tests/harness/config/." /tmp/mpf/xdg/
# The container's Cura writes into the seeded tree — the instance lock
# is its very first write, and the boot retries it forever on EACCES.
# On CI the host-side creators run as a different uid than the
# container's, so the copy must be opened up AFTER it lands (cp -r
# restores the 755 modes the chmod would have fixed).
chmod -R 777 /tmp/mpf/xdg
# The seeded data dir is empty, and git drops empty directories from
# the checkout: a fresh CI tree lacks it while a dev box's working
# tree keeps it. Recreate it — the version carry-over copies it and
# the boot writes its lock and settings into it.
mkdir -p /tmp/mpf/xdg/cura/5.13
# Real mode points the seeded machine record at the real host, at
# runtime, from the environment — the host and key never touch the
# repo, the logs or any committed file.
if [ "${MODE:-scenario}" = "real" ]; then
    [ -n "${REAL_URL:-}" ] || { echo "ui_test: MODE=real needs REAL_URL (and REAL_API_KEY) in the environment"; exit 1; }
    python3 - "$REAL_URL" "${REAL_API_KEY:-}" << 'PY'
import sys
url, key = sys.argv[1], sys.argv[2]
path = "/tmp/mpf/xdg/config/cura/5.13/cura.cfg"
before = open(path).read()
text = before
text = text.replace('"url":"http://127.0.0.1:7125"',
                    '"url":"%s"' % url.replace('\\', '\\\\').replace('"', '\\"'))
text = text.replace('"api_key":""',
                    '"api_key":"%s"' % key.replace('\\', '\\\\').replace('"', '\\"'))
if text == before:
    # A silent no-op would run the "real" gallery against the
    # simulator — the substitution must land or the run must die.
    raise SystemExit("ui_test: the real-mode seed rewrite did not match the seeded record")
open(path, "w").write(text)
import os as _os
_os.chmod(path, 0o600)
PY
fi
# The seed and plugin dirs live under Cura's per-version data dir;
# the seed is written for 5.13, so carry it over for another version.
SEED_VER="${CURA_VERSION%.*}"
if [ "$SEED_VER" != "5.13" ]; then
    cp -r /tmp/mpf/xdg/config/cura/5.13 /tmp/mpf/xdg/config/cura/"$SEED_VER"
    cp -r /tmp/mpf/xdg/cura/5.13 /tmp/mpf/xdg/cura/"$SEED_VER"
    # The carry-over copy lands with the same 755 modes (cp -r); the
    # cross-uid chmod above must cover it too.
    chmod -R 777 /tmp/mpf/xdg/config/cura/"$SEED_VER" /tmp/mpf/xdg/cura/"$SEED_VER"
fi
# CuraEngine's ELF carries a RELATIVE interpreter path, resolved from
# the spawning process's cwd — which is the fakehome (Cura chdirs
# there). Without this link the backend's engine spawn dies with
# ENOENT and no toolpath can ever slice (proven by the cube flow).
mkdir -p /tmp/mpf/fakehome/lib64
# The container runs as a fixed uid and must write this throwaway home
# whatever uid created the mount on the host (the CI runner's user
# differs from the dev box's — the .local mkdir died with EACCES).
# The chmod runs in the container: a previous unit's Cura wrote here
# with owner-only modes, and a host-side chmod would EPERM on files
# it does not own.
docker exec "$CONTAINER" chmod -R 777 /tmp/mpf/fakehome
ln -sfn /lib64/ld-linux-x86-64.so.2 /tmp/mpf/fakehome/lib64/ld-linux-x86-64.so.2
# Stage the suite's test model where the insert-slice flow reads it.
mkdir -p /tmp/mpf/models
cp "$root/tests/harness/models/voron_cube.stl" /tmp/mpf/models/voron_cube.stl
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
# The container's root re-opens the seeded tree as the last staging
# act: whatever uid skew survives between the host-side chmod and the
# boot's view, the tree the boot actually sees ends up world-writable.
docker exec "$CONTAINER" chmod -R 777 /tmp/mpf/xdg
cp "$root/tests/harness/runner.py" /tmp/mpf/harness_runner.py
cp "$root/tests/harness/scenarios.py" /tmp/mpf/scenarios.py
cp "$root/tests/harness/scenario_map.py" /tmp/mpf/scenario_map.py
cp "$root/tests/harness/surface_coverage.py" /tmp/mpf/coverage.py
# The simulator is a test instrument, not a fixture: the working
# tree's version must be what the run peers against — a stale staged
# copy once served old protocol shapes for days and every arm on a
# new field silently no-oped (the power-arm calibration lesson).
# The destination is created here: the CI runners start without the
# harness_tests tree, and the copy used to fail every unit at staging.
mkdir -p /tmp/mpf/harness_tests/tests/harness
cp "$root/tests/harness/simulator.py" "$root/tests/harness/simulator_serve.py" \
    "$root/tests/test_simulator.py" /tmp/mpf/harness_tests/tests/harness/

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
    # Readiness by check, not by luck: the ledger endpoint answers
    # before the run proceeds (the panel's finding).
    for _ in $(seq 1 50); do
        if docker exec "$CONTAINER" python3 -c \
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$SIM_PORT/ledger', timeout=2)" \
                >/dev/null 2>&1; then
            break
        fi
        sleep 0.2
    done
fi

rm -f "$RUN_DIR/index.html"
mkdir -p "$RUN_DIR"
# The container's runner and driver write into the scratch tree (the
# port/token files at /tmp/mpf itself, the galleries under this dir);
# on CI the host-side creators run as a different uid than the
# container's, so the tree must be open to everyone. Throwaway scratch.
chmod 777 /tmp/mpf
chmod -R 777 "$RUN_DIR"

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
        # The port must appear before the scenario can start; the CI
        # runners are 2-vCPU VMs and boot Cura far more slowly than a
        # dev box, so the deadline is generous. The failure report
        # distinguishes a slow boot from a dead one.
        boot_start=$(date +%s)
        for _ in $(seq 1 300); do [ -s /tmp/mpf/harness_port.txt ] && break; sleep 1; done
        if [ -s /tmp/mpf/harness_port.txt ]; then
            echo "ui_test: driver up after $(( $(date +%s) - boot_start ))s"
        else
            echo "ui_test: the driver never came up"
            if docker exec "$CONTAINER" bash -lc 'pgrep -f "UltiMaker-Cur[a]" >/dev/null'; then
                echo "ui_test: Cura is still running — the boot did not finish within the deadline"
            else
                echo "ui_test: Cura is not running — the boot crashed or exited"
            fi
            if docker exec "$CONTAINER" bash -lc 'pgrep -f "Xvfb :9[9]" >/dev/null'; then
                echo "ui_test: Xvfb is up"
            else
                echo "ui_test: Xvfb is not running"
            fi
            # The tree's state as the host seeded it and as the
            # container sees it, the boot process's real uid, and a
            # live write probe — an EACCES loop must be explainable by
            # one of these.
            for d in /tmp/mpf /tmp/mpf/xdg /tmp/mpf/xdg/cura /tmp/mpf/xdg/cura/5.13; do
                echo "ui_test: host  $(stat -c "%a %U %G" "$d" 2>/dev/null) $d"
            done
            docker exec "$CONTAINER" bash -lc '
                for d in /tmp/mpf /tmp/mpf/xdg /tmp/mpf/xdg/cura /tmp/mpf/xdg/cura/5.13; do
                    echo "ui_test: container  $(stat -c "%a %U %G" "$d" 2>/dev/null) $d"
                done
                echo "ui_test: ubuntu user: $(su ubuntu -s /bin/bash -c "id -u; id -g" | tr "\n" " ")"
                if su ubuntu -s /bin/bash -c "touch /tmp/mpf/xdg/cura/5.13/harness-probe" 2>/dev/null; then
                    echo "ui_test: write probe as ubuntu: ok"
                else
                    echo "ui_test: write probe as ubuntu: FAILED"
                fi
                for p in $(pgrep -f "ld-linux.*UltiMaker-Cur[a]"); do
                    echo "ui_test: $(grep "^Uid:" /proc/$p/status 2>/dev/null) of pid $p"
                done'
            # The kernel's verdict on the stall: the process chain and
            # every thread's blocked syscall, then a live strace and
            # gdb backtrace of the loader (SYS_PTRACE is granted at
            # container start for exactly this).
            docker exec "$CONTAINER" bash -lc '
                for pid in $(pgrep -f "UltiMaker-Cur[a]"); do
                    cmd=$(tr "\0" " " < /proc/$pid/cmdline 2>/dev/null)
                    rest=$(sed -E "s/^[0-9]+ \([^)]*\) //" /proc/$pid/stat 2>/dev/null)
                    echo "ui_test: pid $pid ppid $(echo "$rest" | cut -d" " -f2) state $(echo "$rest" | cut -d" " -f1) cpu $(echo "$rest" | cut -d" " -f12): $cmd"
                    for c in $(ps -o pid= --ppid "$pid" 2>/dev/null); do
                        echo "ui_test:   child $c: $(ps -o stat=,args= -p "$c" 2>/dev/null | head -1)"
                    done
                done
                xpid=$(pgrep -f "Xvfb :9[9]" | head -1)
                if [ -n "$xpid" ]; then
                    xrest=$(sed -E "s/^[0-9]+ \([^)]*\) //" /proc/$xpid/stat 2>/dev/null)
                    echo "ui_test: Xvfb pid $xpid state $(echo "$xrest" | cut -d" " -f1) cpu $(echo "$xrest" | cut -d" " -f12)"
                fi
                for pid in $(pgrep -f "ld-linux.*UltiMaker-Cur[a]"); do
                    rest=$(sed -E "s/^[0-9]+ \([^)]*\) //" /proc/$pid/stat 2>/dev/null)
                    echo "ui_test: ld chain pid $pid state $(echo "$rest" | cut -d" " -f1) cpu $(echo "$rest" | cut -d" " -f12)"
                    for t in /proc/$pid/task/*; do
                        echo "ui_test:   tid $(basename $t): wchan $(cat $t/wchan 2>/dev/null), syscall $(cat $t/syscall 2>/dev/null)"
                    done
                done
                for p in $(pgrep -f "ld-linux.*UltiMaker-Cur[a]"); do
                    case $(tr "\0" " " < /proc/$p/cmdline 2>/dev/null) in
                        *"timeout 1800"*|*"su ubuntu"*|*"bash -c"*) ;;
                        *) pid=$p ;;
                    esac
                done
                if [ -n "${pid:-}" ]; then
                    echo "ui_test: strace of loader pid $pid:"
                    timeout 8 strace -f -tt -s 100 -p "$pid" -o /tmp/mpf/strace.txt 2>&1 | tail -3
                    tail -30 /tmp/mpf/strace.txt 2>/dev/null
                    echo "ui_test: gdb backtrace of loader pid $pid:"
                    timeout 30 gdb -batch -ex "set pagination off" -ex "thread apply all bt" -p "$pid" 2>&1 | grep -v "^\[New \|^\[Thread " | head -90
                fi'
            echo "ui_test: cura_run.log ($(wc -c < /tmp/mpf/cura_run.log) bytes):"
            tail -40 /tmp/mpf/cura_run.log
            exit 1
        fi
        if [ "$MODE" = "real" ]; then
            # The host and key ride the container exec ONLY for real
            # mode — simulator runs never carry them (the panel's
            # process-table finding).
            docker exec "$CONTAINER" env DISPLAY=:99 HARNESS_RUN_DIR="$RUN_DIR" \
                HARNESS_COORDS="$COORDS" REAL_URL="${REAL_URL:-}" \
                REAL_API_KEY="${REAL_API_KEY:-}" \
                python3 /tmp/mpf/harness_runner.py "$MODE" "${SCENARIO_GROUP:-}"
        else
            docker exec "$CONTAINER" env DISPLAY=:99 HARNESS_RUN_DIR="$RUN_DIR" \
                HARNESS_COORDS="$COORDS" \
                python3 /tmp/mpf/harness_runner.py "$MODE" "${SCENARIO_GROUP:-}"
        fi
        ;;
esac
echo "ui_test: gallery at $RUN_DIR/index.html"
