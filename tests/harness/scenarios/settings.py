"""The the settings dialogs and what they persist scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations


SCENARIOS = [
    {"id": "i1", "group": "settings", "name": "the transport mode applies with its reason line",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "assert_mode", "mode": "websocket"},
     ]},
    {"id": "i2", "group": "settings", "name": "test connection probes the peer over the wire",
     "steps": [
         {"op": "exec_test_connection"},
         {"op": "sim_ledger", "needle": "server/info", "min": 1, "budget": 15},
         {"op": "sim_ledger", "needle": "objects/list", "min": 1, "budget": 15},
     ]},
    {"id": "i3", "group": "settings", "name": "the cadence validators bound at 250 ms",
     "steps": [
         {"op": "exec_validator", "validator": "validPollInterval", "args": [100], "expect": False},
         {"op": "exec_validator", "validator": "validPollInterval", "args": [1000], "expect": True},
         {"op": "exec_validator", "validator": "validRetryInterval", "args": [0.05], "expect": False},
         {"op": "exec_validator", "validator": "validCacheMax", "args": [15], "expect": False},
         {"op": "exec_validator", "validator": "validCacheMax", "args": [512], "expect": True},
     ]},
    {"id": "i4", "group": "settings", "name": "the camera config persists",
     "steps": [
         {"op": "exec_slot", "slot": "selectWebcam", "args": [0]},
         {"op": "assert_model", "prop": "activeWebcamIndex", "value": 0, "budget": 15},
     ]},
    {"id": "i6", "group": "settings", "name": "save config sends SAVE_CONFIG when the peer has pending changes",
     "steps": [
         {"op": "sim_set", "state": {"configfile": {"save_config_pending": True, "save_config_pending_items": {}}}},
         {"op": "wait_sim", "path": "configfile.save_config_pending", "value": True, "budget": 10},
         # The slot gates on the plugin-side snapshot of the pending
         # flag, which trails the peer's state by a poll; firing the
         # slot before it lands sends nothing, forever.
         {"op": "wait_model", "prop": "canSaveConfig", "value": True, "budget": 10},
         {"op": "exec_slot", "slot": "saveConfig", "args": []},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "i7", "group": "settings", "name": "open frontend hands off to the browser",
     "steps": [
         {"op": "exec_slot", "slot": "openFrontend", "args": []},
     ]},

    # ─── soaks & faults ───────────────────────────────────────
]
