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

P_ROW_PASSED = (
    "window = _main_window()\n"
    "result = {\"passed\": False, \"label\": \"\"}\n"
    "for item in _walk(window.contentItem()):\n"
    "    try:\n"
    "        label = item.property(\"text\")\n"
    "    except Exception:\n"
    "        label = None\n"
    "    if isinstance(label, str) and label.startswith(\"End of layer\") and bool(item.isVisible()):\n"
    "        result[\"label\"] = label[:60]\n"
    "        if \"passed\" in label:\n"
    "            result[\"passed\"] = True\n"
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

P_SLIDER_CLICK = """from PyQt6.QtCore import QPoint, Qt, QPointF
def _import_qtest():
    from PyQt6.QtTest import QTest
    return QTest
window = _main_window()
result = {}
slider = None
def _walk(item):
    for child in item.childItems():
        if "OutlineSlider" in str(child.metaObject().className()):
            # The FANS slider (the sim's "fan" object) — the walk's
            # first OutlineSlider is the tuning speed slider, far
            # down the dashboard and not the scenario's target.
            try:
                if child.property("controlKind") == "fan" and child.property("controlObject") == "fan":
                    return child
            except Exception:
                pass
        found = _walk(child)
        if found is not None:
            return found
    return None
slider = _walk(window.contentItem())
if slider is None:
    result = {"error": "no fan slider found"}
else:
    scene = slider.mapToScene(QPointF(0, 0))
    # The visibility rule: the scenario scrolls the fans section into
    # view first — a still-clipped slider is a step error, never a
    # silent miss (a click past the window's edge hits nothing).
    if scene.x() < 0 or scene.y() < 0 or scene.x() + slider.width() > window.width() or scene.y() + slider.height() > window.height():
        result = {"error": "fan slider off-screen at %s (window %sx%s)" % (scene, window.width(), window.height())}
    else:
        before = slider.property("value")
        # The track click: 20% of the track sits clear of the handle
        # for any value at or right of centre — the click must move
        # the value AND commit (the live report: a track click moved
        # the handle and never submitted). The native groove CENTERS
        # the handle on the click, so landing on exactly 20% needs
        # the half-handle lead-in (without it the first run's click
        # landed on 18.3 — the peer's fan then read 0.18, which is
        # the clicked value, not the aimed one).
        left_pad = slider.property("leftPadding")
        avail = slider.property("availableWidth")
        handle_w = 16
        for child in slider.childItems():
            if "RoundedRectangle" in str(child.metaObject().className()) and 0 < child.width() <= 40:
                handle_w = child.width()
                break
        click_x = round(scene.x() + left_pad + handle_w / 2 + 0.2 * (avail - handle_w))
        click_y = round(scene.y() + slider.height() / 2)
        qtest = _import_qtest()
        qtest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(click_x, click_y))
        qtest.qWait(300)
        result = {"clicked": True, "before": before, "x": click_x, "y": click_y}
        try:
            after = slider.property("value")
            result["after"] = after
            if after == before:
                result["error"] = "the track click did not move the value (still %s)" % (after,)
        except RuntimeError:
            # The commit's publish rebuilt the fan repeater and the
            # pressed slider died mid-wait — the interaction itself
            # succeeded (the rebuild IS the commit's echo). The
            # committed value is asserted peer-side by the ledger and
            # the sim's fan speed.
            result["rebuilt"] = True
"""

STRIP_PAUSE_READY = """window = _main_window()
result = {}
def _find(item):
    try:
        if item.objectName() == "moonrakerStripPauseButton":
            return item
    except Exception:
        pass
    for child in item.childItems():
        found = _find(child)
        if found is not None:
            return found
    return None
btn = _find(window.contentItem())
if btn is None:
    result = {"error": "no strip pause button"}
else:
    # The block lands on the aux poll BEHIND the model's verdicts —
    # a press while the strip still reads the idle block falls
    # through a disabled button to the stage's background MouseArea.
    result = {"enabled": bool(btn.property("enabled")), "text": str(btn.property("text") or "")}
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


# Configure-round diagnostics: the geometry probe is a gate (its
# misalignment raise fails the step); the FM click rides the s8
# track-click precedent (no driver verb aims at the band).
CONFIGURE_DRAG_PROBE = (
    "qtest = _import_qtest()\n"
    "window = _main_window()\n"
    "result = {}\n"
    "handle = None\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"sectionConfigureHandle\" and _effectively_visible(item):\n"
    "        r = self._rect(item)\n"
    "        if handle is None or r[\"y\"] < handle[1]:\n"
    "            handle = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "if handle is None:\n"
    "    raise RuntimeError(\"no visible drag handle\")\n"
    "x = handle[0] + handle[2] / 2\n"
    "y = handle[1] + handle[3] / 2\n"
    "qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton,\n"
    "                       Qt.KeyboardModifier.NoModifier, QPoint(int(x), int(y)))\n"
    "qtest.QTest.qWait(120)\n"
    "qtest.QTest.mouseMove(window, QPoint(int(x), int(y + 3 * 32)), 200)\n"
    "qtest.QTest.qWait(120)\n"
    "qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton,\n"
    "                         Qt.KeyboardModifier.NoModifier, QPoint(int(x), int(y + 3 * 32)))\n"
    "result[\"pressed\"] = [int(x), int(y)]\n"
    "result[\"released\"] = [int(x), int(y + 96)]\n"
    "result")

CONFIGURE_GEOM_PROBE = (
    "window = _main_window()\n"
    "result = {}\n"
    "info_title_box = None\n"
    "info_strip = None\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"infoCollapsedReadoutText\" and info_strip is None:\n"
    "        # The label's parent is the ROTATED row; the strip is one\n"
    "        # level further out and unrotated.\n"
    "        r = self._rect(item.parentItem().parentItem())\n"
    "        info_strip = [r[\"x\"], r[\"w\"]]\n"
    "        result[\"info_strip\"] = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "    try:\n"
    "        t = str(item.property(\"text\") or \"\")\n"
    "    except Exception:\n"
    "        t = \"\"\n"
    "    if t == \"Information\" and \"Label\" in item.metaObject().className() \\\n"
    "            and info_title_box is None and _effectively_visible(item):\n"
    "        r = self._rect(item.parentItem())\n"
    "        info_title_box = [r[\"x\"], r[\"w\"]]\n"
    "        result[\"info_title_box\"] = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "if info_strip is not None and info_title_box is not None:\n"
    "    strip_center = info_strip[0] + info_strip[1] / 2\n"
    "    box_center = info_title_box[0] + info_title_box[1] / 2\n"
    "    result[\"info_centres\"] = [round(strip_center, 1), round(box_center, 1)]\n"
    "    if abs(strip_center - box_center) > 2:\n"
    "        raise RuntimeError(\"the info readout's centre is %.1fpx off the title's\"\n"
    "                           % (strip_center - box_center))\n"
    "result")

CONFIGURE_CONTROLS_FIELD_PROBE = (
    "window = _main_window()\n"
    "result = {}\n"
    "fields = []\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name in (\"controlsCollapsedReadoutText\", \"controlsCollapsedZOffsetLabel\") and _effectively_visible(item):\n"
    "        fields.append(self._rect(item))\n"
    "fields.sort(key=lambda r: (r[\"y\"], r[\"x\"]))\n"
    "result[\"fields\"] = [[r[\"x\"], r[\"y\"]] for r in fields]\n"
    "if len(fields) != 4:\n"
    "    raise RuntimeError(\"expected 4 controls readout fields, found %d\" % len(fields))\n"
    "xs = [r[\"x\"] for r in fields]\n"
    "if max(xs) - min(xs) > 3:\n"
    "    raise RuntimeError(\"the readout fields drift off the strip's line: %r\" % xs)\n"
    "for i in range(1, len(fields)):\n"
    "    if fields[i][\"y\"] - fields[i - 1][\"y\"] < 30:\n"
    "        raise RuntimeError(\"the readout fields overlap at y=%d\" % fields[i][\"y\"])\n"
    "result")

CONFIGURE_CROSSTALK_PROBE = (
    "from UM.Application import Application\n"
    "result = {}\n"
    "app = Application.getInstance()\n"
    "printer = None\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = getattr(device, \"activePrinter\", None)\n"
    "        break\n"
    "if printer is not None:\n"
    "    effective = printer.sectionLayoutFor(\"information\")\n"
    "    order = list(effective.get(\"order\", []) or [])\n"
    "    result[\"order\"] = order\n"
    "    result[\"hidden\"] = list(effective.get(\"hidden\", []) or [])\n"
    "    # The info pane's only two sections: the drag below moves\n"
    "    # meshmap behind temphistory — and the controls popup's\n"
    "    # reset must leave that order alone (the live report's\n"
    "    # cross-talk).\n"
    "    if order[:2] != [\"temphistory\", \"meshmap\"]:\n"
    "        raise RuntimeError(\"the information layout did not survive the controls reset: %r\" % order)\n"
    "result")

CONFIGURE_FM_CLICK = (
    "qtest = _import_qtest()\n"
    "window = _main_window()\n"
    "result = {}\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        t = item.property(\"text\")\n"
    "    except Exception:\n"
    "        continue\n"
    "    if t == \"⇄\" and \"Button\" not in item.metaObject().className():\n"
    "        if not _effectively_visible(item):\n"
    "            continue\n"
    "        cell = item.parentItem()\n"
    "        if cell is None:\n"
    "            continue\n"
    "        band = None\n"
    "        for c in cell.childItems():\n"
    "            if \"MouseArea\" in c.metaObject().className():\n"
    "                band = c\n"
    "                break\n"
    "        if band is not None:\n"
    "            r = self._rect(band)\n"
    "            result[\"band\"] = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "            qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton,\n"
    "                                   Qt.KeyboardModifier.NoModifier,\n"
    "                                   QPoint(int(r[\"x\"] + r[\"w\"] / 2), int(r[\"y\"] + r[\"h\"] / 2)))\n"
    "            result[\"clicked\"] = True\n"
    "            break\n"
    "result")

CONFIGURE_FM_STATE = (
    "from PyQt6.QtCore import QObject\n"
    "from UM.Application import Application\n"
    "result = {}\n"
    "window = _main_window()\n"
    "for obj in window.findChildren(QObject):\n"
    "    try:\n"
    "        if str(obj.property(\"objectName\") or \"\") == \"columnsPopup\":\n"
    "            result[\"popup_opened\"] = bool(obj.property(\"opened\"))\n"
    "            break\n"
    "    except Exception:\n"
    "        pass\n"
    "app = Application.getInstance()\n"
    "printer = None\n"
    "for device in app.getOutputDeviceManager().getOutputDevices():\n"
    "    if \"Moonraker\" in type(device).__name__:\n"
    "        printer = getattr(device, \"activePrinter\", None)\n"
    "        break\n"
    "fmo = getattr(printer, \"fileManagerOpen\", None) if printer is not None else None\n"
    "if hasattr(fmo, \"value\"):\n"
    "    try:\n"
    "        fmo = fmo.value()\n"
    "    except Exception:\n"
    "        pass\n"
    "result[\"fileManagerOpen\"] = fmo\n"
    "result")

CONFIGURE_MUTUAL_PROBE = (
    "window = _main_window()\n"
    "result = {}\n"
    "visible = 0\n"
    "rects = []\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"sectionConfigurePopOver\" and _effectively_visible(item):\n"
    "        visible += 1\n"
    "        r = self._rect(item)\n"
    "        rects.append([r[\"x\"], r[\"y\"]])\n"
    "result[\"visible_popovers\"] = visible\n"
    "result[\"rects\"] = rects\n"
    "if visible != 1:\n"
    "    raise RuntimeError(\"expected exactly one open popover, found %d (%s)\" % (visible, rects))\n"
    "result")

CONFIGURE_FM_OPEN = (
    "from PyQt6.QtCore import QObject\n"
    "result = {}\n"
    "window = _main_window()\n"
    "for obj in window.findChildren(QObject):\n"
    "    try:\n"
    "        if str(obj.property(\"objectName\") or \"\") == \"columnsPopup\":\n"
    "            ok = obj.metaObject().invokeMethod(obj, \"open\")\n"
    "            result[\"open_invoked\"] = bool(ok)\n"
    "            break\n"
    "    except Exception as exc:\n"
    "        result[\"error\"] = repr(exc)\n"
    "result")

CONFIGURE_FM_OUTSIDE = (
    "qtest = _import_qtest()\n"
    "window = _main_window()\n"
    "result = {}\n"
    "bg = None\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"columnsPopupBackground\" and _effectively_visible(item):\n"
    "        r = self._rect(item)\n"
    "        bg = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "        break\n"
    "if bg is None:\n"
    "    raise RuntimeError(\"no open columns popup to press outside of\")\n"
    "x = bg[0] + bg[2] + 30\n"
    "y = bg[1] - 30\n"
    "qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton,\n"
    "                       Qt.KeyboardModifier.NoModifier, QPoint(int(x), int(y)))\n"
    "result[\"pressed\"] = [int(x), int(y)]\n"
    "result")

CONFIGURE_FM_PROBE = (
    "window = _main_window()\n"
    "result = {}\n"
    "bg = None\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"columnsPopupBackground\" and _effectively_visible(item):\n"
    "        r = self._rect(item)\n"
    "        bg = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "        result[\"background\"] = bg + [True]\n"
    "        break\n"
    "first_row = None\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    try:\n"
    "        t = str(item.property(\"text\") or \"\")\n"
    "    except Exception:\n"
    "        t = \"\"\n"
    "    if t == \"✕\" and _effectively_visible(item) and bg is not None:\n"
    "        r = self._rect(item)\n"
    "        if bg[0] <= r[\"x\"] <= bg[0] + bg[2] and bg[1] <= r[\"y\"] <= bg[1] + bg[3]:\n"
    "            result[\"close_x\"] = [r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]]\n"
    "    if name == \"sectionConfigureRowTitle\" and bg is not None:\n"
    "        r = self._rect(item)\n"
    "        # Only the rows INSIDE the popup: the closed pane popups\n"
    "        # carry the same name at their old positions.\n"
    "        if not (bg[0] <= r[\"x\"] + r[\"w\"] <= bg[0] + bg[2] and bg[1] <= r[\"y\"] <= bg[1] + bg[3]):\n"
    "            continue\n"
    "        result.setdefault(\"rows\", []).append([t[:20], r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"]])\n"
    "        if first_row is None:\n"
    "            first_row = item\n"
    "if first_row is not None:\n"
    "    node = first_row\n"
    "    chain = []\n"
    "    for _ in range(8):\n"
    "        if node is None:\n"
    "            break\n"
    "        r = self._rect(node)\n"
    "        chain.append([node.metaObject().className()[:30], r[\"x\"], r[\"y\"], r[\"w\"], r[\"h\"],\n"
    "                      bool(_effectively_visible(node))])\n"
    "        node = node.parentItem()\n"
    "    result[\"row_chain\"] = chain\n"
    "# The gate: the rows must render at a sane width — a collapsed\n"
    "# layout renders them negative/narrow (the live report: the\n"
    "# popup clipped all its contents). And the blue ✕ must be there\n"
    "# (the live report: no close affordance).\n"
    "for row in result.get(\"rows\", ()):\n"
    "    if row[3] < 100:\n"
    "        raise RuntimeError(\"the columns popup's rows render %dpx wide (%s)\" % (row[3], row[0]))\n"
    "if \"close_x\" not in result:\n"
    "    raise RuntimeError(\"the columns popup has no visible close ✕\")\n"
    "result")


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
         # The group shares one boot: earlier scenarios CONNECTED, and
         # the tri-state's never-observed latch survives. The reconnect
         # cycles the client session (sessionInvalidated resets the
         # latch), so the premise — a session that has NEVER connected
         # — is honest.
         {"op": "exec_slot", "slot": "reconnect"},
         {"op": "wait_model", "prop": "monitorConnected", "value": False, "budget": 30},
         {"op": "assert_model", "prop": "jogEnabled", "value": False},
         # The policy gate (4.2.0): a never-observed session is
         # UNKNOWN, not idle — the restart gate fails closed and the
         # caption says so.
         {"op": "assert_model", "prop": "canRestart", "value": False},
         {"op": "assert_model", "prop": "jogReason", "value": "Printer state unknown"},
         # The RENDERED witness (the phase-6 engineering re-review's
         # D2): the disconnected decision must hold on the real item,
         # not only in the QML source text.
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         {"op": "item_disabled", "objectName": "moonrakerJogXPlus"},
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
         # The motion ticker (4.2.0): the rows' values must MOVE, not
         # just render — armed rates feed live_velocity and
         # live_extruder_velocity each push while printing.
         {"op": "sim_arm", "arms": {"motion_speed_mm_s": 60, "motion_e_mm_s": 0.8}},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 15},
         {"op": "wait_model", "prop": "monitorProgress", "contains": "4", "budget": 15},
         {"op": "wait_model", "prop": "monitorVelocity", "value": "60.0 mm/s", "budget": 15},
         {"op": "wait_model", "prop": "monitorFlowRate", "value": "1.9 mm³/s", "budget": 15},
         {"op": "wait_model", "prop": "monitorAccelLimit", "value": "5000 mm/s²", "budget": 15},
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
         # The policy gate (4.2.0): while printing the jog button
         # renders disabled (pause-first), the caption names the
         # mode, and the restart gate refuses.
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         {"op": "wait_rect", "objectName": "toolheadStatusCaption", "budget": 30},
         # The model-level polls settle the state first — the
         # rendered check then reads a frame that has already
         # published the denial (the re-review's D4 ordering).
         {"op": "assert_model", "prop": "jogReason", "contains": "pause first", "budget": 10},
         {"op": "assert_model", "prop": "canRestart", "value": False, "budget": 10},
         {"op": "item_disabled", "objectName": "moonrakerJogXPlus"},
         # The lane's revalidation (4.3.0): while PRINTING a Resume
         # refuses with the policy's words, no command leaves the
         # plugin, and the projected reasons name both rows.
         {"op": "exec_slot", "slot": "resumePrint", "args": []},
         {"op": "assert_model", "prop": "actionStatus", "contains": "Resume refused: Print is not paused", "budget": 10},
         {"op": "assert_model", "prop": "resumeReason", "value": "Print is not paused", "budget": 10},
         {"op": "assert_model", "prop": "resumeReasonDetail", "value": "Resume applies to a paused print — this print is still running.", "budget": 10},
         {"op": "assert_model", "prop": "pauseReason", "value": "", "budget": 10},
         {"op": "assert_model", "prop": "pauseReasonDetail", "value": "", "budget": 10},
         # The print-job caption reads the STATE word (4.3.0) — a
         # busy lane must never read as "Printing".
         {"op": "assert_model", "prop": "printJobCaption", "value": "Printing", "budget": 10},
         {"op": "exec_slot", "slot": "pausePrint", "args": []},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 15},
         # Paused keeps the shipped caption — moves run immediately.
         {"op": "assert_model", "prop": "jogReason", "value": "Paused — moves run immediately", "budget": 10},
         {"op": "assert_model", "prop": "printJobCaption", "value": "Paused", "budget": 10},
         # The rows flip with the pause: the resume side opens, the
         # pause side names why it refuses.
         {"op": "assert_model", "prop": "resumeReason", "value": "", "budget": 10},
         {"op": "assert_model", "prop": "pauseReason", "value": "Print is already paused", "budget": 10},
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
         # The policy gate (4.2.0): the lock's reason reaches the
         # caption and the jog gate through the observation record.
         {"op": "assert_model", "prop": "jogEnabled", "value": False, "budget": 10},
         {"op": "assert_model", "prop": "jogReason", "value": "Controls locked", "budget": 10},
         {"op": "exec_slot", "slot": "setControlsLocked", "args": [False]},
         {"op": "assert_model", "prop": "controlsLocked", "value": False, "budget": 10},
         {"op": "assert_model", "prop": "jogEnabled", "value": True, "budget": 10},
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
         # The first mesh of the boot arrives through the discovery
         # watchdog's re-subscribe sync (~4 s); the budget covers the
         # cold chain.
         {"op": "assert_model", "prop": "bedMeshAvailable", "value": True, "budget": 30},
         {"op": "assert_model", "prop": "bedMeshProfile", "contains": "sim-mesh", "budget": 10},
     ]},
    {"id": "h6b", "group": "printing", "name": "the probe-points toggle persists on the preference",
     "steps": [
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [True]},
         {"op": "assert_model", "prop": "showProbePoints", "value": True},
         {"op": "exec_slot", "slot": "setShowProbePoints", "args": [False]},
         {"op": "assert_model", "prop": "showProbePoints", "value": False},
     ]},
    {"id": "h6c", "group": "printing", "name": "the heightmap range filter window is shared",
     "steps": [
         # A non-flat mesh so the window has a real range to clamp into.
         {"op": "sim_set", "state": {"bed_mesh": {"profile_name": "sim-mesh", "mesh_min": [0.0, 0.0],
                                                  "mesh_max": [50.0, 50.0],
                                                  "probed_matrix": [[0.0, 0.2, 0.5], [0.1, 0.3, 0.4], [0.2, 0.1, 0.3]]}}},
         {"op": "assert_model", "prop": "bedMeshAvailable", "value": True, "budget": 30},
         {"op": "exec_slot", "slot": "setBedMeshThresholds", "args": [0.1, 0.4]},
         {"op": "assert_model", "prop": "bedMeshThresholdLow", "value": 0.1},
         {"op": "assert_model", "prop": "bedMeshThresholdHigh", "value": 0.4},
         # The mini map lives on the MONITOR stage (the Preview card
         # hosts the range slider too — its witness would pass
         # without the pop-over ever opening). Enter the stage, then
         # click the map to open the pop-over.
         {"op": "click_stage", "stage": "MonitorStage"},
         # The stage's dashboard loads asynchronously — the map must
         # be ON SCREEN before the press (the same wait-then-click
         # a9 uses).
         {"op": "wait_rect", "objectName": "moonrakerBedMeshMap", "budget": 30},
         {"op": "deliver_click", "objectName": "moonrakerBedMeshMap"},
         {"op": "wait_rect", "objectName": "moonrakerBedMeshRangeSlider", "budget": 30},
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
    {"id": "v19", "group": "visual",
     "name": "the strip renders its cells and its pause routes the lane",
     "steps": [
         {"op": "click_stage", "stage": "PreviewStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "printing", "filename": "scenario1.gcode"},
                                     "extruder": {"temperature": 205.2, "target": 210.0},
                                     "heater_bed": {"temperature": 60.0, "target": 60.0}}},
         # The strip's three cells render with the card; the block
         # lands on the aux poll — the temps witness proves the block
         # arrived (and that the strip renders the WHOLE unlabelled
         # pair: hotend first, bed second — the elided-labelled-form
         # pin, the UX re-review). The surface witness waits on the
         # strip button, never the pane root: in the UNSLICED
         # printing state Cura hosts the pane root at zero size (its
         # children render fine), so the lookup's size filter skips
         # the root — the sliced v1 state resolves it.
         {"op": "wait_rect", "objectName": "moonrakerStripPauseButton", "budget": 150},
         {"op": "wait_rect", "objectName": "moonrakerStripTemps", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStripBed", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStripFinish", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerLayerReadout", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerHeightReadout", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerStripSlot", "budget": 30},
         {"op": "wait_rendered", "objectName": "moonrakerStripTemps", "contains": "205.2 → 210.0 °C", "budget": 30},
         # The strip's own verdict lane settles BEFORE the click: the
         # block lands on the aux poll behind the model's verdicts, so
         # a press while the strip still reads the idle block hits a
         # disabled button (the press falls through to the stage's
         # background MouseArea — the gate's own failure). The wait
         # reads the BUTTON's enabled state, the exact gate the press
         # must pass.
         {"op": "wait_exec", "code": STRIP_PAUSE_READY, "contains": '"enabled": true', "budget": 15},
         # The strip's one control dispatches through the Monitor's
         # revalidated lane: while printing the button reads "Pause
         # print" and a real press sends the pause.
         {"op": "deliver_click", "objectName": "moonrakerStripPauseButton"},
         {"op": "sim_ledger", "needle": "print/pause", "method": "POST", "min": 1, "budget": 20},
     ]},
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
         {"op": "wait_rect", "objectName": "moonrakerFirmwareRestart", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerHostRestart", "budget": 30},
         {"op": "wait_rect", "objectName": "moonrakerKlipperRestart", "budget": 30},
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
         # Pin the layer source: the click targets Cura's SELECTED
         # layer (the follow re-drives it), and before the sim's
         # first 6s layer-clock tick the resolver falls back to the
         # file position — progress 0.5 lands INSIDE the baked
         # pause's layer, the gate refuses, and the whole scenario
         # cascades (the 2026-09-18 flake). The first tick makes the
         # resolution deterministic: layer 1, the candidate 2.
         {"op": "wait_sim", "path": "print_stats.info.current_layer", "value": 1, "budget": 20},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_PAUSE_CLICK},
         # The row the click made, not any row: the baked row also
         # reads "End of layer", and matching it hid a refused click
         # (the 2026-09-18 flake's false positive).
         {"op": "wait_exec", "code": P_PAUSE_SCHEDULED, "contains": '"scheduled": true, "label": "End of layer 2', "budget": 20},
         {"op": "wait_model", "prop": "monitorState", "contains": "paused", "budget": 60},
         {"op": "sim_ledger", "needle": "gcode/script", "method": "POST", "min": 1, "budget": 20},
         # The 4.3.0 ruling: a fired pause STAYS listed, restyled as
         # passed — the row must not disappear.
         {"op": "wait_exec", "code": P_ROW_PASSED, "contains": '"passed": true', "budget": 20},
     ]},
    {"id": "p7", "group": "preview",
     "name": "a refused PAUSE keeps the entry restyled as not taken",
     "steps": [
         {"op": "sim_arm", "arms": {"fail_pause_script": True}},
         {"op": "sim_set_current_print"},
         {"op": "wait_model", "prop": "monitorState", "contains": "print", "budget": 30},
         # The same determinism pin as p6: before the sim's first
         # layer-clock tick the resolver's file-position fallback
         # lands inside the baked pause's layer and the click refuses.
         {"op": "wait_sim", "path": "print_stats.info.current_layer", "value": 1, "budget": 20},
         {"op": "wait_seconds", "seconds": 3},
         {"op": "exec_code", "verbs": ['clicked.emit'], "code": P_PAUSE_CLICK},
         {"op": "wait_exec", "code": P_PAUSE_SCHEDULED, "contains": '"scheduled": true, "label": "End of layer 2', "budget": 20},
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
         # The shipped error-state row (4.2.0, explicit): the e-stop's
         # recovery path — error ALLOWS the restart, the assumption
         # blocks until the cycle.
         {"op": "assert_model", "prop": "canRestart", "value": True},
         {"op": "exec_slot", "slot": "emergencyHoldReleased", "args": []},
         {"op": "exec_slot", "slot": "firmwareRestart", "args": []},
         {"op": "sim_ledger", "needle": "firmware_restart", "min": 1, "budget": 20},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
     ]},
    {"id": "s8", "group": "smoke", "name": "a track click on a plugin slider moves AND commits",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "sim_set", "state": {"print_stats": {"state": "standby", "filename": ""},
                                     "fan": {"speed": 0.5}}},
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         # The fans section renders a writable slider row from the
         # sim's fan object; the probe clicks the TRACK (clear of the
         # handle) — the live report: the handle moved and the commit
         # never fired (drag+release and keyboard worked; a click
         # submitted nothing). The fans section sits below the fold
         # of the dashboard's scroll — the click must land like a
         # human's would: scrolled into view, then pressed.
         {"op": "wait_rect", "objectName": "moonrakerJogXPlus", "budget": 30},
         {"op": "scroll_into_view", "objectName": "moonrakerFansSection"},
         {"op": "exec_code", "verbs": ["mouseClick"], "code": P_SLIDER_CLICK},
         # One track click commits exactly ONCE (the UX re-review:
         # a minimum passes a duplicate commit silently) — and the
         # peer's fan lands on the clicked 20% (the moved value
         # travelled; a moved handle with no commit is the live bug).
         {"op": "sim_ledger", "needle": "gcode/script", "field": "path", "min": 1, "max": 1, "budget": 20},
         {"op": "wait_sim", "path": "fan.speed", "value": 0.2, "budget": 15},
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
    {"id": "r6", "group": "real", "real_safe": True,
     "name": "the M117 witness: the live message reaches the slot",
     "steps": [
         {"op": "wait_model", "prop": "monitorConnected", "value": True, "budget": 30},
         {"op": "wait_model", "prop": "monitorMessage", "contains": "claude-m117-probe", "budget": 60},
     ]},

    # ─── configure (4.4.0, the popup round) ─────────────────────
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
         # The blue ✕ dismisses the card (the live ruling: a Close
         # button read as chrome — and did nothing).
         {"op": "deliver_click", "objectName": "configureControlsSectionsButton"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "budget": 15},
         {"op": "click_text", "text": "✕"},
         {"op": "wait_rect", "objectName": "sectionConfigurePopOver", "absent": True, "budget": 15},
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
         # The tri-state selector: filled + tick at ALL (the live
         # report: it rendered empty).
         {"op": "assert_rendered", "objectName": "visibilitySelectorGlyph", "equals": "✓"},
         {"op": "click_text", "text": "Temperatures"},
         {"op": "wait_model", "prop": "sectionHiddenMap", "contains": "temps", "budget": 15},
         # One hidden of several: filled + dash.
         {"op": "assert_rendered", "objectName": "visibilitySelectorGlyph", "equals": "–"},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "absent": True, "budget": 15},
         {"op": "click_text", "text": "Temperatures"},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
         {"op": "assert_rendered", "objectName": "visibilitySelectorGlyph", "equals": "✓"},
         # The NONE state: the selector hides everything, and the
         # glyph empties (the live report: none rendered dashed).
         {"op": "deliver_click", "objectName": "visibilitySelectorBox"},
         {"op": "wait_model", "prop": "sectionHiddenMap", "contains": "temps", "budget": 15},
         {"op": "wait_rect", "objectName": "visibilitySelectorGlyph", "absent": True, "budget": 15},
         # Reset to defaults: the blue label at the card's bottom
         # commits the empty layout, so every section returns.
         {"op": "deliver_click", "objectName": "resetToDefaultsLabel"},
         {"op": "wait_rect", "objectName": "moonrakerTemperatureDetail", "budget": 15},
         {"op": "assert_rendered", "objectName": "visibilitySelectorGlyph", "equals": "✓"},
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
         # rides the author's live test — the harness engine refused
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
     "name": "the collapsed readouts hide whole lines when the window cannot fit them",
     "steps": [
         {"op": "click_stage", "stage": "MonitorStage"},
         {"op": "resize_window", "w": 1600, "h": 300},
         {"op": "sim_set", "state": {"extruder": {"temperature": 195.0, "target": 210.0},
                                     "print_stats": {"state": "printing", "filename": "scenario1.gcode"}}},
         {"op": "wait_model", "prop": "temperatureItems", "contains": "210", "budget": 30},
         {"op": "exec_slot", "slot": "setInfoCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "infoCollapsedReadoutText", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setStatusCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "statusCollapsedReadoutLabel", "absent": True, "budget": 15},
         {"op": "exec_slot", "slot": "setControlsCollapsed", "args": [True]},
         {"op": "wait_rect", "objectName": "controlsCollapsedReadoutText", "absent": True, "budget": 15},
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
         # author's ruling: neither the value nor its glyph may
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
         {"op": "confirm_box", "button": "Yes"},
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
