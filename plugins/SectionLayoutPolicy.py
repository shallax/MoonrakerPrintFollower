"""The section-layout policy (4.4.0): per-pane section order and
hidden sets, normalised against the static pane table.

The table is the single source of the 23 hideable section ids — the
QML instantiation order per pane (Controls 14, Information 3, Status
6 — the 4.6.0 move: objects joins the controls pane as a readout, and
the plate map joins the information pane). The console is a pane
key, not a section, and never appears here. The test suite pins the
table against the QML instantiation sites and SECTION_IDS, so a
rename or re-order that skips the table trips the gates.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

# pane -> ordered section ids, the QML instantiation order.
PANE_SECTION_ORDER: Dict[str, Tuple[str, ...]] = {
    "controls": (
        "fileManager", "print", "setup", "tuning", "toolhead",
        "profiles", "macros", "objects",
        "fans", "leds", "pwm", "power", "system", "save",
    ),
    "information": ("plateprogress", "plate", "meshmap", "temphistory"),
    "status": (
        "job", "temps", "fansinfo", "filament",
        "systeminfo", "mcus",
    ),
}

PANE_NAMES: Tuple[str, ...] = tuple(PANE_SECTION_ORDER)

# The state-file key: a per-pane {order, hidden} document, a
# top-level sibling of the sections map (nested values there are
# bool-coerced and destroyed). UiStateStore writes it under this
# literal — that owner imports nothing.
SECTION_LAYOUT_KEY = "sectionLayout"


def layout_for(stored: Any, pane: str) -> Tuple[List[str], List[str]]:
    """The effective (order, hidden) for one pane: the normalised
    stored entry, or the table order with nothing hidden."""
    normalised = normalise_section_layout(stored)
    entry = normalised.get(pane)
    if entry is None:
        return list(PANE_SECTION_ORDER[pane]), []
    return entry["order"], entry["hidden"]


def normalise_section_layout(stored: Any) -> Dict[str, Dict[str, List[str]]]:
    """Validate a stored section-layout document against the static
    pane table: unknown pane keys and unknown ids drop, missing ids
    fill from the table order, hidden ids dedupe and sort. Touched
    panes only — an all-default entry is not worth persisting (the
    stored map only records touched sections, the sections-map
    precedent)."""
    stored = stored if isinstance(stored, dict) else {}
    result: Dict[str, Dict[str, List[str]]] = {}
    for pane, table_order in PANE_SECTION_ORDER.items():
        entry = stored.get(pane)
        entry = entry if isinstance(entry, dict) else {}
        known = set(table_order)
        raw_order = entry.get("order")
        order = ([str(name) for name in raw_order if name in known]
                 if isinstance(raw_order, (list, tuple)) else [])
        order += [name for name in table_order if name not in order]
        raw_hidden = entry.get("hidden")
        hidden = (sorted({str(name) for name in raw_hidden if name in known})
                  if isinstance(raw_hidden, (list, tuple)) else [])
        if order == list(table_order) and not hidden:
            continue
        result[pane] = {"order": order, "hidden": hidden}
    return result
