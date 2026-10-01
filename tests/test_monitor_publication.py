"""The publication owner: one map, one QVariant cache, one changed-group run.

The contracts here are the ones the facade's poll depends on: an unchanged
value never re-converts (the mature chart payload costs milliseconds per
conversion), a quiet poll emits nothing, and a changed group is named in the
table's order — which is the order the Qt signals fire in.
"""
from __future__ import annotations

import unittest

from PyQt6.QtCore import QVariant

from mpf.monitor.MonitorPublication import SIGNAL_GROUPS, MonitorPublication


class MonitorPublicationTests(unittest.TestCase):
    def setUp(self):
        self.publication = MonitorPublication()

    def test_an_unchanged_value_keeps_its_converted_identity(self):
        payload = {"series": [{"name": "extruder"}]}
        self.publication.store({"temperatureChartFull": payload})
        first = self.publication.read(QVariant, "temperatureChartFull", {})
        self.assertIs(self.publication.read(QVariant, "temperatureChartFull", {}), first,
                      "an unchanged value must not re-convert per read")
        # A REBUILT payload of equal content is still a new value: the
        # cache keys on identity, and the producers memoise their own.
        self.publication.store({"temperatureChartFull": dict(payload)})
        rebuilt = self.publication.read(QVariant, "temperatureChartFull", {})
        self.assertIsNot(rebuilt, first)
        self.assertEqual(rebuilt.value(), payload)

    def test_a_plain_kind_is_stored_unconverted(self):
        self.publication.store({"monitorProgress": 12.5})
        self.assertIsInstance(self.publication.read(float, "monitorProgress", 0.0), float)

    def test_a_string_kind_is_not_treated_as_the_qvariant_type(self):
        # The string spelling ("QVariant") is what a handful of declarations
        # use; only the type object converts.
        payload = [1, 2]
        self.publication.store({"plateNavigationSplit": payload})
        self.assertIs(self.publication.read("QVariant", "plateNavigationSplit", None), payload)

    def test_an_absent_key_reads_its_declared_default(self):
        self.assertEqual(self.publication.read(str, "monitorEta", "—"), "—")
        self.assertEqual(self.publication.get("monitorEta", "fallback"), "fallback")

    def test_a_quiet_poll_names_no_group(self):
        values = {"monitorState": "Printing", "plateObjects": {"objects": []}}
        self.publication.store(values)
        self.assertEqual(self.publication.changed(dict(values)), [])

    def test_only_the_moved_groups_are_named_in_table_order(self):
        self.publication.store({"monitorState": "Printing", "plateObjects": {"objects": []},
                                "consoleLines": ["a"]})
        previous = dict(self.publication.values)
        self.publication.store({"monitorState": "Paused", "plateObjects": {"objects": []},
                                "consoleLines": ["a", "b"]})
        self.assertEqual(self.publication.changed(previous),
                         ["monitorChanged", "consoleChanged"])

    def test_a_vanished_key_counts_as_a_change(self):
        self.publication.store({"monitorMessage": "hello"})
        previous = dict(self.publication.values)
        self.publication.store({})
        self.assertEqual(self.publication.changed(previous), ["monitorChanged"])

    def test_the_table_names_every_group_once_and_keeps_its_keys(self):
        names = [name for name, _keys in SIGNAL_GROUPS]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("followerViewChanged", names)
        self.assertLess(names.index("followerViewChanged"), names.index("plateProgressChanged"),
                        "the follower's attach state must precede the plate payloads")
        groups = dict(SIGNAL_GROUPS)
        for group, keys in groups.items():
            self.assertIsInstance(keys, tuple, group)
            self.assertEqual(len(keys), len(set(keys)), group)
        self.assertEqual(groups["plateScrubVectorChanged"], ("plateScrubVector",))
        self.assertNotIn("plateScrubVector", groups["plateProgressChanged"])

    def test_the_stored_map_is_the_object_the_facade_amends(self):
        # The camera transition amends the frame it was committed into: the
        # store must hand the same object on, not a copy.
        values = {"cameraRefreshNonce": 1}
        self.publication.store(values)
        values["cameraRefreshNonce"] = 2
        self.assertEqual(self.publication.get("cameraRefreshNonce"), 2)
        self.assertIs(self.publication.values, values)

    def test_amending_one_key_leaves_its_neighbours_alone(self):
        # The light publish (the thumbnail flush) amends the committed
        # frame instead of rebuilding every row.
        self.publication.store({"consoleLines": ["a"], "monitorState": "Printing"})
        self.publication.set("fileManagerThumbs", ["t"])
        self.assertEqual(self.publication.get("fileManagerThumbs"), ["t"])
        self.assertEqual(self.publication.get("consoleLines"), ["a"])
        self.assertEqual(self.publication.values["monitorState"], "Printing")


if __name__ == "__main__":
    unittest.main()
