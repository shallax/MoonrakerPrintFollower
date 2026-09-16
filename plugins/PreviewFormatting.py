"""Pure projections for the Preview panel: status text, icons and pause items.

Formatting decisions for what Preview displays live here, not in the
coordinator. No Qt, no I/O, no mutable state.
"""
from __future__ import annotations

from typing import Optional


def status_text(*, detail, load_requested, loading, files_phase, index_phase,
                attached, enabled, connected, configured) -> str:
    """Derive the compact one-line status shown above the Preview panel."""
    if load_requested:
        return "Resolving…"
    if loading:
        return "Loading print…"
    if files_phase == "downloading":
        return "Downloading…"
    if index_phase == "indexing":
        return "Indexing…"
    if files_phase == "error" or index_phase == "error":
        return "Error"
    if not attached and enabled:
        return "Detached"
    if not connected:
        return "Disconnected" if configured else "Not configured"
    return str(detail)


def status_icon(text: str) -> str:
    return "CheckCircle" if text in {"Following", "Connected"} else "Information"


def pause_can_toggle(active: bool, selected: Optional[int], current: Optional[int], total: Optional[int]) -> bool:
    return bool(
        active and selected is not None and current is not None
        and selected >= current and (total is None or selected < total - 1)
    )


def pause_unavailable(active: bool, can_toggle: bool, scheduled: bool,
                      current: Optional[int], selected: Optional[int]) -> str:
    """Why the pause toggle is greyed out, or an empty string when usable."""
    if active and not can_toggle and not scheduled:
        if current is None:
            return "Waiting for current print layer"
        if selected is not None and selected < current:
            return f"Layer {selected + 1} already printed"
        return "Final layer ends the print"
    return ""


def pause_eta(remaining: Optional[float], format_duration, clock=None) -> str:
    """The row's ETA: the countdown plus the estimated wall-clock
    finish (the 2026-09-16 ruling — the clock alone is what the user
    checks against the print)."""
    if remaining is None:
        return "ETA unavailable"
    text = "in " + format_duration(remaining)
    if clock is not None:
        text += " · ~" + clock(remaining)
    return text


def pause_summary(items) -> str:
    return "End-of-layer PAUSE: " + ", ".join(str(item["layer"]) for item in items) if items else ""


def pause_items(manual, states, baked, remaining_for, format_duration, current=None, clock=None) -> list:
    """The merged, layer-sorted pause rows: the manual schedule plus the
    gcode's baked pauses (read-only rows; the ruling). A manual entry at
    a baked layer is impossible, so the manual row wins there — the
    gate prevents the double. A baked row the print has already crossed
    stays listed (no removal, no reflow) but is marked passed."""
    items = []
    for layer in manual:
        items.append({"layer": layer + 1,
                      "eta": pause_eta(remaining_for(layer), format_duration, clock),
                      "state": states.get(layer, "scheduled")})
    for layer in baked:
        if layer in manual:
            continue
        items.append({"layer": layer + 1,
                      "eta": pause_eta(remaining_for(layer), format_duration, clock),
                      "state": "baked",
                      "passed": current is not None and current > layer})
    items.sort(key=lambda item: item["layer"])
    return items
