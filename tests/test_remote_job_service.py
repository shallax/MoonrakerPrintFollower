"""Pure tests for the remote-job observation service: the run
identity, the same-file restart detection and the attestation
cross-check."""
import unittest

from plugins.RemoteJobService import RemoteJobService


class RemoteJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RemoteJobService(active_states=("printing", "paused"))

    def _observe(self, filename="part.gcode", state="printing", size=100, position=50, duration=1.0):
        return self.service.observe(
            {"state": state, "filename": filename, "print_duration": duration},
            {"file_size": size, "file_position": position},
        )

    def test_an_inactive_state_creates_no_job(self):
        transition = self._observe(state="idle")
        self.assertIsNone(transition.key)
        self.assertFalse(transition.new_job)
        self.assertEqual(self.service.serial, 0)

    def test_the_first_active_observation_is_a_new_job(self):
        transition = self._observe()
        self.assertTrue(transition.new_job)
        self.assertEqual(self.service.serial, 1)
        self.assertEqual(self.service.filename, "part.gcode")
        self.assertEqual(self.service.printer_state, "printing")

    def test_a_filename_change_is_a_new_job(self):
        self._observe()
        transition = self._observe(filename="other.gcode")
        self.assertTrue(transition.new_job)
        self.assertEqual(self.service.serial, 2)

    def test_a_size_change_is_a_new_job_but_an_unknown_size_is_not(self):
        self._observe()
        self.assertTrue(self._observe(size=200).new_job)
        # A transiently missing virtual_sdcard must not churn the run
        # identity (the reconnect truncation).
        self.assertFalse(self._observe(size=0).new_job)

    def test_a_resumed_state_after_an_inactive_one_is_a_new_job(self):
        self._observe()
        self._observe(state="idle")
        self.assertTrue(self._observe().new_job)

    def test_a_position_regression_is_a_new_job(self):
        self._observe(position=50)
        self.assertTrue(self._observe(position=10).new_job)

    def test_pause_and_resume_offset_rewinds_keep_the_print_identity(self):
        original = self._observe(position=70, duration=10).key
        for state, position in (("paused", 65), ("paused", 60), ("printing", 55)):
            transition = self._observe(state=state, position=position, duration=10)
            self.assertFalse(transition.new_job)
            self.assertEqual(transition.key, original)
        self._observe(state="paused", position=60, duration=12)
        self.assertTrue(self._observe(state="printing", position=10, duration=0.1).new_job)

    def test_a_duration_regression_is_a_new_job(self):
        self._observe(duration=10.0)
        self.assertTrue(self._observe(duration=1.0).new_job)

    def test_steady_observations_keep_the_run(self):
        self._observe()
        transition = self._observe(duration=2.0, position=60)
        self.assertFalse(transition.new_job)
        self.assertEqual(self.service.serial, 1)

    def test_the_position_carried_across_a_job_boundary_is_not_credited(self):
        # Klipper holds the stopped print's byte offset until the new
        # file is read, so the restarted print's first polls report a
        # position the finished print already held — crediting it
        # painted the new first layer with the old print's fraction.
        self._observe(position=700)
        self._observe(state="cancelled", position=700, duration=2.0)
        transition = self._observe(duration=0.1, position=700)
        self.assertTrue(transition.new_job)
        self.assertFalse(self.service.position_attributed(700),
                         "the finished print's offset was credited to the new job")
        self.assertTrue(self.service.position_attributed(0),
                        "the new print's own offset was refused")
        self._observe(position=0)
        self.assertTrue(self.service.position_attributed(700),
                        "the spent boundary re-armed on a later coincidence")

    def test_a_first_job_credits_its_own_position_immediately(self):
        # Nothing was carried across: an attach to a print already
        # running must credit the position it finds, or the follower
        # would read zero and never catch up.
        self._observe(position=700)
        self.assertTrue(self.service.position_attributed(700))
        self._observe(position=900)
        self.assertTrue(self.service.position_attributed(900))

    def test_a_new_job_by_regression_credits_its_own_position(self):
        # A restart detected by the position going backwards reports a
        # value of its own, never the carried one.
        self._observe(position=700)
        self.assertTrue(self._observe(position=10).new_job)
        self.assertTrue(self.service.position_attributed(10))

    def test_reset_clears_the_carried_position(self):
        self._observe(position=700)
        self._observe(state="cancelled", position=700)
        self._observe(position=700)
        self.assertFalse(self.service.position_attributed(700))
        self.service.reset()
        self.assertTrue(self.service.position_attributed(700))

    def test_unparseable_values_coerce_to_defaults(self):
        self._observe(size="big", position="mid", duration="slow")
        observation = self.service.observation
        self.assertEqual(observation.file_size, 0)
        self.assertEqual(observation.file_position, 0)
        self.assertEqual(observation.print_duration, 0.0)

    def test_reset_clears_the_run(self):
        self._observe()
        self.service.reset()
        self.assertIsNone(self.service.key)
        self.assertEqual(self.service.serial, 0)
        self.assertIsNone(self.service.observation)

    def test_the_metadata_latch_cross_check(self):
        self.assertTrue(self.service.current_job_matches(
            {"result": {"jobs": [{"job_id": "j-1"}]}}, "j-1"))
        self.assertFalse(self.service.current_job_matches(
            {"result": {"jobs": [{"job_id": "j-2"}]}}, "j-1"))
        # An unattestable reply is None, never False — an EMPTY
        # history is unattestable too: a False there lands in the
        # caller's permanent-refusal branch (the docstring's own
        # contract, pinned here after the coverage pass found the
        # mismatch).
        self.assertIsNone(self.service.current_job_matches(None, "j-1"))
        self.assertIsNone(self.service.current_job_matches({"result": {}}, "j-1"))
        self.assertIsNone(self.service.current_job_matches({"result": {"jobs": []}}, "j-1"))
        self.assertIsNone(self.service.current_job_matches("garbage", "j-1"))


if __name__ == "__main__":
    unittest.main()
