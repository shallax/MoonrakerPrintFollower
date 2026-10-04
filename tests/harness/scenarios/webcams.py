"""The the webcam stream, its selection and its recovery scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations

from .probe_source import CAM_FRAMES, CAM_STARTED

_DETECTION_MODEL = (
    "from UM.Application import Application\n"
    "model = next((device.activePrinter for device in "
    "Application.getInstance().getOutputDeviceManager().getOutputDevices() "
    "if 'Moonraker' in type(device).__name__ and device.activePrinter is not None), None)\n"
    "if model is None or model._detection is None:\n"
    "    raise RuntimeError('local detection model unavailable')\n"
    "service = model._detection\n"
)

SCENARIOS = [
    {"id": "e1", "group": "webcams", "name": "the camera starts on its own (the first-publish fix)",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_exec", "code": CAM_STARTED, "contains": '"started": true', "budget": 40},
         {"op": "assert_model", "prop": "cameraName", "contains": "sim-cam", "budget": 30},
     ]},
    {"id": "e3", "group": "webcams",
     "name": "the key-carrying stream renders frames through the bridge",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_arm", "arms": {"webcam_bridged": True}},
         {"op": "exec_slot", "slot": "refreshWebcams", "args": []},
         {"op": "wait_exec", "code": CAM_FRAMES, "contains": '"width": 320', "budget": 60},
     ]},
    {"id": "e2", "group": "webcams",
     "name": "the camera's picture carries its overlays, and the scale stands in for the bar",
     "steps": [
         {"op": "assert_model", "prop": "webcamNames", "contains": "sim-cam", "budget": 30},
         # The monitor page holds the camera pane; the stream starts on
         # its own there (e1) and the picture lands fitted to the
         # viewport, so the frame and the gesture surface are the live
         # picture's own box.
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "cameraImage", "budget": 60},
         {"op": "wait_rect", "objectName": "cameraFrame", "budget": 15},
         {"op": "wait_rect", "objectName": "cameraGestureArea", "budget": 15},
         {"op": "wait_rect", "objectName": "cameraLiveBadge", "budget": 15},
         # At the baseline geometry the picture clears the bar's fit
         # rule (200 px wide, and tall enough to keep the chip's band
         # clear above and below), so the bar is the control on the
         # picture — resting on its zoom face, the default one.
         {"op": "wait_rect", "objectName": "cameraBar", "budget": 20},
         {"op": "wait_rect", "objectName": "cameraZoomScale", "budget": 15},
         {"op": "wait_rect", "objectName": "cameraZoomReadout", "budget": 15},
         {"op": "wait_rect", "objectName": "cameraZoomBar", "budget": 15},
         {"op": "wait_rect", "objectName": "cameraZoomMarker", "budget": 15},
         # One bar, two faces, and they never share the box: the rate's
         # face arrives on a gesture (Shift+wheel, right-drag) the
         # harness's input set does not carry, and its render is driven
         # by the Qt suite (test_qml_plate_composition.py). The zoom face
         # standing where the rate's is not is this rule's live half.
         {"op": "wait_rect", "objectName": "cameraFpsScale", "absent": True, "budget": 5},
         {"op": "wait_rect", "objectName": "cameraFpsReadout", "absent": True, "budget": 5},
         {"op": "wait_rect", "objectName": "cameraFpsBar", "absent": True, "budget": 5},
         {"op": "wait_rect", "objectName": "cameraFpsMarker", "absent": True, "budget": 5},
         # The crush: the picture drops under the bar's fit rule and
         # the compact chip takes the bar's place — the two stand-ins
         # are mutually exclusive, so the chip's presence is the bar's
         # absence proved from the other side.
         {"op": "resize_window", "w": 1420, "h": 700},
         {"op": "wait_rect", "objectName": "cameraBarChip", "budget": 20},
         {"op": "wait_rect", "objectName": "cameraBarChipText", "budget": 15},
         {"op": "wait_rect", "objectName": "cameraZoomScale", "absent": True, "budget": 5},
         {"op": "wait_rect", "objectName": "cameraBar", "absent": True, "budget": 20},
     ]},
    {"id": "e4", "group": "webcams",
     "name": "the global detection switch hides controls and stops analysis without deleting printer settings",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "wait_rect", "objectName": "failureDetectionSection", "absent": True, "budget": 10},
         # Synthetic readiness tests the installed-model UI branch without
         # downloading or executing native inference in the simulator.
         {"op": "exec_code", "verbs": [], "code": _DETECTION_MODEL +
          "service._ready = True\n"
          "service.stateChanged.emit()\n"
          "model._publish()\n"
          "result = {'ready': model.detectionReady, 'enabled': model.detectionGlobalEnabled}"},
         {"op": "assert_model", "prop": "detectionGlobalEnabled", "value": True, "budget": 10},
         {"op": "wait_rect", "objectName": "failureDetectionSection", "budget": 10},
         {"op": "exec_code", "verbs": [], "code": _DETECTION_MODEL +
          "saved = model.detectionEnabled\n"
          "if not service.set_enabled(False):\n"
          "    raise RuntimeError(service.error)\n"
          "model._publish()\n"
          "result = {'enabled': model.detectionGlobalEnabled, 'printer_choice_kept': model.detectionEnabled == saved}"},
         {"op": "assert_model", "prop": "detectionGlobalEnabled", "value": False, "budget": 10},
         {"op": "wait_rect", "objectName": "failureDetectionSection", "absent": True, "budget": 10},
         {"op": "assert_model", "prop": "detectionState", "value": "idle", "budget": 10},
         {"op": "exec_code", "verbs": [], "code": _DETECTION_MODEL +
          "if not service.set_enabled(True):\n"
          "    raise RuntimeError(service.error)\n"
          "model._publish()\n"
          "service._ready = False\n"
          "service.stateChanged.emit()\n"
          "model._publish()\n"
          "result = {'enabled': service.enabled, 'ready_restored': not service.ready}"},
         {"op": "wait_rect", "objectName": "failureDetectionSection", "absent": True, "budget": 10},
     ]},

    # ─── files & print start ──────────────────────────────────
]
