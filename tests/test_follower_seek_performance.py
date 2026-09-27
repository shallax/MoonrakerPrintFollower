"""Executable follower seek performance contracts."""
from tests import composed_runtime_support as harness

class RendererOnlySeekBenchmarks(harness.RendererOnlySeekBenchmarks):
    def test_the_renderer_only_seek_matrix(self):
        # The 60k/150k/300k/500k RENDERER matrix: the cold seek's
        # render+schedule cost on a pre-decoded payload, the
        # raster-hot seek's ZERO committed work (the final target's
        # raster side), and the adjacent seek's one-new-layer cost.
        # Request/read/decode/publish stages are deliberately absent.
        model = self.monitor()
        self._feed(model, "popover", width=563, height=492)
        surface = model._plate_surfaces["popover"]
        print("seek matrix (popover 563x492, ms):")
        colds = {}
        for motions in (60000, 150000, 300000, 500000):
            payload = self._dense(motions)
            # A distinct anchor per density: a shared anchor would
            # read the previous density's hot wrapper as "cold".
            anchor = 200 + motions // 10000
            colds[motions] = self._time_seek(model, "popover", anchor, payload)
            # Raster-hot: away and back — the return must commit
            # nothing (zero geometry, zero PNG work).
            self._time_seek(model, "popover", anchor + 1, payload)
            before = surface.stats["committed"]
            hot = self._time_seek(model, "popover", anchor, payload)
            committed = surface.stats["committed"] - before
            adjacent = self._time_seek(model, "popover", anchor + 2, payload)
            print("  %6d motions: cold %7.1f | hot %6.1f (%d commits) | adjacent %7.1f"
                  % (motions, colds[motions], hot, committed, adjacent))
            self.assertEqual(committed, 0,
                             "the raster-hot seek did raster work")
            self.assertLess(hot, 250.0, "the raster-hot seek did not settle")
            self.assertLess(colds[motions], 5000.0)
            self.assertLess(adjacent, 5000.0)

    def test_the_prefix_runs_first_and_the_trace_reads_the_seek(self):
        # The partial seek's demand order: the printed PREFIX runs
        # before the full layer's raster, and the trace's own stages
        # carry the timeline — the prefix's commit lands ahead of
        # the full one.
        model = self.monitor()
        self._feed(model, "popover", width=563, height=492)
        model._seek_trace_enabled = True
        # The harness drives _qt_window directly (no slider slots):
        # the trace opens on the seek's entry mark.
        model._trace("T1 seek entry", {"layer": 200})
        surface = model._plate_surfaces["popover"]
        payload = self._dense(500000)
        start = harness.time.monotonic()
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None},
                         200, "motion index", 250000)
        self._pump_rasters(model, "popover")
        kinds = [entry.get("kind") for entry in model._seek_trace
                 if entry["stage"] == "T9 raster start"]
        self.assertTrue(kinds, "the trace recorded no raster demand")
        self.assertEqual(kinds[0], "prefix",
                         "the prefix did not run before the full raster")
        commits = [(entry["ms"], entry.get("kind"))
                   for entry in model._seek_trace
                   if entry["stage"] == "T11 raster committed"]
        for ms, kind in commits:
            print("T11 %s committed at %7.1f ms" % (kind, ms))
        self.assertTrue(commits and commits[0][1] == "prefix",
                        "the prefix's commit did not land first")
        total = (harness.time.monotonic() - start) * 1000.0
        print("partial 500k seek (prefix + full + ghosts): %7.1f ms total" % total)
        self.assertLess(total, 10000.0)

    def test_the_trace_carries_the_debounce_from_the_raw_tick(self):
        # The slider's raw tick opens the debounce; T1 records how
        # long the commit waited on it — the seek's perceived
        # latency includes the wait, so the trace carries it.
        model = self.monitor()
        model._seek_trace_enabled = True
        model.seekAnchorTicked()
        model.setFollowerLayerAnchor(5)
        entry = model._seek_trace[0]
        self.assertEqual(entry["stage"], "T1 seek entry")
        self.assertGreaterEqual(entry.get("debounce_ms", 0.0), 0.0)
        # A programmatic seek without a tick carries no debounce.
        model._seek_trace = []
        model.setFollowerLayerAnchor(7)
        self.assertNotIn("debounce_ms", model._seek_trace[0])

    def test_the_png_transport_cost(self):
        # The complete PNG transport (encode + write + atomic
        # rename) on the seek's three sibling sizes — the worker's
        # cost, measured apart from the geometry walk.
        from plugins.PlateQt import png_file, render_layer_raster
        plot = {"offsetX": 10.0, "offsetY": 10.0, "sx": 1.0, "sy": 1.0,
                "bedXMin": 0.0, "bedYMax": 250.0}
        view = {"width": 563, "height": 492, "scale": 1.0, "lineScale": 0.7,
                "compact": False, "panX": 0.0, "panY": 0.0}
        payload = self._dense(500000)
        payload["travels"] = [[[0.0, 0.0, 0.0], [10.0, 10.0, 1.0]]]
        coloured, base, travels = render_layer_raster(payload, plot, view)
        for name, image in (("coloured", coloured), ("base", base),
                            ("travels", travels)):
            best = 1e9
            for _ in range(3):
                start = harness.time.monotonic()
                url = png_file(image, "/tmp/mpf", "transport-probe-%s" % name)
                best = min(best, (harness.time.monotonic() - start) * 1000.0)
            print("PNG transport %s: %6.1f ms" % (name, best))
            self.assertTrue(url.startswith("file://"))
            self.assertLess(best, 300.0, "the PNG transport stalled")


