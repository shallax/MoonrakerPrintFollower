# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected Git history (newest first; oldest and latest two content changes):
# 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
# 2026-09-30 [f425547] mpf's subpackages are capitalised
# 2026-09-30 [14500a2] Extract file-browser presentation from the Monitor facade

"""File labels reject invalid numerical inputs without a Monitor dependency."""
import unittest
from mpf.files.browser.FileFormatting import file_size, file_duration_short, file_filament


class FileFormattingTests(unittest.TestCase):
    def test_invalid_and_nonfinite_units_are_empty_not_exceptions(self):
        for value in (None, "junk", [], float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                self.assertEqual(file_size(value), "0 KB")
                self.assertEqual(file_duration_short(value), "—")
                self.assertEqual(file_filament(value), "—")


if __name__ == "__main__":
    unittest.main()
