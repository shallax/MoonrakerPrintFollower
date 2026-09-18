"""Coverage for the gated leak-hunt instrument, plugins/LeakProbe.py.

The probe is opt-in at runtime, but its logic is plain Python, so
every axis is driven here: the sampler's platform readers, the census
walkers, the label and diff helpers, the report assembly and the tick
battery itself. The host is a stub preference store, controller and
device manager, the census roots are fake item trees, and the
Python-allocation axis gets a real tracemalloc run over the
allocations a test makes.

The platform readers are exercised at the ctypes boundary: the mach
task_info, libproc rusage and Win32 psapi structures are filled by
fakes, so the source labels (mach-current vs ru_maxrss-peak-fallback
vs win32-working-set), the refusal rules and the virtual-size fallback
are pinned on whatever host runs the suite — the darwin and win32
call chains themselves obviously are not.

Left uncovered (measured): lines 290-291, the outer except around the
item walk in _qml_class_counts. Roots are a list built just above and
every name the walk can meet is a class name, so the only raise left is
a collision with the census's own error keys ('root-err' and friends) —
the wrapper defending itself against itself, not a reachable state.
Everything else is covered, on this host, for real.
"""
from __future__ import annotations

import builtins
import contextlib
import ctypes
import ctypes.util
import importlib
import os
import pathlib
import sys
import tempfile
import tracemalloc
import unittest
from collections import deque
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

try:
    import PyQt6.QtGui
    import PyQt6.QtQuick
except ImportError:  # the host stdlib suite has no PyQt6: the Qt census tests skip
    PyQt6 = None

try:
    from qt_runtime_support import runtime
except ImportError:  # the suite is also discovered from the repository root
    from tests.qt_runtime_support import runtime

try:
    import plugins.LeakProbe as leakprobe
    from plugins.LeakProbe import (
        LeakProbe, _TOP_N, _camera_line, _diff, _frame_tag, _qml_class_counts,
        _rss_kb, _runtime_sizes, _usage_info_kb, start_leak_probe, stop_leak_probe,
    )
except ImportError:
    # The MODULE imports PyQt6.QtCore itself: on the host (no PyQt6)
    # the whole suite is container-only, so it skips as a module
    # rather than erroring at import.
    raise unittest.SkipTest("LeakProbe needs PyQt6 — container only") from None
from plugins.PrinterConfig import PrinterConfigStore

# The probe's timers need an application object; a module-level app
# keeps timer state stable across the suite's tests (standalone runs
# without one fail to start timers at all — the parallel runner's
# find).
from PyQt6.QtCore import QCoreApplication
_APP = QCoreApplication.instance() or QCoreApplication([])

_LOG_KEY = PrinterConfigStore.LEGACY_MAP["memory_diagnostics_log"]
_TRACE_KEY = PrinterConfigStore.LEGACY_MAP["memory_diagnostics_trace"]


# --- host doubles -------------------------------------------------------

class _Preferences:
    """The store the probe reads live, once per tick."""

    def __init__(self, log=False, trace=False):
        self.values = {_LOG_KEY: log, _TRACE_KEY: trace}

    def getValue(self, key):
        return self.values.get(key)

    def setValue(self, key, value):
        self.values[key] = value


class _Binding:
    """The runtime's binding seam: the probe reads the facade's live
    config through it (the legacy preference mirror is retired)."""

    def __init__(self, log=False, trace=False):
        self.config = SimpleNamespace(memory_diagnostics_log=log, memory_diagnostics_trace=trace)


def _runtime(log=False, trace=False, binding=None):
    return SimpleNamespace(binding=binding if binding is not None else _Binding(log=log, trace=trace))


class _Stage:
    def __init__(self, plugin_id="PreviewStage"):
        self._plugin_id = plugin_id

    def getPluginId(self):
        return self._plugin_id


class _Controller:
    def __init__(self, stage=None):
        self._stage = stage

    def getActiveStage(self):
        return self._stage


class _DeviceManager:
    def __init__(self, devices):
        self._devices = list(devices)

    def getOutputDevices(self):
        return self._devices


@contextlib.contextmanager
def _host(prefs, *, controller=None, devices=None, drop=(), qml_engine=None):
    """Install the Cura application surface the probe reads live.

    ``drop`` omits a member, which is how the probe's wrapped-axis
    failures are produced without a real host.
    """
    surface = {"getPreferences": lambda: prefs}
    if "getController" not in drop:
        surface["getController"] = lambda: controller if controller is not None else _Controller()
    if "getOutputDeviceManager" not in drop:
        surface["getOutputDeviceManager"] = lambda: _DeviceManager(devices or [])
    if qml_engine is not None:
        surface["getQmlEngine"] = lambda: qml_engine
    module = ModuleType("UM.Application")
    module.Application = SimpleNamespace(getInstance=lambda: SimpleNamespace(**surface))
    with patch.dict(sys.modules, {"UM.Application": module}):
        yield


@contextlib.contextmanager
def _resources(storage):
    """The Cura storage path, with the real getStoragePath(kind, name=None)."""
    module = ModuleType("UM.Resources")

    def getStoragePath(kind, name=None):
        return str(pathlib.Path(storage) / name) if name else str(storage)

    module.Resources = SimpleNamespace(Preferences="preferences", getStoragePath=getStoragePath)
    with patch.dict(sys.modules, {"UM.Resources": module}):
        yield module.Resources


@contextlib.contextmanager
def _home(path):
    """Point Path.home() at a scratch dir so no test writes the real one."""
    with patch.object(pathlib.Path, "home", return_value=pathlib.Path(path)):
        yield


def _deny_open(*paths):
    """open() that refuses exactly these paths; everything else passes."""
    real = builtins.open
    denied = {str(path) for path in paths}

    def opener(path, *args, **kwargs):
        if str(path) in denied:
            raise OSError("refused: %s" % path)
        return real(path, *args, **kwargs)

    return patch("builtins.open", side_effect=opener)


@contextlib.contextmanager
def _platform(name):
    """Report a foreign sys.platform: the module reads it live."""
    with patch.object(leakprobe, "sys", SimpleNamespace(platform=name)):
        yield


@contextlib.contextmanager
def _cura_host(prefs, **kwargs):
    """A Cura runtime block plus the host surface: the camera path needs both."""
    with runtime(), _host(prefs, **kwargs):
        yield


def _boom(*_args, **_kwargs):
    raise RuntimeError("refused")


# --- ctypes boundary doubles --------------------------------------------

class _CFunc:
    """A foreign function: argtypes/restype assignable, like ctypes' own."""

    def __init__(self, fn):
        self._fn = fn
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._fn(*args)


class _MachTask:
    """The darwin mach seam: task_info fills MACH_TASK_BASIC_INFO."""

    def __init__(self, kernel=0, resident=0):
        self._kernel = kernel
        self._resident = resident
        self.mach_task_self = _CFunc(lambda: 1)
        self.task_info = _CFunc(self._info)

    def _info(self, task, flavor, ref, count):
        ref._obj.resident_size = self._resident
        return self._kernel


class _Libproc:
    """The darwin libproc seam: rusage v4 plus the taskinfo virtual size."""

    def __init__(self, rc=0, resident=0, footprint=0, lifetime=0, virtual=0, written=1):
        self._values = (rc, resident, footprint, lifetime, virtual, written)
        self.proc_pid_rusage = _CFunc(self._rusage)
        self.proc_pidinfo = _CFunc(self._info)

    def _rusage(self, pid, flavor, ref):
        rc, resident, footprint, lifetime, _virtual, _written = self._values
        usage = ref._obj
        usage.resident_size = resident
        usage.phys_footprint = footprint
        usage.lifetime_max_phys_footprint = lifetime
        return rc

    def _info(self, pid, flavor, address, ref, size):
        _rc, _resident, _footprint, _lifetime, virtual, written = self._values
        ref._obj.virtual_size = virtual
        return written


@contextlib.contextmanager
def _libproc(libproc, *, name="libproc"):
    with patch.object(ctypes.util, "find_library", return_value=name), \
            patch.object(ctypes, "CDLL", return_value=libproc):
        yield


def _mach(task):
    return _libproc(task, name="libc")


def _windll(working_set=0, ok=True, *, psapi=True):
    """The Win32 seam: the memory-info getter and its BOOL return."""

    class _Psapi:
        def GetProcessMemoryInfo(self, process, ref, size):
            if working_set:
                ref._obj.WorkingSetSize = working_set
            return ok

    call = _Psapi().GetProcessMemoryInfo
    kernel = SimpleNamespace(GetCurrentProcess=lambda: 1)
    if psapi:
        return SimpleNamespace(kernel32=kernel,
                               psapi=SimpleNamespace(GetProcessMemoryInfo=call))
    # An older host: only the kernel32 alias carries the call.
    kernel.K32GetProcessMemoryInfo = call
    return SimpleNamespace(kernel32=kernel)


# --- QML census doubles --------------------------------------------------

class _Item:
    """A QML item stand-in: the census only needs childItems()."""

    def __init__(self, *children):
        self._children = list(children)

    def childItems(self):
        return list(self._children)


class _Panel(_Item):
    pass


class _Button(_Item):
    pass


class _Deaf(_Item):
    def childItems(self):
        raise RuntimeError("no children here")


class _Window:
    """A QQuickWindow stand-in: the census reads contentItem()."""

    def __init__(self, content=None):
        self._content = content

    def contentItem(self):
        if isinstance(self._content, Exception):
            raise self._content
        return self._content


class _Gui:
    """The QGuiApplication stand-in: the census walks top-level windows."""

    def __init__(self, windows=(), boom=False):
        self._windows = list(windows)
        self._boom = boom

    def topLevelWindows(self):
        if self._boom:
            raise RuntimeError("no window server")
        return list(self._windows)


@contextlib.contextmanager
def _qml(windows=(), *, boom=False, engine=None):
    """The window walk, with the item tree and the engine both faked."""
    if PyQt6 is None:
        raise unittest.SkipTest("PyQt6 runs only in the container")
    kwargs = {"qml_engine": engine} if engine is not None else {}
    with patch.object(PyQt6.QtQuick, "QQuickWindow", _Window), \
            patch.object(PyQt6.QtGui, "QGuiApplication", _Gui(windows, boom=boom)), \
            _host(_Preferences(), **kwargs):
        yield


# --- the platform readers ------------------------------------------------

class RssReaderTests(unittest.TestCase):
    """The resident-size reader and, above all, its source labels."""

    @unittest.skipIf(sys.platform != "linux", "reads /proc")
    def test_linux_reads_the_live_resident_size(self):
        value, source = _rss_kb()
        self.assertEqual(source, "proc-current")
        self.assertGreater(value, 0)

    @unittest.skipIf(leakprobe.resource is None, "no resource module")
    def test_without_procfs_the_rusage_peak_is_labelled_a_peak(self):
        # The review's catch: a peak fallback must never read as current.
        with _deny_open("/proc/self/status"):
            value, source = _rss_kb()
        self.assertEqual(source, "ru_maxrss-peak-fallback")
        self.assertGreater(value, 0)

    @unittest.skipIf(leakprobe.resource is None, "no resource module")
    def test_a_failing_rusage_read_reports_unavailable(self):
        with _deny_open("/proc/self/status"), \
                patch.object(leakprobe.resource, "getrusage", side_effect=OSError("no rusage")):
            self.assertEqual(_rss_kb(), (0, "unavailable"))

    def test_without_either_source_the_reader_reports_unavailable(self):
        with _deny_open("/proc/self/status"), patch.object(leakprobe, "resource", None):
            self.assertEqual(_rss_kb(), (0, "unavailable"))

    @unittest.skipIf(leakprobe.resource is None, "no resource module")
    def test_darwin_prefers_the_current_mach_reading(self):
        # ru_maxrss is the peak since start and can never show a fall.
        with _deny_open("/proc/self/status"), _platform("darwin"), \
                _mach(_MachTask(resident=2048 * 1024)):
            self.assertEqual(_rss_kb(), (2048, "mach-current"))

    @unittest.skipIf(leakprobe.resource is None, "no resource module")
    def test_darwin_falls_back_to_the_rusage_peak(self):
        with _deny_open("/proc/self/status"), _platform("darwin"), \
                _mach(_MachTask(kernel=1, resident=0)):
            value, source = _rss_kb()
        self.assertEqual(source, "ru_maxrss-peak-fallback")
        self.assertGreater(value, 0)

    @unittest.skipIf(leakprobe.resource is None, "no resource module")
    def test_darwin_with_both_reads_refused_reports_unavailable(self):
        with _deny_open("/proc/self/status"), _platform("darwin"), \
                _mach(_MachTask(kernel=1, resident=0)), \
                patch.object(leakprobe.resource, "getrusage", side_effect=OSError("no rusage")):
            self.assertEqual(_rss_kb(), (0, "unavailable"))

    def test_a_refused_mach_call_is_reported_verbatim(self):
        with _deny_open("/proc/self/status"), _platform("darwin"), \
                patch.object(ctypes.util, "find_library", return_value="libc"), \
                patch.object(ctypes, "CDLL", side_effect=OSError("no libc")):
            value, source = _rss_kb()
        self.assertEqual(value, 0)
        self.assertTrue(source.startswith("mach-failed:"), source)

    def test_win32_reads_the_working_set(self):
        with _deny_open("/proc/self/status"), _platform("win32"), \
                patch.object(leakprobe, "resource", None), \
                patch.object(ctypes, "windll", _windll(working_set=512 * 1024), create=True):
            self.assertEqual(_rss_kb(), (512, "win32-working-set"))

    def test_win32_falls_back_to_the_kernel32_alias(self):
        with _deny_open("/proc/self/status"), _platform("win32"), \
                patch.object(leakprobe, "resource", None), \
                patch.object(ctypes, "windll", _windll(working_set=1024, psapi=False), create=True):
            self.assertEqual(_rss_kb(), (1, "win32-working-set"))

    def test_a_failed_win32_call_is_reported_not_read_as_zero(self):
        # The API returns a BOOL; a refusal must be named, not silently 0.
        with _deny_open("/proc/self/status"), _platform("win32"), \
                patch.object(leakprobe, "resource", None), \
                patch.object(ctypes, "windll", _windll(ok=False), create=True):
            self.assertEqual(_rss_kb(), (0, "win32-failed:GetProcessMemoryInfo"))

    def test_an_unusable_win32_ctypes_is_reported_verbatim(self):
        with _deny_open("/proc/self/status"), _platform("win32"), \
                patch.object(leakprobe, "resource", None), \
                patch.object(ctypes, "windll", None, create=True):
            value, source = _rss_kb()
        self.assertEqual(value, 0)
        self.assertTrue(source.startswith("win32-failed:"), source)


class UsageInfoTests(unittest.TestCase):
    """The darwin footprint axis: rusage v4 plus the layout self-check."""

    @unittest.skipIf(sys.platform == "darwin", "the darwin read returns values")
    def test_off_darwin_the_footprint_axis_is_absent(self):
        self.assertIsNone(_usage_info_kb())

    def test_darwin_reads_the_footprint_axes_and_the_virtual_size(self):
        libproc = _Libproc(rc=0, resident=2048 * 1024, footprint=4096 * 1024,
                           lifetime=8192 * 1024, virtual=65536 * 1024)
        with _platform("darwin"), _libproc(libproc):
            self.assertEqual(_usage_info_kb(), (2048, 4096, 8192, 65536))

    def test_a_refused_rusage_read_yields_nothing(self):
        with _platform("darwin"), _libproc(_Libproc(rc=1, footprint=4096 * 1024)):
            self.assertIsNone(_usage_info_kb())

    def test_a_zero_footprint_yields_nothing(self):
        with _platform("darwin"), _libproc(_Libproc(rc=0, footprint=0)):
            self.assertIsNone(_usage_info_kb())

    def test_an_unwritten_taskinfo_leaves_the_virtual_size_at_zero(self):
        libproc = _Libproc(resident=1024, footprint=2048, lifetime=4096, virtual=65536, written=0)
        with _platform("darwin"), _libproc(libproc):
            self.assertEqual(_usage_info_kb(), (1, 2, 4, 0))

    def test_a_missing_libproc_yields_nothing(self):
        with _platform("darwin"), \
                patch.object(ctypes.util, "find_library", return_value=None), \
                patch.object(ctypes, "CDLL", side_effect=OSError("no libproc")) as ctor:
            self.assertIsNone(_usage_info_kb())
        # find_library found nothing: the documented dylib path is the fallback.
        self.assertEqual(ctor.call_args[0][0], "/usr/lib/libproc.dylib")


# --- the census walkers --------------------------------------------------

class QmlCensusTests(unittest.TestCase):
    """The per-class item census: the window walk and the engine fallback."""

    def test_an_unimportable_quick_module_is_named(self):
        with patch.dict(sys.modules, {"PyQt6.QtQuick": None}):
            counts = _qml_class_counts()
        self.assertEqual(list(counts), ["qml-import-err"])
        self.assertIn("PyQt6.QtQuick", counts["qml-import-err"])

    def test_the_window_walk_counts_every_item_class(self):
        content = _Item(_Panel(_Button(), _Button()), _Deaf())
        with _qml([_Window(content)]):
            counts = _qml_class_counts()
        self.assertEqual(counts["<roots>"], 1)
        self.assertEqual(counts["_Item"], 1)
        self.assertEqual(counts["_Panel"], 1)
        self.assertEqual(counts["_Button"], 2)
        # A childItems() that raises drops that branch, not the census.
        self.assertEqual(counts["_Deaf"], 1)

    def test_a_window_without_a_content_item_is_skipped(self):
        engine = SimpleNamespace(rootObjects=lambda: [_Item(_Button())])
        with _qml([_Window(RuntimeError("gone")), _Window(None), "not-a-window"], engine=engine):
            counts = _qml_class_counts()
        self.assertEqual(counts["<roots>"], 1)
        self.assertEqual(counts["_Button"], 1)

    def test_an_engine_fallback_that_fails_is_named(self):
        with _qml([_Window(None)]):
            counts = _qml_class_counts()
        self.assertEqual(counts["<roots>"], 0)
        self.assertIn("root-err", counts)

    def test_a_failing_window_walk_is_named(self):
        with _qml([], boom=True):
            counts = _qml_class_counts()
        self.assertIn("root-err", counts)


class RuntimeSizesTests(unittest.TestCase):
    """The two-hop collection census of the plugin runtime."""

    def test_collections_are_keyed_by_path(self):
        console = SimpleNamespace(transcript=["a"], ring=deque([1, 2]), pending={},
                                  seen={"x"}, meta=(1,), blob=b"xy", text="abc")
        sizes = _runtime_sizes(SimpleNamespace(console=console))
        self.assertEqual(sizes["console.transcript"], 1)
        self.assertEqual(sizes["console.ring"], 2)
        self.assertEqual(sizes["console.pending"], 0)
        self.assertEqual(sizes["console.seen"], 1)
        self.assertEqual(sizes["console.meta"], 1)
        self.assertEqual(sizes["console.blob"], 2)
        self.assertEqual(sizes["console.text"], 3)

    def test_private_components_and_dunder_attributes_are_skipped(self):
        console = SimpleNamespace(transcript=["a"])
        surface = SimpleNamespace(console=console, _hidden=SimpleNamespace(transcript=["a"]))
        sizes = _runtime_sizes(surface)
        self.assertIn("console.transcript", sizes)
        self.assertNotIn("_hidden.transcript", sizes)
        self.assertFalse([key for key in sizes if "__" in key])

    def test_a_component_that_raises_on_read_is_skipped(self):
        class _Noisy:
            def __init__(self):
                self.items = [1]

            @property
            def exploding(self):
                raise RuntimeError("no read")

        self.assertEqual(_runtime_sizes(SimpleNamespace(console=_Noisy())), {"console.items": 1})

    def test_a_runtime_attribute_that_raises_on_read_is_skipped(self):
        class _Angry:
            @property
            def console(self):
                raise RuntimeError("no console")

        self.assertEqual(_runtime_sizes(_Angry()), {})

    def test_an_unwalkable_runtime_is_reported_not_fatal(self):
        class _Undirigible:
            def __dir__(self):
                raise RuntimeError("no dir")

        self.assertEqual(_runtime_sizes(_Undirigible()), {"walk-err": "RuntimeError('no dir')"})


class DiffTests(unittest.TestCase):
    """The growth report: only real growth, largest first, capped."""

    def test_growth_from_nothing_is_reported(self):
        self.assertEqual(_diff({}, {"console.transcript": 4}), [("console.transcript", 0, 4)])

    def test_flat_and_shrinking_entries_are_dropped(self):
        self.assertEqual(_diff({"a": 1}, {"a": 1}), [])
        self.assertEqual(_diff({"a": 5}, {"a": 2}), [])

    def test_non_integer_counts_are_dropped(self):
        # The census reports its failures as strings; they are not sizes.
        self.assertEqual(_diff({}, {"root-err": "AttributeError('x')"}), [])
        self.assertEqual(_diff({"a": "n/a"}, {"a": 2}), [])

    def test_rows_are_sorted_by_growth_and_capped(self):
        current = {"ring%d" % index: index * 10 for index in range(_TOP_N + 4)}
        rows = _diff({}, current)
        self.assertEqual(len(rows), _TOP_N)
        deltas = [row[2] - row[1] for row in rows]
        self.assertEqual(deltas, sorted(deltas, reverse=True))
        self.assertEqual(rows[0][2], (_TOP_N + 3) * 10)


class FrameTagTests(unittest.TestCase):
    """One tracemalloc frame in ~80 chars, the location always kept."""

    def test_a_multi_line_frame_keeps_its_source_and_location(self):
        frame = ('File "/home/author/src/plugin/PreviewFollower.py", line 412, in _tick\n'
                 '    return self._state.path_layer')
        self.assertEqual(
            _frame_tag(frame),
            "return self._state.path_layer (plugin/PreviewFollower.py:412)")

    def test_a_single_line_frame_keeps_only_the_location(self):
        # PyQt's C-level frames carry no source line at all.
        frame = 'File "/usr/lib/python3/site-packages/PyQt6/QtCore.py", line 7'
        self.assertEqual(_frame_tag(frame), "PyQt6/QtCore.py:7")

    def test_a_frame_without_a_file_header_falls_back_to_its_text(self):
        self.assertEqual(_frame_tag("<frozen importlib._bootstrap>\n    <module>"), "<module>")

    def test_a_short_path_is_not_over_trimmed(self):
        self.assertEqual(_frame_tag('File "x.py", line 3\n    code'), "code (x.py:3)")

    def test_a_header_without_the_line_marker_is_plain_text(self):
        self.assertEqual(_frame_tag('File "x.py"'), 'File "x.py"')

    def test_long_source_text_is_truncated(self):
        frame = 'File "/a/b.py", line 3\n    ' + "x" * 90
        self.assertEqual(_frame_tag(frame), "x" * 48 + " (a/b.py:3)")

    def test_an_empty_frame_is_empty(self):
        self.assertEqual(_frame_tag(""), "")


# --- the camera line -----------------------------------------------------

def _device(camera):
    """A real adapter instance without the plugin's wiring."""
    from plugins.MoonrakerOutputDevice import MoonrakerOutputDevice
    device = MoonrakerOutputDevice.__new__(MoonrakerOutputDevice)
    device._printers = [SimpleNamespace(_camera=camera)]
    return device


class _Socket:
    def __init__(self, backlog=0, deaf=False):
        self._backlog = backlog
        self._deaf = deaf

    def bytesToWrite(self):
        if self._deaf:
            raise RuntimeError("socket gone")
        return self._backlog


class _Reply:
    def __init__(self, buffered=0, deaf=False):
        self._buffered = buffered
        self._deaf = deaf

    def bytesAvailable(self):
        if self._deaf:
            raise RuntimeError("reply gone")
        return self._buffered


class CameraLineTests(unittest.TestCase):
    """The video path's gauges: none / direct / bridged."""

    def _line(self, cameras=(), *, foreign=0, devices=None, **host):
        # The adapter class is imported inside the block: the Cura doubles
        # only exist while it runs.
        with runtime():
            if devices is None:
                devices = [SimpleNamespace(getId=lambda: "foreign") for _ in range(foreign)]
                devices += [_device(camera) for camera in cameras]
            with _host(_Preferences(), devices=devices, **host):
                return _camera_line()

    def test_no_installed_adapter_reports_not_applicable(self):
        self.assertEqual(self._line(), "camera n/a")

    def test_a_foreign_device_is_skipped(self):
        self.assertEqual(self._line(foreign=2), "camera n/a")

    def test_an_absent_or_unconfigured_camera_is_named(self):
        self.assertEqual(self._line([None]), "camera n/a")
        self.assertEqual(self._line([SimpleNamespace()]), "camera none")
        self.assertEqual(self._line([SimpleNamespace(_url="http://p:8080/stream")]), "camera direct")

    def test_a_bridge_exposes_its_backlog_and_buffered_bytes(self):
        bridge = SimpleNamespace(_relays={
            _Socket(backlog=100): (_Reply(buffered=25), None, 0),
            _Socket(backlog=50): (None, None, 0),
        }, _relayed_bytes=900)
        camera = SimpleNamespace(_url="http://p:8080/stream", _camera_bridge=bridge)
        self.assertEqual(self._line([camera]),
                         "camera bridged relays=2 backlog=150B upstream=25B relayed=900B")

    def test_a_deaf_relay_reports_what_it_can_and_no_more(self):
        bridge = SimpleNamespace(_relays={_Socket(deaf=True): (_Reply(deaf=True), None, 0)})
        camera = SimpleNamespace(_url="http://p:8080/stream", _camera_bridge=bridge)
        self.assertEqual(self._line([camera]),
                         "camera bridged relays=1 backlog=0B upstream=0B relayed=0B")

    def test_a_failing_camera_walk_is_reported_verbatim(self):
        line = self._line(drop=("getOutputDeviceManager",))
        self.assertTrue(line.startswith("camera err "), line)


# --- construction, the log path, the entry points ------------------------

class _ProbeCase(unittest.TestCase):
    """A probe writing to a scratch log, with a scratch home directory."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="leak-probe-")
        self.addCleanup(holder.cleanup)
        self.tmp = pathlib.Path(holder.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.log_path = self.tmp / "moonraker_leak.log"

    def _probe(self, surface=None, *, enabled=False):
        """A probe built with the toggle off, then driven by the test."""
        with _home(self.home), _host(_Preferences(log=False)):
            probe = LeakProbe(surface if surface is not None else _runtime(log=enabled))
        self.addCleanup(probe._timer.stop)
        probe._log_path = str(self.log_path)
        probe._enabled = enabled
        return probe

    def _lines(self):
        """The log's messages, timestamps stripped."""
        return [line.split(" ", 1)[1] for line in self.log_path.read_text().splitlines()]


class ConstructorTests(_ProbeCase):
    """Construction stays inert: nothing is measured until the toggle is on."""

    def test_the_toggle_off_writes_no_file_at_all(self):
        with _home(self.home), _host(_Preferences(log=False)):
            probe = LeakProbe(_runtime(log=False))
        self.addCleanup(probe._timer.stop)
        self.assertTrue(probe._timer.isActive())
        self.assertFalse(probe._enabled)
        self.assertFalse(probe._usage_failure_logged)
        self.assertEqual(probe._ticks, 0)
        self.assertIsNone(probe._trace_snapshot)
        self.assertFalse((self.home / "moonraker_leak.log").exists())

    def test_the_toggle_on_registers_the_instance_once(self):
        with _home(self.home), _host(_Preferences(log=True)):
            probe = LeakProbe(_runtime(log=True))
        self.addCleanup(probe._timer.stop)
        lines = (self.home / "moonraker_leak.log").read_text().splitlines()
        self.assertEqual(len(lines), 1)
        message = lines[0].split(" ", 1)[1]
        self.assertIn("registered toggle=True", message)
        self.assertIn("pid=%d" % os.getpid(), message)
        self.assertIn("platform=%s" % sys.platform, message)

    def test_an_unreadable_store_keeps_the_probe_off(self):
        class _AngryBinding:
            @property
            def config(self):
                raise RuntimeError("no config")

        with _home(self.home), _host(_Preferences(log=False)):
            probe = LeakProbe(_runtime(binding=_AngryBinding()))
        self.addCleanup(probe._timer.stop)
        self.assertFalse(probe._enabled)
        self.assertFalse(probe._trace_snapshot)
        self.assertFalse((self.home / "moonraker_leak.log").exists())

    def test_an_unreadable_store_reads_as_off(self):
        class _AngryBinding:
            @property
            def config(self):
                raise RuntimeError("no config")

        probe = LeakProbe(_runtime(binding=_AngryBinding()))
        self.addCleanup(probe._timer.stop)
        self.assertFalse(probe._enabled_now())
        self.assertFalse(probe._trace_enabled_now())

    def test_a_failed_config_read_is_named_in_the_registration(self):
        class _FlakyBinding:
            def __init__(self):
                self.reads = 0

            @property
            def config(self):
                self.reads += 1
                if self.reads > 1:
                    raise RuntimeError("config gone")
                return SimpleNamespace(memory_diagnostics_log=True, memory_diagnostics_trace=False)

        with _home(self.home), _host(_Preferences(log=False)):
            probe = LeakProbe(_runtime(binding=_FlakyBinding()))
        self.addCleanup(probe._timer.stop)
        message = (self.home / "moonraker_leak.log").read_text().splitlines()[0]
        self.assertIn("read-err", message)
        self.assertIn("config gone", message)


class LogPathTests(unittest.TestCase):
    """The documented home-dir file, then the Cura storage dir."""

    def setUp(self):
        holder = tempfile.TemporaryDirectory(prefix="leak-probe-")
        self.addCleanup(holder.cleanup)
        self.tmp = pathlib.Path(holder.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.candidate = self.home / "moonraker_leak.log"

    def test_the_home_file_is_the_first_choice(self):
        with _home(self.home):
            self.assertEqual(LeakProbe._pick_log_path(), str(self.candidate))

    def test_an_unwritable_home_falls_back_to_the_cura_storage(self):
        with _home(self.home), _resources(self.tmp / "cura"), _deny_open(self.candidate):
            self.assertEqual(LeakProbe._pick_log_path(),
                             str(self.tmp / "cura" / "moonraker_leak.log"))

    def test_a_failed_storage_lookup_keeps_the_home_candidate(self):
        with _home(self.home), _deny_open(self.candidate), \
                _resources(self.tmp / "cura") as resources, \
                patch.object(resources, "getStoragePath", side_effect=RuntimeError("no storage")):
            self.assertEqual(LeakProbe._pick_log_path(), str(self.candidate))


class EntryPointTests(_ProbeCase):
    """start/stop: the module global pins the instance, teardown unwires it."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, leakprobe, "_ACTIVE", None)

    def test_start_pins_the_probe_and_stop_releases_it(self):
        with _home(self.home), _host(_Preferences(log=False)):
            probe = start_leak_probe(SimpleNamespace())
        self.addCleanup(probe._timer.stop)
        self.assertIs(leakprobe._ACTIVE, probe)
        stop_leak_probe()
        self.assertIsNone(leakprobe._ACTIVE)
        self.assertIsNone(probe.runtime)

    def test_stop_without_a_probe_is_a_no_op(self):
        leakprobe._ACTIVE = None
        self.assertIsNone(stop_leak_probe())

    def test_a_second_start_returns_the_live_probe(self):
        # The double-start guard: a re-registration must not stack a
        # second timer and abandon the first.
        with _home(self.home), _host(_Preferences(log=False)):
            first = start_leak_probe(SimpleNamespace())
            second = start_leak_probe(SimpleNamespace())
        self.addCleanup(first._timer.stop)
        self.assertIs(first, second)
        self.assertIs(leakprobe._ACTIVE, first)

    def test_stop_clears_the_trace_even_when_no_snapshot_was_taken(self):
        # The trace toggle armed but never sampled used to leave
        # tracemalloc running through the teardown.
        with _home(self.home), _host(_Preferences(log=False)):
            probe = start_leak_probe(SimpleNamespace())
        self.addCleanup(probe._timer.stop)
        probe._enabled = True
        probe._trace_snapshot = None
        tracemalloc.start()
        stop_leak_probe()
        self.assertFalse(tracemalloc.is_tracing())
        self.assertIsNone(leakprobe._ACTIVE)

    def test_teardown_drops_the_trace_window_and_never_raises(self):
        with _home(self.home), _host(_Preferences(log=False)):
            probe = start_leak_probe(SimpleNamespace())
        self.addCleanup(probe._timer.stop)
        probe._enabled = True
        probe._trace_snapshot = object()
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        with patch.object(probe, "_timer", SimpleNamespace(stop=_boom, deleteLater=_boom)), \
                patch.object(leakprobe, "tracemalloc", SimpleNamespace(stop=_boom)):
            stop_leak_probe()
        self.assertIsNone(probe.runtime)
        self.assertIsNone(leakprobe._ACTIVE)

    def test_an_unwritable_log_is_swallowed(self):
        probe = self._probe()
        probe._log_path = str(self.tmp)  # a directory: the write fails
        probe._log("rss=0kb")
        self.assertFalse(self.log_path.exists())


# --- the tick ------------------------------------------------------------

class TickTests(_ProbeCase):
    """The tick battery: each axis wrapped, the toggles read live."""

    def test_a_disabled_tick_writes_nothing_at_all(self):
        probe = self._probe()
        with _host(_Preferences(log=False)):
            probe._tick()
        self.assertFalse(self.log_path.exists())
        self.assertEqual(probe._ticks, 0)

    def test_turning_the_toggle_off_resets_the_whole_window(self):
        probe = self._probe(enabled=True)
        probe._trace_snapshot = object()
        probe._qml_previous = {"QQuickItem": 3}
        probe._sizes_previous = {"console.transcript": 9}
        probe._ticks = 4
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        with _host(_Preferences(log=False)):
            probe._tick()
        self.assertEqual(self._lines(), ["stop"])
        self.assertFalse(probe._enabled)
        self.assertIsNone(probe._trace_snapshot)
        self.assertEqual(probe._qml_previous, {})
        self.assertEqual(probe._sizes_previous, {})
        self.assertEqual(probe._ticks, 0)
        self.assertFalse(tracemalloc.is_tracing())

    def test_turning_the_toggle_off_without_a_trace_window_still_stops(self):
        probe = self._probe(enabled=True)
        with _host(_Preferences(log=False)):
            probe._tick()
        self.assertEqual(self._lines(), ["stop"])
        self.assertFalse(probe._enabled)

    def test_the_first_enabled_tick_creates_the_log_and_names_the_instance(self):
        probe = self._probe()
        picker = MagicMock(return_value=str(self.log_path))
        with _home(self.home), _host(_Preferences(log=True)), \
                patch.object(probe, "_pick_log_path", picker):
            probe._tick()
        self.assertTrue(probe._enabled)
        self.assertEqual(probe._log_path, str(self.log_path))
        self.assertTrue(picker.called)
        first = self._lines()[0]
        self.assertTrue(first.startswith("start pid=%d" % os.getpid()), first)
        self.assertIn("platform=%s" % sys.platform, first)

    def test_an_existing_log_is_kept_when_the_toggle_turns_on(self):
        # The file survives a restart: it must not be picked away.
        probe = self._probe()
        self.log_path.write_text("")
        picker = MagicMock(return_value=str(self.tmp / "elsewhere.log"))
        with _home(self.home), _host(_Preferences(log=True)), \
                patch.object(probe, "_pick_log_path", picker):
            probe._tick()
        self.assertFalse(picker.called)
        self.assertEqual(probe._log_path, str(self.log_path))

    def test_an_enabled_tick_logs_every_axis(self):
        surface = SimpleNamespace(preview=SimpleNamespace(_state=SimpleNamespace(path_layer=12)))
        probe = self._probe(surface, enabled=True)
        with _cura_host(_Preferences(log=True), controller=_Controller(_Stage("PreviewStage"))):
            probe._tick()
        lines = self._lines()
        self.assertTrue(any(line.startswith("rss=") and "src=" in line for line in lines))
        self.assertTrue(any(line == "stage=PreviewStage" for line in lines))
        self.assertTrue(any(line == "layer=12" for line in lines))
        self.assertTrue(any(line == "camera n/a" for line in lines))
        self.assertEqual(probe._ticks, 1)

    def test_a_missing_stage_and_layer_are_named_not_guessed(self):
        probe = self._probe(SimpleNamespace(), enabled=True)
        with _host(_Preferences(log=True)):
            probe._tick()
        lines = self._lines()
        self.assertTrue(any(line == "stage=None" for line in lines))
        self.assertTrue(any(line == "layer=?" for line in lines))

    def test_every_failing_axis_is_named_and_the_tick_completes(self):
        class _AngryPreview:
            @property
            def _state(self):
                raise RuntimeError("no state")

        probe = self._probe(SimpleNamespace(preview=_AngryPreview()), enabled=True)
        with patch.object(leakprobe, "_rss_kb", side_effect=OSError("no mem")), \
                patch.object(leakprobe, "_usage_info_kb", side_effect=OSError("no libproc")), \
                patch.object(leakprobe, "_camera_line", side_effect=OSError("no device")), \
                patch.object(leakprobe, "_qml_class_counts", side_effect=OSError("no engine")), \
                patch.object(leakprobe, "_runtime_sizes", side_effect=OSError("no runtime")), \
                _host(_Preferences(log=True), drop=("getController",)):
            probe._tick()
        lines = self._lines()
        for marker in ("rss-err", "usage-err", "stage-err", "layer-err", "camera-err",
                       "  qml-err", "  size-err"):
            self.assertTrue(any(marker in line for line in lines), marker)
        self.assertEqual(probe._ticks, 1)

    def test_an_absent_darwin_rusage_is_reported_once(self):
        probe = self._probe(enabled=True)
        with patch.object(leakprobe, "_usage_info_kb", return_value=None), \
                _platform("darwin"), _host(_Preferences(log=True)):
            probe._tick()
            probe._tick()
        lines = self._lines()
        self.assertEqual(sum(1 for line in lines if line.startswith("usage n/a")), 1)
        self.assertTrue(probe._usage_failure_logged)

    def test_a_census_failure_is_logged_verbatim(self):
        probe = self._probe(enabled=True)
        census = {"<roots>": 1, "qml-import-err": "ImportError('no QtQuick')"}
        with patch.object(leakprobe, "_qml_class_counts", return_value=census), \
                _host(_Preferences(log=True)):
            probe._tick()
        self.assertTrue(any(line == "  qml-err qml-import-err=ImportError('no QtQuick')"
                            for line in self._lines()))

    def test_growing_counts_and_collections_are_named_by_path(self):
        surface = SimpleNamespace(console=SimpleNamespace(transcript=[]),
                                  preview=SimpleNamespace(_state=None))
        probe = self._probe(surface, enabled=True)
        census = {"<roots>": 1, "QQuickItem": 4}
        # A fresh dict each call: the probe keeps the previous one.
        with patch.object(leakprobe, "_qml_class_counts", side_effect=lambda: dict(census)), \
                _host(_Preferences(log=True)):
            probe._tick()
            surface.console.transcript.append("printed")
            census["QQuickItem"] = 9
            probe._tick()
        lines = self._lines()
        self.assertTrue(any(line == "  qml QQuickItem: 4 -> 9" for line in lines))
        self.assertTrue(any(line == "  size console.transcript: 0 -> 1" for line in lines))
        # <roots> grew once, on the first tick; a flat count is no growth.
        self.assertEqual([line for line in lines if line.startswith("  qml <roots>")],
                         ["  qml <roots>: 0 -> 1"])

    def test_the_trace_axis_runs_on_the_slow_tick_only(self):
        probe = self._probe(enabled=True)
        with patch.object(LeakProbe, "_top_traces", MagicMock(return_value=["trace-armed"])), \
                _host(_Preferences(log=True, trace=True)):
            probe._ticks = 4
            probe._tick()
            self.assertFalse(any("trace-armed" in line for line in self._lines()))
            probe._ticks = 5
            probe._tick()
        self.assertTrue(any("trace-armed" in line for line in self._lines()))

    def test_the_darwin_footprint_axes_are_logged_under_distinct_names(self):
        # The review: no axis may masquerade as another in the log.
        probe = self._probe(enabled=True)
        with patch.object(leakprobe, "_usage_info_kb", return_value=(2048, 4096, 8192, 65536)), \
                _host(_Preferences(log=True)):
            probe._tick()
        lines = self._lines()
        self.assertTrue(any(line == "resident=2048kb src=rusage-v4" for line in lines))
        self.assertTrue(any(line == "phys_footprint=4096kb src=rusage-v4" for line in lines))
        self.assertTrue(any(line == "max_phys_footprint=8192kb src=rusage-v4" for line in lines))
        self.assertTrue(any(line == "virtual=65536kb src=proc-taskinfo" for line in lines))

    def test_a_refused_trace_stop_does_not_break_the_reset(self):
        probe = self._probe(enabled=True)
        probe._trace_snapshot = object()
        with patch.object(leakprobe, "tracemalloc", SimpleNamespace(stop=_boom)), \
                _host(_Preferences(log=False)):
            probe._tick()
        self.assertEqual(self._lines(), ["stop"])
        self.assertIsNone(probe._trace_snapshot)

    def test_a_refused_trace_stop_does_not_break_the_trace_reset(self):
        probe = self._probe(enabled=True)
        probe._trace_snapshot = object()
        with patch.object(leakprobe, "tracemalloc", SimpleNamespace(stop=_boom)), \
                _host(_Preferences(log=True, trace=False)):
            probe._tick()
        self.assertIsNone(probe._trace_snapshot)

    def test_the_trace_window_drops_when_the_trace_toggle_goes_off(self):
        probe = self._probe(enabled=True)
        probe._trace_snapshot = object()
        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        with _host(_Preferences(log=True, trace=False)):
            probe._tick()
        self.assertIsNone(probe._trace_snapshot)
        self.assertFalse(tracemalloc.is_tracing())


class TraceTests(unittest.TestCase):
    """The opt-in Python-allocation axis, driven by real tracemalloc."""

    def setUp(self):
        tracemalloc.stop()
        self.addCleanup(tracemalloc.stop)

    def _probe(self):
        probe = LeakProbe.__new__(LeakProbe)
        probe._trace_snapshot = None
        return probe

    def test_the_first_tick_arms_and_the_next_diffs_real_growth(self):
        probe = self._probe()
        self.assertEqual(probe._top_traces(), ["trace-armed"])
        self.assertTrue(tracemalloc.is_tracing())
        window = probe._trace_snapshot
        self.assertIsNotNone(window)
        anchor = [{"index": index, "payload": "x" * 96} for index in range(4000)]
        lines = probe._top_traces()
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("py ") for line in lines))
        # The growing allocation is named by its own source file.
        self.assertTrue(any("test_leak_probe_coverage.py" in line for line in lines), lines)
        self.assertIsNot(probe._trace_snapshot, window)
        del anchor

    def test_a_broken_tracer_is_reported_not_raised(self):
        class _Broken:
            def start(self, frames):
                raise RuntimeError("boom")

            def take_snapshot(self):
                raise RuntimeError("boom")

        probe = self._probe()
        with patch.object(leakprobe, "tracemalloc", _Broken()):
            self.assertEqual(probe._top_traces(), ["trace-err RuntimeError('boom')"])
            probe._trace_snapshot = object()
            self.assertEqual(probe._top_traces(), ["trace-err RuntimeError('boom')"])


class WindowsImportTests(unittest.TestCase):
    """The Unix-only resource import: the module must load without it."""

    def test_the_module_imports_without_the_resource_module(self):
        real_import = builtins.__import__

        def deny_resource(name, *args, **kwargs):
            if name == "resource":
                raise ImportError("no resource on this platform")
            return real_import(name, *args, **kwargs)

        try:
            with patch.object(builtins, "__import__", side_effect=deny_resource):
                importlib.reload(leakprobe)
            self.assertIsNone(leakprobe.resource)
        finally:
            # The reload mutates the shared module: put it back.
            importlib.reload(leakprobe)
        self.assertIsNotNone(leakprobe.resource)


if __name__ == "__main__":
    unittest.main()
