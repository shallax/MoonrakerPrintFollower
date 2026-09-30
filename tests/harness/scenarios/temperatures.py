"""The the temperature readouts, chart and presets scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations


SCENARIOS = [
    {"id": "c1", "group": "temperatures", "name": "the temperature targets render",
     "steps": [
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0},
                                    "heater_bed": {"temperature": 55.0, "target": 60.0}}},
         {"op": "wait_sim", "path": "extruder.target", "value": 210.0, "budget": 15},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "Hotend", "budget": 15},
     ]},
    {"id": "c2", "group": "temperatures", "name": "the fan and speed surfaces render",
     "steps": [
         # The reset buttons live in the dashboard's tuning section:
         # the stage must be open before the presses (the live leg
         # found the items absent — the delivery never landed).
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"fan": {"speed": 0.4}}},
         {"op": "wait_sim", "path": "fan.speed", "value": 0.4, "budget": 15},
         {"op": "assert_model", "prop": "speedFactorPercent", "value": 100},
         # The reset buttons' convergence: the factors move off 100,
         # the clicks command them back, and the PUBLISHED percents
         # follow (the commands actually applied, not just queued).
         {"op": "sim_set", "state": {"gcode_move": {"speed_factor": 1.37, "extrude_factor": 1.28}}},
         {"op": "assert_model", "prop": "speedFactorPercent", "value": 137, "budget": 15},
         {"op": "assert_model", "prop": "flowFactorPercent", "value": 128, "budget": 15},
         # The tuning section sits below the dashboard's fold: the
         # buttons' absolute coordinates land off the window, so the
         # presses must scroll them into view first (the live leg's
         # off-window delivery).
         {"op": "scroll_into_view", "objectName": "moonrakerTuningSpeedReset"},
         {"op": "deliver_click", "objectName": "moonrakerTuningSpeedReset"},
         {"op": "scroll_into_view", "objectName": "moonrakerTuningFlowReset"},
         {"op": "deliver_click", "objectName": "moonrakerTuningFlowReset"},
         {"op": "assert_model", "prop": "speedFactorPercent", "value": 100, "budget": 15},
         {"op": "assert_model", "prop": "flowFactorPercent", "value": 100, "budget": 15},
     ]},
    {"id": "c3", "group": "temperatures", "name": "the sensor visibility toggles persist",
     "steps": [
         {"op": "exec_slot", "slot": "setTemperatureSensorVisible", "args": ["extruder", True]},
         {"op": "assert_model", "prop": "temperatureItems", "contains": "Hotend", "budget": 10},
     ]},
    {"id": "c4", "group": "temperatures", "name": "runout and MCU sensors render their absence",
     "steps": [
         {"op": "assert_model", "prop": "mcuSummary", "value": "\u2014"},
     ]},
    {"id": "c5", "group": "temperatures", "name": "endstops render from the peer's query",
     "steps": [
         {"op": "wait_model", "prop": "endstopItems", "contains": "X", "budget": 45},
     ]},

    # ─── console ──────────────────────────────────────────────
]
