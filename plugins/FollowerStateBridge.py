from __future__ import annotations

from typing import Any, Optional, Tuple


JobKey = Tuple[str, int, int]


class FollowerStateBridgeMixin:
    """Compatibility names backed by the extracted authoritative services.

    FollowerRuntime still contains mature Cura-facing code that refers to its
    historical private attributes. These properties let that code run unchanged
    while ensuring there is exactly one owner for each state domain.
    """

    # Follower/session -------------------------------------------------
    @property
    def _following_paused(self) -> bool:
        return bool(self._follower_session.following_paused)

    @_following_paused.setter
    def _following_paused(self, value: bool) -> None:
        self._follower_session.following_paused = bool(value)

    # Print identity ---------------------------------------------------
    @property
    def _remote_job_key(self) -> Optional[JobKey]:
        return self._print_tracker.key

    @_remote_job_key.setter
    def _remote_job_key(self, value: Optional[JobKey]) -> None:
        self._print_tracker.key = value

    @property
    def _remote_job_serial(self) -> int:
        return int(self._print_tracker.serial)

    @_remote_job_serial.setter
    def _remote_job_serial(self, value: int) -> None:
        self._print_tracker.serial = max(0, int(value or 0))

    @property
    def _last_remote_file_position(self) -> Optional[int]:
        return self._print_tracker.last_file_position

    @_last_remote_file_position.setter
    def _last_remote_file_position(self, value: Optional[int]) -> None:
        self._print_tracker.last_file_position = None if value is None else int(value)

    @property
    def _last_remote_print_duration(self) -> Optional[float]:
        return self._print_tracker.last_print_duration

    @_last_remote_print_duration.setter
    def _last_remote_print_duration(self, value: Optional[float]) -> None:
        self._print_tracker.last_print_duration = None if value is None else float(value)

    # Scheduled pauses -------------------------------------------------
    @property
    def _scheduled_pause_layers(self) -> set[int]:
        return self._pause_schedule.layers

    @_scheduled_pause_layers.setter
    def _scheduled_pause_layers(self, value) -> None:
        self._pause_schedule.replace(value or ())

    # Preview expectation ---------------------------------------------
    @property
    def _expected_follow_layer(self) -> Optional[int]:
        return self._preview_service.expected.layer

    @_expected_follow_layer.setter
    def _expected_follow_layer(self, value: Optional[int]) -> None:
        self._preview_service.expected.layer = None if value is None else int(value)

    @property
    def _expected_follow_minimum_layer(self) -> Optional[int]:
        return self._preview_service.expected.minimum_layer

    @_expected_follow_minimum_layer.setter
    def _expected_follow_minimum_layer(self, value: Optional[int]) -> None:
        self._preview_service.expected.minimum_layer = None if value is None else int(value)

    @property
    def _expected_follow_path(self) -> Optional[float]:
        return self._preview_service.expected.path

    @_expected_follow_path.setter
    def _expected_follow_path(self, value: Optional[float]) -> None:
        self._preview_service.expected.path = None if value is None else float(value)

    @property
    def _expected_follow_minimum_path(self) -> Optional[int]:
        return self._preview_service.expected.minimum_path

    @_expected_follow_minimum_path.setter
    def _expected_follow_minimum_path(self, value: Optional[int]) -> None:
        self._preview_service.expected.minimum_path = None if value is None else int(value)

    # Downloaded G-code cache -----------------------------------------
    @property
    def _cached_gcode_filename(self) -> Optional[str]:
        return self._gcode_repository.cached.filename

    @_cached_gcode_filename.setter
    def _cached_gcode_filename(self, value: Optional[str]) -> None:
        self._gcode_repository.cached.filename = None if value is None else str(value)

    @property
    def _cached_gcode_path(self) -> Optional[str]:
        return self._gcode_repository.cached.path

    @_cached_gcode_path.setter
    def _cached_gcode_path(self, value: Optional[str]) -> None:
        self._gcode_repository.cached.path = None if value is None else str(value)

    @property
    def _cached_gcode_job_key(self) -> Optional[JobKey]:
        return self._gcode_repository.cached.job_key

    @_cached_gcode_job_key.setter
    def _cached_gcode_job_key(self, value: Optional[JobKey]) -> None:
        self._gcode_repository.cached.job_key = value

    # Cura lifecycle generation ---------------------------------------
    @property
    def _lifecycle_generation(self) -> int:
        return self._cura_lifecycle.generation

    @_lifecycle_generation.setter
    def _lifecycle_generation(self, value: int) -> None:
        self._cura_lifecycle.generation = value

    # G-code index/build/hydration ------------------------------------
    @property
    def _remote_index_generation(self) -> int:
        return self._gcode_index_service.state.generation

    @_remote_index_generation.setter
    def _remote_index_generation(self, value: int) -> None:
        self._gcode_index_service.state.generation = max(0, int(value or 0))

    @property
    def _remote_index_filename(self) -> Optional[str]:
        return self._gcode_index_service.state.filename

    @_remote_index_filename.setter
    def _remote_index_filename(self, value: Optional[str]) -> None:
        self._gcode_index_service.state.filename = None if value is None else str(value)

    @property
    def _remote_index_job_key(self) -> Optional[JobKey]:
        return self._gcode_index_service.state.job_key

    @_remote_index_job_key.setter
    def _remote_index_job_key(self, value: Optional[JobKey]) -> None:
        self._gcode_index_service.state.job_key = value

    @property
    def _remote_layer_ranges(self) -> list:
        return self._gcode_index_service.state.ranges

    @_remote_layer_ranges.setter
    def _remote_layer_ranges(self, value) -> None:
        self._gcode_index_service.state.ranges = list(value or [])

    @property
    def _remote_motion_offsets(self) -> list:
        return self._gcode_index_service.state.motion_offsets

    @_remote_motion_offsets.setter
    def _remote_motion_offsets(self, value) -> None:
        self._gcode_index_service.state.motion_offsets = list(value or [])

    @property
    def _remote_current_layer_map(self) -> dict[int, int]:
        return self._gcode_index_service.state.current_layer_map

    @_remote_current_layer_map.setter
    def _remote_current_layer_map(self, value) -> None:
        self._gcode_index_service.state.current_layer_map = dict(value or {})

    @property
    def _remote_index_data(self) -> Any:
        return self._gcode_index_service.state.data

    @_remote_index_data.setter
    def _remote_index_data(self, value: Any) -> None:
        self._gcode_index_service.state.data = value

    @property
    def _remote_index_build_filename(self) -> Optional[str]:
        return self._gcode_index_service.state.build_filename

    @_remote_index_build_filename.setter
    def _remote_index_build_filename(self, value: Optional[str]) -> None:
        self._gcode_index_service.state.build_filename = None if value is None else str(value)

    @property
    def _remote_index_build_job_key(self) -> Optional[JobKey]:
        return self._gcode_index_service.state.build_job_key

    @_remote_index_build_job_key.setter
    def _remote_index_build_job_key(self, value: Optional[JobKey]) -> None:
        self._gcode_index_service.state.build_job_key = value

    @property
    def _remote_index_cancel_event(self) -> Any:
        return self._gcode_index_service.state.cancel_event

    @_remote_index_cancel_event.setter
    def _remote_index_cancel_event(self, value: Any) -> None:
        self._gcode_index_service.state.cancel_event = value

    @property
    def _remote_index_thread(self) -> Any:
        return self._gcode_index_service.state.thread

    @_remote_index_thread.setter
    def _remote_index_thread(self, value: Any) -> None:
        self._gcode_index_service.state.thread = value

    @property
    def _hydrating_layers(self) -> set[int]:
        return self._gcode_index_service.state.hydrating_layers

    @_hydrating_layers.setter
    def _hydrating_layers(self, value) -> None:
        self._gcode_index_service.state.hydrating_layers = set(value or ())

    @property
    def _hydration_threads(self) -> set:
        return self._gcode_index_service.state.hydration_threads

    @_hydration_threads.setter
    def _hydration_threads(self, value) -> None:
        self._gcode_index_service.state.hydration_threads = set(value or ())
