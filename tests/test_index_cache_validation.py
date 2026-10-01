"""A cache hit is accepted only when its metadata describes valid motion columns."""
import gzip
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from mpf.gcode import IndexCache, IndexCodec
from mpf.gcode.GCodeIndex import build_index_from_bytes
from mpf.moonraker.MoonrakerProtocol import RemoteFileIdentity


class PersistentIndexValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = IndexCache.PersistentIndexCache(self.temp.name)
        data = b";LAYER:0\nG1 X1 Y2 Z0.2 E1 F1200\n"
        self.identity = RemoteFileIdentity("a.gcode", len(data), 123.0, "token")
        self.index = build_index_from_bytes(data)
        self.cache.save(self.identity, self.index)
        self.path = Path(self.cache._path(self.identity))
        raw = gzip.decompress(self.path.read_bytes())
        start = len(IndexCodec._CACHE_MAGIC)
        size = struct.unpack("<I", raw[start:start + 4])[0]
        self.header = json.loads(raw[start + 4:start + 4 + size])
        self.columns = raw[start + 4 + size:]
        self.assertIsNotNone(self.cache.load(self.identity))

    def write_header(self, header):
        encoded = json.dumps(header).encode()
        raw = IndexCodec._CACHE_MAGIC + struct.pack("<I", len(encoded)) + encoded + self.columns
        self.path.write_bytes(gzip.compress(raw))

    def test_invalid_optional_metadata_is_a_miss_not_a_partial_restore(self):
        cases = {
            "extruder_events": [[[0, "not a boolean"]]],
            "start_retractions": [{"16": 1}],
            "layer_heights": [float("nan")],
            "filament_diameter": -1,
            "filament_diameters": {"0": 0},
            "colour_ranges": {"speed": [1, float("inf")]},
            "start_speeds": [-1],
            "start_tools": [16],
            "motion_attributes": "yes",
            "extrusion_column": 1,
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                self.write_header(dict(self.header, **{key: value}))
                self.assertIsNone(self.cache.load(self.identity))
        self.write_header(self.header)
        self.assertIsNotNone(self.cache.load(self.identity))

    def test_ragged_in_memory_columns_do_not_replace_a_valid_cache(self):
        before = self.path.read_bytes()
        for field, replacement in (("motion_x", [[]]), ("motion_extrusion", [[]]),
                                   ("motion_speeds", [[]])):
            original = getattr(self.index, field)
            with self.subTest(field=field):
                setattr(self.index, field, replacement)
                self.cache.save(self.identity, self.index)
                self.assertEqual(self.path.read_bytes(), before)
                setattr(self.index, field, original)

    def test_cache_diagnostics_use_the_injected_host_logger(self):
        logger = Mock()
        with patch.object(IndexCache, "_Logger", logger):
            IndexCache._log("cache %s", "restored")
        logger.log.assert_called_once_with("i", "cache %s", "restored")
