from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from build_curapackage import (
    DETERMINISTIC_FILE_MODE,
    DETERMINISTIC_ZIP_TIMESTAMP,
    build as build_curapackage,
)
from build_marketplace_source import build as build_marketplace_source
from verify_curapackage import verify as verify_curapackage
from verify_marketplace_source import verify as verify_marketplace_source


class ReleaseReproducibilityTests(unittest.TestCase):
    def _assert_normalized_zip_metadata(self, path: pathlib.Path) -> None:
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                self.assertEqual(info.date_time, DETERMINISTIC_ZIP_TIMESTAMP)
                self.assertEqual(info.create_system, 3)
                self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                self.assertEqual((info.external_attr >> 16) & 0o7777, DETERMINISTIC_FILE_MODE & 0o7777)

    def test_curapackage_is_byte_for_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = build_curapackage(root / "first.curapackage")
            second = build_curapackage(root / "second.curapackage")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            verify_curapackage(first)
            self._assert_normalized_zip_metadata(first)

    def test_marketplace_source_zip_is_byte_for_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first = build_marketplace_source(root / "first.zip")
            second = build_marketplace_source(root / "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            verify_marketplace_source(first)
            self._assert_normalized_zip_metadata(first)


if __name__ == "__main__":
    unittest.main()
