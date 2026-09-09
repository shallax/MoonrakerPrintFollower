"""Pure domain tests for the Monitor's console policy."""
from __future__ import annotations

import unittest

from plugins.ConsolePolicy import MAX_HISTORY, MAX_LINE, MAX_PENDING, normalise_line, trim_history


class ConsolePolicyTests(unittest.TestCase):
    def test_input_is_stripped_and_empty_input_is_rejected(self):
        self.assertEqual(normalise_line("  G28  "), "G28")
        self.assertEqual(normalise_line("   "), "")
        self.assertEqual(normalise_line(None), "")
        self.assertEqual(normalise_line(""), "")

    def test_line_breaks_and_oversized_pastes_are_refused(self):
        # Panel security P3: the single-line UI is the only thing that
        # keeps multiline input out of the send path — the policy owns
        # that guarantee now, and a pasted megabyte line must not be
        # sent verbatim.
        self.assertEqual(normalise_line("G28\r\nM140 S60"), "G28M140 S60")
        self.assertEqual(normalise_line("G28\nG1 X0"), "G28G1 X0")
        self.assertEqual(normalise_line("M117 " + "x" * MAX_LINE), "")
        self.assertEqual(normalise_line("M117 " + "x" * (MAX_LINE - 6)), "M117 " + "x" * (MAX_LINE - 6))

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

    def test_pending_cap_bounds_the_consoles_own_lane(self):
        # ConsolePolicy.MAX_PENDING bounds the console's OWN in-flight
        # sends (the console posts its own requests, never the shared
        # one-shot lane). Asserted as a literal: this file must stay
        # stdlib-only.
        self.assertEqual(MAX_PENDING, 16)


if __name__ == "__main__":
    unittest.main()
