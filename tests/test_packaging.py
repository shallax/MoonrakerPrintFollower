"""Release packaging: QML structural checks, hygiene and reproducible artifacts."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_curapackage import (
    DETERMINISTIC_FILE_MODE,
    build as build_curapackage,
    reproducible_zip_timestamp,
    zip_timestamp_for_epoch,
)
from build_marketplace_source import build as build_marketplace_source
from check_qml import check_text
from verify_curapackage import verify as verify_curapackage
from verify_marketplace_source import verify as verify_marketplace_source


class QmlCheckerTests(unittest.TestCase):
    def test_all_qml_pass_structural_checker(self):
        failures = []
        for path in PLUGINS.glob("*.qml"): failures.extend(check_text(path.read_text(), path.name))
        self.assertEqual(failures, [])

    def test_qml_checker_rejects_duplicate_property_and_unbalanced_brace(self):
        failures = check_text("import QtQuick 2.15\nItem { width: 1; width: 2\n", "bad.qml")
        self.assertTrue(any("duplicate property 'width'" in item for item in failures))
        self.assertTrue(any("unclosed '{'" in item for item in failures))

    def test_qml_checker_allows_leading_comment_lines(self):
        failures = check_text("// Moonraker settings panel\nimport QtQuick 2.15\nItem { width: 1 }\n", "commented.qml")
        self.assertEqual(failures, [])


class PackageSourceTests(unittest.TestCase):
    def test_repository_has_no_tracked_python_cache_or_legacy_dashboard(self):
        tracked = subprocess.check_output(["git", "--no-pager", "ls-files"], cwd=ROOT, text=True).splitlines()
        self.assertEqual([name for name in tracked if "__pycache__" in name or name.endswith((".pyc", ".pyo"))], [])
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertIn("__pycache__/", gitignore)
        self.assertIn("*.py[cod]", gitignore)

    def test_package_is_exact_byte_for_byte_source_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            package = pathlib.Path(directory) / "candidate.curapackage"
            build_curapackage(package)
            verify_curapackage(package)


class PackageReproducibilityTests(unittest.TestCase):
    def _assert_normalized_zip_metadata(
        self,
        path: pathlib.Path,
        expected_timestamp: tuple[int, int, int, int, int, int],
    ) -> None:
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                self.assertEqual(info.date_time, expected_timestamp)
                self.assertEqual(info.create_system, 3)
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual((info.external_attr >> 16) & 0o7777, DETERMINISTIC_FILE_MODE & 0o7777)

    def test_default_timestamp_comes_from_head_commit(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOURCE_DATE_EPOCH", None)
            epoch = int(
                subprocess.check_output(
                    ["git", "show", "-s", "--format=%ct", "HEAD"],
                    cwd=ROOT,
                    text=True,
                ).strip()
            )
            self.assertEqual(reproducible_zip_timestamp(), zip_timestamp_for_epoch(epoch))

    def test_source_date_epoch_controls_timestamp_and_zip_rounding(self):
        with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "946684801"}):
            self.assertEqual(reproducible_zip_timestamp(), (2000, 1, 1, 0, 0, 0))

    def test_curapackage_is_byte_for_byte_reproducible(self):
        expected_timestamp = reproducible_zip_timestamp()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = build_curapackage(root / "first.curapackage")
            second = build_curapackage(root / "second.curapackage")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            verify_curapackage(first)
            self._assert_normalized_zip_metadata(first, expected_timestamp)

    def test_marketplace_source_zip_is_byte_for_byte_reproducible(self):
        expected_timestamp = reproducible_zip_timestamp()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = build_marketplace_source(root / "first.zip")
            second = build_marketplace_source(root / "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            verify_marketplace_source(first)
            self._assert_normalized_zip_metadata(first, expected_timestamp)


if __name__ == "__main__":
    unittest.main()
