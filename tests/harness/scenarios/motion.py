"""The the toolhead controls and their interlocks scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import SCROLL_CONTROLS, POWER_STATUS_PROBE

SCENARIOS = [
    {"id": "g1", "group": "motion", "name": "the jog pad's clicks reach the peer as G1 moves",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         # The pad sits below the pane's fold: the press must land on
         # screen, never on a rendered-but-clipped aim (the s7 fix's
         # honest-refusal finding).
         {"op": "scroll_into_view", "objectName": "moonrakerJogXPlus"},
         # Every jog button takes a real press (the map names all six;
         # one representative press overclaimed the pad).
         {"op": "deliver_click", "objectName": "moonrakerJogXPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogXMinus"},
         {"op": "deliver_click", "objectName": "moonrakerJogYPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogYMinus"},
         {"op": "deliver_click", "objectName": "moonrakerJogZPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogZMinus"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g2", "group": "motion", "name": "home and the mesh actions reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerHomeX", "budget": 30},
         # The home row sits below the fold: same preposition as the
         # jog pad (the s7 fix's honest-refusal finding).
         {"op": "scroll_into_view", "objectName": "moonrakerHomeX"},
         # All three home buttons take a real press (the map names
         # all three).
         {"op": "deliver_click", "objectName": "moonrakerHomeX"},
         {"op": "deliver_click", "objectName": "moonrakerHomeY"},
         {"op": "deliver_click", "objectName": "moonrakerHomeZ"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g3", "group": "motion", "name": "the abs/rel toggle rides the command lane",
     "steps": [
         {"op": "exec_slot", "slot": "setPositionMode", "args": [False]},
         {"op": "assert_model", "prop": "positionMode", "value": "Relative"},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "g4", "group": "motion", "name": "extrude and retract reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_extrude"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g5", "group": "motion", "name": "macros render and refuse while printing",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "wait_model", "prop": "monitorFilename", "value": "scenario1.gcode", "budget": 15},
         {"op": "assert_model", "prop": "macroNames", "value": []},
     ]},
    {"id": "g6", "group": "motion", "name": "pause and resume ride the peer's print routes",
     "steps": [
         # Establish this unit's session memory explicitly. macroNames
         # can already be empty before g5's printing update is observed;
         # it is not proof that the model ever saw that print's filename.
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "wait_model", "prop": "monitorFilename", "value": "scenario1.gcode", "budget": 15},
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 15},
         {"op": "wait_model", "prop": "monitorFilename", "value": "", "budget": 15},
         # Clearing print_stats must retain that session-only restart target.
         {"op": "assert_model", "prop": "canRestartLastPrint", "value": True},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         # The policy gate (4.2.0): while printing the jog button
         # renders disabled (pause-first), the caption names the
         # mode, and the restart gate refuses.
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         {"op": "wait_rect", "objectName": "toolheadStatusCaption", "budget": 30},
         # The model-level polls settle the state first — the
         # rendered check then reads a frame that has already
         # published the denial (the re-review's D4 ordering).
         {"op": "assert_model", "prop": "jogReason", "contains": "pause first", "budget": 10},
         {"op": "assert_model", "prop": "canRestart", "value": False, "budget": 10},
         {"op": "assert_model", "prop": "canRestartLastPrint", "value": False},
         {"op": "item_disabled", "objectName": "moonrakerJogXPlus"},
         # The lane's revalidation (4.3.0): while PRINTING a Resume
         # refuses with the policy's words, no command leaves the
         # plugin, and the projected reasons name both rows.
         {"op": "exec_slot", "slot": "resumePrint", "args": []},
         {"op": "assert_model", "prop": "actionStatus", "contains": "Resume refused: Print is not paused", "budget": 10},
         {"op": "assert_model", "prop": "resumeReason", "value": "Print is not paused", "budget": 10},
         {"op": "assert_model", "prop": "resumeReasonDetail", "value": "Resume applies to a paused print — this print is still running.", "budget": 10},
         {"op": "assert_model", "prop": "pauseReason", "value": "", "budget": 10},
         {"op": "assert_model", "prop": "pauseReasonDetail", "value": "", "budget": 10},
         # The print-job caption reads the STATE word (4.3.0) — a
         # busy lane must never read as "Printing".
         {"op": "assert_model", "prop": "printJobCaption", "value": "Printing", "budget": 10},
         {"op": "exec_slot", "slot": "pausePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
         # Paused keeps the shipped caption — moves run immediately.
         {"op": "assert_model", "prop": "jogReason", "value": "Paused — moves run immediately", "budget": 10},
         {"op": "assert_model", "prop": "printJobCaption", "value": "Paused", "budget": 10},
         {"op": "assert_model", "prop": "canRestartLastPrint", "value": False},
         # The rows flip with the pause: the resume side opens, the
         # pause side names why it refuses.
         {"op": "assert_model", "prop": "resumeReason", "value": "", "budget": 10},
         {"op": "assert_model", "prop": "pauseReason", "value": "Print is already paused", "budget": 10},
         {"op": "exec_slot", "slot": "resumePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/resume", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "canPausePrint", "value": True, "budget": 15},
         {"op": "sim_set", "state": {"print_stats": {"state": "complete", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "canRestartLastPrint", "value": True, "budget": 15},
         {"op": "scroll_into_view", "text": "Restart last print"},
         {"op": "click_text", "text": "Restart last print"},
         {"op": "sim_ledger", "needle": "print/start", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "g8", "group": "motion", "name": "the lock toggle flips the controls lock",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": "", "message": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [True]},
         {"op": "assert_model", "prop": "controlsLocked", "value": True, "budget": 10},
         # The policy gate (4.2.0): the lock's reason reaches the
         # caption and the jog gate through the observation record.
         {"op": "assert_model", "prop": "jogEnabled", "value": False, "budget": 10},
         {"op": "assert_model", "prop": "jogReason", "value": "Controls locked", "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [False]},
         {"op": "assert_model", "prop": "controlsLocked", "value": False, "budget": 10},
         {"op": "assert_model", "prop": "jogEnabled", "value": True, "budget": 10},
     ]},
    {"id": "g9", "group": "motion", "name": "power devices list and toggle",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "sim-printer-power", "status": "off", "locked_while_printing": False}]}},
         {"op": "wait_model", "prop": "powerDevices", "contains": "sim-printer-power", "budget": 20},
         {"op": "wait_rect", "text": "Turn on", "budget": 40},
         {"op": "exec_code", "verbs": ['setProperty'], "code": SCROLL_CONTROLS},
         {"op": "emit_click", "text": "Turn on"},
         {"op": "wait_exec", "code": POWER_STATUS_PROBE, "contains": '"sim-printer-power=on"', "budget": 20},
         {"op": "sim_ledger", "needle": "device_power/device", "method": "POST", "min": 1},
     ]},
    {"id": "g9b", "group": "motion", "name": "firmware and host restarts reach the peer",
     "steps": [
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
     ]},

    # ─── preview ──────────────────────────────────────────────
]
