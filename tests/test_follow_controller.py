import os
import sys
import unittest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from FollowController import FollowController, FollowMode, FollowState, decide_layers


class FollowControllerTests(unittest.TestCase):
    def test_state_machine_happy_path_pause_and_resume(self):
        c = FollowController()
        self.assertEqual(c.state, FollowState.DISABLED)
        c.set_enabled(True)
        self.assertEqual(c.state, FollowState.DISCONNECTED)
        c.set_connection(False, connecting=True)
        self.assertEqual(c.state, FollowState.CONNECTING)
        c.set_connection(True)
        self.assertEqual(c.state, FollowState.IDLE)
        c.set_remote_state("printing")
        self.assertEqual(c.state, FollowState.FOLLOWING)
        self.assertTrue(c.may_write_preview)
        c.pause_by_user("layer slider")
        self.assertEqual(c.state, FollowState.USER_OVERRIDE)
        self.assertFalse(c.may_write_preview)
        c.resume()
        self.assertEqual(c.state, FollowState.FOLLOWING)

    def test_remote_pause_and_cura_suspend_are_explicit_states(self):
        c = FollowController()
        c.set_enabled(True)
        c.set_connection(True)
        c.set_remote_state("paused")
        self.assertEqual(c.state, FollowState.REMOTE_PAUSED)
        self.assertTrue(c.may_write_preview)
        c.set_cura_suspended(True)
        self.assertEqual(c.state, FollowState.CURA_SUSPENDED)
        self.assertFalse(c.may_write_preview)
        c.set_cura_suspended(False)
        self.assertEqual(c.state, FollowState.REMOTE_PAUSED)

    def test_follow_modes(self):
        exact = decide_layers(10, 50, FollowMode.EXACT.value)
        self.assertEqual((exact.current_layer, exact.minimum_layer, exact.follow_path), (10, 0, True))

        completed = decide_layers(10, 50, FollowMode.COMPLETED.value)
        self.assertEqual((completed.current_layer, completed.minimum_layer, completed.follow_path), (9, 0, False))

        lookahead = decide_layers(10, 50, FollowMode.LOOKAHEAD.value)
        self.assertEqual((lookahead.current_layer, lookahead.minimum_layer, lookahead.follow_path), (11, 0, False))

        window = decide_layers(10, 50, FollowMode.WINDOW.value)
        self.assertEqual((window.current_layer, window.minimum_layer, window.follow_path), (12, 8, False))

    def test_follow_modes_clamp_at_edges(self):
        self.assertEqual(decide_layers(0, 5, "completed").current_layer, 0)
        self.assertEqual(decide_layers(5, 5, "lookahead").current_layer, 5)
        decision = decide_layers(0, 5, "window")
        self.assertEqual((decision.current_layer, decision.minimum_layer), (2, 0))


if __name__ == "__main__":
    unittest.main()
