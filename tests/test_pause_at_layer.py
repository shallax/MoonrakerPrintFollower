import pathlib
import unittest

from tests.fake_moonraker import FakeMoonraker
from plugins.PauseScheduleService import PauseScheduleService, due_end_of_layer_pauses
from plugins.PreviewFormatting import pause_items, pause_summary
from plugins.MoonrakerSession import MoonrakerSessionState, PollPolicy, RequestCategory
from qt_runtime_support import QT_AVAILABLE, ScriptedTransport, runtime

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
QML = (PLUGINS / "MoonrakerPreviewCard.qml").read_text(encoding="utf-8")


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
        self.assertNotIn("pause_at_layer", (PLUGINS / "PrinterConfig.py").read_text(encoding="utf-8"))

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
        controller = (PLUGINS / "PauseController.py").read_text(encoding="utf-8")
        self.assertIn('body={"script": "PAUSE"}', controller)
        self.assertIn("track_command", controller)
        self.assertIn("generation != self._generation", controller)

    def test_pause_items_merge_baked_rows_sorted_by_layer(self):
        # The 2026-09-16 ruling: the list merges the manual schedule
        # with the gcode's baked pauses, always sorted by layer, and
        # the baked rows are read-only.
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


class NextPausePolicyTests(unittest.TestCase):
    """NextPausePipeline's policy: the NEXT scheduled pause (baked or
    manual, the live ruling) as (human layer, composed ETA, a TIME-based
    progress fraction, baked flag). The seam is the pipeline's own
    collaborators — no coordinator, no Qt and no signal wiring."""
    def _pipeline(self, manual=(), states=None, baked=(), current=0, total=None,
                  elapsed=0.0, anchor_elapsed=None, remaining_value=240.0):
        from types import SimpleNamespace
        from plugins.NextPausePipeline import NextPausePipeline
        from plugins.PrintState import PhysicalLayer
        view = SimpleNamespace(pause_layers=set(baked))
        calls = []
        def remaining(layer, view_arg=None, end=True):
            calls.append((layer, view_arg, end))
            return remaining_value
        pipeline = NextPausePipeline(
            preview=SimpleNamespace(remaining=remaining,
                                    format_duration=lambda seconds: "4m"),
            pauses=SimpleNamespace(layers=set(manual), states=dict(states or {})),
            index=SimpleNamespace(view=view))
        pipeline._anchor_elapsed = anchor_elapsed
        pipeline._anchor_job = None
        pipeline._prev_state = None
        # The resolver-None fallback reads this (the panel's catch:
        # the seam never seeded it, so the fallback had no owner).
        pipeline._last_index = current if current is not None else 0
        return pipeline, PhysicalLayer(index=current, total=total), calls, view

    def test_the_next_pause_is_the_first_unpassed_row(self):
        pipeline, physical, calls, view = self._pipeline(manual={4}, baked={6},
                                                         current=0, elapsed=120.0)
        layer, eta, fraction, baked_flag = pipeline.compute(physical, 120.0)
        self.assertEqual(layer, 5)
        self.assertEqual(fraction, 1 / 3)
        self.assertFalse(baked_flag)
        self.assertTrue(eta.startswith("in 4m · ≈"))
        self.assertIn((4, view, True), calls)

    def test_a_baked_pause_counts_as_scheduled_ahead(self):
        pipeline, physical, _, _ = self._pipeline(baked={6}, current=3, elapsed=120.0)
        layer, _, fraction, baked_flag = pipeline.compute(physical, 120.0)
        self.assertEqual(layer, 7)
        self.assertEqual(fraction, 1 / 3)
        self.assertTrue(baked_flag)

    def test_rows_behind_the_print_are_never_the_target(self):
        # The baked rows' passed flag alone left a fired manual row
        # reading as the target forever (the live report, the stuck
        # 00:00:00 ETA) — a row behind the current layer is skipped
        # whatever its kind.
        pipeline, physical, _, _ = self._pipeline(manual={2, 9}, current=4,
                                                  elapsed=120.0)
        layer, _, _, baked_flag = pipeline.compute(physical, 120.0)
        self.assertEqual(layer, 10)
        self.assertFalse(baked_flag)

    def test_no_pause_ahead_is_the_none_triple(self):
        pipeline, physical, _, _ = self._pipeline(current=2, elapsed=120.0)
        self.assertEqual(pipeline.compute(physical, 120.0),
                         (None, "", None, False))

    def test_the_fraction_is_time_based_not_layer_based(self):
        # The live ruling: the bar tracks time toward the deadline,
        # not layers — a layer-based bar credited the in-progress
        # layer and read half the span early.
        pipeline, physical, _, _ = self._pipeline(manual={4}, current=2,
                                                  elapsed=120.0)
        _, _, fraction, _ = pipeline.compute(physical, 120.0)
        self.assertEqual(fraction, 1 / 3)

    def test_the_bar_resets_at_the_last_pause(self):
        # The zero point is the last time the print PAUSED (the live
        # ruling): at the resume itself the span reads zero.
        pipeline, physical, _, _ = self._pipeline(manual={9}, current=3,
                                                  elapsed=120.0,
                                                  anchor_elapsed=120.0)
        _, _, fraction, _ = pipeline.compute(physical, 120.0)
        self.assertEqual(fraction, 0.0)

    def test_the_bar_spans_the_deadlines(self):
        # Mid-span: the progress is the time between the last pause
        # and the next deadline.
        pipeline, physical, _, _ = self._pipeline(manual={9}, current=5,
                                                  elapsed=180.0,
                                                  anchor_elapsed=120.0,
                                                  remaining_value=60.0)
        _, _, fraction, _ = pipeline.compute(physical, 180.0)
        self.assertEqual(fraction, 0.5)

    def test_the_bar_reads_full_at_the_deadline(self):
        pipeline, physical, _, _ = self._pipeline(manual={9}, current=8,
                                                  elapsed=300.0,
                                                  remaining_value=0.0)
        _, _, fraction, _ = pipeline.compute(physical, 300.0)
        self.assertEqual(fraction, 1.0)

    def test_the_last_known_index_carries_a_resolver_that_drops_to_none(self):
        # The live report: right after a pause the resolver can drop
        # to None — the last known index carries the target selection
        # instead of hiding every pause.
        pipeline, physical, _, _ = self._pipeline(manual={9}, current=None,
                                                  elapsed=120.0)
        pipeline._last_index = 5
        layer, _, _, _ = pipeline.compute(physical, 120.0)
        self.assertEqual(layer, 10)

    def test_the_bar_hides_without_an_eta(self):
        # No ETA, no bar (the live ruling): the fraction is None —
        # the model's -1.0 sentinel renders the fill absent.
        pipeline, physical, _, _ = self._pipeline(manual={4}, current=2,
                                                  elapsed=120.0,
                                                  remaining_value=None)
        layer, _, fraction, _ = pipeline.compute(physical, 120.0)
        self.assertEqual(layer, 5)
        self.assertIsNone(fraction)

    def test_a_stale_elapsed_cannot_drive_the_bar_negative(self):
        # The clamp floor: an elapsed before the anchor (a state
        # hiccup) reads zero, never negative — the bar can never
        # exceed 100% or underflow.
        pipeline, physical, _, _ = self._pipeline(manual={9}, current=3,
                                                  elapsed=50.0,
                                                  anchor_elapsed=120.0)
        _, _, fraction, _ = pipeline.compute(physical, 50.0)
        self.assertEqual(fraction, 0.0)

    def test_the_anchor_is_the_last_pause_time(self):
        # The zero point: the elapsed print time when the print LAST
        # PAUSED (the state edge — any pause), or the print's start
        # while it never has.
        pipeline, _, _, _ = self._pipeline()
        self.assertIsNone(pipeline.update_anchor("job1", "printing", 100.0))
        self.assertEqual(pipeline.update_anchor("job1", "paused", 110.0), 110.0)
        # The resume keeps the anchor — the span continues from it.
        self.assertEqual(pipeline.update_anchor("job1", "printing", 120.0), 110.0)

    def test_a_second_pause_reanchors(self):
        # Only the paused EDGE records — consecutive paused polls are
        # the same pause; a later pause (printing in between) is a
        # fresh zero point.
        pipeline, _, _, _ = self._pipeline()
        pipeline.update_anchor("job1", "paused", 110.0)
        self.assertEqual(pipeline.update_anchor("job1", "paused", 115.0), 110.0)
        pipeline.update_anchor("job1", "printing", 200.0)
        self.assertEqual(pipeline.update_anchor("job1", "paused", 300.0), 300.0)

    def test_a_job_change_clears_the_anchor(self):
        pipeline, _, _, _ = self._pipeline()
        pipeline.update_anchor("job1", "paused", 110.0)
        self.assertIsNone(pipeline.update_anchor("job2", "printing", 200.0))


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the Qt model suite")
class MonitorPauseBlockTests(unittest.TestCase):
    """The popover's reading of the schedule (the monitor model's own
    surface): the Preview card's rows and flags, and — the load-bearing
    difference — the candidate re-read for the layer the POPOVER stands
    on rather than Cura's Preview selection. Scheduling one pause at the
    end of that layer and no other is the point of the whole block."""

    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.client = self.qt.load("MoonrakerClient").MoonrakerClient(transport=ScriptedTransport())
        self.client.configure("http://printer-a", "test-key", 750)
        self.client._poll_timer.stop()
        self.addCleanup(self.client.stop)
        self.block_data = self.block()
        self.model = None

    # ---- the harness -------------------------------------------------

    def block(self, manual=(), states=None, baked=(), current=10, **overrides):
        """The block the coordinator publishes, built the way it builds
        it (the row helper and the flags are the coordinator's own) —
        its candidate is CURA'S selection, the one value the popover
        must not adopt."""
        items = pause_items(set(manual), dict(states or {}), set(baked),
                            lambda layer: None, lambda seconds: f"{seconds:.0f}s",
                            current=current)
        block = {
            "pauseAtLayerActive": True,
            "pauseAtLayerCandidate": 5,  # Cura's Preview selection
            "pauseAtLayerCanToggle": True, "pauseAtLayerScheduled": False,
            "pauseAtLayerSummary": pause_summary(items),
            "pauseAtLayerItems": items, "pauseAtLayerUnavailableText": "",
            "pauseAtLayerHasBaked": any(item["state"] == "baked" for item in items),
            "pauseAtLayerHasClearable": bool(manual),
        }
        block.update(overrides)
        return block

    def snapshot(self, index=10, total=40):
        from plugins.PrintState import PhysicalLayer, PrintSnapshot
        from plugins.RemoteJobService import PrintObservation
        return PrintSnapshot(observation=PrintObservation("printing", "part.gcode", 100, 20, 12.5),
                             layer=PhysicalLayer(index=index, total=total))

    def build(self, **kwargs):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Mesh(QObject):
            changed = pyqtSignal()

            def __init__(self):
                super().__init__()
                self.snapshot = {}
                self.visible = True

            def set_thresholds(self, low, high): pass
            def set_visible(self, value): self.visible = value

        self.print_state = self.snapshot()
        kwargs.setdefault("pause_at_layer_block", lambda: self.block_data)
        model = self.qt.load("MoonrakerMonitorModel").MoonrakerMonitorModel(
            None, 1,
            client=self.client,
            print_state=lambda: self.print_state,
            config=lambda: self.qt.load("PrinterConfig").PrinterConfig(
                url="http://printer-a", api_key="test-key"),
            apply_config=lambda config: None,
            bed_mesh=Mesh(),
            identity=lambda: ("A", "Printer A"),
            **kwargs)
        self.addCleanup(model.setMonitoringActive, False)
        self.model = model
        return model

    def value(self, name):
        value = getattr(self.model, name)
        return value.value() if hasattr(value, "value") else value

    def publish(self, index=10, total=40):
        self.print_state = self.snapshot(index, total)
        self.model._publish()

    # ---- the surface -------------------------------------------------

    def test_the_whole_block_reaches_the_popover_for_its_own_layer(self):
        # The owner's scenario: the popover's slider stands on layer 200
        # (the anchor is the 0-based 199) while Cura's Preview selection
        # is 5. Every published value is read for layer 200 — the rows
        # and the flags are the card's, the candidate and its gates are
        # the popover's.
        self.build()
        self.block_data = self.block(manual={199}, states={199: "scheduled"}, baked={12}, current=13)
        self.publish(index=13, total=250)
        self.model.setFollowerLayerAnchor(199)
        self.assertEqual(self.model.followerLayerAnchor, 199)
        self.assertEqual(self.value("pauseAtLayerCandidate"), 200)
        self.assertTrue(self.value("pauseAtLayerActive"))
        self.assertTrue(self.value("pauseAtLayerCanToggle"))
        self.assertTrue(self.value("pauseAtLayerScheduled"))
        self.assertTrue(self.value("pauseAtLayerHasBaked"))
        self.assertTrue(self.value("pauseAtLayerHasClearable"))
        self.assertEqual(self.value("pauseAtLayerUnavailableText"), "")
        self.assertEqual(self.value("pauseAtLayerSummary"), "End-of-layer PAUSE: 13, 200")
        self.assertEqual(self.value("pauseAtLayerItems"),
                         [{"layer": 13, "eta": "ETA unavailable", "state": "baked", "passed": True},
                          {"layer": 200, "eta": "ETA unavailable", "state": "scheduled"}])

    def test_the_candidate_is_the_follower_layer_not_cura_selection(self):
        # The pin that matters: the block's candidate (Cura's selection,
        # 5) never becomes the popover's. Both are asserted so the test
        # cannot pass by the block happening to agree.
        self.build()
        self.publish()
        self.assertEqual(self.block_data["pauseAtLayerCandidate"], 5)
        self.assertEqual(self.value("pauseAtLayerCandidate"), 0, "no layer yet is no candidate")
        self.model.setFollowerLayerAnchor(199)
        self.assertEqual(self.value("pauseAtLayerCandidate"), 200)
        self.assertEqual(self.model.followerLayerAnchor + 1, self.value("pauseAtLayerCandidate"))

    def test_the_live_layer_is_the_candidate_before_any_slider_commit(self):
        # Attached and never slid: the popover schedules where it is
        # actually showing, so the published anchor is the fallback.
        self.build()
        self.model.setFollowerPopoverOpen(True)
        self.print_state = self.qt.load("PrintState").PrintSnapshot(
            plate_progress={"layers": {"current": {"classes": {}}}, "split": 7,
                            "method": "motion index", "anchor": 7})
        self.model._publish()
        self.assertEqual(self.value("plateProgressAnchor"), 7)
        self.assertEqual(self.value("pauseAtLayerCandidate"), 8)

    def test_the_gates_follow_the_popovers_layer(self):
        # The card's gates are the popover's gates, re-read for its
        # layer: the block's own canToggle/scheduled (Cura's 5) must not
        # cross.
        self.build()
        self.block_data = self.block(manual={199}, states={199: "scheduled"})
        self.publish(index=5, total=250)            # the popover sits BEHIND the print
        self.model.setFollowerLayerAnchor(3)
        self.assertEqual(self.value("pauseAtLayerCandidate"), 4)
        self.assertFalse(self.value("pauseAtLayerCanToggle"))
        self.assertFalse(self.value("pauseAtLayerScheduled"))
        self.assertEqual(self.value("pauseAtLayerUnavailableText"), "Layer 4 already printed")
        # The last layer ends the print: the same refusal the controller
        # applies at total - 1.
        self.publish(index=39, total=40)
        self.model.setFollowerLayerAnchor(39)
        self.assertFalse(self.value("pauseAtLayerCanToggle"))
        self.assertEqual(self.value("pauseAtLayerUnavailableText"), "Final layer ends the print")

    def test_a_baked_pause_at_the_popovers_layer_blocks_the_toggle(self):
        # The controller's backstop, mirrored: the gcode's own pause at
        # this layer leaves the manual button inert, and the reason is
        # the card's own wording.
        self.build()
        self.block_data = self.block(baked={199})
        self.publish()
        self.model.setFollowerLayerAnchor(199)
        self.assertFalse(self.value("pauseAtLayerCanToggle"))
        self.assertFalse(self.value("pauseAtLayerScheduled"))
        self.assertEqual(self.value("pauseAtLayerUnavailableText"),
                         "a pause is baked into the gcode at this layer")

    def test_without_the_seam_the_block_is_inert(self):
        # A model built without the coordinator's seam publishes the
        # declared defaults — never a half-built block.
        self.build(pause_at_layer_block=None)
        self.publish()
        self.assertEqual(self.value("pauseAtLayerCandidate"), 0)
        self.assertEqual(self.value("pauseAtLayerItems"), [])
        self.assertEqual(self.value("pauseAtLayerSummary"), "")
        self.assertFalse(self.value("pauseAtLayerCanToggle"))
        self.assertFalse(self.value("pauseAtLayerActive"))

    def test_the_pause_group_notifies_when_the_schedule_changes(self):
        # A key outside its signal's group never notifies: the popover
        # binds to the group, so a changed block must emit — and a quiet
        # poll must not.
        self.build()
        self.publish()
        seen = []
        self.model.pauseAtLayerChanged.connect(lambda: seen.append(1))
        self.model._publish()
        self.assertEqual(seen, [], "a quiet poll re-notified")
        self.block_data = self.block(manual={11}, states={11: "scheduled"})
        self.model._publish()
        self.assertTrue(seen, "the pause block changed without notifying")
        self.assertTrue(self.value("pauseAtLayerHasClearable"))

    # ---- the intents -------------------------------------------------

    def test_the_toggle_asks_for_the_end_of_the_layer_it_stands_on(self):
        # The popover's own layer, handed over as the 1-based human layer
        # the controller takes — and nothing else: the 0-based schedule
        # slot for layer 200 is 199, which is due only once the print has
        # moved PAST it (the end of 200, never its beginning, never any
        # other layer).
        toggles, removals, clears = [], [], []
        self.build(request_pause_toggle=toggles.append, request_pause_remove=removals.append,
                   request_pause_clear=lambda: clears.append(True))
        self.publish()
        self.model.setFollowerLayerAnchor(199)
        self.model.togglePauseAtLayer(self.value("pauseAtLayerCandidate"))
        self.assertEqual(toggles, [200])
        self.assertEqual(due_end_of_layer_pauses({199}, 199), [], "layer 200's own beginning")
        self.assertEqual(due_end_of_layer_pauses({199}, 200), [199], "the end of layer 200")
        self.assertEqual(due_end_of_layer_pauses({199}, 201), [199])
        # Junk and an absent layer never reach the coordinator.
        for layer in ("junk", None, -4, 0):
            self.model.togglePauseAtLayer(layer)
            self.model.removePauseAtLayer(layer)
        self.assertEqual(toggles, [200])
        self.assertEqual(removals, [])
        self.model.removePauseAtLayer(200)
        self.model.clearPauseAtLayer()
        self.assertEqual(removals, [200])
        self.assertEqual(clears, [True])

    def test_the_toggle_republishes_immediately(self):
        # The button's own label flips on the click: the model must not
        # wait for the next poll to re-read the schedule.
        self.block_data = self.block()
        self.build(request_pause_toggle=lambda layer: setattr(
            self, "block_data", self.block(manual={199}, states={199: "scheduled"})))
        self.publish()
        self.model.setFollowerLayerAnchor(199)
        self.assertFalse(self.value("pauseAtLayerScheduled"))
        self.model.togglePauseAtLayer(200)
        self.assertTrue(self.value("pauseAtLayerScheduled"),
                        "the schedule changed without a publish")


if __name__ == "__main__": unittest.main()
