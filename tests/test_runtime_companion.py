# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-20 — Cover companion staging, corrupt downloads and publication refusal.

"""A later release may reuse only the audited companion for identical payloads."""

import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import stage_runtime_companion as companion
from tools import stage_runtime_mirror as mirror
from tools import verify_runtime_release as release


class RuntimeCompanionTests(unittest.TestCase):
    def test_current_inventory_matches_the_audited_companion(self):
        asset = companion.companion_asset("v6.0.1")
        self.assertEqual(asset["name"], "runtime-source-and-notices-v6.0.1.tar.gz")
        self.assertIn("/v5.4.0/runtime-source-and-notices-v5.4.0.tar.gz", asset["url"])
        self.assertEqual(asset["size"], 984542522)

    def test_changed_payload_name_size_or_hash_requires_a_new_audit(self):
        for key, value in (("name", "other.whl"), ("size", 1), ("sha256", "0" * 64)):
            assets = mirror.inventory(offline=True)
            assets[0][key] = value
            with self.subTest(key=key), patch.object(companion, "inventory", return_value=assets):
                with self.assertRaisesRegex(ValueError, "audit and pin"):
                    companion.companion_asset("v6.0.1")

    def test_invalid_target_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            companion.companion_asset("../other")

    def test_stage_then_offline_verify_and_repair_corrupt_cached_archive(self):
        payload = b"audited source and notices"
        asset = dict(name="runtime-source-and-notices-v6.0.1.tar.gz", size=len(payload),
                     sha256=hashlib.sha256(payload).hexdigest(), url="https://example.org/source")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(companion, "companion_asset", return_value=asset):
            argv = ["stage", directory, "v6.0.1"]
            with patch("sys.argv", argv), patch.object(mirror, "_open", return_value=io.BytesIO(payload)):
                companion.main()
            target = Path(directory) / asset["name"]
            self.assertEqual(target.read_bytes(), payload)
            with patch("sys.argv", argv + ["--verify-only"]), \
                    patch.object(mirror, "_open", side_effect=AssertionError("network")):
                companion.main()
            target.write_bytes(b"tampered")
            with patch("sys.argv", argv + ["--verify-only"]):
                with self.assertRaisesRegex(ValueError, "Missing or mismatched"):
                    companion.main()
            with patch("sys.argv", argv), patch.object(mirror, "_open", return_value=io.BytesIO(payload)):
                companion.main()
            self.assertEqual(target.read_bytes(), payload)

    def test_corrupt_remote_download_leaves_no_publishable_archive(self):
        asset = dict(name="source.tar.gz", size=4, sha256=hashlib.sha256(b"good").hexdigest(),
                     url="https://example.org/source")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(mirror, "_open", return_value=io.BytesIO(b"evil")):
            with self.assertRaisesRegex(ValueError, "integrity"):
                mirror.stage(Path(directory), asset)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_staging_inside_the_repository_is_refused(self):
        for directory in (companion.ROOT, companion.ROOT / "dist"):
            with self.subTest(directory=directory), \
                    patch("sys.argv", ["stage", str(directory), "v6.0.1"]):
                with self.assertRaises(SystemExit):
                    companion.main()

    def test_publication_requires_uploaded_matching_companion_size_and_digest(self):
        asset = companion.companion_asset("v6.0.1")
        good = dict(size=asset["size"], digest="sha256:" + asset["sha256"], state="uploaded")
        for record in (None, dict(good, size=1), dict(good, digest="sha256:" + "0" * 64),
                       dict(good, state="starter"), good):
            published = {} if record is None else {asset["name"]: record}
            with self.subTest(record=record), \
                    patch("sys.argv", ["verify", "v6.0.1", "--require-companion"]), \
                    patch.object(release, "inventory", return_value=[]), \
                    patch.object(release, "release_assets", return_value=published):
                if record == good:
                    release.main()
                else:
                    with self.assertRaisesRegex(ValueError, "redistribution companion"):
                        release.main()


if __name__ == "__main__":
    unittest.main()
