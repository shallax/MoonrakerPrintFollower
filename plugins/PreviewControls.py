from __future__ import annotations

import os
from PyQt6.QtCore import pyqtSlot
from UM.Logger import Logger


class PreviewControlsMixin:
    def _sync_preview_controls_visibility(self, *_args) -> None:
        if self._preview_overlay is None and self._action_panel_controls is None:
            self._update_toolhead_indicator()
            return
        is_preview = False
        try:
            stage = self._controller.getActiveStage()
            if stage is not None:
                stage_id = None
                get_id = getattr(stage, "getId", None)
                if callable(get_id):
                    stage_id = get_id()
                if not stage_id:
                    stage_id = getattr(stage, "stageId", None)
                is_preview = stage_id == "PreviewStage"
        except Exception as error:
            Logger.log("w", "Moonraker Print Follower could not determine Cura's active stage: %s", error)
        for controls in (self._preview_overlay, self._action_panel_controls):
            if controls is None:
                continue
            try:
                controls.setProperty("previewStageActive", is_preview)
            except Exception as error:
                Logger.log("w", "Moonraker Print Follower could not update Preview control visibility: %s", error)
        self._sync_preview_button_state()
        self._update_toolhead_indicator()

    def _reparent_preview_overlay(self) -> None:
        overlay = self._preview_overlay
        if overlay is None:
            return
        try:
            main_window = self._application.getMainWindow()
            if main_window is None or not hasattr(main_window, "contentItem"):
                return
            window_content = main_window.contentItem()
            if window_content is None:
                return
            set_parent_item = getattr(overlay, "setParentItem", None)
            if callable(set_parent_item):
                set_parent_item(window_content)
            try:
                overlay.setParent(window_content)
            except Exception:
                pass
        except Exception as error:
            Logger.log("w", "Moonraker Print Follower could not reparent empty Preview control: %s", error)

    def _create_preview_controls(self, *_args) -> None:
        if self._preview_overlay is not None and self._action_panel_controls is not None:
            return
        try:
            main_window = self._application.getMainWindow()
            if main_window is None or not hasattr(main_window, "contentItem"):
                return
            window_content = main_window.contentItem()
            if window_content is None:
                return
            plugin_path = os.path.dirname(os.path.abspath(__file__))
            if self._preview_overlay is None:
                overlay_path = os.path.join(plugin_path, "EmptyPreviewLoadButton.qml")
                overlay = self._application.createQmlComponent(overlay_path)
                if overlay is None:
                    Logger.log("e", "Moonraker Print Follower could not create empty-Preview control")
                    return
                try:
                    overlay.loadClicked.connect(self._confirm_force_load_current_print)
                except Exception as error:
                    Logger.logException("e", "Moonraker Print Follower could not connect empty-Preview Load button: %s", error)
                    try:
                        overlay.deleteLater()
                    except Exception:
                        pass
                    return
                self._preview_overlay = overlay
                try:
                    overlay.destroyed.connect(self._on_preview_overlay_destroyed)
                except Exception:
                    pass
            self._reparent_preview_overlay()
            if self._action_panel_controls is None:
                action_path = os.path.join(plugin_path, "PreviewActionPanelControls.qml")
                action_controls = self._application.createQmlComponent(action_path)
                if action_controls is None:
                    Logger.log("e", "Moonraker Print Follower could not create action-panel controls")
                else:
                    try:
                        action_controls.loadClicked.connect(self._confirm_force_load_current_print)
                        action_controls.pauseClicked.connect(self._toggle_following_pause)
                        action_controls.pauseAtLayerRequested.connect(self._toggle_pause_at_selected_layer)
                        action_controls.removePauseAtLayerRequested.connect(self._remove_scheduled_pause)
                        action_controls.clearPauseAtLayersRequested.connect(self._clear_scheduled_pauses_from_preview)
                    except Exception as error:
                        Logger.logException("e", "Moonraker Print Follower could not connect action-panel controls: %s", error)
                        try:
                            action_controls.deleteLater()
                        except Exception:
                            pass
                        action_controls = None
                    if action_controls is not None:
                        self._application.addAdditionalComponent("saveButton", action_controls)
                        self._action_panel_controls = action_controls
                        try:
                            action_controls.destroyed.connect(self._on_action_panel_controls_destroyed)
                        except Exception:
                            pass
            self._refresh_simulation_view_connection()
            self._sync_preview_button_state()
            self._sync_preview_controls_visibility()
        except Exception as error:
            Logger.logException("w", "Moonraker Print Follower could not add Preview controls: %s", error)

    @pyqtSlot()
    def confirmForceLoadCurrentPrint(self) -> None:
        self._confirm_force_load_current_print()

    @pyqtSlot()
    def toggleFollowingPause(self) -> None:
        self._toggle_following_pause()
