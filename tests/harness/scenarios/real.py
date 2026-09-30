"""The the read-only dwell against a real printer scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations


SCENARIOS = [
    {"id": "r5", "group": "real", "real_safe": True,
     "name": "the data-render census against the real printer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 60},
         {"op": "census", "budget": 40},
     ]},

    {"id": "r1", "group": "real", "real_safe": True,
     "name": "the real printer's status renders",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_model", "prop": "klippyState", "contains": "ready", "budget": 30},
         {"op": "assert_model", "prop": "printActive"},
     ]},
    {"id": "r2", "group": "real", "real_safe": True,
     "name": "the real printer's temperatures populate",
     "steps": [
         {"op": "wait_model", "prop": "temperatureItems", "budget": 45},
         {"op": "assert_model", "prop": "temperatureItems", "contains": "temperature"},
     ]},
    {"id": "r3", "group": "real", "real_safe": True,
     "name": "the real printer's webcam streams",
     "steps": [
         {"op": "wait_model", "prop": "webcamNames", "budget": 45},
         {"op": "assert_model", "prop": "activeWebcamIndex"},
     ]},
    {"id": "r4", "group": "real", "real_safe": True,
     "name": "the real dwell: poll latency over a steady window",
     "steps": [
         {"op": "dwell", "minutes": 5, "path": "/printer/info"},
     ]},
    {"id": "r6", "group": "real", "real_safe": True,
     "name": "the M117 witness: the live message reaches the slot",
     "steps": [
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_model", "prop": "monitorMessage", "contains": "claude-m117-probe", "budget": 60},
     ]},

    # ─── configure (4.4.0, the popup round) ─────────────────────
]
