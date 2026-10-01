"""The camera recovery policy: the nonce, the veil and the URL rules.

Each transition answers whether the published frame changed, so the
facade keeps its own dispatch. The cadence and the query-only rule are
the two places a wrong answer costs the user a visibly frozen picture.
"""
from __future__ import annotations

import unittest

from PyQt6.QtCore import Qt

from mpf.monitor.camera.CameraRecovery import RETRY_SECONDS, CameraRecovery


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class CameraRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.active = [True]
        self.recovery = CameraRecovery(active=lambda: self.active[0], monotonic=self.clock)

    def test_a_reconnect_moves_the_nonce_once(self):
        self.assertTrue(self.recovery.connection_restored())
        self.assertEqual(self.recovery.nonce, 1)
        self.assertEqual(self.recovery.recovering, False,
                         "a reconnect is not a failure: no veil")

    def test_no_path_moves_the_nonce_while_the_stream_is_off(self):
        self.recovery.set_stream_enabled(False)
        nonce = self.recovery.nonce
        self.assertFalse(self.recovery.connection_restored())
        self.assertFalse(self.recovery.refresh_requested())
        self.assertFalse(self.recovery.stream_stalled())
        self.assertEqual(self.recovery.nonce, nonce)

    def test_the_first_failure_retries_immediately_and_the_rest_wait(self):
        self.assertTrue(self.recovery.stream_stalled())
        self.assertEqual(self.recovery.nonce, 1)
        self.assertTrue(self.recovery.recovering)
        # Within the cadence: the veil is re-raised, the nonce stands.
        self.clock.now += RETRY_SECONDS - 0.5
        self.assertTrue(self.recovery.stream_stalled())
        self.assertEqual(self.recovery.nonce, 1)
        # Past it: one more attempt.
        self.clock.now += 1.0
        self.assertTrue(self.recovery.stream_stalled())
        self.assertEqual(self.recovery.nonce, 2)

    def test_a_recovery_reports_nothing_when_no_veil_stood(self):
        self.assertFalse(self.recovery.stream_restored())
        self.recovery.stream_stalled()
        self.assertTrue(self.recovery.stream_restored())
        self.assertFalse(self.recovery.recovering)
        self.assertFalse(self.recovery.stream_restored(),
                         "a second recovery has already been published")

    def test_the_toggle_moves_the_nonce_in_both_directions(self):
        self.assertTrue(self.recovery.set_stream_enabled(False))
        self.assertFalse(self.recovery.stream_enabled)
        self.assertEqual(self.recovery.nonce, 1)
        self.assertTrue(self.recovery.set_stream_enabled(True))
        self.assertEqual(self.recovery.nonce, 2)
        self.assertFalse(self.recovery.set_stream_enabled(True),
                         "an unchanged toggle has nothing to republish")

    def test_a_wake_reloads_once_and_only_for_the_active_monitor(self):
        self.recovery.seed_application_state(Qt.ApplicationState.ApplicationInactive)
        self.assertTrue(self.recovery.application_state_changed(Qt.ApplicationState.ApplicationActive))
        self.assertFalse(self.recovery.application_state_changed(Qt.ApplicationState.ApplicationActive),
                         "a second Active state is not a wake")

    def test_going_background_reloads_nothing_and_still_records_the_state(self):
        # The state must be recorded on every transition, not only on
        # the wake: the NEXT Active is what has to be detectable.
        self.recovery.seed_application_state(Qt.ApplicationState.ApplicationActive)
        self.assertFalse(self.recovery.application_state_changed(Qt.ApplicationState.ApplicationInactive))
        self.assertEqual(self.recovery.nonce, 0)
        self.assertTrue(self.recovery.application_state_changed(Qt.ApplicationState.ApplicationActive))

    def test_a_deposed_monitor_ignores_the_wake(self):
        self.active[0] = False
        self.recovery.seed_application_state(Qt.ApplicationState.ApplicationInactive)
        self.assertFalse(self.recovery.application_state_changed(Qt.ApplicationState.ApplicationActive))
        self.assertEqual(self.recovery.nonce, 0)

    def test_a_seeded_active_state_is_not_a_wake(self):
        # The construction read: a model built while Cura is already
        # active must not treat its first state as a transition.
        self.recovery.seed_application_state(Qt.ApplicationState.ApplicationActive)
        self.assertFalse(self.recovery.application_state_changed(Qt.ApplicationState.ApplicationActive))

    def test_the_first_url_is_an_attach(self):
        bumped, first_attach = self.recovery.note_url("http://cam/stream")
        self.assertTrue(bumped)
        self.assertTrue(first_attach, "the loader's first request dies silently")
        self.assertEqual(self.recovery.nonce, 1)

    def test_an_unchanged_url_moves_nothing(self):
        self.recovery.note_url("http://cam/stream")
        self.assertEqual(self.recovery.note_url("http://cam/stream"), (False, False))
        self.assertEqual(self.recovery.nonce, 1)

    def test_a_rotated_query_is_the_upstreams_own_noise(self):
        self.recovery.note_url("http://cam/stream")
        self.assertEqual(self.recovery.note_url("http://cam/stream?nonce=7"), (False, False),
                         "a query-only rotation must not reload the live stream")
        self.assertEqual(self.recovery.nonce, 1)
        # The stored URL moved with it, so returning to the origin reloads.
        self.assertEqual(self.recovery.note_url("http://cam/stream"), (False, False))
        self.assertEqual(self.recovery.note_url("http://cam/other"), (True, False))
        self.assertEqual(self.recovery.nonce, 2)

    def test_a_host_change_reloads_without_being_an_attach(self):
        self.recovery.note_url("http://cam/stream")
        bumped, first_attach = self.recovery.note_url("http://other/stream")
        self.assertTrue(bumped)
        self.assertFalse(first_attach)
        self.assertEqual(self.recovery.nonce, 2)


if __name__ == "__main__":
    unittest.main()
