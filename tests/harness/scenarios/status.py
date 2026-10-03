"""The the printer-state readouts and their availability gates scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import P_ANCHOR_ETA_READ, P_PAUSE_BLOCK_READ, PENGUIN_MONITOR_READ

SCENARIOS = [
    {"id": "b1", "group": "status", "name": "standby renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": "", "message": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 15},
     ]},
    {"id": "b2", "group": "status", "name": "printing renders its state word and progress",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.42, "file_size": 1048576}}},
         # The motion ticker (4.2.0): the rows' values must MOVE, not
         # just render — armed rates feed live_velocity and
         # live_extruder_velocity each push while printing.
         {"op": "sim_arm", "arms": {"motion_speed_mm_s": 60, "motion_e_mm_s": 0.8}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "4", "budget": 15},
         {"op": "wait_model", "prop": "monitorVelocity", "value": "60.0 mm/s", "budget": 15},
         {"op": "wait_model", "prop": "monitorFlowRate", "value": "1.9 mm³/s", "budget": 15},
         {"op": "wait_model", "prop": "monitorAccelLimit", "value": "5000 mm/s²", "budget": 15},
     ]},
    {"id": "b3", "group": "status", "name": "paused renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "paused", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
     ]},
    {"id": "b4", "group": "status", "name": "error renders its state word and message",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "error", "message": "sim failure"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "error", "budget": 15},
     ]},
    {"id": "b5", "group": "status", "name": "complete renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "complete", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "complete", "budget": 15},
     ]},
    {"id": "b6", "group": "status", "name": "cancelled renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "cancelled", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "cancel", "budget": 15},
     ]},
    {"id": "b7", "group": "status", "name": "progress tracks the peer's virtual_sdcard",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.75, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "7", "budget": 15},
     ]},
    {"id": "b8", "group": "status", "name": "position and layer render",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "info": {"total_layer": 40, "current_layer": 12}},
                                    "motion_report": {"live_position": [110.5, 90.25, 1.6, 0.0]}}},
         {"op": "wait_model", "prop": "monitorPosition", "contains": "110", "budget": 15},
         {"op": "wait_model", "prop": "monitorLayer", "contains": "1", "budget": 15},
     ]},
    {"id": "b9", "group": "status", "name": "filament totals render",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "filament_used": 3200.0}}},
         {"op": "wait_model", "prop": "filamentUsed", "contains": "3", "budget": 15},
     ]},
    {"id": "b10", "group": "status", "name": "the last-action row renders",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "actionStatus", "value": ""},
     ]},
    {"id": "b11", "group": "status",
     "name": "the exclude-object surface: empty, then armed, then the follower over it",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "plateHasObjects", "value": False},
         # The shared native/QML travel-width contract is a published,
         # read-only surface. Read it explicitly so the coverage matrix
         # has execution evidence rather than a bookkeeping-only entry.
         {"op": "assert_model", "prop": "followerTravelVisualRatio", "value": 0.7},
         # The armed half: Klipper's own exclude_object on the print's
         # lane, in Klipper's shape (name, centre, polygon) — the map,
         # the picker and the follower all read it. The resolved layer
         # rides print_stats.info; the index the follower needs is
         # built below from the picker's own download offer. The layer
         # clock is off so the physical layer holds still through the
         # run.
         {"op": "sim_set_current_print"},
         {"op": "sim_set", "state": {
             "print_stats": {"state": "printing", "filename": "scenario1.gcode",
                             "info": {"total_layer": 40, "current_layer": 20}},
             "exclude_object": {
                 "objects": [
                     {"name": "cube_a", "center": [100.0, 100.0],
                      "polygon": [[80.0, 80.0], [120.0, 80.0], [120.0, 120.0], [80.0, 120.0]]},
                     {"name": "cube_b", "center": [140.0, 100.0],
                      "polygon": [[120.0, 80.0], [160.0, 80.0], [160.0, 120.0], [120.0, 120.0]]}],
                 "excluded_objects": [], "current_object": None}}},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120, "layer_clock_interval_s": 0}},
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "plateHasObjects", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerPlateCanvas", "budget": 20},
         # The 90 px thumbnail is the picker's only opener; the face
         # the gestures live on is inside the card it opens.
         {"op": "deliver_click", "objectName": "moonrakerPlateCanvas"},
         {"op": "wait_rect", "objectName": "moonrakerPlateExcludeFace", "budget": 20},
         # The index the follower needs: the picker's own download row
         # (download + index, no preview) — the path that carries no
         # replace confirmation.
         {"op": "click_text", "text": "Download and index the print to track the printed state."},
         {"op": "wait_model", "prop": "plateLiveAvailable", "value": True, "budget": 90},
         # A press that lands off the open card is swallowed by its
         # outside-click layer, which closes it: the follower's own
         # thumbnail is off the card's box, so its first press pays
         # the dismissal and its second one opens the follower.
         {"op": "deliver_click", "objectName": "moonrakerPlateProgressFace"},
         {"op": "wait_rect", "objectName": "moonrakerPlateExcludeFace", "absent": True, "budget": 20},
         {"op": "deliver_click", "objectName": "moonrakerPlateProgressFace"},
         {"op": "wait_rect", "objectName": "moonrakerPlateProgressFace", "budget": 20},
         {"op": "wait_rect", "objectName": "moonrakerFollowerAttach", "budget": 20},
         # The plate's own face over the live print: the toolhead dot is
         # a physical position, and it stands while the follower follows
         # the print (a detach hides it).
         {"op": "wait_rect", "objectName": "moonrakerPlateToolheadDot", "budget": 20},
         {"op": "wait_model", "prop": "followerAttached", "value": True, "budget": 15},
         {"op": "wait_rect", "objectName": "moonrakerFollowerLayerSlider", "budget": 15},
         {"op": "wait_rect", "objectName": "moonrakerFollowerLayerReadout", "budget": 15},
         {"op": "wait_rendered", "objectName": "moonrakerFollowerLayerReadout", "contains": "/", "budget": 20},
         {"op": "wait_rect", "objectName": "moonrakerFollowerLayerProgress", "budget": 15},
         {"op": "wait_rect", "objectName": "moonrakerFollowerLayerProgressReadout", "budget": 15},
         {"op": "wait_rendered", "objectName": "moonrakerFollowerLayerProgressReadout", "contains": "%", "budget": 30},
         # The face's three render switches and the stroke width are
         # published state the checkboxes read back.
         {"op": "wait_model", "prop": "followerShowPrevious", "value": True, "budget": 15},
         {"op": "wait_model", "prop": "followerShowNext", "value": True, "budget": 15},
         {"op": "wait_model", "prop": "followerShowBase", "value": True, "budget": 15},
         {"op": "assert_model", "prop": "followerLineScale", "value": 1.0, "budget": 15},
         # The checkbox row's real input: "Travels" is the one label the
         # row does not share with the legend, so the press is
         # unambiguous — toggled on, read back, and set back off.
         {"op": "wait_model", "prop": "followerShowTravels", "value": False, "budget": 15},
         {"op": "click_text", "text": "Travels"},
         {"op": "wait_model", "prop": "followerShowTravels", "value": True, "budget": 15},
         {"op": "click_text", "text": "Travels"},
         {"op": "wait_model", "prop": "followerShowTravels", "value": False, "budget": 15},
         # The popover's pause block: the button is laid out with the
         # rest of the follower's controls, and the block behind it is
         # read off the live model so the nine published keys carry
         # execution evidence rather than a bookkeeping-only entry
         # (the travel-ratio precedent above).
         {"op": "wait_rect", "objectName": "moonrakerFollowerPauseButton", "budget": 15},
         {"op": "wait_exec", "code": P_PAUSE_BLOCK_READ, "contains": '"pauseAtLayerRead": true', "budget": 20},
         # The jump rides a view scale only the picture's wheel and
         # right-drag can raise, and the harness's input set carries
         # neither: at the fit it must be absent while the row-mate that
         # shares its visibility rules is present. Its zoomed half is
         # driven by the Qt suite (test_qml_plate_composition.py).
         {"op": "wait_rect", "objectName": "moonrakerFollowerJump", "absent": True, "budget": 5},
         # The detached anchor's ETA (the 5.0.0 request): the row
         # belongs to the detached mode, so the run detaches through
         # the popover's own toggle and waits for it. The fixture's
         # index may carry no timing for the anchor, which the row
         # renders as an em dash — the row and the model key are the
         # pins, not a particular number.
         {"op": "click_text", "text": "Detach"},
         {"op": "wait_model", "prop": "followerAttached", "value": False, "budget": 15},
         {"op": "wait_rect", "objectName": "moonrakerFollowerLayerEta", "budget": 15},
         {"op": "wait_exec", "code": P_ANCHOR_ETA_READ, "contains": '"anchorEtaRead": true', "budget": 20},
     ]},

    {"id": "b12", "group": "status",
     "name": "the smooth GPU follower draws the three-material penguin in twenty seconds",
     "steps": [
         {"op": "sim_arm", "arms": {"gcode_fixture": "penguin"}},
         {"op": "sim_set_current_print", "filename": "penguin.gcode"},
         {"op": "click_stage", "stage": "PrepareStage"},
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         # Before indexing, the mini canvas is fully transparent. Its
         # visible placeholder is the user's opener, not a hidden item.
         {"op": "click_text", "text": "The follower appears once the print's index is built \u2014 open the pop-over to build it."},
         {"op": "wait_rect", "objectName": "moonrakerFollowerAttach", "budget": 20},
         {"op": "click_text", "text": "Download and index this print to follow its progress."},
         {"op": "wait_model", "prop": "plateLiveAvailable", "value": True, "budget": 90},
         {"op": "wait_exec", "code": PENGUIN_MONITOR_READ, "contains": '"smooth_gpu": true', "budget": 30},
         {"op": "sim_arm", "arms": {"gcode_playback_s": 20}},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "wait_exec", "code": PENGUIN_MONITOR_READ, "contains": '"moving": true', "budget": 10},
         {"op": "wait_exec", "code": PENGUIN_MONITOR_READ, "contains": '"finished": true', "budget": 25},
         {"op": "wait_seconds", "seconds": 7},
         {"op": "wait_rendered", "objectName": "moonrakerFollowerLayerReadout", "contains": "3 / 3", "budget": 15},
         {"op": "wait_canvas_ink", "code": PENGUIN_MONITOR_READ, "minimum": 100, "budget": 10},
     ]},

    # ─── temperatures / fans / sensors ────────────────────────
]
