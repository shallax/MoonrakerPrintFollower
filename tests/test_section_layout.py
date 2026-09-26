"""Pure tests for the section-layout policy (4.4.0): the pane table,
the normaliser, and the UiStateStore write path."""
from __future__ import annotations

import sys
import types
import unittest

# UiStateStore imports UM.Logger; the stdlib suite shims it (see
# qt_runtime_support) — a minimal stand-in keeps this test pure.
sys.modules.setdefault(
    "UM.Logger",
    types.SimpleNamespace(Logger=types.SimpleNamespace(log=lambda *args: None)),
)

from plugins.SectionLayoutPolicy import (
    PANE_NAMES,
    PANE_SECTION_ORDER,
    SECTION_LAYOUT_KEY,
    layout_for,
    normalise_section_layout,
)
from plugins.UiStateStore import UiStateStore

import pathlib
import re

_PLUGINS = pathlib.Path(__file__).resolve().parent.parent / "plugins"


def _section_id_counts():
    """Every sectionId: literal in the *Section.qml files, counted —
    a duplicated literal would collapse a set comparison silently."""
    counts = {}
    for path in sorted(_PLUGINS.glob("*Section.qml")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'sectionId:\s*"([a-zA-Z0-9]+)"', text):
            counts[match.group(1)] = counts.get(match.group(1), 0) + 1
    return counts


class SectionLayoutTableTests(unittest.TestCase):
    def test_table_partitions_the_section_id_literals(self):
        ids = [section for pane in PANE_NAMES for section in PANE_SECTION_ORDER[pane]]
        counts = _section_id_counts()
        self.assertEqual(len(ids), 23)
        self.assertEqual(len(set(ids)), 23, "one id sits in two panes")
        self.assertEqual(set(ids), set(counts), "the table drifted from the QML literals")
        for section, count in counts.items():
            self.assertEqual(count, 1, section)

    def test_console_is_not_a_section(self):
        for pane in PANE_NAMES:
            self.assertNotIn("console", PANE_SECTION_ORDER[pane])


class NormaliseSectionLayoutTests(unittest.TestCase):
    def test_unknown_pane_keys_drop(self):
        stored = {"mystery": {"order": ["print"], "hidden": []}}
        self.assertEqual(normalise_section_layout(stored), {})

    def test_unknown_ids_drop_and_missing_ids_fill(self):
        stored = {"controls": {"order": ["print", "ghost", "save"], "hidden": ["ghost", "save"]}}
        normalised = normalise_section_layout(stored)
        order = normalised["controls"]["order"]
        self.assertEqual(len(order), 13)
        self.assertEqual(set(order), set(PANE_SECTION_ORDER["controls"]))
        self.assertEqual(order[0], "print")
        self.assertEqual(order[1], "save")
        self.assertEqual(normalised["controls"]["hidden"], ["save"])

    def test_hidden_dedupes_and_sorts(self):
        stored = {"status": {"hidden": ["mcus", "job", "mcus"]}}
        normalised = normalise_section_layout(stored)
        self.assertEqual(normalised["status"]["hidden"], ["job", "mcus"])
        # The order fills from the table when absent.
        self.assertEqual(normalised["status"]["order"], list(PANE_SECTION_ORDER["status"]))

    def test_all_default_entries_are_not_stored(self):
        stored = {
            "information": {"order": list(PANE_SECTION_ORDER["information"]), "hidden": []},
            "controls": "junk",
        }
        self.assertEqual(normalise_section_layout(stored), {})

    def test_non_dict_stored_is_empty(self):
        self.assertEqual(normalise_section_layout(None), {})
        self.assertEqual(normalise_section_layout([{"order": []}]), {})

    def test_resetting_one_pane_never_touches_the_others(self):
        # The live report: the controls popup's reset-to-defaults
        # visibly reset the information pane's customised sections.
        # The reset re-normalises the whole document with the ONE
        # pane's entry cleared — every other pane's entry must come
        # through byte-identical.
        info_order = list(PANE_SECTION_ORDER["information"])
        stored = {"information": {"order": [info_order[1], info_order[0]] + info_order[2:],
                                  "hidden": [info_order[0]]},
                  "controls": {"order": ["print", "save"], "hidden": ["setup"]}}
        normalised = normalise_section_layout({**stored, "controls": {"order": [], "hidden": []}})
        self.assertEqual(normalised["information"], stored["information"])
        self.assertNotIn("controls", normalised)


class LayoutForTests(unittest.TestCase):
    def test_absent_pane_is_the_table_default(self):
        self.assertEqual(layout_for({}, "controls"),
                         (list(PANE_SECTION_ORDER["controls"]), []))

    def test_touched_pane_returns_the_normalised_entry(self):
        stored = {"controls": {"order": ["save", "print"], "hidden": ["setup"]}}
        order, hidden = layout_for(stored, "controls")
        self.assertEqual(order[0], "save")
        self.assertEqual(hidden, ["setup"])


class _RecordingStore:
    def __init__(self):
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)
        return True


class SectionLayoutStoreTests(unittest.TestCase):
    def test_set_section_layout_writes_under_the_key(self):
        store = _RecordingStore()
        ui = UiStateStore(store)
        layout = {"controls": {"order": ["print"], "hidden": ["save"]}}
        self.assertTrue(ui.set_section_layout(layout))
        self.assertEqual(store.writes, [{"sectionLayout": layout}])
        self.assertEqual(SECTION_LAYOUT_KEY, "sectionLayout")

    def test_set_section_layout_rejects_non_round_trippable_values(self):
        store = _RecordingStore()
        ui = UiStateStore(store)
        self.assertFalse(ui.set_section_layout({"controls": {"order": [float("nan")]}}))
        self.assertEqual(store.writes, [])


if __name__ == "__main__":
    unittest.main()
