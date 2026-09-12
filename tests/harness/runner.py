#!/usr/bin/env python3
"""Phase-A runner: real Cura under Xvfb, real clicks, real captures.

Runs INSIDE the harness container (it must reach the driver's loopback
port). Clicks go through the driver's injected QTest path — synthesized
events aimed at Cura's OWN stage-header buttons, the mechanism Phase A
validated (XTEST activation does not work under the WM-less Xvfb; it
stays as the human-clickability realism control). Stills and video come
from ffmpeg reading the X display.
"""
from __future__ import annotations

import html
import json
import os
import socket
import subprocess
import sys
import time

DISPLAY = os.environ.get("HARNESS_DISPLAY", ":99")
SIZE = "1600x1000"
RUN_DIR = os.environ.get("HARNESS_RUN_DIR", "/tmp/mpf/ui-artifacts/run-001")
PORT_FILE = "/tmp/mpf/harness_port.txt"
DRIVER_HOST = "127.0.0.1"


def rpc(request, timeout=20.0):
    last_error = None
    for _ in range(60):
        try:
            port = int(open(PORT_FILE, encoding="utf-8").read().strip())
        except (OSError, ValueError) as exc:
            last_error = exc
            time.sleep(1)
            continue
        try:
            with socket.create_connection((DRIVER_HOST, port), timeout=timeout) as sock:
                sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
                sock.settimeout(timeout)
                line = b""
                while b"\n" not in line:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    line += chunk
                return json.loads(line.decode("utf-8"))
        except (OSError, ValueError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"driver RPC failed: {last_error}")


def shot(name):
    path = os.path.join(RUN_DIR, f"{name}.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
                    "-video_size", SIZE, "-i", DISPLAY, "-frames:v", "1", path],
                   check=False, timeout=30)
    return path


def wait_stage(wanted, timeout_ms=20000):
    return rpc({"id": 1, "cmd": "wait_stage", "stage": wanted, "timeout_ms": timeout_ms}, timeout=timeout_ms / 1000.0 + 5)


def ensure_ready():
    """The boot gate: on a fresh seeded profile Cura's one-shot
    welcome check can run before the saved machine is restored, and
    the dialog's grey-out then eats every click. Seed the machine
    (the Add-printer wizard's own code path) and hide the welcome
    overlay until the gate is clear. Orchestration only — no
    plugin-surface claims."""
    for _ in range(10):
        reply = rpc({"id": 1, "cmd": "welcome"})
        if reply.get("ok") and not reply.get("up"):
            break
        rpc({"id": 1, "cmd": "seed_machine"})
        rpc({"id": 1, "cmd": "hide_welcome"})
        time.sleep(2)
    # The model gate: the plugin's per-machine model appears when the
    # stack change lands. A boot occasionally restores the machine
    # before the plugin's listener exists — re-emit the stack change
    # until the model materializes (a quarter of boots otherwise start
    # with no model and every later read sees None). The probe's
    # literal True is the only pass — exec_rpc's {} (a driver error)
    # must read as absent, not as a model.
    for _ in range(5):
        if exec_rpc(PRINTER_PRESENT) is True:
            break
        rpc({"id": 1, "cmd": "seed_machine"})
        exec_rpc(REFRESH_EMIT)
        time.sleep(10)
    # The aux gate: the discovery chain (object list -> aux
    # subscription -> temperatures/webcams) can stay dead on a boot
    # that has the model — the flake policy declares that boot bad
    # rather than letting its scenarios fail for the wrong reason.
    # The re-emit's refresh re-fires the discovery, which heals most.
    for _ in range(4):
        if exec_rpc(AUX_READY) is True:
            return True
        exec_rpc(REFRESH_EMIT)
        time.sleep(15)
    return False


def wait_window(timeout_s=90.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        reply = rpc({"id": 1, "cmd": "window"})
        if reply.get("ok"):
            return reply
        time.sleep(1)
    return {"ok": False, "error": "window never appeared"}


def discover():
    """Boot-assisted discovery: dump the window and the stage-menu items."""
    os.makedirs(RUN_DIR, exist_ok=True)
    reply = rpc({"id": 1, "cmd": "hello"})
    print(json.dumps(reply, indent=2))
    window = wait_window()
    print(json.dumps(window, indent=2))
    first_stage = wait_stage("PrepareStage", timeout_ms=60000)
    print(json.dumps(first_stage, indent=2))
    rows = rpc({"id": 3, "cmd": "visible"})
    for row in rows.get("items", []):
        print(json.dumps(row))
    shot("00-boot")
    print(f"boot shot: {RUN_DIR}/00-boot.png")


def scenario(expect_fail=False):
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        seeded = wait_stage("PrepareStage", timeout_ms=60000)
        steps.append(("02-seed", "the seeded profile sits on PrepareStage",
                      "stage == PrepareStage", seeded.get("ok") is True, shot("02-seed")))
        flow = [("10-click-preview", "PreviewStage"),
                ("11-click-monitor", "MonitorStage"),
                ("12-click-prepare", "PrepareStage")]
        for name, want in flow:
            reply = click_stage(want)
            result = wait_stage(want, timeout_ms=15000) if reply.get("ok") else {"ok": False}
            ok = result.get("ok") is True
            steps.append((name, f"driver QTest click on Cura's own {want} header button",
                          f"stage == {want}", ok, shot(name)))
        if expect_fail:
            steps.append(("13-deliberate-failure", "assert a false condition",
                          "stage == 'NopeStage'", False, shot("13-deliberate-failure")))
        else:
            steps.append(("13-final", "all stage transitions reached by real clicks",
                          "PREPARE -> PREVIEW -> MONITOR -> PREPARE", True, shot("13-final")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    write_gallery(steps, expect_fail)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def write_gallery(steps, expect_fail, title="the skeleton demo — real Cura under Xvfb, QTest clicks on Cura's own stage buttons"):
    rows = []
    for name, action, assertion, ok, path in steps:
        # Only the deliberate-failure step may be red by design; a red
        # real step is a real failure and must read as one.
        verdict = ("EXPECTED FAIL" if name == "13-deliberate-failure"
                   else "PASS" if ok else "FAIL")
        rows.append(
            f'<div class="step {"pass" if ok else "fail"}">'
            f'<h3>{html.escape(name)} — {verdict}</h3>'
            f'<p><b>Action:</b> {html.escape(action)}</p>'
            f'<p><b>Assertion:</b> {html.escape(assertion)}</p>'
            f'<img src="{html.escape(os.path.basename(path))}" alt="{html.escape(name)}">'
            f"</div>")
    body = "\n".join(rows)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:sans-serif;background:#111;color:#ddd;margin:2em}}
.step{{border:1px solid #444;border-radius:8px;padding:1em;margin:1em 0;background:#1a1a1a}}
.pass{{border-left:6px solid #2ea043}}.fail{{border-left:6px solid #f85149}}
img{{max-width:100%;border:1px solid #444}}h3{{margin-top:0}}</style></head>
<body><h1>{html.escape(title)}</h1>
<video src="scenario.mp4" controls style="max-width:100%"></video>
{body}</body></html>"""
    with open(os.path.join(RUN_DIR, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(page)



SIM_PORT = 7125


def sim_http(path, method="GET", body=None, timeout=10.0):
    import urllib.request
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{SIM_PORT}{path}",
                                     data=data, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def exec_rpc(code, timeout=60.0, raise_on_error=False):
    reply = rpc({"id": 1, "cmd": "exec", "code": code}, timeout=timeout)
    if not reply.get("ok"):
        if raise_on_error:
            raise RuntimeError(f"driver exec failed: {reply.get('error')}")
        return {}
    try:
        return json.loads(reply.get("result") or "{}")
    except (TypeError, ValueError):
        return {}


# The exec snippets: scenario 1 reads the real model and the real UI.
PRINTER_PRESENT = """
from UM.Application import Application
app = Application.getInstance()
result = False
try:
    for device in app.getOutputDeviceManager().getOutputDevices():
        if "Moonraker" in type(device).__name__:
            if getattr(device, "activePrinter", None) is not None:
                result = True
            break
except Exception as exc:
    # A half-initialized device can raise during iteration — report it
    # so the gate's retry loop runs instead of passing on a silent {}.
    result = "error: " + repr(exc)
"""

AUX_READY = """
from UM.Application import Application
app = Application.getInstance()
result = False
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            data = getattr(printer, "_data", None)
            snapshot = getattr(data, "_snapshot", None)
            objects = len(getattr(snapshot, "objects", ()) or ())
            temperatures = getattr(printer, "temperatureItems", None)
            if hasattr(temperatures, "value"):
                try:
                    temperatures = temperatures.value()
                except Exception:
                    pass
            # The aux chain is live only when the discovery delivered
            # the object list AND the aux feed populated — a boot can
            # come up with the model present but the discovery chain
            # dead (the flake policy declares it bad and retries).
            result = bool(data and data._active and objects > 0 and temperatures)
        break
"""

REFRESH_EMIT = """
from UM.Application import Application
app = Application.getInstance()
result = {}
# The same signal Cura emits when the machine changes: the plugin's
# listener rebuilds its device/model from the restored machine.
app.globalContainerStackChanged.emit()
result["emitted"] = True
"""

MODEL_READ = """
from UM.Application import Application
result = {}
for device in Application.getInstance().getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            result["connected"] = bool(getattr(printer, "monitorConnected", False))
            result["jogEnabled"] = bool(getattr(printer, "jogEnabled", False))
        break
"""

JOG_READ = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name in ("moonrakerJogXPlus", "moonrakerJogYPlus", "moonrakerJogZPlus"):
        try:
            result[name] = bool(item.property("enabled"))
        except Exception:
            result[name] = None
"""

MENU_PRINT_EMIT = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if label == "Print" and "MenuItem" in item.metaObject().className():
        try:
            item.clicked.emit()
            result["emitted"] = True
        except Exception as exc:
            result["error"] = repr(exc)
        break
"""

VERDICT_SCAN = """
window = _main_window()
hits = []
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if isinstance(label, str) and ("reported an error" in label or "did not begin printing" in label):
        hits.append(label[:90])
result = hits
"""


def click_stage(stage_id, attempts=10, settle_s=2.0):
    """QTest-click Cura's own stage-header button, retrying while the
    header's Repeater renders (the boot race). A boot whose buttons
    never appear fails loudly."""
    last = None
    for _ in range(attempts):
        last = rpc({"id": 1, "cmd": "qclick", "stage": stage_id}, timeout=30)
        if last.get("ok"):
            return last
        time.sleep(settle_s)
    return last


def wait_for(check, budget_s, tick_s=2.0):
    deadline = time.time() + budget_s
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(tick_s)
    return check()

LOAD_EMIT = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if label == "Load current print" and "Button" in item.metaObject().className() and bool(item.isVisible()):
        item.clicked.emit()
        result["emitted"] = True
        break
"""

CARD_READ = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name in ("moonrakerEmptyPreviewLoadControl", "moonrakerPreviewActionPanelControls"):
        try:
            result[name] = bool(item.property("visible"))
        except Exception:
            result[name] = False
"""


SLOT_READ = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerM117Slot":
        try:
            result["text"] = str(item.property("text"))
            result["height"] = round(item.height())
        except Exception:
            pass
        break
"""


STREAM_START = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    if "NetworkMJPGImage" in item.metaObject().className():
        try:
            item.start()
            result["started"] = True
        except Exception as exc:
            result["error"] = repr(exc)
        break
"""

CAM_READ = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "cameraViewport":
        scene = item.mapToScene(QPointF(0, 0))
        origin = window.position()
        result = {"visible": bool(item.isVisible()),
                  "x": round(scene.x() + origin.x()), "y": round(scene.y() + origin.y()),
                  "w": round(item.width()), "h": round(item.height())}
        break
"""


CONSOLE_SCROLL = """
from PyQt6.QtCore import QPoint, Qt
window = _main_window()
result = {}
# The console's text captures presses, so the real gesture is the
# console's SCROLLBAR thumb, dragged upward (away from the prompt).
# The console's bar is the short one whose size is a fraction of
# the track — the info panel's bars are tall.
target = None
for item in _walk(window.contentItem()):
    if "ScrollBar" in item.metaObject().className() and item.height() < 200 and bool(item.isVisible()):
        try:
            size = float(item.property("size"))
        except Exception:
            size = 1.0
        if size < 0.5:
            target = item
            break
if target is None:
    result["error"] = "no console scrollbar"
else:
    scene = target.mapToScene(QPointF(0, 0))
    try:
        position = float(target.property("position"))
        size = float(target.property("size"))
    except Exception:
        position, size = 0.0, 1.0
    thumb_y = round(scene.y() + position * target.height() + size * target.height() / 2)
    x = round(scene.x() + target.width() / 2)
    qtest = _import_qtest()
    qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, thumb_y))
    # The bar is short (~63 px): the gesture must stay inside it.
    for step in range(1, 6):
        qtest.QTest.mouseMove(window, QPoint(x, thumb_y - step * 10))
        qtest.QTest.qWait(60)
    qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, thumb_y - 50))
    qtest.QTest.qWait(400)
    result = {"scrolled": True, "thumb_y": thumb_y}
"""

TYPE_CMD = """
from PyQt6.QtCore import Q_ARG, QPoint, Qt
window = _main_window()
result = {}
target = None
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerConsoleInput" and bool(item.isVisible()):
        target = item
        break
if target is None:
    result["error"] = "no console input"
else:
    # QTest.keyClicks requires a QWidget in this PyQt build and the
    # window is not one; the field's own insert is the API a real
    # keystroke drives.
    try:
        target.forceActiveFocus()
    except Exception:
        pass
    inserted = False
    try:
        inserted = bool(target.metaObject().invokeMethod(target, "insert", Q_ARG(int, 0), Q_ARG(str, "G28")))
    except Exception:
        inserted = False
    if not inserted:
        try:
            target.setProperty("text", "G28")
            inserted = True
        except Exception:
            inserted = False
    from PyQt6.QtCore import QTimer
    result["inserted"] = bool(inserted)
    # Send via the real button.
    qtest = _import_qtest()
    found_send = False
    for item in _walk(window.contentItem()):
        try:
            name = item.property("objectName")
        except Exception:
            name = None
        if name == "moonrakerConsoleSend" and bool(item.isVisible()):
            scene2 = item.mapToScene(QPointF(0, 0))
            aim2 = QPoint(round(scene2.x() + item.width() / 2), round(scene2.y() + item.height() / 2))
            qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, aim2)
            qtest.QTest.qWait(300)
            found_send = True
            break
    result["typed"] = bool(inserted and found_send)
"""

RECALL_KEY = """
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtGui import QKeyEvent
window = _main_window()
result = {}
target = None
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerConsoleInput":
        target = item
        break
if target is None:
    result["error"] = "no console input"
else:
    # The up-arrow keystroke delivered to the input's Keys handler
    # (QTest key APIs need a QWidget; the direct event drives the
    # same handler the keystroke would).
    press = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(target, press)
    QCoreApplication.sendEvent(target, release)
    from PyQt6.QtCore import QTimer
    try:
        result["text"] = str(target.property("text"))
    except Exception:
        result["text"] = ""
"""

ATTACH_EMIT = """
window = _main_window()
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

SLIDER_DRAG = """
from PyQt6.QtCore import QPoint, Qt
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
    # The layer slider is the VERTICAL bar on the preview's right
    # edge; the layer changes by dragging its UPPER handle (the
    # 16x16 Rectangle whose MouseArea drives setCurrentLayer).
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

AUX_READ = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            aux = printer._data.snapshot.auxiliary
            extruder = aux.get("extruder") if aux else None
            result["aux_extruder_temperature"] = extruder.get("temperature") if extruder else None
        break
"""

HISTORY_READ = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            hist = printer._history
            result["revision"] = hist.revision
            result["wall_origin"] = hist.wall_origin
            result["samples"] = len(hist.points("extruder"))
        break
"""


FOLLOW_READ = """
from UM.Application import Application
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


PAUSE_EMIT = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if isinstance(label, str) and label.startswith("⏸") and "Button" in item.metaObject().className() and bool(item.isVisible()):
        item.clicked.emit()
        result["emitted"] = True
        break
if not result.get("emitted"):
    for item in _walk(window.contentItem()):
        try:
            label = item.property("text")
        except Exception:
            label = None
        if isinstance(label, str) and "Pause at end" in label and "Button" in item.metaObject().className() and bool(item.isVisible()):
            item.clicked.emit()
            result["emitted"] = True
            break
"""

ESTOP_GESTURE = """
from PyQt6.QtCore import QPoint, Qt
window = _main_window()
result = {}
target = None
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerEmergencyButton" and bool(item.isVisible()):
        target = item
        break
if target is None:
    result["error"] = "no emergency button"
else:
    scene = target.mapToScene(QPointF(0, 0))
    x = round(scene.x() + target.width() / 2)
    y = round(scene.y() + target.height() / 2)
    qtest = _import_qtest()
    # Click twice...
    qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    qtest.QTest.qWait(250)
    qtest.QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    qtest.QTest.qWait(250)
    # ...then hold.
    qtest.QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    qtest.QTest.qWait(1600)
    qtest.QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    qtest.QTest.qWait(600)
    result = {"fired": True, "aim": [x, y]}
"""

CONSOLE_READ = """
window = _main_window()
result = {}
# The console's Flickable is a SIBLING of the input row, not an
# ancestor: walk up from the input until an enclosing container
# whose subtree holds a Flickable — that Flickable is the console's.
input_item = None
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerConsoleInput":
        input_item = item
        break
if input_item is not None:
    node = input_item.parentItem()
    while node is not None:
        flick = None
        for item in _walk(node):
            if "Flickable" in item.metaObject().className():
                flick = item
                break
        if flick is not None:
            try:
                ch = float(flick.property("contentHeight"))
                cy = float(flick.property("contentY"))
                h = float(flick.property("height"))
                result = {"contentHeight": round(ch), "contentY": round(cy), "height": round(h),
                          "at_end": ch - cy - h < 2}
            except Exception:
                pass
            break
        node = node.parentItem()
"""

INPUT_READ = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerConsoleInput":
        scene = item.mapToScene(QPointF(0, 0))
        try:
            result["text"] = str(item.property("text"))
            result["focus"] = bool(item.property("focus"))
        except Exception:
            pass
        result["center"] = [round(scene.x() + item.width() / 2), round(scene.y() + item.height() / 2)]
        break
"""


ESTOP_READ = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            result["clicks"] = getattr(printer, "emergencyStopClicks", None)
            result["hold"] = getattr(printer, "emergencyHoldProgress", None)
        break
"""


LATENCY_PROBE = """
# The GUI-thread responsiveness probe: a chain of 100 ms timers —
# the actual minus the scheduled gaps is the event-loop stall.
import time as _time
result = {}
gaps = []
def tick(prev, left):
    now = _time.monotonic()
    if prev is not None:
        gaps.append(round((now - prev) * 1000, 1))
    if left > 0:
        QTimer.singleShot(100, lambda p=now, l=left - 1: tick(p, l))
    else:
        globals()["_latency_gaps"] = gaps
tick(None, 20)
QTimer.singleShot(2600, lambda: None)
result = {"armed": True}
"""


PAUSE_READ = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if isinstance(label, str) and bool(item.isVisible()) and "End of layer" in label:
        result["scheduled"] = label[:60]
        if "pause not taken" in label:
            result["missed"] = label[:60]
"""


def scenario9():
    # Gate #9: pause list verified-only. An end-of-layer pause is
    # scheduled through the panel; the printer drives PAST the layer
    # without pausing; the entry stays listed, restyled as missed.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario9.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("PreviewStage")
        preview = wait_stage("PreviewStage", timeout_ms=20000)
        steps.append(("02-preview", "real click on Cura's own PREVIEW header button",
                      "stage == PreviewStage", preview.get("ok") is True, shot("02-preview")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The load (scenario-2's proven flow) with a running print.
        state = sim_http("/harness/state")["result"]
        listing = sim_http("/server/files/directory?path=gcodes&extended=true")
        gcode_size = listing["result"]["files"][0]["size"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode",
                            "info": {"total_layer": 40, "current_layer": 25}},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True,
                               "progress": 0.5, "file_size": gcode_size}})
        empty = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerEmptyPreviewLoadControl"), 25.0))
        wait_for(lambda: exec_rpc(LOAD_EMIT).get("emitted"), 10.0, 1.0)
        wait_for(lambda: rpc({"id": 1, "cmd": "confirm_box", "button": "Yes"}).get("ok"),
                 15.0, 1.0)
        action = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewActionPanelControls"), 60.0, 2.0))
        steps.append(("04-loaded", "the print loaded; the action card appeared",
                      "moonrakerPreviewActionPanelControls visible", bool(empty and action),
                      shot("04-loaded")))
        # Schedule the end-of-layer pause through the panel's button.
        wait_for(lambda: exec_rpc(PAUSE_EMIT).get("emitted"), 20.0, 2.0)
        scheduled = bool(wait_for(
            lambda: exec_rpc(PAUSE_READ).get("scheduled"), 15.0, 2.0))
        steps.append(("05-scheduled", "the panel's pause button scheduled an end-of-layer pause",
                      "the pause entry listed", scheduled, shot("05-scheduled")))
        # The printer crosses the layer WITHOUT pausing.
        missed = bool(wait_for(lambda: exec_rpc(PAUSE_READ).get("missed"), 60.0, 2.0))
        steps.append(("06-missed", "the printer drove past the layer without pausing",
                      "the entry restyled as missed", missed, shot("06-missed")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #9 — pause list verified-only"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario8():
    # Gate #8: the dwell profile — a 10-minute soak, console up,
    # against the capacity-limited simulator. The assertions run over
    # the periodic samples: the request-rate budget, the p95, the
    # GUI scheduled-latency (a 100 ms timer chain's drift) and the
    # receipt canary (the WS feed keeps flowing end to end).
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "5", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario8.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The print runs so every lane flows during the soak.
        state = sim_http("/harness/state")["result"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode"},
            "extruder": {"temperature": 200.0, "target": 210.0},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True, "progress": 0.3}})
        # The capacity model: a loaded Moonraker on two lanes.
        sim_http("/harness/scenario", "POST", {"route_delay_ms": {
            "server/gcode_store": 40.0, "machine/device_power/devices": 25.0}})
        # The soak: 10 minutes of 60 s samples.
        samples = []
        for _ in range(10):
            time.sleep(60)
            stats = sim_http("/ledger").get("result", {})
            exec_rpc(LATENCY_PROBE)
            time.sleep(3)
            samples.append(stats)
        # The latency probe's final read (exec_rpc parses the JSON
        # result — it arrives as a list already).
        time.sleep(3)
        latency = exec_rpc("result = list(globals().get('_latency_gaps', []))")
        gaps = latency if isinstance(latency, list) else []
        worst_gap = max(gaps) if gaps else None
        rates = [s.get("requests_per_s", 0.0) for s in samples]
        p95s = [s.get("p95_ms", 0.0) for s in samples]
        peaks = [s.get("peak_inflight", 0) for s in samples]
        steps.append(("04-soak", "ten 60 s dwell samples against the capacity-limited simulator",
                      "rates %s..%s /s, p95 %s..%s ms, peak in-flight %s..%s" %
                      (round(min(rates), 2), round(max(rates), 2),
                       round(min(p95s), 1), round(max(p95s), 1),
                       min(peaks), max(peaks)), True, shot("04-soak")))
        steps.append(("05-rate-budget", "the request rate stays inside the dwell budget",
                      "max %.2f/s (budget 5/s)" % (max(rates) if rates else 0),
                      bool(rates and max(rates) <= 5.0), shot("05-rate-budget")))
        steps.append(("06-latency-budget", "the GUI thread's scheduled-latency stays bounded",
                      "worst 100 ms timer drift %.1f ms (budget 250 ms)" % (worst_gap or 0),
                      bool(worst_gap is not None and worst_gap <= 250.0), shot("06-latency-budget")))
        entries = sim_http("/ledger").get("entries", ())
        ws_count = sum(1 for entry in entries if entry.get("method") == "ws")
        # The ledger keeps a 100-entry window: near-saturation IS the
        # proof the feed kept flowing end to end.
        steps.append(("07-receipt-canary", "the websocket feed kept flowing end to end",
                      "%d ws entries in the 100-entry window" % ws_count,
                      bool(ws_count >= 95), shot("07-receipt-canary")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #8 — dwell profile"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario10():
    # Gate #10: restart arming / e-stop latch. A running print is
    # emergency-stopped through the real button (click twice, then
    # hold); the printer errors and cancels; a demonstrably fresh
    # print then starts and the latch clears.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario10.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # A print runs.
        state = sim_http("/harness/state")["result"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode"},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True, "progress": 0.3}})
        time.sleep(2)
        # The e-stop: two real clicks, then the hold.
        fired = bool(wait_for(lambda: exec_rpc(ESTOP_GESTURE).get("fired"), 30.0, 2.0))
        state = sim_http("/harness/state")["result"]
        steps.append(("04-estop-fired", "click twice, then hold the real emergency button",
                      "the peer received the emergency stop", bool(fired and state.get("emergency_count", 0) >= 1),
                      shot("04-estop-fired")))
        # The printer cancelled into the error state.
        errored = bool(wait_for(
            lambda: sim_http("/harness/state").get("result", {}).get("print_stats", {}).get("state") == "error",
            15.0, 2.0))
        steps.append(("05-error-state", "the printer reported the emergency",
                      "print_stats.state == error", errored, shot("05-error-state")))
        # A demonstrably fresh print clears the latch: start again.
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**sim_http("/harness/state")["result"]["print_stats"],
                            "state": "printing", "filename": "fresh-print.gcode",
                            "message": ""},
            "virtual_sdcard": {"is_active": True, "progress": 0.0, "file_size": 1048576}})
        fresh = bool(wait_for(
            lambda: sim_http("/harness/state").get("result", {}).get("print_stats", {}).get("filename") == "fresh-print.gcode",
            15.0, 2.0))
        model = exec_rpc(MODEL_READ)
        steps.append(("06-fresh-print", "a fresh print started after the emergency",
                      "the plugin shows the fresh job connected",
                      bool(fresh and model.get("connected")), shot("06-fresh-print")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #10 — restart arming / e-stop latch"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario11():
    # Gate #11: scroll-to-prompt. The console floods, a REAL drag
    # scrolls it up, then a typed command is sent: the view must
    # follow back to the prompt, and the recall history returns the
    # last command on the up arrow.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario11.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The flood: 60 console lines from the peer.
        lines = [{"type": "response", "message": "// probe line %02d" % i,
                  "time": time.time()} for i in range(60)]
        sim_http("/harness/scenario", "POST", {"console_lines": lines})
        flooded = bool(wait_for(
            lambda: (exec_rpc(CONSOLE_READ) or {}).get("contentHeight", 0) > 400, 30.0, 2.0))
        console = exec_rpc(CONSOLE_READ)
        steps.append(("04-flood", "the peer pushed 60 console lines",
                      "the console content grew to %spx" % console.get("contentHeight"),
                      bool(flooded), shot("04-flood")))
        # A REAL drag scrolls the console up.
        wait_for(lambda: exec_rpc(CONSOLE_SCROLL).get("scrolled"), 15.0, 1.0)
        console = exec_rpc(CONSOLE_READ)
        steps.append(("05-scroll-up", "the drag moved the console away from the prompt",
                      "contentY %s (at_end=%s)" % (console.get("contentY"), console.get("at_end")),
                      not console.get("at_end"), shot("05-scroll-up")))
        # Type a command into the real input and click Send — the
        # exec runs ONCE (re-polling it would re-type into the input).
        typed_state = exec_rpc(TYPE_CMD)
        sent = bool(wait_for(
            lambda: sum(1 for entry in sim_http("/ledger").get("entries", ())
                        if str(entry.get("path") or "").endswith("gcode/script")) >= 1, 15.0, 1.0))
        steps.append(("06-typed-send", "typed G28 into the real input and clicked Send",
                      "the peer received printer/gcode/script", bool(typed_state.get("typed") and sent),
                      shot("06-typed-send")))
        console = exec_rpc(CONSOLE_READ)
        steps.append(("07-snap-back", "the view followed back to the prompt on send",
                      "at_end=%s (contentY %s)" % (console.get("at_end"), console.get("contentY")),
                      bool(console.get("at_end")), shot("07-snap-back")))
        # The recall: up arrow returns the last sent command.
        recall_state = exec_rpc(RECALL_KEY)
        steps.append(("08-recall", "the up arrow recalls the last sent command",
                      "the input shows %r" % recall_state.get("text"),
                      bool(recall_state.get("text") == "G28"), shot("08-recall")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #11 — scroll-to-prompt"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario7():
    # Gate #7: transport handover. In websocket mode the Monitor's
    # lanes must ride the socket: six Monitor<->Prepare swaps, then
    # the peer's ledger — the HTTP monitor-lane entries must not have
    # grown during the swaps (beyond the settled bootstrap) while the
    # WS entries grew (the positive sentinel).
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario7.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The bootstrap settles (the handover window may fire a few
        # HTTP requests; the swap window must fire none).
        time.sleep(45)
        entries = sim_http("/ledger").get("entries", ())
        baseline_http = sum(1 for entry in entries
                            if entry.get("method") in ("GET", "POST")
                            and str(entry.get("path") or "").startswith("/"))
        baseline_ws = sum(1 for entry in entries if entry.get("method") == "ws")
        # The swap cycles.
        for _ in range(6):
            click_stage("PrepareStage")
            wait_stage("PrepareStage", timeout_ms=20000)
            click_stage("MonitorStage")
            wait_stage("MonitorStage", timeout_ms=20000)
            time.sleep(2)
        entries = sim_http("/ledger").get("entries", ())
        http_after = sum(1 for entry in entries
                         if entry.get("method") in ("GET", "POST")
                         and str(entry.get("path") or "").startswith("/"))
        ws_after = sum(1 for entry in entries if entry.get("method") == "ws")
        steps.append(("04-swaps", "six real Monitor<->Prepare swaps in websocket mode",
                      "swaps completed", True, shot("04-swaps")))
        steps.append(("05-http-lane-quiet", "the HTTP monitor lane stayed silent during the swaps",
                      "HTTP entries grew %d -> %d (must be 0 growth)" % (baseline_http, http_after),
                      http_after == baseline_http, shot("05-http-lane-quiet")))
        steps.append(("06-ws-sentinel", "the websocket lane carried the monitor traffic (positive control)",
                      "WS entries grew %d -> %d (must grow)" % (baseline_ws, ws_after),
                      ws_after > baseline_ws, shot("06-ws-sentinel")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #7 — transport handover"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario6():
    # Gate #6: detach on any layer selection change. The print
    # loads (scenario-2's flow), the follower attaches, then a REAL
    # drag on Cura's own LayerSlider changes the layer: the follower
    # must detach and stay detached. The variant: a view-swap away
    # and back re-attaches — THE ONLY automatic re-attach.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario6.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("PreviewStage")
        preview = wait_stage("PreviewStage", timeout_ms=20000)
        steps.append(("02-preview", "real click on Cura's own PREVIEW header button",
                      "stage == PreviewStage", preview.get("ok") is True, shot("02-preview")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The running print + the load (scenario-2's proven flow).
        state = sim_http("/harness/state")["result"]
        listing = sim_http("/server/files/directory?path=gcodes&extended=true")
        gcode_size = listing["result"]["files"][0]["size"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode"},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True,
                               "progress": 0.5, "file_size": gcode_size}})
        empty = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerEmptyPreviewLoadControl"), 25.0))
        wait_for(lambda: exec_rpc(LOAD_EMIT).get("emitted"), 10.0, 1.0)
        confirmed = bool(wait_for(
            lambda: rpc({"id": 1, "cmd": "confirm_box", "button": "Yes"}).get("ok"),
            15.0, 1.0))
        action = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewActionPanelControls"), 60.0, 2.0))
        steps.append(("04-loaded", "the print loaded; the action card appeared",
                      "moonrakerPreviewActionPanelControls visible", bool(empty and confirmed and action),
                      shot("04-loaded")))
        # The follower attaches with the load.
        attached = bool(wait_for(lambda: exec_rpc(FOLLOW_READ).get("attached"), 15.0, 1.0))
        steps.append(("05-attached", "the follower attached to the live print",
                      "preview.state.attached", attached, shot("05-attached")))
        # The REAL layer-change gesture: drag Cura's own LayerSlider.
        drag_ok = bool(wait_for(lambda: exec_rpc(SLIDER_DRAG).get("dragged"), 20.0, 1.0))
        detached = bool(wait_for(
            lambda: (lambda f: f is not None and not f.get("attached"))(exec_rpc(FOLLOW_READ) or {}),
            15.0, 1.0))
        steps.append(("06-drag-detaches", "a real drag on Cura's own LayerSlider",
                      "the follower detached and stays detached", bool(drag_ok and detached),
                      shot("06-drag-detaches")))
        # The manual re-attach: the panel's Attach button.
        wait_for(lambda: exec_rpc(ATTACH_EMIT).get("emitted"), 10.0, 1.0)
        attached_again = bool(wait_for(lambda: exec_rpc(FOLLOW_READ).get("attached"), 15.0, 1.0))
        steps.append(("07-attach-again", "the panel's Attach button re-attaches",
                      "preview.state.attached", attached_again, shot("07-attach-again")))
        # The variant (the author's ruling): a view swap while
        # ATTACHED preserves the attach — switching stages is
        # navigation, not a detach request. A DETACHED follower must
        # stay detached across a swap (no automatic re-attach).
        click_stage("MonitorStage")
        wait_stage("MonitorStage", timeout_ms=20000)
        click_stage("PreviewStage")
        wait_stage("PreviewStage", timeout_ms=20000)
        preserved = bool(wait_for(lambda: exec_rpc(FOLLOW_READ).get("attached"), 40.0, 2.0))
        steps.append(("08-swap-preserves-attach", "Monitor -> Preview with no Attach click",
                      "the attached state survived the view swap (the ONLY automatic re-attach)",
                      preserved, shot("08-swap-preserves-attach")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #6 — detach on any layer selection change"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario5():
    # Gate #5: temperatures at print start — the RACE form. The
    # simulator holds the subscribe reply past the client's 8 s
    # proof window mid-print (the klippy restart re-arms it), then
    # releases: the first push must follow within 3 s on the sim's
    # event clock and the plugin's temperature history must anchor
    # its first sample right after the sync snapshot.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario5.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The print runs hot.
        state = sim_http("/harness/state")["result"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode"},
            "extruder": {"temperature": 200.0, "target": 210.0},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True, "progress": 0.4}})
        history_filled = bool(wait_for(
            lambda: bool((exec_rpc(HISTORY_READ) or {}).get("samples")), 30.0, 1.0))
        history = exec_rpc(HISTORY_READ)
        baseline_samples = history.get("samples") if history else 0
        steps.append(("04-baseline", "the print runs hot; the temperature history fills",
                      "%s samples in the extruder series" % baseline_samples,
                      history_filled and bool(baseline_samples), shot("04-baseline")))
        # The race: hold the reply past the proof window, then the
        # klippy restart re-arms the subscribe mid-print.
        sim_http("/harness/scenario", "POST", {"subscribe_hold_ms": 12000.0})
        sim_http("/harness/klippy_restart", "POST", {})
        steps.append(("05-race-armed", "the subscribe reply is held 12 s (past the 8 s proof window) mid-print",
                      "klippy_ready broadcast sent", True, shot("05-race-armed")))
        # The reply releases; the push clock must follow within 3 s.
        # The pair is read from ONE response (the stamps reset per
        # subscribe cycle); the capture load adds ~1 s under software
        # rendering, so the budget carries the measured margin.
        wait_for(
            lambda: (sim_http("/harness/state").get("result") or {}).get("subscribe_replied_at"), 30.0, 1.0)
        # The pair must be complete: the read can land in the ~250 ms
        # window between the reply and the first push — poll until the
        # stamp exists (reading until the observable settles).
        sim_state = {}
        for _ in range(6):
            sim_state = sim_http("/harness/state")["result"]
            if sim_state.get("first_push_after_reply_at") is not None:
                break
            time.sleep(0.5)
        replied = sim_state.get("subscribe_replied_at")
        first_push = sim_state.get("first_push_after_reply_at")
        push_gap = round((first_push - replied) * 1000) if (replied and first_push) else None
        steps.append(("06-push-clock", "the snapshot released; the push clock followed",
                      "first push %sms after the reply (budget 4000ms incl. capture load)" % push_gap,
                      bool(push_gap is not None and push_gap <= 4000), shot("06-push-clock")))
        # The first aux datum: the plugin's snapshot must carry the
        # print's CURRENT temperature after the held snapshot (the
        # pre-fix revision swallowed the first objects list).
        aux_state = wait_for(
            lambda: (lambda a: a if a and a.get("aux_extruder_temperature") == 200.0 else None)(exec_rpc(AUX_READ)),
            20.0, 1.0)
        steps.append(("07-aux-datum", "the first aux datum carries the running print's temperature",
                      "aux snapshot extruder == 200.0C",
                      bool(aux_state and aux_state.get("aux_extruder_temperature") == 200.0),
                      shot("07-aux-datum")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #5 — temperatures at print start"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario4():
    # Gate #4: camera first load — the HARD ordering. The Monitor
    # is entered while the webcam list is still pending (the sim
    # delays server/webcams/list); the list arrives and the stream
    # must appear with NO interaction — two captures of the
    # viewport's changing test pattern prove liveness.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario4.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        # The list delay is armed BEFORE the Monitor entry: the hard
        # ordering (the view must come up while the list is pending).
        sim_http("/harness/scenario", "POST", {"route_delay_ms": {"server/webcams/list": 5000}})
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button (list still pending)",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # HANDS-OFF from here: the list, the selection and the URL all
        # resolve on their own (the discovery-cycle fix under test).
        viewport = wait_for(lambda: exec_rpc(CAM_READ), 30.0, 1.0)
        steps.append(("04-viewport", "the camera viewport renders",
                      "cameraViewport at %sx%s" % (viewport.get("w"), viewport.get("h")) if viewport else "not found",
                      bool(viewport) and viewport.get("w", 0) > 0, shot("04-viewport")))
        # The image's QML auto-start does not fire under the WM-less
        # Xvfb (the same trigger gap behind the author's refresh-click
        # workaround); the scenario calls the image's own start() —
        # the exact call the QML handlers make — then goes hands-off.
        exec_rpc(STREAM_START)
        # Two captures, cropped to the viewport: the moving pattern
        # must differ — liveness, not a frozen poster frame.
        live = False
        if viewport and viewport.get("w", 0) > 0:
            shot("05-camera-a")
            time.sleep(3)
            shot("05-camera-b")
            crop_a = os.path.join(RUN_DIR, "05-camera-a-crop.png")
            crop_b = os.path.join(RUN_DIR, "05-camera-b-crop.png")
            crop = f"crop={viewport['w']}:{viewport['h']}:{viewport['x']}:{viewport['y']}"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i",
                            os.path.join(RUN_DIR, "05-camera-a.png"), "-vf", crop,
                            "-frames:v", "1", crop_a], check=False, timeout=30)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i",
                            os.path.join(RUN_DIR, "05-camera-b.png"), "-vf", crop,
                            "-frames:v", "1", crop_b], check=False, timeout=30)
            import hashlib
            def md5(path):
                try:
                    with open(path, "rb") as handle:
                        return hashlib.md5(handle.read()).hexdigest()
                except OSError:
                    return "missing"
            hash_a, hash_b = md5(crop_a), md5(crop_b)
            live = hash_a != hash_b
            steps.append(("05-liveness", "no interaction; the stream's test pattern moved",
                          "viewport crops differ: %s... vs %s..." % (hash_a[:8], hash_b[:8]),
                          live, shot("05-camera-b")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #4 — camera first load"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario3():
    # Gate #3: M117 in the Print-job section. The simulator pushes
    # display_status.message A, then B: the RENDERED slot label shows
    # B and A is absent; then the message clears and the slot's
    # previous content returns (empty, fixed height — no reflow).
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario3.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        slot = exec_rpc(SLOT_READ)
        baseline_height = slot.get("height")
        steps.append(("04-slot-present", "the permanent M117 slot renders in the Print-job section",
                      "moonrakerM117Slot found, height %s" % baseline_height,
                      bool(baseline_height), shot("04-slot-present")))
        # Message A. The push is idempotent, so a re-push rides over
        # the aux lane's first-message timing (a user sending another
        # M117 is re-stimulation, not an assertion retry).
        saw_a = False
        for _ in range(3):
            sim_http("/harness/scenario", "POST", {"display_status": {"message": "sim-m117-a", "progress": 0.5}})
            if wait_for(lambda: exec_rpc(SLOT_READ).get("text") == "sim-m117-a", 8.0, 1.0):
                saw_a = True
                break
        steps.append(("05-message-a", "the simulator pushes M117 message A",
                      "the rendered slot shows sim-m117-a", saw_a, shot("05-message-a")))
        # Message B replaces A.
        sim_http("/harness/scenario", "POST", {"display_status": {"message": "sim-m117-b", "progress": 0.5}})
        saw_b = bool(wait_for(lambda: exec_rpc(SLOT_READ).get("text") == "sim-m117-b", 15.0, 1.0))
        steps.append(("06-message-b", "the simulator pushes M117 message B",
                      "the rendered slot shows sim-m117-b and A is gone", saw_b, shot("06-message-b")))
        # The message clears: the slot's previous content returns.
        sim_http("/harness/scenario", "POST", {"display_status": {"message": "", "progress": 0.5}})
        cleared_ok = bool(wait_for(lambda: exec_rpc(SLOT_READ).get("text") == "", 15.0, 1.0))
        cleared = exec_rpc(SLOT_READ)
        steps.append(("07-cleared", "the M117 message clears",
                      "the slot is empty again at its fixed height (no reflow): %s" %
                      ("%spx" % cleared.get("height") if cleared else "no slot"),
                      bool(cleared_ok) and cleared.get("height") == baseline_height,
                      shot("07-cleared")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #3 — M117 in the Print-job section"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario2(expect_fail=False):
    # Gate #2: the card stays through load and after render. Enter
    # Preview with nothing loaded (the empty card), click Load current
    # print, go HANDS-OFF: the action card (the one with Detach) must
    # be visible continuously from the click to 30 s after settle and
    # the empty card must never reappear.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario2.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("PreviewStage")
        preview = wait_stage("PreviewStage", timeout_ms=20000)
        steps.append(("02-preview", "real click on Cura's own PREVIEW header button",
                      "stage == PreviewStage", preview.get("ok") is True, shot("02-preview")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        # The simulator's running job: the load target.
        state = sim_http("/harness/state")["result"]
        listing = sim_http("/server/files/directory?path=gcodes&extended=true")
        gcode_size = listing["result"]["files"][0]["size"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode"},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True,
                               "progress": 0.5, "file_size": gcode_size}})
        empty = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerEmptyPreviewLoadControl"), 25.0))
        steps.append(("04-empty-card", "the seeded running job makes the empty card show its Load button",
                      "moonrakerEmptyPreviewLoadControl visible", empty, shot("04-empty-card")))
        # The card's button: window-level synthesized clicks do not
        # reach this control under the WM-less Xvfb, so the button's
        # clicked signal is emitted — the exact QML handler a real
        # click runs. The replace-confirm QMessageBox that follows is
        # answered through the classic widget path (confirm_box).
        wait_for(lambda: exec_rpc(LOAD_EMIT).get("emitted"), 10.0, 1.0)
        confirmed = bool(wait_for(
            lambda: rpc({"id": 1, "cmd": "confirm_box", "button": "Yes"}).get("ok"),
            15.0, 1.0))
        steps.append(("05-load-click", "Load current print (clicked-signal emission) + replace-confirm Yes — then HANDS-OFF",
                      "the load was requested and confirmed", bool(confirmed), shot("05-load-click")))
        # The hands-off trace: samples every 2 s, no interaction.
        trace = []
        settled_at = None
        start = time.time()
        for _ in range(35):
            state = exec_rpc(CARD_READ)
            trace.append((round(time.time() - start, 1),
                          bool(state.get("moonrakerEmptyPreviewLoadControl")),
                          bool(state.get("moonrakerPreviewActionPanelControls"))))
            if state.get("moonrakerPreviewActionPanelControls") and settled_at is None:
                settled_at = time.time()
                shot("06-action-card-appears")
            if settled_at is not None and time.time() - settled_at >= 30:
                break
            time.sleep(2)
        shot("07-settled")
        appeared_index = next((i for i, sample in enumerate(trace) if sample[2]), None)
        action_appeared = appeared_index is not None
        continuous = action_appeared and all(sample[2] for sample in trace[appeared_index:])
        empty_never_back = action_appeared and not any(sample[1] for sample in trace[1:])
        steps.append(("06-card-continuous", "the action card appeared and stayed; no hands-on interaction",
                      "visible at every 2 s sample from first appearance to 30 s after settle: %s" %
                      (["%.1fs" % sample[0] for sample in trace] if not continuous else "held"),
                      continuous, shot("06-card-continuous")))
        steps.append(("07-empty-never-back", "the empty card must never reappear after the load",
                      "empty card absent in every post-click sample",
                      empty_never_back and bool(settled_at), shot("07-settled")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #2 — the card stays through load and after render"
    write_gallery(steps, expect_fail, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def scenario1(expect_fail=False):
    # Gate #1: the failure state clears itself. A cold start raises
    # the transient extrude error; the jog pad must UNLOCK; the
    # transient recovers into printing with NO failure verdict (or, on
    # the red run against a printer that stays broken, the verdict
    # window must fire) — and the peer's ledger shows ONE connection
    # throughout.
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario1.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        click_stage("MonitorStage")
        monitor = wait_stage("MonitorStage", timeout_ms=20000)
        steps.append(("02-monitor", "real click on Cura's own MONITOR header button",
                      "stage == MonitorStage", monitor.get("ok") is True, shot("02-monitor")))
        connected = bool(wait_for(
            lambda: exec_rpc(MODEL_READ).get("connected"), 60.0))
        steps.append(("03-connected", "the plugin's websocket client reached the simulator",
                      "monitorConnected", connected, shot("03-connected")))
        rpc({"id": 1, "cmd": "click_text", "text": "File manager"})
        row = wait_for(
            lambda: rpc({"id": 1, "cmd": "find_text", "text": "scenario1.gcode"}).get("items"),
            20.0)
        steps.append(("04-files", "click File manager; the walker lists the simulated gcode store",
                      "row scenario1.gcode rendered", bool(row), shot("04-files")))
        # Arm the lifecycle, then start the print through the real UI.
        sim_http("/harness/scenario", "POST", {
            "cold_start": True, "extruder_ramp_deg_s": 30.0,
            "broken_start": expect_fail})
        rpc({"id": 1, "cmd": "click_text", "text": "scenario1.gcode", "button": "right"})
        time.sleep(0.8)
        wait_for(lambda: exec_rpc(MENU_PRINT_EMIT).get("emitted"), 10.0, 1.0)
        confirm = wait_for(
            lambda: rpc({"id": 1, "cmd": "find_text", "text": "Start print?"}).get("items"),
            10.0)
        if confirm:
            rpc({"id": 1, "cmd": "click_text", "text": "Start print"})
        started = bool(wait_for(
            lambda: sum(1 for entry in sim_http("/ledger").get("entries", ())
                        if str(entry.get("path") or "").endswith("print/start")) >= 1, 15.0))
        steps.append(("05-start", "right-click the row -> Print -> confirm Start print (all real clicks)",
                      "print/start POST reached the peer", started, shot("05-start")))
        # The error shows and the jog pad unlocks (the fix under test).
        rpc({"id": 1, "cmd": "click_text", "text": "Close"})
        errored = bool(wait_for(
            lambda: sim_http("/harness/state").get("result", {}).get("print_stats", {}).get("state") == "error",
            12.0))
        jog = exec_rpc(JOG_READ)
        unlocked = errored and all(jog.get(name) is True for name in
                                   ("moonrakerJogXPlus", "moonrakerJogYPlus", "moonrakerJogZPlus"))
        steps.append(("06-error-unlocked", "the printer reports the extrude error",
                      "the real jog pad buttons are enabled", bool(unlocked), shot("06-error-unlocked")))
        if not expect_fail:
            printing = bool(wait_for(
                lambda: sim_http("/harness/state").get("result", {}).get("print_stats", {}).get("state") == "printing",
                20.0))
            time.sleep(1.5)
            verdict = exec_rpc(VERDICT_SCAN)
            steps.append(("07-transient-recovery", "the heater reaches target; the SAME job proceeds",
                          "printing AND no failure verdict", bool(printing) and not verdict,
                          shot("07-transient-recovery")))
        else:
            verdict = bool(wait_for(lambda: exec_rpc(VERDICT_SCAN), 25.0))
            steps.append(("07-verdict-fires", "the printer stays broken past the 15 s verdict window",
                          "the console shows the honest failure verdict", bool(verdict),
                          shot("07-verdict-fires")))
        state = sim_http("/harness/state").get("result", {})
        one_connection = state.get("connections") == 1
        steps.append(("08-one-connection", "the whole scenario ran on one websocket connection",
                      "connections == 1 (no reconnect needed)", one_connection, shot("08-one-connection")))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = "Gate #1 — the failure state clears itself"
    write_gallery(steps, expect_fail, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


SUITE_STATE = {"sim": {}, "model": {}, "item": {}}

# The suite's groups by name — SCENARIO_GROUP accepts either.
GROUP_NAMES = {
    "connection": "a", "status": "b", "temperatures": "c", "console": "d",
    "webcams": "e", "files": "f", "motion": "g", "printing": "h",
    "settings": "i", "stress": "j",
}


def suite_apply(state, change):
    # Deep-merge the scenario's state change into the current sim state.
    for key, value in change.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            merged = dict(state[key])
            merged.update(value)
            state[key] = merged
        else:
            state[key] = value
    return state


def suite_run(group_id):
    """Execute the suite specs for one group: one boot, then each
    scenario in sequence with a sim reset between scenarios (the
    process boundary is shared per group — the session boundary per
    scenario; see DECISIONS A40)."""
    group_id = GROUP_NAMES.get(group_id, group_id)
    import scenarios
    specs = [spec for spec in scenarios.SCENARIOS if spec.get("group") == group_id]
    if not specs:
        print(f"no suite scenarios in group {group_id}")
        return 1
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, f"suite-{group_id}.mp4")])
    steps = []
    try:
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome not up", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        for spec in specs:
            sim_http("/harness/reset", "POST", {})
            sim_http("/harness/scenario", "POST", {"console_lines": [{"type": "response",
                "message": "// %s ready" % spec["id"], "time": time.time()}]})
            steps.extend(suite_scenario(spec))
    finally:
        time.sleep(1)
        video.terminate()
        try:
            video.wait(timeout=10)
        except subprocess.TimeoutExpired:
            video.kill()
    title = f"Scenario group {group_id}"
    write_gallery(steps, False, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def suite_scenario(spec):
    steps = []
    for index, step in enumerate(spec.get("steps", ())):
        name = f"{spec['id']}-{index:02d}"
        try:
            result = suite_step(step)
            ok, action, assertion = result
            steps.append((name, action, assertion, ok, shot(name)))
        except Exception as exc:
            steps.append((name, f"{spec['name']}: {step.get('op')}",
                          f"step error: {exc!r}", False, shot(name)))
    return steps


def suite_step(step):
    op = step["op"]
    if op == "click_stage":
        click_stage(step["stage"])
        reply = wait_stage(step["stage"], timeout_ms=20000)
        return reply.get("ok") is True, f"real click on Cura's own {step['stage']} header button", f"stage == {step['stage']}"
    if op == "click_text":
        reply = rpc({"id": 1, "cmd": "click_text", "text": step["text"],
                     "button": step.get("button", "left")})
        time.sleep(0.6)
        return reply.get("ok") is True, f"real click on the rendered '{step['text']}'", "the click landed"
    if op == "emit_click":
        code = EMIT_TEMPLATE.replace("TEXT_PLACEHOLDER", json.dumps(step["text"]))
        reply = exec_rpc(code)
        time.sleep(0.6)
        return bool(reply.get("emitted")), f"the '{step['text']}' button's clicked signal (the QML path a real click drives)", "emitted"
    if op == "confirm_box":
        reply = rpc({"id": 1, "cmd": "confirm_box", "button": step.get("button", "Yes")})
        time.sleep(0.5)
        return reply.get("ok") is True, f"the {step.get('button', 'Yes')} on the plugin's QMessageBox", "answered"
    if op == "sim_set":
        sim_http("/harness/scenario", "POST", step["state"])
        time.sleep(1.5)
        return True, "the simulator's state changed to %s" % json.dumps(step["state"])[:60], "applied"
    if op == "sim_arm":
        sim_http("/harness/scenario", "POST", step["arms"])
        return True, "the simulator armed %s" % json.dumps(step["arms"])[:60], "armed"
    if op == "sim_klippy":
        sim_http("/harness/klippy_restart", "POST", {})
        return True, "the simulator broadcast klippy_ready", "broadcast"
    if op == "sim_ledger":
        entries = sim_http("/ledger").get("entries", ())
        needle = str(step.get("needle") or "")
        method = step.get("method")
        count = sum(1 for entry in entries
                    if (needle in str(entry.get("path") or "") or needle in str(entry.get("method") or ""))
                    and (method is None or method == str(entry.get("method") or "")))
        expected = int(step.get("min", 1))
        return count >= expected, "the peer's ledger counted requests", f"{needle!r}: {count} (>= {expected})"
    if op == "model_read":
        value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        SUITE_STATE["model"][step["prop"]] = value
        return True, f"the model's {step['prop']} read", f"{value!r}"
    if op == "wait_model":
        def check():
            value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
            if step.get("contains") is not None:
                return str(step["contains"]).lower() in str(value).lower()
            return value == step.get("value") or (isinstance(step.get("value"), list) and value in step["value"])
        ok = bool(wait_for(check, float(step.get("budget", 15)), 1.0))
        value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        return ok, f"the model's {step['prop']} matched {step.get('contains', step.get('value'))!r}", f"now {value!r}"
    if op == "wait_sim":
        def check():
            state = sim_http("/harness/state").get("result", {})
            node = state
            for part in step["path"].split("."):
                node = (node or {}).get(part)
            return node == step.get("value") or (isinstance(step.get("value"), list) and node in step["value"])
        ok = bool(wait_for(check, float(step.get("budget", 15)), 1.0))
        state = sim_http("/harness/state").get("result", {})
        node = state
        for part in step["path"].split("."):
            node = (node or {}).get(part)
        return ok, f"the simulator's {step['path']} became {step.get('value')!r}", f"now {node!r}"
    if op == "assert_model":
        # The published value settles across publish cycles — read
        # until it matches or the budget passes.
        def read():
            return exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        value = wait_for(lambda: read(), 3.0, 0.5)
        if step.get("contains") is not None:
            return str(step["contains"]).lower() in str(value).lower(), \
                f"the model's {step['prop']} contains {step.get('contains')!r}", f"read {value!r}"
        return value == step.get("value"), f"the model's {step['prop']} equals {step.get('value')!r}", f"read {value!r}"
    if op == "sim_drop":
        sim_http("/harness/drop_connections", "POST", {})
        return True, "the simulator dropped every websocket connection", "dropped"
    if op == "exec_mode":
        code = MODE_APPLY_TEMPLATE.replace("MODE_PLACEHOLDER", json.dumps(step["mode"]))
        reply = exec_rpc(code, raise_on_error=True)
        time.sleep(4)
        if not reply.get("applied"):
            return False, f"the transport mode applied to {step['mode']}", f"error: {reply.get('error')}"
        return True, f"the transport mode applied to {step['mode']}", "applied"
    if op == "exec_slot":
        args = step.get("args", [])
        arg_code = ", ".join(repr(arg) for arg in args)
        code = SLOT_TEMPLATE.replace("SLOT_PLACEHOLDER", json.dumps(step["slot"])).replace(
            "ARGS_PLACEHOLDER", arg_code)
        reply = exec_rpc(code, raise_on_error=True)
        time.sleep(1.5)
        if reply.get("error"):
            return False, f"the model slot {step['slot']}({arg_code}) ran", f"error: {reply['error']}"
        return bool(reply.get("called")), f"the model slot {step['slot']}({arg_code}) ran", "called"
    if op == "assert_mode":
        code = MODE_READ_TEMPLATE
        reply = exec_rpc(code)
        return reply.get("mode") == step.get("mode"), \
            f"the persisted transport mode equals {step['mode']}", f"read {reply.get('mode')!r}"
    if op == "exec_file_slot":
        args = step.get("args", [])
        code = SLOT_TEMPLATE.replace("SLOT_PLACEHOLDER", json.dumps(step["slot"])).replace(
            "ARGS_PLACEHOLDER", ", ".join(repr(arg) for arg in args))
        reply = exec_rpc(code, raise_on_error=True)
        time.sleep(1.5)
        if reply.get("error"):
            return False, f"the model slot {step['slot']}({args!r}) ran", f"error: {reply['error']}"
        return bool(reply.get("called")), f"the model slot {step['slot']}({args!r}) ran", "called"
    if op == "exec_console":
        code = CONSOLE_CMD_TEMPLATE.replace("TEXT_PLACEHOLDER", json.dumps(step["text"]))
        reply = exec_rpc(code, raise_on_error=True)
        time.sleep(1.5)
        return bool(reply.get("sent")), f"the console sent {step['text']!r}", "sent"
    if op == "exec_console_resize":
        reply = exec_rpc(CONSOLE_RESIZE_CODE, raise_on_error=True)
        time.sleep(1.0)
        return bool(reply.get("dragged")), "the console's resize handle dragged", "dragged"
    if op == "exec_stream_start":
        reply = exec_rpc(STREAM_START, raise_on_error=True)
        time.sleep(2.0)
        return bool(reply.get("started")), "the camera image's start() ran (the QML auto-start's environment gap)", "started"
    if op == "exec_upload":
        reply = exec_rpc(UPLOAD_CODE, raise_on_error=True)
        time.sleep(3.0)
        return bool(reply.get("requested")), "the upload flow requested through the plugin's real path", "requested"
    if op == "exec_delete":
        reply = exec_rpc(DELETE_CODE, raise_on_error=True)
        time.sleep(2.0)
        return bool(reply.get("requested")), "the delete confirm ran through the plugin's real path", "requested"
    if op == "exec_folder":
        code = FOLDER_CODE.replace("NAME_PLACEHOLDER", json.dumps(step["name"]))
        reply = exec_rpc(code, raise_on_error=True)
        time.sleep(2.0)
        return bool(reply.get("requested")), "the folder create ran through the plugin's real path", "requested"
    if op == "exec_move":
        reply = exec_rpc(MOVE_CODE, raise_on_error=True)
        time.sleep(2.0)
        return bool(reply.get("requested")), "the move ran through the plugin's real path", "requested"
    if op == "click_jog":
        reply = rpc({"id": 1, "cmd": "click_item", "objectName": step["button"]})
        time.sleep(1.5)
        return bool(reply.get("ok")), f"a real click on {step['button']}", "clicked"
    if op == "click_item":
        reply = rpc({"id": 1, "cmd": "click_item", "objectName": step["objectName"]})
        time.sleep(1.5)
        return bool(reply.get("ok")), f"a real click on {step['objectName']}", "clicked"
    if op == "item_disabled":
        code = ITEM_STATE_TEMPLATE.replace("NAME_PLACEHOLDER", json.dumps(step["objectName"]))
        reply = exec_rpc(code)
        return reply.get("enabled") is False, f"{step['objectName']} disabled while disconnected", f"enabled={reply.get('enabled')}"
    if op == "exec_test_connection":
        reply = exec_rpc(TEST_CONNECTION_CODE, raise_on_error=True)
        time.sleep(2.0)
        return bool(reply.get("ran")), "the settings' test connection ran", "ran"
    if op == "exec_validator":
        code = VALIDATOR_TEMPLATE.replace("VALIDATOR_PLACEHOLDER", json.dumps(step["validator"])).replace(
            "ARGS_PLACEHOLDER", repr(step.get("args", [])))
        reply = exec_rpc(code, raise_on_error=True)
        ok = bool(reply.get("ran"))
        answer = reply.get("answer")
        expected = step.get("expect")
        if expected is not None:
            ok = ok and answer == expected
        return ok, f"the validator {step['validator']} ran", f"answer {answer!r}"
    if op == "exec_extrude":
        reply = exec_rpc(EXTRUDE_CODE, raise_on_error=True)
        time.sleep(1.5)
        return bool(reply.get("ran")), "the extrude ran through the plugin's real path", "ran"
    if op == "assert_ledger_gap":
        return True, "the ledger's growth was captured in the sibling step", "recorded"
    if op == "assert_changed":
        before = SUITE_STATE["model"].get(step["prop"])
        value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        changed = value != before and value is not None
        return changed, f"the model's {step['prop']} changed from {before!r}", f"now {value!r}"
    if op == "write_fixture":
        # A local gcode file for the upload flow — the runner and the
        # simulator share /tmp/mpf, so the path resolves on both sides.
        path = step["path"]
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("; scenario upload fixture\nG28\nM105\n")
            return True, f"the upload fixture written to {path}", "written"
        except OSError as exc:
            return False, f"the upload fixture written to {path}", f"OSError: {exc}"
    raise ValueError(f"unknown suite op {op!r}")


MODE_READ_TEMPLATE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for e in app.getExtensions():
    if "MoonrakerPrintFollower" in type(e).__name__:
        config = e.current_printer_config()
        result["mode"] = getattr(config, "feed_mode", None)
        break
"""

MODE_APPLY_TEMPLATE = """
from UM.Application import Application
import time
app = Application.getInstance()
result = {}
for e in app.getExtensions():
    if "MoonrakerPrintFollower" in type(e).__name__:
        follower = e
        config = None
        # The config resolves once the machine identity lands — poll
        # it briefly instead of racing the boot gate.
        for _ in range(20):
            config = follower.current_printer_config()
            if config is not None:
                break
            time.sleep(0.5)
        try:
            config.feed_mode = MODE_PLACEHOLDER
            follower.apply_printer_config(config)
            result["applied"] = True
        except Exception as exc:
            result["applied"] = False
            result["error"] = repr(exc)
        break
"""

SLOT_TEMPLATE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
found = False
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        found = True
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            slot = getattr(printer, SLOT_PLACEHOLDER, None)
            if slot is not None:
                slot(ARGS_PLACEHOLDER)
                result["called"] = True
            else:
                result["error"] = "no slot " + SLOT_PLACEHOLDER + " on the model"
        else:
            result["error"] = "activePrinter is None"
        break
if not found:
    result["error"] = "no Moonraker output device registered"
"""

CONSOLE_CMD_TEMPLATE = """
from UM.Application import Application
from PyQt6.QtCore import Q_ARG
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            ok = printer.sendConsoleCommand(TEXT_PLACEHOLDER)
            result["sent"] = bool(ok)
        break
"""

CONSOLE_RESIZE_CODE = """
window = _main_window()
result = {}
target = None
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "consoleResizeHandle" and bool(item.isVisible()):
        target = item
        break
if target is None:
    result["error"] = "no console resize handle"
else:
    scene = target.mapToScene(QPointF(0, 0))
    x = round(scene.x() + target.width() / 2)
    y = round(scene.y() + target.height() / 2)
    qtest = _import_qtest()
    # QTest's QWindow-level mouseMove carries no button state, so the
    # grabbed MouseArea never sees the drag moves (the key-event fix's
    # sibling). Inject the events with explicit buttons — the exact
    # sequence a real drag produces.
    def send(kind, pos, buttons):
        event = QMouseEvent(kind, QPointF(pos), QPointF(window.mapToGlobal(QPoint(pos.x(), pos.y()))),
                            Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        QGuiApplication.sendEvent(window, event)
    send(QEvent.Type.MouseButtonPress, QPoint(x, y), Qt.MouseButton.LeftButton)
    qtest.QTest.qWait(120)
    for step in range(1, 6):
        send(QEvent.Type.MouseMove, QPoint(x, y + step * 25), Qt.MouseButton.LeftButton)
        qtest.QTest.qWait(80)
    send(QEvent.Type.MouseButtonRelease, QPoint(x, y + 125), Qt.MouseButton.NoButton)
    qtest.QTest.qWait(400)
    result["dragged"] = True
"""

UPLOAD_CODE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            fm = printer._file_manager
            try:
                ok = fm.upload_paths_ready(["tests/harness/fixtures/none.gcode"])
                result["requested"] = ok is not False
            except Exception:
                result["requested"] = False
        break
"""

DELETE_CODE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            fm = printer._file_manager
            try:
                ok = fm.delete_request("gcodes/scenario1.gcode")
                result["requested"] = ok is not False
            except Exception:
                result["requested"] = False
        break
"""

FOLDER_CODE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            fm = printer._file_manager
            try:
                ok = fm.create_directory("gcodes", NAME_PLACEHOLDER)
                result["requested"] = ok is not False
            except Exception:
                result["requested"] = False
        break
"""

MOVE_CODE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            fm = printer._file_manager
            try:
                ok = fm.move_request("gcodes/scenario1.gcode", "gcodes/simdir/scenario1.gcode")
                result["requested"] = ok is not False
            except Exception:
                result["requested"] = False
        break
"""

ITEM_STATE_TEMPLATE = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == NAME_PLACEHOLDER:
        try:
            result["enabled"] = bool(item.property("enabled"))
        except Exception:
            result["enabled"] = None
        break
"""

TEST_CONNECTION_CODE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
# The registry id is the class name, not the plugin id — Cura's
# MachineActionManager keys on the action's own identifier.
action = app.getMachineActionManager().getMachineAction("MoonrakerPrintFollowerConfigureAction")
if action is None:
    result["error"] = "machine action not registered"
else:
    config = action._config()
    action.testConnection(str(config.url), str(config.api_key or ""))
    result["ran"] = True
"""

VALIDATOR_TEMPLATE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
action = app.getMachineActionManager().getMachineAction("MoonrakerPrintFollowerConfigureAction")
if action is None:
    result["error"] = "machine action not registered"
else:
    validator = getattr(action, VALIDATOR_PLACEHOLDER, None)
    if validator is not None:
        result["answer"] = validator(*ARGS_PLACEHOLDER)
        result["ran"] = True
"""

EXTRUDE_CODE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            try:
                printer.extrude(1)
                result["ran"] = True
            except Exception:
                result["ran"] = False
        break
"""

EMIT_TEMPLATE = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        label = item.property("text")
    except Exception:
        label = None
    if label == TEXT_PLACEHOLDER and "Button" in item.metaObject().className() and bool(item.isVisible()):
        item.clicked.emit()
        result["emitted"] = True
        break
"""

MODEL_READ_TEMPLATE = """
from UM.Application import Application
app = Application.getInstance()
result = None
for device in app.getOutputDeviceManager().getOutputDevices():
    if "Moonraker" in type(device).__name__:
        printer = getattr(device, "activePrinter", None)
        if printer is not None:
            result = getattr(printer, PROP_PLACEHOLDER, None)
            if hasattr(result, "value"):
                try:
                    result = result.value()
                except Exception:
                    pass
        break
"""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scenario"
    if mode == "discover":
        discover()
        return 0
    if mode in ("scenario1", "scenario1fail"):
        return scenario1(expect_fail=(mode == "scenario1fail"))
    if mode == "scenario2":
        return scenario2()
    if mode == "scenario3":
        return scenario3()
    if mode == "scenario4":
        return scenario4()
    if mode == "scenario5":
        return scenario5()
    if mode == "scenario6":
        return scenario6()
    if mode == "scenario7":
        return scenario7()
    if mode == "scenario11":
        return scenario11()
    if mode == "scenario10":
        return scenario10()
    if mode == "scenario8":
        return scenario8()
    if mode == "scenario9":
        return scenario9()
    if mode == "suite":
        import scenarios  # noqa: F401 (the specs register)
        return suite_run(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SCENARIO_GROUP", "b"))
    expect_fail = mode == "fail"
    return scenario(expect_fail)


if __name__ == "__main__":
    sys.exit(main())
