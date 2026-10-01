"""The the boot-to-usable legs every platform runs scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import LANE_CENSUS_PROBE, CAM_FRAMES, CAM_STARTED, E_STOP_SEQUENCE, CARD_EXCLUSIVE_PROBE, CARD_GATE_PROBE, P_SLIDER_DRAG, P_SLIDER_CLICK, P_FOLLOW_READ

SCENARIOS = [
    {"id": "s1", "group": "smoke", "name": "connect: the websocket reaches the peer and the state lands",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 30},
     ]},
    {"id": "s2", "group": "smoke", "name": "dashboard: the Monitor renders and the data census passes",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {
             "power_devices": [
                 {"device": "DFU", "status": "on", "locked_while_printing": False},
                 {"device": "Printer", "status": "off", "locked_while_printing": True}],
             "presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "wait_exec", "code": LANE_CENSUS_PROBE, "contains": '"healthy": true', "budget": 30},
         {"op": "census", "budget": 40},
     ]},
    {"id": "s3", "group": "smoke", "name": "preview card: the locked dual-host behaviour, exclusive in every state",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "exec_code", "verbs": [], "code": CARD_GATE_PROBE},
         {"op": "assert_exec", "code": CARD_EXCLUSIVE_PROBE,
          "contains": '"exclusive": true, "panel": false, "overlay": true'},
         {"op": "insert_model"},
         {"op": "wait_seconds", "seconds": 4},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 60},
         {"op": "assert_exec", "code": CARD_EXCLUSIVE_PROBE,
          "contains": '"exclusive": true, "panel": true, "overlay": false'},
     ]},
    {"id": "s4", "group": "smoke", "name": "follow: attach, load the current print, and a slider drag detaches",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set_current_print"},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120, "gcode_stream_hold": True}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         # The load's own precondition: the plugin's activity gate reads
         # the OBSERVED print (a load request is dropped while the lane
         # sees no active print), and sim_set_current_print only moves
         # the sim. A slow feed then turns the button press into a
         # no-op whose only trace is a missing indicator — the printing
         # group's h2 waits for the same filename before its press.
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_model", "prop": "monitorFilename", "contains": "scenario1", "budget": 30},
         {"op": "emit_click", "text": "Load current print"},
         # The prompt is the card's own dialog: the button is on screen
         # and pressed, the same way the rename confirm is.
         {"op": "wait_rect", "objectName": "moonrakerReplaceConfirmButton", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerReplaceConfirmButton"},
         # And the plugin's own load gate, so a load that never engages
         # fails here with the gate readout instead of 30 s later on the
         # indicator (h2's assertion).
         {"op": "wait_exec", "code": CARD_GATE_PROBE, "contains": '"loadBusy": true', "budget": 30},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "sim_arm", "arms": {"gcode_stream_hold": False}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "wait_seconds", "seconds": 5},
         # 5.11's view rebuilds its layer data only on stage re-entry:
         # the plugin's load lands while Preview is already open, so the
         # rendered path and the layer slider stay hidden until the stage
         # is left and re-entered (the z20 walk-dump). The drag premise
         # then holds.
         {"op": "click_stage", "stage": "PrepareStage", "version_only": ["5.11"]},
         {"op": "wait_seconds", "seconds": 2, "version_only": ["5.11"]},
         {"op": "click_stage", "stage": "PreviewStage", "version_only": ["5.11"]},
         {"op": "wait_seconds", "seconds": 3, "version_only": ["5.11"]},
         {"op": "exec_code", "verbs": ['mouseMove'], "code": P_SLIDER_DRAG},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
     ]},
    {"id": "s5", "group": "smoke", "name": "camera: the stream starts and renders frames, direct and bridged",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_exec", "code": CAM_STARTED, "contains": '"started": true', "budget": 40},
         {"op": "sim_arm", "arms": {"webcam_bridged": True}},
         {"op": "exec_slot", "slot": "refreshWebcams", "args": []},
         {"op": "wait_exec", "code": CAM_FRAMES, "contains": '"width": 320', "budget": 60},
     ]},
    {"id": "s6", "group": "smoke", "name": "print state: pause/resume ride the lane and M117 renders",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         # The press rides the button's OWN enable binding — a disabled
         # QML item is skipped by the hit test, so an early press falls
         # through to the Flickable and reports a refusal with no reason
         # in it. Wait for the term the binding reads.
         {"op": "wait_model", "prop": "canPausePrint", "value": True, "budget": 20},
         {"op": "model_read", "prop": "pauseReason"},
         {"op": "click_text", "text": "Pause"},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
         {"op": "wait_model", "prop": "canResumePrint", "value": True, "budget": 20},
         {"op": "model_read", "prop": "resumeReason"},
         {"op": "click_text", "text": "Resume"},
         {"op": "sim_ledger", "needle": "print/resume", "method": "POST", "min": 1, "budget": 20},
         {"op": "sim_set", "state": {"display_status": {"message": "RENDERED-A", "progress": 0.5}}},
         {"op": "wait_rendered", "objectName": "moonrakerM117Slot", "contains": "RENDERED-A", "budget": 30},
     ]},
    {"id": "s7", "group": "smoke", "name": "commands: the lane round-trips and the e-stop and restart heal",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         # wait_rect proves presence in the rendered tree, and
         # mapToScene reports through clipping: the controls pane is
         # taller than its Flickable's viewport, so an unscrolled press
         # at the pad's scene centre lands below the window — empty
         # space. Scroll it into view first, as the fans leg does.
         {"op": "scroll_into_view", "objectName": "moonrakerJogXPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogXPlus"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_console", "text": "M105"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 2, "budget": 20},
         {"op": "exec_code", "verbs": ['emergencyStopClick', 'emergencyHoldStarted'], "code": E_STOP_SEQUENCE},
         {"op": "sim_ledger", "needle": "emergency_stop", "min": 1, "budget": 20},
         # The shutdown rides the klippyLost path (a connection loss),
         # not a klippyState value; the observable is the print state
         # flipping to error with the stop's message.
         {"op": "wait_model", "prop": "monitorState", "contains": "error", "budget": 20},
         # The shipped error-state row (4.2.0, explicit): the e-stop's
         # recovery path — error ALLOWS the restart, the assumption
         # blocks until the cycle.
         {"op": "assert_model", "prop": "canRestart", "value": True},
         # The refusal's own words, so a restart that never fires is
         # named here rather than inferred from an empty ledger.
         {"op": "model_read", "prop": "restartReason"},
         {"op": "exec_slot", "slot": "emergencyHoldReleased", "args": []},
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
     ]},
    {"id": "s8", "group": "smoke", "name": "a track click on a plugin slider moves AND commits",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": ""},
                                     "fan": {"speed": 0.5}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         # The fans section renders a writable slider row from the
         # sim's fan object; the probe clicks the TRACK (clear of the
         # handle) — the live report: the handle moved and the commit
         # never fired (drag+release and keyboard worked; a click
         # submitted nothing). The fans section sits below the fold
         # of the dashboard's scroll — the click must land like a
         # human's would: scrolled into view, then pressed.
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         # The row's enable binding: the fans section is disabled while
         # the controls are locked, and a disabled track swallows the
         # press without committing anything.
         {"op": "wait_model", "prop": "controlsLocked", "value": False, "budget": 20},
         {"op": "scroll_into_view", "objectName": "moonrakerFansSection"},
         {"op": "exec_code", "verbs": ["mouseClick"], "code": P_SLIDER_CLICK},
         # One track click commits exactly ONCE (the UX re-review:
         # a minimum passes a duplicate commit silently) — and the
         # peer's fan lands on the clicked 20% (the moved value
         # travelled; a moved handle with no commit is the live bug).
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "max": 1, "budget": 20},
         {"op": "wait_sim", "path": "fan.speed", "value": 0.2, "budget": 15},
     ]},

    # ─── geometry probes (diagnostics, not release gates) ─────────
]
