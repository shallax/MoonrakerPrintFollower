# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-18 — 6.0.0: Cover older Cura metadata casing and normalized wheel names.
# 2026-10-16 [1d52947] Handle multiple compatible PyQt wheels deterministically
# 2026-10-16 [10580bf] Match harness QtTest wheels to Cura embedded runtime metadata
# 2026-09-14 [e168ec7] harness: fix the doubled evidence path, with tests

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

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import fetch_cura

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "tools" / "ui_test_paths.sh"
UI_TEST = ROOT / "tools" / "ui_test.sh"


def _posix_shell():
    """The shell the helper is written for, or None.

    The pins here are the SHELL script's behaviour, so they run wherever
    a POSIX shell can be found rather than only on the CI host — a
    Windows dev box qualifies: Git for Windows keeps sh.exe in its own
    usr/bin instead of on PATH, which is why the usual locations are
    named explicitly (INSTRUCTIONS.md, "Windows development"). MPF_SH
    overrides the lookup. A host with no shell at all skips: with the
    helper unrunnable, nothing is proven either way.
    """
    override = os.environ.get("MPF_SH")
    if override:
        return override
    found = shutil.which("sh")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles"),
                 os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        for relative in ("Git/usr/bin/sh.exe", "Git/bin/sh.exe"):
            candidate = Path(base) / relative
            if candidate.exists():
                return str(candidate)
    return None


SHELL = _posix_shell()
NO_SHELL = "no POSIX shell found (set MPF_SH, or install Git for Windows)"


def _helper(*args, check=True):
    if SHELL is None:
        raise unittest.SkipTest(NO_SHELL)
    return subprocess.run(
        [SHELL, str(HELPER), *args],
        capture_output=True, text=True, check=check,
    )


def _resolve(base, name):
    return _helper("resolve", base, name).stdout.strip()


def _container(work_dir, path):
    return _helper("container", work_dir, path).stdout.strip()


class CuraProfileVersionTests(unittest.TestCase):
    def test_qt_wheel_selection_handles_rebuilt_wheels_without_comparing_dicts(self):
        names = ["pyqt6-6.10.0-cp39-abi3-manylinux_2_34_x86_64.whl",
                 "pyqt6-6.10.0-1-cp39-abi3-manylinux_2_34_x86_64.whl",
                 "pyqt6-6.10.0-cp39-abi3-manylinux_2_34_aarch64.whl"]
        files = [{"filename": name} for name in names]
        for candidates in (files, list(reversed(files))):
            self.assertEqual(fetch_cura.pick_wheel(candidates, True)["filename"], names[1])

    def test_stale_cached_wheels_are_replaced_using_separate_binding_and_qt_pins(self):
        version = "5.14.0-beta.1"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            prepared = base / version
            (prepared / "root").mkdir(parents=True)
            (prepared / "UltiMaker-Cura.AppImage").write_bytes(b"cached-appimage")
            wheels = prepared / "wheels"
            wheels.mkdir()
            (wheels / "stale-file").write_bytes(b"old wheel")
            (prepared / "manifest.json").write_text(json.dumps({
                "launch": "loader", "env": "modern", "bundled_python": "3.11",
                "bundled_pyqt6": "6.6.0"}), encoding="utf-8")
            def fetch(package, pin, abi3, destination):
                name = f"{package}-{pin}.whl"
                (destination / name).write_bytes(b"wheel")
                return name
            def unpack(*args, **kwargs):
                binding = wheels / "PyQt6"
                binding.mkdir(exist_ok=True)
                (binding / "QtTest.abi3.so").write_bytes(b"test binding")
            with patch.object(fetch_cura, "BASE", base), patch("sys.argv", ["fetch_cura.py", version]), \
                    patch.object(fetch_cura, "discover", return_value=("3.11", "6.10.0", "6.10.1")), \
                    patch.object(fetch_cura, "fetch_wheel", side_effect=fetch) as downloads, \
                    patch.object(fetch_cura, "sh", side_effect=unpack):
                self.assertEqual(fetch_cura.main(), 0)
            self.assertEqual([(call.args[0], call.args[1]) for call in downloads.call_args_list],
                             [("PyQt6", "6.10.0"), ("PyQt6-Qt6", "6.10.1")])
            self.assertFalse((wheels / "stale-file").exists())
            manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["bundled_pyqt6"], "6.10.0")
            self.assertEqual(manifest["bundled_qt6"], "6.10.1")

    def test_runtime_pins_come_from_embedded_metadata_in_flat_appimages(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "cura").mkdir()
            metadata = root / "cura/CuraVersion.py"
            metadata.write_text("PythonInstalls = {'PyQt6': {'version': '6.10.0'}, "
                                "'PyQt6-Qt6': {'version': '6.10.1'}}\n"
                                "ConanInstalls = {'cpython': {'version': '3.11.9'}}\n",
                                encoding="utf-8")
            self.assertEqual(fetch_cura.discover(root), ("3.11", "6.10.0", "6.10.1"))
            metadata.unlink()
            with self.assertRaisesRegex(RuntimeError, "refusing guessed QtTest"):
                fetch_cura.discover(root)

    def test_older_cura_lowercase_metadata_keeps_independent_runtime_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "cura").mkdir()
            metadata = root / "cura/CuraVersion.py"
            # Cura 5.7's AppImage spells these keys in lowercase.
            metadata.write_text("PythonInstalls = {'pyqt6': {'version': '6.6.0'}, "
                                "'pyqt6-qt6': {'version': '6.6.0'}}\n"
                                "ConanInstalls = {'cpython': {'version': '3.10.4'}}\n",
                                encoding="utf-8")
            self.assertEqual(fetch_cura.discover(root), ("3.10", "6.6.0", "6.6.0"))
            metadata.write_text("PythonInstalls = {'pyqt6': {'version': '6.6.0'}, "
                                "'pyqt6_qt6': {'version': '6.6.1'}}\n"
                                "ConanInstalls = {'cpython': {'version': '3.10.4'}}\n",
                                encoding="utf-8")
            self.assertEqual(fetch_cura.discover(root), ("3.10", "6.6.0", "6.6.1"))
            metadata.write_text("PythonInstalls = {'pyqt6': {'version': '6.6.0'}}\n"
                                "ConanInstalls = {'cpython': {'version': '3.10.4'}}\n",
                                encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "refusing guessed QtTest"):
                fetch_cura.discover(root)

    def test_wheel_metadata_names_are_case_and_separator_independent(self):
        for binding, qt in (("PyQt6", "PyQt6_Qt6"), ("pyqt6", "pyqt6_qt6")):
            with self.subTest(binding=binding), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / "usr/bin").mkdir(parents=True)
                (root / "usr/bin/python3.10").touch()
                (root / f"{binding}-6.6.0.dist-info").mkdir()
                runtime = root / f"{qt}-6.6.1.dist-info"
                runtime.mkdir()
                self.assertEqual(fetch_cura.discover(root), ("3.10", "6.6.0", "6.6.1"))
                runtime.rmdir()
                with self.assertRaisesRegex(RuntimeError, "refusing guessed QtTest"):
                    fetch_cura.discover(root)

    @unittest.skipIf(os.name == "nt", "POSIX access modes")
    def test_extracted_tools_are_readable_across_uids_without_world_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            root.mkdir(mode=0o700)
            nested = root / "lib"
            nested.mkdir(mode=0o700)
            data = nested / "library.so"
            data.write_bytes(b"fixture")
            data.chmod(0o600)
            binary = root / "UltiMaker-Cura"
            binary.write_bytes(b"fixture")
            binary.chmod(0o700)
            outside = Path(temp) / "outside"
            outside.write_bytes(b"untouched")
            outside.chmod(0o600)
            (root / "external-link").symlink_to(outside)
            fetch_cura.make_host_tools_readable(root)
            self.assertEqual(root.stat().st_mode & 0o777, 0o755)
            self.assertEqual(nested.stat().st_mode & 0o777, 0o755)
            self.assertEqual(data.stat().st_mode & 0o777, 0o644)
            self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o600)

    def test_prepared_beta_uses_the_modern_boot_environment(self):
        version = "5.14.0-beta.1"
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            manifest_path = base / version / "manifest.json"
            manifest_path.parent.mkdir()
            root = manifest_path.parent / "root"
            root.mkdir(mode=0o700)
            wheels = manifest_path.parent / "wheels"
            wheels.mkdir(mode=0o700)
            manifest_path.write_text(json.dumps({"launch": "loader", "env": "legacy",
                "bundled_python": "3.11", "bundled_pyqt6": "6.10.0", "bundled_qt6": "6.10.0"}),
                                     encoding="utf-8")
            with patch.object(fetch_cura, "BASE", base), patch("sys.argv", ["fetch_cura.py", version]), \
                    patch.object(fetch_cura, "discover", return_value=("3.11", "6.10.0", "6.10.0")):
                self.assertEqual(fetch_cura.main(), 0)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["env"], "modern")
            if os.name != "nt":
                self.assertEqual(root.stat().st_mode & 0o555, 0o555)
                self.assertEqual(wheels.stat().st_mode & 0o555, 0o555)

    @unittest.skipUnless(SHELL, NO_SHELL)
    def test_beta_and_stable_releases_use_major_minor_profile_directories(self):
        script = UI_TEST.read_text(encoding="utf-8")
        assignment = next(line for line in script.splitlines() if line.startswith("SEED_VER="))
        for version, expected in (("5.13.0", "5.13"), ("5.14.0-beta.1", "5.14")):
            with self.subTest(version=version):
                result = subprocess.run(
                    [SHELL, "-c", assignment + '\nprintf "%s" "$SEED_VER"'],
                    env={**os.environ, "CURA_VERSION": version},
                    capture_output=True, text=True, check=True)
                self.assertEqual(result.stdout, expected)


class ContainerPathMappingTests(unittest.TestCase):
    def test_runner_temp_gallery_maps_to_the_container_mount(self):
        self.assertEqual(
            _container("/runner/_temp/mpf", "/runner/_temp/mpf/ui-artifacts/runs/demo"),
            "/tmp/mpf/ui-artifacts/runs/demo",
        )

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
            out = _helper(*args, check=False)
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
        text = UI_TEST.read_text(encoding="utf-8")
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
        text = UI_TEST.read_text(encoding="utf-8")
        self.assertIn('echo "ui_test: EVIDENCE MISSING', text)
        self.assertIn('[ ! -s "$RUN_DIR/index.html" ]', text)
        self.assertIn('[ ! -s "$RUN_DIR/evidence.json" ]', text)

    def test_boot_wait_reads_the_runs_own_work_dir(self):
        # A slot container writes its port file into ITS /tmp/mpf —
        # the wait must read the run's own work dir, never the shared
        # tree's copy (a stale shared file passed every -j unit's
        # boot gate in 4.1.0's green matrix).
        text = UI_TEST.read_text(encoding="utf-8")
        self.assertIn('[ -s "$WORK_DIR"/harness_port.txt ]', text)
        self.assertNotIn('[ -s /tmp/mpf/harness_port.txt ]', text)

    def test_runner_env_carries_the_run_identity(self):
        # The evidence record's provenance fields read these from the
        # runner's environment — they must cross into the container
        # or every record says "?".
        text = UI_TEST.read_text(encoding="utf-8")
        self.assertIn('CURA_VERSION="$CURA_VERSION" PLUGIN_VERSION="$PLUGIN_VERSION"', text)


class TestingDocPinTests(unittest.TestCase):
    # The TESTING.md reconciliation (the 4.1.0 workstream): the
    # document describes the real harness, and the struck claims may
    # not re-enter the text — a drift back to the false claims fails
    # here instead of at the gate.

    def _doc(self):
        return (ROOT / "docs" / "TESTING.md").read_text(encoding="utf-8")

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

    def test_the_capture_gate_and_its_cost_are_documented(self):
        # Native capture is restored with explicit window bounds. Keep
        # the override and the limit of software-rendered evidence clear.
        text = " ".join(self._doc().split())
        self.assertIn("Native screenshots and recordings are enabled", text)
        self.assertIn("availableGeometry", text)
        self.assertIn("bounds watchdog", text)
        self.assertIn("HARNESS_CAPTURE=off", text)
        self.assertIn("not a benchmark of a physical Mac", text)
        # The liveness verdict rides the leg rather than the platform, and
        # what a miss MEANS there is documented beside the gate rather
        # than folded into its cost: a judged stall where the leg reads
        # its screen, a report-only diagnostic where it does not.
        self.assertIn("The renderer-liveness verdict rides the leg, not the platform", text)
        self.assertIn("report-only diagnostic", text)
        self.assertIn("HEARTBEAT REPORT-ONLY", text)
        self.assertIn("frames_outcome", text)
        # The heartbeat itself and the current macOS capture verdict.
        self.assertIn("mpfLivenessHeartbeat", text)
        self.assertIn("Native macOS capture is now on by default and judged", text)
        # The still-span rule's own change, which is what clears the
        # windows group-status red without retiring the check.
        self.assertIn("A still span nobody drove is not judged", text)

    def test_every_staged_harness_module_reaches_the_work_dir(self):
        """The staged tree is what the run imports, not the checkout.

        The runner is copied flat into the work dir and imports its
        siblings from there, so a harness module that grows — or becomes
        a package — and is not staged fails forty minutes into a gate
        rather than here. The scenario suite is a PACKAGE: a single-file
        copy would leave the staged runner importing nothing.
        """
        text = UI_TEST.read_text(encoding="utf-8")
        for name in ("runner.py", "native_host.py", "log_gate.py",
                     "window_geometry.py", "scenario_map.py",
                     "surface_coverage.py", "liveness.py", "static_leg.py"):
            self.assertIn('cp "$root/tests/harness/%s"' % name, text,
                          "%s is no longer staged" % name)
        self.assertIn('cp "$root"/tests/harness/scenarios/*.py "$WORK_DIR"/scenarios/',
                      text, "the scenario package's modules are not staged")
        self.assertNotIn('cp "$root/tests/harness/scenarios.py"', text,
                         "the flat-module copy is back and would stage nothing")
        # And the directory is never REPLACED. The container imports the
        # package as root, so Python writes root-owned __pycache__ beside
        # the sources and an `rm -rf` of the staged directory fails on
        # them: the second unit of a repeat-boot run could not stage at
        # all, and every one of its .pyc files reported
        # "Permission denied" before the unit failed. Everything else the
        # script removes under the work dir is either host-owned or
        # removed through the container's own root.
        self.assertNotIn('rm -rf "$WORK_DIR"/scenarios', text,
                         "the staged package is removed again, which the "
                         "container's own bytecode makes impossible")
        # Every module the harness directory holds is either staged into
        # the work dir, staged into the plugin, or a host-side test leg.
        staged = {"runner.py", "native_host.py", "log_gate.py",
                  "window_geometry.py", "scenario_map.py",
                  "surface_coverage.py", "simulator.py", "simulator_serve.py",
                  "gcodegen.py", "liveness.py", "static_leg.py"}
        host_only = {"test_harness_specs.py", "test_harness_runner.py",
                     "test_harness_native.py", "test_harness_seed.py",
                     "seed_variants.py"}
        present = {path.name for path in (ROOT / "tests" / "harness").glob("*.py")}
        self.assertGreater(len(present), 10, "the harness directory scan found nothing")
        unaccounted = present - staged - host_only
        self.assertEqual(unaccounted, set(),
                         "harness modules neither staged nor host-only: %s"
                         % sorted(unaccounted))


if __name__ == "__main__":
    unittest.main()
