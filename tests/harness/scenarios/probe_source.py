"""The probe bodies and window floors the scenario groups share.

The driver executes these strings, so they are code the groups name
rather than data: test_harness_specs compiles every ALL-CAPS str the
package exports. The package's __init__ re-exports each one, and a
pin there holds the two sets equal.
"""
from __future__ import annotations

import json
import os

# The scratch the driver-side probes write into, shared with the runner
# (runner.SCRATCH_DIR): the container's work dir IS /tmp/mpf, and on the
# natives that path exists on neither side. Lowercase, unlike this
# module's probe constants: test_harness_specs compiles every ALL-CAPS
# str attribute as probe CODE, and a path is not code.
_scratch_dir = os.environ.get("HARNESS_SCRATCH_DIR", "/tmp/mpf")


def _scratch(name: str) -> str:
    """A path in the shared scratch, as a literal for the driver's code.

    json-encoded because a Windows work dir is all backslash escapes.
    """
    return json.dumps(os.path.join(_scratch_dir, name))


# The application's own window floor: the em-scaled minimum the window
# reports, read off the live window by the driver (880x528 under Xvfb,
# 1040x624 on macOS and Windows). A step that wants the smallest size a
# user can reach asks for it with the "min" axis and takes each
# platform's own value. A LITERAL has to clear the largest of the floors
# because one spec runs on every platform, and it has to clear the floor
# at all because a size the application refuses is a size no user can
# drag to — a step resting on one measures a geometry nobody can reach
# (the 1000-wide requests were clamped to 1040 on Windows and measured
# what the spec never asked for). Nothing forces past the floor;
# test_harness_specs pins the rule.
WINDOW_FLOOR_W = 1040
WINDOW_FLOOR_H = 624

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
    "        if (any(tag in key for tag in (\"ActionPanel\", \"SimulationView\", \"LayerSlider\","
    " \"Slice\", \"OutputProcess\", \"PostProcessing\", \"SaveFile\"))"
    " or name in (\"moonrakerPreviewCard\", \"moonrakerPreviewCardOverlay\","
    " \"moonrakerPreviewCardPanel\", \"moonrakerPreviewObjectTags\")):\n"
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
    "            if item.property(\"objectName\") == \"moonrakerControlsContent\" and abs(c.x() - f.x()) < 2 and abs(item.width() - target) < 24:\n"
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
    "with open(" + _scratch("fm_popup_probe.json") + ", 'w') as _f:\n"
    "    _json.dump(result, _f)\n"
    "# The full census (with coordinates) rides the file; the RETURNED\n"
    "# summary stays well under the driver's 4000-char transport cap\n"
    "# (the reworked popup's named content outgrew it — the 2026-09-18\n"
    "# gate probe's truncation). The ASSIGNMENT matters: the exec\n"
    "# channel reads the `result` variable, and a bare expression's\n"
    "# value is discarded.\n"
    "result = {\"matches\": [r[:2] for r in result[\"matches\"][:5]],\n"
    "          \"fields\": [r[:2] for r in result[\"fields\"][:5]],\n"
    "          \"names\": [r[:2] for r in result[\"names\"][:8]],\n"
    "          \"counts\": {\"matches\": len(result[\"matches\"]),\n"
    "                      \"fields\": len(result[\"fields\"]),\n"
    "                      \"names\": len(result[\"names\"])}}")

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
    "with open(" + _scratch("fm_button_probe.json") + ", 'w') as f:\n"
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
    "with open(" + _scratch("settings_probe.json") + ", 'w') as f:\n"
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
    "    if \"MoonrakerMJPGImage\" in item.metaObject().className():\n"
    "        result[\"started\"] = bool(getattr(item, \"_started\", False))\n"
    "        result[\"width\"] = int(item.property(\"imageWidth\"))\n"
    "        result[\"height\"] = int(item.property(\"imageHeight\"))\n"
    "        break\n"
    "result")

CAM_STARTED = (
    "window = _main_window()\n"
    "result = {}\n"
    "for item in _walk(window.contentItem()):\n"
    "    if \"MoonrakerMJPGImage\" in item.metaObject().className():\n"
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
    # The bounds are the theme's, not a literal: LayerSlider.qml sizes
    # the handles from UM.Theme.getSize("slider_handle"), and that is
    # 16 px only at a screen scale of 1 — the Windows and macOS runners
    # scale it up (22 px there), where a hard-coded 16 matched nothing.
    try:
        from UM.Qt.Bindings.Theme import Theme
        handle_size = float(Theme.getInstance().getSize("slider_handle").width())
    except Exception:
        handle_size = 16.0
    if handle_size <= 0:
        handle_size = 16.0
    handles = [child for child in target.childItems()
               if abs(child.width() - handle_size) < 2 and abs(child.height() - handle_size) < 2 and bool(child.isVisible())]
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
        # The attach drops ride the toolpath: a stage switch restores
        # the follow only while Cura still HAS a toolpath to drive
        # (the ruling), so a drop reads as either "the toolpath left
        # and stayed away" or "the restore missed it" — the two need
        # opposite fixes and the states tell them apart.
        result["toolpath"] = bool(getattr(rt.cura, "has_toolpath", False))
        result["user_detached"] = bool(getattr(rt.coordinator, "_user_detached", False))
        break
"""

P_PAUSE_BLOCK_READ = """window = _main_window()
result = {"pauseAtLayerRead": False}
model = None
for item in _walk(window.contentItem(), depth=64):
    try:
        candidate = item.property("printer")
    except Exception:
        candidate = None
    if candidate is not None and hasattr(candidate, "pauseAtLayerCandidate"):
        model = candidate
        break
if model is not None:
    result["pauseAtLayerRead"] = True
    result["pauseAtLayerActive"] = bool(model.pauseAtLayerActive)
    result["pauseAtLayerCandidate"] = int(model.pauseAtLayerCandidate)
    result["pauseAtLayerCanToggle"] = bool(model.pauseAtLayerCanToggle)
    result["pauseAtLayerScheduled"] = bool(model.pauseAtLayerScheduled)
    result["pauseAtLayerSummary"] = str(model.pauseAtLayerSummary)
    # The model publishes this one wrapped in a QVariant. QML unwraps
    # that transparently; Python cannot, so len() raised TypeError and
    # took the WHOLE probe down with it -- the step then read an error
    # reply on every poll and failed forever, on all three platforms.
    try:
        result["pauseAtLayerItems"] = len(model.pauseAtLayerItems or [])
    except TypeError:
        result["pauseAtLayerItems"] = None
    result["pauseAtLayerUnavailableText"] = str(model.pauseAtLayerUnavailableText)
    result["pauseAtLayerHasBaked"] = bool(model.pauseAtLayerHasBaked)
    result["pauseAtLayerHasClearable"] = bool(model.pauseAtLayerHasClearable)
result"""

P_ANCHOR_ETA_READ = """window = _main_window()
result = {"anchorEtaRead": False}
model = None
for item in _walk(window.contentItem(), depth=64):
    try:
        candidate = item.property("printer")
    except Exception:
        candidate = None
    if candidate is not None and hasattr(candidate, "plateAnchorEta"):
        model = candidate
        break
if model is not None:
    result["anchorEtaRead"] = True
    # A string always (never None): the publish carries the key, and
    # "" is the no-estimate answer the row renders as an em dash.
    result["plateAnchorEta"] = str(model.plateAnchorEta)
    result["followerAttached"] = bool(model.followerAttached)
result"""

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
SCENE_RECT = """def _input_rect(item):
    # Driver evidence rectangles are desktop-relative. QTest takes window
    # coordinates; fitting beneath the macOS menu bar exposes this offset.
    rectangle = self._rect(item)
    origin = item.window().position()
    return dict(rectangle, x=rectangle["x"] - origin.x(),
                y=rectangle["y"] - origin.y())
"""

CONFIGURE_DRAG_PROBE = SCENE_RECT + (
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
    "        r = _input_rect(item)\n"
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
    "    # The information pane's four sections, in the table order the\n"
    "    # pop-over lists: the drag above takes the pane's FIRST row\n"
    "    # (plateprogress) past the other three, so the layout the\n"
    "    # controls popup's reset must leave alone ends plate-last.\n"
    "    if order != [\"plate\", \"meshmap\", \"temphistory\", \"plateprogress\"]:\n"
    "        raise RuntimeError(\"the information layout did not survive the controls reset: %r\" % order)\n"
    "result")

CONFIGURE_DISMISS_PROBE = SCENE_RECT + (
    "qtest = _import_qtest()\n"
    "window = _main_window()\n"
    "result = {}\n"
    "card = None\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"sectionConfigurePopOver\" and _effectively_visible(item):\n"
    "        card = item\n"
    "        break\n"
    "if card is None:\n"
    "    raise RuntimeError(\"no visible configure pop-over\")\n"
    "r = _input_rect(card)\n"
    "# A press on the card's own surface (the title band owns no\n"
    "# control) must not dismiss the card.\n"
    "qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton,\n"
    "                       Qt.KeyboardModifier.NoModifier,\n"
    "                       QPoint(int(r[\"x\"] + r[\"w\"] / 2), int(r[\"y\"] + 12)))\n"
    "qtest.QTest.qWait(120)\n"
    "if not _effectively_visible(card):\n"
    "    raise RuntimeError(\"an in-bounds click dismissed the pop-over\")\n"
    "# A press above the card's top edge (the header band) dismisses\n"
    "# it through the outside-click layer.\n"
    "qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton,\n"
    "                       Qt.KeyboardModifier.NoModifier,\n"
    "                       QPoint(int(r[\"x\"] + 30), int(r[\"y\"] - 10)))\n"
    "qtest.QTest.qWait(120)\n"
    "if _effectively_visible(card):\n"
    "    raise RuntimeError(\"an outside click failed to dismiss the pop-over\")\n"
    "result")

CONFIGURE_TRI_PROBE = (
    "window = _main_window()\n"
    "expected = EXPECT_STATE\n"
    "found = None\n"
    "read_error = \"\"\n"
    "for item in _walk(window.contentItem(), depth=96):\n"
    "    try:\n"
    "        name = str(item.property(\"objectName\") or \"\")\n"
    "    except Exception:\n"
    "        name = \"\"\n"
    "    if name == \"visibilitySelectorBox\" and _effectively_visible(item):\n"
    "        try:\n"
    "            # QML enum properties arrive as enum objects, not ints:\n"
    "            # the value rides .value (the 5.13 leg's TypeError).\n"
    "            state = item.property(\"checkState\")\n"
    "            found = int(getattr(state, \"value\", state))\n"
    "        except Exception as exc:\n"
    "            read_error = repr(exc)\n"
    "        break\n"
    "if found is None:\n"
    "    raise RuntimeError(\"no visible selector checkbox\" + (\" (checkState read failed: \" + read_error + \")\" if read_error else \"\"))\n"
    "if found != expected:\n"
    "    raise RuntimeError(\"the selector state is \" + str(found) + \", expected \" + str(expected))\n"
    "result")

CONFIGURE_FM_CLICK = SCENE_RECT + (
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
    "            r = _input_rect(band)\n"
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

CONFIGURE_FM_OUTSIDE = SCENE_RECT + (
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
    "        r = _input_rect(item)\n"
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
    "# popup clipped all its contents).\n"
    "for row in result.get(\"rows\", ()):\n"
    "    if row[3] < 100:\n"
    "        raise RuntimeError(\"the columns popup's rows render %dpx wide (%s)\" % (row[3], row[0]))\n"
    "result")


P1_RENDER_PROBE = """from UM.Application import Application
app = Application.getInstance()
view = app.getController().getActiveView()
layers = int(view.getMaxLayers()) if view is not None and hasattr(view, "getMaxLayers") else -1
# 5.12+: the load's gcode (40 layers) renders as 39 zero-based — the
# "real toolpath" proof. 5.11's SimulationView neither resolves by
# name nor ingests a gcode load (the walk-dump: the view reads "slice
# first" after the plugin's load replaces the scene), so the render
# premise is a recorded 5.11 accept; the observed layers ride the
# evidence.
result = {"max_layers": layers,
          "render_ok": bool(str(app.getVersion()).startswith("5.11") or layers >= 39)}
"""
P1_FOLLOW_BUTTON = """from UM.Application import Application
app = Application.getInstance()
window = _main_window()
result = {"button": False, "label": None, "version": str(app.getVersion())}
# The follow control renders: 5.12+ reads Detach right after the load;
# 5.11's view platform flaps the toolpath (the version-skipped p3/p5
# premises), so EITHER follow state counts there — the card's control
# rendered is the premise.
wanted = ("Attach", "Detach") if result["version"].startswith("5.11") else ("Detach",)
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if isinstance(label, str) and label in wanted and bool(item.isVisible()):
        result["button"] = True
        result["label"] = label
        break
"""

PENGUIN_MONITOR_READ = """window = _main_window()
result = {"smooth_gpu": False, "moving": False, "finished": False}
for item in _walk(window.contentItem(), depth=96):
    if item.objectName() == "moonrakerPlateProgressFace" and _effectively_visible(item) and not item.property("compact"):
        result["smooth_gpu"] = bool(item.property("gpuRendering") and item.property("motionSmoothing") and item.property("attached"))
        result["motion"] = float(item.property("displayedMotion") or 0)
        canvas = next((child for child in _walk(item) if child.objectName() == "moonrakerPlateProgressCanvas"), None)
        if canvas is not None:
            from PyQt6.QtCore import QPointF
            origin = canvas.mapToGlobal(QPointF(0, 0))
            result["canvas_rect"] = [origin.x(), origin.y(), canvas.width(), canvas.height()]
        model = item.property("printerModel")
        if model is not None:
            result["layer"] = int(model.property("plateProgressAnchor"))
            result["moving"] = result["smooth_gpu"] and result["motion"] > 0
            result["finished"] = result["moving"] and result["layer"] == 2
        break
"""

PENGUIN_PREVIEW_READ = """from UM.Application import Application
app = Application.getInstance()
view = app.getController().getActiveView()
result = {"ready": False, "moving": False, "finished": False}
for extension in app.getExtensions():
    if "MoonrakerPrintFollower" in type(extension).__name__:
        rt = extension._runtime
        state = rt.preview._state
        result["ready"] = bool(state.attached and not rt.presentation.reported_position and rt.preview._motion is not None and view is not None and hasattr(view, "getLayerData") and view.getLayerData() is not None and view.getMaxLayers() == 2)
        result["layer"] = state.expected_layer
        result["path"] = state.expected_path
        result["moving"] = bool(result["ready"] and state.expected_path is not None and state.expected_path > 0 and view.getCurrentLayer() == state.expected_layer)
        result["finished"] = result["moving"] and state.expected_layer == 2
        break
"""
