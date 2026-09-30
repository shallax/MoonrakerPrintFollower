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
