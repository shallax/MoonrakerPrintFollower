#!/bin/sh
# The 2-CPU stress rig: the load-reproducible half of the harness
# determinism work, run repeatedly.
#
# The perf plan's Phase A asks for "green REPEATEDLY on 2 CPUs with the
# full fan-out (taskset -c 0,1 + the subset driver), then green on CI
# twice", and adds the reason: "one green run is not evidence — this
# class produced a different failure set on the same commit on
# consecutive runs." A single green CI round cannot show that; starving
# the machine can, because every failure in this class is a wait or a
# deadline that a busy CPU turns into a wrong answer.
#
# SERIAL, ALWAYS. Each run already oversubscribes sixteen workers onto
# two cores; running several at once multiplies that by the number of
# runs and takes the host down — measured, the machine rebooted. One
# run at a time. Do not add a parallel mode here, and do not launch two
# copies of this script.
#
# The subset is the files that have failed under load, not the whole
# suite: the point is to hammer them, cheaply, many times.
#
# Usage:  tools/stress_rig.sh [runs] [cpu-list]
#         tools/stress_rig.sh 8 0,1        (the default)
#
# Exit status is the number of red runs (0 = green throughout).
set -eu
root="$(git rev-parse --show-toplevel)"
cd "$root"

runs="${1:-8}"
cpus="${2:-0,1}"
jobs="${JOBS:-16}"

# The files the catalogue names: the real-engine render suite, its
# parity and zoom-raster neighbours, the composed-component pins and
# the camera FPS decay. unittest's -p is a filename GLOB, so the suffix
# is part of the pattern — bare module names match nothing and every run
# reports "NO TESTS RAN", which reads as a red leg while testing nothing.
files="test_qml_real_engine.py test_renderer_parity.py test_zoom_raster_real_engine.py test_composed_components.py test_moonraker_mjpg.py"

red=0
run=1
while [ "$run" -le "$runs" ]; do
    started=$(date +%s)
    log="/tmp/mpf/stress-rig-$run.log"
    # --cpuset-cpus, not taskset: the work happens in the container, so
    # pinning the docker client would constrain nothing. JOBS=$jobs is
    # the full fan-out — the rig starves CPUs and oversubscribes threads
    # at the same time, which is the condition the failures need.
    if docker run --rm --cpuset-cpus="$cpus" --user "$(id -u):$(id -g)" \
            -e PYTHONPATH=/work/tests -v "$root":/work -v /tmp/mpf:/tmp/mpf \
            moonraker-print-follower-dev \
            sh -c "cd /work && printf '%s\n' $files | xargs -P $jobs -n1 python3 -m unittest discover -s tests -p" \
            >"$log" 2>&1; then
        # A green exit that ran nothing is the failure this rig was
        # built to avoid reading as a pass.
        if grep -q "NO TESTS RAN" "$log" || ! grep -qE "^Ran [1-9][0-9]* tests" "$log"; then
            echo "run $run/$runs: RED — tested NOTHING (see $log)"
            red=$((red + 1))
        else
            echo "run $run/$runs: GREEN ($(($(date +%s) - started))s)"
        fi
    else
        red=$((red + 1))
        echo "run $run/$runs: RED — the full output is at $log"
        grep -B2 -A25 "FAIL:\|ERROR:" "$log" | head -60 || true
    fi
    run=$((run + 1))
done

echo
if [ "$red" -eq 0 ]; then
    echo "the rig was green $runs/$runs on CPUs $cpus"
else
    echo "the rig was RED in $red of $runs runs on CPUs $cpus"
fi
exit "$red"
