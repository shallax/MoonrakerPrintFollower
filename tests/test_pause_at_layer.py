import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
RUNTIME = (PLUGINS / "FollowerRuntime.py").read_text(encoding="utf-8")
COORDINATOR = (PLUGINS / "FollowerCoordinator.py").read_text(encoding="utf-8")
TRANSPORT = (PLUGINS / "FollowerTransport.py").read_text(encoding="utf-8")
SCHEDULER = (PLUGINS / "PauseScheduleService.py").read_text(encoding="utf-8")
FOLLOWER = "\n".join([RUNTIME, COORDINATOR, TRANSPORT])
QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")
CONFIG = (PLUGINS / "PrinterConfig.py").read_text(encoding="utf-8")
PROTOCOL = (PLUGINS / "MoonrakerProtocol.py").read_text(encoding="utf-8")


class PauseAtLayerTests(unittest.TestCase):
    def test_pause_schedule_is_current_print_only(self):
        self.assertIn("self._pause_schedule_service = PauseScheduleService()", COORDINATOR)
        self.assertIn("self._clear_scheduled_pauses(abort_request=True)", FOLLOWER)
        self.assertNotIn("pause_at_layer", CONFIG)
        self.assertFalse((PLUGINS / "PauseScheduler.py").exists())

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
        self.assertIn("def _remove_scheduled_pause", COORDINATOR)
        self.assertIn("def _clear_scheduled_pauses_from_preview", COORDINATOR)
        self.assertIn("selected_layer >= current", RUNTIME)
        self.assertIn("selected_layer < current", RUNTIME)
        self.assertIn("already printed", RUNTIME)
        self.assertIn("current or a future non-final layer", QML)
        self.assertIn("selected_layer >= max_layer", RUNTIME)

    def test_pause_uses_normal_klipper_macro_through_shared_transport(self):
        self.assertIn("def gcode_script_endpoint", PROTOCOL)
        self.assertIn('body={"script": "PAUSE"}', TRANSPORT)
        self.assertIn("self._client.transport.send_json", TRANSPORT)
        self.assertIn("RequestCategory.COMMAND.value", TRANSPORT)
        self.assertNotIn("self._pause_network.post", TRANSPORT)

    def test_pause_occurs_only_after_target_layer_has_finished(self):
        self.assertIn("due_end_of_layer_pauses", SCHEDULER)
        self.assertIn("layer < current", (PLUGINS / "Core.py").read_text(encoding="utf-8"))
        self.assertIn("consume_due", SCHEDULER)
        self.assertIn("self._send_scheduled_pause(due[0], current_layer)", COORDINATOR)
        self.assertIn("transition observed at layer", TRANSPORT)

    def test_pause_precision_guard_tightens_polling_near_target(self):
        self.assertIn("is_imminent", SCHEDULER)
        self.assertIn("lookahead_layers=1", COORDINATOR)
        self.assertIn("self._client.set_pause_guard(active)", COORDINATOR)
        session = (PLUGINS / "MoonrakerSession.py").read_text(encoding="utf-8")
        self.assertIn("pause_guard_ms: int = 250", session)
        self.assertIn("if urgent and active:", session)

    def test_layer_observation_continues_while_preview_following_is_paused(self):
        observation = RUNTIME.index("observed_layer, observed_source = self._resolve_remote_layer_index")
        paused = RUNTIME.index("if self._following_paused:", observation)
        trigger = RUNTIME.index("self._maybe_trigger_scheduled_pause(observed_layer)", observation)
        self.assertLess(observation, paused)
        self.assertLess(trigger, paused)
        self.assertIn("self._update_selected_layer_eta(view)", RUNTIME[paused:paused + 1200])

    def test_pause_command_is_guarded_and_requires_observed_paused_state(self):
        self.assertIn("lifecycle_generation != self._lifecycle_generation", TRANSPORT)
        self.assertIn("job_key != self._remote_job_key", TRANSPORT)
        self.assertIn("request_generation != self._scheduled_pause_request_generation", TRANSPORT)
        self.assertIn('track_command(self.SCHEDULED_PAUSE_COMMAND, {"paused"}', TRANSPORT)
        self.assertIn("self._client.accept_command(self.SCHEDULED_PAUSE_COMMAND)", TRANSPORT)
        self.assertIn('outcome == "confirmed"', TRANSPORT)


if __name__ == "__main__":
    unittest.main()
