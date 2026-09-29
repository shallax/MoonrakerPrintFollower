"""Coverage suite for the coordinator and the two control owners.

``test_monitor_qml_contracts.py`` reaches PrintCoordinator through the full follower
runtime, so every branch it takes needs a live transport, socket and
Cura host. This file drives the SAME coordinator code against scripted
collaborators instead — which is what the runtime cannot arrange: the
refused and aged load requests, the toolpath flaps that detach, the
header-filament latch, the monitor-only download's terminal conditions,
and the Moonraker metadata ladder's refusal, give-up and supersession
paths. ToolheadController and ConsoleController get the same treatment:
their existing suites walk the happy paths, so this one targets the
preset and clamp edges, the pump's gate transitions, and the console's
reload, clear and pre-migration fallbacks.

Fidelity: every collaborator is a real QObject carrying the real signal
surface, and every assertion reads state the module itself produced — a
published value block, a sent script, a queue's contents. Nothing here
patches the modules under test.

Lines that stay uncovered, and why (the running total moved with the
modules; the references below are against the current files):

* ConsoleController:45 — the body of the module-level
  ``_trim_transcript`` helper. Nothing in mpf/ or tests/ calls it;
  it is unreachable without invoking an unused private helper.
* ToolheadController:410-411 — the ``except ValueError`` guard around
  ``make_extrude_op`` in ``extrude()``. Both arguments reach it only
  from ``set_extrude_distance``/``set_extrude_speed``, which already
  enforce the policy's own bounds, and the direction is normalised to
  +/-1 first: no public call can hand it an out-of-range value. Its
  sibling in ``jog()`` (252-253) IS reachable and is covered — the
  clamp can return a positive move under the minimum distance.
* ToolheadController:430 — the tap-side branch of the queue
  reconciliation in ``_push``. A tap that survived ``push_op`` is never
  the tail the re-clamp acts on: ``jog()`` clamped it against this same
  estimate and snapshot moments earlier, so the re-clamp returns it
  unchanged. The branch keeps the accounting exact for the tap the
  queue actually gained if that ever stops holding.

Everything else in the three modules runs here, including the paths the
existing suites leave to the full follower runtime.
"""
from __future__ import annotations

import copy
import json
import math
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from tests.qt_runtime_support import QT_AVAILABLE, runtime

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal

    from mpf.ConsolePolicy import MAX_HISTORY, MAX_LINE, MAX_PENDING, MAX_TRANSCRIPT
    from mpf.MonitorPermissions import Observation
    from mpf.PrinterConfig import PrinterConfig

    if "UM" not in sys.modules:
        # PrintCoordinator imports UM.Logger; the container has no Cura
        # host, so a no-op Logger stands in (the seam the existing pause
        # suite uses). The trace test patches the module attribute
        # instead, so nothing here depends on this stub recording.
        _um = ModuleType("UM")
        _um_logger = ModuleType("UM.Logger")
        _um_logger.Logger = SimpleNamespace(log=lambda *args, **kwargs: None)
        _um.Logger = _um_logger
        sys.modules["UM"] = _um
        sys.modules["UM.Logger"] = _um_logger

    def _status(state="printing", filename="cube.gcode", **overrides):
        payload = {
            "print_stats": {"state": state, "filename": filename, "print_duration": 120.0,
                            "info": {"current_layer": 5, "total_layer": 100}},
            "virtual_sdcard": {"file_position": 4500, "file_size": 100000, "progress": 0.05},
            "gcode_move": {"gcode_position": [10.0, 10.0, 1.2, 100.0],
                           "absolute_coordinates": True},
            "motion_report": {"live_position": [10.0, 10.0, 1.2, 0.0]},
        }
        payload.update(overrides)
        return payload

    def _ranges(count=6, width=1000):
        return tuple((n * width, (n + 1) * width) for n in range(count))

    def _view(job_key=("cube.gcode", 100000, 1), ranges=None, pause_layers=()):
        return SimpleNamespace(job_key=job_key, ranges=list(ranges or _ranges()),
                               pause_layers=set(pause_layers), current_layer_map={},
                               layer_at=lambda position: 1)

    def _plate_ring(name, vertices=300, offset=0.0):
        """One ring in Klipper's DEFINE shape: a flat coordinate run,
        past the plate's vertex budget, so the projection both validates
        every point and decimates the ring."""
        polygon = []
        for step in range(vertices):
            angle = 2.0 * math.pi * step / vertices
            polygon.append(round(20.0 + offset + 10.0 * math.cos(angle), 3))
            polygon.append(round(20.0 + 10.0 * math.sin(angle), 3))
        return {"name": name, "center": [20.0 + offset, 20.0], "polygon": polygon}

    def _plate_geometry(count=80, vertices=300):
        """A DEFINE payload of `count` rings — the shape every core poll
        carries along with the moving file position."""
        return {"objects": [_plate_ring(f"OBJ_{index}", vertices, float(index))
                            for index in range(count)],
                "excluded_objects": [], "current_object": None}

    def _normalisation_spy():
        """A counter over the plate projection's ring walk — the
        per-vertex work the memo exists to remove. Returns the call
        list and the patch that installs it."""
        from mpf import MonitorFormatting
        calls = []
        real_finite_polygon = MonitorFormatting._finite_polygon

        def counted_finite_polygon(value):
            calls.append(value)
            return real_finite_polygon(value)

        return calls, patch.object(MonitorFormatting, "_finite_polygon",
                                   counted_finite_polygon)

    class _AlwaysWalk:
        """The pre-fix call site: every poll re-normalises the payload."""

        def __init__(self):
            self.walks = 0

        def value(self, exclude_object, job=None):
            from mpf.MonitorFormatting import plate_values
            self.walks += 1
            return plate_values(exclude_object)

    class _Client(QObject):
        statusReceived = pyqtSignal(object)
        connectionChanged = pyqtSignal(bool, str)
        sessionInvalidated = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.connected = False
            self.forced = 0
            self.sent_json = []
            self.reply = None
            self.transport = SimpleNamespace(send_json=self._send_json)

        def _send_json(self, channel, label, method, path, callback, category=None):
            self.sent_json.append((channel, label, method, path, category))
            self.reply = callback
            return True

        def force_refresh(self):
            self.forced += 1

    class _Binding(QObject):
        changed = pyqtSignal()

        def __init__(self, config, configured=True):
            super().__init__()
            self.config = config
            self.configured = configured
            self.identity = ("http://printer", "Printer")

    class _Files(QObject):
        changed = pyqtSignal()
        failed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.job_key = None
            self.path = ""
            self.metadata = None
            self.metadata_complete = False
            self.download_fraction = None
            self.phase = ""
            self._lease = None
            self.metadata_only_started = True
            self.bound = "unbound"
            self.file_requests = []
            self.metadata_requests = 0
            self.metadata_only = []

        def bind(self, job):
            self.bound = job
            self.job_key = job

        def request_file(self, *, retry=False):
            self.file_requests.append(retry)

        def request_metadata(self):
            self.metadata_requests += 1

        def request_metadata_only(self, callback):
            self.metadata_only.append(callback)
            return self.metadata_only_started

        def lease(self):
            return self._lease

    class _Index(QObject):
        changed = pyqtSignal()
        # The pass's throttled tick: the coordinator reads it without
        # running the full refresh, so the fake carries it separately
        # from `changed` — the same split the service makes.
        progress_changed = pyqtSignal()
        failed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.view = None
            self.phase = ""
            self.progress = None
            self.bound = "unbound"
            self.requests = 0
            self.followed = []
            self.hydration = []
            self.tracking_resets = 0
            self.plate_anchors = []
            self.plate_positions = []
            self.plate_lives = []
            self.plate_visited_rows = []
            self.plate_split = None
            self.manual_anchor = None
            self.manual_split = None

        def bind(self, job):
            self.bound = job

        def request(self):
            self.requests += 1

        def set_followed_layer(self, layer):
            self.followed.append(layer)

        def request_hydration(self, layer):
            self.hydration.append(layer)

        def set_manual_anchor(self, layer):
            self.manual_anchor = layer

        def set_manual_split(self, motions):
            self.manual_split = motions

        def observe_motion(self, anchor, file_position=None, live_position=None, paused=False, extruding=None):
            from mpf.PrintState import MotionProgress
            return MotionProgress(anchor, self.plate_split if file_position is not None else None, 100)

        def plate_progress(self, anchor, file_position=None, live_position=None, paused=False, extruding=None, *, motion=...):
            # The service-side prep's shape; the coordinator tests
            # pin the wiring, not the payload.
            self.plate_anchors.append(anchor)
            self.plate_positions.append(file_position)
            self.plate_lives.append(live_position)
            split = self.plate_split if motion is ... else motion.split if motion is not None else None
            return {"layers": {}, "split": split,
                    "method": "unavailable", "anchor": anchor}

        def plate_visited(self, anchor, split, rows):
            # The rows the coordinator hands the printed-object walk —
            # the projection's own output, shared with whatever it was
            # built from.
            self.plate_visited_rows.append((anchor, split, rows))
            return frozenset(row["name"] for row in rows if row.get("polygon"))

        def plate_pass_fraction(self):
            return None

        def reset_tracking(self):
            self.tracking_resets += 1

    class _Cura(QObject):
        changed = pyqtSignal()
        positionChanged = pyqtSignal()
        invalidated = pyqtSignal(str)
        viewSwapped = pyqtSignal()
        fileLoaded = pyqtSignal(str)
        loadFailed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.has_toolpath = False
            self.heights = []
            self.max_layer = None
            self.selected_layer = None
            self.loading = False
            self.preview_active = False
            self.scene_has_objects = False
            self.nudges = 0
            self.layer_view_nudges = 0
            self.watched = []
            self.stages = []
            self.loads = []
            self.invalidated_reasons = []

        def nudge_cura_activity(self):
            self.nudges += 1

        def nudge_layer_view(self):
            self.layer_view_nudges += 1

        def watch(self, enabled):
            self.watched.append(enabled)

        def switch_to_preview(self):
            self.stages.append("PreviewStage")
            return True

        def load(self, lease):
            self.loads.append(lease)

        def invalidate(self, reason):
            self.invalidated_reasons.append(reason)

    class _Preview(QObject):
        changed = pyqtSignal()
        message = pyqtSignal(str)
        controlsChanged = pyqtSignal()
        loadRequested = pyqtSignal()
        attachmentRequested = pyqtSignal()
        improveEtaRequested = pyqtSignal()
        pauseAtLayerRequested = pyqtSignal(int)
        removePauseRequested = pyqtSignal(int)
        clearPausesRequested = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.state = SimpleNamespace(attached=False, eta_text="")
            self.attach_calls = []
            self.reset_prints = 0
            self.reset_trackings = 0
            self.invalidations = 0
            self.override = False
            self.observe_result = ("Following", ())
            self.observed = []
            self.eta_updates = []
            self.remaining_end_value = None
            self.remaining_value = 240.0
            self.remaining_calls = []
            self.format_duration = lambda seconds: f"{seconds:.0f}s"

        def attach(self, on):
            self.attach_calls.append(on)
            self.state.attached = on

        def reset_print(self):
            self.reset_prints += 1

        def reset_tracking(self):
            self.reset_trackings += 1

        def invalidate_view(self):
            self.invalidations += 1

        def detect_override(self):
            return self.override

        def observe(self, snapshot, status, config, view):
            self.observed.append((snapshot, status, config, view))
            return self.observe_result

        def update_eta(self, snapshot, view):
            self.eta_updates.append((snapshot, view))

        def remaining_end(self, view, estimated):
            return self.remaining_end_value

        def remaining(self, layer, view=None, end=True):
            self.remaining_calls.append((layer, view, end))
            return self.remaining_value

    class _Pauses(QObject):
        changed = pyqtSignal()
        message = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.layers = set()
            self.states = {}
            self.bound = "unbound"
            self.toggles = []
            self.removed = []
            self.cleared = 0
            self.observed_layers = []

        def bind(self, job):
            self.bound = job

        def toggle(self, layer, current, total):
            self.toggles.append((layer, current, total))

        def remove(self, layer):
            self.removed.append(layer)

        def clear(self):
            self.cleared += 1

        def observe(self, index):
            self.observed_layers.append(index)

    class _Presentation(QObject):
        controlsChanged = pyqtSignal()
        loadRequested = pyqtSignal()
        attachmentRequested = pyqtSignal()
        improveEtaRequested = pyqtSignal()
        pauseAtLayerRequested = pyqtSignal(int)
        removePauseRequested = pyqtSignal(int)
        clearPausesRequested = pyqtSignal()
        replaceConfirmed = pyqtSignal()
        replaceCancelled = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.published = []

        def publish(self, values):
            self.published.append(values)

    class _BedMesh(QObject):
        def __init__(self):
            super().__init__()
            self.updates = []
            self.clears = 0

        def update(self, value):
            self.updates.append(value)

        def clear(self):
            self.clears += 1

    class _ConsoleData(QObject):
        connectionStateChanged = pyqtSignal(str)
        invalidated = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.requests = []
            self.started = True
            self.callback = None

        def request(self, channel, method, path, callback, *, body=None, replace=False,
                    category="auxiliary", timeout_ms=5000):
            self.requests.append(SimpleNamespace(channel=channel, method=method, path=path,
                                                 body=body, category=category, timeout_ms=timeout_ms))
            self.callback = callback
            return self.started

    class _ConsoleCommands(QObject):
        emergencyStopped = pyqtSignal()

    class _FakePersistence:
        """The facade face ConsoleController uses: per-machine state
        shards, with a write verdict the tests can flip."""

        def __init__(self, shard=None, ok=True, shards=None):
            self.shards = dict(shards or {})
            self.fallback = shard
            self.ok = ok
            self.writes = {}

        def get_machine_state(self, machine_id):
            if machine_id in self.shards:
                return self.shards[machine_id]
            return self.fallback

        def set_machine_state(self, machine_id, patch):
            if not self.ok:
                return False
            self.shards[machine_id] = dict(patch)
            self.writes[machine_id] = dict(patch)
            return True

    class _ToolheadData(QObject):
        changed = pyqtSignal()
        invalidated = pyqtSignal()
        commandChanged = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.active = True
            self.connected = True
            self.snapshot = SimpleNamespace(core={}, auxiliary={})
            self.observation = None
            self.guard_calls = []

        def set_toolhead_guard(self, active):
            self.guard_calls.append(active)

        def set_state(self, state, *, live=(10.0, 10.0, 10.0, 0.0), minimum=(0, 0, 0),
                      maximum=(200, 200, 200), connection="yes"):
            self.snapshot = SimpleNamespace(
                core={"print_stats": {"state": state},
                      "gcode_move": {"absolute_coordinates": True,
                                     "gcode_position": tuple(live)},
                      "motion_report": {"live_position": list(live)}},
                auxiliary={"toolhead": {"homed_axes": "xyz",
                                        "axis_minimum": list(minimum),
                                        "axis_maximum": list(maximum)}})
            self.observation = Observation(
                active=self.active, connection=connection, state=state, homed_axes="xyz",
                assumed_stopped=False, save_config_pending=False, controls_locked=False,
                busy=False)
            self.changed.emit()

    class _ToolheadCommands(QObject):
        changed = pyqtSignal()
        emergencyStopped = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.busy = False
            self.refuse = False
            self.sent = []

        def send(self, label, path, body=None):
            if self.busy or self.refuse:
                return False
            self.sent.append((label, path, body))
            self.busy = True
            return True

        def complete(self):
            self.busy = False
            self.changed.emit()

    _COORDINATOR_CLASS = []

    def _coordinator_class():
        """The coordinator class, imported once — the lazy UM stub above
        must exist before the module's own import runs."""
        if not _COORDINATOR_CLASS:
            from mpf.PrintCoordinator import PrintCoordinator
            _COORDINATOR_CLASS.append(PrintCoordinator)
        return _COORDINATOR_CLASS[0]


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class CoordinatorCoverageTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self.fixture = self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)

    def _pump(self, seconds):
        """Run the event loop for a bounded stretch (timer-driven paths)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.fixture.events(5)

    def _accept(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.fixture.events(5)
            if predicate():
                return True
        return predicate()

    def _make(self, config=None, configured=True):
        coordinator_class = _coordinator_class()
        parts = SimpleNamespace(
            client=_Client(), binding=_Binding(config or PrinterConfig(), configured),
            files=_Files(), index=_Index(), cura=_Cura(), preview=_Preview(),
            pauses=_Pauses(), presentation=_Presentation(), bed_mesh=_BedMesh())
        parts.coordinator = coordinator_class(
            client=parts.client, binding=parts.binding, files=parts.files, index=parts.index,
            cura=parts.cura, preview=parts.preview, pauses=parts.pauses,
            presentation=parts.presentation, bed_mesh=parts.bed_mesh)
        self.addCleanup(parts.coordinator.close)
        return parts

    def _printing(self, parts, **overrides):
        parts.client.statusReceived.emit(_status("printing", **overrides))
        return parts


    def _plate_status(self, geometry, *, position=4500.0, duration=120.0,
                      filename="cube.gcode", reparse=False):
        """One core poll's payload as the session boundary delivers it:
        the DEFINITION rides along with the moving file position and
        the advancing clock, and the status is a fresh deep copy (what
        SessionSnapshot.copy_status hands the coordinator), so object
        identity never survives a poll. `reparse` goes further and
        rebuilds the payload as a new JSON parse would — new containers
        AND new float objects."""
        payload = json.loads(json.dumps(geometry)) if reparse else copy.deepcopy(geometry)
        status = _status("printing", filename=filename,
                         virtual_sdcard={"file_position": position, "file_size": 100000,
                                         "progress": 0.05},
                         exclude_object=payload)
        status["print_stats"]["print_duration"] = duration
        return status

    def _plate_poll(self, parts, geometry, **kwargs):
        """The payload delivered through the client's own signal, so the
        whole observe-then-refresh path runs."""
        parts.client.statusReceived.emit(self._plate_status(geometry, **kwargs))

    def _plate_parts(self, split=500):
        parts = self._printing(self._make())
        parts.index.view = _view()
        parts.index.plate_split = split
        return parts


    def _refresh_cost(self, geometry, *, passthrough=False, polls=4):
        """The coordinator's own refresh path, timed in milliseconds:
        the cold refresh (the one walk the memo cannot remove) and the
        steady-state refreshes after it. The payloads are built outside
        the clock — the status copy is the session boundary's cost, not
        the refresh's, and it would otherwise dominate both numbers
        equally."""
        parts = self._plate_parts()
        real_memo = getattr(parts.coordinator, "_plate_memo", None)
        if passthrough:
            parts.coordinator._plate_memo = _AlwaysWalk()
        payloads = [self._plate_status(geometry, position=4500.0 + step * 100.0,
                                       duration=120.0 + step)
                    for step in range(polls + 1)]
        started = time.perf_counter()
        parts.client.statusReceived.emit(payloads[0])
        cold = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        for payload in payloads[1:]:
            parts.client.statusReceived.emit(payload)
        steady = (time.perf_counter() - started) * 1000.0 / polls
        if passthrough:
            parts.coordinator._plate_memo = real_memo
        return cold, steady

        # The cold path is the same walk plus one definition snapshot —
        # its equality is pinned STRUCTURALLY by the ring-walk spy
        # above, not by a wall-clock ratio (the cold margin flipped
        # under gate load; a stopwatch on the first call proves
        # nothing the spy has not already proven).


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class ToolheadCoverageTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        from mpf.ToolheadController import ToolheadController
        self.controller_class = ToolheadController

    def _make(self, state="paused", **kwargs):
        data = _ToolheadData()
        commands = _ToolheadCommands()
        data.set_state(state, **kwargs)
        controller = self.controller_class(data, commands)
        self.addCleanup(controller.close)
        return controller, data, commands

    @staticmethod
    def _scripts(commands):
        return [body["script"] for _, path, body in commands.sent
                if path == "printer/gcode/script"]


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class ConsoleCoverageTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self.events = self._rt.__enter__().events
        self.addCleanup(self._rt.__exit__, None, None, None)
        from mpf.ConsoleController import ConsoleController
        self.controller_class = ConsoleController
        self.data = _ConsoleData()
        self.commands = _ConsoleCommands()
        self.applied = []
        self.config = None

    def _apply_config(self, config):
        self.applied.append(config)
        self.config = config

    def _make(self, config=None, identity=("A", "Printer A"), persistence=None):
        self.config = config if config is not None else PrinterConfig()
        identity_fn = None if identity is None else (
            identity if callable(identity) else (lambda: identity))
        return self.controller_class(
            self.data, self.commands, config=lambda: self.config,
            apply_config=self._apply_config, identity=identity_fn, persistence=persistence)


    @staticmethod
    def _response(label):
        return {"kind": "response", "text": label, "error": False, "success": False,
                "restored": False}


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['ConsoleCoverageTests', 'CoordinatorCoverageTests', 'MAX_HISTORY', 'MAX_LINE', 'MAX_PENDING', 'MAX_TRANSCRIPT', 'ModuleType', 'Observation', 'PrinterConfig', 'QObject', 'QT_AVAILABLE', 'SimpleNamespace', 'ToolheadCoverageTests', '_AlwaysWalk', '_BedMesh', '_Binding', '_COORDINATOR_CLASS', '_Client', '_ConsoleCommands', '_ConsoleData', '_Cura', '_FakePersistence', '_Files', '_Index', '_Pauses', '_Presentation', '_Preview', '_ToolheadCommands', '_ToolheadData', '_coordinator_class', '_normalisation_spy', '_plate_geometry', '_plate_ring', '_ranges', '_status', '_um', '_um_logger', '_view', 'annotations', 'copy', 'json', 'math', 'os', 'patch', 'pyqtSignal', 'replace', 'runtime', 'sys', 'tempfile', 'time', 'unittest']
