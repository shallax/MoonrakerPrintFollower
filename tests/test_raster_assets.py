# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Verify independent raster leases, scoped cleanup and publication after shutdown.

"""Raster lifetime can be exercised without a Monitor, surface or scene graph."""
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QImage
from mpf.plate.RasterAssets import RasterAssets
from mpf.plate.RasterJobs import RasterTicket

module = importlib.import_module('mpf.plate.RasterAssets')


class RasterAssetsTests(unittest.TestCase):
    def setUp(self):
        self.assets = RasterAssets()
        self.addCleanup(self.assets.close)
        self.image = QImage(2, 2, QImage.Format.Format_ARGB32)
        self.image.fill(0xff00ff00)

    def publish(self, name):
        url = self.assets.publish(self.image, name)
        path = Path(QUrl(url).toLocalFile())
        self.assertTrue(path.is_file())
        return url, path

    def test_references_and_holds_release_independently_without_resurrecting_owners(self):
        url, path = self.publish('shared')
        first, second = self.assets.acquire(), self.assets.acquire()
        self.assets.replace(first, [url])
        self.assets.replace(second, [url])
        self.assets.hold('gesture', url)
        self.assets.hold('gesture', url)
        self.assets.release(first)
        self.assets.prune(keep=0)
        self.assertTrue(path.exists())
        self.assets.release(second)
        self.assets.replace(first, [url])
        self.assets.prune(keep=0)
        self.assertTrue(path.exists())
        self.assets.hold('gesture', '')
        self.assets.prune(keep=0)
        self.assertFalse(path.exists())
        self.assertEqual(self.assets.referenced(), set())
        self.assertEqual(self.assets.held('gesture'), '')

    def test_discarded_results_are_scoped_and_keep_any_current_consumer_reference(self):
        url, path = self.publish('prefix')
        with TemporaryDirectory() as other:
            foreign = Path(other) / 'foreign.png'
            foreign.write_bytes(b'foreign')
            foreign_url = QUrl.fromLocalFile(str(foreign)).toString()
            owner = self.assets.acquire()
            self.assets.replace(owner, [foreign_url, 'https://example.invalid/image'])
            self.assertEqual(self.assets.referenced(), set())
            self.assets.discard_result(('prefix', None, foreign_url, 1))
            self.assertTrue(foreign.exists())
        self.assets.discard_result(('prefix', None, url, 1), [url])
        self.assertTrue(path.exists())
        self.assets.discard_result(('checkpoints', [(1, url)]))
        self.assertFalse(path.exists())
        full = [self.publish('full-'+str(n)) for n in range(3)]
        self.assets.discard_result(('full', None, full[0][0], None, full[1][0], None, full[2][0]), [full[1][0]])
        self.assertEqual([item[1].exists() for item in full], [False, True, False])
        self.assets.discard_result(('nav', None, full[1][0]))
        self.assets.discard_result(('failed', 'no image'))
        self.assets.discard_result(None)
        self.assertFalse(full[1][1].exists())

    def test_closing_during_publication_cannot_resurrect_the_directory(self):
        original = module.png_file
        def after_shutdown(image, directory, name):
            self.assets.close()
            return original(image, directory, name)
        with patch.object(module, 'png_file', after_shutdown):
            self.assertEqual(self.assets.publish(self.image, 'late'), '')
        self.assertFalse(Path(self.assets.directory).exists())
        self.assertEqual(self.assets.publish(self.image, 'later'), '')
        self.assertEqual(self.assets.acquire(), 0)
        self.assets.hold('gesture', 'file:///late.png')
        self.assertEqual(self.assets.held('gesture'), '')

    def test_prune_bounds_unreferenced_files_and_never_removes_inflight_temporaries(self):
        items = [self.publish('image-'+str(n)) for n in range(4)]
        temporary = Path(self.assets.directory) / 'image.png.tmp-1234'
        temporary.write_bytes(b'inflight')
        self.assets.prune([items[0][0]], keep=1)
        self.assertTrue(items[0][1].exists())
        self.assertEqual(sum(item[1].exists() for item in items), 2)
        self.assertTrue(temporary.exists())
        values = self.assets.accounting()
        self.assertEqual(values['rasterDirFiles'], 3)
        self.assertGreater(values['rasterDirBytes'], len(b'inflight'))

    def test_disk_races_and_refusals_leave_lifetime_operations_safe(self):
        url, path = self.publish('raced')
        with patch.object(module.os, 'unlink', side_effect=PermissionError('busy')):
            self.assets.discard_result(('prefix', None, url, 1))
            self.assets.prune(keep=0)
        self.assertTrue(path.exists())
        with patch.object(module.os, 'stat', side_effect=FileNotFoundError('raced')):
            self.assets.prune(keep=0)
            self.assertEqual(self.assets.accounting()['rasterDirFiles'], 0)
        with patch.object(module.os, 'listdir', side_effect=PermissionError('gone')):
            self.assets.prune()
            self.assertEqual(self.assets.accounting(), {'rasterDirFiles':0, 'rasterDirBytes':0})
        with patch.object(module.shutil, 'rmtree', side_effect=OSError('busy')):
            self.assets.close()
        self.assets.close()
        self.assertFalse(path.exists())

    def test_named_receipt_preserves_the_queued_signal_wire_values(self):
        fields = ('mini', 3, 2, 1, 'view', 'prefix', 17, 4, 9)
        ticket = RasterTicket(*fields)
        self.assertEqual(ticket, fields)
        self.assertEqual((ticket.surface, ticket.layer, ticket.serial), ('mini', 3, 9))
