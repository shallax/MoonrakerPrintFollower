"""Cura travel categories across layers, tools, caches and rendering paths."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from mpf.index.GCodeIndex import build_index_from_file, hydrate_layer_from_file, PersistentIndexCache, RemoteFileIdentity
from mpf.plate.PlateProgress import prepare_layer, encode_layer, decode_layer
from mpf.plate.TravelStates import layer_states, TRAVEL_NAMES
from mpf.plate.PreviewColours import DEFAULT_CLASSES, motion_colour


class TravelStateTests(unittest.TestCase):
    def test_legacy_layer_start_retraction_keeps_travel_retracted(self):
        index = SimpleNamespace(
            motion_extrusion=[(0.0,)], motion_tools=[(0,)],
            layer_start_retractions=[{}], layer_start_retracted=[True],
            layer_start_tools=[0], firmware_retractions=[()],
            motion_count=lambda _layer: 1,
        )
        self.assertEqual(list(layer_states(index, 0)), ["TRAVEL_RETRACTED"])

    def test_partial_priming_and_retraction_are_modal_across_layers_and_cold_seek(self):
        data = (b"M83\n;LAYER:0\nG0 X1\nG1 X2 E-1\nG0 X3\nG1 X4 E0.4\n"
                b";LAYER:1\nG0 X5\nG1 X6 E0.6\nG0 X7\n;TYPE:SKIN\nG1 X8 E0.2\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"travel.gcode"; path.write_bytes(data)
            full = build_index_from_file(str(path))
            compact = build_index_from_file(str(path), compact=True)
            cache = PersistentIndexCache(directory)
            identity = RemoteFileIdentity("travel.gcode", len(data), 1, "travel")
            cache.save(identity, compact)
            restored = cache.load(identity)
            self.assertIsNotNone(restored)
            self.assertEqual(list(layer_states(full, 0)), ["TRAVEL", "TRAVEL_RETRACTING", "TRAVEL_RETRACTED", "TRAVEL_PRIMING"])
            self.assertEqual(list(layer_states(full, 1)), ["TRAVEL_RETRACTED", "TRAVEL_PRIMING", "TRAVEL", None])
            for layer in (1, 0):
                self.assertTrue(hydrate_layer_from_file(restored, str(path), layer))
                self.assertEqual(list(layer_states(full, layer)), list(layer_states(restored, layer)))
                expected = prepare_layer(full, layer)
                self.assertEqual(expected, prepare_layer(restored, layer))
                raw = encode_layer(expected)
                self.assertEqual(encode_layer(decode_layer(raw, immutable=True)), raw)
                self.assertEqual(expected["travelClasses"].keys(), set(TRAVEL_NAMES) if layer == 0 else {"TRAVEL", "TRAVEL_RETRACTED", "TRAVEL_PRIMING"})
            # Priming motion 1 is a travel, not a fake wide skin extrusion.
            self.assertEqual(prepare_layer(full, 1)["classes"]["SKIN"][0][-1][2], 3)

    def test_firmware_retraction_keeps_each_tools_state_at_the_same_motion_offset(self):
        data = (b"M83\n;LAYER:0\nT0\nG10\nT1\nG0 X1\nT0\nG0 X2\n"
                b";LAYER:1\nT1\nG0 X3\nT0\nG11\nG0 X4\nG1 X5 E-1\nG1 X6 E1\nG0 X7\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"tools.gcode"; path.write_bytes(data)
            full = build_index_from_file(str(path))
            cache = PersistentIndexCache(directory)
            identity = RemoteFileIdentity("tools.gcode", len(data), 1, "tools")
            cache.save(identity, full)
            restored = cache.load(identity)
            self.assertIsNotNone(restored)
            self.assertEqual(list(layer_states(full, 0)), ["TRAVEL", "TRAVEL_RETRACTED"])
            self.assertEqual(list(layer_states(full, 1)), ["TRAVEL", "TRAVEL", "TRAVEL_RETRACTING", "TRAVEL_PRIMING", "TRAVEL"])
            for layer in (0, 1):
                self.assertEqual(list(layer_states(full, layer)), list(layer_states(restored, layer)))
            compact = build_index_from_file(str(path), compact=True)
            self.assertTrue(hydrate_layer_from_file(compact, str(path), 1))
            self.assertEqual(list(layer_states(full, 1)), list(layer_states(compact, 1)))

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_travel_colours_are_fixed_in_every_colour_mode_and_all_types_share_visibility(self):
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtQuick import QQuickWindow
        from PyQt6 import sip
        from mpf.plate.GpuFollower import GpuFollower, prepare
        from mpf.plate.GpuStrokeMaterial import pack_shader
        from mpf.plate.PlateQt import qml_geometry
        app = QGuiApplication.instance() or QGuiApplication([])
        groups = {name: [((0, i, i), (10, i, i))] for i,name in enumerate(TRAVEL_NAMES)}
        payload = {"motions": 4, "travels": [], "travelClasses": groups}
        self.assertIsInstance(qml_geometry(payload)["travelClasses"]["TRAVEL"][0][0], list)
        # The travel-only extension also round trips without a colour profile.
        self.assertEqual(encode_layer(decode_layer(encode_layer(payload))), encode_layer(payload))
        window = QQuickWindow(); item = GpuFollower(window.contentItem())
        item._data = pack_shader(prepare((("current", payload),)))
        node = None
        for mode in range(6):
            scheme = {"mode": mode, "classes": DEFAULT_CLASSES}
            for name in TRAVEL_NAMES:
                self.assertEqual(motion_colour(payload, name, 0, scheme), DEFAULT_CLASSES[name])
            for visible in (False, True):
                item.settings = {"split": 2, "showTravels": visible, "showBase": True, "trueThickness": True, "lineWidth": 8, "scale": 20, "colourScheme": scheme}
                node = item.updatePaintNode(node, None)
                self.assertEqual(len(node._groups), 4)
                for _role,name,_motions,_data,base,printed in node._groups:
                    self.assertEqual(base.geometry().vertexCount(), 0)
                    self.assertEqual(printed.geometry().vertexCount(), 6 if visible and TRAVEL_NAMES.index(name) < 2 else 0)
                    self.assertEqual(printed._material.width, 1)
                    self.assertFalse(printed._material.physical)
                    self.assertEqual(printed._material.colour_options[0], -1)
                    self.assertEqual(printed._material.colour.name(), DEFAULT_CLASSES[name])
        sip.delete(node); sip.delete(window)
        self.assertIsNotNone(app)
