"""The Moonraker simulator: the harness's network peer.

Speaks the REAL protocols over real TCP, in both transports — the
websocket subscribe/push contract (full snapshot once, changes only
afterward, subscriptions wiped on klippy_ready) and the HTTP lanes the
plugin calls. A scripted Klipper state machine drives the pushes;
fault arms (stalls, drops, refusals, closes) inject the failure modes
the tier-1 scenarios need. Moonraker is what the PRINTER runs, never
what Cura runs — this double is the one permitted fake.
"""
from __future__ import annotations

import asyncio
import json
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

    def push_patch(self) -> Dict[str, Any]:
        """One changed-objects frame, as Moonraker shapes it."""
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
        return {"print_stats": dict(stats), "virtual_sdcard": dict(self.state["virtual_sdcard"]),
                "display_status": dict(self.state["display_status"])}


class SimulatorWebSocket(tornado.websocket.WebSocketHandler):
    def initialize(self, printer: PrinterState) -> None:
        self._printer = printer
        self._subscribed: Dict[str, Any] = {}
        self._push_task: Optional[asyncio.Task] = None

    def check_origin(self, origin: str) -> bool:
        return True

    def open(self) -> None:
        self.set_nodelay(True)
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
            self._respond(request_id, {"gcode_store": [{"type": "response", "message": "// Klipper state: Ready",
                                                        "time": time.time()}]})
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
        wanted = {name: None for name in (params.get("objects") or {})}
        self._subscribed = {name: dict(self._printer.state[name]) for name in wanted
                            if name in self._printer.state}
        # The subscribe reply carries the FULL state ONCE.
        self._respond(request_id, {"status": dict(self._subscribed),
                                   "eventtime": time.time()})
        self._printer.subscription_wiped = False
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
            self.write(json.dumps({"result": {"gcode_store": []}}))
        elif path == "webcams/list":
            self.write(json.dumps({"result": {"webcams": [{"name": "sim-cam", "stream_url": "/webcam"}]}}))
        elif path == "device_power/devices":
            self.write(json.dumps({"result": {"devices": []}}))
        else:
            self.write(json.dumps({"result": {"status": self._printer.state, "objects": objects}}))

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
        elif path == "print/start":
            self._printer.scenario(print_stats={**self._printer.state["print_stats"],
                                                "state": "printing", "filename": self.get_argument("filename", "sim.gcode")})
            self._printer.state["virtual_sdcard"].update({"is_active": True, "progress": 0.0})
            self.write(json.dumps({"result": "ok"}))
        else:
            self.write(json.dumps({"result": "ok"}))


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
        (r"/ledger", LedgerHandler, {"printer": printer}),
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
