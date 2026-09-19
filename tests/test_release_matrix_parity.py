"""The release-matrix parity gate: the GitHub Release workflow's
logical units must match the local canonical release gate's units —
the 4.5.0 drift class where units existed in one place and never ran
in the other. A controlled text parser reads both declarations (the
workflow's UNITS JSON and matrix list, the local script's UNITS
construction) and compares their logical units, modes and groups
against one canonical list; a deliberate change to either side must
change the other in the same commit or this test names the drift."""
from __future__ import annotations

import json
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
LOCAL = (ROOT / "tools" / "harness_release.sh").read_text(encoding="utf-8")
SWEEP = (ROOT / ".github" / "workflows" / "sweep.yml").read_text(encoding="utf-8")

# The canonical release matrix: both smokes, every scenario group in
# the coverage map, and the first-install leg. The real-printer group
# and the dwell soak are out of the release path by design.
EXPECTED_UNITS = (
    "smoke-primary",
    "group-connection",
    "group-status",
    "group-temperatures",
    "group-console",
    "group-webcams",
    "group-files",
    "group-motion",
    "group-printing",
    "group-settings",
    "group-visual",
    "group-preview",
    "group-probe",
    "group-configure",
    "group-stress",
    "firstinstall",
    "smoke-secondary",
)

# The group loop's order in the local script (informational — the
# parity check is set-based, but the loop itself is pinned so a group
# silently dropped from it is a visible diff).
LOCAL_GROUPS = (
    "connection", "status", "temperatures", "console", "webcams", "files",
    "motion", "printing", "settings", "visual", "preview", "probe",
    "configure", "stress",
)

# The UI Version Sweep's per-version units: every release unit — the
# smoke set (one per version, no primary/secondary split), every
# group (named by its bare group) and the first-install leg.
EXPECTED_SWEEP_UNITS = ("smoke",) + LOCAL_GROUPS + ("firstinstall",)

EXPECTED_SWEEP_VERSIONS = (
    "5.7.0", "5.8.0", "5.9.0", "5.10.0", "5.11.0", "5.12.0", "5.13.0",
)


def workflow_units():
    """The workflow's UNITS declaration: name -> {mode, group}."""
    match = re.search(r"UNITS: '(\{.*\})'", WORKFLOW, re.S)
    if not match:
        raise AssertionError("release.yml's UNITS declaration was not found")
    return json.loads(match.group(1))


def sweep_units():
    """The UI Version Sweep's per-version unit list: name -> {mode,
    group}. Every release unit must run on every supported Cura
    version, so this set is the canonical units with the version-smoke
    named plain `smoke` (one smoke per version, no primary/secondary
    split)."""
    units = {}
    for match in re.finditer(r"- \{name: (\S+), mode: (\S+), group: (\S+)\}",
                             SWEEP):
        name, mode, group = match.group(1), match.group(2), match.group(3)
        units[name] = {"mode": mode, "group": group}
    return units


def workflow_matrix_names():
    """The gate job's matrix unit list (anchored between the gate
    job's `matrix:` and its `env:` — the unit-test jobs' matrices
    carry python versions, not units)."""
    gate = WORKFLOW[WORKFLOW.index("matrix:"):WORKFLOW.index("    env:")]
    return [match.group(1) for match in re.finditer(r"^\s+- (\S+)$", gate, re.M)]


def local_units():
    """The local script's logical units: name -> {mode, group}.
    The script names the secondary smoke plain `smoke`; its logical
    name resolves through the version slot."""
    units = {}
    for match in re.finditer(r"(\d+) (\$PRIMARY|\$SECONDARY) (\S+) (\S+)(?: (\S+))?",
                             LOCAL):
        version, name, mode = match.group(2), match.group(3), match.group(4)
        if "$g" in match.group(0):
            continue  # the group loop's template line, not a unit
        group = (match.group(5) or "").rstrip('"')
        mode = mode.rstrip('"')
        key = name
        if version == "$SECONDARY":
            key += "-secondary"
        elif name == "smoke":
            key += "-primary"
        units[key] = {"mode": mode, "group": group}
    groups = re.search(r"for g in ([a-z ]+); do", LOCAL)
    if not groups:
        raise AssertionError("harness_release.sh's group loop was not found")
    for group in groups.group(1).split():
        units["group-" + group] = {"mode": "suite", "group": group}
    return units


class ReleaseMatrixParityTests(unittest.TestCase):
    def test_the_workflow_matrix_matches_the_units_declaration(self):
        names = workflow_matrix_names()
        declared = set(workflow_units())
        self.assertEqual(set(names), declared,
                         "a matrix unit is missing from the UNITS declaration "
                         "(or an UNITS entry is never scheduled)")

    def test_the_workflow_and_the_local_gate_declare_the_same_units(self):
        self.assertEqual(set(workflow_units()), set(local_units()))

    def test_the_canonical_matrix_is_complete(self):
        self.assertEqual(set(workflow_units()), set(EXPECTED_UNITS))
        self.assertEqual(set(local_units()), set(EXPECTED_UNITS))

    def test_mode_and_group_mappings_do_not_drift(self):
        for name, expected in local_units().items():
            declared = workflow_units()[name]
            self.assertEqual(declared.get("mode"), expected["mode"], name)
            if expected["mode"] == "suite":
                # Non-suite modes carry no scenario group in the
                # workflow (their group slot is a no-op).
                self.assertEqual(declared.get("group"), expected["group"], name)

    def test_the_smokes_cover_both_pinned_versions(self):
        declared = workflow_units()
        self.assertEqual(declared["smoke-primary"]["cura"], "5.13.0")
        self.assertEqual(declared["smoke-secondary"]["cura"], "5.12.0")
        local = LOCAL
        self.assertIn("$SECONDARY smoke suite smoke", local)

    def test_the_local_group_loop_carries_every_group(self):
        groups = re.search(r"for g in ([a-z ]+); do", LOCAL).group(1).split()
        self.assertEqual(tuple(groups), LOCAL_GROUPS)

    def test_the_sweep_carries_every_unit_on_every_version(self):
        units = sweep_units()
        self.assertEqual(set(units), set(EXPECTED_SWEEP_UNITS),
                         "a release unit is missing from the UI Version Sweep")
        # Every sweep unit's mode/group mapping matches the canonical
        # local gate mapping (the version smoke behaves like the
        # primary smoke's suite unit; groups carry their bare names).
        expected = dict(local_units())
        expected["smoke"] = {"mode": "suite", "group": "smoke"}
        for name, declared in units.items():
            canonical = expected[name] if name in ("smoke", "firstinstall") \
                else expected["group-" + name]
            self.assertEqual(declared["mode"], canonical["mode"], name)
            if declared["mode"] == "suite":
                self.assertEqual(declared["group"], canonical["group"], name)

    def test_the_sweep_covers_the_supported_version_range(self):
        versions = tuple(re.findall(r'- "(\d+\.\d+\.\d+)"', SWEEP))
        self.assertEqual(versions, EXPECTED_SWEEP_VERSIONS)


if __name__ == "__main__":
    unittest.main()
