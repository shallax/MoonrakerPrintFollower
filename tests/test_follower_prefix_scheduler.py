"""Executable follower prefix scheduler contracts."""
from tests import composed_runtime_support as harness

class NativeRenderSchedulerTests(harness.NativeRenderSchedulerTests):
    def test_a_partial_layer_demands_its_prefix_before_the_full_layer(self):
        # The partial states' prefix: the printed portion renders
        # natively FIRST, so the QML walk never covers the whole
        # printed prefix — only the tail beyond it.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        self.assertEqual(wrapped.prefixSplit, 50,
                         "the prefix never landed at the split")
        self.assertGreater(wrapped.prefixWidth, 0)
        self.assertTrue(wrapped.rasterValid, "the full layer never followed")
        self.assertGreater(wrapped.baseWidth, 0, "the base never followed")

    def test_an_invalidated_prefix_is_requested_again_at_the_same_split(self):
        # Zoom/pan/resize changes the render key without changing the
        # printed boundary. The old prefix must be treated as absent:
        # QML falls back to the vector from motion zero while Python
        # immediately schedules a replacement.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        self.assertTrue(wrapped.prefixValid)
        before = surface.render_count.get(5, 0)

        model.setFollowerView("popover", 1.25, 0.7, 400, 300, False, 0.0, 0.0)
        self.qt.events(5)
        self.assertGreater(surface.render_count.get(5, 0), before,
                           "same-split invalidation never requested a new prefix")
        self._pump_rasters(model, "popover")
        self.assertTrue(wrapped.prefixValid)
        self.assertEqual(wrapped.prefixSplit, 50)

    def test_the_prefix_refreshes_past_the_quarter_and_backward(self):
        # A small live advance keeps the prefix; a quarter-layer
        # advance or a backward move demands a fresh one.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # The motion threshold is the DETACHED scrub's policy: the
        # follower now starts attached, whose checkpoint cadence would
        # swallow the quarter-layer advance this pins. The manual
        # seek is the detach (a bare detach is refused with no layer
        # to hold).
        model.setFollowerLayerAnchor(5)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 60)
        self.assertEqual(wrapped.prefixSplit, 50,
                         "a small advance re-rendered the prefix")
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 160)
        self._pump_rasters(model, "popover")
        self.assertEqual(wrapped.prefixSplit, 160,
                         "the quarter-layer advance never refreshed")
        model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                         5, "motion index", 40)
        self._pump_rasters(model, "popover")
        self.assertEqual(wrapped.prefixSplit, 40,
                         "the backward move never refreshed")

    def test_a_split_change_cancels_the_running_prefix_job(self):
        # A prefix render's identity includes its requested split:
        # moving the split under a RUNNING same-layer prefix must
        # cancel it at the renderer's next boundary — the stale
        # 80-picture may never supersede the new 60-demand.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        module = self.qt.load("MoonrakerMonitorModel")
        real_prefix = module.render_layer_prefix
        entered = harness.threading.Event()
        release = harness.threading.Event()
        calls = []

        def blocked_prefix(payload, plot, view, split, cancel=None,
                          previous=None, previous_split=0):
            calls.append(split)
            entered.set()
            release.wait(10)
            return real_prefix(payload, plot, view, split, cancel=cancel,
                               previous=previous, previous_split=previous_split)

        with harness.patch.object(module, "render_layer_prefix", blocked_prefix):
            model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                             5, "motion index", 80)
            self.assertTrue(entered.wait(10), "the prefix worker never started")
            self.assertEqual(surface.job["split"], 80,
                             "the running job never carried its split")
            model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                             5, "motion index", 60)
            self.assertTrue(surface.job["cancel"].is_set(),
                            "the same-layer split change never cancelled "
                            "the running prefix")
            release.set()
            self._pump_rasters(model, "popover")
        self.assertEqual(surface.layers[5].prefixSplit, 60,
                         "the stale split's prefix survived the cancel")
        self.assertEqual(calls, [80, 60],
                         "the superseded split burned a fresh render")
        self.assertGreaterEqual(surface.stats["cancelled"], 1,
                                "the superseded prefix never recorded its cancel")

    def test_a_prefix_cancels_when_its_layer_slides_to_a_ghost(self):
        # A-B-A: layer 5's blocked prefix slides to the prev ghost
        # when the anchor moves to 6 — a ghost wants a full layer,
        # never a prefix, so the in-flight 80-render must cancel and
        # the revisit at 55 starts from a clean wrapper.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        module = self.qt.load("MoonrakerMonitorModel")
        real_prefix = module.render_layer_prefix
        entered = harness.threading.Event()
        release = harness.threading.Event()

        def blocked_prefix(payload, plot, view, split, cancel=None,
                          previous=None, previous_split=0):
            entered.set()
            release.wait(10)
            return real_prefix(payload, plot, view, split, cancel=cancel,
                               previous=previous, previous_split=previous_split)

        with harness.patch.object(module, "render_layer_prefix", blocked_prefix):
            model._qt_window(surface, {"prev": None, "current": payload, "next": None},
                             5, "motion index", 80)
            self.assertTrue(entered.wait(10), "the prefix worker never started")
            # The anchor moves: layer 5 slides to the prev ghost.
            model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                             6, "motion index", None)
            self.assertTrue(surface.job["cancel"].is_set(),
                            "a prefix whose layer became a ghost stayed live")
            release.set()
            self._pump_rasters(model, "popover")
            self.assertEqual(surface.layers[5].prefixSplit, -1,
                             "the ghost's stale prefix landed")
        # A-B-A: the revisit demands its own prefix at the new split.
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 55)
        self._pump_rasters(model, "popover")
        self.assertEqual(surface.layers[5].prefixSplit, 55,
                         "the revisited layer never re-rendered its prefix")

    def test_prefix_refreshes_publish_distinct_urls(self):
        from PyQt6.QtCore import QUrl
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        # The refresh rides the DETACHED threshold policy: the
        # follower now starts attached, whose checkpoint cadence
        # would leave the second split unrendered and its URL reused.
        model.setFollowerLayerAnchor(5)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None}, 5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        first = wrapped.prefixData
        self.assertTrue(first.startswith("file://"))
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None}, 5, "motion index", 160)
        self._pump_rasters(model, "popover")
        self.assertNotEqual(first, wrapped.prefixData,
                            "the refreshed prefix reused its URL")
        self.assertTrue(harness.os.path.exists(QUrl(first).toLocalFile()),
                        "the earlier prefix's file vanished")
        self.assertTrue(harness.os.path.exists(QUrl(wrapped.prefixData).toLocalFile()))

    def test_a_view_change_invalidates_the_prefix(self):
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None}, 5, "motion index", 50)
        self._pump_rasters(model, "popover")
        wrapped = surface.layers[5]
        self.assertTrue(wrapped.prefixValid)
        old_key = wrapped._prefix_key
        old_url = wrapped.prefixData
        model.setFollowerView("popover", 1.5, 0.7, 563, 492, False, 0.0, 0.0)
        # The flag cannot carry this claim. The flush's invalidation
        # schedules the replacement itself, so the flag reads false only
        # until that re-render lands — and the read then races the
        # landing, failing while the old prefix is already gone. What
        # must not survive is the OLD prefix: its key and its URL. The
        # wait is the settled window the rest of this file uses.
        self._pump_rasters(model, "popover")
        self.assertNotEqual(wrapped._prefix_key, old_key,
                            "the retained prefix kept the old view's key")
        self.assertNotEqual(wrapped.prefixData, old_url,
                            "the old view's prefix is still the one served")
        self.assertEqual(wrapped._prefix_key, wrapped._expected_key,
                         "a stale prefix reads valid after the re-render")
        self.assertTrue(wrapped.prefixValid,
                        "the new view's prefix never read valid")


