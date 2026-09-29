"""Cooperative cancellation and wall-clock yielding shared by indexing workers."""
from __future__ import annotations


import sys
import time


# The background workers' passive yield. Their loops are tight and their
# per-item work is small, so nothing but an explicit hand-back stops a
# worker holding the GIL for a whole pass. The gate is WALL-CLOCK, never
# an iteration count: one layer's cost varies by orders of magnitude
# across files, and a count that frees the UI thread on a sparse file
# starves it on the dense ones where it matters. The sleep is a real
# syscall on Windows, where an unscheduled hand-back does not reliably
# wake a waiting thread, and a bare yield elsewhere, where it does.
_PASSIVE_YIELD_S = 0.006
_YIELD_SLEEP_S = 0.001 if sys.platform == "win32" else 0.0


def passive_yield(now: float, last: float) -> float:
    """Hand the interpreter back once per _PASSIVE_YIELD_S of wall time.

    *last* is the caller's own watermark and the return is the updated
    one — each loop keeps it in its own frame, so the gate costs one
    comparison on the iterations that do not fire.
    """
    if now - last < _PASSIVE_YIELD_S:
        return last
    time.sleep(_YIELD_SLEEP_S)
    return time.monotonic()


class HydrationYield(Exception):
    """Cooperative interruption of a layer hydration.

    The reader walks a whole layer in one loop, so a dense layer is one
    long interval between hand-backs — the interval a foreground seek
    waits out. Nothing has been published when this is raised: the
    arrays commit under the index's lock after the walk, so an
    interrupted layer is still unhydrated and simply reached again.

    It is not PlateProgress's PreparationYield because that module
    imports this one.
    """


# The line loops' own gate: the clock call and the cancellation check
# are kept out of the per-line path by a counter, and the counter is
# small enough that even a slow line cannot stretch the interval past a
# few of the yield gate's periods. The scan and the hydration walk
# share it: the hand-back cadence is a property of the wall-clock gate,
# not of either loop, and a mask that only the loop can honour is what
# let the scan run a whole file's worth of lines between hand-backs.
_YIELD_CHECK_MASK = 63
# The build's byte-offset progress keeps its own, much coarser beat: it
# is a readout rather than a hand-back, and the reader's clock call per
# event is what the coarse beat exists to avoid.
_BUILD_PROGRESS_MASK = 0xFFF
