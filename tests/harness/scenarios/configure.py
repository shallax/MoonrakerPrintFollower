"""The section configuration and its one-at-a-time rules scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import CONFIGURE_DRAG_PROBE, CONFIGURE_GEOM_PROBE, CONFIGURE_CONTROLS_FIELD_PROBE, CONFIGURE_CROSSTALK_PROBE, CONFIGURE_DISMISS_PROBE, CONFIGURE_TRI_PROBE, CONFIGURE_FM_CLICK, CONFIGURE_FM_STATE, CONFIGURE_MUTUAL_PROBE, CONFIGURE_FM_OPEN, CONFIGURE_FM_OUTSIDE, CONFIGURE_FM_PROBE

SCENARIOS = [
    {"id": "x1", "group": "configure",
     "name": "the configure popup opens under its trigger, lists the rows, and Esc closes it first",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "exec_slot", "slot": "sectionLayoutFor", "args": ["controls"]},
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         # The blank-top-left regression: the card must hang below its
         # trigger, never sit on it (the silent anchor-drop landed the
         # popup at the window origin in the live report).
         {"op": "assert_aligned", "item": {"objectName": "sectionConfigurePopOver"},
          "no_overlap": {"objectName": "configureControlsSectionsButton"}},
         {"op": "assert_rendered", "objectName": "sectionConfigureRowTitle", "any": True, "contains": "Print"},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
         # Esc must close the popup FIRST: the trigger still works, so
         # the stage never left (the live report).
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
         # Dismissal geometry: a click on the card's own surface must
         # NOT dismiss it; a click outside its bounds must (the live
         # report: in-bounds clicks dismissed the pop-over).
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "exec_code", "verbs": ["mouseClick"], "code": CONFIGURE_DISMISS_PROBE},
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
         # ONE popover at a time, both directions: the status card
         # opens, then the controls card — the status must dismiss
         # (the live report: they could stack).
         {"op": "deliver_click", "objectName": "configureStatusSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_MUTUAL_PROBE},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
     ]},
    {"id": "x2", "group": "configure",
     "name": "the popup's row toggle hides and restores a section",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "deliver_click", "objectName": "configureStatusSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         # The tri-state selector: the native checkbox's checkState —
         # checked at ALL (the live report: it rendered empty).
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_TRI_PROBE.replace("EXPECT_STATE", "2")},
         {"op": "click_text", "text": "Temperatures"},
         {"op": "wait_model", "prop": "sectionHiddenMap", "contains": "temps", "budget": 15},
         # One hidden of several: partially checked.
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_TRI_PROBE.replace("EXPECT_STATE", "1")},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "absent": True, "budget": 15},
         {"op": "click_text", "text": "Temperatures"},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_TRI_PROBE.replace("EXPECT_STATE", "2")},
         # The NONE state: the selector hides everything, and the
         # state empties (the live report: none rendered dashed).
         {"op": "deliver_click", "objectName": "visibilitySelectorBox"},
         {"op": "wait_model", "prop": "sectionHiddenMap", "contains": "temps", "budget": 15},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_TRI_PROBE.replace("EXPECT_STATE", "0")},
         # Reset to defaults: the blue label at the card's bottom
         # commits the empty layout, so every section returns.
         {"op": "deliver_click", "objectName": "resetToDefaultsLabel"},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_TRI_PROBE.replace("EXPECT_STATE", "2")},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
     ]},
    {"id": "x3", "group": "configure",
     "name": "a committed reorder moves the sections (the rendered geometry follows the slot)",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "rect_of", "objectName": "moonrakerTemperatureDetail"},
         {"op": "exec_slot", "slot": "setSectionLayout",
          "args": ["status", ["temps", "job", "fansinfo", "filament", "objects", "systeminfo", "mcus"], []]},
         {"op": "wait_model", "prop": "sectionLayout", "contains": "temps", "budget": 15},
         # The temps section now leads the pane: its detail row rises
         # above the print-job section it once followed.
         {"op": "assert_rect_change", "objectName": "moonrakerTemperatureDetail",
          "direction": "shrunk", "axis": "y", "by": 40, "budget": 15},
         {"op": "exec_slot", "slot": "setSectionLayout",
          "args": ["status", ["job", "temps", "fansinfo", "filament", "objects", "systeminfo", "mcus"], []]},
         # The restored order IS the table default: the normaliser
         # deliberately drops all-default entries, so the raw model
         # property legitimately reads empty — the rendered position
         # is the witness instead.
         {"op": "assert_rect_change", "objectName": "moonrakerTemperatureDetail",
          "direction": "grown", "axis": "y", "by": 400, "budget": 15},
     ]},
    {"id": "x4", "group": "configure",
     "name": "the collapsed readouts render below their titles",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0},
                                    "print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "wait_model", "prop": "monitorPositionCompact", "contains": "X", "budget": 15},
         {"op": "deliver_click", "objectName": "configureInfoSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [True]},
         {"op": "wait_rendered", "objectName": "infoCollapsedReadoutText", "any": True, "contains": "°C", "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         {"op": "wait_rendered", "objectName": "statusCollapsedReadoutLabel", "any": True, "contains": "Progress", "budget": 15},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [True]},
         {"op": "wait_rendered", "objectName": "controlsCollapsedReadoutText", "any": True, "contains": "X", "budget": 15},
         # The three fixed-width fields stay separate and stacked on
         # the strip's line (the live report: the fields must never
         # overlap as the values change).
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_CONTROLS_FIELD_PROBE},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_GEOM_PROBE},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [False]},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [False]},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [False]},
     ]},

    {"id": "x5", "group": "configure",
     "name": "the file-manager columns popup opens with its background and full-width rows",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "click_text", "text": "File manager"},
         {"op": "wait_rect", "objectName": "columnsPopupBackground", "absent": True, "budget": 15},
         {"op": "exec_code", "verbs": ['mouseClick'], "code": CONFIGURE_FM_CLICK},
         {"op": "wait_rect", "objectName": "columnsPopupBackground", "budget": 15},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_FM_PROBE},
         # Esc dismisses the popup, the card stays (the live report:
         # Esc ignored the popup).
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "columnsPopupBackground", "absent": True, "budget": 15},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_FM_STATE},
         # Reopen, then an outside PRESS dismisses it too. The reopen
         # rides the popup's real open() call (the band's toggle
         # rides the live test — the harness engine refused
         # its reopen click).
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_FM_OPEN},
         {"op": "wait_rect", "objectName": "columnsPopupBackground", "budget": 15},
         {"op": "exec_code", "verbs": ['mouseClick'], "code": CONFIGURE_FM_OUTSIDE},
         {"op": "wait_rect", "objectName": "columnsPopupBackground", "absent": True, "budget": 15},
         # Close the file manager: it covers the whole stage and
         # would swallow the next scenario's clicks.
         {"op": "exec_slot", "slot": "setFileManagerOpen", "args": [False]},
         {"op": "wait_rect", "objectName": "columnsPopupBackground", "absent": True, "budget": 15},
     ]},
    {"id": "x6", "group": "configure",
     "name": "a handle drag commits on the plain release (no follow-up click)",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "deliver_click", "objectName": "configureStatusSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         # A real press/move/release on the first row's handle (the
         # s8 track-click precedent — the driver has no drag verb).
         {"op": "exec_code", "verbs": ['mousePress', 'mouseMove', 'mouseRelease'], "code": CONFIGURE_DRAG_PROBE},
         # The release alone commits: the job section left the top,
         # so the temps detail (the section that moved into its
         # place) rises by at least a section header.
         {"op": "wait_model", "prop": "sectionLayout", "contains": "temps", "budget": 15},
         {"op": "assert_rect_change", "objectName": "moonrakerTemperatureDetail",
          "direction": "shrunk", "axis": "y", "by": 40, "budget": 15},
         {"op": "exec_slot", "slot": "setSectionLayout",
          "args": ["status", ["job", "temps", "fansinfo", "filament", "objects", "systeminfo", "mcus"], []]},
         {"op": "assert_rect_change", "objectName": "moonrakerTemperatureDetail",
          "direction": "grown", "axis": "y", "by": 40, "budget": 15},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
     ]},
    {"id": "x7", "group": "configure",
     "name": "the collapsed rails keep their readouts at the smallest window this stage allows",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         # The window goes to the application's own minimum height — the
         # shortest a user can drag it. The strip's hide is measured
         # against the pane's VISIBLE height, and at MonitorStage the
         # panes' own minimums (180+190+240+200 * screenScaleFactor plus
         # chrome) hold the window at 1600x880 under Xvfb, far above the
         # height that would trip it: the visibility stays ON and each
         # collapsed rail keeps its readout on screen. Measured on Linux
         # — all three found by the visibility-filtered walk, all three
         # in view. The waits below assert that; the absence they
         # replaced held only at a forced 1600x300, a size the floor
         # rule refuses and no user can reach.
         {"op": "resize_window", "w": 1600, "h": "min"},
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0},
                                     "print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "infoCollapsedReadoutText", "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "statusCollapsedReadoutLabel", "budget": 15},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "controlsCollapsedReadoutText", "budget": 15},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [False]},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [False]},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [False]},
     ]},
    {"id": "x8", "group": "configure",
     "name": "unavailable values hide their readouts whole",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         # A STANDBY session whose values are UNAVAILABLE (the
         # ruling: neither the value nor its glyph may
         # render; the X/Y/Z tuple hides whole when any one axis is
         # missing). The sim's kickoff state always carries heaters
         # and positions, so the scenario overwrites those values
         # with None — the plugin's aux merge is a DEEP merge (a
         # blank object would keep the old values alive), and a None
         # value is what the model reads as unavailable. The sim's
         # printing lifecycle would supply ETA/layer values of its
         # own — standby keeps those absent too.
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [True]},
         # The positive control (the panel's catch): the kickoff's
         # heaters are real values — the readout renders BEFORE the
         # unavailability lands, or the absence check below proves
         # nothing.
         {"op": "wait_rect", "objectName": "infoCollapsedReadoutText", "budget": 15},
         {"op": "sim_set", "state": {
             "print_stats": {"state": "standby", "filename": "", "total_duration": 0.0,
                             "print_duration": 0.0, "filament_used": 0.0,
                             "info": {"total_layer": None, "current_layer": None}},
             "extruder": {"temperature": None, "target": None},
             "heater_bed": {"temperature": None, "target": None},
             "motion_report": {"live_position": [], "live_velocity": 0.0,
                               "live_extruder_velocity": 0.0, "steppers": []},
             "gcode_move": {"speed_factor": 1.0, "absolute_coordinates": True, "position": []}}},
         {"op": "wait_rect", "objectName": "infoCollapsedReadoutText", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         # The status strip shows ONLY the flow pair: the ETA, the
         # finish, the layer count and the progress group all hide
         # with their values (and the idle print gate), while the
         # flow's 0 rate is a real standby value and must render.
         {"op": "wait_rect", "objectName": "statusCollapsedReadoutLabel", "absent": True, "budget": 15},
         {"op": "wait_rect", "objectName": "statusCollapsedFlowLabel", "budget": 15},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [True]},
         # The controls strip shows ONLY the z pair: the position
         # cells hide with their unavailable axes, while the z
         # offset's honest zero (no homing origin set) is a real
         # value and must render.
         {"op": "wait_rect", "objectName": "controlsCollapsedReadoutText", "absent": True, "budget": 15},
         {"op": "wait_rect", "objectName": "controlsCollapsedZOffsetLabel", "budget": 15},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [False]},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [False]},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [False]},
     ]},
    {"id": "x10", "group": "configure",
     "name": "the improve flow reveals the next-pause fill",
     "steps": [
         # The fill's fraction needs the ETA, and the ETA needs the
         # follower's observed layer — the PREVIEW must be running
         # (the h2 load flow). scenario1.gcode's baked PAUSE at
         # layer 20 (the generator bakes it) is the pause ahead.
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "info": {"current_layer": 5, "total_layer": 40}},
                                     "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120, "layer_clock_interval_s": 0}},
         {"op": "wait_model", "prop": "monitorFilename", "contains": "scenario1", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "emit_click", "text": "Load current print"},
         # The prompt is the card's own dialog: the button is on screen
         # and pressed, the same way the rename confirm is.
         {"op": "wait_rect", "objectName": "moonrakerReplaceConfirmButton", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerReplaceConfirmButton"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "click_stage", "stage": "MonitorStage"},
         # The improve-Eta flow builds the index and the snapshot's
         # next-pause fraction publishes — the fill renders only
         # then (the live ruling: absent without a pause).
         {"op": "exec_slot", "slot": "improveEta", "args": []},
         {"op": "wait_model", "prop": "improvingEta", "value": False, "budget": 60},
         # The baked pause at zero-based 20 reads human layer 21 —
         # this leg proves the TARGET resolved from the index.
         {"op": "wait_model", "prop": "nextPauseLayer", "value": 21, "budget": 30},
         # The physical layer's resolution feeds the follower's
         # observed layer, which the remaining needs — "1 / 40"
         # carries the / only when the current layer resolved.
         {"op": "wait_model", "prop": "monitorLayer", "contains": "/", "budget": 15},
         # The ETA's composed clock carries the ≈ marker only when
         # the remaining seconds exist — the same condition the
         # fill's fraction needs.
         {"op": "wait_model", "prop": "nextPauseEta", "contains": "≈", "budget": 30},
         # The strip renders only while the status pane is collapsed.
         # The rename made this honest: the old shared objectName
         # matched the job section's always-rendered twin instead.
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "statusNextPauseFill", "budget": 15},
     ]},
    {"id": "x9", "group": "configure",
     "name": "the controls reset never touches the other panes' layouts",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         # Customise the INFORMATION pane: drag its first row down.
         {"op": "deliver_click", "objectName": "configureInfoSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "exec_code", "verbs": ['mousePress', 'mouseMove', 'mouseRelease'], "code": CONFIGURE_DRAG_PROBE},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_CROSSTALK_PROBE},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
         # The CONTROLS popup's reset-to-defaults — the information
         # layout must survive it untouched (the live report).
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "deliver_click", "objectName": "resetToDefaultsLabel"},
         {"op": "exec_code", "verbs": [], "code": CONFIGURE_CROSSTALK_PROBE},
         {"op": "key_press", "key": "Escape"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
     ]},

]
