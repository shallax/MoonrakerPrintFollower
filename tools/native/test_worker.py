# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Record thread stacks before a hung native test reaches its deadline.

"""Run one isolated test file with periodic native-hang diagnostics."""
import faulthandler
import os
from pathlib import Path
import runpy
import sys


def main():
    mode, target, *arguments = sys.argv[1:]
    sys.argv = [target, *arguments]
    faulthandler.enable()
    faulthandler.dump_traceback_later(60, repeat=True)
    # Keep the watchdog through interpreter/atexit cleanup: Qt can block
    # there after unittest has already printed a passing result.
    if mode == "module":
        sys.path.insert(0, os.getcwd())
        runpy.run_module(target, run_name="__main__", alter_sys=True)
    elif mode == "file":
        sys.path.insert(0, str(Path(target).resolve().parent))
        runpy.run_path(target, run_name="__main__")
    else:
        raise ValueError("unknown test invocation: " + mode)


if __name__ == "__main__":
    main()
