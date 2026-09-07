"""Active printer/configuration ownership, independent of following and UI features."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from UM.Logger import Logger

from .CuraAdapter import active_machine_identity
from .PrinterConfig import PrinterConfigStore


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
    def configured(self): return self._machine_id != "unknown" and self.usable(self.normalise(self.config.url))

    @staticmethod
    def normalise(url):
        value = str(url or "").strip()
        if value.lower() in {"", "http:", "https:", "http://", "https://"}: return ""
        value = value.rstrip("/")
        return value if value.lower().startswith(("http://", "https://")) else "http://" + value

    @staticmethod
    def usable(url):
        parsed = QUrl(url)
        return parsed.isValid() and parsed.scheme() in {"http", "https"} and bool(parsed.host())

    def _migrate(self):
        for migrate in (self._store.migrate_legacy_to_current_machine, self._store.migrate_moonraker_connection):
            try: migrate()
            except Exception as error: Logger.log("w", "Moonraker settings migration failed: %s", error)

    def start(self):
        self._migrate()
        self._apply()

    def apply(self, config):
        if self._closed: return
        previous = self.config
        if (self.normalise(previous.url), previous.api_key) != (self.normalise(config.url), config.api_key):
            self._client.stop()  # all owners invalidate before persistence/rebind
        self._store.set(config, self._machine_id)
        self._apply()

    def _machine_changed(self, *_args):
        machine_id, name = self._store.identity()
        if machine_id == self._machine_id:
            self._machine_name = name
            self.changed.emit()
            return
        self._client.stop()  # even if both Cura profiles use the same endpoint
        self._machine_id, self._machine_name = machine_id, name
        self._migrate()
        self._apply()

    def _apply(self):
        config = self.config
        url = self.normalise(config.url)
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


