from __future__ import annotations

from typing import Any

from .MoonrakerOutputDevice import MoonrakerOutputDevice as _BaseMoonrakerOutputDevice


class MoonrakerOutputDevice(_BaseMoonrakerOutputDevice):
    """Moonraker output device with a complete Cura write lifecycle.

    The upload dialog is shown after ``writeStarted`` has been emitted. Closing
    that dialog is a user cancellation, not an output error, but Cura still needs
    a terminal ``writeFinished`` signal to release the save/upload action. The
    base implementation pre-dates that distinction and can leave Cura believing
    a write is still active after Cancel. This subclass makes every started write
    terminate exactly once while retaining ``writeError``/``writeSuccess`` for
    the actual outcome.
    """

    def __init__(self, application: Any, follower: Any, machine_id: str) -> None:
        self._write_terminal_emitted = False
        super().__init__(application, follower, machine_id)

    def requestWrite(self, *args: Any, **kwargs: Any) -> None:
        self._write_terminal_emitted = False
        super().requestWrite(*args, **kwargs)

    def _emit_write_finished_once(self) -> None:
        if self._write_terminal_emitted:
            return
        self._write_terminal_emitted = True
        self.writeFinished.emit(self)

    def cancelUpload(self) -> None:
        if not self._busy:
            return
        self._dialog = None
        self._cleanup()
        self._emit_write_finished_once()

    def _fail(self, text: str) -> None:
        had_started_write = bool(self._busy)
        super()._fail(text)
        if had_started_write:
            self._emit_write_finished_once()

    def _on_upload_finished(self, reply: Any) -> None:
        had_started_write = bool(self._busy)
        super()._on_upload_finished(reply)
        if had_started_write and not self._busy:
            self._emit_write_finished_once()
