"""Active printer/configuration ownership, independent of following and UI features."""
from __future__ import annotations

from dataclasses import replace
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from UM.Logger import Logger

from .CuraAdapter import active_machine_identity
from .PrinterConfig import PrinterConfigStore, normalise_url


class PrinterBinding(QObject):
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
    def config(self): return self._store.get(self._machine_id)
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
        except Exception as error: Logger.log("w", "Moonraker camera preference flush failed: %s", error)

    def start(self):
        self._migrate()
        self._apply()

    def apply(self, config):
        if self._closed: return
        previous = self.config
        self._client.set_trace_http(config.trace_http)
        endpoint_changed = (normalise_url(previous.url), previous.api_key) != (normalise_url(config.url), config.api_key)
        camera_changed = previous.camera_selected != config.camera_selected
        camera_only = camera_changed and replace(previous, camera_selected=config.camera_selected) == config

        if endpoint_changed:
            # Tear the poller down before persistence/rebind without
            # emitting: the client's configure below emits exactly one
            # invalidation wave on the old identity.
            self._client.stop(reset_session=False)

        # Camera selection is UI state, not connection state. Persist it directly
        # against the active machine and flush Cura's preference file immediately;
        # do not reconfigure/restart the Moonraker client just because a dropdown
        # changed.
        self._store.set(config, self._machine_id)
        if camera_changed:
            self._flush_preferences()
        if camera_only:
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


