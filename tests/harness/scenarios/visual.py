"""The the rendered-surface checks and their geometry scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import CONTROLS_PROBE, LANE_CENSUS_PROBE, SCROLL_CONTROLS, P_TOGGLE_STATE, P_POWER_FLIP, STRIP_PAUSE_READY

SCENARIOS = [
    {"id": "v19", "group": "visual",
     "name": "the strip renders its cells and its pause routes the lane",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                     "extruder": {"temperature": 205.2, "target": 210.0},
                                     "heater_bed": {"temperature": 60.0, "target": 60.0}}},
         # The strip's three cells render with the card; the block
         # lands on the aux poll — the temps witness proves the block
         # arrived (and that the strip renders the WHOLE unlabelled
         # pair: hotend first, bed second — the elided-labelled-form
         # pin, the UX re-review). The surface witness waits on the
         # strip button, never the pane root: in the UNSLICED
         # printing state Cura hosts the pane root at zero size (its
         # children render fine), so the lookup's size filter skips
         # the root — the sliced v1 state resolves it.
         {"op": "wait_rect", "objectName": "moonrakerStripPauseButton", "budget": 150},
         {"op": "wait_rect", "objectName": "moonrakerStripTemps", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStripBed", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStripFinish", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerLayerReadout", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerHeightReadout", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStripSlot", "budget": 30},
         {"op": "wait_rendered", "objectName": "moonrakerStripTemps", "contains": "205.2 → 210.0 °C", "budget": 30},
         # The strip's own verdict lane settles BEFORE the click: the
         # block lands on the aux poll behind the model's verdicts, so
         # a press while the strip still reads the idle block hits a
         # disabled button (the press falls through to the stage's
         # background MouseArea — the gate's own failure). The wait
         # reads the BUTTON's enabled state, the exact gate the press
         # must pass.
         {"op": "wait_exec", "code": STRIP_PAUSE_READY, "contains": '"enabled": true', "budget": 15},
         # The strip's one control dispatches through the Monitor's
         # revalidated lane: while printing the button reads "Pause
         # print" and a real press sends the pause.
         {"op": "deliver_click", "objectName": "moonrakerStripPauseButton"},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "v1", "group": "visual",
     "name": "the loaded panel renders, with Cura's </> beside the card",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "insert_model"},
         {"op": "slice_scene"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 150},
         {"op": "exec_code", "verbs": [], "code": "from UM.Application import Application\napp = Application.getInstance()\nresult = {}\nfor e in app.getExtensions():\n    if \"MoonrakerPrintFollower\" in type(e).__name__:\n        rt = e._runtime\n        coord = rt.coordinator if hasattr(rt, \"coordinator\") else rt.follow\n        result[\"gate_logged\"] = repr(getattr(coord, \"_gate_logged\", \"n/a\"))\n        result[\"has_toolpath\"] = bool(getattr(rt.cura, \"has_toolpath\", False))\n        result[\"preview_active\"] = bool(getattr(rt.cura, \"preview_active\", False))\n        result[\"configured\"] = bool(getattr(getattr(rt.binding, \"configured\", None), \"__bool__\", lambda: False)())\n        try:\n            view = app.getController().getActiveView()\n            result[\"active_view\"] = str(view.getPluginId()) if view else None\n            result[\"max_layers\"] = int(view.getMaxLayers()) if view and hasattr(view, \"getMaxLayers\") else None\n        except Exception as exc:\n            result[\"view_err\"] = repr(exc)[:80]\n        break\n"},
         {"op": "add_post_script", "script": "PauseAtHeight"},
         {"op": "wait_rect", "objectName": "postProcessingSaveAreaButton", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "postProcessingSaveAreaButton"},
          "no_overlap": {"objectName": "moonrakerPreviewCard"}},
         {"op": "dump_visible", "needle": "", "region": [600, 520, 1280, 800]},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerPreviewCard"},
          "within": {"window": True}},
     ]},
    {"id": "v2", "group": "visual",
     "name": "the jog pad grid is straight",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerJogXPlus"},
          "anchor": {"objectName": "moonrakerJogXMinus"}, "axis": "center_y", "tol": 3},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerJogYPlus"},
          "anchor": {"objectName": "moonrakerJogYMinus"}, "axis": "center_x", "tol": 3},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerJogZPlus"},
          "anchor": {"objectName": "moonrakerJogZMinus"}, "axis": "center_x", "tol": 3},
     ]},
    {"id": "v3", "group": "visual",
     "name": "the console input and send button share their baseline",
     "steps": [
         {"op": "assert_aligned", "item": {"objectName": "moonrakerConsoleInput"},
          "anchor": {"objectName": "moonrakerConsoleSend"}, "axis": "center_y", "tol": 5},
     ]},
    {"id": "v4", "group": "visual",
     "name": "the info and status panels never overlap",
     "steps": [
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
     ]},
    {"id": "v5", "group": "visual",
     "name": "the M117 slot's rendered text follows the push",
     "steps": [
         {"op": "sim_set", "state": {"display_status": {"message": "RENDERED-A", "progress": 0.5}}},
         {"op": "wait_rendered", "objectName": "moonrakerM117Slot", "contains": "RENDERED-A", "budget": 30},
         {"op": "sim_set", "state": {"display_status": {"message": "RENDERED-B", "progress": 0.5}}},
         {"op": "wait_rendered", "objectName": "moonrakerM117Slot", "contains": "RENDERED-B", "budget": 30},
         {"op": "assert_rendered", "objectName": "moonrakerM117Slot", "not_contains": "RENDERED-A"},
     ]},
    {"id": "v6", "group": "visual",
     "name": "the temperature row's rendered text follows the push",
     "steps": [
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "wait_rendered", "objectName": "moonrakerTemperatureDetail", "contains": "210", "budget": 30},
     ]},
    {"id": "v7", "group": "visual",
     "name": "pane collapse and re-expand hide and restore the content",
     "steps": [
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "rect_of", "objectName": "infoPanel"},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [True]},
         {"op": "assert_rect_change", "objectName": "infoPanel", "direction": "shrunk", "by": 60, "budget": 15},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [False]},
         {"op": "assert_rect_change", "objectName": "infoPanel", "direction": "grown", "by": 60, "budget": 15},
         {"op": "exec_slot", "slot": "setSectionExpanded", "args": ["temps", False]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setSectionExpanded", "args": ["temps", True]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [False]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
     ]},
    {"id": "v8", "group": "visual",
     "name": "the console folds on its own column rule, the panes' fold hands the room back",
     "steps": [
         # The console's rule is its OWN column (< 350), never the
         # window: while the side panes hold their room, the
         # camera+console column is squeezed under it and the console
         # is what yields. 1420 sits mid-band (measured on the
         # dashboard mount: column 299, camera 277, well over its
         # comfort floor; the band's edges bracket it at 1340/1380 and
         # 1470/1480).
         {"op": "resize_window", "w": 1420, "h": 700},
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "absent": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerInfoContent", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStatusContent", "budget": 30},
         # Narrow enough that the camera cannot hold the panes: both
         # fold to their strips, and the column that fold frees is
         # over the console's threshold again — the console comes back
         # on its own rule, with no user click. The width is the
         # application's own floor, the narrowest a user can drag to:
         # 880 under Xvfb, 1040 on native (Windows always ran the
         # native one — the old 1000 was clamped to it there).
         {"op": "resize_window", "w": "min", "h": 700},
         {"op": "wait_rect", "objectName": "moonrakerInfoContent", "absent": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStatusContent", "absent": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
         {"op": "resize_window", "w": 1840, "h": 900},
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerInfoContent", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStatusContent", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
     ]},

    {"id": "v9", "group": "visual",
     "name": "the status text, the console and the pane margins follow reality",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_rendered", "objectName": "moonrakerStatusStateText", "contains": "Printing", "budget": 30},
         {"op": "exec_slot", "slot": "sendConsoleCommand", "args": ["RENDERED-CONSOLE"]},
         {"op": "wait_rendered", "objectName": "moonrakerConsoleOutput", "contains": "RENDERED-CONSOLE", "budget": 30},
         {"op": "assert_aligned", "symmetric_margins": {"left": {"objectName": "infoPanel"},
                                                        "right": {"objectName": "moonrakerControlsPane"}},
          "tol": 8},
     ]},
    {"id": "v10", "group": "visual",
     "name": "a renamed file's row follows in the rendered list",
     "steps": [
         {"op": "exec_slot", "slot": "openFileManager", "args": []},
         {"op": "exec_slot", "slot": "fileRequestRename", "args": ["benchy.gcode"]},
         {"op": "exec_slot", "slot": "filePreviewRename", "args": ["benchy-renamed.gcode"]},
         # The confirm is the dialog's own verb, pressed on screen: a
         # slot call leaves the modal popup open, and its scrim then
         # covers every later scenario in the leg — clicks a user
         # could not make, over a recording that never moved. The
         # witness/absent pair keeps the absence non-vacuous.
         {"op": "wait_rect", "text": "Rename file", "budget": 30},
         {"op": "deliver_click", "objectName": "renameConfirmButton"},
         {"op": "wait_rect", "text": "Rename file", "absent": True, "budget": 30},
         {"op": "wait_model", "prop": "fileManagerRows", "contains": "benchy-renamed", "budget": 30},
         {"op": "wait_rendered", "objectName": "moonrakerFileRowName", "any": True,
          "contains": "benchy-renamed", "budget": 30},
         # The manager is a full-area page: the next scenario's panes
         # must be the ones on screen, and its own Close button is
         # the visible way out.
         {"op": "deliver_click", "objectName": "fileManagerCloseButton"},
     ]},
    {"id": "v11", "group": "visual",
     "name": "the file manager's narrow mode hides the search field, wide restores it",
     "steps": [
         {"op": "exec_slot", "slot": "openFileManager", "args": []},
         # The application's own floor for the width — the narrowest a
         # user can drag to, each platform's own value.
         {"op": "resize_window", "w": "min", "h": 700},
         {"op": "wait_rect", "objectName": "moonrakerFileSearch", "absent": True, "budget": 30},
         {"op": "resize_window", "w": 1840, "h": 900},
         {"op": "wait_rect", "objectName": "moonrakerFileSearch", "budget": 30},
         # The page leaves with the scenario: the rest of the leg
         # asserts the panes behind it.
         {"op": "deliver_click", "objectName": "fileManagerCloseButton"},
     ]},
    {"id": "v12", "group": "visual",
     "name": "the bed mesh view renders in the information pane",
     "steps": [
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                 "mesh_max": [50.0, 50.0],
                                                 "probed_matrix": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]}}},
         {"op": "wait_rect", "objectName": "moonrakerBedMeshMap", "budget": 30},
     ]},


    {"id": "v14", "group": "visual",
     "name": "the DFU toggle clicks through and the locked device refuses",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "DFU", "status": "on", "locked_while_printing": False},
             {"device": "Printer", "status": "off", "locked_while_printing": True}]}},
         {"op": "wait_rect", "text": "Turn off", "budget": 40},
         # The visible-interactions pattern for scrolled content:
         # bring the control into the rendered viewport (with the
         # containment asserted). The switch's clickable region is a
         # custom component whose class the promotion misses — the
         # toggle stays a declared emit for now (the switch
         # addressing follow-up, recorded in DECISIONS).
         {"op": "scroll_into_view", "text": "Turn off"},
         {"op": "emit_click", "text": "Turn off"},
         {"op": "wait_exec", "code": P_POWER_FLIP, "contains": '"status": "off"', "budget": 20},
         {"op": "sim_ledger", "needle": "device_power/device", "method": "POST", "min": 1},
     ]},
    {"id": "v15", "group": "visual",
     "name": "a locked power device disables its toggle while a print runs",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "DFU", "status": "on", "locked_while_printing": False},
             {"device": "Printer", "status": "off", "locked_while_printing": True}]}},
         {"op": "sim_set_current_print"},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         {"op": "wait_rect", "text": "Turn off", "budget": 40},
         {"op": "exec_code", "verbs": ['setProperty'], "code": SCROLL_CONTROLS},
         {"op": "assert_exec", "code": P_TOGGLE_STATE, "contains": '"enabled": [false, true]'},
     ]},
    {"id": "v17", "group": "visual",
     "name": "the data-render census: every data class renders its control",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {
             "power_devices": [
                 {"device": "DFU", "status": "on", "locked_while_printing": False},
                 {"device": "Printer", "status": "off", "locked_while_printing": True}],
             "presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "sim_set", "state": {"extruder": {"temperature": 180.0, "target": 200.0},
                                     "heater_bed": {"temperature": 55.0, "target": 60.0}}},
         {"op": "census", "budget": 40},
     ]},

    {"id": "v18", "group": "visual",
     "name": "the firmware restart reaches the host and heals the feed",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "exec_code", "verbs": ['setProperty'], "code": SCROLL_CONTROLS},
         {"op": "wait_rect", "objectName": "moonrakerFirmwareRestart", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerHostRestart", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerKlipperRestart", "budget": 30},
         {"op": "emit_click", "text": "Firmware restart"},
         {"op": "sim_ledger", "needle": "firmware_restart", "field": "path", "min": 1, "budget": 20},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 40},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
     ]},
    # ─── the preview, end to end (the comprehensive list) ───
    {"id": "v13", "group": "visual",
     "name": "the power rows render the armed devices and keep clear of the scrollbar lane",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "DFU", "status": "on", "locked_while_printing": False},
             {"device": "Printer", "status": "off", "locked_while_printing": True}]}},
         {"op": "wait_rect", "text": "Turn off", "budget": 40},
         {"op": "wait_exec", "code": CONTROLS_PROBE, "contains": '"clear": true', "budget": 20},
         {"op": "dump_visible", "needle": "Turn", "region": [800, 0, 1280, 720]},
     ]},

    {"id": "v16", "group": "visual",
     "name": "the lane census: every polled category's data lands in the snapshot",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {
             "power_devices": [
                 {"device": "DFU", "status": "on", "locked_while_printing": False},
                 {"device": "Printer", "status": "off", "locked_while_printing": True}],
             "presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "wait_exec", "code": LANE_CENSUS_PROBE, "contains": '"healthy": true', "budget": 30},
     ]},

]
