from __future__ import annotations

from typing import Any, Optional, Tuple

from PyQt6.QtCore import QUrl

from .PrinterConfig import PrinterConfig


class FollowerConfigurationMixin:
    def current_printer_config(self) -> PrinterConfig:
        """Return the active Cura printer's persisted configuration."""
        return self._config_store.get()

    def current_printer_identity(self) -> Tuple[str, str]:
        """Return the active Cura machine id and human-readable name."""
        return self._config_store.identity()

    def apply_printer_config(self, config: PrinterConfig) -> None:
        """Persist and immediately apply configuration from the Machine Action."""
        self._config_store.set(config)
        self._follow_controller.set_enabled(config.enabled)
        if not config.enabled:
            self._preview_follower_service.set_paused(False)
            self._follow_controller.resume()
            self._clear_expected_preview_position()
        self._apply_timer_state()
        self._sync_preview_button_state()
        self._update_toolhead_indicator()
        if config.enabled and self._url_is_usable(self._normalise_base_url(config.url)):
            self._client.force_refresh()

    def _toggle_following_pause(self) -> None:
        """Detach or attach Preview movement without changing saved settings."""
        paused = not self._preview_follower_service.following_paused
        self._preview_follower_service.set_paused(paused)
        if paused:
            self._follow_controller.pause_by_user("pause button")
        else:
            self._follow_controller.resume()
            self._preview_follower_service.set_selected_layer_eta_text("")
        self._sync_preview_button_state()

        if paused:
            self._preview_follower_service.runtime.toolhead_path_valid = False
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
        config = self.current_printer_config()
        self._follow_controller.set_connection(
            bool(connected), connecting=not connected and config.enabled
        )
        if not connected:
            self._preview_follower_service.runtime.toolhead_path_valid = False
            self._hide_toolhead_indicator()
        if not connected and self._remote_job_service.printer_state not in self.ACTIVE_STATES:
            self._set_status(detail)
        self._sync_preview_button_state()

    def _on_client_capabilities_changed(self, capabilities) -> None:
        self._last_capabilities = dict(capabilities or {})

    def _on_active_machine_changed(self, *_args) -> None:
        """Transfer the single live Moonraker session to the active Cura printer."""
        machine_id, machine_name = self._config_store.identity()
        if machine_id == self._active_machine_id:
            self._active_machine_name = machine_name
            self._sync_preview_button_state()
            return

        # Invalidate the old printer before making the new machine authoritative.
        self._client.stop()
        self._invalidate_lifecycle("active Cura printer changed")

        self._active_machine_id = machine_id
        self._active_machine_name = machine_name
        self._preview_follower_service.set_paused(False)
        self._preview_follower_service.reset_print_state()
        self._follow_controller.resume()
        self._last_capabilities = {}
        self._clear_scheduled_pauses(abort_request=True)
        self._clear_remote_gcode_index()
        self._remote_job_service.reset()
        self._remote_file_service.clear_identity()
        self._apply_timer_state()
        self._sync_preview_controls_visibility()

    def _active_printer_is_configured_for_following(self) -> bool:
        if self._active_machine_id == "unknown":
            return False
        config = self.current_printer_config()
        return bool(
            config.enabled
            and self._url_is_usable(self._normalise_base_url(config.url))
        )

    def _poll(self, force: bool = False) -> None:
        config = self.current_printer_config()
        if not force and not config.enabled:
            return
        base_url = self._normalise_base_url(config.url)
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
            layer_text = (
                f"layer {remote_layer}/{total}" if total > 0 else f"layer {remote_layer}"
            )
        return f"{name}: {layer_text} — {detail}"

    def _set_status(self, text: str) -> None:
        self._last_status_text = text
        self._sync_preview_button_state()

    def _valid_configured_url(self) -> bool:
        return self._url_is_usable(
            self._normalise_base_url(self.current_printer_config().url)
        )

    @staticmethod
    def _url_is_usable(url: str) -> bool:
        parsed = QUrl(url)
        return (
            parsed.isValid()
            and parsed.scheme() in ("http", "https")
            and bool(parsed.host())
        )

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        value = (value or "").strip().rstrip("/")
        if not value:
            return "http://"
        if not value.lower().startswith(("http://", "https://")):
            value = f"http://{value}"
        return value
