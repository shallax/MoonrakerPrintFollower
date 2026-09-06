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

    def test_preview_can_toggle_and_manage_current_or_future_layers(self):
        self.assertIn("pauseAtLayerRequested", QML)
        self.assertIn("removePauseAtLayerRequested", QML)
        self.assertIn("clearPauseAtLayersRequested", QML)
        self.assertIn('"⏸  Enable pause at end of layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause after layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn("text: \"Can't schedule: \" + base.pauseAtLayerUnavailableText", QML)
        self.assertIn('text: "Enabled pauses"', QML)
        self.assertIn('text: "Clear all pauses"', QML)
        self.assertIn("pauseAtLayerItems", FOLLOWER)
        self.assertIn("def _remove_scheduled_pause", FOLLOWER)
        self.assertIn("def _clear_scheduled_pauses_from_preview", FOLLOWER)
        self.assertIn("selected_layer >= current", FOLLOWER)
        self.assertIn("selected_layer < current", FOLLOWER)
        self.assertIn("already printed", FOLLOWER)
        self.assertIn("current or a future non-final layer", QML)
        self.assertIn("selected_layer >= max_layer", FOLLOWER)

    def test_pause_uses_normal_klipper_macro_through_moonraker(self):
        self.assertIn("def gcode_script_endpoint", PROTOCOL)
        self.assertIn('json.dumps({"script": "PAUSE"}', FOLLOWER)
        self.assertIn("self._pause_network.post", FOLLOWER)

    def test_pause_occurs_only_after_target_layer_has_finished(self):
        self.assertIn("due_end_of_layer_pauses", FOLLOWER)
        self.assertIn("layer < current", (PLUGINS / "Core.py").read_text(encoding="utf-8"))
        self.assertIn("for layer in due:", FOLLOWER)
        self.assertIn("transition observed at layer", FOLLOWER)

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
