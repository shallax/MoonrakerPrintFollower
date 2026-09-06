from __future__ import annotations

import os
import time
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QMessageBox
from UM.Logger import Logger
from .Core import OperationPhase


class PreviewLoadMixin:
    def _confirm_force_load_current_print(self) -> None:
        try:
            stage = self._controller.getActiveStage()
            stage_id = None
            if stage is not None:
                get_id = getattr(stage, "getId", None)
                if callable(get_id):
                    stage_id = get_id()
                if not stage_id:
                    stage_id = getattr(stage, "stageId", None)
            if stage_id != "PreviewStage":
                self._controller.setActiveStage("PreviewStage")
        except Exception as error:
            Logger.log("w", "Moonraker Print Follower could not switch to Preview before confirmation: %s", error)
        self._queue_lifecycle_callback(self._ask_force_load_question)

    def _ask_force_load_question(self) -> None:
        try:
            answer = QMessageBox.question(
                None,
                "Moonraker Print Follower",
                "Replace Cura contents?\n\n"
                "This will discard everything currently loaded in Cura and replace it "
                "with the G-code currently printing in Moonraker.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        except Exception as error:
            Logger.logException("e", "Moonraker Print Follower could not show load confirmation: %s", error)
            self._set_status(f"Could not show load confirmation: {error}")
            return
        if answer != QMessageBox.StandardButton.Yes:
            self._set_status("Load current print cancelled")
            return
        self._queue_lifecycle_callback(self._force_load_current_print)

    def _force_load_current_print(self) -> None:
        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL before loading the current print")
            return
        self._operation.reset(OperationPhase.RESOLVING)
        self._force_load_requested = True
        self._force_load_pending_filename = None
        self._set_status("Finding the current Moonraker print…")
        self._issue_status_request(base_url, self._pref_str(self.PREF_API_KEY), purpose="force_load")

    def _start_forced_gcode_download(self, filename: str) -> None:
        if not filename:
            self._force_load_requested = False
            self._set_status("Moonraker did not report a current G-code filename")
            return
        self._force_load_pending_filename = filename
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            self._set_status(f"{filename}: loading cached current print into Cura Preview…")
            self._queue_lifecycle_callback(lambda f=filename: self._load_cached_remote_gcode_forced(f))
            return
        if (
            self._file_reply is not None
            and self._file_reply.isRunning()
            and self._file_reply_filename == filename
            and self._file_reply_job_key == self._remote_job_key
        ):
            self._set_status(f"{filename}: downloading current print…")
            return
        if self._file_reply is not None:
            self._abort_file_reply()
        if self._begin_gcode_download(filename):
            self._set_status(f"{filename}: downloading current print…")
        else:
            self._force_load_requested = False
            self._force_load_pending_filename = None
            self._set_operation_phase(OperationPhase.ERROR, filename=filename)
            self._set_status(f"Could not start download of current print: {filename}")

    def _load_cached_remote_gcode_forced(self, filename: str) -> None:
        if (
            self._cached_gcode_filename != filename
            or not self._cached_gcode_path
            or self._cached_gcode_job_key != self._remote_job_key
            or not os.path.isfile(self._cached_gcode_path)
        ):
            self._force_load_requested = False
            self._force_load_pending_filename = None
            self._set_status("Current print was downloaded but could not be cached for Cura")
            return
        path = self._cached_gcode_path
        self._cancel_remote_index_build()
        self._cura_load_in_progress = True
        self._follow_controller.set_cura_suspended(True)
        self._cura_load_started_at = time.perf_counter()
        self._cura_load_path = path
        self._cura_load_filename = filename
        self._cura_load_job_key = self._cached_gcode_job_key
        try:
            Logger.log("i", "Moonraker Print Follower forcibly replacing Cura contents with remote G-code: %s", filename)
            self._application.readLocalFile(QUrl.fromLocalFile(path), add_to_recent_files=False)
            self._set_status(f"{filename}: loading into Cura Preview…")
        except Exception as error:
            self._cura_load_in_progress = False
            self._follow_controller.set_cura_suspended(False)
            self._cura_load_started_at = None
            self._cura_load_path = None
            self._cura_load_filename = None
            self._cura_load_job_key = None
            self._cleanup_deferred_cache_dirs()
            self._force_load_requested = False
            self._force_load_pending_filename = None
            Logger.logException("e", "Moonraker Print Follower could not force-load remote G-code into Cura: %s", error)
            self._set_status(f"Could not load current print into Cura: {error}")
