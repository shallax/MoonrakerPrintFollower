from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, Optional, Tuple

from UM.Logger import Logger

from .Core import OperationPhase, RemoteFileIdentity
from .DownloadStream import DownloadTarget
from .MoonrakerProtocol import (
    download_endpoint,
    gcode_script_endpoint,
    metadata_endpoint,
    parse_file_identity,
    status_endpoint,
)
from .MoonrakerSession import RequestCategory
from .MoonrakerTransport import MoonrakerHttpTransport


class _PendingMarker:
    def __init__(self) -> None:
        self.running = True

    def isRunning(self) -> bool:
        return bool(self.running)


class FollowerTransportMixin:
    """Shared Moonraker transport adapter for follower-specific operations.

    Core status always comes from MoonrakerClient. Metadata and commands use the
    same JSON transport. Large G-code downloads retain their streaming reply
    lifecycle but share the transport's request builder and connection pool.
    """

    SCHEDULED_PAUSE_COMMAND = "ScheduledPause"

    def _init_follower_transport(self) -> None:
        self._pending_status_purpose: Optional[str] = None
        self._pending_status_generation = 0
        self._follower_probe_transport = MoonrakerHttpTransport(self)
        self._scheduled_pause_target: Optional[int] = None
        self._scheduled_pause_observed_layer: Optional[int] = None
        self._scheduled_pause_request_generation = 0

        shared_network = self._client.transport.network
        self._network = shared_network
        self._pause_network = shared_network
        self._file_network = shared_network
        self._metadata_network = shared_network
        try:
            self._client.commandChanged.connect(self._on_follower_command_changed)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Core-status reuse: explicit load/test requests do not create a second
    # active-printer status poll.
    # ------------------------------------------------------------------

    def _abort_status_reply(self) -> None:
        self._pending_status_generation += 1
        self._pending_status_purpose = None
        self._reply = None
        self._reply_purpose = None
        try:
            self._follower_probe_transport.cancel_owner("follower-probe")
        except Exception:
            pass

    def _issue_status_request(self, base_url: str, api_key: str, purpose: str) -> None:
        purpose = str(purpose or "poll")
        identity = (str(base_url or "").rstrip("/"), str(api_key or ""))
        active_identity = getattr(self._client.transport, "identity", ("", ""))
        self._pending_status_generation += 1
        generation = self._pending_status_generation
        self._reply_purpose = purpose

        if identity == active_identity:
            self._pending_status_purpose = purpose
            self._client.force_refresh()
            return

        # This path exists only for an unsaved/alternate probe. Keep it isolated
        # from the active printer while still using the same reusable transport.
        self._follower_probe_transport.configure(identity[0], identity[1])
        started = self._follower_probe_transport.send_json(
            "follower-probe",
            purpose,
            "GET",
            status_endpoint(identity[0]),
            lambda payload, error, p=purpose, g=generation: self._on_probe_status(payload, error, p, g),
            replace=True,
            category=RequestCategory.DISCOVERY.value,
        )
        if not started:
            self._set_status("A Moonraker request is already in progress")

    def _on_probe_status(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
        purpose: str,
        generation: int,
    ) -> None:
        if generation != self._pending_status_generation:
            return
        self._reply_purpose = None
        if error:
            self._set_status(f"Moonraker error: {error}")
            return
        result = (payload or {}).get("result") or {}
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            self._set_status("Invalid Moonraker response: missing status")
            return
        self._handle_status_purpose(status, purpose)

    def _consume_pending_shared_status(self, status: Dict[str, Any]) -> None:
        purpose = self._pending_status_purpose
        if not purpose:
            return
        self._pending_status_purpose = None
        self._reply_purpose = None
        self._handle_status_purpose(status, purpose)

    def _handle_status_purpose(self, status: Dict[str, Any], purpose: str) -> None:
        print_stats = status.get("print_stats") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        if purpose == "test":
            state = str(print_stats.get("state") or "unknown")
            filename = str(print_stats.get("filename") or "")
            suffix = f" — {filename}" if filename else ""
            self._set_status(f"Connected to Moonraker; printer state: {state}{suffix}")
            return
        if purpose != "force_load":
            return

        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")
        if state not in self.ACTIVE_STATES:
            self._force_load_requested = False
            self._force_load_pending_filename = None
            self._set_status(
                f"Moonraker is {state or 'not printing'}; there is no active print to load"
            )
            return
        if not filename:
            self._force_load_requested = False
            self._force_load_pending_filename = None
            self._set_status("Moonraker did not report a current G-code filename")
            return

        self._update_remote_job_identity(print_stats, virtual_sdcard)
        self._last_remote_filename = filename
        self._last_remote_state = state
        try:
            reported_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            reported_size = 0
        self._ensure_remote_metadata(filename, reported_size)
        self._start_forced_gcode_download(filename)

    # ------------------------------------------------------------------
    # Metadata JSON via shared transport.
    # ------------------------------------------------------------------

    def _abort_metadata_reply(self) -> None:
        self._client.transport.cancel("follower", "metadata")
        marker = self._metadata_reply
        if isinstance(marker, _PendingMarker):
            marker.running = False
        self._metadata_reply = None
        self._metadata_filename = None
        self._metadata_reply_job_key = None

    def _ensure_remote_metadata(self, filename: str, fallback_size: int = 0) -> None:
        if not filename:
            return
        identity = self._remote_file_identity
        if (
            identity is not None
            and identity.matches_job(filename, fallback_size)
            and self._metadata_job_key == self._remote_job_key
        ):
            return
        if self._metadata_reply is not None and self._metadata_reply.isRunning():
            if self._metadata_filename == filename:
                return
            self._abort_metadata_reply()

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            return
        self._metadata_filename = filename
        self._metadata_reply_generation = self._lifecycle_generation
        self._metadata_reply_job_key = self._remote_job_key
        marker = _PendingMarker()
        self._metadata_reply = marker
        generation = self._metadata_reply_generation
        job_key = self._metadata_reply_job_key
        started = self._client.transport.send_json(
            "follower",
            "metadata",
            "GET",
            metadata_endpoint(base_url, filename),
            lambda payload, error, f=filename, sz=int(fallback_size or 0), g=generation, j=job_key, m=marker:
                self._handle_metadata_payload(payload, error, f, sz, g, j, m),
            replace=True,
            category=RequestCategory.STATIC.value,
        )
        if not started:
            marker.running = False
            self._metadata_reply = None

    def _handle_metadata_payload(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
        filename: str,
        fallback_size: int,
        reply_generation: int,
        reply_job_key: Optional[Tuple[str, int, int]],
        marker: _PendingMarker,
    ) -> None:
        marker.running = False
        if self._metadata_reply is not marker:
            return
        self._metadata_reply = None
        self._metadata_filename = None
        self._metadata_reply_job_key = None
        if reply_generation != self._lifecycle_generation or reply_job_key != self._remote_job_key:
            return

        if error:
            # Metadata is an optimisation. Keep live following usable on minimal
            # Moonraker installs by falling back to non-persistable identity.
            self._remote_file_identity = RemoteFileIdentity(filename, int(fallback_size or 0))
            self._metadata_job_key = self._remote_job_key
            if self._pref_bool(self.PREF_PATH_FOLLOW) and self._cura_has_toolpath():
                self._ensure_remote_gcode_index(filename)
            return
        try:
            identity = parse_file_identity(filename, payload or {}, fallback_size)
            if self._last_remote_filename != filename:
                return
            if self._remote_job_key is not None and not identity.matches_job(
                self._remote_job_key[0], self._remote_job_key[1]
            ):
                return
            self._remote_file_identity = identity
            self._metadata_job_key = self._remote_job_key
            if (
                self._remote_index_data is not None
                and self._remote_index_filename == filename
                and (identity.uuid or identity.modified > 0)
            ):
                self._persist_index_async(identity, self._remote_index_data)
            elif self._pref_bool(self.PREF_PATH_FOLLOW) and self._cura_has_toolpath():
                if self._try_load_persistent_index(filename):
                    if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                        self._queue_lifecycle_callback(lambda: self._poll(force=True))
                else:
                    self._ensure_remote_gcode_index(filename)
        except Exception as exc:
            Logger.log("w", "Moonraker Print Follower metadata lookup failed for %s: %s", filename, exc)

    # ------------------------------------------------------------------
    # Streaming G-code download via shared request builder/connection pool.
    # ------------------------------------------------------------------

    def _begin_gcode_download(self, filename: str) -> bool:
        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url) or not filename:
            return False

        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_key
            ):
                return True
            self._abort_file_reply()

        try:
            job_dir = tempfile.mkdtemp(prefix="job-", dir=self._temp_gcode_dir.name)
            base_name = os.path.basename(filename.replace("\\", "/")) or "moonraker.gcode"
            if os.path.splitext(base_name)[1].lower() not in (".g", ".gcode"):
                base_name += ".gcode"
            target = DownloadTarget.open(os.path.join(job_dir, base_name))
        except Exception as exc:
            Logger.logException("w", "Moonraker Print Follower could not create download target: %s", exc)
            return False

        request = self._client.transport.request(download_endpoint(base_url, filename), timeout_ms=30000)
        request.setRawHeader(b"Accept", b"application/octet-stream")
        self._file_reply_filename = filename
        self._file_reply_generation = self._lifecycle_generation
        self._file_reply_job_key = self._remote_job_key
        self._file_download_target = target
        self._set_operation_phase(OperationPhase.DOWNLOADING, filename=filename)
        reply = self._client.transport.network.get(request)
        try:
            reply.setReadBufferSize(4 * 1024 * 1024)
        except Exception:
            pass
        self._file_reply = reply
        reply_generation = self._file_reply_generation
        reply_job_key = self._file_reply_job_key
        reply.readyRead.connect(lambda r=reply: self._drain_gcode_reply(r))
        reply.finished.connect(
            lambda r=reply, f=filename, g=reply_generation, j=reply_job_key: self._handle_gcode_reply(r, f, g, j)
        )
        return True

    # ------------------------------------------------------------------
    # Scheduled PAUSE command via shared command acknowledgement.
    # ------------------------------------------------------------------

    def _abort_pause_reply(self) -> None:
        self._scheduled_pause_request_generation += 1
        self._client.transport.cancel("follower", "scheduled-pause")
        self._pause_reply = None
        self._pause_reply_job_key = None
        self._scheduled_pause_target = None
        self._scheduled_pause_observed_layer = None

    def _send_scheduled_pause(self, target_layer: int, current_layer: int) -> None:
        config = self._config_store.get()
        base_url = self._normalise_base_url(config.url)
        if not self._url_is_usable(base_url):
            self._set_status(f"Could not PAUSE after layer {target_layer + 1}: Moonraker URL unavailable")
            return

        self._scheduled_pause_request_generation += 1
        request_generation = self._scheduled_pause_request_generation
        lifecycle_generation = self._lifecycle_generation
        job_key = self._remote_job_key
        self._scheduled_pause_target = target_layer
        self._scheduled_pause_observed_layer = current_layer
        self._client.track_command(self.SCHEDULED_PAUSE_COMMAND, {"paused"}, timeout_s=10.0)

        started = self._client.transport.send_json(
            "follower",
            "scheduled-pause",
            "POST",
            gcode_script_endpoint(base_url),
            lambda payload, error, rg=request_generation, lg=lifecycle_generation, j=job_key:
                self._on_scheduled_pause_http_finished(payload, error, rg, lg, j),
            body={"script": "PAUSE"},
            replace=False,
            category=RequestCategory.COMMAND.value,
        )
        if not started:
            self._client.fail_command(
                self.SCHEDULED_PAUSE_COMMAND,
                "another scheduled PAUSE request is already in flight",
            )
            self._set_status(f"Could not PAUSE after layer {target_layer + 1}: request already in flight")
            return
        Logger.log(
            "i",
            "Moonraker Print Follower requesting PAUSE after scheduled layer %d (observed layer %d)",
            target_layer + 1,
            current_layer + 1,
        )

    def _on_scheduled_pause_http_finished(
        self,
        _payload: Optional[Dict[str, Any]],
        error: Optional[str],
        request_generation: int,
        lifecycle_generation: int,
        job_key: Optional[Tuple[str, int, int]],
    ) -> None:
        if (
            request_generation != self._scheduled_pause_request_generation
            or lifecycle_generation != self._lifecycle_generation
            or job_key != self._remote_job_key
        ):
            return
        target_layer = self._scheduled_pause_target
        observed_layer = self._scheduled_pause_observed_layer
        if error:
            self._client.fail_command(self.SCHEDULED_PAUSE_COMMAND, error)
            if target_layer is not None:
                self._set_status(f"PAUSE after layer {target_layer + 1} failed: {error}")
            return
        self._client.accept_command(self.SCHEDULED_PAUSE_COMMAND)
        if target_layer is not None:
            observed_human = (observed_layer + 1) if observed_layer is not None else "?"
            self._set_status(
                f"PAUSE accepted after layer {target_layer + 1}; waiting for printer confirmation "
                f"(transition observed at layer {observed_human})"
            )

    def _on_follower_command_changed(self, event: Any) -> None:
        if not isinstance(event, dict) or str(event.get("name") or "") != self.SCHEDULED_PAUSE_COMMAND:
            return
        outcome = str(event.get("outcome") or "")
        target_layer = self._scheduled_pause_target
        if outcome == "confirmed":
            if target_layer is not None:
                self._set_status(f"PAUSE confirmed after layer {target_layer + 1}")
            self._scheduled_pause_target = None
            self._scheduled_pause_observed_layer = None
        elif outcome in {"failed", "timed_out"}:
            detail = str(event.get("detail") or outcome.replace("_", " "))
            if target_layer is not None:
                self._set_status(f"PAUSE after layer {target_layer + 1}: {detail}")
            self._scheduled_pause_target = None
            self._scheduled_pause_observed_layer = None
