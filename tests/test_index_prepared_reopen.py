"""Executable index prepared reopen contracts."""
from tests import index_plate_support as harness

class PreparedReopenPolicyTests(harness.PreparedReopenPolicyTests):
    def test_prefetched_geometry_gets_motion_arrays_when_the_live_window_arrives(self):
        index = self._compact_view(8, hydrated=(2, 3, 4), followed=3)
        payload = self.qt.load("PlateProgress").decode_layer(self._payload(5), immutable=True)
        # Background GPU prefetch already decoded layer 5 without raw arrays.
        self.service._decoded_lru.set(5, payload, 100)
        with harness.patch.object(self.service, "_advance"):
            self.service.set_gpu_rendering("popover", True)
            self.service.set_followed_layer(4)
        self.assertIn(5, self.service._hydrate_arrays,
                      "hot GPU geometry suppressed the live motion-array demand")
        self.assertNotIn(5, self.service._hydrate, "live handover re-decoded hot geometry")
        self.assertIs(self.service._decoded_lru.peek(5), payload)
        captured = self._capture_submit()
        self.qt.load("GCodeIndexService")
        self.service._foreground_pending.set()  # A prior foreground demand settled.
        def hydrate(target, path, layer, should_stop):
            self.assertIs(target, index)
            self.assertEqual(layer, 5)
            self.assertFalse(should_stop(), "the array worker cancelled on an old demand")
            target.hydrated_layers.add(layer)
            return True
        with harness.patch.object(self.qt.load("IndexTasks"), "hydrate_layer_from_file", side_effect=hydrate):
            self.assertTrue(self.service._drain_arrays_debt(index))
            self.assertEqual(captured[0][1](), ([], {}))
        self.assertIn(5, index.hydrated_layers)

    def test_gpu_distant_prepared_seek_does_not_hydrate_live_motion_arrays(self):
        index = self._compact_view(16, hydrated=(2, 3, 4), followed=3)
        for layer in (9, 10, 11):
            self.service._full_cache.set(layer, self._payload(layer), 100)
        with harness.patch.object(self.service, "_advance"):
            self.service.set_gpu_rendering("popover", True)
            self.service.set_manual_anchor(10)
        captured = self._capture_submit()
        self.qt.load("GCodeIndexService")
        with harness.patch.object(self.service, "_prepared_open"), \
                harness.patch.object(self.files, "lease", side_effect=AssertionError("distant cache requested raw file")), \
                harness.patch.object(self.qt.load("IndexTasks"), "hydrate_layer_from_file", side_effect=AssertionError("distant cache hydrated motion arrays")):
            self.service._advance()
            self.assertEqual(captured[0][0], "hydrate")
            self.assertIsNone(captured[0][2])
            failed, stash = captured[0][1]()
        self.assertEqual(failed, [])
        self.assertIn(10, stash)
        points = stash[10][1]["classes"]["SKIN"][0]
        self.assertIsInstance(points, tuple)
        self.assertIsInstance(points[0], tuple)
        self.assertEqual(index.hydrated_layers, {2, 3, 4})
        self.assertEqual(self.service._hydrate_arrays, set())

    def test_gpu_prefetch_only_uses_nearby_existing_sources_once(self):
        index = self._view(20)
        index.manual_anchor = 10
        index.followed_layer = 3
        self.assertIsNone(self.service._gpu_prefetch_layer(index))
        with harness.patch.object(self.service, "_advance"):
            self.service.set_gpu_rendering("popover", True)
        selected = [self.service._gpu_prefetch_layer(index) for _ in range(14)]
        candidates = [n for n in selected if n is not None]
        self.assertEqual(candidates[0], 12)
        self.assertEqual(len(candidates), len(set(candidates)))
        self.assertTrue(set(candidates).isdisjoint({2, 3, 4, 9, 10, 11}))
        self.assertTrue(all(min(abs(n - 3), abs(n - 10)) <= 4 for n in candidates))
        self.assertIsNone(selected[-1], "a full cache could repeatedly re-decode evicted neighbours")
        with harness.patch.object(self.service, "_advance"):
            self.service.set_gpu_rendering("popover", False)
        self.assertIsNone(self.service._gpu_prefetch_layer(index))

    def test_gpu_speculation_decodes_without_file_or_motion_arrays_and_yields_to_seek(self):
        index = self._view(8)
        index.compact = True
        index.manual_anchor = 3
        index.followed_layer = 3
        self.service._full_cache.set(5, self._payload(5), 100)
        with harness.patch.object(self.service, "_advance"):
            self.service.set_gpu_rendering("popover", True)
        work = []
        self.qt.load("GCodeIndexService")
        with harness.patch.object(self.service, "_prepared_open"), \
                harness.patch.object(self.service, "_submit", side_effect=lambda kind, task, *args: work.append((kind, task))), \
                harness.patch.object(self.files, "lease", side_effect=AssertionError("prefetch requested the file")), \
                harness.patch.object(self.qt.load("IndexTasks"), "hydrate_layer_from_file", side_effect=AssertionError("prefetch hydrated motion arrays")):
            self.service._advance()
            self.assertEqual(work[0][0], "hydrate")
            failed, stash = work[0][1]()
            self.assertEqual(failed, [])
            self.assertIn(5, stash)
            self.service._foreground_pending.set()
            failed, stash = work[0][1]()
            self.assertEqual((failed, stash), ([], {}), "cancelled speculation latched a decode failure")

    def test_a_complete_reopen_takes_the_fast_path(self):
        # : a valid complete cache must not
        # read its own bytes back — the table says complete, the
        # pass stands down, and the fraction reads 100% with zero
        # RAM residency.
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        reads = []
        original_read = self.store.read
        self.store.read = lambda identity, table, layer: (
            reads.append(layer) or original_read(identity, table, layer))
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved)
        self.assertEqual(self.service._full_next, 5)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.service._advance()
        self.assertEqual(self.service._busy, "", "the pass ran after a fast-path reopen")
        self.assertIsNone(self.service._prepared_writer)
        self.assertEqual(reads, [], "the reopen replayed the store")

    def test_the_public_restore_adopts_the_prepared_store(self):
        # G: the PRODUCTION lifecycle — _finish("restore") installs
        # the view, and must OPEN the prepared table BEFORE adopting
        # it (the restore path returns before _advance's open, and
        # _advance never re-adopts). A persisted complete store
        # takes the fast path through the real restore: no raw
        # download, no full-prepare rebuild, and the window's
        # payloads decode from the prepared store.
        # The harness's qt.load duplicates the plugin modules, so the
        # index must come from the HARNESS namespace's GCodeIndex —
        # the restore's isinstance guard reads the service's class
        # from that same namespace.
        index = self.qt.load("GCodeIndex").build_index_from_bytes(
            b"".join(b";LAYER:%d\nG1 X0 Y0 E0.1\n" % layer for layer in range(5)))
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        requested = []

        class Cache:
            def load(self, identity):
                return index

        self.service._cache = Cache()
        self.service._restored = False
        self.files.lease = lambda: None  # the raw lease is unavailable
        self.files.request_file = lambda: requested.append("file")
        submitted = []
        original_submit = self.service._submit
        self.service._submit = (lambda kind, fn, *args, **kwargs:
                                (submitted.append(kind),
                                 original_submit(kind, fn, *args, **kwargs))[1])
        self._pump()
        self.assertTrue(self.service._prepared_saved,
                        "the restored complete store never took the fast path")
        self.assertTrue(self.service._prepared_complete)
        self.assertEqual(self.service._full_next, 5)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.assertEqual(requested, [], "the restore demanded the raw file")
        self.assertNotIn("fullprep", submitted,
                         "the restore started a full prepared rebuild")
        # The window's payloads decode from the prepared store — the
        # presentation source reports the decoded hot state.
        window = self.service.plate_layers(2)
        self.assertIsNotNone(window)
        self.assertIn("current", window)
        # The live window's hydration follows the adoption (the fast
        # path's pump exits before the demand submits): drive it and
        # confirm the prepared store served the payload — no decode
        # from the raw file.
        from PyQt6.QtCore import QCoreApplication
        deadline = harness.time.monotonic() + 5.0
        while harness.time.monotonic() < deadline:
            self.service.request_hydration(2)  # the live seek's demand
            self.service._advance()
            if self.service._presentation_source(2) == "decoded":
                break
            QCoreApplication.processEvents()
            harness.time.sleep(0.01)
        self.assertEqual(self.service._presentation_source(2), "decoded",
                         "the restored current layer never became presentation-ready")

    def test_a_holey_reopen_repairs_without_losing_valid_entries(self):
        # The sparse-repair regression: 0,1,3,4 valid and
        # 2 missing — the repair regenerates 2 and COPIES the valid
        # entries into the new file; nothing complementary-holes.
        from mpf.gcode.PlateProgress import decode_layer
        writer = self.store.open_for_write("print-key", 5)
        for layer in (0, 1, 3, 4):
            self.store.append(writer, layer, self._payload(layer))
        self.store.finish_write(writer)
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertFalse(self.service._prepared_saved,
                         "a holey table took the fast path")
        self._pump()
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(all(entry[0] == self.state_cached for entry in loaded["table"]),
                        "the repair published a complementary hole")
        for layer in (0, 1, 3, 4):
            # The COPIED entries keep their exact payloads.
            raw = self.store.read("print-key", loaded["table"], layer)
            self.assertEqual(decode_layer(raw)["classes"]["SKIN"][0][1][1],
                             float(layer), "layer %d reads another layer's payload" % layer)
        # The REGENERATED hole is the synthetic index's own geometry.
        regrown = decode_layer(self.store.read("print-key", loaded["table"], 2))
        self.assertIn("WALL-OUTER", regrown["classes"])
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_a_demand_prepared_layer_persists_into_the_writer(self):
        # : a manual/live demand prepares the
        # layer BEFORE the pass reaches it — the encoded bytes must
        # enter the writer anyway, or the finish publishes a (0,0)
        # hole for a layer that WAS prepared.
        self._view(5)
        self.service._prepared_open(self.files.identity)
        encoded = self._payload(2)
        self.service._prepared_persist(2, encoded)
        self.assertIsNotNone(self.service._prepared_writer,
                             "the persist opened no writer")
        self.assertEqual(self.service._prepared_writer["table"][2][0],
                         self.state_cached)
        self.assertGreater(self.service._prepared_writer["table"][2][2], 0)
        self.assertIn(2, self.service._prepared_coverage)
        # The pass reaching the same layer copies the cached bytes
        # instead of skipping it (the worker's branch).
        self.service._full_cache.set(2, encoded, len(encoded))
        self._pump()
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(all(entry[0] == self.state_cached for entry in loaded["table"]),
                        "a prepared layer published as a hole")

    def test_the_pass_fraction_counts_stores_not_residency(self):
        # : a 1,000-layer print with a bounded
        # RAM tier must report the store's coverage, not the cache's
        # residency (a 64-entry cache must not cap the band at 6%).
        self._view(layers=1000)
        self.service._prepared_open(self.files.identity)
        for layer in range(900):
            self.service._prepared_coverage.add(layer)
        self.assertEqual(self.service.plate_pass_fraction(), 0.9)
        for layer in range(64):
            self.service._full_cache.set(layer, b"x" * 1000, 1000)
        self.assertEqual(self.service.plate_pass_fraction(), 0.9,
                         "the fraction followed the RAM tier's residency")

    def test_the_fraction_reaches_100_with_uncacheable_layers(self):
        # The coverage truth: a layer the codec refused is as
        # RESOLVED as one it held — the fraction must reach 100%
        # once the pass has given every layer its attempt.
        self._view(3)
        self.qt.load("GCodeIndexService")
        real_encode = self.qt.load("IndexTasks")._encode_layer
        calls = []

        def refusing(payload):
            # The synthetic index's geometry carries no layer marker:
            # the pass walks 0, 1, 2 in order, so the SECOND encode
            # is layer 1's.
            calls.append(payload)
            if len(calls) == 2:
                raise ValueError("refused")
            return real_encode(payload)
        with harness.patch.object(self.qt.load("IndexTasks"), "_encode_layer", refusing):
            self.service._prepared_open(self.files.identity)
            self._pump()
        self.assertEqual(self.service.plate_pass_fraction(), 1.0,
                         "the refused layer capped the fraction")
        loaded = self.store.load_table("print-key")
        self.assertEqual(loaded["table"][1][0], 2,
                         "the refusal did not publish as UNCACHEABLE")
        self.assertTrue(loaded["complete"])

    def test_the_reopen_never_retries_an_uncacheable_layer(self):
        # An UNCACHEABLE layer rides the reopen AS-IS: the fast path
        # stands the pass down, the coverage counts it, and no
        # writer opens to re-walk the refusal.
        writer = self.store.open_for_write("print-key", 3)
        for layer in (0, 2):
            self.store.append(writer, layer, self._payload(layer))
        self.store.append_uncacheable(writer, 1)
        self.store.finish_write(writer)
        self._view(3)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved,
                        "the complete table (refusal included) took the repair path")
        self.assertTrue(self.service._prepared_complete)
        self.assertIsNone(self.service._prepared_writer)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.assertIn(1, self.service._prepared_coverage,
                      "the uncacheable layer left the coverage")
        self.assertFalse(self.service._prepared_served(1),
                         "an uncacheable layer read as served")
        self.service._advance()
        self.assertEqual(self.service._busy, "",
                         "the reopen re-walked the uncacheable layer")

    def test_a_layer_is_not_served_without_a_table(self):
        # No open table (a print whose pass never opened one, a cache
        # clear): nothing is readable without the raw file.
        self.assertFalse(self.service._prepared_served(0))
        self.service._prepared_table = []
        self.assertFalse(self.service._prepared_served(0))

    def test_a_table_of_another_length_is_dropped(self):
        # The file's layer count no longer matches this print's index: the
        # table is unusable, and the fresh pass overwrites it rather than
        # resume one print's geometry onto another's layers.
        self._view(5)
        self.store.finalise("print-key", [self._payload(i) for i in range(3)])
        self.service._prepared_open(self.files.identity)
        self.assertEqual(len(self.service._prepared_coverage), 3)
        self.service._adopt_prepared()
        self.assertIsNone(self.service._prepared_table, "a foreign-length table was adopted")
        self.assertEqual(self.service._prepared_coverage, set())

    def test_an_abandoned_writer_is_aborted(self):
        # An unfinished writer on an abandon path: its temp file goes and
        # the handle closes, so the store never publishes half a pass.
        writer = self.store.open_for_write("print-key", 3)
        self.service._prepared_writer = writer
        self.service._abort_prepared_writer()
        self.assertIsNone(self.service._prepared_writer)
        self.assertTrue(writer["retired"], "the abandoned writer stayed writable")
        self.assertFalse(harness.os.path.exists(writer["temp"]), "the abandoned writer's file survived")

    def test_writers_drop_even_when_the_store_is_already_gone(self):
        # A cache clear or a rebind can take the store first: the writer
        # reference still drops, and no later pass can finalise it.
        self.service._abort_prepared_writer()          # nothing to abandon
        self.service._suspend_prepared_writer()        # nothing to checkpoint
        self.service._prepared = None
        self.service._prepared_writer = {"table": [None]}
        self.service._abort_prepared_writer()
        self.assertIsNone(self.service._prepared_writer)
        self.service._prepared_writer = {"table": [None]}
        self.service._suspend_prepared_writer()
        self.assertIsNone(self.service._prepared_writer)

    def test_a_weak_identity_never_opens_the_prepared_table(self):
        # The same strength gate the index restore obeys: a weak identity
        # (no reliable timestamp) must never adopt the old prepared
        # table, or a re-extracted file resurrects stale geometry.
        self._view(5)
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        self.files.identity.modified = 0.0
        self.service._prepared_open(self.files.identity)
        self.assertIsNone(self.service._prepared_table)
        # And with no table at all there is nothing to adopt.
        self.service._adopt_prepared()
        self.assertFalse(self.service._prepared_saved)

    def test_the_fraction_reads_the_ram_tier_when_nothing_persists(self):
        # No persistence configured: the RAM tier's residency is the only
        # prepared store there is — and no view is no fraction at all.
        self._view(5)
        self.service._prepared = None
        self.service._full_cache.set(0, b"x" * 10, 10)
        self.assertEqual(self.service.plate_pass_fraction(), 1 / 5)
        self.service._view = None
        self.assertIsNone(self.service.plate_pass_fraction())

    def test_an_index_with_no_layers_has_no_fraction(self):
        # No layers is no fraction: the pass bar reads empty, never a
        # division by a zero total.
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, harness.make_index(layers=0))
        self.assertIsNone(self.service.plate_pass_fraction())

    def test_noncompact_hydrated_current_is_presented_first_without_a_file_lease(self):
        # The live regression: non-compact indexes report every layer as
        # hydrated immediately, while the decoded presentation cache is
        # initially empty. CURRENT must still be prepared, and it must
        # land before either ghost without reacquiring the G-code.
        index = self._view(5)
        index.followed_layer = 2
        self.service._last_save_at = harness.time.monotonic()  # keep index-save out of this ordering test
        self.service._prepared_open(self.files.identity)
        requests = []
        self.files.lease = lambda: None
        self.files.request_file = lambda: requests.append(set(self.service._decoded_lru))

        self.qt.load("GCodeIndexService")
        real_prepare = self.qt.load("IndexTasks")._prepare_layer
        prepared = []

        def recording_prepare(index_arg, layer):
            prepared.append(layer)
            return real_prepare(index_arg, layer)

        with harness.patch.object(self.qt.load("IndexTasks"), "_prepare_layer", recording_prepare):
            self.service.request_hydration(2)
            for _ in range(200):
                self.qt.events(5)
                if 2 in self.service._decoded_lru:
                    break

        self.assertIn(2, self.service._decoded_lru,
                      "the hydrated live current never became presentation-ready")
        self.assertEqual(prepared[0], 2,
                         "a ghost was prepared before the visible current")
        self.assertEqual(requests, [],
                         "hydrated index arrays incorrectly requested the raw G-code")
        self.assertEqual(self.service._presentation_source(2), "decoded")

    def test_a_prepared_window_seeks_without_the_file_lease(self):
        # The prepared store serves the demanded window: the seek
        # must never wait on the raw G-code lease — the lease exists
        # only for the hydrate-from-file fallback.
        self._view(3)
        writer = self.store.open_for_write("print-key", 3)
        for layer in range(3):
            self.store.append(writer, layer, self._payload(layer))
        self.store.finish_write(writer)
        self.service._prepared_open(self.files.identity)
        requests = []

        def request():
            # Record the LRU state at request time: the seek itself
            # must be DONE before any file request (the pass's later
            # request is the file's legitimate user).
            requests.append(set(self.service._decoded_lru))
        self.files.request_file = request
        self.files.lease = lambda: None  # the G-code file is ABSENT
        # The manual seek demands its window (the live demand path
        # fires for the manual window regardless of hydration).
        self.service.set_manual_anchor(1)
        for _ in range(200):
            if all(layer in self.service._decoded_lru for layer in (0, 1, 2)):
                break
            self.service._advance()
            self.qt.events(5)
        self.assertEqual(requests, [],
                         "prepared/hydrated presentation work requested the raw G-code")
        for layer in range(3):
            self.assertIn(layer, self.service._decoded_lru,
                          "layer %d never decoded from the store" % layer)

    def test_a_prepared_layer_decodes_without_a_lease_and_hydrates_its_arrays_later(self):
        # F3: a reopened COMPLETE compact store's geometry is the
        # prepared bytes' own work. The raw G-code lease gates only the
        # physical MOTION ARRAYS — a demand with no file in hand must
        # still decode and display, and the arrays then hydrate the
        # moment the file arrives, with no second presentation demand.
        path = harness._write_gcode(b"".join(
            b";LAYER:%d\n;TYPE:WALL\nG1 X0 Y0 E0.1\nG1 X1 Y1 E0.2\nG1 X2 Y2 E0.3\n" % layer
            for layer in range(3)))
        self.addCleanup(harness.os.remove, path)
        index = self.qt.load("GCodeIndex").build_index_from_file(path, compact=True)
        self.assertTrue(index.compact)
        self.assertEqual(index.hydrated_layers, set(), "the compact scan hydrated a layer")
        self.store.finalise("print-key", [self._payload(layer) for layer in range(3)])
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_complete,
                        "the complete store never took the fast path")
        submitted = []
        original_submit = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append((kind, lease)) or original_submit(kind, work, lease))

        # Phase one: no raw file at all. The prepared payload must reach
        # the hot cache anyway — the arrays are the file's business.
        self.files.lease = lambda: None
        self.service.request_hydration(1)
        for _ in range(300):
            self.service._advance()
            self.qt.events(5)
            if 1 in self.service._decoded_lru:
                break
        self.assertIn(1, self.service._decoded_lru,
                      "the prepared compact layer never decoded without a lease")
        self.assertEqual(self.service._presentation_source(1), "decoded")
        self.assertIsNotNone(self.service.plate_layers(1)["current"],
                             "the decoded payload never reached the display bundle")
        self.assertTrue([kind for kind, _lease in submitted if kind == "hydrate"],
                        "the lease-less demand submitted no worker at all")
        self.assertTrue(all(lease is None for kind, lease in submitted if kind == "hydrate"),
                        "the presentation pass waited on a lease it does not need")
        self.assertNotIn(1, index.hydrated_layers, "an absent file hydrated arrays")

        # Phase two: the raw file arrives. The arrays hydrate off the
        # recorded demand alone — the layer is already decoded, so
        # nothing re-asks for its presentation.
        class RawLease:
            def __init__(self, source):
                self.path = source

            def close(self):
                pass

        self.files.lease = lambda: RawLease(path)
        for _ in range(300):
            self.service._advance()
            self.qt.events(5)
            if 1 in index.hydrated_layers:
                break
        self.assertIn(1, index.hydrated_layers,
                      "the motion arrays never hydrated after the raw file arrived")
        self.assertTrue(len(index.motion_offsets[1]) > 0,
                        "the hydration landed no physical motion arrays")

    def test_the_arrays_debt_prunes_and_waits_for_the_file(self):
        # The debt's own contract: it holds only layers the retention
        # window still covers and the index still lacks, and while no
        # file is in hand it submits NOTHING — one request for the file
        # is the whole poll. The file's arrival drains it once, through
        # the arrays' own worker, carrying the lease the presentation
        # never had.
        index = self._compact_view(3, hydrated=(2,), followed=1)
        self.service._hydrate_arrays = {0, 2, 9}
        self.service._full_next = 3  # the pass is done: the poll is the debt's
        self.files.lease = lambda: None
        requested = []
        self.files.request_file = lambda: requested.append(1)
        captured = self._capture_submit()
        self.service._advance()
        self.assertEqual(captured, [], "a lease-less debt submitted a worker")
        self.assertEqual(requested, [1], "the debt never asked for the file")
        self.assertEqual(self.service._hydrate_arrays, {0},
                         "the debt kept a hydrated or out-of-range layer")

        class RawLease:
            path = "/nonexistent/part.gcode"

            def close(self):
                pass

        self.files.lease = lambda: RawLease()
        captured = self._capture_submit()
        self.service._advance()
        self.assertEqual([kind for kind, _work, _lease in captured], ["hydrate"],
                         "the arriving file submitted no arrays worker")
        self.assertIsNotNone(captured[0][2], "the arrays worker took no lease")
        self.assertEqual(captured[0][1](), ([], {}),
                         "the arrays worker answers another contract than the hydrate")
        self.assertEqual(self.service._hydrate_arrays, set(),
                         "the settled debt stayed queued")
        self.assertNotIn(0, index.hydrated_layers)

    def test_a_swept_store_never_receives_an_older_generations_save(self):
        # The clear's lifecycle: a save queued BEFORE the clear must
        # never recreate the swept directory; a rebind's store is a
        # different directory and still receives its save.
        saved = []

        class Store:
            def save(self, identity, index):
                saved.append(identity)
                return "path"

        store = Store()
        service = self.service
        service._swept_store = store
        service._generation = 7
        # The old generation's queued save against the swept store.
        self.assertIsNone(service._save_index(store, 6, "identity", object()))
        self.assertEqual(saved, [], "the swept store received the stale save")
        # The same store under the CURRENT generation saves.
        self.assertEqual(service._save_index(store, 7, "identity", object()), "path")
        self.assertEqual(saved, ["identity"])
        # A different store (a rebind) under an old generation saves.
        other = Store()
        self.assertEqual(service._save_index(other, 6, "other", object()), "path")
        self.assertEqual(saved, ["identity", "other"])

    def test_a_detached_seek_never_rewinds_a_complete_fast_reopen(self):
        # The review's finding: a seek focused the pass by rewinding
        # the frontier — after a fast-path restore that rewind
        # re-read the whole saved store for nothing. The saved latch
        # now holds the frontier: the fast path stays complete.
        self.store.finalise("print-key", [self._payload(i) for i in range(5)])
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved)
        self.assertEqual(self.service._full_next, 5)
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service.set_manual_anchor(3)
        self.assertEqual(self.service._full_next, 5,
                         "the seek rewound the fast path's frontier")
        self._pump()  # the sought window's own demand settles
        self.assertNotIn("fullprep", submitted,
                         "the seek restarted the whole-store walk")
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_the_saved_latch_autonomously_recovers_after_one_failed_publish(self):
        # A failed publish must NOT read as saved, and recovery must not
        # depend on a later user demand or _prepared_persist call.
        self._view(3)
        self.service._prepared_open(self.files.identity)
        self.service._prepared_persist(0, self._payload(0))
        self.service._full_next = len(self.service._view.ranges)
        self.service._prepared_saved = False
        from mpf.gcode.PreparedStore import PreparedCache
        real_finish = PreparedCache.finish_write
        attempts = []

        def fail_once(store, writer):
            attempts.append(writer["identity"])
            if len(attempts) == 1:
                store.abort_write(writer)
                return None
            return real_finish(store, writer)

        with harness.patch.object(PreparedCache, "finish_write", fail_once):
            for _ in range(400):
                self.service._advance()
                self.qt.events(5)
                if self.service._prepared_saved and not self.service._busy:
                    break

        self.assertGreaterEqual(len(attempts), 2,
                                "the failed publish never retried autonomously")
        self.assertTrue(self.service._prepared_saved,
                        "the autonomous retry never latched")
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["complete"])

    def test_a_foreground_seek_interrupts_the_dense_layer_already_in_progress(self):
        # Between-layer checks are insufficient: the request deliberately
        # arrives AFTER one speculative layer has entered preparation.
        # The worker-visible callback must interrupt that same layer and
        # let CURRENT commit before background work resumes.
        index = harness.make_index(layers=40, motions=20000)
        index.followed_layer = 0
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        module = self.qt.load("GCodeIndexService")
        entered = harness.threading.Event()
        interrupted = harness.threading.Event()
        real_prepare = self.qt.load("IndexTasks")._prepare_layer

        def controlled_prepare(index_arg, layer, should_yield=None):
            if should_yield is not None and not interrupted.is_set():
                entered.set()
                deadline = harness.time.monotonic() + 2.0
                while harness.time.monotonic() < deadline:
                    if should_yield():
                        interrupted.set()
                        raise module.PreparationYield()
                    harness.time.sleep(0.001)
            return real_prepare(index_arg, layer)

        with harness.patch.object(self.qt.load("IndexTasks"), "_prepare_layer", controlled_prepare):
            self.service._advance()
            self.assertEqual(self.service._busy, "fullprep")
            self.assertTrue(entered.wait(1.0),
                            "the speculative layer never entered preparation")

            started = harness.time.monotonic()
            self.service.set_manual_anchor(30)
            for _ in range(400):
                self.qt.events(2)
                if 30 in self.service._decoded_lru:
                    break
            elapsed = (harness.time.monotonic() - started) * 1000.0

        self.assertTrue(interrupted.is_set(),
                        "the in-progress speculative layer never yielded")
        self.assertIn(30, self.service._decoded_lru,
                      "foreground CURRENT did not commit after the yield")
        self.assertLess(elapsed, 500.0,
                        "foreground CURRENT waited behind the speculative layer")

    def test_the_pass_batch_yields_to_a_demand(self):
        # A seek mid-pass cuts in: the single worker releases the
        # batch (the yield check between layers), the demand's task
        # runs next, and the pass resumes behind it — the manual
        # window's decoded layers prove the demand committed while
        # the pass was still walking. The dense index stretches the
        # pass across several batches so the seek lands mid-walk.
        index = harness.make_index(layers=60, motions=20000)
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        self.service._advance()
        self.assertEqual(self.service._busy, "fullprep",
                         "the pass never submitted")
        self.service.set_manual_anchor(30)
        full_at_demand = 60
        for _ in range(400):
            self.service._advance()
            self.qt.events(5)
            if {29, 30, 31} <= set(self.service._decoded_lru):
                full_at_demand = self.service._full_next
                break
        self.assertLess(full_at_demand, 60,
                        "the pass finished before the demand cut in")

    def test_the_batch_loop_yields_the_interpreter_with_no_demand_pending(self):
        # The passive-yield pin. A batch's loop is tight and its layers
        # are cheap, so nothing but a wall-clock gate stops the worker
        # holding the GIL for the walk's whole duration — which is what
        # the UI thread reads as a frozen window for the pass. The
        # cadence is counted from the production yield's own calls (the
        # real yield still runs), and the count it is held to is the
        # ASKED count, never a fire count against the wall clock: a
        # descheduled worker cannot hand back a GIL it is not holding,
        # so a floor on fires per unit of time pins the machine's load
        # rather than the gate.
        self.qt.load("GCodeIndexService")
        self.assertTrue(hasattr(self.qt.load("IndexTasks"), "passive_yield"),
                        "the background workers have no passive yield at all")
        # 20,000 layers: the deadline is what ends the walk, never an
        # exhausted frontier — a batch that ran out of layers would stop
        # asking the gate before the window closed.
        index = harness.make_index(layers=20000, motions=20)
        self.service._view = self.qt.load("IndexView").IndexView(self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        captured = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: captured.append((kind, work))
        self.service._advance()
        self.service._submit = original
        self.assertEqual(captured[0][0], "fullprep",
                         "the first submission was not the pass")
        asked = []
        yields = []
        real = self.qt.load("IndexTasks").passive_yield

        def recorded(now, last):
            # The gate is asked far more often than it fires — the
            # demand check runs at the preparation's own granularity —
            # so only the calls that MOVED the watermark are hand-backs.
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                yields.append(harness.time.monotonic())
            return updated

        started = harness.time.monotonic()
        with harness.patch.object(self.qt.load("IndexTasks"), "passive_yield", recorded):
            frontier, _encoded, _uncacheable = captured[0][1]()
        elapsed = harness.time.monotonic() - started
        self.assertGreaterEqual(
            len(asked), 64,
            "the batch walked %d layers in %.0f ms and asked the gate %d times"
            % (frontier, elapsed * 1000.0, len(asked)))
        # The gate FIRING is what a descheduled worker cannot promise:
        # it hands back a GIL only while it holds one, and a run that
        # was off-CPU for the walk fires the gate once. The floor is
        # therefore that it fired at all, and the cadence claim below
        # is made only where there are two fires to measure between —
        # read as a fire count per unit of time it would pin the
        # machine's load, which is the mistake this pin exists to
        # avoid.
        self.assertGreaterEqual(
            len(yields), 1,
            "the batch walked %d layers in %.0f ms and never handed back"
            % (frontier, elapsed * 1000.0))

    def test_the_passive_yield_sleeps_when_due_and_only_then(self):
        # The helper's own contract, with a controlled clock and a
        # RECORDED sleeper. Counting a changed watermark is not proof of
        # a hand-back: the helper returns a fresh monotonic() whether or
        # not it slept, so a version with the sleep REMOVED still "hands
        # back" by that measure — measured, both integration pins passed
        # with only time.sleep(_YIELD_SLEEP_S) deleted. This is the pin
        # that fails on that mutation.
        module = self.qt.load("IndexWork")
        slept = []
        stub = harness.SimpleNamespace(monotonic=lambda: 123.0,
                               sleep=lambda seconds: slept.append(seconds))
        with harness.patch.object(module, "time", stub):
            early = module.passive_yield(10.0, 10.0 - module._PASSIVE_YIELD_S / 2)
            self.assertEqual(slept, [], "the gate slept before it was due")
            self.assertAlmostEqual(early, 10.0 - module._PASSIVE_YIELD_S / 2,
                                   msg="an early ask moved the watermark")
            due = module.passive_yield(10.0, 10.0 - module._PASSIVE_YIELD_S)
            self.assertEqual(len(slept), 1, "a due ask never handed back")
            self.assertEqual(slept[0], module._YIELD_SLEEP_S,
                             "the gate slept for the wrong interval")
            self.assertEqual(due, 123.0, "a due ask left the watermark")

    def test_the_pass_hands_the_interpreter_back_throughout_its_walk(self):
        # The scheduling contract, asserted deterministically: while the
        # pass walks flat-out on the worker, it asks its wall-clock gate
        # throughout and every ask that fires hands the interpreter
        # back. That is what stops a worker starving the UI thread.
        #
        # The beats a real timer sees while this runs are EVIDENCE and
        # are printed, not asserted. On a shared runner a descheduled
        # process and a worker holding the GIL are indistinguishable
        # from the timer's side, so a beat count or a worst gap derived
        # from wall clock pins the runner, not the gate — measured: the
        # same tree passed on one leg and failed another with 19 beats
        # against a minimum of 25.
        self.qt.load("GCodeIndexService")
        index = harness.make_index(layers=8000, motions=20)
        self.service._view = self.qt.load("IndexView").IndexView(self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        beats = []
        heartbeat = self.qt.QTimer()
        heartbeat.setInterval(harness._HEARTBEAT_INTERVAL_MS)
        heartbeat.timeout.connect(lambda: beats.append(harness.time.monotonic()))
        self.addCleanup(heartbeat.stop)
        asked = []
        fires = []
        real = self.qt.load("IndexTasks").passive_yield

        def recorded(now, last):
            # The gate is asked far more often than it fires; only the
            # calls that MOVED the watermark are hand-backs.
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                fires.append(now)
            return updated

        heartbeat.start()
        started = harness.time.monotonic()
        with harness.patch.object(self.qt.load("IndexTasks"), "passive_yield", recorded):
            self._pump(timeout=30.0)
        elapsed = harness.time.monotonic() - started
        heartbeat.stop()
        self.assertGreaterEqual(
            len(asked), 64,
            "the pass walked %.0f ms flat out and asked its gate %d times"
            % (elapsed * 1000.0, len(asked)))
        self.assertGreaterEqual(
            len(fires), 1,
            "the pass never handed the interpreter back in %.0f ms"
            % (elapsed * 1000.0))
        # Timing is EVIDENCE here, never a bound: a median under 50 ms
        # still depends on a shared runner's scheduling, and the beats a
        # timer sees cannot tell a descheduled process from a worker
        # holding the GIL.
        gaps = sorted(b - a for a, b in harness.pairwise(fires))
        print("heartbeat evidence: %d beats, %d gate asks, %d hand-backs, "
              "median hand-back gap %.1f ms over %.0f ms"
              % (len(beats), len(asked), len(fires),
                 (gaps[len(gaps) // 2] * 1000.0 if gaps else -1.0),
                 elapsed * 1000.0))

    def test_the_demands_own_encodings_are_written_by_the_worker(self):
        # The persistence move's two pins at once: a demanded layer's
        # encoded bytes must reach the incremental writer, and they must
        # reach it from the WORKER — the commit's per-layer
        # write+flush+seek+table-write+flush is exactly the UI-thread
        # cost the move removes. The store's append is recorded through
        # the class, so the identity assertion holds for every path that
        # can reach it.
        from mpf.gcode.PreparedStore import PreparedCache
        index = harness.make_index(layers=6, motions=40)
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        ui_thread = harness.threading.get_ident()
        appends = []
        real_append = PreparedCache.append

        def recorded(store, writer, layer, payload):
            appends.append((harness.threading.get_ident(), layer))
            return real_append(store, writer, layer, payload)

        with harness.patch.object(PreparedCache, "append", recorded):
            self.service.request_hydration(2)
            self._pump()
        self.assertIn(2, [layer for _ident, layer in appends],
                      "the demanded layer never reached the writer")
        self.assertNotIn(ui_thread, [ident for ident, _layer in appends],
                         "a store append ran on the UI thread")
        # ...and the once-only guarantee survives the move: the demanded
        # layer's slot holds its bytes in the published store rather than
        # the (0, 0) hole the pass alone would have left behind it.
        loaded = self.store.load_table("print-key")
        self.assertEqual(loaded["table"][2][0], self.state_cached,
                         "the demanded layer published as a hole")

    def test_the_batch_loop_yields_on_the_foreground_event_alone(self):
        # The loop-top yield reads the thread-safe EVENT, never the
        # mutable hydrate set across the thread boundary. With the
        # event set (a demand's signal) and the set EMPTY, a batch
        # must stop at the first loop-top check instead of running
        # its whole deadline.
        index = harness.make_index(layers=5, motions=2)
        self.service._view = self.qt.load("IndexView").IndexView(
            self.files.job_key, index)
        self.service._prepared_open(self.files.identity)
        captured = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            captured.append((kind, work)))
        self.service._advance()
        self.service._submit = original
        self.assertEqual(captured[0][0], "fullprep",
                         "the first submission was not the pass")
        work = captured[0][1]
        self.assertEqual(self.service._hydrate, set())
        self.service._foreground_pending.set()  # the demand's signal alone
        frontier, _encoded, _uncacheable = work()
        self.assertEqual(frontier, 0,
                         "the batch ignored the event and walked on")
        self.service._busy = ""  # the captured batch never ran for real

    def test_the_repair_copies_an_uncacheable_entry_without_a_rewalk(self):
        # The repair copies the old file's UNCACHEABLE state into the
        # new writer WITHOUT re-walking the layer — the codec's
        # refusal stands across sessions, the entry never becomes an
        # EMPTY hole, and only the genuine hole regenerates.
        from mpf.gcode.PreparedStore import STATE_UNCACHEABLE
        writer = self.store.open_for_write("print-key", 5)
        for layer in (0, 1, 4):
            self.store.append(writer, layer, self._payload(layer))
        self.store.append_uncacheable(writer, 3)
        self.store.finish_write(writer)  # complete: 2 EMPTY, 3 refused
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.qt.load("GCodeIndexService")
        prepared_walks = []
        real_prepare = self.qt.load("IndexTasks")._prepare_layer

        def spied(index_arg, layer, should_yield=None):
            prepared_walks.append(layer)
            return real_prepare(index_arg, layer, should_yield)

        with harness.patch.object(self.qt.load("IndexTasks"), "_prepare_layer", spied):
            self._pump()
        loaded = self.store.load_table("print-key")
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded["complete"])
        self.assertEqual(loaded["table"][3], (STATE_UNCACHEABLE, 0, 0),
                         "the repair lost the uncacheable state")
        self.assertEqual(loaded["table"][2][0], self.state_cached,
                         "the genuine hole never regenerated")
        self.assertNotIn(3, prepared_walks,
                         "the repair re-walked the refused layer")
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_a_uuid_only_identity_never_restores(self):
        # The review's identity policy at the SERVICE gate: the uuid
        # alone must never make an identity "strong enough to
        # restore" — the restore's strength is the reliable modified
        # timestamp, because the lookup and the validation both
        # ignore the uuid.
        self._view(5)
        self.service._restored = False
        self.files.identity.uuid = "u1"
        self.files.identity.modified = 0.0
        self.files.identity.size = 0
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertTrue(self.service._restored)
        self.assertNotIn("restore", submitted,
                         "a uuid-only identity attempted the restore")

    def test_a_weak_size_only_identity_never_restores(self):
        # The weak case (the review's policy): a name + size with NO
        # reliable timestamp is insufficient for cross-session reuse —
        # the content may have changed between extractions, so the
        # restore is skipped and the file rebuilds.
        self._view(5)
        self.service._restored = False
        self.files.identity.uuid = ""
        self.files.identity.modified = 0.0
        self.files.identity.size = 100
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertTrue(self.service._restored)
        self.assertNotIn("restore", submitted,
                         "a weak size-only identity attempted the restore")

    def test_a_timestamped_identity_takes_the_restore_gate(self):
        # The contrasting gate: a reliable modified timestamp makes
        # the identity strong enough for the persistent restore —
        # the submission names the restore.
        self._view(5)
        self.service._restored = False
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertTrue(self.service._restored)
        self.assertIn("restore", submitted,
                      "a timestamped identity never took the restore gate")

    def test_a_rebind_checkpoints_the_old_print_writer(self):
        # The rebind CHECKPOINTS (the review's clean-shutdown finding):
        # the old print's committed layers publish as an incomplete
        # store — never a bare drop of the reference, never a .tmp
        # left behind, and the old print's progress survives for its
        # next session.
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self.service._prepared_persist(0, self._payload(0))
        temp = self.service._prepared_writer["temp"]
        self.assertTrue(harness.os.path.exists(temp))
        self.service.bind(("other.gcode", 100, 2))
        self.assertIsNone(self.service._prepared_writer)
        self.assertFalse(harness.os.path.exists(temp),
                         "the rebind left the old print's temp writer")
        leftovers = [name for root, _dirs, names in harness.os.walk(self._dir.name)
                     for name in names if ".tmp-" in name]
        self.assertEqual(leftovers, [])
        table = self.store.load_table("print-key")
        self.assertIsNotNone(table, "the checkpointed partial never published")
        self.assertFalse(table["complete"], "the partial read as complete")
        self.assertEqual(self.store.read("print-key", table["table"], 0),
                         self._payload(0),
                         "the checkpointed layer never round-tripped")

    def test_a_close_waits_for_the_worker_it_started(self):
        # The worker writes the prepared store, and on Windows a
        # directory holding a file that appears after the delete has
        # listed it cannot be removed (WinError 145 in this file's own
        # teardown). The same race lands a write after the plugin
        # believes it has shut down. close() must not return while a
        # worker is still running.
        self._view(3)
        self.service._prepared_open(self.files.identity)
        self.service._advance()
        executor = self.service._executor
        # The premise: work was actually submitted, so there IS a
        # worker to leave running (an empty pool would pass this
        # whatever close() did).
        self.assertTrue(executor._threads, "no worker was ever started")
        self.service.close()
        alive = [t for t in executor._threads if t.is_alive()]
        self.assertEqual(alive, [], "close() left its worker running")

    def test_a_clean_close_publishes_the_partial_preparation(self):
        # The review's clean-shutdown P0: session A prepares a subset,
        # then closes NORMALLY — the checkpoint publishes as an
        # incomplete store. Session B (fresh stores, fresh service,
        # same identity, NO dead pid) sees the coverage immediately,
        # reads the prepared layers from disk, resumes from the EMPTY
        # slots and reaches a complete store.
        self._view(40)
        self.service._prepared_open(self.files.identity)
        # The production append path the pass's worker uses: three
        # layers commit, the pass's remaining walk is interrupted by
        # the NORMAL close — no fake crash, no dead pid.
        for layer in range(3):
            self.service._prepared_persist(layer, self._payload(layer))
        writer = self.service._prepared_writer
        self.assertIsNotNone(writer, "the pass never opened its writer")
        self.service.close()  # the NORMAL close — no fake crash
        table = self.store.load_table("print-key")
        self.assertIsNotNone(table, "the clean close published nothing")
        self.assertFalse(table["complete"], "the partial read as complete")
        prepared = [i for i, entry in enumerate(table["table"])
                    if entry[0] == self.state_cached]
        self.assertGreater(len(prepared), 0, "no layer survived the close")
        self.assertLess(len(prepared), 40, "the partial published as complete")
        for layer in prepared[:3]:
            self.assertEqual(self.store.read("print-key", table["table"], layer),
                             self._payload(layer),
                             "a checkpointed layer never round-tripped")
        # Session B: fresh stores, fresh service, the same identity
        # (the same process — a real disk-backed restart).
        from mpf.gcode.PreparedStore import PreparedCache
        self.store = PreparedCache(self._dir.name)
        module = self.qt.load("GCodeIndexService")
        self.service = module.GCodeIndexService(self.files, object(),
                                                prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = True
        self.service._wanted = True
        self._view(40)
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        fraction = self.service.plate_pass_fraction()
        self.assertGreater(fraction, 0.0,
                           "the resumed session lost the checkpoint")
        self.assertLess(fraction, 1.0,
                        "the resumed session read the partial as complete")
        # The N prepared layers are served from the store; the pass
        # resumes from the EMPTY slots and the final store completes.
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)

    def test_a_weak_re_extraction_never_adopts_the_old_prepared_geometry(self):
        # F3's required E2E: session 1 persists prepared geometry for
        # a WEAK identity (same name + size, modified 0, uuid A).
        # Session 2 is the re-extraction — same key, same layer
        # count, a fresh uuid and DIFFERENT geometry — and must
        # neither attempt the index restore nor adopt the old
        # prepared table: the rebuilt geometry replaces the old
        # representation byte for byte.
        module = self.qt.load("GCodeIndexService")
        self.files.identity.uuid = "uA"
        self.files.identity.modified = 0.0
        self.files.identity.size = 100
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        old_table = self.store.load_table("print-key")
        self.assertIsNotNone(old_table, "session 1 persisted nothing")
        old_payload = self.store.read("print-key", old_table["table"], 0)

        # Session 2: fresh stores, fresh service, the re-extracted
        # identity — same name + size, still no timestamp, a NEW uuid
        # and different geometry (40 motions vs 20) over the SAME
        # layer count, so a layer-count check cannot save us.
        self.service.close()
        from mpf.gcode.PreparedStore import PreparedCache
        self.store = PreparedCache(self._dir.name)
        self.service = module.GCodeIndexService(self.files, object(),
                                                prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = False  # the gate is genuinely exercised
        self.service._wanted = True
        self.files.identity.uuid = "uB"
        index_b = harness.make_index(layers=5, motions=40)
        self.service._view = self.qt.load("IndexView").IndexView(self.files.job_key, index_b)
        # The rejection is read BEFORE the pass is submitted: the old
        # file on disk holds five resolved layers, and the weak identity
        # may seed neither the table nor the coverage from them. Read
        # after _advance() the same assertion races the pass's own
        # completions, which resolve layers into that very set.
        self.service._prepared_open(self.files.identity)
        self.assertIsNone(self.service._prepared_table,
                          "the old prepared table was adopted")
        self.assertEqual(self.service._prepared_coverage, set(),
                         "the old prepared coverage leaked in")
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._advance()
        self.assertNotIn("restore", submitted,
                         "the weak re-extraction attempted the index restore")
        self.assertIsNone(self.service._prepared_table,
                          "the old prepared table was adopted")
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        # The pass ran to its own completion (the settle above waits for
        # its terminal signal): every layer resolved is the FRESH view's,
        # since a set carried over from the old table never reaches the
        # count without the pass.
        self.assertEqual(self.service._prepared_coverage, set(range(5)),
                         "the fresh pass did not resolve the coverage")
        # The published store now holds the NEW geometry's exact
        # bytes — the old representation was replaced, never served.
        new_table = self.store.load_table("print-key")
        self.assertIsNotNone(new_table)
        new_payload = self.store.read("print-key", new_table["table"], 0)
        self.assertNotEqual(new_payload, old_payload,
                            "the old geometry survived the rebuild")
        expected = self.qt.load("IndexTasks")._encode_layer(
            self.qt.load("IndexTasks")._prepare_layer(index_b, 0, lambda: False))
        self.assertEqual(new_payload, expected,
                         "the published layer is not the new geometry's bytes")

    def test_a_regenerated_uuid_with_strong_metadata_still_reuses(self):
        # The preserved strong case: the same filename/size/mtime and
        # a REGENERATED uuid — the reliable timestamp is the voucher,
        # so the persisted prepared store still takes the fast path
        # across sessions (no rebuild, no raw re-walk).
        module = self.qt.load("GCodeIndexService")
        self.files.identity.uuid = "uA"
        self.files.identity.modified = 1.0
        self.files.identity.size = 100
        self._view(5)
        self.service._prepared_open(self.files.identity)
        self._pump()
        self.assertTrue(self.service._prepared_saved)
        old_payload = self.store.read(
            "print-key", self.store.load_table("print-key")["table"], 0)

        self.service.close()
        from mpf.gcode.PreparedStore import PreparedCache
        self.store = PreparedCache(self._dir.name)
        self.service = module.GCodeIndexService(self.files, object(),
                                                prepared=self.store)
        self.addCleanup(self.service.close)
        self.service.bind(self.files.job_key)
        self.service._restored = True
        self.service._wanted = True
        self.files.identity.uuid = "uB"  # the regenerated token
        self._view(5)
        submitted = []
        original = self.service._submit
        self.service._submit = lambda kind, work, lease=None: (
            submitted.append(kind) or original(kind, work, lease))
        self.service._prepared_open(self.files.identity)
        self.service._adopt_prepared()
        self.assertTrue(self.service._prepared_saved,
                        "the strong metadata never took the fast path")
        self.assertEqual(self.service._full_next, 5)
        self.assertEqual(self.service.plate_pass_fraction(), 1.0)
        self.service._advance()
        self.assertNotIn("fullprep", submitted,
                         "the strong reuse rebuilt the whole store")
        table = self.store.load_table("print-key")
        self.assertEqual(self.store.read("print-key", table["table"], 0),
                         old_payload,
                         "the reused persistence serves different geometry")

    def test_the_byte_budgets_bind_the_ram_tiers(self):
        # : the packed tier is pure bytes,
        # the decoded tier holds its slot floor under pressure, and a
        # read refreshes recency.
        self.qt.load("GCodeIndexService")
        packed = self.qt.load("LayerCache")._ByteBoundedLru(max_bytes=200)
        packed.set(0, b"x" * 100, 100)
        packed.set(1, b"y" * 50, 50)
        self.assertIsNotNone(packed.get(0))  # the read refreshes recency
        packed.set(2, b"z" * 70, 70)  # 220 > 200: the least recent (1) goes
        self.assertIn(0, packed)
        self.assertNotIn(1, packed)
        decoded = self.qt.load("LayerCache")._ByteBoundedLru(max_bytes=100, min_entries=2)
        decoded.set("a", object(), 90)
        decoded.set("b", object(), 90)
        self.assertEqual(len(decoded), 2, "the floor evicted under pressure")
        decoded.set("c", object(), 90)
        self.assertEqual(len(decoded), 2)
        self.assertNotIn("a", decoded)
        self.assertIn("b", decoded)

    def test_the_lru_surfaces_its_keys_and_accounts_for_a_rewrite(self):
        # The inspection surface (keys, popitem) and a bulk update that
        # rewrites an entry: the old size leaves the total before the new
        # one is charged, or the budget drifts from the truth.
        self.qt.load("GCodeIndexService")
        lru = self.qt.load("LayerCache")._ByteBoundedLru(max_bytes=100000)
        lru.set(0, b"x" * 100, 100)
        lru.set(1, b"y" * 50, 50)
        self.assertEqual(list(lru.keys()), [0, 1])
        lru.update({0: b"z" * 300, 2: b"w" * 10})
        self.assertEqual(lru.total_bytes(), 300 + 50 + 10)
        key, value = lru.popitem()
        self.assertEqual((key, len(value)), (2, 10), "the newest entry was not the one dropped")
        self.assertEqual(lru.total_bytes(), 350)
        self.assertEqual(len(lru), 2)

    def test_the_hydrate_worker_names_the_layer_it_cannot_decode(self):
        # The worker's own contract: a packed entry the codec refuses is
        # NAMED failed — the latch is what stops every later poll
        # re-reading the same bytes — while the window's other layers
        # stay served. A decode that succeeded is presentation even when
        # the file can no longer supply that layer's motion arrays.
        self._compact_view(3, hydrated=())
        self.service._full_cache.set(0, self._payload(0), 40)
        self.service._full_cache.set(1, b"PPL1\xff", 5)  # truncated: the codec refuses it
        self.service._full_cache.set(2, self._payload(2), 40)
        self.service._hydrate = {0, 1, 2}
        captured = self._capture_submit()
        self.service._advance()
        self.assertEqual([kind for kind, _, _ in captured], ["hydrate"],
                         "the demand's window was not submitted as one task")
        failed, stash = captured[0][1]()
        self.assertEqual(failed, [1], "a refused entry was stashed or a served layer was failed")
        self.assertEqual(sorted(stash), [0, 2], "a decodable payload was dropped with the refusal")
        for layer in (0, 2):
            self.assertIsNotNone(stash[layer][1], "layer %d stashed no payload" % layer)
            self.assertNotIn(layer, failed)

    def test_a_layer_evicted_after_its_submit_is_reported_failed(self):
        # The retention window evicts a layer between the demand's own
        # submit and its worker (the live print's eviction runs on the
        # owner thread). The worker then has neither arrays nor a lease:
        # the layer is named failed, so the latch — never a silent empty
        # — decides whether the poll asks again.
        index = self._compact_view(3, hydrated=(0, 1, 2), followed=1)
        captured = self._capture_submit()
        self.service.request_hydration(1)  # the arrays all still held: no lease is asked
        self.assertEqual([kind for kind, _, _ in captured], ["hydrate"])
        self.assertIsNone(captured[0][2], "an array-less layer demanded the raw file")
        index.hydrated_layers.discard(1)
        self.assertEqual(captured[0][1](), ([1], {}))

    def test_an_evicted_layer_keeps_the_payload_the_cache_holds(self):
        # The same eviction with the packed tier holding the layer: the
        # decode is already paid for and the presentation stands — only
        # the split's arrays are missing — so the worker keeps the
        # payload instead of failing a layer it can still draw.
        index = self._compact_view(3, hydrated=(0, 1, 2), followed=1)
        self.service._full_cache.set(1, self._payload(1), 40)
        captured = self._capture_submit()
        self.service.request_hydration(1)
        self.assertIsNone(captured[0][2], "a packed payload demanded the raw file")
        index.hydrated_layers.discard(1)
        failed, stash = captured[0][1]()
        self.assertEqual(failed, [], "an unhydrated layer lost its decodable payload")
        self.assertIn(1, stash)
        self.assertIsNotNone(stash[1][1])

    def test_a_refused_encode_never_costs_the_layer_its_display(self):
        # The demand's own encode can fail (the codec's refusal): the
        # decoded payload still lands in the stash for the hot cache —
        # a refused encode is a compact-store miss, never a lost layer
        # and never a failed hydrate that would latch the window.
        self._view(3)
        self.service._hydrate = {1}
        captured = self._capture_submit()
        self.qt.load("GCodeIndexService")

        def refusing(payload):
            raise ValueError("refused")

        with harness.patch.object(self.qt.load("IndexTasks"), "_encode_layer", refusing):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["hydrate"])
            failed, stash = captured[0][1]()
        self.assertEqual(failed, [], "a refused encode failed the layer")
        self.assertEqual(sorted(stash), [1])
        encoded, decoded, ram_hit, size = stash[1]
        self.assertIsNone(encoded, "a refused encode was stashed as bytes")
        self.assertIsNotNone(decoded, "the refusal cost the layer its decoded payload")
        self.assertFalse(ram_hit)

    def test_a_compact_pass_reads_the_file_only_for_the_layer_with_no_source(self):
        # A compact index whose remaining layers are hydrated, RAM packed
        # or already resolved in the prepared table rebuilds its store
        # from what it holds — the walk asks for no lease at all. Only
        # the layer with no other source sends it to the file, and a
        # file that cannot serve that layer leaves the frontier ON it
        # rather than publishing a hole for it.
        #
        # The clock is held throughout: which source each layer is read
        # from is the policy under test, and it must not also need the
        # walk to fit inside 120 ms of a shared runner's real time. The
        # budget's own boundary is pinned by the two tests below.
        self._compact_view(4, hydrated=(0,))
        self.service._full_cache.set(1, self._payload(1), 40)
        writer = self.store.open_for_write("print-key", 4)
        self.store.append_uncacheable(writer, 2)
        self.store.finish_write(writer)
        self.service._prepared_open(self.files.identity)
        self.assertEqual(self.service._prepared_table[2][0], 2, "the refusal never loaded")
        captured = self._capture_submit()
        module = self.qt.load("GCodeIndexService")
        with harness.patch.object(module, "time", harness._HeldClock()):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["fullprep"])
            self.assertIsNotNone(captured[0][2], "the pass walked to the file with no lease")
            frontier, encoded, uncacheable = captured[0][1]()
        self.assertEqual(frontier, 3, "the pass walked past a layer it could not read")
        self.assertEqual(uncacheable, {2}, "the resolved refusal was re-walked")
        self.assertIn(0, encoded, "the hydrated layer was never prepared")
        table = self.service._prepared_writer["table"]
        self.assertEqual(table[1][0], self.state_cached,
                         "the RAM-packed layer never rode into the rebuild")

    def test_a_queued_pass_spends_its_budget_from_its_own_execution(self):
        # The budget belongs to the WALK, not to the queue. Spent from
        # the submission, a batch the pool served late arrived with its
        # whole slice already gone and returned the frontier it was
        # handed — the load-dependent 0 != 3 on the source-selection
        # pin. The delay is injected through the clock rather than
        # slept, so the claim is about where the budget starts and
        # never about how busy the runner was.
        self._compact_view(4, hydrated=(0,))
        self.service._full_cache.set(1, self._payload(1), 40)
        writer = self.store.open_for_write("print-key", 4)
        self.store.append_uncacheable(writer, 2)
        self.store.finish_write(writer)
        self.service._prepared_open(self.files.identity)
        captured = self._capture_submit()
        self.qt.load("GCodeIndexService")
        clock = harness._HeldClock()
        with harness.patch.object(self.qt.load("IndexTasks"), "time", clock):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["fullprep"])
            clock.spend(self.qt.load("IndexTasks")._FULL_PREP_BATCH_S * 1.25)
            frontier, _encoded, _uncacheable = captured[0][1]()
        self.assertEqual(frontier, 3,
                         "a queued pass spent its queue delay as its budget")

    def test_a_spent_budget_still_cuts_the_walk(self):
        # The other half of the same contract: the budget still ENDS the
        # walk. Starting it where the batch runs must not make a batch
        # unbounded, or a demand's task would queue behind a whole pass.
        # The slice is spent inside the first layer's own preparation,
        # so the cut lands on an exact frontier rather than on whatever
        # the runner's clock did meanwhile.
        self.qt.load("GCodeIndexService")
        self._compact_view(2000, hydrated=range(2000))
        self.service._prepared_open(self.files.identity)
        captured = self._capture_submit()
        clock = harness._HeldClock()
        real_prepare = self.qt.load("IndexTasks")._prepare_layer

        def spend_the_slice(index, layer, should_yield=None):
            payload = real_prepare(index, layer, should_yield)
            clock.spend(self.qt.load("IndexTasks")._FULL_PREP_BATCH_S)
            return payload

        with harness.patch.object(self.qt.load("IndexTasks"), "time", clock):
            self.service._advance()
            self.assertEqual([kind for kind, _, _ in captured], ["fullprep"])
            with harness.patch.object(self.qt.load("IndexTasks"), "_prepare_layer", spend_the_slice):
                frontier, _encoded, _uncacheable = captured[0][1]()
        self.assertEqual(frontier, 1, "the walk ran on past a spent budget")

    def test_a_restore_reraises_the_followers_window(self):
        # A demand that races the restore is dropped at the view-None
        # guard, and the coordinator's re-assertion carries the SAME
        # anchor the idempotency guard swallows — so the follower's own
        # window is re-raised HERE, the moment the view exists (the live
        # report: a future-layer scrub after a restore rendered nothing
        # and the slider stayed disabled).
        index = self.qt.load("GCodeIndex").build_index_from_bytes(
            b"".join(b";LAYER:%d\nG1 X0 Y0 E0.1\n" % layer for layer in range(5)))
        index.followed_layer = 3

        class Cache:
            def load(self, identity):
                return index

        self.service._cache = Cache()
        self.service._restored = False
        self.files.lease = lambda: None  # the raw lease is unavailable
        submitted = []
        demanded = []
        original = self.service._submit

        def capture(kind, work, lease=None):
            submitted.append(kind)
            if kind == "hydrate":
                # The window at submission: the follower's own layer is
                # the current demand, its two ghosts stay queued.
                demanded.append((set(self.service._hydrating), set(self.service._hydrate)))
            return original(kind, work, lease)
        self.service._submit = capture
        for _ in range(400):
            self.service._advance()
            self.qt.events(5)
            if self.service._view is not None:
                break
        self.assertIn("restore", submitted)
        self.assertEqual(self.service._view._index, index, "the restore never installed the view")
        self.assertTrue(demanded, "the restore re-raised no demand at all")
        self.assertEqual(demanded[0], ({3}, {2, 4}),
                         "the restored follower's window was never re-raised")

    def test_a_dense_layer_yields_and_an_abandoned_walk_publishes_nothing(self):
        # The reader's own gate, and the guarantee that rides it. The
        # asks are counted, never timed: the gate is consulted on a line
        # counter, so how often it was reached is a structural fact
        # about the walk rather than a reading of the machine.
        path = self._dense_layer_file()
        index = harness.build_index_from_file(path, compact=True)
        lines = 60000
        self.assertEqual(len(index.ranges), 1, "the fixture is not one layer")
        self.assertNotIn(0, index.hydrated_layers)
        module = self.qt.load("IndexHydrator")
        asked = []
        fires = []
        real = self.qt.load("IndexWork").passive_yield

        def recorded(now, last):
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                fires.append(now)
            return updated

        stops = []

        def stop():
            stops.append(1)
            return len(stops) >= 2          # abandon on the second gate

        with harness.patch.object(module, "passive_yield", recorded):
            with self.assertRaises(self.qt.load("IndexWork").HydrationYield):
                module.hydrate_layer_from_file(index, path, 0, should_stop=stop)
        # A heartbeat sibling for this walk was withdrawn rather than
        # shipped: on this platform the interpreter's own 5 ms switch
        # interval releases the GIL with the walk's gate disabled
        # (measured), so it could not carry the regression here, and
        # its fixture build came back with NO layers intermittently
        # under the parallel suite — 12/12 correct alone, so the
        # condition needs contention to appear. That signature is
        # recorded as an open question about build_index_from_file
        # rather than papered over with a retry or a skip.
        # The abandoned walk stops early by design, so its count is a
        # floor; the walk that matters is the one below.
        self.assertGreaterEqual(len(asked), 8, "the dense walk never gated")
        self.assertGreaterEqual(len(fires), 2)
        # Prompt, and counted rather than timed: the walk abandons on
        # the consultation that first answers True, not several gates
        # later.
        self.assertEqual(len(stops), 2,
                         "the walk consulted the stop %d times" % len(stops))
        # Abandoned BEFORE the commit: the layer keeps every array it
        # had, which is none of them, and nothing is latched as failed.
        self.assertNotIn(0, index.hydrated_layers,
                         "an abandoned hydration marked the layer hydrated")
        if index.motion_x:
            self.assertEqual(len(index.motion_x[0]), 0,
                             "an abandoned hydration published motion arrays")
        # Non-vacuity, and the structural claim: the same layer walked
        # to the end asks its gate throughout — once per 64 lines, which
        # is a property of the walk rather than of the machine.
        before = len(asked)
        with harness.patch.object(module, "passive_yield", recorded):
            self.assertTrue(module.hydrate_layer_from_file(index, path, 0),
                            "the fixture's layer never hydrates at all")
        self.assertIn(0, index.hydrated_layers)
        self.assertGreater(len(index.motion_x[0]), 1000,
                           "the control walk published no geometry")
        self.assertGreaterEqual(
            len(asked) - before, lines // 64 - 64,
            "the walk to the end asked its gate %d times" % (len(asked) - before))

    def test_the_scan_asks_its_gate_throughout_its_walk(self):
        # The scan's own gate, on the same structural claim the
        # hydration walk is held to. The build loop is a per-line parse
        # of a file that can be megabytes long, and it hands the
        # interpreter back only where it ASKS: a beat coarser than the
        # wall-clock period lets a whole file's worth of lines run
        # between hand-backs, and the gate cannot fire more often than
        # it is consulted. Measured on a 300k-line single layer: 26.5 ms
        # between hand-backs on the old 4096-line beat against 6.7 ms
        # with the 64-line one (p95 25.0 -> 6.4 ms), for ~1% more scan
        # time. The asks are counted rather than timed, so the claim is
        # a property of the walk; the hand-backs are printed as
        # evidence, since a descheduled process cannot hand back a GIL
        # it does not hold.
        path = self._dense_layer_file()
        lines = 60000
        module = self.qt.load("GCodeIndex")
        asked = []
        fires = []
        real = self.qt.load("IndexWork").passive_yield

        def recorded(now, last):
            asked.append(now)
            updated = real(now, last)
            if updated != last:
                fires.append(now)
            return updated

        with harness.patch.object(module, "passive_yield", recorded):
            index = module.build_index_from_file(path, compact=True)
        self.assertTrue(index.ranges, "the fixture built no layers")
        self.assertGreaterEqual(
            len(asked), lines // 64 - 64,
            "the scan walked %d lines and asked its gate %d times"
            % (lines, len(asked)))
        print("scan gate evidence: %d asks, %d hand-backs over %d lines"
              % (len(asked), len(fires), lines))


