"""The the print lifecycle, pause scheduling and exclusions scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import CARD_GATE_PROBE

SCENARIOS = [
    {"id": "h2", "group": "printing", "name": "the load end-to-end from a fresh boot (the flow)",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorFilename", "contains": "scenario1", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120, "gcode_stream_hold": True}},
         {"op": "emit_click", "text": "Load current print"},
         # The prompt is the card's own dialog: the button is on screen
         # and pressed, the same way the rename confirm is.
         {"op": "wait_rect", "objectName": "moonrakerReplacePrompt", "budget": 30},
         # Three answers, each witnessed. Escape and Cancel are the
         # two ways to say no and neither may load (the load busy term
         # is the model's own word on it); Return is the way to say
         # yes. A prompt whose keyboard answers were never wired would
         # otherwise look identical to one that has them.
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "moonrakerReplacePrompt", "absent": True, "budget": 20},
         {"op": "assert_exec", "code": CARD_GATE_PROBE, "contains": '"loadBusy": false'},
         {"op": "emit_click", "text": "Load current print"},
         {"op": "wait_rect", "objectName": "moonrakerReplacePrompt", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerReplaceCancelButton"},
         {"op": "wait_rect", "objectName": "moonrakerReplacePrompt", "absent": True, "budget": 20},
         {"op": "assert_exec", "code": CARD_GATE_PROBE, "contains": '"loadBusy": false'},
         {"op": "emit_click", "text": "Load current print"},
         {"op": "wait_rect", "objectName": "moonrakerReplacePrompt", "budget": 30},
         {"op": "key_press", "key": "Return"},
         {"op": "wait_exec", "code": CARD_GATE_PROBE, "contains": '"loadBusy": true', "budget": 30},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "sim_arm", "arms": {"gcode_stream_hold": False}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
     ]},
    {"id": "h3", "group": "printing", "name": "the attach/detach surface renders",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 20},
     ]},
    {"id": "h6", "group": "printing", "name": "the bed mesh renders with its legend",
     "steps": [
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                  "mesh_max": [50.0, 50.0], "probed_matrix": [[0.0] * 3] * 3}}},
         # The first mesh of the boot arrives through the discovery
         # watchdog's re-subscribe sync (~4 s); the budget covers the
         # cold chain.
         {"op": "assert_model", "prop": "bedMeshAvailable", "value": True, "budget": 30},
         {"op": "assert_model", "prop": "bedMeshProfile", "contains": "sim-mesh", "budget": 10},
     ]},
    {"id": "h6b", "group": "printing", "name": "the probe-points toggle persists on the preference",
     "steps": [
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [True]},
         {"op": "assert_model", "prop": "showProbePoints", "value": True},
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [False]},
         {"op": "assert_model", "prop": "showProbePoints", "value": False},
     ]},
    {"id": "h6c", "group": "printing", "name": "the heightmap range filter window is shared",
     "steps": [
         # A non-flat mesh so the window has a real range to clamp into.
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                  "mesh_max": [50.0, 50.0],
                                                  "probed_matrix": [[0.0, 0.2, 0.5], [0.1, 0.3, 0.4], [0.2, 0.1, 0.3]]}}},
         {"op": "assert_model", "prop": "bedMeshAvailable", "value": True, "budget": 30},
         {"op": "exec_slot", "slot": "setBedMeshThresholds", "args": [0.1, 0.4]},
         {"op": "assert_model", "prop": "bedMeshThresholdLow", "value": 0.1},
         {"op": "assert_model", "prop": "bedMeshThresholdHigh", "value": 0.4},
         # The mini map lives on the MONITOR stage (the Preview card
         # hosts the range slider too — its witness would pass
         # without the pop-over ever opening). Enter the stage, then
         # click the map to open the pop-over.
         {"op": "click_stage", "stage": "MonitorStage"},
         # The stage's dashboard loads asynchronously — the map must
         # be ON SCREEN before the press (the same wait-then-click
         # a9 uses).
         {"op": "wait_rect", "objectName": "moonrakerBedMeshMap", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerBedMeshMap"},
         {"op": "wait_rect", "objectName": "moonrakerBedMeshRangeSlider", "budget": 30},
     ]},
    {"id": "h8", "group": "printing", "name": "the ETA opt-in pulls the print for the monitor",
     "steps": [
         # The improve's journey: the print changes to a file never
         # loaded into the preview, the opt-in pulls it through the
         # download lane, and the hourglass ends (the index landed).
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "delete-me.gcode"}}},
         # A streamed download keeps the resolve window observable on
         # 2-vCPU CI runners (the download route reads no route-delay
         # arms).
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 500}},
         {"op": "exec_slot", "slot": "improveEta", "args": []},
         {"op": "sim_ledger", "needle": "files/gcodes/delete-me.gcode", "field": "path", "min": 1, "budget": 30},
         # The hourglass's publication itself is pinned by the Qt
         # test (the registration-grace fix — the red run's product
         # catch); the live window's observation is a recorded
         # follow-up (the publish races the snapshot rebuild).
         {"op": "wait_model", "prop": "improvingEta", "value": False, "budget": 60},
     ]},

    # ─── settings & persistence ───────────────────────────────
]
