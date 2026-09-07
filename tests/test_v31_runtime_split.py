from __future__ import annotations

import ast
import pathlib
import unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

from plugins.PrintState import LayerResolver, PhysicalLayer, PrintSnapshot
from plugins.PrinterConfig import PrinterConfig

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"


class V31RuntimeSplitTests(unittest.TestCase):
    def test_runtime_is_construction_and_teardown_only(self):
        tree = ast.parse((PLUGINS / "FollowerRuntime.py").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        self.assertEqual(cls.bases, [])
        self.assertEqual({n.name for n in cls.body if isinstance(n, ast.FunctionDef)}, {"__init__", "close"})
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "super" for n in ast.walk(cls)))

    def test_domain_constructors_do_not_accept_whole_follower_or_model(self):
        for name in ("RemoteFileService", "GCodeIndexService", "PreviewFollower", "PauseController", "MonitorData", "MonitorCommands", "MonitorControls", "MonitorCamera", "MonitorTuning", "UploadController"):
            source = (PLUGINS / (name + ".py")).read_text()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.FunctionDef) and node.name == "__init__":
                    self.assertFalse({arg.arg for arg in node.args.args} & {"follower", "model", "context"}, name)
            self.assertNotIn("__getattr__", source)

    def test_physical_snapshot_is_immutable(self):
        snapshot = PrintSnapshot(layer=PhysicalLayer(5, 10))
        with self.assertRaises(FrozenInstanceError): snapshot.layer.index = 4
        with self.assertRaises(FrozenInstanceError): snapshot.job_key = ("x", 1, 1)

    def test_physical_layer_is_not_clamped_by_local_preview_geometry(self):
        resolver = LayerResolver()
        layer = resolver.resolve({"print_stats": {"info": {"current_layer": 100, "total_layer": 200}}}, PrinterConfig(), heights=(0.2, 0.4))
        self.assertEqual(layer.index, 99)
        self.assertEqual(layer.total, 200)

    def test_exact_index_map_and_ranges_precede_z_fallback(self):
        resolver = LayerResolver()
        index = SimpleNamespace(ranges=((0, 10), (10, 20)), current_layer_map={5: 1}, layer_at=lambda p: 0)
        self.assertEqual(resolver.resolve({"print_stats": {"info": {"current_layer": 5}}}, PrinterConfig(), index).index, 1)
        self.assertEqual(resolver.resolve({"virtual_sdcard": {"file_position": 5}}, PrinterConfig(), index).index, 0)

    def test_z_hop_does_not_advance_physical_layer_without_extrusion(self):
        resolver = LayerResolver()
        config = PrinterConfig()
        def observe(z, e):
            return resolver.resolve({"gcode_move": {"gcode_position": [0, 0, z, e]}}, config, heights=(0.2, 0.4, 0.6))
        self.assertIsNone(observe(0.2, 1).index)
        self.assertEqual(observe(0.2, 2).index, 0)
        self.assertEqual(observe(0.6, 2).index, 0)
        self.assertEqual(observe(0.4, 3).index, 1)

    def test_removed_preference_api_and_private_follower_access_are_absent(self):
        for path in PLUGINS.glob("*.py"):
            source = path.read_text()
            self.assertNotIn("self._pref_", source, path.name)
            self.assertNotIn("self._follower._", source, path.name)


if __name__ == "__main__": unittest.main()
