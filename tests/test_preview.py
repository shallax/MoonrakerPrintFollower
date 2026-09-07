"""Preview domain: follower behaviour, formatting, presentation and QML UX."""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import pathlib
from types import SimpleNamespace
import unittest

from plugins.PreviewFollower import PreviewFollower, preview_override_kind
from plugins.PreviewFormatting import (
    pause_can_toggle,
    pause_eta,
    pause_summary,
    pause_unavailable,
    status_icon,
    status_text,
)
from plugins.PrinterConfig import PrinterConfig
from plugins.PrintState import PhysicalLayer, PrintSnapshot
from plugins.RemoteJobService import PrintObservation

PLUGINS = pathlib.Path(__file__).resolve().parents[1] / "plugins"
BUTTON = (PLUGINS / "PreviewSecondaryButton.qml").read_text(encoding="utf-8")
PANEL = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")
EMPTY = (PLUGINS / "EmptyPreviewLoadButton.qml").read_text(encoding="utf-8")


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


class PreviewOverrideTests(unittest.TestCase):
    """preview_override_kind: classifying user movement of Cura's handles."""

    def test_detects_upper_layer_change(self):
        self.assertEqual(
            preview_override_kind(expected_layer=10, current_layer=11),
            "layer",
        )

    def test_detects_lower_layer_handle_change(self):
        self.assertEqual(
            preview_override_kind(
                expected_layer=10,
                current_layer=10,
                expected_minimum_layer=0,
                current_minimum_layer=4,
            ),
            "layer",
        )

    def test_detects_current_and_minimum_path_changes(self):
        self.assertEqual(
            preview_override_kind(
                expected_layer=10,
                current_layer=10,
                expected_path=20.0,
                current_path=21.0,
            ),
            "path",
        )
        self.assertEqual(
            preview_override_kind(
                expected_layer=10,
                current_layer=10,
                expected_path=20.0,
                current_path=20.0,
                expected_minimum_path=0,
                current_minimum_path=3,
            ),
            "path",
        )

    def test_does_not_adopt_or_guess_unarmed_position(self):
        self.assertIsNone(
            preview_override_kind(
                expected_layer=None,
                current_layer=99,
                expected_minimum_layer=None,
                current_minimum_layer=50,
                expected_path=None,
                current_path=100.0,
            )
        )

    def test_ignores_small_fractional_path_noise(self):
        self.assertIsNone(
            preview_override_kind(
                expected_layer=10,
                current_layer=10,
                expected_minimum_layer=0,
                current_minimum_layer=0,
                expected_path=20.0,
                current_path=20.5,
                expected_minimum_path=0,
                current_minimum_path=0,
            )
        )


class PreviewFormattingTests(unittest.TestCase):
    def test_status_priority_covers_every_phase(self):
        base = dict(detail="Following", load_requested=False, loading=False,
                    files_phase="idle", index_phase="ready", attached=True,
                    enabled=True, connected=True, configured=True)
        self.assertEqual(status_text(**base), "Following")
        self.assertEqual(status_text(**{**base, "load_requested": True}), "Resolving…")
        self.assertEqual(status_text(**{**base, "loading": True}), "Loading print…")
        self.assertEqual(status_text(**{**base, "files_phase": "downloading"}), "Downloading…")
        self.assertEqual(status_text(**{**base, "index_phase": "indexing"}), "Indexing…")
        self.assertEqual(status_text(**{**base, "files_phase": "error"}), "Error")
        self.assertEqual(status_text(**{**base, "index_phase": "error"}), "Error")
        self.assertEqual(status_text(**{**base, "attached": False}), "Detached")
        self.assertEqual(status_text(**{**base, "connected": False}), "Disconnected")
        self.assertEqual(status_text(**{**base, "connected": False, "configured": False}), "Not configured")

    def test_status_icon_highlights_only_following_or_connected(self):
        self.assertEqual(status_icon("Following"), "CheckCircle")
        self.assertEqual(status_icon("Connected"), "CheckCircle")
        self.assertEqual(status_icon("Detached"), "Information")
        self.assertEqual(status_icon("Not configured"), "Information")

    def test_pause_toggle_rules(self):
        self.assertFalse(pause_can_toggle(False, 5, 2, 10))     # no active print
        self.assertFalse(pause_can_toggle(True, None, 2, 10))   # nothing selected
        self.assertFalse(pause_can_toggle(True, 1, None, 10))   # no current layer
        self.assertFalse(pause_can_toggle(True, 1, 2, 10))      # already printed
        self.assertTrue(pause_can_toggle(True, 5, 2, 10))
        self.assertFalse(pause_can_toggle(True, 9, 2, 10))      # final layer
        self.assertTrue(pause_can_toggle(True, 9, 2, None))     # unknown total

    def test_pause_unavailable_explains_each_disabled_state(self):
        self.assertEqual(pause_unavailable(False, False, False, 5, 5), "")
        self.assertEqual(pause_unavailable(True, True, False, 5, 5), "")
        self.assertEqual(pause_unavailable(True, False, True, 5, 5), "")
        self.assertEqual(pause_unavailable(True, False, False, None, 5), "Waiting for current print layer")
        self.assertEqual(pause_unavailable(True, False, False, 5, 3), "Layer 4 already printed")
        self.assertEqual(pause_unavailable(True, False, False, 5, 5), "Final layer ends the print")

    def test_pause_eta_and_summary_formatting(self):
        self.assertEqual(pause_eta(90, lambda s: "x"), "in x")
        self.assertEqual(pause_eta(None, lambda s: "x"), "ETA unavailable")
        self.assertEqual(pause_summary([]), "")
        self.assertEqual(pause_summary([{"layer": 4}, {"layer": 7}]), "End-of-layer PAUSE: 4, 7")


class PreviewPresentationContractTests(unittest.TestCase):
    """Source-level contracts for what Preview exposes to the user."""

    def test_selected_layer_eta_uses_live_observation_and_anchor(self):
        preview = (PLUGINS / "PreviewFollower.py").read_text()
        self.assertIn("state.observed_layer", preview)
        self.assertIn("state.duration - state.anchor_duration", preview)
        self.assertIn("@dataclass(frozen=True)", preview)
        self.assertIn("def remaining", preview)
        self.assertIn("datetime.now().astimezone()", preview)

    def test_each_scheduled_pause_has_end_of_layer_eta(self):
        coordinator = (PLUGINS / "PrintCoordinator.py").read_text()
        qml = (PLUGINS / "PreviewActionPanelControls.qml").read_text()
        self.assertIn("self._preview.remaining(layer, self._index.view, end=True)", coordinator)
        self.assertIn("property string pauseEta", qml)
        self.assertIn("parent.pauseEta.length > 0", qml)

    def test_preview_layer_scrub_shows_duration_and_local_clock_eta(self):
        index = (PLUGINS / "GCodeIndex.py").read_text()
        preview = (PLUGINS / "PreviewFollower.py").read_text()
        status = (PLUGINS / "PrintCoordinator.py").read_text()
        self.assertIn("layer_elapsed_times", index)
        self.assertIn("def update_eta", preview)
        self.assertIn("datetime.now().astimezone()", preview)
        self.assertIn("Selected layer", preview)
        self.assertIn('"selectedLayerEtaText": state.eta_text', status)


class PreviewButtonUxTests(unittest.TestCase):
    def test_preview_button_centres_text_both_axes(self):
        self.assertIn("horizontalAlignment: Text.AlignHCenter", BUTTON)
        self.assertIn("verticalAlignment: Text.AlignVCenter", BUTTON)
        self.assertIn("elide: Text.ElideRight", BUTTON)
        self.assertIn('textColor: "transparent"', BUTTON)

    def test_preview_controls_use_centred_wrapper(self):
        self.assertGreaterEqual(PANEL.count("PreviewSecondaryButton"), 6)
        self.assertNotIn("Cura.SecondaryButton", PANEL)
        self.assertGreaterEqual(EMPTY.count("PreviewSecondaryButton"), 2)
        self.assertNotIn("Cura.SecondaryButton", EMPTY)

    def test_following_control_is_detach_attach_not_printer_pause(self):
        self.assertIn('text: base.followingPaused ? "Attach" : "Detach"', PANEL)
        self.assertIn("This does not pause the printer.", PANEL)
        self.assertIn("Attach Cura Preview to the live Moonraker print", PANEL)

    def test_load_button_gets_majority_of_top_row(self):
        self.assertIn("* 0.32", PANEL)
        self.assertIn("buttons.width - base.buttonSpacing - followButton.width", PANEL)
        self.assertIn('text: "Load current print"', PANEL)
        self.assertIn("contentWidth: 300 * screenScaleFactor", PANEL)

    def test_pause_action_keeps_pause_symbol(self):
        self.assertIn('"⏸  Pause at end of selected layer"', PANEL)
        self.assertIn('"⏸  Enable pause at end of layer "', PANEL)


if __name__ == "__main__":
    unittest.main()
