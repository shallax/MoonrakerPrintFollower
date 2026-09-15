"""The Moonraker simulator: the harness's network peer.

Speaks the REAL protocols over real TCP, in both transports — the
websocket subscribe/push contract (full snapshot once, changes only
afterward, subscriptions wiped on klippy_ready) and the HTTP lanes the
plugin calls. A scripted Klipper state machine drives the pushes;
fault arms (stalls, drops, refusals, closes) inject the failure modes
the gate scenarios need. Moonraker is what the PRINTER runs, never
what Cura runs — this double is the one permitted fake.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

import tornado.httpserver
import tornado.ioloop
import tornado.web
import tornado.websocket

# The core subscription set the production client requests
# (MoonrakerProtocol.CORE_OBJECTS, mirrored here so the simulator is
# independent of the plugin's code).
CORE_OBJECTS = ("print_stats", "gcode_move", "virtual_sdcard", "motion_report", "bed_mesh", "display_status")


def make_gcode(layers: int = 40) -> str:
    """A deterministic, Cura-parseable gcode: a square perimeter per
    layer with extrusion moves and M73 progress. Real parse/render
    targets for the load pipeline (A25)."""
    lines = [";FLAVOR:Marlin", ";LAYER_COUNT:%d" % layers, "M73 P0", "G90", "M82"]
    size = 50.0
    layer_height = 0.2
    z = layer_height
    extruded = 0.0
    for layer in range(layers):
        lines.append(";LAYER:%d" % layer)
        corners = [(100.0, 100.0), (100.0 + size, 100.0),
                   (100.0 + size, 100.0 + size), (100.0, 100.0 + size)]
        # Travel to the first corner, then the perimeter.
        lines.append("G0 X%.2f Y%.2f Z%.2f F6000" % (corners[0][0], corners[0][1], z))
        for x, y in corners[1:]:
            extruded += 0.12
            lines.append("G1 X%.2f Y%.2f E%.4f F1800" % (x, y, extruded))
        extruded += 0.12
        lines.append("G1 X%.2f Y%.2f E%.4f F1800" % (corners[0][0], corners[0][1], extruded))
        # A diagonal infill line for visible geometry.
        extruded += 0.08
        lines.append("G1 X%.2f Y%.2f E%.4f F2400" % (100.0 + size, 100.0 + size, extruded))
        z += layer_height
        lines.append("M73 P%d" % min(100, int((layer + 1) * 100 / layers)))
    lines.append("M73 P100")
    lines.append(";TIME_ELAPSED:1234")
    return "\n".join(lines) + "\n"


# Full state once per subscribe reply; pushes carry changes only.
KICKOFF_STATE: Dict[str, Any] = {
    "print_stats": {
        "state": "standby",
        "message": "",
        "filename": "",
        "total_duration": 0.0,
        "print_duration": 0.0,
        "filament_used": 0.0,
        "info": {"total_layer": None, "current_layer": None},
    },
    "gcode_move": {
        "speed_factor": 1.0,
        "extrude_factor": 1.0,
        "absolute_coordinates": True,
        "position": [100.0, 100.0, 0.4, 0.0],
    },
    "virtual_sdcard": {
        "progress": 0.0,
        "file_position": 0,
        "is_active": False,
        "file_size": 0,
    },
    # The motion rows (4.2.0) read these scalars: the sim carries the
    # real object shape (live_velocity, live_extruder_velocity,
    # steppers as NAMES) so tests against the sim are honest.
    "motion_report": {"live_position": [100.0, 100.0, 0.4, 0.0],
                      "live_velocity": 0.0, "live_extruder_velocity": 0.0,
                      "steppers": ["extruder", "stepper_x", "stepper_y", "stepper_z"]},
    "display_status": {"message": "", "progress": 0.0},
    # The accel ceiling and the ACTIVE tool (the per-tool diameter
    # read keys off extruder) ride the aux poll with homed_axes.
    "toolhead": {"homed_axes": "xyz", "extruder": "extruder", "max_accel": 5000.0,
                 "max_velocity": 500.0, "position": [100.0, 100.0, 0.4, 0.0]},
    "heater_bed": {"temperature": 0.0, "target": 0.0},
    "extruder": {"temperature": 0.0, "target": 0.0},
    "system_stats": {"sysload": 0.1, "memavail": 1000000},
    "fan": {"speed": 0.0},
    # The plugin subscribes to configfile for the save-config surface
    # (saveConfigPending) and to bed_mesh for the mesh render — a
    # faithful sim must carry both objects even while empty. The
    # config/settings pair mirrors the real object: config holds the
    # raw STRING, settings the typed float (the diameter read uses
    # settings).
    "configfile": {"save_config_pending": False, "save_config_pending_items": {},
                   "config": {"extruder": {"filament_diameter": "1.75"}},
                   "settings": {"extruder": {"filament_diameter": 1.75}}},
    "bed_mesh": {"profile_name": "", "probed_matrix": [], "mesh_min": [], "mesh_max": [], "profiles": {}},
}


class PrinterState:
    """The scripted Klipper/Moonraker state machine."""

    def __init__(self) -> None:
        self.state = dict(json.loads(json.dumps(KICKOFF_STATE)))
        self.objects_available = list(self.state)
        self.subscription_wiped = False
        self.handlers: Any = set()
        # Fault arms
        self.stall_ms = 0.0          # hold the next push for this long
        self.drop_next = 0           # drop this many pushes
        self.refuse_subscribe = ""   # message to refuse with ("" = accept)
        self.push_cadence_ms = 250
        # The print lifecycle arms (scenario 1): a cold start raises
        # the transient extrude error and ramps the heater; a broken
        # start keeps the error past the client's verdict window.
        self.cold_start = False
        self.broken_start = False
        self.extruder_ramp_deg_s = 30.0
        self.connections = 0
        self._layer_clock_at = time.monotonic()
        # The scenario-5 race: hold the subscribe reply past the
        # client's proof window, then release — the snapshot must
        # still unlock the aux feed promptly. Stamps on the sim's
        # monotonic event clock.
        self.require_api_key = False
        self.subscribe_hold_ms = 0.0
        self.subscribe_replied_at = None
        self.subscribe_replied_wall = None
        self.first_push_after_reply_at = None
        # Changes-only pushes: the patch must carry exactly the
        # objects whose values moved since the last frame (the
        # protocol contract the client's masked diff depends on).
        self._last_pushed: Dict[str, Any] = {}
        # The webcam's MJPEG frames: ffmpeg-generated test patterns
        # (the moving bar + timestamp prove liveness in captures).
        self.webcam_streams = 0
        self.webcam_frames = []
        self.webcam_bridged_base = ""
        self.webcam_down = False  # the stream-failure arm: /webcam 404s
        self.webcam_die_after = 0  # serve N frames then close mid-stream
        self.slow_first_frame_ms = 0.0
        # The power-section devices (the controls pane's toggle rows);
        # armed by scenarios, e.g. a DFU device like a Voron's.
        self.power_devices = []
        # The database presets value (the controls pane's speed
        # presets); armed by scenarios — the lane census asserts the
        # stored rows render, so the sim must serve a real value.
        self.presets_value = {}
        # The gcode download's streaming cadence: milliseconds per
        # 256-byte chunk (0 = one-shot). The preview's load indicator
        # needs bytes arriving on the wire to animate its bar — a
        # pre-request hold delivers nothing and the bar never moves.
        self.gcode_stream_ms = 0
        # The missed-pause arm: the PAUSE gcode script is refused —
        # the controller must keep the entry, restyled.
        self.fail_pause_script = False
        # The corrupt-frame arm: the next push goes out as garbage —
        # the client's socket must fail and reconnect (S-corruption).
        self.corrupt_frame_once = False
        # The new lifecycle arms (round-2 fold-ins): the stageable
        # file listing, the autonomous temperature ticker, the upload
        # refusal/hold, the accepted-but-never-confirmed pause, the
        # injected clock and the push transcript.
        self.temp_tick_deg_c = 0.0
        # The motion ticker (4.2.0): armed speeds advance the live
        # positions and set the velocities each push while printing.
        self.motion_speed_mm_s = 0.0
        self.motion_e_mm_s = 0.0
        self.fail_upload = False
        self.accept_pause_without_state = False
        self.clock_skew_ms = 0.0
        self.push_ledger = []
        self.push_seq = 0
        # Keys the scenario lane could not apply — the runner's
        # sim_arm/sim_set refuse on these.
        self.unknown_keys = []
        try:
            directory = tempfile.mkdtemp(prefix="mpf-frames-")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=2",
                 os.path.join(directory, "frame%02d.jpg")],
                check=False, timeout=30)
            for name in sorted(os.listdir(directory)):
                path = os.path.join(directory, name)
                if not name.endswith(".jpg"):
                    continue
                with open(path, "rb") as handle:
                    self.webcam_frames.append(handle.read())
        except Exception:
            self.webcam_frames = []
        # The console's gcode-store backfill (scenario 11 floods it).
        self.console_lines = [{"type": "response", "message": "// Klipper state: Ready",
                               "time": time.time()}]
        # The gcode store the file manager walks.
        self.gcode_bytes = make_gcode(40).encode("utf-8")
        self.files = [
            {"filename": "scenario1.gcode", "modified": time.time() - 3600.0,
             "size": len(self.gcode_bytes), "permissions": "rw",
             "slicer": "MoonrakerPrintFollower-sim", "estimated_time": 3600.0,
             "layer_height": 0.2, "filament_total": 12.5,
             "uuid": "sim-uuid-1", "job_id": None, "print_start_time": None},
            {"filename": "benchy.gcode", "modified": time.time() - 7200.0,
             "size": len(self.gcode_bytes), "permissions": "rw",
             "slicer": "MoonrakerPrintFollower-sim", "estimated_time": 1800.0,
             "layer_height": 0.2, "filament_total": 6.0,
             "uuid": "sim-uuid-2", "job_id": None, "print_start_time": None},
            # The delete/rename scenarios' disposable target: the
            # group's own scenarios must not destroy each other's
            # files (the f3-deletes-scenario1 knock-on lesson).
            {"filename": "delete-me.gcode", "modified": time.time() - 10800.0,
             "size": len(self.gcode_bytes), "permissions": "rw",
             "slicer": "MoonrakerPrintFollower-sim", "estimated_time": 600.0,
             "layer_height": 0.2, "filament_total": 2.0,
             "uuid": "sim-uuid-3", "job_id": None, "print_start_time": None},
        ]
        self.seed = 0
        self.klippy_ready_broadcast: Any = None  # set by the app on klippy restart
        # The request ledger: the dwell profile's instrument (every
        # request with its timestamps, bytes and in-flight depth).
        self.ledger: List[Dict[str, Any]] = []
        self.inflight = 0
        # Per-route service time in ms: the capacity model. A route
        # with a delay simulates a loaded Moonraker under request load.
        self.route_delay_ms: Dict[str, float] = {}

    def record_begin(self, method: str, path: str) -> int:
        self.inflight += 1
        self.ledger.append({"ts": time.monotonic(), "method": method, "path": path,
                            "bytes": 0, "ms": 0.0, "inflight": self.inflight})
        return len(self.ledger) - 1

    def record_end(self, index: int, nbytes: int) -> None:
        if index is None or index >= len(self.ledger):
            return
        entry = self.ledger[index]
        entry["ms"] = (time.monotonic() - entry["ts"]) * 1000.0
        entry["bytes"] = nbytes
        # The entry keeps the depth it STARTED at: peak_inflight reads
        # what the request itself saw, not the post-completion depth.
        self.inflight = max(0, self.inflight - 1)

    def service_delay(self, path: str) -> float:
        # Normalise both sides: the handlers see the request path WITH
        # its leading slash while the arms spell routes without one —
        # an unnormalised match silently never fires (the calibration
        # lesson).
        path = str(path or "").lstrip("/")
        for prefix, delay in self.route_delay_ms.items():
            if path.startswith(str(prefix).lstrip("/")):
                return delay
        return 0.0

    def ledger_stats(self) -> Dict[str, Any]:
        entries = self.ledger
        window = entries[-60:]
        return {"total": len(entries), "requests_per_s": round(len(window) / max(0.1, window[-1]["ts"] - window[0]["ts"]), 3) if len(window) > 1 else 0.0,
                "peak_inflight": max((e["inflight"] for e in entries), default=0),
                "p95_ms": round(sorted(e["ms"] for e in window)[int(len(window) * 0.95)] if window else 0.0, 2)}

    def klippy_restart(self) -> None:
        """Moonraker wipes every client subscription on a Klippy
        restart; the ready broadcast is the re-subscribe trigger."""
        self.subscription_wiped = True
        frame = {"jsonrpc": "2.0", "method": "notify_klippy_ready", "params": [time.time()]}
        for handler in list(self.handlers):
            try:
                handler.write_message(json.dumps(frame))
            except Exception:
                pass

    def scenario(self, **changes) -> None:
        for name, value in changes.items():
            if name in self.state:
                self.state[name] = value
            elif name in ("cold_start", "broken_start", "extruder_ramp_deg_s",
                          "slow_first_frame_ms", "webcam_down", "webcam_die_after",
                          "route_delay_ms", "subscribe_hold_ms", "require_api_key",
                          "refuse_subscribe"):
                setattr(self, name, value)
            elif name == "console_lines":
                self.console_lines = list(value)
            elif name == "power_devices":
                self.power_devices = list(value)
            elif name == "presets_value":
                self.presets_value = dict(value)
            elif name == "gcode_stream_ms":
                self.gcode_stream_ms = max(0, int(value))
            elif name == "fail_pause_script":
                self.fail_pause_script = bool(value)
            elif name == "corrupt_frame_once":
                self.corrupt_frame_once = bool(value)
            elif name == "files":
                # The stageable listing: F06's 400-file baseline and
                # the file scenarios' arbitrary inventories. Entries
                # without a modified time take the clock seam's now,
                # so date-filter scenarios stage old files with
                # clock_skew_ms instead of hand-computed stamps.
                self.files = list(value)
                for entry in self.files:
                    if entry.get("modified") is None:
                        entry["modified"] = self.now()
            elif name == "temp_tick_deg_c":
                self.temp_tick_deg_c = float(value or 0.0)
            elif name == "motion_speed_mm_s":
                self.motion_speed_mm_s = float(value or 0.0)
            elif name == "motion_e_mm_s":
                self.motion_e_mm_s = float(value or 0.0)
            elif name == "fail_upload":
                self.fail_upload = bool(value)
            elif name == "accept_pause_without_state":
                self.accept_pause_without_state = bool(value)
            elif name == "clock_skew_ms":
                self.clock_skew_ms = float(value or 0.0)
            else:
                # A dropped key silently narrows the very surface the
                # suite exists to widen — record it so the runner can
                # refuse (the panel's finding).
                self.unknown_keys.append(str(name))

    def reset(self) -> None:
        """The hermetic scenario boundary: state back to kickoff, every
        fault arm down, console and ledger cleared. The suite runner
        resets between scenarios so one scenario's arms can never leak
        into the next (the group-a cascade that took down the sweep)."""
        self.state = dict(json.loads(json.dumps(KICKOFF_STATE)))
        self.objects_available = list(self.state)
        self.subscription_wiped = False
        self.stall_ms = 0.0
        self.drop_next = 0
        self.refuse_subscribe = ""
        self.push_cadence_ms = 250
        self.cold_start = False
        self.broken_start = False
        self.extruder_ramp_deg_s = 30.0
        self.require_api_key = False
        self.subscribe_hold_ms = 0.0
        self.slow_first_frame_ms = 0.0
        self.webcam_down = False
        self.webcam_die_after = 0
        self.webcam_bridged_base = ""
        self.route_delay_ms = {}
        self.power_devices = []
        self.presets_value = {}
        self.gcode_stream_ms = 0
        self.fail_pause_script = False
        self.corrupt_frame_once = False
        self.temp_tick_deg_c = 0.0
        self.motion_speed_mm_s = 0.0
        self.motion_e_mm_s = 0.0
        self.fail_upload = False
        self.accept_pause_without_state = False
        self.clock_skew_ms = 0.0
        self.push_ledger = []
        self.push_seq = 0
        self.unknown_keys = []
        self._last_pushed = {}
        self.console_lines = [{"type": "response", "message": "// Klipper state: Ready",
                               "time": time.time()}]
        self.ledger = []
        self.inflight = 0

    def query_status(self, objects: Dict[str, Any]) -> Dict[str, Any]:
        """The faithful objects/query reply: only the NAMED objects,
        honouring per-object field lists (None = the whole object),
        as Moonraker does. The field-list honesty is load-bearing —
        the plugin's filtered aux refresh and the settings-survival
        merge can only be tested against a sim that filters."""
        status: Dict[str, Any] = {}
        for name, fields in (objects or {}).items():
            if name not in self.state:
                continue
            value = self.state[name]
            if fields is None or not isinstance(value, dict):
                status[name] = value
            else:
                status[name] = {field: value[field] for field in fields if field in value}
        return status

    def now(self) -> float:
        # The injected-clock seam: every wall-clock read the arms
        # stage goes through here, so the date-filter and timeout
        # scenarios shift time with clock_skew_ms instead of
        # hand-computed stamps.
        return time.time() + self.clock_skew_ms / 1000.0

    def push_patch(self) -> Dict[str, Any]:
        """One changed-objects frame, as Moonraker shapes it: only
        the objects whose values moved since the last frame."""
        if self.drop_next > 0:
            self.drop_next -= 1
            return {}
        if self.stall_ms:
            time.sleep(self.stall_ms / 1000.0)
        # During a print the interesting values move; idle they stay.
        stats = self.state["print_stats"]
        if stats.get("state") == "printing":
            sd = self.state["virtual_sdcard"]
            sd["progress"] = round(min(1.0, sd.get("progress", 0.0) + 0.001), 6)
            sd["file_position"] = int(sd.get("file_size", 0) * sd["progress"])
            stats["print_duration"] = round(float(stats.get("print_duration", 0.0)) + self.push_cadence_ms / 1000.0, 3)
            self.state["display_status"]["progress"] = sd["progress"]
            # The layer clock: the print crosses one layer every
            # ~6 s of printing (scenario 9 drives past a scheduled
            # pause without the printer pausing).
            info = stats.get("info") or {}
            if time.monotonic() - self._layer_clock_at > 6.0:
                self._layer_clock_at = time.monotonic()
                info["current_layer"] = (int(info.get("current_layer") or 0)) + 1
                info["total_layer"] = 40
                stats["info"] = info
        elif stats.get("state") == "error" and self.cold_start:
            # The cold-start error: Klipper refuses below the minimum
            # temperature while Moonraker ramps the heater; once the
            # target is reached the SAME job proceeds to printing
            # (the transient the verdict must not fire on). A broken
            # start never reaches the target.
            extruder = self.state["extruder"]
            target = float(extruder.get("target") or 0.0)
            temp = float(extruder.get("temperature") or 0.0)
            if temp < target and not self.broken_start:
                extruder["temperature"] = round(min(target, temp + self.extruder_ramp_deg_s * self.push_cadence_ms / 1000.0), 2)
            if temp >= target and not self.broken_start:
                stats["state"] = "printing"
                stats["message"] = ""
                self.cold_start = False
                self.state["virtual_sdcard"].update({"is_active": True})
        elif self.temp_tick_deg_c and stats.get("state") not in ("printing", "error"):
            # The autonomous temperature ticker: the chart scenarios
            # need a moving series WITHOUT a print running. Advances
            # the extruder and the bed every push at the armed rate.
            for name in ("extruder", "heater_bed"):
                entry = self.state.get(name)
                if entry and isinstance(entry, dict):
                    temp = float(entry.get("temperature") or 0.0)
                    entry["temperature"] = round(
                        temp + self.temp_tick_deg_c * self.push_cadence_ms / 1000.0, 2)
        elif self.motion_speed_mm_s and stats.get("state") == "printing":
            # The motion ticker (4.2.0): the motion rows' scenarios
            # need a moving head — advances the live positions and
            # sets the velocities from the armed rates. The sim
            # serves what is scripted: Klipper's 30 s E-trapq
            # history fallback is NOT modelled (it lands with the
            # travel-zeroing work if that ships).
            motion = self.state["motion_report"]
            step_s = self.push_cadence_ms / 1000.0
            motion["live_position"][0] = round(motion["live_position"][0] + self.motion_speed_mm_s * step_s, 4)
            motion["live_position"][3] = round(motion["live_position"][3] + self.motion_e_mm_s * step_s, 4)
            motion["live_velocity"] = self.motion_speed_mm_s
            motion["live_extruder_velocity"] = self.motion_e_mm_s
        # Every state object participates in the changes-only diff, not
        # just the original five — configfile, fan, gcode_move and the
        # rest ride the same contract (the pump used to drop them and
        # only the HTTP full-state query masked it). Deep-copied so the
        # stored last-frame never aliases the live state's dicts.
        snapshot = {name: json.loads(json.dumps(value)) if isinstance(value, (dict, list)) else value
                    for name, value in self.state.items()}
        patch = {name: snapshot[name] for name in snapshot
                 if self._last_pushed.get(name) != snapshot[name]}
        self._last_pushed.update(patch)
        return patch


class SimulatorWebSocket(tornado.websocket.WebSocketHandler):
    def initialize(self, printer: PrinterState) -> None:
        self._printer = printer
        self._subscribed: Dict[str, Any] = {}
        self._push_task: Optional[asyncio.Task] = None

    def check_origin(self, origin: str) -> bool:
        return True

    def prepare(self) -> None:
        # Real Moonraker enforces the API key on the websocket upgrade,
        # not only on HTTP — a missing key under the arm refuses the
        # handshake the way the host would.
        if self._printer.require_api_key and not self.request.headers.get("X-Api-Key"):
            self.set_status(401)
            self.finish(json.dumps({"error": {"code": 401, "message": "unauthorized"}}))

    def open(self) -> None:
        self.set_nodelay(True)
        self._printer.connections += 1
        self._printer.handlers.add(self)

    def on_close(self) -> None:
        self._printer.handlers.discard(self)
        if self._push_task is not None:
            self._push_task.cancel()

    def on_message(self, message) -> None:
        try:
            request = json.loads(message)
        except Exception:
            return
        method = request.get("method")
        entry = self._printer.record_begin("ws", method)
        params = request.get("params") or {}
        request_id = request.get("id")
        if method == "printer.objects.subscribe":
            self._subscribe(request_id, params)
        elif method == "printer.objects.query":
            self._respond(request_id, {"status": self._printer.query_status(params.get("objects") or {})})
        elif method == "printer.objects.list":
            self._respond(request_id, {"objects": list(self._printer.objects_available)})
        elif method == "server.info":
            self._respond(request_id, {"klippy_state": "ready", "components": ["klipper", "moonraker"],
                                       "moonraker_version": "0.13.0"})
        elif method == "printer.info":
            self._respond(request_id, {"state": self._printer.state["print_stats"]["state"],
                                       "state_message": self._printer.state["print_stats"].get("message", "")})
        elif method == "server.gcode_store":
            self._respond(request_id, {"gcode_store": list(self._printer.console_lines)})
        elif method == "server.webcams.list":
            stream = self._printer.webcam_bridged_base + "/webcam" if self._printer.webcam_bridged_base else "/webcam"
            self._respond(request_id, {"webcams": [{"name": "sim-cam", "location": "printer", "stream_url": stream}]})
        elif method == "printer.query_endstops.status":
            self._respond(request_id, {"x": "open", "y": "open", "z": "open"})
        elif method == "machine.device_power.devices":
            self._respond(request_id, {"devices": list(self._printer.power_devices)})
        elif method == "server.database.get_item":
            self._respond(request_id, {"value": self._printer.presets_value})
        else:
            self._respond(request_id, {"result": {}})
        self._printer.record_end(entry, 0)

    def _subscribe(self, request_id: int, params: Dict[str, Any]) -> None:
        if self._printer.refuse_subscribe:
            self._respond(request_id, None, {"code": 400, "message": self._printer.refuse_subscribe})
            return
        if self._printer.subscribe_hold_ms > 0:
            asyncio.create_task(self._subscribe_after_hold(request_id, params))
            return
        self._subscribe_now(request_id, params)

    async def _subscribe_after_hold(self, request_id: int, params: Dict[str, Any]) -> None:
        await asyncio.sleep(self._printer.subscribe_hold_ms / 1000.0)
        self._subscribe_now(request_id, params)

    def _subscribe_now(self, request_id: int, params: Dict[str, Any]) -> None:
        wanted = {name: None for name in (params.get("objects") or {})}
        self._subscribed = {name: dict(self._printer.state[name]) for name in wanted
                            if name in self._printer.state}
        # The subscribe reply carries the FULL state ONCE.
        self._respond(request_id, {"status": dict(self._subscribed),
                                   "eventtime": time.time()})
        # Prime the diff deep-copied (matching push_patch's own frame
        # copies) — a shallow prime aliases the live state's dicts and
        # in-place mutations would never read as changes again.
        self._printer._last_pushed.update(
            {name: json.loads(json.dumps(value)) for name, value in self._subscribed.items()})
        self._printer.subscription_wiped = False
        self._printer.subscribe_replied_at = time.monotonic()
        self._printer.subscribe_replied_wall = time.time()
        self._printer.first_push_after_reply_at = None
        if self._push_task is None:
            self._push_task = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        while True:
            await asyncio.sleep(self._printer.push_cadence_ms / 1000.0)
            if self._printer.subscription_wiped:
                continue
            patch = self._printer.push_patch()
            if not patch:
                continue
            # Pushes carry changed SUBSCRIBED objects only — a real
            # Moonraker never sends objects the client didn't ask for.
            patch = {name: value for name, value in patch.items() if name in self._subscribed}
            if not patch:
                continue
            if self._printer.subscribe_replied_at is not None and \
                    self._printer.first_push_after_reply_at is None:
                self._printer.first_push_after_reply_at = time.monotonic()
            frame = {"jsonrpc": "2.0", "method": "notify_status_update",
                     "params": [patch, time.time()]}
            payload = json.dumps(frame)
            # The push transcript: the peer-side record of what was
            # DELIVERED — frame number, timestamp, topics and a
            # payload digest. The chart round's assertions read this,
            # never the generator's ideal ramp (the round-2
            # CRITICAL-3: the sim recorded requests only).
            self._printer.push_ledger.append({
                "seq": self._printer.push_seq,
                "ts": frame["params"][1],
                "topics": sorted(patch.keys()),
                "digest": hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
            })
            self._printer.push_seq += 1
            try:
                if self._printer.corrupt_frame_once:
                    self._printer.corrupt_frame_once = False
                    self.write_message(b"\x00\xff\xfe garbage")
                else:
                    self.write_message(payload)
            except Exception:
                break

    def _respond(self, request_id: int, result: Any, error: Any = None) -> None:
        response = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            response["error"] = error
        else:
            response["result"] = result
        try:
            self.write_message(json.dumps(response))
        except Exception:
            pass


class StatusHandler(tornado.web.RequestHandler):
    def initialize(self, printer: PrinterState) -> None:
        self._printer = printer
        self._entry = None

    async def prepare(self) -> None:
        self._entry = self._printer.record_begin(self.request.method, self.request.path)
        delay = self._printer.service_delay(self.request.path)
        if delay:
            # Async on purpose: a held route must not stall the whole
            # sim (the websocket pump included) — the old time.sleep
            # froze every lane while one route was held.
            await tornado.gen.sleep(delay / 1000.0)
        if self._printer.require_api_key and not self.request.headers.get("X-Api-Key"):
            self.set_status(401)
            self.finish(json.dumps({"result": {}, "error": {"code": 401, "message": "unauthorized"}}))

    def on_finish(self) -> None:
        written = len(getattr(self, "_write_buffer", b"")) or 0
        self._printer.record_end(self._entry, written)

    async def get(self, path: str = "") -> None:
        # The real auth check: the plugin sends X-Api-Key on its own
        # origin; the simulator accepts the configured test key or none.
        self.set_header("Content-Type", "application/json")
        objects = ",".join(CORE_OBJECTS)
        if path == "objects/query":
            # The GET core poll names its objects as bare query
            # parameters (?print_stats&motion_report&...); reply with
            # exactly those, like Moonraker — the wholesale dump hid
            # the field-list dishonesty (the round-2 domain P3).
            objects = {name: None for name in self.request.query_arguments}
            self.write(json.dumps({"result": {"status": self._printer.query_status(objects)}}))
        elif path == "objects/list":
            self.write(json.dumps({"result": {"objects": list(self._printer.objects_available)}}))
        elif path == "info":
            self.write(json.dumps({"result": {"klippy_state": "ready", "components": ["klipper", "moonraker"]}}))
        elif path == "gcode_store":
            self.write(json.dumps({"result": {"gcode_store": list(self._printer.console_lines)}}))
        elif path == "webcams/list":
            stream = self._printer.webcam_bridged_base + "/webcam" if self._printer.webcam_bridged_base else "/webcam"
            self.write(json.dumps({"result": {"webcams": [{"name": "sim-cam", "stream_url": stream}]}}))
        elif path == "device_power/devices":
            self.write(json.dumps({"result": {"devices": list(self._printer.power_devices)}}))
        elif path == "database/item":
            self.write(json.dumps({"result": {"value": self._printer.presets_value}}))
        elif path.startswith("files/gcodes/"):
            self.set_header("Content-Type", "application/octet-stream")
            # Real Moonraker always declares the length from the
            # on-disk size; the client treats it as the transfer
            # authority (cap, progress, final check).
            self.set_header("Content-Length", str(len(self._printer.gcode_bytes)))
            cadence = self._printer.gcode_stream_ms
            if cadence > 0:
                # Stream the file in small chunks: the client's
                # download-progress signal (and the preview's
                # determinate bar) only advances while bytes arrive.
                for start in range(0, len(self._printer.gcode_bytes), 256):
                    self.write(self._printer.gcode_bytes[start:start + 256])
                    await self.flush()
                    await tornado.gen.sleep(cadence / 1000.0)
            else:
                self.write(self._printer.gcode_bytes)
        elif path == "files/metadata":
            filename = self.get_argument("filename", "")
            entry = next((item for item in self._printer.files
                          if item.get("filename") == filename), None)
            if entry is None:
                # Real Moonraker 404s unknown metadata.
                self.set_status(404)
                self.write(json.dumps({"error": {"code": 404,
                                                 "message": f"Metadata not available for {filename}"}}))
            else:
                self.write(json.dumps({"result": {**entry, "first_layer_height": 0.2,
                                                  "gcode_start_byte": 0}}))
        elif path == "files/directory":
            # The walker's extended listing: dirs, files with the
            # metadata fields directory_rows reads, and disk usage.
            self.write(json.dumps({"result": {
                "dirs": [],
                "files": self._printer.files,
                "disk_usage": {"total": 32 * 1024**3, "used": 4 * 1024**3, "free": 28 * 1024**3}}}))
        else:
            self.write(json.dumps({"result": {"status": self._printer.state, "objects": objects}}))

    def delete(self, path: str = "") -> None:
        # Moonraker's per-file delete: the file leaves the store so a
        # later walk reflects the removal (the plugin drops the row
        # client-side only on the host's success).
        self.set_header("Content-Type", "application/json")
        name = (path or "").rsplit("/", 1)[-1]
        self._printer.files = [entry for entry in self._printer.files
                               if entry.get("filename") != name]
        self.write(json.dumps({"result": "ok"}))

    def post(self, path: str = "") -> None:
        self.set_header("Content-Type", "application/json")
        try:
            body = json.loads(self.request.body or b"{}")
        except Exception:
            body = {}
        if path == "objects/query":
            self.write(json.dumps({"result": {"status": self._printer.query_status(body.get("objects") or {})}}))
        elif path == "emergency_stop":
            # The emergency: the printer cancels into an error state
            # and Klippy shuts down — the real host announces it (the
            # plugin's klippyLost path, the domain review).
            self._printer.scenario(
                print_stats={**self._printer.state["print_stats"],
                             "state": "error", "message": "Emergency stop",
                             "filename": ""},
                virtual_sdcard={**self._printer.state["virtual_sdcard"],
                                "is_active": False, "progress": 0.0})
            self._printer.emergency_count = getattr(self._printer, "emergency_count", 0) + 1
            frame = {"jsonrpc": "2.0", "method": "notify_klippy_shutdown", "params": [time.time()]}
            for handler in list(self._printer.handlers):
                try:
                    handler.write_message(json.dumps(frame))
                except Exception:
                    pass
            self.write(json.dumps({"result": "ok"}))
        elif path in ("print/pause", "print/resume", "print/cancel"):
            # The host's own state machine: the pause/resume/cancel
            # requests actually move print_stats, so the model's
            # canPausePrint/canResumePrint gates follow the flow.
            next_state = {"print/pause": "paused",
                          "print/resume": "printing",
                          "print/cancel": "cancelled"}[path]
            self._printer.scenario(print_stats={**self._printer.state["print_stats"],
                                                "state": next_state})
            self.write(json.dumps({"result": "ok"}))
        elif path == "files/move":
            # Moonraker's move actually renames the file in the store —
            # the next walk must show the new name (the contract the
            # DELETE handler already honours for removal).
            source = body.get("source") or self.get_argument("source", "")
            dest = body.get("dest") or self.get_argument("dest", "")
            src_name = source.rsplit("/", 1)[-1]
            dst_name = dest.rsplit("/", 1)[-1]
            for entry in self._printer.files:
                if entry.get("filename") == src_name:
                    entry["filename"] = dst_name
                    break
            self.write(json.dumps({"result": "ok"}))
        elif path == "gcode/script":
            # The command echoes into the console, like the real host.
            script = str((body.get("script") or "")).strip()
            if script:
                self._printer.console_lines.append(
                    {"type": "command", "message": script,
                     "time": self._printer.now()})
            if self._printer.fail_pause_script and script.strip().upper() == "PAUSE":
                # The missed-pause arm: the host refuses the command —
                # the controller must keep the entry, restyled.
                self.write(json.dumps({"result": {},
                                       "error": {"code": 400,
                                                 "message": "simulated PAUSE refusal"}}))
            else:
                self.write(json.dumps({"result": "ok"}))
                if script.strip().upper() == "PAUSE" and \
                        self._printer.state["print_stats"].get("state") == "printing" and \
                        not self._printer.accept_pause_without_state:
                    # Real ordering: the ack returns FIRST and the
                    # state transition lands on a later tick — Klipper
                    # expands the macro asynchronously. The plugin's
                    # confirmation must survive that window (the
                    # domain review's ordering fix).
                    def flip():
                        self._printer.scenario(
                            print_stats={**self._printer.state["print_stats"],
                                         "state": "paused"})
                    tornado.ioloop.IOLoop.current().add_callback(flip)
        elif path == "device_power/device":
            # The controls pane's toggle. Real Moonraker semantics (the
            # domain review): the locked refusal fires ONLY while a
            # print is active — in standby a locked device toggles
            # fine — a no-op action is a 400, and the reply is keyed
            # by the device's name.
            name = str(body.get("device") or "")
            action = str(body.get("action") or "")
            device = next((item for item in self._printer.power_devices
                           if item.get("device") == name), None)
            printing = self._printer.state["print_stats"].get("state") in ("printing", "paused")
            if device is None:
                self.write(json.dumps({"result": {},
                                       "error": {"code": 404, "message": "unknown device"}}))
            elif str(device.get("status")) == action:
                self.write(json.dumps({"result": {},
                                       "error": {"code": 400,
                                                 "message": f"Device '{name}' already {action}"}}))
            elif device.get("locked_while_printing") and printing and not body.get("force"):
                self.write(json.dumps({"result": {},
                                       "error": {"code": 403, "message": "locked while printing"}}))
            else:
                device["status"] = "on" if action == "on" else "off"
                self.write(json.dumps({"result": {name: device}}))
        elif path in ("restart", "firmware_restart"):
            # The System section's restarts. A firmware restart cycles
            # Klippy — the host broadcasts klippy_ready on the other
            # side, which is the watchdog-heal path's input.
            if path == "firmware_restart":
                self._printer.klippy_restart()
            self.write(json.dumps({"result": "ok"}))
        elif path == "print/start":
            filename = self.get_argument("filename", "sim.gcode")
            if self._printer.cold_start:
                # The real cold start: Moonraker accepts the job and
                # Klipper reports the transient extrude error until
                # the heater reaches target — the print proceeds.
                self._printer.scenario(
                    print_stats={**self._printer.state["print_stats"],
                                 "state": "error", "message": "Extrude below minimum temp",
                                 "filename": filename},
                    extruder={**self._printer.state["extruder"], "target": 210.0})
                self._printer.state["virtual_sdcard"].update({"is_active": True, "progress": 0.0,
                                                              "file_size": len(self._printer.gcode_bytes)})
            else:
                self._printer.scenario(print_stats={**self._printer.state["print_stats"],
                                                    "state": "printing", "filename": filename})
                self._printer.state["virtual_sdcard"].update({"is_active": True, "progress": 0.0,
                                                              "file_size": len(self._printer.gcode_bytes)})
            self.write(json.dumps({"result": "ok"}))
        elif path == "files/upload":
            # The route strips the server/ prefix — the handler must
            # match the STRIPPED form, like every sibling branch. The
            # prefixed match never fired: uploads fell through to the
            # catch-all's bare ok, the honest body was unreachable
            # and fail_upload could never refuse anything.
            # The upload lane, honestly shaped: refused or accepted
            # (holds ride the route_delay_ms lane in prepare — it is
            # async-safe, a blocking sleep would freeze the pump).
            # The filename rides the file part's Content-Disposition
            # (the plugin's multipart shape).
            if self._printer.fail_upload:
                self.set_status(400)
                self.write(json.dumps({"result": {},
                                       "error": {"code": 400,
                                                 "message": "simulated upload refusal"}}))
                return
            raw = self.request.body or b""
            match = re.search(rb'name="file"; filename="([^"]+)"', raw)
            filename = match.group(1).decode("utf-8", "replace") if match else "upload.gcode"
            self._printer.files = [entry for entry in self._printer.files
                                   if entry.get("filename") != filename]
            self._printer.files.append({
                "filename": filename, "modified": self._printer.now(),
                "size": len(raw), "permissions": "rw",
                "slicer": "sim-upload", "estimated_time": 600.0,
                "layer_height": 0.2, "filament_total": 1.0,
                "uuid": "sim-upload-uuid", "job_id": None, "print_start_time": None})
            # The honest upload outcome: the terminal verdict reads
            # this body (print_started/print_queued are distinct), so
            # the sim answers the real shape, never a bare ok.
            self.write(json.dumps({"result": {"print_started": False,
                                              "print_queued": False}}))
        else:
            self.write(json.dumps({"result": "ok"}))


class ControlHandler(tornado.web.RequestHandler):
    """The runner's control lane: arm fault/lifecycle state and read
    the printer back. Harness-only routes, never part of Moonraker's
    protocol."""

    def initialize(self, printer: PrinterState) -> None:
        self._printer = printer

    def post(self, path: str = "") -> None:
        self.set_header("Content-Type", "application/json")
        if path == "scenario":
            try:
                body = json.loads(self.request.body or b"{}")
            except Exception:
                body = {}
            if body.pop("webcam_bridged", False):
                # The container's own (non-loopback) IP plus this
                # request's port: the exact URL the bridge will fetch.
                import socket
                ip = socket.gethostbyname(socket.gethostname())
                port = str(self.request.host).rsplit(":", 1)[-1]
                self._printer.webcam_bridged_base = f"http://{ip}:{port}"
            self._printer.unknown_keys = []
            self._printer.scenario(**body)
            self.write(json.dumps({"result": "ok",
                                   "unknown": list(self._printer.unknown_keys)}))
        elif path == "drop_connections":
            for handler in list(self._printer.handlers):
                try:
                    handler.close()
                except Exception:
                    pass
            self.write(json.dumps({"result": "ok"}))
        elif path == "klippy_restart":
            self._printer.klippy_restart()
            self.write(json.dumps({"result": "ok"}))
        elif path == "reset":
            self._printer.reset()
            self.write(json.dumps({"result": "ok"}))
        else:
            self.write(json.dumps({"result": "ok"}))

    def get(self, path: str = "") -> None:
        self.set_header("Content-Type", "application/json")
        if path == "pushes":
            # The push transcript: what the socket DELIVERED (the
            # chart round's peer-side assertions read this).
            self.write(json.dumps({"entries": self._printer.push_ledger}))
            return
        result = dict(self._printer.state)
        result.update({
            "connections": self._printer.connections,
            "cold_start": self._printer.cold_start,
            "broken_start": self._printer.broken_start,
            "webcam_streams": self._printer.webcam_streams,
            "require_api_key": self._printer.require_api_key,
            "emergency_count": getattr(self._printer, "emergency_count", 0),
            "subscribe_replied_at": self._printer.subscribe_replied_at,
            "subscribe_replied_wall": self._printer.subscribe_replied_wall,
            "first_push_after_reply_at": self._printer.first_push_after_reply_at})
        self.write(json.dumps({"result": result}))


class MJPEGHandler(tornado.web.RequestHandler):
    """A real multipart MJPEG stream: the changing test-pattern
    frames prove liveness in the harness's captures (scenario 4)."""

    def initialize(self, printer: PrinterState) -> None:
        self._printer = printer

    async def get(self) -> None:
        if not self._printer.webcam_frames or self._printer.webcam_down:
            self.set_status(404)
            self.finish()
            return
        self._printer.webcam_streams += 1
        self.set_header("Content-Type", "multipart/x-mixed-replace; boundary=mpfboundary")
        self.set_header("Cache-Control", "no-store")
        first = True
        index = 0
        while True:
            frame = self._printer.webcam_frames[index % len(self._printer.webcam_frames)]
            index += 1
            # The mid-stream death arm: the connection closes after N
            # frames, the way a camera dying mid-print reads to the
            # client (the recovering-state scenario's trigger).
            if self._printer.webcam_die_after and index > self._printer.webcam_die_after:
                return
            if first:
                first = False
                if self._printer.slow_first_frame_ms:
                    await asyncio.sleep(self._printer.slow_first_frame_ms / 1000.0)
            part = (b"--mpfboundary\r\nContent-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
            try:
                self.write(part)
                await self.flush()
            except Exception:
                return
            await asyncio.sleep(0.25)


class LedgerHandler(tornado.web.RequestHandler):
    def initialize(self, printer: PrinterState) -> None:
        self._printer = printer

    def get(self) -> None:
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"result": self._printer.ledger_stats(),
                               "entries": self._printer.ledger[-100:]}))


def make_app(printer: Optional[PrinterState] = None) -> tornado.web.Application:
    printer = printer or PrinterState()
    routes: List[Any] = [
        (r"/websocket", SimulatorWebSocket, {"printer": printer}),
        (r"/webcam", MJPEGHandler, {"printer": printer}),
        (r"/ledger", LedgerHandler, {"printer": printer}),
        (r"/harness/(.*)", ControlHandler, {"printer": printer}),
        (r"/printer/(.*)", StatusHandler, {"printer": printer}),
        (r"/server/(.*)", StatusHandler, {"printer": printer}),
        (r"/machine/(.*)", StatusHandler, {"printer": printer}),
    ]
    return tornado.web.Application(routes)


def serve(port: int = 0, printer: Optional[PrinterState] = None) -> "Simulator":
    return Simulator(port, printer)


class Simulator:
    """A running simulator: one process-local server for tests and the
    runner's scenario control."""

    def __init__(self, port: int = 0, printer: Optional[PrinterState] = None) -> None:
        self.printer = printer or PrinterState()
        self.app = make_app(self.printer)
        self.server = tornado.httpserver.HTTPServer(self.app)
        # All interfaces: the bridged-webcam arm serves the stream on the
        # container's own (non-loopback) IP so the plugin's key-carrying
        # bridge path is exercised for real (the camera path).
        sockets = tornado.netutil.bind_sockets(port, "0.0.0.0")
        self.server.add_sockets(sockets)
        self.port = sockets[0].getsockname()[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        # In-process tests drive the IOLoop themselves; the runner
        # background-threads it.
        self._thread = None

    def scenario(self, **changes) -> None:
        self.printer.scenario(**changes)
