"""The the file browser's listing, filters and transfers scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

import os
from .probe_source import _scratch_dir

SCENARIOS = [
    {"id": "f1", "group": "files", "name": "the file manager browses the simulated store",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         # Close the popup again: the NEXT scenario's File-manager
         # click must not land on this popup's scrim (the overlay
         # interception the delivery record proved — the press was
         # refused by the scrim's rectangle). The popup renders in
         # its own window, so a main-window press at its button's
         # coordinates grabs whatever lives there; the close rides
         # the model slot for now (popup chrome, not a critical
         # journey — the popup-window addressing follow-up is
         # recorded in DECISIONS).
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
         {"op": "wait_rect", "text": "New folder…", "absent": True, "budget": 20},
     ]},
    {"id": "f2", "group": "files", "name": "the upload lane accepts and refuses honestly",
     "steps": [
         # The popup stays open through the flow: the completion
         # refresh walks the listing into the OPEN popup's rows.
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "write_fixture", "path": os.path.join(_scratch_dir, "scenario-upload.gcode")},
         {"op": "exec_file_slot", "slot": "fileUpload",
          "args": [os.path.join(_scratch_dir, "scenario-upload.gcode")]},
         {"op": "sim_ledger", "needle": "files/upload", "field": "path", "min": 1, "budget": 30},
         # The honest-lane proof: only the sim's upload handler (not
         # the catch-all) adds the entry to the store, and the
         # completion refresh walks it into the model's rows.
         {"op": "wait_model", "prop": "fileManagerRows", "contains": "scenario-upload.gcode", "budget": 30},
         # The refusal half: the armed lane answers 400 with its own
         # message, and the note surfaces it. A dead lane would
         # accept this upload and the refusal string never appears.
         {"op": "write_fixture", "path": os.path.join(_scratch_dir, "scenario-upload-refused.gcode")},
         {"op": "sim_arm", "arms": {"fail_upload": True}},
         {"op": "exec_file_slot", "slot": "fileUpload",
          "args": [os.path.join(_scratch_dir, "scenario-upload-refused.gcode")]},
         {"op": "wait_model", "prop": "fileManagerNote", "contains": "simulated upload refusal", "budget": 30},
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
     ]},
    {"id": "f3", "group": "files", "name": "delete removes the row after the confirm",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         # The row's context-menu MouseArea is a sibling of the name
         # label — the real press grabs the row's background
         # rectangle, never the menu trigger (the row-menu follow-up,
         # recorded in DECISIONS with the probe's evidence). The
         # request stays a declared slot; the CONFIRM is the real
         # press on the named verb, and the popup closes by name so
         # its scrim never covers the next scenario's clicks.
         {"op": "exec_file_slot", "slot": "fileRequestDeleteFile", "args": ["delete-me.gcode"]},
         {"op": "deliver_click", "objectName": "deleteConfirmDeleteButton"},
         {"op": "sim_ledger", "needle": "gcodes/delete-me.gcode", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
     ]},
    {"id": "f4", "group": "files", "name": "folder create reaches the peer's directory route",
     "steps": [
         {"op": "exec_file_slot", "slot": "fileCreateDirectory", "args": ["simdir"]},
         {"op": "sim_ledger", "needle": "files/directory", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "f5", "group": "files", "name": "rename confirms into the peer's move route",
     "steps": [
         {"op": "click_text", "text": "File manager"},
         {"op": "exec_file_slot", "slot": "fileRequestRename", "args": ["scenario1.gcode"]},
         {"op": "exec_file_slot", "slot": "filePreviewRename", "args": ["scenario1-renamed.gcode"]},
         {"op": "deliver_click", "objectName": "renameConfirmButton"},
         {"op": "sim_ledger", "needle": "files/move", "min": 1, "budget": 20},
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
     ]},
    {"id": "f6", "group": "files", "name": "print confirm arms the start verdict",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         # The row-menu request stays a declared slot (see f3); the
         # confirm is the real press on the named verb.
         {"op": "exec_file_slot", "slot": "fileRequestPrint", "args": ["benchy.gcode"]},
         {"op": "deliver_click", "objectName": "printConfirmStartButton"},
         {"op": "sim_ledger", "needle": "print/start", "method": "POST", "min": 1, "budget": 20},
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
     ]},

    {"id": "f7", "group": "files",
     "name": "the rename dialog's field and buttons drive the move",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "wait_rect", "text": "New folder…", "budget": 30},
         # The row-menu request stays a declared slot (see f3); the
         # confirm is the real press on the named verb.
         {"op": "exec_file_slot", "slot": "fileRequestRename", "args": ["benchy.gcode"]},
         {"op": "wait_rect", "text": "Rename file", "budget": 20},
         # The dialog's field owns focus; the keystroke appends to
         # the name (an unchanged name is a same-name no-op — the
         # move must be a real move).
         {"op": "key_press", "key": "X"},
         {"op": "deliver_click", "objectName": "renameConfirmButton"},
         {"op": "wait_rect", "text": "Rename file", "absent": True, "budget": 20},
         {"op": "sim_ledger", "needle": "files/move", "min": 1, "budget": 20},
     ]},
    # ─── controls ─────────────────────────────────────────────
]
