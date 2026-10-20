# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-18 — 6.0.0: Cover file sorting, refresh races, active-file protection and print acknowledgements.

"""User-visible outcomes across file-manager races and failures."""
from tests.test_file_manager_coverage import ServiceCase, make_row, sort_rows


class FileManagerPolishTests(ServiceCase):
    def test_text_sort_directions_and_unknown_last(self):
        for column, field in (("name", "filename"), ("status", "last_status"), ("slicer", "slicer")):
            rows = [make_row("b.gcode"), make_row("a.gcode"), make_row("z.gcode")]
            if field != "filename":
                rows = [make_row("b.gcode", **{field: "zulu"}),
                        make_row("a.gcode", **{field: "alpha"}), make_row("z.gcode")]
            expected = ["z.gcode", "b.gcode", "a.gcode"] if column == "name" else ["b.gcode", "a.gcode", "z.gcode"]
            self.assertEqual([row.filename for row in sort_rows(rows, column, False)], expected)
            self.assertEqual([row.filename for row in sort_rows(rows, column, True)], ["a.gcode", "b.gcode", "z.gcode"])

    def test_root_and_last_child_failure_finish_and_retry_recovers(self):
        self.service.open()
        self.deliver("path=gcodes&", None, "HTTP 503")
        self.assertEqual(self.service.walk_error, "HTTP 503")
        self.assertIsNotNone(self.service.refreshed_at)
        self.service.refresh()
        self.directory("path=gcodes&", [("kept.gcode", {})])
        self.assertIsNone(self.service.walk_error)
        self.service.refresh()
        self.directory("path=gcodes&", [], dirs=("broken",))
        self.deliver("path=gcodes/broken", None, "child failed")
        self.assertEqual(self.service.walk_error, "child failed")
        self.assertIsNotNone(self.service.row_for("kept.gcode"))

    def test_old_child_cannot_overwrite_a_new_completed_walk(self):
        self.service.open()
        self.directory("path=gcodes&", [], dirs=("old",))
        old = self.request("path=gcodes/old")
        self.service.refresh()
        self.directory("path=gcodes&", [("new.gcode", {})])
        old.callback({"result": {"files": [{"filename": "stale.gcode"}]}}, None)
        self.assertEqual([row.filename for row in self.service.resident_rows()], ["new.gcode"])

    def test_active_print_is_protected_at_destination_and_directory_ancestors(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {}), ("printing.gcode", {})])
        before = len(self.transport.requests)
        self.assertFalse(self.service.rename_file("a.gcode", "printing.gcode", "printing.gcode", overwrite=True))
        self.assertFalse(self.service.rename_directory("prints", "other", printing_relpath="prints/job.gcode"))
        self.assertFalse(self.service.rename_directory("other", "prints", True, "prints/job.gcode"))
        self.assertFalse(self.service.delete_directory("prints", "prints/job.gcode"))
        self.assertEqual(len(self.transport.requests), before)

    def test_print_refusal_is_visible_and_late_ack_cannot_rearm_or_replace_attempt(self):
        self.service.start_print("a.gcode")
        first = self.request("printer/print/start")
        first.callback({}, "must home axis first")
        self.assertIsNone(self.service.print_attempt)
        self.assertIn("must home axis first", self.notes[-1])
        self.service.start_print("a.gcode")
        old = self.request("printer/print/start")
        self.service.clear_print_attempt()
        old.callback({}, None)
        self.assertIsNone(self.service.print_attempt)
        self.service.start_print("b.gcode")
        current = self.service.print_attempt
        old.callback({}, "late error")
        self.assertIs(self.service.print_attempt, current)
        self.request("printer/print/start").callback({}, None)
        self.assertIs(self.service.print_attempt, current)
