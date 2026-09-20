"""The restore-grace owner: per-print, in-memory stamps and the pure
evaluation that decides whether a restore is offered.

The protocol carries no exclusion timestamps (the domain round verified
Klipper's status is objects / excluded_objects / current_object only), so
the plugin stamps what it witnesses: an exclusion observed on the status
lane records the index layer, and the object's block is marked consumed
whenever the object is observed as current_object while excluded — the
per-layer order is stable, so one observation is exact. Anything the
plugin did not witness is NOT-PLUGIN-MADE: the evaluation fails open
with an honest note, because blocking a rescue the user cannot explain
is a latch, not safety (the console stays the deliberate override).

The stamps never outlive the job: a reconnect, restart or new job folds
every verdict back to unknown, never to a stale "known".
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

# Verdicts (published alongside the per-object state; QML renders them).
UNKNOWN = "unknown"        # not plugin-made — the layer is unknown
CLEAN = "clean"            # witnessed, the block has not been reached
IN_GRACE = "in_grace"      # witnessed, consumed, within the window
PAST_GRACE = "past_grace"  # witnessed, consumed, past the window or unmeasurable

# The window mode values the settings knob stores.
NEVER = "never"

# Detail sentences are policy constants with the numbers interpolated
# here; the QML builds no sentences (the repo rule).
_REASONS = {
    UNKNOWN: (
        "restore allowed — excluded outside the plugin, so the layer it "
        "happened on is unknown; restoring resumes it wherever the print is now"),
    CLEAN: ("restore allowed — the toolhead has not reached its block this "
            "layer, so a restore now leaves no gap"),
    IN_GRACE: "restore allowed — it resumes with a gap of about {gap} layers",
    PAST_GRACE: (
        "restore allowed — the layer it was excluded on is unknown, so it "
        "may resume with a gap"),
}

_BLOCKED_UNKNOWN_STRICT = (
    "restore closed — excluded outside the plugin, and the zero window "
    "cannot prove the restore is clean")
_BLOCKED_REACHED = (
    "restore closed — the toolhead has reached its block, and the "
    "restore window is zero layers")
_BLOCKED_PAST = (
    "restore closed — skipped {gap} layers ago, past the {window}-layer "
    "restore window")


class ExcludeGrace:
    """Witnessed exclusion stamps with a pure evaluation, no Qt."""

    def __init__(self) -> None:
        self._stamps: Dict[str, Dict] = {}

    def clear(self) -> None:
        """A job epoch boundary: nothing witnessed survives it."""
        self._stamps.clear()

    def note_exclusion(self, name: str, layer: Optional[int]) -> None:
        """Record a witnessed exclusion. ``layer`` may be None while the
        index is unavailable; a later observation back-fills it."""
        stamp = self._stamps.get(name)
        if stamp is None:
            self._stamps[name] = {"layer": layer, "consumed": False}
        elif stamp["layer"] is None and layer is not None:
            stamp["layer"] = layer

    def note_restored(self, name: str) -> None:
        """The exclusion is gone from the status: the stamp goes with it."""
        self._stamps.pop(name, None)

    def observe(self, current_object: Optional[str], excluded_names) -> None:
        """Update consumed flags from the live cursor: an excluded object
        observed as current has had its block at least partly skipped."""
        for name in excluded_names:
            stamp = self._stamps.get(name)
            if stamp is not None and name == current_object:
                stamp["consumed"] = True

    def evaluate(self, name: str, *, window_mode, window_layers: int,
                 layer: Optional[int]) -> Tuple[bool, str, str]:
        """The restore decision: (allowed, verdict, detail).

        window_mode is an int (0 = immediate restrict, N = windowed) or
        ExcludeGrace.NEVER. window_layers is the N in the windowed mode.
        layer is the plugin's own index layer (None when unavailable).
        """
        if window_mode == NEVER:
            return True, UNKNOWN, _REASONS[UNKNOWN]
        stamp = self._stamps.get(name)
        if stamp is None:
            if window_mode == 0:
                return False, UNKNOWN, _BLOCKED_UNKNOWN_STRICT
            return True, UNKNOWN, _REASONS[UNKNOWN]
        if not stamp["consumed"]:
            # The clean case is allowed in EVERY windowed mode (the
            # ruling): the block has not been reached, so a restore
            # leaves no gap — mode 0 means "zero gap allowed", not
            # "zero restores".
            return True, CLEAN, _REASONS[CLEAN]
        if window_mode == 0:
            return False, IN_GRACE, _BLOCKED_REACHED
        stamp_layer = stamp["layer"]
        if stamp_layer is None or layer is None:
            # One missing endpoint is incomplete verification, and
            # incomplete verification must not block the rescue.
            return True, PAST_GRACE, _REASONS[PAST_GRACE]
        gap = max(0, int(layer) - int(stamp_layer))
        if gap <= window_layers:
            return True, IN_GRACE, _REASONS[IN_GRACE].format(gap=gap)
        return False, PAST_GRACE, _BLOCKED_PAST.format(gap=gap, window=window_layers)
