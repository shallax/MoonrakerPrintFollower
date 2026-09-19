"""The migration-failure surfaces' owner (the UX persona's spec): a
once-per-failure Cura toast — after the What's-New overlay when it is
due, with a 300 s escape hatch so a stuck overlay can never swallow
the message — and the dialog's persistent banner. The record
(settings global.migration) carries toastShown and bannerDismissed,
so the surfaces are loud once and quiet forever; the record itself is
never removable. The toast raises from ONE plugin-level owner, never
per device — a farm would toast per printer. The UM.Message wiring is
injected (raise_toast) so the ordering state machine tests without
Cura."""
from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer


class MigrationNotice(QObject):
    def __init__(self, persistence, whats_new_gate, raise_toast, parent=None):
        super().__init__(parent)
        self._persistence = persistence
        self._whats_new_gate = whats_new_gate  # callable() -> bool
        self._raise_toast = raise_toast  # callable(record) -> None
        self._escape = None
        self._model_signal = None
        self._closed = False

    def announce(self):
        """Boot-ready time (B1's initializationFinished): the
        deterministic ordering — the overlay first when it is due,
        then the toast. No failure, or already toasted: nothing."""
        record = self._persistence.migration_record()
        if not record or record.get("status") != "failed" or record.get("toastShown"):
            return
        if self._whats_new_gate is None or not self._whats_new_gate():
            self._raise_once()
        elif self._model_signal is not None:
            # The overlay is due: the toast waits for its dismissal.
            self._model_signal.connect(self._raise_once)
        # The escape hatch: a stuck overlay must never swallow the
        # failure message. The sticky toast is safe even while the
        # overlay is up — nothing is lost or hidden.
        if self._escape is None:
            self._escape = QTimer(self)
            self._escape.setSingleShot(True)
            self._escape.setInterval(300000)
            self._escape.timeout.connect(self._raise_once)
            self._escape.start()

    def attach_model(self, model):
        """The overlay's owner (the Monitor model) arrives after the
        notice: take its dismiss signal, then re-announce so the
        deferred path can use it. A swap disconnects the PREVIOUS
        model first (the 2026-09-19 review's F2) — a cached monitor's
        stale dismiss must never fire the toast for the new owner."""
        if self._model_signal is not None:
            try:
                self._model_signal.disconnect(self._raise_once)
            except (TypeError, AttributeError):
                pass
        self._model_signal = getattr(model, "whatsNewDismissed", None)
        self.announce()

    def close(self):
        """Deinitialization: stop the escape timer, drop the model
        signal, and make every queued emission inert."""
        self._closed = True
        if self._escape is not None:
            try:
                self._escape.stop()
            except Exception:
                pass
        if self._model_signal is not None:
            try:
                self._model_signal.disconnect(self._raise_once)
            except (TypeError, AttributeError):
                pass
        self._model_signal = None

    def _raise_once(self, *args):
        if self._closed:
            return
        record = self._persistence.migration_record()
        if not record or record.get("status") != "failed" or record.get("toastShown"):
            return
        try:
            self._raise_toast(record)
        except Exception:
            pass
        # The record write lands even when the surface failed to
        # raise: a broken toast host must not re-toast every launch.
        self._persistence.set_migration_record({"toastShown": True})
