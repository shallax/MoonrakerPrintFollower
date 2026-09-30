"""Executable runtime lifecycle composition contracts."""
from tests import composed_runtime_support as harness

class ComposedComponentTests(harness.ComposedComponentTests):
    def test_facade_has_no_legacy_private_state_or_mixin_bases(self):
        for name in ("_remote_job_service", "_preview_follower_service", "_simulation_view", "_apply_path_progress", "_config_store"):
            self.assertFalse(hasattr(self.follower, name), name)
        self.assertFalse(any("Mixin" in cls.__name__ for cls in type(self.follower).__mro__))

    def test_a_scrub_that_races_the_restore_still_serves_its_window(self):
        # The live report's race: the detach/scrub arrived while the
        # restore was still in flight — its demand died at the
        # view-None guards, and the coordinator's same-anchor
        # re-assertion after the commit hit the idempotency no-op.
        # The commit must re-raise the frozen window itself.
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
        service._cache.save(identity, index)
        # The restart: the view is gone and the restore has not run.
        service._view = None
        service._restored = False
        service._prepared_table = None
        service._prepared_identity = None
        files._path = target
        # The race: the scrub lands BEFORE the restore completes.
        service.set_manual_anchor(8)
        self.assertIsNone(service._view, "the race never raced")
        service._advance()
        for _ in range(400):
            self.qt.events(5)
            if service._view is not None and not service._busy:
                break
        self.assertIsNotNone(service._view, "the restore never installed the view")
        for _ in range(400):
            self.qt.events(5)
            if service._presentation_source(8) == "decoded":
                break
        self.assertEqual(service._presentation_source(8), "decoded",
                         "the racing scrub's window never hydrated")

    def test_a_finished_worker_never_terminates_inside_the_submitting_call(self):
        """The terminal is emitted by the thread that ran the work.

        A pool can finish a fast job before _submit has done its own
        bookkeeping. While the emit rode the future's done-callback,
        that interleaving ran the callback — and so the emit, with an
        auto-connected _finish behind it — on the CALLING thread, inside
        the stack of the public call that submitted the work: the
        service installed a view and cleared a busy flag mid-call,
        while the scrub race above rests on nothing having installed
        the view yet. The worker here finishes and joins before
        submit() returns, so the interleaving happens on every run
        rather than when a runner happens to be slow.
        """
        service = self.parts.index

        class EagerPool:
            """A pool that runs the work to completion on its own thread."""

            def submit(self, work):
                worker = harness.threading.Thread(target=work)
                worker.start()
                worker.join()
                future = harness.Future()
                future.set_result(None)
                return future

            def shutdown(self, **kwargs):
                pass

        real, service._executor = service._executor, EagerPool()
        try:
            service._submit("hydrate", lambda: None, None)
            self.assertEqual(service._busy, "hydrate",
                             "the terminal ran inside the submitting call")
        finally:
            service._executor = real
        for _ in range(200):
            self.qt.events(5)
            if not service._busy:
                break
        self.assertEqual(service._busy, "", "the terminal never arrived")

    def test_a_stale_completion_never_clears_the_new_jobs_busy(self):
        # The review's cache-clear finding: a stale worker's terminal
        # must not clear the busy flag a NEWER task owns — the old
        # code cleared _busy before the generation check, so a second
        # job could be submitted while the first still ran.
        service = self.parts.index
        released = harness.threading.Event()
        released2 = harness.threading.Event()

        def slow_worker():
            released.wait(5)
            return []

        def second_worker():
            released2.wait(5)
            return []

        service._generation = 10
        service._submit("hydrate", slow_worker, None)
        self.assertEqual(service._busy, "hydrate")
        # A bind bumps the generation while the worker still runs;
        # a NEW task submits under the new generation.
        service.bind(("other.gcode", 200, 1))
        service._submit("hydrate", second_worker, None)
        # The stale worker completes: its terminal belongs to the
        # OLD generation and must leave the newer task's busy alone.
        released.set()
        for _ in range(200):
            self.qt.events(5)
            if not service._busy:
                break
        self.assertEqual(service._busy, "hydrate",
                         "a stale completion cleared the newer job's busy")
        released2.set()
        for _ in range(200):
            self.qt.events(5)
            if not service._busy:
                break
        self.assertEqual(service._busy, "",
                         "the new job's own completion never cleared the busy")

    def test_start_print_power_probe_checks_every_device(self):
        config = self.config_type(url="http://printer-a", power_devices="socket,psu")
        upload = self.qt.load("UploadController").UploadController(
            self.follower.client, "A", self.follower.current_printer_identity)
        self.addCleanup(upload.abort)
        upload.begin(config, "part.gcode")
        upload._power = ["socket", "psu"]
        upload._power_off = []
        upload._probe_power(list(upload._power))

        def gets():
            return [r for r in self.transport.requests if r.method == "GET" and "device_power" in r.path]
        self.assertEqual(len(gets()), 1)
        gets()[0].callback({"result": {"socket": "on"}}, None)
        # The probe continues past an already-on device instead of assuming
        # the whole chain is powered.
        self.assertEqual(len(gets()), 2)
        gets()[1].callback({"result": {"psu": "off"}}, None)
        posts = [r for r in self.transport.requests if r.method == "POST" and "device_power" in r.path]
        self.assertEqual(len(posts), 1)
        self.assertIn("device=psu", posts[0].path)
        self.assertIn("action=on", posts[0].path)

    def test_endstops_poll_skipped_while_printing(self):
        # A live report: the 10 s endstops poll's
        # query_endstops paused the toolhead 250-500 ms each time
        # mid-print; quitting Cura stopped it. The states cannot
        # change mid-print, so the poll must stand down while active.
        model = self.monitor()
        model._data._update(core={"print_stats": {"state": "printing"}})
        before = len(self.transport.requests)
        model._data.refresh_endstops()
        self.assertEqual(len(self.transport.requests), before)

    def test_power_display_lists_every_device_the_printer_reports(self):
        # The ruling: the configured auto-power-on list narrows
        # the print-start sequence, never the Monitor display — a
        # configured list silently hid DFU on the real printer.
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", power_devices="24v"))
        model = self.monitor()
        model._data._update(power=[
            {"device": "24v", "status": "on", "locked_while_printing": True},
            {"device": "DFU", "status": "off", "locked_while_printing": True},
        ])
        names = [item["name"] for item in model._controls.power_devices()]
        self.assertEqual(names, ["24v", "DFU"])

    def test_a_stale_workers_results_never_commit_after_a_rebind(self):
        # The worker returns LOCAL
        # results and only the generation-checked _finish commits —
        # a worker from the previous job must never touch the new
        # job's stores.
        service = self.parts.index
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        released = harness.threading.Event()

        def slow_worker():
            released.wait(5)
            return [], {5: (b"raw", {"motions": 1})}
        service._hydrating = {5}
        service._submit("hydrate", slow_worker, None)
        service.bind(("other.gcode", 200, 1))  # the generation bumps
        released.set()
        for _ in range(200):
            self.qt.events(5)
            if not service._busy:
                break
        self.assertNotIn(5, service._full_cache,
                         "a stale worker's encoding reached the new job's cache")
        self.assertNotIn(5, service._decoded_lru,
                         "a stale worker's payload reached the new job's hot cache")

    def test_a_restarted_print_does_not_read_the_finished_prints_fraction(self):
        # The live report: a print stopped partway into its first
        # layer, the same file started again, and the follower read the
        # old fraction the moment the new print came up — the first
        # layer stayed pinned at the finished print's boundary for its
        # whole life. The printer holds the stopped print's byte offset
        # until the new file is read, and the split credited it.
        service = self.parts.index
        source = b"".join(
            b";LAYER:%d\nG0 X0 Y0\nG1 X1 Y0 E1\n" % layer
            + b"".join(b"G1 X%d Y0 E1\n" % m for m in range(2, 101))
            for layer in range(3))
        index = self.qt.load("GCodeIndex").build_index_from_bytes(source)
        module = self.qt.load("GCodeIndexService")
        prepare = self.qt.load("PlateProgress").prepare_layer
        count = index.motion_count(0)
        stop = int(count * 0.36)                 # the stop: 36% of layer 1
        stop_position = int(index.motion_offsets[0][stop])
        stop_x = float(index.motion_x[0][stop])
        stop_z = float(index.motion_z[0][stop])
        self.assertGreater(stop, 2, "the fixture's layer is too short to test")

        def install():
            for layer in range(len(index.ranges)):
                service._decoded_lru[layer] = prepare(index, layer)
            service._view = module.IndexView(self.parts.files.job_key, index)

        def status(*, state="printing", position=0, duration=0.0, x=0.0):
            return {"print_stats": {"filename": "part.gcode", "state": state,
                                    "print_duration": duration,
                                    "info": {"current_layer": 0, "total_layer": 3}},
                    "virtual_sdcard": {"file_size": 4096, "file_position": position},
                    "gcode_move": {"gcode_position": [x, 0.0, stop_z, 10.0], "speed_factor": 1},
                    "motion_report": {"live_position": [x, 0.0, stop_z, 10.0]}}

        def split():
            payload = self.parts.coordinator.snapshot.plate_progress
            return None if payload is None else payload.get("split")

        def published():
            value = model.plateLiveSplit
            return value.value() if hasattr(value, "value") else value

        model = self.monitor()
        self.connect()
        # Print A runs into layer 1's stop point: the warm boundary the
        # follower must be holding when the job switches.
        self.deliver(status(position=stop_position - 200, duration=100.0,
                            x=max(0.0, stop_x - 20)))
        install()
        for _ in range(3):
            self.deliver(status(position=stop_position, duration=120.0, x=stop_x))
            install()
            self.qt.events(10)
        warm = split()
        self.assertIsNotNone(warm, "print A never published a boundary")
        self.assertAlmostEqual(warm, stop, delta=2)
        # Stopped, then the same file again with the printer still
        # reporting A's byte offset and the nozzle parked where the
        # stop left it — the new print's first layer has printed
        # nothing, and reads nothing.
        for _ in range(2):
            self.deliver(status(state="cancelled", position=stop_position,
                                duration=121.0, x=stop_x))
        seen = []
        for poll in range(3):
            self.deliver(status(position=stop_position, duration=0.1 * poll, x=stop_x))
            install()
            self.qt.events(10)
            self.assertEqual(self.parts.coordinator.snapshot.job_key[2], 2,
                             "the restart never became a new job")
            seen.append(split())
            if split() is not None:
                self.assertEqual(published(), 0,
                                 "the face was published the finished print's fraction")
        boundaries = [value for value in seen if value is not None]
        self.assertTrue(boundaries, "the restart never published a boundary")
        self.assertEqual(boundaries, [0] * len(boundaries),
                         "the restarted print's first layer read the finished "
                         "print's fraction")
        # The new print reads bytes of its own: the refusal lifts and
        # the boundary follows the new print's own progress.
        self.deliver(status(position=0, duration=0.3, x=0.0))
        install()
        self.qt.events(10)
        self.assertLessEqual(split() or 0, 2,
                             "the new print's own start jumped the boundary")
        for step in (20, 40):
            self.deliver(status(position=int(index.motion_offsets[0][step]),
                                duration=1.0 + step,
                                x=float(index.motion_x[0][step])))
            install()
            self.qt.events(10)
            self.assertGreaterEqual(split() or 0, step - 1,
                                    "the new print's own progress stopped being painted")

    def test_the_native_render_window_reuses_layer_objects(self):
        # Role-free rasters — a seek changes
        # three references, never the render assets; adjacent anchors
        # share the exact same PlateLayer objects, and a raster that
        # lands commits only while its layer still lives in the cache.
        model = self.monitor()
        payload = {"classes": {"SKIN": [[[0.0, 0.0, 0.0], [1.0, 0.0, 1.0]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2}
        surface = model.plate_renderer._surfaces["popover"]

        def layers(anchor):
            return {"prev": payload if anchor > 0 else None,
                    "current": payload, "next": payload}
        w1 = model.plate_renderer.window_for(surface, layers(200), 200)
        w2 = model.plate_renderer.window_for(surface, layers(201), 201)
        self.assertIs(w1["current"], w2["prev"],
                      "adjacent windows rebuilt the shared render object")
        self.assertIs(w1["next"], w2["current"],
                      "adjacent windows rebuilt the shared render object")
        # The layer object carries its motion count across roles.
        self.assertEqual(w1["current"].motions, 2)
        # A -> B -> A: the same retained object (capacity 6).
        w3 = model.plate_renderer.window_for(surface, layers(200), 200)
        self.assertIs(w3["current"], w1["current"],
                      "the revisit rebuilt the retained render object")

    def test_adjacent_windows_render_only_the_new_layer(self):
        # : N -> N+1 renders ONLY N+2 —
        # N and N+1 keep their retained rasters; and a full 100%
        # seek publishes no scrub vector (finding 4's gate).
        model = self.monitor()
        surface = model.plate_renderer._surfaces["popover"]
        # The scheduler only demands once a context exists — feed it
        # and let the staged flush land.
        model.setFollowerPlot("popover", 0.0, 0.0, 1.0, 1.0, 0.0, 300.0)
        model.setFollowerView("popover", 1.0, 0.7, 400, 300, False, 0.0, 0.0)
        self.qt.events(5)
        payload = {"classes": {"SKIN": [[[0.0, 0.0, 0.0], [1.0, 0.0, 1.0]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2}

        def layers(anchor):
            return {"prev": payload if anchor > 0 else None,
                    "current": payload, "next": payload}
        model.plate_renderer.window_for(surface, layers(200), 200)
        counts = dict(surface.render_count)
        model.plate_renderer.window_for(surface, layers(201), 201)
        self.assertEqual(surface.render_count.get(200), counts.get(200),
                         "the adjacent window re-rendered the retained layer")
        self.assertEqual(surface.render_count.get(201), counts.get(201),
                         "the adjacent window re-rendered the retained layer")
        # The 100% seek carries no scrub vector; the partial split does.
        full = {"layers": {"current": payload}, "split": 2, "motionTotal": 2}
        self.assertIsNone(model.plate_renderer.scrub_vector(full))
        empty = {"layers": {"current": payload}, "split": 0, "motionTotal": 2}
        self.assertIsNone(model.plate_renderer.scrub_vector(empty))
        partial = {"layers": {"current": payload}, "split": 1, "motionTotal": 2}
        self.assertIsNotNone(model.plate_renderer.scrub_vector(partial))

    def test_the_follower_view_signal_precedes_the_plate_payloads(self):
        # The signal ordering: followerAttached must
        # flip BEFORE the new layer's payload lands, or QML paints
        # the new current layer as a pending base for one frame and
        # then clears it.
        model = self.monitor()
        names = [name for name, _keys in model._SIGNAL_KEYS]
        self.assertLess(names.index("followerViewChanged"),
                        names.index("plateProgressChanged"))

    def test_static_geometry_has_independent_notify_signals(self):
        model = self.monitor()
        groups = dict(model._SIGNAL_KEYS)
        for property_name, signal_name in (
                ("plateScrubVector", "plateScrubVectorChanged"),
                ("plateLiveScrubVector", "plateLiveScrubVectorChanged")):
            prop = model.metaObject().property(model.metaObject().indexOfProperty(property_name))
            self.assertEqual(bytes(prop.notifySignal().name()).decode(), signal_name)
            self.assertEqual(groups[signal_name], (property_name,))
            self.assertNotIn(property_name, groups["plateProgressChanged"])

    def test_a_manual_seek_lands_its_window_while_the_full_pass_runs(self):
        # The live report: the layer slider's seek stuck on "Loading
        # layer…" once the full prepared cache's pass existed — the
        # demanded window must cut in and land whatever the pass is
        # doing.
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity(
            "part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        layers = b"".join(
            b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\nG1 X3 Y3 E1\n" % layer
            for layer in range(12))
        # The lease's own path holds the bytes the hydrator reads, and
        # the compact index builds from the same file.
        target = self.plant_download(files, layers)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(target, compact=True)
        self.qt.load("GCodeIndexService")
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        files._path = target
        files._want_file = True

        def wait_idle():
            for _ in range(400):
                self.qt.events(5)
                if not service._busy and not service._hydrate:
                    break

        service.set_manual_anchor(8)
        wait_idle()
        bundle = service.plate_progress(8, None)["layers"]
        self.assertIsNotNone(bundle.get("current"),
                             "the frozen layer never became available")
        self.assertIn(8, service._full_cache,
                      "the demanded layer never reached the full cache")

    def test_a_live_poll_does_not_drop_the_manual_seeks_demand(self):
        # The live report's minute-long first drag: the seek entered the
        # demand queue while a pass batch was in flight, and the next
        # live poll's window filter dropped it — the layer only arrived
        # when the pass walked to it. The manual window must survive
        # set_followed_layer.
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity(
            "part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        layers = b"".join(
            b";LAYER:%d\nG1 X1 Y1 E1\nG1 X2 Y2 E1\nG1 X3 Y3 E1\n" % layer
            for layer in range(12))
        target = self.plant_download(files, layers)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(target, compact=True)
        self.qt.load("GCodeIndexService")
        service._view = self.qt.load("IndexView").IndexView(("part.gcode", 100, 1), index)
        files._path = target
        files._want_file = True
        service._busy = "fullprep"  # a pass batch is in flight at the seek
        service.set_manual_anchor(8)
        self.assertEqual(service._hydrate, {7, 8, 9})
        service.set_followed_layer(2)  # the live poll
        self.assertEqual(service._hydrate, {1, 2, 3, 7, 8, 9},
                         "the live poll dropped the manual seek's demand")
        service._busy = ""
        service._advance()  # the in-flight batch's finish chain

        def wait_idle():
            for _ in range(400):
                self.qt.events(5)
                if not service._busy and not service._hydrate:
                    break

        wait_idle()
        bundle = service.plate_progress(8, None)["layers"]
        self.assertIsNotNone(bundle.get("current"),
                             "the frozen layer never became available")
        self.assertIn(8, service._full_cache,
                      "the demanded layer never reached the full cache")

    def test_service_failure_signals_are_logged(self):
        source = (harness.pathlib.Path(__file__).resolve().parents[1] / "mpf" / "application" / "PrintCoordinator.py").read_text(encoding="utf-8")
        self.assertIn("files.failed.connect", source)
        self.assertIn("index.failed.connect", source)

    def test_smoothing_trace_is_opt_in(self):
        source = (harness.pathlib.Path(__file__).resolve().parents[1] / "mpf" / "FollowerRuntime.py").read_text(encoding="utf-8")
        self.assertIn("MOONRAKER_FOLLOWER_SMOOTHING_TRACE", source)
        self.assertIn("os.environ.get", source)

    def test_emergency_stop_bypasses_busy_command_but_requires_the_held_third_press(self):
        model = self.monitor()
        model._commands._busy = True
        model.emergencyStopClick()
        model.emergencyStopClick()
        self.assertFalse(any(r.channel == "emergency-stop" for r in self.transport.requests))
        # The third press must be held; the test shortens the hold window.
        model._commands._hold_timer.setInterval(30)
        model.emergencyHoldStarted()
        self.qt.events(100)
        self.assertEqual(sum(r.channel == "emergency-stop" for r in self.transport.requests), 1)
        # Releasing the fired hold delivers a click that must not arm anew.
        model.emergencyStopClick()
        self.assertEqual(model.emergencyStopClicks, 0)
        # A genuinely new click starts a fresh arm sequence.
        model.emergencyStopClick()
        self.qt.events()  # the publish coalescer flushes on the next turn
        self.assertEqual(model.emergencyStopClicks, 1)

    def test_power_lock_blocks_mutation_during_print(self):
        model = self.monitor()
        self.deliver(self.status())
        model._data._update(power=[{"device": "printer", "status": "on", "locked_while_printing": True}])
        before = len(self.transport.requests)
        model.setPowerDevice("printer", False)
        self.assertEqual(len(self.transport.requests), before)

    def test_pwm_discovery_scaling_and_led_color_commands(self):
        model = self.monitor()
        self.deliver(self.status())
        model._data._update(auxiliary={
            "configfile": {"config": {"output_pin case_light": {"pwm": "True", "scale": "2"},
                "output_pin relay": {"pwm": "False"}, "neopixel strip": {"color_order": "RGB"}}},
            "output_pin case_light": {"value": 1}, "output_pin relay": {"value": 1},
            "neopixel strip": {"color_data": [[1, 0, 0, 0]]}})
        items = model.pwmOutputItems.value()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["percent"], 50)
        self.assertEqual(items[0]["scale"], 2)
        model._tuning.DEBOUNCE_MS = 10
        model.setPwmOutput("output_pin case_light", 75)
        model.setLedColor("neopixel strip", 0, 100, 0, 100, 50)
        self.qt.events(25)
        scripts = [(r.options.get("body") or {}).get("script", "") for r in self.transport.requests]
        self.assertIn("SET_PIN PIN=case_light VALUE=1.5", scripts)
        self.assertTrue(any("GREEN=0.5000" in script and "WHITE=0.0000" in script for script in scripts))

    def test_temperature_presets_report_actual_targets_not_last_selection(self):
        model = self.monitor()
        model._data._update(presets={"presets": {"pla": {"name": "PLA", "values": {
            "extruder": {"bool": True, "value": 200}}}}}, auxiliary={"extruder": {"target": 180}})
        self.assertFalse(model.temperaturePresetItems.value()[0]["active"])
        model._data._update(auxiliary={"extruder": {"target": 200}})
        self.assertTrue(model.temperaturePresetItems.value()[0]["active"])

    def test_the_true_slider_to_available_latency_across_the_sources(self):
        # The TRUE end-to-end T_available: the model's committed
        # slider slot through the real service pipeline (request ->
        # classify -> read/decode/prepare -> commit -> coordinator ->
        # publish) until plateProgressAvailable flips for the sought
        # layer. Four source states, one real file.
        if "coverage" in harness.sys.modules:
            # The coverage run instruments the hot loops: a wall-clock
            # latency pin measures the TRACER, not the seek (the raw
            # tier already byte-range reads — the measured 6.2 s under
            # coverage is ~3 s bare). The plain suite jobs hold the
            # true bound.
            self.skipTest("wall-clock latency is not measurable under "
                          "coverage instrumentation")
        content = self._generated_gcode(layers=4, motions=20000)
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
        model.setFollowerPopoverOpen(True)
        model.setSectionExpanded("plateprogress", True)
        parts = follower._runtime
        service = parts.index
        parts.cura._view = object()
        parts.coordinator.request_load()
        for _ in range(100):
            if app.loaded_paths: break
            self.qt.events(10)
        app.controller.view = harness.SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
        app.controller.activeViewChanged.emit()
        for _ in range(600):
            if parts.index.view is not None: break
            self.qt.events(10)
        self.assertIsNotNone(parts.index.view, "the index never built")
        # Force the COMPACT presentation: the arrays drop from the
        # index, so a cold seek genuinely hydrates from the served
        # file (the true RAW source), and the prepared store's disk
        # path is the only fallback once the RAM tiers evict.
        index = parts.index.view._index
        index.compact = True
        index.hydrated_layers = set()
        # The live print's window (followed 2 -> {1,2,3}) would
        # otherwise keep re-decoding layer 3 as its ghost after the
        # state evictions below; park the live anchor at 0.
        service.set_followed_layer(0)

        def seek_to_available(layer):
            start = harness.time.monotonic()
            model.setFollowerLayerAnchor(layer)
            for _ in range(2000):
                self.qt.events(10)
                if model.plateProgressAnchor == layer and model.plateProgressAvailable:
                    # The later-layer slider contract: the range is
                    # the sought layer's own motion count the moment
                    # the current lands (the disabled-slider
                    # regression's enabled side).
                    self.assertEqual(model.plateLayerMotionCount, 20000,
                                     "the slider's range is not the sought layer's count")
                    return (harness.time.monotonic() - start) * 1000.0
            self.fail("the seek to %d never became available" % layer)

        def source_of(layer):
            return service._presentation_source(layer)

        # Each leg reports the classifier's OWN verdict for the
        # sought layer — the rows are labeled by what actually
        # served them, never by assumption.
        legs = []

        def leg(name, seek):
            served = source_of(3)
            elapsed = seek()
            legs.append((name, served, elapsed))
            return elapsed

        # RAW cold: the decoded cache holds nothing; the sought layer
        # hydrates from the served file and decodes.
        raw = leg("raw", lambda: seek_to_available(3))
        # DECODED hot: a FAR seek away (layer 0's window never holds
        # layer 3) and back — the payload is already in the
        # presentation cache.
        seek_to_available(0)
        decoded = leg("decoded", lambda: seek_to_available(3))
        # PACKED RAM: leave the window, drain in-flight demands,
        # evict the DECODED entry only — the encoded PPL1 remains
        # and the re-seek decodes it back.
        seek_to_available(0)
        for _ in range(200):
            self.qt.events(10)
            if not service._busy and not service._hydrate:
                break
        service._decoded_lru.pop(3)
        packed = leg("packed", lambda: seek_to_available(3))
        # PREPARED DISK: wait for the background pass to publish,
        # LEAVE the sought layer's window (an away-seek first), let
        # every in-flight demand drain, and only then evict both RAM
        # tiers — the re-seek's only remaining source is the store's
        # file.
        for _ in range(600):
            self.qt.events(10)
            if service._prepared_saved:
                break
        seek_to_available(0)
        for _ in range(200):
            self.qt.events(10)
            if not service._busy and not service._hydrate:
                break
        service._decoded_lru.pop(3)
        service._full_cache.pop(3, None)
        prepared = leg("prepared", lambda: seek_to_available(3))
        print("T_available: " + " | ".join("%s[%s] %.1f" % (name, served, ms)
                                           for name, served, ms in legs) + " ms")
        self.assertLess(raw, 6000.0, "the raw seek stalled")
        self.assertLess(decoded, 1000.0, "the decoded-hot seek stalled")
        self.assertLess(packed, 3000.0, "the packed seek stalled")
        self.assertLess(prepared, 3000.0, "the prepared seek stalled")

    def test_cleared_payloads_read_as_absent(self):
        # The adversarial round's live repro: closing the popup
        # clears the payloads to "" while a surviving dialog stays
        # painted over the dashboard — its buttons used to raise
        # TypeError inside a swallowed Qt slot. Falsy payloads must
        # read as absent on every path.
        model = self.monitor()
        model.openFileManager()
        model.setFileManagerOpen(False)
        self.assertEqual(model.filePrintConfirm, "")
        with harness.patch.object(model._file_manager, "start_print") as start_print:
            model.fileConfirmPrint()
            start_print.assert_not_called()
        self.assertEqual(model.fileUploadProgress, "")
        model._files._on_upload_progress(40)  # must not raise
        model._files._on_upload_finished(True, "bench.gcode")  # must not raise
        self.assertEqual(model.fileUploadProgress, "")

    def test_manual_reconnect_cycles_the_client(self):
        # A live request: a Reconnect that recovers a UI
        # stuck after a printer error — the client cycle runs even
        # when disconnected (unlike the e-stop's connected-only
        # auto-recovery).
        model = self.monitor()
        with harness.patch.object(model._data._client, "stop") as stop, \
             harness.patch.object(model._data._client, "start") as start:
            model.reconnect()
        stop.assert_called_once()
        start.assert_called_once()
        self.assertIn("Reconnecting", model.actionStatus)


