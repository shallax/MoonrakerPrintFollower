"""One Cura Qt model: declarations and composition, not an inheritance stack."""
from __future__ import annotations
import json
import logging
import os
import re
from collections.abc import Mapping
from copy import deepcopy
from PyQt6.QtCore import QLocale, QTimer, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Resources import Resources
from PyQt6.QtGui import QDesktopServices
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from .ConsoleController import ConsoleController


def _british_spelling() -> bool:
    """British spellings for Commonwealth-English locales: the
    ruling is that the plugin is British, but the handful of variant
    strings follow the USER's locale — QLocale decides en_GB vs en_US.
    Bare "en" and other languages get the American spellings Cura's own
    strings use."""
    try:
        for language in QLocale.system().uiLanguages():
            normalised = str(language).replace("-", "_").lower()
            if normalised.split("_")[0] == "en":
                return normalised not in {"en", "en_us"}
    except Exception:
        pass
    return False


from .MonitorCamera import MonitorCamera
from .MonitorCommands import MonitorCommands
from .MonitorControls import MonitorControls
from .MonitorData import MonitorData
from .MonitorPermissions import REASON_DETAIL, R_PAUSED_NOTE, R_UNKNOWN, Verdict, can_jog, can_pause, can_restart, can_resume, can_start_print, jog_caption, section_reason
from .PrintStartOwner import PrintStartOwner
from datetime import datetime

from .FileManager import FileManager
from .FileManagerPolicy import (
    delete_candidates,
    is_gcode_name,
    name_collides,
    normalise_columns,
    path_collides,
    rename_path,
    rename_target,
    upload_relpath,
)
from .MonitorFormatting import (
    core_values,
    endstop_values,
    file_disk_text,
    file_row_payload,
    file_timestamp,
    peripheral_values,
)
from dataclasses import replace

from .PrinterConfig import normalise_temperature_chart
from .StateStore import StateStore
from .MonitorTemperatureHistory import TemperatureHistory, chart_payload
import time
from .MonitorTuning import MonitorTuning
from .ToolheadController import ToolheadController
from .ToolheadPolicy import EXTRUDE_DISTANCE_DEFAULT, EXTRUDE_SPEED_DEFAULT, JOG_DISTANCE_DEFAULT
from .WhatsNew import entries as whats_new_entries, latest_version as whats_new_latest, should_show as whats_new_should_show


# The monitor's panel state lives in a plugin-owned JSON file next to
# cura.cfg. Uranium's preference store is not used: it drops reads and
# writes on unregistered keys depending on version, and only persists on
# Cura's own save cycle, so the state has proven unreliable there. The
# file name predates the extra fields and stays for continuity.
SECTIONS_FILE_NAME = "moonraker_print_follower_sections.json"

# The console pane's user-set height, in screen-scaled pixels; 0 means
# "never dragged" and renders at the pane's own default size. The model
# only guards the obvious hazards — a negative or absurd value from a
# hand-edited file, or a stray drag value. The PANE bounds are the QML's
# clamp: they depend on the live stage layout, which the model cannot see.
CONSOLE_HEIGHT_MAX = 2000


def _sections_path() -> str:
    return Resources.getStoragePath(Resources.Preferences, SECTIONS_FILE_NAME)


def _state_bool(value) -> bool:
    """Coerce a stored flag; string values from hand-edited or older files
    must not hydrate inverted (bool('false') is True)."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


def _state_height(value) -> int:
    """Coerce a stored console height. Junk in a hand-edited file
    degrades to the unset default (0) rather than raising, and a
    NEGATIVE height can never hydrate: the clamp is the model's own
    floor, whatever the stored value claims."""
    try:
        return max(0, min(CONSOLE_HEIGHT_MAX, int(float(value))))
    except (TypeError, ValueError, OverflowError):
        return 0


# The shared chart-config normaliser (PrinterConfig owns the per-printer
# record; the legacy global JSON still feeds it during migration).
_chart_state = normalise_temperature_chart


def _read_state(store=None) -> dict:
    """The persisted panel state: collapsed sections, the control-pane
    collapse, the lock-all toggle and the console's dragged height. The
    first shipped format was a flat section map, which is migrated to the
    current shape on read. The FILE semantics live in the StateStore
    (4.2.0, F11); the coercion below is the model's own (its tests pin
    the fallback document)."""
    decoded = (store or StateStore(_sections_path())).read()
    if isinstance(decoded, dict):
        sections = decoded.get("sections")
        if not isinstance(sections, dict):
            sections = decoded  # legacy flat section map
        return {
            "sections": {str(key): _state_bool(value) for key, value in sections.items()},
            "whatsNewSeen": str(decoded.get("whatsNewSeen") or ""),
            "controlsCollapsed": _state_bool(decoded.get("controlsCollapsed", False)),
            "controlsLocked": _state_bool(decoded.get("controlsLocked", False)),
            "infoCollapsed": _state_bool(decoded.get("infoCollapsed", False)),
            "statusCollapsed": _state_bool(decoded.get("statusCollapsed", False)),
            "consoleHeight": _state_height(decoded.get("consoleHeight", 0)),
            "fileManagerColumns": normalise_columns(decoded.get("fileManagerColumns")),
            "temperatureChart": _chart_state(decoded.get("temperatureChart")),
            "toolhead": _toolhead_state(decoded.get("toolhead")),
        }
    return {"sections": {}, "whatsNewSeen": "", "controlsCollapsed": False, "controlsLocked": False,
            "infoCollapsed": False, "statusCollapsed": False, "consoleHeight": 0,
            "fileManagerColumns": normalise_columns({}),
            "temperatureChart": _chart_state({}),
            "toolhead": _toolhead_state(None)}


def _toolhead_state(stored) -> dict:
    """The jog/extrude selection persists (the live report:
    the chosen options were not saved). Values are floats; anything
    unparsable falls back to the policy defaults."""
    stored = stored if isinstance(stored, dict) else {}
    def number(key, default):
        try:
            value = float(stored.get(key, default))
        except (TypeError, ValueError):
            value = float(default)
        return value if value > 0 else float(default)
    return {
        "jogDistance": number("jogDistance", JOG_DISTANCE_DEFAULT),
        "extrudeDistance": number("extrudeDistance", EXTRUDE_DISTANCE_DEFAULT),
        "extrudeSpeed": number("extrudeSpeed", EXTRUDE_SPEED_DEFAULT),
    }


def _write_state(state: dict) -> None:
    # The legacy module-level name (tests pin it): a transient store
    # — note the MERGE semantics (4.2.0): foreign keys in the file
    # survive, unlike the old fixed-document replace this name once
    # performed.
    StateStore(_sections_path()).write(state)


def value_property(kind, name, signal, default=None):
    """Declarative Qt binding, not a domain-state forwarding mechanism.

    The published values are treated as immutable once `_publish` has
    stored them (every publish replaces the whole dict), so reads return
    the stored object instead of deep-copying per read.
    """
    def read(self):
        value = self._values.get(name, default)
        cached = self._qv_cache.get(name)
        if cached is None or cached[0] is not value:
            # One QVariant conversion per VALUE REBUILD, not per read:
            # at the mature 1800-sample payload a conversion costs
            # ~6.75 ms (measured in the pinned container), and the
            # chart is read by several bindings per aux feed. The
            # stored value's identity is stable across publishes by
            # design (payloads rebuild only on real changes), so the
            # cache hits for every unchanged publish.
            cached = (value, QVariant(value) if kind is QVariant else value)
            self._qv_cache[name] = cached
        return cached[1]
    return pyqtProperty(kind, read, notify=signal)


class MoonrakerMonitorModel(PrinterOutputModel):
    monitorChanged = pyqtSignal()
    previewBlockChanged = pyqtSignal(dict)
    webcamsChanged = pyqtSignal()
    temperatureChartChanged = pyqtSignal()
    temperatureChartLegendChanged = pyqtSignal()
    consoleChanged = pyqtSignal()
    cameraTransformChanged = pyqtSignal()
    peripheralsChanged = pyqtSignal()
    excludeObjectsChanged = pyqtSignal()
    powerDevicesChanged = pyqtSignal()
    systemChanged = pyqtSignal()
    endstopsChanged = pyqtSignal()
    showProbePointsChanged = pyqtSignal()
    actionChanged = pyqtSignal()
    controlsChanged = pyqtSignal()
    emergencyStopChanged = pyqtSignal()
    typedControlsChanged = pyqtSignal()
    toolheadChanged = pyqtSignal()
    # The policy-fed restart gate and its reason (4.2.0).
    restartChanged = pyqtSignal()
    sectionsChanged = pyqtSignal()
    controlsLockChanged = pyqtSignal()
    infoPaneChanged = pyqtSignal()
    statusPaneChanged = pyqtSignal()
    consoleHeightChanged = pyqtSignal()
    cameraRefreshChanged = pyqtSignal()
    cameraRecoveringChanged = pyqtSignal()
    connectionDetailChanged = pyqtSignal()
    fileManagerChanged = pyqtSignal()
    # Fired when the once-per-version overlay should show (the
    # startup check or an explicit reopen); the plugin's window
    # owner listens and creates/shows the QML overlay.
    whatsNewRequested = pyqtSignal()
    fileManagerThumbsChanged = pyqtSignal()

    _SIGNAL_KEYS = (
        ("monitorChanged", ("monitorState", "monitorConnected", "monitorFilename", "monitorProgress", "monitorLayer", "monitorLayerProgress",
                            "improvingEta", "improveEtaProgress", "improveEtaPhase", "monitorElapsed",
                            "monitorEta", "monitorEtaBasis", "monitorFinish", "monitorSpeed", "monitorFlow",
                            "monitorPosition", "monitorVelocity", "monitorFlowRate", "monitorFlowDiameter",
                            "monitorAccelLimit", "monitorMessage", "monitorLayerSource", "filamentUsed", "filamentRemaining",
                            "sectionReason", "sectionReasonDetail")),
        ("webcamsChanged", ("webcamNames", "activeWebcamIndex")),
        ("temperatureChartChanged", ("temperatureChart",)),
        ("temperatureChartLegendChanged", ("temperatureChartLegend",)),
        ("cameraTransformChanged", ("cameraName", "cameraRotation", "cameraFlipHorizontal", "cameraFlipVertical")),
        ("peripheralsChanged", ("temperatureItems", "fanItems", "filamentSensorItems")),
        ("excludeObjectsChanged", ("excludeObjectItems",)),
        ("powerDevicesChanged", ("powerDevices",)),
        ("systemChanged", ("klippyState", "moonrakerVersion", "klipperVersion", "hostLoad", "memoryAvailable",
                           "cpuTemperature", "mcuSummary", "mcuItems")),
        ("endstopsChanged", ("endstopItems", "endstopSummary")),
        ("actionChanged", ("printActive", "canPausePrint", "canResumePrint", "pauseReason", "pauseReasonDetail", "resumeReason", "resumeReasonDetail", "canCancelPrint", "actionBusy",
                           "actionStatus", "emergencyHoldProgress")),
        ("controlsChanged", ("monitorLayerHeight", "macroNames", "hasQuadGantryLevel", "hasBedMesh", "canRunSetup",
                             "temperaturePresetNames", "canApplyTemperaturePreset", "speedFactorPercent", "flowFactorPercent",
                             "zOffset", "zOffsetText", "fanControlItems", "ledItems", "saveConfigPending", "saveConfigSummary",
                             "canSaveConfig")),
        ("emergencyStopChanged", ("emergencyStopClicks",)),
        ("toolheadChanged", ("jogEnabled", "jogDistance", "extrudeDistance", "extrudeSpeed",
                             "homedAxes", "positionMode", "jogStatus", "jogReason", "jogReasonDetail")),
        ("restartChanged", ("canRestart", "restartReason", "restartReasonDetail")),
        ("controlsLockChanged", ("controlsLocked", "controlsCollapsed")),
        ("infoPaneChanged", ("infoCollapsed",)),
        ("statusPaneChanged", ("statusCollapsed",)),
        ("consoleHeightChanged", ("consoleHeight",)),
        ("sectionsChanged", ("sectionExpandedMap",)),
        ("showProbePointsChanged", ("showProbePoints",)),
        ("cameraRefreshChanged", ("cameraRefreshNonce",)),
        ("cameraRecoveringChanged", ("cameraRecovering",)),
        ("connectionDetailChanged", ("connectionDetail",)),
        ("fileManagerChanged", ("fileManagerRows", "fileManagerRecents", "fileManagerDirectory", "fileManagerDirectories", "fileManagerDiskText", "fileManagerNote",
                                "fileManagerRefreshedAt", "fileManagerShown", "fileManagerPage", "fileManagerPageIndex",
                                "fileManagerPageCount", "fileManagerPageSize", "fileManagerPageSelection",
                                "fileManagerEmptyKind", "fileManagerSelected", "fileManagerSortColumn",
                                "fileManagerSortAscending", "fileManagerSearch", "fileManagerOpen", "fileManagerFilters",
                                "filePrintConfirm", "fileDeleteConfirm", "fileRenameTarget",
                                "fileRenameConflict", "fileUploadConfirm", "fileUploadProgress",
                                "fileManagerColumnWidths", "fileManagerColumnOrder", "fileManagerColumnHidden",
                                "fileManagerFilterCounts", "fileManagerFilterOptions", "fileManagerHistoryLoaded",
                                "fileManagerHistoryExhausted", "fileManagerWalkError")),
        # Thumbnails publish ALONE (the live report: each
        # scroll-triggered fetch reply rebuilt the whole payload).
        ("fileManagerThumbsChanged", ("fileManagerThumbs",)),
        ("consoleChanged", ("consoleHistory", "consoleLines", "consoleDropped", "consoleRevisions", "consolePending", "consoleErrorBell")),
        ("typedControlsChanged", ("temperaturePresetItems", "pwmOutputItems", "bedMeshAvailable", "bedMeshProfile",
                                  "bedMeshProfileNames", "bedMeshRows", "bedMeshColumns", "bedMeshValues", "bedMeshMinimum",
                                  "bedMeshMaximum", "bedMeshRange", "bedMeshXMin", "bedMeshXMax", "bedMeshYMin", "bedMeshYMax",
                                  "bedMeshRangeText", "bedMeshPreviewVisible", "bedMeshThresholdLow", "bedMeshThresholdHigh",
                                  "bedMeshMachineWidth", "bedMeshMachineDepth",
                                  "bedMeshCenterIsZero")),
    )

    def __init__(self, output_controller, number_of_extruders, *, client, print_state, config, apply_config, bed_mesh,
                 request_load=None, request_monitor_download=None, request_file_download=None,
                 download_failed=None, preferences_flushed=None, identity=None, state_store=None):
        super().__init__(output_controller, number_of_extruders)
        self._client, self._print_state, self._config, self._apply_config, self._mesh = \
            client, print_state, config, apply_config, bed_mesh
        self._identity = identity
        # The state file's owner (4.2.0, F11/A6): passed in as a
        # capability — 4.3.0's UI-state store consumes the same
        # instance; the default builds the production path.
        self._store = state_store or StateStore(_sections_path(), note=self._on_store_note)
        # Failure notes that fired before the console existed (the
        # hydration read runs first) queue here and flush once the
        # console lands.
        self._store_notes = []
        # The file-manager Download capability: the follower owns the
        # one-shot stream + load-into-Cura (the same lane discipline
        # as the improve-ETA pull). Download failures (stream errors,
        # stale completions, unconfirmed loads) land in the popup's
        # note line through the same channel as file refusals.
        self._request_file_download = request_file_download
        if download_failed is not None:
            download_failed.connect(self._on_file_manager_note)
        self._file_print_confirm = None
        self._file_delete_confirm = None
        self._file_rename_target = None
        self._file_rename_conflict = False
        self._file_upload_confirm = None
        self._file_upload_progress = None
        # The "improve ETA" action reuses the facade's load-current-print
        # flow (download + index), passed in as an explicit capability.
        self._request_load = request_load
        # The monitor-only variant: download + index WITHOUT the preview
        # render (the optimisation); the preview's own load
        # finds the file already local and skips the re-download.
        self._request_monitor_download = request_monitor_download
        self._show_probe_points = bool(getattr(self._config(), "show_probe_points", False))
        self._qv_cache = {}
        self._improving_eta = False
        self._skip_clear_once = False
        self._values = {}
        state = _read_state(self._store)
        self._whats_new_seen = state["whatsNewSeen"]
        self._controls_locked = state["controlsLocked"]
        self._controls_collapsed = state["controlsCollapsed"]
        self._info_collapsed = state["infoCollapsed"]
        self._status_collapsed = state["statusCollapsed"]
        # The console's dragged height (0 = never dragged): a pane SIZE,
        # not printer data, so it lives in the global chrome file beside
        # the pane collapses and rehydrates before the pane exists.
        self._console_height = state["consoleHeight"]
        # The file-manager's own column config rehydrates when the
        # service exists (the state file is the model's to READ; the
        # values are the service's to OWN — the ruling that
        # the file manager is its own thing).
        self._file_columns_state = state["fileManagerColumns"]
        self._camera_refresh_nonce = 0
        self._sections = state["sections"]
        self._toolhead_state = state["toolhead"]
        # The chart config is per-printer (sensor names differ between
        # machines): it lives in the PrinterConfig record, adopting the
        # legacy global JSON block once on first upgrade.
        # The legacy global block migrates only into the FIRST printer
        # record that is empty; a second printer configured before the
        # upgrade starts fresh. One-time migration, by design.
        per_printer = normalise_temperature_chart(getattr(self._config(), "temperature_chart", {}))
        if per_printer:
            self._chart_config = per_printer
        elif state.get("temperatureChart"):
            self._chart_config = state["temperatureChart"]
            self._apply_chart_config()
            self._save_state(replace=True)  # rewrite the global file chrome-only
        else:
            self._chart_config = {}
        self._history = TemperatureHistory()
        self._chart_payload = None  # rebuilt only when the history or config changes
        self._chart_payload_revision = -1
        self._chart_config_key = None
        self._legend_payload = None
        self._data = MonitorData(client, self)
        # The hydrated lock reaches the policy record (the phase-6
        # security re-review, D7): a session that starts locked must
        # read locked, not wait for the padlock to be cycled.
        self._data.set_controls_locked(self._controls_locked)
        self._commands = MonitorCommands(self._data, self)
        self._tuning = MonitorTuning(self._data, self._commands, self)
        self._controls = MonitorControls(self._data, self._commands, self._tuning, bed_mesh, config, self)
        self._camera = MonitorCamera(self._data, config, apply_config, self)
        # The webcam watchdog: a dead bridge relay bumps the refresh
        # nonce (a URL change is the ONLY thing that restarts Cura's
        # loader) and veils the camera until the stream restarts.
        self._camera_last_refresh_at = 0.0
        self._camera_last_url = ""
        self._camera_recovering = False
        self._camera.streamFailed.connect(self._on_stream_failed)
        self._camera.streamRecovered.connect(self._on_stream_recovered)
        # The wake recovery (the author's live report): a stream that
        # survives a suspend shows a FROZEN frame — the image's size
        # is already set, so the render watchdog cannot see it. A
        # wake transition reloads the camera source once, the same
        # way the refresh button does.
        try:
            from PyQt6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            if app is not None:
                self._camera_app_state = app.applicationState()
                app.applicationStateChanged.connect(self._on_app_state_changed)
            else:
                self._camera_app_state = None
        except Exception:
            self._camera_app_state = None
        # The machine geometry for the bed-mesh map (4.2.0): 0 means
        # unknown and the map draws the mesh-bounds view.
        self._machine_width = 0.0
        self._machine_depth = 0.0
        self._machine_center_is_zero = False
        self._mesh_threshold_low = None
        self._mesh_threshold_high = None
        self._mesh_thresholds_touched = False
        self._toolhead = ToolheadController(self._data, self._commands, self)
        # The persisted jog/extrude selection (the live
        # report) — applied before any publish so the first frame
        # already shows the saved options.
        self._toolhead.set_distance(self._toolhead_state["jogDistance"])
        self._toolhead.set_extrude_distance(self._toolhead_state["extrudeDistance"])
        self._toolhead.set_extrude_speed(self._toolhead_state["extrudeSpeed"])
        self._console = ConsoleController(self._data, self._commands, config, apply_config, identity, self)
        # The hydration-time store notes flush now that the console
        # exists (a read failure before this point would otherwise
        # stay silent — the exact class F11 exists to kill).
        for text in self._store_notes:
            self._console.note(text)
        self._store_notes = []
        # The toolhead's clamp rejections land in the console as
        # local notes (a live request).
        self._toolhead.rejectedNote.connect(self._console.note)
        self._console_error_bell = False
        self._console_errors_seen = 0
        self._file_manager = FileManager(client, self)
        self._file_manager.set_column_state(self._file_columns_state)
        # Metascan outcomes land in the console as local notes (the
        # author's live report: the option appeared to do nothing).
        self._file_manager_note = ""
        # The print-start operation's owner (4.3.0): the armed state,
        # the watchdog and the failure verdict moved out of the model
        # into a capabilities-only owner — one owned operation across
        # every start path.
        self._print_start = PrintStartOwner(
            file_manager=self._file_manager,
            console=self._console,
            commands=self._commands,
        )
        self._file_manager.note.connect(self._on_file_manager_note)
        # Upload progress and outcome feed the popup (the
        # live request).
        self._file_manager.uploadProgress.connect(self._on_upload_progress)
        self._file_manager.uploadFinished.connect(self._on_upload_finished)
        # The light publish: thumb transitions never rebuild the rows.
        self._thumbs_dirty = False
        self._file_manager.thumbsChanged.connect(self._publish_thumbs)
        self._file_manager_open = False
        if preferences_flushed is not None:
            # The console marks its sent lines SAVED when the preference
            # file actually flushes (the colour ruling).
            preferences_flushed.connect(self._console.mark_saved)
        for signal in (self._data.changed, self._commands.changed, self._controls.changed, self._camera.changed,
                       self._toolhead.changed, self._console.changed, self._file_manager.changed, bed_mesh.changed):
            signal.connect(self._publish)
        # The history feeds once per auxiliary reply, not per publish
        # (per-publish feeding duplicated samples and halved the window);
        # a session invalidation restarts the window so the previous
        # printer's curves never bleed into the next one.
        self._data.auxiliaryChanged.connect(self._on_auxiliary)
        # The Preview value block rides the aux clock: the data's
        # emission forwards straight through to the output-device
        # edge (the seam's carrier, 4.3.0).
        self._data.previewBlockChanged.connect(self.previewBlockChanged)
        self._data.consoleStoreChanged.connect(self._on_console_store)
        self._data.invalidated.connect(self._on_invalidated)
        # The store's failure latch is per SESSION (A6): a new
        # session may report its own persistence failure.
        self._data.invalidated.connect(self._store.reset_failures)
        # The attach-time reload can run before the active machine's
        # identity resolves; retry it on every poll heartbeat so the
        # restored transcript lands the moment the config is readable
        # (the "commands never rehydrate" report).
        self._data.changed.connect(self._console.reload_if_empty)
        # The author's ruling (2026-09-10): after a reconnect the
        # camera stream restarts — the nonce bump reloads the stream
        # on every connection transition into connected (the
        # e-stop's automatic cycle included).
        self._data.connectionStateChanged.connect(self._on_connection_state)
        self._data.set_active(True)
        self._publish()

    def _on_file_manager_note(self, text: str) -> None:
        # The note feeds BOTH the console and the popup's own status
        # line (refusals must be visible where the action happened).
        self._console.note(text)
        self._file_manager_note = str(text)
        self._publish()

    def _on_connection_state(self, state: str) -> None:
        if state != "yes":
            return
        self._camera_refresh_nonce += 1
        self._publish()

    def _on_stream_failed(self) -> None:
        import time
        now = time.monotonic()
        # The FIRST failure retries immediately: a camera's first
        # fetch can die on a cold-start hiccup (DNS or first contact)
        # while the very next request sails — the live
        # report: leaving and re-entering the Monitor tab, a fresh
        # request, started the stream. Once a retry cycle is running,
        # the 10 s cadence keeps a dead stream from spinning the
        # loader in a tight loop.
        if not self._camera_recovering or now - self._camera_last_refresh_at >= 10.0:
            self._camera_last_refresh_at = now
            self._camera_refresh_nonce += 1
        self._camera_recovering = True
        self._publish()

    def setMachineGeometry(self, width, depth, center_is_zero) -> None:
        """The physical bed dimensions from the machine stack (4.2.0,
        the author's request): the expanded bed-mesh map draws the
        probed bounds within the real bed, extends the boundary
        values to the bed edges and outlines the exact Klipper mesh
        bounds — the Preview overlay's honest visualisation."""
        try:
            width, depth = float(width), float(depth)
        except (TypeError, ValueError):
            return
        self._machine_width = width if width > 0 else 0.0
        self._machine_depth = depth if depth > 0 else 0.0
        self._machine_center_is_zero = bool(center_is_zero)
        self._publish()

    def _on_app_state_changed(self, state) -> None:
        from PyQt6.QtCore import Qt
        previous = self._camera_app_state
        self._camera_app_state = state
        if state == Qt.ApplicationState.ApplicationActive and previous not in (None, Qt.ApplicationState.ApplicationActive):
            # Woke up: reload the camera source once. No veil — the
            # stream may come back instantly, and a stuck veil would
            # read as a failure the user must recover.
            self._camera_refresh_nonce += 1
            self._publish()

    @pyqtSlot()
    def cameraRenderStalled(self) -> None:
        # The render watchdog (the author's live report): a stream
        # that CONNECTED but never painted a frame raises no error
        # signal — the QML pane watches the image's frame size and
        # reports a stall here. The recovery is the same as a stream
        # failure: the nonce bump reloads the source, cadence-limited
        # by the same 10 s gate.
        self._on_stream_failed()

    def _on_stream_recovered(self) -> None:
        if not self._camera_recovering:
            return
        self._camera_recovering = False
        self._publish()

    def _on_console_store(self):
        # Klipper's output arrives from the gcode-store poll; the
        # controller merges it into the transcript feed.
        self._console.append_responses(self._data.console_entries)

    @pyqtSlot(bool)
    def setConsoleExpanded(self, expanded):
        # The console polls the store only while on screen (the
        # ruling); the pane's visibility drives this flag, seeded with
        # the persisted last-seen stamp so the backfill skips the
        # server's stale buffer.
        stored = float(getattr(self._config(), "console_store_time", 0.0) or 0.0)
        if expanded:
            # The console constructed before the active machine existed
            # and read an empty record; by attach time the identity is
            # real, so re-load the transcript if it never did (the
            # author's "completely empty at app start" report).
            self._console.reload_if_empty()
        self._data.set_console_expanded(expanded, stored)

    def _on_auxiliary(self):
        self._history.observe(self._data.snapshot.auxiliary, time.monotonic(), time.time())
        self._publish()

    def _on_invalidated(self):
        self._history.reset()
        # A printer switch must not ring for the previous machine's
        # error lines (the bell's marker counts per-session).
        self._console_errors_seen = 0
        self._console_error_bell = False
        # The file manager's own lifecycle: deactivation cancels its
        # lane, aborts its fetches and clears the previous machine's
        # rows and thumbnails (round-2 A15). The hourglass ends
        # outright: a printer switch makes any in-flight improve
        # moot, whatever the snapshot says.
        self._file_manager.unbind()
        self._improving_eta = False
        self._publish()

    def setMonitoringActive(self, active): self._data.set_active(active)

    def _file_manager_values(self):
        fm = self._file_manager
        now = time.time()
        if not self._file_manager_open:
            # The popup is closed: the grid's bindings are inert, and
            # rebuilding the ROW payloads per poll is pure waste —
            # closing a 400-file listing stalled for seconds (the
            # author's live report). The cheap view state still
            # publishes (the view-mutation contract), only the heavy
            # rows/recents/thumbs/option-scan is skipped. Reopening
            # refills everything below.
            return {
                "fileManagerRows": [],
                "fileManagerRecents": [],
                "fileManagerDirectory": fm.directory,
                "fileManagerDirectories": fm.subdirectories(),
                "fileManagerDiskText": file_disk_text(fm.disk_usage),
                "fileManagerRefreshedAt": f"Last refreshed at {datetime.fromtimestamp(fm.refreshed_at).strftime('%H:%M')}" if fm.refreshed_at else "Not yet refreshed",
                "fileManagerShown": "",
                "fileManagerPage": f"Page {fm.page_index()} of {fm.page_number()}" if fm.view.page_size != "all" else "",
                "fileManagerPageIndex": fm.page_index(),
                "fileManagerPageCount": fm.page_number(),
                "fileManagerPageSize": str(fm.view.page_size),
                "fileManagerPageSelection": "none",
                "fileManagerEmptyKind": "",
                "fileManagerSelected": len(fm.selection),
                "fileManagerSortColumn": fm.view.sort_column,
                "fileManagerSortAscending": fm.view.sort_ascending,
                "fileManagerSearch": fm.view.search,
                "fileManagerFilters": {key: (list(value) if isinstance(value, (list, tuple)) else [value]) for key, value in fm.view.filters.items() if value},
                "fileManagerFilterCounts": {key: len(value) if isinstance(value, (list, tuple)) else 1 for key, value in fm.view.filters.items() if value},
                "fileManagerFilterOptions": {},
                "fileManagerHistoryLoaded": fm.history_loaded,
                "fileManagerHistoryExhausted": fm.history_exhausted,
                "fileManagerWalkError": fm.walk_error or "",
            }
        printing_relpath = self._printing_relpath()
        selection = fm.selection
        rows = []
        for row in fm.page_rows():
            payload = file_row_payload(row, now)
            payload["checked"] = row.relpath in selection
            payload["printing"] = row.relpath == printing_relpath
            rows.append(payload)
        total = fm.total_count()
        size = fm.view.page_size
        if size == "all":
            shown = f"Showing 1–{total} of {total}" if total else "Showing 0 of 0"
        else:
            start = (fm.page_index() - 1) * size + 1 if total else 0
            end = min(start + size - 1, total) if total else 0
            shown = f"Showing {start}–{end} of {total}"
        return {
            "fileManagerRows": rows,
            "fileManagerRecents": [{
                "name": item["filename"],
                "time": file_timestamp(item.get("end_time"), now),
                "relpath": item.get("relpath", ""),
                "thumb": bool(item.get("thumb")),
            } for item in fm.recents()],
            "fileManagerDirectory": fm.directory,
            "fileManagerDirectories": fm.subdirectories(),
            "fileManagerDiskText": file_disk_text(fm.disk_usage),
            "fileManagerRefreshedAt": f"Last refreshed at {datetime.fromtimestamp(fm.refreshed_at).strftime('%H:%M')}" if fm.refreshed_at else "Not yet refreshed",
            "fileManagerShown": shown,
            "fileManagerPage": f"Page {fm.page_index()} of {fm.page_number()}" if fm.view.page_size != "all" else "",
            "fileManagerPageIndex": fm.page_index(),
            "fileManagerPageCount": fm.page_number(),
            "fileManagerPageSize": str(fm.view.page_size),
            "fileManagerPageSelection": fm.selection_state(fm.page_rows()),
            "fileManagerEmptyKind": fm.empty_state(),
            "fileManagerSelected": len(selection),
            "fileManagerSortColumn": fm.view.sort_column,
            "fileManagerSortAscending": fm.view.sort_ascending,
            "fileManagerSearch": fm.view.search,
            "fileManagerFilters": {key: (list(value) if isinstance(value, (list, tuple)) else [value]) for key, value in fm.view.filters.items() if value},
            "fileManagerFilterCounts": {key: len(value) if isinstance(value, (list, tuple)) else 1 for key, value in fm.view.filters.items() if value},
            "fileManagerFilterOptions": fm.filter_option_counts_cached(now=now),
            "fileManagerHistoryLoaded": fm.history_loaded,
            "fileManagerHistoryExhausted": fm.history_exhausted,
            "fileManagerWalkError": fm.walk_error or "",
        }

    def _printing_relpath(self):
        # The ACTIVE print's root-exclusive relpath (round-2 D4), or
        # "" — the state filter is the load-bearing part: Klipper
        # never clears print_stats.filename on completion, so a
        # filename alone would keep the last-printed file badged
        # "printing" with Delete/Rename disabled forever.
        state = self._data.snapshot.core.get("print_stats") or {}
        if str(state.get("state") or "") not in ("printing", "paused"):
            return ""
        return str(state.get("filename") or "")

    def _publish_thumbs(self) -> None:
        """The thumbnail-only publish, COALESCED: landings arrive in
        bursts and each publish forces a QML repaint wave, so a short
        timer batches a burst into one flush (never the rows payload)."""
        if self._thumbs_dirty:
            return
        self._thumbs_dirty = True
        QTimer.singleShot(100, self._flush_thumbs)

    def _flush_thumbs(self) -> None:
        self._thumbs_dirty = False
        # A change-compare: an open popup re-requests the recents
        # strip every poll, and an unchanged payload must not force
        # a repaint wave once per second.
        previous = self._values.get("fileManagerThumbs")
        payload = self._file_manager.thumbnail_payload()
        if previous == payload:
            return
        self._values["fileManagerThumbs"] = payload
        self.fileManagerThumbsChanged.emit()

    def _publish(self):
        fm = self._file_manager
        previous = self._values
        snapshot = self._print_state()
        if self._improving_eta and not self._skip_clear_once \
                and (snapshot.index_ready or not snapshot.load_active):
            # The index landed, or the download/build failed and the
            # coordinator cleared its flags (panel finding P1-1): the
            # hourglass ends and the glyph becomes the retry
            # affordance — settled BEFORE the values build so the
            # published value reflects the cleared state. The 90 s
            # timer stays as the last resort for a hung pull.
            self._improving_eta = False
        self._skip_clear_once = False
        values = core_values(self._data.snapshot, snapshot, self._client.connected)
        # The M117 message lives on Klipper's display_status object,
        # not print_stats — the Print-job slot reads it from the aux
        # snapshot (the report: M117 showed nowhere).
        display = (self._data.snapshot.auxiliary or {}).get("display_status")
        if isinstance(display, Mapping):
            message = str(display.get("message") or "")
            if message:
                values["monitorMessage"] = message
        values.update(peripheral_values(self._data.snapshot))
        values.update(endstop_values(self._data.snapshot, self._client.connected))
        values.update(self._file_manager_values())
        values["fileManagerOpen"] = self._file_manager_open
        values["filePrintConfirm"] = self._file_print_confirm or ""
        values["fileDeleteConfirm"] = self._file_delete_confirm or ""
        values["fileRenameTarget"] = self._file_rename_target or ""
        values["fileRenameConflict"] = bool(self._file_rename_conflict)
        values["fileUploadConfirm"] = self._file_upload_confirm or ""
        values["fileUploadProgress"] = self._file_upload_progress or ""
        values["fileManagerColumnWidths"] = fm.column_widths()
        values["fileManagerColumnOrder"] = fm.column_order()
        values["fileManagerColumnHidden"] = fm.column_hidden()
        values["fileManagerThumbs"] = self._file_manager.thumbnail_payload() if self._file_manager_open else {}
        values["fileManagerNote"] = self._file_manager_note
        # Thumbnails fetch per the RENDER WINDOW, not the page: the
        # QML's visibleRows change drives the request (the
        # live report: an "all / page" listing fired hundreds of
        # thumbnail requests on open — the window bounds them). The
        # recents strip's own fetch stays here (bounded at 50). The
        # watchdog below runs regardless — a print confirmed before
        # the popup closed still needs its verdict.
        if self._file_manager_open:
            rows = []
            for item in self._file_manager.recents():
                row = self._file_manager.row_for(item.get("relpath") or "") if item.get("relpath") else None
                if row is not None and row.thumb_path:
                    rows.append(row)
            self._file_manager.request_thumbnails(rows)
        # The print-start watchdog runs on the publish tick — the
        # owner supervises the armed attempt regardless of the popup's
        # state (a print confirmed before the popup closed still
        # needs its verdict).
        self._print_start.tick(self._data.snapshot.core)
        # The no-reflow rule's sibling ruling (2026-09-10):
        # while DISCONNECTED every control on the Monitor page disables
        # — the QML gates its sections and the emergency stop on this.
        # The tri-state (4.2.0): unknown folds to False, exactly the
        # bool every existing consumer saw before.
        values["monitorConnected"] = self._data.connection_state == "yes"
        # The policy projections (4.2.0): the caption and the restart
        # gate come from the table — one derivation, both view
        # models. A missing observation fails closed.
        observation = self._data.observation
        jog_verdict = can_jog(observation) if observation is not None else Verdict("disabled", R_UNKNOWN)
        restart_verdict = can_restart(observation) if observation is not None else Verdict("disabled", R_UNKNOWN)
        # The caption is the policy's jog_caption — the reason when
        # disabled, the pause-first warning, AND the paused note
        # (the re-review's blocker: the raw reason blanked the paused
        # state, the one where the row must speak).
        values["jogReason"] = jog_caption(observation) if observation is not None else R_UNKNOWN
        values["jogReasonDetail"] = REASON_DETAIL.get(jog_verdict.reason, "")
        if values["jogReason"] == R_PAUSED_NOTE:
            values["jogReasonDetail"] = REASON_DETAIL.get(R_PAUSED_NOTE, "")
        values["canRestart"] = restart_verdict.mode == "allowed"
        values["restartReason"] = restart_verdict.reason if restart_verdict.mode != "allowed" else ""
        values["restartReasonDetail"] = REASON_DETAIL.get(restart_verdict.reason, "")
        section = section_reason(observation) if observation is not None else R_UNKNOWN
        values["sectionReason"] = section
        values["sectionReasonDetail"] = REASON_DETAIL.get(section, "")
        values.update(self._controls.values)
        values.update(self._camera.values)
        values.update(self._toolhead.values)
        values.update(self._console.values)
        # The console error bell (a live request): while
        # the console is collapsed, a NEW error line rings a red bell
        # next to its header until the console expands. Restored
        # lines never ring (they are not new), and the marker counts
        # every error line seen so collapsing later cannot re-ring
        # for old errors.
        lines = values.get("consoleLines") or ()
        error_count = sum(1 for entry in lines if entry.get("error") and not entry.get("restored"))
        if self._sections.get("console") is False:
            if error_count > self._console_errors_seen:
                self._console_error_bell = True
        else:
            self._console_error_bell = False
        self._console_errors_seen = error_count
        values["consoleErrorBell"] = self._console_error_bell
        commands, mesh = self._commands, self._mesh.snapshot
        # The pause/resume rows (4.3.0): the last un-migrated command
        # gate becomes policy projections — one derivation, the
        # reasons ride the strip's middle slot and the Dashboard's
        # tooltips.
        pause_verdict = can_pause(observation)
        resume_verdict = can_resume(observation)
        # The heightmap range filter (the author's request): ONE
        # window drives both surfaces — the Monitor pop-over reads the
        # published keys, the Preview card and scene node follow
        # through the presenter. The window follows the mesh range
        # until the user touches a handle; a touched window is clamped
        # into whatever range the next mesh brings.
        mesh_min = float(mesh.get("minimum") or 0)
        mesh_max = float(mesh.get("maximum") or 0)
        if not mesh or mesh_max <= mesh_min:
            threshold_low = threshold_high = 0.0
        elif self._mesh_thresholds_touched:
            threshold_low = min(max(self._mesh_threshold_low, mesh_min), mesh_max)
            threshold_high = min(max(self._mesh_threshold_high, mesh_min), mesh_max)
            if threshold_low > threshold_high:
                threshold_low, threshold_high = threshold_high, threshold_low
        else:
            threshold_low, threshold_high = mesh_min, mesh_max
        values.update(printActive=commands.print_active,
            canPausePrint=pause_verdict.mode == "allowed",
            canResumePrint=resume_verdict.mode == "allowed",
            pauseReason=pause_verdict.reason,
            pauseReasonDetail=REASON_DETAIL.get(pause_verdict.reason, ""),
            resumeReason=resume_verdict.reason,
            resumeReasonDetail=REASON_DETAIL.get(resume_verdict.reason, ""),
            canCancelPrint=commands.print_active and not commands.busy, actionBusy=commands.busy,
            actionStatus=commands.status, emergencyStopClicks=commands.clicks,
            emergencyHoldProgress=commands.hold_progress, powerDevices=self._controls.power_devices(),
            bedMeshAvailable=bool(mesh), bedMeshProfile=str(mesh.get("profile") or "Current mesh") if mesh else "",
            bedMeshRows=int(mesh.get("rows") or 0), bedMeshColumns=int(mesh.get("columns") or 0),
            bedMeshValues=list(mesh.get("values") or ()), bedMeshMinimum=mesh_min,
            bedMeshMaximum=mesh_max, bedMeshRange=float(mesh.get("range") or 0),
            bedMeshXMin=float(mesh.get("xMin") or 0), bedMeshXMax=float(mesh.get("xMax") or 0),
            bedMeshYMin=float(mesh.get("yMin") or 0), bedMeshYMax=float(mesh.get("yMax") or 0),
            bedMeshRangeText=f"{float(mesh.get('range') or 0):.3f} mm range" if mesh else "",
            bedMeshPreviewVisible=self._mesh.visible,
            bedMeshThresholdLow=threshold_low,
            bedMeshThresholdHigh=threshold_high,
            bedMeshMachineWidth=self._machine_width,
            bedMeshMachineDepth=self._machine_depth,
            bedMeshCenterIsZero=self._machine_center_is_zero,
            controlsLocked=self._controls_locked, controlsCollapsed=self._controls_collapsed,
            infoCollapsed=self._info_collapsed, statusCollapsed=self._status_collapsed,
            consoleHeight=self._console_height,
            cameraRefreshNonce=self._camera_refresh_nonce,
            cameraRecovering=self._camera_recovering,
            connectionDetail=self._data.connection_detail,
            sectionExpandedMap=dict(self._sections),
            temperatureChart=self._chart_value(),
            temperatureChartLegend=self._legend_value(),
            showProbePoints=self._show_probe_points,
            britishSpelling=_british_spelling(),
            improvingEta=(snapshot.load_active or self._improving_eta) and not snapshot.index_ready,
            improveEtaProgress=(max(0.0, min(1.0, snapshot.download_fraction))
                                if (snapshot.load_active or self._improving_eta) and snapshot.download_fraction is not None else -1.0),
            improveEtaPhase=("Downloading…" if (snapshot.load_active or self._improving_eta) and snapshot.download_fraction is not None
                             else "Indexing…" if (snapshot.load_active or self._improving_eta) and snapshot.indexing
                             else "Resolving…" if snapshot.load_active or self._improving_eta else ""))
        self._values = values
        try:
            url = self._camera.url
            if url and url != self._camera_last_url:
                # Any camera-URL transition deserves a fresh load: the
                # first attach's initial request dies silently in the
                # loader (the report — the manual refresh
                # worked because it changed the URL).
                self._camera_last_url = url
                self._camera_refresh_nonce += 1
            self.setCameraUrl(QUrl(url))
        except AttributeError: pass

        # Qt notify signals are part of control ownership. Broadcasting every
        # signal for every poll was re-evaluating bound ComboBox/Slider values
        # while the user was interacting with them, and QVariant-list updates
        # could also rebuild Repeater delegates mid-drag. Only notify the group
        # whose published values actually changed.
        for signal_name, keys in self._SIGNAL_KEYS:
            if any(previous.get(key) != values.get(key) for key in keys):
                getattr(self, signal_name).emit()

    monitorState = value_property(str, "monitorState", monitorChanged, "Not connected")
    monitorConnected = value_property(bool, "monitorConnected", monitorChanged, False)
    monitorFilename = value_property(str, "monitorFilename", monitorChanged, "")
    monitorProgress = value_property(float, "monitorProgress", monitorChanged, 0.0)
    monitorLayer = value_property(str, "monitorLayer", monitorChanged, "—")
    monitorLayerProgress = value_property(float, "monitorLayerProgress", monitorChanged, -1.0)
    monitorLayerSource = value_property(str, "monitorLayerSource", monitorChanged, "")
    filamentUsed = value_property(str, "filamentUsed", monitorChanged, "—")
    filamentRemaining = value_property(str, "filamentRemaining", monitorChanged, "—")
    britishSpelling = value_property(bool, "britishSpelling", monitorChanged, False)
    improvingEta = value_property(bool, "improvingEta", monitorChanged, False)
    improveEtaProgress = value_property(float, "improveEtaProgress", monitorChanged, -1.0)
    improveEtaPhase = value_property(str, "improveEtaPhase", monitorChanged, "")
    monitorElapsed = value_property(str, "monitorElapsed", monitorChanged, "00:00:00")
    monitorEta = value_property(str, "monitorEta", monitorChanged, "—")
    monitorEtaBasis = value_property(str, "monitorEtaBasis", monitorChanged, "")
    monitorFinish = value_property(str, "monitorFinish", monitorChanged, "—")
    monitorSpeed = value_property(str, "monitorSpeed", monitorChanged, "100%")
    monitorFlow = value_property(str, "monitorFlow", monitorChanged, "100%")
    monitorPosition = value_property(str, "monitorPosition", monitorChanged, "—")
    # The motion rows (4.2.0): the defaults read "—" until the first
    # snapshot lands — an idle CONNECTED printer reads 0 (Klipper
    # always reports the motion fields once the object exists).
    monitorVelocity = value_property(str, "monitorVelocity", monitorChanged, "—")
    monitorFlowRate = value_property(str, "monitorFlowRate", monitorChanged, "—")
    monitorFlowDiameter = value_property(str, "monitorFlowDiameter", monitorChanged, "—")
    monitorAccelLimit = value_property(str, "monitorAccelLimit", monitorChanged, "—")
    monitorMessage = value_property(str, "monitorMessage", monitorChanged, "")
    printActive = value_property(bool, "printActive", actionChanged, False)
    canPausePrint = value_property(bool, "canPausePrint", actionChanged, False)
    canResumePrint = value_property(bool, "canResumePrint", actionChanged, False)
    pauseReason = value_property(str, "pauseReason", actionChanged, "")
    pauseReasonDetail = value_property(str, "pauseReasonDetail", actionChanged, "")
    resumeReason = value_property(str, "resumeReason", actionChanged, "")
    resumeReasonDetail = value_property(str, "resumeReasonDetail", actionChanged, "")
    canCancelPrint = value_property(bool, "canCancelPrint", actionChanged, False)
    actionBusy = value_property(bool, "actionBusy", actionChanged, False)
    actionStatus = value_property(str, "actionStatus", actionChanged, "")
    temperatureItems = value_property(QVariant, "temperatureItems", peripheralsChanged, [])
    fanItems = value_property(QVariant, "fanItems", peripheralsChanged, [])
    filamentSensorItems = value_property(QVariant, "filamentSensorItems", peripheralsChanged, [])
    excludeObjectItems = value_property(QVariant, "excludeObjectItems", excludeObjectsChanged, [])
    powerDevices = value_property(QVariant, "powerDevices", powerDevicesChanged, [])
    klippyState = value_property(str, "klippyState", systemChanged, "Unknown")
    moonrakerVersion = value_property(str, "moonrakerVersion", systemChanged, "—")
    klipperVersion = value_property(str, "klipperVersion", systemChanged, "—")
    hostLoad = value_property(str, "hostLoad", systemChanged, "—")
    memoryAvailable = value_property(str, "memoryAvailable", systemChanged, "—")
    cpuTemperature = value_property(str, "cpuTemperature", systemChanged, "—")
    mcuSummary = value_property(str, "mcuSummary", systemChanged, "—")
    mcuItems = value_property(QVariant, "mcuItems", systemChanged, [])
    webcamNames = value_property(QVariant, "webcamNames", webcamsChanged, [])
    activeWebcamIndex = value_property(int, "activeWebcamIndex", webcamsChanged, -1)
    temperatureChart = value_property(QVariant, "temperatureChart", temperatureChartChanged, {})
    temperatureChartLegend = value_property(QVariant, "temperatureChartLegend", temperatureChartLegendChanged, {})
    endstopItems = value_property(QVariant, "endstopItems", endstopsChanged, [])
    endstopSummary = value_property(str, "endstopSummary", endstopsChanged, "")
    showProbePoints = value_property(bool, "showProbePoints", showProbePointsChanged, False)
    consoleHistory = value_property(QVariant, "consoleHistory", consoleChanged, [])
    consoleLines = value_property(QVariant, "consoleLines", consoleChanged, [])
    # Lines the session ring rotated out of its head (0 until the
    # transcript passes its cap); the pane uses it to stay aligned.
    consoleDropped = value_property(int, "consoleDropped", consoleChanged, 0)
    consoleRevisions = value_property(int, "consoleRevisions", consoleChanged, 0)
    consolePending = value_property(int, "consolePending", consoleChanged, 0)
    consoleErrorBell = value_property(bool, "consoleErrorBell", consoleChanged, False)
    cameraName = value_property(str, "cameraName", cameraTransformChanged, "")
    cameraRotation = value_property(int, "cameraRotation", cameraTransformChanged, 0)
    cameraFlipHorizontal = value_property(bool, "cameraFlipHorizontal", cameraTransformChanged, False)
    cameraFlipVertical = value_property(bool, "cameraFlipVertical", cameraTransformChanged, False)
    monitorLayerHeight = value_property(str, "monitorLayerHeight", controlsChanged, "—")
    macroNames = value_property(QVariant, "macroNames", controlsChanged, [])
    hasQuadGantryLevel = value_property(bool, "hasQuadGantryLevel", controlsChanged, False)
    hasBedMesh = value_property(bool, "hasBedMesh", controlsChanged, False)
    canRunSetup = value_property(bool, "canRunSetup", controlsChanged, False)
    temperaturePresetNames = value_property(QVariant, "temperaturePresetNames", controlsChanged, [])
    temperaturePresetItems = value_property(QVariant, "temperaturePresetItems", typedControlsChanged, [])
    canApplyTemperaturePreset = value_property(bool, "canApplyTemperaturePreset", controlsChanged, False)
    speedFactorPercent = value_property(int, "speedFactorPercent", controlsChanged, 100)
    flowFactorPercent = value_property(int, "flowFactorPercent", controlsChanged, 100)
    zOffset = value_property(float, "zOffset", controlsChanged, 0.0)
    zOffsetText = value_property(str, "zOffsetText", controlsChanged, "0.000 mm")
    fanControlItems = value_property(QVariant, "fanControlItems", controlsChanged, [])
    ledItems = value_property(QVariant, "ledItems", controlsChanged, [])
    pwmOutputItems = value_property(QVariant, "pwmOutputItems", typedControlsChanged, [])
    saveConfigPending = value_property(bool, "saveConfigPending", controlsChanged, False)
    saveConfigSummary = value_property(str, "saveConfigSummary", controlsChanged, "")
    canSaveConfig = value_property(bool, "canSaveConfig", controlsChanged, False)
    emergencyStopClicks = value_property(int, "emergencyStopClicks", emergencyStopChanged, 0)
    # The file-manager surface (Snapshot 1): the published page slice,
    # recents, breadcrumb, disk and view metadata — everything the
    # pinned FileManager.qml renders comes from these.
    fileManagerRows = value_property(QVariant, "fileManagerRows", fileManagerChanged, [])
    fileManagerRecents = value_property(QVariant, "fileManagerRecents", fileManagerChanged, [])
    fileManagerDirectory = value_property(QVariant, "fileManagerDirectory", fileManagerChanged, [])
    fileManagerDirectories = value_property(QVariant, "fileManagerDirectories", fileManagerChanged, [])
    fileManagerDiskText = value_property(str, "fileManagerDiskText", fileManagerChanged, "—")
    fileManagerRefreshedAt = value_property(str, "fileManagerRefreshedAt", fileManagerChanged, "Not yet refreshed")
    fileManagerShown = value_property(str, "fileManagerShown", fileManagerChanged, "Showing 0 of 0")
    fileManagerPage = value_property(str, "fileManagerPage", fileManagerChanged, "")
    fileManagerPageIndex = value_property(int, "fileManagerPageIndex", fileManagerChanged, 1)
    fileManagerPageCount = value_property(int, "fileManagerPageCount", fileManagerChanged, 1)
    fileManagerPageSize = value_property(str, "fileManagerPageSize", fileManagerChanged, "25")
    fileManagerPageSelection = value_property(str, "fileManagerPageSelection", fileManagerChanged, "none")
    fileManagerEmptyKind = value_property(str, "fileManagerEmptyKind", fileManagerChanged, "")
    fileManagerSelected = value_property(int, "fileManagerSelected", fileManagerChanged, 0)
    fileManagerSortColumn = value_property(str, "fileManagerSortColumn", fileManagerChanged, "modified")
    fileManagerSortAscending = value_property(bool, "fileManagerSortAscending", fileManagerChanged, False)
    fileManagerSearch = value_property(str, "fileManagerSearch", fileManagerChanged, "")
    fileManagerOpen = value_property(bool, "fileManagerOpen", fileManagerChanged, False)
    filePrintConfirm = value_property(QVariant, "filePrintConfirm", fileManagerChanged, "")
    fileDeleteConfirm = value_property(QVariant, "fileDeleteConfirm", fileManagerChanged, "")
    fileRenameTarget = value_property(QVariant, "fileRenameTarget", fileManagerChanged, "")
    fileRenameConflict = value_property(bool, "fileRenameConflict", fileManagerChanged, False)
    fileUploadConfirm = value_property(QVariant, "fileUploadConfirm", fileManagerChanged, "")
    fileUploadProgress = value_property(QVariant, "fileUploadProgress", fileManagerChanged, "")
    fileManagerColumnWidths = value_property(QVariant, "fileManagerColumnWidths", fileManagerChanged, {})
    fileManagerColumnOrder = value_property(QVariant, "fileManagerColumnOrder", fileManagerChanged, [])
    fileManagerColumnHidden = value_property(QVariant, "fileManagerColumnHidden", fileManagerChanged, [])
    fileManagerThumbs = value_property(QVariant, "fileManagerThumbs", fileManagerThumbsChanged, {})
    fileManagerFilters = value_property(QVariant, "fileManagerFilters", fileManagerChanged, {})
    fileManagerFilterCounts = value_property(QVariant, "fileManagerFilterCounts", fileManagerChanged, {})
    fileManagerFilterOptions = value_property(QVariant, "fileManagerFilterOptions", fileManagerChanged, {})
    fileManagerHistoryLoaded = value_property(int, "fileManagerHistoryLoaded", fileManagerChanged, 0)
    fileManagerHistoryExhausted = value_property(bool, "fileManagerHistoryExhausted", fileManagerChanged, False)
    fileManagerWalkError = value_property(str, "fileManagerWalkError", fileManagerChanged, "")
    fileManagerNote = value_property(str, "fileManagerNote", fileManagerChanged, "")

    def _printer_name(self):
        try:
            _machine_id, name = self._identity()
            return str(name or "the printer")
        except Exception:
            return "the printer"

    def _file_print_confirm_payload(self, relpath):
        from .MonitorFormatting import file_duration_short, file_filament
        key = f"gcodes/{relpath}"
        row = {f"gcodes/{r.relpath}": r for r in self._file_manager.resident_rows()}.get(key)
        if row is None:
            return None
        klippy = self._data.snapshot.server.get("klippy_state")
        ready = str(klippy or "").lower() == "ready"
        homed = "xyz" == str((self._data.snapshot.auxiliary.get("toolhead") or {}).get("homed_axes") or "").lower()
        if ready and not homed:
            ready_text = "The printer is not homed — the print may not start."
        elif ready:
            ready_text = "Printer ready."
        else:
            ready_text = f"Printer state: {str(klippy or 'unknown').capitalize()}."
        return {
            "relpath": row.relpath,
            "name": row.filename,
            "est": file_duration_short(row.estimated_time),
            "filament": file_filament(row.filament),
            "printerName": self._printer_name(),
            "ready": ready and homed,
            "homed": homed,
            "readyText": ready_text,
        }

    @pyqtSlot(str)
    def fileRequestPrint(self, relpath):
        payload = self._file_print_confirm_payload(str(relpath))
        if payload is None:
            return
        # The confirmation shows a LARGE thumbnail: make sure THIS
        # row's large variant is fetched even when it was opened from
        # Recents — the page-driven cache covers visible rows' small
        # variants only.
        row = self._file_manager.row_for(payload["relpath"])
        if row is not None and row.thumb_path:
            self._file_manager.request_thumbnails([row], large=True)
        self._file_print_confirm = payload
        self._publish()

    @pyqtSlot()
    def fileConfirmPrint(self):
        confirm = self._file_print_confirm
        self._file_print_confirm = None
        if confirm:
            # The dispatch gate (4.2.0, S3/N2): the dialog's click was
            # checked when it OPENED; the dispatch re-checks the
            # CURRENT observation — a print started by another client
            # in the window must refuse here, not at Moonraker.
            observation = getattr(self._data, "observation", None)
            verdict = can_start_print(observation) if observation is not None \
                else Verdict("disabled", R_UNKNOWN)
            if verdict.mode != "allowed":
                self._commands.report_status(f"Print start refused: {verdict.reason}")
                self._publish()
                return
            self._file_manager.start_print(confirm["relpath"])
            # The state the snapshot held at confirm time: the
            # matched branch below holds while it stays unchanged, so
            # a stale terminal state from the SAME file's previous
            # job can never wipe the fresh attempt (the adversarial
            # round's repro).
            stats = self._data.snapshot.core.get("print_stats") or {}
            self._print_start.arm(str(stats.get("state") or ""))
            # The print is on its way: the file manager steps aside
            # NOW and the monitor view returns — the verdict (success
            # or failure) reports to the console and the note line,
            # never by waiting inside the popup.
            self.setFileManagerOpen(False)
        self._publish()

    @pyqtSlot()
    def fileCancelPrint(self):
        self._file_print_confirm = None
        self._publish()

    @pyqtSlot(str)
    def fileDownload(self, relpath):
        if self._request_file_download is not None:
            self._request_file_download(str(relpath))

    @pyqtSlot(bool)
    def setFileManagerOpen(self, is_open):
        self._file_manager_open = bool(is_open)
        if not self._file_manager_open:
            self._file_print_confirm = ""
            self._file_delete_confirm = ""
            self._file_rename_target = ""
            self._file_upload_confirm = ""
            self._file_upload_progress = ""
        self._publish()

    @pyqtSlot()
    def reconnect(self):
        """The manual Reconnect (a live request): cycle
        the client and re-arm the monitor — the recovery for a UI
        stuck after a printer error or a dropped connection."""
        self._commands.report_status("Reconnecting…")
        self._data.reconnect()
        self._publish()

    @pyqtSlot()
    def fileClearWalkError(self):
        self._file_manager.clear_walk_error()
        self._publish()

    @pyqtSlot()
    def openFileManager(self):
        # The open trigger: the flag gates the heavy payload work
        # (the live report: the closed popup must not keep
        # paying the per-poll cost).
        self._file_manager_open = True
        self._file_manager_note = ""
        try:
            self._file_manager.bind()
            self._file_manager.open()
        except Exception:
            # A dying slot freezes the whole popup silently (Qt
            # swallows the traceback) — surface it in the walk-error
            # state instead of leaving "Loading files…" forever.
            logging.getLogger(__name__).exception("file manager open failed")
            self._file_manager.changed.emit()

    @pyqtSlot()
    def refreshFileManager(self):
        try:
            self._file_manager.open()
        except Exception:
            logging.getLogger(__name__).exception("file manager refresh failed")
            self._file_manager.changed.emit()

    @pyqtSlot("QVariantList", bool)
    def fileNavigateTo(self, segments, isUp):
        if isUp:
            self._file_manager.navigate_up()
        else:
            self._file_manager.navigate_to([str(segment) for segment in segments])

    @pyqtSlot(str)
    def setFileSearch(self, query):
        self._file_manager.view.change_search(str(query))
        self._file_manager.changed.emit()

    @pyqtSlot(str)
    def setFileSort(self, column):
        self._file_manager.view.change_sort(str(column))
        self._file_manager.changed.emit()

    @pyqtSlot(str)
    def setFilePageSize(self, size):
        self._file_manager.view.change_page_size("all" if size == "all" else int(size))
        self._file_manager.changed.emit()

    @pyqtSlot(int)
    def setFilePage(self, page):
        self._file_manager.view.page = max(1, int(page))
        self._file_manager.changed.emit()

    @pyqtSlot(str, list)
    def setFileFilter(self, category, values):
        category = str(category)
        values = list(values)
        filters = dict(self._file_manager.view.filters)
        if category in ("modified", "print_time"):
            # Single-value categories store a SCALAR (the
            # live report: Print time filtered nothing — the policy
            # does float(["30"]) and the TypeError fallback matched
            # every row; Modified's window lookup failed the same
            # way). One value, or None to clear.
            filters[category] = values[0] if values else None
        else:
            filters[category] = values
        self._file_manager.view.change_filters(filters)
        self._file_manager.changed.emit()

    @pyqtSlot()
    def clearFileFilters(self):
        self._file_manager.view.change_filters({})
        self._file_manager.changed.emit()

    @pyqtSlot(str)
    def toggleFileSelection(self, relpath):
        self._file_manager.toggle_selection(str(relpath))

    @pyqtSlot()
    def clearFileSelection(self):
        self._file_manager.clear_selection()

    @pyqtSlot()
    def toggleFilePageSelection(self):
        self._file_manager.toggle_page_selection()

    @pyqtSlot()
    def fileLoadAllHistory(self):
        self._file_manager.load_all_history()

    @pyqtSlot(str)
    def fileScanMetadata(self, relpath):
        self._file_manager.scan_metadata(str(relpath))


    @pyqtSlot()
    def fileRequestDelete(self):
        """The bulk delete from the selection (Snapshot 3): the
        currently-printing file is never offered (the gate
        — the host 403s it anyway, and the client must not ask)."""
        rows = self._file_manager.resident_rows()
        selected = [row for row in rows if row.relpath in self._file_manager.selection]
        candidates = delete_candidates(selected, self._printing_relpath())
        if not candidates:
            return
        self._file_delete_confirm = {
            "kind": "file",
            "relpaths": [row.relpath for row in candidates],
            "count": len(candidates),
            "first": candidates[0].filename,
            "blocked": len(selected) - len(candidates),
        }
        self._publish()

    @pyqtSlot(str)
    def fileRequestDeleteFile(self, relpath):
        row = self._file_manager.row_for(str(relpath))
        if row is None or row.relpath == self._printing_relpath():
            return
        self._file_delete_confirm = {
            "kind": "file",
            "relpaths": [row.relpath], "count": 1, "first": row.filename, "blocked": 0,
        }
        self._publish()

    @pyqtSlot(str)
    def fileCreateDirectory(self, name):
        """The popup's New-folder dialog: the service validates the
        name and owns the outcome notes."""
        self._file_manager.create_directory(name)
        self._publish()

    @pyqtSlot(str)
    def fileRequestDeleteDir(self, path):
        """The folder delete (a live request — right-click
        a breadcrumb segment or a strip chip)."""
        path = str(path).strip("/")
        if not path:
            return
        self._file_delete_confirm = {
            "kind": "dir", "path": path, "name": path.rsplit("/", 1)[-1],
            "relpaths": [], "count": 1, "first": path.rsplit("/", 1)[-1], "blocked": 0,
        }
        self._publish()

    @pyqtSlot()
    def fileConfirmDelete(self):
        confirm = self._file_delete_confirm
        self._file_delete_confirm = None
        if confirm:
            if confirm.get("kind") == "dir":
                self._file_manager.delete_directory(confirm["path"])
            else:
                self._file_manager.delete_files(confirm["relpaths"], self._printing_relpath())
        self._publish()

    @pyqtSlot()
    def fileCancelDelete(self):
        self._file_delete_confirm = None
        self._publish()

    @pyqtSlot(str)
    def fileRequestRename(self, relpath):
        row = self._file_manager.row_for(str(relpath))
        if row is None or row.relpath == self._printing_relpath():
            return
        self._file_rename_target = {"kind": "file", "path": row.relpath, "name": row.filename}
        self._file_rename_conflict = False
        self._publish()

    @pyqtSlot(str)
    def fileRequestRenameDir(self, path):
        """The folder rename (a live request — right-click
        a breadcrumb segment or a strip chip)."""
        path = str(path).strip("/")
        if not path:
            return
        self._file_rename_target = {"kind": "dir", "path": path,
                                    "name": path.rsplit("/", 1)[-1]}
        self._file_rename_conflict = False
        self._publish()

    @pyqtSlot(str)
    def filePreviewRename(self, name):
        """The live collision check while the name is typed (round-1
        C2/C3: the host's move silently overwrites — the dialog asks
        first)."""
        target = self._file_rename_target
        if not target:
            return
        # REPLACE, never mutate (the same publish-contract rule as
        # the upload progress).
        self._file_rename_target = dict(target, name=str(name))
        target = self._file_rename_target
        if target.get("kind") == "dir":
            proposal = rename_path(target["path"], name)
            self._file_rename_conflict = bool(
                proposal is not None
                and path_collides(self._file_manager.resident_directories(), proposal))
        else:
            row = self._file_manager.row_for(target["path"])
            proposal = rename_target(row, name) if row is not None else None
            self._file_rename_conflict = bool(
                proposal is not None and name_collides(self._file_manager.resident_rows(), proposal))
        self._publish()

    @pyqtSlot()
    def fileConfirmRename(self):
        target = self._file_rename_target
        self._file_rename_target = None
        if target:
            if target.get("kind") == "dir":
                self._file_manager.rename_directory(
                    target["path"], target["name"], overwrite=bool(self._file_rename_conflict))
            else:
                self._file_manager.rename_file(
                    target["path"], target["name"], self._printing_relpath(),
                    overwrite=bool(self._file_rename_conflict))
        self._file_rename_conflict = False
        self._publish()

    @pyqtSlot()
    def fileCancelRename(self):
        self._file_rename_target = None
        self._file_rename_conflict = False
        self._publish()

    @pyqtSlot(str)
    def fileUpload(self, path):
        """Snapshot 3 upload (the ruling): LOCAL gcode files
        only — sliced prints already upload from the Preview view.
        A name collision asks first; otherwise the upload runs."""
        # The picker hands over a file:// URL — the service wants a
        # local path (a plain path passes through unchanged).
        path = QUrl(str(path or "")).toLocalFile() or str(path or "")
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if not is_gcode_name(name):
            self._console.note("Upload refused: only gcode files upload here.")
            return
        target = upload_relpath("/".join(self._file_manager.directory), name)
        if target == self._printing_relpath():
            # The host streams the printing file from disk: replacing
            # it mid-print truncates the running job.
            self._console.note(f"Upload refused: {name} is currently printing.")
            self._publish()
            return
        try:
            free = self._file_manager.disk_usage.get("free")
            free = int(free) if free is not None else None
            needed = os.path.getsize(path)
        except (TypeError, ValueError, OSError):
            free, needed = None, 0
        # None means the walk never reported disk usage — the check
        # stands down. A REAL zero (a full disk) still refuses: the
        # old guard treated the two alike and a missing report
        # disabled the check (the adversarial round's catch).
        if free is not None and needed and needed > free:
            self._console.note(f"Upload refused: {name} needs {needed / 1048576:.0f} MB, "
                               f"{free / 1048576:.0f} MB free on the printer.")
            self._publish()
            return
        if name_collides(self._file_manager.resident_rows(), target):
            self._file_upload_confirm = {"path": path, "filename": name}
            self._publish()
            return
        self._start_upload(path, name)
        self._publish()

    def _start_upload(self, path, name, overwrite=False) -> None:
        # The popup's payload opens on the FIRST publish after this;
        # the service's progress and outcome signals drive it from
        # here on (a live request: a bar while it runs and
        # a success/fail verdict at the end).
        self._file_upload_progress = {"name": name, "percent": 0,
                                      "state": "uploading", "error": ""}
        # The overwrite nod MUST ride through: without it the service
        # re-refuses the colliding name and never emits a verdict —
        # the popup hung on "uploading" forever (the live
        # report).
        self._file_manager.upload_file(path, overwrite=overwrite)

    @pyqtSlot()
    def fileConfirmUpload(self):
        confirm = self._file_upload_confirm
        self._file_upload_confirm = None
        if confirm:
            self._start_upload(confirm["path"], confirm["filename"], overwrite=True)
        self._publish()

    @pyqtSlot()
    def fileUploadDismiss(self):
        self._file_upload_progress = None
        self._publish()

    @pyqtSlot(list)
    def fileRequestVisibleThumbnails(self, relpaths):
        """The render window's thumbnails (the live report:
        the page-wide fetch fired hundreds of requests on "all /
        page" — the QML's visible rows bound them)."""
        if not self._file_manager_open:
            return
        rows = []
        for relpath in (relpaths or []):
            row = self._file_manager.row_for(str(relpath))
            if row is not None and row.thumb_path:
                rows.append(row)
        self._file_manager.request_thumbnails(rows)

    @pyqtSlot(str, float)
    def setFileColumnWidth(self, key, width):
        """Snapshot 3's column resize — the STATE lives in the file
        manager (the ruling: the file manager is its own
        thing, composed into the Monitor page)."""
        if self._file_manager.set_column_width(key, width):
            self._save_state()

    @pyqtSlot(list)
    def setFileColumnOrder(self, order):
        if self._file_manager.set_column_order(order):
            self._save_state()

    @pyqtSlot(str, bool)
    def setFileColumnVisible(self, key, visible):
        if self._file_manager.set_column_visible(key, visible):
            self._save_state()

    def _on_upload_progress(self, percent):
        if not self._file_upload_progress:
            return
        percent = int(percent)
        if percent == self._file_upload_progress["percent"]:
            return
        # REPLACE, never mutate: the publish contract treats the
        # stored dict as immutable (the QVariant cache keys on
        # identity), and an in-place edit serves a stale copy.
        self._file_upload_progress = dict(self._file_upload_progress, percent=percent)
        self._publish()

    def _on_upload_finished(self, ok, detail):
        if not self._file_upload_progress:
            return
        if ok:
            self._file_upload_progress = dict(self._file_upload_progress,
                                              state="done", percent=100, error="")
        else:
            self._file_upload_progress = dict(self._file_upload_progress,
                                              state="failed", error=str(detail))
        self._publish()

    @pyqtSlot()
    def fileCancelUpload(self):
        self._file_upload_confirm = None
        self._publish()
    emergencyHoldProgress = value_property(float, "emergencyHoldProgress", actionChanged, 0.0)
    # The machine geometry (4.2.0): the physical bed the mesh map
    # draws the probed bounds within — 0 means unknown, the map
    # falls back to the mesh-bounds view.
    bedMeshMachineWidth = value_property(float, "bedMeshMachineWidth", typedControlsChanged, 0.0)
    bedMeshMachineDepth = value_property(float, "bedMeshMachineDepth", typedControlsChanged, 0.0)
    bedMeshCenterIsZero = value_property(bool, "bedMeshCenterIsZero", typedControlsChanged, False)
    # The heightmap range filter (the author's request): values
    # outside this window render grey.
    bedMeshThresholdLow = value_property(float, "bedMeshThresholdLow", typedControlsChanged, 0.0)
    bedMeshThresholdHigh = value_property(float, "bedMeshThresholdHigh", typedControlsChanged, 0.0)
    bedMeshAvailable = value_property(bool, "bedMeshAvailable", typedControlsChanged, False)
    bedMeshProfile = value_property(str, "bedMeshProfile", typedControlsChanged, "")
    bedMeshProfileNames = value_property(QVariant, "bedMeshProfileNames", typedControlsChanged, [])
    bedMeshRows = value_property(int, "bedMeshRows", typedControlsChanged, 0)
    bedMeshColumns = value_property(int, "bedMeshColumns", typedControlsChanged, 0)
    bedMeshValues = value_property(QVariant, "bedMeshValues", typedControlsChanged, [])
    bedMeshMinimum = value_property(float, "bedMeshMinimum", typedControlsChanged, 0.0)
    bedMeshMaximum = value_property(float, "bedMeshMaximum", typedControlsChanged, 0.0)
    bedMeshRange = value_property(float, "bedMeshRange", typedControlsChanged, 0.0)
    bedMeshXMin = value_property(float, "bedMeshXMin", typedControlsChanged, 0.0)
    bedMeshXMax = value_property(float, "bedMeshXMax", typedControlsChanged, 0.0)
    bedMeshYMin = value_property(float, "bedMeshYMin", typedControlsChanged, 0.0)
    bedMeshYMax = value_property(float, "bedMeshYMax", typedControlsChanged, 0.0)
    bedMeshRangeText = value_property(str, "bedMeshRangeText", typedControlsChanged, "")
    bedMeshPreviewVisible = value_property(bool, "bedMeshPreviewVisible", typedControlsChanged, True)
    jogEnabled = value_property(bool, "jogEnabled", toolheadChanged, False)
    jogDistance = value_property(float, "jogDistance", toolheadChanged, 25.0)
    extrudeDistance = value_property(float, "extrudeDistance", toolheadChanged, 5.0)
    extrudeSpeed = value_property(float, "extrudeSpeed", toolheadChanged, 300.0)
    homedAxes = value_property(str, "homedAxes", toolheadChanged, "")
    positionMode = value_property(str, "positionMode", toolheadChanged, "")
    jogStatus = value_property(str, "jogStatus", toolheadChanged, "")
    # The policy-fed captions and the restart gate (4.2.0): the
    # defaults fail closed until the first observation lands.
    jogReason = value_property(str, "jogReason", toolheadChanged, "")
    jogReasonDetail = value_property(str, "jogReasonDetail", toolheadChanged, "")
    canRestart = value_property(bool, "canRestart", restartChanged, False)
    restartReason = value_property(str, "restartReason", restartChanged, "")
    restartReasonDetail = value_property(str, "restartReasonDetail", restartChanged, "")
    # The shared section-level denial (4.2.0): the states that grey
    # whole panes, one short form every section's Status row reads.
    sectionReason = value_property(str, "sectionReason", monitorChanged, "")
    sectionReasonDetail = value_property(str, "sectionReasonDetail", monitorChanged, "")
    controlsLocked = value_property(bool, "controlsLocked", controlsLockChanged, False)
    controlsCollapsed = value_property(bool, "controlsCollapsed", controlsLockChanged, False)
    infoCollapsed = value_property(bool, "infoCollapsed", infoPaneChanged, False)
    statusCollapsed = value_property(bool, "statusCollapsed", statusPaneChanged, False)
    consoleHeight = value_property(int, "consoleHeight", consoleHeightChanged, 0)
    cameraRefreshNonce = value_property(int, "cameraRefreshNonce", cameraRefreshChanged, 0)
    cameraRecovering = value_property(bool, "cameraRecovering", cameraRecoveringChanged, False)
    connectionDetail = value_property(str, "connectionDetail", connectionDetailChanged, "")
    sectionExpandedMap = value_property(QVariant, "sectionExpandedMap", sectionsChanged, {})

    @pyqtSlot()
    def refreshAll(self): self._data.refresh_all()
    @pyqtSlot()
    def refreshWebcams(self):
        # The nonce feeds a cache-busting query parameter so the live
        # stream itself reloads, not just the webcam list.
        self._camera_refresh_nonce += 1
        self._data.refresh_webcams()
        self._publish()
    @pyqtSlot(bool)
    def setControlsLocked(self, locked):
        self._controls_locked = bool(locked)
        # The policy record's chrome push-in (4.2.0, A3).
        self._data.set_controls_locked(self._controls_locked)
        self._save_state()
        self._publish()
    @pyqtSlot(bool)
    def setControlsCollapsed(self, collapsed):
        self._controls_collapsed = bool(collapsed)
        self._save_state()
        self._publish()
    @pyqtSlot(bool)
    def setInfoCollapsed(self, collapsed):
        self._info_collapsed = bool(collapsed)
        self._save_state()
        self._publish()
    @pyqtSlot(bool)
    def setStatusCollapsed(self, collapsed):
        self._status_collapsed = bool(collapsed)
        self._save_state()
        self._publish()
    @pyqtSlot(int)
    def setConsoleHeight(self, height):
        # The drag handle's commit: clamped here so no negative or absurd
        # height can ever reach the file, and skipped when unchanged so a
        # drag riding its clamp stops rewriting the state file.
        height = max(0, min(CONSOLE_HEIGHT_MAX, int(height)))
        if height == self._console_height:
            return
        self._console_height = height
        self._save_state()
        self._publish()
    @pyqtSlot(str, bool)
    def setSectionExpanded(self, section, expanded):
        sections = dict(self._sections)
        sections[str(section)] = bool(expanded)
        self._sections = sections
        self._save_state()
        self._publish()

    @pyqtSlot(str, bool)
    def setTemperatureSensorVisible(self, name, visible):
        # Missing keys mean the default (visible); only a real change saves.
        if self._chart_config.get("visible", {}).get(str(name), True) is bool(visible):
            return  # idempotent: a re-bound checkbox must not rewrite the config
        config = self._prune_chart_config(self._chart_config)
        config = deepcopy(config)
        config.setdefault("visible", {})[str(name)] = bool(visible)
        self._chart_config = config
        self._apply_chart_config()
        self._publish()

    @pyqtSlot(str, str)
    def setTemperatureSensorColor(self, name, color):
        color = str(color)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return  # the canvas only renders #rrggbb; anything else would silently draw grey
        # A missing entry means the palette default, so compare against
        # the colour the series actually renders with right now.
        if any(series["name"] == str(name) and series["color"] == color
               for series in self._chart_value().get("series", ())):
            return
        config = self._prune_chart_config(self._chart_config)
        config = deepcopy(config)
        config.setdefault("colors", {})[str(name)] = color
        self._chart_config = config
        self._apply_chart_config()
        self._publish()

    @pyqtSlot(bool)
    def setShowTemperatureTargets(self, show):
        if self._chart_config.get("showTargets") is bool(show):
            return
        self._chart_config = {**self._chart_config, "showTargets": bool(show)}
        self._apply_chart_config()
        self._publish()

    @pyqtSlot(bool)
    def setShowTemperaturePower(self, show):
        if self._chart_config.get("showPower") is bool(show):
            return
        self._chart_config = {**self._chart_config, "showPower": bool(show)}
        self._apply_chart_config()
        self._publish()

    def _chart_value(self):
        """The sample payload, rebuilt only when the history's revision
        or the persisted config actually changed."""
        key = json.dumps(self._chart_config, sort_keys=True)
        if (self._chart_payload is None or self._chart_payload_revision != self._history.revision
                or key != self._chart_config_key):
            self._chart_payload = chart_payload(self._history, self._chart_config)
            self._chart_payload_revision = self._history.revision
            self._chart_config_key = key
        return self._chart_payload

    def _legend_value(self):
        """Legend metadata (identity, labels, colours, visibility): its
        own property so legend delegates only rebuild when something
        actually changed, never at the 1 Hz sample cadence."""
        if self._legend_payload is None:
            self._legend_payload = {}
        key = json.dumps(self._chart_config, sort_keys=True) + "|" + "|".join(self._history.names())
        if self._legend_payload.get("_key") != key:
            chart = self._chart_value()
            self._legend_payload = {
                "_key": key,
                "series": [{"name": series["name"], "label": series["label"],
                            "color": series["color"], "visible": series["visible"],
                            "primary": series["primary"]} for series in chart["series"]],
                "showTargets": chart["showTargets"],
                "showPower": chart["showPower"],
                "palette": chart["palette"],
            }
        return self._legend_payload

    def _apply_chart_config(self):
        """Persist the chart config into the per-printer record (the
        camera_selected precedent); the global JSON keeps chrome only."""
        config = self._config()
        if getattr(config, "temperature_chart", None) != self._chart_config:
            self._apply_config(replace(config, temperature_chart=self._chart_config))

    def _prune_chart_config(self, config):
        """Drop colours/visibility for sensors that no longer exist; never
        prune while the live set is empty (startup before the first aux)."""
        names = self._history.names()
        if not names:
            return config
        for key in ("visible", "colors"):
            entries = config.get(key)
            if isinstance(entries, dict) and any(name not in names for name in entries):
                config = dict(config)
                config[key] = {name: value for name, value in entries.items() if name in names}
        return config

    def _on_store_note(self, _kind, text):
        """The store's failure sink (A6): the console note line is
        the durable channel (the action status's precedence can hide
        a line, round-2 S7)."""
        console = getattr(self, "_console", None)
        if console is not None:
            console.note(text)
        else:
            self._store_notes.append(text)

    def _save_state(self, replace=False):
        self._store.write({
            "sections": dict(self._sections),
            "whatsNewSeen": self._whats_new_seen,
            "controlsCollapsed": self._controls_collapsed,
            "controlsLocked": self._controls_locked,
            "infoCollapsed": self._info_collapsed,
            "statusCollapsed": self._status_collapsed,
            "consoleHeight": self._console_height,
            # The chrome-only rewrite in __init__ runs BEFORE the
            # service exists: rehydrated block then, live state after
            # (the live report: a legacy state file broke
            # the printer binding — this save raised).
            "fileManagerColumns": self._file_manager.column_state() if getattr(self, "_file_manager", None) is not None else self._file_columns_state,
            "toolhead": {
                "jogDistance": self._values.get("jogDistance", JOG_DISTANCE_DEFAULT),
                "extrudeDistance": self._values.get("extrudeDistance", EXTRUDE_DISTANCE_DEFAULT),
                "extrudeSpeed": self._values.get("extrudeSpeed", EXTRUDE_SPEED_DEFAULT),
            },
        }, merge=not replace)
    @pyqtSlot(object)
    def updateMoonrakerStatus(self, status): self._data.observe(status)
    @pyqtSlot()
    def pausePrint(self):
        # The lane's revalidation (4.3.0): the verdict is re-derived
        # from a FRESH observation at dispatch — never the cached
        # property — and a refusal reports the policy's words. A
        # missing observation denies (the pump's fail-closed polarity,
        # not the upload path's None-allows artefact).
        observation = getattr(self._data, "observation", None)
        verdict = can_pause(observation) if observation is not None else Verdict("disabled", R_UNKNOWN)
        if verdict.mode != "allowed":
            self._commands.report_status(f"Pause refused: {verdict.reason}")
            self._publish()
            return
        self._commands.send("Pause", "printer/print/pause")
    @pyqtSlot()
    def resumePrint(self):
        observation = getattr(self._data, "observation", None)
        verdict = can_resume(observation) if observation is not None else Verdict("disabled", R_UNKNOWN)
        if verdict.mode != "allowed":
            self._commands.report_status(f"Resume refused: {verdict.reason}")
            self._publish()
            return
        self._commands.send("Resume", "printer/print/resume")

    @pyqtSlot()
    def stripPausePrint(self):
        # The Preview strip's one control dispatches by state:
        # Resume while paused, Pause otherwise — both routes run the
        # lane's revalidation (the same slots the Dashboard uses).
        observation = getattr(self._data, "observation", None)
        state = observation.state if observation is not None else ""
        if state == "paused":
            self.resumePrint()
        else:
            self.pausePrint()
    @pyqtSlot()
    def cancelPrint(self):
        if self.canCancelPrint: self._commands.send("Cancel", "printer/print/cancel")
    @pyqtSlot(str)
    def excludeObject(self, name): self._controls.exclude(name)
    @pyqtSlot(str, result=bool)
    def sendConsoleCommand(self, text): return self._console.send(text)
    @pyqtSlot()
    def clearConsoleHistory(self): self._console.clear()
    @pyqtProperty(QVariant, constant=True)
    def whatsNewContent(self):
        # The overlay's static content: every version with the latest
        # flagged (rendered open at the top; the rest pre-collapsed).
        return whats_new_entries()

    @pyqtSlot()
    def checkWhatsNew(self):
        # The startup gate: a fresh install (or a version bump) shows
        # the overlay once. The harness seeds the marker, so the
        # suite's runs never see it unless a scenario asks.
        if whats_new_should_show(self._whats_new_seen):
            self.whatsNewRequested.emit()

    @pyqtSlot()
    def showWhatsNew(self):
        # The explicit reopen: never touches the once-per-version
        # marker.
        self.whatsNewRequested.emit()

    @pyqtSlot()
    def dismissWhatsNew(self):
        # Any dismiss path (Close, Esc, outside-click) lands here:
        # the marker records the version, and the overlay stays gone
        # until the next release.
        self._whats_new_seen = whats_new_latest()
        self._save_state()

    @pyqtSlot()
    def improveEta(self):
        # Download and index for the Monitor only — no preview render
        # unless the user loads it there later. The glyph turns into an
        # hourglass until the index lands, the pull fails, or the 90 s
        # timeout gives up; a click while busy is a legitimate retry
        # (the request path is idempotent and coalesced).
        if self._request_monitor_download is not None:
            self._improving_eta = True
            # The publish below must not clear the flag it just set:
            # the coordinator's load_active flips on its NEXT snapshot
            # rebuild, and the stale snapshot in this very publish
            # reads as "the load never started" (the red run: the
            # hourglass never fired when the improve ran from a
            # settled state). Skip the clear once; every later
            # publish sees the updated snapshot and clears honestly.
            self._skip_clear_once = True
            self._publish()
            self._request_monitor_download()
            QTimer.singleShot(90000, self._improve_eta_timeout)

    def _improve_eta_timeout(self):
        if self._improving_eta:
            self._improving_eta = False
            self._publish()
    @pyqtSlot(bool)
    @pyqtSlot(float, float)
    def setBedMeshThresholds(self, low, high):
        # The heightmap range filter (the author's request): ONE
        # shared window drives both surfaces — the Monitor pop-over
        # re-reads the published keys, the Preview card and scene node
        # follow through the presenter — so the two sliders stay
        # synchronised.
        mesh = self._mesh.snapshot
        mesh_min = float(mesh.get("minimum") or 0)
        mesh_max = float(mesh.get("maximum") or 0)
        if not mesh or mesh_max <= mesh_min:
            return
        low = min(max(float(low), mesh_min), mesh_max)
        high = min(max(float(high), mesh_min), mesh_max)
        if low > high:
            low, high = high, low
        if self._mesh_thresholds_touched and (low, high) == (self._mesh_threshold_low, self._mesh_threshold_high):
            return
        self._mesh_threshold_low, self._mesh_threshold_high = low, high
        self._mesh_thresholds_touched = True
        self._mesh.set_thresholds(low, high)
        self._publish()

    @pyqtSlot(bool)
    def setShowProbePoints(self, show):
        if self._show_probe_points is bool(show):
            return
        self._show_probe_points = bool(show)
        config = self._config()
        if getattr(config, "show_probe_points", None) != self._show_probe_points:
            self._apply_config(replace(config, show_probe_points=self._show_probe_points))
        self._publish()
    @pyqtSlot(str, bool)
    def setPowerDevice(self, name, on): self._controls.set_power(name, on)
    @pyqtSlot(int)
    def selectWebcam(self, index): self._camera.select(index)
    @pyqtSlot()
    def openFrontend(self):
        config = self._config()
        QDesktopServices.openUrl(QUrl(config.frontend_target))
    @pyqtSlot(str, str)
    def runMacro(self, name, arguments=""): self._controls.run_macro(name, arguments)
    @pyqtSlot()
    def homeAll(self): self._controls.setup("home")
    @pyqtSlot()
    def runQuadGantryLevel(self): self._controls.setup("qgl")
    @pyqtSlot()
    def calibrateBedMesh(self): self._controls.setup("mesh")
    @pyqtSlot(int)
    def applyTemperaturePreset(self, index): self._controls.apply_preset(index)
    @pyqtSlot(int)
    def previewSpeedFactor(self, percent): self._controls.factor("speed", percent, True)
    @pyqtSlot(int)
    def setSpeedFactor(self, percent): self._controls.factor("speed", percent)
    @pyqtSlot(int)
    def previewFlowFactor(self, percent): self._controls.factor("flow", percent, True)
    @pyqtSlot(int)
    def setFlowFactor(self, percent): self._controls.factor("flow", percent)
    @pyqtSlot(float)
    def adjustZOffset(self, amount): self._controls.z_offset(amount)
    @pyqtSlot()
    def clearZOffset(self): self._controls.z_offset()
    @pyqtSlot(str, int)
    def previewFanSpeed(self, name, percent): self._controls.output("fan", name, percent, True)
    @pyqtSlot(str, int)
    def setFanSpeed(self, name, percent): self._controls.output("fan", name, percent)
    @pyqtSlot(str, int)
    def previewLedBrightness(self, name, percent): self._controls.output("led-brightness", name, percent, True)
    @pyqtSlot(str, int)
    def setLedBrightness(self, name, percent): self._controls.output("led-brightness", name, percent)
    @pyqtSlot(str, int, int, int, int, int)
    def previewLedColor(self, name, r, g, b, w=0, brightness=-1): self._controls.led_color(name, r, g, b, w, brightness, True)
    @pyqtSlot(str, int, int, int, int, int)
    def setLedColor(self, name, r, g, b, w=0, brightness=-1): self._controls.led_color(name, r, g, b, w, brightness)
    @pyqtSlot(str, int)
    def previewPwmOutput(self, name, percent): self._controls.output("pwm-output", name, percent, True)
    @pyqtSlot(str, int)
    def setPwmOutput(self, name, percent): self._controls.output("pwm-output", name, percent)
    @pyqtSlot()
    def saveConfig(self): self._controls.setup("save")
    @pyqtSlot()
    def firmwareRestart(self): self._controls.firmware_restart()
    @pyqtSlot()
    def klipperRestart(self): self._controls.klipper_restart()
    @pyqtSlot()
    def hostRestart(self): self._controls.host_restart()
    @pyqtSlot()
    def emergencyStopClick(self): self._commands.emergency_click()
    @pyqtSlot()
    def emergencyHoldStarted(self): self._commands.emergency_hold_started()
    @pyqtSlot()
    def emergencyHoldReleased(self): self._commands.emergency_hold_released()
    @pyqtSlot(str)
    def loadBedMeshProfile(self, name): self._controls.mesh_profile(name)
    @pyqtSlot()
    def clearBedMesh(self): self._controls.clear_mesh()
    @pyqtSlot(bool)
    def setBedMeshPreviewVisible(self, visible): self._mesh.set_visible(visible)
    @pyqtSlot(str, result=QVariant)
    def macroParameterDefinitions(self, name): return QVariant(self._controls.macro_parameters(name))
    @pyqtSlot(str, int)
    def jog(self, axis, direction): self._toolhead.jog(axis, direction)
    @pyqtSlot(bool)
    def setPositionMode(self, absolute):
        # The abs/rel toggle (a live request).
        self._toolhead.set_absolute(absolute)

    @pyqtSlot(float)
    def setJogDistance(self, distance):
        self._toolhead.set_distance(distance)
        self._save_state()

    @pyqtSlot(float)
    def setExtrudeDistance(self, distance):
        self._toolhead.set_extrude_distance(distance)
        self._save_state()

    @pyqtSlot(float)
    def setExtrudeSpeed(self, speed):
        self._toolhead.set_extrude_speed(speed)
        self._save_state()
    @pyqtSlot(str)
    def home(self, axis): self._toolhead.home(axis)
    @pyqtSlot()
    def motorsOff(self): self._toolhead.motors_off()
    @pyqtSlot()
    def centerToolhead(self): self._toolhead.center_toolhead()
    @pyqtSlot()
    def zToZero(self): self._toolhead.z_to_zero()
    @pyqtSlot(int)
    def extrude(self, direction): self._toolhead.extrude(direction)
    @pyqtSlot()
    def heatersOff(self): self._controls.heaters_off()
