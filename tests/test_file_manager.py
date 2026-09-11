"""File-manager domain: fake Moonraker file/history endpoints first,
then the pure projections (paging, sorting, filtering, column-order
merge, attempts aggregation, Recents dedup) as the services land.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fake_moonraker import FakeMoonraker
from qt_runtime_support import QT_AVAILABLE, ScriptedTransport
if QT_AVAILABLE:
    from plugins.FileManager import FileManager
from plugins.FileManagerPolicy import (
    DEFAULT_COLUMN_ORDER,
    FileRow,
    ViewState,
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
    page_selection_state,
    page_slice,
    path_collides,
    recent_prints,
    rename_path,
    rename_target,
    upload_relpath,
    search_rows,
    sort_rows,
    thumbnail_path,
)


def make_row(name, *, relpath=None, modified=None, size=None, slicer=None,
             estimated_time=None, extruder=None, bed=None, filament=None,
             attempts=None, last_status=None, last_print=None, **extra):
    return FileRow(
        filename=name,
        relpath=relpath or name,
        modified=modified,
        size=size,
        slicer=slicer,
        estimated_time=estimated_time,
        extruder=extruder,
        bed=bed,
        filament=filament,
        attempts=attempts,
        last_status=last_status,
        last_print=last_print,
        **extra,
    )


class FakeFileEndpointsTests(unittest.TestCase):
    def test_directory_lists_one_level_with_basenames_only(self):
        fake = FakeMoonraker()
        fake.set_file("gcodes/root.gcode", modified=10.0, size=100)
        fake.set_file("gcodes/prints/nested.gcode", modified=20.0, size=200)
        fake.set_file("gcodes/prints/deep/inner.gcode", modified=30.0, size=300)
        payload = fake.request("GET", "server/files/directory?path=gcodes&extended=true")
        result = payload["result"]
        self.assertEqual([item["filename"] for item in result["files"]], ["root.gcode"])
        self.assertEqual([item["dirname"] for item in result["dirs"]], ["prints"])
        self.assertIn("disk_usage", result)
        # The response does NOT echo the requested path — the walker
        # reconstructs (root, relpath) itself (round-2 domain D3).
        self.assertNotIn("path", result["files"][0])

    def test_subdirectory_listing_resolves_the_nested_level(self):
        fake = FakeMoonraker()
        fake.set_file("gcodes/prints/nested.gcode")
        payload = fake.request("GET", "server/files/directory?path=gcodes/prints&extended=true")
        self.assertEqual([item["filename"] for item in payload["result"]["files"]], ["nested.gcode"])

    def test_delete_removes_and_trails(self):
        fake = FakeMoonraker()
        fake.set_file("gcodes/foo.gcode")
        payload = fake.request("DELETE", "server/files/gcodes/foo.gcode")
        # Root-inclusive, like Moonraker's own response.
        self.assertEqual(payload["result"], "gcodes/foo.gcode")
        self.assertEqual(fake.deleted, ["gcodes/foo.gcode"])
        self.assertNotIn("gcodes/foo.gcode", fake.files)

    def test_move_renames_in_place(self):
        fake = FakeMoonraker()
        fake.set_file("gcodes/old.gcode")
        fake.request("POST", "server/files/move", {"source": "gcodes/old.gcode", "dest": "gcodes/new.gcode"})
        self.assertNotIn("gcodes/old.gcode", fake.files)
        self.assertIn("gcodes/new.gcode", fake.files)

    def test_history_list_paginates_with_entries_returned_count(self):
        fake = FakeMoonraker()
        for index in range(25):
            fake.add_history_job(f"file_{index}.gcode", start_time=float(index))
        page = fake.request("GET", "server/history/list?limit=10&start=0")
        self.assertEqual(page["result"]["count"], 10)
        self.assertEqual([job["filename"] for job in page["result"]["jobs"]][0], "file_24.gcode")
        tail = fake.request("GET", "server/history/list?limit=10&start=20")
        self.assertEqual(tail["result"]["count"], 5)

    def test_history_job_carries_exists_and_status(self):
        fake = FakeMoonraker()
        fake.add_history_job("gone.gcode", status="completed", exists=False)
        fake.add_history_job("live.gcode", status="interrupted", exists=True)
        # Most-recent-first: the latest job leads.
        jobs = fake.request("GET", "server/history/list")["result"]["jobs"]
        self.assertEqual(jobs[0]["filename"], "live.gcode")
        self.assertEqual(jobs[0]["status"], "interrupted")
        self.assertFalse(jobs[1]["exists"])

    def test_server_info_carries_the_components_list(self):
        fake = FakeMoonraker()
        self.assertIn("history", fake.server_info["components"])


class DirectoryRowsTests(unittest.TestCase):
    def test_rows_carry_the_reconstructed_identity(self):
        rows = directory_rows("gcodes", "prints", [
            {"filename": "a.gcode", "modified": 10.0, "size": 100},
            {"filename": "b.gcode"},
        ])
        self.assertEqual(rows[0].relpath, "prints/a.gcode")
        self.assertEqual(rows[0].modified, 10.0)
        self.assertEqual(rows[0].size, 100)
        self.assertEqual(rows[1].size, None)
        self.assertEqual(rows[1].slicer, None)

    def test_metadata_fields_map_to_typed_columns(self):
        rows = directory_rows("gcodes", "", [
            {"filename": "a.gcode", "object_height": 48.0, "layer_height": 0.2,
             "estimated_time": 6120.0, "slicer": "Cura 5.9",
             "first_layer_extr_temp": 210.0, "first_layer_bed_temp": 60.0,
             "filament_total": 12340.0},
        ])
        row = rows[0]
        self.assertEqual(row.object_height, 48.0)
        self.assertEqual(row.layer_height, 0.2)
        self.assertEqual(row.estimated_time, 6120.0)
        self.assertEqual(row.slicer, "Cura 5.9")
        self.assertEqual(row.extruder, 210.0)
        self.assertEqual(row.filament, 12340.0)

    def test_rows_carry_the_largest_thumbnail_path_from_metadata(self):
        # The plain <file>.png route 404s on a live Moonraker — the
        # thumbnail lives at the metadata's relative_path under
        # .thumbs/ (largest entry, live-proven).
        rows = directory_rows("gcodes", "", [
            {"filename": "a.gcode", "thumbnails": [
                {"width": 32, "height": 32, "size": 1009,
                 "relative_path": ".thumbs/a-32x32.png"},
                {"width": 300, "height": 300, "size": 15616,
                 "relative_path": ".thumbs/a-300x300.png"},
            ]},
            {"filename": "b.gcode"},
        ])
        self.assertEqual(rows[0].thumb_path, ".thumbs/a-300x300.png")
        self.assertIsNone(rows[1].thumb_path)


class GcodeNameTests(unittest.TestCase):
    def test_gcode_extensions_pass_and_everything_else_fails(self):
        for name in ("a.gcode", "b.G", "c.gco", "d.gCode"):
            self.assertTrue(is_gcode_name(name), name)
        for name in ("e.stl", "f.scad", "", "g.gcod", None, 12):
            self.assertFalse(is_gcode_name(name), name)


class MutationPolicyTests(unittest.TestCase):
    def test_rename_target_validates_the_name(self):
        row = FileRow(filename="a.gcode", relpath="prints/a.gcode", root="gcodes")
        self.assertEqual(rename_target(row, "b.gcode"), "prints/b.gcode")
        self.assertEqual(rename_target(row, "  b.gcode  "), "prints/b.gcode")
        self.assertIsNone(rename_target(row, ""))
        self.assertIsNone(rename_target(row, "x/y.gcode"))
        self.assertIsNone(rename_target(row, "a.gcode"))
        top = FileRow(filename="a.gcode", relpath="a.gcode", root="gcodes")
        self.assertEqual(rename_target(top, "b.gcode"), "b.gcode")

    def test_rename_path_and_folder_collisions(self):
        self.assertEqual(rename_path("prints/deep", "other"), "prints/other")
        self.assertEqual(rename_path("prints", "other"), "other")
        self.assertIsNone(rename_path("prints", "prints"))
        self.assertIsNone(rename_path("prints", ""))
        self.assertIsNone(rename_path("prints", "a/b"))
        self.assertTrue(path_collides(["prints", "prints/other"], "prints/other"))
        self.assertFalse(path_collides(["prints"], "other"))

    def test_upload_relpath_joins_the_current_directory(self):
        self.assertEqual(upload_relpath("", "bench.gcode"), "bench.gcode")
        self.assertEqual(upload_relpath("prints", "bench.gcode"), "prints/bench.gcode")
        # The filename comes from the actual file, wherever it was
        # picked from (the author's ruling).
        self.assertEqual(upload_relpath("prints", "/tmp/x/bench.gcode"), "prints/bench.gcode")

    def test_collision_and_delete_candidates(self):
        rows = [FileRow(filename="a.gcode", relpath="a.gcode"),
                FileRow(filename="b.gcode", relpath="b.gcode")]
        self.assertTrue(name_collides(rows, "b.gcode"))
        self.assertFalse(name_collides(rows, "c.gcode"))
        # The printing file is never a delete candidate (the
        # author's gate).
        self.assertEqual(delete_candidates(rows, "b.gcode"), [rows[0]])
        self.assertEqual(delete_candidates(rows, ""), rows)


class ThumbnailPathTests(unittest.TestCase):
    def test_largest_thumbnail_wins(self):
        self.assertEqual(
            thumbnail_path([
                {"width": 32, "relative_path": ".thumbs/a-32x32.png"},
                {"width": 300, "relative_path": ".thumbs/a-300x300.png"},
            ]),
            ".thumbs/a-300x300.png",
        )

    def test_absent_and_empty_payloads_are_none(self):
        self.assertIsNone(thumbnail_path(None))
        self.assertIsNone(thumbnail_path([]))

    def test_small_preference_takes_smallest_covering_a_cell(self):
        # The grid cells pick the smallest >= 32 px entry (16 px is
        # too blurry); when none qualify the largest still serves.
        self.assertEqual(
            thumbnail_path([
                {"width": 16, "relative_path": ".thumbs/a-16x16.png"},
                {"width": 32, "relative_path": ".thumbs/a-32x32.png"},
                {"width": 300, "relative_path": ".thumbs/a-300x300.png"},
            ], prefer="small"),
            ".thumbs/a-32x32.png",
        )
        self.assertEqual(
            thumbnail_path([
                {"width": 16, "relative_path": ".thumbs/a-16x16.png"},
                {"width": 300, "relative_path": ".thumbs/a-300x300.png"},
            ], prefer="small"),
            ".thumbs/a-300x300.png",
        )

    def test_malformed_entries_are_ignored(self):
        self.assertEqual(
            thumbnail_path([
                {"width": 32, "relative_path": ".thumbs/a-32x32.png"},
                "not a dict",
                {"relative_path": ".thumbs/no-width.png"},
                {"width": 64},
            ]),
            ".thumbs/a-32x32.png",
        )


class SortingTests(unittest.TestCase):
    def test_unknown_values_sort_last_in_both_directions(self):
        rows = [make_row("a", modified=3.0), make_row("b"), make_row("c", modified=1.0)]
        desc = sort_rows(rows, "modified", ascending=False)
        self.assertEqual([r.filename for r in desc], ["a", "c", "b"])
        asc = sort_rows(rows, "modified", ascending=True)
        self.assertEqual([r.filename for r in asc], ["c", "a", "b"])

    def test_ties_break_on_name(self):
        rows = [make_row("beta", modified=2.0), make_row("alpha", modified=2.0)]
        self.assertEqual([r.filename for r in sort_rows(rows, "modified", ascending=False)],
                         ["alpha", "beta"])

    def test_first_click_direction_per_column(self):
        self.assertFalse(default_direction("modified"))
        self.assertFalse(default_direction("estimated_time"))
        self.assertTrue(default_direction("name"))
        self.assertTrue(default_direction("slicer"))

    def test_sorting_by_an_unknown_column_falls_back_to_name(self):
        rows = [make_row("beta"), make_row("alpha")]
        self.assertEqual([r.filename for r in sort_rows(rows, "nonsense", True)], ["alpha", "beta"])

    def test_every_sortable_column_key_resolves_on_a_full_row(self):
        # The author's live crash: sorting by Status raised
        # AttributeError because the column key "status" has no
        # matching FileRow field. Every column key must survive a
        # full row, known values and all.
        from plugins.FileManagerPolicy import SORTABLE_COLUMNS
        row = make_row(
            "full.gcode", modified=1.0, size=100, attempts=2, last_status="completed",
            object_height=20.0, layer_height=0.2, estimated_time=1800.0,
            last_print=2.0, slicer="Cura 5.9", extruder=210.0, bed=60.0, filament=10.0,
        )
        for column in SORTABLE_COLUMNS:
            sort_rows([row], column, ascending=True)  # must not raise


class FilteringTests(unittest.TestCase):
    NOW = 1728000000.0  # a fixed instant for deterministic windows

    def test_slicer_is_or_within_the_category_and_unknown_is_a_bucket(self):
        rows = [make_row("a", slicer="Cura 5.9"), make_row("b", slicer="Bambu Studio"), make_row("c")]
        result = filter_rows(rows, {"slicer": ["Cura 5.9", "Bambu Studio"]}, now=self.NOW)
        self.assertEqual({r.filename for r in result}, {"a", "b"})
        unknown = filter_rows(rows, {"slicer": ["unknown"]}, now=self.NOW)
        self.assertEqual([r.filename for r in unknown], ["c"])

    def test_modified_windows_exclude_unknown_dates(self):
        # 2026-09-10 00:00:00 UTC = 1783267200
        rows = [
            make_row("recent", modified=self.NOW - 3600),
            make_row("old", modified=self.NOW - 40 * 86400),
            make_row("unknown"),
        ]
        recent = filter_rows(rows, {"modified": "7d"}, now=self.NOW)
        self.assertEqual([r.filename for r in recent], ["recent"])

    def test_print_time_buckets(self):
        rows = [make_row("quick", estimated_time=600.0), make_row("long", estimated_time=7200.0), make_row("unknown")]
        result = filter_rows(rows, {"print_time": 30}, now=self.NOW)
        self.assertEqual([r.filename for r in result], ["quick"])

    def test_never_printed_matches_the_rows_own_status(self):
        # The filter and the row's Status column must agree: a
        # history-joined status or a metadata print_start_time
        # excludes; only a row that would DISPLAY "Never printed"
        # belongs in the list (membership must not change after
        # "Load all history" flips attempts).
        rows = [
            make_row("printed", attempts=3, last_status="completed"),
            make_row("sliced_only", attempts=0),
            make_row("metadata_says_printed", print_start_time=100.0),
            make_row("fresh"),
        ]
        result = filter_rows(rows, {"never_printed": True}, now=self.NOW)
        self.assertEqual([r.filename for r in result], ["sliced_only", "fresh"])

    def test_categories_are_anded_across_each_other(self):
        rows = [
            make_row("cura_recent", slicer="Cura 5.9", modified=self.NOW - 3600),
            make_row("cura_old", slicer="Cura 5.9", modified=self.NOW - 40 * 86400),
            make_row("bambu_recent", slicer="Bambu Studio", modified=self.NOW - 3600),
        ]
        result = filter_rows(rows, {"slicer": ["Cura 5.9"], "modified": "7d"}, now=self.NOW)
        self.assertEqual([r.filename for r in result], ["cura_recent"])


class FilterOptionCountsTests(unittest.TestCase):
    """The dropdown option lists: [key, label, count] triples whose
    keys are exactly the vocabulary filter_rows accepts."""

    NOW = FilteringTests.NOW

    def test_slicer_options_carry_the_unknown_bucket_and_counts(self):
        rows = [make_row("a", slicer="Cura 5.9"), make_row("b", slicer="Cura 5.9"), make_row("c")]
        options = filter_option_counts(rows, now=self.NOW)["slicer"]
        self.assertIn(["cura 5.9", "Cura 5.9", 2], options)
        self.assertIn(["unknown", "Unknown", 1], options)

    def test_slicer_options_group_case_insensitively_into_one_unknown(self):
        # The author's live report: a literal "Unknown" and an
        # unreported slicer split into TWO Unknown options.
        rows = [make_row("a", slicer="Unknown"), make_row("b"), make_row("c", slicer="CURA 5.9"), make_row("d", slicer="Cura 5.9")]
        options = filter_option_counts(rows, now=self.NOW)["slicer"]
        self.assertIn(["unknown", "Unknown", 2], options)
        self.assertIn(["cura 5.9", "CURA 5.9", 2], options)

    def test_modified_windows_count_files_inside_each(self):
        # NOW is exactly a UTC midnight, so "today" must use a
        # timestamp on the current day, not NOW - 3600 (which is
        # yesterday).
        rows = [
            make_row("today", modified=self.NOW + 3600),
            make_row("week", modified=self.NOW - 5 * 86400),
            make_row("month", modified=self.NOW - 20 * 86400),
            make_row("year", modified=self.NOW - 300 * 86400),
        ]
        options = {key: count for key, _label, count in filter_option_counts(rows, now=self.NOW)["modified"]}
        self.assertEqual(options["today"], 1)
        self.assertEqual(options["7d"], 2)   # today + week
        self.assertEqual(options["30d"], 3)  # + month
        self.assertEqual(options["year"], 3)  # the 300-day-old file is in a previous year

    def test_print_time_bounds_count_files_within_each(self):
        rows = [make_row("quick", estimated_time=600.0), make_row("long", estimated_time=7200.0), make_row("unknown")]
        options = {key: count for key, _label, count in filter_option_counts(rows, now=self.NOW)["print_time"]}
        self.assertEqual(options["30"], 1)    # 10 min only
        self.assertEqual(options["120"], 2)   # 10 min and 2 h both fit 2 h
        self.assertEqual(options["480"], 2)   # the unknown duration is excluded

    def test_never_printed_counts_match_the_rows_own_status(self):
        rows = [
            make_row("printed", attempts=3, last_status="completed"),
            make_row("sliced_only", attempts=0),
            make_row("metadata_says_printed", print_start_time=100.0),
            make_row("fresh"),
        ]
        self.assertEqual(filter_option_counts(rows, now=self.NOW)["never_printed"], 2)


class SearchTests(unittest.TestCase):
    def test_search_is_token_and_and_case_insensitive(self):
        rows = [make_row("benchy_0.2.gcode"), make_row("benchy_0.4.gcode"), make_row("cube.gcode")]
        self.assertEqual({r.filename for r in search_rows(rows, "BENCHY 0.2")}, {"benchy_0.2.gcode"})

    def test_search_matches_the_path_not_just_the_name(self):
        rows = [make_row("a.gcode", relpath="prints/a.gcode"), make_row("b.gcode", relpath="b.gcode")]
        self.assertEqual([r.filename for r in search_rows(rows, "prints")], ["a.gcode"])

    def test_search_applies_after_filters(self):
        rows = [
            make_row("cura_a.gcode", slicer="Cura 5.9"),
            make_row("bambu_a.gcode", slicer="Bambu Studio"),
        ]
        state = ViewState(filters={"slicer": ["Bambu Studio"]}, search="a")
        result = state.apply(rows, now=1728000000.0)
        self.assertEqual([r.filename for r in result], ["bambu_a.gcode"])


class PagingTests(unittest.TestCase):
    def test_slice_is_bounded_by_page_size(self):
        rows = [make_row(f"f{i}.gcode", modified=float(i)) for i in range(5000)]
        self.assertEqual(len(page_slice(rows, 1, 50)), 50)
        self.assertEqual(len(page_slice(rows, 3, 50)), 50)

    def test_last_page_is_partial_and_clamps(self):
        rows = [make_row(f"f{i}.gcode") for i in range(14)]
        self.assertEqual(len(page_slice(rows, 2, 10)), 4)
        self.assertEqual(clamp_page(9, 14, 10), 2)

    def test_all_page_size_returns_everything(self):
        rows = [make_row(f"f{i}.gcode") for i in range(500)]
        self.assertEqual(len(page_slice(rows, 1, "all")), 500)
        self.assertEqual(clamp_page(5, 500, "all"), 1)


class ColumnOrderTests(unittest.TestCase):
    def test_unknown_columns_append_in_default_order(self):
        merged = merge_column_order(["name", "size"], ["name", "size", "slicer", "bed"])
        self.assertEqual(merged[:2], ["name", "size"])
        for column in DEFAULT_COLUMN_ORDER:
            if column not in ("name", "size"):
                self.assertIn(column, merged)

    def test_removed_columns_are_retained(self):
        merged = merge_column_order(["name", "banished"], ["name"])
        self.assertIn("banished", merged)


class AttemptsTests(unittest.TestCase):
    def test_count_and_last_status_word_never_flattened(self):
        rows = [make_row("a.gcode"), make_row("b.gcode")]
        jobs = [
            {"filename": "a.gcode", "status": "error", "end_time": 30.0},
            {"filename": "a.gcode", "status": "completed", "end_time": 10.0},
            {"filename": "a.gcode", "status": "cancelled", "end_time": 20.0},
        ]
        joined = attempts_for(rows, jobs)
        self.assertEqual(joined[0].attempts, 3)
        self.assertEqual(joined[0].last_status, "error")  # newest first
        self.assertEqual(joined[0].last_print, 30.0)
        self.assertEqual(joined[1].attempts, 0)
        self.assertIsNone(joined[1].last_status)


class RecentsTests(unittest.TestCase):
    def test_top_five_distinct_most_recent_first(self):
        jobs = [
            {"filename": "a.gcode", "status": "completed", "exists": True, "end_time": 50.0},
            {"filename": "a.gcode", "status": "error", "exists": True, "end_time": 40.0},
            {"filename": "b.gcode", "status": "completed", "exists": False, "end_time": 30.0},
            {"filename": "c.gcode", "status": "completed", "exists": True, "end_time": 20.0},
        ]
        result = recent_prints(jobs)
        # Gone files are DROPPED, not greyed (the author's live
        # ruling): Moonraker's history is the source of truth, and
        # unlike a local store the user cannot clean a recently
        # deleted entry out of it.
        self.assertEqual([item["filename"] for item in result], ["a.gcode", "c.gcode"])
        self.assertEqual(result[0]["status"], "completed")  # newest job's verdict

    def test_no_local_persistence_survives_in_the_projection(self):
        # The × dismissal is GONE (the author's live ruling,
        # 2026-09-10): recents come from Moonraker's history and
        # carry no client-side state — the projection takes the jobs
        # and nothing else.
        jobs = [{"filename": "a.gcode", "exists": True, "end_time": 10.0},
                {"filename": "b.gcode", "exists": True, "end_time": 5.0}]
        self.assertEqual([item["filename"] for item in recent_prints(jobs)], ["a.gcode", "b.gcode"])


class SelectionStateTests(unittest.TestCase):
    def test_three_states(self):
        rows = [make_row("a.gcode"), make_row("b.gcode"), make_row("c.gcode")]
        self.assertEqual(page_selection_state(rows, set()), "none")
        self.assertEqual(page_selection_state(rows, {"a.gcode"}), "some")
        self.assertEqual(page_selection_state(rows, {"a.gcode", "b.gcode", "c.gcode"}), "all")
        self.assertEqual(page_selection_state([], {"x"}), "none")


class ViewStateTests(unittest.TestCase):
    NOW = 1728000000.0

    def test_any_change_resets_the_page(self):
        state = ViewState(page=4)
        state.change_sort("size")
        self.assertEqual(state.page, 1)
        state.page = 4
        state.change_search("x")
        self.assertEqual(state.page, 1)
        state.page = 4
        state.change_filters({"slicer": ["Cura"]})
        self.assertEqual(state.page, 1)
        state.page = 4
        state.change_page_size(100)
        self.assertEqual(state.page, 1)

    def test_only_one_column_sorted_at_a_time(self):
        state = ViewState(sort_column="modified", sort_ascending=False)
        state.change_sort("size")
        self.assertEqual(state.sort_column, "size")
        self.assertFalse(state.sort_ascending)  # measures default descending
        state.change_sort("size")
        self.assertTrue(state.sort_ascending)  # second click flips
        state.change_sort("name")
        self.assertTrue(state.sort_ascending)  # names default ascending

    def test_empty_state_kinds(self):
        self.assertEqual(empty_kind(0, 0), "no_files")
        self.assertEqual(empty_kind(10, 0), "over_filtered")
        self.assertEqual(empty_kind(10, 4), "")


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 not available")
class FileManagerServiceTests(unittest.TestCase):
    def test_thumbnail_fetch_concurrency_is_capped(self):
        # MAX_THUMB_FETCHES holds: the queue holds the overflow and
        # a freed slot drains the next row.
        rows = [FileRow(filename=f"f{i}.gcode", relpath=f"f{i}.gcode",
                        thumb_path=".thumbs/t-32x32.png") for i in range(6)]
        with patch.object(self.service, "_fetch_thumb") as fetch:
            self.service.request_thumbnails(rows)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(len(self.service._thumb_queue), 3)

    def test_failed_thumbnail_fetch_releases_its_slot(self):
        # A fetch that cannot even start (mkdtemp failing after a
        # /tmp cleanup is the realistic trigger) must give its slot
        # back — three leaked slots would kill every thumbnail for
        # the rest of the session.
        rows = [FileRow(filename="a.gcode", relpath="a.gcode",
                        thumb_path=".thumbs/a-32x32.png")]
        with patch("tempfile.mkdtemp", side_effect=OSError("tmp gone")):
            self.service.request_thumbnails(rows)
        self.assertEqual(self.service._thumb_active, 0)
        self.assertEqual(self.service.thumbnail_payload()["a.gcode"]["state"], "failed")

    def test_abort_clears_the_queue_and_the_counter(self):
        rows = [FileRow(filename=f"f{i}.gcode", relpath=f"f{i}.gcode",
                        thumb_path=".thumbs/t-32x32.png") for i in range(5)]
        with patch.object(self.service, "_fetch_thumb"):
            self.service.request_thumbnails(rows)
            self.assertEqual(self.service._thumb_active, 3)
            self.service._abort_thumbs()
            self.assertEqual(self.service._thumb_active, 0)
            self.assertEqual(self.service._thumb_queue, [])


    """The service against the scripted transport: requests are
    recorded, tests deliver the replies explicitly, and every request
    must ride the service's own owner channel."""

    def setUp(self):
        self.transport = ScriptedTransport()
        self.service = FileManager(SimpleNamespace(transport=self.transport))

    def request(self, fragment):
        # The NEWEST matching request: the transport keeps every
        # request on record, and a refresh re-walks the same paths —
        # delivering to the first match would re-fire a completed
        # walk's callbacks and never complete the new one.
        for request in reversed(self.transport.requests):
            if fragment in request.path:
                return request
        raise AssertionError(f"no request matching {fragment!r}")

    def deliver(self, fragment, payload, error=None):
        request = self.request(fragment)
        request.callback(payload, error)
        return request

    def directory(self, fragment, files, dirs=()):
        self.deliver(fragment, {"result": {
            "files": [{"filename": name, "modified": 10.0, "size": 100, **extra}
                      for name, extra in files],
            "dirs": [{"dirname": name} for name in dirs],
            "disk_usage": {"total": 800, "used": 600, "free": 200},
        }})

    def test_open_walks_the_tree_and_fetches_history(self):
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})])
        self.deliver("history/list", {"result": {"jobs": [
            {"filename": "prints/nested.gcode", "status": "completed", "exists": True, "end_time": 5.0},
        ]}})
        self.assertEqual(self.service.rows_resident, 2)
        self.assertIsNotNone(self.service.refreshed_at)
        self.assertEqual(self.service.disk_usage["free"], 200)
        self.assertEqual(len(self.service.current_rows()), 1)  # the root view shows root files only
        self.service.view.change_search("nested")  # global search spans the tree
        nested = next(row for row in self.service.current_rows() if row.filename == "nested.gcode")
        self.assertEqual(nested.attempts, 1)
        self.assertEqual(nested.last_status, "completed")
        for recorded in self.transport.requests:
            self.assertEqual(recorded.owner, "file-manager")

    def test_navigation_scopes_the_grid(self):
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})])
        self.assertEqual([row.filename for row in self.service.current_rows()], ["root.gcode"])
        self.service.navigate_to(["prints"])
        self.assertEqual([row.filename for row in self.service.current_rows()], ["nested.gcode"])
        self.service.navigate_up()
        self.assertEqual([row.filename for row in self.service.current_rows()], ["root.gcode"])

    def test_directory_scope_is_not_recursive(self):
        # The author's live ruling: a directory shows ITS OWN files,
        # never a recursive aggregate of the subtree.
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})], dirs=("deep",))
        self.directory("path=gcodes/prints/deep", [("inner.gcode", {})])
        self.service.navigate_to(["prints"])
        self.assertEqual([row.filename for row in self.service.current_rows()], ["nested.gcode"])
        # The global search still spans the whole tree.
        self.service.view.change_search("inner")
        self.assertEqual([row.filename for row in self.service.current_rows()], ["inner.gcode"])

    def test_subdirectories_lists_direct_children_and_hides_during_search(self):
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints", "parts"))
        self.directory("path=gcodes/prints", [("nested.gcode", {})], dirs=("deep",))
        # Every walked directory must answer: the listing swaps in
        # only when the whole walk completes.
        self.directory("path=gcodes/parts", [])
        self.directory("path=gcodes/prints/deep", [])
        self.assertEqual(self.service.subdirectories(), ["parts", "prints"])
        self.service.navigate_to(["prints"])
        self.assertEqual(self.service.subdirectories(), ["deep"])
        # During a search the scope is the whole tree: the folder
        # strip hides (directories are navigation, not results).
        self.service.navigate_up()
        self.service.view.change_search("x")
        self.assertEqual(self.service.subdirectories(), [])

    def test_refresh_rediscovers_directories_and_drops_deleted_files(self):
        # The author's live report: refresh never pulled down new
        # directories (a known directory was never re-walked) and
        # deleted files lingered. Every walk builds a fresh listing
        # and swaps it in on completion.
        self.service.open()
        self.directory("path=gcodes&", [("keep.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})])
        self.assertEqual(self.service.subdirectories(), ["prints"])
        self.service.refresh()
        self.directory("path=gcodes&", [("keep.gcode", {})], dirs=("prints", "parts"))
        self.directory("path=gcodes/prints", [("fresh.gcode", {})], dirs=("new",))
        self.directory("path=gcodes/parts", [])
        self.directory("path=gcodes/prints/new", [])
        self.assertEqual(self.service.subdirectories(), ["parts", "prints"])
        self.service.navigate_to(["prints"])
        self.assertEqual(self.service.subdirectories(), ["new"])
        self.assertEqual([row.filename for row in self.service.current_rows()], ["fresh.gcode"])

    def test_search_spans_the_whole_tree(self):
        self.service.open()
        self.directory("path=gcodes&", [("root.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints", [("nested.gcode", {})])
        self.service.view.change_search("nested")
        self.assertEqual([row.filename for row in self.service.current_rows()], ["nested.gcode"])

    def test_load_all_history_pages_until_exhausted(self):
        self.service.open()
        self.deliver("history/list?limit=200", {"result": {
            "count": 200, "jobs": [{"filename": f"f{i}.gcode", "exists": True, "end_time": 0.0}
                                   for i in range(200)],
        }})
        self.service.load_all_history()
        self.deliver("history/list?limit=200&start=0", {"result": {
            "count": 200, "jobs": [{"filename": f"p{i}.gcode", "exists": True, "end_time": 0.0}
                                   for i in range(200)],
        }})
        self.deliver("history/list?limit=200&start=200", {"result": {
            "count": 50, "jobs": [{"filename": f"q{i}.gcode", "exists": True, "end_time": 0.0}
                                  for i in range(50)],
        }})
        self.assertTrue(self.service.history_exhausted)
        self.assertEqual(self.service.history_loaded, 250)

    def test_stale_callbacks_cannot_publish_after_unbind(self):
        self.service.open()
        self.service.unbind()
        self.deliver("path=gcodes&", {"result": {
            "files": [{"filename": "stale.gcode"}], "dirs": [], "disk_usage": {},
        }})
        self.assertEqual(self.service.rows_resident, 0)

    def test_scan_metadata_adopts_the_metascan_response_and_notes(self):
        # Live-proven: Moonraker's metascan response IS the parsed
        # metadata (a 200 carrying the full result) — no re-read.
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("legacy.gcode", {})])
        self.service.scan_metadata("legacy.gcode")
        self.request("metascan")
        self.deliver("metascan", {"result": {
            "layer_height": 0.2, "estimated_time": 3600.0,
        }})
        row = next(row for row in self.service.current_rows() if row.filename == "legacy.gcode")
        self.assertEqual(row.layer_height, 0.2)
        self.assertEqual(row.estimated_time, 3600.0)
        self.assertEqual(notes, ["Metadata refreshed for legacy.gcode."])

    def test_scan_metadata_refusal_surfaces_the_hosts_words(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("legacy.stl", {})])
        self.service.scan_metadata("legacy.stl")
        self.request("metascan")
        self.deliver("metascan", None, error="File legacy.stl is not a valid gcode file")
        self.assertEqual(
            notes, ["Metadata scan refused: File legacy.stl is not a valid gcode file."])

    def test_delete_files_sends_root_inclusive_deletes_and_drops_rows(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {}), ("b.gcode", {})])
        self.service.toggle_selection("a.gcode")
        self.service.delete_files(["a.gcode"], "")
        delete = self.request("server/files/gcodes/a.gcode")
        self.assertEqual(delete.method, "DELETE")
        # Root-INCLUSIVE: the host's only delete route carries both
        # parts (unlike print/start's root-exclusive filename).
        self.assertEqual(delete.path, "server/files/gcodes/a.gcode")
        self.deliver("server/files/gcodes/a.gcode", {"result": "ok"})
        self.assertIsNone(self.service.row_for("a.gcode"))
        self.assertNotIn("a.gcode", self.service.selection)
        self.assertEqual(notes, ["Deleted a.gcode."])
        # The batch refreshes once it drains (the ruling: every
        # stale-making action refreshes) — a fresh walk is pending.
        self.request("path=gcodes&")

    def test_delete_refusal_keeps_the_row_and_surfaces_the_hosts_words(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        self.service.delete_files(["a.gcode"], "")
        self.deliver("server/files/gcodes/a.gcode", None, error="File currently in use")
        self.assertIsNotNone(self.service.row_for("a.gcode"))
        self.assertEqual(notes, ["Delete refused: File currently in use"])

    def test_delete_never_targets_the_printing_file(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        self.service.delete_files(["a.gcode"], "a.gcode")
        for request in self.transport.requests:
            self.assertNotIn("delete:", request.channel)
        self.assertEqual(notes, ["Nothing to delete — the selection is empty or printing."])

    def test_rename_moves_and_rekeys_the_row(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode", ""))
        move = self.request("server/files/move")
        self.assertEqual(move.options["body"], {"source": "gcodes/a.gcode", "dest": "gcodes/b.gcode"})
        self.deliver("server/files/move", {"result": {}})
        self.assertIsNone(self.service.row_for("a.gcode"))
        self.assertIsNotNone(self.service.row_for("b.gcode"))
        self.assertEqual(notes, ["Renamed to b.gcode."])

    def test_rename_collision_needs_the_overwrite_nod(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {}), ("b.gcode", {})])
        self.assertFalse(self.service.rename_file("a.gcode", "b.gcode", ""))
        for request in self.transport.requests:
            self.assertNotIn("move:", request.channel)
        self.assertEqual(notes, ["Rename refused: a file named b.gcode already exists."])
        self.assertTrue(self.service.rename_file("a.gcode", "b.gcode", "", overwrite=True))
        self.request("server/files/move")

    def test_upload_refuses_non_gcode_and_collisions(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {})])
        self.assertFalse(self.service.upload_file("/tmp/thing.stl"))
        self.assertEqual(notes, ["Upload refused: only gcode files upload here."])
        self.assertFalse(self.service.upload_file("/tmp/a.gcode"))
        self.assertEqual(notes[-1], "Upload refused: a file named a.gcode already exists.")

    def test_rename_directory_rekeys_everything_under_it(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("top.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints&", [("deep.gcode", {})])
        self.service.navigate_to(["prints"])
        self.assertTrue(self.service.rename_directory("prints", "parts"))
        move = self.request("server/files/move")
        self.assertEqual(move.options["body"], {"source": "gcodes/prints", "dest": "gcodes/parts"})
        self.deliver("server/files/move", {"result": {}})
        self.assertIsNone(self.service.row_for("prints/deep.gcode"))
        self.assertIsNotNone(self.service.row_for("parts/deep.gcode"))
        self.assertIn("parts", self.service.resident_directories())
        self.assertNotIn("prints", self.service.resident_directories())
        self.assertEqual(self.service.directory, ["parts"])
        self.assertEqual(notes, ["Renamed folder to parts."])

    def test_delete_directory_drops_its_tree_and_pops_the_view(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [("top.gcode", {})], dirs=("prints",))
        self.directory("path=gcodes/prints&", [("deep.gcode", {})])
        self.service.navigate_to(["prints"])
        self.assertTrue(self.service.delete_directory("prints"))
        delete = self.request("server/files/directory?path=gcodes/prints&force=true")
        self.assertEqual(delete.method, "DELETE")
        self.deliver("server/files/directory?path=gcodes/prints&force=true", {"result": "ok"})
        self.assertIsNone(self.service.row_for("prints/deep.gcode"))
        self.assertNotIn("prints", self.service.resident_directories())
        # The view sat inside the deleted tree: it pops to the parent.
        self.assertEqual(self.service.directory, [])
        self.assertEqual(notes, ["Deleted folder prints."])

    def test_create_directory_posts_the_host_route_and_notes_outcomes(self):
        notes = []
        self.service.note.connect(notes.append)
        self.service.open()
        self.directory("path=gcodes&", [])
        self.service.create_directory("  prints  ")
        create = self.request("server/files/directory?path=gcodes/prints")
        self.assertEqual(create.method, "POST")
        create.callback({"result": {"path": "gcodes/prints"}}, None)
        self.assertEqual(notes[-1], "Created folder prints.")
        # A path-shaped or empty name refuses without a request.
        before = len(self.transport.requests)
        self.service.create_directory("a/b")
        self.service.create_directory("")
        self.assertEqual(len(self.transport.requests), before)
        self.assertTrue(any("plain folder name" in note for note in notes))
        # A host refusal surfaces its words.
        self.service.create_directory("sub")
        refusal = self.request("server/files/directory?path=gcodes/sub")
        refusal.callback(None, "Directory exists")
        self.assertTrue(any("Create refused: Directory exists" in note for note in notes))

    def test_selection_toggles_and_reports_three_states(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {}), ("b.gcode", {})])
        rows = self.service.current_rows()
        self.assertEqual(self.service.selection_state(rows), "none")
        self.service.toggle_selection("a.gcode")
        self.assertEqual(self.service.selection_state(rows), "some")
        self.service.toggle_selection("b.gcode")
        self.assertEqual(self.service.selection_state(rows), "all")
        self.service.clear_selection()
        self.assertEqual(self.service.selection_state(rows), "none")

    def test_start_print_posts_the_root_exclusive_path_on_its_own_lane(self):
        self.service.open()
        self.directory("path=gcodes&", [("benchy.gcode", {})])
        self.service.start_print("benchy.gcode")
        request = self.request("print/start")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.path, "printer/print/start?filename=benchy.gcode")
        self.assertEqual(request.owner, "file-manager")
        self.assertEqual(self.service.print_attempt[0], "benchy.gcode")
        # A refused POST clears the awaited transition.
        self.deliver("print/start", {"error": "refused"}, error="refused")
        self.assertIsNone(self.service.print_attempt)

    def test_page_toggle_fills_a_partial_page_and_drops_a_full_one(self):
        self.service.open()
        self.directory("path=gcodes&", [("a.gcode", {}), ("b.gcode", {}), ("c.gcode", {})])
        # A partial page fills up, never drops.
        self.service.toggle_selection("a.gcode")
        self.service.toggle_page_selection()
        self.assertEqual(self.service.selection, {"a.gcode", "b.gcode", "c.gcode"})
        # An all-selected page is dropped entirely.
        self.service.toggle_page_selection()
        self.assertEqual(self.service.selection, set())


if __name__ == "__main__":
    unittest.main()
