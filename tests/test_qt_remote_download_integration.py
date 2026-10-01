"""Executable qt remotefileservicedownloadtests contracts."""
from tests import qt_integration_support as harness

class RemoteFileServiceDownloadTests(harness.RemoteFileServiceDownloadTests):
    def test_raw_cache_restarts_without_a_second_get(self):
        cache_module = self.qt.load("RawSourceCache")
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache = cache_module.RawSourceCache(root, 1024 * 1024 * 1024)
        self.files.bind_cache(cache)
        payload = b"G1 X10\n"
        self.files.bind(("part.gcode", len(payload), 1))
        reply = self._reply_double(payload=payload, size=len(payload))
        gets = []
        self.transport.network = harness.SimpleNamespace(
            get=lambda request: (gets.append(request), reply)[1])
        self.files.request_file()
        request = [r for r in self.transport.requests if r.channel == "metadata"][-1]
        request.callback({"result": {"size": len(payload), "modified": 1}}, None)
        self.assertTrue(self.files._source_metadata_verified)
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))
        self.assertTrue(self._wait(lambda: harness.os.path.isfile(
            cache._path(self.files.identity))))
        original_identity = self.files.identity
        lease = self.files.lease()
        self.files.close()
        self.assertTrue(harness.os.path.isfile(lease.path))
        lease.close()
        self.assertTrue(self._wait(lambda: not any(
            ".gcode.tmp-" in name for name in
            harness.os.listdir(harness.os.path.dirname(cache._path(original_identity))))))

        module = self.qt.load("RemoteFileService")
        restarted = module.RemoteFileService(self.transport)
        self.addCleanup(restarted.close)
        restarted.bind_cache(cache)
        restarted.bind(("part.gcode", len(payload), 2))
        restarted.request_file()
        requests = [r for r in self.transport.requests if r.channel == "metadata"]
        requests[-1].callback({"result": {"size": len(payload), "modified": 1}}, None)
        self.assertEqual(restarted.identity.stable_key(), original_identity.stable_key())
        self.assertTrue(harness.os.path.isfile(cache._path(restarted.identity)))
        self.assertTrue(self._wait(lambda: restarted.phase == "ready"))
        self.assertEqual(len(gets), 1)
        with open(restarted.path, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        cache.max_bytes = 1
        cache.prune()
        with open(restarted.path, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        harness.shutil.rmtree(root)
        with open(restarted.path, "rb") as handle:
            self.assertEqual(handle.read(), payload, "cache clear invalidated the live working file")
        self.assertFalse(cache.restore(
            restarted.identity, "part.gcode",
            harness.os.path.join(harness.os.path.dirname(restarted.path), "missing.gcode")))

    def test_metadata_change_during_download_refuses_stale_source(self):
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache = self.qt.load("RawSourceCache").RawSourceCache(root, 1024 * 1024)
        self.files.bind_cache(cache)
        self.files.bind(("part.gcode", 7, 1))
        self.files._identity = self._identity("part.gcode", 7)
        self.files._metadata_fetched = True
        self.files._source_metadata_verified = True
        reply = self._reply_double(payload=b"G1 X10\n", size=7)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        self.files._identity = self.qt.load("MoonrakerProtocol").RemoteFileIdentity(
            "part.gcode", 7, modified=2)
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "error"))
        self.assertFalse(harness.os.path.exists(cache._path(self._identity("part.gcode", 7))))
        self.assertIsNone(self.files.path)

    def test_unverified_or_changed_metadata_never_restores_raw_source(self):
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache = self.qt.load("RawSourceCache").RawSourceCache(root, 1024 * 1024)
        self.files.bind_cache(cache)
        original = self._identity("part.gcode", 7)
        source = harness.os.path.join(root, "seed.gcode")
        with open(source, "wb") as handle:
            handle.write(b"G1 X10\n")
        cache.unpin(cache.publish(original, original.filename, source))
        other = self._identity("other.gcode", 7)
        cache.unpin(cache.publish(other, other.filename, source))
        changed_size = self._identity("part.gcode", 8)
        source8 = harness.os.path.join(root, "seed-8.gcode")
        with open(source8, "wb") as handle:
            handle.write(b"G1 X100\n")
        cache.unpin(cache.publish(changed_size, changed_size.filename, source8))

        cases = (
            ("metadata failure", None, "offline"),
            ("no size", {"modified": 1}, None),
            ("no modified", {"size": 7}, None),
            ("uuid-only fallback", {"uuid": "transient"}, None),
            ("changed timestamp", {"size": 7, "modified": 2}, None),
            ("changed filename", {"filename": "other.gcode", "size": 7, "modified": 1}, None),
            ("listing size mismatch", {"size": 8, "modified": 1}, None),
        )
        for run, (label, metadata, error) in enumerate(cases, 1):
            with self.subTest(label=label):
                self.files.bind(("part.gcode", 7, run))
                gets = []
                reply = self._reply_double(payload=b"G1 X10\n", size=7)
                self.transport.network = harness.SimpleNamespace(
                    get=lambda request, gets=gets, reply=reply:
                        (gets.append(request), reply)[1])
                self.files.request_file()
                request = [r for r in self.transport.requests if r.channel == "metadata"][-1]
                request.callback({"result": metadata} if metadata is not None else None, error)
                self.assertEqual(self.files._source_metadata_verified,
                                 label == "changed timestamp")
                self.assertEqual(len(gets), 1, "stale source avoided a fresh GET")
                self.assertEqual(self.files.phase, "downloading")
                self.assertIsNone(self.files.path)

    def test_cross_volume_warm_restore_copies_off_ui_thread_with_progress(self):
        import errno
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache_module = self.qt.load("RawSourceCache")
        cache = cache_module.RawSourceCache(root, 32 * 1024 * 1024)
        size = 5 * 1024 * 1024
        source = harness.os.path.join(root, "seed.gcode")
        with open(source, "wb") as handle:
            handle.truncate(size)
        identity = self._identity("part.gcode", size)
        cache.unpin(cache.publish(identity, identity.filename, source))
        self.files.bind_cache(cache)
        self.files.bind(("part.gcode", size, 1))
        self.transport.network = harness.SimpleNamespace(
            get=lambda request: self.fail("warm restore issued a GET"))
        entered = harness.threading.Event()
        release = harness.threading.Event()
        self.addCleanup(release.set)
        original = cache.restore
        restore_threads = []

        def gated_restore(*args, **kwargs):
            restore_threads.append(harness.threading.current_thread())
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)

        progress = []
        self.files.restoreProgress.connect(lambda _op, copied: progress.append(copied))
        with harness.patch.object(cache, "restore", side_effect=gated_restore), \
                harness.patch.object(cache_module.os, "link",
                                     side_effect=OSError(errno.EXDEV, "cross-device link")):
            self.files.request_file()
            request = [r for r in self.transport.requests if r.channel == "metadata"][-1]
            started = harness.time.monotonic()
            request.callback({"result": {"size": size, "modified": 1}}, None)
            self.assertLess(harness.time.monotonic() - started, 0.5)
            self.assertTrue(entered.wait(2), "restore worker never started")
            self.assertNotEqual(restore_threads, [harness.threading.current_thread()])
            self.assertEqual(self.files.phase, "downloading")
            self.assertIsNone(self.files.path)
            release.set()
            self.assertTrue(self._wait(lambda: self.files.phase == "ready"))
        self.assertTrue(progress)
        self.assertEqual(progress[-1], size)
        self.assertEqual(harness.os.path.getsize(self.files.path), size)

    def test_rebinding_cancels_cross_volume_restore_without_a_stale_file(self):
        import errno
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache_module = self.qt.load("RawSourceCache")
        cache = cache_module.RawSourceCache(root, 1024 * 1024)
        identity = self._identity("part.gcode", 7)
        source = harness.os.path.join(root, "seed.gcode")
        with open(source, "wb") as handle:
            handle.write(b"G1 X10\n")
        cache.unpin(cache.publish(identity, identity.filename, source))
        self.files.bind_cache(cache)
        self.files.bind(("part.gcode", 7, 1))
        entered = harness.threading.Event()
        release = harness.threading.Event()
        self.addCleanup(release.set)
        original = cache.restore

        def gated_restore(*args, **kwargs):
            entered.set()
            release.wait(5)
            return original(*args, **kwargs)

        with harness.patch.object(cache, "restore", side_effect=gated_restore), \
                harness.patch.object(cache_module.os, "link",
                                     side_effect=OSError(errno.EXDEV, "cross-device link")):
            self.files.request_file()
            request = [r for r in self.transport.requests if r.channel == "metadata"][-1]
            request.callback({"result": {"size": 7, "modified": 1}}, None)
            self.assertTrue(entered.wait(2))
            directory = self.files._restore_op["directory"]
            self.files.bind(("other.gcode", 7, 2))
            release.set()
            self.assertTrue(self._wait(lambda: not harness.os.path.exists(directory)))
        self.assertIsNone(self.files.path)
        self.assertEqual(self.files.job_key[0], "other.gcode")

    def test_corrupt_warm_source_falls_back_to_one_get_not_restore_loop(self):
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache = self.qt.load("RawSourceCache").RawSourceCache(root, 1024 * 1024)
        identity = self._identity("part.gcode", 7)
        source = harness.os.path.join(root, "seed.gcode")
        with open(source, "wb") as handle:
            handle.write(b"G1 X10\n")
        cache.unpin(cache.publish(identity, identity.filename, source))
        with open(cache._path(identity), "wb") as handle:
            handle.write(b"bad")
        self.files.bind_cache(cache)
        self.files.bind(("part.gcode", 7, 1))
        gets = []
        reply = self._reply_double(payload=b"G1 X10\n", size=7)
        self.transport.network = harness.SimpleNamespace(
            get=lambda request: (gets.append(request), reply)[1])
        self.files.request_file()
        request = [r for r in self.transport.requests if r.channel == "metadata"][-1]
        request.callback({"result": {"size": 7, "modified": 1}}, None)
        self.assertTrue(self._wait(lambda: len(gets) == 1))
        self.assertTrue(self.files._cache_restore_failed)
        self.assertEqual(self.files.phase, "downloading")
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))
        self.assertEqual(len(gets), 1)

    def test_machine_cache_switch_retires_old_job_and_keeps_lease(self):
        root = harness.tempfile.mkdtemp(prefix="raw-cache-test-")
        self.addCleanup(harness.shutil.rmtree, root, True)
        cache_type = self.qt.load("RawSourceCache").RawSourceCache
        self.files.bind_cache(cache_type(harness.os.path.join(root, "a"), 1024))
        self.files.bind(("part.gcode", 7, 1))
        reply = self._reply_double(payload=b"G1 X10\n", size=7)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files._identity = self._identity("part.gcode", 7)
        self.files._metadata_fetched = True
        self.files._source_metadata_verified = True
        self.files.request_file()
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))
        old_folder = harness.os.path.dirname(self.files._cache._path(self.files.identity))
        self.assertTrue(self._wait(lambda: harness.os.path.exists(
            harness.os.path.join(old_folder, "source.gcode"))))
        lease = self.files.lease()
        self.files.bind_cache(cache_type(harness.os.path.join(root, "b"), 1024))
        self.assertIsNone(self.files.job_key)
        self.assertIsNone(self.files.path)
        with open(lease.path, "rb") as handle:
            self.assertEqual(handle.read(), b"G1 X10\n")
        lease.close()
        self.assertTrue(self._wait(lambda: not any(
            ".gcode.tmp-" in name for name in harness.os.listdir(old_folder))))
        self.files.bind(("other.gcode", 10, 3))
        self.files.bind_cache(None)  # unresolved machine invalidates the old job too
        self.assertIsNone(self.files.job_key)

    def test_metadata_only_request_is_identity_neutral(self):
        # 4.2.0 A5/H7: a metadata-only fetch must not touch the job
        # lane's identity, fetched/pending/attempts bits or its
        # metadata cache — a failing one would otherwise overwrite
        # the identity the download path depends on (the 4.0.2
        # hazard), and a successful one would silently hang the
        # download.
        self.files._metadata = {"sentinel": 1}
        self.files._identity = "job-identity"
        seen = []
        self.assertTrue(self.files.request_metadata_only(lambda result, error: seen.append((result, error))))
        for request in self.transport.requests:
            if getattr(request, "channel", "") == "metadata-only":
                request.callback({"result": {"estimated_time": 100}}, None)
                break
        else:
            self.fail("no metadata-only request left the transport")
        self.qt.events(10)
        self.assertEqual(seen, [({"estimated_time": 100}, None)])
        self.assertEqual(self.files._identity, "job-identity")
        self.assertEqual(self.files._metadata, {"sentinel": 1})
        self.assertFalse(self.files._metadata_fetched)
        self.assertFalse(self.files._metadata_pending)
        # A FAILING metadata-only request leaves the lane alone too.
        self.files.request_metadata_only(lambda result, error: seen.append((result, error)))
        for request in self.transport.requests:
            if getattr(request, "channel", "") == "metadata-only":
                request.callback(None, "not found")
                break
        self.qt.events(10)
        self.assertEqual(self.files._identity, "job-identity")
        self.assertEqual(self.files._metadata, {"sentinel": 1})

    def test_failure_latches_until_the_backoff_window_passes(self):
        failures = []
        self.files.failed.connect(failures.append)
        self.files._fail("Connection refused")
        self.assertEqual(failures, ["Connection refused"])
        self.assertEqual(self.files.phase, "error")
        # Inside the window a consumer re-request must not hit the network.
        self.files.request_file()
        self.assertEqual(self.files.phase, "error")
        self.assertEqual(len(self.transport.requests), 0)
        # Past the window the next re-request restarts the download and a
        # successful reply completes it.
        self.files._download_retry_at = 0.0
        reply = self._reply_double()
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        self.assertEqual(self.files.phase, "downloading")
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))
        self.assertEqual(self.files._error, "")
        self.assertTrue(self.files.path and self.files.path.endswith("part.gcode"))

    def test_errored_reply_fails_and_schedules_a_retry(self):
        reply = self._reply_double(error=True)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "error"))
        self.assertEqual(self.files._download_attempts, 1)
        self.assertGreater(self.files._download_retry_at, harness.time.monotonic())

    def test_abort_retires_the_writer_without_a_gui_thread_join(self):
        gate = harness.threading.Event()  # unset: the writer parks on its first write
        GatedTarget = self._gated_target(gate)
        module = self.qt.load("RemoteFileService")
        files = module.RemoteFileService(self.transport, None, target_factory=GatedTarget.open)
        self.addCleanup(gate.set)
        self.addCleanup(files.close)
        files.bind(("part.gcode", 100, 1))
        files._identity = self._identity("part.gcode", 0)
        files._want_file = True
        reply = self._reply_double(payload=b"A" * 32)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        files.request_file()
        op = files._download
        reply.readyRead.emit()  # the writer parks on the first chunk
        start = harness.time.monotonic()
        files.bind(("other.gcode", 50, 2))  # abort while the writer is gated
        self.assertLess(harness.time.monotonic() - start, 1.0)  # returned immediately: no join
        self.assertIsNone(files._download)
        gate.set()  # release the parked writer
        self.assertIsNone(op._writer.join(timeout=2.0))  # the writer retired
        self.assertFalse(op._writer.is_alive())
        self.assertFalse(harness.os.path.exists(harness.os.path.dirname(op.target.path)))  # temp dir removed

    def test_a_stale_writer_never_writes_into_the_next_download(self):
        gate = harness.threading.Event()  # unset: writer A parks on its first write
        GatedTarget = self._gated_target(gate)
        module = self.qt.load("RemoteFileService")
        files = module.RemoteFileService(self.transport, None, target_factory=GatedTarget.open)
        self.addCleanup(gate.set)
        self.addCleanup(files.close)
        files.bind(("part.gcode", 100, 1))
        files._identity = self._identity("part.gcode", 0)
        files._want_file = True
        reply_a = self._reply_double(payload=b"A" * 64)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply_a)
        files.request_file()
        op_a = files._download
        reply_a.readyRead.emit()  # writer A parks on A's chunk
        files.bind(("other.gcode", 50, 2))  # aborts A while it is parked
        files._identity = self._identity("other.gcode", 0)
        files._want_file = True
        reply_b = self._reply_double(payload=b"B" * 48)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply_b)
        files.request_file()
        reply_b.readyRead.emit()
        reply_b.finished.emit()
        gate.set()  # stale writer A resumes against its own closed target
        self.assertTrue(self._wait(lambda: files.phase == "ready"))
        with open(files.path, "rb") as fh:
            self.assertEqual(fh.read(), b"B" * 48)  # exactly B's payload, in order
        self.assertFalse(harness.os.path.exists(harness.os.path.dirname(op_a.target.path)))

    def test_the_size_cap_is_per_attempt(self):
        self.files.MAX_DOWNLOAD_BYTES = 20
        reply = self._reply_double(payload=b"x" * 30)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        reply.readyRead.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "error"))
        self.assertIn("cap", self.files._error)
        # A retry with a smaller file must not inherit the first
        # attempt's bytes (a lifetime counter would trip the cap again).
        self.files.bind(("small.gcode", 10, 2))
        self.files._identity = self._identity("small.gcode", 0)
        self.files._want_file = True
        reply2 = self._reply_double(payload=b"y" * 10)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply2)
        self.files.request_file()
        reply2.readyRead.emit()
        reply2.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))
        with open(self.files.path, "rb") as fh:
            self.assertEqual(fh.read(), b"y" * 10)
        self.assertEqual(self.files._lifetime_received, 40)

    def test_fraction_uses_the_declared_length_and_resets_per_attempt(self):
        reply = self._reply_double(payload=b"z" * 40, size=100)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        reply.readyRead.emit()
        self.assertEqual(self.files.download_fraction, 0.4)
        # A fresh attempt starts at zero against its own declared length.
        self.files.bind(("again.gcode", 80, 2))
        self.files._identity = self._identity("again.gcode", 0)
        self.files._want_file = True
        reply2 = self._reply_double(payload=b"w" * 10, size=50)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply2)
        self.files.request_file()
        self.assertIsNone(self.files.download_fraction)  # indeterminate until the headers arrive
        reply2.readyRead.emit()
        self.assertEqual(self.files.download_fraction, 0.2)

    def test_partial_download_notifies_fraction_without_status_polling(self):
        module = self.qt.load("RemoteFileService")
        reply = self._reply_double(payload=b"", size=100)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        observed = []
        self.files.changed.connect(lambda: observed.append(self.files.download_fraction))
        self.files.request_file()
        clock = [10.0]
        with harness.patch.object(module.time, "monotonic", side_effect=lambda: clock[0]):
            reply._buffer = b"a" * 10
            reply.readyRead.emit()
            self.assertEqual(observed[-1], 0.1)
            count = len(observed)
            clock[0] = 10.1
            reply._buffer = b"b" * 10
            reply.readyRead.emit()
            self.assertEqual(len(observed), count)
            clock[0] = 10.21
            reply._buffer = b"c" * 10
            reply.readyRead.emit()
            self.assertEqual(observed[-1], 0.3)
        reply._buffer = b"d" * 70
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))

    def test_finish_returns_while_the_writer_is_still_gated(self):
        gate = harness.threading.Event()  # unset: the writer parks on its first write
        self.addCleanup(gate.set)
        GatedTarget = self._gated_target(gate)
        module = self.qt.load("RemoteFileService")
        files = module.RemoteFileService(self.transport, None, target_factory=GatedTarget.open)
        self.addCleanup(files.close)
        files.bind(("part.gcode", 100, 1))
        files._identity = self._identity("part.gcode", 0)
        files._want_file = True
        reply = self._reply_double(payload=b"a" * 16)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        files.request_file()
        reply.readyRead.emit()  # the writer parks on the first chunk
        start = harness.time.monotonic()
        reply.finished.emit()  # must return immediately: the GUI thread never joins
        self.assertLess(harness.time.monotonic() - start, 1.0)
        self.assertEqual(files.phase, "downloading")  # terminal still pending
        gate.set()  # release the parked writer
        self.assertTrue(self._wait(lambda: files.phase == "ready"))
        with open(files.path, "rb") as fh:
            self.assertEqual(fh.read(), b"a" * 16)

    def test_fraction_becomes_determinate_when_the_headers_arrive_late(self):
        # The response headers land with the first data, never at
        # reply creation: a creation-time Content-Length read is
        # empty and froze the progress as indeterminate for the whole
        # transfer (the harness's p1-06 lesson).
        from PyQt6.QtCore import QObject, pyqtSignal
        class LateReply(QObject):
            readyRead = pyqtSignal()
            finished = pyqtSignal()
            def __init__(self):
                super().__init__()
                self._chunks = [b"z" * 40, b"y" * 60]  # the declared 100 arrives in two events
                self._headers = False
            def setReadBufferSize(self, size): pass
            def readAll(self):
                return self._chunks.pop(0) if self._chunks else b""
            def rawHeader(self, name):
                if name == b"Content-Encoding":
                    return b""
                return b"100" if self._headers else b""
            def error(self):
                from PyQt6.QtNetwork import QNetworkReply
                return QNetworkReply.NetworkError.NoError
            def errorString(self): return ""
            def abort(self): pass
            def deleteLater(self): pass
        reply = LateReply()
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        self.files.request_file()
        self.assertIsNone(self.files.download_fraction)  # the headers have not arrived
        reply._headers = True  # they land with the first data event
        reply.readyRead.emit()
        self.assertEqual(self.files.download_fraction, 0.4)  # the lazy read catches them
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: self.files.phase == "ready"))

    def test_one_shot_cancel_delivers_exactly_once(self):
        reply = self._reply_double(payload=b"A" * 32, size=32)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        results = []
        download = self.files.download_once("prints/part.gcode", on_ready=lambda p, e: results.append((p, e)))
        reply.readyRead.emit()
        download.cancel()  # the session-invalidation hook
        reply.finished.emit()  # the ghost completion must not re-deliver
        self.assertEqual(len(results), 1)
        path, error = results[0]
        self.assertIsNone(path)
        self.assertIn("cancelled", error)
        self.assertNotIn(download, self.files._one_shots)

    def test_one_shot_constructor_failure_delivers_exactly_once(self):
        with harness.patch.object(harness.tempfile, "mkdtemp", side_effect=OSError("no space")):
            results = []
            self.files.download_once("prints/part.gcode", on_ready=lambda p, e: results.append((p, e)))
        self.assertEqual(results, [(None, "no space")])
        self.assertEqual(len(self.files._one_shots), 0)

    def test_one_shot_transport_switch_delivers_an_error_not_the_file(self):
        reply = self._reply_double(payload=b"B" * 16, size=16)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        results = []
        self.files.download_once("prints/part.gcode", on_ready=lambda p, e: results.append((p, e)))
        self.transport.identity = ("http://printer-b", "other-key")  # mid-stream switch
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: len(results) == 1))
        path, error = results[0]
        self.assertIsNone(path)
        self.assertIn("connection changed", error)

    def test_compressed_encoding_is_refused(self):
        # The identity-encoding contract (the critic's catch): a proxy
        # that ignores the request serves compressed bytes whose
        # length matches ITS declaration — the Content-Encoding
        # header is the tell, and the download must refuse.
        reply = self._reply_double(payload=b"A" * 32, size=32, content_encoding=b"gzip")
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        results = []
        self.files.download_once("prints/part.gcode", on_ready=lambda p, e: results.append((p, e)))
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: len(results) == 1))
        path, error = results[0]
        self.assertIsNone(path)
        self.assertIn("compressed", error)

    def test_listing_size_mismatch_is_refused(self):
        # The listing is the referee, not the transport: a proxy that
        # delivers honest headers but short bytes fails the listing's
        # own size check on finish.
        self.files.bind(("part.gcode", 200, 1))
        self.files._identity = self._identity("part.gcode", 200)
        self.files._want_file = True
        reply = self._reply_double(payload=b"x" * 40, size=40)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        messages = []
        self.files.failed.connect(messages.append)
        self.files.request_file()
        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: len(messages) == 1))
        self.assertIn("does not match the file listing", messages[0])

    def test_the_two_cancel_terminals_stay_distinct(self):
        # The reviewer's A at its source: the user's Cancel and an
        # invalidated session are different terminals, and the user's
        # never borrows the connection-change explanation. Both retire
        # their own temp directory as they deliver.
        reply = self._reply_double(payload=b"A" * 8, size=8)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        cancelled = []
        download = self.files.download_once(
            "prints/part.gcode", on_ready=lambda path, error: cancelled.append((path, error)))
        directory = download._directory
        download.cancel()
        self.assertEqual(cancelled, [(None, "The download was cancelled")])
        self.assertNotIn("connection", cancelled[0][1])
        self.assertFalse(harness.os.path.exists(directory))

        reply = self._reply_double(payload=b"A" * 8, size=8)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        invalidated = []
        self.files.download_once(
            "prints/part.gcode", on_ready=lambda path, error: invalidated.append((path, error)))
        self.files.cancel_one_shots()
        self.assertEqual(invalidated,
                         [(None, "The printer connection changed; the download was cancelled")])

    def test_a_real_save_replaces_the_picked_file(self):
        # End to end through the streamed lane: the temp file lands at
        # the picked path, an existing file is replaced, and nothing of
        # the transfer survives beside it.
        directory = harness.tempfile.mkdtemp(prefix="mpfxtest-save-")
        target = harness.os.path.join(directory, "saved.gcode")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("OLD\n")
        module, download = self._file_download()
        failures = []
        download.failed.connect(failures.append)
        reply = self._reply_double(payload=b"G1 X0\n", size=6)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        with harness.patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            self.assertTrue(download.request_save("prints/part.gcode"))
        self.assertIsNotNone(download.progress())  # the window is open for the transfer

        reply.readyRead.emit()
        reply.finished.emit()
        self.assertTrue(self._wait(lambda: download._save is None))

        self.assertEqual(failures, [])
        self.assertIsNone(download.progress())
        with open(target, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "G1 X0\n")
        self.assertEqual(sorted(harness.os.listdir(directory)), ["saved.gcode"])

    def test_the_popup_cancel_ends_a_real_save_with_the_user_message(self):
        target = self._save_target()
        module, download = self._file_download()
        failures = []
        download.failed.connect(failures.append)
        reply = self._reply_double(payload=b"A" * 8, size=8)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        with harness.patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            self.assertTrue(download.request_save("prints/part.gcode"))
        reply.readyRead.emit()

        download.cancel()

        self.assertEqual(failures, ["The download was cancelled"])
        self.assertIsNone(download.progress())
        self.assertFalse(harness.os.path.exists(target))
        self.assertEqual([name for name in harness.os.listdir(self.files._root) if name.startswith("file-")], [])

    def test_a_session_invalidation_mid_save_touches_no_destination(self):
        target = self._save_target(payload="OLD\n")
        module, download = self._file_download()
        failures = []
        download.failed.connect(failures.append)
        reply = self._reply_double(payload=b"A" * 8, size=8)
        self.transport.network = harness.SimpleNamespace(get=lambda request: reply)
        with harness.patch.object(module, "QFileDialog") as dialog:
            dialog.getSaveFileName.return_value = (target, "")
            self.assertTrue(download.request_save("prints/part.gcode"))
        reply.readyRead.emit()

        self.files.cancel_one_shots()  # the runtime's invalidation wire

        self.assertTrue(self._wait(lambda: download._save is None))
        self.assertEqual(failures, ["The printer connection changed; the download was cancelled"])
        with open(target, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "OLD\n")
        self.assertEqual([name for name in harness.os.listdir(self.files._root) if name.startswith("file-")], [])
