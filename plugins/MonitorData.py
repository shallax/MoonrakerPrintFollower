"""Active Monitor request/poll lifecycle and immutable data projections."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from .ConsolePolicy import MAX_LINE
from .MonitorFormatting import number, result, wanted_object
from .MoonrakerSession import RequestCategory


def freeze(value):
    if isinstance(value, dict): return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)): return tuple(freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class MonitorSnapshot:
    core: Mapping
    auxiliary: Mapping
    objects: tuple
    server: Mapping
    printer: Mapping
    power: tuple
    webcams: tuple
    presets: Mapping
    endstops: Mapping


class MonitorData(QObject):
    changed = pyqtSignal()
    invalidated = pyqtSignal()
    # Fired after each auxiliary reply lands in the snapshot — the
    # temperature history feeds from this, not from every publish.
    auxiliaryChanged = pyqtSignal()
    consoleStoreChanged = pyqtSignal()
    commandChanged = pyqtSignal(object)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client
        self._active = False
        self._generation = 0
        self._timers = {}
        self._console_expanded = False
        self._console_entries = []
        # The store has no cursor API and entries can share a float
        # stamp: dedupe on (time, text) keys instead of the stamp alone.
        # Survives reconnects (not cleared on deactivation) so an entry
        # consumed before a drop never backfills again.
        self._console_seen = deque(maxlen=400)
        self._console_seed = None
        self._clear()
        for category, callback in ((RequestCategory.AUXILIARY, self.refresh_aux),
            (RequestCategory.POWER, self.refresh_power), (RequestCategory.SYSTEM, self.refresh_system),
            (RequestCategory.ENDSTOPS, self.refresh_endstops),
            (RequestCategory.CONSOLE, self.refresh_console_store),
            (RequestCategory.DISCOVERY, self.refresh_discovery)):
            timer = QTimer(self)
            timer.timeout.connect(callback)
            self._timers[category] = timer
        # Use QObject-bound receivers rather than lambdas that capture ``self``.
        # PyQt can automatically disconnect bound QObject receivers when this MonitorData
        # is destroyed; a lambda would outlive the C++ object on the long-lived client.
        client.statusReceived.connect(self.observe)
        client.commandChanged.connect(self.commandChanged.emit)
        client.sessionInvalidated.connect(self._session_invalidated)
        client.connectionChanged.connect(self._connection_changed)

    def _session_invalidated(self):
        self.set_active(False)

    def _connection_changed(self, *_args):
        self.changed.emit()

    def _clear(self):
        empty = freeze({})
        self._snapshot = MonitorSnapshot(empty, empty, (), empty, empty, (), (), empty, empty)

    @property
    def snapshot(self): return self._snapshot
    @property
    def active(self): return self._active

    @property
    def connected(self) -> bool:
        return bool(self._client.connected)

    @property
    def status(self):
        return self._client.status

    def track_command(self, name, expected_states=(), *, timeout_s=10.0):
        self._client.track_command(name, expected_states, timeout_s=timeout_s)

    def accept_command(self, name):
        self._client.accept_command(name)

    def fail_command(self, name, detail):
        self._client.fail_command(name, detail)

    def force_refresh(self):
        self._client.force_refresh()

    def set_toolhead_guard(self, active):
        self._client.set_toolhead_guard(active)

    def _update(self, **patch):
        from dataclasses import replace
        self._snapshot = replace(self._snapshot, **{key: freeze(value) for key, value in patch.items()})
        self.changed.emit()

    def set_active(self, active):
        if bool(active) == self._active: return
        self._active = bool(active)
        if not active:
            self._generation += 1
            for timer in self._timers.values(): timer.stop()
            self._client.transport.cancel_owner("monitor")
            # The console poll state dies with the session so a re-attach
            # is a REAL expand: the backfill seed must re-apply from the
            # persisted stamp (the domain panel's re-seed point — the
            # unchanged-flag early-return once left a rebound session
            # polling without a fresh seed).
            self._console_expanded = False
            self._console_seed = None
            self._console_entries = []
            self._clear()
            self.invalidated.emit()
            self.changed.emit()
        else:
            self._intervals()
            for timer in self._timers.values(): timer.start()
            self.observe(self._client.status)
            self.refresh_all()

    def _intervals(self):
        for category, timer in self._timers.items():
            interval = self._client.session.poll_policy.interval_ms(category, 1000, self._client.session.snapshot.printer_state)
            if timer.interval() != interval: timer.setInterval(interval)

    def request(self, channel, method, path, callback, *, body=None, replace=False, category="auxiliary",
                timeout_ms=5000):
        if not self._active or not self._client.session.base_url: return False
        generation = self._generation
        session = self._client.session.generation
        def finished(payload, error):
            if self._active and generation == self._generation and session == self._client.session.generation:
                callback(payload, error)
        return self._client.transport.send_json("monitor", channel, method, path, finished,
            body=body, replace=replace, category=category, timeout_ms=timeout_ms)

    def later(self, delay_ms, callback):
        generation, session = self._generation, self._client.session.generation
        def run():
            if self._active and generation == self._generation and session == self._client.session.generation:
                callback()
        QTimer.singleShot(delay_ms, run)

    def observe(self, status):
        if not self._active or not isinstance(status, Mapping): return
        status = {name: value for name, value in status.items() if isinstance(value, Mapping)}
        self._intervals()
        self._update(core=dict(status))

    def refresh_all(self):
        if not self._active: return
        self._client.force_refresh()
        self.refresh_discovery()
        self.refresh_power()
        self.refresh_system()
        self.refresh_endstops()
        self.refresh_webcams()

    @property
    def console_entries(self) -> list:
        return list(self._console_entries)

    @staticmethod
    def wants_object(name):
        return wanted_object(name)

    def refresh_discovery(self):
        self.request("objects", "GET", "printer/objects/list", self._objects, category="discovery")
        self.request("presets", "GET", "server/database/item?namespace=mainsail&key=presets",
            lambda payload, error: self._update(presets=result(payload).get("value", {})) if not error and isinstance(result(payload), Mapping) else None,
            category="discovery")

    def _objects(self, payload, error):
        value = result(payload)
        names = value.get("objects") if isinstance(value, Mapping) else None
        if error or not isinstance(names, (tuple, list)): return
        self._update(objects=tuple(sorted(str(name) for name in names)))
        if "configfile" in names:
            self.request("config-static", "POST", "printer/objects/query", self._aux,
                body={"objects": {"configfile": None}}, replace=True, category="discovery")
        self.refresh_aux()

    def refresh_aux(self):
        objects = {name: ["save_config_pending", "save_config_pending_items"] if name == "configfile" else None
                   for name in self._snapshot.objects if self.wants_object(name)}
        if objects: self.request("aux", "POST", "printer/objects/query", self._aux, body={"objects": objects})

    def _aux(self, payload, error):
        value = result(payload)
        incoming = value.get("status") if isinstance(value, Mapping) else None
        if error or not isinstance(incoming, Mapping): return
        # Rebuild from the current wanted set so objects that were renamed or
        # hot-removed stop rendering instead of staying in the snapshot for
        # the rest of the session.
        wanted = {name for name in self._snapshot.objects if self.wants_object(name)}
        merged = {name: state for name, state in self._snapshot.auxiliary.items() if name in wanted}
        for name, value in incoming.items():
            if name not in wanted: continue
            previous = merged.get(name)
            merged[name] = dict(previous, **value) if isinstance(previous, Mapping) and isinstance(value, Mapping) else value
        self._update(auxiliary=merged)
        self.auxiliaryChanged.emit()

    # The console's gcode-store feed: polled ONLY while the console is
    # expanded (the author's ruling), on the CONSOLE poll interval with
    # an idle floor (PollPolicy). The store holds Klipper's output
    # VERBATIM — Moonraker strips nothing (data_store.py stores the
    # payload as delivered) — and modern Klipper's response lines carry
    # no "ok" prefix at all (the "ok" is the RPC result, never console
    # output), so the store can never attest success. The honest proxy:
    # a line that is neither "!!" nor an "//" echo is normal output.
    def set_console_expanded(self, expanded, stored_time=0.0):
        expanded = bool(expanded)
        if expanded == self._console_expanded:
            return
        self._console_expanded = expanded
        if expanded:
            # The persisted last-seen stamp seeds the ONE-SHOT skip on
            # the first fetch: the store's buffer holds entries from
            # other sessions and other clients, and re-adding them was
            # the author's "stale responses without requests" dump.
            self._console_seed = float(stored_time or 0.0)
            self.refresh_console_store()

    def refresh_console_store(self):
        if not self._console_expanded:
            return
        def finished(payload, error):
            if error or not isinstance(result(payload), Mapping):
                return
            store = result(payload).get("gcode_store")
            if not isinstance(store, (list, tuple)):
                return
            seed = self._console_seed
            self._console_seed = None
            seen = self._console_seen
            responses = []
            for entry in store:
                if not isinstance(entry, Mapping) or entry.get("type") != "response":
                    continue
                stamp = number(entry.get("time"), float) or 0.0
                text = str(entry.get("message") or "")
                if not text:
                    continue
                if (stamp, text) in seen:
                    continue
                if seed is not None and stamp <= seed:
                    # The seed protects only this FIRST fetch — but the
                    # entries it skips must stay skipped for the whole
                    # session, or the next poll re-delivers the stale
                    # buffer (the author's live report: the console
                    # re-fetching Moonraker's history on load).
                    seen.append((stamp, text))
                    continue
                seen.append((stamp, text))
                responses.append({
                    "text": text[:MAX_LINE],
                    "error": text.startswith("!!"),
                    "success": not text.startswith("!!") and not text.startswith("//"),
                    "time": stamp,
                })
            if responses:
                self._console_entries = responses
                self.consoleStoreChanged.emit()
        self.request("console-store", "GET", "server/gcode_store?count=100", finished, category="console")

    def refresh_endstops(self):
        # Endstop pin states are NOT part of the objects query; the
        # only live readout is this one-shot status endpoint, polled on
        # a slow cadence (they change at homing, not every second).
        # A failed poll must never erase last-known states: an empty
        # endstop map reads as "not homed yet" while connected, which is
        # a lie about the printer during a transient network blip. The
        # states blank only on invalidation/disconnect.
        self.request("endstops", "GET", "printer/query_endstops/status",
            lambda p, e: self._update(endstops=dict(result(p))) if not e and isinstance(result(p), Mapping) else None,
            category="endstops")

    def refresh_power(self):
        self.request("power-list", "GET", "machine/device_power/devices",
            lambda p, e: self._update(power=result(p).get("devices", ())) if not e and isinstance(result(p), Mapping) else None,
            category="power")

    def refresh_system(self):
        for channel, path, key in (("server-info", "server/info", "server"), ("printer-info", "printer/info", "printer")):
            self.request(channel, "GET", path,
                lambda p, e, k=key: self._update(**{k: result(p)}) if not e and isinstance(result(p), Mapping) else None,
                category="system")

    def refresh_webcams(self):
        # Same retention principle as endstops: a failed poll must never
        # erase last-known cameras — a transient blip would blank the
        # camera column ("no camera") during a printer reboot. The list
        # clears only on invalidation/disconnect.
        self.request("webcams", "GET", "server/webcams/list",
            lambda p, e: self._update(webcams=tuple(item for item in result(p).get("webcams", ()) if isinstance(item, dict) and item.get("enabled", True)))
            if not e and isinstance(result(p), Mapping) else None,
            replace=True, category="discovery")


