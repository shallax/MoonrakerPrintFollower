"""The coverage matrix gate: every addressable surface in the plugin
(a @pyqtSlot verb, an objectName'd item, a protocol endpoint, a
published key) must map to a scenario or carry a justified exclusion.
A silent gap would let a feature exist with no scenario evidence."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "harness"))

from surface_coverage import check, check_evidence, extract  # noqa: E402
from scenario_map import EXCLUSIONS, PREFIX_RULES, SCENARIO_MAP  # noqa: E402


class CoverageMatrixTests(unittest.TestCase):

    def test_every_surface_is_mapped_or_excluded(self):
        mapping = dict(SCENARIO_MAP)
        mapping["_prefix_rules"] = PREFIX_RULES
        mapping["_exclusions"] = EXCLUSIONS
        failures = check(mapping)
        self.assertEqual(failures, [], "uncovered surfaces: %s" % failures)

    def test_the_matrix_is_not_empty(self):
        surfaces = extract()
        self.assertGreater(len(surfaces["slot"]), 100)
        self.assertGreater(len(surfaces["key"]), 100)
        self.assertGreaterEqual(len(surfaces["route"]), 10)
        self.assertGreaterEqual(len(surfaces["objectName"]), 30)

    def test_every_mapped_scenario_has_a_spec(self):
        import scenarios
        ids = {spec["id"] for spec in scenarios.SCENARIOS}
        referenced = {value for value in SCENARIO_MAP.values()}
        for _kind, _prefix, value in PREFIX_RULES:
            referenced.add(value)
        missing = (referenced - ids)
        self.assertEqual(missing, set(), "mapped scenarios with no spec: %s" % missing)

    def test_every_exclusion_carries_the_schema(self):
        # Workstream 4: an exclusion is reason + evidence + date +
        # re-check trigger — a one-line prose entry cannot expire.
        for name, entry in EXCLUSIONS.items():
            for field in ("reason", "evidence", "date", "recheck"):
                self.assertIn(field, entry, f"{name} lacks {field!r}")
                self.assertTrue(str(entry[field]).strip(), f"{name}: {field} is empty")

    def test_execution_check_requires_the_scenario_and_the_surface(self):
        # The evidence-side half: a mapped scenario that never ran,
        # and a running scenario whose steps never name the item,
        # are both failures.
        steps = [
            {"scenario": "g1", "spec": {"op": "deliver_click",
                                        "objectName": "moonrakerJogXPlus"}},
        ]
        failures = check_evidence(
            {"moonrakerJogXPlus": "g1", "moonrakerHomeX": "g2"}, [], steps)
        self.assertIn("scenario:g2 has no evidence steps in the run", failures)
        self.assertNotIn("moonrakerJogXPlus", " ".join(failures))

    def test_execution_check_names_the_item_through_the_step(self):
        # A step names an item by its spec values — the normalized
        # match crosses separators (a jog click names the jog pad).
        steps = [{"scenario": "g1",
                  "spec": {"op": "deliver_click",
                           "objectName": "moonrakerJogXPlus"}}]
        failures = check_evidence(
            {"MoonrakerMonitorModel.jog": "g1", "moonrakerJogXPlus": "g1"},
            [], steps)
        self.assertEqual(failures, [], failures)

    def test_execution_check_holds_qualified_names_to_presence_only(self):
        # Slots and routes are exercised through the scenario's own
        # assertions — presence is the contract, never naming.
        steps = [{"scenario": "g6", "spec": {"op": "exec_slot", "slot": "pausePrint"}}]
        failures = check_evidence(
            {"MoonrakerMonitorModel.cancelPrint": "g6",
             "print_start_endpoint": "g6"}, [], steps)
        self.assertEqual(failures, [], failures)


if __name__ == "__main__":
    unittest.main()
