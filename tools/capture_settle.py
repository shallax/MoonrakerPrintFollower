#!/usr/bin/env python3
"""The pixel settle every capture tool shares.

A capture taken while the scene is still moving is the classic
screenshot flake: a late layout pass, an asynchronous glyph load or a
queued render-thread paint lands after the pixels first look quiet, and
two runs of the same build disagree. The contract here is a
transaction, not a sleep: pump the event loop, grab the whole window,
and require consecutive frames to be pixel-identical across a span —
then RETURN the frame that proved it, so the caller saves the proven
image and never grabs again (the settle-then-re-grab race behind the
sections-collapsed nondeterminism).
"""
from __future__ import annotations

import time

REQUIRED_IDENTICAL_FRAMES = 3
SETTLE_SPAN_SECONDS = 0.5
GRAB_INTERVAL_SECONDS = 0.02


def settle(app, grab, *, label, identical_frames=REQUIRED_IDENTICAL_FRAMES,
           span_seconds=SETTLE_SPAN_SECONDS, timeout_ms=10000, head_start_seconds=0.0,
           pending=None):
    """Pump until the frame is provably still, and return that frame.

    `head_start_seconds` runs a fixed pump first, for a load known to
    land after the first quiet run. `pending()` may refuse a settled
    frame while a known one-shot (a layout retry timer) is still armed.
    """
    if head_start_seconds > 0:
        until = time.monotonic() + head_start_seconds
        while time.monotonic() < until:
            app.processEvents()
            time.sleep(GRAB_INTERVAL_SECONDS)
    deadline = time.monotonic() + timeout_ms / 1000.0
    previous = None
    identical = 0
    first_identical = None
    image = None
    while time.monotonic() < deadline:
        app.processEvents()
        image = grab()
        if previous is not None and image == previous:
            if identical == 0:
                first_identical = time.monotonic()
            identical += 1
            if (pending is None or not pending()) \
                    and identical >= identical_frames - 1 \
                    and time.monotonic() - first_identical >= span_seconds:
                return image
        else:
            identical = 0
            first_identical = None
        previous = image
        time.sleep(GRAB_INTERVAL_SECONDS)
    raise RuntimeError(
        "the capture window never settled (%s: %d identical frames over %.1fs, %.1fs budget)"
        % (label, identical_frames, span_seconds, timeout_ms / 1000.0))
