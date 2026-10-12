"""Current-release download and later-release staging contracts."""

import unittest
from unittest.mock import patch

from mpf.resources.RuntimeMirror import asset_url, release_asset_url
from tools import stage_runtime_mirror


class RuntimeMirrorTests(unittest.TestCase):
    def test_pinned_inventory_can_copy_from_a_previous_release_without_upstream_lookup(self):
        with patch.object(stage_runtime_mirror, "_open",
                          side_effect=AssertionError("upstream lookup")):
            assets = stage_runtime_mirror.inventory(source_release="v5.3.0")
        self.assertEqual(len(assets), 27)
        for asset in assets:
            self.assertEqual(asset["url"], release_asset_url("v5.3.0", asset["name"]))
            self.assertGreater(asset["size"], 0)
            self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")

    def test_offline_verification_needs_no_upstream_lookup(self):
        with patch.object(stage_runtime_mirror, "_open",
                          side_effect=AssertionError("upstream lookup")):
            self.assertEqual(len(stage_runtime_mirror.inventory(offline=True)), 27)

    def test_release_url_escapes_helper_plus_and_refuses_paths(self):
        self.assertIn("%2B", asset_url("cpython-3.12.15+20261003.tar.gz"))
        for name in ("", ".", "..", "../runtime.whl", "x\\runtime.whl"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                asset_url(name)

    def test_release_url_refuses_an_invalid_tag(self):
        for tag in ("main", "v5.4.0/other", "v5.4.0-beta"):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, "release tag"):
                release_asset_url(tag, "model.onnx")


if __name__ == "__main__":
    unittest.main()
