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
            Logger.log(
                "w",
                "Moonraker Print Follower could not switch to Preview before confirmation: %s",
                error,
            )
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
            Logger.logException(
                "e",
                "Moonraker Print Follower could not show load confirmation: %s",
                error,
            )
            self._set_status(f"Could not show load confirmation: {error}")
            return
        if answer != QMessageBox.StandardButton.Yes:
            self._set_status("Load current print cancelled")
            return
        self._queue_lifecycle_callback(self._force_load_current_print)

    def _force_load_current_print(self) -> None:
        config = self.current_printer_config()
        base_url = self._normalise_base_url(config.url)
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL before loading the current print")
            return
        self._operation.begin_force_load()
        self._set_status("Finding the current Moonraker print…")
        self._issue_status_request(base_url, config.api_key, purpose="force_load")

    def _start_forced_gcode_download(self, filename: str) -> None:
        if not filename:
            self._operation.cancel_force_load()
            self._set_status("Moonraker did not report a current G-code filename")
            return
        self._operation.set_force_load_filename(filename)
        if self._remote_file_service.cache_matches(filename, self._remote_job_service.key):
            path = self._remote_file_service.cached_path
            if path and os.path.isfile(path):
                self._set_status(
                    f"{filename}: loading cached current print into Cura Preview…"
                )
                self._queue_lifecycle_callback(
                    lambda f=filename: self._load_cached_remote_gcode_forced(f)
                )
                return
        if (
            self._file_reply is not None
            and self._file_reply.isRunning()
            and self._file_reply_filename == filename
            and self._file_reply_job_key == self._remote_job_service.key
        ):
            self._set_status(f"{filename}: downloading current print…")
            return
        if self._file_reply is not None:
            self._abort_file_reply()
        if self._begin_gcode_download(filename):
            self._set_status(f"{filename}: downloading current print…")
        else:
            self._operation.cancel_force_load()
            self._set_operation_phase(OperationPhase.ERROR, filename=filename)
            self._set_status(f"Could not start download of current print: {filename}")

    def _load_cached_remote_gcode_forced(self, filename: str) -> None:
        if not self._remote_file_service.cache_matches(
            filename, self._remote_job_service.key
        ):
            self._operation.cancel_force_load()
            self._set_status(
                "Current print was downloaded but could not be cached for Cura"
            )
            return
        path = self._remote_file_service.cached_path
        if not path or not os.path.isfile(path):
            self._operation.cancel_force_load()
            self._set_status(
                "Current print was downloaded but could not be cached for Cura"
            )
            return

        self._cancel_remote_index_build()
        self._follow_controller.set_cura_suspended(True)
        self._operation.begin_cura_load(
            filename=filename,
            job_key=self._remote_file_service.cached_job_key,
            local_path=path,
            started_at=time.perf_counter(),
        )
        try:
            Logger.log(
                "i",
                "Moonraker Print Follower forcibly replacing Cura contents with remote G-code: %s",
                filename,
            )
            self._application.readLocalFile(
                QUrl.fromLocalFile(path), add_to_recent_files=False
            )
            self._set_status(f"{filename}: loading into Cura Preview…")
        except Exception as error:
            self._follow_controller.set_cura_suspended(False)
            self._operation.reset(OperationPhase.ERROR)
            self._cleanup_deferred_cache_dirs()
            Logger.logException(
                "e",
                "Moonraker Print Follower could not force-load remote G-code into Cura: %s",
                error,
            )
            self._set_status(f"Could not load current print into Cura: {error}")
