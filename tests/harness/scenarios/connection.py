"""The transport, degradation and reconnection scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import PRESETS_PROBE

SCENARIOS = [
    # ─── transport & connection ───────────────────────────────
    {"id": "a1", "group": "connection", "name": "the ws dot is green only after the first accepted snapshot",
     "steps": [
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
     ]},
    {"id": "a2", "group": "connection", "name": "HTTP mode carries the full feed",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a3", "group": "connection", "name": "the mode switch back to websocket",
     "steps": [
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "assert_mode", "mode": "websocket"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 45},
     ]},
    {"id": "a4", "group": "connection", "name": "silence past the proof window degrades to HTTP",
     "steps": [
         {"op": "sim_arm", "arms": {"subscribe_hold_ms": 12000}},
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "objects", "min": 1, "budget": 40},
     ]},
    {"id": "a5", "group": "connection", "name": "a subscribe refusal degrades to HTTP with the reason",
     "steps": [
         {"op": "sim_arm", "arms": {"refuse_subscribe": "simulated refusal"}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "connectionDetail", "contains": "refused", "budget": 30},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a6", "group": "connection", "name": "a 401 surfaces the key-rejection verdict",
     "expected_log_patterns": [r"MoonrakerHTTP (?:GET|POST) (?:core|monitor)::[a-z-]+ failed: unauthorized"],
     "auth_fault_drain": True,
     "steps": [
         {"op": "sim_arm", "arms": {"require_api_key": True}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "connectionDetail", "contains": "unauthorized", "budget": 30},
     ]},
    {"id": "a7", "group": "connection", "name": "klippy ready re-arms the subscription",
     "steps": [
         # The earlier scenarios degraded the session to HTTP (the
         # proof silence, the refusal): the re-arm contract holds for
         # a WEBSOCKET session, so the scenario returns to it first —
         # a stale ready must not re-subscribe while HTTP is
         # authoritative (the hardening pass).
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a8", "group": "connection", "name": "a dropped connection reconnects on its own",
     "steps": [
         {"op": "sim_drop"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 120},
     ]},
    {"id": "a9", "group": "connection", "name": "disconnected disables every control",
     "expected_log_patterns": [r"MoonrakerHTTP (?:GET|POST) (?:core|monitor)::[a-z-]+ failed: unauthorized"],
     "auth_fault_drain": True,
     "steps": [
         {"op": "sim_arm", "arms": {"refuse_subscribe": "down", "require_api_key": True}},
         {"op": "sim_klippy"},
         # The group shares one boot: earlier scenarios CONNECTED, and
         # the tri-state's never-observed latch survives. The reconnect
         # cycles the client session (sessionInvalidated resets the
         # latch), so the premise — a session that has NEVER connected
         # — is honest.
         {"op": "exec_slot", "slot": "reconnect"},
         {"op": "wait_model", "prop": "monitorConnected", "value": False, "budget": 30},
         {"op": "assert_model", "prop": "jogEnabled", "value": False},
         # The policy gate (4.2.0): a never-observed session is
         # UNKNOWN, not idle — the restart gate fails closed and the
         # caption says so.
         {"op": "assert_model", "prop": "canRestart", "value": False},
         {"op": "assert_model", "prop": "jogReason", "value": "Printer state unknown"},
         # The RENDERED witness (the phase-6 engineering re-review's
         # D2): the disconnected decision must hold on the real item,
         # not only in the QML source text.
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         {"op": "item_disabled", "objectName": "moonrakerJogXPlus"},
     ]},

    {"id": "a10", "group": "connection",
     "name": "HTTP mode still lands the presets",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_arm", "arms": {"presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "wait_exec", "code": PRESETS_PROBE, "contains": '"landed": true', "budget": 30},
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 45},
     ]},
    {"id": "a11", "group": "connection",
     "name": "a corrupt frame fails the socket and the feed reconnects",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "sim_arm", "arms": {"corrupt_frame_once": True}},
         {"op": "wait_seconds", "seconds": 5},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 30},
     ]},
    # ─── printer status ───────────────────────────────────────
]
