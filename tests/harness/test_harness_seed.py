"""The seeded config variants' pins.

The two-boot legs boot from a state the committed fixture is NOT, and
each transform must LAND: a silent no-op would run the leg against the
fixture it exists to differ from, and the leg would then pass for the
wrong reason. The last test here is the one that guards that against
the fixture itself — it applies the transform to a copy of the real
seed, so a fixture that stops carrying the plugin's config fails this
file instead of quietly changing what the first-install leg proves.

Run directly (like the other harness files):
`python3 tests/harness/test_harness_seed.py`.
"""

import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import seed_variants

ROOT = HERE.parent.parent
FIXTURE = ROOT / "tests" / "harness" / "config" / "config" / "cura" / "5.13"

CFG = """[general]
version = 5

[cura]
active_machine = FDM Printer Base Description

[moonrakerprintfollower]
printer_configs_v1 = {"Old Printer": {"url": "http://127.0.0.1:7125"}}

[some_other_plugin]
value = keep me
"""


class TransformTests(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="seed-variants-", dir="/tmp/mpf"))
        (self.dir / "cura.cfg").write_text(CFG, encoding="utf-8")
        (self.dir / "MoonrakerPrintFollower").mkdir()
        (self.dir / "MoonrakerPrintFollower" / "settings.json").write_text("{}", encoding="utf-8")
        (self.dir / "moonrakerprintfollower_sections.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_clean_removes_what_a_first_install_must_not_have(self):
        removed = seed_variants.clean(self.dir)
        self.assertFalse((self.dir / "MoonrakerPrintFollower").exists())
        self.assertFalse((self.dir / "moonrakerprintfollower_sections.json").exists())
        text = (self.dir / "cura.cfg").read_text(encoding="utf-8")
        self.assertNotIn("[moonrakerprintfollower]", text)
        # Cura's own state stays: this is a machine that has never run
        # the PLUGIN, not a fresh Cura.
        self.assertIn("[cura]", text)
        self.assertIn("active_machine = FDM Printer Base Description", text)
        # The section's body left with it, not just its header.
        self.assertNotIn("printer_configs_v1", text)
        # And the OTHER plugin's section is not caught by the sweep.
        self.assertIn("[some_other_plugin]", text)
        self.assertIn("value = keep me", text)
        self.assertEqual(len(removed), 3)

    def test_clean_refuses_a_tree_it_cannot_change(self):
        # A no-op "clean" seed is the failure the leg exists to close:
        # it would boot the pre-migrated fixture and call it a first
        # install.
        shutil.rmtree(self.dir / "MoonrakerPrintFollower")
        (self.dir / "moonrakerprintfollower_sections.json").unlink()
        with self.assertRaises(SystemExit):
            seed_variants.clean(self.dir)

    def test_clean_refuses_a_config_without_the_section(self):
        (self.dir / "cura.cfg").write_text("[general]\nversion = 5\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            seed_variants.clean(self.dir)

    def test_a_missing_config_is_not_an_empty_one(self):
        (self.dir / "cura.cfg").unlink()
        with self.assertRaises(SystemExit):
            seed_variants.clean(self.dir)

    def test_premigration_rewinds_to_the_v1_blob(self):
        seed_variants.premigration(self.dir)
        self.assertFalse((self.dir / "MoonrakerPrintFollower").exists())
        text = (self.dir / "cura.cfg").read_text(encoding="utf-8")
        self.assertIn("[moonrakerprintfollower]", text)
        self.assertIn("printer_configs_v1", text)
        # Both records, because the leg's machine-switch target is the
        # second one, and a transcript for it.
        self.assertIn("Second Machine", text)
        self.assertIn("console_history", text)
        self.assertIn("[some_other_plugin]", text)
        state = (self.dir / "moonrakerprintfollower_sections.json").read_text(encoding="utf-8")
        self.assertIn("whatsNewSeen", state)

    def test_a_section_appears_exactly_once_after_the_rewind(self):
        # A second [moonrakerprintfollower] section makes Cura's parser
        # reject the whole file, and the migration leg would boot the
        # wizard instead of the migration.
        seed_variants.premigration(self.dir)
        text = (self.dir / "cura.cfg").read_text(encoding="utf-8")
        self.assertEqual(text.count("[moonrakerprintfollower]"), 1)


class CliTests(unittest.TestCase):
    """How the two native setup scripts reach the transform. The tree
    is always a COPY: an accidental run against the committed fixture
    would rewrite the seed the whole gate boots from."""

    def setUp(self):
        self.work = pathlib.Path(tempfile.mkdtemp(prefix="seed-cli-", dir="/tmp/mpf"))
        self.target = self.work / "cura"
        shutil.copytree(FIXTURE, self.target)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _run(self, variant, target=None):
        return subprocess.run(
            [sys.executable, str(HERE / "seed_variants.py"), variant, str(target or self.target)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    def test_the_cli_reports_what_it_removed(self):
        done = self._run("clean")
        self.assertEqual(done.returncode, 0, done.stderr.decode("utf-8", "replace"))
        self.assertIn("clean seed removed", done.stdout.decode("utf-8", "replace"))

    def test_the_cli_fails_on_a_tree_with_nothing_to_remove(self):
        self.assertEqual(self._run("clean").returncode, 0)
        again = self._run("clean")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("seed_variants", again.stderr.decode("utf-8", "replace"))

    def test_an_unknown_variant_is_refused(self):
        self.assertEqual(self._run("half").returncode, 2)

    def test_a_missing_directory_is_refused(self):
        self.assertEqual(self._run("clean", self.work / "absent").returncode, 1)


class TheRealFixtureCanBeCleanedTests(unittest.TestCase):
    """The transform against a COPY of the committed seed: the leg's
    premise is that the fixture carries an install that already ran the
    plugin, and if that stops being true the first-install leg proves
    nothing."""

    def test_the_fixture_carries_the_state_the_clean_seed_removes(self):
        work = pathlib.Path(tempfile.mkdtemp(prefix="seed-fixture-", dir="/tmp/mpf"))
        try:
            shutil.copytree(FIXTURE, work / "cura")
            target = work / "cura"
            # The three things the clean transform takes, all of them
            # the fixture's own: if the fixture stops carrying one, the
            # transform starts refusing and the native first boot would
            # never happen.
            self.assertTrue((target / "MoonrakerPrintFollower").is_dir(),
                            "the seed fixture no longer carries the plugin's config folder")
            self.assertTrue((target / "moonrakerprintfollower_sections.json").is_file(),
                            "the seed fixture no longer carries the plugin's state file")
            self.assertIn("[moonrakerprintfollower]",
                          (target / "cura.cfg").read_text(encoding="utf-8"))
            removed = seed_variants.clean(target)
            self.assertFalse((target / "moonrakerprintfollower_sections.json").exists())
            self.assertEqual(len(removed), 3)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
