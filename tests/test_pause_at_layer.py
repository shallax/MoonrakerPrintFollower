import pathlib
import unittest

from plugins.PauseScheduleService import PauseScheduleService
from plugins.MoonrakerSession import PollPolicy, RequestCategory

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text()


class PauseAtLayerTests(unittest.TestCase):
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
