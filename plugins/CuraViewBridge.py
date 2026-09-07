from __future__ import annotations


class CuraViewBridgeMixin:
    def _on_preview_overlay_destroyed(self, obj=None) -> None:
        self._preview_overlay = None

    def _on_action_panel_controls_destroyed(self, obj=None) -> None:
        self._remove_additional_component_reference(
            self._action_panel_controls if self._action_panel_controls is not None else obj
        )
        self._action_panel_controls = None

    def _on_simulation_view_destroyed(self, obj=None) -> None:
        self._connected_simulation_view = None
        self._manual_view_signals_connected = False
        self._update_manual_view_watch_mode()

    def _remove_additional_component_reference(self, component) -> None:
        """Best-effort cleanup for Cura's add-only additional-component API."""
        if component is None:
            return
        remover = getattr(self._application, "removeAdditionalComponent", None)
        if callable(remover):
            try:
                remover("saveButton", component)
                return
            except Exception:
                pass
        components = getattr(self._application, "_additional_components", None)
        if not isinstance(components, dict):
            return
        row = components.get("saveButton")
        if not isinstance(row, list):
            return
        filtered = [item for item in row if item is not component]
        if len(filtered) == len(row):
            return
        components["saveButton"] = filtered
        try:
            self._application.additionalComponentsChanged.emit("saveButton")
        except Exception:
            pass

    def _disconnect_simulation_view_connection(self) -> None:
        view = self._connected_simulation_view
        self._connected_simulation_view = None
        self._manual_view_signals_connected = False
        if view is None:
            return
        try:
            activity_changed = getattr(view, "activityChanged", None)
            if activity_changed is not None:
                activity_changed.disconnect(self._on_simulation_activity_changed)
        except Exception:
            pass
        for signal_name in ("currentLayerNumChanged", "currentPathNumChanged"):
            try:
                signal = getattr(view, signal_name, None)
                if signal is not None:
                    signal.disconnect(self._on_preview_position_changed)
            except Exception:
                pass

    def _refresh_simulation_view_connection(self):
        view = None
        try:
            view = self._controller.getView("SimulationView")
        except Exception:
            pass
        if view is self._connected_simulation_view:
            return view
        old = self._connected_simulation_view
        if old is not None:
            try:
                destroyed = getattr(old, "destroyed", None)
                if destroyed is not None:
                    destroyed.disconnect(self._on_simulation_view_destroyed)
            except Exception:
                pass
            try:
                activity_changed = getattr(old, "activityChanged", None)
                if activity_changed is not None:
                    activity_changed.disconnect(self._on_simulation_activity_changed)
            except Exception:
                pass
            for signal_name in ("currentLayerNumChanged", "currentPathNumChanged"):
                try:
                    signal = getattr(old, signal_name, None)
                    if signal is not None:
                        signal.disconnect(self._on_preview_position_changed)
                except Exception:
                    pass
        self._connected_simulation_view = view
        self._manual_view_signals_connected = False
        if view is None:
            self._update_manual_view_watch_mode()
            return None
        try:
            destroyed = getattr(view, "destroyed", None)
            if destroyed is not None:
                destroyed.connect(self._on_simulation_view_destroyed)
        except Exception:
            pass
        try:
            activity_changed = getattr(view, "activityChanged", None)
            if activity_changed is not None:
                activity_changed.connect(self._on_simulation_activity_changed)
        except Exception:
            pass
        layer_signal = getattr(view, "currentLayerNumChanged", None)
        path_signal = getattr(view, "currentPathNumChanged", None)
        if layer_signal is not None and path_signal is not None:
            try:
                layer_signal.connect(self._on_preview_position_changed)
                path_signal.connect(self._on_preview_position_changed)
                self._manual_view_signals_connected = True
            except Exception:
                self._manual_view_signals_connected = False
        self._update_manual_view_watch_mode()
        return view

    def _on_simulation_activity_changed(self, *_args) -> None:
        """Refresh controls and catch up as soon as Cura finishes loading layer data."""
        self._sync_preview_button_state()
        self._clear_expected_preview_position()
        if (
            self._cura_has_toolpath()
            and self._pref_bool(self.PREF_ENABLED)
            and not self._following_paused
        ):
            self._queue_lifecycle_callback(lambda: self._poll(force=True))

    def _simulation_view(self):
        return self._refresh_simulation_view_connection()
