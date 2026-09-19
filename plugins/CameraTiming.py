"""The T0-T9 cold-camera timing chain (the reviewer's diagnostics).

Trace-gated: `begin(enabled)` arms it from the same preference that
drives the HTTP trace, and every `mark` logs one monotonic stage once
armed. The chain answers WHERE the cold-start camera delay lives —
T0 monitor active / T1 socket start / T2 socket upgraded /
T3 webcam list requested / T4 webcam list landed / T5 camera URL
published / T6 bridge accepts Cura's connection / T7 bridge upstream
request / T8 first upstream bytes / T9 first decoded frame (the QML
side, stamped by the pane itself). Nothing here logs credentials.
"""
from __future__ import annotations

import time

try:
    from UM.Logger import Logger
except ImportError:  # the stdlib-only host suite has no Uranium
    Logger = None

_origin: float = 0.0
_enabled: bool = False
_once_marked: set = set()


def begin(enabled: bool) -> None:
    """Arm the chain from the transport-trace preference; the origin
    is the moment the trace turns on (T0 lands against it). The
    module state dies with the process, so every genuine cold start
    gets a fresh origin — refreshes never reset it."""
    global _origin, _enabled
    _enabled = bool(enabled)
    _origin = time.monotonic()
    _once_marked.clear()


def enabled() -> bool:
    return _enabled


def mark(stage: str, note: str = "") -> None:
    if not _enabled or not _origin or Logger is None:
        return
    Logger.log("i", "camera timing %s +%.3fs %s", stage, time.monotonic() - _origin, note)


def mark_once(stage: str, note: str = "") -> None:
    """A stage that may fire many times per trace (the first-frame
    signal): logged ONCE per origin."""
    if not _enabled or not _origin or stage in _once_marked:
        return
    _once_marked.add(stage)
    mark(stage, note)
