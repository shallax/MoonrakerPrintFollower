"""The plate map's projection: polygon normalisation, sanitisation, the
natural-sort order and the truncation cap — pure, lane-agnostic.
"""
from __future__ import annotations

import unittest

from plugins.MonitorFormatting import MAX_PLATE_OBJECTS, plate_values


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


if __name__ == "__main__":
    unittest.main()
