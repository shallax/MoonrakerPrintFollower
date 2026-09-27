"""Executable follower raster scheduler contracts."""
from tests import composed_runtime_support as harness

class NativeRenderSchedulerTests(harness.NativeRenderSchedulerTests):
    def test_hot_revisits_and_reverses_are_raster_hits(self):
        # The warm-seek matrix: N -> N+1 renders only
        # N+2; N+1 -> N and A -> B -> A are pure raster hits.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        self._pump_rasters(model, "popover")
        counts = dict(surface.render_count)
        self._window(model, "popover", 6)
        self._pump_rasters(model, "popover")
        self.assertEqual(surface.render_count.get(5), counts.get(5),
                         "the adjacent window re-rendered N")
        self.assertEqual(surface.render_count.get(6), counts.get(6),
                         "the adjacent window re-rendered N+1")
        self.assertEqual(surface.render_count.get(7), 1)
        counts = dict(surface.render_count)
        self._window(model, "popover", 5)
        self._pump_rasters(model, "popover")
        self.assertEqual(surface.render_count, counts,
                         "the reverse N+1 -> N was not a raster hit")

    def test_a_dpr_view_backs_the_rasters_at_device_resolution(self):
        # D: a DPR-2 surface's rasters are painted at the DEVICE
        # resolution — the scene-graph samples them down to the
        # logical face; a DPR-2 screen must never enlarge a 1x
        # logical toolpath raster. The backing rides the view, the
        # render key, and the full/prefix dimensions alike.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model.setFollowerView("popover", 1.0, 0.7, 400, 300, False, 0.0, 0.0, 2.0)
        self.qt.events(5)
        self.assertEqual(surface.view["dpr"], 2.0,
                         "the backing scale never reached the surface's view")
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        self.assertEqual(wrapped.rasterWidth, 800,
                         "the DPR-2 raster is not the device width")
        self.assertEqual(wrapped.rasterHeight, 600,
                         "the DPR-2 raster is not the device height")
        self.assertEqual(wrapped.prefixWidth, 800,
                         "the DPR-2 prefix is not the device width")
        self.assertEqual(model.memory_accounting()["backingScale"], 2.0,
                         "the accounting never reported the backing scale")

    def test_the_navigation_raster_is_warm_and_camera_independent(self):
        # The interaction scene: the popover maintains a READY
        # flattened full-bed raster keyed on CONTENT alone — a
        # zoom/pan change (pure presentation) leaves the key and
        # the URL untouched, a split change schedules the update,
        # and the promoted buffer retires its predecessor's file.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        from PyQt6.QtCore import QUrl
        self.assertTrue(surface.nav["url"], "the navigation raster never warmed")
        self.assertTrue(harness.os.path.exists(
            QUrl(surface.nav["url"]).toLocalFile()))
        nav_key = surface.nav["key"]
        nav_url = surface.nav["url"]
        # A pure camera change (zoom + pan): the content key and the
        # ready URL stay — panning/zooming never re-renders the
        # interaction scene.
        model.setFollowerView("popover", 1.5, 0.7, 400, 300, False, 12.0, -8.0)
        self.qt.events(5)
        self.assertEqual(surface.nav["key"], nav_key,
                         "the camera changed the navigation key")
        self.assertEqual(surface.nav["url"], nav_url,
                         "the camera regenerated the navigation raster")
        # A CONTENT change (the split): the background update
        # promotes a new URL. The old file enters bounded retirement;
        # presentation can still hold it independently of the wrapper.
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 120)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and surface.nav["url"] == nav_url:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertNotEqual(surface.nav["url"], nav_url,
                            "the split change never updated the navigation raster")
        model._prune_raster_cache(keep=0)
        self.assertFalse(harness.os.path.exists(QUrl(nav_url).toLocalFile()),
                         "the retired navigation buffer kept its file")
        # The legend checkboxes and the line width are the scene's
        # CONTENT: each flip regenerates the warm raster (its key
        # carries them) — the interaction scene must mirror the
        # exact view's toggles and stroke. The toggle changes ride
        # the publish, which serves the OPEN popover's surface.
        model.setFollowerPopoverOpen(True)
        nav_url = surface.nav["url"]
        for setter, name in ((lambda: model.setFollowerShowPrevious(False),
                              "showPrevious"),
                             (lambda: model.setFollowerView(
                                 "popover", 1.0, 12.0, 400, 300, False,
                                 0.0, 0.0),  # the face's settled feed
                              "lineScale")):
            setter()
            model._publish()  # the poll's publish (the harness has no tick)
            self._pump_rasters(model, "popover")
            deadline = harness.time.monotonic() + 5.0
            while harness.time.monotonic() < deadline and surface.nav["url"] == nav_url:
                self.qt.events(5)
                harness.time.sleep(0.01)
            self.assertNotEqual(surface.nav["url"], nav_url,
                                "the %s change never updated the "
                                "navigation raster" % name)
            nav_url = surface.nav["url"]

    def test_a_stale_navigation_generation_never_promotes(self):
        # The double buffer's gates: a nav completion whose epoch or
        # key no longer matches the pending job dies on arrival —
        # its file unlinks and the ready URL stands.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        ready = surface.nav["url"]
        # A stale ticket from a previous epoch arrives late.
        from PyQt6.QtGui import QImage
        stale_key = surface.nav["key"]
        stale_ticket = ("popover", -1, 0, 0, stale_key, "nav", 50,
                        surface.job_epoch - 1, 99)
        model._nav_committed(("nav", QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied),
                              "file:///tmp/mpf/raster-probe/stale-nav.png", stale_key),
                             stale_ticket)
        self.assertEqual(surface.nav["url"], ready,
                         "a stale epoch's navigation raster promoted")
        # A key mismatch (the demand moved on) discards too.
        model._nav_committed(("nav", QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied),
                              "file:///tmp/mpf/raster-probe/other-nav.png",
                              ("other-key",)), ("popover", -1, 0, 0,
                                                ("other-key",), "nav", 50,
                                                surface.job_epoch, 100))
        self.assertEqual(surface.nav["url"], ready,
                         "a stale key's navigation raster promoted")

    def test_a_failed_navigation_render_never_hot_retries_the_same_demand(self):
        # A persistently failing warm render must not retry forever:
        # every publish re-arms the demand, and the failure terminal
        # re-armed it AGAIN — the identical failing key looped at
        # render cost. The failed key latches until the demand moves.
        # The contract is pinned on SUBMISSIONS — counted on the
        # owner thread — never on the shared pool's scheduling (the
        # coverage tracer's pool starvation would flake the latter).
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # The strict-demand scenarios run DETACHED: the follower now
        # starts attached (the live-follow default), whose compatible
        # gate and window throttle would mask the exact-match policy
        # these tests pin.
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        module = self.qt.load("MoonrakerMonitorModel")
        submitted = []
        real_job = module._RasterJob

        class CountingJob(real_job):
            def __init__(self, work):
                super().__init__(work)
                # The exact-scene scheduler shares this job class;
                # only the nav build emits a "nav" payload.
                if "nav" in work.__code__.co_consts:
                    submitted.append(work)

        def failing(*args, **kwargs):
            raise RuntimeError("injected navigation render failure")

        with harness.patch.object(module, "_RasterJob", CountingJob), \
                harness.patch.object(module, "render_navigation_layer", failing):
            model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                             5, "motion index", 50)
            model._publish()
            self.qt.events(5)
            self.assertEqual(len(submitted), 1,
                             "the warm raster never scheduled its render")
            # The worker's own terminal, delivered inline through the
            # bridge: the failing render ends the active job, and its
            # key latches.
            submitted[0]()
            self.qt.events(5)
            self.assertIsNone(surface.nav["job"],
                              "the failed terminal never cleared the job slot")
            # The publishes keep firing; the identical demand must
            # not resubmit after the failed terminal.
            for _ in range(5):
                model._publish()
                self.qt.events(5)
            self.assertEqual(len(submitted), 1,
                             "the identical failing demand hot-retried")
        # The demand moves (the split): the fresh key re-arms, and
        # with the render healthy again the warm raster recovers.
        # Detached now the anchor exists: the attached compatible
        # gate and window throttle would mask the exact-match policy.
        model.setFollowerAttached(False)
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 120)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertTrue(surface.nav["url"],
                        "the moved demand never painted the warm raster")

    def test_a_print_switch_retires_the_navigation_state(self):
        # The review's finding: the epoch gate rejected the old job's
        # terminal, but nothing freed the slot it no longer owned —
        # the new print could never schedule its warm raster. The
        # switch now retires the whole navigation state, and the old
        # job's late terminals stay inert.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # Detached: the follower starts attached, and the attached
        # throttle would defer the moved demand this test schedules.
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertTrue(surface.nav["url"], "the warm raster never landed")
        # Detached now the anchor exists: the attached throttle would
        # defer the moved demand this test schedules.
        model.setFollowerAttached(False)
        # A new demand schedules a second job; the print switches
        # while it is still in flight.
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 120)
        self.qt.events(5)
        self.assertIsNotNone(surface.nav["job"],
                             "the moved demand never scheduled its job")
        old_epoch = surface.job_epoch
        model._observe_follower_job(("other.gcode", 100, 1))
        model._plate_qt_job = None  # the harness snapshot still reports no job
        self.assertIsNone(surface.nav["job"], "the switch left the old job's slot")
        self.assertIsNone(surface.nav["key"], "the switch kept the old content key")
        self.assertEqual(surface.nav["url"], "", "the switch kept the old ready URL")
        self.assertIsNone(surface.nav.get("failed"),
                          "the switch kept the old failure latch")
        # The old job's late terminals stay inert whatever they carry.
        from PyQt6.QtGui import QImage
        model._nav_committed(("cancelled",),
                             ("popover", -1, 0, 0, ("stale",), "nav", 120,
                              old_epoch, 99))
        model._nav_committed(
            ("nav", QImage(4, 4, QImage.Format.Format_ARGB32_Premultiplied),
             "file:///tmp/mpf/raster-probe/old-print.png", ("stale",)),
            ("popover", -1, 0, 0, ("stale",), "nav", 120, old_epoch, 99))
        self.assertIsNone(surface.nav["job"])
        self.assertEqual(surface.nav["url"], "",
                         "an old print's terminal republished its raster")
        # The new print schedules its own warm raster as soon as its
        # data arrives — the slot is genuinely free.
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         3, "motion index", 10)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertTrue(surface.nav["url"],
                        "the new print never warmed its own raster")

    def test_the_navigation_asset_survives_the_prune_until_retired(self):
        from PyQt6.QtCore import QUrl
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            harness.time.sleep(0.01)
        nav_file = QUrl(surface.nav["url"]).toLocalFile()
        self.assertTrue(harness.os.path.exists(nav_file))
        # A prune that keeps NOTHING still keeps the retained asset.
        model._prune_raster_cache(keep=0)
        self.assertTrue(harness.os.path.exists(nav_file),
                        "the prune unlinked the retained navigation asset")
        # The retirement drops the protection; the next prune collects.
        model._retire_navigation(surface)
        model._prune_raster_cache(keep=0)
        self.assertFalse(harness.os.path.exists(nav_file),
                         "the retired navigation asset never got collected")

    def test_an_empty_navigation_publication_reads_as_a_failure(self):
        # png_file returns "" on a failed save: the worker must
        # report a FAILED render, the latch holds the demand (no
        # hot-retry), and no key ever reads as ready.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        module = self.qt.load("MoonrakerMonitorModel")
        submitted = []
        real_job = module._RasterJob

        class CountingJob(real_job):
            def __init__(self, work):
                super().__init__(work)
                if "nav" in work.__code__.co_consts:
                    submitted.append(work)

        with harness.patch.object(module, "_RasterJob", CountingJob), \
                harness.patch.object(module, "png_file", return_value=""):
            model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                             5, "motion index", 50)
            model._publish()
            self.qt.events(5)
            self.assertEqual(len(submitted), 1,
                             "the warm raster never scheduled its render")
            submitted[0]()  # the worker's own terminal, delivered inline
            self.qt.events(5)
            self.assertIsNone(surface.nav["job"],
                              "the failed terminal never cleared the job slot")
            self.assertEqual(surface.nav["url"], "",
                             "an empty publication recorded a ready raster")
            self.assertIsNone(surface.nav["key"],
                              "an empty publication recorded a successful key")
            self.assertIsNotNone(surface.nav.get("failed"),
                                 "the empty publication never latched")
            for _ in range(5):
                model._publish()
                self.qt.events(5)
            self.assertEqual(len(submitted), 1,
                             "the failed publication hot-retried")

    def test_an_obsolete_navigation_job_never_promotes_after_the_demand_moved(self):
        # The review's stale-promotion finding: a job whose content
        # matches its OWN ticket but not the surface's CURRENT demand
        # must not promote — the demand gate discards it, clears the
        # slot and schedules the current content.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # Detached: the attached compatible-raster gate would let the
        # obsolete split-only job promote.
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        # Detached now the anchor exists: the attached compatible
        # gate would let the obsolete split-only job promote.
        model.setFollowerAttached(False)
        key_a = model._navigation_key(surface)
        self.assertIsNotNone(key_a, "the first demand never keyed")
        # The demand moves BEFORE A completes: the slot is busy, so B
        # is deferred and A stays pending.
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 80)
        key_b = model._navigation_key(surface)
        self.assertNotEqual(key_b, key_a, "the demand never changed")
        self.assertIsNotNone(surface.nav["job"],
                             "the deferred demand lost its pending job")
        # A completes: its key matches its ticket, but the demand is
        # now B — the promotion is refused and B is scheduled.
        self._pump_rasters(model, "popover")
        self.assertNotEqual(surface.nav["key"], key_a,
                            "the obsolete job promoted over the moved demand")
        # B's own completion promotes normally.
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and surface.nav["key"] != key_b:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertEqual(surface.nav["key"], key_b,
                         "the current demand never promoted")
        # A→B→A: the demand returns to A before the stale job lands —
        # identity, not sequence: A's completion promotes because the
        # demand IS A again.
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 90)
        self._pump_rasters(model, "popover")
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline and surface.nav["key"] != key_a:
            self.qt.events(5)
            harness.time.sleep(0.01)
        self.assertEqual(surface.nav["key"], key_a,
                         "the re-demanded content never promoted")

    def test_wrapper_payloads_pin_the_decoded_budget(self):
        # The wrappers charge their payloads against the decoded
        # tier: pin on wrap (the second surface's wrapper adds its
        # own), unpin on print change, and the accounting view
        # reports the tiers' real residency.
        model = self.monitor()
        service = model._index_service
        service._decoded_lru.max_bytes = 1000
        for layer in (4, 5, 6):
            service._decoded_lru.set(layer, self._payload(), 5000)
            service._decoded_sizes[layer] = 5000
        self._feed(model, "popover")
        self._window(model, "popover", 5)
        popover = model._plate_surfaces["popover"]
        self.assertEqual(set(service._decoded_pins), set(popover.layers.keys()),
                         "the pins do not track the wrappers")
        self._feed(model, "mini", width=90, height=90)
        self._window(model, "mini", 5)
        self.assertEqual(service._decoded_pins[5], 2,
                         "the second surface's wrapper did not add its pin")
        accounting = model.memory_accounting()
        self.assertGreater(accounting["decodedBytes"], 0)
        self.assertGreater(accounting["pinnedDecodedBytes"], 0,
                           "the evicted wrappers' payloads were not charged")
        self.assertEqual(accounting["wrapperCount"], 6)
        # A print change clears the wrappers and their pins.
        model._observe_follower_job("new-job")
        self.assertEqual(service._decoded_pins, {})
        self.assertEqual(model.memory_accounting()["wrapperCount"], 0)
        # The cancelled workers deliver their terminal commits here —
        # a straggler's commit on a deleted model is the teardown
        # segfault.
        for _ in range(200):
            self.qt.events(5)
            if all(surface.job is None
                   for surface in model._plate_surfaces.values()):
                break

    def test_the_raster_prune_skips_live_references_and_temps(self):
        # The prune is reference-aware: a file a live wrapper still
        # displays always survives (the newest-N sweep used to be
        # able to unlink the picture on screen), and an in-flight
        # publication's temp is never touched.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        self._window(model, "popover", 5)
        directory = model._raster_cache_dir
        old = harness.os.path.join(directory, "old.png")
        live = harness.os.path.join(directory, "live.png")
        temp = harness.os.path.join(directory, "job.png.tmp-99")
        for path in (old, live, temp):
            with open(path, "wb") as handle:
                handle.write(b"x")
        from PyQt6.QtCore import QUrl
        model._plate_surfaces["popover"].layers[5]._raster_data = \
            QUrl.fromLocalFile(live).toString()
        model._prune_raster_cache(keep=0)
        self.assertFalse(harness.os.path.exists(old), "the unreferenced file survived")
        self.assertTrue(harness.os.path.exists(live), "the live reference was unlinked")
        self.assertTrue(harness.os.path.exists(temp), "the in-flight temp was unlinked")
        # Quiesce the submitted job before the teardown deletes the
        # model: a straggler's commit on a deleted model is the
        # teardown segfault.
        surface = model._plate_surfaces["popover"]
        if surface.job is not None:
            surface.job["cancel"].set()
        for _ in range(200):
            self.qt.events(5)
            if surface.job is None:
                break

    def test_the_raster_prune_keys_the_reference_and_the_scan_together(self):
        # The reference is a URL the wrapper displays and the scan is a
        # directory path: the two spell one file differently — '/' versus
        # the platform's own separator on Windows, a redundant segment
        # wherever — so the set is keyed rather than compared raw. Raw,
        # it matched nothing and the prune unlinked the live picture.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        self._window(model, "popover", 5)
        directory = model._raster_cache_dir
        sub = harness.os.path.join(directory, "sub")
        harness.os.makedirs(sub, exist_ok=True)
        live = harness.os.path.join(directory, "spelled-live.png")
        with open(live, "wb") as handle:
            handle.write(b"x")
        from PyQt6.QtCore import QUrl
        # The wrapper's own spelling of the file: QUrl keeps the '..'
        # through the round trip, which is the shape the Windows leg's
        # '/' spelling takes on this platform.
        spelled = harness.os.path.join(sub, "..", "spelled-live.png")
        model._plate_surfaces["popover"].layers[5]._raster_data = \
            QUrl.fromLocalFile(spelled).toString()
        model._prune_raster_cache(keep=0)
        self.assertTrue(harness.os.path.exists(live),
                        "the reference's own spelling did not protect the file")
        surface = model._plate_surfaces["popover"]
        if surface.job is not None:
            surface.job["cancel"].set()
        for _ in range(200):
            self.qt.events(5)
            if surface.job is None:
                break

    def test_two_models_own_independent_raster_directories(self):
        model_a = self.monitor()
        model_b = self.monitor()
        self.assertNotEqual(model_a._raster_cache_dir, model_b._raster_cache_dir)
        self._feed(model_a, "popover", width=400, height=300)
        payload = self._payload(100)
        a_surface = model_a._plate_surfaces["popover"]
        model_a._qt_window(a_surface, {"prev": None, "current": payload,
                                       "next": None}, 5, "motion index", None)
        self._pump_rasters(model_a, "popover")
        self.assertEqual(harness.os.listdir(model_b._raster_cache_dir), [],
                         "model A wrote into model B's directory")
        self.assertGreater(len(harness.os.listdir(model_a._raster_cache_dir)), 0)

    def test_the_raster_commit_runs_on_the_owner_thread(self):
        # : the worker hands the images through
        # the bridge, and the commit (and every rasterReady it
        # fires) runs on the model's thread.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        self._window(model, "popover", 5)
        from PyQt6.QtCore import QThread
        threads = []
        surface.layers[5].rasterReady.connect(
            lambda: threads.append(QThread.currentThread()))
        self._pump_rasters(model, "popover")
        self.assertTrue(threads, "the raster never landed")
        self.assertTrue(all(thread is QThread.currentThread() for thread in threads),
                        "a worker thread committed the raster")


