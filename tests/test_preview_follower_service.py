import unittest

from plugins.PreviewFollowerService import PreviewFollowerService


class PreviewFollowerServiceTests(unittest.TestCase):
    def test_path_progress_is_monotonic_within_a_layer_and_resets_on_layer_change(self):
        service = PreviewFollowerService()

        service.begin_path_layer(4)
        self.assertEqual(service.tracking.path_layer, 4)
        self.assertIsNone(service.tracking.path_fraction)

        self.assertEqual(service.update_path_fraction(0.6), 0.6)
        self.assertEqual(service.update_path_fraction(0.4), 0.6)
        self.assertEqual(service.tracking.path_fraction, 0.6)
        self.assertEqual(service.update_path_fraction(1.2), 1.0)

        service.begin_path_layer(5)
        self.assertEqual(service.tracking.path_layer, 5)
        self.assertIsNone(service.tracking.path_fraction)

    def test_eta_anchor_captures_layer_entry_duration_once(self):
        service = PreviewFollowerService()
        service.tracking.eta_current_print_duration = 12.5

        service.observe_remote_layer(3)
        self.assertEqual(service.runtime.observed_remote_layer, 3)
        self.assertEqual(service.tracking.eta_anchor_layer, 3)
        self.assertEqual(service.tracking.eta_anchor_print_duration, 12.5)

        service.tracking.eta_current_print_duration = 18.0
        service.observe_remote_layer(3)
        self.assertEqual(service.tracking.eta_anchor_print_duration, 12.5)

        service.observe_remote_layer(4)
        self.assertEqual(service.runtime.observed_remote_layer, 4)
        self.assertEqual(service.tracking.eta_anchor_layer, 4)
        self.assertEqual(service.tracking.eta_anchor_print_duration, 18.0)

    def test_reset_tracking_preserves_print_runtime_without_detaching(self):
        service = PreviewFollowerService()
        service.set_paused(True)
        service.begin_path_layer(7)
        service.update_path_fraction(0.75)
        service.tracking.resolved_remote_layer = 7
        service.tracking.speed_factor = 1.25
        service.tracking.eta_current_print_duration = 100.0
        service.observe_remote_layer(7)
        service.set_selected_layer_eta_text("Selected layer 8 — current print layer")
        service.runtime.last_extruder_position = 42.5
        service.runtime.preview_switched_for_job = True
        service.runtime.toolhead_path_valid = True

        service.reset_tracking()

        self.assertTrue(service.following_paused)
        self.assertIsNone(service.tracking.path_layer)
        self.assertIsNone(service.tracking.path_fraction)
        self.assertIsNone(service.tracking.resolved_remote_layer)
        self.assertEqual(service.tracking.selected_layer_eta_text, "")
        self.assertEqual(service.tracking.speed_factor, 1.0)
        self.assertIsNone(service.tracking.eta_anchor_layer)
        self.assertIsNone(service.tracking.eta_anchor_print_duration)
        self.assertIsNone(service.tracking.eta_current_print_duration)
        self.assertEqual(service.runtime.observed_remote_layer, 7)
        self.assertEqual(service.runtime.last_extruder_position, 42.5)
        self.assertTrue(service.runtime.preview_switched_for_job)
        self.assertFalse(service.runtime.toolhead_path_valid)

    def test_reset_print_state_clears_print_runtime_without_detaching(self):
        service = PreviewFollowerService()
        service.set_paused(True)
        service.tracking.path_layer = 9
        service.tracking.path_fraction = 0.8
        service.runtime.observed_remote_layer = 9
        service.runtime.last_extruder_position = 51.0
        service.runtime.preview_switched_for_job = True
        service.runtime.toolhead_path_valid = True

        service.reset_print_state()

        self.assertTrue(service.following_paused)
        self.assertIsNone(service.tracking.path_layer)
        self.assertIsNone(service.tracking.path_fraction)
        self.assertIsNone(service.runtime.observed_remote_layer)
        self.assertIsNone(service.runtime.last_extruder_position)
        self.assertFalse(service.runtime.preview_switched_for_job)
        self.assertFalse(service.runtime.toolhead_path_valid)


if __name__ == "__main__":
    unittest.main()
