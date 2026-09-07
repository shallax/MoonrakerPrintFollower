from __future__ import annotations

from typing import Any, Optional, Tuple
from PyQt6.QtCore import QUrl
from .PrinterConfig import PrinterConfig


class FollowerConfigurationMixin:
    def _register_preferences(self) -> None:
        self._preferences.addPreference(self.PREF_ENABLED, False)
        self._preferences.addPreference(self.PREF_URL, "http://")
        self._preferences.addPreference(self.PREF_API_KEY, "")
        self._preferences.addPreference(self.PREF_INTERVAL, 750)
        self._preferences.addPreference(self.PREF_ONE_BASED, True)
        self._preferences.addPreference(self.PREF_AUTO_PREVIEW, False)
        self._preferences.addPreference(self.PREF_Z_FALLBACK, True)
        self._preferences.addPreference(self.PREF_Z_TOLERANCE, 0.04)
        self._preferences.addPreference(self.PREF_PATH_FOLLOW, True)

    def current_printer_config(self) -> PrinterConfig:
        """Return the active Cura printer's follower configuration."""
        return self._config_store.get()

    def current_printer_identity(self) -> Tuple[str, str]:
        """Return the active Cura machine id and human-readable name."""
        return self._config_store.identity()

    def apply_printer_config(self, config: PrinterConfig) -> None:
        """Persist and immediately apply configuration from the Machine Action QML."""
        self._config_store.set(config)
        self._follow_controller.set_enabled(config.enabled)
        if not config.enabled:
            self._following_paused = False
            self._follow_controller.resume()
            self._clear_expected_preview_position()
        self._apply_timer_state()
        self._sync_preview_button_state()
        self._update_toolhead_indicator()
        if config.enabled and self._url_is_usable(self._normalise_base_url(config.url)):
            self._client.force_refresh()

    def _toggle_following_pause(self) -> None:
        """Pause or resume Preview movement without changing saved preferences."""
        self._following_paused = not self._following_paused
        if self._following_paused:
            self._follow_controller.pause_by_user("pause button")
        else:
            self._follow_controller.resume()
            self._selected_layer_eta_text = ""
        self._sync_preview_button_state()

        if self._following_paused:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status("Following paused; Moonraker connection remains active")
        else:
            view = self._simulation_view()
            if view is not None:
                self._remember_plugin_preview_position(view)
            else:
                self._clear_expected_preview_position()
            self._set_status("Following resumed; catching up to the current print")
            self._client.force_refresh()

    def _on_client_connection_changed(self, connected: bool, detail: str) -> None:
        self._follow_controller.set_connection(bool(connected), connecting=not connected and self._pref_bool(self.PREF_ENABLED))
        if not connected:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
        if not connected and self._last_remote_state not in self.ACTIVE_STATES:
            self._set_status(detail)
        self._sync_preview_button_state()

    def _on_client_capabilities_changed(self, capabilities) -> None:
        self._last_capabilities = dict(capabilities or {})

    def _on_active_machine_changed(self, *_args) -> None:
        """Transfer the single follower session to Cura's newly active printer.

        There is intentionally only one MoonrakerClient in the plugin. A printer
        switch first stops that client and invalidates every in-flight operation
        owned by the old machine. Only after that teardown is complete do we bind
        the configuration for the new active Cura printer and optionally restart
        polling. Two printers can therefore never drive Preview concurrently.
        """
        machine_id, machine_name = self._config_store.identity()
        if machine_id == self._active_machine_id:
            self._active_machine_name = machine_name
            self._sync_preview_button_state()
            return

        self._client.stop()
        self._invalidate_lifecycle("active Cura printer changed")

        self._active_machine_id = machine_id
        self._active_machine_name = machine_name
        self._following_paused = False
        self._follow_controller.resume()
        self._last_remote_filename = None
        self._last_remote_state = None
        self._last_extruder_position = None
        self._last_capabilities = {}
        self._remote_job_key = None
        self._remote_file_identity = None
        self._last_observed_remote_layer = None
        self._clear_scheduled_pauses(abort_request=True)
        self._discard_cached_gcode()
        self._clear_remote_gcode_index()
        self._config_store.migrate_legacy_to_current_machine()
        self._apply_timer_state()
        self._sync_preview_controls_visibility()

    def _active_printer_is_configured_for_following(self) -> bool:
        """Return True only when the active Cura printer owns a usable session."""
        if self._active_machine_id == "unknown":
            return False
        config = self._config_store.get()
        return bool(
            config.enabled
            and self._url_is_usable(self._normalise_base_url(config.url))
        )

    def _poll(self, force: bool = False) -> None:
        if not force and not self._pref_bool(self.PREF_ENABLED):
            return
        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL for this Cura printer")
            return
        self._client.force_refresh()

    def _active_status_text(
        self,
        filename: str,
        remote_layer: Optional[int],
        total_layer: Any,
        detail: str,
    ) -> str:
        name = filename or "remote print"
        if remote_layer is None:
            layer_text = "layer ?"
        else:
            try:
                total = int(total_layer) if total_layer is not None else 0
            except (TypeError, ValueError):
                total = 0
            layer_text = f"layer {remote_layer}/{total}" if total > 0 else f"layer {remote_layer}"
        return f"{name}: {layer_text} — {detail}"

    def _set_status(self, text: str) -> None:
        self._last_status_text = text
        self._sync_preview_button_state()

    def _valid_configured_url(self) -> bool:
        return self._url_is_usable(self._normalise_base_url(self._pref_str(self.PREF_URL)))

    @staticmethod
    def _url_is_usable(url: str) -> bool:
        parsed = QUrl(url)
        return parsed.isValid() and parsed.scheme() in ("http", "https") and bool(parsed.host())

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        value = (value or "").strip().rstrip("/")
        if not value:
            return "http://"
        if not value.lower().startswith(("http://", "https://")):
            value = f"http://{value}"
        return value

    def _pref_str(self, key: str) -> str:
        value = self._per_printer_pref_value(key)
        return "" if value is None else str(value)

    def _pref_bool(self, key: str) -> bool:
        value = self._per_printer_pref_value(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _pref_int(self, key: str, default: int) -> int:
        try:
            return int(self._per_printer_pref_value(key))
        except (TypeError, ValueError):
            return default

    def _pref_float(self, key: str, default: float) -> float:
        try:
            return float(self._per_printer_pref_value(key))
        except (TypeError, ValueError):
            return default

    def _per_printer_pref_value(self, key: str):
        """Bridge legacy call sites onto the active Cura printer's 1.1 config."""
        config = self._config_store.get()
        mapping = {
            self.PREF_ENABLED: config.enabled,
            self.PREF_URL: config.url,
            self.PREF_API_KEY: config.api_key,
            self.PREF_INTERVAL: config.poll_interval_ms,
            self.PREF_ONE_BASED: config.moonraker_layer_is_one_based,
            self.PREF_AUTO_PREVIEW: config.auto_preview,
            self.PREF_Z_FALLBACK: config.z_fallback,
            self.PREF_Z_TOLERANCE: config.z_tolerance,
            self.PREF_PATH_FOLLOW: config.path_follow,
        }
        if key in mapping:
            return mapping[key]
        return self._preferences.getValue(key)
