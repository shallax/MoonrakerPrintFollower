"""The overnight-leak instrument, gated OFF by default.

The settings' diagnostics toggle ("Log memory diagnostics") arms a
10-second sampler appending to ~/moonraker_leak.log, naming WHAT
grows, on:

- the process's current resident size (per-platform: /proc, mach
  task_info, the Windows working set), plus on macOS the
  phys_footprint, its lifetime maximum and the resident size via
  proc_pid_rusage(RUSAGE_INFO_V4) — the footprint is the axis
  Activity Monitor shows, which the resident reading alone can hide
  once macOS compresses idle pages,
- the live print layer and the camera path's gauges (bridge relay
  backlog, upstream buffering, relayed bytes), so a C++-side climb
  can be pinned to the layer cadence or the video stream,
- tracemalloc's fastest-growing Python allocation tracebacks, only
  when the separate trace toggle is on, and
- the QML side's per-class item counts (diffed tick-to-tick), and
- the plugin runtime's own collection sizes (diffed tick-to-tick),
  so a growing ring, cache or pending dict is named by path.

The Python-allocation trace (tracemalloc) is a separate opt-in
toggle: its snapshot stalls Cura briefly each minute and its spikes
would otherwise pollute the footprint axis. A run whose footprint
climbs while the item counts and collection sizes hold is a
texture/GL-side accumulation, which narrows the hunt to the rendering
paths. Everything is wrapped: the probe must never take the plugin
down, and nothing runs until the toggle is on.
"""
from __future__ import annotations

import os
import sys
import time
import tracemalloc
from collections import deque
from pathlib import Path
from typing import Tuple

from PyQt6.QtCore import QTimer

try:
    import resource  # Unix only — absent on Windows
except ImportError:
    resource = None

_LOG_PATH = Path.home() / "moonraker_leak.log"
_INTERVAL_MS = 10_000
# The trace axis (tracemalloc snapshot/compare) runs every sixth fast
# tick when its separate toggle is on: the stall is real, so it is
# opt-in and minute-ly, never part of the clean footprint cadence.
_SLOW_TICKS = 6
_TOP_N = 8


def _rss_kb() -> Tuple[int, str]:
    """(resident KB, source). The source names WHAT was measured —
    current resident vs a peak fallback vs a failed read — so a log
    line can never be mistaken for a current-RSS reading (the review:
    the macOS peak fallback was silently labeled as RSS).
    """
    # Linux: the live RSS from /proc.
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]), "proc-current"
    except OSError:
        pass
    if sys.platform == "darwin":
        # The CURRENT resident size via mach task_info — ru_maxrss is
        # the peak since start, which can only rise and can never show
        # a fall after cleanup.
        try:
            import ctypes
            import ctypes.util

            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            libc.mach_task_self.restype = ctypes.c_uint32

            class _TimeValue(ctypes.Structure):
                _fields_ = [("seconds", ctypes.c_int32), ("microseconds", ctypes.c_int32)]

            class _BasicInfo(ctypes.Structure):
                _fields_ = [("virtual_size", ctypes.c_uint64),
                            ("resident_size", ctypes.c_uint64),
                            ("resident_size_max", ctypes.c_uint64),
                            ("user_time", _TimeValue),
                            ("system_time", _TimeValue),
                            ("policy", ctypes.c_int32),
                            ("suspend_count", ctypes.c_int32)]

            info = _BasicInfo()
            count = ctypes.c_uint32(ctypes.sizeof(info) // 4)
            kernel = libc.task_info(libc.mach_task_self(), 20,  # MACH_TASK_BASIC_INFO
                                    ctypes.byref(info), ctypes.byref(count))
            if kernel == 0 and info.resident_size:
                return int(info.resident_size) // 1024, "mach-current"
        except Exception as exc:
            return 0, f"mach-failed:{exc!r}"[:48]
        if resource is not None:
            try:
                return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) // 1024, "ru_maxrss-peak-fallback"
            except Exception:
                pass
    elif resource is not None:
        try:
            return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss), "ru_maxrss-peak-fallback"
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            class _Counters(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            try:
                getter = ctypes.windll.psapi.GetProcessMemoryInfo
            except Exception:
                getter = ctypes.windll.kernel32.K32GetProcessMemoryInfo
            # The API's return is a BOOL — a failure must be reported,
            # not silently read as zero.
            if not getter(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return 0, "win32-failed:GetProcessMemoryInfo"
            return int(counters.WorkingSetSize) // 1024, "win32-working-set"
        except Exception as exc:
            return 0, f"win32-failed:{exc!r}"[:48]
    return 0, "unavailable"


def _usage_info_kb():
    """(resident KB, footprint KB, max-footprint KB, virtual KB) or None.
    The footprint comes from proc_pid_rusage(RUSAGE_INFO_V4) —
    ri_phys_footprint is the Activity-Monitor axis. PROC_PIDTASKINFO's
    pti_resident_size is NOT it (the review: the first libproc read
    logged the resident figure twice under two names); the taskinfo
    call still serves the virtual size, which rusage_v4 does not carry.
    The resident reading doubles as a layout self-check: it must track
    the mach rss line within a few KB."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util

        libproc_name = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        libproc = ctypes.CDLL(libproc_name)
        libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        libproc.proc_pid_rusage.restype = ctypes.c_int
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                         ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidinfo.restype = ctypes.c_int

        class _Usage(ctypes.Structure):
            _fields_ = [
                ("uuid", ctypes.c_uint8 * 16),
                ("user_time", ctypes.c_uint64),
                ("system_time", ctypes.c_uint64),
                ("pkg_idle_wkups", ctypes.c_uint64),
                ("interrupt_wkups", ctypes.c_uint64),
                ("pageins", ctypes.c_uint64),
                ("wired_size", ctypes.c_uint64),
                ("resident_size", ctypes.c_uint64),
                ("phys_footprint", ctypes.c_uint64),
                ("proc_start_abstime", ctypes.c_uint64),
                ("proc_exit_abstime", ctypes.c_uint64),
                ("child_user_time", ctypes.c_uint64),
                ("child_system_time", ctypes.c_uint64),
                ("child_pkg_idle_wkups", ctypes.c_uint64),
                ("child_interrupt_wkups", ctypes.c_uint64),
                ("child_pageins", ctypes.c_uint64),
                ("child_elapsed_abstime", ctypes.c_uint64),
                ("diskio_bytesread", ctypes.c_uint64),
                ("diskio_byteswritten", ctypes.c_uint64),
                ("cpu_time_qos_default", ctypes.c_uint64),
                ("cpu_time_qos_maintenance", ctypes.c_uint64),
                ("cpu_time_qos_background", ctypes.c_uint64),
                ("cpu_time_qos_utility", ctypes.c_uint64),
                ("cpu_time_qos_legacy", ctypes.c_uint64),
                ("cpu_time_qos_user_initiated", ctypes.c_uint64),
                ("cpu_time_qos_user_interactive", ctypes.c_uint64),
                ("billed_system_time", ctypes.c_uint64),
                ("serviced_system_time", ctypes.c_uint64),
                ("logical_writes", ctypes.c_uint64),
                ("lifetime_max_phys_footprint", ctypes.c_uint64),
                ("instructions", ctypes.c_uint64),
                ("cycles", ctypes.c_uint64),
                ("billed_energy", ctypes.c_uint64),
                ("serviced_energy", ctypes.c_uint64),
                ("interval_max_phys_footprint", ctypes.c_uint64),
                ("runnable_time", ctypes.c_uint64),
                ("flags", ctypes.c_uint64),
            ]

        usage = _Usage()
        rc = libproc.proc_pid_rusage(os.getpid(), 4,  # RUSAGE_INFO_V4
                                     ctypes.byref(usage))
        if rc != 0 or not usage.phys_footprint:
            return None

        class _TaskInfo(ctypes.Structure):
            _fields_ = [
                ("virtual_size", ctypes.c_uint64),
                ("resident_size", ctypes.c_uint64),
                ("total_user", ctypes.c_uint64),
                ("total_system", ctypes.c_uint64),
                ("threads_user", ctypes.c_uint64),
                ("threads_system", ctypes.c_uint64),
                ("policy", ctypes.c_int32),
                ("faults", ctypes.c_int32),
                ("pageins", ctypes.c_int32),
                ("cow_faults", ctypes.c_int32),
                ("messages_sent", ctypes.c_int32),
                ("messages_received", ctypes.c_int32),
                ("syscalls_mach", ctypes.c_int32),
                ("syscalls_unix", ctypes.c_int32),
                ("csw", ctypes.c_int32),
                ("threadnum", ctypes.c_int32),
                ("numrunning", ctypes.c_int32),
                ("priority", ctypes.c_int32),
            ]

        info = _TaskInfo()
        written = libproc.proc_pidinfo(os.getpid(), 4, 0,  # PROC_PIDTASKINFO
                                       ctypes.byref(info), ctypes.sizeof(info))
        virtual = int(info.virtual_size) // 1024 if written > 0 else 0
        return (int(usage.resident_size) // 1024,
                int(usage.phys_footprint) // 1024,
                int(usage.lifetime_max_phys_footprint) // 1024,
                virtual)
    except Exception:
        return None


def _qml_class_counts():
    """A per-class census of every QQuickItem across the visible
    windows. The tick diff names the accumulating item type."""
    try:
        from PyQt6.QtQuick import QQuickWindow
    except Exception as exc:
        return {"qml-import-err": repr(exc)[:60]}
    counts = {}
    roots = []
    try:
        # Cura 5.13's application has no getQmlEngine() (the review's
        # log shows the AttributeError), so walk the QML windows
        # directly; the engine's rootObjects() stays as the fallback
        # for versions that still carry it.
        from PyQt6.QtGui import QGuiApplication
        for window in QGuiApplication.topLevelWindows():
            if isinstance(window, QQuickWindow):
                try:
                    content = window.contentItem()
                except Exception:
                    content = None
                if content is not None:
                    roots.append(content)
    except Exception as exc:
        counts["root-err"] = repr(exc)[:60]
    if not roots:
        try:
            from UM.Application import Application
            engine = Application.getInstance().getQmlEngine()
            roots = list(engine.rootObjects())
        except Exception as exc:
            counts["root-err"] = repr(exc)[:60]
    counts["<roots>"] = len(roots)
    try:
        for root in roots:
            # Duck-type the walk: whatever the window type is, any
            # object exposing childItems() is traversable. The old
            # isinstance gate skipped everything silently when the
            # roots were neither QQuickWindow nor QQuickItem.
            stack = deque([root])
            while stack:
                child = stack.pop()
                name = type(child).__name__
                counts[name] = counts.get(name, 0) + 1
                try:
                    stack.extend(child.childItems())
                except Exception:
                    continue
    except Exception as exc:
        counts["walk-err"] = repr(exc)[:60]
    return counts


def _runtime_sizes(runtime):
    """Sizes of every collection reachable within two attribute hops
    of the runtime, keyed by path (e.g. 'console._transcript'). The
    component level deliberately includes private attributes — the
    interesting accumulators live there."""
    sizes = {}
    try:
        for name in dir(runtime):
            if name.startswith("_"):
                continue
            try:
                component = getattr(runtime, name)
            except Exception:
                continue
            for attr in dir(component):
                if attr.startswith("__"):
                    continue
                try:
                    value = getattr(component, attr)
                except Exception:
                    continue
                if isinstance(value, (list, dict, deque, tuple, str, set, bytes, bytearray)):
                    sizes[f"{name}.{attr}"] = len(value)
    except Exception as exc:
        sizes["walk-err"] = repr(exc)[:60]
    return sizes


def _diff(previous, current):
    """Positive-delta entries, largest first: (key, previous, current)."""
    rows = []
    for key, size in current.items():
        if not isinstance(size, int):
            continue
        before = previous.get(key, 0)
        if isinstance(before, int) and size > before:
            rows.append((key, before, size))
    rows.sort(key=lambda row: row[2] - row[1], reverse=True)
    return rows[: _TOP_N]


def _camera_line():
    """The video path's gauges for one tick: none / direct / bridged.
    A bridged stream exposes the relay's write backlog and the upstream
    reply's buffered bytes — a loader that stops consuming shows up as
    a climbing backlog, the one way the probe can see the loader's
    C++-side frame accumulation. A direct stream is named but blind."""
    try:
        from UM.Application import Application
        from .MoonrakerOutputDevice import MoonrakerOutputDevice
        for device in Application.getInstance().getOutputDeviceManager().getOutputDevices():
            if not isinstance(device, MoonrakerOutputDevice):
                continue
            camera = getattr(getattr(device, "activePrinter", None), "_camera", None)
            if camera is None:
                return "camera n/a"
            if not getattr(camera, "_url", ""):
                return "camera none"
            bridge = getattr(camera, "_camera_bridge", None)
            if bridge is None:
                return "camera direct"
            backlog = buffered = 0
            for socket, (reply, _buf, _sent) in getattr(bridge, "_relays", {}).items():
                try: backlog += socket.bytesToWrite()
                except Exception: pass
                if reply is not None:
                    try: buffered += reply.bytesAvailable()
                    except Exception: pass
            return "camera bridged relays=%d backlog=%dB upstream=%dB relayed=%dB" % (
                len(getattr(bridge, "_relays", {})), backlog, buffered,
                getattr(bridge, "_relayed_bytes", 0))
        return "camera n/a"
    except Exception as exc:
        return f"camera err {exc!r}"[:80]


def _frame_tag(frame: str) -> str:
    """One traceback frame in ~80 chars: its source text plus file:line.
    The old join cut the outermost frame's header mid-path whenever the
    220-char cap landed there, so the farthest file:line was lost."""
    lines = frame.splitlines()
    head = lines[0].strip() if lines else ""
    text = lines[-1].strip() if len(lines) > 1 else head
    if head.startswith('File "') and '", line ' in head:
        path, _, rest = head[len('File "'):].partition('", line ')
        number = rest.split(",", 1)[0] if rest else "?"
        tail = "/".join(path.rsplit("/", 2)[-2:])
        if len(lines) > 1:
            return f"{text[:48]} ({tail}:{number})"
        # A single-line frame (PyQt's C-level synthesized frames):
        # only the location exists.
        return f"{tail}:{number}"
    return text[:70]


class LeakProbe:
    """The 10-second sampler. Started once from the plugin's
    register() hook for the diagnostic snapshot ONLY."""

    def __init__(self, runtime, parent=None):
        self.runtime = runtime
        self._timer = QTimer(parent)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        # NOTHING heavy here: the probe must not touch tracemalloc or
        # the QML engine during plugin load — the allocator tax and
        # the engine walk mid-initialization stalled Cura's startup
        # after "Loading plugins" (the author's report). Everything
        # defers to the first enabled tick, a minute into the session.
        self._enabled = False
        self._usage_failure_logged = False
        self._ticks = 0
        self._trace_snapshot = None
        self._qml_previous = {}
        self._sizes_previous = {}
        self._log_path = _LOG_PATH
        # The file must not exist at all unless the diagnostics toggle
        # is ON (the ruling): nothing is written — not even a
        # registration line — while the checkbox is unticked. The
        # first enabled tick creates the file and logs the start line.
        if not self._enabled_now():
            self._timer.start()
            return
        self._log_path = self._pick_log_path()
        # One registration line per launch with the toggle ON: the pid
        # and install path name the instance, and the raw toggle value
        # proves what the preference read saw — the Windows run logged
        # nothing at all and the silence could not say why.
        try:
            from UM.Application import Application
            from .PrinterConfig import PrinterConfigStore
            raw = Application.getInstance().getPreferences().getValue(
                PrinterConfigStore.LEGACY_MAP["memory_diagnostics_log"])
        except Exception as exc:
            raw = f"read-err {exc!r}"
        self._log(f"registered toggle={raw!r} pid={os.getpid()} "
                  f"path={os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} "
                  f"platform={sys.platform} log={self._log_path}")
        self._timer.start()

    @staticmethod
    def _pick_log_path() -> str:
        # The home-dir file first (the documented location); if the
        # home write is unavailable, the Cura preferences dir keeps
        # the instrument working and the registration line names the
        # chosen path.
        candidate = Path.home() / "moonraker_leak.log"
        try:
            with open(candidate, "a", encoding="utf-8"):
                pass
            return str(candidate)
        except OSError:
            try:
                from UM.Resources import Resources
                return str(Path(Resources.getStoragePath(Resources.Preferences)) / "moonraker_leak.log")
            except Exception:
                return str(candidate)

    def _enabled_now(self) -> bool:
        # The settings' diagnostics toggle — read live each minute,
        # so the instrument turns on and off without a restart. The
        # key comes from PrinterConfig's map, the preference store's
        # single owner.
        try:
            from UM.Application import Application
            from .PrinterConfig import PrinterConfigStore
            return bool(Application.getInstance().getPreferences().getValue(
                PrinterConfigStore.LEGACY_MAP["memory_diagnostics_log"]))
        except Exception:
            return False

    @staticmethod
    def _trace_enabled_now() -> bool:
        # The separate trace toggle: the snapshot stall is real, so the
        # Python-allocation axis is opt-in on top of the main
        # diagnostics toggle, read live like the main toggle.
        try:
            from UM.Application import Application
            from .PrinterConfig import PrinterConfigStore
            return bool(Application.getInstance().getPreferences().getValue(
                PrinterConfigStore.LEGACY_MAP["memory_diagnostics_trace"]))
        except Exception:
            return False

    def _top_traces(self) -> list:
        try:
            if self._trace_snapshot is None:
                # The first trace tick arms tracemalloc here — never
                # during plugin load — and its first diff covers the
                # minute since, not Cura's startup allocations.
                tracemalloc.start(25)
                self._trace_snapshot = tracemalloc.take_snapshot()
                return ["trace-armed"]
            snapshot = tracemalloc.take_snapshot()
            growth = snapshot.compare_to(self._trace_snapshot, "traceback")
            self._trace_snapshot = snapshot
            lines = []
            for stat in growth[:_TOP_N]:
                frames = stat.traceback.format()[-3:]
                short = " <- ".join(_frame_tag(frame) for frame in frames[-3:])[:220]
                lines.append(f"py +{stat.size_diff:+d}B {stat.count_diff:+d} {short}")
            return lines
        except Exception as exc:
            return [f"trace-err {exc!r}"]

    def _tick(self):
        if not self._enabled_now():
            if self._enabled:
                # The toggle went off: drop the allocator tax and the
                # trace window so re-enabling starts a fresh one.
                self._enabled = False
                if self._trace_snapshot is not None:
                    try:
                        tracemalloc.stop()
                    except Exception:
                        pass
                self._trace_snapshot = None
                self._qml_previous = {}
                self._sizes_previous = {}
                self._ticks = 0
                self._log("stop")
            return
        if not self._enabled:
            self._enabled = True
            if not Path(str(self._log_path)).exists():
                # The toggle turned on after launch: the file is
                # created on the first enabled tick (the no-file-while-
                # off ruling).
                self._log_path = self._pick_log_path()
            # The pid and the install path name the instance: the
            # author's log showed three starts — multiple installed
            # copies each register their own probe.
            self._log("start pid=%s path=%s platform=%s" % (
                os.getpid(), os.path.dirname(os.path.dirname(os.path.abspath(__file__))), sys.platform))
        try:
            rss, source = _rss_kb()
            self._log(f"rss={rss}kb src={source}")
        except Exception as exc:
            self._log(f"rss-err {exc!r}")
        try:
            usage = _usage_info_kb()
            if usage is not None:
                # Distinct lines, so no axis can masquerade as another
                # (the review: the first libproc read logged the same
                # resident figure under two names).
                self._log(f"resident={usage[0]}kb src=rusage-v4")
                self._log(f"phys_footprint={usage[1]}kb src=rusage-v4")
                self._log(f"max_phys_footprint={usage[2]}kb src=rusage-v4")
                self._log(f"virtual={usage[3]}kb src=proc-taskinfo")
            elif sys.platform == "darwin" and not self._usage_failure_logged:
                self._usage_failure_logged = True
                self._log("usage n/a (proc_pid_rusage refused)")
        except Exception as exc:
            self._log(f"usage-err {exc!r}")
        try:
            # The stage at each tick: the native RSS jumps (no Python
            # trace) must be pinned to the page that was showing when
            # they happened (the 2026-09-16 report).
            from UM.Application import Application
            controller = Application.getInstance().getController()
            stage = controller.getActiveStage()
            self._log("stage=%s" % (getattr(stage, "getPluginId", lambda: "?")() if stage is not None else "None"))
        except Exception as exc:
            self._log(f"stage-err {exc!r}")
        try:
            # The live layer separates per-layer preview churn (steps at
            # layer transitions) from everything else in the footprint.
            state = getattr(getattr(self.runtime, "preview", None), "_state", None)
            layer = getattr(state, "path_layer", None)
            self._log("layer=%s" % ("?" if layer is None else int(layer)))
        except Exception as exc:
            self._log(f"layer-err {exc!r}")
        try:
            self._log(_camera_line())
        except Exception as exc:
            self._log(f"camera-err {exc!r}")
        self._ticks += 1
        if self._trace_enabled_now():
            if self._ticks % _SLOW_TICKS == 0:
                for line in self._top_traces():
                    self._log(line)
        elif self._trace_snapshot is not None:
            # The trace toggle went off: drop the allocator tax and the
            # window so re-enabling starts a fresh one. (The author's
            # live find: the old elif wiped the window on EVERY
            # ordinary tick, so every slow tick re-armed from scratch
            # and the compare branch never ran.)
            try:
                tracemalloc.stop()
            except Exception:
                pass
            self._trace_snapshot = None
        try:
            qml = _qml_class_counts()
            bad = {key: value for key, value in qml.items() if not isinstance(value, int)}
            for key, value in bad.items():
                # A census failure must surface verbatim — the old
                # silent-empty census hid a blind axis (the review).
                self._log(f"  qml-err {key}={value}")
            for row in _diff(self._qml_previous, qml):
                self._log(f"  qml {row[0]}: {row[1]} -> {row[2]}")
            self._qml_previous = qml
        except Exception as exc:
            self._log(f"  qml-err {exc!r}")
        try:
            sizes = _runtime_sizes(self.runtime)
            for row in _diff(self._sizes_previous, sizes):
                self._log(f"  size {row[0]}: {row[1]} -> {row[2]}")
            self._sizes_previous = sizes
        except Exception as exc:
            self._log(f"  size-err {exc!r}")

    def _log(self, message):
        try:
            with open(getattr(self, "_log_path", _LOG_PATH), "a", encoding="utf-8") as handle:
                handle.write(f"{time.time():.0f} {message}\n")
                handle.flush()
        except Exception:
            pass


_ACTIVE = None


def start_leak_probe(runtime, parent=None):
    """The single entry. The module global pins the instance: the
    parented timer alone was not enough — the first build's probe was
    collected before its first timed tick and logged nothing. The
    double-start guard returns the live probe — a second registration
    must not stack a second timer and abandon the first (the
    reviewer's catch)."""
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE
    _ACTIVE = LeakProbe(runtime, parent)
    return _ACTIVE


def stop_leak_probe():
    """The teardown path: the probe must not survive plugin
    deinitialisation with a dead runtime and a live timer (the
    review's lifecycle catch — re-registering would otherwise retain
    the old closed runtime and stack another timer)."""
    global _ACTIVE
    probe = _ACTIVE
    _ACTIVE = None
    if probe is None:
        return
    try:
        probe._timer.stop()
        probe._timer.deleteLater()
    except Exception:
        pass
    probe.runtime = None
    # The trace sub-option clears whenever the allocator is running —
    # a stop before the first trace tick used to leave tracemalloc on
    # (the snapshot guard missed the armed-but-unsampled state).
    if getattr(tracemalloc, "is_tracing", lambda: False)():
        try:
            tracemalloc.stop()
        except Exception:
            pass
