"""Active printer/configuration ownership, independent of following and UI features."""
from __future__ import annotations

import time
from dataclasses import replace
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from UM.Logger import Logger

from .CuraAdapter import active_machine_identity
from .PrinterConfig import PrinterConfigStore, normalise_url


class PrinterBinding(QObject):
    # Fires only on BINDING-initiated preference flushes (camera change,
    # the console debounce). Cura's own exit flush never emits it — the
    # name is narrower than it sounds.
    preferencesFlushed = pyqtSignal()
    changed = pyqtSignal()

    def __init__(self, application, client, parent=None):
        super().__init__(parent)
        self._application = application
        self._client = client
        self._store = PrinterConfigStore(application.getPreferences(), lambda: active_machine_identity(application))
        self._machine_id, self._machine_name = self._store.identity()
        self._closed = False
        signal = getattr(application, "globalContainerStackChanged", None)
        self._machine_signal = signal
        if signal is not None: signal.connect(self._machine_changed)

    @property
    def config(self):
        # LIVE identity, never the construction-time cache: the binding
        # constructs before Cura's active machine exists (identity
        # "unknown"), and every cached read saw the unknown machine's
        # EMPTY record — the console's restored transcript never loaded
        # (the author's "console starts completely empty" report).
        return self._store.get()
    @property
    def identity(self): return self._machine_id, self._machine_name
    @property
    def configured(self): return self._machine_id != "unknown" and self.usable(normalise_url(self.config.url))

    @staticmethod
    def usable(url):
        parsed = QUrl(url)
        return parsed.isValid() and parsed.scheme() in {"http", "https"} and bool(parsed.host())

    def _migrate(self):
        for migrate in (self._store.migrate_legacy_to_current_machine, self._store.migrate_moonraker_connection):
            try: migrate()
            except Exception as error: Logger.log("w", "Moonraker settings migration failed: %s", error)

    def _flush_preferences(self):
        """Force preference-backed selections to disk when Cura exposes the hook."""
        save = getattr(self._application, "savePreferences", None)
        if not callable(save): return
        try: save()
        except Exception as error:
            Logger.log("w", "Moonraker camera preference flush failed: %s", error)
            return
        # The console colours its sent lines by SAVED state (the
        # author's ruling), so the flush must be observable.
        self._last_console_flush = time.monotonic()
        self.preferencesFlushed.emit()

    def start(self):
        self._migrate()
        self._apply()

    # Console persists write Cura's in-memory preferences, but Cura
    # only flushes its preference FILE on a clean exit — a quit that
    # skips the flush silently lost the session's transcript (the
    # author's "testing" line vanished between restarts). Flush
    # ourselves, debounced on quiescence with a hard cap: a chatty
    # Klipper re-arms the 2 s timer on every response batch, which once
    # starved the flush indefinitely — the blue "unsaved" lines never
    # settled and a crash lost everything the debounce exists to
    # protect (the engineering panel's starvation).
    _console_flush = None
    _console_flush_max_wait_s = 10.0
    _last_console_flush = None

    def _arm_console_flush(self):
        now = time.monotonic()
        if self._last_console_flush is not None \
                and now - self._last_console_flush >= self._console_flush_max_wait_s:
            self._flush_preferences()
            return
        if self._console_flush is None:
            from PyQt6.QtCore import QTimer
            self._console_flush = QTimer(self)
            self._console_flush.setSingleShot(True)
            self._console_flush.setInterval(2000)
            self._console_flush.timeout.connect(self._flush_preferences)
        self._console_flush.start()

    def apply(self, config):
        if self._closed: return
        previous = self.config
        self._client.set_trace_http(config.trace_http)
        endpoint_changed = (normalise_url(previous.url), previous.api_key) != (normalise_url(config.url), config.api_key)
        camera_changed = previous.camera_selected != config.camera_selected
        camera_only = camera_changed and replace(previous, camera_selected=config.camera_selected) == config
        # Console transcript persists arrive every second while the
        # printer chats; they are storage state, not connection state,
        # and must never reconfigure/restart the client (they did -
        # one configure per response batch).
        console_only = replace(previous, console_transcript=config.console_transcript,
                               console_store_time=config.console_store_time,
                               console_history=config.console_history) == config

        if endpoint_changed:
            # Tear the poller down before persistence/rebind without
            # emitting: the client's configure below emits exactly one
            # invalidation wave on the old identity.
            self._client.stop(reset_session=False)

        # Camera selection is UI state, not connection state. Persist it directly
        # against the active machine and flush Cura's preference file immediately;
        # do not reconfigure/restart the Moonraker client just because a dropdown
        # changed.
        self._store.set(config)  # live identity (see config())
        if camera_changed:
            self._flush_preferences()
        if camera_only or console_only:
            if console_only:
                self._arm_console_flush()
            self.changed.emit()
            return
        self._apply()

    def _machine_changed(self, *_args):
        machine_id, name = self._store.identity()
        if machine_id == self._machine_id:
            self._machine_name = name
            self.changed.emit()
            return
        # A machine switch must invalidate the session state even when both
        # Cura profiles use the same endpoint: subscribers tear down and
        # the generation bump is what stale-callback guards rely on.
        self._client.stop()
        self._machine_id, self._machine_name = machine_id, name
        self._migrate()
        self._apply()

    def _apply(self):
        config = self.config
        url = normalise_url(config.url)
        self._client.configure(url, config.api_key, config.poll_interval_ms)
        if self.configured: self._client.start()
        else: self._client.stop()
        self.changed.emit()

    def close(self):
        if self._closed: return
        self._closed = True
        if self._machine_signal is not None:
            try: self._machine_signal.disconnect(self._machine_changed)
            except Exception: pass
        self._client.stop()


