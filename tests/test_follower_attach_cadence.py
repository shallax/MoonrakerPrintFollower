"""Executable follower attach cadence contracts."""
from tests import composed_runtime_support as harness

class AttachCadenceTests(harness.AttachCadenceTests):
    def test_the_render_drain_waits_for_a_worker_beyond_400_event_turns(self):
        surface = harness.SimpleNamespace(job={"state": "submitted"},
                                          nav={"job": None})
        turns = 0

        def events(_milliseconds):
            nonlocal turns
            turns += 1
            if turns == 401:
                surface.job = None

        self._drain_job(None, surface, harness.SimpleNamespace(events=events))
        self.assertEqual(turns, 401)

    def test_the_render_drain_still_fails_when_a_job_never_finishes(self):
        surface = harness.SimpleNamespace(job={"state": "submitted"},
                                          nav={"job": None})
        clock = harness.SimpleNamespace(now=0.0)

        def events(_milliseconds):
            clock.now += 0.1

        with harness.patch.object(harness, "time",
                                  harness.SimpleNamespace(monotonic=lambda: clock.now)):
            with self.assertRaisesRegex(AssertionError, "render queue never drained"):
                self._drain_job(None, surface, harness.SimpleNamespace(events=events),
                                timeout=2, deadline_s=0.3)

    def test_attached_nav_bakes_once_per_window_and_lands_the_latest_split(self):
        # 30 s of attached polls: the nav raster starts once per
        # window (never per poll), every completed bake commits as a
        # compatible raster (no discard loop), and the final bake
        # paints the LATEST split.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        final_split = None
        for poll in range(self.POLLS):
            split = 20 + poll * 14
            final_split = split
            self._poll(model, surface, payload, 5, split, clock, armed,
                       self.qt)
        clock.t += 10.0  # the last window expires
        self._fire_due_wakes(model, armed, clock)
        self._drain_job(model, surface, self.qt)
        self.assertTrue(surface.nav["url"], "the warm raster never landed")
        self.assertEqual(surface.nav["key"][3], final_split,
                         "the final bake painted a stale split")
        self.assertLessEqual(len(starts["nav"]),
                             self.POLLS * self.POLL_S / 3.0 + 2,
                             "the nav raster baked near per poll")
        self._spacing(starts["nav"], 2.9)

    def test_attached_nav_keeps_a_compatible_raster_eligible(self):
        # A committed raster whose key differs only in the split
        # stays eligible while attached (the QML tail owns the exact
        # progress); detached, the exact demand gate returns.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        self._poll(model, surface, payload, 5, 50, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        self.assertTrue(surface.nav["url"], "the warm raster never landed")
        painted = surface.nav["key"][3]
        self._poll(model, surface, payload, 5, 300, clock, armed, self.qt)
        self.assertEqual(model.plate_renderer.navigation_data(surface),
                         surface.nav["url"],
                         "the compatible raster retired on a split advance")
        model.setFollowerAttached(False)
        self.assertEqual(model.plate_renderer.navigation_data(surface), "",
                         "the detached demand tolerates a stale split")
        model.setFollowerAttached(True)
        self.assertLess(painted, 300,
                        "the compatible raster was not the older one")

    def test_attached_nav_retires_ink_ahead_of_a_corrected_boundary(self):
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        self._poll(model, surface, payload, 5, 300, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        self.assertTrue(model.plate_renderer.navigation_data(surface))
        self._poll(model, surface, payload, 5, 50, clock, armed, self.qt)
        self.assertEqual(model.plate_renderer.navigation_data(surface), "",
                         "a warm raster with future ink survived a physical correction")

    def test_attached_nav_discards_a_future_job_after_a_boundary_correction(self):
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        model.plate_renderer.window_for(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 300)
        future_key = model.plate_renderer._navigation_key(surface)
        self.assertIsNotNone(surface.nav["job"])
        model.plate_renderer.window_for(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._drain_job(model, surface, self.qt)
        self.assertNotEqual(surface.nav["key"], future_key,
                            "an in-flight future raster promoted over corrected progress")

    def test_attached_nav_failure_retries_once_per_window(self):
        # A failing warm render must not hot-retry per poll: the
        # hard-key latch holds for the window, one retry per expiry,
        # and the recovery after the window paints the raster.
        model = self.monitor()
        module = self.qt.load("PlateRenderController")

        def failing(window, plot, view, split=None, **kwargs):
            raise RuntimeError("injected navigation render failure")

        model, surface, clock, armed, starts = self._attached(model)
        starts["nav"].clear()
        with harness.patch.object(module, "render_navigation_layer", failing):
            payload = self._payload(600)
            for poll in range(self.POLLS):
                self._poll(model, surface, payload, 5, 20 + poll * 14,
                           clock, armed, self.qt)
            # A worker may still be completing the last failed bake.
            # Drain before restoring the renderer and advancing the fake
            # clock: a late failure otherwise arms a retry AFTER that
            # advance, which no amount of real event pumping can expire.
            self._drain_job(model, surface, self.qt)
        self.assertEqual(surface.nav["url"], "", "a failed render promoted")
        self.assertLessEqual(len(starts["nav"]),
                             self.POLLS * self.POLL_S / 3.0 + 2,
                             "the failing nav render hot-retried per poll")
        self._spacing(starts["nav"], 2.9)
        # Force the terminal to belong to the final demand, independent of
        # how quickly the failing worker ran relative to the simulated polls.
        with harness.patch.object(module, "render_navigation_layer", failing):
            clock.t = max(clock.t + 5.0, surface.nav.get("wake_at") or 0.0)
            self._fire_due_wakes(model, armed, clock)
            self._drain_job(model, surface, self.qt)
        self.assertEqual(surface.nav["failed"], model.plate_renderer._navigation_key(surface))
        # The recovery: the next window's wake renders for real.
        clock.t += 5.0
        self._fire_due_wakes(model, armed, clock)
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(6)
            harness.time.sleep(0.01)
        self.assertTrue(surface.nav["url"],
                        "the recovered render never painted the raster")

    def test_a_hard_scene_change_bypasses_the_nav_window(self):
        # A LAYER change genuinely re-bakes the scene: it must not
        # wait out the follow window. (The zoom is no longer hard per
        # step — a wheel's steps are one intent and ride the settle;
        # see test_a_zoom_burst_coalesces_onto_its_settle.)
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        self._poll(model, surface, payload, 5, 50, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        before = len(starts["nav"])
        # A NEW layer is new geometry, so it arrives as its own payload
        # (the nav key identifies the window's content by payload
        # identity — re-feeding the same dict would be the same scene).
        layer_payload = self._payload(600)
        self._poll(model, surface, layer_payload, 6, 50, clock, armed,
                   self.qt)
        self._drain_job(model, surface, self.qt)
        self.assertGreater(len(starts["nav"]), before,
                           "the layer change waited out the follow window")

    def test_a_zoom_burst_coalesces_onto_its_settle(self):
        # The measured storm: the zoom rides the content key (the grid
        # and the stroke floor are baked at the level they present
        # at), so every wheel step used to bypass the follow window and
        # buy a whole-scene 4x composite — a chain in which each bake
        # was superseded by the next step. A burst is ONE intent: the
        # steps ride the short settle, and the level the wheel stops on
        # bakes exactly once, without waiting out the follow window.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        self._poll(model, surface, payload, 5, 50, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        before = len(starts["nav"])
        key = surface.nav["key"]
        self.assertIsNotNone(key, "the warm raster never promoted")
        for zoom in (1.2, 1.4, 1.6, 1.8, 2.0):
            model.setFollowerView("popover", zoom, 0.7, 400, 300, False,
                                  0.0, 0.0)
            self.qt.events(5)
        self._drain_job(model, surface, self.qt)
        self.assertEqual(len(starts["nav"]), before,
                         "a wheel step bypassed the settle")
        # The settle expires: one bake, for the level the wheel left.
        clock.t += 0.2
        self._fire_due_wakes(model, armed, clock)
        self._drain_job(model, surface, self.qt)
        self.assertEqual(len(starts["nav"]), before + 1,
                         "the settle did not bake exactly once")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and surface.nav["key"] != key:
            self.qt.events(6)
            harness.time.sleep(0.01)
        self.assertEqual(surface.nav["key"][-1], 2.0,
                         "the settled raster baked a superseded zoom")

    def test_the_live_bake_hands_its_committed_composite_forward(self):
        # The incremental path is WIRED, not merely available: the next
        # window's bake of the SAME scene must receive the composite the
        # last one committed, with its split, and a scene change (a new
        # layer's payload) must receive nothing — a copy is only ever
        # handed over for a picture the caller proves is this scene.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        module = self.qt.load("PlateRenderController")
        real_nav = module.render_navigation_layer
        handed = []

        def recording_nav(window, plot, view, split=None, cancel=None,
                          previous=None, previous_split=0):
            handed.append((previous is not None, previous_split, split))
            return real_nav(window, plot, view, split=split, cancel=cancel,
                            previous=previous, previous_split=previous_split)

        with harness.patch.object(module, "render_navigation_layer", recording_nav):
            payload = self._payload(600)
            self._poll(model, surface, payload, 5, 50, clock, armed, self.qt)
            self._drain_job(model, surface, self.qt)
            self.assertTrue(surface.nav["url"], "the first bake never landed")
            committed = surface.nav["key"][3]
            clock.t += 4.0
            self._poll(model, surface, payload, 5, 300, clock, armed,
                       self.qt)
            self._drain_job(model, surface, self.qt)
            clock.t += 4.0
            self._poll(model, surface, self._payload(600), 6, 60, clock,
                       armed, self.qt)
            self._drain_job(model, surface, self.qt)
        self.assertEqual(handed, [(False, 0, 50), (True, committed, 300),
                                  (False, 0, 60)],
                         "the live bake mis-handed its composite")

    def test_attached_prefix_checkpoints_on_a_five_second_cadence(self):
        # 30 s of attached polls: the native prefix advances at most
        # once per cadence (never per motion threshold), and the last
        # checkpoint snapshots the latest split.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        final_split = None
        for poll in range(self.POLLS):
            split = 100 + poll * 12
            final_split = split
            self._poll(model, surface, payload, 5, split, clock, armed,
                       self.qt)
        # The print stands still at the last split: the frozen split
        # still crosses the cadence, and the next poll's checkpoint
        # paints it — a window's lag is the cadence's design, a
        # stranded prefix is not.
        #
        # SETTLE FIRST, THEN ADVANCE. The advance has to clear the
        # deadline *the last commit established*, and that deadline is
        # armed from this same fake clock at commit time
        # (MoonrakerMonitorModel.plate_renderer._raster_committed: `_prefix_checkpoint_at
        # = time.monotonic() + 5.0`). An older render still in flight — a
        # prefix for the previous split — can commit AFTER the advance,
        # and it then re-arms the window five more simulated seconds out.
        # Nothing the test can pump will make that due, because these
        # waits move real events and never this clock: the checkpoint
        # stays stranded at the previous split (556 against 568 on a
        # loaded Windows runner, while the same commit's other run
        # passed).
        #
        # Draining first removes the possibility of a stale commit landing
        # after the advance at all; taking the max then respects a
        # deadline armed by whatever did commit.
        self._drain_job(model, surface, self.qt)
        layer_number = 5
        wrapped = surface.layers[layer_number]
        clock.t = max(clock.t + 5.0,
                      getattr(wrapped, "_prefix_checkpoint_at", 0.0))
        # The final demand, on the frozen split. Its completion is awaited
        # by the layer's TOKEN — bumped when a job is submitted and popped
        # when it commits — not by a render-start count (which cannot say
        # WHICH job finished) and not by an empty queue (also observable
        # in the gap before a job is submitted). A split that is already
        # current needs no render, and then no token is pending either.
        model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                   "next": None}, layer_number, "motion index",
                             final_split)
        self._fire_due_wakes(model, armed, clock)
        self.assertTrue(
            self._await_prefix_job(surface, layer_number, final_split, self.qt),
            "the final checkpoint's render never completed")
        self._drain_job(model, surface, self.qt)
        wrapped = surface.layers[layer_number]
        self.assertGreater(wrapped.prefixSplit, 0,
                           "the attached prefix never checkpointed")
        self.assertEqual(wrapped.prefixSplit, final_split,
                         "the last checkpoint painted a stale split")
        self.assertLessEqual(len(starts["prefix"]),
                             self.POLLS * self.POLL_S / 5.0 + 2,
                             "the prefix advanced far more than once per cadence")
        self._spacing(starts["prefix"], 4.9)

    def test_a_stale_commit_after_the_final_advance_strands_the_checkpoint(self):
        """The ordering the fix exists for, FORCED rather than waited for.

        A prefix render is held in flight across the final clock advance,
        so its owner-thread commit lands after it — and `_raster_committed`
        re-arms `_prefix_checkpoint_at` from the fake clock at THAT
        moment. The naive advance is then provably short of the deadline,
        and because these waits pump real events without ever moving the
        fake clock, the checkpoint stays stranded at the older split
        however long anything waits. That is the 556-against-568 failure
        on a loaded Windows runner, while the same commit's other run
        passed.

        Both halves are asserted, which is what makes this a regression
        rather than a rerun: the naive ordering MUST strand the
        checkpoint (if it ever stopped doing so, this test would say the
        ordering is no longer reproduced), and advancing past the deadline
        the commit armed MUST land it.
        """
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        module = self.qt.load("PlateRenderController")
        real_prefix = module.render_layer_prefix
        hold = {"armed": False, "used": False}
        gate = harness.threading.Event()
        reached = harness.threading.Event()
        escaped = {}
        # Unconditional, and registered before anything can fail: a
        # worker left parked on the gate would stall the rest of the run.
        self.addCleanup(gate.set)

        def gated_prefix(payload_arg, plot, view, split, **kwargs):
            # Hold the first prefix render submitted after the polls —
            # whichever split it is for; the cadence decides when one is
            # wanted, so naming a split here would hold nothing. The
            # `reached` event witnesses the worker ARRIVING, so the
            # ordering is established by the barrier rather than by a
            # runner happening to be slow.
            if hold["armed"] and not hold["used"]:
                hold["used"] = True
                reached.set()
                # The release is INLINE (see below), so this cannot
                # legitimately time out. Ignoring the return would let
                # the worker render without the release, leaving
                # `reached` set and the later assertion satisfied by a
                # worker that is no longer parked — the barrier bypassed
                # silently. Recorded instead, and asserted after the
                # settle.
                if not gate.wait(60.0):
                    escaped["timeout"] = True
            return real_prefix(payload_arg, plot, view, split, **kwargs)

        final_split = None
        with harness.patch.object(module, "render_layer_prefix", gated_prefix):
            for poll in range(self.POLLS):
                split = 100 + poll * 12
                final_split = split
                hold["armed"] = True
                self._poll(model, surface, payload, 5, split, clock, armed,
                           self.qt)
            # The worker must be PARKED on the gate before the advance,
            # or the ordering below is not the one being forced.
            self.assertTrue(reached.wait(10.0),
                            "no prefix render reached the gate; the ordering "
                            "this test exists to force did not happen")

            # THE NAIVE ORDERING: advance, then release, then settle. The
            # release is INLINE and comes after the advance, so the stale
            # commit cannot land before it — no elapsed delay decides the
            # ordering, and the worker cannot proceed without it.
            clock.t += 5.0
            layer_number = 5
            wrapped = surface.layers[layer_number]
            model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                       "next": None}, layer_number,
                             "motion index", final_split)
            self._fire_due_wakes(model, armed, clock)
            gate.set()
            self._drain_job(model, surface, self.qt)
            self.assertFalse(escaped.get("timeout"),
                             "the worker escaped the gate: it rendered without "
                             "the release, so the ordering was not forced")
            self.assertTrue(hold["used"],
                            "no prefix render was held; the ordering this "
                            "test exists to force did not happen")
            self.assertGreater(wrapped._prefix_checkpoint_at, clock.t,
                               "the stale commit did not re-arm past the "
                               "advance; the ordering is no longer reproduced")
            self.assertNotEqual(wrapped.prefixSplit, final_split,
                                "the naive advance reached the frozen split; "
                                "this ordering no longer demonstrates the defect")

            # THE CORRECTION: respect the deadline the commit armed, then
            # demand the frozen split again and await the job the demand
            # starts — by its token, and asserted rather than assumed.
            clock.t = max(clock.t + 5.0,
                          getattr(wrapped, "_prefix_checkpoint_at", 0.0))
            model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                       "next": None}, layer_number,
                             "motion index", final_split)
            self._fire_due_wakes(model, armed, clock)
            self.assertTrue(
                self._await_prefix_job(surface, layer_number, final_split,
                                       self.qt),
                "the corrected demand's render never completed")
            self._drain_job(model, surface, self.qt)

        wrapped = surface.layers[5]
        self.assertEqual(wrapped.prefixSplit, final_split,
                         "the checkpoint stayed stranded past a stale commit")

    def test_the_prefix_waiter_stays_pending_until_the_job_commits(self):
        """The PENDING branch of `_await_prefix_job`, forced.

        Everywhere else in this file the demand completes within the
        fires, so the waiter takes its already-current path and the
        pending branch is never entered. That is exactly how a wait keyed
        by the wrong dictionary key went unnoticed: it never mattered,
        because the branch it guarded never ran. Here a prefix render is
        parked, its integer-keyed token is observed BEFORE any event
        processing can consume completion, and the waiter is shown to
        stay pending until the worker is released AND its owner-thread
        commit lands.
        """
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        module = self.qt.load("PlateRenderController")
        real_prefix = module.render_layer_prefix
        gate = harness.threading.Event()
        reached = harness.threading.Event()
        self.addCleanup(gate.set)

        escaped = {}

        def gated_prefix(payload_arg, plot, view, split, **kwargs):
            reached.set()
            # Recorded rather than discarded: a timed-out wait would let
            # the worker render without a release, leaving `reached` set
            # and the park unproven.
            if not gate.wait(60.0):
                escaped["timeout"] = True
            return real_prefix(payload_arg, plot, view, split, **kwargs)

        layer_number = 5
        final_split = None
        with harness.patch.object(module, "render_layer_prefix", gated_prefix):
            for poll in range(self.POLLS):
                final_split = 100 + poll * 12
                self._poll(model, surface, payload, layer_number, final_split,
                           clock, armed, self.qt)
            self.assertTrue(reached.wait(10.0),
                            "no prefix render was submitted to park")
            clock.t = max(clock.t + 5.0,
                          getattr(surface.layers[layer_number],
                                  "_prefix_checkpoint_at", 0.0))
            model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                       "next": None}, layer_number,
                             "motion index", final_split)
            self._fire_due_wakes(model, armed, clock)
            self.qt.events(6)
            # The token, on the LAYER NUMBER, while the worker is parked.
            pending = surface.tokens.get(layer_number)
            self.assertIsNotNone(
                pending, "no pending token for the parked render — the waiter "
                "would take its already-current path and prove nothing")
            # Outstanding work: the waiter must NOT report completion.
            self.assertFalse(
                self._await_prefix_job(surface, layer_number, final_split,
                                       self.qt, timeout=0.5),
                "the waiter reported completion while a job was pending")
            self.assertEqual(surface.tokens.get(layer_number), pending,
                             "the token moved while the worker was parked")
            # Release, and require the waiter to see the commit land.
            gate.set()
            self.assertTrue(
                self._await_prefix_job(surface, layer_number, final_split,
                                       self.qt),
                "the waiter never saw the parked job complete")
            self.assertFalse(escaped.get("timeout"),
                             "the worker escaped the gate: it rendered without "
                             "a release")
        self._drain_job(model, surface, self.qt)

    def test_the_waiter_sees_a_job_that_completes_in_its_first_pump(self):
        """A job that completes inside the waiter's FIRST pump.

        The waiter's `seen_pending` has to be sampled before any event
        processing, because a job submitted beforehand can commit during
        that first qt.events() — after which its token is gone and the
        loop never sees it. The waiter would then read the layer as
        already-current and report a completion it never witnessed. The
        split demanded here is deliberately NOT the one that commits, so
        that already-current fallback cannot mask the miss.
        """
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        module = self.qt.load("PlateRenderController")
        real_prefix = module.render_layer_prefix
        gate = harness.threading.Event()
        reached = harness.threading.Event()
        queued = harness.threading.Event()
        escaped = {}
        self.addCleanup(gate.set)

        def gated_prefix(payload_arg, plot, view, split, **kwargs):
            reached.set()
            # Checked, not discarded: a timed-out wait would let the
            # worker render without a release, leaving the park unproven.
            if not gate.wait(60.0):
                escaped["timeout"] = True
            return real_prefix(payload_arg, plot, view, split, **kwargs)

        # The completion is emitted on the WORKER and delivered to the
        # owner thread as a queued call. A DirectConnection runs this
        # slot on the emitting thread, at emit time — so it fires once
        # the completion is genuinely QUEUED for the owner thread, which
        # is the state the waiter's first pump has to consume. Setting a
        # flag inside render_layer_prefix would not prove that: the
        # caller still has to write the PNG and emit.
        from PyQt6.QtCore import Qt as _Qt
        model.plate_renderer._bridge.done.connect(
            lambda *args: queued.set(),
            _Qt.ConnectionType.DirectConnection)

        layer_number = 5
        committed = None
        with harness.patch.object(module, "render_layer_prefix", gated_prefix):
            for poll in range(self.POLLS):
                committed = 100 + poll * 12
                self._poll(model, surface, payload, layer_number, committed,
                           clock, armed, self.qt)
            self.assertTrue(reached.wait(10.0),
                            "no prefix render was submitted to park")
            clock.t = max(clock.t + 5.0,
                          getattr(surface.layers[layer_number],
                                  "_prefix_checkpoint_at", 0.0))
            model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                       "next": None}, layer_number,
                             "motion index", committed)
            self._fire_due_wakes(model, armed, clock)
            self.qt.events(6)
            self.assertIsNotNone(surface.tokens.get(layer_number),
                                 "no pending token to race")
            # Release, then block WITHOUT PUMPING until the worker has
            # emitted — the completion is then queued and only the first
            # pump can deliver it, which is the race exactly.
            gate.set()
            self.assertTrue(queued.wait(10.0),
                            "the worker never emitted its completion")
            # The token is still there because nothing has been pumped.
            # (Any same-layer follow-on work is queued behind it and the
            # helper's loop consumes it too before the deadline.)
            self.assertIsNotNone(surface.tokens.get(layer_number),
                                 "the token was consumed before the pump; "
                                 "this no longer forces the first-pump race")
            self.assertFalse(escaped.get("timeout"),
                             "the worker escaped the gate: it rendered without "
                             "a release")

            class FirstPump:
                """`qt.events`, but the waiter's FIRST pump keeps pumping
                until the watched layer has nothing outstanding.

                The helper samples `seen_pending` before it pumps, and
                that sample only decides anything if ONE pump delivers
                the completion — the six-millisecond pump did not: the
                token outlived it, and the helper's own in-loop
                re-sample then set the flag the pre-pump sample exists
                to set. That is why this test passed with the
                regression restored. Draining the first pump removes
                the timing from the question and leaves the logic.
                """

                def __init__(self):
                    self.first = True
                    self.drained = None

                def events(self, milliseconds=0):
                    if not self.first:
                        return self.qt_events(milliseconds)
                    self.first = False
                    deadline = harness.time.monotonic() + 10.0
                    while True:
                        self.qt_events(milliseconds)
                        if surface.tokens.get(layer_number) is None:
                            self.drained = True
                            return
                        if harness.time.monotonic() >= deadline:
                            self.drained = False
                            return

            waiter_qt = FirstPump()
            waiter_qt.qt_events = self.qt.events
            # A split that will NOT be the committed one, so the
            # already-current path cannot answer for the missed token.
            self.assertTrue(
                self._await_prefix_job(surface, layer_number, committed + 999,
                                       waiter_qt),
                "the waiter missed a job that completed in its first pump")
            self.assertTrue(waiter_qt.drained,
                            "the token outlived even a draining first pump, so "
                            "the pre-pump sample is not what answered and this "
                            "test no longer forces the race it claims to")
        self._drain_job(model, surface, self.qt)

    def test_an_attached_layer_change_bypasses_the_prefix_cadence(self):
        # A new layer's prefix is a fresh demand: it renders
        # immediately, cadence or not.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        self._poll(model, surface, payload, 5, 300, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        starts["prefix"].clear()
        self._poll(model, surface, payload, 6, 40, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        self.assertGreaterEqual(len(starts["prefix"]), 1,
                                "the new layer's prefix waited for the cadence")
        self.assertTrue(surface.layers[6].prefixValid,
                        "the new layer's prefix never committed")

    def test_detached_prefix_keeps_the_motion_threshold_policy(self):
        # Detached scrubbing stays threshold-driven: the cadence must
        # never stretch a manual seek.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # The follower now starts attached; the manual seek is the
        # detach (a bare detach is refused with no layer to hold).
        model.setFollowerLayerAnchor(5)
        surface = model.plate_renderer._surfaces["popover"]
        payload = self._payload(600)
        self._window(model, "popover", 5, payload)
        model.plate_renderer.window_for(surface, {"prev": None, "current": payload,
                                   "next": None}, 5, "motion index", 100)
        self._pump_rasters(model, "popover")
        self.assertFalse(model.plate_renderer._prefix_wanted(surface, 5, 150),
                         "a sub-threshold advance re-rendered the prefix")
        self.assertTrue(model.plate_renderer._prefix_wanted(surface, 5, 250),
                        "a past-threshold advance left the prefix stale")

