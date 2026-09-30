"""The pause-at-layer block the Preview card's popover reads.

The coordinator owns the schedule and its rows; this projection derives
only what the popover's own layer implies — the candidate it would
schedule at, and the gates on its button, re-derived with the same
helpers the card's own gates use so the button is armed exactly when
the coordinator would accept the request. Cura's Preview selection is a
different question, so only the candidate-independent keys cross
unchanged.

Anything but a mapping from the seam — no coordinator, no publish yet —
publishes no block at all, and the properties keep their defaults.
"""
from __future__ import annotations

from collections.abc import Mapping

from ..Preview.PreviewFormatting import pause_can_toggle, pause_unavailable


class PauseAtLayerPresentation:
    """The coordinator's rows, projected for the popover's own layer."""

    def __init__(self, *, block, anchor):
        # The coordinator's published block, read at each call: it is
        # republished under the popover, never snapshotted.
        self._block = block
        # The follower's committed layer, likewise read live: the
        # popover schedules at the layer it is showing, never at the
        # live one behind it.
        self._anchor = anchor

    def _published_block(self) -> dict:
        source = self._block() if self._block is not None else None
        return source if isinstance(source, Mapping) else {}

    def values(self, snapshot, anchor, layer_count) -> dict:
        """`anchor` is the plate's live anchor, already coerced: the
        fallback candidate for a popover that has never been slid."""
        block = self._published_block()
        if not block:
            return {}
        index = self._anchor()
        if index < 0:
            index = anchor
        # The END of the layer the popover stands on: a 1-based human
        # layer, 0 while no layer is known (the card's own contract).
        candidate = index + 1 if index >= 0 else 0
        selected = candidate - 1 if candidate > 0 else None
        items = block.get("pauseAtLayerItems") or []
        manual = {item["layer"] - 1 for item in items if item.get("state") != "baked"}
        baked = {item["layer"] - 1 for item in items if item.get("state") == "baked"}
        baked_block = selected is not None and selected in baked
        active = bool(block.get("pauseAtLayerActive"))
        current = getattr(getattr(snapshot, "layer", None), "index", None)
        total = getattr(getattr(snapshot, "layer", None), "total", None)
        if total is None:
            total = layer_count or None
        scheduled = selected is not None and selected in manual
        indexed = bool(layer_count)
        can_toggle = indexed and not baked_block and pause_can_toggle(active, selected, current, total)
        return {
            "pauseAtLayerActive": active,
            "pauseAtLayerCandidate": candidate,
            "pauseAtLayerCanToggle": can_toggle, "pauseAtLayerScheduled": scheduled,
            "pauseAtLayerSummary": block.get("pauseAtLayerSummary", ""),
            "pauseAtLayerItems": items,
            "pauseAtLayerUnavailableText": ("Print not indexed" if active and not indexed
                                            else "a pause is baked into the gcode at this layer" if baked_block
                                            else pause_unavailable(active, can_toggle, scheduled, current, selected)),
            # The rows' own facts, unchanged by whose layer is selected.
            "pauseAtLayerHasBaked": bool(block.get("pauseAtLayerHasBaked")),
            "pauseAtLayerHasClearable": bool(block.get("pauseAtLayerHasClearable")),
        }
