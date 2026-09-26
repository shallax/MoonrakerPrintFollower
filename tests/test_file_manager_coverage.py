"""Coverage for the file-manager service and its pure view policy.

Two jobs in one file. The policy half pins the projection rules as
values (sorting, filtering, search, paging, attempts, recents, option
counts); the service half drives every entry point against the shared
scripted transport — the walk and its failure modes, the history
window and its pages, the mutation flows and their refusals, the
upload lifecycle, and the thumbnail queue's whole ladder.

The file is self-contained on purpose: the gate measures this file on
its own, so the flows the domain suite already pins are re-driven
through the same production entry points rather than assumed.

Leftover lines: none. This file alone reaches FileManager.py 762/762,
FileManagerPolicy.py 346/346 and FilesViewModel.py 25/25 — every
unreached statement was reachable, so no excusals are needed. The only
condition that leaves lines unmeasured is PyQt6 being absent, which
skips the Qt-marked classes; nothing here needs a live Moonraker host
or a running event loop, since every request, reply and signal is
delivered by hand.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from qt_runtime_support import QT_AVAILABLE, ScriptedTransport

from plugins.FileManagerPolicy import (
    COLUMN_WIDTH_MAX,
    COLUMN_WIDTH_MIN,
    DEFAULT_COLUMN_ORDER,
    SORTABLE_COLUMNS,
    TRAILING_COLUMN_ORDER,
    FileRow,
    ViewState,
    _value_text,
    as_float,
    as_int,
    as_str,
    attempts_for,
    clamp_page,
    default_direction,
    delete_candidates,
    directory_rows,
    empty_kind,
    filter_option_counts,
    filter_rows,
    is_gcode_name,
    merge_column_order,
    name_collides,
    normalise_columns,
    page_count,
    page_selection_state,
    page_slice,
    path_collides,
    recent_prints,
    rename_path,
    rename_target,
    search_rows,
    sort_rows,
    thumbnail_path,
    upload_relpath,
)

if QT_AVAILABLE:
    from PyQt6.QtCore import QCoreApplication, QModelIndex, QObject, Qt, pyqtSignal
    from PyQt6.QtNetwork import QNetworkReply

    from plugins.FileManager import FileManager
    from plugins.FilesViewModel import FilesViewModel


NOW = 1728000000.0  # a fixed instant: the policy never reads the clock


def make_row(name, **fields):
    fields.setdefault("relpath", name)
    return FileRow(filename=name, **fields)


# ---- policy: pure projections ------------------------------------------


class ColumnConfigTests(unittest.TestCase):
    def test_normalise_columns_rejects_unusable_widths_and_keeps_sticky_ones(self):
        state = normalise_columns({"widths": {
            "Size": 5,               # below the band: clamps up
            "thumb": 900,            # a sticky column, above the band: clamps down
            "Modified": "wide",      # not a number: dropped
            "Nope": 120,             # not a column at all: dropped
        }})
        self.assertEqual(state["widths"], {"Size": 40.0, "thumb": 600.0})

    def test_normalise_columns_fills_the_pinned_order_and_drops_unknown_names(self):
        state = normalise_columns({"order": ["Size", "Nope"], "hidden": ["Slicer", "Nope"]})
        self.assertEqual(state["order"][0], "Size")
        self.assertEqual(sorted(state["order"]), sorted(TRAILING_COLUMN_ORDER))
        self.assertEqual(state["hidden"], ["Slicer"])
        # A missing or foreign payload rehydrates to the pinned defaults.
        self.assertEqual(normalise_columns(None)["order"], TRAILING_COLUMN_ORDER)

    def test_value_text_renders_none_as_an_empty_string(self):
        # No in-tree caller yet; the helper's contract is pinned here
        # so its first caller gets the documented rendering.
        self.assertEqual(_value_text(None), "")
        self.assertEqual(_value_text(7), "7")


class DirectoryRowsTests(unittest.TestCase):
    def test_rows_skip_entries_without_a_filename_and_type_the_metadata(self):
        rows = directory_rows("gcodes", "prints", [
            {"filename": ""},
            {},
            {"filename": "a.gcode", "modified": 10.0, "size": "42", "slicer": " Cura 5.9 ",
             "object_height": "48.5", "filament_total": "12340",
             "thumbnails": [{"width": 32, "relative_path": ".thumbs/a-32x32.png"}]},
        ])
        self.assertEqual([row.relpath for row in rows], ["prints/a.gcode"])
        row = rows[0]
        self.assertEqual((row.modified, row.size), (10.0, 42))
        self.assertEqual(row.slicer, "Cura 5.9")
        self.assertEqual((row.object_height, row.filament), (48.5, 12340.0))
        self.assertEqual(row.thumb_path, ".thumbs/a-32x32.png")
        self.assertEqual(row.thumb_small, ".thumbs/a-32x32.png")

    def test_unparseable_values_read_as_unknown(self):
        self.assertIsNone(as_float("tall"))
        self.assertIsNone(as_int("many"))
        self.assertIsNone(as_str("   "))
        self.assertEqual(as_str(12), "12")


class NamingPolicyTests(unittest.TestCase):
    def test_gcode_names_gate_the_per_row_actions(self):
        for name in ("a.gcode", "b.G", "c.gco"):
            self.assertTrue(is_gcode_name(name), name)
        for name in ("e.stl", "", None, 12):
            self.assertFalse(is_gcode_name(name), name)

    def test_rename_targets_validate_the_name_and_keep_the_folder(self):
        row = make_row("a.gcode", relpath="prints/a.gcode")
        self.assertEqual(rename_target(row, "  b.gcode  "), "prints/b.gcode")
        self.assertEqual(rename_path("prints", "other"), "other")
        self.assertEqual(rename_path("prints/deep", "other"), "prints/other")
        for bad in ("", "  ", "a/b", "a\\b", "prints"):
            self.assertIsNone(rename_path("prints", bad), bad)

    def test_uploads_land_in_the_current_directory_by_basename(self):
        self.assertEqual(upload_relpath("", "bench.gcode"), "bench.gcode")
        self.assertEqual(upload_relpath("prints", "/tmp/picked/bench.gcode"), "prints/bench.gcode")

    def test_collisions_and_delete_candidates(self):
        rows = [make_row("a.gcode"), make_row("b.gcode")]
        self.assertTrue(name_collides(rows, "b.gcode"))
        self.assertFalse(name_collides(rows, "c.gcode"))
        self.assertTrue(path_collides(["prints"], "prints"))
        self.assertEqual(delete_candidates(rows, "b.gcode"), [rows[0]])
        self.assertEqual(delete_candidates(rows, ""), rows)

    def test_thumbnail_path_prefers_the_largest_or_the_smallest_cell_image(self):
        thumbs = [
            {"width": 16, "relative_path": ".thumbs/a-16x16.png"},
            {"width": 32, "relative_path": ".thumbs/a-32x32.png"},
            {"width": 300, "relative_path": ".thumbs/a-300x300.png"},
        ]
        self.assertEqual(thumbnail_path(thumbs), ".thumbs/a-300x300.png")
        self.assertEqual(thumbnail_path(thumbs, prefer="small"), ".thumbs/a-32x32.png")
        self.assertEqual(thumbnail_path([{"width": 16, "relative_path": ".thumbs/a-16.png"}],
                                        prefer="small"), ".thumbs/a-16.png")
        self.assertIsNone(thumbnail_path(None))
        # Non-mapping entries and entries without a usable width drop.
        self.assertEqual(
            thumbnail_path(["junk", {"relative_path": ".thumbs/no-width.png"},
                            {"width": 64, "relative_path": ".thumbs/a-64.png"}]),
            ".thumbs/a-64.png")


class SortingTests(unittest.TestCase):
    def test_unknown_values_sort_last_in_both_directions_with_a_name_tiebreak(self):
        rows = [make_row("beta", modified=2.0), make_row("alpha", modified=2.0), make_row("c")]
        desc = sort_rows(rows, "modified", ascending=False)
        self.assertEqual([r.filename for r in desc], ["alpha", "beta", "c"])
        # The tiebreak is direction-independent: equal values keep the
        # same name order however the column is sorted.
        asc = sort_rows(rows, "modified", ascending=True)
        self.assertEqual([r.filename for r in asc], ["alpha", "beta", "c"])

    def test_sorting_by_an_unknown_column_falls_back_to_name(self):
        rows = [make_row("beta"), make_row("alpha")]
        self.assertEqual([r.filename for r in sort_rows(rows, "nonsense", True)],
                         ["alpha", "beta"])

    def test_every_sortable_key_resolves_including_the_status_vocabulary(self):
        # "status" is the header's word for last_status; every other key
        # is a dataclass field. A full row must survive all of them.
        row = make_row("full.gcode", relpath="prints/full.gcode", modified=1.0, size=100,
                       attempts=2, last_status="completed", object_height=20.0,
                       layer_height=0.2, estimated_time=1800.0, last_print=2.0,
                       slicer="Cura 5.9", extruder=210.0, bed=60.0, filament=10.0)
        for column in SORTABLE_COLUMNS:
            sort_rows([row], column, ascending=True)  # must not raise
        self.assertEqual(sort_rows([row], "status", True)[0].filename, "full.gcode")

    def test_first_click_direction_per_column(self):
        self.assertFalse(default_direction("modified"))
        self.assertTrue(default_direction("name"))
        self.assertTrue(default_direction("slicer"))


class FilteringTests(unittest.TestCase):
    def test_slicer_is_or_within_the_category_and_unknown_is_a_bucket(self):
        rows = [make_row("cura", slicer="Cura 5.9"), make_row("bambu", slicer="Bambu Studio"),
                make_row("bare")]
        picked = filter_rows(rows, {"slicer": ["Cura 5.9", "Bambu Studio"]}, now=NOW)
        self.assertEqual([r.filename for r in picked], ["cura", "bambu"])
        self.assertEqual([r.filename for r in filter_rows(rows, {"slicer": "cura 5.9"}, now=NOW)],
                         ["cura"])
        self.assertEqual([r.filename for r in filter_rows(rows, {"slicer": ["unknown"]}, now=NOW)],
                         ["bare"])

    def test_modified_windows_exclude_unknown_dates(self):
        rows = [make_row("today", modified=NOW + 3600),
                make_row("month", modified=NOW - 20 * 86400),
                make_row("season", modified=NOW - 100 * 86400),
                make_row("undated")]
        self.assertEqual([r.filename for r in filter_rows(rows, {"modified": "today"}, now=NOW)],
                         ["today"])
        self.assertEqual([r.filename for r in filter_rows(rows, {"modified": "30d"}, now=NOW)],
                         ["today", "month"])
        self.assertEqual([r.filename for r in filter_rows(rows, {"modified": "year"}, now=NOW)],
                         ["today", "month", "season"])
        # An unrecognised window keeps every dated row.
        self.assertEqual(len(filter_rows(rows, {"modified": "someday"}, now=NOW)), 3)

    def test_print_time_bounds_exclude_long_and_unreported_durations(self):
        rows = [make_row("quick", estimated_time=600.0), make_row("long", estimated_time=7200.0),
                make_row("unmeasured")]
        self.assertEqual([r.filename for r in filter_rows(rows, {"print_time": 30}, now=NOW)],
                         ["quick"])
        # A bound the policy cannot read as minutes excludes nothing.
        self.assertEqual(len(filter_rows(rows, {"print_time": "soon"}, now=NOW)), 2)

    def test_never_printed_follows_the_rows_own_status(self):
        rows = [make_row("printed", attempts=3, last_status="completed"),
                make_row("sliced", attempts=0),
                make_row("metadata_says_printed", print_start_time=100.0)]
        self.assertEqual([r.filename for r in filter_rows(rows, {"never_printed": True}, now=NOW)],
                         ["sliced"])

    def test_categories_are_anded_across_each_other(self):
        rows = [make_row("cura_recent", slicer="Cura 5.9", modified=NOW - 3600),
                make_row("cura_old", slicer="Cura 5.9", modified=NOW - 40 * 86400),
                make_row("bambu_recent", slicer="Bambu Studio", modified=NOW - 3600)]
        picked = filter_rows(rows, {"slicer": ["Cura 5.9"], "modified": "7d"}, now=NOW)
        self.assertEqual([r.filename for r in picked], ["cura_recent"])


class SearchTests(unittest.TestCase):
    def test_search_is_token_and_and_matches_the_path(self):
        rows = [make_row("benchy_0.2.gcode", relpath="prints/benchy_0.2.gcode"),
                make_row("benchy_0.4.gcode"), make_row("cube.gcode")]
        self.assertEqual([r.filename for r in search_rows(rows, "BENCHY 0.2")],
                         ["benchy_0.2.gcode"])
        self.assertEqual([r.filename for r in search_rows(rows, "prints")],
                         ["benchy_0.2.gcode"])
        self.assertEqual(len(search_rows(rows, "  ")), 3)


class PagingTests(unittest.TestCase):
    def test_slices_are_bounded_and_the_last_page_is_partial(self):
        rows = [make_row(f"f{i}.gcode") for i in range(40)]
        self.assertEqual(len(page_slice(rows, 2, 25)), 15)
        self.assertEqual(len(page_slice(rows, 1, "all")), 40)
        self.assertEqual(page_count(40, 25), 2)
        self.assertEqual(page_count(40, "all"), 1)
        self.assertEqual(page_count(0, "all"), 0)

    def test_clamping_handles_empty_listings_and_the_all_page_size(self):
        self.assertEqual(clamp_page(4, 40, 25), 2)
        self.assertEqual(clamp_page(5, 40, "all"), 1)
        self.assertEqual(clamp_page(5, 0, 25), 1)
        # An unusable page size falls back to the 25-row default.
        self.assertEqual(clamp_page(9, 40, None), 2)
        self.assertEqual(len(page_slice(rows_40(), 2, None)), 15)
        self.assertEqual(page_count(40, "many"), 2)


def rows_40():
    return [make_row(f"f{i}.gcode") for i in range(40)]


class AttemptsAndRecentsTests(unittest.TestCase):
    def test_attempts_join_the_newest_status_and_leave_unprinted_rows_empty(self):
        rows = [make_row("a.gcode"), make_row("b.gcode")]
        jobs = [{"filename": "a.gcode", "status": "error", "end_time": 30.0},
                {"filename": "a.gcode", "status": "completed", "end_time": 10.0}]
        joined = attempts_for(rows, jobs)
        self.assertEqual(joined[0].attempts, 2)
        self.assertEqual((joined[0].last_status, joined[0].last_print), ("error", 30.0))
        self.assertEqual((joined[1].attempts, joined[1].last_status), (0, None))

    def test_recent_prints_dedupe_skip_nameless_and_drop_gone_files(self):
        jobs = [{"filename": "a.gcode", "status": "completed", "exists": True, "end_time": 50.0},
                {"filename": "a.gcode", "status": "error", "exists": True, "end_time": 40.0},
                {"filename": "gone.gcode", "exists": False, "end_time": 30.0},
                {"filename": "", "exists": True, "end_time": 20.0},
                {"filename": "b.gcode", "exists": True, "end_time": 10.0}]
        entries = recent_prints(jobs)
        self.assertEqual([entry["filename"] for entry in entries], ["a.gcode", "b.gcode"])
        self.assertEqual(entries[0]["status"], "completed")
        self.assertEqual([entry["filename"] for entry in recent_prints(jobs, limit=1)],
                         ["a.gcode"])


class SelectionAndEmptyTests(unittest.TestCase):
    def test_selection_state_has_three_states(self):
        rows = [make_row("a.gcode"), make_row("b.gcode")]
        self.assertEqual(page_selection_state([], set()), "none")
        self.assertEqual(page_selection_state(rows, set()), "none")
        self.assertEqual(page_selection_state(rows, {"a.gcode"}), "some")
        self.assertEqual(page_selection_state(rows, {"a.gcode", "b.gcode"}), "all")

    def test_empty_kinds_name_the_reason_the_grid_is_empty(self):
        self.assertEqual(empty_kind(0, 0), "no_files")
        self.assertEqual(empty_kind(10, 0), "over_filtered")
        self.assertEqual(empty_kind(10, 4), "")


class ColumnOrderMergeTests(unittest.TestCase):
    def test_persisted_order_stands_and_unknown_columns_append(self):
        merged = merge_column_order(["name", "banished"], ["name"])
        self.assertEqual(merged[:2], ["name", "banished"])
        for column in DEFAULT_COLUMN_ORDER:
            self.assertIn(column, merged)


class FilterOptionCountsTests(unittest.TestCase):
    def test_option_lists_carry_counts_over_the_resident_listing(self):
        rows = [
            make_row("cura_today", slicer="Cura 5.9", modified=NOW + 3600, estimated_time=600.0),
            make_row("cura_week", slicer="CURA 5.9", modified=NOW - 5 * 86400,
                     estimated_time=7200.0),
            make_row("bare", modified=NOW - 300 * 86400, print_start_time=10.0),
        ]
        options = filter_option_counts(rows, now=NOW)
        self.assertEqual(options["slicer"][0], ["cura 5.9", "Cura 5.9", 2])
        self.assertIn(["unknown", "Unknown", 1], options["slicer"])
        modified = {key: count for key, _label, count in options["modified"]}
        self.assertEqual(modified, {"today": 1, "7d": 2, "30d": 2, "year": 2})
        by_key = {key: count for key, _label, count in options["print_time"]}
        self.assertEqual(by_key["30"], 1)
        self.assertEqual(by_key["480"], 2)
        # The row with a metadata print_start_time has been printed.
        self.assertEqual(options["never_printed"], 2)


class ViewStateTests(unittest.TestCase):
    def test_sort_toggles_cancel_other_columns_and_reset_the_page(self):
        state = ViewState(sort_column="modified", sort_ascending=False, page=4)
        state.change_sort("size")
        self.assertEqual((state.sort_column, state.sort_ascending, state.page), ("size", False, 1))
        state.change_sort("size")
        self.assertTrue(state.sort_ascending)  # the second click flips
        state.page = 4
        state.change_sort("name")
        self.assertEqual((state.sort_column, state.sort_ascending, state.page), ("name", True, 1))

    def test_an_unknown_sort_column_changes_nothing(self):
        state = ViewState(sort_column="name", sort_ascending=True, page=3)
        state.change_sort("nonsense")
        self.assertEqual((state.sort_column, state.sort_ascending, state.page), ("name", True, 3))

    def test_filters_search_and_page_size_each_reset_the_page(self):
        state = ViewState(page=4)
        state.change_filters({"slicer": ["Cura 5.9"]})
        self.assertEqual((state.filters, state.page), ({"slicer": ["Cura 5.9"]}, 1))
        state.page = 4
        state.change_search("benchy")
        self.assertEqual((state.search, state.page), ("benchy", 1))
        state.page = 4
        state.change_page_size(100)
        self.assertEqual((state.page_size, state.page), (100, 1))

    def test_apply_filters_first_then_narrows_with_the_search(self):
        rows = [make_row("cura_a.gcode", slicer="Cura 5.9", modified=NOW),
                make_row("bambu_a.gcode", slicer="Bambu Studio", modified=NOW)]
        state = ViewState(filters={"slicer": ["Bambu Studio"]}, search="a", page=1)
        self.assertEqual([r.filename for r in state.apply(rows, now=NOW)], ["bambu_a.gcode"])


# ---- the service, against the scripted transport -----------------------


class RefusingTransport(ScriptedTransport):
    """Refuses the sends a test marks unstartable, like a full lane does."""

    def __init__(self):
        super().__init__()
        self.refuse = []

    def send_json(self, owner, channel, method, path, callback, **kwargs):
        if any(fragment in path for fragment in self.refuse):
            return False
        return super().send_json(owner, channel, method, path, callback, **kwargs)


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 not available")
class ServiceCase(unittest.TestCase):
    transport_factory = ScriptedTransport

    def setUp(self):
        self.transport = self.transport_factory()
        self.service = FileManager(SimpleNamespace(transport=self.transport))
        self.notes = []
        self.changes = []
        self.service.note.connect(self.notes.append)
        self.service.changed.connect(lambda: self.changes.append(True))
        # The service mints a scratch thumb tree per bind; take the
        # current one with it so a run leaves no litter behind.
        self.addCleanup(shutil.rmtree, self.service._thumb_root, True)

    def request(self, fragment):
        # The newest match: a refresh re-walks the same paths, and the
        # completed walk's callback must not fire twice.
        for recorded in reversed(self.transport.requests):
            if fragment in recorded.path:
                return recorded
        raise AssertionError(f"no request matching {fragment!r}")

    def deliver(self, fragment, payload, error=None):
        request = self.request(fragment)
        request.callback(payload, error)
        return request

    def directory(self, fragment, files=(), dirs=()):
        self.deliver(fragment, {"result": {
            "files": [{"filename": name, "modified": 10.0, "size": 100, **extra}
                      for name, extra in files],
            "dirs": [{"dirname": name} for name in dirs],
            "disk_usage": {"total": 800, "used": 600, "free": 200},
        }})

    def walk_requests(self, directory=""):
        """The requests for one level — the root walk by default."""
        channel = f"dir:{directory}"
        return [request for request in self.transport.requests if request.channel == channel]

    def all_walk_requests(self):
        return [request for request in self.transport.requests
                if request.channel.startswith("dir:")]

    def move_requests(self):
        return [request for request in self.transport.requests
                if request.channel.startswith("move:")]

    def open_listing(self, files=(("a.gcode", {}),), dirs=()):
        self.service.open()
        self.directory("path=gcodes&", list(files), dirs)
        self.deliver("history/list", {"result": {"jobs": []}})


class WalkTests(ServiceCase):
    def test_open_walks_the_tree_and_joins_the_history_window(self):
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})])
        self.deliver("history/list", {"result": {"jobs": [
            {"filename": "prints/nested.gcode", "status": "completed", "exists": True,
             "end_time": 5.0}]}})
        self.assertEqual(self.service.rows_resident, 2)
        self.assertIsNotNone(self.service.refreshed_at)
        self.assertEqual(self.service.disk_usage["free"], 200)
        self.assertEqual([row.filename for row in self.service.current_rows()], ["root.gcode"])
        nested = self.service.row_for("prints/nested.gcode")
        self.assertEqual((nested.attempts, nested.last_status), (1, "completed"))
        for recorded in self.transport.requests:
            self.assertEqual(recorded.owner, "file-manager")

    def test_a_listing_error_publishes_without_touching_the_resident_rows(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        changes = len(self.changes)
        self.service.refresh()
        self.deliver("path=gcodes&", None, error="Directory listing failed")
        self.assertEqual(self.service.rows_resident, 1)
        self.assertGreater(len(self.changes), changes)

    def test_a_stale_walk_reply_is_dropped(self):
        self.service.open()
        self.service.unbind()
        self.deliver("path=gcodes&", {"result": {"files": [{"filename": "stale.gcode"}]}})
        self.assertEqual(self.service.rows_resident, 0)

    def test_navigation_scopes_the_grid_and_navigate_up_pops_a_level(self):
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})], dirs=("deep",))
        self.directory("path=gcodes/prints/deep", [("inner.gcode", {})])
        self.assertEqual(self.service.subdirectories(), ["prints"])
        self.service.navigate_to(["prints"])
        self.assertEqual([row.filename for row in self.service.current_rows()], ["nested.gcode"])
        self.assertEqual(self.service.subdirectories(), ["deep"])
        self.service.navigate_up()
        self.assertEqual(self.service.directory, [])
        self.assertEqual([row.filename for row in self.service.current_rows()], ["root.gcode"])
        # The global search spans the whole tree and hides the strip.
        self.service.view.change_search("inner")
        self.assertEqual([row.filename for row in self.service.current_rows()], ["inner.gcode"])
        self.assertEqual(self.service.subdirectories(), [])


class WalkFailureTests(ServiceCase):
    transport_factory = RefusingTransport

    def test_a_fully_refused_walk_surfaces_an_error_rather_than_loading_forever(self):
        # Without this branch a refused walk swaps in "success" with no
        # rows, no timestamp and no banner — the eternal loading face.
        self.transport.refuse = ["path=gcodes&"]
        self.service.open()
        self.assertEqual(self.service.walk_error, "listing failed")
        self.assertTrue(bool(self.service.active))
        self.assertTrue(self.changes)

    def test_a_partially_refused_walk_keeps_the_previous_listing(self):
        # A subdirectory whose send is refused fails the whole walk:
        # the popup keeps what it had rather than going blank.
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})])
        self.assertEqual(self.service.rows_resident, 2)
        self.transport.refuse = ["path=gcodes/prints"]
        self.service.refresh()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.assertEqual(self.service.walk_error, "listing failed")
        self.assertIsNotNone(self.service.row_for("root.gcode"))

    def test_the_walk_stops_at_the_directory_cap(self):
        # MAX_DIRECTORIES bounds the fan-out, not the folder strip: the
        # surplus folders are still known (their parent listed them) and
        # their contents arrive on a later walk.
        with patch("plugins.FileManager.MAX_DIRECTORIES", 2):
            self.service.open()
            self.directory("path=gcodes&", [], dirs=("a", "b", "c"))
            self.assertEqual(len(self.all_walk_requests()), 2)
            self.directory("path=gcodes/a", [("a.gcode", {})])
            self.assertEqual(self.service.resident_directories(), {"a", "b", "c"})
            # Only the walked child contributed rows.
            self.assertEqual(self.service.rows_resident, 1)
            self.assertIsNotNone(self.service.row_for("a/a.gcode"))


class LifecycleTests(ServiceCase):
    def test_bind_and_unbind_survive_a_thumb_tree_removal_failure(self):
        # The temp tree is scratch: a failed removal must not abort the
        # rebind, which would leave the previous machine's rows live.
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        old_root = self.service._thumb_root
        with patch("plugins.FileManager.shutil.rmtree", side_effect=OSError("busy")):
            self.service.bind()
            self.assertEqual(self.service.rows_resident, 0)
            self.assertNotEqual(self.service._thumb_root, old_root)
            self.assertTrue(os.path.isdir(self.service._thumb_root))
            self.service.unbind()
        self.addCleanup(shutil.rmtree, self.service._thumb_root, True)

    def test_bind_and_unbind_cancel_the_lane_and_clear_the_projection(self):
        self.open_listing()
        self.service.toggle_selection("a.gcode")
        self.assertIsNotNone(self.service.row_for("a.gcode"))
        self.service.bind()
        self.assertEqual((self.service.rows_resident, self.service.selection), (0, set()))
        self.assertEqual(self.service.directory, [])
        self.open_listing()
        self.service.unbind()
        self.assertEqual(self.service.rows_resident, 0)
        self.assertEqual(self.transport.cancelled, [("file-manager", None)] * 2)

    def test_active_reports_rows_or_a_walk_error(self):
        self.assertFalse(bool(self.service.active))
        self.open_listing()
        self.assertTrue(bool(self.service.active))
        self.service._rows = {}
        self.service._walk_error = "listing failed"
        self.assertTrue(bool(self.service.active))
        self.service.clear_walk_error()
        self.assertFalse(bool(self.service.active))


class HistoryTests(ServiceCase):
    def test_load_all_history_pages_until_exhausted(self):
        self.open_listing()
        self.service.load_all_history()
        self.deliver("history/list?limit=200&start=0", {"result": {"jobs": [
            {"filename": f"p{i}.gcode", "exists": True, "end_time": 0.0} for i in range(200)]}})
        self.deliver("history/list?limit=200&start=200", {"result": {"jobs": [
            {"filename": f"q{i}.gcode", "exists": True, "end_time": 0.0} for i in range(50)]}})
        self.assertEqual(self.service.history_loaded, 250)
        self.assertTrue(self.service.history_exhausted)

    def test_a_failed_history_page_publishes_without_replacing_the_window(self):
        self.open_listing()
        before = (self.service.history_loaded, self.service.history_exhausted)
        changes = len(self.changes)
        self.service.load_all_history()
        self.deliver("history/list?limit=200&start=0", None, error="history unavailable")
        self.assertEqual((self.service.history_loaded, self.service.history_exhausted), before)
        self.assertGreater(len(self.changes), changes)

    def test_a_failed_window_fetch_leaves_the_history_untouched(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        changes = len(self.changes)
        self.deliver("history/list", None, error="history unavailable")
        self.assertEqual(self.service.history_loaded, 0)
        self.assertGreater(len(self.changes), changes)

    def test_a_paging_reply_that_outlived_its_printer_publishes_nothing(self):
        self.open_listing()
        self.service.load_all_history()
        request = self.request("start=0")
        self.service.unbind()
        request.callback({"result": {"jobs": [{"filename": "stale.gcode"}]}}, None)
        self.assertEqual(self.service.history_loaded, 0)
        self.assertFalse(self.service.history_exhausted)

    def test_history_callbacks_retire_on_a_stale_generation(self):
        self.service.open()
        request = self.request("history/list")
        self.service.unbind()
        request.callback({"result": {"jobs": [{"filename": "stale.gcode"}]}}, None)
        self.assertEqual(self.service.history_loaded, 0)


class StaleCallbackTests(ServiceCase):
    def test_row_actions_retire_on_a_stale_generation(self):
        # A printer switch mid-flight: neither the metascan reply nor
        # the delete reply may publish into the new machine's view.
        self.open_listing()
        self.service.scan_metadata("a.gcode")
        self.service.delete_files(["a.gcode"], "")
        self.service.unbind()
        self.deliver("metascan", {"result": {"layer_height": 0.3}})
        self.deliver("server/files/gcodes/a.gcode", {"result": "ok"})
        self.assertEqual(self.notes, [])

    def test_start_print_retires_on_a_stale_generation(self):
        self.open_listing()
        self.service.start_print("a.gcode")
        self.assertIsNotNone(self.service.print_attempt)
        self.service.unbind()
        self.deliver("print/start", {"result": "ok"})
        self.assertIsNone(self.service.print_attempt)


class RenameTests(ServiceCase):
    def test_rename_refuses_the_printing_file_and_an_unknown_row(self):
        self.open_listing()
        self.assertFalse(self.service.rename_file("a.gcode", "b.gcode", "a.gcode"))
        self.assertFalse(self.service.rename_file("missing.gcode", "b.gcode"))
        self.assertEqual(self.notes, ["Rename refused: a.gcode is printing."])
        self.assertEqual(self.move_requests(), [])

    def test_rename_refuses_a_path_shaped_name(self):
        self.open_listing()
        self.assertFalse(self.service.rename_file("a.gcode", "sub/b.gcode"))
        self.assertEqual(self.notes, [
            "Rename refused: the name is empty, unchanged, or not a plain filename."])
        self.assertEqual(self.move_requests(), [])

    def test_rename_refuses_a_collision_until_the_overwrite_nod(self):
        self.open_listing(files=(("a.gcode", {}), ("b.gcode", {})))
        self.assertFalse(self.service.rename_file("a.gcode", "b.gcode"))
        self.assertEqual(self.notes, ["Rename refused: a file named b.gcode already exists."])
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode", overwrite=True))
        move = self.request("server/files/move")
        self.assertEqual(move.options["body"],
                         {"source": "gcodes/a.gcode", "dest": "gcodes/b.gcode"})

    def test_rename_moves_and_rekeys_the_row_with_its_thumbnail(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {"thumbnails": [
            {"width": 300, "relative_path": ".thumbs/a-300x300.png"}]})])
        with patch.object(self.service, "_fetch_thumb"):
            self.service.request_thumbnails(self.service.current_rows())
        self.assertIn("a.gcode", self.service.thumbnail_payload())
        # The confirming refresh clears the cache afterwards: the rekey
        # is what the rename's own publish carries.
        published = []
        self.service.note.connect(
            lambda _note: published.append(dict(self.service.thumbnail_payload())))
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode"))
        self.deliver("server/files/move", {"result": {}})
        self.assertIsNone(self.service.row_for("a.gcode"))
        self.assertIsNotNone(self.service.row_for("b.gcode"))
        self.assertEqual(self.notes[0], "Renamed to b.gcode.")
        self.assertEqual(len(published), 1)
        self.assertIn("b.gcode", published[0])
        self.assertNotIn("a.gcode", published[0])

    def test_rename_surfaces_the_hosts_refusal_and_keeps_the_row(self):
        self.open_listing()
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode"))
        self.deliver("server/files/move", None, error="File is busy")
        self.assertEqual(self.notes, ["Rename refused: File is busy"])
        self.assertIsNotNone(self.service.row_for("a.gcode"))
        self.assertIsNone(self.service.row_for("b.gcode"))

    def test_a_rename_whose_row_vanished_mid_flight_re_reads_the_truth(self):
        # A walk swapped the listing while the move was in flight: the
        # local rekey has no row to move, so the refresh decides.
        self.open_listing()
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode"))
        self.service.refresh()
        self.directory("path=gcodes&", [])
        self.assertEqual(self.service.rows_resident, 0)
        self.deliver("server/files/move", {"result": {}})
        self.assertIsNone(self.service.row_for("b.gcode"))
        self.assertEqual(len(self.walk_requests()), 3)

    def test_a_stale_rename_reply_publishes_nothing(self):
        self.open_listing()
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode"))
        self.service.unbind()
        self.deliver("server/files/move", {"result": {}})
        self.assertEqual(self.notes, [])
        self.assertEqual(self.service.rows_resident, 0)


class DeleteTests(ServiceCase):
    def test_delete_drops_each_row_and_refreshes_when_the_batch_drains(self):
        notes = []
        self.open_listing(files=(("a.gcode", {}), ("b.gcode", {})))
        self.service.note.connect(notes.append)
        self.service.toggle_selection("a.gcode")
        self.service.delete_files(["a.gcode", "b.gcode"], "")
        self.deliver("server/files/gcodes/a.gcode", {"result": "ok"})
        self.assertIsNone(self.service.row_for("a.gcode"))
        self.assertIsNotNone(self.service.row_for("b.gcode"))
        self.assertEqual(notes, ["Deleted a.gcode."])
        self.deliver("server/files/gcodes/b.gcode", {"result": "ok"})
        self.assertEqual(notes, ["Deleted a.gcode.", "Deleted b.gcode."])
        # The batch refreshes once it drains (every stale-making action
        # refreshes) — one new walk per delete.
        self.assertEqual(len(self.walk_requests()), 2)

    def test_delete_reports_refusals_and_an_empty_selection(self):
        notes = []
        self.open_listing(files=(("a.gcode", {}), ("b.gcode", {})))
        self.service.note.connect(notes.append)
        self.service.delete_files(["a.gcode", "print.gcode"], "print.gcode")
        self.deliver("server/files/gcodes/a.gcode", None, error="File currently in use")
        self.assertEqual(notes, ["Delete refused: File currently in use"])
        self.assertIsNotNone(self.service.row_for("a.gcode"))
        self.service.delete_files(["missing.gcode"], "a.gcode")
        self.assertEqual(notes[-1], "Nothing to delete — the selection is empty or printing.")


class DirectoryMutationTests(ServiceCase):
    def setUp(self):
        super().setUp()
        self.service.open()
        self.directory("path=gcodes&", [("top.gcode", {})], dirs=("prints", "parts"))
        self.directory("path=gcodes/prints", [("deep.gcode", {})])
        self.directory("path=gcodes/parts", [])

    def test_folder_rename_refuses_the_root_and_unusable_names(self):
        self.assertFalse(self.service.rename_directory("", "other"))
        self.assertFalse(self.service.rename_directory("prints", "sub/other"))
        self.assertEqual(self.notes, [
            "Rename refused: the root cannot be renamed.",
            "Rename refused: the name is empty, unchanged, or not a plain name."])
        self.assertEqual(self.move_requests(), [])

    def test_folder_rename_refuses_a_collision_until_the_overwrite_nod(self):
        self.assertFalse(self.service.rename_directory("prints", "parts"))
        self.assertEqual(self.notes, ["Rename refused: a folder named parts already exists."])
        self.assertTrue(self.service.rename_directory("prints", "parts", overwrite=True))
        self.assertEqual(len(self.move_requests()), 1)

    def test_folder_rename_rekeys_thumbs_selection_and_the_nested_prefix(self):
        # Everything the popup holds by path must follow the folder:
        # the thumb cache, the selection, and the current directory.
        with patch.object(self.service, "_fetch_thumb"):
            self.service.request_thumbnails([
                self.service.row_for("prints/deep.gcode"),
                self.service.row_for("top.gcode"),
            ])
        self.service.toggle_selection("prints/deep.gcode")
        self.service.toggle_selection("top.gcode")
        self.service.navigate_to(["prints"])
        published = []
        self.service.note.connect(
            lambda _note: published.append(dict(self.service.thumbnail_payload())))
        self.assertTrue(self.service.rename_directory("prints", "renamed"))
        self.deliver("server/files/move", {"result": {}})
        self.assertEqual(set(published[0]), {"renamed/deep.gcode", "top.gcode"})
        self.assertEqual(self.service.selection, {"renamed/deep.gcode", "top.gcode"})
        self.assertEqual(self.service.directory, ["renamed"])

    def test_folder_rename_surfaces_the_hosts_refusal(self):
        self.assertTrue(self.service.rename_directory("prints", "renamed"))
        self.deliver("server/files/move", None, error="Destination exists")
        self.assertEqual(self.notes, ["Rename refused: Destination exists"])
        self.assertIn("prints", self.service.resident_directories())

    def test_folder_delete_drops_the_subtree_and_pops_the_view(self):
        self.service.navigate_to(["prints"])
        self.assertTrue(self.service.delete_directory("prints"))
        self.deliver("server/files/directory?path=gcodes/prints&force=true", {"result": "ok"})
        self.assertIsNone(self.service.row_for("prints/deep.gcode"))
        self.assertNotIn("prints", self.service.resident_directories())
        self.assertEqual(self.service.directory, [])
        self.assertEqual(self.notes, ["Deleted folder prints."])

    def test_folder_delete_refuses_the_root_and_surfaces_the_hosts_refusal(self):
        self.assertFalse(self.service.delete_directory("/"))
        self.assertEqual(self.notes, ["Delete refused: the root cannot be deleted."])
        self.assertTrue(self.service.delete_directory("prints"))
        self.deliver("server/files/directory?path=gcodes/prints&force=true", None,
                     error="Directory not empty")
        self.assertEqual(self.notes[-1], "Delete refused: Directory not empty")
        self.assertIn("prints", self.service.resident_directories())

    def test_create_directory_validates_the_name_and_notes_the_outcome(self):
        self.service.create_directory("  a/b  ")
        self.service.create_directory("")
        self.assertEqual([note for note in self.notes if "plain folder name" in note],
                         ["Create refused: enter a plain folder name."] * 2)
        self.assertEqual([request for request in self.transport.requests
                          if "gcodes/a/b" in request.path], [])
        self.service.create_directory("  fresh  ")
        self.deliver("server/files/directory?path=gcodes/fresh", {"result": {}})
        self.assertEqual(self.notes[-1], "Created folder fresh.")

    def test_a_failed_create_reports_the_host_words(self):
        self.service.create_directory("fresh")
        self.deliver("server/files/directory?path=gcodes/fresh", None, error="Directory exists")
        self.assertEqual(self.notes[-1], "Create refused: Directory exists")

    def test_stale_folder_replies_publish_nothing(self):
        self.assertTrue(self.service.rename_directory("prints", "renamed"))
        self.assertTrue(self.service.delete_directory("parts"))
        self.service.create_directory("fresh")
        self.service.unbind()
        self.deliver("server/files/move", {"result": {}})
        self.deliver("server/files/directory?path=gcodes/parts&force=true", {"result": "ok"})
        self.deliver("server/files/directory?path=gcodes/fresh", {"result": {}})
        self.assertEqual(self.notes, [])


class PrintStartTests(ServiceCase):
    def test_start_print_arms_the_attempt_until_the_host_refuses_or_it_clears(self):
        self.open_listing()
        self.service.start_print("a.gcode")
        request = self.request("print/start")
        self.assertEqual(request.path, "printer/print/start?filename=a.gcode")
        self.assertEqual(self.service.print_attempt[0], "a.gcode")
        # Success is never the reply: the attempt stays armed for the
        # print_stats transition the model watches.
        self.deliver("print/start", {"result": "ok"})
        self.assertEqual(self.service.print_attempt[0], "a.gcode")
        self.service.clear_print_attempt()
        self.assertIsNone(self.service.print_attempt)
        # A refused POST disarms it: no transition is coming.
        self.service.start_print("a.gcode")
        self.deliver("print/start", None, error="not a valid gcode file")
        self.assertIsNone(self.service.print_attempt)


class ProjectionAccessorTests(ServiceCase):
    def test_the_page_and_selection_helpers_read_the_resident_projection(self):
        self.open_listing(files=(("a.gcode", {}), ("b.gcode", {}), ("c.gcode", {})))
        self.assertEqual(self.service.total_count(), 3)
        self.assertEqual(len(self.service.page_rows()), 3)
        self.assertEqual((self.service.page_index(), self.service.page_number()), (1, 1))
        self.assertEqual(self.service.empty_state(), "")
        self.assertEqual({row.relpath for row in self.service.resident_rows()},
                         {"a.gcode", "b.gcode", "c.gcode"})
        self.assertEqual(self.service.selection_state(self.service.page_rows()), "none")
        self.service.view.change_page_size(2)
        self.assertEqual((self.service.page_number(), len(self.service.page_rows())), (2, 2))
        self.service.view.page = 2
        self.assertEqual([row.filename for row in self.service.page_rows()], ["c.gcode"])
        self.service.toggle_page_selection()
        self.assertEqual(self.service.selection, {"c.gcode"})
        self.assertEqual(self.service.selection_state(self.service.page_rows()), "all")
        self.service.toggle_page_selection()
        self.assertEqual(self.service.selection, set())
        self.service.toggle_selection("a.gcode")
        self.service.toggle_selection("a.gcode")  # the row's own toggle clears it
        self.assertEqual(self.service.selection, set())
        self.service.toggle_selection("a.gcode")
        self.service.clear_selection()
        self.assertEqual(self.service.selection, set())
        # A filtered-to-nothing view names its own empty state.
        self.service.view.change_search("nothing-matches")
        self.assertEqual(self.service.empty_state(), "over_filtered")
        self.assertEqual(self.service.total_count(), 0)

    def test_the_projection_cache_serves_one_evaluation_per_revision_set(self):
        with patch("plugins.FileManager.time.time", return_value=10.0):
            self.open_listing()
            self.service.current_rows()
            self.assertEqual(self.service.projection_count, 1)
            self.service.current_rows()  # the cache hit
            self.assertEqual(self.service.projection_count, 1)
            first = self.service.filter_option_counts_cached(now=10.0)
            self.assertIs(first, self.service.filter_option_counts_cached(now=10.0))
            self.assertEqual(first["slicer"][0][2], 1)
            self.service.view.change_search("a")
            self.service.current_rows()
            self.assertEqual(self.service.projection_count, 2)

    def test_column_state_round_trips_through_the_service(self):
        self.assertEqual(self.service.column_state()["order"], TRAILING_COLUMN_ORDER)
        self.service.set_column_state({"order": ["Size"], "hidden": ["Slicer"],
                                       "widths": {"Size": 120}})
        self.assertEqual(self.service.column_order()[0], "Size")
        self.assertEqual(self.service.column_hidden(), ["Slicer"])
        self.assertEqual(self.service.column_widths(), {"Size": 120.0})

    def test_set_column_width_clamps_and_reports_change(self):
        self.assertEqual(self.service.set_column_width("Size", "wide"), False)
        self.assertTrue(self.service.set_column_width("Size", 10))
        self.assertEqual(self.service.column_widths()["Size"], COLUMN_WIDTH_MIN)
        self.assertEqual(self.service.set_column_width("Size", COLUMN_WIDTH_MIN), False)
        self.assertTrue(self.service.set_column_width("Size", 5000))
        self.assertEqual(self.service.column_widths()["Size"], COLUMN_WIDTH_MAX)

    def test_set_column_order_drops_unknown_names_and_reports_change(self):
        self.assertTrue(self.service.set_column_order(["Size", "Nope"]))
        order = self.service.column_order()
        self.assertEqual(order[0], "Size")
        self.assertNotIn("Nope", order)
        self.assertEqual(len(order), len(TRAILING_COLUMN_ORDER))
        self.assertFalse(self.service.set_column_order(order))

    def test_set_column_visible_hides_shows_and_reports_change(self):
        self.assertTrue(self.service.set_column_visible("Size", False))
        self.assertEqual(self.service.column_hidden(), ["Size"])
        self.assertFalse(self.service.set_column_visible("Size", False))
        self.assertTrue(self.service.set_column_visible("Size", True))
        self.assertEqual(self.service.column_hidden(), [])

    def test_recents_join_the_resident_rows_for_their_thumbnails(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {"thumbnails": [
            {"width": 300, "relative_path": ".thumbs/a-300x300.png"}]})])
        self.deliver("history/list", {"result": {"jobs": [
            {"filename": "a.gcode", "status": "completed", "exists": True, "end_time": 5.0},
            {"filename": "gone.gcode", "status": "completed", "exists": True, "end_time": 4.0},
        ]}})
        entries = {entry["filename"]: entry for entry in self.service.recents()}
        self.assertEqual(entries["a.gcode"]["relpath"], "a.gcode")
        self.assertTrue(entries["a.gcode"]["thumb"])
        # A file the printer no longer lists keeps its card but has no
        # resident row to draw a thumbnail from.
        self.assertEqual(entries["gone.gcode"]["relpath"], "")
        self.assertFalse(entries["gone.gcode"]["thumb"])

    def test_scan_metadata_adopts_the_parsed_metadata_and_notes(self):
        self.open_listing(files=(("legacy.gcode", {}),))
        self.service.scan_metadata("legacy.gcode")
        self.deliver("metascan", {"result": {
            "layer_height": 0.2, "estimated_time": 3600.0, "slicer": "Cura 5.9"}})
        row = self.service.row_for("legacy.gcode")
        self.assertEqual((row.layer_height, row.estimated_time, row.slicer),
                         (0.2, 3600.0, "Cura 5.9"))
        self.assertEqual(self.notes, ["Metadata refreshed for legacy.gcode."])
        # A refusal answers with the host's words instead of silence.
        self.service.scan_metadata("legacy.gcode")
        self.deliver("metascan", None, error="not a valid gcode file")
        self.assertEqual(self.notes[-1],
                         "Metadata scan refused: not a valid gcode file.")


# ---- thumbnails --------------------------------------------------------


class ThumbReply:
    """The QNetworkReply surface the thumbnail handlers touch."""

    def __init__(self, body=b"", error=None, disposal_raises=False):
        self._body = body
        self._error = QNetworkReply.NetworkError.NoError if error is None else error
        self._disposal_raises = disposal_raises
        self.disposed = 0

    def error(self):
        return self._error

    def readAll(self):
        return self._body

    def deleteLater(self):
        self.disposed += 1
        if self._disposal_raises:
            raise RuntimeError("reply already gone")

    def abort(self):
        raise RuntimeError("abort refused")


class ThumbnailTests(ServiceCase):
    PNG = b"\x89PNG\r\n\x1a\nthumbnail-bytes"

    def thumb_row(self, name="a.gcode"):
        return FileRow(filename=name, relpath=name, thumb_path=".thumbs/a-300x300.png",
                       thumb_small=".thumbs/a-32x32.png")

    def seed_thumb(self, relpath="a.gcode"):
        self.service._thumbs[relpath] = {"state": "loading", "url": ""}
        self.service._thumb_active = 1

    def test_a_row_already_holding_its_slot_is_not_enqueued_again(self):
        # Sliding a row in and out of view must not re-fetch it: the
        # slot's state is the guard.
        emissions = []
        self.service.thumbsChanged.connect(lambda: emissions.append(True))
        with patch.object(self.service, "_fetch_thumb"):
            self.service.request_thumbnails([self.thumb_row()])
            active = self.service._thumb_active
            self.service.request_thumbnails([self.thumb_row()])
        self.assertEqual(len(emissions), 1)
        self.assertEqual(self.service._thumb_active, active)

    def test_the_queue_is_bounded_and_a_void_row_takes_the_placeholder(self):
        rows = [FileRow(filename=f"f{i}.gcode", relpath=f"f{i}.gcode",
                        thumb_path=".thumbs/t-32x32.png") for i in range(6)]
        with patch.object(self.service, "_fetch_thumb"):
            self.service.request_thumbnails(rows)
            self.assertEqual(self.service._thumb_active, 3)
            self.assertEqual(len(self.service._thumb_queue), 3)
        with patch.object(self.service, "_fetch_thumb"):
            self.service.request_thumbnails([FileRow(filename="bare.gcode", relpath="bare.gcode")])
        self.assertEqual(self.service.thumbnail_payload()["bare.gcode"]["state"], "none")

    def test_a_fetch_that_cannot_start_fails_only_its_own_slot(self):
        rows = [self.thumb_row()]
        with patch("tempfile.mkdtemp", side_effect=OSError("tmp gone")):
            self.service.request_thumbnails(rows)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state"], "failed")
        self.assertEqual(self.service._thumb_active, 0)
        with patch("tempfile.mkdtemp", side_effect=OSError("tmp gone")):
            self.service._thumbs = {}
            self.service.request_thumbnails(rows, large=True)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state_large"], "failed")

    def test_a_fetch_reply_writes_the_cache_entry_and_releases_its_slot(self):
        reply = ScriptedReply(body=self.PNG)
        self.transport.network = SimpleNamespace(get=lambda request: reply)
        self.service.request_thumbnails([self.thumb_row()])
        self.assertIs(self.service._thumb_replies.get("a.gcode"), reply)
        reply.finished.emit()
        entry = self.service.thumbnail_payload()["a.gcode"]
        self.assertEqual(entry["state"], "ready")
        self.assertTrue(entry["url"].startswith("file://"))
        self.assertEqual(self.service._thumb_active, 0)
        self.assertNotIn("a.gcode", self.service._thumb_replies)

    def test_a_thumbnail_error_and_a_non_png_body_both_fail_the_cell(self):
        # The hourglass must never spin forever: whatever the reply
        # carries, a cell that cannot show an image reads "failed".
        self.seed_thumb()
        self.service._thumb_finished("a.gcode", ThumbReply(
            error=QNetworkReply.NetworkError.ContentNotFoundError),
            self.service._thumb_generation, "/tmp/never-written.png", False)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state"], "failed")
        self.seed_thumb()
        self.service._thumb_finished("a.gcode", ThumbReply(body=b"not a png"),
                                     self.service._thumb_generation, "/tmp/never-written.png", False)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state"], "failed")

    def test_a_large_fetch_publishes_its_own_url_and_file(self):
        path = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-thumb-test-"), "large.png")
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        self.seed_thumb()
        self.service._thumb_finished("a.gcode", ThumbReply(body=self.PNG),
                                     self.service._thumb_generation, path, True)
        entry = self.service.thumbnail_payload()["a.gcode"]
        self.assertEqual(entry["state_large"], "ready")
        self.assertTrue(entry["url_large"].startswith("file://"))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), self.PNG)

    def test_a_thumbnail_that_cannot_be_written_fails_its_slot(self):
        # An unwritable path is the realistic failure (the temp tree
        # cleaned up under a pending reply): it must not raise.
        missing = "/nonexistent-thumb-dir/thumb.png"
        self.seed_thumb()
        self.service._thumb_finished("a.gcode", ThumbReply(body=self.PNG),
                                     self.service._thumb_generation, missing, True)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state_large"], "failed")
        self.seed_thumb()
        self.service._thumb_finished("a.gcode", ThumbReply(body=self.PNG),
                                     self.service._thumb_generation, missing, False)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state"], "failed")

    def test_a_reply_that_refuses_disposal_still_closes_the_fetch(self):
        # deleteLater is Qt's disposal, not the app's contract: a reply
        # that raises there must not strand the queue.
        self.seed_thumb()
        reply = ThumbReply(body=self.PNG, disposal_raises=True)
        self.service._thumb_replies["a.gcode"] = reply
        self.service._thumb_finished("a.gcode", reply, self.service._thumb_generation,
                                     os.path.join(self.service._thumb_root, "t.png"), False)
        self.assertEqual(self.service._thumb_active, 0)
        self.assertEqual(self.service._thumb_replies, {})

    def test_a_stale_reply_retires_even_when_disposal_raises(self):
        self.seed_thumb()
        reply = ThumbReply(disposal_raises=True)
        self.service._thumb_replies["a.gcode"] = reply
        self.service._thumb_finished("a.gcode", reply, self.service._thumb_generation - 1,
                                     "/tmp/never-written.png", False)
        self.assertEqual(self.service._thumb_active, 0)
        self.assertEqual(self.service._thumb_replies, {})

    def test_abort_thumbs_survives_replies_that_refuse_to_abort(self):
        self.service._thumb_replies = {"a.gcode": ThumbReply(disposal_raises=True)}
        self.service._thumb_queue = [("a.gcode", "gcodes", ".thumbs/a.png", False)]
        self.service._thumb_active = 1
        self.service._abort_thumbs()
        self.assertEqual(self.service._thumb_replies, {})
        self.assertEqual(self.service._thumb_queue, [])
        self.assertEqual(self.service._thumb_active, 0)

    def test_abort_uploads_survives_replies_that_refuse_to_abort(self):
        self.service._upload_replies["upload-1"] = (ThumbReply(disposal_raises=True), "a.gcode")
        self.service._abort_uploads()
        self.assertEqual(self.service._upload_replies, {})

    def test_clear_thumbnails_survives_a_tree_removal_failure(self):
        self.service._thumbs["a.gcode"] = {"state": "ready", "url": "file:///x.png"}
        with patch("plugins.FileManager.shutil.rmtree", side_effect=OSError("busy")):
            self.service.clear_thumbnails()
        self.assertEqual(self.service.thumbnail_payload(), {})
        self.addCleanup(shutil.rmtree, self.service._thumb_root, True)


# ---- uploads -----------------------------------------------------------


class UploadTests(ServiceCase):
    def upload_source(self, name="bench.gcode", body="G1 X0\n"):
        path = os.path.join(tempfile.mkdtemp(prefix="mpfxtest-upload-"), name)
        with open(path, "w") as handle:
            handle.write(body)
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        return path

    def reply_for(self, reply):
        self.transport.network = SimpleNamespace(post=lambda request, multipart: reply)
        return reply

    def test_upload_refuses_non_gcode_collisions_and_a_second_same_name(self):
        self.open_listing()
        verdicts = []
        self.service.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        self.assertFalse(self.service.upload_file("/tmp/thing.stl"))
        self.assertFalse(self.service.upload_file("/tmp/a.gcode"))
        self.assertEqual([detail for _ok, detail in verdicts],
                         ["Only gcode files upload here.", "A file named a.gcode already exists."])
        self.reply_for(ScriptedReply())
        source = self.upload_source()
        self.assertTrue(self.service.upload_file(source))
        self.assertFalse(self.service.upload_file(source))
        self.assertIn("already running", verdicts[-1][1])

    def test_upload_streams_the_file_and_resolves_with_progress_and_a_verdict(self):
        self.open_listing()
        reply = self.reply_for(ScriptedReply())
        progress, verdicts = [], []
        self.service.uploadProgress.connect(progress.append)
        self.service.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        self.assertTrue(self.service.upload_file(self.upload_source()))
        self.assertEqual(len(self.service._upload_replies), 1)
        reply.uploadProgress.emit(1, 2)
        self.assertEqual(progress, [50])
        reply.finished.emit()
        self.assertEqual(verdicts, [(True, "bench.gcode")])
        self.assertEqual([note for note in self.notes if note.startswith("Uploaded")],
                         ["Uploaded bench.gcode."])
        self.assertFalse(self.service._upload_replies)

    def test_upload_failure_surfaces_the_hosts_words_or_the_transport_text(self):
        self.open_listing()
        verdicts = []
        self.service.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        for body, expected in [
            (json.dumps({"message": "No space left on device"}).encode(), "No space left on device"),
            (json.dumps({"error": {"message": "Disk full"}}).encode(), "Disk full"),
            (b"<html>bad gateway</html>", "simulated transport failure"),
        ]:
            reply = self.reply_for(ScriptedReply(
                body=body, error=QNetworkReply.NetworkError.InternalServerError))
            self.assertTrue(self.service.upload_file(self.upload_source()))
            reply.finished.emit()
            self.assertEqual(verdicts[-1], (False, expected))
            self.assertEqual(reply.disposed, 1)

    def test_an_upload_lands_in_the_current_directory(self):
        # The destination is the folder the user is looking at: the
        # collision check and the multipart's path field both derive
        # from the current directory, not the root.
        self.open_listing(dirs=("prints",))
        self.directory("path=gcodes/prints", [("bench.gcode", {})])
        self.service.navigate_to(["prints"])
        verdicts = []
        self.service.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        self.assertFalse(self.service.upload_file(self.upload_source()))
        self.assertEqual(verdicts[-1], (False, "A file named bench.gcode already exists."))
        self.reply_for(ScriptedReply())
        self.assertTrue(self.service.upload_file(self.upload_source(), overwrite=True))
        self.assertEqual([relpath for _reply, relpath in self.service._upload_replies.values()],
                         ["prints/bench.gcode"])

    def test_an_upload_that_outlives_its_printer_resolves_with_a_verdict(self):
        self.open_listing()
        reply = self.reply_for(ScriptedReply())
        verdicts = []
        self.service.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        self.assertTrue(self.service.upload_file(self.upload_source()))
        self.service.bind()
        reply.finished.emit()
        self.assertEqual(verdicts, [(False, "The printer changed during the upload.")])

    def test_an_unreadable_source_is_refused_with_its_own_words(self):
        self.open_listing()
        verdicts = []
        self.service.uploadFinished.connect(lambda ok, detail: verdicts.append((ok, detail)))
        self.assertFalse(self.service.upload_file("/nonexistent-dir/bench.gcode"))
        self.assertFalse(verdicts[0][0])
        self.assertTrue(self.notes[-1].startswith("Upload refused: "))


# ---- the files view model ----------------------------------------------


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 not available")
class FilesViewModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QCoreApplication([])

    def test_rows_are_identified_by_relpath_and_rebuilds_reanchor_by_identity(self):
        view = FilesViewModel()
        view.set_rows([{"relpath": "a.gcode"}, {"relpath": "b.gcode"}, {"relpath": "c.gcode"}])
        self.assertEqual(view.rowCount(), 3)
        self.assertEqual(view.data(view.index(1)), "b.gcode")
        view.set_rows([{"relpath": "c.gcode"}, {"relpath": "a.gcode"}, {"relpath": "b.gcode"}])
        self.assertEqual(view.data(view.index(0)), "c.gcode")
        self.assertEqual(view.data(view.index(2)), "b.gcode")

    def test_row_count_and_data_guard_invalid_indexes_and_roles(self):
        view = FilesViewModel()
        view.set_rows([{"relpath": "a.gcode"}, {"relpath": "b.gcode"}])
        # A child of a row is never a row itself.
        self.assertEqual(view.rowCount(view.index(0)), 0)
        self.assertIsNone(view.data(QModelIndex()))
        self.assertIsNone(view.data(view.index(0), Qt.ItemDataRole.ToolTipRole))

    def test_row_identity_reads_plain_objects_as_well_as_mappings(self):
        view = FilesViewModel()
        self.assertEqual(view.row_identity({"relpath": "a.gcode"}), "a.gcode")
        self.assertEqual(view.row_identity({"relpath": None}), "")
        self.assertEqual(view.row_identity(SimpleNamespace(relpath="b.gcode")), "b.gcode")
        self.assertEqual(view.row_identity(object()), "")


if QT_AVAILABLE:

    class ScriptedReply(QObject):
        """A QObject reply: the fetch and upload paths parent their
        devices to it and connect to its signals."""

        finished = pyqtSignal()
        uploadProgress = pyqtSignal(int, int)

        def __init__(self, body=b"", error=None):
            super().__init__()
            self._body = body
            self._error = QNetworkReply.NetworkError.NoError if error is None else error
            self.disposed = 0

        def error(self):
            return self._error

        def errorString(self):
            return "simulated transport failure"

        def readAll(self):
            return self._body

        def deleteLater(self):
            self.disposed += 1

        def abort(self):
            pass


if __name__ == "__main__":
    unittest.main()
