from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from plugins.PreviewFollower import PreviewFollower
from plugins.PrinterConfig import PrinterConfig
from plugins.PrintState import PhysicalLayer, PrintSnapshot
from plugins.RemoteJobService import PrintObservation


class View:
    def __init__(self): self.layer, self.minimum, self.path = 0, 0, 0.0
    def getCurrentLayer(self): return self.layer
    def getMinimumLayer(self): return self.minimum
    def getCurrentPath(self): return self.path
    def getMinimumPath(self): return 0
    def getMaxPaths(self): return 100
    def setLayer(self, value): self.layer = value
    def setMinimumLayer(self, value): self.minimum = value
    def setPath(self, value): self.path = value


class CuraPort:
    suspended = False
    has_toolpath = True
    max_layer = 20
    def __init__(self): self.view = View()
    @property
    def selected_layer(self): return self.view.layer
    @contextmanager
    def writing_preview(self): yield self.view
    def show_nozzle(self): pass
    def switch_to_preview(self): return True


class PreviewFollowerServiceTests(unittest.TestCase):
    def setUp(self):
        self.cura = CuraPort()
        self.service = PreviewFollower(self.cura)
        self.fraction = 0.6
        self.index = SimpleNamespace(ranges=tuple((i, i + 1) for i in range(21)),
            elapsed_times=tuple((i + 1) * 10 for i in range(21)), hydrated=lambda layer: True,
            fraction=lambda *args: (self.fraction, "test"))
        self.config = PrinterConfig(enabled=True, path_follow=True)

    def observe(self, layer=4, duration=12.5):
        snapshot = PrintSnapshot(("part", 100, 1), PrintObservation("printing", "part", 100, 20, duration), PhysicalLayer(layer, 21))
        self.service.observe(snapshot, {"print_stats": {"print_duration": duration}, "virtual_sdcard": {"file_position": 20}}, self.config, self.index)
        return snapshot

    def test_path_progress_is_monotonic_within_a_layer_and_resets_on_layer_change(self):
        self.observe()
        self.assertEqual(self.service.state.path_fraction, 0.6)
        self.fraction = 0.4
        self.observe()
        self.assertEqual(self.service.state.path_fraction, 0.6)
        self.observe(5)
        self.assertEqual(self.service.state.path_fraction, 0.4)
        self.fraction = 1.2
        self.observe(5)
        self.assertEqual(self.service.state.path_fraction, 1.0)

    def test_eta_anchor_captures_layer_entry_duration_once(self):
        self.observe(3, 12.5)
        self.observe(3, 18)
        self.assertEqual(self.service.state.anchor_duration, 12.5)
        self.observe(4, 18)
        self.assertEqual(self.service.state.anchor_duration, 18)

    def test_reset_tracking_preserves_print_observation_and_attachment(self):
        self.observe(7, 100)
        self.service.attach(False)
        self.service.reset_tracking()
        self.assertFalse(self.service.state.attached)
        self.assertIsNone(self.service.state.path_fraction)
        self.assertEqual(self.service.state.observed_layer, 7)
        self.assertEqual(self.service.state.duration, 100)
        self.assertFalse(self.service.state.nozzle_valid)

    def test_reset_print_clears_print_state_without_reattaching(self):
        self.observe(9)
        self.service.attach(False)
        self.service.reset_print()
        self.assertFalse(self.service.state.attached)
        self.assertIsNone(self.service.state.observed_layer)
        self.assertIsNone(self.service.state.path_fraction)

    def test_state_cannot_be_mutated_by_consumers(self):
        with self.assertRaises(FrozenInstanceError): self.service.state.attached = False

    def test_manual_layer_change_detaches_without_changing_physical_layer(self):
        self.observe(4)
        self.cura.view.layer = 10
        self.assertEqual(self.service.detect_override(), "layer")
        self.assertFalse(self.service.state.attached)
        self.assertEqual(self.service.state.observed_layer, 4)

    def test_eta_uses_path_progress_and_live_duration_anchor(self):
        self.observe(4, 100)
        self.assertEqual(self.service.remaining(6, self.index), 14)
        self.observe(4, 108)
        self.assertEqual(self.service.remaining(6, self.index), 12)


if __name__ == "__main__":
    unittest.main()
