"""The what's-new content and its once-per-version marker logic.

The overlay shows once per installation, per version: the persisted
state carries the last SEEN version, and a mismatch with the content
list's head (the shipped version) shows the popup. The content is
hand-written per release — the release checklist adds a new entry
here alongside the version bump, and the latest-version pin in
tests/test_whatsnew.py fails a release that bumps the package
version without a matching entry.

Qt-free on purpose: the pure logic is unit-testable outside the
container.
"""
from __future__ import annotations

from typing import List, Tuple

# One entry per release, latest first. ``items`` is the short list
# the overlay shows — curated, not generated.
WHATS_NEW: Tuple[dict, ...] = (
    {
        "version": "4.1.0",
        "items": (
            "The file manager no longer re-sorts on a temperature tick — "
            "one view per revision set, measured 190x faster warm.",
            "Upload refusals say why: the printer's own words appear in the "
            "upload status line.",
            "The ETA improve now works right after loading any print into "
            "the preview (the hourglass no longer goes missing).",
            "The release gate grew teeth: every step records its evidence, "
            "the gate refuses a run whose proof never lands, and the local "
            "full test matrix runs 2.5x faster in parallel.",
        ),
    },
    {
        "version": "4.0.2",
        "items": (
            "Five transfer and print-identity repairs: downloads retire "
            "cleanly on cancel or printer switch, progress and the size cap "
            "reset per attempt, and uploads report their real outcome.",
        ),
    },
    {
        "version": "4.0.0",
        "items": (
            "The websocket release: the status feed rides a live socket "
            "connection to the printer.",
        ),
    },
    {
        "version": "3.6.0",
        "items": (
            "The file manager: browse, search, upload, delete and rename "
            "remote gcode, with the console resize handle and the no-reflow "
            "safety rule.",
        ),
    },
)


def latest_version() -> str:
    return str(WHATS_NEW[0]["version"])


def should_show(seen: str) -> bool:
    """The once-per-version gate: show when the stored marker is not
    the shipped version (a fresh install stores nothing)."""
    return str(seen or "") != latest_version()


def entries() -> List[dict]:
    """The overlay's content shape: every version, with the latest
    flagged so the overlay can render it open at the top and the rest
    as pre-collapsed sections."""
    return [
        {"version": entry["version"], "items": list(entry["items"]),
         "isLatest": index == 0}
        for index, entry in enumerate(WHATS_NEW)
    ]
