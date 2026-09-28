"""The what's-new content and its once-per-version marker contract.

The content is hand-curated per release; these pins make the curation
checkable — a version bump in package.json without a matching WHATS_NEW
head fails here, and the harness's seeded marker stays pinned to the
shipped version so the suite's runs never see the popup unless a
scenario asks.
"""
from __future__ import annotations

import json
import pathlib
import unittest

from plugins.WhatsNew import WHATS_NEW, entries, latest_version, should_show

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


class WhatsNewContentTests(unittest.TestCase):
    def test_shipped_release_notes_are_frozen(self):
        # Once a release's notes are written, they are FROZEN — a
        # later release adds its own entry, never edits the older
        # ones (the ruling). The pin covers every entry
        # except the head (the release in development); shipping a
        # new release moves the old head into the frozen set and
        # recomputes this pin in the same pass (the version bump
        # checklist).
        import hashlib

        historical = [
            {"version": entry["version"], "headline": entry["headline"],
             "items": list(entry["items"])}
            for entry in WHATS_NEW[1:]
        ]
        payload = json.dumps(historical, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        self.assertEqual(
            digest,
            "2cd302508e683e0d56aec5cb71cea6d35c12c6a4140ac67732b483430866044f",
            "the historical what's-new content changed — shipped release "
            "notes are frozen; only a new head entry may be added, and "
            "this pin recomputed for the release")

    def test_the_content_reads_like_release_notes(self):
        # The ruling: the popup's content is hand-curated and
        # user-facing — the maintainer-level detail stays in
        # CHANGELOG.md. No markdown survives into the rendered text.
        for entry in WHATS_NEW:
            for item in (entry["headline"], *entry["items"]):
                self.assertNotIn("`", item, entry["version"])
                self.assertNotIn("**", item, entry["version"])
    def test_the_latest_entry_is_the_shipped_package_version(self):
        # The release checklist adds a WHATS_NEW head alongside the
        # version bump — this pin fails a release that does one
        # without the other.
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(latest_version(), package["package_version"])
        self.assertEqual(latest_version(), WHATS_NEW[0]["version"])

    def test_should_show_gates_on_the_marker(self):
        # A fresh install stores nothing — the gate shows. Any stored
        # marker that is not the shipped version also shows; only the
        # shipped version itself stays quiet.
        self.assertTrue(should_show(""))
        self.assertTrue(should_show(None))
        self.assertTrue(should_show("4.0.2"))
        self.assertFalse(should_show(latest_version()))

    def test_entries_flag_only_the_latest(self):
        content = entries()
        self.assertTrue(content[0]["isLatest"])
        self.assertFalse(any(entry["isLatest"] for entry in content[1:]))

    def test_entries_preserve_every_curated_item_in_order(self):
        content = entries()
        self.assertEqual([entry["version"] for entry in content],
                         [entry["version"] for entry in WHATS_NEW])
        for entry, curated in zip(content, WHATS_NEW, strict=True):
            self.assertEqual(entry["items"], list(curated["items"]))
            self.assertEqual(entry["headline"], curated["headline"])

    def test_every_release_has_curated_items(self):
        for entry in WHATS_NEW:
            self.assertTrue(entry["version"])
            self.assertTrue(entry["headline"])
            self.assertTrue(entry["items"])
            self.assertTrue(all(entry["items"]))
        self.assertEqual(len({entry["version"] for entry in WHATS_NEW}),
                         len(WHATS_NEW))

    def test_the_overlay_sources_are_present(self):
        # The overlay's QML ships beside the content module; the
        # extension loads it at offer time, so a dropped file would
        # fail at runtime, not here — this pins the packaging half.
        self.assertTrue((PLUGINS / "WhatsNewOverlay.qml").is_file())


class WhatsNewSeedTests(unittest.TestCase):
    def test_the_harness_seed_marker_suppresses_the_suite_popup(self):
        # The suite's seeded profile carries the marker so its runs
        # never see the popup unless a scenario (z16) clears it —
        # this pin keeps the seed on the shipped version.
        # The 4.5.0 fixture: the marker lives in the state document
        # under the plugin's one persistence folder now, not the
        # pre-4.5.0 sections file.
        seed = json.loads(
            (ROOT / "tests/harness/config/config/cura/5.13"
             / "MoonrakerPrintFollower" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(seed["whatsNewSeen"], latest_version())
        self.assertFalse(should_show(seed["whatsNewSeen"]))


if __name__ == "__main__":
    unittest.main()
