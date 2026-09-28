"""Preview-family coverage: the follower, motion, framing, toolhead and data seams.

Targets the branch-heavy error and fallback paths of the six modules the domain
suites reach mainly through their happy paths. Every double is local and
minimal: the Cura port/view, the index, the client and the snapshot are faked
at exactly the seam the module under test calls.

Qt-bound paths run against the real PyQt6 in the container (offscreen).

Lines no test can reach, with the reason:

* PreviewMotion.py:153  — ``write()``'s ramp-from-fraction fallback for a
  missing previous observation. The jump branch and ``reset()`` set
  ``_obs_time`` and ``_displayed`` together, so the non-jump path this
  guards always has a previous observation.
* PrintState.py:267-269 — the "past the last known start" arm of the
  height ladder. It needs the highest start at or below ``z`` to be the
  final entry, but that same entry is then within ``z_tolerance`` of
  ``z`` and the exact-match arm above has already claimed it.
* ToolheadPolicy.py:85  — ``clamp_relative_move``'s inverted-range
  (``minimum > maximum``) refusal. No target satisfies both clamps at
  once, so the floor or the maximum clamp returns first.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from tests.qt_runtime_support import QT_AVAILABLE

from plugins import SocketFraming
from plugins.SocketFraming import (
    FrameState,
    FramingError,
    MAX_MESSAGE_BYTES,
    accept_value,
    build_client_key,
    build_handshake,
    encode_binary_frame,
    encode_close_frame,
    encode_ping,
    encode_pong,
    encode_text_frame,
    parse_frames,
    verify_handshake,
)
from plugins.ToolheadPolicy import (
    CENTER_Z_MM,
    EXTRUDE_SPEED_MAX,
    JOG_DISTANCE_DEFAULT,
    JOG_DISTANCE_MAX,
    JOG_DISTANCE_MIN,
    MAX_PENDING_OPS,
    STATUS_QUEUE_FULL,
    axis_ok,
    center_script,
    clamp_relative_move,
    extrude_distance_ok,
    extrude_script,
    extrude_speed_ok,
    home_script,
    jog_distance_ok,
    jog_gate,
    jog_script,
    make_center_op,
    make_extrude_op,
    make_home_op,
    make_jog_op,
    make_motors_off_op,
    make_z0_op,
    motors_off_script,
    position_mode_text,
    push_op,
    z0_script,
)
from plugins.PrintState import LayerResolver, MotionProgress, PhysicalLayer, PrintSnapshot
from plugins.PreviewFollower import PreviewFollower, preview_override_kind

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal
    from plugins.MonitorData import MonitorData, freeze
    from plugins.PreviewMotion import PreviewMotion


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------


class FakeView:
    """The Cura SimulationView subset the Preview family reads and writes."""

    def __init__(self, layer=0, minimum=0, path=0.0, maximum=100, minimum_path=0):
        self.layer, self.minimum, self.path = int(layer), int(minimum), float(path)
        self.maximum, self.minimum_path = maximum, int(minimum_path)
        self.writes = []          # (handle, value) from apply_preview_decision
        self.paths = []           # setPath calls
        self.minimum_paths = []   # setMinimumPath calls
        self.resets = 0           # resetLayerData calls

    def getCurrentLayer(self): return self.layer
    def getMinimumLayer(self): return self.minimum
    def getCurrentPath(self): return self.path
    def getMinimumPath(self): return self.minimum_path
    def getMaxPaths(self): return self.maximum

    def setLayer(self, value): self.layer = int(value); self.writes.append(("layer", int(value)))
    def setMinimumLayer(self, value): self.minimum = int(value); self.writes.append(("minimum", int(value)))
    def setPath(self, value): self.path = float(value); self.paths.append(float(value))
    def setMinimumPath(self, value): self.minimum_path = int(value); self.minimum_paths.append(int(value))
    def resetLayerData(self): self.resets += 1


@contextmanager
def writing_preview():
    yield


class FakeCura:
    """The Cura port PreviewFollower drives."""

    def __init__(self, view=None):
        self.view = view
        self.suspended = False
        self.has_toolpath = True
        self.max_layer = 10
        self.selected_layer = None
        self.switches = 0
        self.nozzles = 0
        self.switch_result = True
        self.writes = 0

    def switch_to_preview(self):
        self.switches += 1
        return self.switch_result

    def show_nozzle(self): self.nozzles += 1

    def writing_preview(self):
        self.writes += 1
        return writing_preview()


class FakeIndex:
    """The MultiIndex subset PreviewFollower reads."""

    def __init__(self, layers=5, elapsed=None, hydrated=None, fraction=0.5,
                 method="byte-range", mapping=None, at=None):
        self.ranges = tuple((0, 100) for _ in range(layers))
        self.elapsed_times = [10.0 * i for i in range(layers)] if elapsed is None else list(elapsed)
        self._hydrated = set(range(layers)) if hydrated is None else set(hydrated)
        self._fraction, self.method = fraction, method
        self.current_layer_map = dict(mapping or {})
        self._at = dict(at or {})

    def hydrated(self, layer): return layer in self._hydrated
    def fraction(self, layer, position, live, previous): return (self._fraction, self.method)
    def layer_at(self, position): return self._at.get(position)


def preview_config(**overrides):
    base = dict(enabled=True, follow_mode="exact", auto_preview=False, path_follow=False,
                eta_learn=False, path_smoothing=True, show_toolhead_indicator=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def snapshot(layer=None, active=True, state="printing", motion=None):
    return SimpleNamespace(layer=SimpleNamespace(index=layer), active=active,
                           observation=SimpleNamespace(state=state), motion_progress=motion)


def motion_observation(layer, index, st):
    """The already-accepted service output at the Preview's input seam."""
    try:
        int((st.get("virtual_sdcard") or {}).get("file_position"))
    except (TypeError, ValueError):
        return None
    if index is None or not index.hydrated(layer):
        return None
    return MotionProgress(layer, round(index._fraction * 1000), 1000, index.method)


def status(**overrides):
    base = {"print_stats": {"print_duration": 60.0}, "gcode_move": {"speed_factor": 1.0}}
    base.update(overrides)
    return base


class MotionTrace:
    """PreviewMotion's `remember` hook plus the writes a follower delegates."""

    def __init__(self):
        self.calls = 0
        self.reset_calls = 0
        self.writes = []

    def __call__(self):
        self.calls += 1

    def reset(self):
        self.reset_calls += 1

    def write(self, layer, fraction, method=""):
        self.writes.append((layer, fraction, method))


# --------------------------------------------------------------------------
# SocketFraming — pure RFC 6455 client framing
# --------------------------------------------------------------------------


def client_frame(opcode, payload=b"", *, fin=True, rsv=0):
    """A well-formed, unmasked server frame (the client role expects no mask)."""
    first = (0x80 if fin else 0x00) | rsv | opcode
    length = len(payload)
    if length < 126:
        head = bytes([first, length])
    elif length < 65536:
        head = bytes([first, 126]) + struct.pack(">H", length)
    else:
        head = bytes([first, 127]) + struct.pack(">Q", length)
    return head + payload


def payload_of(frame):
    """The masked payload of a client frame, un-XORed with its own mask."""
    length = frame[1] & 0x7F
    index = 2
    if length == 126:
        length, index = struct.unpack(">H", frame[2:4])[0], 4
    elif length == 127:
        length, index = struct.unpack(">Q", frame[2:10])[0], 10
    mask = frame[index:index + 4]
    body = frame[index + 4:index + 4 + length]
    return bytes(byte ^ mask[n % 4] for n, byte in enumerate(body))


class HandshakeTests(unittest.TestCase):
    pass


def upgrade_response(key, *, status="HTTP/1.1 101 Switching Protocols",
                     upgrade="websocket", connection="Upgrade", extra=()):
    lines = [status]
    if upgrade is not None: lines.append("Upgrade: %s" % upgrade)
    if connection is not None: lines.append("Connection: %s" % connection)
    lines.append("Sec-WebSocket-Accept: %s" % accept_value(key))
    lines.extend(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


class VerifyHandshakeTests(unittest.TestCase):
    KEY = "dGhlIHNhbXBsZSBub25jZQ=="


class ClientFrameEncodingTests(unittest.TestCase):
    pass


class ParseFramesTests(unittest.TestCase):
    pass


class CloseFrameTests(unittest.TestCase):
    pass


# --------------------------------------------------------------------------
# ToolheadPolicy — scripts, gates and the jog queue
# --------------------------------------------------------------------------


class ToolheadScriptTests(unittest.TestCase):
    pass


class ClampRelativeMoveTests(unittest.TestCase):
    pass


class JogQueueTests(unittest.TestCase):
    pass


# --------------------------------------------------------------------------
# PrintState.LayerResolver — the physical-layer estimator
# --------------------------------------------------------------------------


def layer_config(**overrides):
    base = dict(moonraker_layer_is_one_based=True, z_fallback=True, z_tolerance=0.02)
    base.update(overrides)
    return SimpleNamespace(**base)


def z_status(z, e, *, progress=0.5, current_layer=None, total_layer=None, state="printing",
             objects=True):
    """A status carrying a G-code position pair; `objects=False` drops the mappings."""
    if not objects:
        return {"print_stats": [], "virtual_sdcard": [], "gcode_move": []}
    info = {}
    if current_layer is not None: info["current_layer"] = current_layer
    if total_layer is not None: info["total_layer"] = total_layer
    sdcard = {} if progress is None else {"progress": progress}
    return {"print_stats": {"state": state, "info": info},
            "virtual_sdcard": sdcard,
            "gcode_move": {"gcode_position": [0.0, 0.0, z, e]}}


class LayerResolverBasicsTests(unittest.TestCase):
    pass


class LayerResolverZEstimateTests(unittest.TestCase):
    """The extrusion-guarded Z estimator: seeds, plateau commits and its retires."""

    def drive(self, samples, *, config=None, metadata=None, index=None, heights=(),
              progress=0.5):
        resolver = LayerResolver()
        config = config or layer_config()
        layers = [resolver.resolve(z_status(z, e, progress=progress), config, index=index,
                                   metadata=metadata, heights=heights)
                  for z, e in samples]
        return resolver, layers


class LayerResolverGeometryTests(unittest.TestCase):
    HEIGHTS = (0.2, 0.4, 0.6, 0.8, 1.0)
    METADATA = {"layer_height": 0.2, "first_layer_height": 0.2}

    def resolve_at(self, z, *, heights, metadata=None, config=None):
        # The geometry paths live inside the extrusion-guarded estimate, so
        # every probe needs a baseline poll before the Z sample.
        resolver = LayerResolver()
        config = config or layer_config()
        resolver.resolve(z_status(max(0.2, z - 0.2), 0.0, progress=0.5), config,
                         metadata=metadata or self.METADATA, heights=heights)
        return resolver.resolve(z_status(z, 1.0, progress=0.5), config,
                                metadata=metadata or self.METADATA, heights=heights)


class LayerResolverStartTests(unittest.TestCase):
    pass


class PrintSnapshotTests(unittest.TestCase):
    pass


# --------------------------------------------------------------------------
# PreviewFollower — policy, tracking and the ETA projections
# --------------------------------------------------------------------------


class DeferredView(FakeView):
    """A view whose writes land an observation late (Cura's hung restore)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pending = []

    def setLayer(self, value): self.pending.append(("setLayer", int(value)))
    def setMinimumLayer(self, value): self.pending.append(("setMinimumLayer", int(value)))

    def commit(self):
        for name, value in self.pending:
            getattr(FakeView, name)(self, value)
        self.pending = []


class PathBlockedView(FakeView):
    """A view with no path API: hasattr() must see it as tracking-unavailable."""

    @property
    def setPath(self):
        raise AttributeError("setPath")


def follower_of(view=None, **port_overrides):
    port = FakeCura(view)
    for name, value in port_overrides.items():
        setattr(port, name, value)
    return PreviewFollower(port), port


class OverrideKindTests(unittest.TestCase):
    pass


class FollowerDetachTests(unittest.TestCase):
    pass


class FollowerViewTests(unittest.TestCase):
    pass


class FollowerObserveTests(unittest.TestCase):
    def setUp(self):
        self.view = FakeView(layer=3)
        self.follower, self.port = follower_of(self.view)

    def observe(self, snap=None, st=None, config=None, index=None):
        snap, st, index = snap or snapshot(3), st or status(), index or FakeIndex()
        snap.motion_progress = motion_observation(snap.layer.index, index, st)
        return self.follower.observe(snap, st, config or preview_config(), index)


class FollowerPathTests(unittest.TestCase):
    def setUp(self):
        self.view = FakeView(layer=3, maximum=1000)
        self.follower, self.port = follower_of(self.view)

    def run_path(self, *, index=..., st=None, config=None, view=None, motion=None, vsc=...):
        if view is not None:
            self.port.view = view
        if motion is not None:
            self.follower.bind_motion(motion)
        if st is None:
            st = status()
            if vsc is ...:
                st["virtual_sdcard"] = {"file_position": 400}
            elif vsc is not None:
                st["virtual_sdcard"] = vsc
        index = FakeIndex() if index is ... else index
        return self.follower.observe(snapshot(3, motion=motion_observation(3, index, st)), st,
                                     config or preview_config(path_follow=True), index)

    def path_detail(self, *, index=..., st=None, view=None, vsc=..., smooth=True):
        """_follow_path directly: observe() reports only its own status word."""
        if view is not None:
            self.port.view = view
        if st is None:
            st = status()
            if vsc is ...:
                st["virtual_sdcard"] = {"file_position": 400}
            elif vsc is not None:
                st["virtual_sdcard"] = vsc
        index = FakeIndex() if index is ... else index
        return self.follower._follow_path(self.port.view, 3, index,
                                         motion_observation(3, index, st), smooth=smooth)


class FollowerEtaTests(unittest.TestCase):
    def make(self, elapsed=None, duration=60.0, elapsed_layer=3):
        view = FakeView(layer=elapsed_layer)
        follower, port = follower_of(view)
        index = FakeIndex(elapsed=elapsed)
        follower.observe(snapshot(elapsed_layer),
                         status(print_stats={"print_duration": duration}), preview_config(), index)
        return follower, port, index


# --------------------------------------------------------------------------
# PreviewMotion — the Qt tick driver
# --------------------------------------------------------------------------


class FakeClock:
    """Replaces the module's time reference so the ticks are deterministic."""

    def __init__(self): self.now = 1000.0
    def monotonic(self): return self.now
    def advance(self, seconds): self.now += seconds


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class PreviewMotionTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from PyQt6.QtCore import QCoreApplication
        # QTimer needs an application object even when it never fires here.
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.patch = patch("plugins.PreviewMotion.time", FakeClock())
        # PreviewMotion resolves `time.monotonic` through its own module global.
        self.clock = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.view = FakeView(layer=1, maximum=1000)
        self.cura = FakeCura(self.view)
        self.remember = MotionTrace()
        self.motion = PreviewMotion(self.cura, self.remember)


# --------------------------------------------------------------------------
# MonitorData — the request/poll lifecycle and its projections
# --------------------------------------------------------------------------


class FakeSession:
    """The session record MonitorData reads for intervals and generations."""

    def __init__(self):
        self.base_url = "http://printer:7125/"
        self.generation = 1
        self.snapshot = SimpleNamespace(printer_state="ready")
        self.forced = {}
        self.calls = []
        self.poll_policy = SimpleNamespace(interval_ms=self.interval_ms)

    def interval_ms(self, category, default, printer_state):
        self.calls.append((category, default, printer_state))
        return int(self.forced.get(category, default))


class FakeTransport:
    """The wire the monitor sends through; every request stays inspectable."""

    def __init__(self):
        self.sent = []
        self.cancelled = []

    def send_json(self, owner, channel, method, path, callback, *, body=None, replace=False,
                  category=None, timeout_ms=None):
        self.sent.append(SimpleNamespace(owner=owner, channel=channel, method=method, path=path,
                                         callback=callback, body=body, replace=replace,
                                         category=category, timeout_ms=timeout_ms))
        return True

    def cancel_owner(self, name): self.cancelled.append(name)


class FakeDataClient(QObject if QT_AVAILABLE else object):
    """The MoonrakerClient seam MonitorData binds to."""

    if QT_AVAILABLE:
        statusReceived = pyqtSignal(object)
        commandChanged = pyqtSignal(object)
        sessionInvalidated = pyqtSignal()
        connectionChanged = pyqtSignal(bool, str)

    def __init__(self):
        if QT_AVAILABLE:
            super().__init__()
        self.connected = False
        self.status = {"print_stats": {"state": "standby"}}
        self.aux_interval_ms = 2500
        self.console_interval_ms = 1000
        self.effective_feed_mode = "http"
        self.session = FakeSession()
        self.transport = FakeTransport()
        self.assumed_stopped = False
        self.aux_patch = None
        self.rpc_ok = False
        self.rpcs = []
        self.aux_sets = []
        self.drains = 0
        self.refreshes = 0
        self.stopped = 0
        self.started = 0
        self.resubscribes = 0
        self.tracked = []
        self.accepted = []
        self.failed = []
        self.settled = []
        self.assumed_stops = 0
        self.guards = []

    def rpc(self, method, params, callback):
        self.rpcs.append((method, params, callback))
        return self.rpc_ok

    def drain_aux(self):
        self.drains += 1
        return (self.aux_patch, 1.0)

    def set_auxiliary_objects(self, names): self.aux_sets.append(set(names))
    def force_refresh(self): self.refreshes += 1
    def resubscribe(self): self.resubscribes += 1
    def assume_print_stopped(self): self.assumed_stops += 1
    def set_toolhead_guard(self, active): self.guards.append(active)
    def stop(self): self.stopped += 1
    def start(self): self.started += 1

    def track_command(self, name, expected_states=(), *, timeout_s=10.0):
        self.tracked.append((name, tuple(expected_states), timeout_s))

    def accept_command(self, name): self.accepted.append(name)
    def fail_command(self, name, detail): self.failed.append((name, detail))
    def settle_command(self, name, detail): self.settled.append((name, detail))


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class MonitorDataTests(unittest.TestCase):
    def setUp(self):
        from PyQt6.QtCore import QCoreApplication
        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.client = FakeDataClient()
        self.data = MonitorData(self.client)
        for timer in self.data._timers.values(): timer.stop()
        self.data._console_watch.stop()
        self.data._watchdog.stop()
        self.changes = []
        self.invalidations = []
        self.blocks = []
        self.states = []
        self.aux_changes = []
        self.console_changes = []
        self.data.changed.connect(lambda: self.changes.append(1))
        self.data.invalidated.connect(lambda: self.invalidations.append(1))
        self.data.previewBlockChanged.connect(self.blocks.append)
        self.data.connectionStateChanged.connect(self.states.append)
        self.data.auxiliaryChanged.connect(lambda: self.aux_changes.append(1))
        self.data.consoleStoreChanged.connect(lambda: self.console_changes.append(1))
        self.addCleanup(self.data.set_owner_active, False)

    def pump(self, ms=20):
        # A real nested event loop: processEvents() alone never delivers the
        # later(0) pushes these tests wait on.
        from PyQt6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def request_to(self, channel, position=-1):
        sent = [item for item in self.client.transport.sent if item.channel == channel]
        return sent[position]

    def activate(self):
        self.client.connected = True
        self.data.set_owner_active(True)


class MonitorConnectionTests(MonitorDataTests):
    pass


class MonitorRequestTests(MonitorDataTests):
    pass


class MonitorProjectionTests(MonitorDataTests):
    pass


class MonitorAuxTests(MonitorDataTests):
    pass


class MonitorLaneTests(MonitorDataTests):
    pass


class MonitorConsoleTests(MonitorDataTests):
    def store(self, entries):
        return {"result": {"gcode_store": entries}}


# Explicit exports retain dependencies used by extracted cases. Importing this
# module creates no Qt application; setUpClass owns application startup.
__all__ = ['CENTER_Z_MM', 'ClampRelativeMoveTests', 'ClientFrameEncodingTests', 'CloseFrameTests', 'DeferredView', 'EXTRUDE_SPEED_MAX', 'FakeClock', 'FakeCura', 'FakeDataClient', 'FakeIndex', 'FakeSession', 'FakeTransport', 'FakeView', 'FollowerDetachTests', 'FollowerEtaTests', 'FollowerObserveTests', 'FollowerPathTests', 'FollowerViewTests', 'FrameState', 'FramingError', 'HandshakeTests', 'JOG_DISTANCE_DEFAULT', 'JOG_DISTANCE_MAX', 'JOG_DISTANCE_MIN', 'JogQueueTests', 'LayerResolver', 'LayerResolverBasicsTests', 'LayerResolverGeometryTests', 'LayerResolverStartTests', 'LayerResolverZEstimateTests', 'MAX_MESSAGE_BYTES', 'MAX_PENDING_OPS', 'Mock', 'MonitorAuxTests', 'MonitorConnectionTests', 'MonitorConsoleTests', 'MonitorData', 'MonitorDataTests', 'MonitorLaneTests', 'MonitorProjectionTests', 'MonitorRequestTests', 'MotionTrace', 'OverrideKindTests', 'ParseFramesTests', 'PathBlockedView', 'PhysicalLayer', 'PreviewFollower', 'PreviewMotion', 'PreviewMotionTests', 'PrintSnapshot', 'PrintSnapshotTests', 'QObject', 'QT_AVAILABLE', 'STATUS_QUEUE_FULL', 'SimpleNamespace', 'SocketFraming', 'ToolheadScriptTests', 'VerifyHandshakeTests', 'accept_value', 'annotations', 'axis_ok', 'base64', 'build_client_key', 'build_handshake', 'center_script', 'clamp_relative_move', 'client_frame', 'contextmanager', 'encode_binary_frame', 'encode_close_frame', 'encode_ping', 'encode_pong', 'encode_text_frame', 'extrude_distance_ok', 'extrude_script', 'extrude_speed_ok', 'follower_of', 'freeze', 'hashlib', 'home_script', 'jog_distance_ok', 'jog_gate', 'jog_script', 'layer_config', 'make_center_op', 'make_extrude_op', 'make_home_op', 'make_jog_op', 'make_motors_off_op', 'make_z0_op', 'motors_off_script', 'parse_frames', 'payload_of', 'position_mode_text', 'preview_config', 'preview_override_kind', 'push_op', 'pyqtSignal', 'replace', 'snapshot', 'status', 'struct', 'unittest', 'upgrade_response', 'verify_handshake', 'writing_preview', 'z0_script', 'z_status']
