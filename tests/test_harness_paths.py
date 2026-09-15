"""Regression pins for the run-dir resolution rule.

The doubled-path bug (shipped in 4.0.2): the container side of
ui_test.sh nested an ABSOLUTE run-dir name under its own ui-artifacts
prefix, so every gallery landed at /tmp/mpf/ui-artifacts//tmp/mpf/
ui-artifacts/runs/... while the gate report and CI's upload read the
straight path. Fourteen green gate jobs, zero artifacts uploaded.
The rule now lives in tools/ui_test_paths.sh and both sides of
ui_test.sh resolve through it; these tests pin the behaviour and the
wiring.
"""

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "tools" / "ui_test_paths.sh"
UI_TEST = ROOT / "tools" / "ui_test.sh"


def _resolve(base, name):
    out = subprocess.run(
        ["sh", str(HELPER), "resolve", base, name],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _container(work_dir, path):
    out = subprocess.run(
        ["sh", str(HELPER), "container", work_dir, path],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


class ContainerPathMappingTests(unittest.TestCase):
    def test_slot_prefix_rewrites_to_the_mount_root(self):
        # The slot's host dir mounts at the container's /tmp/mpf —
        # a host-form slot path must become the container form.
        self.assertEqual(
            _container("/tmp/mpf/slot-1", "/tmp/mpf/slot-1/xdg"), "/tmp/mpf/xdg"
        )
        self.assertEqual(
            _container("/tmp/mpf/slot-1", "/tmp/mpf/slot-1/ui-artifacts/run"),
            "/tmp/mpf/ui-artifacts/run",
        )

    def test_container_form_paths_pass_through(self):
        # Paths already in the container form (the hardcoded /tmp/mpf
        # sites in the runner, the driver and the boot env) are
        # untouched.
        self.assertEqual(
            _container("/tmp/mpf/slot-1", "/tmp/mpf/xdg"), "/tmp/mpf/xdg"
        )
        self.assertEqual(
            _container("/tmp/mpf/slot-1", "/tmp/mpf/harness_port.txt"),
            "/tmp/mpf/harness_port.txt",
        )

    def test_serial_run_is_the_identity(self):
        # With the shared /tmp/mpf as the work dir, the mapping is
        # the identity — the serial run's host==container premise.
        self.assertEqual(_container("/tmp/mpf", "/tmp/mpf/xdg"), "/tmp/mpf/xdg")

    def test_slot_prefix_matches_at_a_component_boundary(self):
        # slot-12 is not slot-1's tail: a naive prefix test rewrites
        # /tmp/mpf/slot-12/x to /tmp/mpf2/x.
        self.assertEqual(
            _container("/tmp/mpf/slot-1", "/tmp/mpf/slot-12/xdg"), "/tmp/mpf/slot-12/xdg"
        )
        self.assertEqual(
            _container("/tmp/mpf/slot-1", "/tmp/mpf/slot-1/xdg"), "/tmp/mpf/xdg"
        )

    def test_dotdot_components_are_refused(self):
        # A `..` in either subcommand's path would escape the scratch
        # tree (a container-side rm -rf follows it) — both refuse.
        for args in (("container", "/tmp/mpf", "/tmp/mpf/../shared"),
                     ("resolve", "/tmp/mpf", "../shared/run"),
                     ("container", "/tmp/mpf", "/tmp/mpf/x/..")):
            out = subprocess.run(
                ["sh", str(HELPER), *args], capture_output=True, text=True,
            )
            self.assertEqual(out.returncode, 2, args)


class RunDirResolutionTests(unittest.TestCase):
    def test_absolute_name_passes_through_verbatim(self):
        # The regression itself: an absolute run-dir name must never
        # gain a ui-artifacts prefix on either side of the run.
        name = "/tmp/mpf/ui-artifacts/runs/2026-09-14-220945/group-visual"
        self.assertEqual(_resolve("/tmp/mpf", name), name)

    def test_relative_name_nests_under_ui_artifacts(self):
        self.assertEqual(
            _resolve("/tmp/mpf", "run-001"), "/tmp/mpf/ui-artifacts/run-001"
        )

    def test_host_and_container_views_resolve_the_same_suffix(self):
        # The two views of the one scratch tree differ only in their
        # base; the resolved suffix must agree or the report and the
        # writer drift apart again. Resolved against two different
        # bases, the suffix must be the same (the slot mount gives
        # the container a different root path for the same tree).
        host = _resolve("/tmp/mpf", "run-001")
        container = _resolve("/tmp/mpf/slot-1", "run-001")
        self.assertTrue(host.endswith("/ui-artifacts/run-001"), host)
        self.assertTrue(container.endswith("/ui-artifacts/run-001"), container)

    def test_ui_test_resolves_both_sides_through_the_shared_rule(self):
        # The wiring: ui_test.sh must not build either side's path
        # inline — RUN_DIR resolves through the helper, and the
        # container-side path resolves through it AGAIN via the
        # container mapping. The old doubled construction is gone.
        text = UI_TEST.read_text()
        self.assertIn(
            'RUN_DIR="$(tools/ui_test_paths.sh resolve', text
        )
        self.assertIn(
            'CONTAINER_RUN_DIR="$(container_path "$(tools/ui_test_paths.sh resolve', text
        )
        self.assertIn(
            'tools/ui_test_paths.sh container "$WORK_DIR" "$1"', text
        )
        self.assertNotIn('CONTAINER_WORK_DIR}/ui-artifacts/${RUN_DIR_NAME', text)

    def test_ui_test_refuses_a_run_without_landed_evidence(self):
        # The gate half of the regression: a run whose gallery never
        # reached the reported path must fail, whatever the verdict
        # said — and the suite modes' machine-readable record must
        # land too.
        text = UI_TEST.read_text()
        self.assertIn('echo "ui_test: EVIDENCE MISSING', text)
        self.assertIn('[ ! -s "$RUN_DIR/index.html" ]', text)
        self.assertIn('[ ! -s "$RUN_DIR/evidence.json" ]', text)

    def test_boot_wait_reads_the_runs_own_work_dir(self):
        # A slot container writes its port file into ITS /tmp/mpf —
        # the wait must read the run's own work dir, never the shared
        # tree's copy (a stale shared file passed every -j unit's
        # boot gate in 4.1.0's green matrix).
        text = UI_TEST.read_text()
        self.assertIn('[ -s "$WORK_DIR"/harness_port.txt ]', text)
        self.assertNotIn('[ -s /tmp/mpf/harness_port.txt ]', text)

    def test_runner_env_carries_the_run_identity(self):
        # The evidence record's provenance fields read these from the
        # runner's environment — they must cross into the container
        # or every record says "?".
        text = UI_TEST.read_text()
        self.assertIn('CURA_VERSION="$CURA_VERSION" PLUGIN_VERSION="$PLUGIN_VERSION"', text)


class TestingDocPinTests(unittest.TestCase):
    # The TESTING.md reconciliation (the 4.1.0 workstream): the
    # document describes the real harness, and the struck claims may
    # not re-enter the text — a drift back to the false claims fails
    # here instead of at the gate.

    def _doc(self):
        return (ROOT / "TESTING.md").read_text()

    def test_reconciliation_marker_is_present(self):
        self.assertIn("Reconciliation status (2026-09-15", self._doc())

    def test_struck_claims_do_not_reenter(self):
        text = self._doc()
        # The claims the audit struck: XTEST as the activation rule,
        # the per-scenario Cura process, the ≤12-verb pin, the
        # xwd/QScreen canonical capture, the resolved-address
        # manifest, the 3-attempt and 240-minute claims, the
        # exact-version image pins — and the re-review's second pass:
        # the run-nonce handshake, the failure taxonomy, the
        # faulthandler dump, the machine-profile budgets, the
        # committed red galleries, the fixed-1600x1000 diagram, the
        # XTEST-as-canonical path, the transcript replay, the
        # metronome denial, the 45-verb and 297-layer numbers and
        # the letter catalogue.
        for phrase in ("injected through the X server's XTEST extension",
                       "its own Cura process",
                       "≤ a dozen generic",
                       "xwd -root",
                       "resolved-address manifest",
                       "up to 3 attempts",
                       "240-minute",
                       "by exact version, like the rest of the repo",
                       "run nonce",
                       "failure taxonomy",
                       "faulthandler",
                       "machine profile",
                       "commits its red gallery",
                       "fixed 1600x1000x24",
                       "exactly as a human mouse",
                       "kept in-tree and replayed",
                       "the metronome is NOT the default",
                       "45 verbs",
                       "297 layers",
                       "REAL_MOONRAKER_URL",
                       "orchestrates the whole release gate"):
            self.assertNotIn(phrase, text, phrase)


if __name__ == "__main__":
    unittest.main()
