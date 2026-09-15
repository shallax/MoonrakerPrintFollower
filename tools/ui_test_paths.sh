#!/bin/sh
# The harness's path rules, ONE module with tests:
#
#   resolve <base> <run_dir_name>
#       The run-dir resolution rule. Absolute names pass through (the
#       release gate's timestamped root); relative names nest under
#       <base>/ui-artifacts. The two sides drifted apart once — the
#       container nested an absolute name under its own ui-artifacts
#       prefix and every gallery landed where neither the gate nor CI
#       ever looked — so the rule lives here, shared, with tests.
#
#   container <work_dir> <path>
#       The host->container path mapping. The slot mount maps the
#       slot's host dir onto the container's /tmp/mpf — inside a slot
#       container, host-form paths do not exist. Work-dir-prefixed
#       paths rewrite to the container form; everything else passes
#       through. The serial run (work_dir IS /tmp/mpf) is the
#       identity case.
if [ "$#" -ne 3 ]; then
    echo "usage: ui_test_paths.sh resolve <base> <run_dir_name> | container <work_dir> <path>" >&2
    exit 2
fi
if [ "$1" = "container" ]; then
    case "$3" in
        "$2"*) printf '/tmp/mpf%s\n' "${3#"$2"}" ;;
        *) printf '%s\n' "$3" ;;
    esac
    exit 0
fi
if [ "$1" != "resolve" ]; then
    echo "usage: ui_test_paths.sh resolve <base> <run_dir_name> | container <work_dir> <path>" >&2
    exit 2
fi
case "$3" in
    /*) printf '%s\n' "$3" ;;
    *) printf '%s\n' "$2/ui-artifacts/$3" ;;
esac
