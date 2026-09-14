#!/bin/sh
# Resolves a run's evidence dir from (base, run_dir_name) — ONE rule
# for the host and container sides of ui_test.sh. Absolute names pass
# through (the release gate's timestamped root); relative names nest
# under <base>/ui-artifacts. The two sides drifted apart once — the
# container nested an absolute name under its own ui-artifacts prefix
# and every gallery landed where neither the gate nor CI ever looked —
# so the rule lives here, shared, with tests.
#
# Usage:  ui_test_paths.sh resolve <base> <run_dir_name>
if [ "$#" -ne 3 ] || [ "$1" != "resolve" ]; then
    echo "usage: ui_test_paths.sh resolve <base> <run_dir_name>" >&2
    exit 2
fi
case "$3" in
    /*) printf '%s\n' "$3" ;;
    *) printf '%s\n' "$2/ui-artifacts/$3" ;;
esac
