"""The the console pane's feed and its send path scenarios.

One group of the suite, in the order the runner executes it.
The assembly point is the package's __init__.
"""
from __future__ import annotations


SCENARIOS = [
    {"id": "d1", "group": "console", "name": "a sent command's response renders",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         # The console's expand is chrome state (a declared slot,
         # like the popup close); the typing and the send are real
         # input: the field takes a real press to focus, the command
         # is typed, and the named Send button takes the real press.
         {"op": "exec_slot", "slot": "setConsoleExpanded", "args": [True]},
         # The expand is chrome state and the pane lays out behind it, so
         # the input is WITNESSED before it is pressed: without the wait
         # the press lands on a console that has not been laid out yet and
         # is refused as "no visible item" (the Windows gate leg, once, on
         # the same commit whose other platform legs and other runs were
         # green — a race, not a regression).
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerConsoleInput"},
         {"op": "key_press", "key": "M"},
         {"op": "key_press", "key": "1"},
         {"op": "key_press", "key": "0"},
         {"op": "key_press", "key": "5"},
         {"op": "wait_rect", "objectName": "moonrakerConsoleSend", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerConsoleSend"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "d3", "group": "console", "name": "clear empties the console",
     "steps": [
         {"op": "sim_set", "state": {"console_lines": [{"type": "response", "message": "// line %d" % i,
                                                       "time": 1.0} for i in range(10)]}},
         {"op": "deliver_click", "objectName": "moonrakerConsoleClear"},
         {"op": "assert_model", "prop": "consoleHistory", "value": []},
     ]},
    {"id": "d5", "group": "console", "name": "the console resize commits through the model",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_slot", "slot": "setConsoleExpanded", "args": [True]},
         {"op": "exec_slot", "slot": "setConsoleHeight", "args": [240]},
         {"op": "assert_model", "prop": "consoleHeight", "value": 240, "budget": 10},
     ]},

    # ─── camera ───────────────────────────────────────────────
]
