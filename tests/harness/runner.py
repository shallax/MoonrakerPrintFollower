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
import shutil
import socket
import subprocess
import sys

# Every verdict line carries a tick or a cross, and CI's Windows runners
# give Python a cp1252 stdout: printing one raised UnicodeEncodeError and
# took the whole leg down (measured - the Windows suite failed on
# "PASSED \u2705" itself, not on anything it was reporting). Ask for
# UTF-8 once, here, rather than avoiding the characters.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import time

# The platform dispatch lives beside this file (the container staging
# copies it next to the runner) and is the ONE place a host platform is
# chosen.
import native_host

DISPLAY = os.environ.get("HARNESS_DISPLAY", ":99")
# One display geometry (the round-2 contract): the screen SIZE, the
# capture size and the window pin resolve from the environment, set
# by ui_test.sh. The window pins SMALLER than the screen — headroom,
# so the screen-fits assertion has something to defend.
SIZE = os.environ.get("HARNESS_GEOMETRY", "1920x1080")
WINDOW_SIZE = os.environ.get("HARNESS_WINDOW", "1840x1040")
RUN_DIR = os.environ.get("HARNESS_RUN_DIR", "/tmp/mpf/ui-artifacts/run-" + time.strftime("%Y-%m-%d-%H%M%S"))
# The RPC rendezvous: the driver, inside Cura, writes the port and the
# per-run token here and the runner reads them back. ONE variable —
# the two sides then cannot disagree about the path — and the default
# keeps the Linux container (whose work dir IS /tmp/mpf) unchanged.
RPC_DIR = os.environ.get("HARNESS_RPC_DIR", "/tmp/mpf")
PORT_FILE = os.path.join(RPC_DIR, "harness_port.txt")
TOKEN_FILE = os.path.join(RPC_DIR, "harness_token.txt")
# The scratch Cura and the runner both read. The container's work dir IS
# /tmp/mpf, so one literal served both sides of its mount; on the natives
# it serves neither — there is no /tmp/mpf, and Windows resolves a
# leading-slash path against the CALLER's drive, so the driver (Cura's
# drive) and the runner (the checkout's) opened different files under the
# same string. The native harness points this at its own work dir.
SCRATCH_DIR = os.environ.get("HARNESS_SCRATCH_DIR", "/tmp/mpf")
DRIVER_HOST = "127.0.0.1"
SYSTEM = native_host.current_system()


def progress(message):
    """One line of stage progress, flushed.

    A leg that prints nothing until it ends is unreadable while it runs,
    and worse: a leg killed at its timeout dies with whatever is still
    sitting in a block buffer, so the log shows nothing of how far it
    got. flush=True rather than a `-u` on every caller, because the
    container leg, the native legs and a local run all reach this file
    by different argv."""
    print(f"ui_test: {message}", flush=True)


def still_argv(path):
    """One frame's ffmpeg argv — the capture helper's platform choice.
    The natives record the display they are given, so the display must
    already be at HARNESS_GEOMETRY or the frame fails shot()'s own
    size check, which is the honest reading of a frame that is not the
    one the run asked for."""
    return native_host.still_argv(SYSTEM, path, size=SIZE, display=DISPLAY,
                                  screen_index=native_host.screen_index())


def record_argv(path, framerate=15):
    """A recording's ffmpeg argv: the platform's input device, and the
    natives' fragmented output flags — a native recorder is stopped by
    a kill, and a plain mp4 would lose the index that makes it
    playable."""
    return native_host.recorder_argv(SYSTEM, path, size=SIZE, display=DISPLAY,
                                     screen_index=native_host.screen_index(),
                                     framerate=framerate)


# The capture gate. A leg whose screen cannot be relied on to present
# runs its steps with no recorder and no stills: every step assertion
# is answered in-process from the live QML tree and the models, so the
# pictures are the whole of what is lost — the static verdict and the
# display guard read them, and nothing else does. The renderer-liveness
# verdict is deliberately NOT part of this gate: it reads the app's own
# frame count, has nothing to do with the screen or the recorder, and
# is the one measurement a picture-less leg must keep, because a
# responsive QML tree is not proof of rendering.
#
# The LEG says so, not the platform: tools/native_harness.sh exports
# HARNESS_CAPTURE=off with its reason for the CI mac, whose runner has
# no GPU (OpenGL in a macOS guest is software by construction — Apple's
# paravirtual GPU is Metal-only) and on which the window stops
# presenting partway through a leg. Measured on
# gate-group-connection-macos-latest: the stills go byte-identical
# while the menu bar clock and the dock keep ticking in the same
# frames, and a real click on Cura's own MonitorStage header (`a9-07`)
# changes nothing on screen — while all 45 steps pass, because they
# read the tree. A Mac with a real GPU presents normally, so this is
# deliberately not a platform default: a local run keeps its pictures,
# and HARNESS_CAPTURE=on restores them on the CI mac for a look.
# The recorder's own origin: every step records its offset from this,
# which is what aligns a step to the second of the recording it ran in.
# None when nothing is being recorded.
CAPTURE_T0 = None


def capture_enabled(override):
    """The gate's decision: off only when the leg said off. Anything
    else — unset, empty, "on", a typo — captures, so a missing or
    mangled variable fails toward the recording rather than away from
    it."""
    return (override or "").strip().lower() != "off"


CAPTURE = capture_enabled(os.environ.get("HARNESS_CAPTURE"))
CAPTURE_REASON = os.environ.get("HARNESS_CAPTURE_REASON") or (
    "" if CAPTURE else "this leg was told to capture nothing (HARNESS_CAPTURE=off) "
                       "and gave no reason — see TESTING.md on the capture gate")


def start_recorder(path, framerate=15):
    """The platform recorder, or None when this leg captures nothing."""
    global CAPTURE_T0
    if not CAPTURE:
        return None
    CAPTURE_T0 = time.monotonic()
    return subprocess.Popen(record_argv(path, framerate=framerate))


def stop_recorder(video):
    """Stop a recorder whatever start_recorder returned."""
    if video is None:
        return
    time.sleep(1)
    video.terminate()
    try:
        video.wait(timeout=10)
    except subprocess.TimeoutExpired:
        video.kill()


def harvest_cura_log(dest_dir, suffix=""):
    """Cura's own log, kept beside the evidence.

    The container leg harvests this from the seeded XDG roots; a native
    leg has no ui_test.sh behind it, so its setup script hands the
    config directory over as HARNESS_CURA_CONFIG and the copy happens
    here. It is the file that says why a driver never answered, and it
    is deliberately taken more than once on the two-boot legs: the
    relaunch truncates cura.log, so boot 1's log has to be lifted
    before the second launch starts.
    """
    src = os.environ.get("HARNESS_CURA_CONFIG", "")
    if not src or not os.path.isdir(src) or not os.path.isdir(dest_dir):
        return 0
    kept = 0
    for name in sorted(os.listdir(src)):
        if not name.startswith("cura.log"):
            continue
        try:
            shutil.copyfile(os.path.join(src, name),
                            os.path.join(dest_dir, name + suffix))
        except OSError:
            continue
        kept += 1
    if kept:
        print(f"ui_test: kept {kept} Cura log file(s) in {dest_dir}")
    return kept


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
            # Present the per-run token on every request: the driver
            # refuses anything else (the security panel's H1 — an
            # unauthenticated exec channel in Cura's process).
            token = ""
            try:
                with open(TOKEN_FILE, encoding="utf-8") as handle:
                    token = handle.read().strip()
            except OSError:
                pass
            request = dict(request)
            request["token"] = token
            with socket.create_connection((DRIVER_HOST, port), timeout=timeout) as sock:
                sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
                sock.settimeout(timeout)
                line = b""
                while b"\n" not in line:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    line += chunk
                # A reply can arrive coalesced with another write from
                # the driver; take the first object and discard the
                # tail (json.loads would raise "Extra data").
                line = line.split(b"\n", 1)[0]
                return json.loads(line.decode("utf-8"))
        except (OSError, ValueError) as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"driver RPC failed: {last_error}")


def _png_size(path):
    """The PNG header's declared dimensions, or None when the file is
    not a readable PNG. A capture must BE what shot() asked for — a
    truncated or mis-sized frame passes the old 100-byte check."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return (int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big"))


def shot(name):
    """One frame into the run dir. A capture that did not happen must
    read as a failure — a silently missing image would otherwise fall
    back to a stale frame with the same name (the panel's finding).
    The frame must also match the declared SIZE: a clamped or
    truncated grab still writes a plausible file, and every
    evidence claim downstream rests on these pixels."""
    if not CAPTURE:
        # No frame, and deliberately no error: a leg that captures
        # nothing must not fail every step for the absence of the
        # picture it was told not to take.
        return (None, None)
    path = os.path.join(RUN_DIR, f"{name}.png")
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    try:
        subprocess.run(still_argv(path), check=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as exc:
        return (path, f"capture failed: {exc!r}"[:80])
    if not os.path.exists(path) or os.path.getsize(path) < 100:
        return (path, "capture failed: empty frame")
    expected = tuple(int(part) for part in SIZE.split("x"))
    if _png_size(path) != expected:
        return (path, f"capture failed: frame is {_png_size(path)}, expected {expected}")
    # The highlight-on-capture rule: the harness composites an outline
    # onto the frame for the element the step resolved — the evidence
    # geometry, never Cura QML.
    if _FRAME_OUTLINE[0] is not None:
        _outline(path, _FRAME_OUTLINE[0])
    return (path, None)


def _outline(path, geometry):
    """Draw the step's evidence geometry onto the captured frame (a
    harness-side overlay — the product QML never draws it)."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return
    try:
        x, y, w, h = (round(part) for part in geometry)
        with Image.open(path) as image:
            draw = ImageDraw.Draw(image)
            for offset in (0, 1, 2):
                draw.rectangle(
                    [x - offset, y - offset, x + w + offset, y + h + offset],
                    outline="#ffd11f", width=1)
            image.save(path)
    except Exception:
        pass


# The geometry the NEXT shot outlines (set by the step executor from
# the evidence entry's geometry — cleared by the executor after use).
_FRAME_OUTLINE = [None]


def wait_stage(wanted, timeout_ms=20000):
    return rpc({"id": 1, "cmd": "wait_stage", "stage": wanted, "timeout_ms": timeout_ms}, timeout=timeout_ms / 1000.0 + 5)


def ensure_ready(require_aux=True):
    """The boot gate: on a fresh seeded profile Cura's one-shot
    welcome check can run before the saved machine is restored, and
    the dialog's grey-out then eats every click. Seed the machine
    (the Add-printer wizard's own code path) and hide the welcome
    overlay until the gate is clear. Orchestration only — no
    plugin-surface claims. The first-install leg passes
    require_aux=False: a clean install has no printer to connect to,
    so the discovery chain cannot come alive — that absence is the
    state under test, not a bad boot."""
    for _ in range(10):
        reply = rpc({"id": 1, "cmd": "welcome"})
        if reply.get("ok") and not reply.get("up"):
            break
        rpc({"id": 1, "cmd": "seed_machine"})
        rpc({"id": 1, "cmd": "hide_welcome"})
        # The g-code-suitability message: dismissed conditionally at
        # boot, never waited for — a flow that loads no gcode never
        # spends time on it.
        rpc({"id": 1, "cmd": "hide_gcode_warning"})
        # The version-update toast (5.11/5.12's MessageStack puts it
        # over the console's buttons): same conditional dismissal.
        rpc({"id": 1, "cmd": "hide_update_toast"})
        time.sleep(2)
    # Cura's first-boot window size is nondeterministic, and a narrow
    # window collapses the header's stage buttons into the overflow
    # menu — the stage clicks then fail ("stage button not found")
    # while a wide window passes. Pin the geometry after the welcome
    # settles so every run has the same layout; the probes measure
    # the live tree either way.
    _want = [int(part) for part in WINDOW_SIZE.split("x")]
    _screen = [int(part) for part in SIZE.split("x")]
    try:
        _pin = rpc({"id": 1, "cmd": "window_pin", "w": _want[0], "h": _want[1]}, timeout=70)
        # The boot gate fails when the window cannot render on the
        # screen (the pin's own report must match BOTH) — a window
        # larger than the screen used to pass by self-report alone.
        _pin_ok = bool(_pin.get("ok")
                       and list(_pin.get("size") or ()) == _want
                       and list(_pin.get("screen") or ()) == _screen)
    except Exception:
        _pin_ok = False
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
    if not require_aux:
        return _pin_ok
    # The aux gate: the discovery chain (object list -> aux
    # subscription -> temperatures/webcams) can stay dead on a boot
    # that has the model — the flake policy declares that boot bad
    # rather than letting its scenarios fail for the wrong reason.
    # The re-emit's refresh re-fires the discovery, which heals most.
    for _ in range(4):
        if exec_rpc(AUX_READY) is True:
            return _pin_ok
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
    video_path = os.path.join(RUN_DIR, "scenario.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    write_gallery(steps, expect_fail, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


# A leg whose screen does not move is not a success (the static-green
# ruling): the recording IS the evidence, so a leg that shows the same
# picture for most of its length cannot have exercised anything,
# whatever its steps reported. The thresholds are measured, not
# chosen: across the 51 galleries the longest static span on a leg
# that is not one of the frozen macOS ones is 59 s, and every
# legitimate idle (a step waiting on a model, a budget of 15-30 s) sits
# well under that, so the absolute floor is 60 s; the share is what
# separates a leg that froze (39-92% of its duration) from one that
# spent a single step waiting.
STATIC_LEG_SECONDS = 60.0
STATIC_LEG_SHARE = 0.35
# The encoder's own dither moves a frozen frame by about one grey
# level (measured on the galleries: 254 of 393 frozen consecutive
# pairs differ by at most 1), so an exact-hash comparison reads a 394 s
# frozen stretch as 3 s — two orders of magnitude wrong. Frames are
# compared as 64x36 grey means, and a pair counts as unchanged within
# one level.
STATIC_FRAME_MAD = 1.0
STATIC_SAMPLE = (64, 36)
# The room a driven span needs for its response to be captured before
# the span can be called still anyway: the decode samples at one frame
# per second, and a step's offset from the recorder's start carries
# about a second of ffmpeg startup error, so two seconds covers both.
STATIC_DRIVEN_RESPONSE_S = 2.0


def frame_mad(before, after):
    """Mean absolute difference between two grey frames (0-255)."""
    if not before or len(before) != len(after):
        return 255.0
    return sum(abs(a - b) for a, b in zip(before, after, strict=True)) / float(len(before))


def static_run(frames):
    """The longest near-identical run: (start, length) in frames."""
    best = (0, 1) if frames else (0, 0)
    start = 0
    length = 1
    for index in range(1, len(frames)):
        if frame_mad(frames[index - 1], frames[index]) <= STATIC_FRAME_MAD:
            length += 1
        else:
            start, length = index, 1
        if length > best[1]:
            best = (start, length)
    return best


def static_verdict(frames, seconds=STATIC_LEG_SECONDS, share=STATIC_LEG_SHARE,
                   interactions=None):
    """Whether a leg's frame sequence is too static to be a success.

    Returns None when there is nothing to judge (fewer than two
    frames), else a dict with the span, its share of the leg and the
    verdict. One frame per second, so a length IS a duration.

    A span nobody DROVE is not judged (the non-interactive ruling). The
    rule exists to catch a window that stopped presenting while it was
    being driven; a stretch whose steps only pushed simulator state and
    read models asked nothing of the screen, so its stillness says
    nothing about the screen. gate-group-status is the measured case:
    its first 34 steps contain no input at all, the recording is
    correctly still across them, and the leg went red on Windows at
    62 s where ubuntu ran the same leg at 59 s — a one-second margin
    deciding a verdict is the tell that the span was not the thing
    being measured.

    "Drove" means an input landed INSIDE the run with room to spare,
    not that one clipped its edge: a click in the last moments of a run
    is the event that ENDED the stillness — the run is still, and
    correctly so, right up to it. Measured on ubuntu's group-status:
    the run covers seconds 7..67 and the first click is at 68.4, so a
    rule reading the input's trailing edge (as an earlier version of
    this did) counted the very click that broke the stillness as proof
    the stillness was fine. STATIC_DRIVEN_RESPONSE_S is the room a
    response needs to be captured: the decode samples at one frame per
    second and the step's offset carries about a second of startup
    error, so two seconds covers both.

    `interactions` is the list of seconds at which the leg sent real
    input; None means the alignment is unknown, and every span is
    judged as before — a leg that cannot be aligned keeps the old teeth
    rather than silently losing them."""
    if len(frames) < 2:
        return None
    start, length = static_run(frames)
    covered = length / float(len(frames))
    last = start + length - 1
    driven = interactions is None or any(
        start <= at <= last - STATIC_DRIVEN_RESPONSE_S for at in interactions)
    ok = not (length >= seconds and covered >= share) or not driven
    return {"ok": ok, "start_s": start, "span_s": length,
            "share": round(covered, 3), "frames": len(frames),
            "driven": driven}


def _interaction_seconds(run_dir):
    """The seconds at which the recording was driven, from the sibling
    evidence.json: when each real-input step STARTED, on the recorder's
    own clock. None when the evidence cannot place the steps (no
    `at_s`), which keeps the old everything-is-judged behaviour rather
    than silently retiring the check; a list — possibly empty, for a leg
    that clicked nothing — when it can.

    The step's START is the whole of what this needs: whether the
    screen answered an input is a question about the run AFTER it, so
    the input's own duration says nothing, and carrying it in only ever
    stretched a late input back over a stillness it did not break."""
    path = os.path.join(run_dir, "evidence.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    steps = data.get("steps") or []
    if not any(step.get("at_s") is not None for step in steps):
        return None
    return [step["at_s"] for step in steps
            if step.get("class") == "ui-interaction" and step.get("at_s") is not None]


def static_leg_check(video_path, interactions=None):
    """The leg-level static check: decode the recording at one frame
    per second and fail the leg when one near-identical run covers
    most of it. The recorder itself is ffmpeg, so the decoder is
    present wherever a recording exists; a leg with no analysable
    recording is recorded, never failed (there is nothing to read)."""
    if not video_path or not os.path.exists(video_path):
        return None
    argv = ["ffmpeg", "-v", "error", "-i", video_path,
            "-vf", f"fps=1,scale={STATIC_SAMPLE[0]}:{STATIC_SAMPLE[1]},format=gray",
            "-f", "rawvideo", "-"]
    try:
        done = subprocess.run(argv, capture_output=True, check=False)
    except OSError:
        return None
    size = STATIC_SAMPLE[0] * STATIC_SAMPLE[1]
    raw = done.stdout
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    return static_verdict(frames, interactions=interactions)


def _merge_static_into_evidence(run_dir, records):
    """Put the leg's static verdict in the machine-readable record as
    well as the gallery: the artifact audit reads evidence.json."""
    path = os.path.join(run_dir, "evidence.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    data["static_leg"] = records
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def static_leg_report(run_dir):
    """Every recording a leg leaves, judged (the static-green ruling).

    The leg fails when one near-identical run covers most of a
    recording, whatever its steps reported. It runs in the runner's own
    exit path, so it covers every mode: the suite groups, the
    boot-only first-install and migration legs, and their second boots.
    A recording that cannot be decoded is left unjudged rather than
    called static. A leg that captures nothing is not judged at all:
    the static rule reads pictures, and there are none to read."""
    if not CAPTURE:
        print("ui_test: STATIC LEG — not judged: this leg captures nothing "
              f"({CAPTURE_REASON or 'no reason recorded'})")
        return 0
    records = []
    for root, _dirs, names in os.walk(run_dir):
        for name in sorted(names):
            if not name.endswith(".mp4"):
                continue
            path = os.path.join(root, name)
            # A recording is judged against the steps that ran beside
            # it: the second boot's recording must not be read against
            # the first boot's interactions.
            interactions = _interaction_seconds(root)
            verdict = static_leg_check(path, interactions=interactions)
            if verdict is None:
                continue
            verdict["file"] = os.path.relpath(path, run_dir)
            records.append(verdict)
            if not verdict["ok"]:
                print(f"ui_test: STATIC LEG — {verdict['file']}: the screen was still "
                      f"for {verdict['span_s']}s of its {verdict['frames']}s "
                      f"({int(verdict['share'] * 100)}%)")
            elif not verdict.get("driven", True):
                # Named, never silent: a span nobody drove is not a pass
                # on the screen, it is a stretch where the screen was
                # not asked anything.
                print(f"ui_test: STATIC LEG — {verdict['file']}: not judged, "
                      f"no input step fell inside its {verdict['span_s']}s still "
                      f"stretch (of {verdict['frames']}s)")
    if not records:
        return 0
    with open(os.path.join(run_dir, "static_leg.json"), "w", encoding="utf-8") as handle:
        json.dump(records, handle, indent=2)
        handle.write("\n")
    _merge_static_into_evidence(run_dir, records)
    return 1 if any(not record["ok"] for record in records) else 0


def _verdict(steps):
    """The suite verdict: failures print to stdout too, so a cell's
    job log names the failing steps even when the gallery upload dies
    (the two flaky groups once failed with no reachable evidence). A
    capture that refused to land (shot()'s dimension check) is a
    failure too — a PASS whose galleries are all broken is not
    evidence."""
    failed = [name for name, _, _, ok, cap in steps
              if not ok or (isinstance(cap, tuple) and cap[1])]
    if failed:
        print(f"ui_test: FAILED \u274c {len(failed)} of {len(steps)} steps failed: "
              f"{', '.join(failed)}")
    else:
        print(f"ui_test: PASSED \u2705 all {len(steps)} steps passed")
    return 0 if not failed else 1


# The per-run evidence record (the spine): every step leaves a
# machine-readable entry — op, target spec, verdict, capture and
# wall-clock duration — so the coverage execution check, the F08
# classification and the budget record all read one artifact instead
# of three. The delivery fields (resolved item, accepted event) are
# filled by the driver machinery as it lands; schema 1 ships with
# them null rather than absent.
EVIDENCE = []

# The presentation record (the static-green ruling): a scenario's frame
# counts at its start and its end. Every step reads the QML tree, so an
# app whose window stopped painting still passes every assertion over a
# still screen — this is the measurement that says whether the window
# was painting at all, and it separates a stalled scene graph from a
# stalled capture.
FRAME_PROBES = []

# The sample's own vocabulary. Only a stall is a claim about the app:
# everything the probe could not tell apart from a stall is named as
# unverified with the reason, because a measurement that could not be
# taken must not read as one that was.
LIVENESS_RENDERED = "rendered"
LIVENESS_STALLED = "stalled"
LIVENESS_UNVERIFIED = "unverified"


def frames_probe(phase, scenario_id):
    """One frame-count sample, recorded.

    Every sample asks for a frame explicitly: the driver requests a
    render and lets the event loop deliver it before it reads the
    count, so the difference the verdict reads is frames the app chose
    to deliver after being asked, not frames that happened to land."""
    sample = {"scenario": scenario_id, "phase": phase}
    try:
        reply = rpc({"id": 1, "cmd": "frames", "kick": True, "settle_ms": 300},
                    timeout=30)
    except Exception as exc:
        reply = {"ok": False, "error": repr(exc)}
    for key in ("swapped", "gained", "exposed", "visible", "active",
                "visibility", "state", "platform", "error", "kicked",
                "settle_ms", "frame_signal"):
        if key in reply:
            sample[key] = reply[key]
    sample["ok"] = bool(reply.get("ok"))
    FRAME_PROBES.append(sample)
    return sample


def _count(value):
    """A frame count, or None when the sample did not carry one: an
    absent count must never be read as zero, which is a stall."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _delivered(sample):
    """Whether the render request this sample made brought a frame
    back: the count it gained during its own settle."""
    return (_count(sample.get("gained")) or 0) > 0


def _saw_frames(sample):
    """Whether this sample knows of a frame at all — the cumulative
    count moved, now or earlier in the run. This is what separates a
    renderer that stopped from one that was never seen to paint."""
    return (_count(sample.get("swapped")) or 0) > 0 or _delivered(sample)


def _shown(sample):
    """Whether the display can be expected to show this window: an
    explicit yes on both flags. A hidden or minimised window delivers
    no frame by design, so it must not be read as a freeze, and a flag
    that did not arrive must not be read as either."""
    return sample.get("visible") is True and sample.get("exposed") is True


def _shape(sample):
    # What the sample saw, for a diagnostic that names the window it
    # judged rather than only the count.
    return (f"swapped={sample.get('swapped')} gained={sample.get('gained')} "
            f"visible={sample.get('visible')} exposed={sample.get('exposed')} "
            f"active={sample.get('active')} visibility={sample.get('visibility')} "
            f"state={sample.get('state')} platform={sample.get('platform')}")


def liveness_of(sample, seen):
    """One sample's outcome and the reason for it.

    `seen` says whether any EARLIER sample in this run saw a frame, and
    it is what separates a freeze from a platform whose window never
    painted: a signal never observed working cannot be read as one that
    stopped, so a zero before the first frame is its own outcome rather
    than a stall."""
    if sample.get("frame_signal") is False:
        return (LIVENESS_UNVERIFIED,
                "the frame signal could not be attached to the window")
    if not sample.get("ok"):
        detail = sample.get("error")
        return (LIVENESS_UNVERIFIED, "the window did not answer the probe"
                + (f": {detail}" if detail else ""))
    if _count(sample.get("swapped")) is None:
        return (LIVENESS_UNVERIFIED,
                "the probe answered without a frame count, so it measured nothing")
    if sample.get("kicked") is False or sample.get("settle_ms") == 0:
        # The count is only a verdict about the renderer if a render was
        # asked for and the event loop was given room to deliver it.
        return (LIVENESS_UNVERIFIED,
                "the count was read without a render request and a settle, "
                "so a zero in it says nothing about the renderer")
    if _delivered(sample):
        return (LIVENESS_RENDERED,
                f"the window delivered {sample.get('gained')} frame(s) after the "
                f"render request ({_shape(sample)})")
    if not _shown(sample):
        return (LIVENESS_UNVERIFIED,
                "the window is not on the display, so no frame is due from it "
                f"({_shape(sample)})")
    if seen:
        return (LIVENESS_STALLED,
                "the window is visible and exposed and delivered no frame after "
                "the render request, having delivered frames earlier in this run: "
                f"{_shape(sample)}")
    return (LIVENESS_UNVERIFIED,
            "no frame has been delivered on this platform in this run yet, so an "
            "initialising renderer and a platform that never emits the frame "
            f"signal look the same here ({_shape(sample)})")


def liveness_outcome(samples):
    """Every scenario's presentation outcome, in scenario order.

    The verdict reads the END sample — the one taken after the
    scenario's own steps — and only a visible, exposed window that
    delivered no frame after a render request, having delivered frames
    earlier in the run, counts as a stall. Everything the probe cannot
    tell apart from that is its own outcome: a hidden or minimised
    window, a renderer that has not painted yet, an unanswered probe
    and a platform that never emits the signal are named as unverified
    with their reason, so neither a freeze nor a missing measurement
    can pass as the other. Capture plays no part in it — the frame
    count is read from the app, and the leg's pictures are not
    evidence in it."""
    phases = {}
    for index, sample in enumerate(samples):
        phases.setdefault(sample.get("scenario"), {})[sample.get("phase")] = index
    records = []
    for scenario_id in sorted(phases, key=str):
        start_index = phases[scenario_id].get("start")
        end_index = phases[scenario_id].get("end")
        if start_index is None or end_index is None:
            # Both ends are what make the reading a comparison rather
            # than a threshold; a scenario with one sample is not
            # judged (the boot-only legs record none at all).
            continue
        seen = any(_saw_frames(sample) for sample in samples[:end_index])
        outcome, reason = liveness_of(samples[end_index], seen)
        records.append({"scenario": scenario_id, "outcome": outcome,
                        "reason": reason, "start": samples[start_index],
                        "end": samples[end_index]})
    return records


def stalled_scenarios(records):
    """The scenarios whose renderer stopped presenting: the ones that
    fail, whatever the leg captured."""
    return [record["scenario"] for record in records
            if record["outcome"] == LIVENESS_STALLED]

# F08's evidence classification: a step's class derives from its
# MECHANISM, never from a declared label — the scenario's claim is
# the minimum class over its steps (one real click plus six slot
# calls is application integration, not UI interaction). The
# ratcheting pin in test_harness_runner.py holds the census: real
# input can only grow, direct invocation can only shrink.
CLASS_ORDER = {"diagnostic-probe": 0, "application-integration": 1,
               "ui-interaction": 2}

DIRECT_INVOCATION_OPS = frozenset((
    "exec_slot", "exec_file_slot", "emit_click", "click_jog",
    "exec_mode", "exec_validator", "exec_console",
    "exec_extrude", "exec_test_connection", "exec_code"))

REAL_INPUT_OPS = frozenset(("deliver_click", "click_stage", "click_text",
                            "key_press"))


def _delivery_name(identified):
    # A readable name for a delivery record's hit/grabber: the
    # objectName when the item carries one, else the class chain's
    # first entry.
    if not isinstance(identified, dict):
        return identified
    if identified.get("objectName"):
        return f"{identified['objectName']} ({identified.get('class')})"
    return identified.get("class") or "no item"


def _classify(op, delivery):
    if op in REAL_INPUT_OPS:
        # Real input; the delivery record decides. click_stage's
        # acceptance is the stage transition itself (the effect);
        # key_press's is the driver's sent flag.
        if isinstance(delivery, dict) and (
                delivery.get("accepted")
                or (op == "key_press" and delivery.get("sent"))):
            return "ui-interaction"
        return "application-integration"
    if op in DIRECT_INVOCATION_OPS:
        return "application-integration"
    return "diagnostic-probe"


def foreground_guard():
    """The display belongs to Cura between steps (the visible-
    interactions ruling): the evidence records the DISPLAY, so a step
    that hands it to another process — a link press opening a browser
    — makes every frame after it evidence of that process instead.
    The driver re-raises and reads the state back. A platform with no
    foreground authority (the WM-less Xvfb, before the window has ever
    been active) answers `none`: recorded, never a failure, because
    there is no baseline to read a theft against."""
    if not CAPTURE:
        # The guard protects the RECORDING. With no frames there is
        # nothing to protect, and re-raising Cura would be a side
        # effect on a leg that claims nothing about the display.
        return {"authority": "not-applicable", "capture": "off"}
    try:
        state = rpc({"id": 1, "cmd": "foreground", "raise": True}, timeout=20)
    except Exception as exc:
        return {"authority": "unreachable", "foreground": None,
                "error": repr(exc)[:120]}
    if not state.get("ok"):
        return {"authority": "unreachable", "foreground": None,
                "error": str(state.get("error"))[:120]}
    after = state.get("after") or {}
    before = state.get("before") or {}
    return {"authority": state.get("authority"),
            "foreground": state.get("foreground"),
            "raised": bool(state.get("raised")),
            "focus": after.get("focus"), "held_by": before.get("focus")}


def _foreground_verdict(foreground, ok, assertion):
    """Fold the guard into a step's verdict: a display that did not
    come home fails the step (its own frames, and every later one, are
    evidence of the wrong process); a display that came home is named
    in the assertion, never hidden."""
    if foreground is None:
        return ok, assertion
    if foreground.get("authority") == "not-applicable":
        # Left unsaid on every assertion, deliberately: the capture
        # block in evidence.json already records the mode once for the
        # whole leg, and a suffix on each of 55 steps is noise that
        # hides the assertion it is appended to.
        return ok, assertion
    if foreground.get("foreground") is False and foreground.get("authority") not in (
            None, "none", "unreachable"):
        return False, (f"{assertion} · the display was taken from Cura and the "
                       "re-raise did not get it back")
    if foreground.get("raised"):
        return ok, f"{assertion} · another window had taken the display; Cura was re-raised"
    return ok, assertion


def _evidence_entry(spec, index, step, name, ok, action, assertion, capture, started,
                    delivery=None, geometry=None, walk=None, foreground=None):
    path, capture_error = capture if isinstance(capture, tuple) else (capture, None)
    op = step.get("op") if isinstance(step, dict) else "boot"
    return {
        "schema": 1,
        "scenario": spec.get("id"),
        "step": index,
        "name": name,
        "op": op,
        "spec": step,
        "ok": bool(ok),
        "action": action,
        "assertion": assertion,
        "capture": os.path.basename(path) if path else None,
        "capture_error": capture_error,
        "duration_ms": round((time.monotonic() - started) * 1000),
        # Seconds from the recorder's start to this step's start: what
        # aligns a step to the second of the recording it ran in, which
        # is how the static check knows whether a still span was driven.
        "at_s": (round(started - CAPTURE_T0, 1)
                 if CAPTURE_T0 is not None else None),
        "delivery": delivery,
        # The evidence-visibility fields (the review's C8/C9): the
        # resolved element's scene rect — what the capture outlines —
        # and the walk that resolved it, each its own channel so
        # neither can collide with the delivery record.
        "geometry": geometry,
        "walk": walk,
        "class": _classify(op, delivery),
        # The foreground record (the visible-interactions ruling): what
        # the display showed when the step's frame was captured.
        "foreground": foreground,
    }


def write_evidence(title):
    """evidence.json beside the gallery: the machine-readable run
    record. A run whose evidence never landed fails ui_test.sh's
    EVIDENCE MISSING check whatever the verdict said."""
    by_class = {"diagnostic-probe": 0, "application-integration": 0,
                "ui-interaction": 0}
    scenario_min = {}
    for entry in EVIDENCE:
        by_class[entry["class"]] += 1
        sid = entry["scenario"]
        if sid is None or entry["class"] == "diagnostic-probe":
            # Probe-class steps (waits, reads, the boot) say nothing
            # about a scenario's input mechanism; folding them into
            # the minimum made every scenario read diagnostic-probe.
            continue
        order = CLASS_ORDER[entry["class"]]
        if sid not in scenario_min or order < CLASS_ORDER[scenario_min[sid]]:
            scenario_min[sid] = entry["class"]
    run = {
        "schema": 1,
        "title": title,
        "cura": os.environ.get("CURA_VERSION", "?"),
        "plugin": os.environ.get("PLUGIN_VERSION", "?"),
        "mode": os.environ.get("HARNESS_MODE", ""),
        "written": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "classification": {
            "steps": by_class,
            "scenario_minimum": scenario_min,
        },
        # The capture record, always present: a reader of this file must
        # never have to infer from an empty gallery whether the leg took
        # no pictures because it was told not to or because the harness
        # broke.
        "capture": {
            "mode": "on" if CAPTURE else "off",
            "reason": CAPTURE_REASON,
            "judged": CAPTURE,
        },
        "steps": EVIDENCE,
    }
    if FRAME_PROBES:
        run["frames"] = FRAME_PROBES
        outcomes = liveness_outcome(FRAME_PROBES)
        # The outcome list rides beside the samples so a reader of the
        # artifact gets the verdict the leg acted on, not only the
        # counts it was derived from.
        run["frames_outcome"] = outcomes
        run["frames_stalled"] = stalled_scenarios(outcomes)
        # Announced in BOTH capture modes: the pictures are what the
        # capture gate gives up, and whether the app painted is the one
        # measurement that must not go with them. An unverified
        # scenario says so too — a leg whose window was never shown, or
        # was never seen to deliver a frame, must not read as one where
        # rendering was confirmed.
        for record in outcomes:
            if record["outcome"] == LIVENESS_STALLED:
                print(f"ui_test: NO FRAMES — scenario {record['scenario']}: "
                      f"{record['reason']}")
            elif record["outcome"] == LIVENESS_UNVERIFIED:
                print(f"ui_test: FRAMES UNVERIFIED — scenario {record['scenario']}: "
                      f"{record['reason']}")

    with open(os.path.join(RUN_DIR, "evidence.json"), "w", encoding="utf-8") as handle:
        json.dump(run, handle, indent=2)


def write_gallery(steps, expect_fail, title="the skeleton demo — real Cura under Xvfb, QTest clicks on Cura's own stage buttons",
                  video=None):
    write_evidence(title)
    rows = []
    for name, action, assertion, ok, path in steps:
        capture_error = None
        if isinstance(path, tuple):
            path, capture_error = path
        # The container-skip markers carry no capture (path None) —
        # the gallery renders them text-only.
        if path is None:
            path = ""
        # Only the deliberate-failure step may be red by design; a red
        # real step is a real failure and must read as one.
        verdict = ("EXPECTED FAIL" if name == "13-deliberate-failure"
                   else "PASS" if ok else "FAIL")
        if capture_error:
            verdict = "FAIL"
        rows.append(
            f'<div class="step {"pass" if ok and not capture_error else "fail"}" '
            f'id="step-{html.escape(str(name))}">'
            f'<h3>{html.escape(name)} — {verdict}</h3>'
            f'<p><b>Action:</b> {html.escape(action)}</p>'
            f'<p><b>Assertion:</b> {html.escape(assertion)}</p>'
            + (f'<p><b>Capture:</b> {html.escape(capture_error)}</p>' if capture_error else "")
            + (f'<img src="{html.escape(os.path.basename(path))}" alt="{html.escape(name)}">'
               if os.path.exists(path) else "")
            + "</div>")
    body = "\n".join(rows)
    # The summary first: a 169-step gallery is read for its failures,
    # and hunting them by eye down a page of passing steps is how a red
    # leg gets skimmed instead of read. Each failing step links to its
    # own entry below.
    failed = [(name, assertion) for name, _action, assertion, ok, path in steps
              if not ok or (isinstance(path, tuple) and path[1])]
    expected = [name for name, _a, _s, _ok, _p in steps if name == "13-deliberate-failure"]
    if failed:
        summary = ('<div class="summary fail">'
                   f'<h2>{len(failed)} of {len(steps)} steps FAILED</h2><ul>'
                   + "".join(f'<li><a href="#step-{html.escape(str(n))}">{html.escape(str(n))}</a>'
                             f' — {html.escape(str(a))}</li>' for n, a in failed)
                   + "</ul></div>")
    else:
        summary = (f'<div class="summary pass"><h2>all {len(steps)} steps passed</h2>'
                   + (f'<p class="note">({len(expected)} expected failure by design)</p>'
                      if expected else "")
                   + "</div>")
    provenance = " · ".join(part for part in (
        f"Cura {os.environ.get('CURA_VERSION', '?')}",
        f"plugin {os.environ.get('PLUGIN_VERSION', '?')}",
        os.environ.get("HARNESS_MODE", ""),
        time.strftime("%Y-%m-%d %H:%M"),
    ) if part)
    if not CAPTURE:
        # Say WHY the gallery is empty, in the gallery: an unlabelled
        # picture-free page reads as a broken harness.
        video_tag = ('<p class="note"><b>No captures on this leg by design.</b> '
                     f'{html.escape(CAPTURE_REASON)}</p>')
    else:
        video_tag = (f'<video src="{html.escape(os.path.basename(video))}" controls '
                     f'style="max-width:100%"></video>'
                     if video and os.path.exists(video) else
                     '<p class="note">no recording for this run</p>')
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:sans-serif;background:#111;color:#ddd;margin:2em}}
.step{{border:1px solid #444;border-radius:8px;padding:1em;margin:1em 0;background:#1a1a1a}}
.pass{{border-left:6px solid #2ea043}}.fail{{border-left:6px solid #f85149}}
.summary{{border:1px solid #444;border-radius:8px;padding:1em;margin:1em 0;background:#181818}}
.summary.fail{{border-left:6px solid #f85149}}.summary.pass{{border-left:6px solid #2ea043}}
.summary h2{{margin:0 0 .5em}}.summary ul{{margin:0;padding-left:1.4em}}
.summary a{{color:#f85149}}a{{color:#79c0ff}}
img{{max-width:100%;border:1px solid #444}}h3{{margin-top:0}}</style></head>
<body><h1>{html.escape(title)}</h1>
<p class="note">{html.escape(provenance)}</p>
{summary}
{video_tag}
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
    if reply.get("truncated"):
        # A truncated probe result reads as "{}" after parsing — the
        # silent-data-loss trap the z-group calibration hit twice. Fail
        # loudly so the failure is visible, never a false absence.
        if raise_on_error:
            raise RuntimeError("driver exec result truncated at 4000 chars")
        return {"_truncated": True}
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


def boot_step(hello):
    """The boot record for every gallery: Cura's liveness, the driver
    plugin's registration in Cura's registry (a broken registration
    once let the whole suite pass while Cura flagged the driver as
    failed to load), and the QtTest injection state."""
    qtest_state = rpc({"id": 1, "cmd": "qtest_state"})
    registered = hello.get("registered")
    note = (f"Cura alive: pid {hello.get('pid')}, platform {hello.get('platform')}"
            f" · registered={registered}"
            f" · qtest: ok={qtest_state.get('ok')} err={qtest_state.get('error')}"
            f" wheel={qtest_state.get('sys_path_has_wheel')}")
    return ("00-boot", note, "hello succeeds", registered is True, shot("00-boot"))

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
    if name in ("moonrakerPreviewCard", "moonrakerPreviewCard"):
        try:
            result[name] = bool(item.property("visible"))
        except Exception:
            result[name] = False
"""

# The replace prompt's own button, emitted — the classic probes' mechanism
# throughout (their host is the WM-less Xvfb, where a synthesized click
# does not reach the card). The prompt is the card's popup, so this is the
# QML handler a real press runs, not a widget box to answer.
REPLACE_CONFIRM_EMIT = """
window = _main_window()
result = {}
for item in _walk(window.contentItem()):
    try:
        name = item.property("objectName")
    except Exception:
        name = None
    if name == "moonrakerReplaceConfirmButton" and bool(item.isVisible()):
        item.clicked.emit()
        result["emitted"] = True
        break
"""


def press_replace_confirm(budget=15.0, interval=1.0):
    """Answer the card's replace prompt: its own Replace button.
    Replaces the widget path — there is no QMessageBox to find."""
    return bool(wait_for(
        lambda: exec_rpc(REPLACE_CONFIRM_EMIT).get("emitted"), budget, interval))


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
    if "MoonrakerMJPGImage" in item.metaObject().className():
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
    # edge; the layer changes by dragging its UPPER handle (the square
    # Rectangle whose MouseArea drives setCurrentLayer). Its size is
    # asked of the theme the QML itself reads (LayerSlider.qml:
    # handleSize = UM.Theme.getSize("slider_handle").width) — a
    # literal 16 is only right at a screen scale of 1, and the Windows
    # and macOS runners scale the theme up, where the handles are 22 px
    # and the size test matched nothing at all.
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
    video_path = os.path.join(RUN_DIR, "scenario9.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewCard"), 25.0))
        wait_for(lambda: exec_rpc(LOAD_EMIT).get("emitted"), 10.0, 1.0)
        press_replace_confirm()
        action = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewCard"), 60.0, 2.0))
        steps.append(("04-loaded", "the print loaded; the action card appeared",
                      "moonrakerPreviewCard visible", bool(empty and action),
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
        stop_recorder(video)
    title = "Gate #9 — pause list verified-only"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario8():
    # Gate #8: the dwell profile — a 10-minute soak, console up,
    # against the capacity-limited simulator. The assertions run over
    # the periodic samples: the request-rate budget, the p95, the
    # GUI scheduled-latency (a 100 ms timer chain's drift) and the
    # receipt canary (the WS feed keeps flowing end to end).
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario8.mp4")
    video = start_recorder(video_path, framerate=5)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #8 — dwell profile"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario10():
    # Gate #10: restart arming / e-stop latch. A running print is
    # emergency-stopped through the real button (click twice, then
    # hold); the printer errors and cancels; a demonstrably fresh
    # print then starts and the latch clears.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario10.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #10 — restart arming / e-stop latch"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario11():
    # Gate #11: scroll-to-prompt. The console floods, a REAL drag
    # scrolls it up, then a typed command is sent: the view must
    # follow back to the prompt, and the recall history returns the
    # last command on the up arrow.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario11.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #11 — scroll-to-prompt"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario7():
    # Gate #7: transport handover. In websocket mode the Monitor's
    # lanes must ride the socket: six Monitor<->Prepare swaps, then
    # the peer's ledger — the HTTP monitor-lane entries must not have
    # grown during the swaps (beyond the settled bootstrap) while the
    # WS entries grew (the positive sentinel).
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario7.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #7 — transport handover"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario6():
    # Gate #6: detach on any layer selection change. The print
    # loads (scenario-2's flow), the follower attaches, then a REAL
    # drag on Cura's own LayerSlider changes the layer: the follower
    # must detach and stay detached. The variant: a view-swap away
    # and back re-attaches — THE ONLY automatic re-attach.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario6.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewCard"), 25.0))
        wait_for(lambda: exec_rpc(LOAD_EMIT).get("emitted"), 10.0, 1.0)
        confirmed = press_replace_confirm()
        action = bool(wait_for(
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewCard"), 60.0, 2.0))
        steps.append(("04-loaded", "the print loaded; the action card appeared",
                      "moonrakerPreviewCard visible", bool(empty and confirmed and action),
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
        # The variant (the ruling): a view swap while
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
        stop_recorder(video)
    title = "Gate #6 — detach on any layer selection change"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario5():
    # Gate #5: temperatures at print start — the RACE form. The
    # simulator holds the subscribe reply past the client's 8 s
    # proof window mid-print (the klippy restart re-arms it), then
    # releases: the first push must follow within 3 s on the sim's
    # event clock and the plugin's temperature history must anchor
    # its first sample right after the sync snapshot.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario5.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #5 — temperatures at print start"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario4():
    # Gate #4: camera first load — the HARD ordering. The Monitor
    # is entered while the webcam list is still pending (the sim
    # delays server/webcams/list); the list arrives and the stream
    # must appear with NO interaction — two captures of the
    # viewport's changing test pattern prove liveness.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario4.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        # Xvfb (the same trigger gap behind the refresh-click
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
        stop_recorder(video)
    title = "Gate #4 — camera first load"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario3():
    # Gate #3: M117 in the Print-job section. The simulator pushes
    # display_status.message A, then B: the RENDERED slot label shows
    # B and A is absent; then the message clears and the slot's
    # previous content returns (empty, fixed height — no reflow).
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario3.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #3 — M117 in the Print-job section"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario2(expect_fail=False):
    # Gate #2: the card stays through load and after render. Enter
    # Preview with nothing loaded (the empty card), click Load current
    # print, go HANDS-OFF: the action card (the one with Detach) must
    # be visible continuously from the click to 30 s after settle and
    # the empty card must never reappear.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario2.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
            lambda: exec_rpc(CARD_READ).get("moonrakerPreviewCard"), 25.0))
        steps.append(("04-empty-card", "the seeded running job makes the empty card show its Load button",
                      "moonrakerPreviewCard visible", empty, shot("04-empty-card")))
        # The card's button: window-level synthesized clicks do not
        # reach this control under the WM-less Xvfb, so the button's
        # clicked signal is emitted — the exact QML handler a real
        # click runs. The replace-confirm that follows is the card's
        # own popup, pressed at its button.
        wait_for(lambda: exec_rpc(LOAD_EMIT).get("emitted"), 10.0, 1.0)
        confirmed = press_replace_confirm()
        steps.append(("05-load-click", "Load current print (clicked-signal emission) + replace-confirm Yes — then HANDS-OFF",
                      "the load was requested and confirmed", bool(confirmed), shot("05-load-click")))
        # The hands-off trace: samples every 2 s, no interaction.
        trace = []
        settled_at = None
        start = time.time()
        for _ in range(35):
            state = exec_rpc(CARD_READ)
            trace.append((round(time.time() - start, 1),
                          bool(state.get("moonrakerPreviewCard")),
                          bool(state.get("moonrakerPreviewCard"))))
            if state.get("moonrakerPreviewCard") and settled_at is None:
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
        stop_recorder(video)
    title = "Gate #2 — the card stays through load and after render"
    write_gallery(steps, expect_fail, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def scenario1(expect_fail=False):
    # Gate #1: the failure state clears itself. A cold start raises
    # the transient extrude error; the jog pad must UNLOCK; the
    # transient recovers into printing with NO failure verdict (or, on
    # the red run against a printer that stays broken, the verdict
    # window must fire) — and the peer's ledger shows ONE connection
    # throughout.
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "scenario1.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, shot("01-gate")))
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
        stop_recorder(video)
    title = "Gate #1 — the failure state clears itself"
    write_gallery(steps, expect_fail, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


# ---- The first-install leg (modes firstinstall1 / firstinstall2) ----
#
# One machine, one xdg tree, two boots: ui_test.sh's firstinstall mode
# seeds the tree CLEAN for boot 1 (no plugin config folder, no cura.cfg
# section) and hands the same tree back for boot 2 untouched. Boot 1
# proves the activation semantics a never-run install gets and then
# configures the printer through the plugin's own save path; boot 2
# proves the second boot keeps that config (the lost-config report).
FIRST_INSTALL_URL = "http://127.0.0.1:7125"
# The record's witness: an inert settings field carrying a value only
# this leg writes, so a record rebuilt from the legacy defaults can
# never read as the one boot 1 saved.
FIRST_INSTALL_MARKER = "harness-firstinstall"
# Boot 1's document, written where both boots can read it (the mode's
# unit dir — a container path, handed in by ui_test.sh).
BOOT1_DOCUMENT = os.environ.get("HARNESS_BOOT1_DOC", "/tmp/mpf/boot1-document.json")




def _recursive_diff(before, after, path=""):
    """The added/removed/changed paths between two documents."""
    diff = []
    keys = set((before or {}).keys()) | set((after or {}).keys())
    for key in sorted(keys):
        p = f"{path}.{key}" if path else key
        if key not in (before or {}):
            diff.append({"path": p, "change": "added", "value": after.get(key)})
        elif key not in (after or {}):
            diff.append({"path": p, "change": "removed"})
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            diff.extend(_recursive_diff(before[key], after[key], p))
        elif before[key] != after[key]:
            diff.append({"path": p, "change": "changed",
                         "before": before[key], "after": after[key]})
    return diff

def plugin_document():
    """The settings document as the driver reads it OFF DISK (Cura's
    own storage rule). None when there is no readable document — the
    absence is a state this leg must be able to see."""
    try:
        reply = rpc({"id": 1, "cmd": "plugin_settings"})
    except RuntimeError:
        return None
    if not reply.get("ok"):
        return None
    return reply


def document_of(reply):
    document = (reply or {}).get("document")
    return document if isinstance(document, dict) else None


def cura_process_alive():
    """Cura's own process, through the platform dispatch. The call was
    a bare pgrep, which on Windows raises OSError and reads as "still
    running" — a clean quit then reports as a failed one."""
    try:
        done = subprocess.run(native_host.process_alive_argv(SYSTEM),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return True
    return done.returncode == 0


def driver_port_dead():
    """The driver's socket: a refused connection is the plugin's own
    death certificate (the port file outlives the process)."""
    try:
        port = int(open(PORT_FILE, encoding="utf-8").read().strip())
    except (OSError, ValueError):
        return True
    try:
        with socket.create_connection((DRIVER_HOST, port), timeout=2):
            return False
    except OSError:
        return True


def first_install1():
    """Boot 1 of the first-install leg: a machine that has never run
    the plugin. The v2 document must appear with nothing migrated into
    it, the printer's config must save through the plugin's own save
    verb, and the app must quit cleanly — boot 2 reads whatever this
    boot leaves on disk."""
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "firstinstall1.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        # The aux half of the gate is skipped by construction: a clean
        # install has no printer to discover, so the chain it checks
        # cannot come alive.
        gate = ensure_ready(require_aux=False)
        steps.append(("01-gate", "boot gate: the machine is restored, no welcome overlay",
                      "welcome absent, window at the pinned geometry (no printer configured yet)",
                      gate, shot("01-gate")))
        # The activation (the ruling): nothing was migrated,
        # so the document activates directly — the version, no machine
        # records, and NO migration record for the second boot to
        # re-run against live config.
        document = wait_for(lambda: document_of(plugin_document()), 120.0)
        global_section = (document or {}).get("global")
        activated = (isinstance(document, dict)
                     and document.get("configVersion") == 2
                     and document.get("machines") == {}
                     and isinstance(global_section, dict)
                     and "migration" not in global_section)
        steps.append(("02-activate", "the plugin activates its v2 settings document on a clean install",
                      "configVersion 2, machines {}, no migration key in global",
                      activated, shot("02-activate")))
        # The configuration: the settings dialog's own save verb, so
        # the write goes through the plugin's validation and its
        # persistence path — never a file edited behind the app.
        save = rpc({"id": 1, "cmd": "plugin_save_config",
                    "params": {"url": FIRST_INSTALL_URL,
                               "api_key": FIRST_INSTALL_MARKER,
                               # The save validates that the translate
                               # pair has equal lengths; the marker is
                               # its own output so both stay inert.
                               "filename_translate_input": FIRST_INSTALL_MARKER,
                               "filename_translate_output": FIRST_INSTALL_MARKER}},
                   timeout=60)
        steps.append(("03-configure", "the printer is configured through the settings save the dialog uses",
                      "the save was accepted", bool(save.get("ok")), shot("03-configure")))
        # The save read back from the file, not from the plugin's
        # memory: what boot 2 gets is what is on disk.
        after = document_of(plugin_document())
        machines = (after or {}).get("machines")
        marked = {key: value for key, value in (machines or {}).items()
                  if isinstance(value, dict)
                  and value.get("filename_translate_input") == FIRST_INSTALL_MARKER}
        recorded = (len(marked) == 1
                    and list(marked.values())[0].get("url") == FIRST_INSTALL_URL)
        steps.append(("04-recorded", "the saved config is in the settings file on disk",
                      "exactly one machine record carries the leg's marker and the printer's url",
                      recorded, shot("04-recorded")))
        if after:
            # The handoff: boot 2 diffs the live record against this.
            with open(BOOT1_DOCUMENT, "w", encoding="utf-8") as handle:
                json.dump(after, handle, indent=2, sort_keys=True)
        # The clean exit: boot 2 must find a tree Cura closed itself,
        # not one this script killed mid-write. The driver acks the
        # request before it closes, so an ack that did not arrive is a
        # failure, not the shutdown eating its own reply.
        try:
            quit_reply = rpc({"id": 1, "cmd": "quit"}, timeout=30)
        except RuntimeError as exc:
            quit_reply = {"ok": False, "error": repr(exc)}
        deadline = time.time() + 150
        while time.time() < deadline and (cura_process_alive() or not driver_port_dead()):
            time.sleep(2)
        exited = driver_port_dead() and not cura_process_alive()
        if not (quit_reply.get("ok") and exited):
            # Which half failed — the ack (did the ask land) or the
            # exit (did the app leave) — has to reach the log: the
            # gallery's assertion line is static text.
            print(f"ui_test: quit reply={quit_reply!r} exited={exited}")
        steps.append(("05-quit", "the driver asks Cura to close itself for the second boot",
                      "closeApplication accepted and the process is gone",
                      bool(quit_reply.get("ok")) and exited, shot("05-quit")))
    finally:
        stop_recorder(video)
    title = "First install — boot 1: the clean activation and the save"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def first_install2():
    """Boot 2 of the first-install leg: the same tree, booted again.
    The document boot 1 left holds live configuration; the migration
    machinery must not replace it with records rebuilt from the legacy
    blob — the machine record must still be there, field for field."""
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "firstinstall2.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        # This boot has a printer: the saved record points at the
        # simulator, so the full gate applies.
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: the saved machine is restored and connects",
                      "welcome absent, window at the pinned geometry",
                      gate, shot("01-gate")))
        try:
            with open(BOOT1_DOCUMENT, encoding="utf-8") as handle:
                before = json.load(handle)
        except (OSError, ValueError):
            before = None
        previous = (before or {}).get("machines") or {}
        document = wait_for(lambda: document_of(plugin_document()), 90.0)
        current = (document or {}).get("machines") or {}
        survived = bool(previous) and all(
            isinstance(current.get(key), dict)
            and all(current[key].get(field) == value for field, value in record.items())
            for key, record in previous.items())
        steps.append(("02-record-survives", "the config written on the first boot is still there",
                      "every field of the boot-1 machine record is unchanged after the second boot",
                      survived, shot("02-record-survives")))
        # Beyond the record: "untouched" is asserted over the WHOLE
        # document. The migration machinery's signature is the record
        # it writes into global, and a record rebuilt from the legacy
        # defaults could never carry boot 1's values field for field —
        # so an equal document proves neither happened.
        untouched = bool(before) and document is not None and document == before
        if not untouched:
            # The diagnostic dump (the reviewer's demand): the exact
            # recursive diff that names the second-boot writer.
            diff = _recursive_diff(before, document)
            print("FIRSTINSTALL-DIFF " + json.dumps(diff, sort_keys=True)[:4000])
        steps.append(("03-untouched", "the second boot leaves the settings document alone",
                      "the document is identical to the one the first boot left",
                      untouched, shot("03-untouched")))
    finally:
        stop_recorder(video)
    title = "First install — boot 2: the config survives the second boot"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


# ---- the two-boot legs on a native host (modes firstinstall / migration) ----
#
# On Linux tools/ui_test.sh drives the pair: it owns the launch there, so
# the between-boots work (stop the first app, clear the rendezvous, boot
# again) lives in that script. A native host's setup script launches ONCE
# per job, so that same work has no shell to live in and happens here.
#
# Each boot is the SAME runner entry point the container leg calls, run as
# a subprocess: the gallery, the video and the verdict come from identical
# code, and the boots stay as isolated as they are on Linux. The mode
# itself stops being a fall-through to scenario(), which is what a job
# asking for "firstinstall" used to get on a native host.

TWO_BOOT_MODES = {"firstinstall": ("firstinstall1", "firstinstall2"),
                  "migration": ("migration1", "migration2")}

# The natives' own setup scripts wait 300 ticks for the driver's port
# file; the second boot starts from the same cold cache (no OS file
# cache for Cura's bundle is shared across processes) and gets the same
# budget.
SECOND_BOOT_DEADLINE_S = 300.0


def boot_env(run_dir, boot1_document):
    """The environment one boot runs with: where its gallery lands and
    the document boot 1 leaves for boot 2 to diff against."""
    env = dict(os.environ)
    env["HARNESS_RUN_DIR"] = run_dir
    env["HARNESS_BOOT1_DOC"] = boot1_document
    return env


def wait_for_driver(deadline_s):
    """The driver ANSWERING — which is not the same as the port file
    existing: the file outlives the process, so boot 1's port would
    pass the check while the socket behind it is dead."""
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        try:
            reply = rpc({"id": 1, "cmd": "hello"}, timeout=10)
        except RuntimeError:
            continue
        if reply.get("ok"):
            return reply
    return None


def two_boot_run(mode):
    """The two-boot legs, both boots, from the mode the workflow names."""
    boot1_mode, boot2_mode = TWO_BOOT_MODES[mode]
    runner = os.path.abspath(__file__)
    boot1_document = os.path.join(RUN_DIR, "boot1-document.json")

    print(f"ui_test: {mode}: boot 1 ({boot1_mode}) -> {RUN_DIR}")
    rc1 = subprocess.call([sys.executable, runner, boot1_mode],
                          env=boot_env(RUN_DIR, boot1_document))
    # Boot 1's log before boot 2's launch truncates it.
    harvest_cura_log(RUN_DIR, suffix="-boot1")

    # Boot 2 needs the tree to itself: whatever the first boot left
    # running goes first, and the rendezvous files go with it (a stale
    # port would send the second boot to a dead socket). The kill is
    # unconditional — a runner that crashed mid-scenario must not leave
    # an app behind that the second boot then races.
    subprocess.call(native_host.kill_command(SYSTEM),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    for name in ("harness_port.txt", "harness_token.txt"):
        try:
            os.remove(os.path.join(RPC_DIR, name))
        except OSError:
            pass

    binary = os.environ.get("HARNESS_CURA_BIN", "")
    if not binary:
        print("ui_test: HARNESS_CURA_BIN is unset - the native setup script must export it, "
              "or the second boot has no app to launch")
        return rc1 or 1
    command = native_host.launch_command(SYSTEM, binary,
                                         working_dir=os.environ.get("HARNESS_CURA_CWD") or None)
    # start_new_session: the app must outlive this process (the capture,
    # not the launcher, is what keeps it alive), and it inherits this
    # process's environment — the one the setup script exported, GL
    # variables included.
    subprocess.Popen(command["argv"], cwd=command["cwd"], start_new_session=True)
    print(f"ui_test: {mode}: relaunched {binary} for boot 2")

    hello = wait_for_driver(SECOND_BOOT_DEADLINE_S)
    if hello is None:
        print(f"ui_test: the second boot's driver never came up within "
              f"{SECOND_BOOT_DEADLINE_S:.0f}s - Cura did not load the staged plugin")
        return 1

    boot2_dir = os.path.join(RUN_DIR, "boot2")
    print(f"ui_test: {mode}: boot 2 ({boot2_mode}) -> {boot2_dir}")
    rc2 = subprocess.call([sys.executable, runner, boot2_mode],
                          env=boot_env(boot2_dir, boot1_document))
    # The second boot's gallery is half this leg's evidence, and the
    # workflow's guard only knows about the first boot's directory.
    if not os.path.isfile(os.path.join(boot2_dir, "index.html")):
        print(f"ui_test: EVIDENCE MISSING - boot 2 wrote no gallery at {boot2_dir}/index.html")
        return rc2 or 1
    return rc1 or rc2


# ---- The migration leg (modes migration1 / migration2) ----
#
# One xdg tree, two boots, seeded PRE-migration (ui_test.sh's
# premigration seed: the 4.3.0-era printer_configs_v1 blob with two
# machine records plus the old state file, no v2 files): boot 1 runs
# the real one-shot — every machine record migrates at once, the
# backup lands, the blob leaves cura.cfg, the transcript splits into
# the per-machine shard, and the machine-switch verb re-routes the
# live model; boot 2 proves the one-shot never re-runs (the document
# and the backup set are untouched).
MIGRATION_MACHINES = {
    "FDM Printer Base Description": "http://127.0.0.1:7125",
    "Second Machine": "http://127.0.0.1:7126",
}

# The post-migration cura.cfg check: after the preference save, the
# blob must be GONE and the whole [moonrakerprintfollower] section
# with it — the clean resets every key to its registered default and
# Uranium's writer omits defaults (the "no trace" contract). Read
# through the driver's own process (the exec verb), never a staged
# file.
MIGRATION_CFG_PROBE = """
from UM.Resources import Resources
import os
path = os.path.join(Resources.getConfigStoragePath(), "cura.cfg")
try:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    # Uranium's writer omits default values but keeps the section
    # HEADER — an empty [moonrakerprintfollower] shell remains. The
    # contract is the config's removal: the blob key gone, and no
    # legacy plugin keys left in the section.
    section = text.split("[moonrakerprintfollower]", 1)
    residue = section[1].split("[", 1)[0] if len(section) > 1 else ""
    result = {"blob_gone": "printer_configs_v1" not in text,
              "section_clean": not [line for line in residue.splitlines() if line.strip()]}
except Exception as exc:
    result = {"error": repr(exc)}
"""

# The live identity after the machine switch: the binding's identity
# pair must name the switched-to machine.
MIGRATION_ADD_MACHINE_PROBE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
try:
    manager = app.getMachineManager()
    before = manager.activeMachine.getName() if manager.activeMachine else None
    added = bool(manager.addMachine(str("fdmprinter")))
    result["added"] = added
    result["before"] = before
    result["new_name"] = manager.activeMachine.getName() if manager.activeMachine else None
except Exception as exc:
    result["error"] = repr(exc)
"""


MIGRATION_IDENTITY_PROBE = """
from UM.Application import Application
app = Application.getInstance()
result = {}
for e in app.getExtensions():
    if "MoonrakerPrintFollower" in type(e).__name__:
        binding = getattr(getattr(e, "_runtime", None), "binding", None)
        if binding is not None:
            try:
                result["identity"] = [str(value) for value in binding.identity]
            except Exception as exc:
                result["identity"] = ["ERR", repr(exc)[:80]]
        break
"""


def migration1():
    """Boot 1 of the migration leg: the pre-migration fixture runs the
    real one-shot, and the machine switch re-routes the live model."""
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "migration1.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: the pre-migration machine is restored",
                      "welcome absent, window at the pinned geometry",
                      gate, shot("01-gate")))
        # The one-shot runs at boot (B1's initializationFinished);
        # poll the file on disk until the ok record lands.
        def migrated_document():
            reply = rpc({"id": 1, "cmd": "read_json_file",
                         "name": "MoonrakerPrintFollower/settings.json"})
            if not reply.get("ok"):
                return None
            document = reply.get("document")
            if not isinstance(document, dict):
                return None
            record = (document.get("global") or {}).get("migration")
            if not isinstance(record, dict) or record.get("status") != "ok":
                return None
            return document
        document = wait_for(migrated_document, 120.0)
        machines = (document or {}).get("machines") or {}
        record = ((document or {}).get("global") or {}).get("migration") or {}
        migrated = (isinstance(document, dict)
                    and document.get("configVersion") == 2
                    and all(machines.get(name) == url
                            or (isinstance(machines.get(name), dict)
                                and machines[name].get("url") == url)
                            for name, url in MIGRATION_MACHINES.items())
                    and record.get("status") == "ok"
                    and record.get("reason") == "migrated"
                    and record.get("records") == len(MIGRATION_MACHINES)
                    and record.get("backupWritten") is True
                    and isinstance(record.get("backupName"), str))
        steps.append(("02-migrated", "the one-shot migrated every machine record at once",
                      "configVersion 2, both records with their urls, ok/migrated, records 2, backup written",
                      migrated, shot("02-migrated")))
        # The clean's preference resets reach the FILE at the next
        # preference save — Cura's shutdown flush, which boot 2
        # verifies. The session still owes a save so the cleaned
        # preferences ride it: the settings save the dialog uses.
        save = rpc({"id": 1, "cmd": "plugin_save_config", "params": {}}, timeout=60)
        steps.append(("03-save", "the settings save carried the cleaned preferences",
                      "the save was accepted", bool(save.get("ok")), shot("03-save")))


        # The second machine's history landed in its own state shard.
        shard_reply = rpc({"id": 1, "cmd": "read_json_file",
                           "name": "MoonrakerPrintFollower/machines/Second+Machine.json"})
        shard = shard_reply.get("document") if shard_reply.get("ok") else None
        transcript = (shard or {}).get("consoleTranscript") or []
        shard_ok = bool(transcript and str((transcript[0] or {}).get("text")) == "// second machine history")
        steps.append(("05-shard", "the console history split into the second machine's state shard",
                      "Second Machine's shard carries the history line",
                      shard_ok, shot("05-shard")))
        # The machine switch: the harness's Cura side carries ONE
        # machine stack, so the switch ADDS a second one — Cura
        # activates what it adds — and the live model must follow
        # the new active machine (the unit suites cover the A->B->A
        # cycles; this is the live re-route proof).
        add = wait_for(lambda: exec_rpc(MIGRATION_ADD_MACHINE_PROBE), 60.0)
        new_name = (add or {}).get("new_name") if isinstance(add, dict) else None
        identity = wait_for(lambda: exec_rpc(MIGRATION_IDENTITY_PROBE), 30.0)
        switched = (bool(add.get("added")) and isinstance(identity, dict)
                    and bool(new_name)
                    and (identity.get("identity") or [None, None])[1] == new_name)
        steps.append(("06-switch", "the machine add re-routes the live model",
                      "the binding's identity names the newly added machine",
                      switched, shot("06-switch")))
        if document:
            with open(BOOT1_DOCUMENT, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2, sort_keys=True)
        try:
            quit_reply = rpc({"id": 1, "cmd": "quit"}, timeout=30)
        except RuntimeError as exc:
            quit_reply = {"ok": False, "error": str(exc)}
        steps.append(("07-clean-exit", "the app quit cleanly after the migration",
                      "the driver acked the quit", bool(quit_reply.get("ok")), shot("07-clean-exit")))
    finally:
        stop_recorder(video)
    title = "Migration — boot 1: the v1 blob migrates into the v2 files"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def migration2():
    """Boot 2 of the migration leg: the second boot leaves the
    migrated tree alone — the one-shot never re-runs."""
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "migration2.mp4")
    video = start_recorder(video_path, framerate=15)
    try:
        steps = []
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: the migrated machine is restored",
                      "welcome absent, window at the pinned geometry",
                      gate, shot("01-gate")))
        # The quit's preference flush landed with boot 1's shutdown:
        # the clean's defaults now own the file, so the blob and the
        # whole section are gone — the no-trace contract.
        cfg = wait_for(lambda: exec_rpc(MIGRATION_CFG_PROBE), 30.0)
        blob_gone = (isinstance(cfg, dict)
                     and cfg.get("blob_gone") is True
                     and cfg.get("section_clean") is True)
        steps.append(("02-blob-gone", "the v1 blob is gone from cura.cfg",
                      "no printer_configs_v1 key and the section holds no keys",
                      blob_gone, shot("02-blob-gone")))
        try:
            with open(BOOT1_DOCUMENT, encoding="utf-8") as handle:
                before = json.load(handle)
        except (OSError, ValueError):
            before = None
        reply = rpc({"id": 1, "cmd": "read_json_file",
                     "name": "MoonrakerPrintFollower/settings.json"})
        document = reply.get("document") if reply.get("ok") else None
        untouched = bool(before) and document is not None and document == before
        if not untouched:
            print("MIGRATION-DIFF " + json.dumps(
                _recursive_diff(before, document), sort_keys=True)[:4000])
        steps.append(("03-untouched", "the second boot leaves the migrated document alone",
                      "the document is identical to the one boot 1 left",
                      untouched, shot("03-untouched")))
        # The backup set: exactly the one backup boot 1 wrote — no
        # re-run, no new copy.
        backup_probe = """
from UM.Resources import Resources
import os
base = Resources.getConfigStoragePath()
result = {"backups": sorted(
    name for name in os.listdir(base)
    if name.startswith("cura.cfg."))}
"""
        backups = wait_for(lambda: exec_rpc(backup_probe), 30.0)
        one_backup = isinstance(backups, dict) and len(backups.get("backups") or []) == 1
        steps.append(("04-one-backup", "no second migration ran",
                      "exactly one cura.cfg backup exists after the second boot",
                      one_backup, shot("04-one-backup")))
        try:
            quit_reply = rpc({"id": 1, "cmd": "quit"}, timeout=30)
        except RuntimeError as exc:
            quit_reply = {"ok": False, "error": str(exc)}
        steps.append(("05-clean-exit", "the app quit cleanly after the second boot",
                      "the driver acked the quit", bool(quit_reply.get("ok")), shot("05-clean-exit")))
    finally:
        stop_recorder(video)
    title = "Migration — boot 2: the second boot leaves the migrated tree alone"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


SUITE_STATE = {"sim": {}, "model": {}, "item": {}, "rect": {}, "stash": {}}

# The suite's groups by name — SCENARIO_GROUP accepts either.
ATTACH_READ = ("from UM.Application import Application\n"
               "app = Application.getInstance()\n"
               "result = {}\n"
               "for e in app.getExtensions():\n"
               "    if \"MoonrakerPrintFollower\" in type(e).__name__:\n"
               "        coord = getattr(getattr(e, \"_runtime\", None), \"coordinator\", None)\n"
               "        if coord is not None:\n"
               "            result[\"attached\"] = bool(getattr(getattr(coord, \"_preview\", None), \"state\", None)\n"
               "                                              and coord._preview.state.attached)\n"
               "        break\n")

# The suite's groups — the id IS the name (the letter scheme was
# retired by request): connection, status, temperatures, console,
# webcams, files, motion, printing, settings, stress, visual, preview,
# probe, real (the real-printer read-only group).


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
    import scenarios
    specs = [spec for spec in scenarios.SCENARIOS if spec.get("group") == group_id]
    if not specs:
        print(f"no suite scenarios in group {group_id}")
        return 1
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, f"suite-{group_id}.mp4")
    video = start_recorder(video_path, framerate=15)
    steps = []
    try:
        hello = rpc({"id": 1, "cmd": "hello"})
        _boot = boot_step(hello)
        steps.append(_boot)
        EVIDENCE.append(_evidence_entry({"id": group_id}, -1, {"op": "hello"},
                                        _boot[0], _boot[3], _boot[1], _boot[2],
                                        _boot[4], time.monotonic()))
        gate = ensure_ready()
        _gate_cap = shot("01-gate")
        steps.append(("01-gate", "boot gate: active machine present, no welcome overlay",
                      "welcome absent, window at the pinned geometry", gate, _gate_cap))
        EVIDENCE.append(_evidence_entry({"id": group_id}, -2, {"op": "boot_gate"},
                                        "01-gate", gate,
                                        "boot gate: active machine present, no welcome overlay",
                                        "welcome absent, window at the pinned geometry", _gate_cap, time.monotonic()))
        wait_stage("PrepareStage", timeout_ms=60000)
        progress(f"{group_id}: {len(specs)} scenario(s) in this group")
        done = 0
        for spec in specs:
            if spec.get("container_skip"):
                # The engine-divergent scenarios (real Cura
                # proves them live; the container's engine cannot —
                # TECH_DEBT's two-engine item). The skip is a
                # RECORDED marker, never a silent pass: the reason
                # rides the gallery and the recheck trigger lives in
                # TECH_DEBT #2.
                steps.append((spec["id"] + "-skip",
                              "skipped on the container engine",
                              spec["container_skip"], True, None))
                continue
            version_skip = spec.get("version_skip") or {}
            version = os.environ.get("CURA_VERSION", "")
            reason = version_skip.get(version) or next(
                (text for prefix, text in version_skip.items()
                 if version.startswith(prefix)), None)
            if reason:
                # The version-divergent scenarios (5.11's preview
                # platform cannot hold the premise — the walk-dump
                # evidence). The skip is a RECORDED marker with the
                # reason in the gallery, never a silent pass.
                steps.append((spec["id"] + "-skip",
                              f"skipped on Cura {version or 'unknown'}",
                              reason, True, None))
                continue
            sim_http("/harness/reset", "POST", {})
            # The update toast may land mid-suite (the check completes
            # after the boot gate); re-dismiss it per scenario so a
            # late toast never swallows the console presses.
            rpc({"id": 1, "cmd": "hide_update_toast"})
            sim_http("/harness/scenario", "POST", {"console_lines": [{"type": "response",
                "message": "// %s ready" % spec["id"], "time": time.time()}]})
            started = time.monotonic()
            done += 1
            # One line per scenario, flushed. A silent leg is unreadable
            # while it runs and — worse — a leg killed at its timeout
            # dies with whatever is still buffered, so the log shows
            # nothing of how far it got.
            progress(f"[{done}] {spec['id']} — {spec.get('name', '')}")
            scenario_steps = suite_scenario(spec)
            steps.extend(scenario_steps)
            failed = [entry[0] for entry in scenario_steps if not entry[3]]
            # The verdict LEADS: a scan of this log should never have
            # to read "0 failed" as a pass, or hunt for which scenario
            # is the red one.
            progress(f"[{done}] {spec['id']}: "
                     + ("PASSED \u2705" if not failed else "FAILED \u274c")
                     + f" in {time.monotonic() - started:.0f}s — "
                     + (f"{len(scenario_steps)} steps, failed: {', '.join(failed)}"
                        if failed else f"all {len(scenario_steps)} steps passed"))
    finally:
        stop_recorder(video)
    title = f"Scenario group {group_id}"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)


def suite_scenario(spec, step_fn=None):
    # Resolved lazily: suite_step is defined below this function.
    if step_fn is None:
        step_fn = suite_step
    steps = []
    start_sample = frames_probe("start", spec["id"])
    # The calibration pre-step (a suite default, not a per-spec
    # field): every scenario starts from the baseline geometry, so no
    # scenario's resize can leak into the next inside a group's
    # shared boot (the round-2 H1/M-3). A one-shot resize — the boot
    # pin's late reapplies would otherwise yank the window back to
    # the baseline mid-scenario, right after the scenario's own
    # crush resize. The driver verifies the size it applied; a
    # mismatch is a recorded failing step, not a swallowed exception.
    _want = [int(part) for part in WINDOW_SIZE.split("x")]
    for index, step in enumerate(spec.get("steps", ())):
        if index == 0:
            try:
                reply = rpc({"id": 1, "cmd": "resize", "w": _want[0], "h": _want[1]},
                            timeout=40)
                _got = reply.get("size") or []
                _ok = bool(reply.get("ok")
                           and [int(_got[0]), int(_got[1])] == _want)
            except Exception:
                _ok = False
                _got = []
            if not _ok:
                name = f"{spec['id']}-00-geometry"
                cap = shot(name)
                steps.append((name,
                              "the calibration pre-step: window at the baseline geometry",
                              f"resize to {WINDOW_SIZE} verified; the window reports {_got}",
                              False, cap))
                EVIDENCE.append(_evidence_entry(
                    spec, -3, {"op": "resize"}, name, False,
                    "the calibration pre-step: window at the baseline geometry",
                    f"resize to {WINDOW_SIZE} verified; the window reports {_got}",
                    cap, time.monotonic()))
        name = f"{spec['id']}-{index:02d}"
        started = time.monotonic()
        try:
            result = step_fn(step)
            ok, action, assertion = result[:3]
            delivery = result[3] if len(result) > 3 else None
            geometry = result[4] if len(result) > 4 else None
            walk = result[5] if len(result) > 5 else None
            # Before the frame: a step that handed the display to
            # another process gets one chance to bring it home, and
            # the capture then shows Cura rather than the intruder.
            foreground = foreground_guard()
            ok, assertion = _foreground_verdict(foreground, ok, assertion)
            _FRAME_OUTLINE[0] = geometry
            capture = shot(name)
            _FRAME_OUTLINE[0] = None
            steps.append((name, action, assertion, ok, capture))
            EVIDENCE.append(_evidence_entry(spec, index, step, name, ok, action,
                                            assertion, capture, started, delivery,
                                            geometry, walk, foreground))
        except Exception as exc:
            capture = shot(name)
            steps.append((name, f"{spec['name']}: {step.get('op')}",
                          f"step error: {exc!r}", False, capture))
            EVIDENCE.append(_evidence_entry(spec, index, step, name, False,
                                            f"{spec['name']}: {step.get('op')}",
                                            f"step error: {exc!r}", capture, started))
    end_sample = frames_probe("end", spec["id"])
    # The liveness verdict, folded into the scenario's own steps: a
    # renderer that stopped presenting FAILS the scenario in both
    # capture modes, because a scenario whose every assertion was
    # answered over a window that had stopped painting is not a pass.
    records = liveness_outcome([start_sample, end_sample])
    if records and records[0]["outcome"] == LIVENESS_STALLED:
        record = records[0]
        name = f"{spec['id']}-zz-frames"
        action = ("the presentation probe: the render request after the "
                  "scenario's last step")
        assertion = record["reason"]
        # A still of the stalled window is the one picture worth taking
        # on a failing scenario — and there is none to take on a leg
        # that captures nothing, where shot() answers (None, None).
        capture = shot(name)
        steps.append((name, action, assertion, False, capture))
        # Index -4: the negative block is the harness's own steps, not a
        # spec step, and the classification reads it as a diagnostic.
        EVIDENCE.append(_evidence_entry(spec, -4, {"op": "frames_probe"}, name,
                                        False, action, assertion, capture,
                                        time.monotonic()))
    return steps

# ─── Real-printer read-only mode (TESTING.md §2.5) ───────────────
# The real host is a live printer: a live print must never
# be touched. Only the r-group's real_safe scenarios run, and every
# step outside the read-only allowlist is refused — recorded in the
# gallery as a failure, never a command reaching the printer.
REAL_SAFE_SLOTS = {
    # Client-UI state only — nothing in this set sends anything.
    "reconnect", "refreshAll", "refreshWebcams", "selectWebcam",
    "setShowProbePoints", "setBedMeshPreviewVisible",
    "setSectionExpanded", "setConsoleExpanded", "setStatusCollapsed",
    "setInfoCollapsed", "setControlsCollapsed", "setConsoleHeight",
    "clearConsoleHistory",
}

# Real-mode click targets: a text click on a mutating control would
# drive the live printer. The allowlisted texts are stage/observation
# only (the panel's finding).
REAL_MUTATING_TEXT = ("pause", "resume", "cancel", "start", "restart",
                      "turn on", "turn off", "emergency", "jog", "home",
                      "extrude", "delete", "rename", "upload", "print")
# The ratcheting contract: both allowlists are pinned exactly in
# tests/harness/test_harness_runner.py. They may only shrink, and any
# change moves the pin and the decision record in the same commit.
# Every input verb is denied by default in real mode — none of the
# click/key/emit verbs appear here, and a new one must not without a
# ruling.
REAL_SAFE_OPS = {"click_stage", "click_text", "model_read",
                 "wait_model", "assert_model", "exec_slot", "dwell",
                 "rect_of", "assert_aligned", "assert_rendered",
                 "wait_rendered", "wait_rect", "dump_visible",
                 "resize_window", "census"}


def real_dwell(step):
    """The real dwell: sample a read-only GET's latency over the
    window and watch the model's progress across it. GETs only, on
    /printer/* and /server/* routes — nothing that commands."""
    import urllib.request
    path = step.get("path", "/printer/info")
    if not (path.startswith("/printer/") or path.startswith("/server/")):
        return (False, "real dwell",
                f"REFUSED: dwell path {path!r} is not a read-only route")
    minutes = float(step.get("minutes", 5))
    url = os.environ.get("REAL_URL", "").rstrip("/")
    api_key = os.environ.get("REAL_API_KEY", "")
    if not url:
        return (False, "real dwell", "REAL_URL is not set")
    def progress():
        value = exec_rpc(MODEL_READ_TEMPLATE.replace(
            "PROP_PLACEHOLDER", json.dumps("monitorProgress")))
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    start_progress = progress()
    deadline = time.time() + minutes * 60
    samples = []
    mid_shot = False
    while time.time() < deadline:
        t0 = time.time()
        try:
            req = urllib.request.Request(url + path)
            if api_key:
                req.add_header("X-Api-Key", api_key)
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read(1)
            samples.append((time.time() - t0) * 1000)
        except Exception as exc:
            samples.append(f"error:{exc!r}")
        if not mid_shot and time.time() >= deadline - minutes * 60 / 2:
            shot("r4-mid")
            mid_shot = True
        time.sleep(5)
    shot("r4-end")
    end_progress = progress()
    numeric = [s for s in samples if isinstance(s, float)]
    if numeric:
        profile = (f"{len(numeric)} samples over {minutes:.0f} min: "
                   f"min {min(numeric):.1f} ms, avg "
                   f"{sum(numeric) / len(numeric):.1f} ms, "
                   f"max {max(numeric):.1f} ms")
    else:
        profile = f"{len(samples)} samples, none measured"
    errors = sum(1 for s in samples if isinstance(s, str))
    return (len(numeric) > 0,
            f"the real dwell sampled GET {path} over {minutes:.0f} minutes",
            f"{profile}; {errors} error samples; "
            f"print progress {start_progress} -> {end_progress}")


def real_step(step):
    """One step of a real_safe scenario. Anything outside the
    read-only allowlist is a hard refusal, gallery-recorded."""
    op = step["op"]
    if op == "dwell":
        return real_dwell(step)
    if op not in REAL_SAFE_OPS:
        return (False, f"{op} (real mode)",
                f"REFUSED: op {op!r} is not in the read-only allowlist")
    if op == "exec_slot" and step["slot"] not in REAL_SAFE_SLOTS:
        return (False, f"{step['slot']} (real mode)",
                f"REFUSED: slot {step['slot']!r} is not read-only")
    if op == "click_text" and any(word in str(step.get("text") or "").lower()
                                  for word in REAL_MUTATING_TEXT):
        return (False, f"click_text {step['text']!r} (real mode)",
                "REFUSED: the label names a mutating control")
    return suite_step(step)


def real_run():
    """The read-only dwell against a real printer. The seeded
    machine record points at REAL_URL (ui_test.sh seeds it at
    runtime, never in the repo); only the r-group runs, under the
    real_step refusals. Observation only — no commands, no restarts,
    no print starts."""
    import scenarios
    specs = [spec for spec in scenarios.SCENARIOS if spec.get("group") == "real"]
    for spec in specs:
        if not spec.get("real_safe"):
            print(f"refusing real-mode scenario {spec.get('id')}: not real_safe")
            return 1
    if not specs:
        print("no real-printer scenarios")
        return 1
    os.makedirs(RUN_DIR, exist_ok=True)
    video_path = os.path.join(RUN_DIR, "real.mp4")
    video_path = os.path.join(RUN_DIR, "real.mp4")
    video = start_recorder(video_path, framerate=15)
    steps = []
    try:
        hello = rpc({"id": 1, "cmd": "hello"})
        steps.append(boot_step(hello))
        gate = ensure_ready()
        steps.append(("01-gate", "boot gate: active machine present, discovery chain live",
                      "gate clear", gate, shot("01-gate")))
        wait_stage("PrepareStage", timeout_ms=60000)
        for spec in specs:
            steps.extend(suite_scenario(spec, step_fn=real_step))
    finally:
        stop_recorder(video)
    title = "Real-printer read-only — observation, no commands"
    write_gallery(steps, False, title, video=video_path)
    print(f"gallery: {RUN_DIR}/index.html")
    return _verdict(steps)



def suite_step(step):
    op = step["op"]
    only = step.get("version_only")
    if only is not None and not any(
            os.environ.get("CURA_VERSION", "").startswith(prefix) for prefix in only):
        # A step that exists only on some versions (s4's 5.11 stage
        # round-trip): recorded as skipped on the others, never silent.
        return True, "skipped: step applies to other Cura versions", "version_only", None
    if op == "click_stage":
        reply = click_stage(step["stage"])
        result = wait_stage(step["stage"], timeout_ms=20000)
        if result.get("ok"):
            note = f"stage == {step['stage']}"
        elif not reply.get("ok"):
            note = f"stage == {step['stage']} [qclick failed: {reply.get('error')}]"
        else:
            note = ("stage == {stage} [aim={aim} hit={hit} size={size} "
                    "clicked={clicked} stage_after={stage_after}]").format(
                stage=step["stage"], aim=reply.get("aim"), hit=reply.get("hit"),
                size=reply.get("size"), clicked=reply.get("clicked"),
                stage_after=reply.get("stage"))
        landed = result.get("ok") is True
        # The stage transition itself is the delivery's acceptance
        # evidence — the qclick reply carries the events and the hit.
        delivery = {"accepted": landed, "events": reply.get("events"),
                    "hit": reply.get("hit")}
        return landed, f"real click on Cura's own {step['stage']} header button", note, delivery
    if op == "click_text":
        reply = rpc({"id": 1, "cmd": "click_text", "text": step["text"],
                     "button": step.get("button", "left")})
        time.sleep(0.6)
        delivery = reply.get("delivery") or {}
        landed = bool(reply.get("ok") and delivery.get("accepted"))
        note = (f"the press was accepted by {_delivery_name(delivery.get('grabber'))}") if landed \
            else f"the press was NOT accepted [delivery={delivery!r}]"
        return landed, f"real click on the rendered '{step['text']}'", note, delivery, \
            reply.get("geometry"), reply.get("walk")
    if op == "deliver_click":
        request = {"id": 1, "cmd": "deliver_click"}
        if "objectName" in step:
            request["objectName"] = step["objectName"]
        elif "objectName_state" in step:
            slot, key = step["objectName_state"]
            request["objectName"] = SUITE_STATE["stash"][slot][key]
        elif "text" in step:
            request["text"] = step["text"]
        else:
            return False, "deliver_click", "step names no target"
        reply = rpc(request)
        time.sleep(0.6)
        delivery = reply.get("delivery") or {}
        if not reply.get("ok"):
            # The driver refuses an aim that provably cannot land
            # (outside the window, or clipped out by a pane) — the
            # honest failure a silent empty-space click never gave.
            return False, f"a real press/release on {step.get('objectName') or step.get('text')}", \
                f"refused before the press: {reply.get('error')}", None, reply.get("geometry"), reply.get("walk")
        if step.get("expect") == "not_accepted":
            # The negative half of the proof: the target RESOLVED (a
            # real item is under the aim) but no item accepted the
            # press — a disabled control refuses the click.
            refused = bool(reply.get("ok") and not delivery.get("accepted") and delivery.get("hit"))
            note = (f"the press was refused by {_delivery_name(delivery.get('hit'))}") if refused \
                else f"unexpected delivery [delivery={delivery!r}]"
            return refused, f"a refused press/release on {step.get('objectName') or step.get('text')}", note, delivery, \
                reply.get("geometry"), reply.get("walk")
        landed = bool(reply.get("ok") and delivery.get("accepted"))
        note = (f"the press was accepted by {_delivery_name(delivery.get('grabber'))}") if landed \
            else f"the press was NOT accepted [delivery={delivery!r}]"
        return landed, f"a real press/release on {step.get('objectName') or step.get('text')}", note, delivery, \
            reply.get("geometry"), reply.get("walk")
    if op == "key_press":
        reply = rpc({"id": 1, "cmd": "key_press", "key": step["key"]})
        time.sleep(0.4)
        sent = bool(reply.get("ok") and reply.get("sent"))
        return sent, f"the {step['key']} key", "sent", {"sent": sent}
    if op == "scroll_into_view":
        request = {"id": 1, "cmd": "scroll_into_view"}
        if "objectName" in step:
            request["objectName"] = step["objectName"]
        elif "objectName_state" in step:
            slot, key = step["objectName_state"]
            request["objectName"] = SUITE_STATE["stash"][slot][key]
        elif "text" in step:
            request["text"] = step["text"]
        else:
            return False, "scroll_into_view", "step names no target"
        reply = rpc(request)
        time.sleep(0.3)
        contained = bool(reply.get("ok") and reply.get("contained"))
        note = (f"contained ({reply.get('after')} within viewport {reply.get('viewport')})"
                if contained else f"NOT contained [reply={reply!r}]")
        return contained, f"scrolled {step.get('objectName') or step.get('text')} into view", note, None, \
            reply.get("geometry"), reply.get("walk")
    if op == "emit_click":
        code = EMIT_TEMPLATE.replace("TEXT_PLACEHOLDER", json.dumps(step["text"]))
        reply = exec_rpc(code)
        time.sleep(0.6)
        return bool(reply.get("emitted")), f"the '{step['text']}' button's clicked signal (the QML path a real click drives)", "emitted"
    if op == "sim_set":
        reply = sim_http("/harness/scenario", "POST", step["state"])
        unknown = reply.get("unknown") or []
        time.sleep(1.5)
        return (not unknown, "the simulator's state changed to %s" % json.dumps(step["state"])[:60],
                "applied" if not unknown else f"REFUSED: unknown keys {unknown}")
    if op == "sim_arm":
        reply = sim_http("/harness/scenario", "POST", step["arms"])
        unknown = reply.get("unknown") or []
        return (not unknown, "the simulator armed %s" % json.dumps(step["arms"])[:60],
                "armed" if not unknown else f"REFUSED: unknown arms {unknown}")
    if op == "sim_klippy":
        sim_http("/harness/klippy_restart", "POST", {})
        return True, "the simulator broadcast klippy_ready", "broadcast"
    if op == "sim_ledger":
        # The one-shot-read footgun, fixed: the declared budget now
        # MEANS the wait window, and `field` actually filters (the
        # panel's finding — 30 steps declared a budget that was never
        # read, 16 declared an unread field).
        needle = str(step.get("needle") or "")
        method = step.get("method")
        field = str(step.get("field") or "")
        def matched_count():
            entries = sim_http("/ledger").get("entries", ())
            matched = [entry for entry in entries
                       if ((not field and (needle in str(entry.get("path") or "")
                                           or needle in str(entry.get("method") or "")))
                           or (field and needle in str(entry.get(field) or "")))
                       and (method is None or method == str(entry.get("method") or ""))]
            return matched
        matched = wait_for(lambda: (matched_count() or None),
                           float(step.get("budget", 15)), 1.0)
        if not matched:
            matched = matched_count()
        expected = int(step.get("min", 1))
        ceiling = step.get("max")
        detail = "; ".join(f"{e['method']} {e['path']} {e['ms']:.0f}ms"
                           for e in matched[-6:])
        if ceiling is not None:
            ok = expected <= len(matched) <= int(ceiling)
            bound = f" ({expected}..{ceiling})"
        else:
            ok = len(matched) >= expected
            bound = f" (>= {expected})"
        return ok, "the peer's ledger counted requests", \
            f"{needle!r}: {len(matched)}{bound} [{detail}]"
    if op == "model_read":
        value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        SUITE_STATE["model"][step["prop"]] = value
        return True, f"the model's {step['prop']} read", f"{value!r}"
    if op == "wait_model":
        def check():
            value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
            if step.get("contains") is not None:
                return str(step["contains"]).lower() in str(value).lower()
            if "value" in step:
                return value == step.get("value") or (isinstance(step.get("value"), list) and value in step["value"])
            # No expectation given: the property must be populated.
            return value not in (None, "", [], {})
        ok = bool(wait_for(check, float(step.get("budget", 15)), 1.0))
        value = exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        if step.get("contains") is not None:
            wanted = step.get("contains")
        elif "value" in step:
            wanted = step.get("value")
        else:
            wanted = "a populated value"
        return ok, f"the model's {step['prop']} matched {wanted!r}", f"now {value!r}"
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
        # until the EXPECTATION matches or the budget passes. The
        # check returns the MATCH, never the raw value: a truthy
        # stale value (speed 100 while the sim moved to 137) used to
        # short-circuit the wait and read once (the re-verify's
        # c2-05/06 race on 5.7/5.8).
        def read():
            return exec_rpc(MODEL_READ_TEMPLATE.replace("PROP_PLACEHOLDER", json.dumps(step["prop"])))
        def check():
            value = read()
            if step.get("contains") is not None:
                return str(step["contains"]).lower() in str(value).lower()
            if "value" in step:
                return value == step.get("value")
            # No expectation given: the property must be populated (a
            # False or 0 still counts as present).
            return value not in (None, "", [], {})
        ok = bool(wait_for(check, float(step.get("budget", 15)), 0.5))
        value = read()
        if step.get("contains") is not None:
            return ok, f"the model's {step['prop']} contains {step.get('contains')!r}", f"read {value!r}"
        if "value" in step:
            return ok, f"the model's {step['prop']} equals {step.get('value')!r}", f"read {value!r}"
        return ok, f"the model's {step['prop']} is populated", f"read {value!r}"
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
        aim = str(reply.get("aim") or "clicked")
        label = "a real click on" if "emit" not in aim else "the clicked signal of"
        return bool(reply.get("ok")), f"{label} {step['button']} ({aim})", "clicked", None, \
            reply.get("geometry"), reply.get("walk")
    if op == "click_item":
        reply = rpc({"id": 1, "cmd": "click_item", "objectName": step["objectName"]})
        time.sleep(1.5)
        aim = str(reply.get("aim") or "clicked")
        label = "a real click on" if "emit" not in aim else "the clicked signal of"
        return bool(reply.get("ok")), f"{label} {step['objectName']} ({aim})", "clicked", None, \
            reply.get("geometry"), reply.get("walk")
    if op == "item_disabled":
        code = ITEM_STATE_TEMPLATE.replace("NAME_PLACEHOLDER", json.dumps(step["objectName"]))
        reply = exec_rpc(code)
        return reply.get("enabled") is False, f"{step['objectName']} disabled", f"enabled={reply.get('enabled')}"
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
        # A local gcode file for the upload flow — the spec builds the
        # path from SCRATCH_DIR, so it resolves on both sides here.
        path = step["path"]
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("; scenario upload fixture\nG28\nM105\n")
            return True, f"the upload fixture written to {path}", "written"
        except OSError as exc:
            return False, f"the upload fixture written to {path}", f"OSError: {exc}"
    if op == "rect_of":
        # Read an item's rect into the suite state, addressed by
        # objectName, rendered text, or class name (Cura-native).
        ref = {k: step[k] for k in ("objectName", "text", "className") if k in step}
        key = next(iter(ref.values()))
        reply = rpc({"id": 1, "cmd": "rect", **ref})
        if not reply.get("ok") or "rect" not in reply:
            return (False, f"the rect of {key}",
                    f"driver: {reply.get('error', reply)}")
        rect = reply["rect"]
        SUITE_STATE["rect"][key] = rect
        geometry = [rect["x"], rect["y"], rect["w"], rect["h"]]
        return True, f"the rect of {key} ({reply.get('found')})", f"{rect}", None, \
            geometry, reply.get("walk")

    if op == "assert_aligned":
        # Geometry pins: centre alignment on one axis, containment,
        # or non-overlap. All read the live rects — the rendered
        # layout, not the model's opinion of it.
        def resolve(ref):
            keys = ("objectName", "text", "className", "window")
            payload = {k: ref[k] for k in keys if k in ref}
            key = next(iter(ref.values()))
            reply = rpc({"id": 1, "cmd": "rect", **payload})
            if not reply.get("ok") or "rect" not in reply:
                raise RuntimeError(f"rect of {key}: {reply.get('error', reply)}")
            return key, reply["rect"]
        if "symmetric_margins" in step:
            left_key, a = resolve(step["symmetric_margins"]["left"])
            right_key, b = resolve(step["symmetric_margins"]["right"])
            _win_key, win = resolve({"window": True})
            left_gap = a["x"] - win["x"]
            right_gap = (win["x"] + win["w"]) - (b["x"] + b["w"])
            tol = float(step.get("tol", 6))
            return (abs(left_gap - right_gap) <= tol,
                    f"{left_key}'s left gap mirrors {right_key}'s right gap",
                    f"left {left_gap}px vs right {right_gap}px (tol {tol}); a={a} b={b}")
        item_key, a = resolve(step["item"])
        if "no_overlap" in step:
            other_key, b = resolve(step["no_overlap"])
            overlap = not (a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or
                           a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
            return (not overlap, f"{item_key} must not overlap {other_key}",
                    f"a={a} b={b} {'OVERLAP' if overlap else 'clear'}")
        if "within" in step:
            other_key, b = resolve(step["within"])
            inside = (a["x"] >= b["x"] and a["y"] >= b["y"] and
                      a["x"] + a["w"] <= b["x"] + b["w"] and
                      a["y"] + a["h"] <= b["y"] + b["h"])
            return (inside, f"{item_key} sits within {other_key}",
                    f"a={a} b={b} {'inside' if inside else 'OUTSIDE'}")
        other_key, b = resolve(step["anchor"])
        axis = step.get("axis", "center_y")
        tol = float(step.get("tol", 5))
        if axis == "center_y":
            gap = abs((a["y"] + a["h"] / 2) - (b["y"] + b["h"] / 2))
        else:
            gap = abs((a["x"] + a["w"] / 2) - (b["x"] + b["w"] / 2))
        return (gap <= tol, f"{item_key} and {other_key} share {axis}",
                f"gap {gap:.1f}px (tol {tol}); a={a} b={b}")

    if op == "assert_rendered":
        reply = rpc({"id": 1, "cmd": "text", "objectName": step["objectName"],
                     "all": bool(step.get("any"))})
        if not reply.get("ok"):
            return (False, f"the rendered text of {step['objectName']}",
                    f"driver: {reply.get('error')}")
        if step.get("any"):
            rendered = "; ".join(reply.get("texts") or [])
            needle = step.get("contains")
            ok = needle is not None and needle in rendered
            return (ok, f"the rendered text of {step['objectName']} (any instance)",
                    f"now {rendered[:160]!r}")
        rendered = reply.get("text") or ""
        if step.get("contains") is not None:
            ok = step["contains"] in rendered
        elif step.get("not_contains") is not None:
            ok = step["not_contains"] not in rendered
        elif step.get("equals") is not None:
            ok = rendered == step["equals"]
        else:
            ok = bool(rendered.strip())
        return (ok, f"the rendered text of {step['objectName']}",
                f"now {rendered!r}")

    if op == "wait_rendered":
        # The rendered-follows-model probe: the QML label's actual
        # text must follow a push — the model being right is not
        # enough (the pause-restyle bug class).
        def check():
            reply = rpc({"id": 1, "cmd": "text", "objectName": step["objectName"],
                         "all": bool(step.get("any"))})
            if not reply.get("ok"):
                return False
            if step.get("any"):
                rendered = "; ".join(reply.get("texts") or [])
                return step.get("contains", "") in rendered
            rendered = reply.get("text") or ""
            if step.get("contains") is not None:
                return step["contains"] in rendered
            if step.get("not_contains") is not None:
                return step["not_contains"] not in rendered
            if step.get("equals") is not None:
                return rendered == step["equals"]
            return bool(rendered.strip())
        ok = bool(wait_for(check, float(step.get("budget", 15)), 1.0))
        reply = rpc({"id": 1, "cmd": "text", "objectName": step["objectName"],
                     "all": bool(step.get("any"))})
        if step.get("any"):
            rendered = "; ".join(reply.get("texts") or []) if reply.get("ok") else reply.get("error")
        else:
            rendered = reply.get("text") if reply.get("ok") else reply.get("error")
        return (ok, f"the rendered text of {step['objectName']} followed the push",
                f"now {str(rendered)[:160]!r}")

    if op == "wait_rect":
        # An item's presence in the rendered tree — the collapse and
        # resize scenarios wait on this. An ABSENT wait that never
        # observed the item at all is labeled: "absent" must mean
        # "the product hid it", not "the probe never resolved it"
        # (the panel's finding).
        ref = {}
        for k in ("objectName", "text", "className"):
            if k in step:
                ref[k] = step[k]
            elif f"{k}_state" in step:
                slot, key = step[f"{k}_state"]
                ref[k] = SUITE_STATE["stash"][slot][key]
        key = next(iter(ref.values()))
        observed = bool(SUITE_STATE["rect"].get(("seen", key)))

        def check():
            reply = rpc({"id": 1, "cmd": "rect", **ref})
            if reply.get("ok"):
                SUITE_STATE["rect"][("seen", key)] = True
            if step.get("absent"):
                return not reply.get("ok")
            return reply.get("ok")
        ok = bool(wait_for(check, float(step.get("budget", 15)),
                           float(step.get("poll", 1.0))))
        reply = rpc({"id": 1, "cmd": "rect", **ref})
        now = "absent" if not reply.get("ok") else reply["rect"]
        geometry = None
        view_note = ""
        if reply.get("ok") and isinstance(reply.get("rect"), dict):
            rect = reply["rect"]
            geometry = [rect["x"], rect["y"], rect["w"], rect["h"]]
            # Presence in the tree is not presence on screen: an item
            # scrolled out of its pane still reports a rect (mapToScene
            # ignores clipping). Say so rather than let "entered the
            # rendered tree" imply the control is reachable.
            if rect.get("in_view") is False:
                view_note = " · NOT in view (a press at its centre would land on empty space)"
        seen_note = ("observed earlier" if (observed or SUITE_STATE["rect"].get(("seen", key)))
                     else "never observed in this scenario")
        return (ok, f"{key} {'left the rendered tree' if step.get('absent') else 'entered the rendered tree'}",
                f"now {now} ({seen_note}){view_note}", None, geometry, reply.get("walk"))

    if op == "census":
        # The data-render census: every data class present in the
        # snapshot must map to a rendered control. A fixed probe (the
        # code lives in the suite, never in caller steps) so the
        # real-printer mode can allow it while arbitrary exec stays
        # refused.
        import scenarios as _scenarios
        reply = exec_rpc(_scenarios.CENSUS_PROBE, raise_on_error=True)
        checks = reply.get("checks", {})
        complete = bool(reply.get("complete"))
        detail = "; ".join(f"{name}:{'ok' if ok else 'MISSING'}"
                           for name, ok in checks.items())
        return (complete, "the data-render census (every data class renders its control)",
                f"{'complete' if complete else 'INCOMPLETE'}: {detail}")

    if op == "dump_visible":
        # A diagnostic: every visible item matching the needle, with
        # geometry — the calibration evidence for matcher choices.
        rows = rpc({"id": 3, "cmd": "visible"}).get("items", [])
        needle = str(step.get("needle") or "").lower()
        region = step.get("region")
        if region:
            x0, y0, x1, y1 = region
            rows = [r for r in rows
                    if r["x"] < x1 and r["y"] < y1 and r["x"] + r["w"] > x0
                    and r["y"] + r["h"] > y0]
        hits = [r for r in rows
                if needle in (r["class"] + r["name"] + r["text"]).lower()]
        cap = 40 if region else 12
        brief = "; ".join(f"{r['class']}|{r['name']}|{r['text'][:20]!r}"
                          f"@{r['x']},{r['y']} {r['w']}x{r['h']}"
                          for r in hits[:cap])
        return True, f"visible items matching {needle!r}", brief or "no matches"

    if op == "resize_window":
        # A "min" axis is the application's own floor, read off the
        # live window by the driver, so one spec asks every platform
        # for the smallest size its users can drag to. Nothing forces
        # past that floor.
        asked = [step["w"], step["h"]]
        reply = rpc({"id": 1, "cmd": "resize", "w": asked[0], "h": asked[1]})
        if not reply.get("ok"):
            return (False, f"resize to {asked[0]}x{asked[1]}", f"driver: {reply.get('error')}")
        got = reply.get("size") or [0, 0]
        note = f"actual {got}"
        if reply.get("minimum"):
            # Recorded on every resize so the floor each platform
            # reports stays visible (macOS and Windows 1040x624, Xvfb
            # 880x528) — a target below it is a geometry nobody can
            # reach, and the note is where that shows.
            note += f" · window minimum {reply['minimum']}"
        if "min" in asked:
            note += f" · asked {asked[0]}x{asked[1]}"
        return True, f"the window resized to {got[0]}x{got[1]}", note

    if op == "sim_set_current_print":
        # The running-job state the load needs, with the sim's REAL
        # gcode size (gate #2's recipe — a size mismatch aborts the
        # load in suite conditions).
        state = sim_http("/harness/state")["result"]
        listing = sim_http("/server/files/directory?path=gcodes&extended=true")
        gcode_size = listing["result"]["files"][0]["size"]
        sim_http("/harness/scenario", "POST", {
            "print_stats": {**state["print_stats"], "state": "printing",
                            "filename": "scenario1.gcode"},
            "virtual_sdcard": {**state["virtual_sdcard"], "is_active": True,
                               "progress": 0.5, "file_size": gcode_size}})
        return True, "the simulator's running job (the real gcode size)", \
            f"file_size {gcode_size}"

    if op == "assert_rect_change":
        # Compare against the rect_of cache: the pane-collapse pins
        # (the pane must actually shrink/grow in the rendered tree).
        key = step["objectName"]
        direction = step.get("direction", "shrunk")
        axis = step.get("axis", "w")
        by = float(step.get("by", 20))

        def check():
            reply = rpc({"id": 1, "cmd": "rect", "objectName": key})
            if not reply.get("ok"):
                return False
            rect = reply["rect"]
            before = SUITE_STATE["rect"].get(key)
            if before is None:
                return False
            delta = rect[axis] - before[axis]
            return delta <= -by if direction == "shrunk" else delta >= by
        ok = bool(wait_for(check, float(step.get("budget", 15)), 1.0))
        reply = rpc({"id": 1, "cmd": "rect", "objectName": key})
        now = reply.get("rect") if reply.get("ok") else "absent"
        before = SUITE_STATE["rect"].get(key)
        if ok and isinstance(now, dict):
            # Re-anchor the cache on the new state so the next pin
            # compares against THIS transition's result.
            SUITE_STATE["rect"][key] = now
        return (ok, f"{key} {direction} by at least {by:.0f}px on {axis}",
                f"{before} -> {now}")

    if op == "exec_code":
        # An inline driver probe — the settings dialog's opener uses
        # the machine-action registry, which no slot exposes.
        # raise_on_error: a driver-side exception must surface as a
        # step error — exec_rpc's silent {} masked probe failures as
        # "the probe saw nothing" (the z-group calibration lesson).
        try:
            reply = exec_rpc(step["code"], raise_on_error=True)
        except RuntimeError as exc:
            return (False, "the driver executed the inline probe",
                    f"driver error: {exc}")
        if reply.get("error"):
            return (False, "the driver executed the inline probe",
                    f"driver: {reply['error']}")
        if step.get("stash"):
            # The probe's result feeds later steps (the _state keys):
            # a scenario can address a surface it read at runtime
            # instead of pinning version-bound names or prose.
            # exec_rpc already parsed the result — the reply IS the
            # probe's dict (looking for a "result" key here stashed
            # {} and starved the later steps).
            SUITE_STATE["stash"][step["stash"]] = reply if isinstance(reply, dict) else {}
        return True, "the driver executed the inline probe", f"{reply}"

    if op == "insert_model":
        # Cura's own reader chain inserts the suite's test model (the
        # Voron cube) — the path the drag-drop drives.
        code = ("from UM.Application import Application\n"
                "from PyQt6.QtCore import QUrl\n"
                "app = Application.getInstance()\n"
                "result = {}\n"
                "try:\n"
                "    app.readLocalFile(QUrl.fromLocalFile("
                + json.dumps(os.path.join(SCRATCH_DIR, "models", "voron_cube.stl")) + "),"
                " add_to_recent_files=False)\n"
                "    result[\"read\"] = True\n"
                "except Exception as exc:\n"
                "    result[\"error\"] = repr(exc)")
        reply = exec_rpc(code, raise_on_error=True)
        if reply.get("error"):
            return (False, "the Voron cube inserted through Cura's reader chain",
                    f"driver: {reply['error']}")
        return True, "the Voron cube inserted through Cura's reader chain", "read"

    if op == "slice_scene":
        # The real slice: the backend's engine slices the scene and
        # the layer job feeds the SimulationView (needs the Preview
        # stage active — Cura's own auto-switch does not fire in the
        # harness).
        reply = exec_rpc("from UM.Application import Application\n"
                         "app = Application.getInstance()\n"
                         "result = {}\n"
                         "try:\n"
                         "    app.getBackend().forceSlice()\n"
                         "    result[\"slice\"] = True\n"
                         "except Exception as exc:\n"
                         "    result[\"error\"] = repr(exc)",
                         raise_on_error=True)
        if reply.get("error"):
            return (False, "the scene sliced by the engine",
                    f"driver: {reply['error']}")
        return True, "the scene sliced by the engine", "sliced"

    if op == "add_post_script":
        # Activate a post-processing script through the plugin's own
        # manager — Cura's save-area `</>` button only renders while a
        # script is active (the insert-a-pause flow).
        reply = exec_rpc("from UM.Application import Application\napp = Application.getInstance()\nresult = {}\nplugin = app.getPluginRegistry().getPluginObject(\"PostProcessingPlugin\")\ntry:\n    plugin.addScriptToList(\"PauseAtHeight\")\n    result[\"added\"] = True\nexcept Exception as exc:\n    result[\"error\"] = repr(exc)",
                         raise_on_error=True)
        if reply.get("error"):
            return (False, "the post-processing script activated",
                    f"driver: {reply['error']}")
        return True, "the post-processing script activated", "active"

    if op == "gap_between":
        # The vertical gap between two stacked items: above's bottom
        # edge to below's top edge, within [min, max]. With
        # edges=bottoms: above's bottom edge to below's bottom edge
        # (the </> button must sit ON the card's bottom line — the
        # live report).
        def resolve(ref):
            keys = ("objectName", "text", "className", "window")
            payload = {k: ref[k] for k in keys if k in ref}
            key = next(iter(ref.values()))
            reply = rpc({"id": 1, "cmd": "rect", **payload})
            if not reply.get("ok") or "rect" not in reply:
                raise RuntimeError(f"rect of {key}: {reply.get('error', reply)}")
            return key, reply["rect"]
        a_key, a = resolve(step["above"])
        b_key, b = resolve(step["below"])
        if step.get("edges") == "bottoms":
            gap = (b["y"] + b["h"]) - (a["y"] + a["h"])
        else:
            gap = b["y"] - (a["y"] + a["h"])
        lo = float(step.get("min", -1000))
        hi = float(step.get("max", 1000))
        return (lo <= gap <= hi,
                f"{a_key} and {b_key} hold their vertical gap",
                f"gap {gap}px (wanted [{lo}, {hi}]); a={a} b={b}")

    if op == "assert_exec":
        reply = exec_rpc(step["code"])
        if reply.get("error"):
            return (False, "the driver's inline probe", f"driver: {reply['error']}")
        rendered = json.dumps(reply, default=str)
        ok = step["contains"] in rendered if step.get("contains") is not None else True
        return (ok, "the driver's inline probe", rendered[:200])

    if op == "wait_exec":
        # Poll an inline probe until its result matches — the follow
        # state's transitions land through the presentation, not the
        # model's published properties.
        def check():
            reply = exec_rpc(step["code"])
            if reply.get("error"):
                return False
            rendered = json.dumps(reply, default=str)
            if step.get("contains") is not None:
                return step["contains"] in rendered
            if step.get("not_contains") is not None:
                return step["not_contains"] not in rendered
            return True
        ok = bool(wait_for(check, float(step.get("budget", 15)), 1.0))
        reply = exec_rpc(step["code"])
        rendered = json.dumps(reply, default=str) if reply.get("error") is None else reply.get("error")
        return (ok, "the driver's inline probe followed the change", str(rendered)[:200])

    if op == "wait_seconds":
        # A plain settle window — the follow's own 2s recovery lag
        # swallows changes right after a load or a view swap.
        time.sleep(float(step.get("seconds", 3)))
        return True, f"settled {step.get('seconds', 3)}s", "settled"

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
            # The field is a str-valued Enum; assigning a bare string
            # would bypass the coercion production callers always have,
            # and the apply path reads .value (the 5.7.0 sweep's find —
            # it failed every version, not just the floor).
            config.feed_mode = config.feed_mode.__class__(MODE_PLACEHOLDER)
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
    if mode == "firstinstall1":
        return first_install1()
    if mode == "firstinstall2":
        return first_install2()
    if mode in TWO_BOOT_MODES:
        # Both boots, driven from here: only a native host reaches this
        # (tools/ui_test.sh runs the container's pair itself), and
        # without it "firstinstall" fell through to scenario() below —
        # a different leg wearing the mode's name.
        return two_boot_run(mode)
    if mode == "migration1":
        return migration1()
    if mode == "migration2":
        return migration2()
    if mode == "scenario10":
        return scenario10()
    if mode == "scenario8":
        return scenario8()
    if mode == "scenario9":
        return scenario9()
    if mode == "suite":
        import scenarios  # noqa: F401 (the specs register)
        return suite_run(sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SCENARIO_GROUP", "b"))
    if mode == "real":
        return real_run()
    expect_fail = mode == "fail"
    return scenario(expect_fail)


if __name__ == "__main__":
    try:
        _rc = main()
    finally:
        # In a finally: a run that raised is the one whose log is worth
        # reading, and a native leg has nowhere else to keep it.
        harvest_cura_log(RUN_DIR)
    # The leg-level static check, after every recording is closed: a
    # green verdict over a screen that never moved is not a success.
    # Both run even when the scenario already failed — the recordings of
    # a failed leg are the ones whose verdicts get read afterwards, and
    # an `or` here would leave exactly those legs without the record.
    _static_rc = static_leg_report(RUN_DIR)
    sys.exit(_rc or _static_rc)
