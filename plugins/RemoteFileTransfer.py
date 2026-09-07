from __future__ import annotations

import os
import shutil
from typing import Optional, Tuple

from PyQt6.QtNetwork import QNetworkReply
from UM.Logger import Logger

from .Core import OperationPhase


class RemoteFileTransferMixin:
    """Own the streaming reply and temporary-file lifecycle for remote G-code.

    Request construction and the connection pool remain owned by
    MoonrakerHttpTransport/FollowerTransport. Cached-file identity remains owned
    by RemoteFileService through the coordinator.
    """

    def _abort_file_reply(self) -> None:
        reply = self._file_reply
        self._file_reply = None
        self._file_reply_filename = None
        self._file_reply_job_key = None
        target = self._file_download_target
        self._file_download_target = None
        if reply is not None:
            try:
                reply.readyRead.disconnect()
            except Exception:
                pass
            try:
                reply.finished.disconnect()
            except Exception:
                pass
            try:
                if reply.isRunning():
                    reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass
        if target is not None:
            target.abort(remove=True)
            self._cleanup_cached_job_dir(target.path)
        if self._operation.phase == OperationPhase.DOWNLOADING:
            self._set_operation_phase(OperationPhase.IDLE)

    def _drain_gcode_reply(self, reply: QNetworkReply) -> None:
        if reply is not self._file_reply:
            return
        target = self._file_download_target
        if target is None:
            return
        try:
            chunk = reply.readAll()
            if chunk:
                target.write(chunk)
        except Exception as error:
            filename = (
                self._file_reply_filename
                or self._force_load_pending_filename
                or "current print"
            )
            forced = bool(
                self._force_load_requested
                and self._force_load_pending_filename == self._file_reply_filename
            )
            Logger.log(
                "w",
                "Moonraker Print Follower streaming download write failed: %s",
                error,
            )
            self._abort_file_reply()
            if forced:
                self._force_load_requested = False
                self._force_load_pending_filename = None
                self._set_operation_phase(
                    OperationPhase.ERROR, filename=str(filename)
                )
                self._set_status(f"Could not write downloaded G-code: {error}")

    def _handle_gcode_reply(
        self,
        reply: QNetworkReply,
        filename: str,
        reply_generation: int,
        reply_job_key: Optional[Tuple[str, int, int]],
    ) -> None:
        if reply is not self._file_reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return

        # Drain any bytes delivered with finished() before releasing the reply.
        self._drain_gcode_reply(reply)
        target = self._file_download_target
        self._file_reply = None
        self._file_reply_filename = None
        self._file_reply_job_key = None
        self._file_download_target = None

        try:
            if not filename or target is None:
                if target is not None:
                    target.abort(remove=True)
                return
            if (
                reply_generation != self._lifecycle_generation
                or reply_job_key != self._remote_job_key
            ):
                target.abort(remove=True)
                self._cleanup_cached_job_dir(target.path)
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                target.abort(remove=True)
                self._cleanup_cached_job_dir(target.path)
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not download %s: %s",
                    filename,
                    reply.errorString(),
                )
                if self._force_load_pending_filename == filename:
                    self._set_operation_phase(
                        OperationPhase.ERROR, filename=filename
                    )
                    self._set_status(
                        "Could not download current print from Moonraker: "
                        + reply.errorString()
                    )
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                return

            target.flush_close()
            self._adopt_cached_gcode_path(filename, target.path, reply_job_key)
            identity = self._remote_file_identity
            if (
                identity is not None
                and identity.size > 0
                and target.bytes_written != identity.size
            ):
                Logger.log(
                    "w",
                    "Moonraker Print Follower downloaded %d bytes for %s; metadata reported %d",
                    target.bytes_written,
                    filename,
                    identity.size,
                )
                bad_path = target.path
                self._discard_cached_gcode()
                self._cleanup_cached_job_dir(bad_path)
                if self._force_load_pending_filename == filename:
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                    self._set_operation_phase(
                        OperationPhase.ERROR, filename=filename
                    )
                    self._set_status(
                        f"Downloaded G-code size mismatch for {filename}; "
                        "refusing to load a partial file"
                    )
                return

            forced_load = self._force_load_pending_filename == filename
            if forced_load:
                self._load_cached_remote_gcode_forced(filename)
            elif (
                self._pref_bool(self.PREF_PATH_FOLLOW)
                and self._cura_has_toolpath()
                and not self._cura_load_in_progress
            ):
                self._start_remote_gcode_index_build_from_file(
                    filename, target.path
                )
            else:
                self._set_operation_phase(
                    OperationPhase.READY, filename=filename
                )
        except Exception as error:
            Logger.logException(
                "w",
                "Moonraker Print Follower failed to process remote G-code %s: %s",
                filename,
                error,
            )
            if target is not None:
                target.abort(remove=True)
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass

    def _cleanup_cached_job_dir(self, path: Optional[str]) -> None:
        if not path:
            return
        try:
            job_dir = os.path.dirname(os.path.abspath(path))
            temp_root = os.path.abspath(self._temp_gcode_dir.name)
            if os.path.commonpath((job_dir, temp_root)) != temp_root:
                return
            if self._cura_load_in_progress and self._cura_load_path:
                if os.path.abspath(path) == os.path.abspath(self._cura_load_path):
                    self._deferred_cache_dirs.add(job_dir)
                    return
            shutil.rmtree(job_dir, ignore_errors=True)
            self._deferred_cache_dirs.discard(job_dir)
        except Exception as error:
            Logger.log(
                "w",
                "Moonraker Print Follower could not clean cached G-code directory: %s",
                error,
            )

    def _cleanup_deferred_cache_dirs(self) -> None:
        if not self._deferred_cache_dirs:
            return
        active_dir = None
        if self._cura_load_in_progress and self._cura_load_path:
            try:
                active_dir = os.path.dirname(os.path.abspath(self._cura_load_path))
            except Exception:
                active_dir = None
        for job_dir in tuple(self._deferred_cache_dirs):
            if active_dir is not None and job_dir == active_dir:
                continue
            shutil.rmtree(job_dir, ignore_errors=True)
            self._deferred_cache_dirs.discard(job_dir)
