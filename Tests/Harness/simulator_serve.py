#!/usr/bin/env python3
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
