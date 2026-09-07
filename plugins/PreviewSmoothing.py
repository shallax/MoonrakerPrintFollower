"""Pure display policy for the Preview path within one layer.

The physical observation stays untouched; this module only decides how the
*displayed* head position moves between observations.

The printer moves at a roughly constant rate along each segment and only
changes speed at corners, so the displayed head does the same: it cruises
at the estimated physical velocity and treats the newest observed target as
a hard ceiling. Corner dwells stall the target and the head catches up
there naturally, so no easing term is needed (and easing would invent
acceleration the printer does not have).

Guarantees:

- never exceeds the observed target (the head never gets ahead of reality);
- never decreases (the head never snaps back within a layer);
- moves at a steady rate between observations — no fake ease-in/ease-out.

Layer transitions are not smoothed here: the motion driver jumps directly
to the new layer's target, exactly as the unsmoothed follower does.
"""
from __future__ import annotations


def advance_display(*, displayed: float, target: float, velocity: float, dt: float) -> float:
    """Advance the displayed path for one tick at the estimated physical rate."""
    target = max(float(displayed), min(1.0, max(0.0, float(target))))
    if target <= displayed:
        return displayed
    dt = max(0.0, float(dt))
    velocity = max(0.0, float(velocity))
    return min(target, displayed + velocity * dt)
