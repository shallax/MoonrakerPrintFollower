"""The still-frame analysis: whether a leg's recording moved.

Every suite step reads the QML tree, so a leg whose screen froze can
still report every step green. This owner decodes a recording, measures
the longest near-identical stretch, decides whether an input step fell
inside it, and writes the verdict into the leg's own artifacts.

It takes the capture gate as an argument: whether a leg's screen is
judged at all is the leg's decision, not the measurement's.
"""
from __future__ import annotations

import json
import os
import subprocess

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
        # First-install and migration are boot/configuration probes. Their
        # tuple steps use RPC and disk reads, never screen input, so an
        # unchanged Prepare window is expected. Their evidence has no
        # timed steps; distinguish that known no-input shape from old or
        # broken evidence whose interaction timestamps are missing.
        if (data.get("mode") in {"firstinstall", "firstinstall1", "firstinstall2",
                                  "migration", "migration1", "migration2"}
                and not steps
                and data.get("classification", {}).get("steps", {}).get("ui-interaction") == 0):
            return []
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


def report(run_dir, capture, capture_reason):
    """Every recording a leg leaves, judged (the static-green ruling).

    The leg fails when one near-identical run covers most of a
    recording, whatever its steps reported. It runs in the runner's own
    exit path, so it covers every mode: the suite groups, the
    boot-only first-install and migration legs, and their second boots.
    A recording that cannot be decoded is left unjudged rather than
    called static. A leg that captures nothing is not judged at all:
    the static rule reads pictures, and there are none to read."""
    if not capture:
        print("ui_test: STATIC LEG — not judged: this leg captures nothing "
              f"({capture_reason or 'no reason recorded'})")
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


