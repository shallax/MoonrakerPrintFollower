"""Executable contracts for the completed component boundaries."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from concurrent.futures import Future
from http.server import ThreadingHTTPServer
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from types import SimpleNamespace

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, ScriptedSocket, ScriptedTransport, runtime


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class ComposedComponentTests(unittest.TestCase):
    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        self.socket = ScriptedSocket()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=self.socket)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.parts = self.follower._runtime
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        # The harness tests HTTP semantics; the product default stays in
        # PrinterConfig, never in the harness.
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def status(self, *, layer=10, filename="part.gcode", duration=30, position=40, state="printing"):
        return {"print_stats": {"filename": filename, "state": state, "print_duration": duration,
                "info": {"current_layer": layer, "total_layer": 50}},
            "virtual_sdcard": {"file_size": 100, "file_position": position},
            "gcode_move": {"gcode_position": [1, 1, 2, 10], "speed_factor": 1, "extrude_factor": 1}}

    def deliver(self, status):
        import time
        client = self.follower.client
        client._handle_http_status({"result": {"status": status}}, None, client._generation, time.monotonic())

    def connect(self):
        # The dispatch gate's connection clause (4.2.0): the real
        # confirm dialog can only be reached with an observed
        # connection — the fixture establishes it the same way.
        client = self.follower.client
        client._connected = True
        client.connectionChanged.emit(True, "Moonraker connected over http polling")

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    def plant_download(self, files, layers):
        """A downloaded G-code file under the service's own root, torn
        down once the service has let go of it.

        The last assert is not the last READ: a worker lane can still be
        walking the file (the reader holds the lease its lane took), and
        Windows refuses a delete while any handle is open (WinError 32).
        The teardown waits on the service's own release discipline — one
        lane at a time, the reader's lease — so a real leak still fails
        here rather than passing silently."""
        target = os.path.join(files._root, "job-1", "part.gcode")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(layers)
        service = self.parts.index

        def drop_the_download():
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and (service._busy or files._leases):
                self.qt.events(5)
            os.remove(target)

        self.addCleanup(drop_the_download)
        return target

    def test_facade_has_no_legacy_private_state_or_mixin_bases(self):
        for name in ("_remote_job_service", "_preview_follower_service", "_simulation_view", "_apply_path_progress", "_config_store"):
            self.assertFalse(hasattr(self.follower, name), name)
        self.assertFalse(any("Mixin" in cls.__name__ for cls in type(self.follower).__mro__))

    def test_thumbnail_publishes_coalesce_onto_one_flush(self):
        # A burst of landings repaints the QML once, not once per
        # callback (the landing storm stalled scrolling). The flush
        # rides a 100 ms debounce, so the "nothing before the
        # debounce" reading races the wall clock on slow runners —
        # the two load-independent invariants are one flush per
        # burst and no flush for a no-change emit.
        model = self.monitor()
        count = []
        model.fileManagerThumbsChanged.connect(lambda: count.append(1))
        # A real change in the payload, then a burst of signals: the
        # flush emits once, and a no-change flush emits nothing.
        model._file_manager._thumbs["x.gcode"] = {"state": "ready", "url": ""}
        for _ in range(3):
            model._file_manager.thumbsChanged.emit()
        self.qt.events(200)
        self.assertEqual(count, [1])
        model._file_manager.thumbsChanged.emit()
        self.qt.events(200)
        self.assertEqual(count, [1])

    def test_monitor_and_preview_consume_one_physical_observation(self):
        model = self.monitor()
        self.deliver(self.status(layer=10))
        before = self.follower.print_state
        self.assertEqual(before.layer.index, 9)
        self.assertEqual(model.monitorLayer, "10 / 50")
        for _ in range(5):
            model._publish()
        self.assertIs(self.follower.print_state, before)
        with self.assertRaises(FrozenInstanceError):
            before.layer.index = 20

    def test_monitor_snapshot_is_deeply_immutable(self):
        model = self.monitor()
        self.deliver(self.status())
        snapshot = model._data.snapshot
        with self.assertRaises(TypeError): snapshot.core["print_stats"]["state"] = "paused"
        with self.assertRaises(TypeError): snapshot.core["print_stats"] = {}

    def test_no_borrowed_cura_file_released_by_unrelated_completion_or_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "print.gcode")
            pathlib.Path(path).write_text("G1 X0")
            releases = []
            lease = self.qt.load("RemoteFileService").FileLease(path, releases.append)
            self.parts.cura._view = object()  # the load preflight needs a build volume
            self.assertTrue(self.parts.cura.load(lease))
            self.app.fileCompleted.emit(os.path.join(directory, "unrelated.stl"))
            self.assertEqual(releases, [])
            self.assertTrue(self.parts.cura.loading)
            self.parts.cura.close()
            self.assertEqual(releases, [path])  # shutdown releases, never drops
            self.app.fileCompleted.emit(path)
            self.assertEqual(releases, [path])

    def test_rebind_retires_file_but_lease_keeps_it_alive(self):
        files = self.parts.files
        files.bind(("old.gcode", 100, 1))
        directory = tempfile.mkdtemp(dir=files._root)
        path = os.path.join(directory, "old.gcode")
        pathlib.Path(path).write_text("G1 X0")
        files._path = path
        lease = files.lease()
        files.bind(("new.gcode", 100, 2))
        self.assertTrue(os.path.exists(path))
        lease.close()
        self.assertFalse(os.path.exists(path))
        lease.close()

    def test_cache_restore_is_off_ui_thread_and_request_queue_is_bounded(self):
        service, files = self.parts.index, self.parts.files
        key = ("part.gcode", 100, 1)
        files.bind(key)
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 100, modified=1)
        service.bind(key)
        entered, release = threading.Event(), threading.Event()
        threads = []
        def load(identity):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return None
        self.addCleanup(release.set)
        with patch.object(service._cache, "load", load), patch.object(files, "request_file"):
            service.request()
            self.assertTrue(entered.wait(1))
            for _ in range(50): service.request()
            self.assertEqual(len(threads), 1)
            self.assertNotEqual(threads[0], threading.get_ident())
            service.bind(None)
            release.set()
            for _ in range(50):
                self.qt.events(5)
                if not service._busy: break
        self.assertIsNone(service.view)

    def test_index_view_does_not_expose_motion_arrays(self):
        module = self.qt.load("GCodeIndexService")
        index = self.qt.load("GCodeIndex").LayerMotionIndex(ranges=[(0, 100)], current_layer_map={1: 0})
        view = module.IndexView(("part.gcode", 100, 1), index)
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
        service._prepared_table = None
        service._prepared_identity = None
        files._path = None
        requests = []
        with patch.object(files, "request_file", lambda *a, **k: requests.append(1)):
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
                worker = threading.Thread(target=work)
                worker.start()
                worker.join()
                future = Future()
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
        released = threading.Event()
        released2 = threading.Event()

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

    def test_the_invalidate_retires_the_active_prepared_writer(self):
        # The review's cache-clear finding: invalidate must RETIRE
        # (freeze and checkpoint) the active prepared writer, not
        # discard the reference — a bare drop would leak the writer's
        # temp file and leave a future append racing the deletion.
        service = self.parts.index
        writer = {"retired": False}
        service._prepared_writer = writer
        with patch.object(service, "_suspend_prepared_writer") as suspend:
            service.invalidate()
        self.assertTrue(writer["retired"], "the writer was never frozen")
        self.assertEqual(suspend.call_count, 1,
                         "the writer was never suspended/checkpointed")
        self.assertIsNone(service._prepared_writer,
                          "the writer reference was dropped without retiring")

    def test_the_cache_clear_invalidate_drops_every_index_listener_state(self):
        # The live ruling: once the cache-clear wipes the backing
        # files, every listener must believe the index does not exist —
        # the view, the prepared tables, the decoded and full caches,
        # the manual anchor and the memos all go, the changed signal
        # fires, and nothing rebuilds on its own.
        module = self.qt.load("GCodeIndexService")
        service = self.parts.index
        index = self.qt.load("GCodeIndex").LayerMotionIndex(
            ranges=[(0, 100)], current_layer_map={1: 0})
        service._view = module.IndexView(("part.gcode", 100, 1), index)
        service._job = ("part.gcode", 100, 1)
        service._wanted = service._restored = service._save = True
        service._prepared_table = {"layer": 0}
        service._prepared_identity = ("part.gcode", 100, 1)
        service._prepared_complete = True
        service._prepared_coverage = {0}
        service._full_cache.set("layer", object(), 16)
        service._decoded_lru.set("layer", object(), 16)
        service._decoded_lru.protected = {3}
        service._decoded_pins = {3: 16}
        service._decoded_sizes = {3: 16}
        service._plate_layers_memos = {("part.gcode", 100, 1): object()}
        service._manual_anchor = 4
        service._manual_split = 12
        service._split_floor = 7
        service._split_refined = 9
        service._split_floor_key = ("part.gcode", 4)
        service._visited = {1, 2}
        service._visited_upto = 5
        service._visited_settled = frozenset({1})
        service._visited_key = ("part.gcode", 100, 1)
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
        self.assertIsNone(service._prepared_table)
        self.assertIsNone(service._prepared_identity)
        self.assertFalse(service._prepared_complete)
        self.assertEqual(service._prepared_coverage, set())
        self.assertEqual(len(service._full_cache), 0)
        self.assertEqual(len(service._decoded_lru), 0)
        self.assertEqual(service._decoded_lru.protected, set())
        self.assertEqual(service._decoded_pins, {})
        self.assertEqual(service._decoded_sizes, {})
        self.assertEqual(service._plate_layers_memos, {})
        self.assertIsNone(service._manual_anchor)
        self.assertIsNone(service._manual_split)
        self.assertIsNone(service._split_floor)
        self.assertIsNone(service._split_refined)
        self.assertIsNone(service._split_floor_key)
        self.assertEqual(service._visited, set())
        self.assertEqual(service._visited_upto, -1)
        self.assertEqual(service._visited_settled, frozenset())
        self.assertIsNone(service._visited_key)
        self.assertEqual(service._hydrate, set())
        self.assertIsNone(service._hydrating)
        self.assertEqual(service._failed_hydrate, set())
        self.assertIsNone(service._progress)

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

    def test_failed_hydration_is_latched_until_a_new_file_arrives(self):
        service, files = self.parts.index, self.parts.files
        files.bind(("part.gcode", 100, 1))
        files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity("part.gcode", 100, modified=1)
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        handle = tempfile.NamedTemporaryFile(suffix=".gcode", delete=False)
        handle.write(b";LAYER:0\nG1 X1\n;LAYER:1\nG1 X2\n")
        handle.close()
        self.addCleanup(os.remove, handle.name)
        gci = self.qt.load("GCodeIndex")
        index = gci.build_index_from_file(handle.name, compact=True)
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
        files._path = os.path.join(files._root, "job-1", "part.gcode")
        files._want_file = True

        def wait_idle():
            for _ in range(200):
                self.qt.events(5)
                if not service._busy and not service._hydrate: break
        with patch.object(module, "hydrate_layer_from_file", return_value=False) as hydrate:
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
        with patch.object(module, "hydrate_layer_from_file", return_value=True) as hydrate2:
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
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
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
            self.assertEqual(module._decode_layer(raw)["motions"], 3,
                             "layer %d's cache entry lost its geometry" % layer)
        # The retention still holds the arrays down to the two windows
        # plus the last hydrated layer's own — the cache, not the
        # hydration, serves the far layers.
        self.assertEqual(index.hydrated_layers, {0, 1, 2, 8, 9})

    def test_a_stale_workers_results_never_commit_after_a_rebind(self):
        # The worker returns LOCAL
        # results and only the generation-checked _finish commits —
        # a worker from the previous job must never touch the new
        # job's stores.
        service = self.parts.index
        service.bind(("part.gcode", 100, 1))
        service._restored = True
        service._wanted = True
        released = threading.Event()

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

    def test_a_completed_seek_republishes_without_new_telemetry(self):
        # A completed manual seek republishes
        # off the worker's own completion — never waiting on the next
        # printer heartbeat.
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
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
        files._path = target
        files._want_file = True
        model = self.monitor()
        model.setFollowerPopoverOpen(True)
        self.deliver(self.status(layer=2, filename="part.gcode"))
        self.qt.events(30)
        model.setFollowerLayerAnchor(8)
        for _ in range(400):
            self.qt.events(5)
            if model.plateProgressAvailable:
                break
        self.assertTrue(model.plateProgressAvailable,
                        "the seek's layer never published without new telemetry")

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
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
        self.assertEqual(service.plate_pass_fraction(), 0.0)
        service._full_cache.update({0: b"x", 1: b"x", 2: b"x", 3: b"x"})
        self.assertAlmostEqual(service.plate_pass_fraction(), 0.4)

    def test_the_native_render_window_reuses_layer_objects(self):
        # Role-free rasters — a seek changes
        # three references, never the render assets; adjacent anchors
        # share the exact same PlateLayer objects, and a raster that
        # lands commits only while its layer still lives in the cache.
        model = self.monitor()
        payload = {"classes": {"SKIN": [[[0.0, 0.0, 0.0], [1.0, 0.0, 1.0]]]},
                   "travels": [], "travelStarts": [], "travelEnds": [], "motions": 2}
        surface = model._plate_surfaces["popover"]

        def layers(anchor):
            return {"prev": payload if anchor > 0 else None,
                    "current": payload, "next": payload}
        w1 = model._qt_window(surface, layers(200), 200)
        w2 = model._qt_window(surface, layers(201), 201)
        self.assertIs(w1["current"], w2["prev"],
                      "adjacent windows rebuilt the shared render object")
        self.assertIs(w1["next"], w2["current"],
                      "adjacent windows rebuilt the shared render object")
        # The layer object carries its motion count across roles.
        self.assertEqual(w1["current"].motions, 2)
        # A -> B -> A: the same retained object (capacity 6).
        w3 = model._qt_window(surface, layers(200), 200)
        self.assertIs(w3["current"], w1["current"],
                      "the revisit rebuilt the retained render object")

    def test_the_render_cache_is_bounded_and_generation_isolated(self):
        model = self.monitor()
        surface = model._plate_surfaces["popover"]
        payload = {"classes": {}, "travels": [], "travelStarts": [],
                   "travelEnds": [], "motions": 1}
        for layer in range(10):
            model._qt_layer(surface, payload, layer)
        self.assertLessEqual(len(surface.layers), 6)
        self.assertNotIn(0, surface.layers,
                         "the oldest render object never evicted")
        # A new job clears every surface's cache: the old geometry
        # can never answer the new print's window.
        model._observe_follower_job("new-job")
        self.assertEqual(surface.layers, {})
        self.assertEqual(model._plate_qt_job, "new-job")

    def test_adjacent_windows_render_only_the_new_layer(self):
        # : N -> N+1 renders ONLY N+2 —
        # N and N+1 keep their retained rasters; and a full 100%
        # seek publishes no scrub vector (finding 4's gate).
        model = self.monitor()
        surface = model._plate_surfaces["popover"]
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
        model._qt_window(surface, layers(200), 200)
        counts = dict(surface.render_count)
        model._qt_window(surface, layers(201), 201)
        self.assertEqual(surface.render_count.get(200), counts.get(200),
                         "the adjacent window re-rendered the retained layer")
        self.assertEqual(surface.render_count.get(201), counts.get(201),
                         "the adjacent window re-rendered the retained layer")
        # The 100% seek carries no scrub vector; the partial split does.
        full = {"layers": {"current": payload}, "split": 2, "motionTotal": 2}
        self.assertIsNone(model._scrub_vector_for(full))
        empty = {"layers": {"current": payload}, "split": 0, "motionTotal": 2}
        self.assertIsNone(model._scrub_vector_for(empty))
        partial = {"layers": {"current": payload}, "split": 1, "motionTotal": 2}
        self.assertIsNotNone(model._scrub_vector_for(partial))

    def test_the_follower_view_signal_precedes_the_plate_payloads(self):
        # The signal ordering: followerAttached must
        # flip BEFORE the new layer's payload lands, or QML paints
        # the new current layer as a pending base for one frame and
        # then clears it.
        model = self.monitor()
        names = [name for name, _keys in model._SIGNAL_KEYS]
        self.assertLess(names.index("followerViewChanged"),
                        names.index("plateProgressChanged"))

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
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
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
        module = self.qt.load("GCodeIndexService")
        service._view = module.IndexView(("part.gcode", 100, 1), index)
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
        source = (pathlib.Path(__file__).resolve().parents[1] / "plugins" / "PrintCoordinator.py").read_text(encoding="utf-8")
        self.assertIn("files.failed.connect", source)
        self.assertIn("index.failed.connect", source)

    def test_smoothing_trace_is_opt_in(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "plugins" / "FollowerRuntime.py").read_text(encoding="utf-8")
        self.assertIn("MOONRAKER_FOLLOWER_SMOOTHING_TRACE", source)
        self.assertIn("os.environ.get", source)

    def test_file_backed_writer_and_duplicate_preparation_ownership(self):
        module = self.qt.load("CuraOutputWriter")
        config = self.config_type(url="http://printer-a", upload_dialog=True)
        writer = module.CuraOutputWriter(self.app)
        prepared = writer.prepare(config, "part.gcode")
        self.addCleanup(prepared.close)
        self.assertEqual(pathlib.Path(prepared.path).read_text(encoding="utf-8"), "G1 X0\n")
        upload = self.qt.load("UploadController").UploadController(self.follower.client, "A", self.follower.current_printer_identity)
        self.addCleanup(upload.abort)
        upload.begin(config, "part.gcode")
        upload.prepared(prepared)
        second = writer.prepare(config, "second.gcode")
        with self.assertRaises(RuntimeError): upload.prepared(second)
        self.assertFalse(os.path.exists(second.path))
        self.assertTrue(os.path.exists(prepared.path))

    def test_tuning_debounce_keeps_latest_value_and_ignores_old_callbacks(self):
        model = self.monitor()
        self.deliver(self.status())
        tuning = model._tuning
        tuning.DEBOUNCE_MS = 10
        for value in range(110, 140): model.setSpeedFactor(value)
        self.qt.events(25)
        commands = [r for r in self.transport.requests if r.channel.startswith("quick-")]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].options["body"], {"script": "M220 S139"})
        model.setMonitoringActive(False)
        commands[0].callback({}, None)
        self.assertFalse(tuning._pending)

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

    def test_macro_definitions_are_cached_until_config_changes(self):
        model = self.monitor()
        data = model._data
        data._update(objects=("gcode_macro TEST",), auxiliary={"configfile": {"config": {
            "gcode_macro TEST": {"gcode": "{{ params.COUNT|default(2)|int }}"}}}})
        controls_module = self.qt.load("MonitorControls")
        with patch.object(controls_module, "infer_macro_parameters", wraps=controls_module.infer_macro_parameters) as infer:
            model.macroParameterDefinitions("TEST")
            model.macroParameterDefinitions("TEST")
            data._update(core={"print_stats": {"state": "standby"}})
            model.macroParameterDefinitions("TEST")
            self.assertEqual(infer.call_count, 1)

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

    def test_malformed_monitor_objects_degrade_without_throwing(self):
        model = self.monitor()
        model.updateMoonrakerStatus({"print_stats": "bad", "virtual_sdcard": [], "gcode_move": 7, "motion_report": None})
        self.assertEqual(model.monitorPosition, "—")
        self.assertEqual(model.monitorProgress, 0)

    def test_file_backed_multipart_upload_and_terminal_ordering(self):
        received = []
        class Handler(PipeSafeHandler):
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
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
        content = (pathlib.Path(__file__).parent / "fixtures" / "gcode" / "cura.gcode").read_bytes()
        status = self.status(layer=2)
        status["virtual_sdcard"]["file_size"] = len(content)
        class Handler(PipeSafeHandler):
            def do_GET(self):
                if self.path.startswith("/server/files/gcodes/"):
                    body = content
                elif self.path.startswith("/server/files/metadata"):
                    body = json.dumps({"result": {"size": len(content), "modified": 1}}).encode()
                else:
                    body = json.dumps({"result": {"status": status}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
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
        app.controller.view = SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
        app.controller.activeViewChanged.emit()
        for _ in range(300):
            if parts.index.view is not None: break
            self.qt.events(10)
        self.assertIsNotNone(parts.index.view)
        self.assertEqual(len(parts.index.view.ranges), 3)
        self.assertEqual(pathlib.Path(parts.files.path).read_bytes(), content)
        app.fileCompleted.emit(parts.files.path)
        self.assertFalse(parts.cura.loading)

    def test_the_mini_and_open_popover_become_available_after_the_index_builds(self):
        # The live regressions' common root: an index-hydrated layer
        # must still DEMAND its presentation payload — the mini's
        # placeholder and the popover's Loading layer… clear only
        # when the decoded current actually lands, with no other
        # user action.
        content = (pathlib.Path(__file__).parent / "fixtures" / "gcode" / "cura.gcode").read_bytes()
        status = self.status(layer=2)
        status["virtual_sdcard"]["file_size"] = len(content)
        class Handler(PipeSafeHandler):
            def do_GET(self):
                if self.path.startswith("/server/files/gcodes/"):
                    body = content
                elif self.path.startswith("/server/files/metadata"):
                    body = json.dumps({"result": {"size": len(content), "modified": 1}}).encode()
                else:
                    body = json.dumps({"result": {"status": status}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
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
        app.controller.view = SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
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

    def test_the_true_slider_to_available_latency_across_the_sources(self):
        # The TRUE end-to-end T_available: the model's committed
        # slider slot through the real service pipeline (request ->
        # classify -> read/decode/prepare -> commit -> coordinator ->
        # publish) until plateProgressAvailable flips for the sought
        # layer. Four source states, one real file.
        if "coverage" in sys.modules:
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
        class Handler(PipeSafeHandler):
            def do_GET(self):
                if self.path.startswith("/server/files/gcodes/"):
                    body = content
                elif self.path.startswith("/server/files/metadata"):
                    body = json.dumps({"result": {"size": len(content), "modified": 1}}).encode()
                else:
                    body = json.dumps({"result": {"status": status}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
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
        app.controller.view = SimpleNamespace(getActivity=lambda: True, getLayerData=lambda: object())
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
            start = time.monotonic()
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
                    return (time.monotonic() - start) * 1000.0
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

    @staticmethod
    def _generated_gcode(layers, motions):
        lines = ["; generated seek benchmark", "G90"]
        for layer in range(layers):
            lines.append(";LAYER:%d" % layer)
            for m in range(motions):
                lines.append("G1 X%.2f Y%.2f E0.02"
                             % ((m % 200) * 0.5, (m // 200) * 0.4))
        return "\n".join(lines).encode("utf-8")

    def test_mesh_observation_flows_through_coordinator_not_monitor(self):
        model = self.monitor()
        status = self.status()
        status["bed_mesh"] = {"mesh_matrix": [[0, 1], [2, 3]], "mesh_min": [0, 0], "mesh_max": [10, 10]}
        self.deliver(status)
        self.assertTrue(self.follower.bed_mesh.snapshot)
        # Monitor deactivation must not touch the shared mesh observation;
        # the coordinator is its only writer.
        model.setMonitoringActive(False)
        model._controls.observe()
        self.assertTrue(self.follower.bed_mesh.snapshot)
        self.follower.client.stop()
        self.assertFalse(self.follower.bed_mesh.snapshot)

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
        with patch.object(module.PrintStartOwner, "FILE_PRINT_START_TIMEOUT_S", -1.0):
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
        with patch.object(module.PrintStartOwner, "FILE_PRINT_START_TIMEOUT_S", -1.0):
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
        with patch.object(model._file_manager, "_fetch_thumb") as fetch:
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
        with patch.object(model._file_manager, "upload_file", return_value=True):
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
        with patch.object(model._file_manager, "start_print") as start_print:
            model.fileConfirmPrint()
            start_print.assert_not_called()
        self.assertEqual(model.fileUploadProgress, "")
        model._on_upload_progress(40)  # must not raise
        model._on_upload_finished(True, "bench.gcode")  # must not raise
        self.assertEqual(model.fileUploadProgress, "")

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

    def test_manual_reconnect_cycles_the_client(self):
        # A live request: a Reconnect that recovers a UI
        # stuck after a printer error — the client cycle runs even
        # when disconnected (unlike the e-stop's connected-only
        # auto-recovery).
        model = self.monitor()
        with patch.object(model._data._client, "stop") as stop, \
             patch.object(model._data._client, "start") as start:
            model.reconnect()
        stop.assert_called_once()
        start.assert_called_once()
        self.assertIn("Reconnecting", model.actionStatus)

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
        with patch("os.path.getsize", return_value=400 * 1048576):
            model.fileUpload("/tmp/big.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("MB free" in line for line in lines))
        # A REAL zero (full disk) refuses; only a missing report
        # (None) stands the check down (the adversarial round's
        # catch: the old guard treated the two alike).
        model._file_manager.disk_usage["free"] = 0
        with patch("os.path.getsize", return_value=1048576):
            model.fileUpload("/tmp/one-mb.gcode")
        self.assertEqual(model.fileUploadConfirm, "")
        lines = [entry["text"] for entry in model._console.values["consoleLines"]]
        self.assertTrue(any("MB free" in line for line in lines))
        model._file_manager.disk_usage.clear()
        with patch.object(model, "_start_upload") as start:
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

    def test_qml_public_api_is_present_without_model_subclasses(self):
        model = self.monitor()
        properties = "monitorState monitorConnected connectionDetail monitorFilename monitorProgress monitorLayer monitorElapsed monitorEta monitorFinish monitorSpeed monitorFlow monitorPosition monitorPositionCompact monitorVelocity monitorFlowRate monitorFlowDiameter monitorAccelLimit monitorMessage printActive printJobCaption canPausePrint canResumePrint pauseReason pauseReasonDetail resumeReason resumeReasonDetail canCancelPrint actionBusy actionStatus temperatureItems fanItems filamentSensorItems powerDevices klippyState moonrakerVersion klipperVersion hostLoad memoryAvailable cpuTemperature mcuSummary mcuItems webcamNames activeWebcamIndex cameraName cameraRotation cameraFlipHorizontal cameraFlipVertical cameraFps cameraFpsMin cameraFpsMax monitorLayerHeight macroNames hasQuadGantryLevel hasBedMesh canRunSetup temperaturePresetNames temperaturePresetItems canApplyTemperaturePreset speedFactorPercent flowFactorPercent zOffset zOffsetText fanControlItems ledItems pwmOutputItems saveConfigPending saveConfigSummary canSaveConfig emergencyStopClicks bedMeshAvailable bedMeshProfile bedMeshProfileNames bedMeshRows bedMeshColumns bedMeshValues bedMeshMinimum bedMeshMaximum bedMeshRange bedMeshXMin bedMeshXMax bedMeshYMin bedMeshYMax bedMeshRangeText bedMeshPreviewVisible bedMeshThresholdLow bedMeshThresholdHigh bedMeshMachineWidth bedMeshMachineDepth bedMeshCenterIsZero jogEnabled jogDistance extrudeDistance extrudeSpeed homedAxes positionMode jogStatus jogReason jogReasonDetail canRestart restartReason restartReasonDetail sectionReason sectionReasonDetail controlsLocked controlsCollapsed infoCollapsed statusCollapsed sectionLayout sectionHiddenMap consoleHeight cameraRefreshNonce cameraRecovering emergencyHoldProgress temperatureChartMini temperatureChartFull temperatureChartLatest temperatureChartLegend consoleHistory consolePending consoleErrorBell endstopItems endstopSummary monitorEtaBasis showProbePoints fileManagerRows fileManagerRecents fileManagerDirectory fileManagerDirectories fileManagerDiskText fileManagerRefreshedAt fileManagerShown fileManagerPage fileManagerPageIndex fileManagerPageCount fileManagerPageSize fileManagerPageSelection fileManagerEmptyKind fileManagerSelected fileManagerSortColumn fileManagerSortAscending fileManagerSearch fileManagerOpen fileManagerFilters filePrintConfirm fileDeleteConfirm fileRenameTarget fileRenameConflict fileUploadConfirm fileUploadProgress fileManagerThumbs fileManagerFilterCounts fileManagerFilterOptions fileManagerHistoryLoaded fileManagerHistoryExhausted fileManagerWalkError fileManagerNote".split()
        meta = model.metaObject()
        for name in properties: self.assertGreaterEqual(meta.indexOfProperty(name), 0, name)
        for name in "pausePrint resumePrint cancelPrint reconnect refreshAll refreshWebcams selectWebcam setCameraFps runMacro homeAll runQuadGantryLevel calibrateBedMesh applyTemperaturePreset setSpeedFactor setFlowFactor adjustZOffset clearZOffset setFanSpeed setLedBrightness setLedColor setPwmOutput saveConfig emergencyStopClick emergencyHoldStarted emergencyHoldReleased loadBedMeshProfile clearBedMesh setBedMeshPreviewVisible setBedMeshThresholds macroParameterDefinitions jog setJogDistance setExtrudeDistance setExtrudeSpeed home motorsOff centerToolhead zToZero extrude heatersOff firmwareRestart klipperRestart hostRestart setControlsLocked setControlsCollapsed setInfoCollapsed setStatusCollapsed setSectionLayout sectionLayoutFor setConsoleHeight setTemperatureSensorVisible setTemperatureSensorColor setShowTemperatureTargets setShowTemperaturePower sendConsoleCommand clearConsoleHistory improveEta setShowProbePoints openFileManager refreshFileManager fileNavigateTo setFileSearch setFileSort setFileManagerOpen setPositionMode setFilePageSize setFilePage setFileFilter clearFileFilters toggleFileSelection toggleFilePageSelection clearFileSelection fileLoadAllHistory fileScanMetadata fileRequestDelete fileRequestDeleteFile fileRequestDeleteDir fileCreateDirectory fileConfirmDelete fileCancelDelete fileRequestRename fileRequestRenameDir filePreviewRename fileConfirmRename fileCancelRename fileUpload fileConfirmUpload fileCancelUpload fileUploadDismiss fileClearWalkError fileRequestVisibleThumbnails".split():
            self.assertTrue(any(bytes(meta.method(i).name()).decode() == name for i in range(meta.methodCount())), name)
        self.assertEqual(type(model).__bases__[0].__name__, "PrinterModel")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class NativeRenderSchedulerTests(unittest.TestCase):
    """The round-4 render architecture: per-surface contexts, bounded demand scheduling,
    ghost state, idempotent setters and scheduler hygiene."""

    def setUp(self):
        context = runtime()
        self.qt = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.transport = ScriptedTransport()
        root = self.qt.load("FollowerRuntime")
        real = root.MoonrakerClient
        self.app = self.qt.Application()
        self.socket = ScriptedSocket()
        with patch.object(root, "MoonrakerClient", lambda parent: real(parent, transport=self.transport, socket=self.socket)):
            self.follower = self.qt.load("MoonrakerPrintFollower").MoonrakerPrintFollower(self.app)
        self.addCleanup(self.qt.events)
        self.addCleanup(self.follower.deinitialize)
        self.config_type = self.qt.load("PrinterConfig").PrinterConfig
        self.follower.apply_printer_config(self.config_type(url="http://printer-a", path_follow=False, feed_mode="http"))

    def monitor(self):
        output = self.qt.load("MoonrakerOutputDevicePlugin").MoonrakerOutputDevicePlugin(self.app, self.follower)
        output.start()
        self.addCleanup(output.stop)
        return output._current.activePrinter

    @staticmethod
    def _payload(motions=2):
        points = [[float(i), 0.0, float(i)] for i in range(motions + 1)]
        return {"classes": {"SKIN": [points]},
                "travels": [], "travelStarts": [], "travelEnds": [], "motions": motions}

    @staticmethod
    def _dense(motions=200000):
        points = [[i * 0.5 % 240.0, 2.0, float(i)] for i in range(motions + 1)]
        return {"classes": {"FILL": [points]},
                "travels": [], "travelStarts": [], "travelEnds": [], "motions": motions}

    def _feed(self, model, name, width=400, height=300, compact=False, pan_x=0.0, pan_y=0.0):
        model.setFollowerPlot(name, 0.0, 0.0, 1.0, 1.0, 0.0, float(height))
        model.setFollowerView(name, 1.0, 0.7, width, height, compact, pan_x, pan_y)
        self.qt.events(5)  # the staged flush

    def _window(self, model, name, anchor, payload=None):
        surface = model._plate_surfaces[name]
        payload = payload or self._payload()
        model._qt_window(surface, {"prev": payload if anchor > 0 else None,
                                   "current": payload, "next": payload}, anchor)

    def _hot(self, surface, layer):
        wrapped = surface.layers.get(layer)
        return wrapped is not None and wrapped.rasterValid

    def _pump_rasters(self, model, name, timeout=400, deadline_s=30.0):
        """Drive the event loop until the surface's desired window is
        fully rasterised (the workers deliver through the queued
        bridge signals).

        The claim is that the rasters SETTLE, not that they settle
        within a number of loop turns — and a loaded runner spends
        longer per turn than an idle one while the worker delivering
        through the queued bridge is asynchronous either way. The turn
        budget stays as the floor (an idle machine returns in a handful
        of turns); the wall-clock deadline is what decides, so a slow
        machine waits rather than fails and a genuine hang still fails
        rather than hangs (the Windows CI flake, 2026-09-24)."""
        surface = model._plate_surfaces[name]

        def settled():
            if surface.job is not None:
                return False
            desired = surface.desired
            if desired is None:
                return True
            if not self._hot(surface, desired["current"]):
                return False
            return all(self._hot(surface, layer) for layer in desired["ghosts"].values())

        started = time.monotonic()
        for _ in range(timeout):
            self.qt.events(5)
            if settled():
                return
        while time.monotonic() - started < deadline_s:
            self.qt.events(5)
            if settled():
                return
        self.fail("the rasters did not settle")

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
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def blocked_prefix(payload, plot, view, split, cancel=None,
                          previous=None, previous_split=0):
            calls.append(split)
            entered.set()
            release.wait(10)
            return real_prefix(payload, plot, view, split, cancel=cancel,
                               previous=previous, previous_split=previous_split)

        with patch.object(module, "render_layer_prefix", blocked_prefix):
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
                           "state": "submitted", "cancel": threading.Event(),
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
        entered = threading.Event()
        release = threading.Event()

        def blocked_prefix(payload, plot, view, split, cancel=None,
                          previous=None, previous_split=0):
            entered.set()
            release.wait(10)
            return real_prefix(payload, plot, view, split, cancel=cancel,
                               previous=previous, previous_split=previous_split)

        with patch.object(module, "render_layer_prefix", blocked_prefix):
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
        from PyQt6.QtCore import QUrl
        self.assertTrue(surface.nav["url"], "the navigation raster never warmed")
        self.assertTrue(os.path.exists(
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
        # promotes a new URL and the old file unlinks.
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 120)
        self._pump_rasters(model, "popover")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and surface.nav["url"] == nav_url:
            self.qt.events(5)
            time.sleep(0.01)
        self.assertNotEqual(surface.nav["url"], nav_url,
                            "the split change never updated the navigation raster")
        self.assertFalse(os.path.exists(QUrl(nav_url).toLocalFile()),
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
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and surface.nav["url"] == nav_url:
                self.qt.events(5)
                time.sleep(0.01)
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
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

    def test_a_stale_nav_cancellation_never_clears_the_new_job(self):
        # The review's finding: a superseded job's late terminal
        # cleared the slot a NEWER job owned (the epoch matched).
        # The serial gates every terminal path.
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        surface.nav["job"] = {"key": ("k",), "cancel": None,
                              "epoch": surface.job_epoch, "serial": 7}
        with patch.object(model, "_schedule_navigation") as schedule:
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
        with patch.object(model, "_schedule_navigation") as schedule:
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

        with patch.object(module, "_RasterJob", CountingJob), \
                patch.object(module, "render_navigation_layer", failing):
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
        self.assertTrue(surface.nav["url"],
                        "the new print never warmed its own raster")

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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and surface.nav["url"] == old_url:
            self.qt.events(5)
            time.sleep(0.01)
        self.assertEqual(model._navigation_data_value(surface), surface.nav["url"],
                         "the replacement raster never became eligible")

    def test_the_navigation_asset_survives_the_prune_until_retired(self):
        from PyQt6.QtCore import QUrl
        model = self.monitor()
        self._feed(model, "popover", width=400, height=300)
        surface = model._plate_surfaces["popover"]
        payload = self._payload(400)
        model._qt_window(surface, {"prev": payload, "current": payload, "next": None},
                         5, "motion index", 50)
        self._pump_rasters(model, "popover")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(5)
            time.sleep(0.01)
        nav_file = QUrl(surface.nav["url"]).toLocalFile()
        self.assertTrue(os.path.exists(nav_file))
        # A prune that keeps NOTHING still keeps the retained asset.
        model._prune_raster_cache(keep=0)
        self.assertTrue(os.path.exists(nav_file),
                        "the prune unlinked the retained navigation asset")
        # The retirement drops the protection; the next prune collects.
        model._retire_navigation(surface)
        model._prune_raster_cache(keep=0)
        self.assertFalse(os.path.exists(nav_file),
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

        with patch.object(module, "_RasterJob", CountingJob), \
                patch.object(module, "png_file", return_value=""):
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and surface.nav["key"] != key_b:
            self.qt.events(5)
            time.sleep(0.01)
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
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and surface.nav["key"] != key_a:
            self.qt.events(5)
            time.sleep(0.01)
        self.assertEqual(surface.nav["key"], key_a,
                         "the re-demanded content never promoted")

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
                       "state": "running", "cancel": threading.Event(),
                       "epoch": epoch, "serial": 10}
        old_ticket = ("popover", 5, 1, generation, key, "full", None, epoch, 10)
        model._retire_surface(surface)

        surface.tokens[5] = 1
        surface.job = {"layer": 5, "token": 1, "generation": generation,
                       "state": "submitted", "cancel": threading.Event(),
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
        with patch.object(module, "render_layer_raster", explode):
            self._window(model, "popover", 5)
            for _ in range(60):
                self.qt.events(5)
                if surface.job is None:
                    break
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
        # refresh (the detached 114% burn).
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
            self.assertEqual((service._full_next, len(advances), len(windows)),
                             first, "the same anchor re-ran the seek focus")
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
        old = os.path.join(directory, "old.png")
        live = os.path.join(directory, "live.png")
        temp = os.path.join(directory, "job.png.tmp-99")
        for path in (old, live, temp):
            with open(path, "wb") as handle:
                handle.write(b"x")
        from PyQt6.QtCore import QUrl
        model._plate_surfaces["popover"].layers[5]._raster_data = \
            QUrl.fromLocalFile(live).toString()
        model._prune_raster_cache(keep=0)
        self.assertFalse(os.path.exists(old), "the unreferenced file survived")
        self.assertTrue(os.path.exists(live), "the live reference was unlinked")
        self.assertTrue(os.path.exists(temp), "the in-flight temp was unlinked")
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
        sub = os.path.join(directory, "sub")
        os.makedirs(sub, exist_ok=True)
        live = os.path.join(directory, "spelled-live.png")
        with open(live, "wb") as handle:
            handle.write(b"x")
        from PyQt6.QtCore import QUrl
        # The wrapper's own spelling of the file: QUrl keeps the '..'
        # through the round trip, which is the shape the Windows leg's
        # '/' spelling takes on this platform.
        spelled = os.path.join(sub, "..", "spelled-live.png")
        model._plate_surfaces["popover"].layers[5]._raster_data = \
            QUrl.fromLocalFile(spelled).toString()
        model._prune_raster_cache(keep=0)
        self.assertTrue(os.path.exists(live),
                        "the reference's own spelling did not protect the file")
        surface = model._plate_surfaces["popover"]
        if surface.job is not None:
            surface.job["cancel"].set()
        for _ in range(200):
            self.qt.events(5)
            if surface.job is None:
                break

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
        self.assertEqual(os.listdir(model_b._raster_cache_dir), [],
                         "model A wrote into model B's directory")
        self.assertGreater(len(os.listdir(model_a._raster_cache_dir)), 0)

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
        self.assertTrue(os.path.exists(QUrl(first).toLocalFile()),
                        "the earlier prefix's file vanished")
        self.assertTrue(os.path.exists(QUrl(wrapped.prefixData).toLocalFile()))

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
        model.setFollowerView("popover", 1.5, 0.7, 563, 492, False, 0.0, 0.0)
        self.qt.events(5)
        self.assertFalse(wrapped.prefixValid,
                         "the view change left the old prefix valid")

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


class AttachCadenceTests(NativeRenderSchedulerTests):
    """The attached live-follow cadences: a continuously advancing
    split must NOT re-bake the 4x navigation raster or the native
    prefix per poll (the dire-follow report). The nav bake runs at
    most once per start-time window and commits as a slightly older,
    compatible raster; the prefix checkpoints on its own cadence
    while the QML tail accumulates. A fake clock simulates the 30 s
    print deterministically — the workers stay real."""

    POLL_S = 0.75
    POLLS = 40  # 30 s of print at the monitor's poll cadence

    def _attached(self, model, name="popover", width=400, height=300):
        """Attach, open the popover, feed the surface, and install the
        deterministic clock plus the render-start counters."""
        self._feed(model, name, width=width, height=height)
        model.setFollowerAttached(True)
        model.setFollowerPopoverOpen(True)
        surface = model._plate_surfaces[name]
        module = self.qt.load("MoonrakerMonitorModel")
        clock = _FakeClock(module)
        starts = {"nav": [], "prefix": []}
        real_nav = module.render_navigation_layer
        real_prefix = module.render_layer_prefix

        def nav(window, plot, view, split=None, **kwargs):
            starts["nav"].append((clock.t, split))
            return real_nav(window, plot, view, split, **kwargs)

        def prefix(payload, plot, view, split, **kwargs):
            starts["prefix"].append((clock.t, split))
            return real_prefix(payload, plot, view, split, **kwargs)

        self.patches = [patch.object(module, "time", clock),
                        patch.object(module, "render_navigation_layer", nav),
                        patch.object(module, "render_layer_prefix", prefix)]
        for entry in self.patches:
            entry.start()
        self.addCleanup(lambda: [entry.stop() for entry in self.patches])
        armed = []
        # The wake seam: the tests fire the wakes themselves at the
        # fake clock's deadlines — a real singleShot would wait out
        # the (simulated) window.
        model._nav_arm_wake = lambda s: armed.append((s, s.nav.get("wake_at"))) or None
        # Drop the instance attribute on cleanup: re-attaching the
        # class function here stored it UNBOUND on the instance, so
        # every later call passed the surface as self and the arm
        # died with a missing-surface TypeError.
        self.addCleanup(lambda: model.__dict__.pop("_nav_arm_wake", None))
        return model, surface, clock, armed, starts

    @staticmethod
    def _fire_due_wakes(model, armed, clock):
        due = [entry for entry in armed
               if entry[1] is not None and clock.t >= entry[1]]
        armed[:] = [entry for entry in armed if entry not in due]
        for surface, deadline in due:
            model._nav_wake(surface, deadline)

    @staticmethod
    def _drain_job(model, surface, qt, timeout=400):
        for _ in range(timeout):
            qt.events(6)
            if surface.job is None and surface.nav["job"] is None:
                return
        raise AssertionError("the render queue never drained")

    def _poll(self, model, surface, payload, anchor, split, clock, armed, qt):
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None}, anchor, "motion index", split)
        clock.t += self.POLL_S
        self._fire_due_wakes(model, armed, clock)
        qt.events(6)

    @staticmethod
    def _spacing(starts, minimum):
        for (a, _), (b, _) in zip(starts, starts[1:], strict=False):
            if b - a < minimum:
                raise AssertionError(
                    "two starts %ss apart, the cadence is %ss" %
                    (round(b - a, 2), minimum))

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
        self.assertEqual(model._navigation_data_value(surface),
                         surface.nav["url"],
                         "the compatible raster retired on a split advance")
        model.setFollowerAttached(False)
        self.assertEqual(model._navigation_data_value(surface), "",
                         "the detached demand tolerates a stale split")
        model.setFollowerAttached(True)
        self.assertLess(painted, 300,
                        "the compatible raster was not the older one")

    def test_attached_nav_failure_retries_once_per_window(self):
        # A failing warm render must not hot-retry per poll: the
        # hard-key latch holds for the window, one retry per expiry,
        # and the recovery after the window paints the raster.
        model = self.monitor()
        module = self.qt.load("MoonrakerMonitorModel")

        def failing(window, plot, view, split=None, **kwargs):
            raise RuntimeError("injected navigation render failure")

        model, surface, clock, armed, starts = self._attached(model)
        starts["nav"].clear()
        with patch.object(module, "render_navigation_layer", failing):
            payload = self._payload(600)
            for poll in range(self.POLLS):
                self._poll(model, surface, payload, 5, 20 + poll * 14,
                           clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        self.assertEqual(surface.nav["url"], "", "a failed render promoted")
        self.assertLessEqual(len(starts["nav"]),
                             self.POLLS * self.POLL_S / 3.0 + 2,
                             "the failing nav render hot-retried per poll")
        self._spacing(starts["nav"], 2.9)
        # The recovery: the next window's wake renders for real.
        clock.t += 5.0
        self._fire_due_wakes(model, armed, clock)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not surface.nav["url"]:
            self.qt.events(6)
            time.sleep(0.01)
        self.assertTrue(surface.nav["url"],
                        "the recovered render never painted the raster")

    def test_a_hard_scene_change_bypasses_the_nav_window(self):
        # A zoom change genuinely re-bakes the scene: it must not
        # wait out the follow window.
        model = self.monitor()
        model, surface, clock, armed, starts = self._attached(model)
        payload = self._payload(600)
        self._poll(model, surface, payload, 5, 50, clock, armed, self.qt)
        self._drain_job(model, surface, self.qt)
        before = len(starts["nav"])
        model.setFollowerView("popover", 2.0, 0.7, 400, 300, False, 0.0, 0.0)
        self.qt.events(5)
        self._drain_job(model, surface, self.qt)
        self.assertGreater(len(starts["nav"]), before,
                           "the zoom change waited out the follow window")

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
        clock.t += 5.0
        self._poll(model, surface, payload, 5, final_split, clock, armed,
                   self.qt)
        self._drain_job(model, surface, self.qt)
        wrapped = surface.layers[5]
        self.assertGreater(wrapped.prefixSplit, 0,
                           "the attached prefix never checkpointed")
        self.assertEqual(wrapped.prefixSplit, final_split,
                         "the last checkpoint painted a stale split")
        self.assertLessEqual(len(starts["prefix"]),
                             self.POLLS * self.POLL_S / 5.0 + 2,
                             "the prefix advanced far more than once per cadence")
        self._spacing(starts["prefix"], 4.9)

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
        surface = model._plate_surfaces["popover"]
        payload = self._payload(600)
        self._window(model, "popover", 5, payload)
        model._qt_window(surface, {"prev": None, "current": payload,
                                   "next": None}, 5, "motion index", 100)
        self._pump_rasters(model, "popover")
        self.assertFalse(model._prefix_wanted(surface, 5, 150),
                         "a sub-threshold advance re-rendered the prefix")
        self.assertTrue(model._prefix_wanted(surface, 5, 250),
                        "a past-threshold advance left the prefix stale")


class _FakeClock:
    """The deterministic test clock: only `monotonic` is simulated
    (the model's cadences read it); every other attribute delegates
    to the real module."""

    def __init__(self, module):
        self.t = 1000.0
        self._real = module.time

    def monotonic(self):
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


class RendererOnlySeekBenchmarks(NativeRenderSchedulerTests):
    """A RENDERER/SCHEDULER microbenchmark, not an end-to-end latency
    proof: the payloads arrive pre-decoded via _qt_window(), so the
    request/classify/read/decode/prepare/coordinator/publish stages
    are NOT in these numbers. The true slider-to-available and
    slider-to-picture measurements live in the end-to-end
    benchmarks."""

    def _time_seek(self, model, name, anchor, payload, split=None):
        surface = model._plate_surfaces[name]
        start = time.monotonic()
        model._qt_window(surface, {"prev": payload if anchor > 0 else None,
                                   "current": payload, "next": payload},
                         anchor, "motion index", split)
        self._pump_rasters(model, name)
        return (time.monotonic() - start) * 1000.0

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
        start = time.monotonic()
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
        total = (time.monotonic() - start) * 1000.0
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
                start = time.monotonic()
                url = png_file(image, "/tmp/mpf", "transport-probe-%s" % name)
                best = min(best, (time.monotonic() - start) * 1000.0)
            print("PNG transport %s: %6.1f ms" % (name, best))
            self.assertTrue(url.startswith("file://"))
            self.assertLess(best, 300.0, "the PNG transport stalled")


if __name__ == "__main__": unittest.main()




