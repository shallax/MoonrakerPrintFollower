"""Pure tests for the print-start operation owner (the 4.3.0
extraction): the awaited-transition verdicts, the moot-differing
state, the error hold and the timeout failure."""
import time
import unittest
from unittest.mock import patch

from plugins.PrintStartOwner import PrintStartOwner


class _FileManager:
    def __init__(self):
        self.attempt = None
        self.cleared = 0

    @property
    def print_attempt(self):
        return self.attempt

    def clear_print_attempt(self):
        self.cleared += 1
        self.attempt = None


class _Console:
    def __init__(self):
        self.notes = []

    def note(self, text):
        self.notes.append(text)


class _Commands:
    def __init__(self):
        self.statuses = []

    def report_status(self, text):
        self.statuses.append(text)


class PrintStartOwnerTests(unittest.TestCase):
    def setUp(self):
        self.fm = _FileManager()
        self.console = _Console()
        self.commands = _Commands()
        self.owner = PrintStartOwner(file_manager=self.fm, console=self.console, commands=self.commands)

    def _tick(self, filename, state, message="", **stats):
        self.owner.tick({"print_stats": {"filename": filename, "state": state, "message": message, **stats}})

    def _arm(self, filename="part.gcode"):
        # A FRESH timestamp: an ancient one trips the 15 s timeout on
        # the first tick and no transition can hold.
        self.fm.attempt = (filename, time.time())

    def test_no_attempt_is_a_no_op(self):
        self.owner.tick({"print_stats": {}})
        self.assertEqual(self.fm.cleared, 0)

    def test_the_live_state_with_the_right_file_is_the_success(self):
        for state in ("printing", "paused"):
            self._arm()
            self.owner.arm("paused")
            self._tick("part.gcode", state)
            self.assertIsNone(self.fm.print_attempt, state)
            self.assertEqual(self.console.notes, [])

    def test_a_cold_error_holds_and_remembers_the_words(self):
        self._arm()
        self._tick("part.gcode", "error", message="cold extruder")
        self.assertIsNotNone(self.fm.print_attempt)
        self.assertEqual(self.owner._start_error, "cold extruder")

    def test_an_unchanged_armed_state_holds(self):
        self._arm()
        self.owner.arm("complete")
        self._tick("part.gcode", "complete")
        self.assertIsNotNone(self.fm.print_attempt)

    def test_a_differing_terminal_state_is_moot(self):
        self._arm()
        self.owner.arm("complete")
        self._tick("part.gcode", "cancelled")
        self.assertIsNone(self.fm.print_attempt)
        self.assertEqual(self.console.notes, [])

    def test_a_mismatched_filename_never_clears(self):
        # A poll in the window between the confirm and the printer's
        # state change must not wipe the attempt.
        self._arm()
        self._tick("other.gcode", "printing")
        self.assertIsNotNone(self.fm.print_attempt)

    def test_the_timeout_with_a_remembered_error_carries_the_words(self):
        self.fm.attempt = ("part.gcode", 100.0)
        self.owner.arm("paused")
        with patch("plugins.PrintStartOwner.time.time", return_value=100.0):
            self._tick("part.gcode", "error", message="cold extruder")
        with patch("plugins.PrintStartOwner.time.time", return_value=120.0):
            self.owner.tick({"print_stats": {}})
        self.assertIsNone(self.fm.print_attempt)
        self.assertIn("cold extruder", self.console.notes[0])
        self.assertEqual(self.commands.statuses, self.console.notes)

    def test_the_timeout_without_an_error_is_generic(self):
        self.fm.attempt = ("part.gcode", 100.0)
        with patch("plugins.PrintStartOwner.time.time", return_value=120.0):
            self.owner.tick({"print_stats": {}})
        self.assertIsNone(self.fm.print_attempt)
        self.assertEqual(self.console.notes[0], "Print start failed — The printer did not begin printing.")
        self.assertEqual(self.commands.statuses[0], self.console.notes[0])

    def test_arm_resets_the_error(self):
        self._arm()
        with patch("plugins.PrintStartOwner.time.time", return_value=time.time() + 0.1):
            self._tick("part.gcode", "error", message="cold extruder")
        self.owner.arm("paused")
        self.assertEqual(self.owner._start_error, "")


if __name__ == "__main__":
    unittest.main()
