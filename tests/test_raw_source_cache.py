import errno
import os
import tempfile
import unittest
from dataclasses import asdict
from functools import partial
from unittest.mock import patch

from mpf.gcode.IndexCache import PersistentIndexCache
from mpf.gcode.GCodeIndex import build_index_from_bytes
from mpf.gcode.PlateProgress import decode_layer, encode_layer
from mpf.gcode.PreparedStore import PreparedCache
from mpf.gcode.RawSourceCache import RawSourceCache
from mpf.moonraker.MoonrakerProtocol import RemoteFileIdentity
from mpf.settings.PrinterConfig import PrinterConfig


class RawSourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = os.path.join(self.temp.name, "prints")
        self.cache = RawSourceCache(self.root, 2048 * 1024 * 1024)
        self.source = os.path.join(self.temp.name, "download.gcode")
        with open(self.source, "wb") as handle:
            handle.write(b"G1 X10\n")
        self.identity = RemoteFileIdentity("part.gcode", 7, 123.0)

    def test_durable_restore_requires_complete_matching_metadata(self):
        pin = self.cache.publish(self.identity, "part.gcode", self.source)
        destination = os.path.join(self.temp.name, "restored.gcode")
        self.assertFalse(self.cache.restore(RemoteFileIdentity("part.gcode", 7), "part.gcode", destination))
        self.assertFalse(self.cache.restore(RemoteFileIdentity("part.gcode", 7, 124), "part.gcode", destination))
        self.assertFalse(self.cache.restore(self.identity, "other.gcode", destination))
        restored_pin = self.cache.restore(self.identity, "part.gcode", destination)
        self.assertTrue(restored_pin)
        self.cache.unpin(pin)
        os.unlink(self.cache._path(self.identity))
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"G1 X10\n")
        self.cache.unpin(restored_pin)

    def test_changed_identity_uses_a_different_print_folder(self):
        original = self.cache._path(self.identity)
        variants = (
            RemoteFileIdentity("renamed.gcode", 7, 123),
            RemoteFileIdentity("part.gcode", 8, 123),
            RemoteFileIdentity("part.gcode", 7, 124),
        )
        self.assertEqual(len({original, *(self.cache._path(item) for item in variants)}), 4)

    def test_legacy_512_upgrades_and_new_explicit_512_survives(self):
        self.assertEqual(asdict(PrinterConfig.from_dict({}))["cache_max_mb"], 2048)
        self.assertEqual(PrinterConfig.from_dict({"cache_max_mb": 512}).cache_max_mb, 2048)
        self.assertEqual(PrinterConfig.from_dict(
            {"cache_max_mb": 512, "cache_max_mb_explicit": True}).cache_max_mb, 512)
        for value in (16, 256, 1024, 4096):
            self.assertEqual(PrinterConfig.from_dict({"cache_max_mb": value}).cache_max_mb, value)

    def test_partial_source_never_publishes_or_restores(self):
        with open(self.source, "wb") as handle:
            handle.write(b"G1")
        with self.assertRaisesRegex(OSError, "changed"):
            self.cache.publish(self.identity, "part.gcode", self.source)
        self.assertFalse(os.path.exists(self.cache._path(self.identity)))
        with open(self.source, "wb") as handle:
            handle.write(b"G1 X10\n")
        pin = self.cache.publish(self.identity, "part.gcode", self.source)
        self.cache.unpin(pin)
        with open(self.cache._path(self.identity), "wb") as handle:
            handle.write(b"G1")
        self.assertFalse(self.cache.restore(self.identity, "part.gcode",
                                            os.path.join(self.temp.name, "restored.gcode")))

    def test_cross_volume_publication_is_atomic_and_rejects_short_copies(self):
        with patch("mpf.gcode.RawSourceCache.os.link",
                   side_effect=OSError(errno.EXDEV, "cross-volume link")):
            pin = self.cache.publish(self.identity, self.identity.filename, self.source)
        destination = self.cache._path(self.identity)
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), b"G1 X10\n")
        self.cache.unpin(pin)
        os.unlink(destination)

        def short_copy(_source, target):
            with open(target, "wb") as handle:
                handle.write(b"G1")

        with patch("mpf.gcode.RawSourceCache.os.link",
                   side_effect=OSError(errno.EXDEV, "cross-volume link")), \
                patch("mpf.gcode.RawSourceCache.shutil.copyfile", side_effect=short_copy):
            with self.assertRaisesRegex(OSError, "incomplete"):
                self.cache.publish(self.identity, self.identity.filename, self.source)
        self.assertFalse(os.path.exists(destination))
        self.assertEqual(os.listdir(os.path.dirname(destination)), [])

    def test_restore_copy_reports_failures_and_rechecks_source_size(self):
        pin = self.cache.publish(self.identity, self.identity.filename, self.source)
        destination = os.path.join(self.temp.name, "restored.gcode")
        errors = []

        def fail_progress(_copied):
            raise OSError("disk failed")

        with patch("mpf.gcode.RawSourceCache.os.link",
                   side_effect=OSError(errno.EXDEV, "cross-volume link")):
            self.assertFalse(self.cache.restore(
                self.identity, self.identity.filename, destination,
                progress=fail_progress,
                on_error=errors.append))
            self.assertEqual(str(errors[0]), "disk failed")
            self.assertFalse(os.path.exists(destination))

            def shorten_source(_copied):
                with open(self.cache._path(self.identity), "wb") as handle:
                    handle.write(b"G1")

            self.assertFalse(self.cache.restore(
                self.identity, self.identity.filename, destination,
                progress=shorten_source))
        self.assertFalse(os.path.exists(destination))
        self.cache.unpin(pin)

    def test_retired_namespace_and_stale_temporary_file_are_not_published(self):
        weak = RemoteFileIdentity("part.gcode", 7)
        self.assertIsNone(self.cache.publish(weak, weak.filename, self.source))
        folder = os.path.dirname(self.cache._path(self.identity))
        os.makedirs(folder)
        stale = os.path.join(folder, "source.gcode.tmp-0-dead")
        with open(stale, "wb"):
            pass
        with patch("mpf.gcode.RawSourceCache.temporary_owner_alive", return_value=False):
            self.cache.sweep()
        self.assertFalse(os.path.exists(stale))
        os.rmdir(folder)
        os.rmdir(self.root)
        self.assertIsNone(self.cache.publish(self.identity, self.identity.filename, self.source))
        self.cache.sweep()
        self.cache.prune()

    def test_large_source_and_derived_files_share_budget(self):
        size = 467_500_381
        large = RemoteFileIdentity("large.gcode", size, 100)
        with open(self.source, "wb") as handle:
            handle.truncate(size)
        pin = self.cache.publish(large, "large.gcode", self.source)
        folder = os.path.dirname(self.cache._path(large))
        with open(os.path.join(folder, "index.mpfi.gz"), "wb") as handle:
            handle.truncate(1 * 1024 * 1024)
        with open(os.path.join(folder, "prepared.mpfp"), "wb") as handle:
            handle.truncate(344 * 1024 * 1024)
        self.cache.prune()
        self.assertTrue(os.path.exists(self.cache._path(large)))
        self.assertLess(sum(os.path.getsize(os.path.join(folder, name))
                            for name in os.listdir(folder)), self.cache.max_bytes)
        migrated = PrinterConfig.from_dict({"cache_max_mb": 512})
        self.assertEqual(migrated.cache_max_mb, 2048)
        self.cache.unpin(pin)
        restarted = RawSourceCache(self.root, migrated.cache_max_mb * 1024 * 1024)
        index = PersistentIndexCache(self.root, max_bytes=restarted.max_bytes,
                                     max_entries=None)
        prepared = PreparedCache(self.root, max_bytes=restarted.max_bytes)
        index.prune()
        prepared._evict(os.path.join(folder, "prepared.mpfp"))
        restarted.prune()
        restored = os.path.join(self.temp.name, "warm-large.gcode")
        restored_pin = restarted.restore(large, large.filename, restored)
        self.assertTrue(restored_pin)
        self.assertEqual(os.path.getsize(restored), size)
        self.assertTrue(all(os.path.isfile(os.path.join(folder, name))
                            for name in ("source.gcode", "index.mpfi.gz", "prepared.mpfp")))
        self.cache.max_bytes = 512 * 1024 * 1024
        self.cache.prune()
        self.assertTrue(os.path.exists(folder), "a live source pin must protect its folder")
        restarted.unpin(restored_pin)
        self.cache.prune()
        self.assertFalse(os.path.exists(folder))

    def test_each_store_prunes_oldest_whole_folder_and_respects_active_pin(self):
        for pruner in ("index", "prepared", "source"):
            with self.subTest(pruner=pruner):
                root = os.path.join(self.temp.name, pruner)
                budget = 16 * 1024 * 1024
                source_size = 14 * 1024 * 1024
                self.assertLess(source_size, budget)
                self.assertGreater(source_size + 3 * 1024 * 1024, budget)
                raw = RawSourceCache(root, budget)
                index = PersistentIndexCache(root, max_bytes=budget, max_entries=None)
                prepared = PreparedCache(root, max_bytes=budget)
                folders = []
                for order, name in enumerate(("older.gcode", "newer.gcode")):
                    identity = RemoteFileIdentity(name, source_size, 100 + order)
                    path = os.path.join(self.temp.name, f"{pruner}-{name}")
                    with open(path, "wb") as handle:
                        handle.truncate(identity.size)
                    pin = raw.publish(identity, name, path)
                    folder = os.path.dirname(raw._path(identity))
                    for filename in ("index.mpfi.gz", "prepared.mpfp"):
                        with open(os.path.join(folder, filename), "wb") as handle:
                            handle.truncate((1 if filename.startswith("index") else 2)
                                            * 1024 * 1024)
                    for filename in ("source.gcode", "index.mpfi.gz", "prepared.mpfp"):
                        os.utime(os.path.join(folder, filename), (100 + order, 100 + order))
                    folders.append((folder, pin))
                old_folder, old_pin = folders[0]
                new_folder, new_pin = folders[1]
                if pruner == "index":
                    prune = partial(index.prune, keep=os.path.join(new_folder, "index.mpfi.gz"))
                elif pruner == "prepared":
                    prune = partial(prepared._evict, os.path.join(new_folder, "prepared.mpfp"))
                else:
                    prune = partial(raw.prune, keep=os.path.join(new_folder, "source.gcode"))
                prune()
                self.assertTrue(os.path.exists(old_folder), "an active source was evicted")
                self.assertEqual(set(os.listdir(old_folder)),
                                 {"source.gcode", "index.mpfi.gz", "prepared.mpfp", os.path.basename(old_pin)})
                raw.unpin(old_pin)
                prune()
                self.assertFalse(os.path.exists(old_folder), "the old print's three representations were split")
                self.assertTrue(os.path.exists(new_folder))
                self.assertTrue(all(os.path.exists(os.path.join(new_folder, name))
                                    for name in ("source.gcode", "index.mpfi.gz", "prepared.mpfp")))
                raw.unpin(new_pin)
                raw.prune()
                self.assertFalse(os.path.exists(new_folder),
                                 "the over-budget print was only partially removed")

    def test_source_larger_than_budget_stays_whole_while_active(self):
        self.cache.max_bytes = 16 * 1024 * 1024
        identity = RemoteFileIdentity("oversized.gcode", 17 * 1024 * 1024, 1)
        with open(self.source, "wb") as handle:
            handle.truncate(identity.size)
        pin = self.cache.publish(identity, identity.filename, self.source)
        folder = os.path.dirname(self.cache._path(identity))
        self.assertIsNotNone(pin)
        for name in ("index.mpfi.gz", "prepared.mpfp"):
            with open(os.path.join(folder, name), "wb") as handle:
                handle.write(b"derived")
        self.cache.prune()
        self.assertTrue(all(os.path.exists(os.path.join(folder, name))
                            for name in ("source.gcode", "index.mpfi.gz", "prepared.mpfp")))
        self.cache.unpin(pin)
        self.cache.prune()
        self.assertFalse(os.path.exists(folder))

    def test_raw_publication_evicts_the_other_print_as_one_folder(self):
        self.cache.max_bytes = 15 * 1024 * 1024
        older = RemoteFileIdentity("old.gcode", 6 * 1024 * 1024, 1)
        newer = RemoteFileIdentity("new.gcode", 6 * 1024 * 1024, 2)
        with open(self.source, "wb") as handle:
            handle.truncate(older.size)
        old_pin = self.cache.publish(older, older.filename, self.source)
        old_dir = os.path.dirname(self.cache._path(older))
        for name in ("index.mpfi.gz", "prepared.mpfp"):
            with open(os.path.join(old_dir, name), "wb") as handle:
                handle.truncate(2 * 1024 * 1024)
        self.cache.unpin(old_pin)
        new_pin = self.cache.publish(newer, newer.filename, self.source)
        self.assertFalse(os.path.exists(old_dir), "source publication left orphaned derived files")
        self.assertTrue(os.path.isfile(self.cache._path(newer)))
        self.cache.unpin(new_pin)

    def test_warm_restart_restores_raw_index_and_prepared_from_one_print(self):
        gcode = b";LAYER:0\nG1 X0 Y0 E0.1\nG1 X10 Y10 E1\n"
        identity = RemoteFileIdentity("warm.gcode", len(gcode), 123)
        with open(self.source, "wb") as handle:
            handle.write(gcode)
        pin = self.cache.publish(identity, identity.filename, self.source)
        index_cache = PersistentIndexCache(self.root, max_bytes=self.cache.max_bytes,
                                           max_entries=None)
        index_cache.save(identity, build_index_from_bytes(gcode))
        prepared = PreparedCache(self.root, max_bytes=self.cache.max_bytes)
        payload = {"classes": {"SKIN": [[[0., 0., 0.], [1., 0., 0.]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 1}
        self.assertIsNotNone(prepared.finalise(identity.stable_key(), [encode_layer(payload)]))
        self.cache.unpin(pin)

        raw_after_restart = RawSourceCache(self.root, self.cache.max_bytes)
        index_after_restart = PersistentIndexCache(self.root, max_bytes=self.cache.max_bytes,
                                                   max_entries=None)
        prepared_after_restart = PreparedCache(self.root, max_bytes=self.cache.max_bytes)
        working = os.path.join(self.temp.name, "warm-working.gcode")
        restored_pin = raw_after_restart.restore(identity, identity.filename, working)
        self.assertTrue(restored_pin)
        with open(working, "rb") as handle:
            self.assertEqual(handle.read(), gcode)
        self.assertIsNotNone(index_after_restart.load(identity))
        table = prepared_after_restart.load_table(identity.stable_key())
        self.assertIsNotNone(table)
        self.assertIn("SKIN", decode_layer(prepared_after_restart.read(
            identity.stable_key(), table["table"], 0))["classes"])
        self.assertEqual(set(os.listdir(os.path.dirname(raw_after_restart._path(identity))))
                         - {os.path.basename(restored_pin)},
                         {"source.gcode", "index.mpfi.gz", "prepared.mpfp"})
        raw_after_restart.unpin(restored_pin)

    def test_raw_restore_refreshes_lru_before_budget_eviction(self):
        self.cache.max_bytes = 16 * 1024 * 1024
        identities = []
        pins = []
        for order in range(3):
            identity = RemoteFileIdentity(f"print-{order}.gcode", 6 * 1024 * 1024, order + 1)
            source = os.path.join(self.temp.name, f"print-{order}.gcode")
            with open(source, "wb") as handle:
                handle.truncate(identity.size)
            pin = self.cache.publish(identity, identity.filename, source)
            os.utime(self.cache._path(identity), (100 + order, 100 + order))
            identities.append(identity)
            pins.append(pin)
        restored = os.path.join(self.temp.name, "working.gcode")
        restored_pin = self.cache.restore(identities[0], identities[0].filename, restored)
        self.assertTrue(restored_pin)
        self.cache.unpin(restored_pin)
        for pin in pins:
            self.cache.unpin(pin)
        self.cache.prune()
        self.assertTrue(os.path.isfile(self.cache._path(identities[0])))
        self.assertFalse(os.path.exists(self.cache._path(identities[1])))
        self.assertTrue(os.path.isfile(self.cache._path(identities[2])))


if __name__ == "__main__":
    unittest.main()
