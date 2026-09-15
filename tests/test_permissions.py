"""Pure tests for the 4.2.0 permissions policy (MonitorPermissions).

The table is the consolidation's contract: one derivation, every
consumer. These tests pin the rulings — the fail-closed prelude, the
shipped state mappings preserved, and the per-action rows."""
import unittest

from plugins.MonitorPermissions import (
    Observation,
    R_BUSY,
    R_DISCONNECTED,
    R_LOCKED,
    R_PAUSE_FIRST,
    R_PAUSED_NOTE,
    R_PRINTING,
    R_UNKNOWN,
    Verdict,
    can_jog,
    can_macro,
    can_power,
    can_restart,
    can_set_absolute,
    can_start_print,
    can_z_offset,
    jog_caption,
)


def obs(**overrides):
    fields = dict(active=True, connection="yes", state="standby", homed_axes="xyz",
                  assumed_stopped=False, save_config_pending=False,
                  controls_locked=False, busy=False)
    fields.update(overrides)
    return Observation(**fields)


class PolicyPreludeTests(unittest.TestCase):
    def test_unknown_disables_every_action_with_the_unknown_reason(self):
        for action in (can_jog, can_restart, can_start_print, can_macro, can_z_offset):
            self.assertEqual(action(obs(connection="unknown")), Verdict("disabled", R_UNKNOWN))
        self.assertEqual(can_power(obs(connection="unknown"), True), Verdict("disabled", R_UNKNOWN))

    def test_disconnected_disables_every_action(self):
        for action in (can_jog, can_restart, can_start_print):
            self.assertEqual(action(obs(connection="no")), Verdict("disabled", R_DISCONNECTED))

    def test_the_estop_assumption_releases_the_guards_not_blocks_them(self):
        # The shipped ruling: the e-stop ASSUMES the print cancelled
        # and the snapshot-derived guards release immediately (the
        # state reads cancelled, jog unlocks for recovery). The
        # record carries assumed_stopped for the dispatch predicates;
        # the click-time gates rule on the rewritten state.
        self.assertEqual(can_jog(obs(state="cancelled", assumed_stopped=True)),
                         Verdict("allowed", ""))
        self.assertEqual(can_restart(obs(state="cancelled", assumed_stopped=True)),
                         Verdict("allowed", ""))
        # The assumption itself stays visible to the record's readers.
        self.assertTrue(obs(state="cancelled", assumed_stopped=True).assumed_stopped)

    def test_the_controls_lock_blocks_the_toolhead(self):
        self.assertEqual(can_jog(obs(controls_locked=True)), Verdict("disabled", R_LOCKED))

    def test_reason_precedence_names_the_disconnect_before_the_lock(self):
        self.assertEqual(can_jog(obs(connection="no", controls_locked=True)),
                         Verdict("disabled", R_DISCONNECTED))


class PolicyRulingTests(unittest.TestCase):
    def test_jog_mirrors_the_shipped_jog_gate(self):
        self.assertEqual(can_jog(obs(state="printing")), Verdict("pause-first", R_PAUSE_FIRST))
        for state in ("standby", "paused", "complete", "cancelled", "error"):
            self.assertEqual(can_jog(obs(state=state)), Verdict("allowed", ""), state)
        # Unobserved states disable (jog_gate's "" default).
        self.assertEqual(can_jog(obs(state="")), Verdict("disabled", R_UNKNOWN))
        self.assertEqual(can_jog(obs(state="idle")), Verdict("disabled", R_UNKNOWN))

    def test_jog_caption_names_every_state(self):
        # The Status-row caption (the UX adjudication): the reason
        # when disabled, the pause-first warning, the paused note,
        # and nothing when there is nothing to say.
        self.assertEqual(jog_caption(obs(state="")), R_UNKNOWN)
        self.assertEqual(jog_caption(obs(connection="no")), R_DISCONNECTED)
        self.assertEqual(jog_caption(obs(state="printing")), R_PAUSE_FIRST)
        self.assertEqual(jog_caption(obs(state="paused")), R_PAUSED_NOTE)
        self.assertEqual(jog_caption(obs(state="standby")), "")

    def test_jog_allows_while_not_homed(self):
        # The explicit row (H3): jog never consults homing.
        self.assertEqual(can_jog(obs(homed_axes="")), Verdict("allowed", ""))

    def test_set_absolute_follows_the_toolhead_gate(self):
        self.assertEqual(can_set_absolute(obs(state="printing")), Verdict("pause-first", R_PAUSE_FIRST))
        self.assertEqual(can_set_absolute(obs(state="error")), Verdict("allowed", ""))

    def test_restart_refuses_while_printing_and_paused(self):
        self.assertEqual(can_restart(obs(state="printing")), Verdict("disabled", R_PRINTING))
        self.assertEqual(can_restart(obs(state="paused")), Verdict("disabled", R_PRINTING))
        self.assertEqual(can_restart(obs(state="standby")), Verdict("allowed", ""))
        self.assertEqual(can_restart(obs(state="error")), Verdict("allowed", ""))

    def test_restart_allows_with_a_busy_lane(self):
        # Busy is deliberately not a click-time gate — one-shots
        # queue behind the in-flight command.
        self.assertEqual(can_restart(obs(state="standby", busy=True)), Verdict("allowed", ""))

    def test_start_print_allows_not_homed_and_refuses_a_running_print(self):
        self.assertEqual(can_start_print(obs(homed_axes="")), Verdict("allowed", ""))
        self.assertEqual(can_start_print(obs(state="printing")), Verdict("disabled", R_PRINTING))
        self.assertEqual(can_start_print(obs(state="")), Verdict("disabled", R_UNKNOWN))

    def test_power_is_per_device(self):
        self.assertEqual(can_power(obs(state="printing"), True), Verdict("disabled", R_PRINTING))
        self.assertEqual(can_power(obs(state="printing"), False), Verdict("allowed", ""))
        self.assertEqual(can_power(obs(state="standby"), True), Verdict("allowed", ""))
        self.assertEqual(can_power(obs(state=""), False), Verdict("disabled", R_UNKNOWN))

    def test_macro_follows_the_restart_shape(self):
        self.assertEqual(can_macro(obs(state="printing")), Verdict("disabled", R_PRINTING))
        self.assertEqual(can_macro(obs(state="standby")), Verdict("allowed", ""))

    def test_z_offset_allows_mid_print_but_not_while_busy(self):
        # The explicit per-action row (N3): babystepping mid-print is
        # legitimate; the click gate is the busy flag.
        self.assertEqual(can_z_offset(obs(state="printing")), Verdict("allowed", ""))
        self.assertEqual(can_z_offset(obs(state="standby", busy=True)), Verdict("disabled", R_BUSY))


if __name__ == "__main__":
    unittest.main()
