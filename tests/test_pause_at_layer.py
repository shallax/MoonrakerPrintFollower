import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
FOLLOWER = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")
QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")
CONFIG = (PLUGINS / "PrinterConfig.py").read_text(encoding="utf-8")
PROTOCOL = (PLUGINS / "MoonrakerProtocol.py").read_text(encoding="utf-8")


class PauseAtLayerTests(unittest.TestCase):
    def test_pause_schedule_is_current_print_only(self):
        self.assertIn("self._scheduled_pause_layers: set[int] = set()", FOLLOWER)
        self.assertIn("self._clear_scheduled_pauses(abort_request=True)", FOLLOWER)
        self.assertNotIn("pause_at_layer", CONFIG)

    def test_preview_can_toggle_multiple_future_layers(self):
        self.assertIn("pauseAtLayerRequested", QML)
        self.assertIn('"Enable pause at layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause at layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn("Scheduled PAUSE layers:", FOLLOWER)
        self.assertIn("selected_layer > current", FOLLOWER)

    def test_pause_uses_normal_klipper_macro_through_moonraker(self):
        self.assertIn("def gcode_script_endpoint", PROTOCOL)
        self.assertIn('json.dumps({"script": "PAUSE"}', FOLLOWER)
        self.assertIn("self._pause_network.post", FOLLOWER)

    def test_polling_can_cross_a_short_target_layer_without_missing_pause(self):
        self.assertIn("layer <= current_layer", FOLLOWER)
        self.assertIn("due = sorted", FOLLOWER)
        self.assertIn("for layer in due:", FOLLOWER)

    def test_layer_observation_continues_while_preview_following_is_paused(self):
        observation = FOLLOWER.index("observed_layer, observed_source = self._resolve_remote_layer_index")
        paused = FOLLOWER.index("if self._following_paused:", observation)
        trigger = FOLLOWER.index("self._maybe_trigger_scheduled_pause(observed_layer)", observation)
        self.assertLess(observation, paused)
        self.assertLess(trigger, paused)
        self.assertIn("self._update_selected_layer_eta(view)", FOLLOWER[paused:paused + 1200])

    def test_pause_command_is_guarded_by_print_and_lifecycle_identity(self):
        self.assertIn("lifecycle_generation != self._lifecycle_generation", FOLLOWER)
        self.assertIn("job_key != self._remote_job_key", FOLLOWER)
        self.assertIn("request_generation != self._pause_reply_generation", FOLLOWER)


if __name__ == "__main__":
    unittest.main()
