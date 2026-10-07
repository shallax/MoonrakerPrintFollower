"""The the Preview card and the plate follower scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import P1_PCT_PROBE, P_PAUSE_CLICK, P_PAUSE_SCHEDULED, P_ROW_PASSED, P_MISSED, P_SLIDER_DRAG, P_FOLLOW_READ, P_ATTACH_EMIT, P1_RENDER_PROBE, P1_FOLLOW_BUTTON, PENGUIN_PREVIEW_READ

VIEW_OPTIONS_READ = '''
result = {}
for item in _walk(_main_window().contentItem()):
    if item.objectName() != "moonrakerPreviewObjectTagsControls": continue
    items = list(_walk(item))
    labels = {child.property("text") for child in items if isinstance(child.property("text"), str)}
    result["sections"] = {"Toolhead", "Object Name Banners", "Bedmesh"}.issubset(labels)
    result["smooth_path"] = any(child.objectName() == "moonrakerEstimatedToolheadPosition" and child.property("text") == "Smooth path" for child in items)
    result["enabled_label"] = any(child.objectName() == "moonrakerPreviewObjectTagsEnabled" and child.property("text") == "Enabled" for child in items)
    break
'''

TOOLHEAD_SETUP_READ = '''
from PyQt6.QtGui import QGuiApplication
result = {"visible": False, "model_controls": False}
for window in QGuiApplication.topLevelWindows():
    if window.objectName() == "moonrakerToolheadSetupDialog" and window.isVisible():
        result["visible"] = True
        result["model_controls"] = any(item.objectName() == "toolheadChooseModel" and item.isVisible() and item.isEnabled() for item in _walk(window.contentItem()))
'''

TOOLHEAD_POSITION_READ = '''
from UM.Application import Application
result = {}
for extension in Application.getInstance().getExtensions():
    if "MoonrakerPrintFollower" in type(extension).__name__:
        runtime = extension._runtime
        node = runtime.toolhead._node
        view = runtime.cura.view
        root = runtime.cura.controller.getScene().getRoot()
        compatibility = bool(view and view.getCompatibilityMode())
        native_parented = bool(view and view.getNozzleNode().getParent() is root)
        released = not runtime.cura.toolhead_override and runtime.toolhead._native is None
        # Cura's legacy SimulationPass intentionally omits its nozzle,
        # and NativeNozzleLifecycle leaves that mode alone. Normal mode
        # must restore the nozzle to this scene; both must release our
        # suppression owner and hide our reported-position node.
        result = {"reported": runtime.presentation.reported_position,
                  "visible": bool(node and node.isVisible()),
                  "native_restored": bool(view and released and (native_parented or compatibility))}
        if node:
            point = node.render_position()
            stack = Application.getInstance().getGlobalContainerStack()
            width, depth = [float(stack.getProperty(key, "value")) for key in ("machine_width", "machine_depth")]
            centred = bool(stack.getProperty("machine_center_is_zero", "value"))
            expected = (100 if centred else 100-width/2, 20, -100 if centred else depth/2-100)
            result["position_ok"] = all(abs(actual-target) < .01 for actual, target in zip((point.x, point.y, point.z), expected))
            result["actual_position"] = [point.x, point.y, point.z]
            result["expected_position"] = list(expected)
        result["compatibility_mode"] = compatibility
        result["native_parented"] = native_parented
        result["override_released"] = released
        break
'''

SCENARIOS = [
    {"id": "p1", "group": "preview",
     "name": "the load renders the real toolpath, the indicator, and the card",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set_current_print"},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120, "gcode_stream_hold": True}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "emit_click", "text": "Load current print"},
         # The prompt is the card's own dialog: the button is on screen
         # and pressed, the same way the rename confirm is.
         {"op": "wait_rect", "objectName": "moonrakerReplaceConfirmButton", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerReplaceConfirmButton"},
         {"op": "wait_exec", "code": P1_PCT_PROBE, "contains": '"pct": true', "budget": 20},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "sim_arm", "arms": {"gcode_stream_hold": False}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "assert_exec", "code": P1_RENDER_PROBE,
          "contains": '"render_ok": true'},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "wait_exec", "code": P1_FOLLOW_BUTTON, "contains": '"button": true', "budget": 30},
     ]},
    {"id": "p2", "group": "preview",
     "version_skip": {"5.11": "5.11\u2019s SimulationView hides its layer slider once the plugin\u2019s gcode load replaces the sliced scene \u2014 the drag premise is 5.12+ only (the walk-dump)"},
     "name": "a real drag on Cura's layer slider auto-detaches the follow",
     "steps": [
         {"op": "wait_seconds", "seconds": 5},
         {"op": "exec_code", "verbs": ['mouseMove'], "code": P_SLIDER_DRAG},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
         {"op": "dump_visible", "needle": "detach|follow|attach", "region": [600, 560, 1280, 800]},
     ]},
    {"id": "p3", "group": "preview",
     "version_skip": {"5.11": "5.11\u2019s preview flaps the toolpath on every stage round-trip, so the attach cannot survive a tab switch \u2014 the premise is 5.12+ only (the walk-dump)"},
     "name": "rapid tab and view switching never drops the attach",
     "steps": [
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_ATTACH_EMIT},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
         {"op": "click_stage", "stage": "PrepareStage"},
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "click_stage", "stage": "PrepareStage"},
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 60},
     ]},
    {"id": "p4", "group": "preview",
     "name": "the pause-at-layer entry schedules and renders",
     "steps": [
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": "window = _main_window()\nresult = {}\nfor item in _walk(window.contentItem()):\n    try:\n        label = item.property(\"text\")\n    except Exception:\n        label = None\n    if isinstance(label, str) and label.startswith(\"\\u23f8\") and \"Button\" in item.metaObject().className() and bool(item.isVisible()):\n        item.clicked.emit()\n        result[\"emitted\"] = True\n        break"},
         {"op": "wait_exec", "code": "window = _main_window()\nresult = {}\nfor item in _walk(window.contentItem()):\n    try:\n        label = item.property(\"text\")\n    except Exception:\n        label = None\n    if isinstance(label, str) and \"Layer\" in label and bool(item.isVisible()):\n        result[\"scheduled\"] = True\n        result[\"label\"] = label[:40]\n        break",
          "contains": '"scheduled": true', "budget": 20},
         {"op": "dump_visible", "needle": "layer", "region": [600, 560, 1280, 800]},
     ]},
    {"id": "p5", "group": "preview",
     "version_skip": {"5.11": "5.11\u2019s toolpath flap leaves the card\u2019s follow state arbitrary after p3\u2019s switches \u2014 the Detach/Attach dance premise is 5.12+ only"},
     "name": "the card's Detach button detaches and Attach re-attaches",
     "steps": [
         {"op": "click_text", "text": "Detach"},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_ATTACH_EMIT},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
     ]},

    {"id": "p6", "group": "preview",
     "version_skip": {"5.11": "the pause\u2019s layer targeting follows the view\u2019s selected layer, which 5.11\u2019s view cannot hold after the gcode load \u2014 the fired-pause premise is 5.12+ only"},
     "name": "the scheduled pause fires as the print crosses the layer",
     "steps": [
         {"op": "sim_set_current_print", "current_layer": 1, "layer_clock_interval_s": 3600},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         # The same POST seeded layer 1 and held the clock before waiting
         # for the UI. Even an 8+ second startup cannot skip that layer.
         {"op": "wait_sim", "path": "print_stats.info.current_layer", "value": 1, "budget": 20},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_PAUSE_CLICK},
         # The row the click made, not any row: the baked row also
         # reads "End of layer", and matching it hid a refused click
         # (the 2026-09-18 flake's false positive).
         {"op": "wait_exec", "code": P_PAUSE_SCHEDULED, "contains": '"scheduled": true, "label": "End of layer 2', "budget": 20},
         # The row is read: the pause now has to fire, and it fires
         # by the print crossing the layer.
         {"op": "sim_arm", "arms": {"layer_clock_interval_s": 6}},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 60},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
         # The 4.3.0 ruling: a fired pause STAYS listed, restyled as
         # passed — the row must not disappear.
         {"op": "wait_exec", "code": P_ROW_PASSED, "contains": '"passed": true', "budget": 20},
     ]},
    {"id": "p7", "group": "preview",
     "expected_log_messages": ["MoonrakerHTTP POST pause::scheduled failed: simulated PAUSE refusal"],
     "version_skip": {"5.11": "the refused-pause flow shares p6\u2019s selected-layer targeting premise \u2014 5.12+ only"},
     "name": "a refused PAUSE keeps the entry restyled as not taken",
     "steps": [
         {"op": "sim_arm", "arms": {"fail_pause_script": True}},
         {"op": "sim_set_current_print", "current_layer": 1, "layer_clock_interval_s": 3600},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         # p6's atomic layer seed/hold also covers the refusal flow.
         {"op": "wait_sim", "path": "print_stats.info.current_layer", "value": 1, "budget": 20},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_PAUSE_CLICK},
         {"op": "wait_exec", "code": P_PAUSE_SCHEDULED, "contains": '"scheduled": true, "label": "End of layer 2', "budget": 20},
         {"op": "sim_arm", "arms": {"layer_clock_interval_s": 6}},
         {"op": "wait_exec", "code": P_MISSED, "contains": '"missed": true', "budget": 60},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},

    {'id': 'p9', 'group': 'preview', 'version_skip': {'5.11': 'Loaded Preview toolpaths require Cura 5.12+'}, 'name': 'reported toolhead follows live parking and restores estimated nozzle', 'steps': [
        {'op': 'sim_set_current_print'},
        {'op': 'wait_model', 'prop': 'monitorFilename', 'value': 'scenario1.gcode', 'budget': 30},
        {'op': 'click_stage', 'stage': 'PreviewStage'},
        {'op': 'wait_rect', 'objectName': 'moonrakerPreviewCardOverlay', 'budget': 30},
        {'op': 'click_text', 'text': 'Load current print'},
        {'op': 'wait_rect', 'objectName': 'moonrakerReplaceConfirmButton', 'budget': 30},
        {'op': 'deliver_click', 'objectName': 'moonrakerReplaceConfirmButton'},
        {'op': 'wait_rect', 'objectName': 'moonrakerPreviewCard', 'budget': 240},
        {'op': 'wait_rect', 'objectName': 'loadIndicatorContent', 'absent': True, 'budget': 60},
        {'op': 'exec_code', 'verbs': ['clicked.emit'], 'code': P_ATTACH_EMIT},
        {'op': 'wait_exec', 'code': P_FOLLOW_READ, 'contains': '"attached": true', 'budget': 20}, {'op': 'exec_code', 'verbs': ['setProperty'], 'code': 'result = {}\nfor item in _walk(_main_window().contentItem()):\n    if item.objectName() == "moonrakerPreviewObjectTags":\n        item.setProperty("controlsExpanded", True)\n        result["expanded"] = True'}, {'op': 'wait_rect', 'objectName': 'moonrakerReportedToolheadPosition', 'budget': 20}, {'op': 'deliver_click', 'objectName': 'moonrakerReportedToolheadPosition'}, {'op': 'sim_set', 'state': {'print_stats': {'state': 'complete'}, 'motion_report': {'live_position': [100, 100, 20, 0]}, 'gcode_move': {'homing_origin': [0, 0, 0, 0], 'position': [100, 100, 20, 0], 'gcode_position': [100, 100, 20, 0]}, 'toolhead': {'homed_axes': 'xyz'}}}, {'op': 'wait_exec', 'code': TOOLHEAD_POSITION_READ, 'contains': '"reported": true, "visible": true, "native_restored": false, "position_ok": true', 'budget': 30}, {'op': 'deliver_click', 'objectName': 'moonrakerEstimatedToolheadPosition'}, {'op': 'wait_exec', 'code': TOOLHEAD_POSITION_READ, 'contains': '"reported": false, "visible": false, "native_restored": true', 'budget': 20}, {'op': 'wait_exec', 'code': P_FOLLOW_READ, 'contains': '"attached": true', 'budget': 20}, {'op': 'deliver_click', 'objectName': 'moonrakerPreviewObjectTagsHandle'}]},

    {"id": "p8", "group": "preview",
     "version_skip": {"5.11": "5.11 cannot retain a loaded G-code toolpath in SimulationView; the continuous attached Preview premise requires 5.12+"},
     "name": "attached Preview smoothly follows the same penguin in twenty seconds",
     "steps": [
         {"op": "sim_arm", "arms": {"gcode_fixture": "penguin"}},
         {"op": "sim_set_current_print", "filename": "penguin.gcode"},
         # Setting simulator state is not delivery: wait for the actual
         # snapshot before the card changes height/state under the press.
         {"op": "wait_model", "prop": "monitorFilename", "value": "penguin.gcode", "budget": 30},
         {"op": "wait_model", "prop": "monitorState", "value": "Printing", "budget": 30},
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "click_text", "text": "Load current print"},
         {"op": "wait_rect", "objectName": "moonrakerReplaceConfirmButton", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerReplaceConfirmButton"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "wait_exec", "code": PENGUIN_PREVIEW_READ, "contains": '"ready": true', "budget": 30},
         {"op": "sim_arm", "arms": {"gcode_playback_s": 20}},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "wait_exec", "code": PENGUIN_PREVIEW_READ, "contains": '"moving": true', "budget": 10},
         {"op": "wait_exec", "code": PENGUIN_PREVIEW_READ, "contains": '"finished": true', "budget": 25},
         {"op": "wait_seconds", "seconds": 7},
         {"op": "assert_exec", "code": PENGUIN_PREVIEW_READ, "contains": '"finished": true'},
     ]},

    {"id": "p10", "group": "preview", "name": "View Options names its sections and opens toolhead setup",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewObjectTagsHandle", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerPreviewObjectTagsHandle"},
         {"op": "wait_exec", "code": VIEW_OPTIONS_READ, "contains": '"sections": true, "smooth_path": true, "enabled_label": true', "budget": 20},
         {"op": "wait_rect", "objectName": "moonrakerSetupToolhead", "budget": 20},
         {"op": "deliver_click", "objectName": "moonrakerSetupToolhead"},
         {"op": "wait_exec", "code": TOOLHEAD_SETUP_READ, "contains": '"visible": true, "model_controls": true', "budget": 30},
         {"op": "deliver_click", "text": "Cancel"},
         {"op": "wait_exec", "code": TOOLHEAD_SETUP_READ, "contains": '"visible": false', "budget": 20},
     ]},

    # ─── the smoke set (the release gate's sanity layer) ──────────
]
