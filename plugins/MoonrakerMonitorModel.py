"""One Cura Qt model: declarations and composition, not an inheritance stack."""
from __future__ import annotations
import json
import logging
import os
import re
from copy import deepcopy
from PyQt6.QtCore import QLocale, QTimer, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Resources import Resources
from PyQt6.QtGui import QDesktopServices
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from .ConsoleController import ConsoleController


def _british_spelling() -> bool:
    """British spellings for Commonwealth-English locales: the author's
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
from datetime import datetime

from .FileManager import FileManager
from .FileManagerPolicy import (
    delete_candidates,
    filter_option_counts,
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
from .MonitorTemperatureHistory import TemperatureHistory, chart_payload
import time
from .MonitorTuning import MonitorTuning
from .ToolheadController import ToolheadController
from .ToolheadPolicy import EXTRUDE_DISTANCE_DEFAULT, EXTRUDE_SPEED_DEFAULT, JOG_DISTANCE_DEFAULT


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


def _read_state() -> dict:
    """The persisted panel state: collapsed sections, the control-pane
    collapse, the lock-all toggle and the console's dragged height. The
    first shipped format was a flat section map, which is migrated to the
    current shape on read."""
    try:
        with open(_sections_path(), "r", encoding="utf-8") as handle:
            decoded = json.load(handle)
        if isinstance(decoded, dict):
            sections = decoded.get("sections")
            if not isinstance(sections, dict):
                sections = decoded  # legacy flat section map
            return {
                "sections": {str(key): _state_bool(value) for key, value in sections.items()},
                "controlsCollapsed": _state_bool(decoded.get("controlsCollapsed", False)),
                "controlsLocked": _state_bool(decoded.get("controlsLocked", False)),
                "infoCollapsed": _state_bool(decoded.get("infoCollapsed", False)),
                "statusCollapsed": _state_bool(decoded.get("statusCollapsed", False)),
                "consoleHeight": _state_height(decoded.get("consoleHeight", 0)),
                "fileManagerColumns": normalise_columns(decoded.get("fileManagerColumns")),
                "temperatureChart": _chart_state(decoded.get("temperatureChart")),
                "toolhead": _toolhead_state(decoded.get("toolhead")),
            }
    except Exception:
        pass
    return {"sections": {}, "controlsCollapsed": False, "controlsLocked": False,
            "infoCollapsed": False, "statusCollapsed": False, "consoleHeight": 0,
            "fileManagerColumns": normalise_columns({}),
            "temperatureChart": _chart_state({}),
            "toolhead": _toolhead_state(None)}


def _toolhead_state(stored) -> dict:
    """The jog/extrude selection persists (the author's live report:
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
    try:
        path = _sections_path()
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        os.replace(path + ".tmp", path)
    except Exception:
        pass


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
    # The print-start watchdog: how long the print_stats transition
    # may take before the plugin says the start failed.
    FILE_PRINT_START_TIMEOUT_S = 15.0
    monitorChanged = pyqtSignal()
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
    sectionsChanged = pyqtSignal()
    controlsLockChanged = pyqtSignal()
    infoPaneChanged = pyqtSignal()
    statusPaneChanged = pyqtSignal()
    consoleHeightChanged = pyqtSignal()
    cameraRefreshChanged = pyqtSignal()
    fileManagerChanged = pyqtSignal()
    fileManagerThumbsChanged = pyqtSignal()

    _SIGNAL_KEYS = (
        ("monitorChanged", ("monitorState", "monitorConnected", "monitorFilename", "monitorProgress", "monitorLayer", "monitorLayerProgress",
                            "improvingEta", "improveEtaProgress", "improveEtaPhase", "monitorElapsed",
                            "monitorEta", "monitorEtaBasis", "monitorFinish", "monitorSpeed", "monitorFlow",
                            "monitorPosition", "monitorMessage", "monitorLayerSource", "filamentUsed", "filamentRemaining")),
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
        ("actionChanged", ("printActive", "canPausePrint", "canResumePrint", "canCancelPrint", "actionBusy",
                           "actionStatus", "emergencyHoldProgress")),
        ("controlsChanged", ("monitorLayerHeight", "macroNames", "hasQuadGantryLevel", "hasBedMesh", "canRunSetup",
                             "temperaturePresetNames", "canApplyTemperaturePreset", "speedFactorPercent", "flowFactorPercent",
                             "zOffset", "zOffsetText", "fanControlItems", "ledItems", "saveConfigPending", "saveConfigSummary",
                             "canSaveConfig")),
        ("emergencyStopChanged", ("emergencyStopClicks",)),
        ("toolheadChanged", ("jogEnabled", "jogDistance", "extrudeDistance", "extrudeSpeed",
                             "homedAxes", "positionMode", "jogStatus")),
        ("controlsLockChanged", ("controlsLocked", "controlsCollapsed")),
        ("infoPaneChanged", ("infoCollapsed",)),
        ("statusPaneChanged", ("statusCollapsed",)),
        ("consoleHeightChanged", ("consoleHeight",)),
        ("sectionsChanged", ("sectionExpandedMap",)),
        ("showProbePointsChanged", ("showProbePoints",)),
        ("cameraRefreshChanged", ("cameraRefreshNonce",)),
        ("fileManagerChanged", ("fileManagerRows", "fileManagerRecents", "fileManagerDirectory", "fileManagerDirectories", "fileManagerDiskText",
                                "fileManagerRefreshedAt", "fileManagerShown", "fileManagerPage", "fileManagerPageIndex",
                                "fileManagerPageCount", "fileManagerPageSize", "fileManagerPageSelection",
                                "fileManagerEmptyKind", "fileManagerSelected", "fileManagerSortColumn",
                                "fileManagerSortAscending", "fileManagerSearch", "fileManagerOpen", "fileManagerFilters",
                                "filePrintConfirm", "fileDeleteConfirm", "fileRenameTarget",
                                "fileRenameConflict", "fileUploadConfirm", "fileUploadProgress",
                                "fileManagerColumnWidths", "fileManagerColumnOrder", "fileManagerColumnHidden",
                                "fileManagerFilterCounts", "fileManagerFilterOptions", "fileManagerHistoryLoaded",
                                "fileManagerHistoryExhausted", "fileManagerWalkError")),
        # Thumbnails publish ALONE (the author's live report: each
        # scroll-triggered fetch reply rebuilt the whole payload).
        ("fileManagerThumbsChanged", ("fileManagerThumbs",)),
        ("consoleChanged", ("consoleHistory", "consoleLines", "consoleDropped", "consoleRevisions", "consolePending", "consoleErrorBell")),
        ("typedControlsChanged", ("temperaturePresetItems", "pwmOutputItems", "bedMeshAvailable", "bedMeshProfile",
                                  "bedMeshProfileNames", "bedMeshRows", "bedMeshColumns", "bedMeshValues", "bedMeshMinimum",
                                  "bedMeshMaximum", "bedMeshRange", "bedMeshXMin", "bedMeshXMax", "bedMeshYMin", "bedMeshYMax",
                                  "bedMeshRangeText", "bedMeshPreviewVisible")),
    )

    def __init__(self, output_controller, number_of_extruders, *, client, print_state, config, apply_config, bed_mesh,
                 request_load=None, request_monitor_download=None, request_file_download=None,
                 preferences_flushed=None, identity=None):
        super().__init__(output_controller, number_of_extruders)
        self._client, self._print_state, self._config, self._apply_config, self._mesh = \
            client, print_state, config, apply_config, bed_mesh
        self._identity = identity
        # The file-manager Download capability: the follower owns the
        # one-shot stream + load-into-Cura (the same lane discipline
        # as the improve-ETA pull).
        self._request_file_download = request_file_download
        self._file_print_confirm = None
        self._file_delete_confirm = None
        self._file_rename_target = None
        self._file_rename_conflict = False
        self._file_upload_confirm = None
        self._file_upload_progress = None
        self._print_match_attempt = None
        self._print_matched_at = None
        self._print_baseline = (0.0, 0.0)
        # The "improve ETA" action reuses the facade's load-current-print
        # flow (download + index), passed in as an explicit capability.
        self._request_load = request_load
        # The monitor-only variant: download + index WITHOUT the preview
        # render (the author's optimisation); the preview's own load
        # finds the file already local and skips the re-download.
        self._request_monitor_download = request_monitor_download
        self._show_probe_points = bool(getattr(self._config(), "show_probe_points", False))
        self._qv_cache = {}
        self._improving_eta = False
        self._values = {}
        state = _read_state()
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
        # values are the service's to OWN — the author's ruling that
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
            self._save_state()  # rewrite the global file chrome-only
        else:
            self._chart_config = {}
        self._history = TemperatureHistory()
        self._chart_payload = None  # rebuilt only when the history or config changes
        self._chart_payload_revision = -1
        self._chart_config_key = None
        self._legend_payload = None
        self._data = MonitorData(client, self)
        self._commands = MonitorCommands(self._data, self)
        self._tuning = MonitorTuning(self._data, self._commands, self)
        self._controls = MonitorControls(self._data, self._commands, self._tuning, bed_mesh, config, self)
        self._camera = MonitorCamera(self._data, config, apply_config, self)
        self._toolhead = ToolheadController(self._data, self._commands, self)
        # The persisted jog/extrude selection (the author's live
        # report) — applied before any publish so the first frame
        # already shows the saved options.
        self._toolhead.set_distance(self._toolhead_state["jogDistance"])
        self._toolhead.set_extrude_distance(self._toolhead_state["extrudeDistance"])
        self._toolhead.set_extrude_speed(self._toolhead_state["extrudeSpeed"])
        self._console = ConsoleController(self._data, self._commands, config, apply_config, identity, self)
        # The toolhead's clamp rejections land in the console as
        # local notes (the author's live request).
        self._toolhead.rejectedNote.connect(self._console.note)
        self._console_error_bell = False
        self._console_errors_seen = 0
        self._file_manager = FileManager(client, self)
        self._file_manager.set_column_state(self._file_columns_state)
        # Metascan outcomes land in the console as local notes (the
        # author's live report: the option appeared to do nothing).
        self._file_manager.note.connect(self._console.note)
        # Upload progress and outcome feed the popup (the author's
        # live request).
        self._file_manager.uploadProgress.connect(self._on_upload_progress)
        self._file_manager.uploadFinished.connect(self._on_upload_finished)
        # The light publish: thumb transitions never rebuild the rows.
        self._thumbs_dirty = False
        self._file_manager.thumbsChanged.connect(self._publish_thumbs)
        self._file_manager_open = False
        if preferences_flushed is not None:
            # The console marks its sent lines SAVED when the preference
            # file actually flushes (the author's colour ruling).
            preferences_flushed.connect(self._console.mark_saved)
        for signal in (self._data.changed, self._commands.changed, self._controls.changed, self._camera.changed,
                       self._toolhead.changed, self._console.changed, self._file_manager.changed, bed_mesh.changed):
            signal.connect(self._publish)
        # The history feeds once per auxiliary reply, not per publish
        # (per-publish feeding duplicated samples and halved the window);
        # a session invalidation restarts the window so the previous
        # printer's curves never bleed into the next one.
        self._data.auxiliaryChanged.connect(self._on_auxiliary)
        self._data.consoleStoreChanged.connect(self._on_console_store)
        self._data.invalidated.connect(self._on_invalidated)
        # The attach-time reload can run before the active machine's
        # identity resolves; retry it on every poll heartbeat so the
        # restored transcript lands the moment the config is readable
        # (the author's "commands never rehydrate" report).
        self._data.changed.connect(self._console.reload_if_empty)
        # The author's ruling (2026-09-10): after a reconnect the
        # camera stream restarts — the nonce bump reloads the stream
        # on every connection transition into connected (the
        # e-stop's automatic cycle included).
        self._data.connectionStateChanged.connect(self._on_connection_state)
        self._data.set_active(True)
        self._publish()

    def _on_connection_state(self, connected: bool) -> None:
        if not connected:
            return
        self._camera_refresh_nonce += 1
        self._publish()

    def _on_console_store(self):
        # Klipper's output arrives from the gcode-store poll; the
        # controller merges it into the transcript feed.
        self._console.append_responses(self._data.console_entries)

    @pyqtSlot(bool)
    def setConsoleExpanded(self, expanded):
        # The console polls the store only while on screen (the author's
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
            "fileManagerFilterOptions": filter_option_counts(fm.resident_rows(), now=now),
            "fileManagerHistoryLoaded": fm.history_loaded,
            "fileManagerHistoryExhausted": fm.history_exhausted,
            "fileManagerWalkError": fm.walk_error or "",
        }

    def _printing_relpath(self):
        state = self._data.snapshot.core.get("print_stats") or {}
        filename = str(state.get("filename") or "")
        if not filename:
            return None
        # print_stats.filename is root-exclusive (round-2 D4).
        return filename

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
        self._values["fileManagerThumbs"] = self._file_manager.thumbnail_payload()
        self.fileManagerThumbsChanged.emit()

    def _publish(self):
        fm = self._file_manager
        previous = self._values
        snapshot = self._print_state()
        values = core_values(self._data.snapshot, snapshot, self._client.connected)
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
        # Thumbnails fetch per the RENDER WINDOW, not the page: the
        # QML's visibleRows change drives the request (the author's
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
        # The awaited print transition (round-2 D4: success is NEVER
        # the POST reply). A start that never transitions — OR that
        # matches the filename but never makes PROGRESS (Klipper can
        # accept the start and freeze before the first motion — the
        # author's live report: the UI stayed "printing" on a failed
        # start) — explains itself and drops the assumed-active state.
        attempt = self._file_manager.print_attempt
        if attempt is not None:
            stats = self._data.snapshot.core.get("print_stats") or {}
            filename = str(stats.get("filename") or "")
            if self._print_match_attempt != attempt:
                self._print_match_attempt = attempt
                self._print_matched_at = None
            if filename == attempt[0]:
                if self._print_matched_at is None:
                    self._print_matched_at = time.time()
                    vdcard = self._data.snapshot.core.get("virtual_sdcard") or {}
                    self._print_baseline = (
                        float(stats.get("print_duration") or 0.0),
                        float(vdcard.get("file_position") or 0.0),
                    )
                else:
                    vdcard = self._data.snapshot.core.get("virtual_sdcard") or {}
                    duration = float(stats.get("print_duration") or 0.0)
                    position = float(vdcard.get("file_position") or 0.0)
                    if duration > self._print_baseline[0] or position > self._print_baseline[1]:
                        # The print is genuinely moving: the start
                        # succeeded.
                        self._file_manager.clear_print_attempt()
                    elif time.time() - self._print_matched_at > self.FILE_PRINT_START_TIMEOUT_S:
                        self._print_start_failed("The printer accepted the file but no progress began.")
            elif time.time() - attempt[1] > self.FILE_PRINT_START_TIMEOUT_S:
                self._print_start_failed("The printer did not begin printing.")
        # The no-reflow rule's sibling ruling (the author, 2026-09-10):
        # while DISCONNECTED every control on the Monitor page disables
        # — the QML gates its sections and the emergency stop on this.
        values["monitorConnected"] = self._client.connected
        values.update(self._controls.values)
        values.update(self._camera.values)
        values.update(self._toolhead.values)
        values.update(self._console.values)
        # The console error bell (the author's live request): while
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
        state_word = commands.state
        values.update(printActive=commands.print_active,
            canPausePrint=state_word == "printing" and not commands.busy,
            canResumePrint=commands.state == "paused" and not commands.busy,
            canCancelPrint=commands.print_active and not commands.busy, actionBusy=commands.busy,
            actionStatus=commands.status, emergencyStopClicks=commands.clicks,
            emergencyHoldProgress=commands.hold_progress, powerDevices=self._controls.power_devices(),
            bedMeshAvailable=bool(mesh), bedMeshProfile=str(mesh.get("profile") or "Current mesh") if mesh else "",
            bedMeshRows=int(mesh.get("rows") or 0), bedMeshColumns=int(mesh.get("columns") or 0),
            bedMeshValues=list(mesh.get("values") or ()), bedMeshMinimum=float(mesh.get("minimum") or 0),
            bedMeshMaximum=float(mesh.get("maximum") or 0), bedMeshRange=float(mesh.get("range") or 0),
            bedMeshXMin=float(mesh.get("xMin") or 0), bedMeshXMax=float(mesh.get("xMax") or 0),
            bedMeshYMin=float(mesh.get("yMin") or 0), bedMeshYMax=float(mesh.get("yMax") or 0),
            bedMeshRangeText=f"{float(mesh.get('range') or 0):.3f} mm range" if mesh else "",
            bedMeshPreviewVisible=self._mesh.visible,
            controlsLocked=self._controls_locked, controlsCollapsed=self._controls_collapsed,
            infoCollapsed=self._info_collapsed, statusCollapsed=self._status_collapsed,
            consoleHeight=self._console_height,
            cameraRefreshNonce=self._camera_refresh_nonce,
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
        if self._improving_eta and (snapshot.index_ready or not snapshot.load_active):
            # The index landed, or the download/build failed and the
            # coordinator cleared its flags (panel finding P1-1): the
            # hourglass ends and the glyph becomes the retry affordance.
            # The 90 s timer stays as the last resort for a hung pull.
            self._improving_eta = False
        self._values = values
        try: self.setCameraUrl(QUrl(self._camera.url))
        except AttributeError: pass

        # Qt notify signals are part of control ownership. Broadcasting every
        # signal for every poll was re-evaluating bound ComboBox/Slider values
        # while the user was interacting with them, and QVariant-list updates
        # could also rebuild Repeater delegates mid-drag. Only notify the group
        # whose published values actually changed.
        for signal_name, keys in self._SIGNAL_KEYS:
            if any(previous.get(key) != values.get(key) for key in keys):
                getattr(self, signal_name).emit()

    def _print_start_failed(self, reason) -> None:
        """The start's failure verdict (the author's live ruling): the
        console note, the action status, and the assumed-stopped state
        so the UI is never left claiming an active print."""
        self._file_manager.clear_print_attempt()
        self._print_matched_at = None
        self._console.note(f"Print start failed — {reason}")
        self._commands.report_status(f"Print start failed — {reason}")
        self._data.assume_print_stopped()


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
    monitorMessage = value_property(str, "monitorMessage", monitorChanged, "")
    printActive = value_property(bool, "printActive", actionChanged, False)
    canPausePrint = value_property(bool, "canPausePrint", actionChanged, False)
    canResumePrint = value_property(bool, "canResumePrint", actionChanged, False)
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
        self._print_match_attempt = None
        self._print_matched_at = None
        self._print_baseline = (0.0, 0.0)
        if confirm is not None:
            self._file_manager.start_print(confirm["relpath"])
        self._publish()

    @pyqtSlot()
    def fileCancelPrint(self):
        self._file_print_confirm = None
        self._print_match_attempt = None
        self._print_matched_at = None
        self._print_baseline = (0.0, 0.0)
        self._publish()

    @pyqtSlot(str)
    def fileDownload(self, relpath):
        if self._request_file_download is not None:
            self._request_file_download(str(relpath))

    @pyqtSlot(bool)
    def setFileManagerOpen(self, is_open):
        self._file_manager_open = bool(is_open)
        self._publish()

    @pyqtSlot()
    def openFileManager(self):
        # The open trigger: the flag gates the heavy payload work
        # (the author's live report: the closed popup must not keep
        # paying the per-poll cost).
        self._file_manager_open = True
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
            # Single-value categories store a SCALAR (the author's
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


    def _printing_relpath(self) -> str:
        return str((self._data.snapshot.core.get("print_stats") or {}).get("filename") or "")

    @pyqtSlot()
    def fileRequestDelete(self):
        """The bulk delete from the selection (Snapshot 3): the
        currently-printing file is never offered (the author's gate
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
    def fileRequestDeleteDir(self, path):
        """The folder delete (the author's live request — right-click
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
        """The folder rename (the author's live request — right-click
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
        """Snapshot 3 upload (the author's ruling): LOCAL gcode files
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
        if name_collides(self._file_manager.resident_rows(), target):
            self._file_upload_confirm = {"path": path, "filename": name}
            self._publish()
            return
        self._start_upload(path, name)
        self._publish()

    def _start_upload(self, path, name, overwrite=False) -> None:
        # The popup's payload opens on the FIRST publish after this;
        # the service's progress and outcome signals drive it from
        # here on (the author's live request: a bar while it runs and
        # a success/fail verdict at the end).
        self._file_upload_progress = {"name": name, "percent": 0,
                                      "state": "uploading", "error": ""}
        # The overwrite nod MUST ride through: without it the service
        # re-refuses the colliding name and never emits a verdict —
        # the popup hung on "uploading" forever (the author's live
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
        """The render window's thumbnails (the author's live report:
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
        manager (the author's ruling: the file manager is its own
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
        if self._file_upload_progress is None:
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
        if self._file_upload_progress is None:
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
    controlsLocked = value_property(bool, "controlsLocked", controlsLockChanged, False)
    controlsCollapsed = value_property(bool, "controlsCollapsed", controlsLockChanged, False)
    infoCollapsed = value_property(bool, "infoCollapsed", infoPaneChanged, False)
    statusCollapsed = value_property(bool, "statusCollapsed", statusPaneChanged, False)
    consoleHeight = value_property(int, "consoleHeight", consoleHeightChanged, 0)
    cameraRefreshNonce = value_property(int, "cameraRefreshNonce", cameraRefreshChanged, 0)
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

    def _save_state(self):
        _write_state({
            "sections": dict(self._sections),
            "controlsCollapsed": self._controls_collapsed,
            "controlsLocked": self._controls_locked,
            "infoCollapsed": self._info_collapsed,
            "statusCollapsed": self._status_collapsed,
            "consoleHeight": self._console_height,
            # The chrome-only rewrite in __init__ runs BEFORE the
            # service exists: rehydrated block then, live state after
            # (the author's live report: a legacy state file broke
            # the printer binding — this save raised).
            "fileManagerColumns": self._file_manager.column_state() if getattr(self, "_file_manager", None) is not None else self._file_columns_state,
            "toolhead": {
                "jogDistance": self._values.get("jogDistance", JOG_DISTANCE_DEFAULT),
                "extrudeDistance": self._values.get("extrudeDistance", EXTRUDE_DISTANCE_DEFAULT),
                "extrudeSpeed": self._values.get("extrudeSpeed", EXTRUDE_SPEED_DEFAULT),
            },
        })
    @pyqtSlot(object)
    def updateMoonrakerStatus(self, status): self._data.observe(status)
    @pyqtSlot()
    def pausePrint(self):
        if self.canPausePrint: self._commands.send("Pause", "printer/print/pause")
    @pyqtSlot()
    def resumePrint(self):
        if self.canResumePrint: self._commands.send("Resume", "printer/print/resume")
    @pyqtSlot()
    def cancelPrint(self):
        if self.canCancelPrint: self._commands.send("Cancel", "printer/print/cancel")
    @pyqtSlot(str)
    def excludeObject(self, name): self._controls.exclude(name)
    @pyqtSlot(str, result=bool)
    def sendConsoleCommand(self, text): return self._console.send(text)
    @pyqtSlot()
    def clearConsoleHistory(self): self._console.clear()
    @pyqtSlot()
    def improveEta(self):
        # Download and index for the Monitor only — no preview render
        # unless the user loads it there later. The glyph turns into an
        # hourglass until the index lands, the pull fails, or the 90 s
        # timeout gives up; a click while busy is a legitimate retry
        # (the request path is idempotent and coalesced).
        if self._request_monitor_download is not None:
            self._improving_eta = True
            self._publish()
            self._request_monitor_download()
            QTimer.singleShot(90000, self._improve_eta_timeout)

    def _improve_eta_timeout(self):
        if self._improving_eta:
            self._improving_eta = False
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
        # The abs/rel toggle (the author's live request).
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
