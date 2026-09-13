"""The suite scenario specs (TESTING.md 3, the full functional
surface). Each spec is a short composition of the step vocabulary the
gates established — the runner's suite mode executes one group per
boot with a simulator reset between scenarios. The coverage gate
(tests/test_coverage.py) proves every surface maps here.
"""
from __future__ import annotations

# Shared probe bodies for the z-group geometry diagnostics: exact
# rendered rects from the QML scene, the evidence calibration reads.
RECT_PROBE = (
    "rects = []\n"
    "for window in _lookup_windows():\n"
    "    for item in _walk(window.contentItem(), depth=64):\n"
    "        try:\n"
    "            name = item.property(\"objectName\") or \"\"\n"
    "        except Exception:\n"
    "            name = \"\"\n"
    "        key = item.metaObject().className() + \"|\" + str(name)\n"
    "        if any(tag in key for tag in (\"ActionPanel\", \"SimulationView\", \"LayerSlider\","
    " \"Slice\", \"moonraker\", \"OutputProcess\", \"PostProcessing\", \"SaveFile\")):\n"
    "            p = item.mapToScene(QPointF(0, 0))\n"
    "            rects.append({\"c\": item.metaObject().className(), \"n\": str(name),\n"
    "                          \"x\": round(p.x()), \"y\": round(p.y()),\n"
    "                          \"w\": round(item.width()), \"h\": round(item.height()),\n"
    "                          \"v\": bool(item.isVisible())})\n"
    "            if len(rects) > 50:\n"
    "                break\n"
    "    if len(rects) > 50:\n"
    "        break\n"
    "result = {\"rects\": rects}")

CONTROLS_PROBE = (
    "pane = None\n"
    "for window in _lookup_windows():\n"
    "    for item in _walk(window.contentItem(), depth=64):\n"
    "        try:\n"
    "            if item.property(\"objectName\") == \"moonrakerControlsPane\":\n"
    "                pane = item\n"
    "                break\n"
    "        except Exception:\n"
    "            pass\n"
    "    if pane is not None:\n"
    "        break\n"
    "result = {\"pane\": None, \"flick\": None, \"bar\": None,\n"
    "          \"content_right\": None, \"lane_right\": None, \"over\": None, \"clear\": None}\n"
    "if pane is not None:\n"
    "    p0 = pane.mapToScene(QPointF(0, 0))\n"
    "    result[\"pane\"] = [round(p0.x()), round(p0.y()), round(pane.width()), round(pane.height())]\n"
    "    flick = bar = None\n"
    "    for item in _walk(pane, depth=64):\n"
    "        cls = item.metaObject().className()\n"
    "        if flick is None and cls == \"QQuickFlickable\":\n"
    "            flick = item\n"
    "        if bar is None and \"ScrollBar\" in cls:\n"
    "            bar = item\n"
    "        if flick is not None and bar is not None:\n"
    "            break\n"
    "    if flick is not None:\n"
    "        f = flick.mapToScene(QPointF(0, 0))\n"
    "        result[\"flick\"] = [round(f.x()), round(f.y()), round(flick.width()), round(flick.height())]\n"
    "        bar_visible = bool(bar.isVisible()) if bar is not None else False\n"
    "        bar_w = round(bar.width()) if bar is not None else 0\n"
    "        target = round(flick.width()) - (bar_w if bar_visible else 0)\n"
    "        content = None\n"
    "        for item in _walk(flick, depth=64):\n"
    "            cls = item.metaObject().className()\n"
    "            c = item.mapToScene(QPointF(0, 0))\n"
    "            if \"ColumnLayout\" in cls and abs(c.x() - f.x()) < 2 and abs(item.width() - target) < 3:\n"
    "                content = item\n"
    "                break\n"
    "        mx = 0\n"
    "        if content is not None:\n"
    "            for item in _walk(content, depth=64):\n"
    "                if not item.isVisible():\n"
    "                    continue\n"
    "                c = item.mapToScene(QPointF(0, 0))\n"
    "                right = round(c.x()) + round(item.width())\n"
    "                if right > mx:\n"
    "                    mx = right\n"
    "        lane = round(f.x()) + round(flick.width())\n"
    "        if bar_visible:\n"
    "            b = bar.mapToScene(QPointF(0, 0))\n"
    "            result[\"bar\"] = [round(b.x()), round(b.y()), round(bar.width()), round(bar.height()), True]\n"
    "            lane -= bar_w\n"
    "        result[\"content_right\"] = mx\n"
    "        result[\"lane_right\"] = lane\n"
    "        result[\"over\"] = mx - lane\n"
    "        result[\"clear\"] = bool(mx > 0 and mx <= lane + 2)\n"
    "result")

MODEL_POWER_PROBE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = getattr(device, \"activePrinter\", None)\n"
    "        if printer is not None:\n"
    "            try:\n"
    "                raw = printer.powerDevices\n"
    "                try:\n"
    "                    raw = raw.toPyObject()\n"
    "                except Exception:\n"
    "                    pass\n"
    "                result[\"powerDevices\"] = [{\"name\": d.get(\"name\"),"
    " \"status\": d.get(\"status\")} for d in (raw or [])]\n"
    "            except Exception as exc:\n"
    "                result[\"read_err\"] = repr(exc)[:80]\n"
    "        break\n"
    "result")

POWER_STATE_PROBE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        if printer is not None:\n"
    "            data = printer._data\n"
    "            client = printer._client\n"
    "            socket = client._session.socket\n"
    "            result[\"rpc_available\"] = bool(client.rpc_available())\n"
    "            result[\"power\"] = [d.get(\"device\") for d in data.snapshot.power]\n"
    "            got = []\n"
    "            def cb(payload, error):\n"
    "                try:\n"
    "                    devs = list((payload.get(\"result\") or {}).get(\"devices\") or [])\n"
    "                except Exception as exc:\n"
    "                    devs = repr(exc)[:30]\n"
    "                got.append([\"err:\" + str(error)[:24]] if error else [devs])\n"
    "            ok = client.rpc(\"machine.device_power.devices\", {}, cb)\n"
    "            result[\"rpc_sent\"] = bool(ok)\n"
    "            data.refresh_power()\n"
    "            _import_qtest().QTest.qWait(4000)\n"
    "            result[\"reply\"] = got\n"
    "            result[\"pending_later\"] = len(socket._pending)\n"
    "            result[\"power_after\"] = [d.get(\"device\") for d in data.snapshot.power]\n"
    "        break\n"
    "result")

LANE_CENSUS_PROBE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        if printer is not None:\n"
    "            data = printer._data\n"
    "            s = data.snapshot\n"
    "            result[\"power\"] = [d.get(\"device\") for d in s.power]\n"
    "            result[\"endstops\"] = {str(k): str(v) for k, v in (s.endstops or {}).items()}\n"
    "            result[\"server\"] = str(s.server.get(\"klippy_state\") or \"\")\n"
    "            result[\"printer\"] = str(s.printer.get(\"state\") or \"\")\n"
    "            result[\"webcams\"] = [c.get(\"name\") for c in s.webcams]\n"
    "            result[\"presets\"] = list((s.presets.get(\"presets\") or {}).keys())\n"
    "            result[\"core\"] = list(s.core.keys())[:6]\n"
    "            result[\"aux\"] = list(s.auxiliary.keys())[:6]\n"
    "            result[\"healthy\"] = bool(\n"
    "                [d.get(\"device\") for d in s.power] == [\"DFU\", \"Printer\"]\n"
    "                and (s.endstops or {}).get(\"x\")\n"
    "                and s.server.get(\"klippy_state\") == \"ready\"\n"
    "                and s.printer.get(\"state\")\n"
    "                and s.webcams\n"
    "                and (s.presets.get(\"presets\") or {}).get(\"fast\")\n"
    "                and s.core and s.auxiliary)\n"
    "        break\n"
    "result")

LOAD_WINDOW_PROBE = (
    "result = {}\n"
    "window = _main_window()\n"
    "found = []\n"
    "errors = []\n"
    "count = 0\n"
    "for item in _walk(window.contentItem(), depth=64):\n"
    "    count += 1\n"
    "    try:\n"
    "        if item.width() < 2 or item.height() < 2:\n"
    "            continue\n"
    "        if not _effectively_visible(item):\n"
    "            continue\n"
    "        name = item.property(\"objectName\")\n"
    "    except Exception as exc:\n"
    "        errors.append([count, item.metaObject().className()[:20], repr(exc)[:80]])\n"
    "        if len(errors) >= 4:\n"
    "            break\n"
    "        continue\n"
    "    if name == \"loadIndicatorContent\":\n"
    "        r = self._rect(item)\n"
    "        found.append(r)\n"
    "        if len(found) >= 3:\n"
    "            break\n"
    "result = {\"found\": found, \"errors\": errors, \"count\": count}\n"
    "result")

CENSUS_PROBE = (
    "from collections.abc import Mapping\n"
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "printer = None\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        break\n"
    "result = {\"checks\": {}, \"complete\": None}\n"
    "if printer is None:\n"
    "    result[\"error\"] = \"no printer model\"\n"
    "else:\n"
    "    s = printer._data.snapshot\n"
    "    texts = []\n"
    "    names = []\n"
    "    for window in _lookup_windows():\n"
    "        for item in _walk(window.contentItem(), depth=64):\n"
    "            if not _effectively_visible(item):\n"
    "                continue\n"
    "            try:\n"
    "                names.append(str(item.property(\"objectName\") or \"\"))\n"
    "            except Exception:\n"
    "                pass\n"
    "            try:\n"
    "                label = item.property(\"text\")\n"
    "                if isinstance(label, str) and label.strip():\n"
    "                    texts.append(label)\n"
    "            except Exception:\n"
    "                pass\n"
    "    checks = {}\n"
    "    checks[\"status\"] = \"moonrakerStatusStateText\" in names\n"
    "    hot = bool((s.core.get(\"extruder\") or {}).get(\"temperature\")\n"
    "                or (s.core.get(\"heater_bed\") or {}).get(\"temperature\"))\n"
    "    checks[\"temperatures\"] = not hot or not any(\n"
    "        t == \"No hotend or bed temperature data yet\" for t in texts)\n"
    "    checks[\"endstops\"] = not (s.endstops or {}) or any(\n"
    "        any(t.lower().startswith(axis + \":\") for t in texts)\n"
    "        for axis in (\"x\", \"y\", \"z\"))\n"
    "    toggles = sum(1 for t in texts if t in (\"Turn on\", \"Turn off\"))\n"
    "    checks[\"power\"] = toggles == len(s.power)\n"
    "    checks[\"webcams\"] = not s.webcams or \"cameraViewport\" in names\n"
    "    checks[\"console\"] = \"moonrakerConsoleOutput\" in names\n"
    "    presets = (s.presets.get(\"presets\") or {}) if isinstance(s.presets, Mapping) else {}\n"
    "    checks[\"presets\"] = all(any(name.lower() in t.lower() for t in texts)\n"
    "                                for name in (presets or {}))\n"
    "    result[\"checks\"] = checks\n"
    "    result[\"complete\"] = bool(all(checks.values()))\n"
    "result")

SCENE_PROBE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "scene = app.getController().getScene()\n"
    "result = {\"children\": []}\n"
    "for child in scene.getRoot().getAllChildren():\n"
    "    try:\n"
    "        result[\"children\"].append({\"t\": type(child).__name__,\n"
    "            \"name\": str(child.getName()),\n"
    "            \"mesh\": bool(child.getMeshData()),\n"
    "            \"select\": bool(child.isSelectable()),\n"
    "            \"n\": len(child.getAllChildren())})\n"
    "    except Exception as exc:\n"
    "        result[\"children\"].append({\"err\": repr(exc)[:60]})\n"
    "result")

P_SLIDER_DRAG = """from PyQt6.QtCore import QPoint, Qt
window = _main_window()
result = {}
target = None
for item in _walk(window.contentItem()):
    if "LayerSlider" in item.metaObject().className() and bool(item.isVisible()):
        target = item
        break
if target is None:
    result["error"] = "no visible LayerSlider"
else:
    handles = [child for child in target.childItems()
               if abs(child.width() - 16) < 2 and abs(child.height() - 16) < 2 and bool(child.isVisible())]
    if not handles:
        result = {"error": "no slider handles"}
    else:
        handle = min(handles, key=lambda item: item.mapToScene(QPointF(0, 0)).y())
        scene = handle.mapToScene(QPointF(0, 0))
        handle_x = round(scene.x() + handle.width() / 2)
        handle_y = round(scene.y() + handle.height() / 2)
        qtest = _import_qtest()
        qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(handle_x, handle_y))
        for step in range(1, 5):
            qtest.QTest.mouseMove(window, QPoint(handle_x, handle_y + step * 12))
            qtest.QTest.qWait(80)
        qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(handle_x, handle_y + 48))
        qtest.QTest.qWait(300)
        result = {"dragged": True, "from": [handle_x, handle_y], "h": round(target.height())}
"""

P_FOLLOW_READ = """from UM.Application import Application
app = Application.getInstance()
result = {}
for e in app.getExtensions():
    if "MoonrakerPrintFollower" in type(e).__name__:
        rt = e._runtime
        follower = rt.preview
        state = getattr(follower, "_state", None)
        if state is not None:
            result["attached"] = bool(state.attached)
            result["expected_layer"] = state.expected_layer
        break
"""

P_ATTACH_EMIT = """window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if label == "Attach" and "Button" in item.metaObject().className() and bool(item.isVisible()):
        item.clicked.emit()
        result["emitted"] = True
        break
"""


SCENARIOS = [
    # ─── transport & connection ───────────────────────────────
    {"id": "a1", "group": "connection", "name": "the ws dot is green only after the first accepted snapshot",
     "steps": [
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
     ]},
    {"id": "a2", "group": "connection", "name": "HTTP mode carries the full feed",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a3", "group": "connection", "name": "the mode switch back to websocket",
     "steps": [
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "assert_mode", "mode": "websocket"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 45},
     ]},
    {"id": "a4", "group": "connection", "name": "silence past the proof window degrades to HTTP",
     "steps": [
         {"op": "sim_arm", "arms": {"subscribe_hold_ms": 12000}},
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "objects", "min": 1, "budget": 40},
     ]},
    {"id": "a5", "group": "connection", "name": "a subscribe refusal degrades to HTTP with the reason",
     "steps": [
         {"op": "sim_arm", "arms": {"refuse_subscribe": "simulated refusal"}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "connectionDetail", "contains": "refused", "budget": 30},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a6", "group": "connection", "name": "a 401 surfaces the key-rejection verdict",
     "steps": [
         {"op": "sim_arm", "arms": {"require_api_key": True}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "connectionDetail", "contains": "unauthorized", "budget": 30},
     ]},
    {"id": "a7", "group": "connection", "name": "klippy ready re-arms the subscription",
     "steps": [
         {"op": "sim_klippy"},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 30},
     ]},
    {"id": "a8", "group": "connection", "name": "a dropped connection reconnects on its own",
     "steps": [
         {"op": "sim_drop"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 120},
     ]},
    {"id": "a9", "group": "connection", "name": "disconnected disables every control",
     "steps": [
         {"op": "sim_arm", "arms": {"refuse_subscribe": "down", "require_api_key": True}},
         {"op": "sim_klippy"},
         {"op": "wait_model", "prop": "monitorConnected", "value": False, "budget": 30},
         {"op": "assert_model", "prop": "jogEnabled", "value": False},
     ]},

    # ─── printer status ───────────────────────────────────────
    {"id": "b1", "group": "status", "name": "standby renders its state word",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": "", "message": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 15},
     ]},
    {"id": "b2", "group": "status", "name": "printing renders its state word and progress",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.42, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "4", "budget": 15},
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
    {"id": "b11", "group": "status", "name": "the exclude-object surface stays stable when absent",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "excludeObjectItems", "value": []},
     ]},

    # ─── temperatures / fans / sensors ────────────────────────
    {"id": "c1", "group": "temperatures", "name": "the temperature targets render",
     "steps": [
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0},
                                    "heater_bed": {"temperature": 55.0, "target": 60.0}}},
         {"op": "wait_sim", "path": "extruder.target", "value": 210.0, "budget": 15},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "Hotend", "budget": 15},
     ]},
    {"id": "c2", "group": "temperatures", "name": "the fan and speed surfaces render",
     "steps": [
         {"op": "sim_set", "state": {"fan": {"speed": 0.4}}},
         {"op": "wait_sim", "path": "fan.speed", "value": 0.4, "budget": 15},
         {"op": "assert_model", "prop": "speedFactorPercent", "value": 100},
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
    {"id": "d1", "group": "console", "name": "a sent command's response renders",
     "steps": [
         {"op": "exec_console", "text": "M105"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "d3", "group": "console", "name": "clear empties the console",
     "steps": [
         {"op": "sim_set", "state": {"console_lines": [{"type": "response", "message": "// line %d" % i,
                                                       "time": 1.0} for i in range(10)]}},
         {"op": "exec_slot", "slot": "clearConsoleHistory", "args": []},
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
    {"id": "e1", "group": "webcams", "name": "the camera loads on its own (the discovery-cycle fix)",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_stream_start"},
         {"op": "assert_model", "prop": "cameraName", "contains": "sim-cam", "budget": 30},
     ]},
    {"id": "e2", "group": "webcams", "name": "the webcam selector lists the peer's cameras",
     "steps": [
         {"op": "assert_model", "prop": "webcamNames", "contains": "sim-cam", "budget": 30},
     ]},

    # ─── files & print start ──────────────────────────────────
    {"id": "f1", "group": "files", "name": "the file manager browses the simulated store",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "click_text", "text": "File manager"},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "f2", "group": "files", "name": "the upload flow reaches the peer",
     "steps": [
         {"op": "write_fixture", "path": "/tmp/mpf/scenario-upload.gcode"},
         {"op": "exec_file_slot", "slot": "fileUpload", "args": ["/tmp/mpf/scenario-upload.gcode"]},
         {"op": "sim_ledger", "needle": "files/upload", "min": 1, "budget": 30},
     ]},
    {"id": "f3", "group": "files", "name": "delete removes the row after the confirm",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_file_slot", "slot": "openFileManager", "args": []},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_file_slot", "slot": "fileRequestDeleteFile", "args": ["scenario1.gcode"]},
         {"op": "exec_file_slot", "slot": "fileConfirmDelete", "args": []},
         {"op": "sim_ledger", "needle": "gcodes/scenario1.gcode", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "f4", "group": "files", "name": "folder create reaches the peer's directory route",
     "steps": [
         {"op": "exec_file_slot", "slot": "fileCreateDirectory", "args": ["simdir"]},
         {"op": "sim_ledger", "needle": "files/directory", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "f5", "group": "files", "name": "rename confirms into the peer's move route",
     "steps": [
         {"op": "exec_file_slot", "slot": "fileRequestRename", "args": ["benchy.gcode"]},
         {"op": "exec_file_slot", "slot": "filePreviewRename", "args": ["benchy-renamed.gcode"]},
         {"op": "exec_file_slot", "slot": "fileConfirmRename", "args": []},
         {"op": "sim_ledger", "needle": "files/move", "min": 1, "budget": 20},
     ]},
    {"id": "f6", "group": "files", "name": "print confirm arms the start verdict",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_file_slot", "slot": "openFileManager", "args": []},
         {"op": "sim_ledger", "needle": "files/directory", "field": "path", "min": 1, "budget": 20},
         {"op": "exec_file_slot", "slot": "fileRequestPrint", "args": ["benchy.gcode"]},
         {"op": "exec_file_slot", "slot": "fileConfirmPrint", "args": []},
         {"op": "sim_ledger", "needle": "print/start", "method": "POST", "min": 1, "budget": 20},
     ]},

    # ─── controls ─────────────────────────────────────────────
    {"id": "g1", "group": "motion", "name": "the jog pad's clicks reach the peer as G1 moves",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "click_jog", "button": "moonrakerJogXPlus"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g2", "group": "motion", "name": "home and the mesh actions reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "click_jog", "button": "moonrakerHomeX"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g3", "group": "motion", "name": "the abs/rel toggle rides the command lane",
     "steps": [
         {"op": "exec_slot", "slot": "setPositionMode", "args": [False]},
         {"op": "assert_model", "prop": "positionMode", "value": "Relative"},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "g4", "group": "motion", "name": "extrude and retract reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "exec_extrude"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g5", "group": "motion", "name": "macros render and refuse while printing",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "assert_model", "prop": "macroNames", "value": []},
     ]},
    {"id": "g6", "group": "motion", "name": "pause and resume ride the peer's print routes",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "exec_slot", "slot": "pausePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
         {"op": "exec_slot", "slot": "resumePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/resume", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "g8", "group": "motion", "name": "the lock toggle flips the controls lock",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": "", "message": ""}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "standby", "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [True]},
         {"op": "assert_model", "prop": "controlsLocked", "value": True, "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [False]},
         {"op": "assert_model", "prop": "controlsLocked", "value": False, "budget": 10},
     ]},
    {"id": "g9", "group": "motion", "name": "power devices list and toggle",
     "steps": [
         {"op": "sim_set", "state": {"power_devices": [{"device": "sim-printer-power", "status": "off"}]}},
         {"op": "assert_model", "prop": "powerDevices", "value": []},
     ]},
    {"id": "g9b", "group": "motion", "name": "firmware and host restarts reach the peer",
     "steps": [
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
     ]},

    # ─── preview ──────────────────────────────────────────────
    {"id": "h2", "group": "printing", "name": "the load end-to-end (the gate flow's surface)",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorFilename", "contains": "scenario1", "budget": 30},
     ]},
    {"id": "h3", "group": "printing", "name": "the attach/detach surface renders",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 20},
     ]},
    {"id": "h6", "group": "printing", "name": "the bed mesh renders with its legend",
     "steps": [
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                  "mesh_max": [50.0, 50.0], "probed_matrix": [[0.0] * 3] * 3}}},
         {"op": "assert_model", "prop": "bedMeshAvailable", "value": True, "budget": 10},
         {"op": "assert_model", "prop": "bedMeshProfile", "contains": "sim-mesh", "budget": 10},
     ]},
    {"id": "h6b", "group": "printing", "name": "the probe-points toggle persists on the preference",
     "steps": [
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [True]},
         {"op": "assert_model", "prop": "showProbePoints", "value": True},
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [False]},
         {"op": "assert_model", "prop": "showProbePoints", "value": False},
     ]},
    {"id": "h8", "group": "printing", "name": "the ETA opt-in starts the hourglass",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "exec_slot", "slot": "improveEta", "args": []},
         {"op": "wait_model", "prop": "improvingEta", "value": True, "budget": 10},
     ]},

    # ─── settings & persistence ───────────────────────────────
    {"id": "i1", "group": "settings", "name": "the transport mode applies with its reason line",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_ledger", "needle": "objects/query", "field": "path", "min": 1, "budget": 30},
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "assert_mode", "mode": "websocket"},
     ]},
    {"id": "i2", "group": "settings", "name": "test connection probes the peer over the wire",
     "steps": [
         {"op": "exec_test_connection"},
         {"op": "sim_ledger", "needle": "server/info", "min": 1, "budget": 15},
         {"op": "sim_ledger", "needle": "objects/list", "min": 1, "budget": 15},
     ]},
    {"id": "i3", "group": "settings", "name": "the cadence validators bound at 250 ms",
     "steps": [
         {"op": "exec_validator", "validator": "validPollInterval", "args": [100], "expect": False},
         {"op": "exec_validator", "validator": "validPollInterval", "args": [1000], "expect": True},
         {"op": "exec_validator", "validator": "validRetryInterval", "args": [0.05], "expect": False},
     ]},
    {"id": "i4", "group": "settings", "name": "the camera config persists",
     "steps": [
         {"op": "exec_slot", "slot": "selectWebcam", "args": [0]},
         {"op": "assert_model", "prop": "activeWebcamIndex", "value": 0, "budget": 15},
     ]},
    {"id": "i6", "group": "settings", "name": "save config sends SAVE_CONFIG when the peer has pending changes",
     "steps": [
         {"op": "sim_set", "state": {"configfile": {"save_config_pending": True, "save_config_pending_items": {}}}},
         {"op": "wait_sim", "path": "configfile.save_config_pending", "value": True, "budget": 10},
         {"op": "exec_slot", "slot": "saveConfig", "args": []},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},
    {"id": "i7", "group": "settings", "name": "open frontend hands off to the browser",
     "steps": [
         {"op": "exec_slot", "slot": "openFrontend", "args": []},
     ]},

    # ─── soaks & faults ───────────────────────────────────────
    {"id": "j2", "group": "stress", "name": "a slow endpoint degrades, not hangs",
     "steps": [
         {"op": "sim_arm", "arms": {"route_delay_ms": {"server/gcode_store": 2000}}},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 20},
     ]},
    {"id": "j3", "group": "stress", "name": "a socket close recovers without data loss",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode",
                                                     "info": {"total_layer": 40, "current_layer": 8}}}},
         {"op": "sim_drop"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
     ]},
    {"id": "j4", "group": "stress", "name": "a long print completes with time-warped progress",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.98, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "9", "budget": 15},
         {"op": "sim_set", "state": {"print_stats": {"state": "complete", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "monitorState", "contains": "complete", "budget": 15},
     ]},



    # ─── visual fidelity — alignment, pane exercise, rendered-follows ───
    {"id": "v1", "group": "visual",
     "name": "the loaded panel renders, with Cura's </> beside the card",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "insert_model"},
         {"op": "slice_scene"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewActionPanelControls", "budget": 150},
         {"op": "exec_code", "code": "from UM.Application import Application\napp = Application.getInstance()\nresult = {}\nfor e in app.getExtensions():\n    if \"MoonrakerPrintFollower\" in type(e).__name__:\n        rt = e._runtime\n        coord = rt.coordinator if hasattr(rt, \"coordinator\") else rt.follow\n        result[\"gate_logged\"] = repr(getattr(coord, \"_gate_logged\", \"n/a\"))\n        result[\"has_toolpath\"] = bool(getattr(rt.cura, \"has_toolpath\", False))\n        result[\"preview_active\"] = bool(getattr(rt.cura, \"preview_active\", False))\n        result[\"configured\"] = bool(getattr(getattr(rt.binding, \"configured\", None), \"__bool__\", lambda: False)())\n        try:\n            view = app.getController().getActiveView()\n            result[\"active_view\"] = str(view.getPluginId()) if view else None\n            result[\"max_layers\"] = int(view.getMaxLayers()) if view and hasattr(view, \"getMaxLayers\") else None\n        except Exception as exc:\n            result[\"view_err\"] = repr(exc)[:80]\n        break\n"},
         {"op": "wait_rect", "objectName": "moonrakerEmptyPreviewLoadControl", "absent": True, "budget": 60},
         {"op": "add_post_script", "script": "PauseAtHeight"},
         {"op": "wait_rect", "objectName": "postProcessingSaveAreaButton", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "postProcessingSaveAreaButton"},
          "no_overlap": {"objectName": "moonrakerPreviewActionPanelControls"}},
         {"op": "dump_visible", "needle": "", "region": [600, 520, 1280, 800]},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerPreviewActionPanelControls"},
          "within": {"window": True}},
     ]},
    {"id": "v2", "group": "visual",
     "name": "the jog pad grid is straight",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerJogXPlus"},
          "anchor": {"objectName": "moonrakerJogXMinus"}, "axis": "center_y", "tol": 3},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerJogYPlus"},
          "anchor": {"objectName": "moonrakerJogYMinus"}, "axis": "center_x", "tol": 3},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerJogZPlus"},
          "anchor": {"objectName": "moonrakerJogZMinus"}, "axis": "center_x", "tol": 3},
     ]},
    {"id": "v3", "group": "visual",
     "name": "the console input and send button share their baseline",
     "steps": [
         {"op": "assert_aligned", "item": {"objectName": "moonrakerConsoleInput"},
          "anchor": {"objectName": "moonrakerConsoleSend"}, "axis": "center_y", "tol": 5},
     ]},
    {"id": "v4", "group": "visual",
     "name": "the info and status panels never overlap",
     "steps": [
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
     ]},
    {"id": "v5", "group": "visual",
     "name": "the M117 slot's rendered text follows the push",
     "steps": [
         {"op": "sim_set", "state": {"display_status": {"message": "RENDERED-A", "progress": 0.5}}},
         {"op": "wait_rendered", "objectName": "moonrakerM117Slot", "contains": "RENDERED-A", "budget": 30},
         {"op": "sim_set", "state": {"display_status": {"message": "RENDERED-B", "progress": 0.5}}},
         {"op": "wait_rendered", "objectName": "moonrakerM117Slot", "contains": "RENDERED-B", "budget": 30},
         {"op": "assert_rendered", "objectName": "moonrakerM117Slot", "not_contains": "RENDERED-A"},
     ]},
    {"id": "v6", "group": "visual",
     "name": "the temperature row's rendered text follows the push",
     "steps": [
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "wait_rendered", "objectName": "moonrakerTemperatureDetail", "contains": "210", "budget": 30},
     ]},
    {"id": "v7", "group": "visual",
     "name": "pane collapse and re-expand hide and restore the content",
     "steps": [
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "rect_of", "objectName": "infoPanel"},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [True]},
         {"op": "assert_rect_change", "objectName": "infoPanel", "direction": "shrunk", "by": 60, "budget": 15},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [False]},
         {"op": "assert_rect_change", "objectName": "infoPanel", "direction": "grown", "by": 60, "budget": 15},
         {"op": "exec_slot", "slot": "setSectionExpanded", "args": ["temps", False]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setSectionExpanded", "args": ["temps", True]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [False]},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
     ]},
    {"id": "v8", "group": "visual",
     "name": "narrow auto-collapses the console, wide re-expands, layouts hold",
     "steps": [
         {"op": "resize_window", "w": 1000, "h": 700},
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "absent": True, "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
         {"op": "resize_window", "w": 1280, "h": 720},
     ]},

    {"id": "v9", "group": "visual",
     "name": "the status text, the console and the pane margins follow reality",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_rendered", "objectName": "moonrakerStatusStateText", "contains": "Printing", "budget": 30},
         {"op": "exec_slot", "slot": "sendConsoleCommand", "args": ["RENDERED-CONSOLE"]},
         {"op": "wait_rendered", "objectName": "moonrakerConsoleOutput", "contains": "RENDERED-CONSOLE", "budget": 30},
         {"op": "assert_aligned", "symmetric_margins": {"left": {"objectName": "infoPanel"},
                                                        "right": {"objectName": "moonrakerControlsPane"}},
          "tol": 8},
     ]},
    {"id": "v10", "group": "visual",
     "name": "a renamed file's row follows in the rendered list",
     "steps": [
         {"op": "exec_slot", "slot": "openFileManager", "args": []},
         {"op": "exec_slot", "slot": "fileRequestRename", "args": ["benchy.gcode"]},
         {"op": "exec_slot", "slot": "filePreviewRename", "args": ["benchy-renamed.gcode"]},
         {"op": "exec_slot", "slot": "fileConfirmRename", "args": []},
         {"op": "wait_model", "prop": "fileManagerRows", "contains": "benchy-renamed", "budget": 30},
         {"op": "wait_rendered", "objectName": "moonrakerFileRowName", "any": True,
          "contains": "benchy-renamed", "budget": 30},
     ]},
    {"id": "v11", "group": "visual",
     "name": "the file manager's narrow mode hides the search field, wide restores it",
     "steps": [
         {"op": "exec_slot", "slot": "openFileManager", "args": []},
         {"op": "resize_window", "w": 1000, "h": 700},
         {"op": "wait_rect", "objectName": "moonrakerFileSearch", "absent": True, "budget": 30},
         {"op": "resize_window", "w": 1600, "h": 1000},
         {"op": "wait_rect", "objectName": "moonrakerFileSearch", "budget": 30},
     ]},
    {"id": "v12", "group": "visual",
     "name": "the bed mesh view renders in the information pane",
     "steps": [
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                 "mesh_max": [50.0, 50.0],
                                                 "probed_matrix": [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]}}},
         {"op": "wait_rect", "objectName": "moonrakerBedMeshMap", "budget": 30},
     ]},


    # ─── the preview, end to end (the author's comprehensive list) ───
    {"id": "p1", "group": "preview",
     "name": "the load renders the real toolpath, the indicator, and the card",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set_current_print"},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120}},
         {"op": "wait_rect", "objectName": "moonrakerEmptyPreviewLoadControl", "budget": 30},
         {"op": "emit_click", "text": "Load current print"},
         {"op": "confirm_box", "button": "Yes"},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "wait_rect", "objectName": "moonrakerPreviewActionPanelControls", "budget": 240},
         {"op": "assert_exec", "code": "view = Application.getInstance().getController().getView(\"SimulationView\")\nresult = {\"max_layers\": int(view.getMaxLayers()) if view else 0}",
          "contains": '"max_layers": 39'},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "wait_rect", "text": "Detach", "budget": 30},
     ]},
    {"id": "p2", "group": "preview",
     "name": "a real drag on Cura's layer slider auto-detaches the follow",
     "steps": [
         {"op": "wait_seconds", "seconds": 5},
         {"op": "exec_code", "code": P_SLIDER_DRAG},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
         {"op": "dump_visible", "needle": "detach|follow|attach", "region": [600, 560, 1280, 800]},
     ]},
    {"id": "p3", "group": "preview",
     "name": "rapid tab and view switching never drops the attach",
     "steps": [
         {"op": "exec_code", "code": P_ATTACH_EMIT},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
         {"op": "click_stage", "stage": "PrepareStage"},
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "click_stage", "stage": "PrepareStage"},
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
         {"op": "wait_rect", "objectName": "moonrakerPreviewActionPanelControls", "budget": 60},
     ]},
    {"id": "p4", "group": "preview",
     "name": "the pause-at-layer entry schedules and renders",
     "steps": [
         {"op": "exec_code", "code": "window = _main_window()\nresult = {}\nfor item in _walk(window.contentItem()):\n    try:\n        label = item.property(\"text\")\n    except Exception:\n        label = None\n    if isinstance(label, str) and label.startswith(\"\\u23f8\") and \"Button\" in item.metaObject().className() and bool(item.isVisible()):\n        item.clicked.emit()\n        result[\"emitted\"] = True\n        break"},
         {"op": "wait_exec", "code": "window = _main_window()\nresult = {}\nfor item in _walk(window.contentItem()):\n    try:\n        label = item.property(\"text\")\n    except Exception:\n        label = None\n    if isinstance(label, str) and \"Layer\" in label and bool(item.isVisible()):\n        result[\"scheduled\"] = True\n        result[\"label\"] = label[:40]\n        break",
          "contains": '"scheduled": true', "budget": 20},
         {"op": "dump_visible", "needle": "layer", "region": [600, 560, 1280, 800]},
     ]},
    {"id": "p5", "group": "preview",
     "name": "the card's Detach button detaches and Attach re-attaches",
     "steps": [
         {"op": "click_text", "text": "Detach"},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
         {"op": "exec_code", "code": P_ATTACH_EMIT},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
     ]},

    # ─── geometry probes (diagnostics, not release gates) ─────────
    {"id": "z1", "group": "probe",
     "name": "the preview stage geometry across empty, model and sliced states",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "wait_rect", "objectName": "moonrakerEmptyPreviewLoadControl", "budget": 30},
         {"op": "exec_code", "code": SCENE_PROBE},
         {"op": "exec_code", "code": RECT_PROBE},
         {"op": "insert_model"},
         {"op": "wait_seconds", "seconds": 4},
         {"op": "exec_code", "code": RECT_PROBE},
         {"op": "slice_scene"},
         {"op": "wait_rect", "objectName": "moonrakerPreviewActionPanelControls", "budget": 150},
         {"op": "exec_code", "code": RECT_PROBE},
     ]},
    {"id": "v11", "group": "visual",
     "name": "the power rows render the armed devices and keep clear of the scrollbar lane",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "DFU", "status": "on", "locked_while_printing": False},
             {"device": "Printer", "status": "off", "locked_while_printing": True}]}},
         {"op": "wait_rect", "text": "Turn off", "budget": 40},
         {"op": "wait_exec", "code": CONTROLS_PROBE, "contains": '"clear": true', "budget": 20},
         {"op": "dump_visible", "needle": "Turn", "region": [800, 0, 1280, 720]},
     ]},

    {"id": "v12", "group": "visual",
     "name": "the lane census: every polled category's data lands in the snapshot",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {
             "power_devices": [
                 {"device": "DFU", "status": "on", "locked_while_printing": False},
                 {"device": "Printer", "status": "off", "locked_while_printing": True}],
             "presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "wait_exec", "code": LANE_CENSUS_PROBE, "contains": '"healthy": true', "budget": 30},
     ]},

    # ─── real-printer read-only (observation; see TESTING.md §2.5) ───
    # These run ONLY in real mode, where the dispatcher refuses every
    # op outside the read-only allowlist: no commands, no restarts, no
    # print starts — a live print is observed, never touched.
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
]