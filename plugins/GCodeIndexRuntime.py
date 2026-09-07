from __future__ import annotations

import os
import threading
from typing import Optional

from PyQt6.QtCore import pyqtSlot
from UM.Logger import Logger

from .Core import OperationPhase, RemoteFileIdentity
from .GCodeIndex import (
    LayerMotionIndex,
    build_index_from_file,
    hydrate_layer_from_file,
)


class GCodeIndexRuntimeMixin:
    """Run background index/cache work owned by GCodeIndexService.

    Worker mechanics remain Cura/Qt-facing here, while all mutable active index,
    build and hydration identity lives in the service composed by the coordinator.
    Every callback is guarded by index generation plus Cura/job identity.
    """

    def _ensure_remote_layer_hydrated(self, layer: int) -> None:
        index = self._gcode_index_service.data
        if index is None or not getattr(index, "compact", False):
            return
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            return
        if layer < 0 or layer >= len(index.ranges):
            return
        if layer in getattr(index, "hydrated_layers", set()):
            return
        if not self._gcode_index_service.begin_hydration(layer):
            return

        path = self._remote_file_service.cached_path
        if not (
            path
            and self._remote_file_service.cached_filename == self._remote_job_service.filename
            and self._remote_file_service.cached_job_key == self._remote_job_service.key
            and os.path.isfile(path)
        ):
            self._gcode_index_service.finish_hydration(layer)
            return

        generation = self._gcode_index_service.generation
        job_key = self._remote_job_service.key

        def worker() -> None:
            ok = False
            try:
                ok = bool(hydrate_layer_from_file(index, path, layer))
            except Exception as error:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not hydrate layer %d: %s",
                    layer,
                    error,
                )
            if not self._destroyed:
                self._remoteLayerHydrated.emit(
                    generation, layer, ok and job_key == self._remote_job_service.key
                )

        thread = threading.Thread(
            target=worker,
            name=f"MoonrakerPrintFollowerLayer{layer}",
            daemon=True,
        )
        self._gcode_index_service.add_hydration_thread(thread)
        thread.start()

    @pyqtSlot(int, int, bool)
    def _on_remote_layer_hydrated(
        self, generation: int, layer: int, ok: bool
    ) -> None:
        self._gcode_index_service.finish_hydration(layer)
        if generation != self._gcode_index_service.generation or not ok:
            return
        index = self._gcode_index_service.data
        if index is None or layer not in getattr(index, "hydrated_layers", set()):
            return
        self._gcode_index_service.update_motion_offsets(index)
        self._persist_index_async(self._remote_file_service.identity, index)
        if self.current_printer_config().enabled and not self._preview_follower_service.following_paused:
            self._client.force_refresh()

    def _cancel_remote_index_build(
        self, wait: bool = False, timeout: float = 1.0
    ) -> None:
        """Cancel the active index generation without blocking normal Cura UI work."""
        thread = self._gcode_index_service.thread
        self._gcode_index_service.invalidate_build()
        if (
            wait
            and thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=max(0.0, timeout))
            if thread.is_alive():
                Logger.log(
                    "w",
                    "Moonraker Print Follower index worker did not stop within %.2fs",
                    timeout,
                )
        if thread is not None and not thread.is_alive():
            self._gcode_index_service.clear_finished_thread(thread)
        if self._operation.phase == OperationPhase.INDEXING:
            next_phase = (
                OperationPhase.READY
                if self._gcode_index_service.data is not None
                else OperationPhase.IDLE
            )
            self._set_operation_phase(next_phase)

    def _persist_index_async(
        self,
        identity: Optional[RemoteFileIdentity],
        index: Optional[LayerMotionIndex],
    ) -> None:
        if identity is None or index is None or not index:
            return
        if not identity.uuid and identity.modified <= 0:
            return
        self._cache_save_threads = {
            thread for thread in self._cache_save_threads if thread.is_alive()
        }

        def worker() -> None:
            try:
                self._persistent_index_cache.save(identity, index)
            except Exception as error:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not persist path index: %s",
                    error,
                )

        thread = threading.Thread(
            target=worker,
            name="MoonrakerPrintFollowerCache",
            daemon=True,
        )
        self._cache_save_threads.add(thread)
        thread.start()

    def _try_load_persistent_index(self, filename: str) -> bool:
        if not filename or self._remote_file_service.identity is None:
            return False
        if self._remote_file_service.metadata_job_key != self._remote_job_service.key:
            return False
        if not self._remote_file_service.identity.uuid and self._remote_file_service.identity.modified <= 0:
            return False
        if not self._remote_file_service.identity.matches_job(
            filename, self._remote_job_service.key[1] if self._remote_job_service.key else 0
        ):
            return False
        if (
            self._gcode_index_service.filename == filename
            and self._gcode_index_service.job_key == self._remote_job_service.key
            and self._gcode_index_service.data is not None
        ):
            return True
        index = self._persistent_index_cache.load(self._remote_file_service.identity)
        if index is None:
            return False
        return self._install_remote_index(
            filename, index, self._remote_job_service.key, source="restored cached"
        )

    def _ensure_remote_gcode_cached(self, filename: str) -> None:
        if not filename:
            return
        if (
            self._remote_file_service.cached_filename == filename
            and self._remote_file_service.cached_path
            and self._remote_file_service.cached_job_key == self._remote_job_service.key
            and os.path.isfile(self._remote_file_service.cached_path)
        ):
            return
        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_service.key
            ):
                return
            self._abort_file_reply()
        self._begin_gcode_download(filename)

    def _ensure_remote_gcode_index(self, filename: str) -> None:
        if not filename or self._operation.is_cura_loading or self._slicing_in_progress:
            return
        if (
            self._remote_file_service.metadata_job_key != self._remote_job_service.key
            and self._metadata_filename == filename
        ):
            return

        if self._try_load_persistent_index(filename):
            if self._gcode_index_service.data is not None and getattr(
                self._gcode_index_service.data, "compact", False
            ):
                self._ensure_remote_gcode_cached(filename)
            return
        if (
            self._gcode_index_service.filename == filename
            and self._gcode_index_service.job_key == self._remote_job_service.key
        ):
            return
        if (
            self._gcode_index_service.build_filename == filename
            and self._gcode_index_service.build_job_key == self._remote_job_service.key
        ):
            return

        if (
            self._remote_file_service.cached_filename == filename
            and self._remote_file_service.cached_path
            and self._remote_file_service.cached_job_key == self._remote_job_service.key
            and os.path.isfile(self._remote_file_service.cached_path)
        ):
            self._start_remote_gcode_index_build_from_file(
                filename, self._remote_file_service.cached_path
            )
            return

        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_service.key
            ):
                return
            self._abort_file_reply()
        self._begin_gcode_download(filename)

    def _start_remote_gcode_index_build_from_file(
        self, filename: str, path: str
    ) -> None:
        if not filename or not path or not os.path.isfile(path):
            return
        job_key = self._remote_job_service.key
        if (
            self._gcode_index_service.filename == filename
            and self._gcode_index_service.job_key == job_key
        ):
            return
        if (
            self._gcode_index_service.build_filename == filename
            and self._gcode_index_service.build_job_key == job_key
        ):
            return
        if self._try_load_persistent_index(filename):
            return

        active_thread = self._gcode_index_service.thread
        if active_thread is not None and active_thread.is_alive():
            self._cancel_remote_index_build(wait=True, timeout=0.5)
            active_thread = self._gcode_index_service.thread
            if active_thread is not None and active_thread.is_alive():
                self._queue_lifecycle_callback(
                    lambda f=filename, p=path: self._start_remote_gcode_index_build_from_file(
                        f, p
                    ),
                    100,
                )
                return

        lifecycle_generation = self._cura_lifecycle_bridge.generation
        job_serial = job_key[2] if job_key is not None else 0
        cancel_event = threading.Event()

        # The worker closes over generation after begin_build has established
        # this build as the service's active generation.
        generation_holder = {"value": self._gcode_index_service.generation}

        def worker() -> None:
            try:
                index = build_index_from_file(path, cancel_event)
            except Exception as error:
                Logger.logException(
                    "w",
                    "Moonraker Print Follower failed to index cached G-code %s: %s",
                    filename,
                    error,
                )
                index = LayerMotionIndex()
            if not self._destroyed and not cancel_event.is_set():
                self._remoteIndexReady.emit(
                    generation_holder["value"],
                    filename,
                    index,
                    lifecycle_generation,
                    job_serial,
                )

        thread = threading.Thread(
            target=worker,
            name="MoonrakerPrintFollowerIndex",
            daemon=True,
        )
        generation_holder["value"] = self._gcode_index_service.begin_build(
            filename, job_key, cancel_event, thread
        )
        self._set_operation_phase(OperationPhase.INDEXING, filename=filename)
        thread.start()

    @pyqtSlot(int, str, object, int, int)
    def _on_remote_index_ready(
        self,
        generation: int,
        filename: str,
        index,
        lifecycle_generation: int,
        job_serial: int,
    ) -> None:
        if generation != self._gcode_index_service.generation:
            return
        if lifecycle_generation != self._cura_lifecycle_bridge.generation:
            return
        if filename != self._remote_job_service.filename:
            return
        if self._remote_job_service.key is not None and job_serial != self._remote_job_service.key[2]:
            return

        self._gcode_index_service.finish_build()
        if isinstance(index, LayerMotionIndex) and self._install_remote_index(
            filename, index, self._remote_job_service.key, source="built"
        ):
            if self._remote_file_service.identity is not None and (
                self._remote_file_service.identity.uuid
                or self._remote_file_service.identity.modified > 0
            ):
                self._persist_index_async(self._remote_file_service.identity, index)
            if self.current_printer_config().enabled and not self._preview_follower_service.following_paused:
                self._queue_lifecycle_callback(lambda: self._poll(force=True))
        else:
            self._set_operation_phase(OperationPhase.READY, filename=filename)
            Logger.log(
                "w",
                "Moonraker Print Follower found no layer markers in remote G-code %s",
                filename,
            )
