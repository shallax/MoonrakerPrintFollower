"""Pure display-smoothing policy for the Preview path within one layer.

The physical observation stays untouched; this module only decides how the
*displayed* head position moves between observations.

The head cruises at the estimated physical velocity and treats the newest
observed target as a hard ceiling. Guarantees:

- never exceeds the observed target (the head never gets ahead of reality);
- never decreases (the head never snaps back within a layer);
- moves at a steady glide while the estimate is accurate, instead of
  racing to each new observation and holding (chase-hold stutter);
- a gentle gap decay keeps any accumulated lag bounded without ever
  forcing a minimum speed, so slow prints glide slowly.

Layer transitions are not smoothed here: the motion driver jumps directly
to the new layer's target, exactly as the unsmoothed follower does.
"""
from __future__ import annotations

# Fraction of the remaining gap closed per second when the velocity
# estimate has drifted from reality (equilibrium lag ≈ velocity / decay).
LAG_DECAY_PER_SECOND = 1.0


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
