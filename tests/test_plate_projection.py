"""The plate map's projection: polygon normalisation, sanitisation, the
natural-sort order, the truncation cap and the memo the coordinator's
poll path rides — pure, lane-agnostic.
"""
from __future__ import annotations

import copy
import unittest

from plugins.MonitorFormatting import MAX_PLATE_OBJECTS, PlateProjectionMemo, plate_values


class _RaisingEqual:
    """A value whose equality raises: the payload the memo's cheap
    compare cannot judge, and must therefore degrade on."""

    def __eq__(self, other):
        raise RuntimeError("not comparable")


class PlateValuesTests(unittest.TestCase):
    # The live fixture: a DEFINE line from the 50-copy capture, exactly
    # as Klipper reported it (names uppercased by Klipper; coordinates
    # absolute bed mm, passed through unmodified — the domain round).
    LIVE = {
        "objects": [
            {"name": "DELETEDAYKEYCHAIN_STL_44",
             "center": [34.540, 17.511],
             "polygon": [[23.692, 6.456], [23.692, 29.72],
                         [46.304, 29.72], [46.304, 6.456]]},
        ],
        "excluded_objects": [],
        "current_object": None,
    }

    def test_pairs_polygons_pass_through(self):
        result = plate_values(self.LIVE)
        row = result["objects"][0]
        self.assertEqual(row["center"], [34.540, 17.511])
        self.assertEqual(row["polygon"][0], [23.692, 6.456])
        self.assertEqual(len(row["polygon"]), 4)

    def test_flat_polygons_normalise_to_pairs(self):
        flat = [23.692, 6.456, 23.692, 29.72, 46.304, 29.72, 46.304, 6.456]
        result = plate_values({"objects": [{"name": "a",
                                            "polygon": flat}],
                               "excluded_objects": []})
        self.assertEqual(result["objects"][0]["polygon"][-1], [46.304, 6.456])

    def test_garbage_polygons_fall_back_to_the_centre_dot(self):
        for bad in ("text", [1, 2, 3], [[1, 2]], [[1, 2], [3, 4]],
                    [[float("nan"), 2], [3, 4], [5, 6]],
                    [[1e308, 2], [3, 4], [5, 6]]):
            result = plate_values({"objects": [{"name": "a", "polygon": bad}],
                                   "excluded_objects": []})
            self.assertIsNone(result["objects"][0]["polygon"], repr(bad))

    def test_the_natural_sort_recovers_the_name_numbering(self):
        names = ["STL_1", "STL_10", "STL_2", "STL", "STL_11", "STL_9"]
        result = plate_values({"objects": [{"name": name} for name in names],
                               "excluded_objects": []})
        self.assertEqual([row["name"] for row in result["objects"]],
                         ["STL", "STL_1", "STL_2", "STL_9", "STL_10", "STL_11"])
        # The define sequence survives for the printed rule.
        self.assertEqual([row["order"] for row in result["objects"]],
                         [3, 0, 2, 5, 1, 4])

    def test_excluded_and_current_flags(self):
        status = {"objects": [{"name": "A"}, {"name": "B"}],
                  "excluded_objects": ["A"], "current_object": "B"}
        result = plate_values(status)
        by_name = {row["name"]: row for row in result["objects"]}
        self.assertTrue(by_name["A"]["excluded"])
        self.assertTrue(by_name["B"]["current"])
        self.assertEqual(result["excludedCount"], 1)

    def test_the_truncation_cap_states_what_it_dropped(self):
        status = {"objects": [{"name": f"obj_{index}"}
                              for index in range(MAX_PLATE_OBJECTS + 7)],
                  "excluded_objects": []}
        result = plate_values(status)
        self.assertEqual(len(result["objects"]), MAX_PLATE_OBJECTS)
        self.assertEqual(result["truncated"], 7)

    def test_a_missing_exclude_object_reads_as_an_empty_plate(self):
        result = plate_values(None)
        self.assertEqual(result["objects"], [])
        self.assertEqual(result["truncated"], 0)

    def test_absent_objects_with_exclusions_still_reports_the_count(self):
        result = plate_values({"excluded_objects": ["A", "B"], "objects": []})
        self.assertEqual(result["excludedCount"], 2)


class PlateProjectionMemoTests(unittest.TestCase):
    """The projection memo: the projection's VALUE is reused while the
    definition is unchanged (a returned dict object is the observable —
    reuse hands back the same one, a re-walk builds a new one), and every
    field the projection reads can invalidate it."""

    SOURCE = {
        "objects": [
            {"name": "A", "center": [1.0, 2.0],
             "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]},
            {"name": "B", "center": [4.0, 5.0],
             "polygon": [[3.0, 3.0], [4.0, 3.0], [4.0, 4.0], [3.0, 4.0]]},
        ],
        "excluded_objects": [],
        "current_object": None,
    }

    def setUp(self):
        self.memo = PlateProjectionMemo()

    def _project(self, source=None, job="cube.gcode"):
        return self.memo.value(copy.deepcopy(self.SOURCE if source is None else source), job)

    def test_an_unchanged_definition_is_reused(self):
        first = self._project()
        self.assertIs(first, self._project(), "the same definition was re-walked")

    def test_every_field_the_projection_reads_can_invalidate(self):
        mutations = {
            "name": lambda source: source["objects"][0].update(name="A2"),
            "centre": lambda source: source["objects"][0].update(center=[9.0, 9.0]),
            "centre pair count": lambda source: source["objects"][0].update(center=[9.0, 9.0, 9.0]),
            "ring": lambda source: source["objects"][0].update(polygon=[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]),
            "ring length": lambda source: source["objects"][0]["polygon"].append([2.0, 1.0]),
            "ring replaced by a scalar": lambda source: source["objects"][0].update(polygon=7),
            "row order": lambda source: source["objects"].reverse(),
            "row added": lambda source: source["objects"].append(
                {"name": "C", "polygon": [[5.0, 5.0], [6.0, 5.0], [6.0, 6.0]]}),
            "excluded": lambda source: source.update(excluded_objects=["A"]),
            "current": lambda source: source.update(current_object="B"),
        }
        for label, mutate in mutations.items():
            with self.subTest(field=label):
                memo = PlateProjectionMemo()
                first = memo.value(copy.deepcopy(self.SOURCE), "cube.gcode")
                source = copy.deepcopy(self.SOURCE)
                mutate(source)
                self.assertIsNot(first, memo.value(source, "cube.gcode"),
                                 "%s did not invalidate the projection" % label)

    def test_a_late_define_arrival_re_walks_and_carries_the_row(self):
        first = self._project()
        source = copy.deepcopy(self.SOURCE)
        source["objects"].append({"name": "LATE",
                                  "polygon": [[7.0, 7.0], [8.0, 7.0], [8.0, 8.0]]})
        again = self._project(source)
        self.assertIsNot(first, again)
        self.assertIn("LATE", [row["name"] for row in again["objects"]])

    def test_a_new_job_drops_the_definition(self):
        first = self._project(job="cube.gcode")
        self.assertIsNot(first, self._project(job="other.gcode"),
                         "another print reused the previous definition")
        # None is a real job value (the monitor-only print's unresolved
        # identity), not a "no job" escape.
        self.assertIsNot(first, self._project(job=None))

    def test_a_payload_mutated_after_it_was_judged_is_not_served(self):
        # The memo keeps a COPY of what it judged: a payload mutated in
        # place (a caller sharing the rows with another lane) must not
        # read as unchanged.
        source = copy.deepcopy(self.SOURCE)
        first = self.memo.value(source, "cube.gcode")
        source["objects"][0]["polygon"][0][0] = 42.0
        self.assertIsNot(first, self.memo.value(source, "cube.gcode"))

    def test_the_same_points_in_other_containers_are_the_same_definition(self):
        # A tuple where the payload sent lists (a re-parse, another
        # lane's payload): equal points are an unchanged definition and
        # must not cost a ring walk — while the payload's own shape is
        # what keeps the steady-state compare C-level.
        listed = {"objects": [{"name": "A", "center": [1.0, 2.0],
                               "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]}],
                  "excluded_objects": []}
        first = self.memo.value(listed, "cube.gcode")
        tupled = {"objects": [{"name": "A", "center": (1.0, 2.0),
                               "polygon": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0))}],
                  "excluded_objects": []}
        self.assertIs(first, self.memo.value(tupled, "cube.gcode"))

    def test_rows_a_payload_cannot_carry_are_judged_as_absent(self):
        # A stray scalar among the rows (the projection skips anything
        # that is not a named mapping) neither invalidates on its own
        # nor hides a row that appears in its place.
        source = {"objects": [{"name": "A", "polygon": None}, "junk"],
                  "excluded_objects": []}
        first = self.memo.value(copy.deepcopy(source), "cube.gcode")
        self.assertIs(first, self.memo.value(copy.deepcopy(source), "cube.gcode"))
        appeared = {"objects": [{"name": "A", "polygon": None}, {"name": "B"}],
                    "excluded_objects": []}
        self.assertIsNot(first, self.memo.value(appeared, "cube.gcode"))

    def test_a_source_that_cannot_be_judged_is_projected_every_poll(self):
        # `objects` as a generator: nothing to compare cheaply, so the
        # projection is rebuilt rather than served stale.
        def rows():
            return (row for row in copy.deepcopy(self.SOURCE)["objects"])

        first = self.memo.value({"objects": rows()}, "cube.gcode")
        self.assertIsNot(first, self.memo.value({"objects": rows()}, "cube.gcode"))

    def test_a_missing_definition_memoises_the_empty_projection(self):
        first = self.memo.value(None, "cube.gcode")
        self.assertEqual(first["objects"], [])
        self.assertIs(first, self.memo.value(None, "cube.gcode"))

    def test_a_payload_the_compare_cannot_judge_is_projected_fresh(self):
        # A value whose equality raises must degrade to a fresh walk —
        # the poll path cannot take an exception from the memo.
        source = {"objects": [{"name": "A", "polygon": [_RaisingEqual()]}],
                  "excluded_objects": []}
        first = self.memo.value(source, "cube.gcode")
        self.assertEqual(first["objects"][0]["polygon"], None)
        self.assertIsNot(first, self.memo.value(copy.deepcopy(source), "cube.gcode"))


if __name__ == "__main__":
    unittest.main()
