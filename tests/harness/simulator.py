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
import json
import os
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
    "motion_report": {"live_position": [100.0, 100.0, 0.4, 0.0]},
    "display_status": {"message": "", "progress": 0.0},
    "toolhead": {"homed_axes": "xyz"},
    "heater_bed": {"temperature": 0.0, "target": 0.0},
    "extruder": {"temperature": 0.0, "target": 0.0},
    "system_stats": {"sysload": 0.1, "memavail": 1000000},
    "fan": {"speed": 0.0},
    # The plugin subscribes to configfile for the save-config surface
    # (saveConfigPending) and to bed_mesh for the mesh render — a
    # faithful sim must carry both objects even while empty.
    "configfile": {"save_config_pending": False, "save_config_pending_items": {}},
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
        self.webcam_down = False  # the stream-failure arm: /webcam 404s
        self.webcam_die_after = 0  # serve N frames then close mid-stream
        self.slow_first_frame_ms = 0.0
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
             "print_start_time": None},
            {"filename": "benchy.gcode", "modified": time.time() - 7200.0,
             "size": len(self.gcode_bytes), "permissions": "rw",
             "slicer": "MoonrakerPrintFollower-sim", "estimated_time": 1800.0,
             "layer_height": 0.2, "filament_total": 6.0,
             "print_start_time": None},
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
        for prefix, delay in self.route_delay_ms.items():
            if path.startswith(prefix):
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
        self.route_delay_ms = {}
        self._last_pushed = {}
        self.console_lines = [{"type": "response", "message": "// Klipper state: Ready",
                               "time": time.time()}]
        self.ledger = []
        self.inflight = 0

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
            self._respond(request_id, {"status": {name: dict(self._printer.state[name])
                                                  for name in params.get("objects", {})
                                                  if name in self._printer.state}})
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
            self._respond(request_id, {"webcams": [{"name": "sim-cam", "location": "printer", "stream_url": "/webcam"}]})
        elif method == "printer.query_endstops.status":
            self._respond(request_id, {"x": "open", "y": "open", "z": "open"})
        elif method == "machine.device_power.devices":
            self._respond(request_id, {"devices": []})
        elif method == "server.database.get_item":
            self._respond(request_id, {"value": {}})
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
            try:
                self.write_message(json.dumps(frame))
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

    def prepare(self) -> None:
        self._entry = self._printer.record_begin(self.request.method, self.request.path)
        delay = self._printer.service_delay(self.request.path)
        if delay:
            time.sleep(delay / 1000.0)
        if self._printer.require_api_key and not self.request.headers.get("X-Api-Key"):
            self.set_status(401)
            self.finish(json.dumps({"result": {}, "error": {"code": 401, "message": "unauthorized"}}))

    def on_finish(self) -> None:
        written = len(getattr(self, "_write_buffer", b"")) or 0
        self._printer.record_end(self._entry, written)

    def get(self, path: str = "") -> None:
        # The real auth check: the plugin sends X-Api-Key on its own
        # origin; the simulator accepts the configured test key or none.
        self.set_header("Content-Type", "application/json")
        objects = ",".join(CORE_OBJECTS)
        if path == "objects/query":
            self.write(json.dumps({"result": {"status": self._printer.state}}))
        elif path == "objects/list":
            self.write(json.dumps({"result": {"objects": list(self._printer.objects_available)}}))
        elif path == "info":
            self.write(json.dumps({"result": {"klippy_state": "ready", "components": ["klipper", "moonraker"]}}))
        elif path == "gcode_store":
            self.write(json.dumps({"result": {"gcode_store": list(self._printer.console_lines)}}))
        elif path == "webcams/list":
            self.write(json.dumps({"result": {"webcams": [{"name": "sim-cam", "stream_url": "/webcam"}]}}))
        elif path == "device_power/devices":
            self.write(json.dumps({"result": {"devices": []}}))
        elif path.startswith("files/gcodes/"):
            self.set_header("Content-Type", "application/octet-stream")
            self.write(self._printer.gcode_bytes)
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
            wanted = (body.get("objects") or {}).keys()
            status = {name: self._printer.state[name] for name in wanted if name in self._printer.state}
            self.write(json.dumps({"result": {"status": status}}))
        elif path == "emergency_stop":
            # The emergency: the printer cancels into an error state
            # and Moonraker drops the connection (the plugin's
            # reconnect-after-emergency path).
            self._printer.scenario(
                print_stats={**self._printer.state["print_stats"],
                             "state": "error", "message": "Emergency stop",
                             "filename": ""},
                virtual_sdcard={**self._printer.state["virtual_sdcard"],
                                "is_active": False, "progress": 0.0})
            self._printer.emergency_count = getattr(self._printer, "emergency_count", 0) + 1
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
                                                              "file_size": 1048576})
            else:
                self._printer.scenario(print_stats={**self._printer.state["print_stats"],
                                                    "state": "printing", "filename": filename})
                self._printer.state["virtual_sdcard"].update({"is_active": True, "progress": 0.0})
            self.write(json.dumps({"result": "ok"}))
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
            self._printer.scenario(**body)
            self.write(json.dumps({"result": "ok"}))
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
        sockets = tornado.netutil.bind_sockets(port, "127.0.0.1")
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
