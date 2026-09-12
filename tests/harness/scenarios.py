"""The suite scenario specs (TESTING.md 3, the full functional
surface). Each spec is a short composition of the step vocabulary the
gates established — the runner's suite mode executes one group per
boot with a simulator reset between scenarios. The coverage gate
(tests/test_coverage.py) proves every surface maps here.
"""
from __future__ import annotations

SCENARIOS = [
    # ─── A: transport & connection ───────────────────────────────
    {"id": "a1", "group": "a", "name": "the ws dot is green only after the first accepted snapshot",
     "steps": [
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
     ]},
    {"id": "a2", "group": "a", "name": "HTTP mode carries the full feed",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a3", "group": "a", "name": "the mode switch back to websocket",
     "steps": [
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "assert_mode", "mode": "websocket"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 45},
     ]},
    {"id": "a4", "group": "a", "name": "silence past the proof window degrades to HTTP",
     "steps": [
         {"op": "sim_arm", "arms": {"subscribe_hold_ms": 12000}},
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "objects", "min": 1, "budget": 40},
     ]},
    {"id": "a5", "group": "a", "name": "a subscribe refusal degrades to HTTP with the reason",
     "steps": [
         {"op": "sim_arm", "arms": {"refuse_subscribe": "simulated refusal"}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "connectionDetail", "contains": "refused", "budget": 30},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a6", "group": "a", "name": "a 401 surfaces the key-rejection verdict",
     "steps": [
         {"op": "sim_arm", "arms": {"require_api_key": True}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "connectionDetail", "contains": "unauthorized", "budget": 30},
     ]},
    {"id": "a7", "group": "a", "name": "klippy ready re-arms the subscription",
     "steps": [
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a8", "group": "a", "name": "a dropped connection reconnects on its own",
     "steps": [
         {"op": "sim_drop"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 120},
     ]},
    {"id": "a9", "group": "a", "name": "disconnected disables every control",
     "steps": [
         {"op": "sim_arm", "arms": {"refuse_subscribe": "down", "require_api_key": True}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "monitorConnected", "value": False, "budget": 30},
         {"op": "assert_model", "prop": "jogEnabled", "value": False},
     ]},

    # ─── B: printer status ───────────────────────────────────────
    {"id": "b1", "group": "b", "name": "standby renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": "", "message": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 15},
     ]},
    {"id": "b2", "group": "b", "name": "printing renders its state word and progress",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.42, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "4", "budget": 15},
     ]},
    {"id": "b3", "group": "b", "name": "paused renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "paused", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
     ]},
    {"id": "b4", "group": "b", "name": "error renders its state word and message",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "error", "message": "sim failure"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "error", "budget": 15},
     ]},
    {"id": "b5", "group": "b", "name": "complete renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "complete", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "complete", "budget": 15},
     ]},
    {"id": "b6", "group": "b", "name": "cancelled renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "cancelled", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "cancel", "budget": 15},
     ]},
    {"id": "b7", "group": "b", "name": "progress tracks the peer's virtual_sdcard",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.75, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "7", "budget": 15},
     ]},
    {"id": "b8", "group": "b", "name": "position and layer render",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "info": {"total_layer": 40, "current_layer": 12}},
                                    "motion_report": {"live_position": [110.5, 90.25, 1.6, 0.0]}}},
         {"op": "wait_model", "prop": "monitorPosition", "contains": "110", "budget": 15},
         {"op": "wait_model", "prop": "monitorLayer", "contains": "1", "budget": 15},
     ]},
    {"id": "b9", "group": "b", "name": "filament totals render",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "filament_used": 3200.0}}},
         {"op": "wait_model", "prop": "filamentUsed", "contains": "3", "budget": 15},
     ]},
    {"id": "b10", "group": "b", "name": "the last-action row renders",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "actionStatus", "value": ""},
     ]},
    {"id": "b11", "group": "b", "name": "the exclude-object surface stays stable when absent",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "excludeObjectItems", "value": []},
     ]},

    # ─── C: temperatures / fans / sensors ────────────────────────
    {"id": "c1", "group": "c", "name": "the temperature targets render",
     "steps": [
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0},
                                    "heater_bed": {"temperature": 55.0, "target": 60.0}}},
         {"op": "wait_sim", "path": "extruder.target", "value": 210.0, "budget": 15},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "Hotend", "budget": 15},
     ]},
    {"id": "c2", "group": "c", "name": "the fan and speed surfaces render",
     "steps": [
         {"op": "sim_set", "state": {"fan": {"speed": 0.4}}},
         {"op": "wait_sim", "path": "fan.speed", "value": 0.4, "budget": 15},
         {"op": "assert_model", "prop": "speedFactorPercent", "value": 100},
     ]},
    {"id": "c3", "group": "c", "name": "the sensor visibility toggles persist",
     "steps": [
         {"op": "exec_slot", "slot": "setTemperatureSensorVisible", "args": ["extruder", True]},
         {"op": "assert_model", "prop": "temperatureItems", "contains": "Hotend", "budget": 10},
     ]},
    {"id": "c4", "group": "c", "name": "runout and MCU sensors render their absence",
     "steps": [
         {"op": "assert_model", "prop": "mcuSummary", "value": "\u2014"},
     ]},
    {"id": "c5", "group": "c", "name": "endstops render from the peer's query",
     "steps": [
         {"op": "wait_model", "prop": "endstopSummary", "contains": "Not homed", "budget": 45},
     ]},

    # ─── D: console ──────────────────────────────────────────────
    {"id": "d1", "group": "d", "name": "a sent command's response renders",
     "steps": [
         {"op": "exec_console", "text": "M105"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "d3", "group": "d", "name": "clear empties the console",
     "steps": [
         {"op": "sim_set", "state": {"console_lines": [{"type": "response", "message": "// line %d" % i,
                                                       "time": 1.0} for i in range(10)]}},
         {"op": "exec_slot", "slot": "clearConsoleHistory", "args": []},
         {"op": "assert_model", "prop": "consoleHistory", "value": []},
     ]},
    {"id": "d5", "group": "d", "name": "the console resize commits through the model",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_slot", "slot": "setConsoleExpanded", "args": [True]},
         {"op": "exec_slot", "slot": "setConsoleHeight", "args": [240]},
         {"op": "assert_model", "prop": "consoleHeight", "value": 240, "budget": 10},
     ]},

    # ─── E: camera ───────────────────────────────────────────────
    {"id": "e1", "group": "e", "name": "the camera loads on its own (the discovery-cycle fix)",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_stream_start"},
         {"op": "assert_model", "prop": "cameraName", "contains": "sim-cam", "budget": 30},
     ]},
    {"id": "e2", "group": "e", "name": "the webcam selector lists the peer's cameras",
     "steps": [
         {"op": "assert_model", "prop": "webcamNames", "contains": "sim-cam", "budget": 30},
     ]},

    # ─── F: files & print start ──────────────────────────────────
    {"id": "f1", "group": "f", "name": "the file manager browses the simulated store",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "f2", "group": "f", "name": "the upload flow reaches the peer",
     "steps": [
         {"op": "write_fixture", "path": "/tmp/mpf/scenario-upload.gcode"},
         {"op": "exec_file_slot", "slot": "fileUpload", "args": ["/tmp/mpf/scenario-upload.gcode"]},
         {"op": "sim_ledger", "needle": "files/upload", "min": 1, "budget": 30},
     ]},
    {"id": "f3", "group": "f", "name": "delete removes the row after the confirm",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_file_slot", "slot": "openFileManager", "args": []},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_file_slot", "slot": "fileRequestDeleteFile", "args": ["scenario1.gcode"]},
         {"op": "exec_file_slot", "slot": "fileConfirmDelete", "args": []},
         {"op": "sim_ledger", "needle": "gcodes/scenario1.gcode", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "f4", "group": "f", "name": "folder create reaches the peer's directory route",
     "steps": [
         {"op": "exec_file_slot", "slot": "fileCreateDirectory", "args": ["simdir"]},
         {"op": "sim_ledger", "needle": "files/directory", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "f5", "group": "f", "name": "rename confirms into the peer's move route",
     "steps": [
         {"op": "exec_file_slot", "slot": "fileRequestRename", "args": ["benchy.gcode"]},
         {"op": "exec_file_slot", "slot": "filePreviewRename", "args": ["benchy-renamed.gcode"]},
         {"op": "exec_file_slot", "slot": "fileConfirmRename", "args": []},
         {"op": "sim_ledger", "needle": "files/move", "min": 1, "budget": 20},
     ]},
    {"id": "f6", "group": "f", "name": "print confirm arms the start verdict",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_file_slot", "slot": "openFileManager", "args": []},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_file_slot", "slot": "fileRequestPrint", "args": ["benchy.gcode"]},
         {"op": "exec_file_slot", "slot": "fileConfirmPrint", "args": []},
         {"op": "sim_ledger", "needle": "print/start", "method": "POST", "min": 1, "budget": 20},
     ]},

    # ─── G: controls ─────────────────────────────────────────────
    {"id": "g1", "group": "g", "name": "the jog pad's clicks reach the peer as G1 moves",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "click_jog", "button": "moonrakerJogXPlus"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g2", "group": "g", "name": "home and the mesh actions reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "click_jog", "button": "moonrakerHomeX"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g3", "group": "g", "name": "the abs/rel toggle rides the command lane",
     "steps": [
         {"op": "exec_slot", "slot": "setPositionMode", "args": [False]},
         {"op": "assert_model", "prop": "positionMode", "value": "Relative"},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "g4", "group": "g", "name": "extrude and retract reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_extrude"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g5", "group": "g", "name": "macros render and refuse while printing",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "macroNames", "value": []},
     ]},
    {"id": "g6", "group": "g", "name": "pause and resume ride the peer's print routes",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "exec_slot", "slot": "pausePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
         {"op": "exec_slot", "slot": "resumePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/resume", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "g8", "group": "g", "name": "the lock toggle flips the controls lock",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": "", "message": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [True]},
         {"op": "assert_model", "prop": "controlsLocked", "value": True, "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [False]},
         {"op": "assert_model", "prop": "controlsLocked", "value": False, "budget": 10},
     ]},
    {"id": "g9", "group": "g", "name": "power devices list and toggle",
     "steps": [
         {"op": "sim_set", "state": {"power_devices": [{"device": "sim-printer-power", "status": "off"}]}},
         {"op": "assert_model", "prop": "powerDevices", "value": []},
     ]},
    {"id": "g9b", "group": "g", "name": "firmware and host restarts reach the peer",
     "steps": [
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
     ]},

    # ─── H: preview ──────────────────────────────────────────────
    {"id": "h2", "group": "h", "name": "the load end-to-end (the gate flow's surface)",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorFilename", "contains": "scenario1", "budget": 30},
     ]},
    {"id": "h3", "group": "h", "name": "the attach/detach surface renders",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 20},
     ]},
    {"id": "h6", "group": "h", "name": "the bed mesh renders with its legend",
     "steps": [
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                  "mesh_max": [50.0, 50.0], "probed_matrix": [[0.0] * 3] * 3}}},
         {"op": "assert_model", "prop": "bedMeshAvailable", "value": True, "budget": 10},
         {"op": "assert_model", "prop": "bedMeshProfile", "contains": "sim-mesh", "budget": 10},
     ]},
    {"id": "h6b", "group": "h", "name": "the probe-points toggle persists on the preference",
     "steps": [
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [True]},
         {"op": "assert_model", "prop": "showProbePoints", "value": True},
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [False]},
         {"op": "assert_model", "prop": "showProbePoints", "value": False},
     ]},
    {"id": "h8", "group": "h", "name": "the ETA opt-in starts the hourglass",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "exec_slot", "slot": "improveEta", "args": []},
         {"op": "wait_model", "prop": "improvingEta", "value": True, "budget": 10},
     ]},

    # ─── I: settings & persistence ───────────────────────────────
    {"id": "i1", "group": "i", "name": "the transport mode applies with its reason line",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "assert_mode", "mode": "websocket"},
     ]},
    {"id": "i2", "group": "i", "name": "test connection probes the peer over the wire",
     "steps": [
         {"op": "exec_test_connection"},
         {"op": "sim_ledger", "needle": "server/info", "min": 1, "budget": 15},
         {"op": "sim_ledger", "needle": "objects/list", "min": 1, "budget": 15},
     ]},
    {"id": "i3", "group": "i", "name": "the cadence validators bound at 250 ms",
     "steps": [
         {"op": "exec_validator", "validator": "validPollInterval", "args": [100], "expect": False},
         {"op": "exec_validator", "validator": "validPollInterval", "args": [1000], "expect": True},
         {"op": "exec_validator", "validator": "validRetryInterval", "args": [0.05], "expect": False},
     ]},
    {"id": "i4", "group": "i", "name": "the camera config persists",
     "steps": [
         {"op": "exec_slot", "slot": "selectWebcam", "args": [0]},
         {"op": "assert_model", "prop": "activeWebcamIndex", "value": 0, "budget": 15},
     ]},
    {"id": "i6", "group": "i", "name": "save config sends SAVE_CONFIG when the peer has pending changes",
     "steps": [
         {"op": "sim_set", "state": {"configfile": {"save_config_pending": True, "save_config_pending_items": {}}}},
         {"op": "wait_sim", "path": "configfile.save_config_pending", "value": True, "budget": 10},
         {"op": "exec_slot", "slot": "saveConfig", "args": []},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "i7", "group": "i", "name": "open frontend hands off to the browser",
     "steps": [
         {"op": "exec_slot", "slot": "openFrontend", "args": []},
     ]},

    # ─── J: soaks & faults ───────────────────────────────────────
    {"id": "j2", "group": "j", "name": "a slow endpoint degrades, not hangs",
     "steps": [
         {"op": "sim_arm", "arms": {"route_delay_ms": {"server/gcode_store": 2000}}},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 20},
     ]},
    {"id": "j3", "group": "j", "name": "a socket close recovers without data loss",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "info": {"total_layer": 40, "current_layer": 8}}}},
         {"op": "sim_drop"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
     ]},
    {"id": "j4", "group": "j", "name": "a long print completes with time-warped progress",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.98, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "9", "budget": 15},
         {"op": "sim_set", "state": {"print_stats": {"state": "complete", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "complete", "budget": 15},
     ]},
]
