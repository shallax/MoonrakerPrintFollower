import pathlib
import unittest

from tests.fake_moonraker import FakeMoonraker
from plugins.PauseScheduleService import PauseScheduleService, due_end_of_layer_pauses
from plugins.MoonrakerSession import MoonrakerSessionState, PollPolicy, RequestCategory

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text()


class PauseAtLayerTests(unittest.TestCase):
    def test_end_of_layer_pause_is_not_due_when_target_layer_is_reached(self):
        self.assertEqual(due_end_of_layer_pauses({91}, 91), [])

    def test_end_of_layer_pause_becomes_due_after_transition(self):
        self.assertEqual(due_end_of_layer_pauses({91}, 92), [91])

    def test_end_of_layer_pause_handles_poll_skips_and_orders_targets(self):
        self.assertEqual(due_end_of_layer_pauses({94, 91, 92}, 94), [91, 92])

    def test_schedule_is_print_local_and_clearable(self):
        schedule = PauseScheduleService()
        self.assertTrue(schedule.schedule(4))
        self.assertFalse(schedule.schedule(4))
        self.assertEqual(schedule.layers, frozenset({4}))
        schedule.clear()
        self.assertFalse(schedule.layers)
        self.assertNotIn("pause_at_layer", (PLUGINS / "PrinterConfig.py").read_text())

    def test_pause_occurs_only_after_target_layer_finishes(self):
        schedule = PauseScheduleService()
        schedule.schedule(4)
        self.assertEqual(schedule.consume_due(4), [])
        self.assertEqual(schedule.consume_due(5), [4])
        self.assertEqual(schedule.consume_due(6), [])

    def test_multiple_crossed_targets_coalesce_without_replay(self):
        schedule = PauseScheduleService()
        for layer in (2, 3, 5): schedule.schedule(layer)
        self.assertEqual(schedule.consume_due(4), [2, 3])
        self.assertEqual(schedule.layers, frozenset({5}))

    def test_precision_guard_only_tightens_near_target(self):
        schedule = PauseScheduleService()
        schedule.schedule(10)
        self.assertFalse(schedule.is_imminent(8))
        self.assertTrue(schedule.is_imminent(9))
        self.assertTrue(schedule.is_imminent(10))
        self.assertEqual(PollPolicy().interval_ms(RequestCategory.CORE, 750, "printing", urgent=True), 250)

    def test_scheduled_pause_tightens_polling_and_confirms_from_status(self):
        fake = FakeMoonraker([
            {"print_stats": {"state": "printing", "info": {"current_layer": 9}}},
            {"print_stats": {"state": "printing", "info": {"current_layer": 10}}},
            {"print_stats": {"state": "printing", "info": {"current_layer": 11}}},
            {"print_stats": {"state": "paused", "info": {"current_layer": 11}}},
        ])
        session = MoonrakerSessionState()
        scheduler = PauseScheduleService()

        fake.poll_session(session, now=0)
        self.assertTrue(scheduler.schedule(10))
        self.assertTrue(scheduler.is_imminent(9, lookahead_layers=1))
        session.set_pause_guard(True)
        self.assertEqual(
            session.poll_policy.interval_ms(RequestCategory.CORE, 750, session.snapshot.printer_state, urgent=session.pause_guard),
            250,
        )
        fake.poll_session(session, now=1)
        self.assertEqual(scheduler.consume_due(10), [])
        fake.poll_session(session, now=2)
        self.assertEqual(scheduler.consume_due(11), [10])
        session.set_pause_guard(False)
        command = session.commands.issue("ScheduledPause", {"paused"}, timeout_s=10, now=2)
        fake.request("POST", "/printer/gcode/script", {"script": "PAUSE"})
        session.commands.accepted("ScheduledPause")
        self.assertFalse(command.terminal)
        _, changes = fake.poll_session(session, now=3)
        self.assertEqual([item.outcome for item in changes], ["confirmed"])
        self.assertEqual(fake.commands[-1].name, "PAUSE")

    def test_preview_menu_preserves_schedule_management(self):
        for token in ("pauseAtLayerRequested", "removePauseAtLayerRequested", "clearPauseAtLayersRequested",
                      "pauseAtLayerUnavailableText", 'text: "Enabled pauses"', 'text: "Clear all pauses"',
                      "current or a future non-final layer"):
            self.assertIn(token, QML)
        controller = (PLUGINS / "PauseController.py").read_text()
        self.assertIn('body={"script": "PAUSE"}', controller)
        self.assertIn("track_command", controller)
        self.assertIn("generation != self._generation", controller)


if __name__ == "__main__": unittest.main()
