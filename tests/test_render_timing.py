"""GPU timing contracts: bounded, asynchronous, opt-in and failure-contained."""
import ctypes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mpf.diagnostics.RenderTiming import RenderTiming, _GLQueries


class RenderTimingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.marker = Path(folder.name) / "timing"
        self.marker.touch()
        self.now = 0.
        self.backend = Mock(spec=_GLQueries)
        self.backend.create.side_effect = list(range(1, 30))
        self.backend.available.return_value = False
        self.backend.result.return_value = 2_000_000
        self.factory = Mock(return_value=self.backend)
        self.logger = Mock()
        self.timer = RenderTiming(self.marker, clock=lambda: self.now,
                                  backend_factory=self.factory, logger=self.logger)

    def test_disabled_marker_never_constructs_gl_and_preserves_callback(self):
        self.marker.unlink()
        timer = RenderTiming(self.marker, backend_factory=self.factory)
        self.assertEqual(timer.measure("head", lambda: 17), 17)
        self.factory.assert_not_called()
        with self.assertRaisesRegex(ValueError, "render"):
            timer.measure("head", lambda: (_ for _ in ()).throw(ValueError("render")))
        self.marker.touch()
        timer.measure("head", lambda: None)
        self.factory.assert_not_called()  # Admission is checked once.

    def test_default_marker_and_admission_errors_disable_silently(self):
        with patch.object(Path, "is_file", autospec=True, return_value=True) as admitted:
            self.assertTrue(RenderTiming().enabled)
            admitted.assert_called_once_with(Path(__file__).resolve().parents[1] / "mpf" / "toolhead" / ".profile-rendering")
        with patch.object(Path, "is_file", side_effect=OSError("missing")):
            self.assertFalse(RenderTiming(self.marker).enabled)
        self.assertFalse(RenderTiming(self.marker, clock=Mock(side_effect=ValueError("clock"))).enabled)

    def test_sampling_is_globally_one_per_second_and_pending_queries_are_bounded(self):
        for now in (0, .2, 1.1, 2.2, 3.3, 100.):
            self.now = now
            self.assertEqual(self.timer.measure("head", lambda: "drawn"), "drawn")
        self.assertEqual(self.backend.create.call_count, 2)
        self.assertEqual(len(self.timer._pending), 2)
        self.backend.result.assert_not_called()
        self.backend.delete.assert_not_called()

    def test_two_passes_alternate_and_extra_labels_cannot_grow_state(self):
        self.backend.available.return_value = True
        for now in (0., 1.1, 2.2, 3.3, 4.4):
            self.now = now
            for name in ("scene", "head", "extra"):
                self.timer.measure(name, lambda: None)
        self.assertEqual(self.timer._names, ["scene", "head"])
        self.assertEqual(self.backend.create.call_count, 5)
        self.assertEqual(self.timer._totals["scene"][0], 3)
        self.assertEqual(self.timer._totals["head"][0], 2)

    def test_completed_results_are_collected_without_blocking_and_aggregated(self):
        self.timer.measure("head", lambda: None)
        self.backend.available.return_value = True
        self.now = 10.1
        self.timer.measure("head", lambda: None)
        self.backend.result.assert_called_once_with(1)
        self.backend.delete.assert_called_once_with(1)
        self.logger.assert_called_once_with("head: 1 samples, mean 2.000 ms, max 2.000 ms")
        self.assertEqual(self.timer._totals, {})

    def test_default_logger_identifies_own_commands_and_empty_intervals_do_not_log(self):
        self.timer._logger = None
        logger = Mock()
        with patch.dict(sys.modules, {"UM.Logger": SimpleNamespace(Logger=logger)}):
            self.now = 10.1
            self.timer.measure("head", lambda: None)
            logger.log.assert_not_called()
            self.backend.available.return_value = True
            self.now = 10.2
            self.timer.measure("head", lambda: None)
            logger.log.assert_called_once_with("i", "toolhead GPU timing (own commands): %s",
                                               "head: 1 samples, mean 2.000 ms, max 2.000 ms")

    def test_callback_failure_propagates_but_always_ends_gpu_query(self):
        with self.assertRaisesRegex(ValueError, "render"):
            self.timer.measure("head", lambda: (_ for _ in ()).throw(ValueError("render")))
        self.backend.end.assert_called_once_with()
        self.assertEqual(self.timer._pending, [("head", 1)])

    def test_factory_clock_begin_and_allocation_failures_cannot_break_rendering(self):
        for target in (self.factory, self.backend.create, self.backend.begin):
            with self.subTest(target=target):
                target.side_effect = RuntimeError("diagnostic")
                timer = RenderTiming(self.marker, clock=lambda: 0., backend_factory=self.factory)
                self.assertEqual(timer.measure("head", lambda: 42), 42)
                self.assertFalse(timer.enabled)
                target.side_effect = None
        timer = RenderTiming(self.marker, backend_factory=self.factory)
        timer._clock = Mock(side_effect=RuntimeError("clock"))
        self.assertEqual(timer.measure("head", lambda: 42), 42)
        self.assertFalse(timer.enabled)

    def test_poll_result_delete_and_logger_failures_disable_and_keep_callback(self):
        for stage in ("available", "result", "delete", "logger"):
            with self.subTest(stage=stage):
                backend = Mock(spec=_GLQueries)
                backend.create.return_value = 1
                backend.available.return_value = True
                backend.result.return_value = 1_000_000
                logger = Mock()
                self.now = 0.
                timer = RenderTiming(self.marker, clock=lambda: self.now,
                                     backend_factory=lambda backend=backend: backend, logger=logger)
                timer.measure("head", lambda: None)
                (logger if stage == "logger" else getattr(backend, stage)).side_effect = ValueError(stage)
                self.now = 20.
                self.assertEqual(timer.measure("head", lambda: "visible"), "visible")
                self.assertFalse(timer.enabled)
                self.assertEqual(timer._pending, [])

    def test_end_and_cleanup_failures_preserve_render_error(self):
        self.backend.end.side_effect = RuntimeError("end")
        self.backend.delete.side_effect = RuntimeError("delete")
        with self.assertRaisesRegex(ValueError, "render"):
            self.timer.measure("head", lambda: (_ for _ in ()).throw(ValueError("render")))
        self.assertFalse(self.timer.enabled)
        self.assertEqual(self.timer._pending, [])

    def test_nested_failure_cannot_leave_outer_query_pending_when_disabled(self):
        def nested():
            self.timer._clock = Mock(side_effect=RuntimeError("nested"))
            return self.timer.measure("head", lambda: 9)
        self.assertEqual(self.timer.measure("head", nested), 9)
        self.backend.end.assert_called_once_with()
        self.backend.delete.assert_called_once_with(1)
        self.assertFalse(self.timer.enabled)
        self.assertEqual(self.timer._pending, [])


class RawGLQueryTests(unittest.TestCase):
    def setUp(self):
        self.current = 0
        self.name = 37
        self.result = 8_000_000_001
        self.ready = 1
        self.deleted = []
        self.callbacks = {}
        def function(name, arguments, callback):
            self.callbacks[name] = ctypes.CFUNCTYPE(None, *arguments)(callback)
        pointer = ctypes.POINTER
        function(b"glGenQueries", (ctypes.c_int, pointer(ctypes.c_uint)), lambda count, out: out.__setitem__(0, self.name))
        function(b"glDeleteQueries", (ctypes.c_int, pointer(ctypes.c_uint)), lambda count, names: self.deleted.append((count, names[0])))
        function(b"glBeginQuery", (ctypes.c_uint, ctypes.c_uint), lambda target, query: setattr(self, "current", query))
        function(b"glEndQuery", (ctypes.c_uint,), lambda target: setattr(self, "current", 0))
        function(b"glGetQueryiv", (ctypes.c_uint, ctypes.c_uint, pointer(ctypes.c_int)), lambda target, kind, out: out.__setitem__(0, self.current))
        function(b"glGetQueryObjectuiv", (ctypes.c_uint, ctypes.c_uint, pointer(ctypes.c_uint)), lambda query, kind, out: out.__setitem__(0, self.ready))
        function(b"glGetQueryObjectui64v", (ctypes.c_uint, ctypes.c_uint, pointer(ctypes.c_uint64)), lambda query, kind, out: out.__setitem__(0, self.result))
        self.context = SimpleNamespace(getProcAddress=lambda name: ctypes.cast(self.callbacks[name], ctypes.c_void_p).value,
            format=lambda: SimpleNamespace(majorVersion=lambda: 4, minorVersion=lambda: 1), hasExtension=lambda name: False)
        self.active = self.context
        surface = patch.dict(sys.modules, {"PyQt6.QtGui": SimpleNamespace(QOpenGLContext=SimpleNamespace(currentContext=lambda: self.active))})
        surface.start()
        self.addCleanup(surface.stop)

    def test_native_abi_preserves_64bit_results_and_checks_availability(self):
        queries = _GLQueries()
        query = queries.create()
        self.assertEqual(query, 37)
        queries.begin(query)
        self.assertEqual(self.current, query)
        queries.end()
        self.assertEqual(self.current, 0)
        self.assertTrue(queries.available(query))
        self.ready = 0
        self.assertFalse(queries.available(query))
        self.assertEqual(queries.result(query), 8_000_000_001)
        queries.delete(query)
        self.assertEqual(self.deleted, [(1, 37)])

    def test_unavailable_context_or_entrypoints_fail_without_query_allocation(self):
        self.active = None
        with self.assertRaisesRegex(RuntimeError, "context"): _GLQueries()
        self.active = SimpleNamespace(getProcAddress=lambda _: 0, format=self.context.format)
        with self.assertRaisesRegex(RuntimeError, "unavailable"): _GLQueries()

    def test_legacy_context_requires_timer_extension_before_any_gl_operation(self):
        self.context.format = lambda: SimpleNamespace(majorVersion=lambda: 2, minorVersion=lambda: 0)
        with self.assertRaisesRegex(RuntimeError, "unsupported"): _GLQueries()
        self.context.hasExtension = lambda name: name == b"GL_ARB_timer_query"
        self.assertEqual(_GLQueries().create(), 37)

    def test_context_replacement_rejects_all_old_query_operations(self):
        queries = _GLQueries()
        self.active = object()
        for operation in (queries.create, lambda: queries.begin(37), queries.end,
                          lambda: queries.available(37), lambda: queries.result(37), lambda: queries.delete(37)):
            with self.assertRaisesRegex(RuntimeError, "retired"): operation()
        self.assertEqual(self.deleted, [])

    def test_failed_allocation_and_existing_host_timer_are_rejected(self):
        queries = _GLQueries()
        self.name = 0
        with self.assertRaisesRegex(RuntimeError, "allocation"): queries.create()
        self.current = 91
        with self.assertRaisesRegex(RuntimeError, "host"): queries.begin(37)
        self.assertEqual(self.current, 91)

    def test_unsigned_query_names_survive_signed_current_query_getter(self):
        self.name = 0x80000001
        queries = _GLQueries()
        query = queries.create()
        queries.begin(query)
        self.assertEqual(query, self.name)
        queries.end()
        queries.delete(query)
        self.assertEqual(self.deleted, [(1, self.name)])

    def test_driver_refusing_begin_is_detected_without_waiting(self):
        queries = _GLQueries()
        queries._begin = lambda *args: None
        with self.assertRaisesRegex(RuntimeError, "did not begin"): queries.begin(37)
