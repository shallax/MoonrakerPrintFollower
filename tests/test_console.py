"""Pure domain tests for the Monitor's console policy."""
from __future__ import annotations

import unittest

from plugins.ConsolePolicy import MAX_HISTORY, MAX_PENDING, normalise_line, trim_history


class ConsolePolicyTests(unittest.TestCase):
    def test_input_is_stripped_and_empty_input_is_rejected(self):
        self.assertEqual(normalise_line("  G28  "), "G28")
        self.assertEqual(normalise_line("   "), "")
        self.assertEqual(normalise_line(None), "")
        self.assertEqual(normalise_line(""), "")

    def test_history_trims_to_the_newest_entries(self):
        lines = [f"line-{i}" for i in range(MAX_HISTORY + 40)]
        trimmed = trim_history(lines)
        self.assertEqual(len(trimmed), MAX_HISTORY)
        self.assertEqual(trimmed[0], f"line-{40}")
        self.assertEqual(trimmed[-1], f"line-{MAX_HISTORY + 39}")

    def test_history_accepts_only_lists_and_coerces_entries(self):
        self.assertEqual(trim_history(None), [])
        self.assertEqual(trim_history("not-a-list"), [])
        self.assertEqual(trim_history([1, 2, 3]), ["1", "2", "3"])

    def test_pending_cap_mirrors_the_shared_lane(self):
        # ConsolePolicy.MAX_PENDING mirrors MonitorCommands'
        # MAX_QUEUED_COMMANDS so the console can never flood the shared
        # one-shot lane (asserted as a literal: MonitorCommands imports
        # Qt, and this file must stay stdlib-only).
        self.assertEqual(MAX_PENDING, 16)


if __name__ == "__main__":
    unittest.main()
