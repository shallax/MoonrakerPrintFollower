import os
import sys
import unittest

PLUGIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "plugins"))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from Core import OperationContext, OperationPhase, RemoteFileIdentity, preview_override_kind


class CoreTests(unittest.TestCase):
    def test_operation_transition_retains_unspecified_identity(self):
        ctx = OperationContext()
        key = ("part.gcode", 123, 7)
        ctx.transition(OperationPhase.RESOLVING, filename="part.gcode", job_key=key)
        ctx.force_load = True
        ctx.transition(OperationPhase.DOWNLOADING)
        self.assertEqual(ctx.phase, OperationPhase.DOWNLOADING)
        self.assertEqual(ctx.filename, "part.gcode")
        self.assertEqual(ctx.job_key, key)
        self.assertTrue(ctx.force_load)

    def test_operation_reset_clears_transient_fields(self):
        ctx = OperationContext(
            phase=OperationPhase.CURA_LOADING,
            filename="x.gcode",
            job_key=("x.gcode", 1, 1),
            force_load=True,
            local_path="/tmp/x.gcode",
            started_at=12.0,
            message="loading",
        )
        ctx.reset()
        self.assertEqual(ctx.phase, OperationPhase.IDLE)
        self.assertIsNone(ctx.filename)
        self.assertIsNone(ctx.job_key)
        self.assertFalse(ctx.force_load)
        self.assertIsNone(ctx.local_path)
        self.assertIsNone(ctx.started_at)
        self.assertEqual(ctx.message, "")

    def test_busy_phase_classification(self):
        ctx = OperationContext()
        for phase in (
            OperationPhase.RESOLVING,
            OperationPhase.DOWNLOADING,
            OperationPhase.CURA_LOADING,
            OperationPhase.INDEXING,
        ):
            ctx.phase = phase
            self.assertTrue(ctx.is_busy)
        for phase in (OperationPhase.IDLE, OperationPhase.READY, OperationPhase.ERROR):
            ctx.phase = phase
            self.assertFalse(ctx.is_busy)

    def test_remote_identity_prefers_uuid(self):
        a = RemoteFileIdentity("same.gcode", 100, 1.0, "abc")
        b = RemoteFileIdentity("same.gcode", 100, 999.0, "abc")
        self.assertEqual(a.stable_key(), b.stable_key())
        self.assertEqual(a.stable_key(), "uuid:abc")

    def test_remote_identity_fallback_distinguishes_modified(self):
        a = RemoteFileIdentity("same.gcode", 100, 1.0, "")
        b = RemoteFileIdentity("same.gcode", 100, 2.0, "")
        self.assertNotEqual(a.stable_key(), b.stable_key())

    def test_remote_identity_job_match_allows_unknown_size(self):
        identity = RemoteFileIdentity("a.gcode", 100, 1.0, "")
        self.assertTrue(identity.matches_job("a.gcode", 100))
        self.assertTrue(identity.matches_job("a.gcode", 0))
        self.assertFalse(identity.matches_job("a.gcode", 101))
        self.assertFalse(identity.matches_job("b.gcode", 100))

    def test_preview_override_detects_upper_layer_change(self):
        self.assertEqual(
            preview_override_kind(expected_layer=10, current_layer=11),
            "layer",
        )

    def test_preview_override_detects_lower_layer_handle_change(self):
        self.assertEqual(
            preview_override_kind(
                expected_layer=10,
                current_layer=10,
                expected_minimum_layer=0,
                current_minimum_layer=4,
            ),
            "layer",
        )

    def test_preview_override_detects_current_and_minimum_path_changes(self):
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

    def test_preview_override_does_not_adopt_or_guess_unarmed_position(self):
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

    def test_preview_override_ignores_small_fractional_path_noise(self):
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


if __name__ == "__main__":
    unittest.main()
