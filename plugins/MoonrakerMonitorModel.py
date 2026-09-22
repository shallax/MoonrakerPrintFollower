"""One Cura Qt model: declarations and composition, not an inheritance stack."""
from __future__ import annotations
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
from PyQt6.QtCore import QLocale, QThreadPool, QTimer, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Resources import Resources
from PyQt6.QtGui import QDesktopServices
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from .ConsoleController import ConsoleController


def _coerce_anchor(value):
    """A valid integer ZERO is a layer (the P0 zero-index bug: the
    old `value or -1` read the first layer as missing and refused the
    detach/scrub on it). Only None and unparseable values mean
    missing."""
    if value is None:
        return -1
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


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
from .PlateQt import (
    PlateLayer, RasterBridge, _RasterJob, _PLATE_TRAVEL_VISUAL_RATIO,
    _bridge_emit, png_file, render_layer_prefix, render_layer_raster,
    render_navigation_layer,
)
from .MonitorCommands import MonitorCommands
from .MonitorControls import MonitorControls, _exclude_status
from .MonitorData import MonitorData
from .MonitorPermissions import REASON_DETAIL, R_PAUSED_NOTE, R_UNKNOWN, Verdict, can_jog, can_pause, can_restart, can_resume, can_start_print, jog_caption, section_reason
from .FilesViewModel import FilesViewModel
from .PrintStartOwner import PrintStartOwner
from .UiStateStore import UiStateStore
from datetime import datetime

from .FileManager import FileManager
from .SectionLayoutPolicy import PANE_NAMES, layout_for, normalise_section_layout
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
    number,
    print_job_caption,
    file_disk_text,
    file_row_payload,
    file_timestamp,
    peripheral_values,
    plate_values,
)
from dataclasses import replace

from .PrinterConfig import (
    CAMERA_FPS_DEFAULT,
    CAMERA_FPS_FALLBACK_MAX,
    CAMERA_FPS_MIN,
    normalise_temperature_chart,
)
from .StateStore import StateStore
from .MonitorTemperatureHistory import (
    DORMANT_CHART,
    PALETTE,
    TemperatureHistory,
    chart_payload,
    latest_values,
    mini_chart_payload,
    mini_names,
    series_metadata,
)
from .MonitorTuning import MonitorTuning
from .ToolheadController import ToolheadController
from .ToolheadPolicy import EXTRUDE_DISTANCE_DEFAULT, EXTRUDE_SPEED_DEFAULT, JOG_DISTANCE_DEFAULT
from .WhatsNew import entries as whats_new_entries, latest_version as whats_new_latest, should_show as whats_new_should_show


# The monitor's panel state lives in a plugin-owned JSON file next to
# cura.cfg. Uranium's preference store is not used: it drops reads and
# writes on unregistered keys depending on version, and only persists on
# Cura's own save cycle, so the state has proven unreliable there. The
# file name predates the extra fields and stays for continuity.
SECTIONS_FILE_NAME = "moonrakerprintfollower_sections.json"

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


def _migration_banner_text(record):
    """The dialog banner's copy (the UX spec): the rollback recipe is
    the message — the file name, the folder route and the
    reinstall-previous-version steps."""
    backup = str(record.get("backupName") or "")
    if record.get("backupWritten") and backup:
        return ("Your Moonraker settings did not carry over from the previous version, so the plugin is using defaults. "
                "Cura's configuration was saved as %s. Open it from Help > Show Configuration Folder. "
                "To roll back: close Cura, reinstall the previous version of the plugin, and copy that file over cura.cfg.") % backup
    return ("Your Moonraker settings did not carry over from the previous version, so the plugin is using defaults. "
            "Nothing was removed — your existing Cura configuration is untouched.")


def _migration_diagnostics_text(record):
    """The permanent diagnostics row's copy (after dismissal): the
    recipe is demoted, never deleted."""
    backup = str(record.get("backupName") or "")
    if record.get("backupWritten") and backup:
        return "Settings migration failed. The previous configuration is saved as %s." % backup
    return "Settings migration failed. Nothing was removed."


def _read_state(store=None) -> dict:
    """The persisted panel state: collapsed sections, the control-pane
    collapse, the lock-all toggle and the console's dragged height. The
    first shipped format was a flat section map, which is migrated to the
    current shape on read. The FILE semantics live in the StateStore
    (4.2.0, F11); the coercion below is the model's own (its tests pin
    the fallback document)."""
    decoded = _store_read(store or StateStore(_sections_path()))
    if isinstance(decoded, dict):
        sections = decoded.get("sections")
        if not isinstance(sections, dict):
            # The legacy flat section map — recognised ONLY when every
            # value is a bool: a document that lacks `sections` and
            # carries the UI-state store's sibling keys must not
            # hydrate them as sections (4.3.0, the second consumer).
            if all(isinstance(value, bool) for value in decoded.values()):
                sections = decoded
            else:
                sections = {}
        return {
            "sections": {str(key): _state_bool(value) for key, value in sections.items()},
            "sectionLayout": normalise_section_layout(decoded.get("sectionLayout")),
            "whatsNewSeen": str(decoded.get("whatsNewSeen") or ""),
            "controlsCollapsed": _state_bool(decoded.get("controlsCollapsed", False)),
            "controlsLocked": _state_bool(decoded.get("controlsLocked", False)),
            "infoCollapsed": _state_bool(decoded.get("infoCollapsed", False)),
            "statusCollapsed": _state_bool(decoded.get("statusCollapsed", False)),
            "consoleHeight": _state_height(decoded.get("consoleHeight", 0)),
            "fileManagerColumns": normalise_columns(decoded.get("fileManagerColumns")),
            "temperatureChart": _chart_state(decoded.get("temperatureChart")),
            "toolhead": _toolhead_state(decoded.get("toolhead")),
            "followerView": _follower_view_state(decoded.get("followerView")),
        }
    return {"sections": {}, "sectionLayout": normalise_section_layout({}), "whatsNewSeen": "",
            "controlsCollapsed": False, "controlsLocked": False,
            "infoCollapsed": False, "statusCollapsed": False, "consoleHeight": 0,
            "fileManagerColumns": normalise_columns({}),
            "temperatureChart": _chart_state({}),
            "toolhead": _toolhead_state(None),
            "followerView": _follower_view_state(None)}


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


def _follower_view_state(stored) -> dict:
    """The print follower's view settings — GLOBAL, not per printer
    (the live ruling): the layer toggles, the stroke thickness and the
    centred follow. Bools stay booleans; the scale clamps to the
    control's 0.5-2.0 range."""
    stored = stored if isinstance(stored, dict) else {}
    def flag(key, default):
        value = stored.get(key, default)
        return value if isinstance(value, bool) else default
    def scale(value):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.7
        return min(2.0, max(0.5, parsed))
    return {
        "showPrevious": flag("showPrevious", True),
        "showNext": flag("showNext", True),
        "showBase": flag("showBase", True),
        "showTravels": flag("showTravels", False),
        # The centred follow is an OPTION and stays one: the default
        # render path is the cheap one (the 2026-09-20 ruling).
        "keepCentred": flag("keepCentred", False),
        "lineScale": scale(stored.get("lineScale", 0.7)),
    }


def _store_read(store):
    """The store slot's read: the persistence facade owns the global
    document in production (4.5.0); the StateStore double serves the
    harness's config-only path."""
    if hasattr(store, "state_global_document"):
        return store.state_global_document()
    return store.read()


def _store_write(store, update: dict, merge: bool = True, delete: tuple = ()) -> None:
    """The store slot's write: the facade's global-document merge in
    production, the StateStore's merge in the double."""
    if hasattr(store, "merge_state_global") and merge:
        store.merge_state_global(update, delete=delete)
    else:
        store.write(update, merge=merge, delete=delete)


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


# The plate's no-index payload, one stable identity: a quiet poll must
# never re-wrap an empty plate (the identity memo's empty twin).
_EMPTY_PLATE = {"objects": [], "truncated": 0, "excludedCount": 0}


class _RenderSurface:
    """One follower surface's native render context: the popover and the mini each own their view, plot,
    generation, layer wrappers and scheduler state, so neither can
    overwrite the other's context or invalidate its rasters. The
    decoded payloads stay shared between surfaces; the rendered
    images (and their keys) never are."""

    def __init__(self, name):
        self.name = name
        self.plot = None
        self.view = {}
        self.generation = 0
        self.layers = OrderedDict()          # layer -> PlateLayer (bound 6)
        self.anchor = None
        self.anchor_epoch = 0
        # The desired demand, set per publish: the current layer and
        # the ghost pair, stamped with the anchor epoch they belong
        # to .
        self.desired = None
        self.tokens = {}                     # layer -> demand token
        self.job = None                      # {"layer", "token", "generation", "state", "cancel"}
        self.render_count = {}               # layer -> raster requests
        self.render_serial = 0               # the immutable-asset serial
        self.job_epoch = 0                   # the print epoch (set by the model)
        self.visible = False                 # the surface's QML consumer gate
        self.stats = {"started": 0, "committed": 0, "superseded": 0,
                      "cancelled": 0, "failed": 0, "discarded": 0, "depth_max": 0}
        self.job_failures = 0                # the persistent-failure latch
        # The staged plot/view pair: the setters stage, one zero-tick
        # flush commits the burst .
        self.stage = {"plot": None, "view": None, "armed": False}
        # The navigation raster's double-buffered slot: `url` is the
        # READY interaction scene the face may switch to instantly,
        # `job` the in-flight background update (one per surface —
        # live updates coalesce), both camera-independent and
        # epoch-keyed. The mini never carries one.
        self.nav = {"key": None, "url": "", "job": None, "cancel": None,
                    "serial": 0}

    def render_key(self):
        """The key a raster must carry to display on this surface
        : the generation (which bumps
        exactly when the context changes) plus the explicit
        pixel-affecting inputs and the PRINT epoch, so a key is
        self-describing and a stale worker from a previous print
        can never match it."""
        view = self.view
        plot = self.plot or {}
        return (self.name, self.job_epoch, self.generation,
                int(view.get("width") or 0), int(view.get("height") or 0),
                bool(view.get("compact")), round(float(view.get("scale") or 1.0), 6),
                round(float(view.get("lineScale") or 0.7), 6),
                round(float(view.get("panX") or 0.0), 3), round(float(view.get("panY") or 0.0), 3),
                round(float(view.get("dpr") or 1.0), 6),
                round(float(plot.get("offsetX") or 0.0), 6), round(float(plot.get("offsetY") or 0.0), 6),
                round(float(plot.get("sx") or 0.0), 6), round(float(plot.get("sy") or 0.0), 6),
                round(float(plot.get("bedXMin") or 0.0), 6), round(float(plot.get("bedYMax") or 0.0), 6))


class MoonrakerMonitorModel(PrinterOutputModel):
    whatsNewDismissed = pyqtSignal()
    monitorChanged = pyqtSignal()
    previewBlockChanged = pyqtSignal(dict)
    webcamsChanged = pyqtSignal()
    temperatureChartMiniChanged = pyqtSignal()
    temperatureChartFullChanged = pyqtSignal()
    temperatureChartLatestChanged = pyqtSignal()
    temperatureChartLegendChanged = pyqtSignal()
    consoleChanged = pyqtSignal()
    cameraTransformChanged = pyqtSignal()
    peripheralsChanged = pyqtSignal()
    plateObjectsChanged = pyqtSignal()
    plateProgressChanged = pyqtSignal()
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
    sectionLayoutChanged = pyqtSignal()
    controlsLockChanged = pyqtSignal()
    infoPaneChanged = pyqtSignal()
    statusPaneChanged = pyqtSignal()
    consoleHeightChanged = pyqtSignal()
    cameraRefreshChanged = pyqtSignal()
    cameraRecoveringChanged = pyqtSignal()
    cameraFpsChanged = pyqtSignal()
    webcamStreamEnabledChanged = pyqtSignal()
    followerViewChanged = pyqtSignal()
    connectionDetailChanged = pyqtSignal()
    fileManagerChanged = pyqtSignal()
    # Fired when the once-per-version overlay should show (the
    # startup check or an explicit reopen); the plugin's window
    # owner listens and creates/shows the QML overlay.
    whatsNewRequested = pyqtSignal()
    fileManagerThumbsChanged = pyqtSignal()

    _SIGNAL_KEYS = (
        ("monitorChanged", ("monitorState", "monitorConnected", "monitorFilename", "monitorProgress", "monitorLayer", "monitorLayerProgress",
                            "platePassFraction",
                            "improvingEta", "improveEtaProgress", "improveEtaPhase", "monitorElapsed",
                            "monitorEta", "monitorEtaBasis", "monitorFinish", "monitorSpeed", "monitorFlow",
                            "monitorPosition", "monitorPositionCompact", "monitorVelocity", "monitorFlowRate", "monitorFlowDiameter",
                            "monitorAccelLimit", "monitorMessage", "monitorLayerSource", "filamentUsed", "filamentRemaining",
                            "sectionReason", "sectionReasonDetail",
                            # The round's additions (the panel's catch):
                            # a key outside its signal's group never
                            # notifies — the next-pause readout and
                            # the collapsed cells went stale while
                            # PAUSED (the other keys in the group
                            # masked it while printing).
                            "nextPauseLayer", "nextPauseEta", "nextPauseFraction", "nextPauseBaked",
                            "monitorPositionX", "monitorPositionY", "monitorPositionZ",
                            "migrationBannerVisible", "migrationBannerText", "migrationBackupAvailable",
                            "migrationDiagnosticsVisible", "migrationDiagnosticsText")),
        ("webcamsChanged", ("webcamNames", "activeWebcamIndex")),
        ("temperatureChartMiniChanged", ("temperatureChartMini",)),
        ("temperatureChartFullChanged", ("temperatureChartFull",)),
        ("temperatureChartLatestChanged", ("temperatureChartLatest",)),
        ("temperatureChartLegendChanged", ("temperatureChartLegend",)),
        ("cameraTransformChanged", ("cameraName", "cameraRotation", "cameraFlipHorizontal", "cameraFlipVertical")),
        ("peripheralsChanged", ("temperatureItems", "fanItems", "filamentSensorItems")),
        ("plateObjectsChanged", ("plateObjects", "plateDot", "plateHasObjects")),
        # The follower view's state precedes the plate payloads: a
        # detaching seek flips followerAttached in the SAME emission
        # cycle BEFORE the new layer's payload arrives, so QML never
        # paints the new current layer as a pending base while it
        # still reads the previous attached state and then clears it
        # .
        ("followerViewChanged", ("followerShowPrevious", "followerShowNext", "followerShowBase", "followerShowTravels", "followerLineScale",
                                 "followerTravelVisualRatio", "followerKeepCentred", "followerAttached", "followerLayerAnchor")),
        ("plateProgressChanged", ("plateLayers", "plateSplit", "plateScrubVector", "plateProgressAnchor", "plateProgressAvailable", "plateProgressReason",
                                  "plateLayerCount", "plateLayerMotionCount",
                                  "plateLiveLayers", "plateLiveSplit", "plateLiveAnchor", "plateLiveAvailable", "plateLiveScrubVector",
                                  "plateNavigationData")),
        ("powerDevicesChanged", ("powerDevices",)),
        ("systemChanged", ("klippyState", "moonrakerVersion", "klipperVersion", "hostLoad", "memoryAvailable",
                           "cpuTemperature", "mcuSummary", "mcuItems")),
        ("endstopsChanged", ("endstopItems", "endstopSummary")),
        ("actionChanged", ("printActive", "printJobCaption", "canPausePrint", "canResumePrint", "pauseReason", "pauseReasonDetail", "resumeReason", "resumeReasonDetail", "canCancelPrint", "actionBusy",
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
        ("sectionLayoutChanged", ("sectionLayout", "sectionHiddenMap")),
        ("showProbePointsChanged", ("showProbePoints",)),
        ("cameraRefreshChanged", ("cameraRefreshNonce",)),
        ("webcamStreamEnabledChanged", ("webcamStreamEnabled",)),
        ("cameraFpsChanged", ("cameraFps", "cameraFpsMin", "cameraFpsMax")),
        ("traceCameraTimingChanged", ("traceCameraTiming",)),
        ("cameraRecoveringChanged", ("cameraRecovering",)),
        ("connectionDetailChanged", ("connectionDetail",)),
        ("fileManagerChanged", ("fileManagerRows", "fileManagerRecents", "fileManagerDirectory", "fileManagerDirectories", "fileManagerDiskText", "fileManagerNote",
                                "fileManagerRefreshedAt", "fileManagerShown", "fileManagerPage", "fileManagerPageIndex",
                                "fileManagerPageCount", "fileManagerPageSize", "fileManagerPageSelection",
                                "fileManagerEmptyKind", "fileManagerSelected", "fileManagerSortColumn",
                                "fileManagerSortAscending", "fileManagerSearch", "fileManagerOpen", "fileManagerFilters",
                                "filePrintConfirm", "fileDeleteConfirm", "fileRenameTarget",
                                "fileRenameConflict", "fileUploadConfirm", "fileUploadProgress",
                                "fileDownloadProgress",
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
                 request_plate_anchor=None, request_plate_split=None,
                 request_follower_popover_open=None,
                 download_failed=None, request_download_progress=None, cancel_file_download=None,
                 identity=None, state_store=None, persistence=None, index_service=None):
        super().__init__(output_controller, number_of_extruders)
        self._client, self._print_state, self._config, self._apply_config, self._mesh = \
            client, print_state, config, apply_config, bed_mesh
        # The decoded cache's owner (the follower's index service):
        # the render wrappers pin their payloads there so the decoded
        # budget counts what the wrappers keep alive, and the memory
        # accounting reads the tiers back. Optional — the tests and
        # the harness mount without it.
        self._index_service = index_service
        # The follower's anchor seam (the pop-over's layer slider): the
        # model publishes the state, the coordinator owns the payload.
        # The split seam is the progress slider's scrub, same shape.
        self._request_plate_anchor = request_plate_anchor
        self._request_plate_split = request_plate_split
        # The coordinator's explicit demand gate: the closed popover
        # stops the frozen window's per-poll serving (the reviewer's
        # C).
        self._request_follower_popover_open = request_follower_popover_open
        self._identity = identity
        # The state file's owner (4.2.0, F11/A6): passed in as a
        # capability — 4.3.0's UI-state store consumes the same
        # instance; the default builds the production path.
        # The store slot: the persistence facade owns the global chrome
        # in production (4.5.0); the StateStore double serves the
        # harness's config-only path.
        self._store = persistence or state_store or StateStore(_sections_path(), note=self._on_store_note)
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
        # The save download's progress window: the model polls the
        # follower's progress payload each publish; the Cancel button
        # retires the in-flight stream.
        self._request_download_progress = request_download_progress
        self._cancel_file_download = cancel_file_download
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
        self._improve_started_snapshot = None
        self._migration_record_cache = None
        self._migration_record_read = False
        self._peripheral_cache = (None, {})
        self._endstop_cache = (None, {})
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
        follower_view = state["followerView"]
        self._follower_show_previous = follower_view["showPrevious"]
        self._follower_show_next = follower_view["showNext"]
        self._follower_show_base = follower_view["showBase"]
        self._follower_show_travels = follower_view["showTravels"]
        self._follower_line_scale = follower_view["lineScale"]
        self._follower_keep_centred = follower_view["keepCentred"]
        # The follower's attach state and its frozen layer — a LIVE view
        # state, never persisted: a restart follows the print again, and
        # the frozen layer belongs to the file that was printing.
        self._follower_attached = True
        self._follower_layer_anchor = -1
        self._follower_layer_split = None
        self._follower_job = None
        # The native render pipeline: PER-
        # SURFACE render contexts  — the
        # compact mini and the full popover hold their own view,
        # plot, generation, layer wrappers and scheduler state. The
        # QML's role shrinks to composition; the vector geometry
        # crosses only for the scrub's delta, as its own key.
        self._plate_surfaces = {"popover": _RenderSurface("popover"),
                                "mini": _RenderSurface("mini")}
        self._plate_qt_job = None
        # The PRINT epoch: a monotonic counter bumped on every job
        # switch. It rides every ticket, render key and asset file
        # name, so a stale worker from the previous print can never
        # structurally match the new print's request — even when the
        # layer, token and generation all coincide.
        self._plate_job_epoch = 0
        self._raster_bridge = RasterBridge(self)
        # The scheduler's accounting: the
        # worker reports its start AND its completion through the
        # bridge, so a job superseded BEFORE it ran is countable.
        self._raster_bridge.started.connect(self._raster_started)
        self._raster_bridge.done.connect(self._raster_committed)
        # The raster cache directory (the transport ruling): the
        # QML Images consume file:// PNGs the workers write here —
        # a data: URL loads but never renders, and a QImage variant
        # segfaults the Canvas (both engine-proven). INSTANCE-OWNED:
        # every model gets its own directory, so two printers can
        # never collide on filenames or prune each other's assets;
        # the model's destruction removes it.
        self._raster_cache_dir = tempfile.mkdtemp(prefix="mpf-raster-%d-" % os.getpid())
        self.destroyed.connect(self._cleanup_raster_dir)
        self.destroyed.connect(self._release_all_pins)
        # The seek trace: disabled by
        # default; MOONRAKER_FOLLOWER_SEEK_TRACE=1 (or the config's
        # seek_trace) records the stage timeline with the queue
        # depth per event.
        self._seek_trace_enabled = os.environ.get("MOONRAKER_FOLLOWER_SEEK_TRACE") == "1"
        self._seek_trace = []
        self._seek_tick_mono = None
        # The plate surfaces' open states (the QML reports them): a
        # closed popover freezes its payload keys.
        self._follower_popover_open = False
        self._picker_popover_open = False
        self._section_layout = state["sectionLayout"]
        # The UI-state store (4.3.0): the sections map's persistence
        # moves to the second consumer — the model's save payload
        # stops rewriting the whole map, so the two writers can no
        # longer clobber each other at the top level.
        self._ui_state = UiStateStore(store=self._store)
        # The files view model (4.3.0): the stable-identity surface
        # behind the list-valued projection — an internal
        # collaborator, never the published surface.
        self._files_model = FilesViewModel(self)
        self._files_model_rev = -1
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
            # The legacy global block migrates into the per-printer
            # record once: the store deletes ONLY that key — a
            # full-document rewrite would erase the UI-state store's
            # sibling keys (4.3.0, the sibling rule's single
            # exception removed).
            _store_write(self._store, {"sections": dict(self._sections)}, delete=("temperatureChart",))
        else:
            self._chart_config = {}
        self._plate_cache_key = None
        self._plate_geometry = None
        self._plate_payload = None
        self._history = TemperatureHistory()
        # Each chart surface has its own cache, invalidated only by what
        # it actually reads: the mini and latest caches by the history
        # revision and config, the full cache additionally by the
        # pop-over's open state (closed serves the shared dormant
        # object — same identity every feed, so no signal and no
        # QVariant re-conversion while no full chart exists).
        self._chart_mini = None
        self._chart_mini_key = None
        self._chart_full = None
        self._chart_full_key = None
        self._chart_latest = None
        self._chart_latest_revision = -1
        self._chart_open = False  # the pop-over's hydration gate (K)
        self._legend_payload = None
        self._data = MonitorData(client, self)
        # The hydrated lock reaches the policy record: a session
        # that starts locked must read locked, not wait for the
        # padlock to be cycled.
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
        self._webcam_stream_enabled = True
        self._camera.streamFailed.connect(self._on_stream_failed)
        self._camera.streamRecovered.connect(self._on_stream_recovered)
        # The wake recovery (a live report): a stream that
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
        self._console = ConsoleController(self._data, self._commands, config, apply_config, identity, self,
                                          persistence=self._store if hasattr(self._store, "set_machine_state") else None)
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
        # live report: the option appeared to do nothing).
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
        self._publish_pending = False
        self._file_manager.thumbsChanged.connect(self._publish_thumbs)
        self._file_manager_open = False
        # The console's saved-state colouring now rides its own 2 s
        # settle after each shard write (ConsoleController); the
        # preference-flush channel retired with the transcript (4.5.0).
        # The publication coalescer: ONE data.changed fans out
        # through the collaborators, each of which used to publish
        # the full model again — one landing built the projection
        # three or four
        # times. The heartbeat signals schedule; one flush per
        # event-loop turn rebuilds once. The two USER-ACTION
        # collaborators publish synchronously so a jog or slider's
        # own status is visible before the slot returns — and their
        # observe() now emits only when the projection actually
        # changed, so heartbeats no longer fan out through them.
        for signal in (self._data.changed, self._commands.changed, self._camera.changed,
                       self._console.changed, bed_mesh.changed):
            signal.connect(self._schedule_publish)
        # The user-action collaborators publish synchronously so a
        # click's own re-render happens before the slot returns; the
        # file manager joins them because its view mutations (sort,
        # search, page) must re-render immediately (the live report
        # of the carousel advancing one step then stopping).
        for signal in (self._controls.changed, self._toolhead.changed, self._file_manager.changed):
            signal.connect(self._publish)
        # The auxiliary arrivals publish the pane readouts; the chart
        # feeds from its own 1 s clock (see _on_chart_tick), so the
        # delivery slider can never shrink or stretch the chart's
        # advertised 30-minute window. A session invalidation restarts
        # the window so the previous printer's curves never bleed into
        # the next one.
        self._data.auxiliaryChanged.connect(self._on_auxiliary)
        self._chart_timer = QTimer(self)
        self._chart_timer.setInterval(1000)
        self._chart_timer.timeout.connect(self._on_chart_tick)
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
        # The ruling (2026-09-10): after a reconnect the
        # camera stream restarts — the nonce bump reloads the stream
        # on every connection transition into connected (the
        # e-stop's automatic cycle included).
        self._data.connectionStateChanged.connect(self._on_connection_state)
        self._data.set_owner_active(True)
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
        if not self._webcam_stream_enabled:
            return
        self._camera_refresh_nonce += 1
        self._schedule_publish()

    def _on_stream_failed(self) -> None:
        if not self._webcam_stream_enabled:
            return
        from .CameraTiming import mark
        mark("T6-watchdog", "camera render stalled")
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
        a request): the expanded bed-mesh map draws the
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
            # Woke up: reload the camera source once — for the ACTIVE
            # monitor only; a deposed cached monitor must not
            # publish. No veil — the stream may come back instantly,
            # and a stuck veil would read as a failure the user must
            # recover.
            if not getattr(self._data, "active", False):
                return
            self._camera_refresh_nonce += 1
            self._publish()

    @pyqtSlot()
    def cameraRenderStalled(self) -> None:
        # The render watchdog (a live report): a stream
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
            # "completely empty at app start" report).
            self._console.reload_if_empty()
        self._data.set_console_expanded(expanded, stored)

    def _on_auxiliary(self):
        self._schedule_publish()

    def _layer_index(self, snapshot):
        layer_info = getattr(snapshot, "layer", None)
        return getattr(layer_info, "index", None)

    def _plate_objects_value(self, visited):
        """The plate geometry: polygons memoised per job on the lane
        object's identity (the freeze fix keeps it stable across
        ticks), the volatile flags and the verdicts overlaid per
        publish."""
        aux = self._data.snapshot.auxiliary.get("exclude_object") if self._data.snapshot.auxiliary else None
        core = self._data.snapshot.core.get("exclude_object") if self._data.snapshot.core else None
        poly_source = aux if isinstance(aux, Mapping) and aux.get("objects") else \
            core if isinstance(core, Mapping) and core.get("objects") else None
        if poly_source is None:
            self._plate_cache_key = None
            self._plate_geometry = None
            self._plate_payload = _EMPTY_PLATE
            return self._plate_payload
        key = id(poly_source.get("objects"))
        if self._plate_cache_key != key:
            self._plate_geometry = plate_values(poly_source)
            self._plate_cache_key = key
            self._plate_payload = None
        status = _exclude_status(self._data.snapshot)
        excluded = frozenset(status.get("excluded_objects") or ())
        current = status.get("current_object")
        # The visited set comes from the PRINT snapshot (the monitor
        # snapshot never carries plate_visited — the green-printed
        # report: reading it there made passed always false).
        visited = visited or frozenset()
        rows = []
        for row in self._plate_geometry["objects"]:
            fresh = dict(row)
            fresh["excluded"] = fresh["name"] in excluded
            fresh["current"] = fresh["name"] == current
            # Passed: the executed motions have touched the polygon
            # this layer (the live ruling: the print order is NOT the
            # define order on every machine — the toolhead's own
            # visits are the truth, read back from the layer's
            # start).
            fresh["passed"] = (fresh["name"] in visited
                               and not fresh["excluded"]
                               and not fresh["current"])
            rows.append(fresh)
        payload = {"objects": rows,
                   "truncated": self._plate_geometry["truncated"],
                   "excludedCount": len(excluded)}
        # The identity memo: unchanged rows republish the SAME object,
        # so QML never re-binds and the canvas never repaints on a
        # quiet poll (the live report — the picker crawled once the
        # index arrived because every poll re-wrapped the polygons).
        if payload != self._plate_payload:
            self._plate_payload = payload
        return self._plate_payload

    def _on_chart_tick(self):
        # The chart's fixed 1 s sampling (Mainsail's temperature store
        # cadence): a delivery slower than 1 s simply holds the last
        # value — a truthful step, never an interpolation. The feed
        # pauses while the session is disconnected so a reconnect
        # re-arms the window through the gap reset instead of bridging
        # a frozen snapshot.
        if self._data.connection_state == "yes":
            self._history.observe(self._data.snapshot.auxiliary, time.monotonic(), time.time())
            self._schedule_publish()

    def _on_invalidated(self):
        self._history.reset()
        self._plate_cache_key = None
        self._plate_geometry = None
        self._plate_payload = None
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
        self._improve_started_snapshot = None
        self._publish()

    def setMonitoringActive(self, active):
        if not active:
            # Ownership revocation retires the chart pop-over's
            # transient hydration state: a cached monitor must not
            # resume full-history construction after a machine switch
            # while no chart is open on screen. It must land BEFORE
            # the ownership call — that synchronously emits the
            # invalidation whose publish then already serves the
            # dormant full payload (it is served whenever _chart_open
            # is false), and a later open hydrates normally.
            # A SESSION invalidation on an owned monitor does not run
            # this path: the chart stays logically open through a
            # disconnect, and the next feed rehydrates it.
            self._chart_open = False
            self._chart_full = None
        if active:
            self._chart_timer.start()
        else:
            self._chart_timer.stop()
        self._data.set_owner_active(active)
        # The post-migration ready point: the record may have landed
        # since construction (the early publishes read it while the
        # migration was still pending) — the cache re-reads once here
        # and then holds, even a None (the heartbeat never parses the
        # settings file; H1).
        self._migration_record_read = False
        self._migration_record_cache = None
        if active:
            # The stage-entry hook (the 4.5.0 live find): the Monitor
            # shell exists by the time Cura activates the stage, and
            # the pane order's apply must land before the first frame
            # — the signal path the toggle uses, fired here.
            self.sectionLayoutChanged.emit()

    def _file_manager_values(self):
        fm = self._file_manager
        now = time.time()
        if not self._file_manager_open:
            # The popup is closed: the grid's bindings are inert, and
            # rebuilding the ROW payloads per poll is pure waste —
            # closing a 400-file listing stalled for seconds (the
            # live report). The cheap view state still
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
        # The view model rebuilds ONLY when the projection's revision
        # moves — the per-publish cost stays on the cached rows.
        if fm.projection_count != self._files_model_rev:
            self._files_model_rev = fm.projection_count
            self._files_model.set_rows(rows)
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

    @staticmethod
    def _coerce(value, sentinel):
        """The Optional-snapshot seam (the 4.5.0 debt pack): a None fed
        straight into a typed C++ property crashed the model — the
        sentinel stands in uniformly instead of the per-field prose
        this replaces."""
        return value if value is not None else sentinel

    def _schedule_publish(self):
        """The publication coalescer: heartbeat signals schedule one
        flush per event-loop turn, so a single data landing
        publishes the full model once instead of once per
        collaborator."""
        if self._publish_pending:
            return
        self._publish_pending = True
        QTimer.singleShot(0, self._flush_publish)

    def _flush_publish(self):
        self._publish_pending = False
        self._publish()

    def _publish(self):
        fm = self._file_manager
        previous = self._values
        snapshot = self._print_state()
        # The follower's attach state belongs to ONE print: a new file
        # re-attaches it before the value block reads the state.
        self._observe_follower_job(getattr(snapshot, "job_key", None))
        if self._improving_eta and snapshot is not self._improve_started_snapshot \
                and not snapshot.load_active:
            # The coordinator REBUILT its snapshot since the improve
            # began (every rebuild is a new object) and the load is
            # terminated — the index landed or the download/build
            # failed (panel finding P1-1). The identity gate is the
            # replacement for the one-shot skip latch: the publish
            # coalescer's extra turns re-publish the SAME pre-rebuild
            # snapshot, which used to clear the hourglass the moment
            # any publish ran. The hourglass ends and the glyph
            # becomes the retry affordance — settled BEFORE the
            # values build so the published value reflects the
            # cleared state. The 90 s timer stays as the last resort
            # for a hung pull.
            self._improving_eta = False
        values = core_values(self._data.snapshot, snapshot, self._client.connected)
        # Connected with no auxiliary data landed yet: the pane's
        # loading state (the 2026-09-16 request — the empty grey page
        # on entry reads as dead, not as arriving).
        values["monitorLoading"] = bool(self._client.connected and not self._data.snapshot.auxiliary)
        # The download progress window's payload: {name, percent} while
        # a save download streams, "" otherwise (the popup's gate).
        values["fileDownloadProgress"] = (self._request_download_progress() or ""
                                          if self._request_download_progress is not None else "")
        # The M117 message lives on Klipper's display_status object,
        # not print_stats — the Print-job slot reads it from the aux
        # snapshot (the report: M117 showed nowhere).
        display = (self._data.snapshot.auxiliary or {}).get("display_status")
        if isinstance(display, Mapping):
            message = str(display.get("message") or "")
            if message:
                values["monitorMessage"] = message
        # The lane-identity caches: the peripheral scan and the
        # endstop projection rebuild only when their lane's data
        # object actually changed — a
        # core-only heartbeat used to rescan every sensor and fan.
        # The key spans BOTH lanes: the volatile exclude fields read
        # the core lane (the 4.6.0 move), so a core-only update must
        # rebuild the rows too.
        core_exclude = self._data.snapshot.core.get("exclude_object") if self._data.snapshot.core else None
        aux_key = (id(self._data.snapshot.auxiliary), id(core_exclude))
        if self._peripheral_cache[0] != aux_key:
            self._peripheral_cache = (aux_key, peripheral_values(self._data.snapshot))
        values.update(self._peripheral_cache[1])
        # The map's states are plain — included, current, excluded,
        # and the passed fill (the objects the executed motions have
        # touched on the current layer, read from the print snapshot).
        # The picker's map gates the same way: the popover's open
        # state or the section's expansion keeps it live, and a fully
        # closed picker carries the last payload untouched.
        if self._picker_popover_open or self._sections.get("plate", True) is not False:
            values["plateObjects"] = self._plate_objects_value(
                getattr(snapshot, "plate_visited", frozenset()))
            # The QML-facing support flag: a plain bool, so no binding
            # ever needs to reach INTO the payload (the empty-plate live
            # report — member access on the QVariant payload is not a
            # binding worth trusting).
            values["plateHasObjects"] = bool(values["plateObjects"]["objects"])
        else:
            values["plateObjects"] = self._values.get("plateObjects", _EMPTY_PLATE)
            values["plateHasObjects"] = self._values.get("plateHasObjects", False)
        # The follower face's payload, SPLIT (the perf ruling): the
        # static layers publish with the service's memoised identity
        # (QML never re-wraps the polylines on a quiet poll), and the
        # volatile split crosses as a bare number. The envelope
        # carries the availability and the reason.
        # TWO payloads (the live request): the popover reads the
        # frozen one while detached (the live one otherwise), and the
        # MINI reads only the live one — the mini never detaches with
        # the popover, and neither does the picker (its map is the
        # plate_objects value, always the live layer's).
        # The job bar's background-optimisation band publishes here,
        # OUTSIDE the popover's gates — the bar is always visible.
        fraction = getattr(snapshot, "plate_pass_fraction", None)
        values["platePassFraction"] = fraction if fraction is not None else -1.0
        lookup_ms = getattr(snapshot, "plate_lookup_ms", None)
        if lookup_ms is None:
            lookup_ms = getattr(snapshot, "plate_decode_ms", None)  # legacy test/snapshot
        progress = getattr(snapshot, "plate_progress", None)
        follower = getattr(snapshot, "plate_manual_progress", None)
        popover = follower if follower is not None else progress
        # The surfaces gate their payloads: a closed popover or a
        # collapsed section never re-wraps a fresh payload, so the
        # memo churn costs nothing while nothing renders (the live
        # request). While gated the keys carry the last published
        # objects; opening or expanding resumes the live values.
        if self._follower_popover_open:
            values["plateLayers"] = (self._qt_window(self._plate_surfaces["popover"],
                                                     popover["layers"], popover.get("anchor"),
                                                     popover.get("method"), popover.get("split"),
                                                     lookup_ms)
                                     if popover is not None else {})
            values["plateScrubVector"] = self._scrub_vector_for(popover)
            values["plateSplit"] = popover["split"] if popover is not None else None
            # The progress slider's range: the layer's own motion count (0
            # while the payload has not landed — the slider reads dead).
            try:
                values["plateLayerMotionCount"] = int(popover["motionTotal"]) if popover is not None else 0
            except (TypeError, ValueError, KeyError):
                values["plateLayerMotionCount"] = 0
            # -1, never None: the anchor is an int property, and a None
            # publish crashes the QVariant-to-int conversion (the live
            # report's TypeError).
            values["plateProgressAnchor"] = (popover["anchor"]
                                              if popover is not None and popover["anchor"] is not None else -1)
            values["plateProgressAvailable"] = bool(popover is not None and popover.get("layers", {}).get("current") is not None)
            if popover is None:
                # No index at all: the reason stays empty — the face's
                # download action owns that state (its idle line
                # carries the offer). A non-empty reason is the
                # exists-but-loading case, the plain label's own.
                values["plateProgressReason"] = ""
            elif not values["plateProgressAvailable"]:
                values["plateProgressReason"] = "Loading layer…"
            else:
                values["plateProgressReason"] = ""
            # The warm interaction raster: a retained URL reaches the
            # face ONLY while it is READY for the current demand (its
            # key matches the demand key) — a scrub, a toggle or a
            # failed replacement retires the stale raster from the
            # face the moment the demand moves, and the exact scene
            # serves the gesture instead (the review's lifecycle).
            surface = self._plate_surfaces["popover"]
            values["plateNavigationData"] = self._navigation_data_value(surface)
            self._schedule_navigation(surface)
        else:
            values["plateLayers"] = self._values.get("plateLayers", {})
            values["plateScrubVector"] = self._values.get("plateScrubVector")
            values["plateSplit"] = self._values.get("plateSplit")
            values["plateLayerMotionCount"] = self._values.get("plateLayerMotionCount", 0)
            values["plateProgressAnchor"] = self._values.get("plateProgressAnchor", -1)
            values["plateProgressAvailable"] = self._values.get("plateProgressAvailable", False)
            values["plateProgressReason"] = self._values.get("plateProgressReason", "")
            values["plateNavigationData"] = self._values.get("plateNavigationData", "")
        # The mini's own view: the live payload, always (the live
        # request). The section's collapse gates it — a collapsed
        # mini never re-renders.
        if self._sections.get("plateprogress", True) is not False:
            values["plateLiveLayers"] = (self._qt_window(self._plate_surfaces["mini"],
                                                         progress["layers"], progress.get("anchor"),
                                                         progress.get("method"), progress.get("split"),
                                                         lookup_ms)
                                         if progress is not None else {})
            values["plateLiveScrubVector"] = self._scrub_vector_for(progress)
            values["plateLiveSplit"] = progress["split"] if progress is not None else None
            values["plateLiveAnchor"] = (progress["anchor"]
                                          if progress is not None and progress["anchor"] is not None else -1)
            values["plateLiveAvailable"] = bool(progress is not None and progress.get("layers", {}).get("current") is not None)
        else:
            values["plateLiveLayers"] = self._values.get("plateLiveLayers", {})
            values["plateLiveScrubVector"] = self._values.get("plateLiveScrubVector")
            values["plateLiveSplit"] = self._values.get("plateLiveSplit")
            values["plateLiveAnchor"] = self._values.get("plateLiveAnchor", -1)
            values["plateLiveAvailable"] = self._values.get("plateLiveAvailable", False)
        # The layer slider's range: the index's own layer count. A
        # manual anchor outside the file is refused by the coordinator,
        # so this is the range the QML slider reads back.
        try:
            layer_count = int(getattr(snapshot, "plate_layer_count", 0) or 0)
        except (TypeError, ValueError):
            layer_count = 0
        values["plateLayerCount"] = max(0, layer_count)
        # The plate's toolhead dot (physical position, the marker
        # convention): validity rides the connection — a paused
        # print's position is honest, a disconnected one is a lie if
        # drawn live .
        motion = self._data.snapshot.core.get("motion_report") or {}
        position = motion.get("live_position") or ()
        values["plateDot"] = {
            "x": number(position[0], 0.0) if len(position) >= 2 else 0.0,
            "y": number(position[1], 0.0) if len(position) >= 2 else 0.0,
            "valid": bool(self._client.connected and len(position) >= 2),
        }
        endstops_key = (id(self._data.snapshot.endstops), self._client.connected)
        if self._endstop_cache[0] != endstops_key:
            self._endstop_cache = (endstops_key,
                                   endstop_values(self._data.snapshot, self._client.connected))
        values.update(self._endstop_cache[1])
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
        # disabled, the pause-first warning, AND the paused note (the
        # raw reason blanked the paused state, the one where the row
        # must speak).
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
        values["webcamStreamEnabled"] = self._webcam_stream_enabled
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
        # The heightmap range filter (a request): ONE
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
            printJobCaption=print_job_caption(observation),
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
            sectionLayout=self._section_layout,
            sectionHiddenMap={section: True for entry in self._section_layout.values()
                              for section in entry["hidden"]},
            temperatureChartMini=self._chart_mini_value(),
            temperatureChartFull=self._chart_full_value(),
            temperatureChartLatest=self._chart_latest_value(),
            temperatureChartLegend=self._legend_value(),
            showProbePoints=self._show_probe_points,
            followerShowPrevious=self._follower_show_previous,
            followerShowNext=self._follower_show_next,
            followerShowBase=self._follower_show_base,
            followerShowTravels=self._follower_show_travels,
            followerLineScale=self._follower_line_scale,
            followerTravelVisualRatio=_PLATE_TRAVEL_VISUAL_RATIO,
            followerKeepCentred=self._follower_keep_centred,
            followerAttached=self._follower_attached,
            followerLayerAnchor=self._follower_layer_anchor,
            britishSpelling=_british_spelling(),
            improvingEta=(snapshot.load_active or self._improving_eta) and not snapshot.index_ready,
            # The next scheduled pause (the live ruling): the
            # JobSection's readout and both stacked bars' orange
            # third fill.
            nextPauseLayer=self._coerce(snapshot.next_pause_layer, -1),
            nextPauseEta=snapshot.next_pause_eta,
            # The typed property cannot hold None (the live crash: a
            # NoneType into a C++ double) — the -1.0 sentinel means
            # "no pause ahead", same contract as the progress fields.
            nextPauseFraction=self._coerce(snapshot.next_pause_fraction, -1.0),
            nextPauseBaked=bool(snapshot.next_pause_baked),
            # The determinate fraction through both phases: the
            # download's byte fraction, then the index build's own
            # byte-offset progress (the scanner reports it).
            improveEtaProgress=self._coerce(
                max(0.0, min(1.0, snapshot.download_fraction if snapshot.download_fraction is not None else snapshot.index_fraction))
                if (snapshot.load_active or self._improving_eta)
                   and (snapshot.download_fraction is not None or snapshot.index_fraction is not None)
                else None, -1.0),
            improveEtaPhase=("Downloading…" if (snapshot.load_active or self._improving_eta) and snapshot.download_fraction is not None
                             else "Indexing…" if (snapshot.load_active or self._improving_eta) and snapshot.indexing
                             else "Resolving…" if snapshot.load_active or self._improving_eta else ""))
        record = self._migration_record()
        failed = bool(record and record.get("status") == "failed")
        values["migrationBannerVisible"] = bool(failed and not record.get("bannerDismissed"))
        values["migrationBannerText"] = _migration_banner_text(record) if failed else ""
        values["migrationBackupAvailable"] = bool(failed and record.get("backupWritten") and record.get("backupName"))
        values["migrationDiagnosticsVisible"] = bool(failed and record.get("bannerDismissed"))
        values["migrationDiagnosticsText"] = _migration_diagnostics_text(record) if failed else ""
        self._values = values
        first_attach = False
        try:
            url = self._camera.url if self._webcam_stream_enabled else ""
            if url:
                last_url = self._camera_last_url
                # A query-only transition is the upstream's own noise
                # (a rotated nonce in the reported stream URL): the
                # live stream keeps working, so no reload and no nonce
                # bump — the pane's guard ignores the query too. An
                # origin, port or path transition still reloads.
                def _stripped(u):
                    cut = u.find("?")
                    return u[:cut] if cut >= 0 else u
                changed = _stripped(url) != _stripped(last_url or "")
                if url != last_url:
                    self._camera_last_url = url
                if changed:
                    # Any camera-URL transition deserves a fresh load:
                    # the first attach's initial request dies silently
                    # in the loader (the report — the manual refresh
                    # worked because it changed the URL).
                    first_attach = not last_url
                    self._camera_refresh_nonce += 1
                    # The bump rides THIS publish's values (the camera-
                    # delay fix): published one cycle late it drove a
                    # SECOND stream application after the URL's — the
                    # QML coalescer collapses the same-cycle pair into
                    # one.
                    values["cameraRefreshNonce"] = self._camera_refresh_nonce
            self.setCameraUrl(QUrl(url))
        except AttributeError:
            pass
        from .CameraTiming import enabled as camera_timing_enabled, mark as camera_timing_mark
        values["traceCameraTiming"] = camera_timing_enabled()
        if first_attach:
            # T5: the FINAL url QML consumes, sanitised to
            # scheme/host/port/path (never query credentials).
            sanitised = QUrl(str(url))
            sanitised.setQuery("")
            camera_timing_mark("T5", "camera URL published: %s" % sanitised.toString())

        # Qt notify signals are part of control ownership. Broadcasting every
        # signal for every poll was re-evaluating bound ComboBox/Slider values
        # while the user was interacting with them, and QVariant-list updates
        # could also rebuild Repeater delegates mid-drag. Only notify the group
        # whose published values actually changed.
        for signal_name, keys in self._SIGNAL_KEYS:
            if any(previous.get(key) != values.get(key) for key in keys):
                getattr(self, signal_name).emit()

    monitorState = value_property(str, "monitorState", monitorChanged, "Not connected")
    # The migration-failure surfaces (the UX ruling): the dialog's
    # banner and the permanent diagnostics row read these.
    migrationBannerVisible = value_property(bool, "migrationBannerVisible", monitorChanged, False)
    migrationBannerText = value_property(str, "migrationBannerText", monitorChanged, "")
    migrationBackupAvailable = value_property(bool, "migrationBackupAvailable", monitorChanged, False)
    migrationDiagnosticsVisible = value_property(bool, "migrationDiagnosticsVisible", monitorChanged, False)
    migrationDiagnosticsText = value_property(str, "migrationDiagnosticsText", monitorChanged, "")
    monitorConnected = value_property(bool, "monitorConnected", monitorChanged, False)
    monitorFilename = value_property(str, "monitorFilename", monitorChanged, "")
    monitorProgress = value_property(float, "monitorProgress", monitorChanged, 0.0)
    monitorLayer = value_property(str, "monitorLayer", monitorChanged, "—")
    monitorLayerProgress = value_property(float, "monitorLayerProgress", monitorChanged, -1.0)
    platePassFraction = value_property(float, "platePassFraction", monitorChanged, -1.0)
    plateScrubVector = value_property(QVariant, "plateScrubVector", plateProgressChanged, None)
    plateLiveScrubVector = value_property(QVariant, "plateLiveScrubVector", plateProgressChanged, None)
    # The interaction scene's READY flattened full-bed raster URL
    # (camera-independent; the pan/zoom presentation transforms
    # never re-render it).
    plateNavigationData = value_property(str, "plateNavigationData", plateProgressChanged, "")
    monitorLayerSource = value_property(str, "monitorLayerSource", monitorChanged, "")
    filamentUsed = value_property(str, "filamentUsed", monitorChanged, "—")
    filamentRemaining = value_property(str, "filamentRemaining", monitorChanged, "—")
    britishSpelling = value_property(bool, "britishSpelling", monitorChanged, False)
    improvingEta = value_property(bool, "improvingEta", monitorChanged, False)
    nextPauseLayer = value_property(int, "nextPauseLayer", monitorChanged, -1)
    nextPauseEta = value_property(str, "nextPauseEta", monitorChanged, "")
    nextPauseFraction = value_property(float, "nextPauseFraction", monitorChanged, -1.0)
    nextPauseBaked = value_property(bool, "nextPauseBaked", monitorChanged, False)
    improveEtaProgress = value_property(float, "improveEtaProgress", monitorChanged, -1.0)
    improveEtaPhase = value_property(str, "improveEtaPhase", monitorChanged, "")
    monitorElapsed = value_property(str, "monitorElapsed", monitorChanged, "00:00:00")
    monitorEta = value_property(str, "monitorEta", monitorChanged, "—")
    monitorEtaBasis = value_property(str, "monitorEtaBasis", monitorChanged, "")
    monitorFinish = value_property(str, "monitorFinish", monitorChanged, "—")
    monitorSpeed = value_property(str, "monitorSpeed", monitorChanged, "100%")
    monitorFlow = value_property(str, "monitorFlow", monitorChanged, "100%")
    monitorPosition = value_property(str, "monitorPosition", monitorChanged, "—")
    monitorPositionCompact = value_property(str, "monitorPositionCompact", monitorChanged, "—")
    # The collapsed strip's per-axis cells (the live ruling).
    monitorPositionX = value_property(str, "monitorPositionX", monitorChanged, "—")
    monitorPositionY = value_property(str, "monitorPositionY", monitorChanged, "—")
    monitorPositionZ = value_property(str, "monitorPositionZ", monitorChanged, "—")
    # The motion rows (4.2.0): the defaults read "—" until the first
    # snapshot lands — an idle CONNECTED printer reads 0 (Klipper
    # always reports the motion fields once the object exists).
    monitorVelocity = value_property(str, "monitorVelocity", monitorChanged, "—")
    monitorFlowRate = value_property(str, "monitorFlowRate", monitorChanged, "—")
    monitorFlowDiameter = value_property(str, "monitorFlowDiameter", monitorChanged, "—")
    monitorAccelLimit = value_property(str, "monitorAccelLimit", monitorChanged, "—")
    monitorMessage = value_property(str, "monitorMessage", monitorChanged, "")
    # Connected with no auxiliary data landed yet — the pane's honest
    # empty state (the 2026-09-16 request: a Loading prompt instead of
    # a grey page on entry).
    monitorLoading = value_property(bool, "monitorLoading", monitorChanged, False)
    printActive = value_property(bool, "printActive", actionChanged, False)
    printJobCaption = value_property(str, "printJobCaption", actionChanged, "")
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
    plateObjects = value_property(QVariant, "plateObjects", plateObjectsChanged, {"objects": [], "truncated": 0, "excludedCount": 0})
    plateDot = value_property(QVariant, "plateDot", plateObjectsChanged, {"x": 0.0, "y": 0.0, "valid": False})
    plateHasObjects = value_property(bool, "plateHasObjects", plateObjectsChanged, False)
    plateLayers = value_property(QVariant, "plateLayers", plateProgressChanged, {})
    plateSplit = value_property(QVariant, "plateSplit", plateProgressChanged, None)
    plateProgressAnchor = value_property(int, "plateProgressAnchor", plateProgressChanged, -1)
    plateProgressAvailable = value_property(bool, "plateProgressAvailable", plateProgressChanged, False)
    plateProgressReason = value_property(str, "plateProgressReason", plateProgressChanged, "")
    plateLayerCount = value_property(int, "plateLayerCount", plateProgressChanged, 0)
    plateLayerMotionCount = value_property(int, "plateLayerMotionCount", plateProgressChanged, 0)
    plateLiveLayers = value_property(QVariant, "plateLiveLayers", plateProgressChanged, {})
    plateLiveSplit = value_property(QVariant, "plateLiveSplit", plateProgressChanged, None)
    plateLiveAnchor = value_property(int, "plateLiveAnchor", plateProgressChanged, -1)
    plateLiveAvailable = value_property(bool, "plateLiveAvailable", plateProgressChanged, False)
    followerShowPrevious = value_property(bool, "followerShowPrevious", followerViewChanged, True)
    followerShowNext = value_property(bool, "followerShowNext", followerViewChanged, True)
    followerShowBase = value_property(bool, "followerShowBase", followerViewChanged, True)
    followerShowTravels = value_property(bool, "followerShowTravels", followerViewChanged, False)
    followerLineScale = value_property(float, "followerLineScale", followerViewChanged, 0.7)
    followerTravelVisualRatio = value_property(float, "followerTravelVisualRatio", followerViewChanged,
                                               _PLATE_TRAVEL_VISUAL_RATIO)
    followerKeepCentred = value_property(bool, "followerKeepCentred", followerViewChanged, False)
    followerAttached = value_property(bool, "followerAttached", followerViewChanged, True)
    followerLayerAnchor = value_property(int, "followerLayerAnchor", followerViewChanged, -1)
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
    temperatureChartMini = value_property(QVariant, "temperatureChartMini", temperatureChartMiniChanged, {})
    temperatureChartFull = value_property(QVariant, "temperatureChartFull", temperatureChartFullChanged, {})
    temperatureChartLatest = value_property(QVariant, "temperatureChartLatest", temperatureChartLatestChanged, {})
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
    fileDownloadProgress = value_property(QVariant, "fileDownloadProgress", fileManagerChanged, "")
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

    @pyqtSlot()
    def fileDownloadCancel(self):
        if self._cancel_file_download is not None:
            self._cancel_file_download()

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
    # The heightmap range filter (a request): values
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
    # The T0-T9 cold-camera timing chain's QML side: the host mirrors
    # the trace gate, and the pane's first decoded frame lands here so
    # T9 shares the SAME monotonic origin as every Python stage.
    traceCameraTimingChanged = pyqtSignal()
    traceCameraTiming = value_property(bool, "traceCameraTiming", traceCameraTimingChanged, False)
    webcamStreamEnabled = value_property(bool, "webcamStreamEnabled", webcamStreamEnabledChanged, True)
    # The webcam decode throttle: the effective rate the renderer is
    # told to decode at, the bar's floor, and the selected camera's own
    # configured ceiling (Moonraker's target_fps from the webcam list).
    # The defaults hold until the first publish lands.
    cameraFps = value_property(float, "cameraFps", cameraFpsChanged, CAMERA_FPS_DEFAULT)
    cameraFpsMin = value_property(float, "cameraFpsMin", cameraFpsChanged, CAMERA_FPS_MIN)
    cameraFpsMax = value_property(float, "cameraFpsMax", cameraFpsChanged, CAMERA_FPS_FALLBACK_MAX)

    @pyqtSlot()
    def cameraFirstFrameRendered(self):
        from .CameraTiming import mark_once
        mark_once("T9", "first decoded frame")

    @pyqtSlot(result=int)
    def cameraPaneInstanceId(self):
        # The pane's process-wide diagnostic id: every pane instance
        # (one per machine model) draws from the SAME sequence, so
        # pane-side trace lines can never collide across models.
        from .CameraTiming import next_actor_id
        return next_actor_id()

    @pyqtSlot(int, str)
    def cameraPaneTrace(self, pane_id, event):
        # The QML side of the cold-start trace: applyCamera calls,
        # visibility start/stops and watchdog firings, labelled with
        # the pane's id and sanitised by the pane itself.
        from .CameraTiming import mark
        mark("T6-qml", "pane %d: %s" % (int(pane_id), str(event)))
    cameraRecovering = value_property(bool, "cameraRecovering", cameraRecoveringChanged, False)
    connectionDetail = value_property(str, "connectionDetail", connectionDetailChanged, "")
    sectionExpandedMap = value_property(QVariant, "sectionExpandedMap", sectionsChanged, {})
    sectionLayout = value_property(QVariant, "sectionLayout", sectionLayoutChanged, {})
    sectionHiddenMap = value_property(QVariant, "sectionHiddenMap", sectionLayoutChanged, {})

    @pyqtSlot()
    def refreshAll(self): self._data.refresh_all()
    @pyqtSlot()
    def refreshWebcams(self):
        # The nonce feeds a cache-busting query parameter so the live
        # stream itself reloads, not just the webcam list.
        if not self._webcam_stream_enabled:
            return
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
        # The sections map persists through the UI-state store — the
        # model's save no longer rewrites the whole map (4.3.0).
        self._ui_state.set_sections(self._sections)
        # Collapsing the mini's section retires its demand — the
        # thumbnail's ghost work stops while nothing shows it.
        if str(section) == "plateprogress" and not expanded:
            self._retire_surface(self._plate_surfaces["mini"])
        self._publish()

    @pyqtSlot(str, "QVariantList", "QVariantList")
    def setSectionLayout(self, pane, order, hidden):
        # Typed QVariantList params, never bare `object`: the QML
        # bridge silently refuses the untyped signature — the
        # harness's hook showed zero signal emissions from the
        # popup's commit path while the Python-side call worked.
        pane = str(pane)
        if pane not in PANE_NAMES:
            return
        normalised = normalise_section_layout(
            {**self._section_layout, pane: {"order": order, "hidden": hidden}})
        if normalised == self._section_layout:
            return  # idempotent: a re-bound popup must not rewrite the file
        self._section_layout = normalised
        self._ui_state.set_section_layout(self._section_layout)
        self._publish()

    @pyqtSlot(str, result="QVariant")
    def sectionLayoutFor(self, pane):
        """The effective (order, hidden) for one pane — the popup's
        rows always enumerate the full table, never the visible set."""
        pane = str(pane)
        if pane not in PANE_NAMES:
            return {}
        order, hidden = layout_for(self._section_layout, pane)
        return {"order": order, "hidden": hidden}

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
               for series in series_metadata(self._history, self._chart_config)):
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

    def _chart_config_key(self):
        return json.dumps(self._chart_config, sort_keys=True)

    def _chart_mini_value(self):
        """The compact preview (temperatureChartMini): a bounded
        payload rebuilt per history revision — its SIZE stops growing
        once the window outgrows the mini render budget (the build
        stays one allocation-light scan over the raw window)."""
        key = (self._history.revision, self._chart_config_key())
        if self._chart_mini is None or self._chart_mini_key != key:
            self._chart_mini = mini_chart_payload(self._history, self._chart_config)
            self._chart_mini_key = key
        return self._chart_mini

    def _chart_full_value(self):
        """The pop-over payload (temperatureChartFull): DORMANT while
        the pop-over is closed — the same empty object every publish,
        so the property never re-converts and the full-chart signal
        never fires on a raw history sample. Opening hydrates it;
        closing returns the path to dormancy on the very next publish."""
        if not self._chart_open:
            return DORMANT_CHART
        key = (self._history.revision, self._chart_config_key())
        if self._chart_full is None or self._chart_full_key != key:
            self._chart_full = chart_payload(self._history, self._chart_config)
            self._chart_full_key = key
        return self._chart_full

    def _chart_latest_value(self):
        """One scalar per series for the legends' live-value labels —
        never a full payload search."""
        if self._chart_latest is None or self._chart_latest_revision != self._history.revision:
            self._chart_latest = latest_values(self._history)
            self._chart_latest_revision = self._history.revision
        return self._chart_latest

    def _legend_value(self):
        """Legend metadata (identity, labels, colours, visibility, and
        the mini selection's row list): its own property so legend
        delegates only rebuild when the config or the sensor set
        actually changed, never at the sample cadence."""
        if self._legend_payload is None:
            self._legend_payload = {}
        key = json.dumps(self._chart_config, sort_keys=True) + "|" + "|".join(self._history.names())
        if self._legend_payload.get("_key") != key:
            metadata = series_metadata(self._history, self._chart_config)
            selected = set(mini_names([series["name"] for series in metadata],
                                      self._chart_config.get("visible")
                                      if isinstance(self._chart_config.get("visible"), dict) else {}))
            self._legend_payload = {
                "_key": key,
                "series": [{"name": series["name"], "label": series["label"],
                            "color": series["color"], "visible": series["visible"],
                            "primary": series["primary"]} for series in metadata],
                "miniSeries": [{"name": series["name"], "label": series["label"],
                                "color": series["color"]}
                               for series in metadata if series["name"] in selected],
                "showTargets": bool(self._chart_config.get("showTargets", True)),
                "showPower": bool(self._chart_config.get("showPower", True)),
                "palette": list(PALETTE),
            }
        return self._legend_payload

    @pyqtSlot(bool)
    def setChartOpen(self, opened):
        # The pop-over's hydration gate: the full chart payload
        # materialises only while the pop-over is open; closed, it
        # is the shared dormant object.
        opened = bool(opened)
        if opened == self._chart_open:
            return
        self._chart_open = opened
        self._chart_full = None  # hydration or dormancy lands on the next publish
        self._publish()

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

    def _migration_record(self):
        """The settings document's migration record via the facade;
        None in the harness's config-only double. Cached: the record
        lands once during hydration and only the dismiss slot mutates
        it — the heartbeat must not re-read the settings file."""
        if self._migration_record_read:
            return self._migration_record_cache
        self._migration_record_read = True
        if hasattr(self._store, "migration_record"):
            self._migration_record_cache = self._store.migration_record()
        return self._migration_record_cache

    @pyqtSlot()
    def dismissMigrationBanner(self):
        if hasattr(self._store, "set_migration_record"):
            self._store.set_migration_record({"bannerDismissed": True})
            # The cache mirrors the store's merge: the diagnostics row
            # still needs the status/backup fields under the flag.
            self._migration_record_cache = {**(self._migration_record_cache or {}),
                                            "bannerDismissed": True}
            self._publish()

    @pyqtSlot()
    def openMigrationBackupFolder(self):
        # The recipe's route: the folder that exists NOW — the config
        # directory moves between Cura versions and portable installs.
        QDesktopServices.openUrl(QUrl.fromLocalFile(Resources.getConfigStoragePath()))

    def _on_store_note(self, _kind, text):
        """The store's failure sink (A6): the console note line is
        the durable channel (the action status's precedence can hide
        a line, round-2 S7)."""
        console = getattr(self, "_console", None)
        if console is not None:
            console.note(text)
        else:
            self._store_notes.append(text)

    def _save_state(self):
        _store_write(self._store, {
            "whatsNewSeen": self._whats_new_seen,
            "controlsCollapsed": self._controls_collapsed,
            "controlsLocked": self._controls_locked,
            "infoCollapsed": self._info_collapsed,
            "statusCollapsed": self._status_collapsed,
            "consoleHeight": self._console_height,
            "followerView": {
                "showPrevious": self._follower_show_previous,
                "showNext": self._follower_show_next,
                "showBase": self._follower_show_base,
                "showTravels": self._follower_show_travels,
                "keepCentred": self._follower_keep_centred,
                "lineScale": self._follower_line_scale,
            },
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
        })
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

    @pyqtSlot(str)
    def restoreObject(self, name): self._controls.restore(name)

    @pyqtSlot(bool)
    def setFollowerPopoverOpen(self, popover_open):
        """The popover's open state: closed freezes the follower's
        payload keys on their last values, open resumes them (the live
        request — a closed surface must not re-wrap per poll). Closing
        also retires the surface's pending demand — no obsolete ghost
        or prefix work burns while nothing shows it."""
        popover_open = bool(popover_open)
        if popover_open == self._follower_popover_open:
            return
        self._follower_popover_open = popover_open
        if self._request_follower_popover_open is not None:
            self._request_follower_popover_open(popover_open)
        if not popover_open:
            self._retire_surface(self._plate_surfaces["popover"])
        self._publish()

    @pyqtSlot(bool)
    def setPickerPopoverOpen(self, popover_open):
        """The picker popover's open state, the same gate."""
        popover_open = bool(popover_open)
        if popover_open == self._picker_popover_open:
            return
        self._picker_popover_open = popover_open
        self._publish()
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
        # The migration notice's ordering hook (the UX ruling): the
        # failure toast waits for this moment, never races the overlay.
        self.whatsNewDismissed.emit()

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
            # settled state). The started-snapshot is recorded BEFORE
            # the publish so the identity gate cannot fire on it.
            self._improve_started_snapshot = self._print_state()
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
        # The heightmap range filter (a request): ONE
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

    @pyqtSlot(float)
    def setCameraFps(self, fps):
        """The FPS control's commit (the bar's drag or a wheel notch):
        the pane's renderer decodes at the new rate, the value persists
        per machine, and the stream itself is untouched."""
        self._camera.set_fps(fps)

    @pyqtSlot(bool)
    def setWebcamStreamEnabled(self, enabled):
        """The stream toggle (the live request): OFF really stops the
        stream — the bridge halts its upstream fetch, the published
        URL goes blank so the loader stops pulling, and the
        watchdog/refresh paths stand down. ON republishes the URL and
        bumps the nonce so the pane re-applies the stream."""
        if self._webcam_stream_enabled is bool(enabled):
            return
        self._webcam_stream_enabled = bool(enabled)
        if not self._webcam_stream_enabled:
            self._camera.suspend_stream()
        else:
            # The bridge's local listener died with the suspend —
            # rebuild it before the URL republishes, or the pane
            # pulls a dead loopback URL and freezes (the live
            # report: only a camera re-select revived it).
            self._camera.resume_stream()
        self._camera_refresh_nonce += 1
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

    # The follower view settings persist GLOBALLY (the live ruling),
    # not per printer: they ride the panel state document, not the
    # machine config.
    @pyqtSlot(bool)
    def setFollowerShowPrevious(self, show):
        if self._follower_show_previous is bool(show):
            return
        self._follower_show_previous = bool(show)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerShowNext(self, show):
        if self._follower_show_next is bool(show):
            return
        self._follower_show_next = bool(show)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerShowBase(self, show):
        if self._follower_show_base is bool(show):
            return
        self._follower_show_base = bool(show)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerShowTravels(self, show):
        if self._follower_show_travels is bool(show):
            return
        self._follower_show_travels = bool(show)
        self._save_state()
        self._publish()

    @pyqtSlot(float)
    def setFollowerLineScale(self, scale):
        try:
            scale = min(2.0, max(0.5, float(scale)))
        except (TypeError, ValueError):
            return
        if self._follower_line_scale == scale:
            return
        self._follower_line_scale = scale
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerKeepCentred(self, keep):
        if self._follower_keep_centred is bool(keep):
            return
        self._follower_keep_centred = bool(keep)
        self._save_state()
        self._publish()

    def _plate_anchor_request(self, anchor):
        """The coordinator's anchor seam (the confirm*/toggle*
        capability pattern): None rejoins the live layer, an index
        freezes the face on it."""
        if self._request_plate_anchor is not None:
            self._request_plate_anchor(anchor)

    def _plate_split_request(self, motions):
        """The coordinator's scrub seam: a motion count the frozen
        layer draws up to, None for the whole base."""
        if self._request_plate_split is not None:
            self._request_plate_split(motions)

    def _surface_for(self, surface):
        """The named surface, or a bare name mapped to it (the QML
        publishes "popover"/"mini" strings; the tests may hand the
        object)."""
        if isinstance(surface, _RenderSurface):
            return surface
        return self._plate_surfaces.get(surface)

    def _qt_layer(self, surface, payload, layer):
        """The layer's retained native-render object FOR ONE SURFACE
        : the mini and the popover hold
        separate PlateLayers, so neither's raster can ever be
        consumed by the other. Raster demand is the scheduler's —
        never this path's.

        The wrapper's identity is (surface, layer, print epoch):
        within ONE epoch a layer's payload is content-addressed and
        immutable — the decoded LRU reuses the same object per layer
        and the repair/hydration paths never replace a layer's
        geometry under a surviving wrapper — and the epoch boundary
        retires the wrappers wholesale, so a payload object can never
        be swapped under a live wrapper (the pinned invariant;
        test_a_layer_payload_is_immutable_within_a_print_epoch)."""
        if payload is None or layer < 0:
            return None
        cached = surface.layers.get(layer)
        if cached is not None:
            surface.layers.move_to_end(layer)
            return cached
        wrapped = PlateLayer(payload)
        wrapped.set_expected_key(surface.render_key())
        surface.layers[layer] = wrapped
        # The wrapper pins the payload against the decoded budget:
        # the LRU's eviction must not uncharge bytes the wrapper
        # keeps alive.
        if self._index_service is not None:
            self._index_service.pin_decoded(layer)
        while len(surface.layers) > 6:
            evicted, _ = surface.layers.popitem(last=False)
            # The bookkeeping must not outlive the wrapper: an
            # evicted layer's tokens go with it.
            surface.tokens.pop(evicted, None)
            if self._index_service is not None:
                self._index_service.unpin_decoded(evicted)
        return wrapped

    def _raster_hot(self, surface, layer):
        """The render key's verdict: a
        raster is usable only when its key matches the surface's
        CURRENT key — an old-view image never reads as current
        after a zoom/pan/resize."""
        wrapped = surface.layers.get(layer)
        return wrapped is not None and wrapped.rasterValid

    def _scrub_vector_for(self, popover):
        """The vector crosses into QML ONLY for the partial progress
        states: a full 100% seek — and the
        empty 0% — display through the raster alone, so the measured
        ~500 ms nested QVariant wrap never rides an ordinary seek.
        The first partial scrub activates it (one wrap per layer,
        lazily)."""
        if popover is None:
            return None
        layers = popover.get("layers") or {}
        current = layers.get("current")
        split = popover.get("split")
        motions = popover.get("motionTotal") or 0
        if current is None or split is None or motions <= 0:
            return None
        if split <= 0 or split >= motions:
            return None
        return current

    def _qt_window(self, surface, layers, anchor, method=None, split=None,
                   lookup_ms=None):
        """The prev/current/next window for ONE SURFACE: the
        wrappers are created lazily and
        the desired state — current first, then the ghosts — feeds
        the surface's scheduler. The anchor lives ON the surface:
        the mini validates against its live anchor, the popover
        against whichever layer it displays (frozen included). The
        SPLIT rides the desired state too: a partial layer's
        printed prefix is its own demand. `lookup_ms` is only the
        coordinator's cheap plate_progress() lookup; service worker time
        is measured separately and must never be inferred from it."""
        surface = self._surface_for(surface)
        if surface is None or not layers:
            return {}
        if not isinstance(anchor, int):
            anchor = 0
        if surface.anchor != anchor:
            surface.anchor = anchor
            surface.anchor_epoch += 1
        current = self._qt_layer(surface, layers.get("current"), anchor)
        self._trace("T6 payload obtained", {
            "surface": surface.name, "layer": anchor,
            "method": method or (layers.get("method") if isinstance(layers, dict) else None),
            "lookup_ms": round(lookup_ms, 1) if lookup_ms is not None else None})
        self._trace("T7/T8 layer obtained", {
            "surface": surface.name, "layer": anchor,
            "raster": "hot" if self._raster_hot(surface, anchor) else "miss"})
        window = {"prev": self._qt_layer(surface, layers.get("prev"), anchor - 1),
                  "current": current,
                  "next": self._qt_layer(surface, layers.get("next"), anchor + 1)}
        # The desired ghost state: a full
        # pair, never a single overwritable slot; each ghost renders
        # at most once per demand — the scheduler's hot-check skips
        # a ghost that is already rasterised or in flight.
        surface.desired = {
            "current": anchor,
            "ghosts": {role: layer
                       for role, layer in (("prev", anchor - 1), ("next", anchor + 1))
                       if layers.get(role) is not None and layer >= 0},
            "epoch": surface.anchor_epoch,
            "split": split,
        }
        # A running job whose layer left the window cancels at the
        # renderer's next segment boundary — the new current never
        # waits out a full obsolete render. A new demand re-arms the
        # persistent-failure latch.
        surface.job_failures = 0
        self._cancel_obsolete_job(surface)
        self._schedule_surface(surface)
        # The interaction raster follows the CONTENT state (the
        # window, the split, the toggles) — its warm background
        # update schedules here, never on the camera path.
        self._schedule_navigation(surface)
        return window

    def _prefix_wanted(self, surface, layer, split):
        """The partial layer's prefix demand: none yet, a backward
        move, or the live split has run a quarter of the layer past
        the rendered prefix — the tail's QML walk stays a cheap
        delta between prefix refreshes."""
        wrapped = surface.layers.get(layer)
        if wrapped is None or split is None or split <= 0:
            return False
        motions = wrapped.motions
        if split >= motions:
            return False
        # A prefix rendered for an old view/plot key is no prefix at all.
        # QML hides it; the scheduler must therefore request a replacement
        # even when the numeric split did not move.
        if not wrapped.prefixValid:
            return True
        have = wrapped.prefixSplit
        if have < 0:
            return True
        if split < have:
            return True
        # The refresh threshold rides the INCREMENTAL render: the
        # worker strokes only [have, split) over the committed
        # picture, so a refresh costs O(delta), not O(split) — the
        # threshold tightens to keep the canvas's tail walk (the
        # visible gap between refreshes) small.
        return split - have > max(100, int(motions * 0.03))

    def _schedule_surface(self, surface):
        """The bounded demand scheduler :
        ONE job in flight per surface; the current layer's demand
        always outranks the ghosts; a hot layer never creates work;
        the newest desired current supersedes an obsolete one —
        rapid slider movement through 100..104 starts at most one
        current job plus its ghosts, never one per visited layer."""
        if surface.job is not None:
            return  # the running job's completion re-schedules
        if surface.plot is None or not surface.view.get("width"):
            return  # no context yet — the demand waits for the feed
        desired = surface.desired
        if desired is None:
            return
        split = desired.get("split")
        # The demand queue, highest priority first: a partial
        # layer's printed PREFIX (the measured verdict — the QML
        # walk for the partial states costs ~900 ms at 500k), then
        # the full current (the grey base rides it), then the
        # ghosts.
        demand = []
        current = desired["current"]
        if self._prefix_wanted(surface, current, split):
            demand.append(("prefix", current, split))
        demand.append(("full", current, None))
        for role in ("prev", "next"):
            layer = desired["ghosts"].get(role)
            if layer is not None:
                demand.append(("full", layer, None))
        # The scheduler's depth: the not-yet-hot demands this pass
        # could burn work for (the trace and the rapid-drag report
        # read it).
        depth = 0
        for _kind, layer, _split in demand:
            if _kind == "prefix":
                depth += 1
            elif not self._raster_hot(surface, layer):
                depth += 1
        surface.stats["depth_max"] = max(surface.stats["depth_max"], depth)
        for kind, layer, prefix_split in demand:
            wrapped = surface.layers.get(layer)
            if wrapped is None:
                continue
            if kind == "prefix":
                if not self._prefix_wanted(surface, layer, split):
                    continue
            elif self._raster_hot(surface, layer):
                continue
            token = surface.tokens.get(layer, 0) + 1
            surface.tokens[layer] = token
            surface.render_count[layer] = surface.render_count.get(layer, 0) + 1
            generation = surface.generation
            surface.render_serial += 1
            serial = surface.render_serial
            epoch = surface.job_epoch
            self._trace("T9 raster start", {
                "surface": surface.name, "layer": layer, "queue": depth,
                "generation": generation, "token": token, "kind": kind,
                "split": prefix_split, "epoch": epoch, "serial": serial})
            plot = surface.plot
            view = dict(surface.view)
            key = surface.render_key()
            cancel = threading.Event()
            surface.job = {"layer": layer, "token": token,
                           "generation": generation, "state": "submitted",
                           "cancel": cancel, "epoch": epoch, "serial": serial,
                           "kind": kind, "split": prefix_split}
            ticket = (surface.name, layer, token, generation, key, kind,
                      prefix_split, epoch, serial)
            payload = wrapped._payload
            # The incremental render's base (the forward scrub's
            # refresh cost): the wrapper's committed prefix picture
            # and its boundary — the worker copies the image and
            # strokes only [previous_split, prefix_split). A stale
            # context falls back to the full walk: the picture bakes
            # the view transform, so only the SAME render key may
            # seed the copy — a zoom or pan between the commit and
            # this render would stroke the new view over old-scale
            # pixels (the out-of-scale ghost).
            previous_image = getattr(wrapped, "_prefix", None)
            previous_boundary = wrapped.prefixSplit
            if getattr(wrapped, "_prefix_key", None) != key:
                previous_image = None
                previous_boundary = 0

            def build(ticket=ticket, payload=payload, plot=plot, view=view,
                      surface=surface, layer=layer, generation=generation,
                      kind=kind, prefix_split=prefix_split, epoch=epoch,
                      serial=serial, cancel=cancel,
                      directory=self._raster_cache_dir,
                      bridge=self._raster_bridge,
                      previous_image=previous_image,
                      previous_boundary=previous_boundary):
                # Every job ends in exactly ONE terminal emit: the
                # success payload, a cancelled marker, or a failure
                # marker. A worker that throws can never wedge the
                # surface's job slot. The emits ride the teardown
                # guard — a bridge whose owner died mid-build drops
                # the job instead of aborting the pool thread.
                def emit(payload):
                    _bridge_emit(bridge, "done", payload, ticket)
                if not _bridge_emit(bridge, "started", ticket):
                    return
                try:
                    if cancel.is_set():
                        emit(("cancelled",))
                        return
                    stem = "r-%s-e%d-%d-g%d-s%d" % (
                        surface.name, epoch, layer, generation, serial)
                    if kind == "prefix":
                        image = render_layer_prefix(
                            payload, plot, view, prefix_split, cancel=cancel,
                            previous=previous_image,
                            previous_split=previous_boundary
                            if previous_boundary is not None else 0)
                        if cancel.is_set():
                            emit(("cancelled",))
                            return
                        url = png_file(image, directory, stem + "-p%d" % prefix_split)
                        emit(("prefix", image, url, prefix_split))
                        return
                    coloured, base, travels = render_layer_raster(
                        payload, plot, view, cancel=cancel)
                    if cancel.is_set():
                        emit(("cancelled",))
                        return
                    emit(("full", coloured, png_file(coloured, directory, stem + "-c"),
                          base, png_file(base, directory, stem + "-b"),
                          travels, png_file(travels, directory, stem + "-t")))
                except Exception as exc:
                    emit(("failed", str(exc)))
            QThreadPool.globalInstance().start(_RasterJob(build))
            return

    def _navigation_backing(self, surface):
        """The interaction raster's backing: 400% of the 100%-fit
        view, reduced when 4x would blow the safe single-buffer
        budget (the double-buffered peak holds two CPU images and
        two GPU textures — the fallback logs once and shrinks)."""
        width = int(surface.view.get("width") or 0)
        height = int(surface.view.get("height") or 0)
        if width <= 0 or height <= 0:
            return 4.0
        budget = 64 * 1024 * 1024  # one CPU buffer's safe share
        backing = min(4.0, (budget / (width * height * 4.0)) ** 0.5)
        if backing < 4.0:
            logging.getLogger("MoonrakerPrintFollower").warning(
                "navigation raster backing reduced to %.1fx for the "
                "%dx%d surface (memory safety)", backing, width, height)
        return max(1.0, backing)

    def _navigation_data_value(self, surface):
        """The face-eligible navigation URL: the retained raster
        reaches QML ONLY while it is READY for the current demand —
        its key matches the demand key. A stale raster (a demand that
        moved, a replacement still rendering, a failed replacement)
        reads "" and the exact scene serves the gesture."""
        demand = self._navigation_key(surface)
        if surface.nav["url"] and demand is not None \
                and surface.nav["key"] == demand:
            return surface.nav["url"]
        return ""

    def _navigation_key(self, surface):
        """The interaction raster's content key: the job epoch, the
        window's payload identity, the split, the toggles, the line
        style, the plot and the surface's dimensions. PAN and ZOOM
        are presentation transforms and never appear here — this is
        why both stay free while interacting."""
        desired = surface.desired
        if desired is None:
            return None
        window = []
        for layer in (desired["current"], desired["ghosts"].get("prev"),
                      desired["ghosts"].get("next")):
            wrapped = surface.layers.get(layer) if layer is not None else None
            window.append(id(wrapped._payload) if wrapped is not None else None)
        return (surface.name, surface.job_epoch, tuple(window),
                desired.get("split"),
                bool(getattr(self, "followerShowPrevious", True)),
                bool(getattr(self, "followerShowNext", True)),
                bool(getattr(self, "followerShowBase", True)),
                bool(getattr(self, "followerShowTravels", False)),
                round(float(surface.view.get("lineScale") or 0.7), 6),
                int(surface.view.get("width") or 0),
                int(surface.view.get("height") or 0),
                # The ZOOM rides the key (the live ruling): the grid
                # is baked at the width that presents as the canvas's
                # 1 px AT THIS ZOOM, so a zoom change re-bakes the
                # single flat raster. The pan stays a presentation
                # transform and never appears here.
                round(float(surface.view.get("scale") or 1.0), 6),
                round(float(self.bedMeshMachineWidth or 0.0), 6),
                round(float(self.bedMeshMachineDepth or 0.0), 6),
                tuple(sorted((k, round(float(v), 6))
                             for k, v in (surface.plot or {}).items())))

    def _schedule_navigation(self, surface):
        """The warm interaction raster's demand: ONE background job
        per surface (the live updates coalesce on the key), never on
        the camera path, and only for the popover — the mini does
        not carry this feature. A ready URL is what the face can
        switch to INSTANTLY on the first camera input."""
        if surface.name != "popover" or surface.nav["job"] is not None \
                or surface.plot is None:
            return
        key = self._navigation_key(surface)
        # The failed-key latch: a demand whose last attempt FAILED is
        # never retried while it stays identical (every publish would
        # re-arm it at render cost). Any demand change produces a new
        # key and re-arms; a success clears the latch.
        if key is None or key == surface.nav["key"] \
                or key == surface.nav.get("failed"):
            return
        desired = surface.desired
        window = {}
        for role, layer in (("current", desired["current"]),
                            ("prev", desired["ghosts"].get("prev")),
                            ("next", desired["ghosts"].get("next"))):
            wrapped = surface.layers.get(layer) if layer is not None else None
            window[role] = wrapped._payload if wrapped is not None else None
        if window["current"] is None:
            return
        backing = self._navigation_backing(surface)
        view = {"width": int(surface.view.get("width") or 0),
                "height": int(surface.view.get("height") or 0),
                "scale": 1.0,
                # The grid's adaptive width: the pen painted at
                # backing / zoom presents as the canvas's 1 px at
                # this zoom (the raster's camera transform scales
                # it back up — the live ruling). The dpr rides the
                # same coverage contract: the stroke floor presents
                # min(2/dpr, 1) logical px at this zoom.
                "zoom": float(surface.view.get("scale") or 1.0),
                "dpr": min(2.0, max(1.0, float(surface.view.get("dpr") or 1.0))),
                "lineScale": float(surface.view.get("lineScale") or 0.7),
                "travelVisualRatio": surface.view.get("travelVisualRatio"),
                "compact": False, "panX": 0.0, "panY": 0.0,
                "backing": backing,
                # The legend checkboxes are the scene's CONTENT: the
                # warm raster must mirror the exact view's toggles.
                "showPrevious": bool(getattr(self, "followerShowPrevious", True)),
                "showNext": bool(getattr(self, "followerShowNext", True)),
                "showBase": bool(getattr(self, "followerShowBase", True)),
                "showTravels": bool(getattr(self, "followerShowTravels", False)),
                # The bed's machine bounds: the grid rides the same
                # composite — the COMPLETE scene (the grid AND the
                # geometry) switches to the warm raster as one.
                "bedWidth": float(self.bedMeshMachineWidth or 0.0),
                "bedDepth": float(self.bedMeshMachineDepth or 0.0)}
        plot = dict(surface.plot)
        split = desired.get("split")
        epoch = surface.job_epoch
        surface.nav["serial"] += 1
        serial = surface.nav["serial"]
        cancel = threading.Event()
        surface.nav["cancel"] = cancel
        surface.nav["job"] = {"key": key, "cancel": cancel, "epoch": epoch,
                             "serial": serial}
        ticket = (surface.name, -1, 0, 0, key, "nav", split, epoch, serial)

        def build(ticket=ticket, window=window, plot=plot, view=view,
                  split=split, cancel=cancel, surface=surface,
                  serial=serial, epoch=epoch, key=key,
                  directory=self._raster_cache_dir,
                  bridge=self._raster_bridge):
            def emit(payload):
                _bridge_emit(bridge, "done", payload, ticket)
            try:
                if cancel.is_set():
                    emit(("cancelled",))
                    return
                image = render_navigation_layer(window, plot, view,
                                                split, cancel=cancel)
                if cancel.is_set():
                    emit(("cancelled",))
                    return
                url = png_file(image, directory,
                               "n-%s-e%d-s%d" % (surface.name, epoch, serial))
                if not url:
                    # An unpublished PNG is a FAILED render, never a
                    # successful one: the terminal's failure latch
                    # holds the demand until it moves (no hot-retry),
                    # and the stale ready raster stays ineligible for
                    # the failed demand.
                    logging.getLogger("MoonrakerPrintFollower").warning(
                        "navigation raster publication failed (%s, epoch %d, "
                        "serial %d)", surface.name, epoch, serial)
                    emit(("failed", "the navigation PNG could not publish"))
                    return
                emit(("nav", image, url, key))
            except Exception as exc:
                emit(("failed", str(exc)))
        QThreadPool.globalInstance().start(_RasterJob(build))

    def _nav_committed(self, images, ticket):
        """The interaction raster's commit: the epoch and the
        content key gate the double buffer — a superseded or stale
        generation's file dies on arrival, the ready URL is promoted
        atomically, and the retired buffer unlinks."""
        name, _layer, _token, _gen, key, _kind, _split, epoch, serial = ticket
        surface = self._plate_surfaces.get(name)
        if surface is None:
            return
        job = surface.nav["job"]
        if images and isinstance(images, tuple) and images[0] in ("failed", "cancelled"):
            # The terminal must belong to the ACTIVE job — the serial
            # is the job's own identity. A stale cancellation from a
            # superseded job (A cancelled, B started, A's terminal
            # arrives) must never clear the slot B owns (the review's
            # finding).
            if job is not None and job["serial"] == serial \
                    and surface.job_epoch == epoch:
                surface.nav["job"] = None
                # The ACTIVE job ended without a picture: its key
                # latches so the identical demand never hot-retries,
                # and a demand that has SINCE CHANGED reschedules.
                surface.nav["failed"] = job["key"]
                self._schedule_navigation(surface)
            return
        if not images or not isinstance(images, tuple) or images[0] != "nav":
            return
        _kind, _image, url, painted_key = images
        if job is None or surface.job_epoch != epoch or job["serial"] != serial \
                or job["key"] != key or painted_key != key:
            self._unlink_asset_files(images)
            return
        # The demand gate (the review's stale-promotion finding): an
        # obsolete-but-internally-consistent job must not promote —
        # the content it painted is no longer what the surface needs,
        # even though its own ticket and key still match themselves.
        demand = self._navigation_key(surface)
        if demand is None or demand != key:
            self._unlink_asset_files(images)
            surface.nav["job"] = None
            self._schedule_navigation(surface)
            return
        surface.nav["job"] = None
        surface.nav["failed"] = None
        old = surface.nav["url"]
        surface.nav["url"] = url
        surface.nav["key"] = key
        if old and old != url:
            try:
                os.unlink(QUrl(old).toLocalFile())
            except OSError:
                pass
        self._publish()

    @staticmethod
    def _unlink_asset_files(images):
        """A discarded job's files are dead on arrival — remove
        them now, never wait for the generic pruning."""
        if not isinstance(images, tuple) or not images:
            return
        if images[0] == "full":
            for url in images[2], images[4], images[6]:
                if not url or not url.startswith("file://"):
                    continue
                try:
                    os.unlink(QUrl(url).toLocalFile())
                except OSError:
                    pass
        elif images[0] == "prefix":
            url = images[2]
            if url and url.startswith("file://"):
                try:
                    os.unlink(QUrl(url).toLocalFile())
                except OSError:
                    pass
        elif images[0] == "nav":
            url = images[2]
            if url and url.startswith("file://"):
                try:
                    os.unlink(QUrl(url).toLocalFile())
                except OSError:
                    pass

    def _cleanup_raster_dir(self):
        """The instance's own raster directory goes with the model —
        never another model's assets."""
        try:
            import shutil
            shutil.rmtree(self._raster_cache_dir, ignore_errors=True)
        except OSError:
            pass

    def memory_accounting(self):
        """The model's memory story in one view: the service's RAM
        tiers (packed, decoded, pinned), the wrappers' pixel bytes,
        and the raster directory's disk bytes. The lifecycle frees
        them on their own paths — wrappers unpin on eviction and
        print change, the directory prunes on commit and print
        change, and destruction rmtrees it."""
        packed = decoded = pinned = 0
        if self._index_service is not None:
            packed = self._index_service.packed_bytes()
            decoded = self._index_service.decoded_resident_bytes()
            pinned = self._index_service.pinned_decoded_bytes()
        wrappers = 0
        wrapper_images = 0
        for surface in self._plate_surfaces.values():
            wrappers += len(surface.layers)
            for wrapped in surface.layers.values():
                wrapper_images += wrapped.memory_bytes()
        dir_files = 0
        dir_bytes = 0
        try:
            for name in os.listdir(self._raster_cache_dir):
                try:
                    dir_bytes += os.stat(os.path.join(self._raster_cache_dir, name)).st_size
                    dir_files += 1
                except OSError:
                    pass
        except OSError:
            pass
        backing = 1.0
        for surface in self._plate_surfaces.values():
            backing = max(backing, float(surface.view.get("dpr") or 1.0))
        return {"packedBytes": packed, "decodedBytes": decoded,
                "pinnedDecodedBytes": pinned,
                "wrapperCount": wrappers, "wrapperImageBytes": wrapper_images,
                "rasterDirFiles": dir_files, "rasterDirBytes": dir_bytes,
                "backingScale": backing}

    def _referenced_raster_files(self):
        """The asset files the live wrappers still display, plus the
        retained navigation raster: the prune must never unlink a URL
        a wrapper or the warm-scene face still reads. A retired
        navigation asset (the url cleared) drops out of the set and
        the next prune collects it."""
        referenced = set()
        for surface in self._plate_surfaces.values():
            nav_url = surface.nav.get("url") if surface.nav else None
            if nav_url:
                referenced.add(QUrl(nav_url).toLocalFile())
            retained = getattr(surface, "retained_prefix", "")
            if retained:
                referenced.add(QUrl(retained).toLocalFile())
            for wrapped in surface.layers.values():
                for url in (wrapped.rasterData, wrapped.baseData,
                            wrapped.travelData, wrapped.prefixData):
                    if url:
                        referenced.add(QUrl(url).toLocalFile())
        return referenced

    def _prune_raster_cache(self, keep=64):
        """The raster cache's bound (the file-URL transport): the
        newest `keep` PNGs survive, a file a live wrapper still
        displays ALWAYS survives (the old newest-N sweep could
        unlink the picture on screen), and an in-flight publication's
        temp is never touched. Scoped to THIS model's directory, so
        another printer's assets are never touched."""
        try:
            referenced = self._referenced_raster_files()
            entries = []
            for name in os.listdir(self._raster_cache_dir):
                path = os.path.join(self._raster_cache_dir, name)
                if path in referenced or ".tmp-" in name:
                    continue
                try:
                    entries.append((os.stat(path).st_mtime, path))
                except OSError:
                    continue
            for _mtime, path in sorted(entries)[:-keep] if keep else entries:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        except OSError:
            pass

    @staticmethod
    def _raster_job_matches(job, layer, token, generation, epoch, serial):
        """Exact identity of one submitted raster job.

        Tokens can restart after a surface retire/reopen, while generation
        and print epoch may stay unchanged. The monotonic serial is what
        makes those otherwise-identical jobs collision-proof.
        """
        return bool(job is not None
                    and job["layer"] == layer
                    and job["token"] == token
                    and job["generation"] == generation
                    and job["epoch"] == epoch
                    and job["serial"] == serial)

    @pyqtSlot(object)
    def _raster_started(self, ticket):
        """The worker's first line: a job the demand replaced while
        still queued is countable as superseded-before-start."""
        name, layer, token, generation, _key, _kind, _split, epoch, serial = ticket
        surface = self._plate_surfaces.get(name)
        if surface is None:
            return
        job = surface.job
        if self._raster_job_matches(job, layer, token, generation, epoch, serial):
            job["state"] = "running"
            surface.stats["started"] += 1
            self._trace("T10 raster running", {"surface": name, "layer": layer})

    def _unpin_surface(self, surface):
        """Release the surface's wrapper pins: the decoded budget
        uncharges the payloads only when the wrappers actually go
        (a retire keeps the wrappers hot, so it does NOT unpin)."""
        if self._index_service is None:
            return
        for layer in list(surface.layers.keys()):
            self._index_service.unpin_decoded(layer)

    def _release_all_pins(self):
        """The model's death releases every wrapper pin: the index
        service outlives the monitor (the follower owns it)."""
        if self._index_service is None:
            return
        for surface in self._plate_surfaces.values():
            self._unpin_surface(surface)

    def _retire_surface(self, surface):
        """A surface whose QML consumer has gone retires its
        demand: the running/queued job cancels cooperatively, the
        desired state goes, and the next publish rebuilds it from
        the payload. The hot rasters stay cached — only the
        no-longer-needed work stops."""
        if surface.job is not None:
            surface.job["cancel"].set()
            surface.stats["superseded"] += 1
        surface.desired = None
        surface.job = None
        surface.tokens.clear()
        self._retire_navigation(surface)

    def _retire_navigation(self, surface):
        """The navigation raster's retirement (the review's coherent
        lifecycle): the in-flight update cancels, the job slot frees,
        the content key and the ready URL invalidate, the failure
        latch resets, and the retained asset drops into the prune's
        reach. Every lifecycle that ends a surface's or a print's
        ownership — the popover close, the print switch, the model's
        destruction — runs exactly this, so no path can leave a
        stale slot the new print cannot schedule through."""
        if surface.nav["job"] is not None:
            surface.nav["cancel"].set()
            surface.nav["job"] = None
        surface.nav["key"] = None
        surface.nav["url"] = ""
        # The failure latch resets with the lifecycle: a reopened
        # popover (or a new print) retries a demand whose earlier
        # failure may have been transient (a payload since rebuilt).
        surface.nav["failed"] = None

    def _cancel_obsolete_job(self, surface):
        """The running job no longer matches the desired demand —
        the anchor moved, the demand was replaced, or a prefix's
        requested split changed: cancel it at the renderer's next
        segment boundary so the new current never waits out a full
        obsolete render."""
        job = surface.job
        if job is None:
            return
        desired = surface.desired
        if desired is None:
            job["cancel"].set()
            return
        layer = job["layer"]
        if job.get("split") is not None:
            # A prefix render only ever serves the desired current:
            # it becomes obsolete when the current moves on (a ghost
            # wants a full, never a prefix) or when the split moves
            # BACKWARD under it — the painted interval would exceed
            # the demand. A forward advance keeps the render: the
            # prefix at P still owns [0..P] of the newer demand, and
            # the tail covers [P..Q] (the review's scrub policy —
            # cancelling useful work fed the disappearance).
            if layer != desired["current"] \
                    or desired.get("split") is None \
                    or desired.get("split") < job["split"]:
                job["cancel"].set()
        elif layer != desired["current"] and layer not in desired["ghosts"].values():
            # Submitted or running: the flag stops a queued job at
            # its pre-render check and a running one at the next
            # segment boundary.
            job["cancel"].set()

    @pyqtSlot(object, object)
    def _trace(self, stage, extra=None):
        """The seek timeline, disabled by default:
        MOONRAKER_FOLLOWER_SEEK_TRACE=1 (or the config's seek_trace)
        records each stage with its wall-clock offset from the
        seek's entry.

        The measurable stages: T1 the debounced slider commit,
        carrying the debounce measured from the raw slider tick
        (the slider reports it), T6 the payload's arrival (its
        method says which index path served it, the coordinator's
        decode duration rides beside it), T7/T8 the
        PlateLayer obtained with the raster hot/miss verdict, T9
        the job's demand with the queue depth, generation and
        token, T10 the worker's first line, T11 the owner-thread
        commit with the scheduler's counters, T12 a context
        commit, T13 the publish that hands the committed picture
        to the scene — the composition beyond is the engine's own
        and is not measurable from the model's side."""
        if not self._seek_trace_enabled and not (self._config() is not None and self._config().seek_trace):
            return
        if stage == "T1 seek entry":
            self._seek_trace = []
            self._seek_start = time.monotonic()
        if not self._seek_trace and stage != "T1 seek entry":
            return
        entry = {"stage": stage, "ms": (time.monotonic() - self._seek_start) * 1000}
        if extra:
            entry.update(extra)
        self._seek_trace.append(entry)

    @pyqtSlot(object, object)
    def _raster_committed(self, images, ticket):
        """The owner-thread commit: validate the print epoch, the
        generation, the layer's token and the retained identity,
        hand the images to the wrapper, and let the scheduler take
        the next demand. Only an EXACT ticket match may clear the
        active job — a stale completion can never clear an
        unrelated submitted one."""
        name, layer, token, generation, key, kind, prefix_split, epoch, serial = ticket
        surface = self._plate_surfaces.get(name)
        if surface is None:
            return
        if kind == "nav":
            # The navigation raster's own commit: the exact scene's
            # job slot and counters never see these tickets.
            self._nav_committed(images, ticket)
            return
        exact_job = self._raster_job_matches(
            surface.job, layer, token, generation, epoch, serial)
        # The terminal kinds arrive without rendered assets: a
        # cancelled job stops where it was told, a failed one
        # reports the exception.
        if kind == "cancelled" or (images and isinstance(images, tuple)
                                   and images[0] == "cancelled"):
            if not exact_job:
                # A retired/replaced job may finish cancellation after a
                # same-layer token has been reused. It is stale terminal
                # noise, not a cancellation of the active job.
                surface.stats["discarded"] += 1
                self._schedule_surface(surface)
                return
            surface.stats["cancelled"] += 1
            surface.job = None
            self._trace("T11 raster cancelled", {"surface": name, "layer": layer})
            self._schedule_surface(surface)
            return
        if images and isinstance(images, tuple) and images[0] == "failed":
            if not exact_job:
                # Stale failures must not poison the current job's
                # persistent-failure latch. Serial identity applies to
                # every terminal path, not only successful commits.
                surface.stats["discarded"] += 1
                self._schedule_surface(surface)
                return
            surface.stats["failed"] += 1
            surface.job_failures = getattr(surface, "job_failures", 0) + 1
            logging.getLogger("MoonrakerPrintFollower").warning(
                "raster worker failed: %s", images[1])
            surface.job = None
            self._trace("T11 raster failed", {"surface": name, "layer": layer,
                                              "error": images[1][:120]})
            if surface.job_failures >= 5:
                # A persistently failing render must not retry every
                # cycle; the demand retires and the next payload's
                # change re-arms it.
                surface.desired = None
                self._publish()
                return
            self._schedule_surface(surface)
            return
        # Only the EXACT ticket clears or commits against the active job.
        # A retired surface can restart token numbering at one, so
        # layer/token/generation/epoch without serial is insufficient.
        if exact_job:
            surface.job = None
        if not exact_job or epoch != surface.job_epoch or generation != surface.generation \
                or surface.tokens.get(layer) != token \
                or surface.layers.get(layer) is None:
            surface.stats["discarded"] += 1
            self._unlink_asset_files(images)
            self._schedule_surface(surface)
            return
        wrapped = surface.layers[layer]
        if kind == "prefix":
            _kind, prefix, prefix_data, prefix_split = images
            desired = surface.desired
            if desired is None or layer != desired["current"] \
                    or desired.get("split") is None \
                    or prefix_split > desired.get("split"):
                # The demand moved under this render: the painted
                # interval is beyond the requested one (a backward
                # move) or the layer left the current slot — the
                # prefix must never supersede the newer demand's
                # picture. A FORWARD advance keeps the render: the
                # prefix at P still owns [0..P] of the newer demand,
                # and the tail covers [P..Q] (the review's scrub
                # policy — discarding useful work fed the
                # disappearance).
                surface.stats["discarded"] += 1
                self._unlink_asset_files(images)
                self._schedule_surface(surface)
                return
            wrapped.set_prefix(prefix, prefix_data, prefix_split, key)
            # The face's atomic handover retains the PREVIOUS prefix's
            # pixels until the new composition is jointly present — the
            # prune must protect that file too (one URL, replaced each
            # commit, cleared on the surface's retirement).
            if wrapped.prefixData:
                surface.retained_prefix = wrapped.prefixData
            if surface.tokens.get(layer) == token:
                surface.tokens.pop(layer, None)
            surface.stats["committed"] += 1
            self._trace("T11 raster committed", {
                "surface": name, "layer": layer, "kind": "prefix",
                "split": prefix_split})
            self._prune_raster_cache()
            self._schedule_surface(surface)
            self._publish()
            self._trace("T13 raster published", {
                "surface": name, "kind": "prefix"})
            return
        _kind, coloured, coloured_data, base, base_data, travels, travel_data = images
        wrapped.set_raster(coloured, key, coloured_data)
        wrapped.set_base(base, key, base_data)
        wrapped.set_travels(travels, key, travel_data)
        if surface.tokens.get(layer) == token:
            surface.tokens.pop(layer, None)
        desired = surface.desired
        if desired is not None and (layer == desired["current"]
                                    or layer in desired["ghosts"].values()):
            surface.stats["committed"] += 1
        else:
            surface.stats["discarded"] += 1
        self._trace("T11 raster committed", {
            "surface": name, "layer": layer,
            "started": surface.stats["started"],
            "committed": surface.stats["committed"],
            "superseded": surface.stats["superseded"],
            "discarded": surface.stats["discarded"],
            "depth_max": surface.stats["depth_max"]})
        self._prune_raster_cache()
        self._schedule_surface(surface)
        self._publish()
        self._trace("T13 raster published", {
            "surface": name, "kind": "full"})

    def _arm_context_flush(self, surface):
        """One zero-tick flush per burst of staged context changes
        : the plot+view pair a transition
        publishes coalesces into ONE generation and ONE wave."""
        if surface.stage["armed"]:
            return
        surface.stage["armed"] = True
        QTimer.singleShot(0, lambda s=surface: self._flush_surface_context(s))

    def _flush_surface_context(self, surface):
        """The staged context commits: one generation per settled
        burst, and only the visible current + ghosts re-raster —
        historical LRU entries stay stale and re-render lazily when
        revisited."""
        surface.stage["armed"] = False
        staged_plot = surface.stage["plot"]
        staged_view = surface.stage["view"]
        if staged_plot is None and staged_view is None:
            return
        plot = staged_plot if staged_plot is not None else surface.plot
        view = staged_view if staged_view is not None else surface.view
        if plot == surface.plot and view == surface.view:
            surface.stage["plot"] = None
            surface.stage["view"] = None
            return
        surface.plot = plot
        surface.view = view
        surface.generation += 1
        surface.stage["plot"] = None
        surface.stage["view"] = None
        key = surface.render_key()
        # The retained wrappers' rasters read invalid at the new key
        #  but STAY cached — only the
        # visible window re-rasters.
        for wrapped in surface.layers.values():
            wrapped.set_expected_key(key)
        self._trace("T12 context committed", {"surface": surface.name,
                                              "generation": surface.generation})
        self._schedule_surface(surface)

    @pyqtSlot(str, float, float, int, int, bool, float, float)
    def setFollowerView(self, surface, scale, lineScale, width, height, compact,
                        panX, panY, dpr=1.0):
        """The raster's view inputs for ONE SURFACE . An
        exact repeat is a no-op ; the
        plot+view pair coalesces into one flush. The DEVICE-PIXEL
        backing rides the view: the worker paints at the device
        resolution (bounded supersampling) and the scene-graph
        samples the raster down to the logical face — a DPR-2
        screen never enlarges a 1x toolpath raster."""
        surface = self._surface_for(surface)
        if surface is None:
            return
        view = {"scale": float(scale), "lineScale": float(lineScale),
                "travelVisualRatio": _PLATE_TRAVEL_VISUAL_RATIO,
                "width": int(width), "height": int(height),
                "compact": bool(compact),
                "panX": float(panX), "panY": float(panY),
                "dpr": min(2.0, max(1.0, float(dpr)))}
        # Idempotence compares against the EFFECTIVE value — the
        # staged one when a burst is pending: A -> B -> A before
        # the flush must end at A, never commit the intermediate B.
        effective = surface.stage["view"]
        if effective is None:
            effective = surface.view
        if view == effective:
            return
        surface.stage["view"] = view
        self._arm_context_flush(surface)

    @pyqtSlot(str, float, float, float, float, float, float)
    def setFollowerPlot(self, surface, offsetX, offsetY, sx, sy, bedXMin, bedYMax):
        """The bed plot for ONE SURFACE (fed on the canvas's
        re-fit): the native renderer uses the SAME mapping the
        face's painters did, so the blit lands the identical
        picture. Staged like the view — an exact repeat is a no-op,
        and a plot+view pair flushes once."""
        surface = self._surface_for(surface)
        if surface is None:
            return
        plot = {"offsetX": float(offsetX), "offsetY": float(offsetY),
                "sx": float(sx), "sy": float(sy),
                "bedXMin": float(bedXMin), "bedYMax": float(bedYMax)}
        effective = surface.stage["plot"]
        if effective is None:
            effective = surface.plot
        if plot == effective:
            return
        surface.stage["plot"] = plot
        self._arm_context_flush(surface)

    def _observe_follower_job(self, job):
        """A new print re-attaches the follower: the frozen layer
        belonged to the file that was printing."""
        if job != self._plate_qt_job:
            # The render caches belong to that file too: a new
            # print's geometry must never answer with the old job's
            # images . The
            # reset runs per surface, scheduler state included —
            # no residue across jobs. The PRINT epoch bumps so an
            # in-flight worker from the previous print can never
            # match a new print's ticket, whatever its layer, token
            # and generation.
            self._plate_qt_job = job
            self._plate_job_epoch += 1
            for surface in self._plate_surfaces.values():
                surface.job_epoch = self._plate_job_epoch
                if surface.job is not None:
                    surface.job["cancel"].set()
                self._unpin_surface(surface)
                surface.layers.clear()
                surface.tokens.clear()
                surface.job = None
                surface.desired = None
                surface.render_count.clear()
                # The navigation state belongs to the print too: the
                # epoch gate would reject the old job's terminal but
                # nothing else freed its slot — the new print must be
                # able to schedule its own warm raster immediately.
                self._retire_navigation(surface)
                # The face's retained previous prefix belongs to the
                # old print as well: drop the prune protection so the
                # file can be collected.
                surface.retained_prefix = ""
        if job == self._follower_job:
            return
        self._follower_job = job
        if not self._follower_attached:
            self._follower_attached = True
            self._follower_layer_anchor = -1
            self._follower_layer_split = None
            self._plate_anchor_request(None)
            self._plate_split_request(None)

    @pyqtSlot(bool)
    def setFollowerAttached(self, attached):
        """Attach/detach: detached freezes the anchor on the layer the
        face is showing — and seeds the scrub with the live split, so
        the frozen layer keeps drawing exactly where the print stood
        (the live report: a detach that changed nothing read as dead).
        Attached rejoins the live print and abandons the scrub. A
        refused detach (no layer to hold) leaves the follower attached
        rather than publishing a state the coordinator cannot serve."""
        attached = bool(attached)
        if attached == self._follower_attached:
            return
        self._follower_attached = attached
        if attached:
            self._follower_layer_anchor = -1
            self._follower_layer_split = None
            self._plate_anchor_request(None)
            self._plate_split_request(None)
        else:
            frozen = self._follower_layer_anchor
            if frozen < 0:
                # Nothing frozen yet: the LIVE layer is where the face
                # stands, so that is what the detach holds on to.
                frozen = _coerce_anchor(
                    self._values.get("plateProgressAnchor", -1))
            if frozen >= 0:
                self._follower_layer_anchor = frozen
                seed = self._values.get("plateSplit")
                if seed is not None and frozen == self._values.get("plateProgressAnchor"):
                    # Detaching FROM the live layer: continue the fill
                    # where it stood. A seek to another layer carries
                    # no split — the scrub belongs to the live layer.
                    self._follower_layer_split = int(seed)
                else:
                    self._follower_layer_split = None
                self._plate_anchor_request(frozen)
                self._plate_split_request(self._follower_layer_split)
            else:
                # No layer to freeze (no index): a detach that would
                # change nothing is refused rather than published as a
                # state the coordinator cannot serve.
                self._follower_attached = True
                return
        self._publish()

    @pyqtSlot()
    def seekAnchorTicked(self):
        """The slider's RAW tick (the debounce's start): the seek's
        perceived latency includes the 80 ms wait before the commit,
        so the trace records it and T1 carries the debounce."""
        self._seek_tick_mono = time.monotonic()

    @pyqtSlot(int)
    def setFollowerLayerAnchor(self, layer):
        extra = {"layer": layer}
        tick = self._seek_tick_mono
        self._seek_tick_mono = None
        if tick is not None:
            # Only a recent tick is this commit's debounce (a stray
            # programmatic seek carries no tick).
            elapsed = (time.monotonic() - tick) * 1000.0
            if elapsed <= 5000.0:
                extra["debounce_ms"] = round(elapsed, 1)
        self._trace("T1 seek entry", extra)
        """The layer slider's committed value (the debounced request):
        a manual layer IS a detach — the face cannot follow the print
        and hold another layer at once. A seek lands the layer at
        100% — the scrub starts at the whole layer (the live
        request), so the FULL marker rides the split seam."""
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            return
        if layer < 0:
            return
        if self._follower_attached:
            self._follower_attached = False
        if self._follower_layer_anchor == layer and self._follower_layer_split == -1:
            self._publish()
            return
        self._follower_layer_anchor = layer
        self._follower_layer_split = -1
        self._plate_anchor_request(layer)
        self._plate_split_request(-1)
        self._publish()

    @pyqtSlot(int)
    def setFollowerLayerProgress(self, motions):
        """The progress slider's committed value (the debounced
        request): the scrub is a within-layer seek. From the LIVE
        layer it is itself the detach — the layer freezes where the
        print stood and the fill rides the scrubbed boundary."""
        try:
            motions = int(motions)
        except (TypeError, ValueError):
            return
        total = int(self._values.get("plateLayerMotionCount", 0) or 0)
        motions = max(0, min(motions, total))
        if self._follower_attached:
            frozen = _coerce_anchor(
                self._values.get("plateProgressAnchor", -1))
            if frozen < 0:
                return
            self._follower_attached = False
            self._follower_layer_anchor = frozen
            self._plate_anchor_request(frozen)
        if self._follower_layer_split == motions:
            self._publish()
            return
        self._follower_layer_split = motions
        self._plate_split_request(motions)
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
