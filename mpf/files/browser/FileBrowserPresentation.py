"""File-browser drafts, actions and cached row projections.

The backend owns remote operations; this object owns the popup's transient
state. Printer dispatch is an injected capability, not access to the Monitor
model. The facade remains the sole public QML slot surface. Synchronous change
notifications preserve click/confirmation ordering and immutable row payloads.
"""
from __future__ import annotations

from datetime import datetime
import logging
import os
import time
from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from .FileFormatting import file_disk_text, file_row_payload, file_timestamp
from .FileManagerPolicy import (delete_candidates, is_gcode_name, name_collides,
                                path_collides, rename_path, rename_target, upload_relpath)
from .FilesViewModel import FilesViewModel


class FileBrowserPresentation(QObject):
    changed = pyqtSignal()

    def __init__(self, manager, *, snapshot, printer_name, start_print, note, save_columns, parent=None):
        super().__init__(parent)
        self._file_manager = manager
        self._snapshot = snapshot
        self._printer_name = printer_name
        self._dispatch_print = start_print
        self._note = note
        self._save_columns = save_columns
        self._file_print_confirm = None
        self._file_delete_confirm = None
        self._file_rename_target = None
        self._file_rename_conflict = False
        self._file_upload_confirm = None
        self._file_upload_progress = None
        self._file_manager_note = ""
        self._file_manager_open = False
        self._files_model = FilesViewModel(self)
        self._files_model_rev = -1
        manager.uploadProgress.connect(self._on_upload_progress)
        manager.uploadFinished.connect(self._on_upload_finished)

    def set_note(self, text):
        self._file_manager_note = str(text)
        self.changed.emit()

    def values(self):
        """The file-owned portion of a Monitor publication, without network dispatch."""
        fm = self._file_manager
        values = self._file_manager_values()
        values.update(
            fileManagerOpen=self._file_manager_open,
            filePrintConfirm=self._file_print_confirm or "",
            fileDeleteConfirm=self._file_delete_confirm or "",
            fileRenameTarget=self._file_rename_target or "",
            fileRenameConflict=bool(self._file_rename_conflict),
            fileUploadConfirm=self._file_upload_confirm or "",
            fileUploadProgress=self._file_upload_progress or "",
            fileManagerColumnWidths=fm.column_widths(),
            fileManagerColumnOrder=fm.column_order(),
            fileManagerColumnHidden=fm.column_hidden(),
            fileManagerThumbs=fm.thumbnail_payload() if self._file_manager_open else {},
            fileManagerNote=self._file_manager_note,
        )
        return values

    def request_recent_thumbnails(self):
        """Only the bounded recents strip; grid thumbnails follow its viewport."""
        if self._file_manager_open:
            rows = []
            for item in self._file_manager.recents():
                row = self._file_manager.row_for(item.get("relpath") or "") if item.get("relpath") else None
                if row is not None and row.thumb_path:
                    rows.append(row)
            self._file_manager.request_thumbnails(rows)

    def fileRequestPrint(self, relpath):
        payload = self._file_print_confirm_payload(str(relpath))
        if payload is None:
            return
        # The confirmation shows a LARGE thumbnail: make sure THIS
        # row's large variant is fetched even when it was opened from
        # Recents — the page-driven cache covers visible rows' small
        # variants only.
        row = self._file_manager.row_for(payload["relpath"])
        if row is not None and row.thumb_path:
            self._file_manager.request_thumbnails([row], large=True)
        self._file_print_confirm = payload
        self.changed.emit()

    def fileConfirmPrint(self):
        confirm = self._file_print_confirm
        self._file_print_confirm = None
        if confirm:
            # The dispatch gate (4.2.0, S3/N2): the dialog's click was
            # checked when it OPENED; the dispatch re-checks the
            # CURRENT observation — a print started by another client
            # in the window must refuse here, not at Moonraker.
            if not self._dispatch_print(confirm["relpath"]):
                self.changed.emit()
                return
            # The print is on its way: the file manager steps aside
            # NOW and the monitor view returns — the verdict (success
            # or failure) reports to the console and the note line,
            # never by waiting inside the popup.
            self.setFileManagerOpen(False)
        self.changed.emit()

    def fileCancelPrint(self):
        self._file_print_confirm = None
        self.changed.emit()

    def setFileManagerOpen(self, is_open):
        self._file_manager_open = bool(is_open)
        if not self._file_manager_open:
            self._file_print_confirm = ""
            self._file_delete_confirm = ""
            self._file_rename_target = ""
            self._file_upload_confirm = ""
            self._file_upload_progress = ""
        self.changed.emit()

    def fileClearWalkError(self):
        self._file_manager.clear_walk_error()
        self.changed.emit()

    def openFileManager(self):
        # The open trigger: the flag gates the heavy payload work
        # (the live report: the closed popup must not keep
        # paying the per-poll cost).
        self._file_manager_open = True
        self._file_manager_note = ""
        try:
            self._file_manager.bind()
            self._file_manager.open()
        except Exception:
            # A dying slot freezes the whole popup silently (Qt
            # swallows the traceback) — surface it in the walk-error
            # state instead of leaving "Loading files…" forever.
            logging.getLogger(__name__).exception("file manager open failed")
            self._file_manager.changed.emit()

    def refreshFileManager(self):
        try:
            self._file_manager.open()
        except Exception:
            logging.getLogger(__name__).exception("file manager refresh failed")
            self._file_manager.changed.emit()

    def fileNavigateTo(self, segments, isUp):
        if isUp:
            self._file_manager.navigate_up()
        else:
            self._file_manager.navigate_to([str(segment) for segment in segments])

    def setFileSearch(self, query):
        self._file_manager.view.change_search(str(query))
        self._file_manager.changed.emit()

    def setFileSort(self, column):
        self._file_manager.view.change_sort(str(column))
        self._file_manager.changed.emit()

    def setFilePageSize(self, size):
        self._file_manager.view.change_page_size("all" if size == "all" else int(size))
        self._file_manager.changed.emit()

    def setFilePage(self, page):
        self._file_manager.view.page = max(1, int(page))
        self._file_manager.changed.emit()

    def setFileFilter(self, category, values):
        category = str(category)
        values = list(values)
        filters = dict(self._file_manager.view.filters)
        if category in ("modified", "print_time"):
            # Single-value categories store a SCALAR (the
            # live report: Print time filtered nothing — the policy
            # does float(["30"]) and the TypeError fallback matched
            # every row; Modified's window lookup failed the same
            # way). One value, or None to clear.
            filters[category] = values[0] if values else None
        else:
            filters[category] = values
        self._file_manager.view.change_filters(filters)
        self._file_manager.changed.emit()

    def clearFileFilters(self):
        self._file_manager.view.change_filters({})
        self._file_manager.changed.emit()

    def toggleFileSelection(self, relpath):
        self._file_manager.toggle_selection(str(relpath))

    def clearFileSelection(self):
        self._file_manager.clear_selection()

    def toggleFilePageSelection(self):
        self._file_manager.toggle_page_selection()

    def fileLoadAllHistory(self):
        self._file_manager.load_all_history()

    def fileScanMetadata(self, relpath):
        self._file_manager.scan_metadata(str(relpath))

    def fileRequestDelete(self):
        """The bulk delete from the selection (Snapshot 3): the
        currently-printing file is never offered (the gate
        — the host 403s it anyway, and the client must not ask)."""
        rows = self._file_manager.resident_rows()
        selected = [row for row in rows if row.relpath in self._file_manager.selection]
        candidates = delete_candidates(selected, self._printing_relpath())
        if not candidates:
            return
        self._file_delete_confirm = {
            "kind": "file",
            "relpaths": [row.relpath for row in candidates],
            "count": len(candidates),
            "first": candidates[0].filename,
            "blocked": len(selected) - len(candidates),
        }
        self.changed.emit()

    def fileRequestDeleteFile(self, relpath):
        row = self._file_manager.row_for(str(relpath))
        if row is None or row.relpath == self._printing_relpath():
            return
        self._file_delete_confirm = {
            "kind": "file",
            "relpaths": [row.relpath], "count": 1, "first": row.filename, "blocked": 0,
        }
        self.changed.emit()

    def fileCreateDirectory(self, name):
        """The popup's New-folder dialog: the service validates the
        name and owns the outcome notes."""
        self._file_manager.create_directory(name)
        self.changed.emit()

    def fileRequestDeleteDir(self, path):
        """The folder delete (a live request — right-click
        a breadcrumb segment or a strip chip)."""
        path = str(path).strip("/")
        if not path:
            return
        self._file_delete_confirm = {
            "kind": "dir", "path": path, "name": path.rsplit("/", 1)[-1],
            "relpaths": [], "count": 1, "first": path.rsplit("/", 1)[-1], "blocked": 0,
        }
        self.changed.emit()

    def fileConfirmDelete(self):
        confirm = self._file_delete_confirm
        self._file_delete_confirm = None
        if confirm:
            if confirm.get("kind") == "dir":
                self._file_manager.delete_directory(confirm["path"])
            else:
                self._file_manager.delete_files(confirm["relpaths"], self._printing_relpath())
        self.changed.emit()

    def fileCancelDelete(self):
        self._file_delete_confirm = None
        self.changed.emit()

    def fileRequestRename(self, relpath):
        row = self._file_manager.row_for(str(relpath))
        if row is None or row.relpath == self._printing_relpath():
            return
        self._file_rename_target = {"kind": "file", "path": row.relpath, "name": row.filename}
        self._file_rename_conflict = False
        self.changed.emit()

    def fileRequestRenameDir(self, path):
        """The folder rename (a live request — right-click
        a breadcrumb segment or a strip chip)."""
        path = str(path).strip("/")
        if not path:
            return
        self._file_rename_target = {"kind": "dir", "path": path,
                                    "name": path.rsplit("/", 1)[-1]}
        self._file_rename_conflict = False
        self.changed.emit()

    def filePreviewRename(self, name):
        """The live collision check while the name is typed (round-1
        C2/C3: the host's move silently overwrites — the dialog asks
        first)."""
        target = self._file_rename_target
        if not target:
            return
        # REPLACE, never mutate (the same publish-contract rule as
        # the upload progress).
        self._file_rename_target = dict(target, name=str(name))
        target = self._file_rename_target
        if target.get("kind") == "dir":
            proposal = rename_path(target["path"], name)
            self._file_rename_conflict = bool(
                proposal is not None
                and path_collides(self._file_manager.resident_directories(), proposal))
        else:
            row = self._file_manager.row_for(target["path"])
            proposal = rename_target(row, name) if row is not None else None
            self._file_rename_conflict = bool(
                proposal is not None and name_collides(self._file_manager.resident_rows(), proposal))
        self.changed.emit()

    def fileConfirmRename(self):
        target = self._file_rename_target
        self._file_rename_target = None
        if target:
            if target.get("kind") == "dir":
                self._file_manager.rename_directory(
                    target["path"], target["name"], overwrite=bool(self._file_rename_conflict))
            else:
                self._file_manager.rename_file(
                    target["path"], target["name"], self._printing_relpath(),
                    overwrite=bool(self._file_rename_conflict))
        self._file_rename_conflict = False
        self.changed.emit()

    def fileCancelRename(self):
        self._file_rename_target = None
        self._file_rename_conflict = False
        self.changed.emit()

    def fileUpload(self, path):
        """Snapshot 3 upload (the ruling): LOCAL gcode files
        only — sliced prints already upload from the Preview view.
        A name collision asks first; otherwise the upload runs."""
        # The picker hands over a file:// URL — the service wants a
        # local path (a plain path passes through unchanged).
        path = QUrl(str(path or "")).toLocalFile() or str(path or "")
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if not is_gcode_name(name):
            self._note("Upload refused: only gcode files upload here.")
            return
        target = upload_relpath("/".join(self._file_manager.directory), name)
        if target == self._printing_relpath():
            # The host streams the printing file from disk: replacing
            # it mid-print truncates the running job.
            self._note(f"Upload refused: {name} is currently printing.")
            self.changed.emit()
            return
        try:
            free = self._file_manager.disk_usage.get("free")
            free = int(free) if free is not None else None
            needed = os.path.getsize(path)
        except (TypeError, ValueError, OSError):
            free, needed = None, 0
        # None means the walk never reported disk usage — the check
        # stands down. A REAL zero (a full disk) still refuses: the
        # old guard treated the two alike and a missing report
        # disabled the check (the adversarial round's catch).
        if free is not None and needed and needed > free:
            self._note(f"Upload refused: {name} needs {needed / 1048576:.0f} MB, "
                               f"{free / 1048576:.0f} MB free on the printer.")
            self.changed.emit()
            return
        if name_collides(self._file_manager.resident_rows(), target):
            self._file_upload_confirm = {"path": path, "filename": name}
            self.changed.emit()
            return
        self._start_upload(path, name)
        self.changed.emit()

    def fileConfirmUpload(self):
        confirm = self._file_upload_confirm
        self._file_upload_confirm = None
        if confirm:
            self._start_upload(confirm["path"], confirm["filename"], overwrite=True)
        self.changed.emit()

    def fileUploadDismiss(self):
        self._file_upload_progress = None
        self.changed.emit()

    def fileRequestVisibleThumbnails(self, relpaths):
        """The render window's thumbnails (the live report:
        the page-wide fetch fired hundreds of requests on "all /
        page" — the QML's visible rows bound them)."""
        if not self._file_manager_open:
            return
        rows = []
        for relpath in (relpaths or []):
            row = self._file_manager.row_for(str(relpath))
            if row is not None and row.thumb_path:
                rows.append(row)
        self._file_manager.request_thumbnails(rows)

    def setFileColumnWidth(self, key, width):
        """Snapshot 3's column resize — the STATE lives in the file
        manager (the ruling: the file manager is its own
        thing, composed into the Monitor page)."""
        if self._file_manager.set_column_width(key, width):
            self._save_columns()

    def setFileColumnOrder(self, order):
        if self._file_manager.set_column_order(order):
            self._save_columns()

    def setFileColumnVisible(self, key, visible):
        if self._file_manager.set_column_visible(key, visible):
            self._save_columns()

    def fileCancelUpload(self):
        self._file_upload_confirm = None
        self.changed.emit()

    def _file_manager_values(self):
        fm = self._file_manager
        now = time.time()
        if not self._file_manager_open:
            # The popup is closed: the grid's bindings are inert, and
            # rebuilding the ROW payloads per poll is pure waste —
            # closing a 400-file listing stalled for seconds (the
            # live report). The cheap view state still
            # publishes (the view-mutation contract), only the heavy
            # rows/recents/thumbs/option-scan is skipped. Reopening
            # refills everything below.
            return {
                "fileManagerRows": [],
                "fileManagerRecents": [],
                "fileManagerDirectory": fm.directory,
                "fileManagerDirectories": fm.subdirectories(),
                "fileManagerDiskText": file_disk_text(fm.disk_usage),
                "fileManagerRefreshedAt": f"Last refreshed at {datetime.fromtimestamp(fm.refreshed_at).strftime('%H:%M')}" if fm.refreshed_at else "Not yet refreshed",
                "fileManagerShown": "",
                "fileManagerPage": f"Page {fm.page_index()} of {fm.page_number()}" if fm.view.page_size != "all" else "",
                "fileManagerPageIndex": fm.page_index(),
                "fileManagerPageCount": fm.page_number(),
                "fileManagerPageSize": str(fm.view.page_size),
                "fileManagerPageSelection": "none",
                "fileManagerEmptyKind": "",
                "fileManagerSelected": len(fm.selection),
                "fileManagerSortColumn": fm.view.sort_column,
                "fileManagerSortAscending": fm.view.sort_ascending,
                "fileManagerSearch": fm.view.search,
                "fileManagerFilters": {key: (list(value) if isinstance(value, (list, tuple)) else [value]) for key, value in fm.view.filters.items() if value},
                "fileManagerFilterCounts": {key: len(value) if isinstance(value, (list, tuple)) else 1 for key, value in fm.view.filters.items() if value},
                "fileManagerFilterOptions": {},
                "fileManagerHistoryLoaded": fm.history_loaded,
                "fileManagerHistoryExhausted": fm.history_exhausted,
                "fileManagerWalkError": fm.walk_error or "",
            }
        printing_relpath = self._printing_relpath()
        selection = fm.selection
        rows = []
        for row in fm.page_rows():
            payload = file_row_payload(row, now)
            payload["checked"] = row.relpath in selection
            payload["printing"] = row.relpath == printing_relpath
            rows.append(payload)
        # The view model rebuilds ONLY when the projection's revision
        # moves — the per-publish cost stays on the cached rows.
        if fm.projection_count != self._files_model_rev:
            self._files_model_rev = fm.projection_count
            self._files_model.set_rows(rows)
        total = fm.total_count()
        size = fm.view.page_size
        if size == "all":
            shown = f"Showing 1–{total} of {total}" if total else "Showing 0 of 0"
        else:
            start = (fm.page_index() - 1) * size + 1 if total else 0
            end = min(start + size - 1, total) if total else 0
            shown = f"Showing {start}–{end} of {total}"
        return {
            "fileManagerRows": rows,
            "fileManagerRecents": [{
                "name": item["filename"],
                "time": file_timestamp(item.get("end_time"), now),
                "relpath": item.get("relpath", ""),
                "thumb": bool(item.get("thumb")),
            } for item in fm.recents()],
            "fileManagerDirectory": fm.directory,
            "fileManagerDirectories": fm.subdirectories(),
            "fileManagerDiskText": file_disk_text(fm.disk_usage),
            "fileManagerRefreshedAt": f"Last refreshed at {datetime.fromtimestamp(fm.refreshed_at).strftime('%H:%M')}" if fm.refreshed_at else "Not yet refreshed",
            "fileManagerShown": shown,
            "fileManagerPage": f"Page {fm.page_index()} of {fm.page_number()}" if fm.view.page_size != "all" else "",
            "fileManagerPageIndex": fm.page_index(),
            "fileManagerPageCount": fm.page_number(),
            "fileManagerPageSize": str(fm.view.page_size),
            "fileManagerPageSelection": fm.selection_state(fm.page_rows()),
            "fileManagerEmptyKind": fm.empty_state(),
            "fileManagerSelected": len(selection),
            "fileManagerSortColumn": fm.view.sort_column,
            "fileManagerSortAscending": fm.view.sort_ascending,
            "fileManagerSearch": fm.view.search,
            "fileManagerFilters": {key: (list(value) if isinstance(value, (list, tuple)) else [value]) for key, value in fm.view.filters.items() if value},
            "fileManagerFilterCounts": {key: len(value) if isinstance(value, (list, tuple)) else 1 for key, value in fm.view.filters.items() if value},
            "fileManagerFilterOptions": fm.filter_option_counts_cached(now=now),
            "fileManagerHistoryLoaded": fm.history_loaded,
            "fileManagerHistoryExhausted": fm.history_exhausted,
            "fileManagerWalkError": fm.walk_error or "",
        }

    def _printing_relpath(self):
        # The ACTIVE print's root-exclusive relpath (round-2 D4), or
        # "" — the state filter is the load-bearing part: Klipper
        # never clears print_stats.filename on completion, so a
        # filename alone would keep the last-printed file badged
        # "printing" with Delete/Rename disabled forever.
        state = self._snapshot().core.get("print_stats") or {}
        if str(state.get("state") or "") not in ("printing", "paused"):
            return ""
        return str(state.get("filename") or "")

    def _file_print_confirm_payload(self, relpath):
        from .FileFormatting import file_duration_short, file_filament
        key = f"gcodes/{relpath}"
        row = {f"gcodes/{r.relpath}": r for r in self._file_manager.resident_rows()}.get(key)
        if row is None:
            return None
        klippy = self._snapshot().server.get("klippy_state")
        ready = str(klippy or "").lower() == "ready"
        homed = "xyz" == str((self._snapshot().auxiliary.get("toolhead") or {}).get("homed_axes") or "").lower()
        if ready and not homed:
            ready_text = "The printer is not homed — the print may not start."
        elif ready:
            ready_text = "Printer ready."
        else:
            ready_text = f"Printer state: {str(klippy or 'unknown').capitalize()}."
        return {
            "relpath": row.relpath,
            "name": row.filename,
            "est": file_duration_short(row.estimated_time),
            "filament": file_filament(row.filament),
            "printerName": self._printer_name(),
            "ready": ready and homed,
            "homed": homed,
            "readyText": ready_text,
        }

    def _start_upload(self, path, name, overwrite=False) -> None:
        # The popup's payload opens on the FIRST publish after this;
        # the service's progress and outcome signals drive it from
        # here on (a live request: a bar while it runs and
        # a success/fail verdict at the end).
        self._file_upload_progress = {"name": name, "percent": 0,
                                      "state": "uploading", "error": ""}
        # The overwrite nod MUST ride through: without it the service
        # re-refuses the colliding name and never emits a verdict —
        # the popup hung on "uploading" forever (the live
        # report).
        self._file_manager.upload_file(path, overwrite=overwrite)

    def _on_upload_progress(self, percent):
        if not self._file_upload_progress:
            return
        percent = int(percent)
        if percent == self._file_upload_progress["percent"]:
            return
        # REPLACE, never mutate: the publish contract treats the
        # stored dict as immutable (the QVariant cache keys on
        # identity), and an in-place edit serves a stale copy.
        self._file_upload_progress = dict(self._file_upload_progress, percent=percent)
        self.changed.emit()

    def _on_upload_finished(self, ok, detail):
        if not self._file_upload_progress:
            return
        if ok:
            self._file_upload_progress = dict(self._file_upload_progress,
                                              state="done", percent=100, error="")
        else:
            self._file_upload_progress = dict(self._file_upload_progress,
                                              state="failed", error=str(detail))
        self.changed.emit()
