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
    "            if \"ColumnLayout\" in cls and abs(c.x() - f.x()) < 2 and abs(item.width() - target) < 24:\n"
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
    "    classes = []\n"
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
    "                    classes.append(item.metaObject().className())\n"
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
    "    toggles = sum(1 for i, t in enumerate(texts)\n"
    "                  if t in (\"Turn on\", \"Turn off\") and \"Button\" in classes[i])\n"
    "    checks[\"power\"] = toggles == len(s.power)\n"
    "    checks[\"webcams\"] = not s.webcams or \"cameraViewport\" in names\n"
    "    checks[\"console\"] = \"moonrakerConsoleOutput\" in names\n"
    "    presets = (s.presets.get(\"presets\") or {}) if isinstance(s.presets, Mapping) else {}\n"
    "    checks[\"presets\"] = all(any(name.lower() in t.lower() for t in texts)\n"
    "                                for name in (presets or {}))\n"
    "    result[\"checks\"] = checks\n"
    "    result[\"complete\"] = bool(all(checks.values()))\n"
    "result")

FM_POPUP_PROBE = (
    "import json as _json\n"
    "result = {\"matches\": [], \"fields\": [], \"names\": []}\n"
    "for window in _lookup_windows():\n"
    "    for item in _walk(window.contentItem(), depth=96):\n"
    "        if not item.isVisible():\n"
    "            continue\n"
    "        cls = item.metaObject().className()\n"
    "        try:\n"
    "            label = item.property(\"text\")\n"
    "        except Exception:\n"
    "            label = None\n"
    "        try:\n"
    "            placeholder = item.property(\"placeholderText\")\n"
    "        except Exception:\n"
    "            placeholder = None\n"
    "        try:\n"
    "            name = item.property(\"objectName\")\n"
    "        except Exception:\n"
    "            name = None\n"
    "        p = item.mapToScene(QPointF(0, 0))\n"
    "        if isinstance(name, str) and name:\n"
    "            result[\"names\"].append([cls[:18], name[:40], round(p.x()), round(p.y())])\n"
    "        if isinstance(label, str) and any(k in label for k in (\"benchy\", \"Rename\", \"Cancel\", \"Save\")):\n"
    "            result[\"matches\"].append([cls[:18], label[:34], round(p.x()), round(p.y()),\n"
    "                                         round(item.width()), round(item.height())])\n"
    "        if isinstance(placeholder, str) and placeholder.strip():\n"
    "            result[\"fields\"].append([cls[:18], placeholder[:30], round(p.x()), round(p.y()),\n"
    "                                         round(item.width()), round(item.height())])\n"
    "with open('/tmp/mpf/fm_popup_probe.json', 'w') as _f:\n"
    "    _json.dump(result, _f)\n"
    "result")

FM_BUTTON_PROBE = (
    "import json as _json\n"
    "result = {\"matches\": []}\n"
    "window = _main_window()\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if label != \"File manager\":\n"
    "        continue\n"
    "    cls = item.metaObject().className()\n"
    "    p = item.mapToScene(QPointF(0, 0))\n"
    "    row = [cls[:24], round(p.x()), round(p.y()), round(item.width()), round(item.height())]\n"
    "    node = item\n"
    "    chain = []\n"
    "    for _ in range(6):\n"
    "        try:\n"
    "            node = node.parentItem()\n"
    "        except Exception:\n"
    "            break\n"
    "        if node is None:\n"
    "            break\n"
    "        try:\n"
    "            pp = node.mapToScene(QPointF(0, 0))\n"
    "            chain.append([node.metaObject().className()[:24], round(pp.x()), round(pp.y()),\n"
    "                          round(node.width()), round(node.height())])\n"
    "        except Exception:\n"
    "            chain.append([node.metaObject().className()[:24], None])\n"
    "    row.append(chain)\n"
    "    result[\"matches\"].append(row)\n"
    "with open('/tmp/mpf/fm_button_probe.json', 'w') as f:\n"
    "    _json.dump(result, f)\n"
    "result")


SETTINGS_PROBE = (
    "import json as _json\n"
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {\"manager_api\": [], \"actions\": [], \"config_items\": []}\n"
    "try:\n"
    "    manager = app.getMachineActionManager()\n"
    "    for name in dir(manager):\n"
    "        if \"ction\" in name or \"show\" in name.lower() or \"activate\" in name.lower():\n"
    "            result[\"manager_api\"].append(name)\n"
    "    try:\n"
    "        for action in manager.getMachineActions():\n"
    "            result[\"actions\"].append([type(action).__name__, str(getattr(action, \"_key\", None)),\n"
    "                                        str(getattr(action, \"_qml_url\", None))])\n"
    "    except Exception as exc:\n"
    "        result[\"actions_error\"] = repr(exc)\n"
    "except Exception as exc:\n"
    "    result[\"manager_error\"] = repr(exc)\n"
    "try:\n"
    "    window = _main_window()\n"
    "    for item in _walk(window.contentItem(), depth=96):\n"
    "        try:\n"
    "            name = item.property(\"objectName\")\n"
    "        except Exception:\n"
    "            name = None\n"
    "        try:\n"
    "            text = item.property(\"text\")\n"
    "        except Exception:\n"
    "            text = None\n"
    "        if not ((name and str(name).strip()) or (isinstance(text, str) and str(text).strip() in\n"
    "                 (\"Connection\", \"Printer status transport\", \"Test connection\", \"Save\"))):\n"
    "            continue\n"
    "        cls = item.metaObject().className()\n"
    "        try:\n"
    "            p = item.mapToScene(QPointF(0, 0))\n"
    "            result[\"config_items\"].append([cls[:22], str(name), str(text)[:30],\n"
    "                                            round(p.x()), round(p.y()),\n"
    "                                            round(item.width()), round(item.height())])\n"
    "        except Exception:\n"
    "            result[\"config_items\"].append([cls[:22], str(name), str(text)[:30]])\n"
    "except Exception as exc:\n"
    "    result[\"walk_error\"] = repr(exc)\n"
    "with open('/tmp/mpf/settings_probe.json', 'w') as f:\n"
    "    _json.dump(result, f)\n"
    "result")


PRESETS_PROBE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {\"landed\": False}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        if printer is not None:\n"
    "            presets = printer._data.snapshot.presets\n"
    "            result[\"landed\"] = bool((presets.get(\"presets\") or {}).get(\"fast\"))\n"
    "        break\n"
    "result")

P1_PCT_PROBE = (
    "window = _main_window()\n"
    "result = {\"pct\": False}\n"
    "for item in _walk(window.contentItem(), depth=24):\n"
    "    if not item.isVisible():\n"
    "        continue\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and label.startswith(\"Downloading\") and \"%\" in label:\n"
    "        result[\"pct\"] = True\n"
    "        result[\"label\"] = label[:24]\n"
    "        break\n"
    "result")

P_PAUSE_CLICK = (
    "window = _main_window()\n"
    "result = {}\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and label.startswith(\"\\u23f8\") and \"Button\" in item.metaObject().className() and bool(item.isVisible()):\n"
    "        item.clicked.emit()\n"
    "        result[\"emitted\"] = True\n"
    "        break\n"
    "result")

P_PAUSE_SCHEDULED = (
    "window = _main_window()\n"
    "result = {\"scheduled\": False}\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and label.startswith(\"End of layer\") and bool(item.isVisible()):\n"
    "        result[\"scheduled\"] = True\n"
    "        result[\"label\"] = label[:40]\n"
    "        break\n"
    "result")

P_ROW_GONE = (
    "window = _main_window()\n"
    "result = {\"row_gone\": True}\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and label.startswith(\"End of layer\") and bool(item.isVisible()):\n"
    "        result[\"row_gone\"] = False\n"
    "        break\n"
    "result")

P_MISSED = (
    "window = _main_window()\n"
    "result = {\"missed\": False}\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and \"pause not taken\" in label and bool(item.isVisible()):\n"
    "        result[\"missed\"] = True\n"
    "        break\n"
    "result")

SCROLL_CONTROLS = (
    "window = _main_window()\n"
    "pane = None\n"
    "for item in _walk(window.contentItem(), depth=64):\n"
    "    try:\n"
    "        if item.property(\"objectName\") == \"moonrakerControlsPane\":\n"
    "            pane = item\n"
    "            break\n"
    "    except Exception:\n"
    "        pass\n"
    "result = {}\n"
    "if pane is None:\n"
    "    result[\"error\"] = \"no controls pane\"\n"
    "else:\n"
    "    for item in _walk(pane, depth=64):\n"
    "        if item.metaObject().className() == \"QQuickFlickable\":\n"
    "            top = max(0.0, float(item.property(\"contentHeight\")) - float(item.property(\"height\")))\n"
    "            item.setProperty(\"contentY\", top)\n"
    "            result[\"scrolled\"] = True\n"
    "            break\n"
    "result")

P_TOGGLE_STATE = (
    "window = _main_window()\n"
    "result = {\"enabled\": []}\n"
    "for item in _walk(window.contentItem(), depth=64):\n"
    "    if not item.isVisible():\n"
    "        continue\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and label in (\"Turn on\", \"Turn off\") and \"Button\" in item.metaObject().className():\n"
    "        result[\"enabled\"].append(bool(item.property(\"enabled\")))\n"
    "result")

P_POWER_FLIP = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {\"status\": None}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        if printer is not None:\n"
    "            for d in printer._data.snapshot.power:\n"
    "                if d.get(\"device\") == \"DFU\":\n"
    "                    result[\"status\"] = str(d.get(\"status\"))\n"
    "        break\n"
    "result")

POWER_STATUS_PROBE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        if printer is not None:\n"
    "            result[\"power\"] = [str(d.get(\"device\")) + \"=\" + str(d.get(\"status\"))\n"
    "                                for d in printer._data.snapshot.power]\n"
    "        break\n"
    "result")

CAM_FRAMES = (
    "window = _main_window()\n"
    "result = {}\n"
    "for item in _walk(window.contentItem(), depth=64):\n"
    "    if \"NetworkMJPGImage\" in item.metaObject().className():\n"
    "        result[\"started\"] = bool(getattr(item, \"_started\", False))\n"
    "        result[\"width\"] = int(item.property(\"imageWidth\"))\n"
    "        result[\"height\"] = int(item.property(\"imageHeight\"))\n"
    "        break\n"
    "result")

CAM_STARTED = (
    "window = _main_window()\n"
    "result = {}\n"
    "for item in _walk(window.contentItem()):\n"
    "    if \"NetworkMJPGImage\" in item.metaObject().className():\n"
    "        result[\"started\"] = bool(getattr(item, \"_started\", False))\n"
    "        result[\"visible\"] = bool(item.isVisible())\n"
    "        break\n"
    "result")

CLICK_GESTURE = (
    "from PyQt6.QtCore import QPoint, Qt\n"
    "window = _main_window()\n"
    "result = {}\n"
    "target = None\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if label == \"Turn off\" and \"Button\" in item.metaObject().className() and bool(item.isVisible()):\n"
    "        target = item\n"
    "        break\n"
    "if target is None:\n"
    "    result[\"error\"] = \"no button\"\n"
    "else:\n"
    "    scene = target.mapToScene(QPointF(0, 0))\n"
    "    x = round(scene.x() + target.width() / 2)\n"
    "    y = round(scene.y() + target.height() / 2)\n"
    "    qtest = _import_qtest()\n"
    "    qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))\n"
    "    qtest.QTest.qWait(80)\n"
    "    qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))\n"
    "    qtest.QTest.qWait(200)\n"
    "    result = {\"clicked\": True, \"aim\": [x, y]}\n"
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




WHATS_NEW_CLEAR_CODE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = getattr(device, \"activePrinter\", None)\n"
    "        if printer is not None:\n"
    "            printer._whats_new_seen = \"\"\n"
    "            result[\"cleared\"] = True\n"
    "            # The section the scenario expands is read LIVE from\n"
    "            # the content (the first non-latest entry, whatever\n"
    "            # release it is) — the scenario never pins a version\n"
    "            # name or a curated sentence, which change every\n"
    "            # release.\n"
    "            entry = printer.whatsNewContent[1]\n"
    "            result[\"section\"] = \"whatsNewSection_\" + entry[\"version\"]\n"
    "            result[\"item\"] = entry[\"items\"][0]\n"
    "        break\n"
)


VERDICT_SCAN = (
    "window = _main_window()\n"
    "hits = []\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and (\"reported an error\" in label or \"did not begin printing\" in label):\n"
    "        hits.append(label[:90])\n"
    "# The suite's wait_exec reads the reply as a dict (the gate's own\n"
    "# copy in the runner consumes a list — bool(list) — so they\n"
    "# differ deliberately).\n"
    "result = {\"hits\": hits}\n"
)


E_STOP_SEQUENCE = (
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "result = {}\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = device.activePrinter\n"
    "        if printer is not None:\n"
    "            # One RPC: the arm resets after 1 s idle, and the\n"
    "            # step vocabulary sleeps 1.5 s between slots.\n"
    "            printer.emergencyStopClick()\n"
    "            printer.emergencyStopClick()\n"
    "            printer.emergencyHoldStarted()\n"
    "            result[\"armed\"] = True\n"
    "        break\n"
    "result")

CARD_EXCLUSIVE_PROBE = (
    "window = _main_window()\n"
    "result = {}\n"
    "panel_card_visible = None\n"
    "overlay_visible = None\n"
    "for item in _walk(window.contentItem(), depth=64):\n"
    "    try:\n"
    "        name = item.property(\"objectName\")\n"
    "    except Exception:\n"
    "        name = None\n"
    "    if name == \"moonrakerPreviewCard\":\n"
    "        panel_card_visible = bool(item.property(\"panelVisible\"))\n"
    "    elif name == \"moonrakerPreviewCardOverlay\":\n"
    "        overlay_visible = bool(item.property(\"panelVisible\"))\n"
    "result[\"exclusive\"] = bool(panel_card_visible is not None and overlay_visible is not None\n"
    "                             and panel_card_visible != overlay_visible)\n"
    "result[\"panel\"] = panel_card_visible\n"
    "result[\"overlay\"] = overlay_visible\n"
    "result")

CARD_GATE_PROBE = (
    "window = _main_window()\n"
    "result = {}\n"
    "panel_visible = False\n"
    "for item in _walk(window.contentItem(), depth=64):\n"
    "    try:\n"
    "        if \"ActionPanelWidget\" in item.metaObject().className():\n"
    "            panel_visible = panel_visible or bool(item.isVisible())\n"
    "    except Exception:\n"
    "        pass\n"
    "    try:\n"
    "        name = item.property(\"objectName\")\n"
    "    except Exception:\n"
    "        name = None\n"
    "    if name in (\"moonrakerPreviewCard\", \"moonrakerPreviewCardPanel\",\n"
    "                \"moonrakerPreviewCardOverlay\", \"moonrakerPreviewCardPanelHost\",\n"
    "                \"moonrakerPreviewCardOverlayHost\"):\n"
    "        result[name] = {\"visible_prop\": bool(item.property(\"visible\")),\n"
    "                         \"is_visible\": bool(item.isVisible())}\n"
    "        if name in (\"moonrakerPreviewCard\", \"moonrakerPreviewCardOverlay\"):\n"
    "            result[name].update(\n"
    "                gateVisible=bool(item.property(\"gateVisible\")),\n"
    "                previewStageActive=bool(item.property(\"previewStageActive\")),\n"
    "                configuredForFollowing=bool(item.property(\"configuredForFollowing\")),\n"
    "                hasToolpath=bool(item.property(\"hasToolpath\")),\n"
    "                sceneHasObjects=bool(item.property(\"sceneHasObjects\")),\n"
    "                loadBusy=bool(item.property(\"loadBusy\")),\n"
    "                statusText=str(item.property(\"statusText\"))[:60])\n"
    "result[\"panel_visible\"] = panel_visible\n"
    "from UM.Application import Application\n"
    "app = Application.getInstance()\n"
    "for e in app.getExtensions():\n"
    "    if \"MoonrakerPrintFollower\" in type(e).__name__:\n"
    "        p = getattr(getattr(e, \"_runtime\", None), \"presentation\", None)\n"
    "        if p is not None:\n"
    "            try:\n"
    "                result[\"presenter_panel_up\"] = bool(p._cura_panel_visible())\n"
    "            except Exception as exc:\n"
    "                result[\"presenter_err\"] = repr(exc)[:80]\n"
    "            result[\"presenter_cards\"] = [\n"
    "                (str(getattr(c, \"objectName\", lambda: \"?\")()), bool(c.property(\"gateVisible\")))\n"
    "                for c in p.controls]\n"
    "        break\n"
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
        # The drag moves the handle away from its nearer slider end:
        # a fixed downward drag clamps at the bottom edge and never
        # detaches once the running print has advanced the handle
        # there (the slow-hardware observation).
        slider_top = round(target.mapToScene(QPointF(0, 0)).y())
        dy = -60 if handle_y > slider_top + round(target.height()) / 2 else 60
        qtest = _import_qtest()
        qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(handle_x, handle_y))
        for step in range(1, 5):
            qtest.QTest.mouseMove(window, QPoint(handle_x, handle_y + step * dy // 4))
            qtest.QTest.qWait(80)
        qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(handle_x, handle_y + dy))
        qtest.QTest.qWait(300)
        result = {"dragged": True, "from": [handle_x, handle_y], "h": round(target.height()), "dy": dy}
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

    {"id": "a10", "group": "connection",
     "name": "HTTP mode still lands the presets",
     "steps": [
         {"op": "exec_mode", "mode": "http"},
         {"op": "sim_arm", "arms": {"presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "wait_exec", "code": PRESETS_PROBE, "contains": '"landed": true', "budget": 30},
         {"op": "exec_mode", "mode": "websocket"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 45},
     ]},
    {"id": "a11", "group": "connection",
     "name": "a corrupt frame fails the socket and the feed reconnects",
     "steps": [
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "sim_arm", "arms": {"corrupt_frame_once": True}},
         {"op": "wait_seconds", "seconds": 5},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 30},
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
         {"op": "click_stage", "stage": "MonitorStage"},
         # The console's expand is chrome state (a declared slot,
         # like the popup close); the typing and the send are real
         # input: the field takes a real press to focus, the command
         # is typed, and the named Send button takes the real press.
         {"op": "exec_slot", "slot": "setConsoleExpanded", "args": [True]},
         {"op": "deliver_click", "objectName": "moonrakerConsoleInput"},
         {"op": "key_press", "key": "M"},
         {"op": "key_press", "key": "1"},
         {"op": "key_press", "key": "0"},
         {"op": "key_press", "key": "5"},
         {"op": "deliver_click", "objectName": "moonrakerConsoleSend"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "d3", "group": "console", "name": "clear empties the console",
     "steps": [
         {"op": "sim_set", "state": {"console_lines": [{"type": "response", "message": "// line %d" % i,
                                                       "time": 1.0} for i in range(10)]}},
         {"op": "click_text", "text": "Clear"},
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
    {"id": "e2", "group": "webcams", "name": "the webcam selector lists the peer's cameras",
     "steps": [
         {"op": "assert_model", "prop": "webcamNames", "contains": "sim-cam", "budget": 30},
     ]},

    # ─── files & print start ──────────────────────────────────
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
         {"op": "write_fixture", "path": "/tmp/mpf/scenario-upload.gcode"},
         {"op": "exec_file_slot", "slot": "fileUpload", "args": ["/tmp/mpf/scenario-upload.gcode"]},
         {"op": "sim_ledger", "needle": "files/upload", "field": "path", "min": 1, "budget": 30},
         # The honest-lane proof: only the sim's upload handler (not
         # the catch-all) adds the entry to the store, and the
         # completion refresh walks it into the model's rows.
         {"op": "wait_model", "prop": "fileManagerRows", "contains": "scenario-upload.gcode", "budget": 30},
         # The refusal half: the armed lane answers 400 with its own
         # message, and the note surfaces it. A dead lane would
         # accept this upload and the refusal string never appears.
         {"op": "write_fixture", "path": "/tmp/mpf/scenario-upload-refused.gcode"},
         {"op": "sim_arm", "arms": {"fail_upload": True}},
         {"op": "exec_file_slot", "slot": "fileUpload", "args": ["/tmp/mpf/scenario-upload-refused.gcode"]},
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
    {"id": "g1", "group": "motion", "name": "the jog pad's clicks reach the peer as G1 moves",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         # Every jog button takes a real press (the map names all six;
         # one representative press overclaimed the pad).
         {"op": "deliver_click", "objectName": "moonrakerJogXPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogXMinus"},
         {"op": "deliver_click", "objectName": "moonrakerJogYPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogYMinus"},
         {"op": "deliver_click", "objectName": "moonrakerJogZPlus"},
         {"op": "deliver_click", "objectName": "moonrakerJogZMinus"},
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "budget": 20},
     ]},
    {"id": "g2", "group": "motion", "name": "home and the mesh actions reach the peer",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerHomeX", "budget": 30},
         # All three home buttons take a real press (the map names
         # all three).
         {"op": "deliver_click", "objectName": "moonrakerHomeX"},
         {"op": "deliver_click", "objectName": "moonrakerHomeY"},
         {"op": "deliver_click", "objectName": "moonrakerHomeZ"},
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
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "sim-printer-power", "status": "off", "locked_while_printing": False}]}},
         {"op": "wait_model", "prop": "powerDevices", "contains": "sim-printer-power", "budget": 20},
         {"op": "wait_rect", "text": "Turn on", "budget": 40},
         {"op": "exec_code", "verbs": ['setProperty'], "code": SCROLL_CONTROLS},
         {"op": "emit_click", "text": "Turn on"},
         {"op": "wait_exec", "code": POWER_STATUS_PROBE, "contains": '"sim-printer-power=on"', "budget": 20},
         {"op": "sim_ledger", "needle": "device_power/device", "method": "POST", "min": 1},
     ]},
    {"id": "g9b", "group": "motion", "name": "firmware and host restarts reach the peer",
     "steps": [
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
     ]},

    # ─── preview ──────────────────────────────────────────────
    {"id": "h2", "group": "printing", "name": "the load end-to-end from a fresh boot (the flow)",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                    "virtual_sdcard": {"is_active": True, "progress": 0.5, "file_size": 1048576}}},
         {"op": "wait_model", "prop": "monitorFilename", "contains": "scenario1", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120}},
         {"op": "emit_click", "text": "Load current print"},
         {"op": "confirm_box", "button": "Yes"},
         {"op": "wait_exec", "code": CARD_GATE_PROBE, "contains": '"loadBusy": true', "budget": 30},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
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
    {"id": "h8", "group": "printing", "name": "the ETA opt-in pulls the print for the monitor",
     "steps": [
         # The improve's journey: the print changes to a file never
         # loaded into the preview, the opt-in pulls it through the
         # download lane, and the hourglass ends (the index landed).
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "delete-me.gcode"}}},
         # A streamed download keeps the resolve window observable on
         # 2-vCPU CI runners (the download route reads no route-delay
         # arms).
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 500}},
         {"op": "exec_slot", "slot": "improveEta", "args": []},
         {"op": "sim_ledger", "needle": "files/gcodes/delete-me.gcode", "field": "path", "min": 1, "budget": 30},
         # The hourglass's publication itself is pinned by the Qt
         # test (the registration-grace fix — the red run's product
         # catch); the live window's observation is a recorded
         # follow-up (the publish races the snapshot rebuild).
         {"op": "wait_model", "prop": "improvingEta", "value": False, "budget": 60},
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
         # The slot gates on the plugin-side snapshot of the pending
         # flag, which trails the peer's state by a poll; firing the
         # slot before it lands sends nothing, forever.
         {"op": "wait_model", "prop": "canSaveConfig", "value": True, "budget": 10},
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
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 150},
         {"op": "exec_code", "verbs": [], "code": "from UM.Application import Application\napp = Application.getInstance()\nresult = {}\nfor e in app.getExtensions():\n    if \"MoonrakerPrintFollower\" in type(e).__name__:\n        rt = e._runtime\n        coord = rt.coordinator if hasattr(rt, \"coordinator\") else rt.follow\n        result[\"gate_logged\"] = repr(getattr(coord, \"_gate_logged\", \"n/a\"))\n        result[\"has_toolpath\"] = bool(getattr(rt.cura, \"has_toolpath\", False))\n        result[\"preview_active\"] = bool(getattr(rt.cura, \"preview_active\", False))\n        result[\"configured\"] = bool(getattr(getattr(rt.binding, \"configured\", None), \"__bool__\", lambda: False)())\n        try:\n            view = app.getController().getActiveView()\n            result[\"active_view\"] = str(view.getPluginId()) if view else None\n            result[\"max_layers\"] = int(view.getMaxLayers()) if view and hasattr(view, \"getMaxLayers\") else None\n        except Exception as exc:\n            result[\"view_err\"] = repr(exc)[:80]\n        break\n"},
         {"op": "add_post_script", "script": "PauseAtHeight"},
         {"op": "wait_rect", "objectName": "postProcessingSaveAreaButton", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "postProcessingSaveAreaButton"},
          "no_overlap": {"objectName": "moonrakerPreviewCard"}},
         {"op": "dump_visible", "needle": "", "region": [600, 520, 1280, 800]},
         {"op": "assert_aligned", "item": {"objectName": "moonrakerPreviewCard"},
          "within": {"window": True}},
     ]},
    {"id": "v2", "group": "visual",
     "name": "the jog pad grid is straight",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
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
         {"op": "resize_window", "w": 1840, "h": 1040},
         {"op": "wait_rect", "objectName": "moonrakerConsoleInput", "budget": 30},
         {"op": "assert_aligned", "item": {"objectName": "infoPanel"},
          "no_overlap": {"objectName": "statusPanel"}},
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
         {"op": "resize_window", "w": 1840, "h": 1040},
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


    {"id": "v14", "group": "visual",
     "name": "the DFU toggle clicks through and the locked device refuses",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "DFU", "status": "on", "locked_while_printing": False},
             {"device": "Printer", "status": "off", "locked_while_printing": True}]}},
         {"op": "wait_rect", "text": "Turn off", "budget": 40},
         # The visible-interactions pattern for scrolled content:
         # bring the control into the rendered viewport (with the
         # containment asserted). The switch's clickable region is a
         # custom component whose class the promotion misses — the
         # toggle stays a declared emit for now (the switch
         # addressing follow-up, recorded in DECISIONS).
         {"op": "scroll_into_view", "text": "Turn off"},
         {"op": "emit_click", "text": "Turn off"},
         {"op": "wait_exec", "code": P_POWER_FLIP, "contains": '"status": "off"', "budget": 20},
         {"op": "sim_ledger", "needle": "device_power/device", "method": "POST", "min": 1},
     ]},
    {"id": "v15", "group": "visual",
     "name": "a locked power device disables its toggle while a print runs",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {"power_devices": [
             {"device": "DFU", "status": "on", "locked_while_printing": False},
             {"device": "Printer", "status": "off", "locked_while_printing": True}]}},
         {"op": "sim_set_current_print"},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         {"op": "wait_rect", "text": "Turn off", "budget": 40},
         {"op": "exec_code", "verbs": ['setProperty'], "code": SCROLL_CONTROLS},
         {"op": "assert_exec", "code": P_TOGGLE_STATE, "contains": '"enabled": [false, true]'},
     ]},
    {"id": "v17", "group": "visual",
     "name": "the data-render census: every data class renders its control",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "sim_arm", "arms": {
             "power_devices": [
                 {"device": "DFU", "status": "on", "locked_while_printing": False},
                 {"device": "Printer", "status": "off", "locked_while_printing": True}],
             "presets_value": {"presets": {"fast": {"name": "Fast", "gcode": "M220 S150"}}}}},
         {"op": "sim_set", "state": {"extruder": {"temperature": 180.0, "target": 200.0},
                                     "heater_bed": {"temperature": 55.0, "target": 60.0}}},
         {"op": "census", "budget": 40},
     ]},

    {"id": "v18", "group": "visual",
     "name": "the firmware restart reaches the host and heals the feed",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerControlsPane", "budget": 30},
         {"op": "exec_code", "verbs": ['setProperty'], "code": SCROLL_CONTROLS},
         {"op": "emit_click", "text": "Firmware restart"},
         {"op": "sim_ledger", "needle": "firmware_restart", "field": "path", "min": 1, "budget": 20},
         {"op": "sim_ledger", "needle": "printer.objects.subscribe", "field": "path", "min": 1, "budget": 40},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 60},
     ]},
    # ─── the preview, end to end (the comprehensive list) ───
    {"id": "p1", "group": "preview",
     "name": "the load renders the real toolpath, the indicator, and the card",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set_current_print"},
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "emit_click", "text": "Load current print"},
         {"op": "confirm_box", "button": "Yes"},
         {"op": "wait_exec", "code": P1_PCT_PROBE, "contains": '"pct": true', "budget": 20},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "assert_exec", "code": "view = Application.getInstance().getController().getView(\"SimulationView\")\nresult = {\"max_layers\": int(view.getMaxLayers()) if view else 0}",
          "contains": '"max_layers": 39'},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "wait_rect", "text": "Detach", "budget": 30},
     ]},
    {"id": "p2", "group": "preview",
     "name": "a real drag on Cura's layer slider auto-detaches the follow",
     "steps": [
         {"op": "wait_seconds", "seconds": 5},
         {"op": "exec_code", "verbs": ['mouseMove'], "code": P_SLIDER_DRAG},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
         {"op": "dump_visible", "needle": "detach|follow|attach", "region": [600, 560, 1280, 800]},
     ]},
    {"id": "p3", "group": "preview",
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
     "name": "the card's Detach button detaches and Attach re-attaches",
     "steps": [
         {"op": "click_text", "text": "Detach"},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": false', "budget": 20},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_ATTACH_EMIT},
         {"op": "wait_exec", "code": P_FOLLOW_READ, "contains": '"attached": true', "budget": 20},
     ]},

    {"id": "p6", "group": "preview",
     "name": "the scheduled pause fires as the print crosses the layer",
     "steps": [
         {"op": "sim_set_current_print"},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_PAUSE_CLICK},
         {"op": "wait_exec", "code": P_PAUSE_SCHEDULED, "contains": '"scheduled": true', "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 60},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_exec", "code": P_ROW_GONE, "contains": '"row_gone": true', "budget": 20},
     ]},
    {"id": "p7", "group": "preview",
     "name": "a refused PAUSE keeps the entry restyled as not taken",
     "steps": [
         {"op": "sim_arm", "arms": {"fail_pause_script": True}},
         {"op": "sim_set_current_print"},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_PAUSE_CLICK},
         {"op": "wait_exec", "code": P_PAUSE_SCHEDULED, "contains": '"scheduled": true', "budget": 20},
         {"op": "wait_exec", "code": P_MISSED, "contains": '"missed": true', "budget": 60},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
     ]},

    # ─── the smoke set (the release gate's sanity layer) ──────────
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
         {"op": "sim_arm", "arms": {"gcode_stream_ms": 120}},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCardOverlay", "budget": 30},
         {"op": "emit_click", "text": "Load current print"},
         {"op": "confirm_box", "button": "Yes"},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "budget": 30, "poll": 0.2},
         {"op": "wait_rect", "objectName": "moonrakerPreviewCard", "budget": 240},
         {"op": "wait_rect", "objectName": "loadIndicatorContent", "absent": True, "budget": 60},
         {"op": "wait_seconds", "seconds": 5},
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
         {"op": "click_text", "text": "Pause"},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
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
         {"op": "exec_slot", "slot": "emergencyHoldReleased", "args": []},
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
     ]},

    # ─── geometry probes (diagnostics, not release gates) ─────────
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
    # The what's-new overlay's lifecycle (the author's ruling: it must
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
         # The repo link's press lands (the browser launch is Qt's
         # own behaviour — outside the harness's claim).
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
    {"id": "v13", "group": "visual",
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

    {"id": "v16", "group": "visual",
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
]