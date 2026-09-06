from __future__ import annotations

from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlencode

from PyQt6.QtCore import QTimer, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Logger import Logger

from .MoonrakerOutputDevice import MoonrakerOutputDevice as _BaseMoonrakerOutputDevice


class MoonrakerOutputDevice(_BaseMoonrakerOutputDevice):
    """Moonraker output device with a complete, re-entrancy-safe Cura write lifecycle.

    Cura invokes the upload/cancel slots from inside the QML dialog's event handler.
    Destroying the last Python reference to that dialog, closing the output stream,
    or emitting a terminal write signal before the QML handler returns can invalidate
    objects still on Qt's stack. Accepted/rejected dialogs are therefore finalized on
    the next event-loop turn.

    This wrapper also discovers writable subdirectories from Moonraker's ``gcodes``
    root so the upload folder combo box reflects the printer rather than only paths
    previously typed into Cura. Hidden path components (names beginning with ``.``)
    are deliberately excluded from the upload prompt.
    """

    uploadPathsChanged = pyqtSignal()
    MAX_DISCOVERED_UPLOAD_DIRS = 256
    ROOT_UPLOAD_LABEL = "<root>"

    def __init__(self, application: Any, follower: Any, machine_id: str) -> None:
        self._write_terminal_emitted = False
        self._accept_pending = False
        self._cancel_pending = False
        self._upload_path_options: List[str] = [self.ROOT_UPLOAD_LABEL]
        self._folder_scan_queue: List[str] = []
        self._folder_scan_seen: Set[str] = set()
        self._folder_scan_discovered: Set[str] = set()
        super().__init__(application, follower, machine_id)
        self._publish_upload_paths()

    def requestWrite(self, *args: Any, **kwargs: Any) -> None:
        self._write_terminal_emitted = False
        self._accept_pending = False
        self._cancel_pending = False
        super().requestWrite(*args, **kwargs)

    # ------------------------------------------------------------------
    # Upload dialog lifetime and folder discovery
    # ------------------------------------------------------------------

    @staticmethod
    def _is_hidden_remote_path(path: str) -> bool:
        normalised = str(path or "").replace("\\", "/").strip("/")
        return any(part.startswith(".") for part in normalised.split("/") if part)

    # The UI labels Moonraker's logical empty/root path as <root>.
    @pyqtProperty(str, notify=uploadPathsChanged)
    def initialUploadPath(self) -> str:
        path = self._normalise_remote_path(self._path_name)
        if not path or self._is_hidden_remote_path(path):
            return self.ROOT_UPLOAD_LABEL
        return path

    @pyqtProperty(QVariant, notify=uploadPathsChanged)
    def uploadPathOptions(self) -> QVariant:
        return QVariant(list(self._upload_path_options))

    def _publish_upload_paths(self) -> None:
        options = {self.ROOT_UPLOAD_LABEL}
        try:
            for path in self._config.upload_paths:
                normalised = self._normalise_remote_path(path)
                if normalised and not self._is_hidden_remote_path(normalised):
                    options.add(normalised)
            current = self._normalise_remote_path(self._config.upload_path)
            if current and not self._is_hidden_remote_path(current):
                options.add(current)
        except Exception:
            pass
        options.update(
            item for item in self._folder_scan_discovered
            if not self._is_hidden_remote_path(item)
        )
        ordered = [self.ROOT_UPLOAD_LABEL] + sorted(
            (item for item in options if item and item != self.ROOT_UPLOAD_LABEL),
            key=str.casefold,
        )
        if ordered != self._upload_path_options:
            self._upload_path_options = ordered
            self.uploadPathsChanged.emit()

    def _show_upload_dialog(self) -> None:
        super()._show_upload_dialog()
        if self._dialog is not None and self._busy:
            self._refresh_upload_paths()

    def _refresh_upload_paths(self) -> None:
        self._folder_scan_queue = ["gcodes"]
        self._folder_scan_seen = {"gcodes"}
        self._folder_scan_discovered = set()
        self._publish_upload_paths()
        self._scan_next_upload_directory()

    def _scan_next_upload_directory(self) -> None:
        if not self._busy or not self._folder_scan_queue:
            return
        path = self._folder_scan_queue.pop(0)
        self._json_request(
            "GET",
            "server/files/directory?" + urlencode({"path": path}),
            lambda payload, error, parent=path: self._on_upload_directory(parent, payload, error),
        )

    def _on_upload_directory(
        self,
        parent: str,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if not self._busy:
            return
        if error:
            Logger.log("w", "Moonraker Print Follower: could not list upload folder '%s': %s", parent, error)
            self._scan_next_upload_directory()
            return

        result = (payload or {}).get("result") or {}
        directories = result.get("dirs") if isinstance(result, dict) else None
        if isinstance(directories, list):
            for item in directories:
                if not isinstance(item, dict):
                    continue
                dirname = str(item.get("dirname") or "").strip().strip("/\\")
                permissions = str(item.get("permissions") or "rw").lower()
                if not dirname or dirname.startswith(".") or "w" not in permissions:
                    continue
                full_path = parent.rstrip("/") + "/" + dirname
                if self._is_hidden_remote_path(full_path):
                    continue
                if full_path in self._folder_scan_seen:
                    continue
                self._folder_scan_seen.add(full_path)
                if len(self._folder_scan_seen) > self.MAX_DISCOVERED_UPLOAD_DIRS:
                    break
                relative = full_path[len("gcodes/"):] if full_path.startswith("gcodes/") else ""
                relative = self._normalise_remote_path(relative)
                if relative and not self._is_hidden_remote_path(relative):
                    self._folder_scan_discovered.add(relative)
                self._folder_scan_queue.append(full_path)

        self._publish_upload_paths()
        self._scan_next_upload_directory()

    @pyqtSlot(str, str, bool)
    def acceptUpload(self, path: str, filename: str, start_print: bool) -> None:
        if not self._busy or self._accept_pending or self._cancel_pending:
            return
        path = str(path or "").strip()
        if path == self.ROOT_UPLOAD_LABEL:
            path = ""
        path = self._normalise_remote_path(path)
        filename = self._normalise_filename(filename)
        if not filename:
            return
        if "." not in filename:
            filename += "." + self._output_format
        self._path_name = path
        self._file_name = filename
        self._start_print = bool(start_print)
        self._accept_pending = True
        # The QML handler calls Dialog.accept() immediately after this slot returns.
        QTimer.singleShot(0, self._finish_accept_upload)

    def _finish_accept_upload(self) -> None:
        if not self._accept_pending:
            return
        self._accept_pending = False
        if not self._busy:
            self._release_dialog()
            return
        self._remember_upload_choices(self._path_name, self._start_print)
        self._release_dialog()
        self._begin_upload()

    @pyqtSlot()
    def cancelUpload(self) -> None:
        if not self._busy or self._cancel_pending or self._accept_pending:
            return
        self._cancel_pending = True
        # Do not tear down the dialog/stream while its onRejected handler is running.
        QTimer.singleShot(0, self._finish_cancel_upload)

    def _finish_cancel_upload(self) -> None:
        if not self._cancel_pending:
            return
        self._cancel_pending = False
        if not self._busy:
            self._release_dialog()
            return
        self._release_dialog()
        self._cleanup()
        self._emit_write_finished_once()

    def _release_dialog(self) -> None:
        dialog = self._dialog
        self._dialog = None
        if dialog is None:
            return
        try:
            dialog.deleteLater()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Cura write lifecycle
    # ------------------------------------------------------------------

    def _emit_write_finished_once(self) -> None:
        if self._write_terminal_emitted:
            return
        self._write_terminal_emitted = True
        # Keep terminal delivery out of network/QML callbacks for the same reason
        # dialog teardown is deferred above.
        QTimer.singleShot(0, self._emit_write_finished)

    def _emit_write_finished(self) -> None:
        self.writeFinished.emit(self)

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
