"""The coverage matrix gate: every addressable surface in the plugin
(a @pyqtSlot verb, an objectName'd item, a protocol endpoint, a
published key) must map to a scenario or carry a justified exclusion.
A silent gap would let a feature exist with no scenario evidence."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "harness"))

from surface_coverage import check, extract  # noqa: E402
from tier2_map import EXCLUSIONS, PREFIX_RULES, SCENARIO_MAP  # noqa: E402


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
        import tier2_scenarios
        ids = {spec["id"] for spec in tier2_scenarios.SCENARIOS}
        tier1 = {f"t1-{n}" for n in range(1, 12)}
        referenced = {value for value in SCENARIO_MAP.values()}
        for _kind, _prefix, value in PREFIX_RULES:
            referenced.add(value)
        missing = (referenced - ids - tier1)
        self.assertEqual(missing, set(), "mapped scenarios with no spec: %s" % missing)


if __name__ == "__main__":
    unittest.main()
