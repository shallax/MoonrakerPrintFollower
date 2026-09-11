#!/usr/bin/env python3
"""Phase-A runner: real Cura under Xvfb, real XTEST clicks, real captures.

Runs INSIDE the harness container (it must reach the driver's loopback
port). Input goes through xdotool (XTEST); stills and video come from
ffmpeg reading the X display. The driver only observes.
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


def click(x, y):
    subprocess.run(["xdotool", "mousemove", str(x), str(y)], check=False, timeout=10)
    time.sleep(0.15)
    subprocess.run(["xdotool", "click", "1"], check=False, timeout=10)


def wait_stage(wanted, timeout_ms=20000):
    return rpc({"id": 1, "cmd": "wait_stage", "stage": wanted, "timeout_ms": timeout_ms}, timeout=timeout_ms / 1000.0 + 5)


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


def scenario(coords, expect_fail=False):
    os.makedirs(RUN_DIR, exist_ok=True)
    video = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab", "-video_size", SIZE,
         "-framerate", "15", "-i", DISPLAY, os.path.join(RUN_DIR, "scenario.mp4")])
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(("00-boot", f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}",
                      "hello succeeds", True, shot("00-boot")))
        for name, coord, want, _label in coords:
            click(*coord)
            result = wait_stage(want)
            ok = result.get("ok") is True
            expected = False if expect_fail and name == "monitor" else True
            steps.append((name, f"XTEST click at {coord} -> expected stage {want}",
                          f"stage == {want}", ok == expected, shot(name)))
        if expect_fail:
            steps.append(("deliberate-failure", "assert a false condition",
                          "stage == 'NopeStage'", False, shot("deliberate-failure")))
        else:
            steps.append(("final", "all stage transitions reached",
                          "PREPARE -> PREVIEW -> MONITOR", True, shot("final")))
    finally:
        time.sleep(1)
        video.terminate()
    write_gallery(steps, expect_fail)
    print(f"gallery: {RUN_DIR}/index.html")
    return 0 if all(step[3] for step in steps) else 1


def write_gallery(steps, expect_fail):
    rows = []
    for name, action, assertion, ok, path in steps:
        verdict = "PASS" if ok else ("EXPECTED FAIL" if expect_fail and not ok else "FAIL")
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
<body><h1>Phase A — real Cura under Xvfb, XTEST clicks</h1>
<video src="scenario.mp4" controls style="max-width:100%"></video>
{body}</body></html>"""
    with open(os.path.join(RUN_DIR, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(page)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "scenario"
    if mode == "discover":
        discover()
        return 0
    coords = json.load(open(os.environ.get("HARNESS_COORDS", "/tmp/mpf/harness_coords.json"), encoding="utf-8"))
    expect_fail = mode == "fail"
    return scenario(coords, expect_fail)


if __name__ == "__main__":
    sys.exit(main())
