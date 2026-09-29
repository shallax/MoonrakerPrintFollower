"""The next-pause pipeline: the bar's anchor, the merged rows, the target."""
from __future__ import annotations

from datetime import datetime, timedelta

from .PreviewFormatting import pause_items


def _clock(remaining):
    """The composed wall-clock ETA text for a remaining-seconds value."""
    return (datetime.now().astimezone() + timedelta(seconds=remaining)).strftime("%H:%M")


class NextPausePipeline:
    """The NEXT scheduled pause (baked or manual): its human layer, its
    composed ETA and the print's progress toward that pause.

    Owns the bar's zero point in TIME — the elapsed print time when the
    print LAST PAUSED (any pause, scheduled or manual) or the print's
    start while it never has — the last known layer index the resolver's
    None reading falls back to, the merged rows and their change key.
    """

    def __init__(self, *, preview, pauses, index):
        self._preview, self._pauses, self._index = preview, pauses, index
        self._anchor_elapsed = None
        self._anchor_job = None
        self._prev_state = None
        self._last_index = None
        self._rows = None

    def reset(self):
        # The session boundary owns the bar's whole state: the anchor,
        # the edge latch and the last known index must never survive
        # into a new session (the panel's catch).
        self._anchor_elapsed = None
        self._anchor_job = None
        self._prev_state = None
        self._last_index = None

    def track(self, index):
        # Right after a pause the resolver can drop to None — the last
        # known index carries the target selection (the live report).
        if index is not None:
            self._last_index = index

    def update_anchor(self, job, state, elapsed):
        """The bar's zero point: the elapsed print time when the print
        LAST PAUSED — any pause, scheduled or manual — or the print's
        start while it never has. A job change clears it. Returns the
        anchor in seconds."""
        if job != self._anchor_job:
            self._anchor_elapsed = None
            self._anchor_job = job
            # A new print's resolver reading None must never inherit the
            # previous print's layer (the panel's catch).
            self._last_index = None
        if state == "paused" and self._prev_state != "paused":
            self._anchor_elapsed = elapsed
        self._prev_state = state
        return self._anchor_elapsed

    def baked_layers(self):
        """The gcode's own pause layers, read-only rows (the ruling)."""
        view = self._index.view
        return set(view.pause_layers) if view is not None else set()

    def _merge(self, baked, current=None):
        return pause_items(
            set(self._pauses.layers), self._pauses.states, baked,
            lambda layer: self._preview.remaining(layer, self._index.view, end=True),
            self._preview.format_duration,
            current=current,
            clock=_clock,
        )

    def _inputs(self, baked):
        # The states join the key: a fired pause keeps its layer (the
        # stay-listed ruling) but flips its state — a layers-only key
        # would serve the pre-fire rows forever.
        return (set(self._pauses.layers), baked,
                tuple(sorted(self._pauses.states.items())))

    def rebuild(self, current=None):
        """The refresh pass: rebuild the rows — their ETA clock moves
        with the wall — and re-arm the publish-side cache."""
        baked = self.baked_layers()
        items = self._merge(baked, current)
        self._rows = (self._inputs(baked), items)
        return items

    def rows(self, current=None):
        """The publish path: the rows the last refresh built, while
        their inputs are unchanged. A publish without a refresh (the
        pause toggles) can never serve stale rows, and a miss rebuilds
        without re-arming the cache."""
        baked = self.baked_layers()
        cached = self._rows
        if cached is not None and cached[0] == self._inputs(baked):
            return cached[1]
        return self._merge(baked, current)

    def compute(self, physical, elapsed=0.0, items=None):
        """The next scheduled pause as (human layer, composed ETA,
        progress fraction, baked flag), or (None, "", None, False) while
        no pause lies ahead.

        The fraction is TIME-BASED (the live ruling — a layer-based bar
        credited the in-progress layer and read half the span early),
        spanning the DEADLINES: zero at the previous pause's deadline
        (the print's start for the first one), full at the next one, the
        speed-corrected remaining keeping it on the ETA's own scale.
        Without the index's timing the fraction reads None (no ETA, no
        bar — the ruling)."""
        if items is None:
            items = self._merge(self.baked_layers(), physical.index)
        # A row BEHIND the current layer is never the target — the
        # baked rows' passed flag alone left a fired manual row reading
        # as the target forever (the stuck 00:00:00 ETA).
        current = physical.index if physical.index is not None else self._last_index
        view = self._index.view
        anchor = self._anchor_elapsed or 0.0
        for item in items:
            if current is not None and (item["layer"] - 1) < current:
                continue
            pause_zero = item["layer"] - 1
            remaining = self._preview.remaining(pause_zero, view, end=True) if view is not None else None
            # No ETA, no bar (the live ruling): the None fraction rides
            # the -1.0 sentinel and the fill renders absent.
            if remaining is None:
                return item["layer"], item["eta"], None, item.get("state") == "baked"
            # The clamp floors the numerator FIRST (the panel's catch):
            # a stale elapsed before the anchor with an exhausted
            # remaining must read 0, never the full bar.
            numerator = (elapsed or 0.0) - anchor
            span = numerator + remaining
            fraction = (1.0 if numerator >= 0 and span <= 0
                        else max(0.0, min(1.0, numerator / span)) if span > 0 else 0.0)
            return item["layer"], item["eta"], fraction, item.get("state") == "baked"
        return None, "", None, False
