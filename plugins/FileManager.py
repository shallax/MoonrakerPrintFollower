"""The file-manager service: resident listing, history and view state.

One owner for the popup's data (round-2 A1): a full-tree walk on open
into a resident listing (search is global by the author's ruling), the
current directory as the grid's scope, history with the bounded
window and the Load-all escape hatch, disk usage from the root
listing, and the view pipeline (filter → search → sort → page) from
FileManagerPolicy. The request lane is its own ("file-manager"),
never MonitorData's "monitor" lane; a bind/deactivate bumps the
generation so stale callbacks can never publish.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Set
from urllib.parse import quote

from PyQt6.QtCore import QByteArray, QFile, QIODevice, QObject, QUrl, QVariant, pyqtSignal
from PyQt6.QtNetwork import QHttpMultiPart, QHttpPart, QNetworkReply, QNetworkRequest

from .MoonrakerProtocol import (
    _moonraker_error_text,
    delete_endpoint,
    directory_create_endpoint,
    directory_delete_endpoint,
    move_endpoint,
    print_start_endpoint,
)
from .FileManagerPolicy import (
    COLUMN_WIDTH_MAX,
    COLUMN_WIDTH_MIN,
    TRAILING_COLUMN_ORDER,
    FileRow,
    HISTORY_WINDOW,
    ViewState,
    attempts_for,
    clamp_page,
    directory_rows,
    empty_kind,
    is_gcode_name,
    name_collides,
    normalise_columns,
    page_count,
    page_selection_state,
    page_slice,
    path_collides,
    recent_prints,
    rename_path,
    rename_target,
    upload_relpath,
)

MAX_DIRECTORIES = 50


class FileManager(QObject):
    # The thumbnail fetch queue's concurrency cap: scrolling past rows
    # enqueues their downloads, which fire together as the queue
    # drains — a bounded burst, never chained through the previous
    # request's completion handler (that serialised the queue and
    # stretched the settle across the whole listing).
    MAX_THUMB_FETCHES = 3

    changed = pyqtSignal()
    # Local explanations for per-row actions (metascan outcomes) —
    # the model routes these into the console feed like the
    # toolhead's rejectedNote.
    note = pyqtSignal(str)
    # Upload progress and outcome (Snapshot 3 finish — the author's
    # live request: a progress bar and a success/fail verdict in the
    # popup).
    uploadProgress = pyqtSignal(int)
    uploadFinished = pyqtSignal(bool, str)
    # Thumbnail transitions publish ALONE (the author's live report:
    # scrolling a 400-file listing fired a fetch per newly visible
    # row and each reply rebuilt the whole payload — the storm).
    thumbsChanged = pyqtSignal()

    def __init__(self, client, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._client = client
        self._generation = 0
        self._rows: Dict[str, FileRow] = {}
        self._dirs: Set[str] = set()
        self._history: List[Dict[str, Any]] = []
        self._history_exhausted = False
        self._disk_usage: Dict[str, int] = {}
        self._refreshed_at: Optional[float] = None
        self._walk_error: Optional[str] = None
        self._print_attempt: Optional[tuple] = None
        self._thumbs: Dict[str, Dict[str, str]] = {}
        self._thumb_root = tempfile.mkdtemp(prefix="mpf-thumbs-")
        # In-flight thumbnail replies ride this registry until their
        # handlers run — the transport's own lifetime pattern (see
        # _fetch_thumb: a bare closure connected to a network signal
        # is a use-after-free trap in PyQt).
        self._thumb_replies: Dict[str, QNetworkReply] = {}
        self._thumb_generation = 0
        self._thumb_queue: List[tuple] = []
        self._thumb_active = 0
        self._upload_replies: Dict[str, QNetworkReply] = {}
        # Snapshot 3's column config lives HERE, not in the model (the
        # author's live ruling: the file manager is its own thing,
        # composed into the Monitor page — the model only merges).
        columns = normalise_columns({})
        self._column_widths: Dict[str, float] = dict(columns["widths"])
        self._column_order: List[str] = list(columns["order"])
        self._column_hidden: Set[str] = set(columns["hidden"])
        self._directory: List[str] = []
        self._selection: Set[str] = set()
        self._view = ViewState()

    # ---- lifecycle -------------------------------------------------

    def bind(self) -> None:
        """(Re)attach to the active printer: invalidates everything and
        starts a fresh walk. Called on device activation and printer
        switches — a stale popup must never carry the previous
        machine's rows (round-2 A15)."""
        self._generation += 1
        self._thumb_generation += 1
        self._client.transport.cancel_owner("file-manager")
        self._abort_thumbs()
        self._abort_uploads()
        self._rows = {}
        self._dirs = set()
        self._history = []
        self._history_exhausted = False
        self._disk_usage = {}
        self._refreshed_at = None
        self._walk_error = None
        self._print_attempt = None
        self._thumbs = {}
        self._thumb_queue = []
        self._thumb_active = 0
        try:
            shutil.rmtree(self._thumb_root, ignore_errors=True)
        except Exception:
            pass
        self._thumb_root = tempfile.mkdtemp(prefix="mpf-thumbs-")
        self._directory = []
        self._selection = set()
        self.changed.emit()

    def unbind(self) -> None:
        """Printer deactivated: cancel the lane and clear everything."""
        self._generation += 1
        self._thumb_generation += 1
        self._client.transport.cancel_owner("file-manager")
        self._abort_thumbs()
        self._abort_uploads()
        self._rows = {}
        self._dirs = set()
        self._history = []
        self._history_exhausted = False
        self._disk_usage = {}
        self._refreshed_at = None
        self._walk_error = None
        self._print_attempt = None
        self._thumbs = {}
        self._thumb_queue = []
        self._thumb_active = 0
        try:
            shutil.rmtree(self._thumb_root, ignore_errors=True)
        except Exception:
            pass
        self._thumb_root = tempfile.mkdtemp(prefix="mpf-thumbs-")
        self._directory = []
        self._selection = set()
        self.changed.emit()

    # ---- fetching --------------------------------------------------

    def open(self) -> None:
        """Refetch on open (the author's ruling): a fresh walk plus the
        history window. The previous rows stay visible until the new
        listing lands (the popup never opens empty)."""
        self._walk()
        self._fetch_history(HISTORY_WINDOW)

    def refresh(self) -> None:
        # Thumbnails regenerate with the file (the author's ruling:
        # refresh is the refetch of everything visible).
        self.clear_thumbnails()
        self.open()

    def _walk(self) -> None:
        generation = self._generation
        transport = self._client.transport
        visited = 0
        pending = 0
        # Each walk builds a FRESH listing and swaps it in on
        # completion: refresh must discover new files AND new
        # directories, and files deleted on the printer must leave
        # the rows. Reusing the resident dicts meant an already-known
        # directory was never re-walked (the author's live report:
        # refresh missed new subdirectories) and deleted files
        # lingered forever. On a partial failure the previous
        # listing stays (the popup never goes blank on a refresh).
        fresh_rows: Dict[str, FileRow] = {}
        fresh_dirs: Set[str] = set()
        walked_error: List[Optional[str]] = [None]

        def visit(directory: str, is_root: bool) -> None:
            nonlocal visited, pending
            if visited >= MAX_DIRECTORIES:
                return
            visited += 1
            pending += 1
            def finished(payload, error) -> None:
                nonlocal pending
                pending -= 1
                if generation != self._generation:
                    return
                if error or not isinstance(payload, dict):
                    walked_error[0] = error or "listing failed"
                    self.changed.emit()
                    return
                result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
                for entry in result.get("dirs", ()) or ():
                    name = str(entry.get("dirname") or "")
                    if name and not name.startswith("."):
                        # The resident set stores FULL paths
                        # ("prints/deep"), not basenames — the folder
                        # strip derives its children by prefix.
                        path = f"{directory}/{name}" if directory else name
                        if path not in fresh_dirs:
                            fresh_dirs.add(path)
                            visit(path, False)
                rows = directory_rows("gcodes", directory, result.get("files", ()) or ())
                for row in rows:
                    fresh_rows[f"gcodes/{row.relpath}"] = row
                usage = result.get("disk_usage")
                if is_root and isinstance(usage, dict):
                    self._disk_usage = {str(k): int(v) for k, v in usage.items() if isinstance(v, (int, float))}
                if pending == 0:
                    self._refreshed_at = time.time()
                    if walked_error[0] is None:
                        self._rows = fresh_rows
                        self._dirs = fresh_dirs
                        self._walk_error = None
                        self._rejoin_history()
                    else:
                        self._walk_error = walked_error[0]
                self.changed.emit()
            started = transport.send_json("file-manager", f"dir:{directory}", "GET",
                f"server/files/directory?path={quote('gcodes' + (f'/{directory}' if directory else ''), safe='/')}&extended=true",
                finished, replace=True)
            if not started:
                pending -= 1
                # A refused start must still terminate the walk with
                # a visible failure — an empty walk with no error and
                # no timestamp is the eternal "Loading files…" face.
                walked_error[0] = "listing failed"
                self._walk_error = walked_error[0]
                self.changed.emit()
        visit("", True)

    def _fetch_history(self, limit: int) -> None:
        generation = self._generation
        transport = self._client.transport

        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            if error or not isinstance(payload, dict):
                self.changed.emit()
                return
            result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            jobs = result.get("jobs", ()) or ()
            self._history = [job for job in jobs if isinstance(job, dict)]
            self._history_exhausted = len(self._history) < limit
            self._rejoin_history()
            self.changed.emit()
        transport.send_json("file-manager", "history", "GET",
            f"server/history/list?limit={max(1, int(limit))}", finished, replace=True)

    def load_all_history(self) -> None:
        """The complete escape hatch (the author's ruling): page the
        list until exhausted — one click resolves even a file printed
        a thousand jobs ago."""
        generation = self._generation
        transport = self._client.transport
        batch = 200

        def page(start: int, accumulated: List[Dict[str, Any]]) -> None:
            def finished(payload, error) -> None:
                if generation != self._generation:
                    return
                if error or not isinstance(payload, dict):
                    self.changed.emit()
                    return
                result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
                jobs = [job for job in (result.get("jobs", ()) or ()) if isinstance(job, dict)]
                accumulated.extend(jobs)
                if len(jobs) < batch or len(accumulated) >= 10000:
                    self._history = accumulated
                    self._history_exhausted = True
                    self._rejoin_history()
                    self.changed.emit()
                    return
                page(start + batch, accumulated)
            transport.send_json("file-manager", "history", "GET",
                f"server/history/list?limit={batch}&start={start}", finished, replace=True)
        page(0, [])

    def scan_metadata(self, relpath: str) -> None:
        """Per-row host-side parse (metascan) — the one lever that
        fills a legacy file's empty columns (round-2 domain D2).
        Live-proven (2026-09-10): Moonraker's metascan response IS
        the parsed metadata (a 200 carrying the full result) — no
        re-read. Non-gcode files draw a host refusal ("not a valid
        gcode file"), and BOTH outcomes answer as a console note: the
        author's live report was an option that appeared to do
        nothing at all."""
        generation = self._generation
        transport = self._client.transport

        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            if error or not isinstance(payload, dict):
                self.note.emit(f"Metadata scan refused: {error or 'the printer returned no data'}.")
                return
            data = payload.get("result") if isinstance(payload.get("result"), dict) else payload
            key = f"gcodes/{relpath}"
            row = self._rows.get(key)
            if row is not None and isinstance(data, dict):
                self._rows[key] = FileRow(**{**vars(row), **_row_from_metadata(relpath, data)})
                self.changed.emit()
            self.note.emit(f"Metadata refreshed for {relpath.rsplit('/', 1)[-1]}.")
        transport.send_json("file-manager", f"metascan:{relpath}", "POST",
            f"server/files/metascan?filename={quote(relpath, safe='/')}", finished, replace=True)

    def row_for(self, relpath: str) -> Optional[FileRow]:
        """The resident row for a root-exclusive relpath, or None."""
        return self._rows.get(f"gcodes/{relpath}")

    def delete_files(self, relpaths: Sequence[str], printing_relpath: Any = "") -> None:
        """Snapshot 3: HTTP DELETE per file (root-inclusive — the
        host's only delete route). The currently-printing file is
        refused client-side (the author's gate — the host 403s it
        anyway, and the client must not even ask). Every outcome
        answers as a console note; the local rows drop on success
        and ONE refresh re-walks after the batch (the ruling: every
        stale-making action refreshes)."""
        generation = self._generation
        transport = self._client.transport
        blocked = str(printing_relpath or "")
        targets = [str(p) for p in relpaths
                   if str(p) != blocked and self._rows.get(f"gcodes/{str(p)}") is not None]
        if not targets:
            self.note.emit("Nothing to delete — the selection is empty or printing.")
            return
        remaining = [len(targets)]

        def finished(payload, error, relpath) -> None:
            if generation != self._generation:
                return
            remaining[0] -= 1
            if error:
                self.note.emit(f"Delete refused: {error}")
            else:
                self._rows.pop(f"gcodes/{relpath}", None)
                self._selection.discard(relpath)
                self._thumbs.pop(relpath, None)
                self.note.emit(f"Deleted {relpath.rsplit('/', 1)[-1]}.")
                self.changed.emit()
            if remaining[0] == 0:
                self.refresh()
        for relpath in targets:
            row = self._rows.get(f"gcodes/{relpath}")
            transport.send_json("file-manager", f"delete:{relpath}", "DELETE",
                delete_endpoint(transport.identity[0], row.root, row.relpath),
                lambda payload, error, p=relpath: finished(payload, error, p),
                replace=True, category="command")

    def rename_file(self, relpath: str, new_name: Any, printing_relpath: Any = "",
                    overwrite: bool = False) -> bool:
        """Snapshot 3: POST server/files/move {source, dest}, both
        root-inclusive. The printing file is refused, an unusable
        name is refused, and a collision needs the caller's overwrite
        nod (the host silently overwrites — the client asks first,
        round-1 C2/C3). Returns True when the request was sent."""
        generation = self._generation
        relpath = str(relpath)
        if relpath == str(printing_relpath or ""):
            self.note.emit(f"Rename refused: {relpath.rsplit('/', 1)[-1]} is printing.")
            return False
        row = self._rows.get(f"gcodes/{relpath}")
        if row is None:
            return False
        target = rename_target(row, new_name)
        if target is None:
            self.note.emit("Rename refused: the name is empty, unchanged, or not a plain filename.")
            return False
        if name_collides(list(self._rows.values()), target) and not overwrite:
            self.note.emit(f"Rename refused: a file named {str(new_name).strip()} already exists.")
            return False
        transport = self._client.transport

        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            if error:
                self.note.emit(f"Rename refused: {error}")
                return
            old_key = f"gcodes/{relpath}"
            current = self._rows.get(old_key)
            if current is None:
                # A walk swapped the listing mid-flight: the refresh
                # below re-reads the truth.
                self.refresh()
                return
            new_row = FileRow(**{**vars(current), "filename": target.rsplit("/", 1)[-1],
                                 "relpath": target})
            self._rows.pop(old_key, None)
            self._rows[f"gcodes/{target}"] = new_row
            thumb = self._thumbs.pop(relpath, None)
            if thumb is not None:
                self._thumbs[target] = thumb
            self.note.emit(f"Renamed to {new_row.filename}.")
            self.changed.emit()
            self.refresh()
        transport.send_json("file-manager", f"move:{relpath}", "POST",
            move_endpoint(transport.identity[0]),
            finished, body={"source": f"{row.root}/{row.relpath}", "dest": f"{row.root}/{target}"},
            replace=True, category="command")
        return True

    def upload_file(self, local_path: str, overwrite: bool = False) -> bool:
        """Snapshot 3: multipart POST server/files/upload — LOCAL
        gcode files only (the author's ruling: sliced prints upload
        from the Preview view already). Lands in the CURRENT
        directory so it appears where the user is looking. A name
        collision needs the overwrite nod (the host silently
        overwrites)."""
        generation = self._generation
        local_path = str(local_path or "")
        name = os.path.basename(local_path)
        if not is_gcode_name(name):
            # The verdict must answer even on an early refusal —
            # otherwise the popup hangs on "uploading" (the author's
            # live report).
            self.uploadFinished.emit(False, "Only gcode files upload here.")
            self.note.emit("Upload refused: only gcode files upload here.")
            return False
        relpath = upload_relpath("/".join(self._directory), name)
        if not overwrite and name_collides(list(self._rows.values()), relpath):
            self.uploadFinished.emit(False, f"A file named {name} already exists.")
            self.note.emit(f"Upload refused: a file named {name} already exists.")
            return False
        transport = self._client.transport
        try:
            source = QFile(local_path)
            if not source.open(QIODevice.OpenModeFlag.ReadOnly):
                raise OSError(source.errorString())
            multipart = QHttpMultiPart(QHttpMultiPart.ContentType.FormDataType)
            part = QHttpPart()
            part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                           QVariant(f'form-data; name="file"; filename="{name}"'))
            part.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, QVariant("application/octet-stream"))
            part.setBodyDevice(source)
            multipart.append(part)
            fields = {"root": "gcodes"}
            if self._directory:
                fields["path"] = "/".join(self._directory)
            for field, value in fields.items():
                part = QHttpPart()
                part.setHeader(QNetworkRequest.KnownHeaders.ContentDispositionHeader,
                               QVariant(f'form-data; name="{field}"'))
                part.setBody(QByteArray(value.encode()))
                multipart.append(part)
            request = transport.request("server/files/upload", timeout_ms=30000)
            reply = transport.network.post(request, multipart)
            # The same raw-reply ownership law as thumbnails: the
            # reply rides the registry into a bound-method handler;
            # the multipart and file device live as the reply's
            # children so nothing dies mid-stream.
            multipart.setParent(reply)
            source.setParent(reply)
            self._upload_replies[relpath] = reply
            reply.uploadProgress.connect(
                lambda sent, total, g=generation: self.uploadProgress.emit(
                    max(0, min(100, int(sent * 100 / total))))
                if total > 0 and g == self._generation else None)
            reply.finished.connect(
                lambda r=reply, p=relpath, g=generation, n=name: self._upload_finished(p, r, g, n)
            )
            return True
        except Exception as error:
            self.uploadFinished.emit(False, str(error))
            self.note.emit(f"Upload refused: {error}")
            return False

    def _upload_finished(self, relpath: str, reply, generation: int, name: str) -> None:
        if self._upload_replies.get(relpath) is reply:
            self._upload_replies.pop(relpath, None)
        if generation != self._generation:
            # The printer changed mid-upload: the popup must resolve
            # with a verdict, not hang.
            self.uploadFinished.emit(False, "The printer changed during the upload.")
            return
        if reply.error() != QNetworkReply.NetworkError.NoError:
            detail = reply.errorString()
            try:
                body = json.loads(bytes(reply.readAll()).decode("utf-8", errors="replace"))
                words = _moonraker_error_text(body) if isinstance(body, dict) else ""
                if words:
                    detail = words
            except Exception:
                pass
            self.uploadFinished.emit(False, detail)
            self.note.emit(f"Upload refused: {detail}")
            return
        self.uploadFinished.emit(True, name)
        self.note.emit(f"Uploaded {name}.")
        self.changed.emit()
        self.refresh()

    def _abort_uploads(self) -> None:
        for reply in list(self._upload_replies.values()):
            try:
                reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass
        self._upload_replies = {}

    def rename_directory(self, directory: str, new_name: Any, overwrite: bool = False) -> bool:
        """Snapshot 3: rename a FOLDER (the author's live request —
        right-click a breadcrumb segment or a strip chip). The host's
        move takes {source, dest} directory paths; on success the
        resident rows, dirs, the current path, the selection and the
        thumb cache all rekey under the new prefix."""
        generation = self._generation
        directory = str(directory).strip("/")
        if not directory:
            self.note.emit("Rename refused: the root cannot be renamed.")
            return False
        target = rename_path(directory, new_name)
        if target is None:
            self.note.emit("Rename refused: the name is empty, unchanged, or not a plain name.")
            return False
        if not overwrite and path_collides(self._dirs, target):
            self.note.emit(f"Rename refused: a folder named {str(new_name).strip()} already exists.")
            return False
        transport = self._client.transport

        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            if error:
                self.note.emit(f"Rename refused: {error}")
                return
            self._rekey_directory(directory, target)
            self.note.emit(f"Renamed folder to {target.rsplit('/', 1)[-1]}.")
            self.changed.emit()
            self.refresh()
        transport.send_json("file-manager", f"move:{directory}", "POST",
            move_endpoint(transport.identity[0]),
            finished, body={"source": f"gcodes/{directory}", "dest": f"gcodes/{target}"},
            replace=True, category="command")
        return True

    def delete_directory(self, directory: str) -> bool:
        """Snapshot 3: delete a FOLDER (the author's live request).
        The host's DELETE handles directories; on success the rows
        and dirs under it drop, and the view pops to the parent when
        it sat inside the deleted tree."""
        generation = self._generation
        directory = str(directory).strip("/")
        if not directory:
            self.note.emit("Delete refused: the root cannot be deleted.")
            return False
        transport = self._client.transport

        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            if error:
                self.note.emit(f"Delete refused: {error}")
                return
            prefix = directory + "/"
            self._rows = {key: row for key, row in self._rows.items()
                          if not key.startswith(f"gcodes/{prefix}")}
            self._dirs = {path for path in self._dirs
                          if path != directory and not path.startswith(prefix)}
            current = "/".join(self._directory)
            if current == directory or current.startswith(prefix):
                self._directory = directory.split("/")[:-1]
            self.note.emit(f"Deleted folder {directory.rsplit('/', 1)[-1]}.")
            self.changed.emit()
            self.refresh()
        transport.send_json("file-manager", f"delete:{directory}", "DELETE",
            directory_delete_endpoint(transport.identity[0], "gcodes", directory),
            finished, replace=True, category="command")
        return True

    def create_directory(self, name: str) -> None:
        """Create a folder in the current directory (the author's
        live request): the host's directory route takes the full
        target path."""
        name = str(name or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            self.note.emit("Create refused: enter a plain folder name.")
            return
        directory = "/".join(self._directory)
        target = f"{directory}/{name}" if directory else name
        generation = self._generation
        transport = self._client.transport

        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            if error:
                self.note.emit(f"Create refused: {error}")
                self.changed.emit()
                return
            self.note.emit(f"Created folder {name}.")
            self.refresh()

        transport.send_json("file-manager", f"mkdir:{target}", "POST",
            directory_create_endpoint(transport.identity[0], "gcodes", target),
            finished, replace=True, category="command")

    def _rekey_directory(self, directory: str, target: str) -> None:
        """Point every resident path under ``directory`` at ``target``
        (the rename's local bookkeeping — the walk re-confirms)."""
        prefix = directory + "/"
        moved_rows = {}
        for key, row in list(self._rows.items()):
            if not key.startswith(f"gcodes/{prefix}"):
                continue
            row = FileRow(**{**vars(row), "relpath": target + row.relpath[len(directory):]})
            moved_rows[f"gcodes/{row.relpath}"] = row
            self._rows.pop(key, None)
        self._rows.update(moved_rows)
        self._dirs = {(target + path[len(directory):] if path == directory or path.startswith(prefix) else path)
                      for path in self._dirs}
        current = "/".join(self._directory)
        if current == directory or current.startswith(prefix):
            current = target + current[len(directory):]
            self._directory = current.split("/") if current else []
        moved_thumbs = {}
        for relpath, entry in list(self._thumbs.items()):
            if relpath == directory or relpath.startswith(prefix):
                moved_thumbs[target + relpath[len(directory):]] = entry
            else:
                moved_thumbs[relpath] = entry
        self._thumbs = moved_thumbs
        moved_selection = {target + relpath[len(directory):] for relpath in self._selection
                           if relpath.startswith(prefix)}
        self._selection = ({relpath for relpath in self._selection if not relpath.startswith(prefix)}
                           | moved_selection)

    def start_print(self, relpath: str) -> None:
        """POST printer/print/start with the root-exclusive filename
        (round-2 D4). Success is NEVER the reply: a print_stats
        transition with the expected filename is — the model watches
        the snapshot. The request rides the binding's current
        identity, so the POST re-validates it (the author's ruling).
        """
        generation = self._generation
        relpath = str(relpath)
        def finished(payload, error) -> None:
            if generation != self._generation:
                return
            self._print_attempt = None if error else (relpath, time.time())
            self.changed.emit()
        self._print_attempt = (relpath, time.time())
        self._client.transport.send_json("file-manager", f"print:{relpath}", "POST",
            print_start_endpoint(self._client.transport.identity[0], relpath),
            finished, replace=True, category="command")

    @property
    def print_attempt(self):
        """(relpath, started_at) of the awaited print transition, or
        None. The model clears it when print_stats.filename matches."""
        return self._print_attempt

    def clear_print_attempt(self) -> None:
        self._print_attempt = None
        self.changed.emit()

    def request_thumbnails(self, rows: Sequence[FileRow], large: bool = False) -> None:
        """The visible rows' thumbnails: one one-shot fetch per row
        per variant, following the METADATA's thumbnail relative_path
        (``.thumbs/<name>-<size>.png``). The plain ``<file>.png``
        sibling does not exist — a live Moonraker answers it 404 for
        every file. Rows whose metadata has no thumbnail record as
        "none" (the placeholder) and are never fetched. The cache is
        keyed by relpath with TWO slots: the list cells fetch the
        small (>= 32 px) thumbnail — the largest preview's UI-thread
        decode stutters the list — and the print dialog asks for the
        large one with ``large=True``. A refresh clears the cache;
        thumbnails regenerate with the file."""
        changed_any = False
        for row in rows:
            relpath = str(row.relpath)
            entry = self._thumbs.get(relpath) or {}
            slot = "state_large" if large else "state"
            url_slot = "url_large" if large else "url"
            if entry.get(slot):
                continue
            changed_any = True
            thumb_path = row.thumb_path if large else (row.thumb_small or row.thumb_path)
            entry[slot] = "none" if not thumb_path else "loading"
            entry[url_slot] = ""
            self._thumbs[relpath] = entry
            if thumb_path:
                # Enqueue, never fetch directly: the queue drains at a
                # bounded concurrency and the hourglass spins until the
                # callback lands.
                self._thumb_queue.append((relpath, row.root, thumb_path, large))
        if changed_any:
            self.thumbsChanged.emit()
        self._drain_thumbs()

    def _drain_thumbs(self) -> None:
        while self._thumb_active < self.MAX_THUMB_FETCHES and self._thumb_queue:
            relpath, root, thumb_path, large = self._thumb_queue.pop(0)
            self._thumb_active += 1
            self._fetch_thumb(relpath, root, thumb_path, large)

    def _fetch_thumb(self, relpath: str, root: str, thumb_path: str, large: bool) -> None:
        generation = self._thumb_generation
        try:
            directory = tempfile.mkdtemp(prefix="thumb-", dir=self._thumb_root)
            path = os.path.join(directory, os.path.basename(thumb_path) or "thumb.png")
            # relative_path is relative to the gcode FILE's parent: a
            # folder-resident file's thumbnail lives under
            # <root>/<dirname(relpath)>/.thumbs/, not <root>/.thumbs/
            # (live-proven against Moonraker's metadata builder).
            parent = relpath.rsplit("/", 1)[0] if "/" in relpath else ""
            prefix = f"{quote(root, safe='/')}/{quote(parent, safe='/')}" if parent else quote(root, safe='/')
            request = self._client.transport.request(
                f"server/files/{prefix}/{quote(thumb_path, safe='/')}", timeout_ms=10000)
            request.setRawHeader(b"Accept", b"image/png")
            reply = self._client.transport.network.get(request)
            # The reply rides the registry until its handler runs, and
            # the signal connects into a BOUND method — the transport's
            # own lifetime pattern (MoonrakerTransport.send_json). A
            # bare closure connected to QNetworkReply.finished is a
            # use-after-free trap in PyQt: the author's live crash
            # report was a SIGSEGV in PyQtSlot::call on the main
            # thread, delivered from a QtNetwork signal right after
            # the popup opened. All fetch state (the reply, relpath,
            # target path, generation) binds as a default argument so
            # nothing can be collected mid-flight.
            self._thumb_replies[relpath] = reply
            reply.finished.connect(
                lambda r=reply, p=relpath, g=generation, t=path, l=large: self._thumb_finished(p, r, g, t, l)
            )
        except Exception:
            entry = self._thumbs.get(relpath) or {}
            if large:
                entry["state_large"] = "failed"
            else:
                entry["state"] = "failed"
            self._thumbs[relpath] = entry
            self.changed.emit()
            # The slot must always come back, whatever failed — a
            # leaked permit is a permanently stuck queue.
            self._thumb_active = max(0, self._thumb_active - 1)
            self._drain_thumbs()

    def _thumb_finished(self, relpath: str, reply, generation: int, path: str, large: bool) -> None:
        # The identity check keeps a stale reply (a refresh cleared
        # the cache while it was still in flight) from unregistering
        # its successor's fetch under the same key.
        if self._thumb_replies.get(relpath) is reply:
            self._thumb_replies.pop(relpath, None)
        # One slot frees: the queue drains the next waiting row.
        self._thumb_active = max(0, self._thumb_active - 1)
        if generation != self._thumb_generation:
            # The cache was cleared (refresh) or the printer changed:
            # this reply's bytes belong to the previous view.
            self._drain_thumbs()
            return
        try:
            # PyQt6 enum comparison: ``error() != 0`` is ALWAYS true —
            # the enum members never equal plain ints, so the success
            # path raised on every fetch (the author's live report:
            # no thumbnails, all cells failed). Compare against the
            # enum itself, the transport's form.
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError("thumbnail fetch failed")
            data = bytes(reply.readAll())
            # No thumbnail to display: the hourglass must
            # NEVER spin forever (the author's live ruling) —
            # an empty or non-PNG body falls back to the
            # placeholder like any failure.
            if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
                raise ValueError("no thumbnail")
            with open(path, "wb") as handle:
                handle.write(data)
            entry = self._thumbs.get(relpath) or {}
            if large:
                entry["state_large"] = "ready"
                entry["url_large"] = QUrl.fromLocalFile(path).toString()
            else:
                entry["state"] = "ready"
                entry["url"] = QUrl.fromLocalFile(path).toString()
            self._thumbs[relpath] = entry
        except Exception:
            entry = self._thumbs.get(relpath) or {}
            if large:
                entry["state_large"] = "failed"
            else:
                entry["state"] = "failed"
            self._thumbs[relpath] = entry
        try:
            reply.deleteLater()
        except Exception:
            pass
        self.thumbsChanged.emit()
        # One slot frees: the queue drains the next waiting row
        # immediately — the fetches fire together, not chained.
        self._drain_thumbs()

    def _abort_thumbs(self) -> None:
        """Hard-stop every in-flight thumbnail fetch (printer change or
        shutdown). Abort fires each reply's finished handler, which
        drains the registry itself."""
        for reply in list(self._thumb_replies.values()):
            try:
                reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass
        self._thumb_replies = {}
        self._thumb_queue = []
        self._thumb_active = 0

    def thumbnail_payload(self) -> Dict[str, Dict[str, str]]:
        return {relpath: dict(entry) for relpath, entry in self._thumbs.items()}

    def clear_thumbnails(self) -> None:
        # In-flight fetches retire through the generation guard — the
        # cache alone resets here, so a refresh genuinely regenerates
        # every thumbnail with the file. The temp tree goes with the
        # cache: the published file:// URLs die in the same publish,
        # and stale replies write nothing (the generation guard runs
        # first).
        self._thumb_generation += 1
        self._thumbs = {}
        self._thumb_queue = []
        self._thumb_active = 0
        try:
            shutil.rmtree(self._thumb_root, ignore_errors=True)
        except Exception:
            pass
        self._thumb_root = tempfile.mkdtemp(prefix="mpf-thumbs-")
        self.thumbsChanged.emit()

    def _rejoin_history(self) -> None:
        joined = attempts_for(list(self._rows.values()), self._history)
        self._rows = {f"gcodes/{row.relpath}": row for row in joined}

    # ---- navigation and view --------------------------------------

    @property
    def directory(self) -> List[str]:
        return list(self._directory)

    def navigate_to(self, segments: Sequence[str]) -> None:
        self._directory = [str(segment) for segment in segments if str(segment)]
        self._view.page = 1
        self.changed.emit()

    def navigate_up(self) -> None:
        self._directory = self._directory[:-1]
        self._view.page = 1
        self.changed.emit()

    def subdirectories(self) -> List[str]:
        """The folder strip's contents: direct children of the current
        directory, basenames, alphabetical. Directories never join
        the file rows — they carry no print metadata (the author's
        ruling). Hidden while a search is active (the scope is then
        the whole tree, not a directory)."""
        if self._view.search:
            return []
        prefix = f"{'/'.join(self._directory)}/" if self._directory else ""
        children = []
        for path in self._dirs:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            if "/" not in rest:
                children.append(rest)
        return sorted(children, key=str.casefold)

    @property
    def view(self) -> ViewState:
        return self._view

    def current_rows(self) -> List[FileRow]:
        """Filter → search → sort over the right scope: the whole tree
        while a search is active (the author's global-search ruling),
        otherwise the CURRENT level only — a directory shows its own
        files, never a recursive aggregate of the subtree (the
        author's live ruling)."""
        if self._view.search:
            base = list(self._rows.values())
        else:
            prefix = f"{'/'.join(self._directory)}/" if self._directory else ""
            base = [row for row in self._rows.values()
                    if row.relpath.startswith(prefix)
                    and "/" not in row.relpath[len(prefix):]]
        return self._view.apply(base, now=time.time())

    def page_rows(self) -> List[FileRow]:
        return page_slice(self.current_rows(), self._view.page, self._view.page_size)

    def total_count(self) -> int:
        return len(self.current_rows())

    def page_index(self) -> int:
        # clamp_page's `total` is the ROW count (it derives the last
        # page itself) — passing the page count double-counted and
        # clamped every listing over one page back to page 1 (the
        # author's dead carousel, bisected in the probe).
        return clamp_page(self._view.page, len(self.current_rows()), self._view.page_size)

    def page_number(self) -> int:
        return page_count(len(self.current_rows()), self._view.page_size)

    def empty_state(self) -> str:
        return empty_kind(len(self._rows), len(self.current_rows()))

    # ---- selection -------------------------------------------------

    @property
    def selection(self) -> Set[str]:
        return set(self._selection)

    def toggle_selection(self, relpath: str) -> None:
        if relpath in self._selection:
            self._selection.discard(relpath)
        else:
            self._selection.add(relpath)
        self.changed.emit()

    def clear_selection(self) -> None:
        self._selection = set()
        self.changed.emit()

    def toggle_page_selection(self) -> None:
        """The header checkbox: select the whole page, or drop it
        entirely when the page is already all-selected (partial pages
        fill up — the author's ruling on its three states)."""
        page_rows = self.page_rows()
        relpaths = {row.relpath for row in page_rows}
        if relpaths and relpaths <= self._selection:
            self._selection -= relpaths
        else:
            self._selection |= relpaths
        self.changed.emit()

    def selection_state(self, page_rows: Sequence[FileRow]) -> str:
        return page_selection_state(page_rows, self._selection)

    # ---- recents ---------------------------------------------------

    def recents(self) -> List[Dict[str, Any]]:
        """The strip's entries, joined onto the resident rows so the
        cards can show the row's thumbnail (the author's live request
        — the metadata is already in hand)."""
        entries = recent_prints(self._history)
        for entry in entries:
            row = self._rows.get(f"gcodes/{entry['filename']}")
            entry["relpath"] = row.relpath if row is not None else ""
            entry["thumb"] = bool(row is not None and row.thumb_path)
        return entries

    # ---- accessors -------------------------------------------------

    @property
    def rows_resident(self) -> int:
        return len(self._rows)

    def resident_directories(self) -> Set[str]:
        return set(self._dirs)

    # ---- column config (Snapshot 3) --------------------------------

    def column_state(self) -> Dict[str, Any]:
        """The persisted block: widths hold only user-set values; the
        order is the pinned sequence until changed."""
        return {"widths": dict(self._column_widths), "order": list(self._column_order),
                "hidden": sorted(self._column_hidden)}

    def set_column_state(self, state: Any) -> None:
        """Rehydrate from the state file (the model reads the file;
        the service owns the values)."""
        columns = normalise_columns(state)
        self._column_widths = dict(columns["widths"])
        self._column_order = list(columns["order"])
        self._column_hidden = set(columns["hidden"])

    def column_widths(self) -> Dict[str, float]:
        return dict(self._column_widths)

    def column_order(self) -> List[str]:
        return list(self._column_order)

    def column_hidden(self) -> List[str]:
        return sorted(self._column_hidden)

    def set_column_width(self, key: str, width: Any) -> bool:
        """One width per column key, clamped to the policy's band.
        Returns True when anything changed (the model saves + publishes)."""
        key = str(key)
        try:
            width = max(COLUMN_WIDTH_MIN, min(COLUMN_WIDTH_MAX, float(width)))
        except (TypeError, ValueError):
            return False
        if self._column_widths.get(key) == width:
            return False
        self._column_widths[key] = width
        self.changed.emit()
        return True

    def set_column_order(self, order: Any) -> bool:
        """The trailing columns' sequence. Unknown names drop; missing
        ones fill from the previous order."""
        known = set(TRAILING_COLUMN_ORDER)
        incoming = [str(name) for name in (order or []) if name in known]
        incoming += [name for name in self._column_order if name not in incoming]
        if incoming == self._column_order:
            return False
        self._column_order = incoming
        self.changed.emit()
        return True

    def set_column_visible(self, key: str, visible: bool) -> bool:
        key = str(key)
        hidden = set(self._column_hidden)
        if visible:
            hidden.discard(key)
        else:
            hidden.add(key)
        if hidden == self._column_hidden:
            return False
        self._column_hidden = hidden
        self.changed.emit()
        return True

    def resident_rows(self) -> List[FileRow]:
        """The whole resident listing (filter-option counts are
        computed over this, not the filtered slice — round-2 UX)."""
        return list(self._rows.values())

    @property
    def history_loaded(self) -> int:
        return len(self._history)

    @property
    def history_exhausted(self) -> bool:
        return self._history_exhausted

    @property
    def disk_usage(self) -> Dict[str, int]:
        return dict(self._disk_usage)

    @property
    def refreshed_at(self) -> Optional[float]:
        return self._refreshed_at

    @property
    def walk_error(self) -> Optional[str]:
        return self._walk_error

    @property
    def active(self) -> bool:
        return self._rows or self._walk_error is not None


def _row_from_metadata(relpath: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Re-read a single file's metadata into a row (post-metascan)."""
    from .FileManagerPolicy import as_float, as_int, as_str, thumbnail_path
    name = relpath.rsplit("/", 1)[-1]
    return {
        "filename": name,
        "relpath": relpath,
        "root": "gcodes",
        "modified": as_float(data.get("modified")),
        "size": as_int(data.get("size")),
        "object_height": as_float(data.get("object_height")),
        "layer_height": as_float(data.get("layer_height")),
        "estimated_time": as_float(data.get("estimated_time")),
        "slicer": as_str(data.get("slicer")),
        "extruder": as_float(data.get("first_layer_extr_temp")),
        "bed": as_float(data.get("first_layer_bed_temp")),
        "filament": as_float(data.get("filament_total")),
        "thumb_path": thumbnail_path(data.get("thumbnails")),
        "print_start_time": as_float(data.get("print_start_time")),
    }
