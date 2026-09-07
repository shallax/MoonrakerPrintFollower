"""Pure display-smoothing policy for the Preview path within one layer.

The physical observation stays untouched; this module only decides how the
*displayed* head position converges toward the newest observed target.

Guarantees:
- never exceeds the observed target (the head never gets ahead of reality);
- never decreases (the head never snaps back within a layer);
- exponential easing on small gaps so the final approach feels smooth;
- a linear catch-up floor on large gaps so the head cannot lag far behind.

Layer transitions are not smoothed here: the motion driver jumps directly
to the new layer's target, which is exactly how the unsmoothed follower
behaves.
"""
from __future__ import annotations

import math

# Exponential convergence rate per second; dominates small gaps.
CONVERGENCE_PER_SECOND = 6.0
# Linear catch-up floor, in fraction of the layer per second.
CATCH_UP_PER_SECOND = 1.5


def advance_display(*, displayed: float, target: float, dt: float) -> float:
    """Advance the displayed path toward the observed target for one tick."""
    target = max(float(displayed), min(1.0, max(0.0, float(target))))
    if target <= displayed:
        return displayed
    dt = max(0.0, float(dt))
    gap = target - displayed
    easing = 1.0 - math.exp(-CONVERGENCE_PER_SECOND * dt)
    step = max(gap * easing, min(gap, CATCH_UP_PER_SECOND * dt))
    return min(target, displayed + step)
