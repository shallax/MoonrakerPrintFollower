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


def click_stage(stage_id, attempts=10, settle_s=2.0):
    """QTest-click Cura's own stage-header button via the driver. The
    header buttons render a little after the controller reports the
    stage, so retry while the UI catches up; a boot whose buttons
    never appear fails loudly (the flake policy declares it bad)."""
    last = None
    for _ in range(attempts):
        last = rpc({"id": 1, "cmd": "qclick", "stage": stage_id}, timeout=30)
        if last.get("ok") is True:
            return last
        time.sleep(settle_s)
    return last


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
            return True
        rpc({"id": 1, "cmd": "seed_machine"})
        rpc({"id": 1, "cmd": "hide_welcome"})
        time.sleep(2)
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
    write_gallery(steps, expect_fail)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def write_gallery(steps, expect_fail, title="Phase A — real Cura under Xvfb, QTest clicks on Cura's own stage buttons"):
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
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Phase A run</title>
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


def exec_rpc(code, timeout=60.0):
    reply = rpc({"id": 1, "cmd": "exec", "code": code}, timeout=timeout)
    if not reply.get("ok"):
        return {}
    try:
        return json.loads(reply.get("result") or "{}")
    except (TypeError, ValueError):
        return {}


# The exec snippets: scenario 1 reads the real model and the real UI.
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


def wait_for(check, budget_s, tick_s=2.0):
    deadline = time.time() + budget_s
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(tick_s)
    return check()

def scenario1(expect_fail=False):
    # Tier-1 #1: the failure state clears itself. A cold start raises
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
        rpc({"id": 1, "cmd": "qclick", "stage": "MonitorStage"})
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
    title = "Tier-1 #1 — the failure state clears itself"
    write_gallery(steps, expect_fail, title)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scenario"
    if mode == "discover":
        discover()
        return 0
    if mode in ("scenario1", "scenario1fail"):
        return scenario1(expect_fail=(mode == "scenario1fail"))
    expect_fail = mode == "fail"
    return scenario(expect_fail)


if __name__ == "__main__":
    sys.exit(main())
