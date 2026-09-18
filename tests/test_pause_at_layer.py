import pathlib
import unittest

from tests.fake_moonraker import FakeMoonraker
from tests.qt_runtime_support import QT_AVAILABLE
from plugins.PauseScheduleService import PauseScheduleService, due_end_of_layer_pauses
from plugins.MoonrakerSession import MoonrakerSessionState, PollPolicy, RequestCategory

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
QML = (PLUGINS / "MoonrakerPreviewCard.qml").read_text()


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

    def test_pause_items_merge_baked_rows_sorted_by_layer(self):
        # The 2026-09-16 ruling: the list merges the manual schedule
        # with the gcode's baked pauses, always sorted by layer, and
        # the baked rows are read-only.
        from plugins.PreviewFormatting import pause_items
        remaining = {3: 120.0, 7: 400.0, 11: 900.0}
        items = pause_items({7}, {7: "scheduled"}, {3, 11},
                            lambda layer: remaining.get(layer),
                            lambda seconds: f"{seconds:.0f}s",
                            current=6,
                            clock=lambda seconds: "14:32")
        self.assertEqual(items, [
            {"layer": 4, "eta": "in 120s · ≈14:32", "state": "baked", "passed": True},
            {"layer": 8, "eta": "in 400s · ≈14:32", "state": "scheduled"},
            {"layer": 12, "eta": "in 900s · ≈14:32", "state": "baked", "passed": False},
        ])


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class NextPausePolicyTests(unittest.TestCase):
    """_compute_next_pause's policy: the NEXT scheduled pause (baked or
    manual, the live ruling) as (human layer, composed ETA, a TIME-based
    progress fraction, baked flag). The seam is the few attributes the
    function reads — the coordinator is built without its signal
    wiring."""
    def _coordinator(self, manual=(), states=None, baked=(), current=0, total=None,
                     elapsed=0.0, anchor_elapsed=None, remaining_value=240.0):
        from types import SimpleNamespace, ModuleType
        import sys
        # PrintCoordinator imports UM.Logger; no host doubles exist in
        # the container leg, so the seam registers a no-op module once.
        if "UM" not in sys.modules:
            um = ModuleType("UM")
            logger_module = ModuleType("UM.Logger")
            logger_module.Logger = SimpleNamespace(
                log=staticmethod(lambda *args, **kwargs: None))
            um.Logger = logger_module
            sys.modules["UM"] = um
            sys.modules["UM.Logger"] = logger_module
        from plugins.PrintCoordinator import PrintCoordinator
        from plugins.PrintState import PhysicalLayer
        # __new__ without __init__: sip forbids object.__new__ on a
        # QObject subclass, so the class's own allocator is the seam.
        coordinator = PrintCoordinator.__new__(PrintCoordinator)
        coordinator._pause_anchor_elapsed = anchor_elapsed
        coordinator._pause_anchor_job = None
        coordinator._prev_print_state = None
        # The resolver-None fallback reads this (the panel's catch:
        # the seam never seeded it, so the fallback had no owner).
        coordinator._last_index = current if current is not None else 0
        view = SimpleNamespace(pause_layers=set(baked))
        coordinator._index = SimpleNamespace(view=view)
        coordinator._pauses = SimpleNamespace(layers=set(manual), states=dict(states or {}))
        calls = []
        def remaining(layer, view_arg=None, end=True):
            calls.append((layer, view_arg, end))
            return remaining_value
        coordinator._preview = SimpleNamespace(remaining=remaining,
                                               format_duration=lambda seconds: "4m")
        return coordinator, PhysicalLayer(index=current, total=total), calls, view

    def test_the_next_pause_is_the_first_unpassed_row(self):
        coordinator, physical, calls, _ = self._coordinator(manual={4}, baked={6},
                                                            current=0, elapsed=120.0)
        layer, eta, fraction, baked_flag = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(layer, 5)
        self.assertEqual(fraction, 1 / 3)
        self.assertFalse(baked_flag)
        self.assertTrue(eta.startswith("in 4m · ≈"))
        self.assertIn((4, coordinator._index.view, True), calls)

    def test_a_baked_pause_counts_as_scheduled_ahead(self):
        coordinator, physical, _, _ = self._coordinator(baked={6}, current=3, elapsed=120.0)
        layer, _, fraction, baked_flag = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(layer, 7)
        self.assertEqual(fraction, 1 / 3)
        self.assertTrue(baked_flag)

    def test_rows_behind_the_print_are_never_the_target(self):
        # The baked rows' passed flag alone left a fired manual row
        # reading as the target forever (the live report, the stuck
        # 00:00:00 ETA) — a row behind the current layer is skipped
        # whatever its kind.
        coordinator, physical, _, _ = self._coordinator(manual={2, 9}, current=4,
                                                        elapsed=120.0)
        layer, _, _, baked_flag = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(layer, 10)
        self.assertFalse(baked_flag)

    def test_no_pause_ahead_is_the_none_triple(self):
        coordinator, physical, _, _ = self._coordinator(current=2, elapsed=120.0)
        self.assertEqual(coordinator._compute_next_pause(physical, 120.0),
                         (None, "", None, False))

    def test_the_fraction_is_time_based_not_layer_based(self):
        # The live ruling: the bar tracks time toward the deadline,
        # not layers — a layer-based bar credited the in-progress
        # layer and read half the span early.
        coordinator, physical, _, _ = self._coordinator(manual={4}, current=2,
                                                        elapsed=120.0)
        _, _, fraction, _ = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(fraction, 1 / 3)

    def test_the_bar_resets_at_the_last_pause(self):
        # The zero point is the last time the print PAUSED (the live
        # ruling): at the resume itself the span reads zero.
        coordinator, physical, _, _ = self._coordinator(manual={9}, current=3,
                                                        elapsed=120.0,
                                                        anchor_elapsed=120.0)
        _, _, fraction, _ = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(fraction, 0.0)

    def test_the_bar_spans_the_deadlines(self):
        # Mid-span: the progress is the time between the last pause
        # and the next deadline.
        coordinator, physical, _, _ = self._coordinator(manual={9}, current=5,
                                                        elapsed=180.0,
                                                        anchor_elapsed=120.0,
                                                        remaining_value=60.0)
        _, _, fraction, _ = coordinator._compute_next_pause(physical, 180.0)
        self.assertEqual(fraction, 0.5)

    def test_the_bar_reads_full_at_the_deadline(self):
        coordinator, physical, _, _ = self._coordinator(manual={9}, current=8,
                                                        elapsed=300.0,
                                                        remaining_value=0.0)
        _, _, fraction, _ = coordinator._compute_next_pause(physical, 300.0)
        self.assertEqual(fraction, 1.0)

    def test_the_last_known_index_carries_a_resolver_that_drops_to_none(self):
        # The live report: right after a pause the resolver can drop
        # to None — the last known index carries the target selection
        # instead of hiding every pause.
        coordinator, physical, _, _ = self._coordinator(manual={9}, current=None,
                                                        elapsed=120.0)
        coordinator._last_index = 5
        layer, _, _, _ = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(layer, 10)

    def test_the_bar_hides_without_an_eta(self):
        # No ETA, no bar (the live ruling): the fraction is None —
        # the model's -1.0 sentinel renders the fill absent.
        coordinator, physical, _, _ = self._coordinator(manual={4}, current=2,
                                                        elapsed=120.0,
                                                        remaining_value=None)
        layer, _, fraction, _ = coordinator._compute_next_pause(physical, 120.0)
        self.assertEqual(layer, 5)
        self.assertIsNone(fraction)

    def test_a_stale_elapsed_cannot_drive_the_bar_negative(self):
        # The clamp floor: an elapsed before the anchor (a state
        # hiccup) reads zero, never negative — the bar can never
        # exceed 100% or underflow.
        coordinator, physical, _, _ = self._coordinator(manual={9}, current=3,
                                                        elapsed=50.0,
                                                        anchor_elapsed=120.0)
        _, _, fraction, _ = coordinator._compute_next_pause(physical, 50.0)
        self.assertEqual(fraction, 0.0)

    def test_the_anchor_is_the_last_pause_time(self):
        # The zero point: the elapsed print time when the print LAST
        # PAUSED (the state edge — any pause), or the print's start
        # while it never has.
        coordinator, _, _, _ = self._coordinator()
        self.assertIsNone(coordinator._update_pause_anchor("job1", "printing", 100.0))
        self.assertEqual(coordinator._update_pause_anchor("job1", "paused", 110.0), 110.0)
        # The resume keeps the anchor — the span continues from it.
        self.assertEqual(coordinator._update_pause_anchor("job1", "printing", 120.0), 110.0)

    def test_a_second_pause_reanchors(self):
        # Only the paused EDGE records — consecutive paused polls are
        # the same pause; a later pause (printing in between) is a
        # fresh zero point.
        coordinator, _, _, _ = self._coordinator()
        coordinator._update_pause_anchor("job1", "paused", 110.0)
        self.assertEqual(coordinator._update_pause_anchor("job1", "paused", 115.0), 110.0)
        coordinator._update_pause_anchor("job1", "printing", 200.0)
        self.assertEqual(coordinator._update_pause_anchor("job1", "paused", 300.0), 300.0)

    def test_a_job_change_clears_the_anchor(self):
        coordinator, _, _, _ = self._coordinator()
        coordinator._update_pause_anchor("job1", "paused", 110.0)
        self.assertIsNone(coordinator._update_pause_anchor("job2", "printing", 200.0))


if __name__ == "__main__": unittest.main()
