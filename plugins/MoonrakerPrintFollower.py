"""Cura extension that makes Preview follow a remote Klipper/Moonraker print.

The exact path uses Moonraker's ``print_stats.info.current_layer``.  If that
value is unavailable, an optional best-effort fallback maps the printer's
current G-code Z position to Cura's sliced layer heights.

No third-party Python packages are required.  Cura's bundled Qt networking is
used instead of QtWebSockets so the plugin works in stock packaged Cura builds.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from array import array
from bisect import bisect_right
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from UM.Extension import Extension
from UM.Logger import Logger
from UM.Scene.Iterator.DepthFirstIterator import DepthFirstIterator



class MoonrakerPrintFollower(QObject, Extension):
    """Synchronise Cura's SimulationView layer with a Moonraker print."""

    _remoteIndexReady = pyqtSignal(int, str, object, object, object, int, int)

    PLUGIN_ID = "Moonraker_Print_Follower"
    PREF_ROOT = "moonraker_print_follower"

    PREF_ENABLED = f"{PREF_ROOT}/enabled"
    PREF_URL = f"{PREF_ROOT}/url"
    PREF_API_KEY = f"{PREF_ROOT}/api_key"
    PREF_INTERVAL = f"{PREF_ROOT}/poll_interval_ms"
    PREF_ONE_BASED = f"{PREF_ROOT}/moonraker_layer_is_one_based"
    PREF_AUTO_PREVIEW = f"{PREF_ROOT}/auto_preview"
    PREF_Z_FALLBACK = f"{PREF_ROOT}/z_fallback"
    PREF_Z_TOLERANCE = f"{PREF_ROOT}/z_tolerance"
    PREF_PATH_FOLLOW = f"{PREF_ROOT}/path_follow"

    ACTIVE_STATES = {"printing", "paused"}

    def __init__(self, application) -> None:
        QObject.__init__(self)
        Extension.__init__(self)
        self._application = application
        self._preferences = application.getPreferences()
        self._controller = application.getController()

        self.setMenuName("Moonraker Print Follower")
        self.addMenuItem("Configure…", self._show_configuration_dialog)
        self.addMenuItem("Toggle following", self._toggle_following)
        self.addMenuItem("Poll now", self._poll_now)
        self.addMenuItem("Load current print…", self._confirm_force_load_current_print)

        self._register_preferences()

        self._network = QNetworkAccessManager()
        self._reply: Optional[QNetworkReply] = None
        self._reply_purpose: Optional[str] = None

        # A separate network manager is used for the one-time G-code download so
        # regular status polling can continue while a large file is being indexed.
        self._file_network = QNetworkAccessManager()
        self._file_reply: Optional[QNetworkReply] = None
        self._file_reply_filename: Optional[str] = None
        self._file_reply_generation = 0
        self._file_reply_job_key: Optional[Tuple[str, int, int]] = None

        self._timer = QTimer()
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._poll)

        self._dialog: Optional[QDialog] = None
        self._enabled_checkbox: Optional[QCheckBox] = None
        self._url_edit: Optional[QLineEdit] = None
        self._api_key_edit: Optional[QLineEdit] = None
        self._interval_edit: Optional[QLineEdit] = None
        self._one_based_checkbox: Optional[QCheckBox] = None
        self._auto_preview_checkbox: Optional[QCheckBox] = None
        self._z_fallback_checkbox: Optional[QCheckBox] = None
        self._z_tolerance_spin: Optional[QDoubleSpinBox] = None
        self._path_follow_checkbox: Optional[QCheckBox] = None
        self._status_label: Optional[QLabel] = None

        self._last_status_text = "Not connected"
        self._last_remote_filename: Optional[str] = None
        self._last_remote_state: Optional[str] = None
        self._last_extruder_position: Optional[float] = None
        self._preview_switched_for_job = False
        self._last_source: Optional[str] = None
        self._force_load_requested = False
        self._force_load_pending_filename: Optional[str] = None
        self._following_paused = False

        # Detect direct user interaction with Cura's Preview layer/path controls.
        # SimulationView does not expose a stable public "user changed slider"
        # signal across Cura 5.x, so watch the public current-value API instead.
        # Values written by this plugin establish the expected position; any later
        # deviation while following is active is a manual override and pauses the
        # session without changing the saved enabled preference.
        self._manual_view_watch_timer = QTimer()
        self._manual_view_watch_timer.setInterval(75)
        self._manual_view_watch_timer.timeout.connect(self._watch_for_manual_preview_change)
        self._manual_view_signals_connected = False
        self._applying_follow_update = 0
        self._expected_follow_layer: Optional[int] = None
        self._expected_follow_path: Optional[float] = None
        self._manual_view_ignore_until = 0.0

        self._preview_overlay = None
        self._action_panel_controls = None
        self._connected_simulation_view = None
        self._scene = None
        self._scene_structure_signature: Tuple[int, ...] = ()
        self._lifecycle_generation = 0
        self._destroyed = False
        self._slicing_in_progress = False
        self._scene_settle_until = 0.0

        # Identify a print independently from its filename. Moonraker retains
        # print_stats after completion and users often reprint the same file.
        # A monotonically increasing serial prevents stale cache/index reuse
        # when file_position or print_duration resets for a new run.
        self._remote_job_serial = 0
        self._remote_job_key: Optional[Tuple[str, int, int]] = None
        self._last_remote_file_position: Optional[int] = None
        self._last_remote_print_duration: Optional[float] = None

        # Keep the downloaded G-code available for the duration of the Cura
        # session. Cura reads G-code asynchronously, so the file must outlive
        # the call to readLocalFile(). TemporaryDirectory cleans it up when
        # Cura exits.
        self._temp_gcode_dir = tempfile.TemporaryDirectory(
            prefix="cura-moonraker-print-follower-"
        )
        self._cached_gcode_filename: Optional[str] = None
        self._cached_gcode_path: Optional[str] = None
        self._cached_gcode_job_key: Optional[Tuple[str, int, int]] = None
        # Keep Cura's in-flight file identity separate from the reusable cache.
        # A new Moonraker job may invalidate the cache while Cura is still
        # asynchronously finishing a read of the previous file.
        self._cura_load_path: Optional[str] = None
        self._cura_load_filename: Optional[str] = None
        self._cura_load_job_key: Optional[Tuple[str, int, int]] = None
        self._deferred_cache_dirs: set[str] = set()

        # Remote G-code index used for within-layer progress. Each entry stores
        # the layer byte range plus byte offsets of G0/G1/G2/G3 motion commands.
        # Arrays keep memory usage reasonable even for very large files.
        self._remote_index_filename: Optional[str] = None
        self._remote_index_job_key: Optional[Tuple[str, int, int]] = None
        self._remote_layer_ranges: List[Tuple[int, int]] = []
        self._remote_motion_offsets: List[array] = []
        self._remote_current_layer_map: Dict[int, int] = {}
        self._remote_index_build_filename: Optional[str] = None
        self._remote_index_build_job_key: Optional[Tuple[str, int, int]] = None
        self._remote_index_generation = 0
        self._remote_index_cancel_event: Optional[threading.Event] = None
        self._remote_index_thread: Optional[threading.Thread] = None
        self._cura_load_in_progress = False
        self._cura_load_started_at: Optional[float] = None
        self._remoteIndexReady.connect(self._on_remote_index_ready)

        file_completed = getattr(self._application, "fileCompleted", None)
        if file_completed is not None:
            try:
                file_completed.connect(self._on_cura_file_completed)
            except Exception:
                pass

        main_window_changed = getattr(self._application, "mainWindowChanged", None)
        if main_window_changed is not None:
            try:
                main_window_changed.connect(self._on_main_window_changed)
            except Exception:
                pass

        # Cura can rebuild view/scene objects while loading models. sceneChanged
        # also fires for harmless transforms/settings changes, so only structural
        # changes (nodes added/removed/replaced) invalidate follower work.
        try:
            self._scene = self._controller.getScene()
            self._scene_structure_signature = self._get_scene_structure_signature()
            scene_changed = getattr(self._scene, "sceneChanged", None)
            if scene_changed is not None:
                scene_changed.connect(self._on_scene_changed)
        except Exception:
            self._scene = None
            self._scene_structure_signature = ()

        # Slicing has explicit lifecycle signals. Suspend Preview writes while
        # CuraEngine is replacing layer data instead of treating every ordinary
        # sceneChanged notification as a destructive transition.
        try:
            backend = self._application.getBackend()
        except Exception:
            backend = None
        self._backend = backend
        if backend is not None:
            for signal_name, handler in (
                ("slicingStarted", self._on_slicing_started),
                ("slicingCancelled", self._on_slicing_finished),
                ("printDurationMessage", self._on_slicing_finished),
            ):
                signal = getattr(backend, signal_name, None)
                if signal is not None:
                    try:
                        signal.connect(handler)
                    except Exception:
                        pass

        active_view_changed = getattr(self._controller, "activeViewChanged", None)
        if active_view_changed is not None:
            try:
                active_view_changed.connect(self._on_active_view_changed)
            except Exception:
                pass

        # The controls have two placements: an empty-Preview overlay and Cura's
        # official action-panel extension row. Both remain strictly Preview-only.
        active_stage_changed = getattr(self._controller, "activeStageChanged", None)
        if active_stage_changed is not None:
            try:
                active_stage_changed.connect(self._sync_preview_controls_visibility)
            except Exception:
                pass

        try:
            if self._application.getMainWindow() is not None:
                self._create_preview_controls()
        except Exception:
            pass

        self._apply_timer_state()

    # ------------------------------------------------------------------
    # Preferences and UI
    # ------------------------------------------------------------------

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

    def _show_configuration_dialog(self) -> None:
        # Re-create the dialog each time so its fields always reflect the
        # persisted preference values.
        dialog = QDialog()
        dialog.setWindowTitle("Moonraker Print Follower")
        dialog.setMinimumWidth(520)
        self._dialog = dialog

        root = QVBoxLayout(dialog)

        intro = QLabel(
            "Keep Cura's Preview layer and toolpath sliders synchronised with "
            "the job currently printing in Klipper."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        connection_group = QGroupBox("Moonraker")
        connection_form = QFormLayout(connection_group)

        self._enabled_checkbox = QCheckBox("Enable automatic following")
        self._enabled_checkbox.setChecked(self._pref_bool(self.PREF_ENABLED))
        connection_form.addRow(self._enabled_checkbox)

        self._url_edit = QLineEdit(self._pref_str(self.PREF_URL))
        self._url_edit.setPlaceholderText("http://voron.local:7125")
        connection_form.addRow("URL", self._url_edit)

        self._api_key_edit = QLineEdit(self._pref_str(self.PREF_API_KEY))
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("Optional Moonraker API key")
        connection_form.addRow("API key", self._api_key_edit)

        self._interval_edit = QLineEdit(str(self._pref_int(self.PREF_INTERVAL, 750)))
        self._interval_edit.setPlaceholderText("Positive integer milliseconds, e.g. 137")
        connection_form.addRow("Poll interval (ms)", self._interval_edit)
        root.addWidget(connection_group)

        behaviour_group = QGroupBox("Layer synchronisation")
        behaviour_form = QFormLayout(behaviour_group)

        self._one_based_checkbox = QCheckBox(
            "Fallback when G-code mapping is unavailable: Moonraker current_layer is 1-based"
        )
        self._one_based_checkbox.setChecked(self._pref_bool(self.PREF_ONE_BASED))
        behaviour_form.addRow(self._one_based_checkbox)

        self._path_follow_checkbox = QCheckBox(
            "Follow progress through each layer (horizontal toolpath slider)"
        )
        self._path_follow_checkbox.setChecked(self._pref_bool(self.PREF_PATH_FOLLOW))
        behaviour_form.addRow(self._path_follow_checkbox)

        self._auto_preview_checkbox = QCheckBox(
            "Switch to Preview once when a print starts"
        )
        self._auto_preview_checkbox.setChecked(self._pref_bool(self.PREF_AUTO_PREVIEW))
        behaviour_form.addRow(self._auto_preview_checkbox)

        self._z_fallback_checkbox = QCheckBox(
            "Use Z-height fallback when Moonraker has no current_layer"
        )
        self._z_fallback_checkbox.setChecked(self._pref_bool(self.PREF_Z_FALLBACK))
        behaviour_form.addRow(self._z_fallback_checkbox)

        self._z_tolerance_spin = QDoubleSpinBox()
        self._z_tolerance_spin.setRange(0.005, 0.250)
        self._z_tolerance_spin.setDecimals(3)
        self._z_tolerance_spin.setSingleStep(0.005)
        self._z_tolerance_spin.setSuffix(" mm")
        self._z_tolerance_spin.setValue(self._pref_float(self.PREF_Z_TOLERANCE, 0.04))
        behaviour_form.addRow("Z match tolerance", self._z_tolerance_spin)
        root.addWidget(behaviour_group)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self._status_label = QLabel(self._last_status_text)
        self._status_label.setWordWrap(True)
        status_row.addWidget(self._status_label, 1)
        test_button = QPushButton("Test connection")
        test_button.clicked.connect(self._test_connection_from_dialog)
        status_row.addWidget(test_button)
        root.addLayout(status_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_dialog_and_close)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)

        dialog.finished.connect(self._dialog_finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _dialog_finished(self, _result: int) -> None:
        self._dialog = None
        self._enabled_checkbox = None
        self._url_edit = None
        self._api_key_edit = None
        self._interval_edit = None
        self._one_based_checkbox = None
        self._auto_preview_checkbox = None
        self._z_fallback_checkbox = None
        self._z_tolerance_spin = None
        self._path_follow_checkbox = None
        self._status_label = None

    def _save_dialog_and_close(self) -> None:
        if not self._dialog:
            return
        if self._save_dialog_settings():
            self._dialog.accept()

    def _save_dialog_settings(self) -> bool:
        if not all(
            (
                self._enabled_checkbox,
                self._url_edit,
                self._api_key_edit,
                self._interval_edit,
                self._one_based_checkbox,
                self._auto_preview_checkbox,
                self._z_fallback_checkbox,
                self._z_tolerance_spin,
                self._path_follow_checkbox,
            )
        ):
            return False

        interval_text = self._interval_edit.text().strip()
        try:
            interval = int(interval_text)
        except (TypeError, ValueError):
            self._set_status("Poll interval must be a positive whole number of milliseconds")
            self._interval_edit.setFocus()
            self._interval_edit.selectAll()
            return False
        if interval <= 0:
            self._set_status("Poll interval must be greater than 0 ms")
            self._interval_edit.setFocus()
            self._interval_edit.selectAll()
            return False

        self._preferences.setValue(self.PREF_ENABLED, self._enabled_checkbox.isChecked())
        self._preferences.setValue(self.PREF_URL, self._normalise_base_url(self._url_edit.text()))
        self._preferences.setValue(self.PREF_API_KEY, self._api_key_edit.text().strip())
        self._preferences.setValue(self.PREF_INTERVAL, interval)
        self._preferences.setValue(self.PREF_ONE_BASED, self._one_based_checkbox.isChecked())
        self._preferences.setValue(self.PREF_AUTO_PREVIEW, self._auto_preview_checkbox.isChecked())
        self._preferences.setValue(self.PREF_Z_FALLBACK, self._z_fallback_checkbox.isChecked())
        self._preferences.setValue(self.PREF_Z_TOLERANCE, self._z_tolerance_spin.value())
        self._preferences.setValue(self.PREF_PATH_FOLLOW, self._path_follow_checkbox.isChecked())

        self._apply_timer_state()
        self._sync_preview_button_state()
        return True

    def _toggle_following(self) -> None:
        enabled = not self._pref_bool(self.PREF_ENABLED)
        self._preferences.setValue(self.PREF_ENABLED, enabled)
        self._apply_timer_state()
        self._sync_preview_button_state()
        if enabled:
            self._set_status("Following enabled; waiting for Moonraker")
            self._poll()
        else:
            self._expected_follow_layer = None
            self._expected_follow_path = None
            self._set_status("Following disabled")

    def _toggle_following_pause(self) -> None:
        """Pause or resume Preview movement without changing saved preferences."""

        self._following_paused = not self._following_paused
        self._sync_preview_button_state()

        if self._following_paused:
            self._set_status("Following paused; Moonraker polling continues")
        else:
            self._expected_follow_layer = None
            self._expected_follow_path = None
            self._manual_view_ignore_until = time.monotonic() + 0.25
            self._set_status("Following resumed; catching up to the current print")
            self._poll(force=True)

    def _is_preview_stage_active(self) -> bool:
        try:
            stage = self._controller.getActiveStage()
            if stage is None:
                return False
            get_id = getattr(stage, "getId", None)
            stage_id = get_id() if callable(get_id) else getattr(stage, "stageId", None)
            return stage_id == "PreviewStage"
        except Exception:
            return False

    def _watch_for_manual_preview_change(self) -> None:
        """Fallback watcher for Cura builds without layer/path change signals."""
        self._check_for_manual_preview_change()

    def _on_preview_position_changed(self, *_args) -> None:
        """Event-driven manual override detection for normal Cura 5.x builds."""
        self._check_for_manual_preview_change()

    def _check_for_manual_preview_change(self) -> None:
        """Pause following when Cura's Preview position changes outside our write.

        Cura 5.13 exposes ``currentLayerNumChanged`` and
        ``currentPathNumChanged``. Those signals are preferred because they react
        immediately to a user drag. A 75 ms value watcher remains only as a
        compatibility fallback for builds that do not expose both signals.
        """
        if not self._pref_bool(self.PREF_ENABLED) or self._following_paused:
            return
        if self._applying_follow_update:
            return
        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            return
        if not self._is_preview_stage_active() or not self._cura_has_toolpath():
            self._expected_follow_layer = None
            self._expected_follow_path = None
            return
        if time.monotonic() < self._manual_view_ignore_until:
            return

        view = self._simulation_view()
        if view is None:
            return

        try:
            current_layer = int(view.getCurrentLayer())
        except Exception:
            return

        try:
            current_path = float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else None
        except Exception:
            current_path = None

        # Establish a baseline after loading, stage changes or resume. Do not
        # interpret Cura's own initialisation as user interaction.
        if self._expected_follow_layer is None:
            self._expected_follow_layer = current_layer
            self._expected_follow_path = current_path
            return

        layer_changed = current_layer != self._expected_follow_layer
        path_changed = (
            self._expected_follow_path is not None
            and current_path is not None
            and abs(current_path - self._expected_follow_path) >= 0.75
        )

        if not layer_changed and not path_changed:
            return

        self._following_paused = True
        self._expected_follow_layer = current_layer
        self._expected_follow_path = current_path
        self._sync_preview_button_state()
        if layer_changed:
            self._set_status("Following paused because the Preview layer was changed manually")
        else:
            self._set_status("Following paused because the Preview path position was changed manually")

    def _update_manual_view_watch_mode(self) -> None:
        """Use SimulationView signals when available, timer polling otherwise."""
        should_run = (
            self._pref_bool(self.PREF_ENABLED)
            and self._valid_configured_url()
            and not self._manual_view_signals_connected
        )
        if should_run:
            self._manual_view_watch_timer.start()
        else:
            self._manual_view_watch_timer.stop()

    def _remember_plugin_preview_position(self, view) -> None:
        """Record Cura's settled position after a plugin-driven follow update."""
        try:
            self._expected_follow_layer = int(view.getCurrentLayer())
        except Exception:
            self._expected_follow_layer = None
        try:
            self._expected_follow_path = (
                float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else None
            )
        except Exception:
            self._expected_follow_path = None
        # setLayer() can reset the path slider internally before/after setPath().
        # Ignore that brief Cura-internal transition, then resume watching.
        self._manual_view_ignore_until = time.monotonic() + 0.20

    def _queue_lifecycle_callback(self, callback, delay_ms: int = 0) -> None:
        generation = self._lifecycle_generation
        def run() -> None:
            if self._destroyed or generation != self._lifecycle_generation:
                return
            try:
                callback()
            except Exception as error:
                Logger.logException(
                    "e",
                    "Moonraker Print Follower delayed callback failed: %s",
                    error,
                )
        QTimer.singleShot(delay_ms, run)

    def _get_scene_structure_signature(self) -> Tuple[int, ...]:
        """Return a stable signature that changes only when scene nodes change.

        ``Scene.sceneChanged`` is deliberately broad in Cura and fires for
        transforms, settings and redraw-related updates. Using object identity of
        the actual node tree lets us distinguish those harmless notifications
        from model/G-code additions, removals and replacements.
        """
        try:
            scene = self._controller.getScene()
            root = scene.getRoot()
            return tuple(sorted(id(node) for node in DepthFirstIterator(root)))
        except Exception:
            return ()

    def _abort_status_reply(self) -> None:
        reply = self._reply
        self._reply = None
        self._reply_purpose = None
        if reply is None:
            return
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

    def _abort_file_reply(self) -> None:
        reply = self._file_reply
        self._file_reply = None
        self._file_reply_filename = None
        self._file_reply_job_key = None
        if reply is None:
            return
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

    def _invalidate_lifecycle(self, reason: str, abort_network: bool = True) -> None:
        self._lifecycle_generation += 1
        self._expected_follow_layer = None
        self._expected_follow_path = None
        self._manual_view_ignore_until = time.monotonic() + 0.35
        self._cancel_remote_index_build()

        if abort_network:
            self._abort_status_reply()
            self._abort_file_reply()
            self._file_reply_generation = self._lifecycle_generation

        was_loading = self._cura_load_in_progress
        self._cura_load_in_progress = False
        self._cura_load_started_at = None
        self._cura_load_path = None
        self._cura_load_filename = None
        self._cura_load_job_key = None
        # If Cura was still parsing a remote file, do not delete a deferred
        # cache directory underneath its asynchronous reader. TemporaryDirectory
        # will remove it at shutdown if no later fileCompleted event owns it.
        if not was_loading:
            self._cleanup_deferred_cache_dirs()
        self._force_load_requested = False
        self._force_load_pending_filename = None
        Logger.log("d", "Moonraker Print Follower invalidated scene lifecycle: %s", reason)

    def _on_scene_changed(self, source=None) -> None:
        # Do not invalidate while our own remote G-code reader is constructing its
        # scene. Those scene changes are expected and belong to the current load.
        if self._cura_load_in_progress:
            self._scene_structure_signature = self._get_scene_structure_signature()
            return
        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            self._scene_structure_signature = self._get_scene_structure_signature()
            return

        try:
            current_scene = self._controller.getScene()
        except Exception:
            current_scene = None

        if current_scene is not self._scene:
            try:
                old_signal = getattr(self._scene, "sceneChanged", None)
                if old_signal is not None:
                    old_signal.disconnect(self._on_scene_changed)
            except Exception:
                pass
            self._scene = current_scene
            if self._scene is not None:
                try:
                    self._scene.sceneChanged.connect(self._on_scene_changed)
                except Exception:
                    pass

        signature = self._get_scene_structure_signature()
        if signature == self._scene_structure_signature:
            return

        self._scene_structure_signature = signature
        self._scene_settle_until = time.monotonic() + 0.35
        self._invalidate_lifecycle("Cura scene structure changed")
        # A direct delete/add operation may not have a fileCompleted signal. Give
        # Cura one event-loop beat, then rediscover its current view and resume.
        self._queue_lifecycle_callback(self._finish_scene_settle, 100)

    def _finish_scene_settle(self) -> None:
        self._scene_structure_signature = self._get_scene_structure_signature()
        self._refresh_simulation_view_connection()
        self._sync_preview_button_state()
        if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
            self._poll(force=True)

    def _on_slicing_started(self, *_args) -> None:
        if self._cura_load_in_progress:
            return
        self._slicing_in_progress = True
        self._scene_settle_until = time.monotonic() + 0.5
        self._invalidate_lifecycle("Cura slicing started")

    def _on_slicing_finished(self, *_args) -> None:
        if not self._slicing_in_progress:
            return
        self._slicing_in_progress = False
        self._scene_settle_until = time.monotonic() + 0.35
        self._scene_structure_signature = self._get_scene_structure_signature()
        self._refresh_simulation_view_connection()
        self._sync_preview_button_state()
        if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
            self._queue_lifecycle_callback(lambda: self._poll(force=True), 100)

    def _on_active_view_changed(self, *_args) -> None:
        # Rebind SimulationView if Cura replaced the QObject underneath us.
        self._refresh_simulation_view_connection()
        self._sync_preview_controls_visibility()

    def _on_main_window_changed(self, *_args) -> None:
        self._invalidate_lifecycle("Cura main window changed")
        # Do not append another saveButton component on every mainWindowChanged.
        # Cura keeps additional components at application scope, so the existing
        # action control can be reparented by the new ActionPanel. Only the empty
        # Preview overlay needs its visual parent updated.
        self._reparent_preview_overlay()
        if self._preview_overlay is None or self._action_panel_controls is None:
            self._create_preview_controls()
        try:
            self._application.additionalComponentsChanged.emit("saveButton")
        except Exception:
            pass
        self._sync_preview_controls_visibility()

    def _on_preview_overlay_destroyed(self, obj=None) -> None:
        self._preview_overlay = None

    def _on_action_panel_controls_destroyed(self, obj=None) -> None:
        self._remove_additional_component_reference(
            self._action_panel_controls if self._action_panel_controls is not None else obj
        )
        self._action_panel_controls = None

    def _on_simulation_view_destroyed(self, obj=None) -> None:
        # This handler is connected only to the currently tracked view. Avoid
        # comparing a destroyed Qt proxy: depending on PyQt lifetime timing the
        # object passed by destroyed() may not compare identically to our proxy.
        self._connected_simulation_view = None
        self._manual_view_signals_connected = False
        self._update_manual_view_watch_mode()

    def _remove_additional_component_reference(self, component) -> None:
        """Best-effort cleanup for Cura's add-only additional-component API.

        Cura 5.13 exposes ``addAdditionalComponent`` but no corresponding public
        remove call. On normal main-window rebuilds we reuse the same component,
        so this is only needed when the component is actually destroyed or the
        plugin shuts down. The private-map fallback is capability-checked and
        isolated here so a future Cura version can simply skip it safely.
        """
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

    def _sync_preview_button_state(self, *_args) -> None:
        has_toolpath = self._cura_has_toolpath()
        for controls in (self._preview_overlay, self._action_panel_controls):
            if controls is None:
                continue
            try:
                controls.setProperty("followingPaused", self._following_paused)
                controls.setProperty(
                    "followingEnabled", self._pref_bool(self.PREF_ENABLED)
                )
                controls.setProperty("hasToolpath", has_toolpath)
            except Exception:
                pass

    def _on_simulation_activity_changed(self, *_args) -> None:
        """Refresh controls and catch up as soon as Cura finishes loading layer data."""

        self._sync_preview_button_state()
        self._expected_follow_layer = None
        self._expected_follow_path = None
        self._manual_view_ignore_until = time.monotonic() + 0.25
        if (
            self._cura_has_toolpath()
            and self._pref_bool(self.PREF_ENABLED)
            and not self._following_paused
        ):
            # readLocalFile() parses G-code asynchronously. A normal timer tick can
            # arrive while SimulationView still reports zero layers and be clamped
            # to layer 0. Force a fresh poll the moment Cura announces real layer
            # data so following resumes immediately after a manual remote load.
            self._queue_lifecycle_callback(lambda: self._poll(force=True))

    def _cura_has_toolpath(self) -> bool:
        """Return True only when Cura's SimulationView actually has layer data."""

        view = self._simulation_view()
        if view is None:
            return False

        get_activity = getattr(view, "getActivity", None)
        if callable(get_activity):
            try:
                return bool(get_activity())
            except Exception:
                pass

        # Fallback for unusual Cura builds: actual layer data is stronger evidence
        # than getMaxLayers(), because a valid one-layer print also has max layer 0.
        get_layer_data = getattr(view, "getLayerData", None)
        if callable(get_layer_data):
            try:
                return get_layer_data() is not None
            except Exception:
                pass
        return False

    def _sync_preview_controls_visibility(self, *_args) -> None:
        """Expose both control placements only while Preview is active.

        The QML components themselves decide which placement is visible based on
        ``CuraApplication.platformActivity``: the empty-Preview overlay is used
        when Cura has no action panel, and the registered ``saveButton`` component
        is used once Cura's Slice/Save/Upload panel exists.
        """

        if self._preview_overlay is None and self._action_panel_controls is None:
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
            Logger.log(
                "w",
                "Moonraker Print Follower could not determine Cura's active stage: %s",
                error,
            )

        for controls in (self._preview_overlay, self._action_panel_controls):
            if controls is None:
                continue
            try:
                controls.setProperty("previewStageActive", is_preview)
            except Exception as error:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not update Preview control visibility: %s",
                    error,
                )
        self._sync_preview_button_state()

    def _poll_now(self) -> None:
        self._poll(force=True)

    def _apply_timer_state(self) -> None:
        interval = self._pref_int(self.PREF_INTERVAL, 750)
        if interval <= 0:
            self._timer.stop()
            self._manual_view_watch_timer.stop()
            self._set_status("Poll interval must be greater than 0 ms")
            return

        # Deliberately do not clamp, round, snap or impose a plugin-side upper
        # limit. The exact positive integer entered by the user is passed to Qt.
        # If the underlying Qt build cannot represent it, report that rather
        # than silently changing the requested interval.
        try:
            self._timer.setInterval(interval)
            if self._pref_bool(self.PREF_ENABLED) and self._valid_configured_url():
                self._timer.start(interval)
                self._update_manual_view_watch_mode()
            else:
                self._timer.stop()
                self._manual_view_watch_timer.stop()
                self._expected_follow_layer = None
                self._expected_follow_path = None
        except (OverflowError, TypeError, ValueError) as error:
            self._timer.stop()
            self._manual_view_watch_timer.stop()
            self._set_status(f"Qt rejected poll interval {interval} ms: {error}")
            Logger.log(
                "w",
                "Moonraker Print Follower could not apply poll interval %s ms: %s",
                interval,
                error,
            )

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
            Logger.log(
                "w",
                "Moonraker Print Follower could not reparent empty Preview control: %s",
                error,
            )

    def _create_preview_controls(self, *_args) -> None:
        """Create adaptive Preview controls using Cura's native action-panel slot.

        When Cura has no platform activity, its entire bottom-right action panel is
        absent, so a small direct overlay keeps ``Load current print`` reachable.
        As soon as the action panel exists, the overlay hides itself and a second
        component registered in Cura's official ``saveButton`` extension area takes
        over. Cura then lays our controls out alongside other plugins (for example
        Post Processing) instead of us guessing the panel's size or position.
        """

        if self._preview_overlay is not None and self._action_panel_controls is not None:
            return
        try:
            from UM.PluginRegistry import PluginRegistry

            main_window = self._application.getMainWindow()
            if main_window is None or not hasattr(main_window, "contentItem"):
                return
            window_content = main_window.contentItem()
            if window_content is None:
                return

            plugin_path = PluginRegistry.getInstance().getPluginPath(self.PLUGIN_ID)
            if not plugin_path:
                return

            if self._preview_overlay is None:
                overlay_path = os.path.join(plugin_path, "EmptyPreviewLoadButton.qml")
                overlay = self._application.createQmlComponent(overlay_path)
                if overlay is None:
                    Logger.log("e", "Moonraker Print Follower could not create empty-Preview control")
                    return

                try:
                    overlay.loadClicked.connect(self._confirm_force_load_current_print)
                except Exception as error:
                    Logger.logException(
                        "e",
                        "Moonraker Print Follower could not connect empty-Preview Load button: %s",
                        error,
                    )
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
                    except Exception as error:
                        Logger.logException(
                            "e",
                            "Moonraker Print Follower could not connect action-panel controls: %s",
                            error,
                        )
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

                # Rebind to Cura's current SimulationView instance. Cura can replace
            # this QObject during model/slice transitions.
            self._refresh_simulation_view_connection()

            self._sync_preview_button_state()
            self._sync_preview_controls_visibility()
        except Exception as error:
            Logger.logException(
                "w",
                "Moonraker Print Follower could not add Preview controls: %s",
                error,
            )

    @pyqtSlot()
    def confirmForceLoadCurrentPrint(self) -> None:
        """QML entry point for the Preview Load current print button."""

        self._confirm_force_load_current_print()

    @pyqtSlot()
    def toggleFollowingPause(self) -> None:
        """QML entry point for the Preview Pause/Resume following button."""

        self._toggle_following_pause()

    def _confirm_force_load_current_print(self) -> None:
        """Ask for confirmation using Cura's own reliable Qt Widgets path.

        Cura itself uses ``QMessageBox.question(None, ...)`` for destructive
        confirmations. Using that exact synchronous API avoids the event and
        signal-delivery problems we hit with both QML dialogs and a hand-built
        modeless QDialog.
        """

        try:
            # Keep the manual action Preview-oriented. The Extensions-menu entry
            # can be invoked from Prepare, so switch stages before asking.
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

        # Defer one event-loop turn so a requested stage change can complete.
        self._queue_lifecycle_callback(self._ask_force_load_question)

    def _ask_force_load_question(self) -> None:
        """Synchronously ask Yes/No, then act on the returned button value."""

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

        # The QMessageBox has already closed at this point. Queue the scene/network
        # change onto the next event-loop turn rather than doing it inside the
        # nested QMessageBox event loop.
        self._queue_lifecycle_callback(self._force_load_current_print)

    def _force_load_current_print(self) -> None:
        """Resolve the active Moonraker job, re-download it and replace Cura's scene."""

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL before loading the current print")
            return

        self._force_load_requested = True
        self._force_load_pending_filename = None
        self._set_status("Finding the current Moonraker print…")
        self._issue_status_request(
            base_url,
            self._pref_str(self.PREF_API_KEY),
            purpose="force_load",
        )

    def _start_forced_gcode_download(self, filename: str) -> None:
        if not filename:
            self._force_load_requested = False
            self._set_status("Moonraker did not report a current G-code filename")
            return

        self._force_load_pending_filename = filename

        # The follower normally downloads the active G-code shortly after a print
        # starts so it can map Moonraker's file_position to Cura's path slider.
        # If that exact job is already cached, loading it should be immediate: do
        # not throw away a perfectly good multi-megabyte file and download/index
        # it all over again just because the user clicked the button.
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            self._set_status(f"{filename}: loading cached current print into Cura Preview…")
            self._queue_lifecycle_callback(lambda f=filename: self._load_cached_remote_gcode_forced(f))
            return

        # If the same file is already downloading for path indexing, reuse that
        # request. The reply handler will cache it, start Cura loading immediately,
        # and build the path index separately.
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

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        encoded_name = quote(filename, safe="/")
        endpoint = f"{base_url}/server/files/gcodes/{encoded_name}"
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/octet-stream")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(30000)
        api_key = self._pref_str(self.PREF_API_KEY)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))

        self._file_reply_filename = filename
        self._file_reply_generation = self._lifecycle_generation
        self._file_reply_job_key = self._remote_job_key
        reply = self._file_network.get(request)
        self._file_reply = reply
        reply_generation = self._file_reply_generation
        reply_job_key = self._file_reply_job_key
        reply.finished.connect(
            lambda r=reply, f=filename, g=reply_generation, j=reply_job_key: self._handle_gcode_reply(r, f, g, j)
        )
        self._set_status(f"{filename}: downloading current print…")

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

        # Keep our Python-heavy path indexer out of Cura's way while Cura parses
        # the G-code. The index is rebuilt after Cura emits fileCompleted.
        self._cancel_remote_index_build()
        self._cura_load_in_progress = True
        self._cura_load_started_at = time.perf_counter()
        self._cura_load_path = path
        self._cura_load_filename = filename
        self._cura_load_job_key = self._cached_gcode_job_key

        try:
            Logger.log(
                "i",
                "Moonraker Print Follower forcibly replacing Cura contents with remote G-code: %s",
                filename,
            )
            # Use Cura's public, supported loader exactly as the last known-good
            # v0.9.8 implementation did. Do not bypass CuraApplication's normal
            # ReadFileJob / GCodeReader lifecycle with private APIs.
            self._application.readLocalFile(
                QUrl.fromLocalFile(path),
                add_to_recent_files=False,
            )
            self._set_status(f"{filename}: loading into Cura Preview…")
        except Exception as error:
            self._cura_load_in_progress = False
            self._cura_load_started_at = None
            self._cura_load_path = None
            self._cura_load_filename = None
            self._cura_load_job_key = None
            self._cleanup_deferred_cache_dirs()
            self._force_load_requested = False
            self._force_load_pending_filename = None
            Logger.logException(
                "e",
                "Moonraker Print Follower could not force-load remote G-code into Cura: %s",
                error,
            )
            self._set_status(f"Could not load current print into Cura: {error}")

    # ------------------------------------------------------------------
    # Moonraker HTTP
    # ------------------------------------------------------------------

    def _poll(self, force: bool = False) -> None:
        if not force and not self._pref_bool(self.PREF_ENABLED):
            return
        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            self._set_status("Set a Moonraker URL in Extensions → Moonraker Print Follower")
            return
        self._issue_status_request(
            base_url,
            self._pref_str(self.PREF_API_KEY),
            purpose="poll",
        )

    def _test_connection_from_dialog(self) -> None:
        if not self._url_edit or not self._api_key_edit:
            return
        base_url = self._normalise_base_url(self._url_edit.text())
        if not self._url_is_usable(base_url):
            self._set_status("Enter a Moonraker URL, e.g. http://voron.local:7125")
            return
        self._set_status("Testing…")
        self._issue_status_request(
            base_url,
            self._api_key_edit.text().strip(),
            purpose="test",
        )

    def _issue_status_request(self, base_url: str, api_key: str, purpose: str) -> None:
        if self._reply is not None and self._reply.isRunning():
            if purpose == "force_load":
                # A normal poll queries the same print_stats object we need here.
                # Promote the in-flight reply instead of dropping the explicit
                # user action or creating a second request path.
                self._reply_purpose = "force_load"
                self._set_status("Finding the current Moonraker print…")
            elif purpose == "test":
                self._set_status("A Moonraker request is already in progress")
            return

        endpoint = f"{base_url}/printer/objects/query?print_stats&gcode_move&virtual_sdcard"
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))

        self._reply_purpose = purpose
        reply = self._network.get(request)
        self._reply = reply
        reply.finished.connect(lambda r=reply: self._handle_status_reply(r))

    def _handle_status_reply(self, reply: QNetworkReply) -> None:
        # Ignore a late completion from an aborted/replaced request. Capturing
        # the actual reply object prevents an old ``finished`` signal from ever
        # processing the state belonging to a newer request.
        if reply is not self._reply:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return

        purpose = self._reply_purpose or "poll"
        self._reply = None
        self._reply_purpose = None

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._set_status(f"Moonraker error: {reply.errorString()}")
                return

            raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
            payload = json.loads(raw)
            result = payload.get("result") or {}
            status = result.get("status") or {}
            print_stats = status.get("print_stats") or {}
            gcode_move = status.get("gcode_move") or {}
            virtual_sdcard = status.get("virtual_sdcard") or {}

            if purpose == "test":
                state = str(print_stats.get("state") or "unknown")
                filename = str(print_stats.get("filename") or "")
                suffix = f" — {filename}" if filename else ""
                self._set_status(f"Connected to Moonraker; printer state: {state}{suffix}")
                return

            if purpose == "force_load":
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
                self._start_forced_gcode_download(filename)
                return

            self._apply_remote_status(print_stats, gcode_move, virtual_sdcard)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
            Logger.logException("w", "Moonraker Print Follower could not parse Moonraker status: %s", error)
            self._set_status(f"Invalid Moonraker response: {error}")
        except Exception as error:  # Keep an extension failure from destabilising Cura.
            Logger.logException("e", "Moonraker Print Follower status handling failed: %s", error)
            self._set_status(f"Print follower error: {error}")
        finally:
            reply.deleteLater()

    # ------------------------------------------------------------------
    # Layer synchronisation
    # ------------------------------------------------------------------

    def _update_remote_job_identity(
        self,
        print_stats: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
    ) -> Optional[Tuple[str, int, int]]:
        """Track the actual print run, not merely the G-code filename.

        Moonraker retains print_stats after completion and users commonly print
        the same file repeatedly. Filename-only caching can therefore reuse an
        index from the previous run. File size plus reset detection for
        file_position / print_duration gives us a stable per-run identity without
        another network request.
        """
        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")
        active = state in self.ACTIVE_STATES and bool(filename)

        try:
            file_size = int(virtual_sdcard.get("file_size") or 0)
        except (TypeError, ValueError):
            file_size = 0
        try:
            file_position = int(virtual_sdcard.get("file_position") or 0)
        except (TypeError, ValueError):
            file_position = 0
        try:
            print_duration = float(print_stats.get("print_duration") or 0.0)
        except (TypeError, ValueError):
            print_duration = 0.0

        new_job = False
        current = self._remote_job_key
        if active:
            if current is None:
                new_job = True
            elif current[0] != filename or current[1] != file_size:
                new_job = True
            elif self._last_remote_state not in self.ACTIVE_STATES:
                new_job = True
            elif (
                self._last_remote_file_position is not None
                and file_position < self._last_remote_file_position
            ):
                # virtual_sdcard.file_position is monotonic within a run. Any
                # backwards movement therefore identifies a restarted/replaced
                # job, including very small G-code files.
                new_job = True
            elif (
                self._last_remote_print_duration is not None
                and print_duration + 0.05 < self._last_remote_print_duration
            ):
                new_job = True

            if new_job:
                self._remote_job_serial += 1
                self._clear_remote_gcode_index()
                self._remote_job_key = (filename, file_size, self._remote_job_serial)
                Logger.log(
                    "i",
                    "Moonraker Print Follower detected new print run #%d: %s (%d bytes)",
                    self._remote_job_serial,
                    filename,
                    file_size,
                )

            self._last_remote_file_position = file_position
            self._last_remote_print_duration = print_duration
        else:
            self._last_remote_file_position = None
            self._last_remote_print_duration = None

        return self._remote_job_key

    def _apply_remote_status(
        self,
        print_stats: Dict[str, Any],
        gcode_move: Dict[str, Any],
        virtual_sdcard: Dict[str, Any],
    ) -> None:
        state = str(print_stats.get("state") or "")
        filename = str(print_stats.get("filename") or "")

        previous_filename = self._last_remote_filename
        self._update_remote_job_identity(print_stats, virtual_sdcard)

        if filename != previous_filename:
            self._last_extruder_position = None
            self._preview_switched_for_job = False
            self._last_source = None
        self._last_remote_filename = filename

        if state != self._last_remote_state:
            if state not in self.ACTIVE_STATES:
                self._last_extruder_position = None
                self._preview_switched_for_job = False
            self._last_remote_state = state

        if state not in self.ACTIVE_STATES:
            label = state or "unknown"
            suffix = f" — {filename}" if filename else ""
            self._set_status(f"Moonraker connected; printer is {label}{suffix}")
            return

        if self._following_paused:
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="following paused; Moonraker polling continues",
                )
            )
            return

        # Keep the active G-code cached so an explicit Load current print can be
        # nearly immediate.  Do not burn CPU indexing it while Cura has no local
        # toolpath to follow: that work is useless until Preview contains layer
        # data and, worse, can contend with Cura's own G-code parser.
        if self._pref_bool(self.PREF_PATH_FOLLOW) and filename:
            if self._cura_has_toolpath() and not self._slicing_in_progress:
                self._ensure_remote_gcode_index(filename)
            else:
                self._ensure_remote_gcode_cached(filename)

        if self._slicing_in_progress or time.monotonic() < self._scene_settle_until:
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="Cura is rebuilding local layer data; following temporarily suspended",
                )
            )
            return

        view = self._simulation_view()
        if view is None or not hasattr(view, "setLayer"):
            self._set_status("Connected, but Cura's SimulationView is unavailable")
            return

        self._maybe_switch_to_preview()

        info = print_stats.get("info") or {}
        remote_layer = info.get("current_layer")
        total_layer = info.get("total_layer")

        target_layer: Optional[int] = None
        source = ""

        if remote_layer is not None:
            try:
                raw_remote_layer = int(remote_layer)
                if (
                    self._remote_index_filename == filename
                    and raw_remote_layer in self._remote_current_layer_map
                ):
                    # Prefer the CURRENT_LAYER values embedded in the actual G-code.
                    # This makes layer numbering self-describing and avoids an
                    # off-by-one mismatch if the preference namespace was reset or
                    # a slicer/macro reports zero-based layers instead of one-based.
                    target_layer = self._remote_current_layer_map[raw_remote_layer]
                    source = "Moonraker current_layer (G-code mapped)"
                else:
                    target_layer = raw_remote_layer
                    if self._pref_bool(self.PREF_ONE_BASED):
                        target_layer -= 1
                    source = "Moonraker current_layer"
            except (TypeError, ValueError):
                target_layer = None

        if target_layer is None and self._pref_bool(self.PREF_Z_FALLBACK):
            target_layer = self._layer_from_z(view, gcode_move)
            if target_layer is not None:
                source = "Z-height fallback"

        if target_layer is None:
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=total_layer,
                    detail="waiting for layer data",
                )
            )
            return

        try:
            max_layer = int(view.getMaxLayers()) if hasattr(view, "getMaxLayers") else target_layer
        except Exception:
            max_layer = target_layer

        # Cura's SimulationView layer is zero-based and its max is the largest
        # valid zero-based index.
        target_layer = max(0, min(target_layer, max(0, max_layer)))

        # If the local preview is clearly not the same sliced job, avoid
        # silently claiming perfect synchronisation.  We still follow (clamped)
        # because a user may intentionally be inspecting a similar G-code file.
        mismatch = ""
        try:
            if total_layer is not None:
                total = int(total_layer)
                local_total = max_layer + 1
                if total > 0 and local_total > 0 and abs(total - local_total) > 2:
                    mismatch = f"; layer-count mismatch remote {total} / local {local_total}"
        except (TypeError, ValueError):
            pass

        try:
            current = int(view.getCurrentLayer()) if hasattr(view, "getCurrentLayer") else -1
        except Exception:
            current = -1

        path_detail = ""
        self._applying_follow_update += 1
        try:
            if current != target_layer:
                view.setLayer(target_layer)

            if self._pref_bool(self.PREF_PATH_FOLLOW):
                path_detail = self._apply_path_progress(view, target_layer, virtual_sdcard)
        finally:
            self._applying_follow_update = max(0, self._applying_follow_update - 1)

        self._remember_plugin_preview_position(view)

        self._last_source = source
        detail = f"following via {source}"
        if path_detail:
            detail += f"; {path_detail}"
        detail += mismatch
        self._set_status(
            self._active_status_text(
                filename,
                remote_layer=target_layer + 1,
                total_layer=total_layer,
                detail=detail,
            )
        )

    def _apply_path_progress(
        self,
        view,
        target_layer: int,
        virtual_sdcard: Dict[str, Any],
    ) -> str:
        """Drive Cura's horizontal path slider using Moonraker file position.

        The remote G-code is indexed once per print. For each Cura layer we
        retain the byte offsets of motion commands. ``virtual_sdcard.file_position``
        then tells us approximately how many of those commands Klipper has
        consumed. Scaling that count to Cura's path count gives a much closer
        visual match than using whole-file percentage or elapsed time.
        """

        if not hasattr(view, "setPath") or not hasattr(view, "getMaxPaths"):
            return "within-layer tracking unavailable in this Cura build"

        if (
            self._remote_index_filename != self._last_remote_filename
            or self._remote_index_job_key != self._remote_job_key
        ):
            # setLayer() normally resets the horizontal slider to the end of the
            # layer. While the remote file is still being indexed, show the start
            # instead so we do not misleadingly display a completed layer.
            try:
                view.setPath(0.0)
            except Exception:
                pass
            if (
                (self._file_reply is not None and self._file_reply.isRunning())
                or self._remote_index_build_filename == self._last_remote_filename
            ):
                return "indexing remote G-code"
            return "waiting for remote G-code index"

        if target_layer < 0 or target_layer >= len(self._remote_layer_ranges):
            return "no remote path index for this layer"

        try:
            file_position = int(virtual_sdcard.get("file_position"))
        except (TypeError, ValueError):
            return "waiting for file position"

        try:
            max_paths = int(view.getMaxPaths())
        except Exception:
            return "Cura path count unavailable"

        if max_paths <= 0:
            return "layer has no toolpaths"

        layer_start, layer_end = self._remote_layer_ranges[target_layer]
        if layer_end <= layer_start:
            return "invalid remote layer range"

        position = max(layer_start, min(file_position, layer_end))
        motions = self._remote_motion_offsets[target_layer]

        if len(motions) > 0:
            completed = bisect_right(motions, position)
            fraction = completed / len(motions)
            method = "motion index"
        else:
            fraction = (position - layer_start) / (layer_end - layer_start)
            method = "byte position"

        fraction = max(0.0, min(1.0, float(fraction)))
        target_path = fraction * max_paths

        try:
            current_path = float(view.getCurrentPath()) if hasattr(view, "getCurrentPath") else -1.0
        except Exception:
            current_path = -1.0

        # Avoid needlessly forcing a redraw for tiny changes. A half path is
        # smaller than the slider can usefully communicate visually.
        if abs(current_path - target_path) >= 0.5:
            view.setPath(target_path)

        return f"path {round(target_path)}/{max_paths} ({fraction * 100:.1f}%, {method})"

    def _cancel_remote_index_build(self, wait: bool = False, timeout: float = 1.0) -> None:
        """Ask the current index worker to stop and invalidate its result.

        Normal scene changes cancel cooperatively without blocking Cura's GUI.
        Shutdown and replacement of a worker may wait briefly so a daemon thread
        is not left running against plugin state that is being torn down.
        """

        event = self._remote_index_cancel_event
        if event is not None:
            event.set()
        thread = self._remote_index_thread
        self._remote_index_cancel_event = None
        self._remote_index_generation += 1
        self._remote_index_build_filename = None
        self._remote_index_build_job_key = None
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
            self._remote_index_thread = None

    def _cleanup_cached_job_dir(self, path: Optional[str]) -> None:
        """Remove a per-job cache directory when Cura no longer needs it."""
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

    def _discard_cached_gcode(self) -> None:
        old_path = self._cached_gcode_path
        self._cached_gcode_filename = None
        self._cached_gcode_path = None
        self._cached_gcode_job_key = None
        self._cleanup_cached_job_dir(old_path)

    def _clear_remote_gcode_index(self) -> None:
        self._cancel_remote_index_build()
        self._remote_index_filename = None
        self._remote_index_job_key = None
        self._remote_layer_ranges = []
        self._remote_motion_offsets = []
        self._remote_current_layer_map = {}
        self._discard_cached_gcode()

        self._abort_file_reply()

    def _ensure_remote_gcode_cached(self, filename: str) -> None:
        """Download the active job once without starting a path-index build."""

        if not filename:
            return
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            return
        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_key
            ):
                return
            self._abort_file_reply()

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            return

        encoded_name = quote(filename, safe="/")
        endpoint = f"{base_url}/server/files/gcodes/{encoded_name}"
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/octet-stream")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(30000)
        api_key = self._pref_str(self.PREF_API_KEY)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))

        self._file_reply_filename = filename
        self._file_reply_generation = self._lifecycle_generation
        self._file_reply_job_key = self._remote_job_key
        reply = self._file_network.get(request)
        self._file_reply = reply
        reply_generation = self._file_reply_generation
        reply_job_key = self._file_reply_job_key
        reply.finished.connect(
            lambda r=reply, f=filename, g=reply_generation, j=reply_job_key: self._handle_gcode_reply(r, f, g, j)
        )

    def _ensure_remote_gcode_index(self, filename: str) -> None:
        if not filename or self._cura_load_in_progress or self._slicing_in_progress:
            return

        if (
            self._remote_index_filename == filename
            and self._remote_index_job_key == self._remote_job_key
        ):
            return

        if (
            self._remote_index_build_filename == filename
            and self._remote_index_build_job_key == self._remote_job_key
        ):
            return

        # If we already have the current job cached (for example because the user
        # just loaded it into Cura), build the path index from that local copy in a
        # worker thread instead of downloading it again.
        if (
            self._cached_gcode_filename == filename
            and self._cached_gcode_path
            and self._cached_gcode_job_key == self._remote_job_key
            and os.path.isfile(self._cached_gcode_path)
        ):
            self._start_remote_gcode_index_build_from_file(filename, self._cached_gcode_path)
            return

        if self._file_reply is not None and self._file_reply.isRunning():
            if (
                self._file_reply_filename == filename
                and self._file_reply_job_key == self._remote_job_key
            ):
                return
            self._abort_file_reply()

        base_url = self._normalise_base_url(self._pref_str(self.PREF_URL))
        if not self._url_is_usable(base_url):
            return

        encoded_name = quote(filename, safe="/")
        endpoint = f"{base_url}/server/files/gcodes/{encoded_name}"
        request = QNetworkRequest(QUrl(endpoint))
        request.setRawHeader(b"Accept", b"application/octet-stream")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(30000)
        api_key = self._pref_str(self.PREF_API_KEY)
        if api_key:
            request.setRawHeader(b"X-Api-Key", api_key.encode("utf-8"))

        self._file_reply_filename = filename
        self._file_reply_generation = self._lifecycle_generation
        self._file_reply_job_key = self._remote_job_key
        reply = self._file_network.get(request)
        self._file_reply = reply
        reply_generation = self._file_reply_generation
        reply_job_key = self._file_reply_job_key
        reply.finished.connect(
            lambda r=reply, f=filename, g=reply_generation, j=reply_job_key: self._handle_gcode_reply(r, f, g, j)
        )

    def _start_remote_gcode_index_build(self, filename: str, data: bytes) -> None:
        """Build the motion-offset index off Cura's GUI thread."""

        if not filename:
            return
        job_key = self._remote_job_key
        if self._remote_index_filename == filename and self._remote_index_job_key == job_key:
            return
        if self._remote_index_build_filename == filename and self._remote_index_build_job_key == job_key:
            return

        if self._remote_index_thread is not None and self._remote_index_thread.is_alive():
            self._cancel_remote_index_build(wait=True, timeout=0.5)
            if self._remote_index_thread is not None and self._remote_index_thread.is_alive():
                self._queue_lifecycle_callback(
                    lambda f=filename, d=data: self._start_remote_gcode_index_build(f, d),
                    100,
                )
                return

        generation = self._remote_index_generation
        lifecycle_generation = self._lifecycle_generation
        job_serial = job_key[2] if job_key is not None else 0
        cancel_event = threading.Event()
        self._remote_index_cancel_event = cancel_event
        self._remote_index_build_filename = filename
        self._remote_index_build_job_key = job_key

        def worker() -> None:
            try:
                ranges, motions, layer_map = self._build_remote_gcode_index(data, cancel_event)
            except Exception as error:
                Logger.logException(
                    "w",
                    "Moonraker Print Follower failed to index remote G-code %s: %s",
                    filename,
                    error,
                )
                ranges, motions, layer_map = [], [], {}
            if not self._destroyed:
                self._remoteIndexReady.emit(
                    generation,
                    filename,
                    ranges,
                    motions,
                    layer_map,
                    lifecycle_generation,
                    job_serial,
                )

        thread = threading.Thread(
            target=worker,
            name="MoonrakerPrintFollowerIndex",
            daemon=True,
        )
        self._remote_index_thread = thread
        thread.start()

    def _start_remote_gcode_index_build_from_file(self, filename: str, path: str) -> None:
        if not filename:
            return
        job_key = self._remote_job_key
        if self._remote_index_build_filename == filename and self._remote_index_build_job_key == job_key:
            return
        if self._remote_index_thread is not None and self._remote_index_thread.is_alive():
            self._cancel_remote_index_build(wait=True, timeout=0.5)
            if self._remote_index_thread is not None and self._remote_index_thread.is_alive():
                self._queue_lifecycle_callback(
                    lambda f=filename, p=path: self._start_remote_gcode_index_build_from_file(f, p),
                    100,
                )
                return
        generation = self._remote_index_generation
        lifecycle_generation = self._lifecycle_generation
        job_serial = job_key[2] if job_key is not None else 0
        cancel_event = threading.Event()
        self._remote_index_cancel_event = cancel_event
        self._remote_index_build_filename = filename
        self._remote_index_build_job_key = job_key

        def worker() -> None:
            try:
                # Read in bounded chunks so cancellation/shutdown does not have to
                # wait for one uninterruptible whole-file read on very large jobs.
                data_buffer = bytearray()
                with open(path, "rb") as handle:
                    while True:
                        if cancel_event.is_set():
                            return
                        chunk = handle.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        data_buffer.extend(chunk)
                if cancel_event.is_set():
                    return
                ranges, motions, layer_map = self._build_remote_gcode_index(
                    data_buffer, cancel_event
                )
            except Exception as error:
                Logger.logException(
                    "w",
                    "Moonraker Print Follower failed to index cached G-code %s: %s",
                    filename,
                    error,
                )
                ranges, motions, layer_map = [], [], {}
            if not self._destroyed:
                self._remoteIndexReady.emit(
                    generation,
                    filename,
                    ranges,
                    motions,
                    layer_map,
                    lifecycle_generation,
                    job_serial,
                )

        thread = threading.Thread(
            target=worker,
            name="MoonrakerPrintFollowerIndex",
            daemon=True,
        )
        self._remote_index_thread = thread
        thread.start()

    @pyqtSlot(int, str, object, object, object, int, int)
    def _on_remote_index_ready(
        self,
        generation: int,
        filename: str,
        ranges,
        motions,
        layer_map,
        lifecycle_generation: int,
        job_serial: int,
    ) -> None:
        if generation != self._remote_index_generation:
            return
        if lifecycle_generation != self._lifecycle_generation:
            return
        if filename != self._last_remote_filename:
            return
        if self._remote_job_key is not None and job_serial != self._remote_job_key[2]:
            return

        self._remote_index_build_filename = None
        self._remote_index_build_job_key = None
        self._remote_index_cancel_event = None
        self._remote_index_thread = None
        if ranges:
            self._remote_layer_ranges = ranges
            self._remote_motion_offsets = motions
            self._remote_current_layer_map = dict(layer_map or {})
            self._remote_index_filename = filename
            self._remote_index_job_key = self._remote_job_key
            Logger.log(
                "i",
                "Moonraker Print Follower indexed %d layers from remote G-code %s",
                len(ranges),
                filename,
            )
            if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                self._queue_lifecycle_callback(lambda: self._poll(force=True))
        else:
            Logger.log(
                "w",
                "Moonraker Print Follower found no layer markers in remote G-code %s",
                filename,
            )

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

        self._file_reply = None
        self._file_reply_filename = None
        self._file_reply_job_key = None

        if not filename:
            reply.deleteLater()
            return
        if reply_generation != self._lifecycle_generation:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        if reply_job_key != self._remote_job_key:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                Logger.log(
                    "w",
                    "Moonraker Print Follower could not download %s: %s",
                    filename,
                    reply.errorString(),
                )
                if self._force_load_pending_filename == filename:
                    self._force_load_requested = False
                    self._force_load_pending_filename = None
                    self._set_status(
                        f"Could not download current print from Moonraker: {reply.errorString()}"
                    )
                return

            data = bytes(reply.readAll())
            self._cache_remote_gcode(filename, data, reply_job_key)

            # Start Cura's own asynchronous G-code loader as soon as the bytes are
            # cached. While Cura is parsing, deliberately do NOT start our own
            # Python indexer: both are CPU-heavy Python loops and would contend
            # for the GIL.
            forced_load = self._force_load_pending_filename == filename
            if forced_load:
                self._load_cached_remote_gcode_forced(filename)
            elif (
                self._pref_bool(self.PREF_PATH_FOLLOW)
                and self._cura_has_toolpath()
                and not self._cura_load_in_progress
            ):
                self._start_remote_gcode_index_build(filename, data)
        except Exception as error:
            Logger.logException(
                "w",
                "Moonraker Print Follower failed to process remote G-code %s: %s",
                filename,
                error,
            )
        finally:
            reply.deleteLater()

    def _cache_remote_gcode(
        self,
        filename: str,
        data: bytes,
        job_key: Optional[Tuple[str, int, int]] = None,
    ) -> None:
        """Persist one downloaded Moonraker file for Cura's async G-code reader."""

        try:
            # A session may see many jobs with the same filename. Keep only the
            # current reusable cache instead of accumulating one directory per run.
            if self._cached_gcode_path:
                self._discard_cached_gcode()
            job_dir = tempfile.mkdtemp(prefix="job-", dir=self._temp_gcode_dir.name)
            base_name = os.path.basename(filename.replace("\\", "/")) or "moonraker.gcode"
            extension = os.path.splitext(base_name)[1].lower()
            if extension not in (".g", ".gcode"):
                base_name += ".gcode"
            path = os.path.join(job_dir, base_name)
            with open(path, "wb") as handle:
                handle.write(data)

            self._cached_gcode_filename = filename
            self._cached_gcode_path = path
            self._cached_gcode_job_key = job_key if job_key is not None else self._remote_job_key
            Logger.log(
                "i",
                "Moonraker Print Follower cached remote G-code %s at %s",
                filename,
                path,
            )
        except Exception as error:
            Logger.logException(
                "w",
                "Moonraker Print Follower could not cache remote G-code %s: %s",
                filename,
                error,
            )
            self._cached_gcode_filename = None
            self._cached_gcode_path = None
            self._cached_gcode_job_key = None

    def _on_cura_file_completed(self, file_name: str) -> None:
        """Finish an explicit remote load without altering Cura's Prepare stage."""

        is_remote_file = False
        load_path = self._cura_load_path
        if load_path:
            try:
                is_remote_file = (
                    os.path.abspath(str(file_name)) == os.path.abspath(load_path)
                )
            except Exception:
                is_remote_file = False

        if not is_remote_file:
            if self._cura_load_in_progress:
                self._invalidate_lifecycle("another Cura file completed during remote load")
            self._scene_structure_signature = self._get_scene_structure_signature()
            self._scene_settle_until = time.monotonic() + 0.25
            self._refresh_simulation_view_connection()
            self._sync_preview_button_state()
            if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                self._queue_lifecycle_callback(lambda: self._poll(force=True), 100)
            return

        filename = self._cura_load_filename or self._cached_gcode_filename
        load_job_key = self._cura_load_job_key
        was_forced = bool(
            self._force_load_requested
            and filename
            and self._force_load_pending_filename == filename
        )

        load_seconds = None
        if self._cura_load_started_at is not None:
            load_seconds = max(0.0, time.perf_counter() - self._cura_load_started_at)
        self._cura_load_in_progress = False
        self._cura_load_started_at = None
        self._cura_load_path = None
        self._cura_load_filename = None
        self._cura_load_job_key = None
        self._cleanup_deferred_cache_dirs()
        self._force_load_requested = False
        self._force_load_pending_filename = None
        self._preview_switched_for_job = True  # Cura's G-code loader switches to Preview itself.
        self._scene_structure_signature = self._get_scene_structure_signature()
        self._scene_settle_until = time.monotonic() + 0.25
        self._refresh_simulation_view_connection()

        # Layer data is populated asynchronously after readLocalFile(). Activity
        # changes normally update the UI, but this immediate refresh is harmless.
        self._sync_preview_button_state()

        if filename:
            Logger.log(
                "i",
                "Moonraker Print Follower loaded active remote G-code into Cura Preview: %s",
                filename,
            )
            if was_forced:
                suffix = f" in {load_seconds:.1f}s" if load_seconds is not None else ""
                self._set_status(f"{filename}: loaded current print into Cura Preview{suffix}")
                Logger.log(
                    "i",
                    "Moonraker Print Follower Cura G-code parse completed%s",
                    suffix,
                )
                # Build the motion index only after Cura has finished parsing, so
                # following work cannot slow the import itself.
                if (
                    self._pref_bool(self.PREF_PATH_FOLLOW)
                    and load_job_key == self._remote_job_key
                ):
                    self._queue_lifecycle_callback(lambda f=filename: self._ensure_remote_gcode_index(f))

                # The fileCompleted signal can precede SimulationView activity on
                # large G-code files. Poll now and again shortly afterwards; the
                # activityChanged hook will also force a catch-up when layers land.
                if self._pref_bool(self.PREF_ENABLED) and not self._following_paused:
                    self._queue_lifecycle_callback(lambda: self._poll(force=True))
                    self._queue_lifecycle_callback(lambda: self._poll(force=True), 250)

    @staticmethod
    def _build_remote_gcode_index(
        data: bytes, cancel_event: Optional[threading.Event] = None
    ) -> Tuple[List[Tuple[int, int]], List[array], Dict[int, int]]:
        """Return per-layer byte ranges and motion-command byte offsets.

        Motion matching remains compatible with v0.9.8. In addition to the
        byte ranges, the index records the literal SET_PRINT_STATS_INFO
        CURRENT_LAYER values so Moonraker layer numbering can be mapped to Cura
        without relying on a manually configured 0/1-based assumption.
        """

        layer_comment = re.compile(rb"^\s*;LAYER:\s*-?\d+\s*$", re.IGNORECASE)
        stats_marker = re.compile(
            rb"^\s*SET_PRINT_STATS_INFO\b.*\bCURRENT_LAYER\s*=\s*(-?\d+)",
            re.IGNORECASE,
        )
        motion = re.compile(rb"^\s*(?:N\d+\s+)?G(?:0|1|2|3)(?:\s|$)", re.IGNORECASE)
        elapsed = re.compile(rb"^\s*;TIME_ELAPSED:", re.IGNORECASE)
        stats_values: List[int] = []

        def parse(marker, collect_stats: bool = False) -> Tuple[List[Tuple[int, int]], List[array]]:
            blocks: List[Dict[str, Any]] = []
            current: Optional[Dict[str, Any]] = None
            offset = 0

            line_number = 0
            data_length = len(data)
            while offset < data_length:
                if (
                    cancel_event is not None
                    and (line_number & 0x7FF) == 0
                    and cancel_event.is_set()
                ):
                    return [], []

                newline = data.find(b"\n", offset)
                line_end = data_length if newline < 0 else newline + 1
                line = data[offset:line_end]
                stripped = line.rstrip(b"\r\n")
                if collect_stats:
                    stats_match = stats_marker.search(stripped)
                    if stats_match is not None:
                        try:
                            value = int(stats_match.group(1))
                            if not stats_values or stats_values[-1] != value:
                                stats_values.append(value)
                        except (TypeError, ValueError):
                            pass
                if marker.search(stripped):
                    if current is not None and current["end"] is None:
                        current["end"] = offset
                    current = {"start": offset, "end": None, "motions": array("Q")}
                    blocks.append(current)
                elif current is not None:
                    if current["end"] is None and elapsed.search(stripped):
                        current["end"] = offset
                    if current["end"] is None and motion.search(stripped):
                        current["motions"].append(offset)
                offset = line_end
                line_number += 1

            if cancel_event is not None and cancel_event.is_set():
                return [], []

            if current is not None and current["end"] is None:
                current["end"] = len(data)

            ranges: List[Tuple[int, int]] = []
            motions: List[array] = []
            for block in blocks:
                start = int(block["start"])
                end = int(block["end"] if block["end"] is not None else len(data))
                ranges.append((start, max(start + 1, end)))
                motions.append(block["motions"])
            return ranges, motions

        ranges, motions = parse(layer_comment, collect_stats=True)
        if not ranges and not (cancel_event is not None and cancel_event.is_set()):
            ranges, motions = parse(stats_marker)

        if cancel_event is not None and cancel_event.is_set():
            return [], [], {}

        # SET_PRINT_STATS_INFO is the authoritative source of Moonraker's
        # current_layer value. When the file contains one CURRENT_LAYER marker
        # per indexed layer, map those literal values straight to Cura's
        # zero-based layer indices. This supports both 0-based and 1-based
        # macros (and any other constant starting value) without guessing.
        layer_map: Dict[int, int] = {}
        if ranges and len(stats_values) == len(ranges):
            for index, value in enumerate(stats_values):
                if value in layer_map:
                    layer_map = {}
                    break
                layer_map[value] = index
        elif ranges and len(stats_values) >= 2:
            # Some files omit a marker at one end. If the observed values form
            # a clean +1 sequence, the first value still defines the numbering
            # base well enough to map values that fall inside the indexed range.
            if all(stats_values[i] == stats_values[0] + i for i in range(len(stats_values))):
                base = stats_values[0]
                for index in range(len(ranges)):
                    layer_map[base + index] = index

        return ranges, motions, layer_map

    def _simulation_view(self):
        return self._refresh_simulation_view_connection()

    def _maybe_switch_to_preview(self) -> None:
        if self._preview_switched_for_job or not self._pref_bool(self.PREF_AUTO_PREVIEW):
            return
        try:
            self._controller.setActiveStage("PreviewStage")
            self._preview_switched_for_job = True
        except Exception as error:
            # Following can still work without forcing the Preview stage.
            Logger.log("w", "Moonraker Print Follower could not activate Preview: %s", error)

    def _layer_from_z(self, view, gcode_move: Dict[str, Any]) -> Optional[int]:
        """Best-effort fallback when print_stats.info.current_layer is absent.

        Z-hop can briefly place the nozzle at a different height.  To avoid
        chasing those moves, a candidate layer is accepted only on a poll where
        the reported G-code extruder position has advanced.
        """

        position = gcode_move.get("gcode_position")
        if not isinstance(position, (list, tuple)) or len(position) < 4:
            return None

        try:
            z = float(position[2])
            e = float(position[3])
        except (TypeError, ValueError):
            return None

        previous_e = self._last_extruder_position
        self._last_extruder_position = e
        if previous_e is None or e <= previous_e + 0.0001:
            return None

        try:
            max_layer = int(view.getMaxLayers())
        except Exception:
            return None
        if max_layer < 0:
            return None

        # SimulationView builds this cache when activated.  Calculate it on
        # demand if Cura has sliced data but the user has not opened Preview yet.
        calculate_cache = getattr(view, "_calculateLayerHeightsCache", None)
        if callable(calculate_cache):
            try:
                calculate_cache()
            except Exception:
                pass

        get_height = getattr(view, "_getLayerHeight", None)
        if not callable(get_height):
            return None

        tolerance = max(0.001, self._pref_float(self.PREF_Z_TOLERANCE, 0.04))
        best: Optional[Tuple[float, int]] = None
        for layer in range(max_layer + 1):
            try:
                height = float(get_height(layer))
            except Exception:
                continue
            if height <= 0:
                continue
            delta = abs(height - z)
            if delta <= tolerance and (best is None or delta < best[0]):
                best = (delta, layer)

        return best[1] if best is not None else None

    def _shutdown(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._timer.stop()
        self._manual_view_watch_timer.stop()
        self._invalidate_lifecycle("plugin shutdown")
        self._cancel_remote_index_build(wait=True, timeout=2.0)
        self._disconnect_simulation_view_connection()

        try:
            if self._scene is not None:
                scene_changed = getattr(self._scene, "sceneChanged", None)
                if scene_changed is not None:
                    scene_changed.disconnect(self._on_scene_changed)
        except Exception:
            pass

        try:
            file_completed = getattr(self._application, "fileCompleted", None)
            if file_completed is not None:
                file_completed.disconnect(self._on_cura_file_completed)
        except Exception:
            pass
        try:
            main_window_changed = getattr(self._application, "mainWindowChanged", None)
            if main_window_changed is not None:
                main_window_changed.disconnect(self._on_main_window_changed)
        except Exception:
            pass
        try:
            active_view_changed = getattr(self._controller, "activeViewChanged", None)
            if active_view_changed is not None:
                active_view_changed.disconnect(self._on_active_view_changed)
        except Exception:
            pass
        try:
            active_stage_changed = getattr(self._controller, "activeStageChanged", None)
            if active_stage_changed is not None:
                active_stage_changed.disconnect(self._sync_preview_controls_visibility)
        except Exception:
            pass

        backend = getattr(self, "_backend", None)
        if backend is not None:
            for signal_name, handler in (
                ("slicingStarted", self._on_slicing_started),
                ("slicingCancelled", self._on_slicing_finished),
                ("printDurationMessage", self._on_slicing_finished),
            ):
                try:
                    signal = getattr(backend, signal_name, None)
                    if signal is not None:
                        signal.disconnect(handler)
                except Exception:
                    pass

        overlay = self._preview_overlay
        self._preview_overlay = None
        if overlay is not None:
            try:
                overlay.loadClicked.disconnect(self._confirm_force_load_current_print)
            except Exception:
                pass
            try:
                overlay.setProperty("visible", False)
                overlay.deleteLater()
            except Exception:
                pass

        controls = self._action_panel_controls
        self._action_panel_controls = None
        if controls is not None:
            self._remove_additional_component_reference(controls)
            try:
                controls.loadClicked.disconnect(self._confirm_force_load_current_print)
            except Exception:
                pass
            try:
                controls.pauseClicked.disconnect(self._toggle_following_pause)
            except Exception:
                pass
            try:
                controls.setProperty("visible", False)
                controls.deleteLater()
            except Exception:
                pass

        self._cura_load_in_progress = False
        self._cura_load_path = None
        self._cura_load_filename = None
        self._cura_load_job_key = None
        self._cleanup_deferred_cache_dirs()
        try:
            self._temp_gcode_dir.cleanup()
        except Exception:
            pass

    def deinitialize(self) -> None:
        """Stop timers, workers and Qt references before Cura unloads the plugin."""
        self._shutdown()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
        if self._status_label is not None:
            self._status_label.setText(text)

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
        value = self._preferences.getValue(key)
        return "" if value is None else str(value)

    def _pref_bool(self, key: str) -> bool:
        value = self._preferences.getValue(key)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _pref_int(self, key: str, default: int) -> int:
        try:
            return int(self._preferences.getValue(key))
        except (TypeError, ValueError):
            return default

    def _pref_float(self, key: str, default: float) -> float:
        try:
            return float(self._preferences.getValue(key))
        except (TypeError, ValueError):
            return default
