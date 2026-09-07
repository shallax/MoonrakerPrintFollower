"""Pure display policy for the Preview path within one layer.

The physical observation stays untouched; this module only decides how the
*displayed* head position moves between observations.

The head cruises at the estimated physical velocity and treats the newest
observed target as a hard ceiling; a gap-feedback term compensates the
estimate's drift so the head stays in sync. The feedback is sized so the
steady-state lag is small, while corner dwells still stall the target and
resync the head naturally.

Guarantees:

- never exceeds the observed target (the head never gets ahead of reality);
- never decreases (the head never snaps back within a layer);
- steady cruise between observations, with the gap term only correcting
  drift — no chase bursts.

Layer transitions are not smoothed here: the motion driver jumps directly
to the new layer's target, exactly as the unsmoothed follower does.
"""
from __future__ import annotations

# Fraction of the remaining gap closed per second (equilibrium lag from
# estimate error ≈ error / decay). Strong enough to keep the head in sync
# without racing each new observation.
LAG_DECAY_PER_SECOND = 0.8


def advance_display(*, displayed: float, target: float, velocity: float, dt: float,
                    lag_decay_per_second: float = LAG_DECAY_PER_SECOND) -> float:
    """Advance the displayed path for one tick at the estimated physical rate."""
    target = max(float(displayed), min(1.0, max(0.0, float(target))))
    if target <= displayed:
        return displayed
    dt = max(0.0, float(dt))
    velocity = max(0.0, float(velocity))
    gap = target - displayed
    step = velocity * dt + gap * lag_decay_per_second * dt
    return min(target, displayed + step)
