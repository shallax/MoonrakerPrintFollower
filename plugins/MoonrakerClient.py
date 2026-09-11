from __future__ import annotations

import time
from typing import Any, Dict, Iterable, Optional

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal

from .MoonrakerProtocol import CORE_OBJECTS, status_endpoint, websocket_endpoint
from .MoonrakerSession import MoonrakerSession, MoonrakerSessionState, RequestCategory


class MoonrakerClient(QObject):
    """Shared resilient HTTP-only core poller over one MoonrakerSession."""

    statusReceived = pyqtSignal(object)
    connectionChanged = pyqtSignal(bool, str)
    capabilitiesChanged = pyqtSignal(object)
    commandChanged = pyqtSignal(object)
    sessionInvalidated = pyqtSignal()

    RETRY_DELAYS_MS = (1000, 2000, 5000, 10000, 30000)

    def __init__(self, parent=None, session=None, transport=None, *, socket=None, proof_timeout_ms: int = 8000) -> None:
        super().__init__(parent)
        self._base_url = ""
        self._api_key = ""
        self._poll_interval_ms = 750
        self._enabled = False
        self._connected = False
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        self._generation = 0
        self._aux_names: set = set()
        self._effective_feed_mode = "http"
        self._last_applied_stamp = 0.0
        self._proof_timer = QTimer(self)
        self._proof_timer.setSingleShot(True)
        self._proof_timer.setInterval(max(10, int(proof_timeout_ms)))
        if isinstance(session, MoonrakerSession):
            self._session = session
        else:
            state = session if isinstance(session, MoonrakerSessionState) else None
            self._session = MoonrakerSession(self, state=state, transport=transport, socket=socket)
        self._capabilities: Dict[str, Any] = {
            "objects": [],
            "current_layer": False,
            "file_position": False,
            "motion_report": False,
        }
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setSingleShot(False)
        self._poll_timer.setInterval(self._poll_interval_ms)
        self._poll_timer.timeout.connect(lambda: self._refresh(force=False))
        self._command_timer = QTimer(self)
        self._command_timer.setInterval(100)
        self._command_timer.timeout.connect(self.expire_commands)

    @property
    def session(self) -> MoonrakerSession:
        return self._session

    def set_trace_http(self, enabled) -> None:
        self._session.transport.set_trace_http(enabled)

    @property
    def transport(self):
        return self._session.transport

    @property
    def transport_metrics(self) -> Dict[str, Dict[str, float | int]]:
        return self._session.transport.metrics

    @property
    def connected(self) -> bool:
        return bool(self._connected)

    @property
    def effective_feed_mode(self) -> str:
        """What the feed actually runs on — configured mode unless the
        startup proof or a subscribe refusal degraded it to HTTP."""
        return self._effective_feed_mode

    @property
    def configured_feed_mode(self) -> str:
        return self._session.feed_mode

    @property
    def capabilities(self) -> Dict[str, Any]:
        return dict(self._capabilities)

    @property
    def status(self) -> Dict[str, Any]:
        return self._session.snapshot.copy_status()

    def configure(self, base_url: str, api_key: str, poll_interval_ms: int, *, feed_mode=None) -> None:
        new_base_url = str(base_url or "").rstrip("/")
        new_api_key = str(api_key or "")
        try:
            new_interval = max(1, int(poll_interval_ms))
        except (TypeError, ValueError):
            new_interval = 750
        endpoint_changed = (new_base_url, new_api_key) != (self._base_url, self._api_key)
        mode_changed = feed_mode is not None and str(feed_mode).strip().lower() != self._session.feed_mode
        rebind = endpoint_changed or mode_changed
        was_enabled = self._enabled
        if rebind:
            # Synchronous UI-thread subscribers cancel streaming uploads and
            # deferred work while the transport still has the OLD identity.
            # The emit is unconditional: configure is the rebind point, and
            # subscribers must tear down even when the poller was not
            # enabled (an upload can be in flight without polling). Callers
            # that want no second wave (PrinterBinding) stop with
            # reset_session=False beforehand.
            self.stop(reset_session=False)
            self.sessionInvalidated.emit()
        self._base_url = new_base_url
        self._api_key = new_api_key
        self._poll_interval_ms = new_interval
        if rebind:
            self._session.configure(new_base_url, new_api_key, feed_mode)
        self._effective_feed_mode = self._session.feed_mode
        self._apply_adaptive_interval()
        if rebind and was_enabled:
            self.start()

    def start(self) -> None:
        if self._enabled:
            return
        self._generation += 1
        self._enabled = True
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        self._capabilities.update({
            "objects": [],
            "current_layer": False,
            "file_position": False,
            "motion_report": False,
        })
        self.capabilitiesChanged.emit(dict(self._capabilities))
        self._effective_feed_mode = self._session.feed_mode
        self._last_applied_stamp = 0.0
        self._apply_adaptive_interval()
        self._poll_timer.start()
        self.connectionChanged.emit(False, "Connecting to Moonraker")
        if self._effective_feed_mode == "websocket":
            self._start_socket()
        else:
            self.force_refresh()

    def _start_socket(self) -> None:
        socket = self._session.socket
        generation = self._generation

        def on_sync(status, stamp):
            self.admit_status(status, origin="sync", stamp=float(stamp), generation=generation)

        def on_failed(reason):
            if generation != self._generation:
                return
            self._handle_failure(f"WebSocket feed failed: {reason}")

        def on_refused(error):
            if generation != self._generation:
                return
            message = str((error or {}).get("message") or "")
            if message.lower() == "unauthorized":
                self._handle_failure("WebSocket feed: the API key was rejected")
                return
            # A structured subscribe refusal is a terminal capability
            # failure, not a link failure: degrade the feed to HTTP
            # WITHOUT a session reset (the automatic-fallback ruling).
            self._effective_feed_mode = "http"
            self.connectionChanged.emit(
                False,
                "This Moonraker refused the status subscription; using HTTP polling",
            )
            self.force_refresh()

        def on_klippy_ready():
            # Moonraker wipes every client subscription on a Klippy
            # restart (F1): the ready broadcast is the re-subscribe
            # trigger, and the previous print is definitively over (F11).
            if generation != self._generation:
                return
            self._session.state.assume_print_stopped = False
            self._subscribe()

        def on_klippy_lost(what):
            if generation != self._generation:
                return
            self._handle_failure(f"Klippy lost the connection ({what})")

        socket.syncSnapshot.connect(on_sync)
        socket.failed.connect(on_failed)
        socket.subscribeRefused.connect(on_refused)
        socket.klippyReady.connect(on_klippy_ready)
        socket.klippyLost.connect(on_klippy_lost)
        socket.upgraded.connect(lambda: self._subscribe())
        socket.start(
            websocket_endpoint(self._base_url),
            self._api_key,
            set(CORE_OBJECTS),
            set(self._aux_names),
        )
        # The startup proof (the fallback-on-silence ruling): real data
        # within the window, or the feed degrades to HTTP with a reason.
        self._proof_timer.timeout.connect(lambda: self._proof_failed(generation))
        self._proof_timer.start()

    def _proof_failed(self, generation: int) -> None:
        if generation != self._generation or self._effective_feed_mode != "websocket":
            return
        if self._last_applied_stamp:
            return  # data already arrived; the proof passed
        self._effective_feed_mode = "http"
        self._session.socket.stop()
        self.connectionChanged.emit(
            False, "WebSocket feed stayed silent; using HTTP polling"
        )
        self.force_refresh()

    def _subscribe(self) -> None:
        socket = self._session.socket
        objects = {name: None for name in sorted(set(CORE_OBJECTS) | set(self._aux_names))}
        socket.subscribe(objects)

    def set_auxiliary_objects(self, names: set) -> None:
        """The Monitor's wanted set feeds the merged subscription (A8/F5);
        a membership change re-issues the one subscription."""
        if set(names) == self._aux_names:
            return
        self._aux_names = set(names)
        if self._enabled and self._effective_feed_mode == "websocket":
            self._subscribe()

    def drain_aux(self):
        """The Monitor's auxiliary timer drains this accumulator in
        websocket mode — the socket is a source, not a clock (A11)."""
        if self._effective_feed_mode != "websocket":
            return None, 0.0
        return self._session.socket.drain_aux()

    def stop(self, *, reset_session: bool = True) -> None:
        self._generation += 1
        self._enabled = False
        self._poll_timer.stop()
        self._command_timer.stop()
        self._proof_timer.stop()
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        if reset_session:
            self.sessionInvalidated.emit()
        self._session.coalescer.cancel(RequestCategory.CORE.value)
        self._session.transport.cancel_owner("core")
        self._session.socket.stop()
        was_connected = self._connected
        self._connected = False
        self._session.connected = False
        if reset_session:
            self._session.reset()
        if was_connected:
            self.connectionChanged.emit(False, "Moonraker polling stopped")

    def set_toolhead_guard(self, active: bool) -> None:
        changed = self._session.set_toolhead_guard(active)
        self._apply_adaptive_interval()
        if changed and active and self._enabled:
            self.force_refresh()

    def set_pause_guard(self, active: bool) -> None:
        changed = self._session.set_pause_guard(active)
        self._apply_adaptive_interval()
        if changed and active and self._enabled:
            self.force_refresh()

    def force_refresh(self) -> None:
        self._refresh(force=True)

    def _refresh(self, *, force: bool) -> None:
        if not self._enabled or not self._base_url:
            return
        if time.monotonic() < self._retry_not_before:
            return
        if self._effective_feed_mode == "websocket":
            self._drain_socket_feed(force)
            return
        key = RequestCategory.CORE.value
        if not self._session.coalescer.begin(key, force=force):
            return
        generation = self._generation
        issued_at = time.monotonic()
        started = self._session.transport.send_json(
            "core",
            "status",
            "GET",
            status_endpoint(self._base_url),
            lambda payload, error, g=generation, s=issued_at: self._handle_http_status(payload, error, g, s),
            category=RequestCategory.CORE.value,
        )
        if not started:
            self._session.coalescer.complete(key)

    def _drain_socket_feed(self, force: bool) -> None:
        """The delivery clock's websocket tick: drain the core accumulator
        on the SAME timer and policy as the HTTP poll (A11). Reconnects
        while the socket is down, respecting the retry ladder."""
        socket = self._session.socket
        if not socket.is_upgraded:
            if force or time.monotonic() >= self._retry_not_before:
                self._start_socket()
            return
        patch, stamp = socket.drain_core()
        if patch:
            self.admit_status(patch, origin="fragment", stamp=stamp, generation=self._generation)

    def _queue_refresh(self, generation: int) -> None:
        def refresh() -> None:
            if generation == self._generation and self._enabled:
                self.force_refresh()
        QTimer.singleShot(0, refresh)

    def assume_print_stopped(self) -> None:
        """The e-stop's assumption (the author's ruling): the print is
        over until the printer reports otherwise. The CURRENT snapshot
        re-emits immediately with the assumed state so every consumer
        re-evaluates now, not at the next poll — which may never come
        if the stop wedged Moonraker."""
        state = self._session.state
        if state.assume_print_stopped:
            return
        state.assume_print_stopped = True
        merged = self._session.snapshot.copy_status()
        if merged and str((merged.get("print_stats") or {}).get("state") or "").lower() in {"printing", "paused"}:
            merged["print_stats"]["state"] = "cancelled"
            self.statusReceived.emit(merged)

    def _handle_http_status(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
        generation: int,
        issued_at: float,
    ) -> None:
        key = RequestCategory.CORE.value
        if generation != self._generation:
            return
        try:
            if error:
                self._handle_failure(f"Moonraker request failed: {error}")
                return
            result = (payload or {}).get("result")
            status = result.get("status") if isinstance(result, dict) else None
            if not isinstance(status, dict) or any(not isinstance(value, dict) for value in status.values()):
                self._handle_failure("Moonraker returned an invalid status response")
                return
            # An HTTP response is a complete snapshot: admit it as a sync
            # stamped when the request was ISSUED, not when it landed.
            self.admit_status(status, origin="sync", stamp=issued_at, generation=generation)
        except Exception as exc:
            self._handle_failure(f"Moonraker status error: {exc}")
        finally:
            # Signal subscribers may rebind synchronously. Never complete the
            # coalescer slot belonging to their new generation.
            if generation == self._generation:
                follow_up = self._session.coalescer.complete(key)
                if follow_up and self._enabled and not self._retry_delay_ms:
                    self._queue_refresh(generation)

    def admit_status(self, patch: Dict[str, Any], *, origin: str, stamp: float, generation: int) -> None:
        """The single admission point for both feeds (A2/A7).

        Ordering: a sync (a complete object set) applies whole or is
        dropped whole when a newer write is already applied; a fragment
        always applies within its socket generation. The e-stop
        assumption rewrites the merged status at this one site, whatever
        the source.
        """
        if generation != self._generation:
            return
        try:
            stamp = float(stamp)
        except (TypeError, ValueError):
            stamp = time.monotonic()
        if origin == "sync" and stamp <= self._last_applied_stamp:
            return
        merged, changed_commands = self._session.merge_status(patch)
        self._last_applied_stamp = max(self._last_applied_stamp, stamp)
        self._handle_success()
        if generation != self._generation:
            return
        self._apply_adaptive_interval()
        self._update_status_capabilities(merged)
        if generation != self._generation:
            return
        if self._session.state.assume_print_stopped:
            # The e-stop's assumption (the author's ruling): until
            # the printer reports a real non-printing state, every
            # emitted status reads as cancelled — the monitor's
            # guards, the jog gate AND the follower's coordinator
            # all consume this one observation and need no
            # per-consumer conditionals.
            state = str((merged.get("print_stats") or {}).get("state") or "").lower()
            if state in {"printing", "paused"}:
                merged["print_stats"]["state"] = "cancelled"
            else:
                self._session.state.assume_print_stopped = False
        self.statusReceived.emit(merged)
        for command in changed_commands:
            if generation != self._generation:
                break
            self.commandChanged.emit(command.as_dict())

    def _apply_adaptive_interval(self) -> None:
        interval = self._session.poll_policy.interval_ms(
            RequestCategory.CORE,
            self._poll_interval_ms,
            self._session.snapshot.printer_state,
            urgent=self._session.pause_guard or self._session.toolhead_guard,
        )
        interval = max(interval, self._retry_delay_ms)
        if self._poll_timer.interval() != interval:
            self._poll_timer.setInterval(interval)

    def _handle_success(self) -> None:
        self._retry_index = 0
        self._retry_delay_ms = 0
        self._retry_not_before = 0.0
        self._session.connected = True
        if not self._connected:
            self._connected = True
            self.connectionChanged.emit(True, "Moonraker connected")

    def _handle_failure(self, reason: str) -> None:
        delay = self.RETRY_DELAYS_MS[min(self._retry_index, len(self.RETRY_DELAYS_MS) - 1)]
        self._retry_index = min(self._retry_index + 1, len(self.RETRY_DELAYS_MS) - 1)
        adaptive = self._session.poll_policy.interval_ms(
            RequestCategory.CORE,
            self._poll_interval_ms,
            self._session.snapshot.printer_state,
            urgent=self._session.pause_guard or self._session.toolhead_guard,
        )
        retry_interval = max(adaptive, delay)
        self._retry_delay_ms = retry_interval
        self._retry_not_before = time.monotonic() + retry_interval / 1000.0
        if self._poll_timer.interval() != retry_interval:
            self._poll_timer.setInterval(retry_interval)
        if self._enabled:
            # Backoff starts at failure, not at the previous request's start.
            # A repeating/coarse timer could otherwise fire before the deadline
            # and postpone the next retry by a whole extra polling interval.
            self._poll_timer.start()
        self._connected = False
        self._session.connected = False
        self.connectionChanged.emit(False, f"{reason}; retrying in {retry_interval / 1000:g}s")

    def track_command(self, name: str, expected_states: Iterable[str] = (), *, timeout_s: float = 10.0) -> None:
        command = self._session.commands.issue(name, expected_states, timeout_s=timeout_s)
        self._command_timer.start()
        self.commandChanged.emit(command.as_dict())

    def accept_command(self, name: str) -> None:
        command = self._session.commands.accepted(name)
        if command is not None:
            self.commandChanged.emit(command.as_dict())
        self.force_refresh()

    def fail_command(self, name: str, detail: str) -> None:
        command = self._session.commands.failed(name, detail)
        if command is not None:
            self.commandChanged.emit(command.as_dict())

    def expire_commands(self) -> None:
        generation = self._generation
        changed = self._session.commands.expire()
        for command in changed:
            if generation != self._generation:
                return
            self.commandChanged.emit(command.as_dict())
        if not self._session.commands.has_pending:
            self._command_timer.stop()

    def _update_status_capabilities(self, status: Optional[Dict[str, Any]] = None) -> None:
        status = status if isinstance(status, dict) else self._session.snapshot.copy_status()
        objects = set(self._capabilities.get("objects") or [])
        objects.update(str(key) for key in status.keys())
        self._capabilities["objects"] = sorted(objects)
        print_stats = status.get("print_stats") or {}
        info = print_stats.get("info") if isinstance(print_stats, dict) else {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        self._capabilities["current_layer"] = isinstance(info, dict) and info.get("current_layer") is not None
        self._capabilities["file_position"] = isinstance(virtual_sdcard, dict) and virtual_sdcard.get("file_position") is not None
        self._capabilities["motion_report"] = "motion_report" in status
        self.capabilitiesChanged.emit(dict(self._capabilities))
