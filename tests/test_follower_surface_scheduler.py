"""Executable follower surface scheduler contracts."""
from tests import composed_runtime_support as harness

class NativeRenderSchedulerTests(harness.NativeRenderSchedulerTests):
    def test_mini_and_popover_render_contexts_are_independent(self):
        # : the same decoded layer feeds both
        # surfaces, but each surface's wrappers, views, generations
        # and rasters are its own — feeding one never invalidates
        # the other's.
        model = self.monitor()
        self._feed(model, "popover", width=600, height=400)
        self._feed(model, "mini", width=90, height=90, compact=True)
        self._window(model, "popover", 5)
        self._window(model, "mini", 5)
        popover = model._plate_surfaces["popover"]
        mini = model._plate_surfaces["mini"]
        self.assertIsNot(popover.layers[5], mini.layers[5],
                         "the surfaces shared a PlateLayer")
        self._pump_rasters(model, "popover")
        self._pump_rasters(model, "mini")
        self.assertTrue(popover.layers[5].rasterValid)
        self.assertTrue(mini.layers[5].rasterValid)
        self.assertEqual(popover.layers[5].raster.width(), 600)
        self.assertEqual(mini.layers[5].raster.width(), 90)
        pop_gen, mini_gen = popover.generation, mini.generation
        model.setFollowerView("mini", 1.0, 0.7, 120, 120, True, 0.0, 0.0)
        self.qt.events(5)
        self.assertEqual(popover.generation, pop_gen,
                         "the mini's re-feed bumped the popover's generation")
        self.assertNotEqual(mini.generation, mini_gen)
        self.assertTrue(popover.layers[5].rasterValid,
                        "the mini's re-feed invalidated the popover's raster")
        mini_gen = mini.generation
        model.setFollowerView("popover", 1.5, 0.7, 600, 400, False, 10.0, 0.0)
        self.qt.events(5)
        self.assertNotEqual(popover.generation, pop_gen)
        self.assertEqual(mini.generation, mini_gen,
                         "the popover's re-feed bumped the mini's generation")

    def test_a_detached_popover_does_not_disturb_the_live_mini(self):
        # : the popover's frozen anchor is ITS
        # surface state — the mini keeps its live anchor and its
        # rasters stay valid while the popover seeks elsewhere.
        model = self.monitor()
        self._feed(model, "popover", width=600, height=400)
        self._feed(model, "mini", width=90, height=90, compact=True)
        self._window(model, "mini", 3)
        mini = model._plate_surfaces["mini"]
        self._pump_rasters(model, "mini")
        self._window(model, "popover", 200)
        self._pump_rasters(model, "popover")
        self.assertEqual(mini.desired["current"], 3)
        self.assertTrue(all(wrapped.rasterValid for wrapped in mini.layers.values()),
                        "the popover's seek disturbed the mini's rasters")

    def test_the_current_layer_renders_before_the_ghosts(self):
        # A cold random anchor
        # lands current first, then each ghost exactly once — in
        # any order, but never before the current.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        landed = {}

        def on_ready(layer, wrapped):
            # The commit's three setters each notify; only the FIRST
            # validity (the raster's own landing) marks the order.
            if wrapped.rasterValid and layer not in landed:
                landed[layer] = True
        for layer in (4, 5, 6):
            surface.layers[layer].rasterReady.connect(
                lambda l=layer: on_ready(l, surface.layers[l]))
        self._pump_rasters(model, "popover")
        self.assertEqual(list(landed)[0], 5, "a ghost rendered before the current layer")
        self.assertEqual(set(landed), {4, 5, 6},
                         "a ghost never rendered, or rendered twice")

    def test_a_quiet_publish_never_reenqueues_a_pending_ghost(self):
        # The duplicate-render repro: repeated
        # publishes while a ghost is pending must not enqueue it
        # again — and once everything is hot, nothing re-renders.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        for _ in range(5):
            self._window(model, "popover", 5)  # the publish churn
        self._pump_rasters(model, "popover")
        for layer in (4, 5, 6):
            self.assertEqual(surface.render_count[layer], 1,
                             "layer %d rendered more than once" % layer)

    def test_a_rapid_drag_schedules_bounded_work(self):
        # : a rapid 100..104 drag while 100's
        # job is in flight starts at most the final current plus its
        # ghosts — the obsolete visited layers never rasterise.
        model = self.monitor()
        surface = model._plate_surfaces["popover"]
        self._feed(model, "popover", width=400, height=300)
        payload = self._dense(200000)
        self._window(model, "popover", 100, payload)
        self.assertEqual(surface.render_count.get(100), 1)
        for anchor in (101, 102, 103, 104):
            self._window(model, "popover", anchor, payload)
        self.assertIsNotNone(surface.job, "the drag started a second job mid-flight")
        for anchor in (101, 102, 103):
            self.assertNotIn(anchor, surface.render_count,
                             "an obsolete visited layer started rendering")
        self._pump_rasters(model, "popover")
        self.assertEqual(surface.render_count.get(100), 1)
        for anchor in (101, 102):
            self.assertNotIn(anchor, surface.render_count,
                             "an obsolete visited layer rendered after the settle")
        self.assertIn(104, surface.render_count)
        # 103 rides the final anchor's own window as its previous
        # ghost — exactly once, like 105.
        self.assertEqual(surface.render_count.get(103), 1)
        self.assertLessEqual(surface.stats["started"], 4,
                             "the drag started unbounded raster jobs")

    def test_an_identical_context_publication_is_a_no_op(self):
        # : the exact same view/plot pair — a
        # settle's repeat feed — bumps nothing, invalidates nothing,
        # requests nothing.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        self._pump_rasters(model, "popover")
        generation = surface.generation
        counts = dict(surface.render_count)
        model.setFollowerView("popover", 1.0, 0.7, 400, 300, False, 0.0, 0.0)
        model.setFollowerPlot("popover", 0.0, 0.0, 1.0, 1.0, 0.0, 300.0)
        self.qt.events(5)
        self.assertEqual(surface.generation, generation)
        self.assertEqual(surface.render_count, counts)
        self.assertTrue(all(wrapped.rasterValid for wrapped in surface.layers.values()))

    def test_a_plot_plus_view_transition_is_one_generation(self):
        # : the QML's plot+view pair (the
        # onPlotChanged handler) coalesces into ONE generation and
        # one wave — never plot-generation-then-view-generation.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        generation = surface.generation
        model.setFollowerPlot("popover", 5.0, 6.0, 1.0, 1.0, 0.0, 300.0)
        model.setFollowerView("popover", 1.2, 0.7, 400, 300, False, 0.0, 0.0)
        self.qt.events(5)
        self.assertEqual(surface.generation, generation + 1,
                         "the plot+view pair flushed more than one generation")

    def test_the_scheduler_bookkeeping_stays_bounded(self):
        # : seeking through hundreds of
        # layers leaves no pending tokens, no job residue, and the
        # render counts stay bounded by the final window.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload()
        for anchor in range(300):
            self._window(model, "popover", anchor, payload)
        self._pump_rasters(model, "popover")
        self.assertEqual(surface.tokens, {}, "stale tokens survived the seek")
        self.assertIsNone(surface.job)
        self.assertLessEqual(len(surface.render_count), 4,
                             "obsolete layers burned raster work")
        self.assertLessEqual(surface.stats["depth_max"], 3)

    def test_an_evicted_layer_leaves_no_scheduler_residue(self):
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload()
        self._window(model, "popover", 0, payload)
        self._pump_rasters(model, "popover")
        for layer in range(6, 12):
            model._qt_layer(surface, payload, layer)
        self.assertNotIn(0, surface.layers)
        self.assertNotIn(0, surface.tokens,
                         "the evicted layer's token survived")

    def test_stale_split_completions_never_land_across_a_rapid_scrub(self):
        # The rapid scrub 80-60-40-55-30-70: each completion races
        # its split change and arrives as the surface's EXACT job —
        # the commit-time split check decides per DIRECTION. A
        # completion BEYOND the standing demand (a backward move)
        # discards and reschedules; a completion at or BEHIND it (a
        # forward move) LANDS — a prefix at P still owns [0..P] of
        # the newer demand (the review's scrub policy).
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        wrapped = model._qt_layer(surface, payload, 5)
        generation = surface.generation
        epoch = surface.job_epoch
        key = surface.render_key()
        from PyQt6.QtGui import QImage
        prefix = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
        # (completed split, standing demand, backward?) — the landed
        # record each step must show.
        steps = [(80, 60, True, -1),
                 (60, 40, True, -1),
                 (40, 55, False, 40),
                 (55, 30, True, 40),
                 (30, 70, False, 30)]
        for index, (split, next_split, backward, landed) in enumerate(steps):
            surface.tokens[5] = 1
            surface.job = {"layer": 5, "token": 1, "generation": generation,
                           "state": "submitted", "cancel": harness.threading.Event(),
                           "epoch": epoch, "serial": 100 + index,
                           "kind": "prefix", "split": split}
            surface.desired = {"current": 5, "ghosts": {},
                               "epoch": surface.anchor_epoch, "split": split}
            # ...races the next scrub tick before its completion arrives.
            surface.desired = {"current": 5, "ghosts": {},
                               "epoch": surface.anchor_epoch, "split": next_split}
            ticket = ("popover", 5, 1, generation, key, "prefix", split,
                      epoch, 100 + index)
            model._raster_committed(("prefix", prefix, "", split), ticket)
            self.assertEqual(wrapped.prefixSplit, landed,
                             "step %d landed the wrong split record" % index)
            if backward:
                self.assertIsNotNone(surface.job,
                                     "the stale discard never rescheduled the demand")
                self.assertEqual(surface.job["split"], next_split,
                                 "the reschedule followed the stale split, "
                                 "not the standing demand")
        self._pump_rasters(model, "popover")
        # The pump's own publishes may re-feed the standing demand and
        # land a fresh prefix; the record must never regress below the
        # last accepted forward prefix.
        self.assertGreaterEqual(wrapped.prefixSplit, 30,
                                "the accepted forward prefix regressed")

    def test_a_stale_nav_cancellation_never_clears_the_new_job(self):
        # The review's finding: a superseded job's late terminal
        # cleared the slot a NEWER job owned (the epoch matched).
        # The serial gates every terminal path.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        surface.nav["job"] = {"key": ("k",), "cancel": None,
                              "epoch": surface.job_epoch, "serial": 7}
        with harness.patch.object(model, "_schedule_navigation") as schedule:
            model._nav_committed(("cancelled",),
                                 ("popover", -1, 0, 0, ("k",), "nav", 50,
                                  surface.job_epoch, 6))
        self.assertIsNotNone(surface.nav["job"],
                             "a stale cancellation cleared the newer job's slot")
        self.assertEqual(surface.nav["job"]["serial"], 7)
        self.assertEqual(schedule.call_count, 0,
                         "a stale terminal rescheduled over the live job")

    def test_the_active_nav_cancellation_clears_and_reschedules(self):
        # The active job's own terminal clears its slot and re-arms
        # an outstanding demand.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        surface.nav["job"] = {"key": ("k",), "cancel": None,
                              "epoch": surface.job_epoch, "serial": 7}
        with harness.patch.object(model, "_schedule_navigation") as schedule:
            model._nav_committed(("cancelled",),
                                 ("popover", -1, 0, 0, ("k",), "nav", 50,
                                  surface.job_epoch, 7))
        self.assertIsNone(surface.nav["job"],
                          "the active job's cancellation kept its slot")
        self.assertEqual(schedule.call_count, 1,
                         "the cancelled active job never re-armed its demand")

    def test_a_stale_nav_success_never_promotes_over_the_new_job(self):
        # Two jobs can share a KEY (a zoom away and back): the serial
        # distinguishes them — a same-key stale success must not
        # promote over the newer job.
        from PyQt6.QtGui import QImage
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        ready = surface.nav["url"]
        shared_key = ("k",)
        surface.nav["job"] = {"key": shared_key, "cancel": None,
                              "epoch": surface.job_epoch, "serial": 7}
        model._nav_committed(
            ("nav", QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied),
             "file:///tmp/mpf/raster-probe/stale-serial.png", shared_key),
            ("popover", -1, 0, 0, shared_key, "nav", 50,
             surface.job_epoch, 6))
        self.assertEqual(surface.nav["url"], ready,
                         "a same-key stale success promoted over the newer job")

    def test_the_ready_url_publishes_only_for_the_current_demand(self):
        # A retained raster is not a READY one: the published URL
        # retires the moment the demand moves, and only a raster
        # whose key matches the CURRENT demand reaches the face.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # Detached: the attached compatible-raster gate would keep the
        # split-stale raster eligible.
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertEqual(model._navigation_data_value(surface), surface.nav["url"],
                         "the ready raster never published")
        # Detached now the anchor exists: the attached compatible
        # gate would keep the split-stale raster eligible.
        model.setFollowerAttached(False)
        # The demand moves: the retained URL retires from the face
        # before the replacement commits (no events — the old key
        # still stands, the demand is already the new one).
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 120)
        self.assertEqual(model._navigation_data_value(surface), "",
                         "the stale raster stayed eligible after the demand moved")
        # The replacement commits: its own key publishes.
        old_url = surface.nav["url"]
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and surface.nav["url"] == old_url:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertEqual(model._navigation_data_value(surface), surface.nav["url"],
                         "the replacement raster never became eligible")

    def test_a_stale_completion_cannot_touch_the_new_job(self):
        # : an old generation's worker result
        # arriving after a job switch is discarded, never committed.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        ticket = ("popover", 5, 1, surface.generation, surface.render_key(),
                  "full", None, surface.job_epoch, 1)
        model._observe_follower_job("new-job")
        discarded = surface.stats["discarded"]
        from PyQt6.QtGui import QImage
        blank = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
        model._raster_committed(("full", blank, "", blank, "", blank, ""), ticket)
        self.assertEqual(surface.layers, {})
        self.assertEqual(surface.stats["discarded"], discarded + 1)

    def test_a_stale_completion_never_clears_an_unrelated_submitted_job(self):
        # The exact-match rule: an old ticket's completion must not
        # touch a NEWER submitted job — the old ticket simply
        # discards, and the submitted job survives to run.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._dense(200000)
        self._window(model, "popover", 100, payload)
        submitted = dict(surface.job)
        # A stale ticket from an older demand arrives while the
        # 100-job is submitted (its worker has not started).
        stale = ("popover", 100, surface.job["token"] - 1, surface.generation,
                 surface.render_key(), "full", None, surface.job_epoch, 0)
        from PyQt6.QtGui import QImage
        blank = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
        model._raster_committed(("full", blank, "", blank, "", blank, ""), stale)
        self.assertIsNotNone(surface.job,
                             "the stale completion cleared the submitted job")
        self.assertEqual(surface.job["token"], submitted["token"],
                         "the submitted job's token changed")
        self.assertEqual(surface.job["layer"], submitted["layer"])
        self._pump_rasters(model, "popover")

    def test_retire_reopen_same_layer_cannot_collide_on_a_reused_token(self):
        # Exact reproduction of the serial collision: retire clears the
        # token map, so a reopened same-layer request can be token 1 in
        # the same generation/epoch. Only serial distinguishes it.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload()
        wrapped = model._qt_layer(surface, payload, 5)
        generation = surface.generation
        epoch = surface.job_epoch
        key = surface.render_key()

        surface.tokens[5] = 1
        surface.job = {"layer": 5, "token": 1, "generation": generation,
                       "state": "running", "cancel": harness.threading.Event(),
                       "epoch": epoch, "serial": 10}
        old_ticket = ("popover", 5, 1, generation, key, "full", None, epoch, 10)
        model._retire_surface(surface)

        surface.tokens[5] = 1
        surface.job = {"layer": 5, "token": 1, "generation": generation,
                       "state": "submitted", "cancel": harness.threading.Event(),
                       "epoch": epoch, "serial": 11}
        from PyQt6.QtGui import QImage
        blank = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
        discarded = surface.stats["discarded"]
        model._raster_committed(("full", blank, "", blank, "", blank, ""), old_ticket)

        self.assertIsNotNone(surface.job,
                             "old serial cleared the reopened job")
        self.assertEqual(surface.job["serial"], 11)
        self.assertEqual(surface.tokens.get(5), 1,
                         "old serial consumed the reopened token")
        self.assertFalse(wrapped.rasterValid,
                         "old serial committed pixels into the reopened layer")
        self.assertEqual(surface.stats["discarded"], discarded + 1)

        # Terminal results from the retired serial are stale too. In
        # particular, a stale failure arriving when the new job already
        # has four failures must not trip its persistent-failure latch.
        surface.desired = {"current": 5, "ghosts": {}, "split": None}
        surface.job_failures = 4
        failed = surface.stats["failed"]
        cancelled = surface.stats["cancelled"]
        discarded = surface.stats["discarded"]
        model._raster_committed(("failed", "old worker"), old_ticket)
        self.assertIsNotNone(surface.job)
        self.assertEqual(surface.job["serial"], 11)
        self.assertEqual(surface.job_failures, 4)
        self.assertIsNotNone(surface.desired,
                             "stale failure retired the reopened demand")
        self.assertEqual(surface.stats["failed"], failed)
        self.assertEqual(surface.stats["discarded"], discarded + 1)

        discarded = surface.stats["discarded"]
        model._raster_committed(("cancelled",), old_ticket)
        self.assertIsNotNone(surface.job)
        self.assertEqual(surface.job["serial"], 11)
        self.assertEqual(surface.stats["cancelled"], cancelled)
        self.assertEqual(surface.stats["discarded"], discarded + 1)

    def test_a_worker_exception_never_wedges_the_scheduler(self):
        # A throwing render ends as a terminal failure: the job slot
        # clears, the failure counts, and the next demand renders.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        module = self.qt.load("MoonrakerMonitorModel")

        def explode(*args, **kwargs):
            raise RuntimeError("injected render failure")
        with harness.patch.object(module, "render_layer_raster", explode):
            self._window(model, "popover", 5)
            # The arrival is the RETIREMENT, not a turn count. Every
            # failed render reschedules in the same call that clears
            # the slot, so the slot is empty only between two
            # statements of that call — the one empty slot a pump can
            # see is the persistent-failure retirement, after all five
            # rounds. A turn is not a unit of time either: the pump's
            # 5 ms timer may already have expired, and sixty of them
            # burn in one scheduling slice while the worker waits for a
            # thread.
            deadline = harness.time.monotonic() + 30.0
            while surface.job is not None or surface.desired is not None:
                self.qt.events(5)
                if harness.time.monotonic() >= deadline:
                    self.fail("the failed worker wedged the surface: %r"
                              % (surface.job,))
        self.assertIsNone(surface.job, "the failed worker wedged the surface")
        self.assertGreaterEqual(surface.stats["failed"], 1)
        # The persistent failures retired the demand; a fresh seek
        # re-arms the latch and renders.
        self._window(model, "popover", 5)
        self._pump_rasters(model, "popover")
        self.assertTrue(surface.layers[5].rasterValid)

    def test_a_layer_payload_is_immutable_within_a_print_epoch(self):
        # H's audit: within one print epoch a layer's payload is
        # content-addressed and immutable — the decoded LRU reuses
        # ONE object per layer, and the repair/hydration paths
        # regenerate layers only when the print itself changed. The
        # wrapper's identity is therefore (surface, layer, epoch),
        # never the payload's object: a content-equivalent
        # replacement (a re-decode) reuses the wrapper without
        # re-rendering, and the epoch boundary retires the wrappers
        # wholesale — no payload can ever be swapped under a live
        # wrapper.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload()
        wrapped = model._qt_layer(surface, payload, 5)
        # A content-equivalent replacement keeps the wrapper — the
        # layer's geometry is immutable per print, and the quiet
        # republish never re-renders (the quiet-publish and revisit
        # pins beside this one).
        refreshed = self._payload()
        self.assertIs(model._qt_layer(surface, refreshed, 5), wrapped,
                      "a content-equivalent payload re-wrapped the layer")
        # The print boundary retires the wrappers wholesale — the
        # epoch, not the payload, owns the identity.
        model._observe_follower_job("other-job")
        self.assertNotIn(5, surface.layers,
                         "the print change left the old epoch's wrapper")

    def test_the_demanded_windows_survive_the_decoded_trim(self):
        # Both demanded windows — the live print's ±1 and the
        # detached follower's frozen ±1 — must survive the decoded
        # budget's eviction: evicting a demanded layer flips the
        # memoised bundle's decoded bit and the demand re-decodes
        # it, a per-poll eviction/redemption thrash (the detached
        # 114% burn).
        model = self.monitor()
        service = model._index_service
        service._decoded_lru.max_bytes = 10 ** 9
        for layer in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
            service._decoded_lru.set(layer, self._payload(), 5000)
            service._decoded_sizes[layer] = 5000
        service._decoded_lru.min_entries = 1
        service._decoded_lru.max_bytes = 1000
        # The wiring: both anchors hand their windows to the
        # protection (a minimal view stands in — this fixture's
        # service never builds a real one).
        from types import SimpleNamespace
        from threading import Lock
        service._view = SimpleNamespace(_index=SimpleNamespace(manual_anchor=None,
                                                               followed_layer=None,
                                                               cache_lock=Lock()),
                                        ranges=list(range(20)),
                                        hydrated=lambda layer: False,
                                        pause_layers=set())
        # The worker machinery stays out of this scope: the test pins
        # the trim's protection, not the demand loop.
        service._request_window = lambda layer: None
        service._advance = lambda: None
        service.set_followed_layer(10)
        self.assertEqual(service._decoded_lru.protected, {9, 10, 11},
                         "the live window never entered the protection")
        service._manual_anchor = 5
        service._apply_manual_anchor()
        self.assertEqual(service._decoded_lru.protected, {4, 5, 6, 9, 10, 11},
                         "the frozen window never joined the protection")
        service._decoded_lru.set(13, self._payload(), 5000)  # the trim fires
        for layer in (4, 5, 6, 9, 10, 11):
            self.assertIn(layer, service._decoded_lru,
                          "a demanded layer %d evicted" % layer)
        self.assertNotIn(1, service._decoded_lru,
                         "an undemanded layer survived the trim")
        service._manual_anchor = None
        service._apply_manual_anchor()
        self.assertEqual(service._decoded_lru.protected, {9, 10, 11},
                         "re-attaching kept the frozen window protected")

    def test_the_same_manual_anchor_is_a_no_op(self):
        # The coordinator re-asserts the SAME anchor on every
        # refresh while detached; without the no-op the rewind, the
        # demand and the advance ran per refresh and the worker's
        # own completion fed the loop back through changed ->
        # refresh (the detached 114% burn). The rewind and the
        # advance stay suppressed; the DEMAND stands, because a
        # window whose request was dropped while the worker was busy
        # never came back otherwise (the label stood on it).
        service = self.follower._runtime.index
        service._manual_anchor_calls = 0
        service._manual_anchor_changes = 0
        advances = []
        windows = []
        original_advance = service._advance
        original_window = service._request_manual_window
        service._advance = lambda: advances.append(1) or original_advance()
        service._request_manual_window = lambda: windows.append(1) or original_window()
        try:
            service.set_manual_anchor(5)
            first = (service._full_next, len(advances), len(windows))
            service.set_manual_anchor(5)
            service.set_manual_anchor(5)
            self.assertEqual((service._full_next, len(advances)), first[:2],
                             "the same anchor re-ran the seek focus or the advance")
            self.assertGreater(len(windows), first[2],
                               "the re-assert did not keep the demand standing")
            self.assertEqual(service._manual_anchor_calls, 3,
                             "the idempotency counters missed calls")
            self.assertEqual(service._manual_anchor_changes, 1,
                             "the same anchor counted as a change")
        finally:
            service._advance = original_advance
            service._request_manual_window = original_window

    def test_the_closed_popover_stops_the_frozen_serving(self):
        # The explicit demand gate: the manual anchor stays as
        # lightweight state, but a closed popover serves no frozen
        # window; reopening re-arms the gate and the next poll's
        # refresh resumes the serving.
        model = self.monitor()
        coordinator = self.follower._runtime.coordinator
        self.assertFalse(coordinator._popover_open,
                         "the popover starts open")
        model.setFollowerPopoverOpen(True)
        self.assertTrue(coordinator._popover_open,
                        "the open never reached the coordinator")
        self.follower.setPlateAnchor(5)
        self.assertTrue(coordinator._manual_serving_active(),
                        "the open popover does not serve the frozen window")
        model.setFollowerPopoverOpen(False)
        self.assertFalse(coordinator._popover_open)
        self.assertEqual(coordinator._plate_anchor, 5,
                         "closing dropped the manual anchor")
        self.assertFalse(coordinator._manual_serving_active(),
                         "the closed popover keeps serving the frozen window")
        # The reopen re-arms the gate itself; the serving resumes on
        # the next refresh (the per-poll cadence — no immediate
        # recompute that would throw away the standing snapshot).
        model.setFollowerPopoverOpen(True)
        self.assertTrue(coordinator._manual_serving_active(),
                        "the reopen never re-armed the frozen serving")

    def test_a_real_anchor_change_still_focuses_once(self):
        # The idempotency must not swallow real changes: each new
        # anchor focuses the preparation exactly once.
        service = self.follower._runtime.index
        service._manual_anchor_changes = 0
        self.follower.setPlateAnchor(100)
        self.follower.setPlateAnchor(250)
        self.assertEqual(service._manual_anchor_changes, 2,
                         "the real anchor changes did not both land")

    def test_a_discarded_completion_releases_its_images(self):
        # The discard path keeps NO image: after the handler returns,
        # nothing but the (dead) tuple ever referenced the rendered
        # pixels — a superseded job's QImages free with it.
        import gc
        import weakref

        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        surface.desired = None  # the discard's re-schedule finds nothing
        if surface.job is not None:
            surface.job["cancel"].set()
            surface.job = None
        module = self.qt.load("MoonrakerMonitorModel")
        payload = self._payload()
        payload["travels"] = [[[0.0, 0.0, 0.0], [10.0, 10.0, 1.0]]]
        coloured, base, travels = module.render_layer_raster(
            payload, surface.plot, surface.view)
        refs = [weakref.ref(image) for image in (coloured, base, travels)]
        stale = ("popover", 5, "stale-token", surface.generation,
                 surface.render_key(), "full", None, surface.job_epoch, 0)
        model._raster_committed(("full", coloured, "", base, "", travels, ""), stale)
        self.assertGreaterEqual(surface.stats["discarded"], 1)
        del coloured, base, travels
        gc.collect()
        self.assertEqual([ref() for ref in refs], [None] * 3,
                         "a discarded completion's image survived")
        # The cancelled worker's terminal commit delivers here, not
        # on a deleted model after the teardown.
        for _ in range(200):
            self.qt.events(5)
            if surface.job is None:
                break

    def test_a_queued_obsolete_job_cancels_before_its_render(self):
        # A submitted (not yet running) job whose layer left the
        # window gets the cancel flag: the worker's pre-render
        # check stops it before any QPainter work.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._dense(500000)
        self._window(model, "popover", 100, payload)
        self.assertEqual(surface.job["state"], "submitted")
        self._window(model, "popover", 300, payload)
        self.assertTrue(surface.job["cancel"].is_set(),
                        "the obsolete queued job never got its cancel flag")
        self._pump_rasters(model, "popover")
        self.assertTrue(surface.layers[300].rasterValid)

    def test_closing_the_popover_retires_its_demand(self):
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        model.setFollowerPopoverOpen(True)
        payload = self._dense(500000)
        self._window(model, "popover", 100, payload)
        self.assertIsNotNone(surface.job)
        model.setFollowerPopoverOpen(False)
        self.assertIsNone(surface.desired,
                          "the closed popover kept its demand")
        self.assertIsNone(surface.job)
        self.assertEqual(surface.tokens, {})
        # Reopening rebuilds the demand from the next payload.
        model.setFollowerPopoverOpen(True)
        self._window(model, "popover", 100, payload)
        self._pump_rasters(model, "popover")
        self.assertTrue(surface.layers[100].rasterValid)

    def test_a_staged_ba_reversal_commits_a(self):
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        generation = surface.generation
        model.setFollowerView("popover", 2.0, 0.7, 400, 300, False, 0.0, 0.0)
        model.setFollowerView("popover", 1.0, 0.7, 400, 300, False, 0.0, 0.0)
        self.qt.events(5)
        self.assertEqual(surface.generation, generation,
                         "the B->A reversal committed the intermediate")
        self.assertEqual(surface.view["scale"], 1.0)

    def test_a_staged_abc_burst_commits_c_once(self):
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        generation = surface.generation
        model.setFollowerView("popover", 1.2, 0.7, 563, 492, False, 0.0, 0.0)
        model.setFollowerView("popover", 1.5, 0.7, 563, 492, False, 0.0, 0.0)
        model.setFollowerView("popover", 1.8, 0.7, 563, 492, False, 0.0, 0.0)
        self.qt.events(5)
        self.assertEqual(surface.generation, generation + 1,
                         "the burst flushed more than one generation")
        self.assertEqual(surface.view["scale"], 1.8)

    def test_a_backward_seek_restores_a_checkpoint_without_another_native_job(self):
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        model.setFollowerLayerAnchor(5)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        urls = {}
        for split in (50, 160):
            model._qt_window(surface, {"prev": None, "current": payload,
                                       "next": None}, 5, "motion index", split)
            self._pump_rasters(model, "popover")
            urls[split] = surface.layers[5].prefixData
        committed = surface.stats["committed"]
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None}, 5, "motion index", 80)
        wrapped = surface.layers[5]
        self.assertEqual(wrapped.prefixSplit, 50)
        self.assertEqual(wrapped.prefixData, urls[50])
        self.assertTrue(wrapped.prefixValid)
        self._pump_rasters(model, "popover")
        self.assertEqual(surface.stats["committed"], committed,
                         "a cached backward seek submitted another exact raster")

    def test_the_print_epoch_blocks_a_colliding_stale_completion(self):
        # The print switch recreates layer 100 with token 1 at
        # generation 4 — the old print's identical ticket must be
        # mathematically incapable of committing.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(200)
        self._window(model, "popover", 100, payload)
        old_epoch = surface.job_epoch
        old_generation = surface.generation
        model._observe_follower_job("new-job")
        self.assertEqual(surface.job_epoch, old_epoch + 1,
                         "the print switch never bumped the epoch")
        # The new print recreates the SAME layer/token/generation
        # numbers (the collision case).
        surface.generation = old_generation
        self._window(model, "popover", 100, payload)
        ticket = ("popover", 100, 1, old_generation, surface.render_key(),
                  "full", None, old_epoch, 1)
        from PyQt6.QtGui import QImage
        blank = QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied)
        committed_before = surface.stats["committed"]
        model._raster_committed(("full", blank, "", blank, "", blank, ""), ticket)
        self.assertEqual(surface.stats["committed"], committed_before,
                         "the old print's raster committed into the new print")
        self.assertFalse(surface.layers[100].rasterValid)


