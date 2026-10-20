# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Verify caller-owned cache namespaces and unambiguous implementation-module imports.

"""Cache consumers need neither Moonraker identity rules nor module-shadowing exports."""
import importlib
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest

from mpf.gcode import build_index_from_bytes
from mpf.gcode.cache import CacheIdentity, PersistentIndexCache


class CacheSourceIdentityTests(unittest.TestCase):
    def test_explicit_keys_keep_same_filename_and_metadata_in_distinct_namespaces(self):
        data = b';LAYER:0\nG1 X1\n'
        with TemporaryDirectory() as directory:
            cache = PersistentIndexCache(directory)
            first = CacheIdentity('consumer:first', 'same.gcode', len(data), 123)
            second = CacheIdentity('consumer:second', 'same.gcode', len(data), 123)
            cache.save(first, build_index_from_bytes(data))
            cache.save(second, build_index_from_bytes(data.replace(b'X1', b'X2')))
            self.assertEqual(cache.load(first).motion_x[0][-1], 1)
            self.assertEqual(cache.load(second).motion_x[0][-1], 2)
            self.assertNotEqual(cache._path(first), cache._path(second))

    def test_cache_convenience_exports_do_not_shadow_implementation_modules(self):
        import mpf.gcode
        from mpf.gcode import cache
        for name in ('RawSourceCache', 'SourceIdentity', 'IndexResidency'):
            module = importlib.import_module('mpf.gcode.'+name)
            self.assertIsInstance(getattr(mpf.gcode, name), ModuleType)
            self.assertIs(getattr(mpf.gcode, name), module)
            self.assertIsNot(getattr(cache, name), module)
