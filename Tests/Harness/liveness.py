"""The presentation record and its verdict.

Every suite step reads the QML tree, so an application whose window
stopped painting still passes every assertion over a still screen. This
is the measurement that says whether the window was painting at all, and
it separates a stalled scene graph from a stalled capture.

The owner holds the samples, the sample vocabulary, the verdict and the
platform gating. It takes the transport it does not own — `probe()` is
handed the runner's rpc callable — and the capture gate as an argument,
because whether a measured stall is ACTED on is a leg's decision, never
part of the reading.
"""
from __future__ import annotations

# The presentation record (the static-green ruling): what the window
# was asked to present at each end of a scenario, and whether a frame
# answered. Every step reads the QML tree, so an app whose window
# stopped painting still passes every assertion over a still screen —
# this is the measurement that says whether the window was painting at
# all, and it separates a stalled scene graph from a stalled capture.
FRAME_PROBES = []

# The sample's own vocabulary. Only a stall is a claim about the app:
# everything the probe could not tell apart from a stall is named as
# unverified with the reason, because a measurement that could not be
# taken must not read as one that was.
LIVENESS_RENDERED = "rendered"
LIVENESS_STALLED = "stalled"
LIVENESS_UNVERIFIED = "unverified"

# The heartbeat's deadline, sent with every sample: the window is asked
# to present a change its scene graph cannot ignore, and the frame that
# makes due has this long to land — twice, because a software
# rasteriser's frame interval is close to the fixed settle this
# replaced and one missed frame is not a stopped renderer.
FRAME_HEARTBEAT_DEADLINE_MS = 1500


def _count(value):
    """A frame count, or None when the sample did not carry one: an
    absent count must never be read as zero, which is a stall."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _delivered(sample):
    """Whether a frame was delivered in this sample's own window."""
    return (_count(sample.get("gained")) or 0) > 0


def _saw_frames(sample):
    """Whether this sample knows of a frame at all — the cumulative
    count moved, now or earlier in the run. This is what separates a
    renderer that stopped from one that was never seen to paint."""
    return (_count(sample.get("swapped")) or 0) > 0 or _delivered(sample)


def _advanced(start, end):
    """Whether the window painted across the scenario: the count it
    carried at the scenario's start against the count at its end."""
    before, after = _count(start.get("swapped")), _count(end.get("swapped"))
    return before is not None and after is not None and after > before


def _shown(sample):
    """Whether the display can be expected to show this window: an
    explicit yes on both flags. A hidden or minimised window delivers
    no frame by design, so it must not be read as a freeze, and a flag
    that did not arrive must not be read as either."""
    return sample.get("visible") is True and sample.get("exposed") is True


def _heartbeat_of(sample):
    """The sample's heartbeat record, or None when it carried none: a
    sample without one made no frame due, and nothing is not a pass."""
    record = sample.get("heartbeat")
    return record if isinstance(record, dict) else None


def _answered(sample):
    """Whether a frame answered the forced scene change this sample
    made — the measurement the verdict rests on."""
    record = _heartbeat_of(sample)
    return bool(record and record.get("answered"))


def _landed(sample):
    """Whether the sample's change verifiably landed on a visible item
    in the window's own scene: a change that did not land made no frame
    due, so a miss against one says nothing about the renderer."""
    record = _heartbeat_of(sample)
    if not record or not record.get("asked"):
        return False
    return any(attempt.get("verified") and attempt.get("in_scene")
               and attempt.get("is_visible")
               for attempt in record.get("attempts") or [])


def _calibrated(sample, seen):
    """Whether this window's render loop has been observed presenting in
    this run: either a heartbeat of its own was answered earlier (the
    strong reading — the loop answered a change it was told about), or
    it has delivered frames, which the sample's own count carries. A
    window that is neither has never been seen to present anything, and
    a miss from it is not a freeze."""
    record = _heartbeat_of(sample)
    if record is not None and record.get("calibrated"):
        return True
    return bool(seen)


def _calibration_note(sample, seen):
    record = _heartbeat_of(sample)
    if record is not None and record.get("calibrated"):
        return f"it had already answered a heartbeat of its own ({record.get('window')})"
    if seen:
        return "it had painted earlier in this run"
    return "it has never been seen to present"


def _shape(sample):
    # What the sample saw, for a diagnostic that names the window it
    # judged rather than only the count.
    return (f"swapped={sample.get('swapped')} gained={sample.get('gained')} "
            f"visible={sample.get('visible')} exposed={sample.get('exposed')} "
            f"active={sample.get('active')} visibility={sample.get('visibility')} "
            f"state={sample.get('state')} platform={sample.get('platform')}")


def _span(start, end):
    # The pair a stall is read from: the baseline the scenario's own
    # advance is measured against, the window it was measured on, and
    # the closing sample's own counts.
    return (f"swapped={start.get('swapped')}->{end.get('swapped')} "
            f"gained={end.get('gained')} visible={end.get('visible')} "
            f"exposed={end.get('exposed')} active={end.get('active')} "
            f"visibility={end.get('visibility')} state={end.get('state')} "
            f"platform={end.get('platform')}")


def _beat(sample):
    """The heartbeat half of a diagnostic: what was asked of the window,
    what came back, and what it cost."""
    record = _heartbeat_of(sample)
    if record is None:
        return "no heartbeat rode with this sample"
    if not record.get("asked"):
        return f"the heartbeat was not placed: {record.get('reason')}"
    plot = " then ".join(
        f"{attempt.get('property')} {attempt.get('from')}->{attempt.get('to')} "
        f"swapped {attempt.get('swapped_before')}->{attempt.get('swapped_after')} "
        f"gained {attempt.get('gained')} in {attempt.get('waited_ms')}ms"
        for attempt in record.get("attempts") or [])
    return (f"{record.get('window')} beat {record.get('sequence')} "
            f"[{plot or 'no change landed'}] "
            f"deadline={record.get('deadline_ms')}ms")


def probe(rpc, phase, scenario_id):
    """One presentation sample, recorded.

    The window is told to present a change its scene graph cannot
    ignore, and the sample carries what that change was, whether it
    landed and whether a frame answered it. The frame counts ride
    beside it as diagnostics: a window with no reason to paint answers
    a passive render request with nothing while a healthy renderer sits
    behind it, so a count that did not move is not evidence of a
    freeze, and the heartbeat is."""
    sample = {"scenario": scenario_id, "phase": phase}
    try:
        reply = rpc({"id": 1, "cmd": "frames", "heartbeat": True,
                     "deadline_ms": FRAME_HEARTBEAT_DEADLINE_MS}, timeout=60)
    except Exception as exc:
        reply = {"ok": False, "error": repr(exc)}
    for key in ("swapped", "gained", "since", "exposed", "visible", "active",
                "visibility", "state", "platform", "error",
                "frame_signal", "fresh_window", "heartbeat"):
        if key in reply:
            sample[key] = reply[key]
    sample["ok"] = bool(reply.get("ok"))
    FRAME_PROBES.append(sample)
    # The request and its completion go into the leg's own log, not only
    # into the artifact: a red leg must say what the window was asked
    # for and what came back without a reader opening evidence.json.
    print(f"ui_test: heartbeat {scenario_id}/{phase}: {_beat(sample)} — "
          + ("a frame answered it" if _answered(sample) else "no frame answered it"),
          flush=True)
    return sample


def liveness_of(start, end, seen):
    """One scenario's outcome and the reason for it.

    `end` is the measurement: the window was told to present a change
    its scene graph cannot ignore, and either a frame answered it or
    none did. `start` is the baseline the scenario's own advance is read
    against, and `seen` says whether this window has been observed
    painting in this run — the reading that separates a freeze from a
    window never seen to present anything."""
    if end.get("frame_signal") is False:
        return (LIVENESS_UNVERIFIED,
                "the frame signal could not be attached to the window")
    if not end.get("ok"):
        detail = end.get("error")
        return (LIVENESS_UNVERIFIED, "the window did not answer the probe"
                + (f": {detail}" if detail else ""))
    if _count(end.get("swapped")) is None:
        return (LIVENESS_UNVERIFIED,
                "the probe answered without a frame count, so it measured nothing")
    if _heartbeat_of(end) is None:
        return (LIVENESS_UNVERIFIED,
                "the probe answered without a heartbeat, so no frame was made "
                f"due and a count that did not move says nothing ({_shape(end)})")
    if _answered(end):
        return (LIVENESS_RENDERED,
                f"the window answered the heartbeat that forced a repaint with "
                f"{end.get('gained')} frame(s) ({_beat(end)}; {_shape(end)})")
    # From here the heartbeat missed, and every gate below is what must
    # hold before that miss can be read as a renderer that stopped.
    if end.get("fresh_window") is True:
        # The counter attached to a window it had not been counting, so
        # this is that window's first count and the scenario's span was
        # read off the window it replaced: neither end is a reading of
        # this one, so it cannot carry a verdict.
        return (LIVENESS_UNVERIFIED,
                "the window was replaced for this sample, so the count has no "
                f"history to be read against ({_shape(end)})")
    if _advanced(start, end):
        return (LIVENESS_RENDERED,
                f"the window painted {_count(end['swapped']) - _count(start['swapped'])} "
                "frame(s) across the scenario, so the renderer was presenting; the "
                f"heartbeat that closed it gained none ({_span(start, end)})")
    if not _landed(end):
        return (LIVENESS_UNVERIFIED,
                "the heartbeat's change did not verifiably land on a visible item "
                f"in the window's own scene, so no frame was due from it "
                f"({_beat(end)})")
    if not _shown(end):
        return (LIVENESS_UNVERIFIED,
                "the window is not on the display, so no frame is due from it "
                f"({_shape(end)})")
    if _calibrated(end, seen):
        record = _heartbeat_of(end) or {}
        attempts = record.get("attempts") or []
        return (LIVENESS_STALLED,
                "the window is visible and exposed and answered no frame to "
                f"{len(attempts)} forced scene change(s) inside "
                f"{record.get('deadline_ms')}ms each, and painted nothing across "
                "the scenario, having been seen to present in this run "
                f"({_beat(end)}; {_calibration_note(end, seen)}; {_span(start, end)})")
    return (LIVENESS_UNVERIFIED,
            "no frame has been delivered on this platform in this run yet, so an "
            "initialising renderer and a platform that never emits the frame "
            f"signal look the same here ({_shape(end)})")


def gating(platform, capture):
    """Whether a measured stall FAILS the scenario here, and why.

    The measurement is the same in both capture modes; what differs is
    whether a leg may act on it. Linux and Windows legs read the screen
    beside the app, so a window that answered no forced repaint is a
    red. A leg that captures nothing has declared its own screen
    unjudgeable — the hosted macOS runner, whose software OpenGL stops
    presenting partway through a leg — and there a heartbeat failure is
    a REPORT-ONLY diagnostic: kept in the evidence, announced in the
    log, and never read as renderer coverage the platform has not
    demonstrated. HARNESS_CAPTURE=on restores the pictures on that leg,
    and with them the verdict."""
    if not capture:
        return False, ("report-only: this leg captures nothing and has declared "
                       f"its screen unjudgeable (platform={platform}), so a missed "
                       "heartbeat is a diagnostic here and no renderer coverage "
                       "is claimed for it")
    return True, "judged"


def outcome_records(samples, capture):
    """Every scenario's presentation outcome, in scenario order.

    The verdict reads a scenario's two samples together: the END one
    carries the heartbeat — the change the window was told to present —
    and the START one is the baseline the scenario's own advance is read
    against. A frame answering the heartbeat, or frames delivered across
    the span, mean the renderer was presenting. A miss is a stall only
    where every gate holds: the window was shown, the change landed on a
    visible item, the count did not move, the window was not replaced
    under the sample, and this window has been seen to present already
    in this run. Everything the probe cannot tell apart from that is its
    own outcome with its reason — an unanswered probe, a missing
    heartbeat, a change that did not land, a hidden or minimised window,
    a window the counter attached to fresh, a renderer never seen to
    paint — so neither a freeze nor a missing measurement can pass as
    the other. Whether a measured stall is ACTED on is separate and
    platform-appropriate (liveness_gating), and it is recorded with the
    outcome rather than left to the reader."""
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
        # A leg's boot can replace the window, and frames seen on the
        # window that was replaced say nothing about this one: the
        # history the verdict reads starts at the last fresh
        # attachment at or before the sample it judges.
        fresh = [index for index in range(start_index, end_index + 1)
                 if samples[index].get("fresh_window") is True]
        floor = fresh[-1] if fresh else 0
        seen = any(_saw_frames(sample) for sample in samples[floor:end_index])
        verdict, reason = liveness_of(samples[start_index], samples[end_index],
                                      seen)
        judged, reading = gating(samples[end_index].get("platform"),
                                 capture)
        records.append({"scenario": scenario_id, "outcome": verdict,
                        "reason": reason, "judged": judged, "gating": reading,
                        "start": samples[start_index],
                        "end": samples[end_index]})
    return records


def stalled_scenarios(records):
    """The scenarios whose renderer stopped presenting and whose leg
    acts on it: the ones that fail, whatever the leg captured."""
    return [record["scenario"] for record in records
            if record["outcome"] == LIVENESS_STALLED and record["judged"]]


def report_only_scenarios(records):
    """The scenarios whose window answered no forced repaint on a leg
    that does not judge its screen: measured, announced, and not acted
    on — kept apart from the stalls so a reader can never mistake a
    report-only diagnostic for a pass, or for a failure."""
    return [record["scenario"] for record in records
            if record["outcome"] == LIVENESS_STALLED and not record["judged"]]

