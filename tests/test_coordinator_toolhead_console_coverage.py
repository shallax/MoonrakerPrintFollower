"""Coverage suite for the coordinator and the two control owners.

``test_monitor.py`` reaches PrintCoordinator through the full follower
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

Lines that stay uncovered, and why — three statements out of 946:

* ConsoleController:45 — the body of the module-level
  ``_trim_transcript`` helper. Nothing in plugins/ or tests/ calls it;
  it is unreachable without invoking an unused private helper.
* ToolheadController:312-313 — the ``except ValueError`` guard around
  ``make_extrude_op`` in ``extrude()``. Both arguments reach it only
  from ``set_extrude_distance``/``set_extrude_speed``, which already
  enforce the policy's own bounds, and the direction is normalised to
  +/-1 first: no public call can hand it an out-of-range value. Its
  sibling in ``jog()`` (196-197) IS reachable and is covered — the
  clamp can return a positive move under the minimum distance.

Everything else in the three modules runs here, including the paths the
existing suites leave to the full follower runtime.
"""
from __future__ import annotations

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

    from plugins.ConsolePolicy import MAX_HISTORY, MAX_LINE, MAX_PENDING, MAX_TRANSCRIPT
    from plugins.MonitorPermissions import Observation
    from plugins.PrinterConfig import PrinterConfig

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

        def plate_progress(self, anchor, file_position=None, live_position=None):
            # The service-side prep's shape; the coordinator tests
            # pin the wiring, not the payload.
            self.plate_anchors.append(anchor)
            self.plate_positions.append(file_position)
            self.plate_lives.append(live_position)
            return {"layers": {}, "split": None, "method": "unavailable", "anchor": anchor}

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
            self.confirm_replace_callback = None
            self.loads = []
            self.invalidated_reasons = []

        def nudge_cura_activity(self):
            self.nudges += 1

        def nudge_layer_view(self):
            self.layer_view_nudges += 1

        def watch(self, enabled):
            self.watched.append(enabled)

        def confirm_replace(self, callback):
            self.confirm_replace_callback = callback

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
            from plugins.PrintCoordinator import PrintCoordinator
            _COORDINATOR_CLASS.append(PrintCoordinator)
        return _COORDINATOR_CLASS[0]


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class CoordinatorCoverageTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)

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

    def test_every_collaborator_signal_reaches_its_handler(self):
        parts = self._make()
        coordinator = parts.coordinator
        before = len(parts.presentation.published)
        parts.files.changed.emit()
        self.assertGreater(len(parts.presentation.published), before)
        parts.presentation.loadRequested.emit()
        self.assertIsNotNone(parts.cura.confirm_replace_callback)
        parts.cura.has_toolpath = True
        parts.presentation.attachmentRequested.emit()
        self.assertEqual(parts.preview.attach_calls, [True])
        parts.presentation.clearPausesRequested.emit()
        self.assertEqual(parts.pauses.cleared, 1)
        parts.presentation.pauseAtLayerRequested.emit(4)
        self.assertEqual(parts.pauses.toggles[-1], (3, None, None))
        parts.presentation.removePauseRequested.emit(4)
        self.assertEqual(parts.pauses.removed, [3])
        started = parts.index.requests
        parts.presentation.improveEtaRequested.emit()
        self.assertEqual(parts.index.requests, started + 1)
        parts.preview.controlsChanged.emit()
        parts.pauses.changed.emit()
        parts.pauses.message.emit("note from pauses")
        self.assertEqual(coordinator._detail, "note from pauses")
        parts.client.connectionChanged.emit(False, "Disconnected")
        self.assertEqual(coordinator._detail, "Disconnected")
        parts.client.sessionInvalidated.emit()
        self.assertEqual(coordinator._detail, "Not connected")
        # A service failure is logged, never raised into the caller.
        parts.files.failed.emit("boom")
        parts.index.failed.emit("boom")

    def test_a_printing_observation_binds_every_service_to_the_run(self):
        parts = self._printing(self._make())
        coordinator, files = parts.coordinator, parts.files
        self.assertTrue(coordinator.snapshot.active)
        self.assertEqual(coordinator.snapshot.layer.index, 4)
        self.assertEqual(coordinator.snapshot.layer.total, 100)
        self.assertEqual(coordinator.snapshot.layer.source, "Moonraker current_layer")
        self.assertEqual(files.bound, coordinator.snapshot.job_key)
        self.assertEqual(parts.index.bound, coordinator.snapshot.job_key)
        self.assertEqual(parts.pauses.bound, coordinator.snapshot.job_key)
        self.assertEqual(parts.pauses.observed_layers, [4])
        resets = parts.preview.reset_prints
        # The next poll of the SAME run is not a new job: no reset.
        self._printing(parts)
        self.assertEqual(parts.preview.reset_prints, resets)
        # A new filename re-starts the run and clears the preview state.
        parts.client.statusReceived.emit(_status("printing", filename="other.gcode"))
        self.assertEqual(parts.preview.reset_prints, resets + 1)

    def test_a_standby_frame_drops_the_run_identity(self):
        parts = self._printing(self._make())
        parts.client.statusReceived.emit(_status("standby"))
        self.assertFalse(parts.coordinator.snapshot.active)
        self.assertIsNone(parts.index.bound)
        self.assertIsNone(parts.files.bound)
        self.assertIsNone(parts.pauses.bound)

    def test_observe_ignores_non_mappings_and_a_closed_coordinator(self):
        coordinator = self._make().coordinator
        coordinator.observe("not a status")
        coordinator.observe(None)
        self.assertFalse(coordinator.snapshot.active)
        coordinator.close()
        coordinator.observe(_status())
        self.assertFalse(coordinator.snapshot.active)

    def test_an_unconfigured_load_request_only_explains_itself(self):
        coordinator = self._make(configured=False).coordinator
        coordinator.request_load()
        self.assertEqual(coordinator._detail,
                         "Set a Moonraker URL before loading the current print")
        self.assertFalse(coordinator._loads.load_requested)
        coordinator.download_for_monitor()
        self.assertEqual(coordinator._detail,
                         "Set a Moonraker URL before improving the monitor estimate")
        self.assertFalse(coordinator._loads.monitor_requested)

    def test_a_load_request_ages_out_against_a_standby_printer(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.request_load()
        self.assertTrue(coordinator._loads.load_requested)
        self.assertEqual(coordinator._detail, "Resolving current print…")
        self.assertEqual(parts.client.forced, 1)
        coordinator._loads._load_requested_at = time.monotonic() - 10.0
        coordinator.refresh()
        self.assertFalse(coordinator._loads.load_requested)
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_a_monitor_request_ages_out_against_a_standby_printer(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.download_for_monitor()
        self.assertTrue(coordinator._loads.monitor_requested)
        self.assertEqual(coordinator._detail,
                         "Downloading and indexing the print for the monitor…")
        self.assertEqual(parts.index.requests, 1)
        coordinator._loads._monitor_requested_at = time.monotonic() - 10.0
        coordinator.refresh()
        self.assertFalse(coordinator._loads.monitor_requested)
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_an_active_print_gets_the_longer_request_window(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads.request_monitor()
        coordinator._loads._monitor_requested_at = time.monotonic() - 3.0
        coordinator.refresh()
        self.assertTrue(coordinator._loads.monitor_requested)  # the 5 s window holds
        coordinator._loads._monitor_requested_at = time.monotonic() - 6.0
        coordinator.refresh()
        self.assertFalse(coordinator._loads.monitor_requested)
        self.assertNotEqual(coordinator._detail, "No active Moonraker print to load")

    def test_the_toolpath_arrival_nudges_cura_once_and_attaches(self):
        parts = self._make()
        coordinator, cura = parts.coordinator, parts.cura
        cura.has_toolpath = True
        coordinator.refresh()
        self.assertEqual((cura.nudges, cura.layer_view_nudges), (1, 1))
        self.assertEqual(parts.preview.attach_calls, [True])
        coordinator.refresh()
        self.assertEqual((cura.nudges, cura.layer_view_nudges), (1, 1))
        # The toolpath going away detaches for real (the 2026-09-17 ruling).
        cura.has_toolpath = False
        coordinator.refresh()
        self.assertEqual(parts.preview.attach_calls, [True, False])
        self.assertFalse(coordinator._had_toolpath)

    def test_a_toolpath_flap_never_overrides_a_deliberate_detach(self):
        parts = self._make()
        coordinator, cura = parts.coordinator, parts.cura
        cura.has_toolpath = True
        coordinator.refresh()
        coordinator.toggle_attachment()
        self.assertFalse(parts.preview.state.attached)
        self.assertTrue(coordinator._user_detached)
        for toolpath in (False, True):
            cura.has_toolpath = toolpath
            coordinator.refresh()
        self.assertEqual(parts.preview.attach_calls, [True, False])

    def test_attachment_is_refused_without_a_toolpath(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.toggle_attachment()
        self.assertEqual(parts.preview.attach_calls, [])
        self.assertFalse(coordinator._user_detached)
        # Detaching stays available: the control pins to detached.
        parts.preview.state.attached = True
        coordinator.toggle_attachment()
        self.assertEqual(parts.preview.attach_calls, [False])
        self.assertEqual(parts.client.forced, 0)

    def test_attaching_forces_a_refresh_and_detaching_does_not(self):
        parts = self._make()
        coordinator, cura = parts.coordinator, parts.cura
        cura.has_toolpath = True
        coordinator.toggle_attachment()
        self.assertTrue(parts.preview.state.attached)
        self.assertFalse(coordinator._user_detached)
        self.assertEqual(parts.client.forced, 1)

    def test_the_downloaded_header_supplies_the_filament_total(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "cube.gcode")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(";Filament used: 1.5m\nG1 X0\n")
        parts = self._printing(self._make())
        parts.files.path = path
        parts.coordinator.refresh()
        self.assertEqual(parts.coordinator.snapshot.filament_total, 1500.0)
        # A path yielding no header hands the scrub back to the metadata.
        parts.files.path = os.path.join(directory.name, "missing.gcode")
        parts.files.metadata = {"filament_total": 250.0}
        parts.coordinator.refresh()
        self.assertEqual(parts.coordinator.snapshot.filament_total, 250.0)
        # And an unparsable metadata value leaves the total absent.
        parts.files.metadata = {"filament_total": "not-a-number"}
        parts.coordinator.refresh()
        self.assertIsNone(parts.coordinator.snapshot.filament_total)

    def test_a_matching_index_view_arms_the_layer_progress(self):
        parts = self._printing(self._make())
        parts.index.view = _view()
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertTrue(snapshot.index_ready)
        self.assertEqual(snapshot.layer_progress, 0.5)
        # A position past the range is clamped, never extrapolated.
        parts.client.statusReceived.emit(
            _status("printing", virtual_sdcard={"file_position": 99999}))
        self.assertEqual(parts.coordinator.snapshot.layer_progress, 1.0)
        # A non-numeric position reads as no progress at all.
        parts.client.statusReceived.emit(
            _status("printing", virtual_sdcard={"file_position": "many"}))
        self.assertIsNone(parts.coordinator.snapshot.layer_progress)

    def test_an_index_view_for_another_file_is_not_this_print_evidence(self):
        parts = self._printing(self._make())
        parts.client.connected = True
        parts.index.view = _view(job_key=("stale.gcode", 100000, 7))
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertFalse(snapshot.index_ready)
        self.assertIsNone(snapshot.layer_progress)
        self.assertIsNone(parts.preview.observed[-1][3])

    def test_the_monitor_download_retires_on_index_error(self):
        parts = self._printing(self._make())
        parts.coordinator._loads.request_monitor()
        parts.index.phase = "error"
        parts.coordinator.refresh()
        self.assertFalse(parts.coordinator._loads.monitor_requested)

    def test_the_monitor_download_retires_on_download_error(self):
        parts = self._printing(self._make())
        parts.coordinator._loads.request_monitor()
        parts.files.phase = "error"
        parts.coordinator.refresh()
        self.assertFalse(parts.coordinator._loads.monitor_requested)

    def test_the_monitor_download_retires_once_the_index_lands(self):
        parts = self._printing(self._make())
        parts.coordinator._loads.request_monitor()
        parts.index.view = _view()
        parts.coordinator.refresh()
        self.assertFalse(parts.coordinator._loads.monitor_requested)

    def test_a_pending_load_hands_the_file_lease_to_cura(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = coordinator.snapshot.job_key
        parts.files.path = "/downloads/cube.gcode"
        parts.files._lease = "LEASE-1"
        coordinator.refresh()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(parts.cura.loads, ["LEASE-1"])
        # A lease that vanished between the read and the load loads nothing.
        coordinator._loads._load_job = coordinator.snapshot.job_key
        parts.files._lease = None
        coordinator.refresh()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(parts.cura.loads, ["LEASE-1"])

    def test_a_print_that_changed_mid_load_reports_it(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = ("gone.gcode", 1, 1)
        coordinator.refresh()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(coordinator._detail, "Print changed before it could be loaded")

    def test_a_load_waits_while_cura_is_still_busy(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = coordinator.snapshot.job_key
        parts.files.path = "/downloads/cube.gcode"
        parts.files._lease = "LEASE-1"
        parts.cura.loading = True
        coordinator.refresh()
        self.assertEqual(coordinator._loads._load_job, coordinator.snapshot.job_key)
        self.assertEqual(parts.cura.loads, [])

    def test_the_trace_line_names_the_resolution_source(self):
        parts = self._make(config=PrinterConfig(trace_layer=True))
        from plugins import PrintCoordinator as coordinator_module
        with patch.object(coordinator_module, "Logger") as logger:
            self._printing(parts)
        trace = [call for call in logger.log.call_args_list if "layer trace" in str(call)]
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0].args[-1], "Moonraker current_layer")
        # The 5 s throttle keeps the next poll from repeating it.
        first_at = parts.coordinator._layer_trace_at
        with patch.object(coordinator_module, "Logger") as logger:
            parts.coordinator.refresh()
        self.assertEqual(logger.log.call_args_list, [])
        parts.coordinator._layer_trace_at = first_at - 10.0
        with patch.object(coordinator_module, "Logger") as logger:
            parts.coordinator.refresh()
        self.assertTrue(any("layer trace" in str(call) for call in logger.log.call_args_list))

    def test_a_disconnected_client_invalidates_the_preview_view(self):
        parts = self._printing(self._make())
        before = parts.preview.invalidations
        parts.client.connected = False
        parts.coordinator.refresh()
        self.assertEqual(parts.preview.invalidations, before + 1)
        self.assertEqual(parts.index.followed, [])

    def test_the_plate_payload_uses_the_fresh_physical_layer(self):
        # The green-printed fix's second half: plate_progress and
        # plate_visited read the FRESH physical layer, never the
        # previous snapshot's — the old code kept the passed set on
        # the outgoing layer across a transition.
        parts = self._printing(self._make())
        parts.index.view = _view()
        status = _status()
        status["print_stats"]["info"]["current_layer"] = 5
        parts.client.statusReceived.emit(status)
        parts.coordinator.refresh()
        status["print_stats"]["info"]["current_layer"] = 6
        parts.client.statusReceived.emit(status)
        parts.coordinator.refresh()
        self.assertEqual(parts.index.plate_anchors, [4, 4, 5, 5],
                         "the plate payload anchored on the previous snapshot's layer")

    def test_a_detach_frozen_on_the_live_layer_carries_no_file_position(self):
        # The live report: detaching froze the CURRENT layer, and the
        # anchor-equality test kept feeding the payload the live file
        # position — the split never stopped, so the detach read as
        # dead. Attached-ness is the test: a frozen anchor is frozen
        # even while the print is still on that layer.
        parts = self._printing(self._make())
        parts.index.view = _view()
        # The serving gate (the reviewer's C): the frozen payload
        # serves while the popover is open.
        parts.coordinator.set_popover_open(True)
        parts.coordinator.set_plate_anchor(4)  # == the live layer
        parts.coordinator.refresh()
        # TWO payloads per refresh while detached and the popover is
        # open: the live one for the mini, the frozen one for the
        # popover (the live request). The detach's own refresh plus
        # the explicit one: four calls.
        self.assertEqual(parts.index.plate_anchors, [4, 4, 4, 4])
        self.assertIsNone(parts.index.plate_positions[-1],
                          "the frozen layer was still handed the live position")
        self.assertEqual(parts.index.manual_anchor, 4)
        # The scrub rides the same seam.
        parts.coordinator.set_plate_split(37)
        self.assertEqual(parts.index.manual_split, 37)
        # Re-attaching restores the live position flow (one payload).
        parts.coordinator.set_plate_anchor(None)
        parts.coordinator.refresh()
        self.assertIsNone(parts.index.manual_anchor)
        self.assertEqual(parts.index.plate_positions[-1], 4500)

    def test_the_plate_payload_shares_the_resolved_file_position(self):
        # A view with a valid position: the ONE resolved value feeds
        # both consumers — the layer fraction measures it (4500 of the
        # 4000..5000 range) and the plate split is handed the same
        # offset.
        parts = self._printing(self._make())
        parts.index.view = _view()
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertTrue(snapshot.index_ready)
        self.assertEqual(snapshot.layer_progress, 0.5)
        self.assertEqual(parts.index.plate_anchors, [4])
        self.assertEqual(parts.index.plate_positions, [4500])

    def test_a_monitor_only_index_builds_the_plate_payload_without_a_view(self):
        # The Improve-ETA download leaves the index's job key UNRESOLVED
        # (it names the active print, never a loaded file), so the
        # identity gate refuses the view — no view, hence no layer
        # fraction, but the plate still anchors on the resolved
        # physical layer and reads the live position.
        parts = self._printing(self._make())
        parts.index.view = _view(job_key=())
        parts.coordinator.refresh()
        snapshot = parts.coordinator.snapshot
        self.assertFalse(snapshot.index_ready)
        self.assertIsNone(snapshot.layer_progress)
        self.assertEqual(parts.index.plate_anchors, [4])
        self.assertEqual(parts.index.plate_positions, [4500])
        self.assertIsNotNone(snapshot.plate_progress)
        self.assertEqual(snapshot.plate_progress["anchor"], 4)
        self.assertEqual(snapshot.plate_layer_count, len(parts.index.view.ranges),
                         "monitor-only plate rendered with a zero layer-slider range")

    def test_an_unresolved_physical_layer_builds_no_plate_payload(self):
        # The print's own layer never resolved while the index exists:
        # there is no anchor, so the plate APIs are never asked — and
        # the position's presence on the status is not one.
        parts = self._make()
        view = _view()
        view.layer_at = lambda position: None  # the file position maps to no layer
        parts.index.view = view
        status = _status()
        status["print_stats"]["info"] = {}
        parts.client.statusReceived.emit(status)
        snapshot = parts.coordinator.snapshot
        self.assertIsNone(snapshot.layer.index)
        self.assertIsNone(snapshot.plate_progress)
        self.assertEqual(parts.index.plate_anchors, [])
        self.assertEqual(parts.index.plate_positions, [])

    def test_a_missing_or_invalid_file_position_resolves_to_none(self):
        # Missing, null and non-numeric fields all resolve to None —
        # never to byte 0, which would read as real progress at the
        # head of the layer — and neither consumer acts on it.
        parts = self._printing(self._make())
        parts.index.view = _view()
        for sdcard in ({}, {"file_position": None}, {"file_position": "many"},
                       {"file_position": [1]}):
            with self.subTest(sdcard=sdcard):
                parts.client.statusReceived.emit(
                    _status("printing", virtual_sdcard=sdcard))
                self.assertIsNone(parts.coordinator.snapshot.layer_progress)
                self.assertEqual(parts.index.plate_anchors[-1], 4)
                self.assertIsNone(parts.index.plate_positions[-1])

    def test_the_plate_split_reads_the_toolheads_own_position(self):
        # The painted boundary follows the NOZZLE, so the head's own
        # position must reach the service beside the dispatcher's — read
        # from the same status the Preview's follower reads it from, in
        # the G-code's own coordinate space.
        parts = self._printing(self._make())
        parts.index.view = _view()
        parts.coordinator.refresh()
        self.assertEqual(parts.index.plate_lives[-1], (10.0, 10.0, 1.2))

    def test_the_live_position_survives_a_pause(self):
        # A pause is where the refinement matters most: the dispatcher
        # sits where it stopped, the pause macro parks the head away
        # from the path. The status keeps flowing, so the position keeps
        # flowing with it and the service can hold its boundary.
        parts = self._make()
        parts.index.view = _view()
        status = _status("paused")
        status["motion_report"]["live_position"] = [140.0, 140.0, 10.0, 0.0]
        parts.client.statusReceived.emit(status)
        parts.coordinator.refresh()
        self.assertEqual(parts.index.plate_lives[-1], (140.0, 140.0, 10.0))
        self.assertEqual(parts.index.plate_positions[-1], 4500)

    def test_a_missing_motion_report_hands_no_live_position(self):
        # No telemetry is None, never a fabricated origin: the service
        # then keeps the coarse boundary, exactly as it always did.
        parts = self._printing(self._make(), motion_report={})
        parts.index.view = _view()
        parts.coordinator.refresh()
        self.assertIsNone(parts.index.plate_lives[-1])
        self.assertEqual(parts.index.plate_positions[-1], 4500)

    def test_no_plate_path_raises_across_the_position_variants(self):
        # Every combination the unbound-position defect could reach,
        # replayed on one coordinator: each poll completes and
        # publishes. The view-absent variant raised UnboundLocalError
        # before the position was resolved ahead of both consumers.
        parts = self._make()

        def layerless_status():
            status = _status()
            status["print_stats"]["info"] = {}
            return status

        def unmapped_view():
            view = _view()
            view.layer_at = lambda position: None
            return view

        variants = (
            ("view and position", _view(), _status()),
            ("monitor-only index", _view(job_key=()), _status()),
            ("no physical layer", unmapped_view(), layerless_status()),
            ("missing position", _view(), _status("printing", virtual_sdcard={})),
            ("null position", _view(),
             _status("printing", virtual_sdcard={"file_position": None})),
            ("non-numeric position", _view(),
             _status("printing", virtual_sdcard={"file_position": "many"})),
            ("no index at all", None, _status()),
        )
        for label, view, status in variants:
            with self.subTest(label):
                parts.index.view = view
                parts.client.statusReceived.emit(status)
                parts.coordinator.refresh()
                self.assertIsNotNone(parts.coordinator.snapshot)
                self.assertTrue(parts.presentation.published)

    def test_a_connected_client_publishes_the_followed_layer_and_hydration(self):
        parts = self._printing(self._make())
        parts.client.connected = True
        parts.preview.observe_result = ("Following", (3, 5))
        parts.coordinator.refresh()
        self.assertEqual(parts.index.followed, [4])
        self.assertEqual(parts.index.hydration, [3, 5])
        self.assertEqual(parts.coordinator._detail, "Following")

    def test_the_pause_rows_are_built_once_and_reused_by_a_bare_publish(self):
        parts = self._printing(self._make())
        parts.pauses.layers = {7}
        parts.pauses.states = {7: "scheduled"}
        parts.coordinator.refresh()
        built = len(parts.preview.remaining_calls)
        self.assertGreater(built, 0)
        parts.coordinator._publish()
        self.assertEqual(len(parts.preview.remaining_calls), built)
        # The states join the cache key: a fired pause rebuilds the rows.
        parts.pauses.states = {7: "fired"}
        parts.coordinator._publish()
        self.assertGreater(len(parts.preview.remaining_calls), built)

    def test_the_published_load_phase_tracks_each_terminal_phase(self):
        parts = self._make()
        coordinator = parts.coordinator

        def published():
            coordinator.refresh()
            return parts.presentation.published[-1]

        parts.files.phase = "downloading"
        parts.files.download_fraction = 0.4
        self.assertEqual(published()["loadPhase"], "Downloading…")
        self.assertEqual(published()["loadProgress"], 0.4)
        parts.files.phase = "resolving"
        self.assertEqual(published()["loadPhase"], "Resolving…")
        parts.files.phase = ""
        parts.files.download_fraction = None
        parts.index.phase = "indexing"
        parts.index.progress = 0.25
        result = published()
        self.assertEqual(result["loadPhase"], "Indexing…")
        self.assertEqual(result["loadProgress"], 0.25)
        parts.index.phase = ""
        parts.cura.loading = True
        self.assertEqual(published()["loadPhase"], "Rendering…")
        parts.cura.loading = False
        coordinator._loads.request_load()
        self.assertEqual(published()["loadPhase"], "Resolving current print…")
        coordinator._loads.reset()
        self.assertEqual(published()["loadProgress"], -1.0)

    def test_a_baked_pause_blocks_the_manual_toggle_for_that_layer(self):
        parts = self._make()
        parts.index.view = _view(pause_layers=(5,))
        parts.cura.selected_layer = 5
        parts.coordinator._publish()
        block = parts.presentation.published[-1]
        self.assertFalse(block["pauseAtLayerCanToggle"])
        self.assertEqual(block["pauseAtLayerUnavailableText"],
                         "a pause is baked into the gcode at this layer")
        self.assertTrue(block["pauseAtLayerHasBaked"])
        # The backstop: the request is refused however it arrived.
        parts.coordinator.toggle_pause(6)
        self.assertEqual(parts.pauses.toggles, [])

    def test_the_pause_total_falls_back_to_cura_max_layer(self):
        parts = self._make()
        parts.cura.max_layer = 99
        parts.coordinator.toggle_pause(50)
        self.assertEqual(parts.pauses.toggles, [(49, None, 100)])
        parts.coordinator._publish()
        self.assertEqual(parts.presentation.published[-1]["pauseAtLayerCandidate"], 0)

    def test_a_scene_invalidation_does_not_abort_the_load(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.request_load()
        before = parts.preview.invalidations
        parts.cura.invalidated.emit("stage swapped")
        self.assertEqual(parts.preview.invalidations, before + 1)
        self.assertTrue(coordinator._loads.load_requested)

    def test_a_view_swap_restores_attachment_only_with_a_toolpath(self):
        parts = self._make()
        parts.preview.state.attached = True
        parts.cura.has_toolpath = True
        parts.cura.viewSwapped.emit()
        self.assertEqual(parts.preview.attach_calls, [True])
        # Without a toolpath to drive, a swap leaves the follower detached.
        parts.preview.attach_calls.clear()
        parts.cura.has_toolpath = False
        parts.cura.viewSwapped.emit()
        self.assertEqual(parts.preview.attach_calls, [])
        # A swap while detached is navigation, never a re-attach.
        parts.preview.state.attached = False
        parts.cura.has_toolpath = True
        parts.cura.viewSwapped.emit()
        self.assertEqual(parts.preview.attach_calls, [])

    def test_an_installed_index_resets_tracking_while_it_builds(self):
        parts = self._make()
        parts.index.phase = "indexing"
        parts.index.changed.emit()
        self.assertEqual(parts.preview.reset_trackings, 1)
        parts.index.phase = "ready"
        parts.index.changed.emit()
        self.assertEqual(parts.preview.reset_trackings, 1)

    def test_position_changes_are_throttled_and_reanchor_the_eta(self):
        parts = self._make()
        coordinator = parts.coordinator
        parts.preview.override = True
        parts.cura.positionChanged.emit()
        self.assertEqual(coordinator._detail, "Detached")
        published = len(parts.presentation.published)
        coordinator._publish_at = time.monotonic() + 10.0
        parts.cura.positionChanged.emit()
        self.assertEqual(len(parts.presentation.published), published)
        # Past the throttle with a matching view: the ETA is re-anchored.
        coordinator._publish_at = 0.0
        parts.index.view = _view()
        parts.files.job_key = ("cube.gcode", 100000, 1)
        parts.preview.remaining_end_value = 600.0
        parts.cura.positionChanged.emit()
        self.assertEqual(coordinator.snapshot.layer_eta, 600.0)
        self.assertEqual(parts.preview.eta_updates[-1][1], parts.index.view)

    def test_a_position_change_ignores_a_view_for_another_file(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator._publish_at = 0.0
        parts.index.view = _view(job_key=("other.gcode", 1, 1))
        parts.files.job_key = ("cube.gcode", 100000, 1)
        parts.preview.remaining_end_value = 600.0
        parts.cura.positionChanged.emit()
        self.assertIsNone(coordinator.snapshot.layer_eta)
        self.assertEqual(parts.presentation.published[-1]["selectedLayerEtaText"], "")

    def test_position_changes_name_no_override_with_the_gate_off(self):
        parts = self._make(config=PrinterConfig(enabled=False))
        parts.preview.override = True
        parts.cura.positionChanged.emit()
        self.assertNotEqual(parts.coordinator._detail, "Detached")

    def test_a_loaded_file_invalidates_and_a_failed_load_explains_itself(self):
        parts = self._make()
        coordinator = parts.coordinator
        before = parts.preview.invalidations
        parts.cura.fileLoaded.emit("/downloads/cube.gcode")
        self.assertEqual(parts.preview.invalidations, before + 1)
        self.assertEqual(parts.client.forced, 1)
        coordinator._loads._load_job = ("cube.gcode", 1, 1)
        parts.cura.loadFailed.emit("no disk space")
        self.assertIsNone(coordinator._loads._load_job)
        self.assertEqual(coordinator._detail, "Could not load current print: no disk space")

    def test_confirm_load_delegates_to_cura_replace_prompt(self):
        parts = self._make()
        parts.coordinator.confirm_load()
        self.assertIsNotNone(parts.cura.confirm_replace_callback)
        parts.cura.confirm_replace_callback()
        self.assertTrue(parts.coordinator._loads.load_requested)

    def test_the_preview_block_keeps_the_newest_stamp(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator.receive_preview_block("not a mapping")
        self.assertIsNone(coordinator._preview_block)
        coordinator.receive_preview_block({"stamp": 10.0, "state": "old"})
        coordinator.receive_preview_block({"stamp": 4.0, "state": "stale"})
        self.assertEqual(coordinator._preview_block[0]["state"], "old")
        coordinator.receive_preview_block({"stamp": 12.0, "state": "new"})
        self.assertEqual(coordinator._preview_block[0]["state"], "new")
        coordinator._publish()
        block = parts.presentation.published[-1]
        self.assertEqual(block["previewBlock"]["state"], "new")
        self.assertTrue(block["previewBlockStale"])  # the feed is down
        parts.client.connected = True
        coordinator._preview_block = ({"state": "new"}, time.monotonic())
        coordinator._publish()
        self.assertFalse(parts.presentation.published[-1]["previewBlockStale"])
        # An aged block is stale even on a live connection.
        coordinator._preview_block = ({"state": "new"},
                                      time.monotonic() - coordinator.PREVIEW_BLOCK_STALE_S - 1)
        coordinator._publish()
        self.assertTrue(parts.presentation.published[-1]["previewBlockStale"])

    def test_a_bare_publish_still_reports_the_stage_and_the_readouts(self):
        parts = self._make()
        parts.cura.preview_active = True
        parts.cura.has_toolpath = True
        parts.cura.scene_has_objects = True
        parts.binding.identity = ("http://printer", "Voron")
        parts.index.view = _view(pause_layers=(9,))
        parts.coordinator._publish()
        block = parts.presentation.published[-1]
        self.assertTrue(block["previewStageActive"])
        self.assertEqual(block["activePrinterName"], "Voron")
        self.assertTrue(block["hasToolpath"])
        self.assertTrue(block["sceneHasObjects"])
        self.assertEqual(block["pauseAtLayerSummary"], "End-of-layer PAUSE: 10")
        self.assertTrue(block["pauseAtLayerHasBaked"])
        self.assertFalse(block["pauseAtLayerHasClearable"])
        self.assertFalse(block["layerReadoutAvailable"])
        self.assertEqual(block["layerReadoutText"], "—")
        self.assertFalse(block["heightReadoutAvailable"])
        self.assertEqual(block["heightReadoutText"], "—")

    def test_the_gate_diagnostics_log_only_on_change(self):
        parts = self._make()
        from plugins import PrintCoordinator as coordinator_module
        with patch.object(coordinator_module, "Logger") as logger:
            parts.coordinator._publish()
            self.assertEqual(len(logger.log.call_args_list), 1)
            parts.coordinator._publish()
        self.assertEqual(len(logger.log.call_args_list), 1)
        with patch.object(coordinator_module, "Logger") as logger:
            parts.cura.has_toolpath = True
            parts.coordinator._publish()
        self.assertTrue(any("preview card gates" in str(call)
                            for call in logger.log.call_args_list))

    def test_close_silences_every_later_publication(self):
        parts = self._make()
        coordinator = parts.coordinator
        coordinator.close()
        published = len(parts.presentation.published)
        coordinator.refresh()
        coordinator._publish()
        parts.pauses.changed.emit()
        self.assertEqual(len(parts.presentation.published), published)
        self.assertFalse(coordinator._processing)

    def test_reset_binding_clears_the_whole_session(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator._loads._load_job = ("x", 1, 1)
        coordinator._loads.request_load()
        coordinator._loads.request_monitor()
        coordinator._header_total_mm = 100.0
        coordinator._next_pause._anchor_elapsed = 30.0
        coordinator._next_pause._anchor_job = coordinator.snapshot.job_key
        coordinator._next_pause._last_index = 4
        coordinator._user_detached = True
        coordinator._mr_meta = {"layer_height": 0.2}
        coordinator._mr_meta_key = ("cube.gcode", coordinator.snapshot.job_key)
        coordinator._mr_meta_checks = 2
        coordinator._next_pause._prev_state = "printing"
        parts.cura.has_toolpath = True
        coordinator.reset_binding()
        self.assertIsNone(coordinator._loads._load_job)
        self.assertFalse(coordinator._loads.load_requested)
        self.assertFalse(coordinator._loads.monitor_requested)
        self.assertIsNone(coordinator._header_total_mm)
        self.assertIsNone(coordinator._next_pause._anchor_elapsed)
        self.assertIsNone(coordinator._next_pause._anchor_job)
        self.assertIsNone(coordinator._next_pause._prev_state)
        self.assertIsNone(coordinator._next_pause._last_index)
        self.assertFalse(coordinator._user_detached)
        self.assertEqual(coordinator._mr_meta, {})
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertEqual(coordinator._detail, "Not connected")
        self.assertFalse(coordinator.snapshot.active)
        self.assertIsNone(parts.pauses.bound)
        self.assertIsNone(parts.index.bound)
        self.assertIsNone(parts.files.bound)
        self.assertEqual(parts.bed_mesh.clears, 1)
        self.assertEqual(parts.cura.invalidated_reasons, ["active printer binding changed"])
        # A fresh binding with a toolpath already in Cura keeps following.
        self.assertEqual(parts.preview.attach_calls[-1], True)

    def test_a_dropped_metadata_send_leaves_no_identity_behind(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        files.metadata_only_started = False
        coordinator._maybe_fetch_mr_metadata("cube.gcode", ("cube.gcode", 100000, 1))
        self.assertEqual(coordinator._mr_meta_asked, ("", ""))
        self.assertEqual(coordinator._mr_meta_at, 0.0)
        self.assertFalse(coordinator._mr_meta_pending)

    def test_the_metadata_fetch_is_single_flight_and_throttled(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)
        self.assertTrue(coordinator._mr_meta_pending)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)  # one request at a time
        coordinator._mr_meta_pending = False
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)  # inside the 30 s window
        coordinator._mr_meta_at = time.monotonic() - 31.0
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 2)
        # A new key re-arms the give-up counter and skips the window.
        coordinator._mr_meta_pending = False
        coordinator._mr_meta_checks = 2
        coordinator._maybe_fetch_mr_metadata("other.gcode", job)
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertEqual(len(files.metadata_only), 3)

    def test_a_latched_payload_retires_the_whole_ladder(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        files.metadata_only[-1]({"result": {"job_id": None, "layer_height": 0.2,
                                            "filament_total": 100.0}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("cube.gcode", job))
        self.assertEqual(coordinator._mr_metadata_for("cube.gcode", job)["layer_height"], 0.2)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        self.assertEqual(len(files.metadata_only), 1)

    def test_a_named_job_id_rides_the_history_cross_check(self):
        parts = self._make()
        coordinator = parts.coordinator
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        parts.files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
        self.assertEqual(len(parts.client.sent_json), 1)
        self.assertEqual(parts.client.sent_json[0][1], "mr-history")
        self.assertEqual(parts.client.sent_json[0][3],
                         "server/history/list?limit=1&order=desc")
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        parts.client.reply({"result": {"jobs": [{"job_id": 77}]}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("cube.gcode", job))
        self.assertEqual(coordinator._mr_meta_checks, 0)

    def test_a_mismatched_job_id_is_never_latched(self):
        parts = self._make()
        coordinator = parts.coordinator
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        parts.files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
        from plugins import PrintCoordinator as coordinator_module
        with patch.object(coordinator_module, "Logger") as logger:
            parts.client.reply({"result": {"jobs": [{"job_id": 99}]}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertTrue(any("refused" in str(call) for call in logger.log.call_args_list))

    def test_an_unattestable_check_gives_up_after_the_limit(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        key = ("cube.gcode", job)
        limit = coordinator.MR_META_CHECK_LIMIT
        for step in range(limit):
            coordinator._mr_meta_at = 0.0
            coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
            files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
            parts.client.reply({"result": {"jobs": []}}, None)
            if step < limit - 1:
                self.assertEqual(coordinator._mr_meta_key, ("", ""))
                self.assertEqual(coordinator._mr_meta_checks, step + 1)
        self.assertEqual(coordinator._mr_meta_key, key)
        self.assertEqual(coordinator._mr_meta_checks, 0)
        self.assertEqual(coordinator._mr_metadata_for(*key)["layer_height"], 0.2)

    def test_a_failed_history_request_also_gives_up_after_the_limit(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        for _ in range(coordinator.MR_META_CHECK_LIMIT):
            coordinator._mr_meta_at = 0.0
            coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
            files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
            parts.client.reply(None, "connection lost")
        self.assertEqual(coordinator._mr_meta_key, ("cube.gcode", job))
        self.assertFalse(coordinator._mr_meta_pending)

    def test_a_failed_metadata_fetch_never_latches(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        files.metadata_only[-1]({"error": "boom"}, "boom")
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertFalse(coordinator._mr_meta_pending)
        # A payload that is not a mapping is refused the same way.
        coordinator._mr_meta_at = 0.0
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        files.metadata_only[-1]({"result": ["not", "a", "mapping"]}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_asked, ("cube.gcode", job))

    def test_a_superseded_history_reply_never_latches(self):
        parts = self._make()
        coordinator = parts.coordinator
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        parts.files.metadata_only[-1]({"result": {"job_id": 77, "layer_height": 0.2}}, None)
        stale = parts.client.reply
        coordinator.reset_binding()
        stale({"result": {"jobs": [{"job_id": 77}]}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)

    def test_the_next_pause_target_and_fraction_follow_the_deadlines(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        parts.index.view = _view()
        parts.pauses.layers = {9}
        parts.preview.remaining_value = 300.0
        coordinator.refresh()
        snapshot = coordinator.snapshot
        self.assertEqual(snapshot.next_pause_layer, 10)
        self.assertAlmostEqual(snapshot.next_pause_fraction, 120.0 / 420.0, places=4)
        # The compute can build its own rows when the caller has none.
        self.assertEqual(coordinator._next_pause.compute(snapshot.layer, 120.0)[0], 10)
        # An exhausted remaining with time left short-circuits to a full bar.
        parts.preview.remaining_value = -500.0
        coordinator.refresh()
        self.assertEqual(coordinator.snapshot.next_pause_fraction, 1.0)
        # A row BEHIND the current layer is never the target.
        parts.pauses.layers = {2}
        parts.preview.remaining_value = 300.0
        coordinator.refresh()
        self.assertIsNone(coordinator.snapshot.next_pause_layer)
        self.assertEqual(coordinator.snapshot.next_pause_eta, "")

    def test_a_superseded_metadata_reply_is_dropped(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        pending = files.metadata_only[-1]
        coordinator.reset_binding()
        pending({"result": {"job_id": None}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))
        self.assertEqual(coordinator._mr_meta_checks, 0)

    def test_a_closed_coordinator_drops_a_late_metadata_reply(self):
        parts = self._make()
        coordinator, files = parts.coordinator, parts.files
        job = ("cube.gcode", 100000, 1)
        coordinator._maybe_fetch_mr_metadata("cube.gcode", job)
        pending = files.metadata_only[-1]
        coordinator.close()
        pending({"result": {"job_id": None}}, None)
        self.assertEqual(coordinator._mr_meta_key, ("", ""))

    def test_the_bed_mesh_observation_reaches_the_presenter(self):
        parts = self._printing(self._make(), bed_mesh={"mesh_min": [0, 0], "mesh_max": [10, 10],
                                                       "probed_matrix": [[0.1]]})
        self.assertEqual(len(parts.bed_mesh.updates), 1)
        self.assertEqual(parts.cura.watched, [True])

    def test_the_metadata_and_index_pull_start_only_with_a_toolpath(self):
        parts = self._printing(self._make())
        parts.coordinator.refresh()
        self.assertEqual((parts.files.metadata_requests, parts.index.requests), (0, 0))
        parts.cura.has_toolpath = True
        parts.coordinator.refresh()
        self.assertGreaterEqual(parts.files.metadata_requests, 1)
        self.assertGreaterEqual(parts.index.requests, 1)

    def test_a_disabled_follower_never_requests_the_index(self):
        parts = self._printing(self._make(config=PrinterConfig(path_follow=False)))
        parts.cura.has_toolpath = True
        parts.coordinator.refresh()
        self.assertEqual(parts.index.requests, 0)

    def test_a_load_request_with_an_active_print_waits_for_the_file(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        coordinator.request_load()
        self.assertTrue(coordinator._loads.load_requested)
        coordinator.observe(_status("printing"))
        self.assertFalse(coordinator._loads.load_requested)
        self.assertEqual(coordinator._loads._load_job, coordinator.snapshot.job_key)
        self.assertEqual(parts.files.file_requests, [True])

    def test_a_load_request_with_no_active_print_explains_itself(self):
        coordinator = self._make().coordinator
        coordinator.request_load()
        coordinator.observe(_status("standby"))
        self.assertFalse(coordinator._loads.load_requested)
        self.assertEqual(coordinator._detail, "No active Moonraker print to load")

    def test_a_non_numeric_metadata_estimate_is_ignored(self):
        parts = self._printing(self._make())
        parts.files.metadata = {"estimated_time": "not-a-number"}
        parts.coordinator.refresh()
        self.assertIsNone(parts.coordinator.snapshot.estimated_time)
        parts.files.metadata = {"estimated_time": 3600.0}
        parts.coordinator.refresh()
        self.assertEqual(parts.coordinator.snapshot.estimated_time, 3600.0)

    def test_a_non_numeric_print_duration_reads_as_zero(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        parts.client.statusReceived.emit(_status("paused"))
        self.assertEqual(coordinator._next_pause._anchor_elapsed, 120.0)
        before = coordinator.snapshot.job_key
        parts.client.statusReceived.emit(_status("printing", print_stats={
            "state": "printing", "filename": "cube.gcode", "print_duration": "ages",
            "info": {"current_layer": 5, "total_layer": 100}}))
        # The bad duration was swallowed as 0.0, and a rewind to zero is
        # a restart: the run identity advances and the anchor clears.
        self.assertNotEqual(coordinator.snapshot.job_key, before)
        self.assertIsNone(coordinator._next_pause._anchor_elapsed)

    def test_the_pause_anchor_holds_the_last_pause_and_clears_on_a_new_job(self):
        coordinator = self._make().coordinator
        coordinator._next_pause.update_anchor("cube.gcode", "printing", 100.0)
        coordinator._next_pause.update_anchor("cube.gcode", "paused", 140.0)
        self.assertEqual(coordinator._next_pause._anchor_elapsed, 140.0)
        # Still paused: a later poll never re-stamps the anchor.
        coordinator._next_pause.update_anchor("cube.gcode", "paused", 200.0)
        self.assertEqual(coordinator._next_pause._anchor_elapsed, 140.0)
        # A new job clears the anchor AND the last known index.
        coordinator._next_pause._last_index = 12
        coordinator._next_pause.update_anchor("other.gcode", "printing", 5.0)
        self.assertIsNone(coordinator._next_pause._anchor_elapsed)
        self.assertIsNone(coordinator._next_pause._last_index)

    def test_a_resolver_that_drops_to_none_keeps_the_last_index(self):
        parts = self._printing(self._make())
        coordinator = parts.coordinator
        # The frame that OPENS a run clears the remembered index with the
        # anchor; a later frame of the same run is what records it.
        self.assertIsNone(coordinator._next_pause._last_index)
        self._printing(parts)
        self.assertEqual(coordinator._next_pause._last_index, 4)
        parts.client.statusReceived.emit(_status("paused", print_stats={
            "state": "paused", "filename": "cube.gcode", "print_duration": 130.0,
            "info": {"current_layer": 0, "total_layer": 100}}))
        self.assertIsNone(coordinator.snapshot.layer.index)
        self.assertEqual(coordinator._next_pause._last_index, 4)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class ToolheadCoverageTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self._rt.__enter__()
        self.addCleanup(self._rt.__exit__, None, None, None)
        from plugins.ToolheadController import ToolheadController
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

    def test_distance_presets_reject_everything_out_of_range(self):
        controller, _, _ = self._make()
        for value in ("wide", 0, -5, 0.001, 500):
            controller.set_distance(value)
            self.assertEqual(controller.values["jogDistance"], 25.0)
        controller.set_distance("1.5")
        self.assertEqual(controller.values["jogDistance"], 1.5)
        for value in ("wide", 0, 0.01, 500):
            controller.set_extrude_distance(value)
            self.assertEqual(controller.values["extrudeDistance"], 5.0)
        controller.set_extrude_distance(10)
        self.assertEqual(controller.values["extrudeDistance"], 10.0)
        for value in ("fast", 0, 5000):
            controller.set_extrude_speed(value)
            self.assertEqual(controller.values["extrudeSpeed"], 300.0)
        controller.set_extrude_speed(120)
        self.assertEqual(controller.values["extrudeSpeed"], 120.0)

    def test_a_jog_is_dropped_when_under_a_minimum_move_of_travel_is_left(self):
        # The clamp can land under the smallest legal move; that is not a
        # jog, so nothing is sent.
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0),
                                             maximum=(0.005, 200, 200))
        controller.jog("x", 1)
        self.assertEqual(commands.sent, [])

    def test_a_configured_negative_z_floor_still_refuses_the_nudge(self):
        # Printers configure a negative Z minimum for probe travel; the
        # pad must never send the head below 0.00.
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0),
                                             minimum=(0, 0, -5))
        notes = []
        controller.rejectedNote.connect(notes.append)
        controller.jog("z", -1)
        self.assertEqual(commands.sent, [])
        self.assertEqual(len(notes), 1)
        controller.jog("z", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 Z25 F600\nG90"])

    def test_a_refused_send_leaves_the_move_queued(self):
        controller, _, commands = self._make(state="paused")
        commands.refuse = True
        controller.jog("x", 1)
        self.assertEqual(commands.sent, [])
        self.assertEqual(len(controller._pending), 1)
        commands.refuse = False
        commands.changed.emit()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_nested_pump_is_dropped(self):
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        commands.busy = False
        # A dispatch already running must not re-enter and double-send.
        controller._pumping = True
        controller._pump()
        self.assertEqual(commands.sent, [])
        controller._pumping = False
        controller._pump()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_jog_with_a_bad_axis_or_direction_is_dropped(self):
        controller, _, commands = self._make()
        controller.jog("w", 1)
        controller.jog("x", 2)
        controller.jog("x", "sideways")
        controller.jog("x", None)
        self.assertEqual(commands.sent, [])

    def test_a_z_nudge_into_the_floor_is_reported_once_per_burst(self):
        controller, data, commands = self._make(live=(10.0, 10.0, 0.0, 0.0))
        notes = []
        controller.rejectedNote.connect(notes.append)
        controller.jog("z", -1)
        controller.jog("z", -1)
        self.assertEqual(notes, ["Z nudge rejected — the head would go below 0.00 Z."])
        self.assertEqual(controller.values["jogStatus"],
                         "Z nudge rejected — the head would go below 0.00 Z")
        self.assertEqual(commands.sent, [])
        # A successful Z move re-arms the note for the next burst — once
        # the poll reports the moved level, so the head is measured where
        # it really is.
        controller.set_distance(5)
        controller.jog("z", 1)
        commands.complete()
        data.set_state("paused", live=(10.0, 10.0, 5.0, 0.0))
        self.assertAlmostEqual(controller._axis_estimate["z"], 5.0)
        controller.jog("z", -1)  # lands exactly on the floor: allowed
        controller.jog("z", -1)  # below it: the note fires again
        self.assertEqual(len(notes), 2)

    def test_a_configured_axis_floor_forbids_the_negative_side(self):
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0))
        controller.jog("x", -1)
        self.assertEqual(commands.sent, [])
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_jog_without_position_data_refuses_the_negative_side(self):
        controller, data, commands = self._make()
        data.snapshot = SimpleNamespace(core={}, auxiliary={})
        data.changed.emit()
        controller.jog("y", -1)
        controller.jog("y", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 Y25 F3000\nG90"])

    def test_the_queue_re_clamps_a_merged_tail(self):
        # The documented overshoot: a tail whose clamp at queue time no
        # longer holds against the re-read position is rewritten, and a
        # refused tail is dropped with it.
        from plugins.ToolheadPolicy import make_jog_op, make_motors_off_op
        controller, _, _ = self._make(live=(10.0, 10.0, 10.0, 0.0))
        clamped = controller._clamp_tail((make_jog_op("x", 250.0, True),))
        self.assertEqual([op.distance for op in clamped], [190.0])
        pending = (make_jog_op("x", 190.0, True), make_jog_op("x", -50.0, True))
        self.assertEqual(controller._clamp_tail(pending), pending[:1])
        # An in-range tail and a non-jog tail are both left alone.
        pending = (make_jog_op("x", 100.0, True),)
        self.assertEqual(controller._clamp_tail(pending), pending)
        home_tail = (make_motors_off_op(),)
        self.assertEqual(controller._clamp_tail(home_tail), home_tail)
        self.assertEqual(controller._clamp_tail(()), ())

    def test_the_z_projection_advances_with_every_accepted_move(self):
        controller, data, commands = self._make(live=(0.0, 0.0, 10.0, 0.0))
        commands.busy = True
        controller.set_distance(5)
        controller.jog("z", 1)
        self.assertAlmostEqual(controller._axis_estimate["z"], 15.0)
        controller.jog("z", 1)
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        # While a Z move is queued the poll never re-syncs the estimate.
        data.set_state("paused", live=(0.0, 0.0, 10.0, 0.0))
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        commands.complete()
        commands.complete()
        # Both moves have left the queue, but the poll still reads the
        # pre-command level: the head has not been reported at 20 yet, so
        # the projection is unreconciled, not stale — dropping it here
        # would re-arm the estimate for the next tap (the dispatch seam).
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        self.assertAlmostEqual(controller._axis_up_pending["z"], 10.0)
        # The poll reporting the commanded level is the head arriving:
        # adopt it and lift the owed reflection.
        data.set_state("paused", live=(0.0, 0.0, 20.0, 0.0))
        self.assertAlmostEqual(controller._axis_estimate["z"], 20.0)
        self.assertAlmostEqual(controller._axis_up_pending["z"], 0.0)

    def test_a_stale_poll_after_dispatch_keeps_the_upward_projection(self):
        # Repeated +5 jogs from X=195 with the maximum at 200. Each send
        # frees the lane while the poll still reads 195 — the level the
        # move started from. Adopting it re-armed the projection, so one
        # command left the plugin per tap and the burst walked the head
        # past the maximum.
        controller, data, commands = self._make(live=(195.0, 10.0, 10.0, 0.0),
                                                maximum=(200, 200, 200))
        controller.set_distance(5)
        for _ in range(3):
            controller.jog("x", 1)
            commands.complete()  # the send frees the lane
            data.set_state("paused", live=(195.0, 10.0, 10.0, 0.0),
                           maximum=(200, 200, 200))  # the delayed poll
        self.assertEqual(self._scripts(commands), ["G91\nG1 X5 F3000\nG90"])
        self.assertAlmostEqual(controller._axis_estimate["x"], 200.0)

    def test_the_upward_projection_holds_through_the_queue_drain(self):
        # Two legal +5 taps from X=190 queue behind a held lane and cover
        # the 200 maximum between them; the third is refused outright.
        # The polls that land while the queue drains must not re-open the
        # boundary, and a tap in the other direction still reads the
        # projection rather than the stale poll.
        controller, data, commands = self._make(live=(190.0, 10.0, 10.0, 0.0),
                                                maximum=(200, 200, 200))
        controller.set_distance(5)
        commands.busy = True  # hold the lane: both taps queue
        controller.jog("x", 1)
        controller.jog("x", 1)
        controller.jog("x", 1)  # no headroom is left: nothing is queued
        self.assertAlmostEqual(controller._axis_estimate["x"], 200.0)
        commands.busy = False
        commands.changed.emit()  # the lane clears: the queue starts draining
        self.assertEqual(len(self._scripts(commands)), 1)
        data.set_state("paused", live=(190.0, 10.0, 10.0, 0.0),
                       maximum=(200, 200, 200))
        commands.complete()  # the last queued move goes out
        self.assertEqual(len(self._scripts(commands)), 2)
        self.assertEqual(self._scripts(commands)[-1], "G91\nG1 X5 F3000\nG90")
        # The queue is drained and every poll so far is pre-move: the
        # boundary tap stays refused.
        data.set_state("paused", live=(190.0, 10.0, 10.0, 0.0),
                       maximum=(200, 200, 200))
        controller.jog("x", 1)
        self.assertEqual(len(self._scripts(commands)), 2)
        # The opposite direction still measures against the projection
        # (200), not the stale poll (190).
        commands.complete()  # the lane clears for the next move
        controller.jog("x", -1)
        self.assertEqual(self._scripts(commands)[-1], "G91\nG1 X-5 F3000\nG90")

    def test_a_poll_below_the_pre_command_level_still_adopts(self):
        # The reconciliation's other edge: an upward move's own reflection
        # lags at the pre-command level, but a poll that drops BELOW it is
        # the head genuinely moving down (an external macro, a home) and
        # must be adopted — the projection may not freeze above the truth.
        controller, data, _ = self._make(live=(10.0, 10.0, 10.0, 0.0))
        controller.set_distance(5)
        controller.jog("x", 1)  # projection 15
        self.assertAlmostEqual(controller._axis_estimate["x"], 15.0)
        data.set_state("paused", live=(10.0, 10.0, 10.0, 0.0))  # mid-flight
        self.assertAlmostEqual(controller._axis_estimate["x"], 15.0)
        data.set_state("paused", live=(4.0, 10.0, 10.0, 0.0))  # below the start
        self.assertAlmostEqual(controller._axis_estimate["x"], 4.0)

    def test_polled_z_prefers_the_live_motion_report(self):
        controller, data, _ = self._make()
        self.assertAlmostEqual(controller._polled_axis("z"), 10.0)
        data.snapshot = SimpleNamespace(
            core={"motion_report": {"live_position": [0, 0, "high"]},
                  "gcode_move": {"gcode_position": [0, 0, 3.5]}}, auxiliary={})
        self.assertAlmostEqual(controller._polled_axis("z"), 3.5)
        data.snapshot = SimpleNamespace(
            core={"motion_report": {"live_position": [0, 0]},
                  "gcode_move": {"gcode_position": [0, 0, "north"]}}, auxiliary={})
        self.assertIsNone(controller._polled_axis("z"))
        data.snapshot = SimpleNamespace(
            core={"motion_report": {"live_position": [0, 0]},
                  "gcode_move": {"gcode_position": []}}, auxiliary={})
        self.assertIsNone(controller._polled_axis("z"))

    def test_a_bad_axis_limit_abandons_the_clamp(self):
        controller, _, commands = self._make(live=(10.0, 10.0, 10.0, 0.0),
                                             minimum=("low", 0, 0))
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_home_centre_and_park_moves(self):
        controller, _, commands = self._make(live=(0.0, 0.0, 10.0, 0.0),
                                             minimum=(0, 0, 0), maximum=(200, 100, 200))
        controller.home()
        commands.complete()
        controller.home("y")
        commands.complete()
        controller.home("nope")
        controller.motors_off()
        commands.complete()
        controller.center_toolhead()
        commands.complete()
        controller.z_to_zero()
        commands.complete()
        scripts = self._scripts(commands)
        self.assertEqual(scripts, ["G28", "G28 Y", "M18", "G1 X100 Y50 Z50 F3000",
                                   "G1 Z0 F600"])

    def test_centre_is_refused_without_usable_axis_limits(self):
        controller, data, commands = self._make()
        data.snapshot = SimpleNamespace(core={}, auxiliary={"toolhead": {"axis_minimum": [0]}})
        controller.center_toolhead()
        data.snapshot = SimpleNamespace(
            core={}, auxiliary={"toolhead": {"axis_minimum": [0, "north"],
                                             "axis_maximum": [200, 200]}})
        controller.center_toolhead()
        self.assertEqual(commands.sent, [])

    def test_extrude_edges(self):
        controller, _, commands = self._make()
        controller.extrude(0)
        controller.extrude("sideways")
        controller.extrude(-1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 E-5 F300\nG90"])

    def test_the_absolute_toggle_reaches_the_printer_and_holds_the_readout(self):
        controller, data, commands = self._make()
        controller.set_absolute(False)
        self.assertEqual(controller.values["positionMode"], "Relative")
        self.assertEqual(commands.sent[-1], ("Relative mode", "printer/gcode/script",
                                             {"script": "G91"}))
        # The poll still reports absolute: the latch holds the user's choice.
        data.set_state("paused")
        self.assertEqual(controller.values["positionMode"], "Relative")
        # Once the printer adopts it, the next poll releases the latch.
        data.snapshot.core["gcode_move"]["absolute_coordinates"] = False
        data.changed.emit()
        self.assertIsNone(controller._mode_latch)
        commands.complete()
        controller.set_absolute(True)
        self.assertEqual(commands.sent[-1][2], {"script": "G90"})
        self.assertEqual(controller.values["positionMode"], "Absolute")

    def test_an_expired_mode_latch_stops_holding(self):
        controller, data, _ = self._make()
        controller.set_absolute(False)
        controller._mode_latch = time.monotonic() - 1.0
        data.set_state("paused")  # the poll reports absolute again
        self.assertEqual(controller.values["positionMode"], "Absolute")
        self.assertIsNone(controller._mode_latch)

    def test_a_printing_jog_waits_for_the_pause_it_requested(self):
        controller, data, commands = self._make(state="printing")
        self.assertFalse(controller.values["jogEnabled"])
        controller.jog("x", 1)
        self.assertEqual(controller.values["jogStatus"], "Waiting for the printer to pause…")
        self.assertEqual([path for _, path, _ in commands.sent], ["printer/print/pause"])
        self.assertTrue(controller._pause_waiting)
        self.assertTrue(controller._pause_in_flight)
        self.assertTrue(controller._deadline.isActive())
        # A second tap must not re-send the Pause.
        data.changed.emit()
        self.assertEqual(len(commands.sent), 1)
        # A confirmed pause stops the deadline and moves once the state lands.
        controller._command_changed({"name": "Pause", "terminal": True, "outcome": "confirmed"})
        self.assertFalse(controller._deadline.isActive())
        self.assertEqual(controller.values["jogStatus"], "Printer paused — moving now.")
        commands.complete()
        data.set_state("paused")
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_a_refused_pause_drops_the_queue_and_says_so(self):
        controller, data, _ = self._make(state="printing")
        controller.jog("x", 1)
        data.commandChanged.emit({"name": "Pause", "terminal": True, "outcome": "failed"})
        self.assertEqual(controller.values["jogStatus"],
                         "The printer did not pause — queued moves were cancelled.")
        self.assertEqual(controller._pending, ())
        self.assertFalse(controller._pause_waiting)
        self.assertFalse(controller._deadline.isActive())

    def test_non_pause_and_non_terminal_command_events_are_ignored(self):
        controller, data, _ = self._make(state="printing")
        controller.jog("x", 1)
        data.commandChanged.emit({"name": "Extrude", "terminal": True, "outcome": "failed"})
        self.assertTrue(controller._pause_waiting)
        data.commandChanged.emit({"name": "Pause", "terminal": False})
        self.assertTrue(controller._pause_waiting)

    def test_a_resume_mid_drain_cancels_the_rest(self):
        controller, data, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        self.assertEqual(len(controller._pending), 1)
        controller._draining = True
        data.observation = replace(data.observation, state="printing")
        controller._pump_dispatch()
        self.assertEqual(controller.values["jogStatus"],
                         "The print resumed — queued moves were cancelled.")
        self.assertEqual(controller._pending, ())
        self.assertEqual(commands.sent, [])

    def test_the_pause_deadline_cancels_the_queue(self):
        controller, _, _ = self._make(state="printing")
        controller.jog("x", 1)
        controller._deadline.timeout.emit()
        self.assertEqual(controller.values["jogStatus"],
                         "The printer did not pause — queued moves were cancelled.")
        self.assertFalse(controller._pause_waiting)
        self.assertEqual(controller._pending, ())
        # A late pause does not resurrect anything.
        controller._pause_failed()
        self.assertEqual(controller._pending, ())

    def test_a_disabled_gate_drops_the_queue(self):
        controller, data, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        data.observation = replace(data.observation, connection="no")
        controller.observe()
        controller._pump()
        self.assertFalse(controller.values["jogEnabled"])
        self.assertEqual(controller.values["jogStatus"], "Printer is not ready for toolhead moves.")
        self.assertEqual(controller._pending, ())
        self.assertEqual(commands.sent, [])

    def test_an_unobserved_print_state_fails_closed(self):
        controller, data, commands = self._make(state="paused")
        data.observation = None
        controller.observe()
        controller.jog("x", 1)
        self.assertFalse(controller.values["jogEnabled"])
        self.assertEqual(commands.sent, [])

    def test_a_busy_lane_holds_the_queue_until_it_clears(self):
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        controller.jog("x", 1)
        self.assertEqual(commands.sent, [])
        self.assertEqual(len(controller._pending), 1)
        commands.complete()
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])
        self.assertEqual(controller._pending, ())

    def test_the_queue_full_cap_rejects_the_newest_tap(self):
        controller, _, commands = self._make(state="paused")
        commands.busy = True
        # A distance the axis projection never clamps (16 x 5 mm of
        # headroom from x=10): the cap, not the limit, is what the
        # newest tap hits.
        controller.set_distance(5)
        for _ in range(16):
            controller.jog("x", 1)
        controller.jog("x", 1)
        self.assertEqual(len(controller._pending), 16)
        self.assertEqual(controller.values["jogStatus"],
                         "Too many queued moves — wait for the printer to catch up.")

    def test_the_guard_cooldown_releases_the_fast_poll_floor(self):
        controller, data, commands = self._make(state="paused")
        controller.jog("x", 1)
        self.assertTrue(controller._guard_latched)
        self.assertTrue(data.guard_calls[-1])
        commands.complete()
        self.assertFalse(controller._guard_latched)
        self.assertTrue(controller._guard_cooldown.isActive())
        self.assertTrue(data.guard_calls[-1])  # the cooldown still holds it
        controller._guard_cooldown.timeout.emit()
        self.assertFalse(data.guard_calls[-1])

    def test_a_data_owner_without_a_guard_setter_is_tolerated(self):
        data = _ToolheadData()
        commands = _ToolheadCommands()
        data.set_state("paused")
        controller = self.controller_class(data, commands)
        self.addCleanup(controller.close)
        # The guard is optional: a data owner exposing no setter is a
        # plain no-op, never an AttributeError in the middle of a move.
        data.set_toolhead_guard = None
        controller.jog("x", 1)
        self.assertEqual(self._scripts(commands), ["G91\nG1 X25 F3000\nG90"])

    def test_an_emergency_stop_and_a_session_reset_clear_everything(self):
        controller, data, commands = self._make(state="printing")
        controller.jog("x", 1)
        commands.emergencyStopped.emit()
        self.assertEqual(controller._pending, ())
        self.assertFalse(controller._pause_waiting)
        self.assertEqual(controller.values["jogStatus"], "")
        controller.jog("x", 1)
        data.invalidated.emit()
        self.assertEqual(controller._pending, ())
        self.assertFalse(data.guard_calls[-1])

    def test_the_state_readout_needs_an_active_connected_printer(self):
        controller, data, _ = self._make(state="paused")
        self.assertEqual(controller._state(), "paused")
        data.active = False
        self.assertEqual(controller._state(), "")
        data.active = True
        data.connected = False
        self.assertEqual(controller._state(), "")
        data.connected = True
        data.snapshot.core.pop("print_stats")
        self.assertEqual(controller._state(), "")

    def test_close_stops_both_timers(self):
        controller, _, _ = self._make(state="printing")
        controller.jog("x", 1)
        controller.close()
        self.assertFalse(controller._deadline.isActive())
        self.assertFalse(controller._guard_cooldown.isActive())

    def test_the_values_block_carries_the_whole_control_surface(self):
        controller, _, _ = self._make(state="standby")
        controller.set_distance(5)
        controller.set_extrude_distance(2.5)
        controller.set_extrude_speed(60)
        values = controller.values
        self.assertEqual(values, {"jogEnabled": True, "jogDistance": 5.0,
                                  "extrudeDistance": 2.5, "extrudeSpeed": 60.0,
                                  "homedAxes": "xyz", "positionMode": "Absolute",
                                  "jogStatus": ""})
        # The property hands back a copy: a caller cannot rewrite the model.
        values["jogDistance"] = 999
        self.assertEqual(controller.values["jogDistance"], 5.0)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime not available")
class ConsoleCoverageTests(unittest.TestCase):
    def setUp(self):
        self._rt = runtime()
        self.events = self._rt.__enter__().events
        self.addCleanup(self._rt.__exit__, None, None, None)
        from plugins.ConsoleController import ConsoleController
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

    def test_a_response_burst_coalesces_into_one_shard_write(self):
        # J (the 2026-09-19 review): ten response batches inside one
        # debounce window produce ONE shard write, and the final
        # transcript carries every surviving line.
        persistence = _FakePersistence()
        controller = self._make(identity=("A", "Printer A"), persistence=persistence)
        for i in range(10):
            controller.append_responses([{"text": "line %d" % i, "error": False,
                                          "success": False, "time": 1000.0 + i}])
        self.assertEqual(controller._transcript[-1]["text"], "line 9")
        self.assertEqual(persistence.writes, {}, "nothing persists inside the window")
        self.events(450)  # the debounce fires
        stored = persistence.writes.get("A", {}).get("consoleTranscript") or []
        self.assertEqual([entry["text"] for entry in stored[-10:]], ["line %d" % i for i in range(10)])
        self.assertEqual(list(persistence.writes), ["A"], "one write for the whole burst")

    def test_a_machine_switch_retires_the_dirty_debounce_window(self):
        identity = {"value": ("A", "Printer A")}
        persistence = _FakePersistence()
        controller = self._make(identity=lambda: identity["value"], persistence=persistence)
        controller.append_responses([{"text": "dirty A line", "error": False,
                                      "success": False, "time": 10.0}])
        identity["value"] = ("B", "Printer B")
        controller._session_invalidated()
        self.events(450)  # the debounce would have fired
        self.assertNotIn("A", persistence.writes, "A's dirty lines must never persist after the switch")
        self.assertNotIn("B", persistence.writes, "the retired window must not leak into B")

    def test_an_empty_line_is_nothing_but_an_oversized_one_explains_itself(self):
        controller = self._make()
        self.assertFalse(controller.send(""))
        self.assertFalse(controller.send("   "))
        self.assertEqual(controller._transcript, [])
        self.assertFalse(controller.send("x" * (MAX_LINE + 1)))
        self.assertEqual([entry["text"] for entry in controller._transcript],
                         ["Command too long — ignored."])
        # A multiline paste is folded to one line, not refused.
        self.assertTrue(controller.send("G28\nM18"))
        self.assertEqual(controller.values["consoleHistory"][-1], "G28M18")

    def test_a_full_lane_refuses_the_command(self):
        controller = self._make()
        controller._in_flight = set(range(MAX_PENDING))
        self.assertFalse(controller.send("G28"))
        self.assertEqual(controller._transcript[-1]["text"],
                         "Too many commands waiting — try again in a moment.")

    def test_an_undeliverable_command_keeps_the_draft(self):
        controller = self._make()
        self.data.started = False
        self.assertFalse(controller.send("G28"))
        self.assertEqual(controller._transcript[-1]["text"],
                         "Command queue full or Moonraker unavailable — try again.")
        self.assertEqual(self.data.requests[-1].timeout_ms, 30000)
        self.assertEqual(self.data.requests[-1].channel, "console")
        self.assertEqual(self.data.requests[-1].path, "printer/gcode/script")

    def test_a_send_lands_and_its_verdict_colours_the_line(self):
        controller = self._make()
        self.assertTrue(controller.send("G28"))
        entry = controller._transcript[-1]
        self.assertEqual(entry, {"kind": "command", "text": "G28", "error": False,
                                 "success": False, "restored": False, "saved": False})
        self.assertEqual(controller.values["consolePending"], 1)
        revisions = controller.values["consoleRevisions"]
        self.data.callback({"result": "ok"}, None)
        self.assertTrue(entry["success"])
        self.assertEqual(controller.values["consoleRevisions"], revisions + 1)
        self.assertEqual(controller.values["consolePending"], 0)

    def test_a_server_refusal_marks_the_line_and_a_timeout_does_not(self):
        controller = self._make()
        controller.send("G28")
        entry = controller._transcript[-1]
        self.data.callback({"error": {"message": "Extrude below minimum temp"}},
                           "Extrude below minimum temp")
        self.assertTrue(entry["error"])
        self.assertFalse(entry["success"])
        controller.send("M18")
        second = controller._transcript[-1]
        self.data.callback(None, "timed out")
        self.assertFalse(second["error"])
        self.assertFalse(second["success"])
        self.assertEqual(controller._transcript[-1]["text"],
                         "No response from the printer — the command may still be running.")

    def test_a_late_completion_from_a_dead_session_drains_nothing(self):
        controller = self._make()
        controller.send("G28")
        entry = controller._transcript[-1]
        self.commands.emergencyStopped.emit()
        self.assertEqual(controller._in_flight, set())
        self.data.callback({"result": "ok"}, None)
        self.assertTrue(entry["success"])
        self.assertEqual(controller.values["consolePending"], 0)

    def test_responses_are_appended_and_blank_ones_are_skipped(self):
        controller = self._make()
        controller.append_responses([])
        # Only genuinely empty text is skipped; whitespace is a line.
        controller.append_responses([{"text": ""}, {"text": None}])
        self.assertEqual(controller._transcript, [])
        controller.append_responses([{"text": "ok", "error": False, "success": True, "time": 4.0},
                                     {"text": "x" * (MAX_LINE + 5), "time": 6.0}])
        self.assertEqual([entry["kind"] for entry in controller._transcript],
                         ["response", "response"])
        self.assertEqual(len(controller._transcript[-1]["text"]), MAX_LINE)
        self.assertEqual(controller._store_time, 6.0)
        # The stamp is monotonic: an older response never rewinds it.
        controller.append_responses([{"text": "again", "time": 1.0}])
        self.assertEqual(controller._store_time, 6.0)
        self.assertEqual(controller._transcript[-1]["kind"], "response")

    @staticmethod
    def _response(label):
        return {"kind": "response", "text": label, "error": False, "success": False,
                "restored": False}

    def test_the_ring_rotates_and_pins_the_newest_commands(self):
        controller = self._make()
        command = {"kind": "command", "text": "G28", "error": False, "success": False,
                   "restored": False}
        controller._append_entries([command] + [self._response(f"line {n}")
                                                for n in range(MAX_HISTORY - 1)])
        self.assertEqual(len(controller._transcript), MAX_HISTORY)
        controller._append_entries([self._response("next")])
        # The command is pinned through the rotation; nothing was dropped
        # here because the rotation took exactly the one pinned command.
        self.assertEqual(controller._transcript[0]["text"], "G28")
        self.assertEqual(controller.values["consoleDropped"], 0)
        controller._append_entries([self._response("again")])
        self.assertEqual(controller.values["consoleDropped"], 1)
        self.assertEqual(controller._transcript[0]["text"], "G28")

    def test_a_note_is_never_persisted(self):
        controller = self._make()
        controller.note("a local explanation")
        entry = controller._transcript[-1]
        self.assertEqual(entry["kind"], "note")
        self.assertNotIn(entry, controller._persist_window())
        controller.mark_saved()
        self.assertNotIn("saved", entry)

    def test_the_legacy_history_migrates_when_the_transcript_is_absent(self):
        config = SimpleNamespace(console_transcript=None, console_history=["G28", "M18"],
                                 console_store_time=0.0)
        controller = self._make(config=config)
        self.assertEqual([entry["text"] for entry in controller._transcript], ["G28", "M18"])
        self.assertTrue(all(entry["restored"] for entry in controller._transcript))
        self.assertEqual(controller.values["consoleHistory"], ["G28", "M18"])

    def test_an_empty_stored_list_is_a_genuine_clear(self):
        # The Clear-doesn't-stick report: an empty record must not fall
        # through to the legacy history re-migration.
        controller = self._make(config=PrinterConfig(console_transcript=[],
                                                     console_history=["G28"]))
        self.assertEqual(controller._transcript, [])

    def test_a_stored_transcript_keeps_the_newest_commands(self):
        stored = ([{"kind": "command", "text": "oldest-command", "error": False,
                    "success": False}]
                  + [{"kind": "response", "text": f"line {n}", "error": False, "success": False}
                     for n in range(MAX_TRANSCRIPT + 20)]
                  + [{"kind": "command", "text": "newest-command", "error": False,
                      "success": False}])
        controller = self._make(config=PrinterConfig(console_transcript=stored,
                                                     console_store_time=9.0))
        texts = [entry["text"] for entry in controller._transcript]
        self.assertIn("newest-command", texts)
        self.assertNotIn("oldest-command", texts)
        self.assertEqual(len(controller._transcript),
                         MAX_TRANSCRIPT + controller.MAX_PERSIST_COMMANDS)
        self.assertEqual(controller._store_time, 9.0)
        self.assertTrue(all(entry["restored"] for entry in controller._transcript))

    def test_clear_stops_an_empty_transcript_and_empties_a_full_one(self):
        controller = self._make()
        controller.clear()
        self.assertEqual(self.applied, [])
        controller.send("G28")
        controller.clear()
        self.assertEqual(controller._transcript, [])
        self.assertEqual(controller.values["consoleHistory"], [])

    def test_the_clear_does_not_reload_the_legacy_history(self):
        persistence = _FakePersistence(shards={"A": {"consoleTranscript": [
            {"kind": "command", "text": "G28", "error": False, "success": False}]}})
        controller = self._make(persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual([entry["text"] for entry in controller._transcript], ["G28"])
        controller.clear()
        self.assertEqual(persistence.writes["A"], {"consoleTranscript": []})
        # The cleared shard blocks the reload: Clear sticks.
        controller.reload_if_empty()
        self.assertEqual(controller._transcript, [])

    def test_reload_keeps_a_live_session_on_the_same_machine(self):
        controller = self._make()
        controller.send("G28")
        controller._transcript_identity = "A"
        revisions = controller.values["consoleRevisions"]
        controller.reload_if_empty()
        self.assertEqual(controller.values["consoleRevisions"], revisions)
        self.assertEqual(controller._transcript[-1]["text"], "G28")

    def test_reload_holds_off_until_cura_knows_the_machine(self):
        for identity in (None, ("unknown", "Unknown Cura printer")):
            controller = self._make(identity=identity)
            controller.reload_if_empty()
            self.assertIsNone(controller._transcript_identity)

    def test_a_machine_switch_swaps_the_transcript(self):
        # A's lines must never bleed into B's pane and B's record.
        persistence = _FakePersistence(shards={
            "A": {"consoleTranscript": [{"kind": "command", "text": "A-only",
                                         "error": False, "success": False}]},
            "B": {"consoleTranscript": []}})
        controller = self._make(persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual([entry["text"] for entry in controller._transcript], ["A-only"])
        self.assertEqual(controller._transcript_identity, "A")
        controller._identity = lambda: ("B", "Printer B")
        controller.reload_if_empty()
        self.assertEqual(controller._transcript_identity, "B")
        self.assertEqual(controller._transcript, [])
        self.assertEqual(controller._dropped, 0)

    def test_an_empty_pane_on_the_same_machine_stays_quiet(self):
        controller = self._make()
        controller._transcript_identity = "A"
        revisions = controller.values["consoleRevisions"]
        controller.reload_if_empty()
        self.assertEqual(controller.values["consoleRevisions"], revisions)

    def test_the_shard_reader_distinguishes_absent_from_empty(self):
        controller = self._make()
        self.assertIsNone(controller._shard_transcript("A"))
        self.assertIsNone(controller._shard_transcript(""))
        # A shard that is not a mapping at all is ABSENT (the config
        # record still carries the transcript)...
        controller._persistence = _FakePersistence(shard="not a dict")
        self.assertIsNone(controller._shard_transcript("A"))
        # ...but a shard whose transcript is unusable reads as a Clear,
        # which is authoritative: an empty pane, not the config record.
        controller._persistence = _FakePersistence(shard={"consoleTranscript": "nope"})
        self.assertEqual(controller._shard_transcript("A"), [])
        controller._persistence = _FakePersistence(
            shard={"consoleTranscript": [{"kind": "command", "text": "shard"}]})
        self.assertEqual(controller._shard_transcript("A")[0]["text"], "shard")

    def test_a_shard_load_carries_its_own_store_stamp(self):
        persistence = _FakePersistence(shard={
            "consoleTranscript": [{"kind": "command", "text": "shard", "error": False,
                                   "success": False}],
            "consoleStoreTime": 12.0})
        controller = self._make(persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual(controller._transcript[0]["text"], "shard")
        self.assertEqual(controller._store_time, 12.0)
        self.assertEqual(controller._transcript_identity, "A")

    def test_a_missing_shard_falls_back_to_the_config_record(self):
        persistence = _FakePersistence(shard=None)
        controller = self._make(
            config=PrinterConfig(console_transcript=[{"kind": "command", "text": "config-side",
                                                      "error": False, "success": False}],
                                 console_store_time=3.0),
            persistence=persistence)
        controller.reload_if_empty()
        self.assertEqual(controller._transcript[0]["text"], "config-side")
        self.assertEqual(controller._store_time, 3.0)

    def test_an_empty_shard_never_reloads_the_config_record(self):
        # The constructor seeds from the config record; once the machine
        # resolves, its shard is authoritative — an EMPTY shard (a
        # genuine Clear) must not fall back to that record.
        persistence = _FakePersistence(shards={"A": {"consoleTranscript": []}})
        controller = self._make(
            config=PrinterConfig(console_transcript=[{"kind": "command", "text": "config-side",
                                                      "error": False, "success": False}]),
            persistence=persistence)
        self.assertEqual([entry["text"] for entry in controller._transcript], ["config-side"])
        # The pane currently belongs to another machine, so the next
        # reload reads A's record — and the EMPTY shard wins.
        controller._transcript_identity = "previous-machine"
        controller.reload_if_empty()
        self.assertEqual(controller._transcript, [])
        self.assertEqual(controller._transcript_identity, "A")

    def test_persisting_rides_the_shard_and_settles_the_colour(self):
        persistence = _FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        controller.send("G28")
        stored = persistence.writes["A"]["consoleTranscript"][0]
        self.assertEqual(stored, {"kind": "command", "text": "G28", "error": False,
                                  "success": False})
        self.assertEqual(self.applied, [])  # no legacy write beside the shard
        entry = controller._transcript[-1]
        self.assertFalse(entry["saved"])
        controller.mark_saved()
        self.assertTrue(entry["saved"])

    def test_an_unresolved_identity_skips_the_persist_entirely(self):
        persistence = _FakePersistence(shard=None)
        controller = self._make(identity=("unknown", "Unknown Cura printer"),
                                persistence=persistence)
        controller.send("G28")
        self.assertEqual(persistence.writes, {})
        self.assertEqual(self.applied, [])

    def test_a_rejected_shard_write_notes_the_console(self):
        persistence = _FakePersistence(shard=None, ok=False)
        controller = self._make(persistence=persistence)
        controller.send("G28")
        self.assertTrue(any("could not be saved" in entry["text"]
                            for entry in controller._transcript))

    def test_the_config_only_double_still_writes_the_legacy_record(self):
        controller = self._make()
        controller.send("G28")
        self.assertEqual(len(self.applied), 1)
        self.assertEqual(self.applied[-1].console_transcript[0]["text"], "G28")
        self.assertEqual(self.applied[-1].console_store_time, controller._store_time)
        # The guard: an unchanged record is never rewritten.
        controller._persist_legacy()
        self.assertEqual(len(self.applied), 1)

    def test_the_persist_window_pins_the_newest_commands_in_order(self):
        # The persisted record is the last MAX_TRANSCRIPT lines PLUS the
        # newest commands, so a typed request survives being rotated out
        # of the kept tail — up to MAX_PERSIST_COMMANDS of them.
        controller = self._make()
        def command(text):
            return {"kind": "command", "text": text, "error": False, "success": False,
                    "restored": False, "saved": False}
        controller._transcript = (
            [command(f"pin {n}") for n in range(5)]
            + [self._response(f"mid {n}") for n in range(40)]
            + [command(f"tail {n}") for n in range(7)]
            + [self._response(f"end {n}") for n in range(43)])
        window = controller._persist_window()
        texts = [entry["text"] for entry in window]
        self.assertEqual(texts[:3], ["pin 2", "pin 3", "pin 4"])
        self.assertNotIn("pin 0", texts)
        self.assertNotIn("pin 1", texts)
        self.assertEqual(len(window), MAX_TRANSCRIPT + 3)
        self.assertEqual(sum(1 for entry in window if entry["kind"] == "command"),
                         controller.MAX_PERSIST_COMMANDS)

    def test_mark_saved_only_touches_the_persisted_window(self):
        persistence = _FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        old = [{"kind": "command", "text": f"old {n}", "error": False, "success": False,
                "restored": False, "saved": False} for n in range(12)]
        oldest = old[0]
        restored = {"kind": "command", "text": "restored", "error": False, "success": False,
                    "restored": True, "saved": False}
        old[1] = restored
        tail = ([{"kind": "command", "text": f"new {n}", "error": False, "success": False,
                  "restored": False, "saved": False} for n in range(10)]
                + [self._response(f"line {n}") for n in range(MAX_TRANSCRIPT - 10)])
        controller._transcript = old + tail
        # The settle only claims what a SUCCESSFUL write stored, so the
        # write has to happen first (it is what arms the colour flip).
        controller._persist()
        controller.mark_saved()
        self.assertFalse(oldest["saved"])       # pushed out of the window
        self.assertFalse(restored["saved"])     # restored lines never claim disk
        self.assertTrue(tail[0]["saved"])       # a retained command inside it
        self.assertNotIn("saved", tail[-1])     # responses are left alone

    def test_a_write_failing_after_a_success_never_colours_its_unwritten_lines(self):
        # The review's catch: the settle used to colour "on disk" from
        # the transcript window alone, so a shard write that failed
        # after an earlier success painted lines it never stored.
        persistence = _FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        controller.send("G28")
        stored = controller._transcript[-1]
        persistence.ok = False
        controller.send("M18")
        # The failed write leaves its own note after the command.
        unwritten = next(entry for entry in controller._transcript if entry["text"] == "M18")
        controller.mark_saved()  # the settle the successful write armed
        self.assertTrue(stored["saved"])
        self.assertFalse(unwritten["saved"])

    def test_a_claim_holds_while_its_line_is_still_in_the_pane(self):
        # The claim names the entries a success stored, so a line the
        # pane still shows keeps its "on disk" verdict after newer
        # traffic has pushed it out of the persisted window — it WAS
        # written, and only the pane dropping it retires the claim.
        persistence = _FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        self.assertTrue(controller.send("G28"))
        written = controller._transcript[-1]
        controller._transcript.extend(
            [{"kind": "command", "text": f"M{n}", "error": False, "success": False,
              "restored": False, "saved": False} for n in range(controller.MAX_PERSIST_COMMANDS)]
            + [self._response(f"line {n}") for n in range(MAX_TRANSCRIPT)])
        self.assertFalse(any(entry is written for entry in controller._persist_window()))
        # A later success settles ITS window; the earlier claim has to
        # survive that write's bookkeeping.
        controller._persist()
        controller.mark_saved()
        self.assertTrue(written["saved"])

    def test_the_settle_is_not_restarted_by_continuous_traffic(self):
        # A chatty feed writes the shard every few hundred ms; a settle
        # restarted by every success would push the colour flip out
        # forever, so a pending settle is left to fire.
        persistence = _FakePersistence(shard=None)
        controller = self._make(persistence=persistence)
        controller._saved_timer.setInterval(200)
        controller.send("G28")
        first = controller._transcript[-1]
        self.events(150)
        controller.send("M18")
        self.events(150)
        self.assertTrue(first["saved"])

    def test_the_connection_note_prints_transitions_only(self):
        controller = self._make()
        self.data.connectionStateChanged.emit("unknown")
        self.assertEqual(controller._transcript[-1]["text"], "Disconnected from Moonraker.")
        self.assertEqual(controller._transcript[-1]["kind"], "note")
        self.data.connectionStateChanged.emit("yes")
        self.assertEqual(controller._transcript[-1]["text"], "Connected to Moonraker.")
        # The same state again writes nothing: a flapping link must not
        # fill the feed with repeats.
        self.data.connectionStateChanged.emit("yes")
        self.assertEqual(len(controller._transcript), 2)
        self.data.connectionStateChanged.emit("no")
        self.assertEqual(controller._transcript[-1]["text"], "Disconnected from Moonraker.")

    def test_a_session_invalidation_drops_pending_sends(self):
        controller = self._make()
        controller.send("G28")
        self.assertEqual(controller.values["consolePending"], 1)
        self.data.invalidated.emit()
        self.assertEqual(controller.values["consolePending"], 0)
        self.assertEqual(controller._in_flight, set())
        # The live pane is left alone: a same-machine reconnect must not
        # re-stamp a working session.
        self.assertEqual(controller._transcript[-1]["text"], "G28")
        self.data.invalidated.emit()
        self.assertEqual(controller.values["consolePending"], 0)

    def test_the_emergency_stop_note_only_prints_with_pending_sends(self):
        controller = self._make()
        self.commands.emergencyStopped.emit()
        self.assertEqual(controller._transcript, [])
        controller.send("G28")
        self.commands.emergencyStopped.emit()
        self.assertEqual(controller._transcript[-1]["text"],
                         "Pending console commands dropped by the emergency stop.")


if __name__ == "__main__":
    unittest.main()
