"""Executable runtime index composition contracts."""
from tests import composed_runtime_support as harness

class ComposedComponentTests(harness.ComposedComponentTests):
    def test_no_borrowed_cura_file_released_by_unrelated_completion_or_shutdown(self):
        with harness.tempfile.TemporaryDirectory() as directory:
            path = harness.os.path.join(directory, "print.gcode")
            harness.pathlib.Path(path).write_text("G1 X0")
            releases = []
            lease = self.qt.load("RemoteFileService").FileLease(path, releases.append)
            self.parts.cura._view = object()  # the load preflight needs a build volume
            self.assertTrue(self.parts.cura.load(lease))
            self.app.fileCompleted.emit(harness.os.path.join(directory, "unrelated.stl"))
            self.assertEqual(releases, [])
            self.assertTrue(self.parts.cura.loading)
            self.parts.cura.close()
            self.assertEqual(releases, [path])  # shutdown releases, never drops
            self.app.fileCompleted.emit(path)
            self.assertEqual(releases, [path])

    def test_rebind_retires_file_but_lease_keeps_it_alive(self):
        files = self.parts.files
        files.bind(("old.gcode", 100, 1))
        directory = harness.tempfile.mkdtemp(dir=files._root)
        path = harness.os.path.join(directory, "old.gcode")
        harness.pathlib.Path(path).write_text("G1 X0")
        files._path = path
        lease = files.lease()
        files.bind(("new.gcode", 100, 2))
        self.assertTrue(harness.os.path.exists(path))
        lease.close()
        self.assertFalse(harness.os.path.exists(path))
        lease.close()

    def test_cache_restore_is_off_ui_thread_and_request_queue_is_bounded(self):
        service, files = self.parts.index, self.parts.files
        key = ("part.gcode", 100, 1)
        files.bind(key)
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 100, modified=1)
        service.bind(key)
        entered, release = harness.threading.Event(), harness.threading.Event()
        threads = []
        def load(identity):
            threads.append(harness.threading.get_ident())
            entered.set()
            release.wait(2)
            return None
        self.addCleanup(release.set)
        with harness.patch.object(service._cache, "load", load), harness.patch.object(files, "request_file"):
            service.request()
            self.assertTrue(entered.wait(1))
            for _ in range(50): service.request()
            self.assertEqual(len(threads), 1)
            self.assertNotEqual(threads[0], harness.threading.get_ident())
            service.bind(None)
            release.set()
            for _ in range(50):
                self.qt.events(5)
                if not service._busy: break
        self.assertIsNone(service.view)

    def test_index_view_does_not_expose_motion_arrays(self):
        self.qt.load("GCodeIndexService")
        index = self.qt.load("MotionIndex").LayerMotionIndex(ranges=[(0, 100)], current_layer_map={1: 0})
        view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        self.assertEqual(view.layer_at(50), 0)
        self.assertIsInstance(view.ranges, tuple)
        with self.assertRaises(TypeError): view.current_layer_map[2] = 1
        self.assertFalse(hasattr(view, "motion_offsets"))

    def test_a_restored_index_serves_a_future_layer_scrub_without_a_prepared_table(self):
        # The live report: after a restart restores the index but the
        # prepared table is missing (the previous session quit before
        # the pass published it), scrubbing to a FUTURE layer
        # rendered only the grid and the layer-progress bar stayed
        # disabled — a reindex was the only fix. The restored view
        # must demand the raw file for the far layer and serve it
        # once the file arrives — never latch, never stall.
        service, files = self.parts.index, self.parts.files
        key = ("part.gcode", 100, 1)
        files.bind(key)
        identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity(
            "part.gcode", 100, modified=1)
        files._identity = identity
        service.bind(key)
        service._restored = True
        service._wanted = True
        layers = b"".join(
            b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\nG1 X3 Y3 E1\n" % layer
            for layer in range(10))
        target = self.plant_download(files, layers)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(target, compact=True)
        # The previous session saved the index but never published a
        # prepared table.
        service._cache.save(identity, index)
        # The restart: a fresh boot restores through the real branch;
        # the session's downloaded file is gone with the old process.
        service._view = None
        service._restored = False
        service._prepared.table = None
        service._prepared.identity = None
        files._path = None
        requests = []
        with harness.patch.object(files, "request_file", lambda *a, **k: requests.append(1)):
            service._advance()
            for _ in range(200):
                self.qt.events(5)
                if service._view is not None and not service._busy:
                    break
        self.assertIsNotNone(service._view,
                            "the restore never installed the view")
        # The scrub to a future layer: the raw demand must re-request
        # the file (no prepared table, no hydrated arrays), and the
        # layer must land once the download arrives.
        service.set_manual_anchor(8)
        for _ in range(50):
            self.qt.events(5)
        self.assertEqual(requests, [1],
                         "the future-layer demand never re-requested the file")
        files._path = target
        files.changed.emit()
        for _ in range(400):
            self.qt.events(5)
            if service._presentation_source(8) == "decoded":
                break
        self.assertEqual(service._presentation_source(8), "decoded",
                         "the restored view never served the future layer")
        self.assertNotIn(8, service._failed_hydrate,
                         "the future layer latched instead of hydrating")

    def test_the_invalidate_retires_the_active_prepared_writer(self):
        # The review's cache-clear finding: invalidate must RETIRE
        # (freeze and checkpoint) the active prepared writer, not
        # discard the reference — a bare drop would leak the writer's
        # temp file and leave a future append racing the deletion.
        service = self.parts.index
        writer = {"retired": False}
        service._prepared.writer = writer
        with harness.patch.object(service._prepared, "suspend") as suspend:
            service.invalidate()
        self.assertTrue(writer["retired"], "the writer was never frozen")
        self.assertEqual(suspend.call_count, 1,
                         "the writer was never suspended/checkpointed")
        self.assertIsNone(service._prepared.writer,
                          "the writer reference was dropped without retiring")

    def test_the_cache_clear_invalidate_drops_every_index_listener_state(self):
        # The live ruling: once the cache-clear wipes the backing
        # files, every listener must believe the index does not exist —
        # the view, the prepared tables, the decoded and full caches,
        # the manual anchor and the memos all go, the changed signal
        # fires, and nothing rebuilds on its own.
        self.qt.load("GCodeIndexService")
        service = self.parts.index
        index = self.qt.load("MotionIndex").LayerMotionIndex(
            ranges=[(0, 100)], current_layer_map={1: 0})
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        service._job = ("part.gcode", 100, 1)
        service._wanted = service._restored = service._save = True
        service._prepared.table = {"layer": 0}
        service._prepared.identity = ("part.gcode", 100, 1)
        service._prepared.complete = True
        service._prepared.coverage = {0}
        service._full_cache.set("layer", object(), 16)
        service._decoded_lru.set("layer", object(), 16)
        service._decoded_lru.protected = {3}
        service._decoded_pins = {3: 16}
        service._decoded_sizes = {3: 16}
        service._plate_layers_memos = {("part.gcode", 100, 1): object()}
        service._manual_anchor = 4
        service._manual_split = 12
        service._split_tracker.begin(("part.gcode", 100, 1), 4)
        service._split_tracker.floor = 7
        service._split_tracker.refined = 9
        service._objects._visited = {1, 2}
        service._objects._visited_upto = 5
        service._objects._visited_settled = frozenset({1})
        service._objects._visited_key = ("part.gcode", 100, 1)
        service._hydrate = {4}
        service._hydrating = 4
        service._failed_hydrate = {2}
        service._progress = 0.5
        emissions = []
        service.changed.connect(lambda: emissions.append(1))

        service.invalidate()

        self.assertEqual(emissions, [1], "the invalidate never announced itself")
        self.assertIsNone(service.view)
        self.assertIsNone(service._job)
        self.assertFalse(service._wanted or service._restored or service._save)
        self.assertIsNone(service._prepared.table)
        self.assertIsNone(service._prepared.identity)
        self.assertFalse(service._prepared.complete)
        self.assertEqual(service._prepared.coverage, set())
        self.assertEqual(len(service._full_cache), 0)
        self.assertEqual(len(service._decoded_lru), 0)
        self.assertEqual(service._decoded_lru.protected, set())
        self.assertEqual(service._decoded_pins, {})
        self.assertEqual(service._decoded_sizes, {})
        self.assertEqual(service._plate_layers_memos, {})
        self.assertIsNone(service._manual_anchor)
        self.assertIsNone(service._manual_split)
        self.assertIsNone(service._split_tracker.floor)
        self.assertIsNone(service._split_tracker.refined)
        self.assertIsNone(service._split_tracker.key)
        self.assertEqual(service._objects._visited, set())
        self.assertEqual(service._objects._visited_upto, -1)
        self.assertEqual(service._objects._visited_settled, frozenset())
        self.assertIsNone(service._objects._visited_key)
        self.assertEqual(service._hydrate, set())
        self.assertIsNone(service._hydrating)
        self.assertEqual(service._failed_hydrate, set())
        self.assertIsNone(service._progress)

    def test_failed_hydration_is_latched_until_a_new_file_arrives(self):
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        handle = harness.tempfile.NamedTemporaryFile(suffix=".gcode", delete=False)
        handle.write(b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n")
        handle.close()
        self.addCleanup(harness.os.remove, handle.name)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(handle.name, compact=True)
        self.qt.load("GCodeIndexService")
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        files._path = harness.os.path.join(files._root, "job-1", "part.gcode")
        files._want_file = True

        def wait_idle():
            for _ in range(200):
                self.qt.events(5)
                if not service._busy and not service._hydrate: break
        with harness.patch.object(self.qt.load("IndexTasks"), "hydrate_layer_from_file", return_value=False) as hydrate:
            service.request_hydration(0)
            wait_idle()
            # A second poll re-requests the same layer; the latch must stop
            # the whole-file re-read.
            service.request_hydration(0)
            wait_idle()
        self.assertEqual(hydrate.call_count, 2)  # layers 0 and 1, once each
        self.assertEqual(service._failed_hydrate, {0, 1})
        self.assertEqual(service._busy, "")
        # A new file invalidates the latch: hydration is attempted again.
        files.changed.emit()
        with harness.patch.object(self.qt.load("IndexTasks"), "hydrate_layer_from_file", return_value=True) as hydrate2:
            service.request_hydration(0)
            wait_idle()
        self.assertEqual(hydrate2.call_count, 2)
        self.assertEqual(service._failed_hydrate, set())

    def test_the_background_pass_caches_layers_outside_both_windows(self):
        # A 10-layer compact index with the live
        # and manual anchors fixed — the pass used to reach the end
        # while the cache held only the two windows (each background
        # hydrate was evicted by the retention before its prepare).
        # Every layer must land in the cache and stay available after
        # the arrays' eviction.
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity(
            "part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        layers = b"".join(
            b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\nG1 X3 Y3 E1\n" % layer
            for layer in range(10))
        target = self.plant_download(files, layers)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(target, compact=True)
        self.qt.load("GCodeIndexService")
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        files._path = target
        files._want_file = True
        # The live print stands on layer 1; the follower is frozen on 9.
        index.followed_layer = 1
        index.manual_anchor = 9
        service._manual_anchor = 9
        service._advance()  # the poll that starts the pass chain

        def wait_pass():
            for _ in range(800):
                self.qt.events(5)
                if service._full_next >= len(index.ranges) and not service._busy:
                    break
        wait_pass()
        self.assertGreaterEqual(service._full_next, len(index.ranges),
                                "the pass never reached the end")
        for layer in range(10):
            self.assertIn(layer, service._full_cache,
                          "layer %d outside both windows never cached" % layer)
            raw = service._full_cache[layer]
            self.assertEqual(self.qt.load("IndexTasks")._decode_layer(raw)["motions"], 3,
                             "layer %d's cache entry lost its geometry" % layer)
        # The retention still holds the arrays down to the two windows
        # plus the last hydrated layer's own — the cache, not the
        # hydration, serves the far layers.
        self.assertEqual(index.hydrated_layers, {0, 1, 2, 8, 9})

    def test_the_job_bar_band_tracks_the_prepared_share(self):
        # The optimisation band's value: the share of layers the
        # prepared store holds — the cache is the evidence, so a
        # latched failure keeps the band below 100% . None without a view.
        service = self.parts.index
        service.bind(("part.gcode", 100, 1))
        self.assertIsNone(service.plate_pass_fraction())
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_bytes(b"".join(
            b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\n" % layer
            for layer in range(10)))
        self.qt.load("GCodeIndexService")
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        self.assertEqual(service.plate_pass_fraction(), 0.0)
        service._full_cache.update({0: b"x", 1: b"x", 2: b"x", 3: b"x"})
        self.assertAlmostEqual(service.plate_pass_fraction(), 0.4)

    def test_the_render_cache_is_bounded_and_generation_isolated(self):
        model = self.monitor()
        surface = model.plate_renderer._surfaces["popover"]
        payload = {"classes": {}, "travels": [], "travelStarts": [],
                   "travelEnds": [], "motions": 1}
        for layer in range(10):
            model.plate_renderer._qt_layer(surface, payload, layer)
        self.assertLessEqual(len(surface.layers), 6)
        self.assertNotIn(0, surface.layers,
                         "the oldest render object never evicted")
        # A new job clears every surface's cache: the old geometry
        # can never answer the new print's window.
        model._observe_follower_job("new-job")
        self.assertEqual(surface.layers, {})
        self.assertEqual(model._plate_qt_job, "new-job")

    def test_file_backed_writer_and_duplicate_preparation_ownership(self):
        module = self.qt.load("CuraOutputWriter")
        config = self.config_type(url="http://printer-a", upload_dialog=True)
        writer = module.CuraOutputWriter(self.app)
        prepared = writer.prepare(config, "part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(harness.pathlib.Path(prepared.path).read_text(encoding="utf-8"), "G1 X0\n")
        upload = self.qt.load("UploadController").UploadController(self.follower.client, "A", self.follower.current_printer_identity)
        self.addCleanup(upload.abort)
        upload.begin(config, "part.gcode")
        upload.prepared(prepared)
        second = writer.prepare(config, "second.gcode")
        with self.assertRaises(RuntimeError): upload.prepared(second)
        self.assertFalse(harness.os.path.exists(second.path))
        self.assertTrue(harness.os.path.exists(prepared.path))

    def test_macro_definitions_are_cached_until_config_changes(self):
        model = self.monitor()
        data = model._data
        data._update(objects=("gcode_macro TEST",), auxiliary={"configfile": {"config": {
            "gcode_macro TEST": {"gcode": "{{ params.COUNT|default(2)|int }}"}}}})
        controls_module = self.qt.load("MonitorControls")
        with harness.patch.object(controls_module, "infer_macro_parameters", wraps=controls_module.infer_macro_parameters) as infer:
            model.macroParameterDefinitions("TEST")
            model.macroParameterDefinitions("TEST")
            data._update(core={"print_stats": {"state": "standby"}})
            model.macroParameterDefinitions("TEST")
            self.assertEqual(infer.call_count, 1)

    def test_file_backed_multipart_upload_and_terminal_ordering(self):
        received = []
        class Handler(harness.PipeSafeHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                body = b'{"result":{"item":{"path":"part.gcode"}}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        harness.threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = "http://127.0.0.1:" + str(server.server_port)
        client = self.qt.load("MoonrakerClient").MoonrakerClient()
        client.configure(url, "", 750)
        self.addCleanup(client.stop)
        config = self.config_type(url=url, upload_dialog=False, upload_start_print=False)
        module = self.qt.load("MoonrakerOutputDevice")
        device = module.MoonrakerOutputDevice(self.app, "A", client=client, config=lambda: config,
            apply_config=lambda value: None, active_identity=lambda: ("A", "A"))
        self.addCleanup(device.deactivate)
        terminal, reentrant = [], []
        def success(_device):
            try: device.requestWrite(None)
            except module.OutputDeviceError.DeviceBusyError: reentrant.append("blocked")
        device.writeSuccess.connect(success)
        device.writeFinished.connect(terminal.append)
        device.requestWrite(None)
        for _ in range(200):
            if terminal: break
            self.qt.events(10)
        self.assertEqual(terminal, [device])
        self.assertEqual(reentrant, ["blocked"])
        self.assertEqual(len(received), 1)
        self.assertIn(b"G1 X0", received[0])
        self.assertIn(b'name="root"', received[0])
        self.assertFalse(device._upload.busy)

    def test_real_download_index_and_cura_load_pipeline(self):
        content = (harness.pathlib.Path(__file__).parent / "fixtures" / "gcode" / "cura.gcode").read_bytes()
        status = self.status(layer=2)
        status["virtual_sdcard"]["file_size"] = len(content)
        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                if self.path.startswith("/server/files/gcodes/"):
                    body = content
                elif self.path.startswith("/server/files/metadata"):
                    body = harness.json.dumps({"result": {"size": len(content), "modified": 1}}).encode()
                else:
                    body = harness.json.dumps({"result": {"status": status}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        harness.threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        app = self.qt.Application()
        follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.addCleanup(follower.deinitialize)
        follower.apply_printer_config(self.config_type(url="http://127.0.0.1:" + str(server.server_port), enabled=True, path_follow=True, feed_mode="http"))
        parts = follower._runtime
        # An active-but-unloaded print pulls nothing: the metadata and
        # index serve the Preview, which needs the print loaded in Cura.
        parts.cura._view = object()  # the load preflight needs a build volume
        parts.coordinator.request_load()
        for _ in range(100):
            if app.loaded_paths: break
            self.qt.events(10)
        self.assertEqual(app.loaded_paths, [parts.files.path])
        self.assertTrue(parts.cura.loading)
        # The load gives Cura the toolpath; only then does the
        # metadata/index pull start and the index build.
        app.controller.view = harness.SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
        app.controller.activeViewChanged.emit()
        for _ in range(300):
            if parts.index.view is not None: break
            self.qt.events(10)
        self.assertIsNotNone(parts.index.view)
        self.assertEqual(len(parts.index.view.ranges), 3)
        self.assertEqual(harness.pathlib.Path(parts.files.path).read_bytes(), content)
        app.fileCompleted.emit(parts.files.path)
        self.assertFalse(parts.cura.loading)

    def test_the_mini_and_open_popover_become_available_after_the_index_builds(self):
        # The live regressions' common root: an index-hydrated layer
        # must still DEMAND its presentation payload — the mini's
        # placeholder and the popover's Loading layer… clear only
        # when the decoded current actually lands, with no other
        # user action.
        content = (harness.pathlib.Path(__file__).parent / "fixtures" / "gcode" / "cura.gcode").read_bytes()
        status = self.status(layer=2)
        status["virtual_sdcard"]["file_size"] = len(content)
        class Handler(harness.PipeSafeHandler):
            def do_GET(self):
                if self.path.startswith("/server/files/gcodes/"):
                    body = content
                elif self.path.startswith("/server/files/metadata"):
                    body = harness.json.dumps({"result": {"size": len(content), "modified": 1}}).encode()
                else:
                    body = harness.json.dumps({"result": {"status": status}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = harness.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        harness.threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        app = self.qt.Application()
        follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(app)
        self.addCleanup(follower.deinitialize)
        follower.apply_printer_config(self.config_type(url="http://127.0.0.1:" + str(server.server_port), enabled=True, path_follow=True, feed_mode="http"))
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(app, follower)
        output.start()
        self.addCleanup(output.stop)
        model = output._current.activePrinter
        # The initially expanded popover and the mini's section.
        model.setFollowerPopoverOpen(True)
        model.setSectionExpanded("plateprogress", True)
        parts = follower._runtime
        parts.cura._view = object()
        parts.coordinator.request_load()
        for _ in range(100):
            if app.loaded_paths: break
            self.qt.events(10)
        self.assertEqual(app.loaded_paths, [parts.files.path])
        self.assertTrue(parts.cura.loading)
        app.controller.view = harness.SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
        app.controller.activeViewChanged.emit()
        for _ in range(300):
            if parts.index.view is not None: break
            self.qt.events(10)
        self.assertIsNotNone(parts.index.view, "the index never built")
        # The presentation demand fires from the hydrated index (no
        # decoded payload exists yet) and the availability follows
        # the decoded current, not the hydration.
        for _ in range(400):
            self.qt.events(10)
            if model.plateProgressAvailable and model.plateLiveAvailable:
                break
        self.assertTrue(model.plateProgressAvailable,
                        "the open popover stayed on Loading layer…")
        self.assertTrue(model.plateLiveAvailable,
                        "the mini stayed on the build-index placeholder")
        self.assertEqual(model.plateProgressReason, "",
                         "the popover's reason never cleared")
        self.assertIsNotNone(model._values.get("plateLayers", {}).get("current"),
                             "the popover's current never landed")
        self.assertIsNotNone(model._values.get("plateLiveLayers", {}).get("current"),
                             "the mini's current never landed")

    def test_file_manager_paging_with_resident_data(self):
        # A live report: the page carousel stopped. This
        # exercises the REAL model end to end — walk, publish, page
        # slice — with 30 resident files.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": f"f{i:02d}.gcode", "modified": 10.0, "size": 100}
                              for i in range(30)],
                    "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()

        def rows():
            value = model.fileManagerRows
            return value.value() if hasattr(value, "value") else list(value)
        self.assertEqual(len(rows()), 25)
        self.assertEqual(rows()[0]["name"], "f00.gcode")
        # No print_start_time in the metadata means the file has
        # genuinely never printed — the status says so even while
        # the history window is only partially loaded (the
        # live ruling).
        self.assertEqual(rows()[0]["status"], "Never printed")
        self.assertEqual(model.fileManagerPageCount, 2)
        model.setFilePage(2)
        self.assertEqual(model.fileManagerPageIndex, 2)
        self.assertEqual(len(rows()), 5)
        self.assertEqual(rows()[0]["name"], "f25.gcode")

    def test_file_manager_print_confirmation_flow(self):
        # Snapshot 2: the confirmation carries the row's payload and
        # the printer's name; confirming POSTs the root-exclusive
        # print/start; the print_stats transition is the success (the
        # POST reply is never it — round-2 D4).
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "benchy.gcode", "modified": 10.0, "size": 100,
                               "estimated_time": 6120.0, "filament_total": 12340.0}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileRequestPrint("benchy.gcode")
        confirm = model.filePrintConfirm
        if hasattr(confirm, "value"):
            confirm = confirm.value()
        self.assertEqual(confirm["name"], "benchy.gcode")
        self.assertTrue(confirm["printerName"])
        # The fixture's mock carries no server state and no homing:
        # the readiness line must say so (a live report —
        # an unhomed printer failed silently).
        self.assertIn("readyText", confirm)
        self.assertFalse(confirm["homed"])
        self.assertTrue(confirm["readyText"])
        self.connect()
        model.fileConfirmPrint()
        self.assertEqual(model.filePrintConfirm, "")
        # The confirm dismisses the popup immediately — never a wait
        # on the watchdog or the transition (the ruling).
        self.assertFalse(model.fileManagerOpen)
        posts = [r for r in self.transport.requests if "print/start" in r.path]
        self.assertEqual(len(posts), 1)
        # The composed fixture's binding carries a base URL: the full
        # form (the service test pins the relative variant).
        self.assertEqual(posts[0].path, "http://printer-a/printer/print/start?filename=benchy.gcode")
        # The watchdog: a start that never transitions explains
        # itself in the console, and the verdict NEVER rewrites the
        # observed printer state (a live print must not read
        # "cancelled" — the e-stop alone owns that assumption).
        module = self.qt.load("PrintStartOwner")
        # A negative sentinel: the watchdog has already expired. A 0.0
        # one does not say that on a clock that ticks — Windows' wall
        # clock advances every ~15.6 ms, so an attempt stamped in the
        # current tick still reads a zero elapsed.
        with harness.patch.object(module.PrintStartOwner, "FILE_PRINT_START_TIMEOUT_S", -1.0):
            model._publish()
        self.assertIsNone(model._file_manager.print_attempt)
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("Print start failed" in line for line in lines))
        self.assertIn("Print start failed", model.actionStatus)
        self.assertFalse(self.follower.client._session.state.assume_print_stopped)
        # The transition itself is the success (round-2 D4: the POST
        # reply is never it): the filename match with a live state
        # clears immediately, even with zero progress — the
        # pre-extrusion window pins print_duration at 0.0.
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "benchy.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileRequestPrint("benchy.gcode")
        self.connect()
        model.fileConfirmPrint()
        self.assertIsNotNone(model._file_manager.print_attempt)
        self.deliver(self.status(filename="benchy.gcode", state="printing", position=0))
        self.qt.events()
        self.assertIsNone(model._file_manager.print_attempt)
        # The print is live: the popup steps aside (the
        # live request — the monitor view returns).
        self.assertFalse(model.fileManagerOpen)
        # The host's own error verdict surfaces with its words.
        # The printer stands by first — a confirm against a stale
        # "printing" snapshot would honestly read as success.
        self.deliver(self.status(filename="other.gcode", state="standby", position=0))
        self.qt.events()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "benchy.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileRequestPrint("benchy.gcode")
        self.connect()
        model.fileConfirmPrint()
        self.assertIsNotNone(model._file_manager.print_attempt)
        status = self.status(filename="benchy.gcode", state="error", position=0)
        status["print_stats"]["message"] = "Not homed"
        self.deliver(status)
        self.qt.events()
        # A transient error holds the attempt — a cold-start error
        # must not read as a failed start while the job carries on.
        self.assertIsNotNone(model._file_manager.print_attempt)
        module = self.qt.load("PrintStartOwner")
        # Already expired, by the same sentinel rule as above.
        with harness.patch.object(module.PrintStartOwner, "FILE_PRINT_START_TIMEOUT_S", -1.0):
            model._publish()
        self.assertIsNone(model._file_manager.print_attempt)
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("Not homed" in line for line in lines))

    def test_file_request_print_pulls_the_rows_thumbnail(self):
        # The confirmation's large thumbnail (the live
        # request): the page-driven cache covers visible rows only,
        # so opening the dialog for an OFF-PAGE row (the Recents
        # case) must fetch that row's thumbnail explicitly.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                files = [{"filename": f"f{i:02d}.gcode", "modified": 10.0, "size": 100}
                         for i in range(30)]
                # Oldest sorts last: page 2, outside the page cache.
                files.append({"filename": "benchy.gcode", "modified": 1.0, "size": 100,
                              "thumbnails": [{"width": 300, "height": 300,
                                              "relative_path": ".thumbs/benchy-300x300.png"}]})
                request.callback({"result": {
                    "files": files, "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        with harness.patch.object(model._file_manager._thumbnails, "_fetch") as fetch:
            model.fileRequestPrint("benchy.gcode")
            fetch.assert_called_once()
            # The dialog asks for the LARGE variant (the grid cells
            # fetch the small one).
            self.assertEqual(fetch.call_args.args, (
                "benchy.gcode", "gcodes", ".thumbs/benchy-300x300.png", True))

    def test_file_delete_and_rename_round_trip_through_the_model(self):
        # Snapshot 3: selection → delete confirmation → DELETE at the
        # root-inclusive endpoint; rename request → live collision →
        # overwrite confirm → move with both parts in the body.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100},
                              {"filename": "b.gcode", "modified": 9.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.toggleFileSelection("a.gcode")
        model.fileRequestDelete()
        confirm = model.fileDeleteConfirm
        if hasattr(confirm, "value"):
            confirm = confirm.value()
        self.assertEqual(confirm["count"], 1)
        self.assertEqual(confirm["first"], "a.gcode")
        self.assertEqual(confirm["blocked"], 0)
        model.fileConfirmDelete()
        deletes = [r for r in self.transport.requests if r.method == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0].path, "http://printer-a/server/files/gcodes/a.gcode")
        model.fileRequestRename("b.gcode")
        model.filePreviewRename("a.gcode")  # collides with the resident row
        self.assertTrue(model.fileRenameConflict)
        model.fileConfirmRename()
        moves = [r for r in self.transport.requests if "server/files/move" in r.path]
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].options["body"],
                         {"source": "gcodes/b.gcode", "dest": "gcodes/a.gcode"})

    def test_file_upload_collision_asks_before_overwriting(self):
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileUpload("/tmp/a.gcode")
        confirm = model.fileUploadConfirm
        if hasattr(confirm, "value"):
            confirm = confirm.value()
        self.assertEqual(confirm["filename"], "a.gcode")
        self.assertEqual(confirm["path"], "/tmp/a.gcode")
        model.fileConfirmUpload()
        self.assertEqual(model.fileUploadConfirm, "")
        # The overwrite upload ran; under the scripted transport (no
        # raw network) it refuses gracefully — the real-socket test
        # proves the multipart itself.
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("Upload refused" in line for line in lines))

    def test_file_upload_progress_flow_through_the_popup(self):
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [], "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        with harness.patch.object(model._file_manager, "upload_file", return_value=True):
            model.fileUpload("/tmp/bench.gcode")
        progress = model.fileUploadProgress
        if hasattr(progress, "value"):
            progress = progress.value()
        self.assertEqual(progress["name"], "bench.gcode")
        self.assertEqual(progress["state"], "uploading")
        self.assertEqual(progress["percent"], 0)
        # The service signals drive the transitions (the scripted
        # transport cannot run the multipart — emit as the service
        # would; the real-socket test proves the signals' source).
        model._file_manager.uploadProgress.emit(42)
        model._file_manager.uploadFinished.emit(True, "bench.gcode")
        progress = model.fileUploadProgress
        if hasattr(progress, "value"):
            progress = progress.value()
        self.assertEqual(progress["state"], "done")
        self.assertEqual(progress["percent"], 100)
        model.fileUploadDismiss()
        self.assertEqual(model.fileUploadProgress, "")

    def test_file_manager_refusal_note_reaches_the_popup_and_console(self):
        model = self.monitor()
        model.openFileManager()
        refusal = "Upload refused: simulated upload refusal"
        model._file_manager.note.emit(refusal)
        self.assertEqual(model.fileManagerNote, refusal)
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any(refusal in line for line in lines))
        model.openFileManager()
        self.assertEqual(model.fileManagerNote, "")

    def test_watchdog_holds_through_a_same_file_reprint(self):
        # The adversarial round's repro: Klipper never clears the
        # filename, so a re-print arms against the previous job's
        # stale terminal state. The verdict is the state CHANGE,
        # never the filename alone.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [],
                }}, None)
                break
        self.qt.events()
        self.deliver(self.status(filename="a.gcode", state="complete"))
        self.qt.events()
        model.fileRequestPrint("a.gcode")
        self.connect()
        model.fileConfirmPrint()
        self.assertIsNotNone(model._file_manager.print_attempt)
        # The unchanged stale state holds the attempt.
        model._publish()
        self.assertIsNotNone(model._file_manager.print_attempt)
        # The transition to a live state is the success.
        self.deliver(self.status(filename="a.gcode", state="printing"))
        model._publish()
        self.assertIsNone(model._file_manager.print_attempt)

    def test_upload_refuses_the_printing_file_and_disk_shortfall(self):
        # The host streams the printing file from disk: replacing it
        # mid-print truncates the running job, so the upload refuses
        # outright. The disk guard refuses before the far end fails.
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [{"filename": "a.gcode", "modified": 10.0, "size": 100}],
                    "dirs": [], "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        self.deliver(self.status(filename="a.gcode", state="printing"))
        self.qt.events()
        model.fileUpload("/tmp/a.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("currently printing" in line for line in lines))
        with harness.patch("os.path.getsize", return_value=400 * 1048576):
            model.fileUpload("/tmp/big.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("MB free" in line for line in lines))
        # A REAL zero (full disk) refuses; only a missing report
        # (None) stands the check down (the adversarial round's
        # catch: the old guard treated the two alike).
        model._file_manager.disk_usage["free"] = 0
        with harness.patch("os.path.getsize", return_value=1048576):
            model.fileUpload("/tmp/one-mb.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("MB free" in line for line in lines))
        model._file_manager.disk_usage.clear()
        with harness.patch.object(model._files, "_start_upload") as start:
            model.fileUpload("/tmp/unknown-disk.gcode")
        start.assert_called_once()  # no report → the guard stands down

    def test_file_non_gcode_upload_refuses_without_a_prompt(self):
        model = self.monitor()
        model.openFileManager()
        for request in reversed(self.transport.requests):
            if "path=gcodes&" in request.path:
                request.callback({"result": {
                    "files": [], "dirs": [],
                    "disk_usage": {"total": 800, "used": 600, "free": 200},
                }}, None)
                break
        self.qt.events()
        model.fileUpload("/tmp/thing.stl")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("only gcode files" in line for line in lines))

    def test_file_manager_open_flag_round_trips_through_the_model(self):
        # A live report: the File-manager button stopped
        # opening the popup once the flag moved into the model. The
        # flag must publish, read back, AND NOTIFY — the QML binding
        # re-evaluates on the signal, and a Python-only read passes
        # even when the notify never fires (the second report's
        # exact hole: the flag sat outside the signal group).
        model = self.monitor()
        self.assertFalse(model.fileManagerOpen)
        fired = []
        model.fileManagerChanged.connect(lambda: fired.append(True))
        model.setFileManagerOpen(True)
        self.assertTrue(model.fileManagerOpen)
        self.assertEqual(fired, [True])
        # A full publish rebuild must not lose the flag.
        model._publish()
        self.assertTrue(model.fileManagerOpen)
        model.setFileManagerOpen(False)
        self.assertFalse(model.fileManagerOpen)
        self.assertEqual(fired, [True, True])

    def test_file_manager_view_mutations_republish_immediately(self):
        # A live report: ticking a filter changed nothing
        # and the page carousel advanced one step then stopped — the
        # slots mutated the view dataclass without re-publishing, so
        # nothing re-rendered until an unrelated signal did. Every
        # view mutation must publish on its own.
        model = self.monitor()
        model.setFileSort("size")
        self.assertEqual(model.fileManagerSortColumn, "size")
        model.setFileSearch("benchy")
        self.assertEqual(model.fileManagerSearch, "benchy")
        model.setFilePageSize("all")
        self.assertEqual(model.fileManagerPageSize, "all")
        # No walk data in this fixture: the page index clamps to 1
        # (never an empty page), but the mutation must still publish.
        model.setFilePage(2)
        self.assertEqual(model.fileManagerPageIndex, 1)
        model.setFileFilter("slicer", ["Cura 5.9"])
        self.assertEqual(model.fileManagerFilters, {"slicer": ["Cura 5.9"]})
        self.assertEqual(model.fileManagerFilterCounts, {"slicer": 1})
        # Single-value categories publish as one-element lists (the
        # QML's checked bindings) but filter as scalars.
        model.setFileFilter("modified", ["7d"])
        self.assertEqual(model.fileManagerFilters, {"slicer": ["Cura 5.9"], "modified": ["7d"]})
        self.assertEqual(model.fileManagerFilterCounts, {"slicer": 1, "modified": 1})
        model.setFileFilter("modified", [])
        self.assertEqual(model.fileManagerFilters, {"slicer": ["Cura 5.9"]})
        model.clearFileFilters()
        self.assertEqual(model.fileManagerFilters, {})

    def test_leave_monitor_stage_chooses_preview_or_prepare(self):
        # A live request: Esc on the Monitor page goes to
        # the Preview stage when anything is sliced, Prepare
        # otherwise.
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        device = output._current
        device.leaveMonitorStage()
        self.assertEqual(self.app.controller.stage, "PrepareStage")
        device._has_slice = lambda: True
        device.leaveMonitorStage()
        self.assertEqual(self.app.controller.stage, "PreviewStage")
