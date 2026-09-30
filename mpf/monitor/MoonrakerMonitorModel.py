"""One Cura Qt model: declarations and composition, not an inheritance stack."""
from __future__ import annotations

import time
from collections.abc import Mapping
from PyQt6.QtCore import QLocale, QTimer, QUrl, QVariant, pyqtProperty, pyqtSignal, pyqtSlot
from UM.Resources import Resources
from UM.Logger import Logger
from PyQt6.QtGui import QDesktopServices
from cura.PrinterOutput.Models.PrinterOutputModel import PrinterOutputModel
from .console.ConsoleController import ConsoleController
from .temperature.TemperaturePresentation import TemperaturePresentation


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


from .camera.CameraRecovery import CameraRecovery
from .camera.MonitorCamera import MonitorCamera
from ..plate.PlateQt import _PLATE_TRAVEL_VISUAL_RATIO
from ..plate.PlateRenderController import PlateRenderController, SceneInputs
from .controls.MonitorCommands import MonitorCommands
from .controls.MonitorControls import MonitorControls, _exclude_status
from .MonitorData import MonitorData
from .MonitorPublication import MonitorPublication
from .MonitorPermissions import REASON_DETAIL, R_PAUSED_NOTE, R_UNKNOWN, Verdict, can_jog, can_pause, can_restart, can_resume, can_start_print, jog_caption, section_reason
from ..files.browser.FileBrowserPresentation import FileBrowserPresentation
from ..printing.PrintStartOwner import PrintStartOwner
from .layout.UiStateStore import UiStateStore

from ..files.browser.FileManager import FileManager
from .layout.SectionLayoutPolicy import PANE_NAMES, layout_for, normalise_section_layout
from ..files.browser.FileManagerPolicy import (
    normalise_columns,
)
from .MonitorFormatting import (core_values, endstop_values, number, print_job_caption, peripheral_values, PlateProjectionMemo)
from dataclasses import replace

from ..settings.PrinterConfig import (
    CAMERA_FPS_DEFAULT,
    CAMERA_FPS_FALLBACK_MAX,
    CAMERA_FPS_MIN,
    normalise_temperature_chart,
)
from ..settings.StateStore import StateStore
from .controls.MonitorTuning import MonitorTuning
from .toolhead.ToolheadController import ToolheadController
from .toolhead.ToolheadPolicy import EXTRUDE_DISTANCE_DEFAULT, EXTRUDE_SPEED_DEFAULT, JOG_DISTANCE_DEFAULT
# The pause gates, shared with the Preview card: the popover's own
# candidate is re-read, its refusals are not re-worded.
from ..preview.PreviewFormatting import pause_can_toggle, pause_unavailable
from ..whatsnew.WhatsNew import entries as whats_new_entries, latest_version as whats_new_latest, should_show as whats_new_should_show

from ..settings.MigrationPresentation import migration_banner_text, migration_diagnostics_text


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
# What the follower's placeholder says when the layer it is on will
# never arrive: the service latches a source it could not present
# ("failed") and refuses an anchor a shrunken file left behind
# ("outside"). Both are distinct from a load still in progress, which
# is the label's own default.
_PLATE_REFUSAL_REASONS = {
    "failed": "This layer failed to load.",
    "outside": "This layer is not in this file.",
}


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
    control's 1-8 logical-pixel range."""
    stored = stored if isinstance(stored, dict) else {}
    def flag(key, default):
        value = stored.get(key, default)
        return value if isinstance(value, bool) else default
    def scale(value):
        try:
            parsed = float(value)
            return float(min(8, max(1, round(parsed))))
        except (TypeError, ValueError, OverflowError):
            return 1.0
    return {
        "showPrevious": flag("showPrevious", True),
        "showNext": flag("showNext", True),
        "showBase": flag("showBase", True),
        "showTravels": flag("showTravels", False),
        "trueThickness": flag("trueThickness", False),
        "showRetractions": flag("showRetractions", False),
        "showUnretractions": flag("showUnretractions", False),
        "antialiasing": flag("antialiasing", False),
        "keepCentred": flag("keepCentred", False),
        "lineScale": scale(stored.get("lineScale", 1.0)),
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
        return self._publication.read(kind, name, default)
    return pyqtProperty(kind, read, notify=signal)


# The plate's no-index payload, one stable identity: a quiet poll must
# never re-wrap an empty plate (the identity memo's empty twin).
_EMPTY_PLATE = {"objects": [], "truncated": 0, "excludedCount": 0}


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
    plateScrubVectorChanged = pyqtSignal()
    plateLiveScrubVectorChanged = pyqtSignal()
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
    # The popover's pause-at-layer block (the coordinator's schedule,
    # the popover's own candidate).
    pauseAtLayerChanged = pyqtSignal()

    def __init__(self, output_controller, number_of_extruders, *, client, print_state, config, apply_config, bed_mesh,
                 request_load=None, request_monitor_download=None, request_file_download=None,
                 request_plate_anchor=None, request_plate_split=None,
                 request_follower_popover_open=None,
                 pause_at_layer_block=None, request_pause_toggle=None,
                 request_pause_remove=None, request_pause_clear=None,
                 download_failed=None, request_download_progress=None, cancel_file_download=None,
                 identity=None, state_store=None, persistence=None, index_service=None, colour_scheme=None):
        super().__init__(output_controller, number_of_extruders)
        # The publication exists before anything can read a value
        # property: the declarations below resolve through it.
        self._publication = MonitorPublication()
        self._client, self._print_state, self._config, self._apply_config, self._mesh = \
            client, print_state, config, apply_config, bed_mesh
        # The decoded cache's owner (the follower's index service):
        # the render wrappers pin their payloads there so the decoded
        # budget counts what the wrappers keep alive, and the memory
        # accounting reads the tiers back. Optional — the tests and
        # the harness mount without it.
        self._index_service = index_service
        self._colour_scheme = colour_scheme
        if colour_scheme is not None:
            colour_scheme.changed.connect(self._on_colour_scheme_changed)
        # The follower's anchor seam (the pop-over's layer slider): the
        # model publishes the state, the coordinator owns the payload.
        # The split seam is the progress slider's scrub, same shape.
        self._request_plate_anchor = request_plate_anchor
        self._request_plate_split = request_plate_split
        # The coordinator's explicit demand gate: the closed popover
        # stops the frozen window's per-poll serving (the reviewer's
        # C).
        self._request_follower_popover_open = request_follower_popover_open
        # The pause-at-layer seams: the block READ (the coordinator's
        # own schedule, derived once — the popover re-derives only its
        # candidate from it) and the three intents. Optional, like the
        # anchor seams above: a model without them publishes no pause
        # block and drops the intents.
        self._pause_at_layer_block = pause_at_layer_block
        self._request_pause_toggle = request_pause_toggle
        self._request_pause_remove = request_pause_remove
        self._request_pause_clear = request_pause_clear
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
        self._last_print_files = {}
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
        # The "improve ETA" action reuses the facade's load-current-print
        # flow (download + index), passed in as an explicit capability.
        self._request_load = request_load
        # The monitor-only variant: download + index WITHOUT the preview
        # render (the optimisation); the preview's own load
        # finds the file already local and skips the re-download.
        self._request_monitor_download = request_monitor_download
        self._show_probe_points = bool(getattr(self._config(), "show_probe_points", False))
        self._improving_eta = False
        self._improve_started_snapshot = None
        self._migration_record_cache = None
        self._migration_record_read = False
        self._peripheral_cache = (None, {})
        self._endstop_cache = (None, {})
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
        self._sections = state["sections"]
        follower_view = state["followerView"]
        self._follower_show_previous = follower_view["showPrevious"]
        self._follower_show_next = follower_view["showNext"]
        self._follower_show_base = follower_view["showBase"]
        self._follower_show_travels = follower_view["showTravels"]
        self._follower_true_thickness = follower_view["trueThickness"]
        self._follower_show_retractions = follower_view["showRetractions"]
        self._follower_show_unretractions = follower_view["showUnretractions"]
        self._follower_antialiasing = follower_view["antialiasing"]
        self._follower_keep_centred = follower_view["keepCentred"]
        self._follower_line_scale = follower_view["lineScale"]
        # The follower's attach state and its frozen layer — a LIVE view
        # state, never persisted: a restart follows the print again, and
        # the frozen layer belongs to the file that was printing.
        self._follower_attached = True
        self._follower_layer_anchor = -1
        self._follower_layer_split = None
        self._follower_job = None
        self._plate_qt_job = None
        # The native render pipeline: PER-SURFACE render contexts — the
        # compact mini and the full popover hold their own view, plot,
        # generation, layer wrappers and scheduler state, and neither can
        # invalidate the other's rasters. The QML's role shrinks to
        # composition; the vector geometry crosses only for the scrub's
        # delta, as its own key. The renderer's workers, rasters, assets,
        # pins and cleanup belong to the controller behind this facade.
        self._follower_popover_open = False
        self.plate_renderer = PlateRenderController(
            pins=self._index_service,
            # Read at the call, never snapshotted: the seek trace's switch
            # is a live debug toggle.
            trace_requested=lambda: (self._config() is not None
                                     and self._config().seek_trace),
            attached=lambda: self._follower_attached,
            popover_open=lambda: self._follower_popover_open,
            scene_inputs=self._renderer_scene_inputs,
            notify=self._publish,
            parent=self)
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
        self._toolhead_state = state["toolhead"]
        # The chart config is per-printer (sensor names differ between
        # machines): it lives in the PrinterConfig record, adopting the
        # legacy global JSON block once on first upgrade.
        # The legacy global block migrates only into the FIRST printer
        # record that is empty; a second printer configured before the
        # upgrade starts fresh. One-time migration, by design.
        per_printer = normalise_temperature_chart(getattr(self._config(), "temperature_chart", {}))
        legacy_chart = state.get("temperatureChart") if not per_printer else None
        self._temperature = TemperaturePresentation(
            config, apply_config, per_printer or legacy_chart or {}, self)
        if legacy_chart:
            self._temperature._apply_chart_config()
            _store_write(self._store, {"sections": dict(self._sections)}, delete=("temperatureChart",))
        self._plate_memo = PlateProjectionMemo()
        self._plate_job_seen = None
        self._plate_geometry = None
        self._plate_payload = None
        self._data = MonitorData(client, self)
        # The hydrated lock reaches the policy record: a session
        # that starts locked must read locked, not wait for the
        # padlock to be cycled.
        self._data.set_controls_locked(self._controls_locked)
        self._commands = MonitorCommands(self._data, self)
        self._tuning = MonitorTuning(self._data, self._commands, self)
        # The object gestures bind to the print that received the
        # click: the coordinator's job key is the only identity that
        # tells a restarted same-name print from the one before it.
        self._controls = MonitorControls(self._data, self._commands, self._tuning, bed_mesh, config, self,
                                         job_identity=lambda: getattr(self._print_state(), "job_key", None))
        self._camera = MonitorCamera(self._data, config, apply_config, self)
        # The stream's freshness policy: a dead bridge relay, the
        # reconnect, the toggle and the wake all resolve to one reload
        # nonce, and the pane veils itself while a recovery is running.
        self._camera_recovery = CameraRecovery(
            active=lambda: bool(getattr(self._data, "active", False)))
        self._camera.streamFailed.connect(self._on_stream_failed)
        self._camera.streamRecovered.connect(self._on_stream_recovered)
        # The wake watch (a live report): a stream that survives a
        # suspend shows a FROZEN frame whose image size is already set,
        # so the render watchdog cannot see it — the wake transition
        # reloads the source once, the way the refresh button does.
        try:
            from PyQt6.QtGui import QGuiApplication
            app = QGuiApplication.instance()
            if app is not None:
                self._camera_recovery.seed_application_state(app.applicationState())
                app.applicationStateChanged.connect(self._on_app_state_changed)
        except Exception:
            pass
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
        self._files = FileBrowserPresentation(
            self._file_manager, snapshot=lambda: self._data.snapshot,
            printer_name=self._printer_name, start_print=self._start_browser_print,
            note=self._console.note, save_columns=self._save_state, parent=self,
        )
        self._files.changed.connect(lambda: self._publish())
        # The light publish: thumb transitions never rebuild the rows.
        self._thumbs_dirty = False
        self._publish_pending = False
        self._file_manager.thumbsChanged.connect(self._publish_thumbs)
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
        self._temperature.changed.connect(lambda: self._publish())
        self._data.set_owner_active(True)
        self._publish()

    def _on_file_manager_note(self, text: str) -> None:
        # The note feeds BOTH the console and the popup's own status
        # line (refusals must be visible where the action happened).
        self._console.note(text)
        self._publish()

    def _on_connection_state(self, state: str) -> None:
        if state != "yes":
            return
        if self._camera_recovery.connection_restored():
            self._schedule_publish()

    def _on_stream_failed(self) -> None:
        # The render watchdog and the bridge's own failure signal land
        # on the same recovery: the cadence and the veil are the
        # policy's.
        if self._camera_recovery.stream_stalled():
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
        if self._camera_recovery.application_state_changed(state):
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
        if self._camera_recovery.stream_restored():
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
        """The plate geometry, tied to the CURRENT job: the core lane
        owns exclude_object (CORE_OBJECTS), so its report leads — even
        a report naming no objects yet, which clears the plate rather
        than re-showing the last print's. The auxiliary copy serves
        the lane's first landing alone and never crosses a job
        boundary: the finished print's polygons under the new job's
        flags put an old object on the map at the old position, where
        a tap then acted on the same-named object of the new print.

        The projection is memoised on the definition, not on the lane
        object's identity — the core lane re-freezes per poll, so
        identity can never tell a changed polygon from a re-wrapped
        one (the polygons re-walk O(vertices) of Python per poll)."""
        aux = self._data.snapshot.auxiliary.get("exclude_object") if self._data.snapshot.auxiliary else None
        core = self._data.snapshot.core.get("exclude_object") if self._data.snapshot.core else None
        stats = self._data.snapshot.core.get("print_stats") if self._data.snapshot.core else None
        filename = stats.get("filename") if isinstance(stats, Mapping) else None
        # None is a real job value (a monitor-only print resolves no
        # file), never a "missing" one.
        job = str(filename) if filename else None
        core_plate = core if isinstance(core, Mapping) else None
        aux_plate = aux if isinstance(aux, Mapping) else None
        if core_plate is not None:
            source = core_plate
        elif aux_plate is not None and self._plate_job_seen in (None, job):
            # First landing: only the auxiliary copy has arrived. It is
            # the current plate only while the job has not moved since
            # it was read.
            source = aux_plate
        else:
            source = None
        self._plate_job_seen = job
        if source is None or not source.get("objects"):
            self._plate_geometry = None
            self._plate_payload = _EMPTY_PLATE
            return self._plate_payload
        geometry = self._plate_memo.value(source, job)
        if self._plate_geometry is not geometry:
            self._plate_geometry = geometry
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
            self._temperature._history.observe(self._data.snapshot.auxiliary, time.monotonic(), time.time())
            self._schedule_publish()

    def _on_invalidated(self):
        self._temperature._history.reset()
        # The session boundary drops the projection: a reconnect's
        # first snapshot must never match a definition read for the
        # previous session, and the job it was read under is gone.
        self._plate_memo = PlateProjectionMemo()
        self._plate_job_seen = None
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
            self._temperature._chart_open = False
            self._temperature._chart_full = None
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
        self._publication.set("fileManagerThumbs", payload)
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

    @property
    def _values(self):
        """The committed map, owned by the publication. Every reader here
        treats it as read-only; `_publication` is the only thing that
        replaces or amends it."""
        return self._publication.values

    def _publish(self):
        """One publication transaction.

        The phases name the statement order they always ran in: the
        authoritative snapshot and the state transitions that must land
        before any value reads them; the value build; ONE commit of the
        whole map; the camera transition, which amends the frame it was
        computed in; and the notification pass over what moved. The side
        effects interleaved through the build (the thumbnail request, the
        print-start watchdog tick, the navigation demand) stay in the
        phase they always ran in — their position relative to the reads
        around them is contractual.
        """
        previous = self._values
        snapshot = self._observe()
        self._publication.store(self._compose(snapshot))
        self._apply_camera_url()
        self._emit_changed(previous)

    def _observe(self):
        """The snapshot read and the transitions that precede the values
        built from it."""
        snapshot = self._print_state()
        # The follower's attach state belongs to ONE print: a new file
        # re-attaches it before the value block reads the state.
        self._observe_follower_job(getattr(snapshot, "job_key", None))
        self._commands.observe_job(getattr(snapshot, "job_key", None))
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
        return snapshot

    def _compose(self, snapshot):
        """The value build: the presenters' groups, the policy
        projections and the layout state, merged in the order the keys
        were always written in. The collaborators are asked for their
        group; nothing here writes configuration or dispatches a
        command on a presenter's behalf."""
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
        values["plateTrackingAvailable"] = bool(progress is not None and progress.get("layers", {}).get("current") is not None)
        follower = getattr(snapshot, "plate_manual_progress", None)
        # The attached state must read the LIVE payload: the old
        # order let a stale manual payload (a detach's residue)
        # override the live print — the review's attach finding.
        popover = (
            progress if self._follower_attached
            else follower if follower is not None else progress)
        # The surfaces gate their payloads: a closed popover or a
        # collapsed section never re-wraps a fresh payload, so the
        # memo churn costs nothing while nothing renders (the live
        # request). While gated the keys carry the last published
        # objects; opening or expanding resumes the live values.
        if self._follower_popover_open:
            values["plateLayers"] = (self.plate_renderer.window_for(
                                         "popover", popover["layers"], popover.get("anchor"),
                                         popover.get("method"), popover.get("split"), lookup_ms)
                                     if popover is not None else {})
            values["plateScrubVector"] = (None if self.plate_renderer.gpu_rendering("popover")
                                          else self.plate_renderer.scrub_vector(popover))
            values["plateSplit"] = popover["split"] if popover is not None else None
            values["platePartial"] = popover.get("partial", 0.0) if popover is not None else 0.0
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
                values["plateProgressReason"] = ("Print indexed — waiting for the print to reach an indexed layer."
                                                 if snapshot.index_ready else "" if self._commands.print_active
                                                 else "No active print.")
            elif not values["plateProgressAvailable"]:
                # A refusal must not read as a load in progress: the
                # service latches a layer it could not present, and the
                # label promised a load that was never coming (the live
                # report — the indicator stood forever).
                values["plateProgressReason"] = _PLATE_REFUSAL_REASONS.get(
                    popover.get("refusal") or "", "Loading layer…")
            else:
                values["plateProgressReason"] = ""
            # The warm interaction raster: a retained URL reaches the
            # face ONLY while it is READY for the current demand (its
            # key matches the demand key) — a scrub, a toggle or a
            # failed replacement retires the stale raster from the
            # face the moment the demand moves, and the exact scene
            # serves the gesture instead (the review's lifecycle).
            surface = self.plate_renderer.surface("popover")
            # The renderer projects its own navigation block inside THIS
            # publication: the ready URL (only while its key matches the
            # demand), the split it was painted to, the backing it was
            # baked at and the scene epoch the face keys its composition
            # on. Computing it here — and re-issuing the demand in the
            # same turn — keeps the face from ever reading a torn pair.
            values.update(self.plate_renderer.navigation_values(surface))
            self.plate_renderer.schedule_navigation(surface)
        else:
            values["plateLayers"] = self._values.get("plateLayers", {})
            values["plateScrubVector"] = self._values.get("plateScrubVector")
            values["plateSplit"] = self._values.get("plateSplit")
            values["plateLayerMotionCount"] = self._values.get("plateLayerMotionCount", 0)
            values["plateProgressAnchor"] = self._values.get("plateProgressAnchor", -1)
            values["plateProgressAvailable"] = self._values.get("plateProgressAvailable", False)
            values["plateProgressReason"] = self._values.get("plateProgressReason", "")
            values["plateNavigationData"] = self._values.get("plateNavigationData", "")
            values["plateNavigationSplit"] = self._values.get("plateNavigationSplit")
            values["plateNavigationBacking"] = self._values.get("plateNavigationBacking", 4.0)
            values["plateSceneEpoch"] = self._values.get("plateSceneEpoch", "")
        # The mini's own view: the live payload, always (the live
        # request). The section's collapse gates it — a collapsed
        # mini never re-renders.
        if self._sections.get("plateprogress", True) is not False:
            values["plateLiveLayers"] = (self.plate_renderer.window_for(
                                             "mini", progress["layers"], progress.get("anchor"),
                                             progress.get("method"), progress.get("split"), lookup_ms)
                                         if progress is not None else {})
            values["plateLiveScrubVector"] = (None if self.plate_renderer.gpu_rendering("mini")
                                              else self.plate_renderer.scrub_vector(progress))
            values["plateLiveSplit"] = progress["split"] if progress is not None else None
            values["plateLivePartial"] = progress.get("partial", 0.0) if progress is not None else 0.0
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
        # The popover's pause block: the freshly computed anchor is the
        # fallback candidate, so a popover that has never been slid
        # schedules at the layer it is actually showing.
        values.update(self._pause_at_layer_values(
            snapshot, values["plateProgressAnchor"], values["plateLayerCount"]))
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
        values.update(self._files.values())
        self._files.request_recent_thumbnails()
        # The print-start watchdog runs on the publish tick — the
        # owner supervises the armed attempt regardless of the popup's
        # state (a print confirmed before the popup closed still
        # needs its verdict).
        self._print_start.tick(self._data.snapshot.core)
        self._observe_last_print()
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
        values["webcamStreamEnabled"] = self._camera_recovery.stream_enabled
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
            canRestartLastPrint=self._can_restart_last_print(),
            pauseReason=pause_verdict.reason,
            pauseReasonDetail=REASON_DETAIL.get(pause_verdict.reason, ""),
            resumeReason=resume_verdict.reason,
            resumeReasonDetail=REASON_DETAIL.get(resume_verdict.reason, ""),
            printJobCaption=print_job_caption(observation),
            canCancelPrint=commands.print_active and not commands.busy, actionBusy=commands.busy,
            actionStatus=commands.status, actionTimestamp=commands.status_timestamp, emergencyStopClicks=commands.clicks,
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
            cameraRefreshNonce=self._camera_recovery.nonce,
            cameraRecovering=self._camera_recovery.recovering,
            connectionDetail=self._data.connection_detail,
            sectionExpandedMap=dict(self._sections),
            sectionLayout=self._section_layout,
            sectionHiddenMap={section: True for entry in self._section_layout.values()
                              for section in entry["hidden"]},
            temperatureChartMini=self._temperature._chart_mini_value(),
            temperatureChartFull=self._temperature._chart_full_value(),
            temperatureChartLatest=self._temperature._chart_latest_value(),
            temperatureChartLegend=self._temperature._legend_value(),
            showProbePoints=self._show_probe_points,
            followerShowPrevious=self._follower_show_previous,
            followerShowNext=self._follower_show_next,
            followerShowBase=self._follower_show_base,
            followerShowTravels=self._follower_show_travels,
            followerShowRetractions=self._follower_show_retractions,
            followerShowUnretractions=self._follower_show_unretractions,
            followerAntialiasing=self._follower_antialiasing,
            followerKeepCentred=self._follower_keep_centred,
            followerMotionSmoothing=bool(getattr(self._config(), "path_smoothing", True)),
            followerSoftwareRendering=bool(getattr(self._config(), "software_follower_renderer", False)),
            followerLineScale=self._follower_line_scale,
            followerTrueThickness=self._follower_true_thickness,
            followerTravelVisualRatio=_PLATE_TRAVEL_VISUAL_RATIO,
            followerAttached=self._follower_attached,
            followerLayerAnchor=self._follower_layer_anchor,
            britishSpelling=_british_spelling(),
            improvingEta=(snapshot.load_active or self._improving_eta) and not snapshot.index_ready,
            printIndexReady=snapshot.index_ready,
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
        values["migrationBannerText"] = migration_banner_text(record) if failed else ""
        values["migrationBackupAvailable"] = bool(failed and record.get("backupWritten") and record.get("backupName"))
        values["migrationDiagnosticsVisible"] = bool(failed and record.get("bannerDismissed"))
        values["migrationDiagnosticsText"] = migration_diagnostics_text(record) if failed else ""
        return values

    def _apply_camera_url(self) -> None:
        """The camera transition, on the frame just committed: the URL
        reaches the output device and a genuine transition bumps the
        reload nonce IN THE SAME frame — published one cycle late it
        drove a second stream application after the URL's (the
        camera-delay fix). The query-only case is the upstream's own
        noise: the live stream keeps working, so the pane's guard and
        this one both ignore it."""
        first_attach = False
        try:
            url = self._camera.url if self._camera_recovery.stream_enabled else ""
            if url:
                bumped, first_attach = self._camera_recovery.note_url(url)
                if bumped:
                    self._publication.set("cameraRefreshNonce", self._camera_recovery.nonce)
            self.setCameraUrl(QUrl(url))
        except AttributeError:
            pass
        from ..diagnostics.CameraTiming import enabled as camera_timing_enabled, mark as camera_timing_mark
        self._publication.set("traceCameraTiming", camera_timing_enabled())
        if first_attach:
            # T5: the FINAL url QML consumes, sanitised to
            # scheme/host/port/path (never query credentials).
            sanitised = QUrl(str(url))
            sanitised.setQuery("")
            camera_timing_mark("T5", "camera URL published: %s" % sanitised.toString())

    def _emit_changed(self, previous) -> None:
        """Qt notify signals are part of control ownership. Broadcasting
        every signal for every poll re-evaluated bound ComboBox/Slider
        values while the user was interacting with them, and
        QVariant-list updates rebuilt Repeater delegates mid-drag. Only
        the group whose published values actually changed is notified,
        in the table's order."""
        for signal_name in self._publication.changed(previous):
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
    plateScrubVector = value_property(QVariant, "plateScrubVector", plateScrubVectorChanged, None)
    plateLiveScrubVector = value_property(QVariant, "plateLiveScrubVector", plateLiveScrubVectorChanged, None)
    # The interaction scene's READY flattened full-bed raster URL
    # (camera-independent; the pan/zoom presentation transforms
    # never re-render it).
    plateNavigationData = value_property(str, "plateNavigationData", plateProgressChanged, "")
    plateNavigationSplit = value_property("QVariant", "plateNavigationSplit", plateProgressChanged, None)
    plateNavigationBacking = value_property("QVariant", "plateNavigationBacking", plateProgressChanged, 4.0)
    plateSceneEpoch = value_property(str, "plateSceneEpoch", plateProgressChanged, "")
    monitorLayerSource = value_property(str, "monitorLayerSource", monitorChanged, "")
    filamentUsed = value_property(str, "filamentUsed", monitorChanged, "—")
    filamentRemaining = value_property(str, "filamentRemaining", monitorChanged, "—")
    britishSpelling = value_property(bool, "britishSpelling", monitorChanged, False)
    improvingEta = value_property(bool, "improvingEta", monitorChanged, False)
    printIndexReady = value_property(bool, "printIndexReady", monitorChanged, False)
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
    canRestartLastPrint = value_property(bool, "canRestartLastPrint", actionChanged, False)
    actionBusy = value_property(bool, "actionBusy", actionChanged, False)
    actionStatus = value_property(str, "actionStatus", actionChanged, "")
    actionTimestamp = value_property(str, "actionTimestamp", actionChanged, "")
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
    plateTrackingAvailable = value_property(bool, "plateTrackingAvailable", plateProgressChanged, False)
    plateProgressReason = value_property(str, "plateProgressReason", plateProgressChanged, "")
    plateLayerCount = value_property(int, "plateLayerCount", plateProgressChanged, 0)
    plateLayerMotionCount = value_property(int, "plateLayerMotionCount", plateProgressChanged, 0)
    plateLiveLayers = value_property(QVariant, "plateLiveLayers", plateProgressChanged, {})
    plateLiveSplit = value_property(QVariant, "plateLiveSplit", plateProgressChanged, None)
    platePartial = value_property(float, "platePartial", plateProgressChanged, 0.0)
    plateLivePartial = value_property(float, "plateLivePartial", plateProgressChanged, 0.0)
    plateLiveAnchor = value_property(int, "plateLiveAnchor", plateProgressChanged, -1)
    plateLiveAvailable = value_property(bool, "plateLiveAvailable", plateProgressChanged, False)
    followerShowPrevious = value_property(bool, "followerShowPrevious", followerViewChanged, True)
    followerShowNext = value_property(bool, "followerShowNext", followerViewChanged, True)
    followerShowBase = value_property(bool, "followerShowBase", followerViewChanged, True)
    followerShowTravels = value_property(bool, "followerShowTravels", followerViewChanged, False)
    followerShowRetractions = value_property(bool, "followerShowRetractions", followerViewChanged, False)
    followerShowUnretractions = value_property(bool, "followerShowUnretractions", followerViewChanged, False)
    followerAntialiasing = value_property(bool, "followerAntialiasing", followerViewChanged, False)
    followerKeepCentred = value_property(bool, "followerKeepCentred", followerViewChanged, False)
    followerMotionSmoothing = value_property(bool, "followerMotionSmoothing", followerViewChanged, True)
    followerSoftwareRendering = value_property(bool, "followerSoftwareRendering", followerViewChanged, False)
    @pyqtProperty("QVariantMap", notify=followerViewChanged)
    def followerColourScheme(self):
        return self._colour_scheme.snapshot if self._colour_scheme is not None else {"mode": 1}

    @pyqtSlot(int)
    def setFollowerColourMode(self, mode):
        if self._colour_scheme is not None:
            self._colour_scheme.set_mode(mode)

    def _on_colour_scheme_changed(self):
        self.followerViewChanged.emit()
        self.plate_renderer.patch_view(colourScheme=self.followerColourScheme)

    followerTrueThickness = value_property(bool, "followerTrueThickness", followerViewChanged, False)
    followerLineScale = value_property(float, "followerLineScale", followerViewChanged, 1.0)
    followerTravelVisualRatio = value_property(float, "followerTravelVisualRatio", followerViewChanged,
                                               _PLATE_TRAVEL_VISUAL_RATIO)
    followerAttached = value_property(bool, "followerAttached", followerViewChanged, True)
    followerLayerAnchor = value_property(int, "followerLayerAnchor", followerViewChanged, -1)
    # The pause-at-layer block the popover reads (the Preview card's
    # own keys, the popover's own candidate). Same shape and the same
    # schedule as the card's — one derivation, two readings.
    pauseAtLayerActive = value_property(bool, "pauseAtLayerActive", pauseAtLayerChanged, False)
    pauseAtLayerCandidate = value_property(int, "pauseAtLayerCandidate", pauseAtLayerChanged, 0)
    pauseAtLayerCanToggle = value_property(bool, "pauseAtLayerCanToggle", pauseAtLayerChanged, False)
    pauseAtLayerScheduled = value_property(bool, "pauseAtLayerScheduled", pauseAtLayerChanged, False)
    pauseAtLayerSummary = value_property(str, "pauseAtLayerSummary", pauseAtLayerChanged, "")
    pauseAtLayerItems = value_property(QVariant, "pauseAtLayerItems", pauseAtLayerChanged, [])
    pauseAtLayerUnavailableText = value_property(str, "pauseAtLayerUnavailableText", pauseAtLayerChanged, "")
    pauseAtLayerHasBaked = value_property(bool, "pauseAtLayerHasBaked", pauseAtLayerChanged, False)
    pauseAtLayerHasClearable = value_property(bool, "pauseAtLayerHasClearable", pauseAtLayerChanged, False)
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

    def _start_browser_print(self, relpath):
        """Recheck current printer permission at dispatch, not dialog-open time."""
        observation = getattr(self._data, "observation", None)
        verdict = can_start_print(observation) if observation is not None else Verdict("disabled", R_UNKNOWN)
        if verdict.mode != "allowed":
            self._commands.report_status(f"Print start refused: {verdict.reason}")
            return False
        self._file_manager.start_print(relpath)
        stats = self._data.snapshot.core.get("print_stats") or {}
        self._print_start.arm(str(stats.get("state") or ""))
        return True

    def _printer_name(self):
        try:
            _machine_id, name = self._identity()
            return str(name or "the printer")
        except Exception:
            return "the printer"


    @pyqtSlot(str)
    def fileRequestPrint(self, relpath):
        return self._files.fileRequestPrint(relpath)

    @pyqtSlot()
    def fileConfirmPrint(self):
        return self._files.fileConfirmPrint()

    @pyqtSlot()
    def fileCancelPrint(self):
        return self._files.fileCancelPrint()

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
        return self._files.setFileManagerOpen(is_open)

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
        return self._files.fileClearWalkError()

    @pyqtSlot()
    def openFileManager(self):
        # The open trigger: the flag gates the heavy payload work
        # (the live report: the closed popup must not keep
        # paying the per-poll cost).
        return self._files.openFileManager()

    @pyqtSlot()
    def refreshFileManager(self):
        return self._files.refreshFileManager()

    @pyqtSlot("QVariantList", bool)
    def fileNavigateTo(self, segments, isUp):
        return self._files.fileNavigateTo(segments, isUp)

    @pyqtSlot(str)
    def setFileSearch(self, query):
        return self._files.setFileSearch(query)

    @pyqtSlot(str)
    def setFileSort(self, column):
        return self._files.setFileSort(column)

    @pyqtSlot(str)
    def setFilePageSize(self, size):
        return self._files.setFilePageSize(size)

    @pyqtSlot(int)
    def setFilePage(self, page):
        return self._files.setFilePage(page)

    @pyqtSlot(str, list)
    def setFileFilter(self, category, values):
        return self._files.setFileFilter(category, values)

    @pyqtSlot()
    def clearFileFilters(self):
        return self._files.clearFileFilters()

    @pyqtSlot(str)
    def toggleFileSelection(self, relpath):
        return self._files.toggleFileSelection(relpath)

    @pyqtSlot()
    def clearFileSelection(self):
        return self._files.clearFileSelection()

    @pyqtSlot()
    def toggleFilePageSelection(self):
        return self._files.toggleFilePageSelection()

    @pyqtSlot()
    def fileLoadAllHistory(self):
        return self._files.fileLoadAllHistory()

    @pyqtSlot(str)
    def fileScanMetadata(self, relpath):
        return self._files.fileScanMetadata(relpath)


    @pyqtSlot()
    def fileRequestDelete(self):
        return self._files.fileRequestDelete()

    @pyqtSlot(str)
    def fileRequestDeleteFile(self, relpath):
        return self._files.fileRequestDeleteFile(relpath)

    @pyqtSlot(str)
    def fileCreateDirectory(self, name):
        return self._files.fileCreateDirectory(name)

    @pyqtSlot(str)
    def fileRequestDeleteDir(self, path):
        return self._files.fileRequestDeleteDir(path)

    @pyqtSlot()
    def fileConfirmDelete(self):
        return self._files.fileConfirmDelete()

    @pyqtSlot()
    def fileCancelDelete(self):
        return self._files.fileCancelDelete()

    @pyqtSlot(str)
    def fileRequestRename(self, relpath):
        return self._files.fileRequestRename(relpath)

    @pyqtSlot(str)
    def fileRequestRenameDir(self, path):
        return self._files.fileRequestRenameDir(path)

    @pyqtSlot(str)
    def filePreviewRename(self, name):
        return self._files.filePreviewRename(name)

    @pyqtSlot()
    def fileConfirmRename(self):
        return self._files.fileConfirmRename()

    @pyqtSlot()
    def fileCancelRename(self):
        return self._files.fileCancelRename()

    @pyqtSlot(str)
    def fileUpload(self, path):
        return self._files.fileUpload(path)


    @pyqtSlot()
    def fileConfirmUpload(self):
        return self._files.fileConfirmUpload()

    @pyqtSlot()
    def fileUploadDismiss(self):
        return self._files.fileUploadDismiss()

    @pyqtSlot(list)
    def fileRequestVisibleThumbnails(self, relpaths):
        return self._files.fileRequestVisibleThumbnails(relpaths)

    @pyqtSlot(str, float)
    def setFileColumnWidth(self, key, width):
        return self._files.setFileColumnWidth(key, width)

    @pyqtSlot(list)
    def setFileColumnOrder(self, order):
        return self._files.setFileColumnOrder(order)

    @pyqtSlot(str, bool)
    def setFileColumnVisible(self, key, visible):
        return self._files.setFileColumnVisible(key, visible)


    @pyqtSlot()
    def fileCancelUpload(self):
        return self._files.fileCancelUpload()
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
    # The webcam rate: the renderer's decode cadence and, when a
    # snapshot URL is available at 5 FPS or below, its poll cadence.
    # The bar's floor and selected camera's own
    # configured ceiling (Moonraker's target_fps from the webcam list).
    # The defaults hold until the first publish lands.
    cameraFps = value_property(float, "cameraFps", cameraFpsChanged, CAMERA_FPS_DEFAULT)
    cameraFpsMin = value_property(float, "cameraFpsMin", cameraFpsChanged, CAMERA_FPS_MIN)
    cameraFpsMax = value_property(float, "cameraFpsMax", cameraFpsChanged, CAMERA_FPS_FALLBACK_MAX)
    cameraSnapshotMaxFps = value_property(float, "cameraSnapshotMaxFps", cameraFpsChanged, 5.0)
    cameraSnapshotAvailable = value_property(bool, "cameraSnapshotAvailable", cameraFpsChanged, False)
    cameraSnapshotMode = value_property(bool, "cameraSnapshotMode", cameraFpsChanged, False)

    @pyqtSlot()
    def cameraFirstFrameRendered(self):
        from ..diagnostics.CameraTiming import mark_once
        mark_once("T9", "first decoded frame")

    @pyqtSlot(result=int)
    def cameraPaneInstanceId(self):
        # The pane's process-wide diagnostic id: every pane instance
        # (one per machine model) draws from the SAME sequence, so
        # pane-side trace lines can never collide across models.
        from ..diagnostics.CameraTiming import next_actor_id
        return next_actor_id()

    @pyqtSlot(int, str)
    def cameraPaneTrace(self, pane_id, event):
        # The QML side of the cold-start trace: applyCamera calls,
        # visibility start/stops and watchdog firings, labelled with
        # the pane's id and sanitised by the pane itself.
        from ..diagnostics.CameraTiming import mark
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
        if not self._camera_recovery.refresh_requested():
            return
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
    def _renderer_scene_inputs(self):
        """The presentation settings a scene key or a bake reads, as one
        snapshot taken at the call: the renderer never reaches back into
        the model's properties."""
        return SceneInputs(
            show_previous=bool(self.followerShowPrevious),
            show_next=bool(self.followerShowNext),
            show_base=bool(self.followerShowBase),
            show_travels=bool(self.followerShowTravels),
            bed_width=float(self.bedMeshMachineWidth or 0.0),
            bed_depth=float(self.bedMeshMachineDepth or 0.0))

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
            self.plate_renderer.retire("mini")
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
        return self._temperature.setTemperatureSensorVisible(name, visible)

    @pyqtSlot(str, str)
    def setTemperatureSensorColor(self, name, color):
        return self._temperature.setTemperatureSensorColor(name, color)

    @pyqtSlot(bool)
    def setShowTemperatureTargets(self, show):
        return self._temperature.setShowTemperatureTargets(show)

    @pyqtSlot(bool)
    def setShowTemperaturePower(self, show):
        return self._temperature.setShowTemperaturePower(show)


    @pyqtSlot(bool)
    def setChartOpen(self, opened):
        # The pop-over's hydration gate: the full chart payload
        # materialises only while the pop-over is open; closed, it
        # is the shared dormant object.
        return self._temperature.setChartOpen(opened)


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
                "trueThickness": self._follower_true_thickness,
                "showRetractions": self._follower_show_retractions,
                "showUnretractions": self._follower_show_unretractions,
                "antialiasing": self._follower_antialiasing,
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

    def _last_print_key(self):
        try:
            return (self._identity()[0], self._config().url)
        except Exception:
            return None

    def _observe_last_print(self):
        key = self._last_print_key()
        if key is None:
            return
        stats = self._data.snapshot.core.get("print_stats") or {}
        filename = str(stats.get("filename") or "").strip()
        if filename and stats.get("state") in {"printing", "paused"}:
            self._last_print_files[key] = filename

    def _can_restart_last_print(self):
        observation = getattr(self._data, "observation", None)
        # Session memory survives a cancel macro's SDCARD_RESET_FILE,
        # but is never persisted or recovered from an older Cura session.
        return bool(observation is not None
                    and can_restart(observation).mode == "allowed"
                    and not self._commands.busy
                    and self._file_manager.print_attempt is None
                    and self._last_print_files.get(self._last_print_key()))

    @pyqtSlot()
    def restartLastPrint(self):
        # Revalidate at dispatch: another client can start a job between
        # publication of the enabled button and its click.
        if not self._can_restart_last_print():
            return
        stats = self._data.snapshot.core.get("print_stats") or {}
        self._file_manager.start_print(self._last_print_files[self._last_print_key()])
        self._print_start.arm(str(stats.get("state") or ""))
        self._publish()
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
            self.plate_renderer.retire("popover")
        self._publish()

    @pyqtSlot(str)
    def followerHoldReport(self, text):
        """The plate face's barrier report, for the warm raster's hold.

        Gated by the seek trace's own switch: this reports a gesture's
        held state, and a healthy gesture must log nothing. Routed
        through THIS logger on purpose — Cura's QML message handler
        carries warnings only, so a console.log from the face is
        written into a void.
        """
        if not self.plate_renderer.trace_enabled():
            return
        Logger.log("i", "MPF-HOLD %s", text)

    @pyqtSlot(bool)
    def setFollowerInteracting(self, interacting):
        """Defer only 4x navigation bakes; NEVER freeze live plate state.

        The face presents its latched entry raster while native exact
        checkpoints and split telemetry continue publishing behind it.
        The signal needs no full _publish: it changes no public state.
        On settle, schedule the latest warm demand once.
        """
        self.plate_renderer.set_interacting(interacting)

    @pyqtSlot(str)
    def setFollowerGestureRaster(self, url):
        """The navigation raster the face's live gesture is holding.

        The gesture latches its entry raster and presents that exact
        file for the gesture's whole life, so it has to survive a
        supersede: a bake committing mid-gesture otherwise unlinked
        the very picture on screen. Empty when no gesture is live.
        """
        self.plate_renderer.hold_gesture_raster(url)

    @pyqtSlot(result=int)
    def acquirePlateAssetOwner(self):
        return self.plate_renderer.acquire_asset_owner()

    @pyqtSlot(int, "QVariantList")
    def setPlateAssetReferences(self, owner, urls):
        """Replace one face's references without walking the cache or
        publishing model state in a QML callback. Unknown/released tokens
        cannot resurrect an owner. Only this model's local assets count.
        """
        self.plate_renderer.set_asset_references(owner, urls)

    @pyqtSlot(int)
    def releasePlateAssetOwner(self, owner):
        self.plate_renderer.release_asset_owner(owner)
        # Retirement remains bounded; the next normal prune can collect
        # released assets. Destruction never publishes into a dying face.

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
        if self._print_state().index_ready:
            return
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
        if not self._camera_recovery.set_stream_enabled(enabled):
            return
        if self._camera_recovery.stream_enabled:
            # The bridge's local listener died with the suspend —
            # rebuild it before the URL republishes, or the pane
            # pulls a dead loopback URL and freezes (the live
            # report: only a camera re-select revived it).
            self._camera.resume_stream()
        else:
            self._camera.suspend_stream()
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

    @pyqtSlot(bool)
    def setFollowerShowRetractions(self, show):
        if self._follower_show_retractions is bool(show):
            return
        self._follower_show_retractions = bool(show)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerShowUnretractions(self, show):
        if self._follower_show_unretractions is bool(show):
            return
        self._follower_show_unretractions = bool(show)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerAntialiasing(self, enabled):
        if self._follower_antialiasing is bool(enabled):
            return
        self._follower_antialiasing = bool(enabled)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerKeepCentred(self, keep):
        if self._follower_keep_centred is bool(keep):
            return
        self._follower_keep_centred = bool(keep)
        self._save_state()
        self._publish()

    @pyqtSlot(bool)
    def setFollowerTrueThickness(self, enabled):
        if self._follower_true_thickness is bool(enabled):
            return
        self._follower_true_thickness = bool(enabled)
        self._save_state()
        self._publish()
        self.plate_renderer.patch_view(trueThickness=bool(enabled))

    @pyqtSlot(float)
    def setFollowerLineScale(self, scale):
        try:
            scale = float(min(8, max(1, round(float(scale)))))
        except (TypeError, ValueError, OverflowError):
            return
        if self._follower_line_scale == scale and not self._follower_true_thickness:
            return
        self.setFollowerTrueThickness(False)
        self._follower_line_scale = scale
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

    def _pause_toggle_request(self, human_layer):
        """The coordinator's pause seam (the confirm*/toggle*
        capability pattern): a 1-based human layer, exactly as the
        Preview card's own button sends it."""
        if self._request_pause_toggle is not None:
            self._request_pause_toggle(human_layer)

    def _pause_remove_request(self, human_layer):
        if self._request_pause_remove is not None:
            self._request_pause_remove(human_layer)

    def _pause_clear_request(self):
        if self._request_pause_clear is not None:
            self._request_pause_clear()

    def _published_pause_block(self):
        """The coordinator's pause block, as published (the card's own
        values). Anything but a mapping — no seam, no coordinator
        publish yet — publishes no block at all, so the properties keep
        their defaults."""
        source = self._pause_at_layer_block
        block = source() if source is not None else None
        return block if isinstance(block, Mapping) else {}

    def _pause_at_layer_values(self, snapshot, anchor, layer_count):
        """The pause block the POPOVER reads: the coordinator's rows and
        schedule (never a second derivation of them), with the candidate
        re-read for the layer the popover itself stands on — its
        committed follower anchor while it holds one, else the live
        layer it follows. Cura's Preview selection is a different
        question, so only the candidate-independent keys cross
        unchanged.

        The candidate's own gates are re-derived with the same helpers
        the card's are, over the same schedule read back out of the
        rows: the button the popover draws must be armed exactly when
        the coordinator would accept the request."""
        block = self._published_pause_block()
        if not block:
            return {}
        index = self._follower_layer_anchor
        if index < 0:
            index = _coerce_anchor(anchor)
        # The END of the layer the popover stands on: a 1-based human
        # layer, 0 while no layer is known (the card's own contract).
        candidate = index + 1 if index >= 0 else 0
        selected = candidate - 1 if candidate > 0 else None
        items = block.get("pauseAtLayerItems") or []
        manual = {item["layer"] - 1 for item in items if item.get("state") != "baked"}
        baked = {item["layer"] - 1 for item in items if item.get("state") == "baked"}
        baked_block = selected is not None and selected in baked
        active = bool(block.get("pauseAtLayerActive"))
        current = getattr(getattr(snapshot, "layer", None), "index", None)
        total = getattr(getattr(snapshot, "layer", None), "total", None)
        if total is None:
            total = layer_count or None
        scheduled = selected is not None and selected in manual
        indexed = bool(layer_count)
        can_toggle = indexed and not baked_block and pause_can_toggle(active, selected, current, total)
        return {
            "pauseAtLayerActive": active,
            "pauseAtLayerCandidate": candidate,
            "pauseAtLayerCanToggle": can_toggle, "pauseAtLayerScheduled": scheduled,
            "pauseAtLayerSummary": block.get("pauseAtLayerSummary", ""),
            "pauseAtLayerItems": items,
            "pauseAtLayerUnavailableText": ("Print not indexed" if active and not indexed
                                            else "a pause is baked into the gcode at this layer" if baked_block
                                            else pause_unavailable(active, can_toggle, scheduled, current, selected)),
            # The rows' own facts, unchanged by whose layer is selected.
            "pauseAtLayerHasBaked": bool(block.get("pauseAtLayerHasBaked")),
            "pauseAtLayerHasClearable": bool(block.get("pauseAtLayerHasClearable")),
        }






















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
        return dict({"packedBytes": packed, "decodedBytes": decoded,
                     "pinnedDecodedBytes": pinned}, **self.plate_renderer.accounting())
















    @pyqtSlot()
    def setFollowerGestureBake(self):
        """The face's press hook: the drag is about to latch the warm
        raster — bake the current split once, outside the cadence."""
        self.plate_renderer.gesture_bake()

    # Both feeds call this slot: the popover passes the DPR ninth,
    # the mini's compact feed stops at eight. A single eight-argument
    # registration silently drops the ninth (the engine's "Too many
    # arguments, ignoring 1"), so the nine-argument form is
    # registered as its own overload.
    @pyqtSlot(str, bool)
    def setFollowerGpuRendering(self, name, enabled):
        """A mounted face selects its backend; GPU faces need no raster jobs.

        Retire outstanding CPU demand on a change. A queued publication
        rebuilds the current demand so switching back resumes the original
        full/prefix/navigation/checkpoint scheduler with the current view.
        """
        if self.plate_renderer.set_gpu_rendering(name, enabled):
            self._schedule_publish()

    @pyqtSlot(str, float, float, int, int, bool, float, float, float, float)
    @pyqtSlot(str, float, float, int, int, bool, float, float, float)
    @pyqtSlot(str, float, float, int, int, bool, float, float)
    def setFollowerView(self, surface, scale, lineScale, width, height, compact,
                        panX, panY, dpr=1.0, pixelWidth=0.0):
        """The raster's view inputs for ONE SURFACE . An
        exact repeat is a no-op ; the
        plot+view pair coalesces into one flush. The DEVICE-PIXEL
        backing rides the view: the worker paints at the device
        resolution (bounded supersampling) and the scene-graph
        samples the raster down to the logical face — a DPR-2
        screen never enlarges a 1x toolpath raster."""
        record = self.plate_renderer.surface(surface)
        if record is None:
            return
        view = {"scale": float(scale), "lineScale": float(lineScale),
                "travelVisualRatio": _PLATE_TRAVEL_VISUAL_RATIO,
                "width": int(width), "height": int(height),
                "compact": bool(compact),
                "panX": float(panX), "panY": float(panY),
                "dpr": min(2.0, max(1.0, float(dpr)))}
        view["colourScheme"] = self.followerColourScheme
        view["trueThickness"] = self._follower_true_thickness
        if pixelWidth > 0:
            view["lineWidthPx"] = float(pixelWidth)
        # The staging compares against the EFFECTIVE value and arms the
        # one zero-tick flush: a plot+view pair published by one transition
        # commits as ONE generation.
        self.plate_renderer.stage_context(record, "view", view)

    @pyqtSlot(str, float, float, float, float, float, float)
    def setFollowerPlot(self, surface, offsetX, offsetY, sx, sy, bedXMin, bedYMax):
        """The bed plot for ONE SURFACE (fed on the canvas's
        re-fit): the native renderer uses the SAME mapping the
        face's painters did, so the blit lands the identical
        picture. Staged like the view — an exact repeat is a no-op,
        and a plot+view pair flushes once."""
        record = self.plate_renderer.surface(surface)
        if record is None:
            return
        plot = {"offsetX": float(offsetX), "offsetY": float(offsetY),
                "sx": float(sx), "sy": float(sy),
                "bedXMin": float(bedXMin), "bedYMax": float(bedYMax)}
        # Staged like the view — an exact repeat is a no-op, and a
        # plot+view pair flushes once.
        self.plate_renderer.stage_context(record, "plot", plot)

    def _observe_follower_job(self, job):
        """A new print re-attaches the follower: the frozen layer
        belonged to the file that was printing."""
        if job != self._plate_qt_job:
            # The render caches belong to that file too: a new
            # print's geometry must never answer with the old job's
            # images. The print epoch bumps with the reset, so an
            # in-flight worker from the previous print can never match
            # a new print's ticket, whatever its layer, token and
            # generation.
            self._plate_qt_job = job
            self.plate_renderer.begin_print()
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
            if frozen < 0:
                # The published anchor may lag the surface's demand
                # (a direct feed before the coordinator's publish):
                # the surface's own current layer is the same truth.
                presented = self.plate_renderer.current_layer("popover")
                if presented is not None:
                    frozen = _coerce_anchor(presented)
            if frozen < 0 and (self._values.get("plateLayerCount", 0) > 0
                               or self._print_state().index_ready):
                # The index can be ready before the physical print has
                # reached one of its layers. The count can also lag the
                # ready signal by a publish. Hold the detach at layer zero
                # and serve its geometry as soon as that count arrives.
                frozen = 0
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
        self.plate_renderer.seek_ticked()

    @pyqtSlot(int)
    def setFollowerLayerAnchor(self, layer):
        self.plate_renderer.trace_seek_entry(layer)
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

    @pyqtSlot(int)
    def togglePauseAtLayer(self, layer):
        """The popover's pause button: schedule at the END of the layer
        the popover stands on, or remove the pause already scheduled
        there. The layer is the human (1-based) one the block publishes
        as its candidate, so the two can never drift. The coordinator
        owns the refusals (a passed layer, the final layer, a baked
        pause) — the published canToggle mirrors them, and junk never
        reaches the seam."""
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            return
        if layer < 1:
            return
        self._pause_toggle_request(layer)
        self._publish()

    @pyqtSlot(int)
    def removePauseAtLayer(self, layer):
        try:
            layer = int(layer)
        except (TypeError, ValueError):
            return
        if layer < 1:
            return
        self._pause_remove_request(layer)
        self._publish()

    @pyqtSlot()
    def clearPauseAtLayer(self):
        self._pause_clear_request()
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
    @pyqtSlot(float)
    def previewSpeedFactor(self, percent): self._controls.factor("speed", percent, True)
    @pyqtSlot(float)
    def setSpeedFactor(self, percent): self._controls.factor("speed", percent)
    @pyqtSlot(float)
    def previewFlowFactor(self, percent): self._controls.factor("flow", percent, True)
    @pyqtSlot(float)
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
