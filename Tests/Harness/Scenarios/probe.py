"""The the geometry diagnostics the evidence calibration reads scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import RECT_PROBE, FM_POPUP_PROBE, FM_BUTTON_PROBE, SETTINGS_PROBE, SCENE_PROBE, WHATS_NEW_CLEAR_CODE, VERDICT_SCAN, CARD_EXCLUSIVE_PROBE, CARD_GATE_PROBE

SCENARIOS = [
    {"id": "z9", "group": "probe",
     "name": "the card's visibility terms across the idle and loaded states",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "exec_code", "verbs": [], "code": CARD_GATE_PROBE},
         {"op": "assert_exec", "code": CARD_EXCLUSIVE_PROBE,
          "contains": '"exclusive": true, "panel": false, "overlay": true'},
         {"op": "insert_model"},
         {"op": "wait_seconds", "seconds": 4},
         {"op": "exec_code", "verbs": [], "code": CARD_GATE_PROBE},
         {"op": "assert_exec", "code": CARD_EXCLUSIVE_PROBE,
          "contains": '"exclusive": true, "panel": true, "overlay": false'},
     ]},
    # The settings-dialog discovery probe (the round-2 HIGH-8): the
    # configuration page is a Cura.MachineAction with zero objectNames
    # and no scenario has ever opened it. This answers the two
    # questions that gate its scenarios: what the manager's activation
    # API is, and whether the page's items live in the main window's
    # tree. The dump lands in the scratch dir.
    {"id": "z12", "group": "probe",
     "name": "the settings page's activation API and its walkable content",
     "steps": [
         {"op": "click_stage", "stage": "PrepareStage"},
         {"op": "exec_code", "verbs": [], "code": SETTINGS_PROBE},
     ]},
    {"id": "z13", "group": "probe",
     "name": "the File-manager button's text match and its parent chain",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "wait_seconds", "seconds": 2},
         {"op": "exec_code", "verbs": [], "code": FM_BUTTON_PROBE},
     ]},
    # The red run (the acceptance's red-run proof): the broken-start
    # journey against a printer that stays broken — the failure
    # verdict window must fire, recorded as the expected red.
    {"id": "z14", "group": "probe",
     "name": "the red run — a broken start fires the honest failure verdict (expected red)",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "sim_arm", "arms": {"cold_start": True, "extruder_ramp_deg_s": 30.0,
                                    "broken_start": True}},
         {"op": "exec_file_slot", "slot": "fileRequestPrint", "args": ["scenario1.gcode"]},
         {"op": "deliver_click", "objectName": "printConfirmStartButton"},
         {"op": "sim_ledger", "needle": "print/start", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_sim", "path": "print_stats.state", "value": "error", "budget": 20},
         {"op": "wait_exec", "code": VERDICT_SCAN,
          "contains": "reported an error", "budget": 40, "expect_red": True},
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
     ]},
    # The overlay half of the four-part criterion: the delete dialog's
    # dimmer covers the pane, so a press aimed at the search box must
    # resolve but be refused — while the dialog's own verbs still
    # accept (the criterion's control).
    {"id": "z15", "group": "probe",
     "name": "the overlay half of the proof — a dimmer refuses a press aimed at a covered control",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_file_slot", "slot": "fileRequestDeleteFile", "args": ["delete-me.gcode"]},
         {"op": "deliver_click", "objectName": "moonrakerFileSearch", "expect": "not_accepted"},
         {"op": "deliver_click", "objectName": "deleteConfirmDeleteButton"},
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
     ]},
    # The what's-new overlay's lifecycle (the ruling: it must
    # show once per version and dismiss by Esc, an outside press, and
    # the Close button). The seeded profile carries the marker, so the
    # suite never sees the popup unless a scenario asks — this one
    # clears the marker and runs the startup check for real.
    {"id": "z16", "group": "probe",
     "name": "the what's-new overlay — the offer, three dismissals, the marker",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_code", "verbs": [], "code": WHATS_NEW_CLEAR_CODE,
          "stash": "whatsnew"},
         {"op": "exec_slot", "slot": "checkWhatsNew", "args": []},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "budget": 20},
         # Dismissal one: Esc, IMMEDIATELY — the first key after the
         # popup appears, before any click inside it (the popup must
         # hold focus from the moment it opens). The popup lives in
         # Cura's own window; the key reaches its close policy.
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "absent": True, "budget": 20},
         # The marker landed: the startup check stays quiet now (the
         # quiet check IS the marker's proof).
         {"op": "exec_slot", "slot": "checkWhatsNew", "args": []},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "absent": True, "budget": 5},
         # Dismissal two: a press outside the card — the modal dimmer
         # refuses the press AND closes the popup. First, the
         # pre-collapsed sections prove themselves: a real press on a
         # previous version's header expands its items — the header
         # and the item text were read live from the content, never
         # pinned.
         {"op": "exec_slot", "slot": "showWhatsNew", "args": []},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "budget": 20},
         {"op": "scroll_into_view", "objectName_state": ["whatsnew", "section"]},
         {"op": "deliver_click", "objectName_state": ["whatsnew", "section"]},
         {"op": "wait_rect", "text_state": ["whatsnew", "item"], "budget": 20},
         {"op": "deliver_click", "objectName": "moonrakerControlsPane", "expect": "not_accepted"},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "absent": True, "budget": 20},
         # Dismissal three: the Close button itself.
         {"op": "exec_slot", "slot": "showWhatsNew", "args": []},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "budget": 20},
         {"op": "deliver_click", "objectName": "whatsNewCloseButton"},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "absent": True, "budget": 20},
         # The repo link's press lands. Qt hands the URL to the OS,
         # which on the natives raises a browser OVER Cura — after
         # which nothing else in the run is visible. The runner's
         # foreground guard (before every frame) re-raises Cura and
         # reads the state back, so the recording stays Cura's and a
         # display that does not come home fails the step instead of
         # being scoped away.
         {"op": "exec_slot", "slot": "showWhatsNew", "args": []},
         {"op": "wait_rect", "objectName": "whatsNewRepoLink", "budget": 20},
         {"op": "scroll_into_view", "objectName": "whatsNewRepoLink"},
         {"op": "deliver_click", "objectName": "whatsNewRepoLink"},
         {"op": "exec_slot", "slot": "showWhatsNew", "args": []},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "budget": 20},
         {"op": "deliver_click", "objectName": "whatsNewCloseButton"},
         {"op": "wait_rect", "objectName": "whatsNewCloseButton", "absent": True, "budget": 20},
     ]},
    # The visible-interactions proof pair: the phase-0 evidence that a
    # real press/release lands — accepted by the item under the aim,
    # reaching the peer — and that a refused press reads as refused.
    {"id": "z10", "group": "probe",
     "name": "the deliver_click proof — a real press/release on a named control is accepted and reaches the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         # The jog grid now sits below the controls pane's fold at the
         # suite geometry: the preposition scrolls its Flickable so the
         # press has a place to land (the driver refuses an aim outside
         # the window's content).
         {"op": "scroll_into_view", "objectName": "moonrakerJogXPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogXPlus"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "z11", "group": "probe",
     "name": "the refused-press proof — a disabled control resolves but does not accept the press",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "jogEnabled", "value": False, "budget": 15},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         # The same fold as z10: a refused press still has to reach the
         # control, so the row is scrolled into the pane's viewport
         # first.
         {"op": "scroll_into_view", "objectName": "moonrakerJogXPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogXPlus", "expect": "not_accepted"},
     ]},
    {"id": "z1", "group": "probe",
     "name": "the preview stage geometry across empty, model and sliced states",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "exec_code", "verbs": [], "code": SCENE_PROBE},
         {"op": "exec_code", "verbs": [], "code": RECT_PROBE},
         {"op": "insert_model"},
         {"op": "wait_seconds", "seconds": 4},
         {"op": "exec_code", "verbs": [], "code": RECT_PROBE},
         {"op": "slice_scene"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 150},
         {"op": "exec_code", "verbs": [], "code": RECT_PROBE},
     ]},
    {"id": "z4", "group": "probe",
     "name": "the FM rename dialog's walkable content",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_file_slot", "slot": "openFileManager", "args": []},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_file_slot", "slot": "fileRequestRename", "args": ["benchy.gcode"]},
         {"op": "wait_seconds", "seconds": 2},
         {"op": "exec_code", "verbs": [], "code": FM_POPUP_PROBE},
         # The round-2 H3 proof: a real press lands on a NAMED VERB
         # inside the open Popup and the dialog closes — the walk,
         # the delivery and the effect in one step.
         {"op": "deliver_click", "objectName": "renameConfirmCancelButton"},
         {"op": "wait_rect", "objectName": "renameConfirmCancelButton", "absent": True, "budget": 20},
     ]},

    # ─── real-printer read-only (observation; see TESTING.md §2.5) ───
    # These run ONLY in real mode, where the dispatcher refuses every
    # op outside the read-only allowlist: no commands, no restarts, no
    # print starts — a live print is observed, never touched.
]
