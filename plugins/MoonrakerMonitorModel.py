"""One Cura Qt model: declarations and composition, not an inheritance stack."""
from __future__ import annotations
import json
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
from .MonitorFormatting import core_values, endstop_values, peripheral_values
from dataclasses import replace

from .PrinterConfig import normalise_temperature_chart
from .MonitorTemperatureHistory import TemperatureHistory, chart_payload
import time
from .MonitorTuning import MonitorTuning
from .ToolheadController import ToolheadController


# The monitor's panel state lives in a plugin-owned JSON file next to
# cura.cfg. Uranium's preference store is not used: it drops reads and
# writes on unregistered keys depending on version, and only persists on
# Cura's own save cycle, so the state has proven unreliable there. The
# file name predates the extra fields and stays for continuity.
SECTIONS_FILE_NAME = "moonraker_print_follower_sections.json"


def _sections_path() -> str:
    return Resources.getStoragePath(Resources.Preferences, SECTIONS_FILE_NAME)


def _state_bool(value) -> bool:
    """Coerce a stored flag; string values from hand-edited or older files
    must not hydrate inverted (bool('false') is True)."""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return bool(value)


# The shared chart-config normaliser (PrinterConfig owns the per-printer
# record; the legacy global JSON still feeds it during migration).
_chart_state = normalise_temperature_chart


def _read_state() -> dict:
    """The persisted panel state: collapsed sections, the control-pane
    collapse and the lock-all toggle. The first shipped format was a flat
    section map, which is migrated to the current shape on read."""
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
                "temperatureChart": _chart_state(decoded.get("temperatureChart")),
            }
    except Exception:
        pass
    return {"sections": {}, "controlsCollapsed": False, "controlsLocked": False,
            "infoCollapsed": False, "statusCollapsed": False,
            "temperatureChart": _chart_state({})}


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
    cameraRefreshChanged = pyqtSignal()

    _SIGNAL_KEYS = (
        ("monitorChanged", ("monitorState", "monitorFilename", "monitorProgress", "monitorLayer", "monitorLayerProgress",
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
        ("sectionsChanged", ("sectionExpandedMap",)),
        ("showProbePointsChanged", ("showProbePoints",)),
        ("cameraRefreshChanged", ("cameraRefreshNonce",)),
        ("consoleChanged", ("consoleHistory", "consoleLines", "consoleDropped", "consolePending", "consoleStatus")),
        ("typedControlsChanged", ("temperaturePresetItems", "pwmOutputItems", "bedMeshAvailable", "bedMeshProfile",
                                  "bedMeshProfileNames", "bedMeshRows", "bedMeshColumns", "bedMeshValues", "bedMeshMinimum",
                                  "bedMeshMaximum", "bedMeshRange", "bedMeshXMin", "bedMeshXMax", "bedMeshYMin", "bedMeshYMax",
                                  "bedMeshRangeText", "bedMeshPreviewVisible")),
    )

    def __init__(self, output_controller, number_of_extruders, *, client, print_state, config, apply_config, bed_mesh,
                 request_load=None, request_monitor_download=None):
        super().__init__(output_controller, number_of_extruders)
        self._client, self._print_state, self._config, self._apply_config, self._mesh = \
            client, print_state, config, apply_config, bed_mesh
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
        self._camera_refresh_nonce = 0
        self._sections = state["sections"]
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
        self._console = ConsoleController(self._data, self._commands, config, apply_config, self)
        for signal in (self._data.changed, self._commands.changed, self._controls.changed, self._camera.changed,
                       self._toolhead.changed, self._console.changed, bed_mesh.changed):
            signal.connect(self._publish)
        # The history feeds once per auxiliary reply, not per publish
        # (per-publish feeding duplicated samples and halved the window);
        # a session invalidation restarts the window so the previous
        # printer's curves never bleed into the next one.
        self._data.auxiliaryChanged.connect(self._on_auxiliary)
        self._data.consoleStoreChanged.connect(self._on_console_store)
        self._data.invalidated.connect(self._on_invalidated)
        self._data.set_active(True)
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
        self._publish()

    def setMonitoringActive(self, active): self._data.set_active(active)

    def _publish(self):
        previous = self._values
        snapshot = self._print_state()
        values = core_values(self._data.snapshot, snapshot, self._client.connected)
        values.update(peripheral_values(self._data.snapshot))
        values.update(endstop_values(self._data.snapshot, self._client.connected))
        values.update(self._controls.values)
        values.update(self._camera.values)
        values.update(self._toolhead.values)
        values.update(self._console.values)
        commands, mesh = self._commands, self._mesh.snapshot
        values.update(printActive=commands.print_active, canPausePrint=commands.state == "printing" and not commands.busy,
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

    monitorState = value_property(str, "monitorState", monitorChanged, "Not connected")
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
    consolePending = value_property(int, "consolePending", consoleChanged, 0)
    consoleStatus = value_property(str, "consoleStatus", consoleChanged, "")
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
    @pyqtSlot(float)
    def setJogDistance(self, distance): self._toolhead.set_distance(distance)
    @pyqtSlot(float)
    def setExtrudeDistance(self, distance): self._toolhead.set_extrude_distance(distance)
    @pyqtSlot(float)
    def setExtrudeSpeed(self, speed): self._toolhead.set_extrude_speed(speed)
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
