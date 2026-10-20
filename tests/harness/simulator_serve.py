#!/usr/bin/env python3
# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-09-27 [a1bfd0e] Capture both popovers and exercise smooth penguin following in both views
# 2026-09-12 [94f182f] harness: Phase B live-proven — the production client feeds from the simulator

"""Run the simulator standalone: python3 simulator_serve.py [port]."""
from __future__ import annotations

import sys
from pathlib import Path

# Native launches use this script's directory as sys.path[0]. The shared
# screenshot G-code generator lives in tools; Linux stages a flat copy.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tornado.ioloop

from simulator import serve

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7125
    sim = serve(port)
    print(f"simulator on {sim.base_url}", flush=True)
    tornado.ioloop.IOLoop.current().start()
