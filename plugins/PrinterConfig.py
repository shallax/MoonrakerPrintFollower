from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from math import isfinite
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

# ConsolePolicy owns these bounds; PrinterConfig may not import it
# (module-layering pin), so the coercion repeats the numbers. Drift is
# harmless: the controller re-trims the transcript on load regardless.
_CONSOLE_TRANSCRIPT_CAP = 60  # MAX_TRANSCRIPT (50) + the command retention (10)
_CONSOLE_LINE_CAP = 8 * 1024


def normalise_url(value: Any) -> str:
    """Canonical Moonraker base URL: scheme required, no trailing slash.

    Scheme-only input (``http:``, ``https://``, …) is the unconfigured
    placeholder and maps to ``http://``; ``usable_url`` rejects it.
    Control characters and embedded userinfo are stripped (panel
    security P3): Qt logs the full request URL on errors, so
    ``http://user:pass@host`` would leak credentials into Cura's log.
    """
    text = str(value or "").strip()
    if not text or text.lower() in ("http:", "https:", "http://", "https://"):
        return "http://"
    if any(ord(ch) < 32 for ch in text):
        return "http://"
    if not text.lower().startswith(("http://", "https://")):
        text = f"http://{text}"
    split = urlsplit(text)
    if split.username is not None or split.password is not None:
        host = split.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if split.port is not None:
            host = f"{host}:{split.port}"
        text = urlunsplit((split.scheme, host, split.path, split.query, split.fragment))
    # rstrip("/") would eat the scheme's own "//"; only strip path separators.
    while text.endswith("/") and not text.endswith("://"):
        text = text[:-1]
    return text


def upload_path_safe(value: Any) -> str:
    """The upload path with Moonraker-side traversal segments refused:
    the dialog applies UploadController.valid_path, but a hand-edited
    or migrated config must not carry ".." segments to the upload API
    either (panel security P3 — same rule, one place per surface)."""
    text = str(value or "").strip().strip("/")
    if text == "<root>":
        return ""
    parts = text.replace("\\", "/").split("/")
    return "" if any(part.startswith(".") for part in parts if part) else text


def normalise_temperature_chart(value: Any) -> dict:
    """The chart config block: per-sensor visibility/colours plus the
    display toggles, coerced so hand-edited values cannot silently flip
    semantics (bool("false") is True)."""
    if not isinstance(value, Mapping) or not value:
        # An empty block stays empty: it means "never configured", and a
        # materialised default block would defeat the legacy migration.
        return {}
    visible = value.get("visible") if isinstance(value.get("visible"), Mapping) else {}
    colors = value.get("colors") if isinstance(value.get("colors"), Mapping) else {}

    def truth(item):
        return item if isinstance(item, bool) else str(item).strip().lower() in ("1", "true", "yes", "on")

    return {
        "visible": {str(key): truth(item) for key, item in visible.items()},
        "colors": {str(key): str(item) for key, item in colors.items()},
        "showTargets": truth(value.get("showTargets", True)),
        "showPower": truth(value.get("showPower", True)),
    }


class FeedMode(str, Enum):
    """The status-feed transport for one printer.

    The value is the persisted spelling. Websocket is the product
    default for new and upgraded installs; the unknown-value fallback
    below is the same default, so a corrupt or foreign record never
    bricks the connection settings.
    """

    WEBSOCKET = "websocket"
    HTTP = "http"


@dataclass
class PrinterConfig:
    # Live Preview follower settings.
    enabled: bool = False
    url: str = "http://"
    api_key: str = ""
    poll_interval_ms: int = 750
    moonraker_layer_is_one_based: bool = True
    auto_preview: bool = False
    z_fallback: bool = True
    z_tolerance: float = 0.04
    trace_layer: bool = False
    trace_http: bool = False
    # The status-feed transport, per printer (mixed fleets mix modes).
    # The product default lives here, never in a client-side code default.
    feed_mode: FeedMode = FeedMode.WEBSOCKET
    path_follow: bool = True
    path_smoothing: bool = True
    show_toolhead_indicator: bool = True
    follow_mode: str = "exact"

    # Integrated Moonraker upload settings.
    frontend_url: str = ""
    output_format: str = "gcode"
    upload_dialog: bool = True
    upload_path: str = ""
    upload_paths: List[str] = field(default_factory=list)
    upload_start_print: bool = False
    upload_remember_state: bool = False
    upload_autohide_message: bool = False
    power_devices: str = ""
    ready_retry_interval_s: float = 0.5
    filename_translate_input: str = ""
    filename_translate_output: str = ""
    filename_translate_remove: str = ""

    # Monitor/webcam settings. Modern Moonraker webcam entries are discovered
    # automatically. These fields retain the old Moonraker Connection camera as
    # a fallback for installations that do not expose /server/webcams/list.
    camera_url: str = ""
    camera_rotation: int = 0
    camera_mirror: bool = False
    camera_selected: str = ""

    # Monitor user preferences that are machine-specific: sensor names
    # differ between printers, so chart colours/visibility and the
    # console history live here rather than in the global chrome file
    # (which keeps the sections map, pane collapse and the controls
    # lock).
    temperature_chart: Dict[str, Any] = field(default_factory=dict)
    console_history: List[str] = field(default_factory=list)
    # The persisted console transcript (the author's ruling): the last
    # ~50 lines of BOTH the user's commands and Klipper's gcode-store
    # output survive across sessions; restored lines grey in the pane.
    console_transcript: List[Dict[str, Any]] = field(default_factory=list)
    # The gcode-store poll's last-seen timestamp, persisted with the
    # transcript so the next session's expand-backfill never repeats
    # already-seen lines.
    console_store_time: float = 0.0
    # The bed-mesh pop-over's probe-point overlay, per printer.
    show_probe_points: bool = False

    @property
    def frontend_target(self) -> str:
        """The URL a browser should open: the dedicated frontend when set, else the printer."""
        return self.frontend_url or self.url

    @classmethod
    def from_dict(cls, value: Any) -> "PrinterConfig":
        raw = value if isinstance(value, dict) else {}
        defaults = cls()
        data: Dict[str, Any] = {}
        for key in asdict(defaults):
            data[key] = raw.get(key, getattr(defaults, key))

        try:
            data["poll_interval_ms"] = max(1, min(3_600_000, int(data["poll_interval_ms"])))
        except (TypeError, ValueError):
            data["poll_interval_ms"] = defaults.poll_interval_ms
        try:
            tolerance = float(data["z_tolerance"])
            if not isfinite(tolerance) or not (0.005 <= tolerance <= 0.250):
                tolerance = defaults.z_tolerance
            data["z_tolerance"] = tolerance
        except (TypeError, ValueError):
            data["z_tolerance"] = defaults.z_tolerance
        try:
            data["ready_retry_interval_s"] = min(
                60.0, max(0.1, float(data["ready_retry_interval_s"]))
            )
        except (TypeError, ValueError):
            data["ready_retry_interval_s"] = defaults.ready_retry_interval_s
        try:
            rotation = int(data["camera_rotation"])
        except (TypeError, ValueError):
            rotation = defaults.camera_rotation
        data["camera_rotation"] = rotation if rotation in {0, 90, 180, 270} else 0

        data["url"] = normalise_url(data.get("url"))

        for key in (
            "api_key", "follow_mode", "frontend_url", "output_format",
            "upload_path", "power_devices", "filename_translate_input",
            "filename_translate_output", "filename_translate_remove", "camera_url", "camera_selected",
        ):
            data[key] = str(data.get(key) or getattr(defaults, key))

        paths = data.get("upload_paths")
        if isinstance(paths, (list, tuple)):
            data["upload_paths"] = [safe for safe in (upload_path_safe(item) for item in paths) if safe]
        else:
            data["upload_paths"] = []
        data["upload_path"] = upload_path_safe(data.get("upload_path"))

        transcript = data.get("console_transcript")
        if isinstance(transcript, (list, tuple)):
            cleaned = []
            for entry in transcript:
                if not isinstance(entry, Mapping):
                    continue
                kind = str(entry.get("kind") or "")
                if kind not in {"command", "response"}:
                    continue
                cleaned.append({
                    "kind": kind,
                    "text": str(entry.get("text") or "")[:_CONSOLE_LINE_CAP],
                    "error": bool(entry.get("error")),
                    # The success flag colours the restored "ok" green;
                    # dropping it here rendered every restored response
                    # neutral grey (the author's "never seen a coloured
                    # line" report).
                    "success": bool(entry.get("success")),
                })
            data["console_transcript"] = cleaned[-_CONSOLE_TRANSCRIPT_CAP:]
        else:
            data["console_transcript"] = []
        try:
            store_time = float(data.get("console_store_time") or 0.0)
            # A finite stamp beyond 2100-01-01 (or before the epoch) is a
            # corrupt record, not a printer clock: the feed's watermark
            # would freeze the console forever (panel security P2-4).
            if not isfinite(store_time) or store_time < 0.0 or store_time > 4_102_444_800.0:
                store_time = 0.0
            data["console_store_time"] = store_time
        except (TypeError, ValueError):
            data["console_store_time"] = 0.0

        for key in (
            "enabled", "moonraker_layer_is_one_based", "auto_preview",
            "z_fallback", "path_follow", "path_smoothing", "show_toolhead_indicator",
            "trace_layer", "trace_http",
            "upload_dialog", "upload_start_print", "upload_remember_state",
            "upload_autohide_message", "camera_mirror",
        ):
            item = data[key]
            if not isinstance(item, bool):
                data[key] = str(item).strip().lower() in ("1", "true", "yes", "on")

        if data["follow_mode"] not in {"exact", "completed", "lookahead", "window"}:
            data["follow_mode"] = "exact"
        if data["output_format"].lower() not in {"gcode", "ufp"}:
            data["output_format"] = "gcode"
        else:
            data["output_format"] = data["output_format"].lower()

        # Missing keys are pre-4.0.0 records: the product default (ruled).
        # A present-but-unknown value falls back the same way, but never
        # silently when the value merely needs spelling coercion.
        raw_mode = data.get("feed_mode")
        if not isinstance(raw_mode, FeedMode):
            try:
                raw_mode = FeedMode(str(raw_mode).strip().lower())
            except (TypeError, ValueError):
                raw_mode = defaults.feed_mode
        data["feed_mode"] = raw_mode

        data["upload_path"] = data["upload_path"].strip().strip("/")
        data["temperature_chart"] = normalise_temperature_chart(data.get("temperature_chart"))
        # A corrupt legacy record must not load an unbounded history
        # list into memory (panel security P3): trim like every other
        # retained list in this record (ConsolePolicy.MAX_HISTORY = 200;
        # the layering pin keeps the number local).
        history = data.get("console_history")
        data["console_history"] = [str(line) for line in history][-200:] if isinstance(history, (list, tuple)) else []
        return cls(**data)


class PrinterConfigStore:
    """Persist all Moonraker settings against Cura's machine instance."""

    PREF_KEY = "moonraker_print_follower/printer_configs_v1"
    MIGRATED_KEY = "moonraker_print_follower/printer_configs_migrated_v1"

    # The separate Moonraker Connection plugin stores its per-printer settings
    # here. Import those values once so uninstalling the old plugin does not make
    # users re-enter their connection and upload configuration.
    MOONRAKER_CONNECTION_PREF_KEY = "moonraker/instances"
    MOONRAKER_CONNECTION_MIGRATED_KEY = (
        "moonraker_print_follower/moonraker_connection_migrated_v1"
    )

    LEGACY_MAP = {
        "enabled": "moonraker_print_follower/enabled",
        "url": "moonraker_print_follower/url",
        "api_key": "moonraker_print_follower/api_key",
        "poll_interval_ms": "moonraker_print_follower/poll_interval_ms",
        "moonraker_layer_is_one_based": "moonraker_print_follower/moonraker_layer_is_one_based",
        "auto_preview": "moonraker_print_follower/auto_preview",
        "z_fallback": "moonraker_print_follower/z_fallback",
        "z_tolerance": "moonraker_print_follower/z_tolerance",
        "trace_layer": "moonraker_print_follower/trace_layer",
        "trace_http": "moonraker_print_follower/trace_http",
        "path_follow": "moonraker_print_follower/path_follow",
    }
    LEGACY_DEFAULTS = {
        "enabled": False,
        "url": "http://",
        "api_key": "",
        "poll_interval_ms": 750,
        "moonraker_layer_is_one_based": True,
        "auto_preview": False,
        "z_fallback": True,
        "z_tolerance": 0.04,
        "trace_layer": False,
        "trace_http": False,
        "path_follow": True,
    }

    def __init__(self, preferences, identity_provider) -> None:
        self._preferences = preferences
        self._identity_provider = identity_provider
        preferences.addPreference(self.PREF_KEY, "{}")
        preferences.addPreference(self.MIGRATED_KEY, False)
        preferences.addPreference(self.MOONRAKER_CONNECTION_PREF_KEY, "{}")
        preferences.addPreference(self.MOONRAKER_CONNECTION_MIGRATED_KEY, False)
        # Legacy follower keys used to be registered by the old runtime. Keep
        # their registration here so migration is self-contained after the
        # runtime split and stored values remain readable during upgrade.
        for field_name, pref_key in self.LEGACY_MAP.items():
            preferences.addPreference(pref_key, self.LEGACY_DEFAULTS[field_name])

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _decode_mapping(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        try:
            decoded = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = {}
        return decoded if isinstance(decoded, dict) else {}

    def identity(self) -> Tuple[str, str]:
        try:
            machine_id, machine_name = self._identity_provider()
        except Exception:
            machine_id, machine_name = "unknown", "Unknown Cura printer"
        machine_id = str(machine_id or "unknown")
        machine_name = str(machine_name or machine_id)
        return machine_id, machine_name

    def _load_all(self) -> Dict[str, Dict[str, Any]]:
        return self._decode_mapping(self._preferences.getValue(self.PREF_KEY))

    def _save_all(self, data: Dict[str, Dict[str, Any]]) -> None:
        self._preferences.setValue(
            self.PREF_KEY,
            json.dumps(data, sort_keys=True, separators=(",", ":")),
        )

    def _legacy_config(self) -> PrinterConfig:
        raw = {}
        for config_field, pref_key in self.LEGACY_MAP.items():
            raw[config_field] = self._preferences.getValue(pref_key)
        return PrinterConfig.from_dict(raw)

    def migrate_legacy_to_current_machine(self) -> bool:
        """Move pre-per-printer follower preferences into the active machine once."""
        if self._truthy(self._preferences.getValue(self.MIGRATED_KEY)):
            return False
        machine_id, _ = self.identity()
        if machine_id == "unknown":
            # Cura can instantiate extensions before the first machine stack is
            # fully available. Defer migration rather than assigning data to an
            # artificial printer identity.
            return False
        data = self._load_all()
        if machine_id not in data:
            data[machine_id] = asdict(self._legacy_config())
            self._save_all(data)
        self._preferences.setValue(self.MIGRATED_KEY, True)
        return True

    def migrate_moonraker_connection(self) -> int:
        """Import settings from the old standalone Moonraker Connection plugin.

        Existing follower URL/API-key values win when already configured; upload
        specific values are imported because the follower had no equivalent fields.
        """
        if self._truthy(
            self._preferences.getValue(self.MOONRAKER_CONNECTION_MIGRATED_KEY)
        ):
            return 0

        legacy_all = self._decode_mapping(
            self._preferences.getValue(self.MOONRAKER_CONNECTION_PREF_KEY)
        )
        if not legacy_all:
            self._preferences.setValue(self.MOONRAKER_CONNECTION_MIGRATED_KEY, True)
            return 0

        data = self._load_all()
        imported = 0
        for machine_id, legacy in legacy_all.items():
            if not isinstance(legacy, dict):
                continue
            key = str(machine_id)
            current = PrinterConfig.from_dict(data.get(key))
            merged = asdict(current)

            legacy_raw = str(legacy.get("url") or "").strip()
            legacy_url = normalise_url(legacy_raw) if legacy_raw else ""
            if legacy_url and current.url.strip() in ("", "http://", "https://"):
                merged["url"] = legacy_url
            legacy_api_key = str(legacy.get("api_key") or "").strip()
            if legacy_api_key and not current.api_key:
                merged["api_key"] = legacy_api_key

            mapping = {
                "frontend_url": "frontend_url",
                "output_format": "output_format",
                "upload_dialog": "upload_dialog",
                "upload_path": "upload_path",
                "upload_start_print_job": "upload_start_print",
                "upload_remember_state": "upload_remember_state",
                "upload_autohide_messagebox": "upload_autohide_message",
                "power_device": "power_devices",
                "retry_interval": "ready_retry_interval_s",
                "trans_input": "filename_translate_input",
                "trans_output": "filename_translate_output",
                "trans_remove": "filename_translate_remove",
                "camera_url": "camera_url",
                "camera_image_rotation": "camera_rotation",
                "camera_image_mirror": "camera_mirror",
            }
            for old_key, new_key in mapping.items():
                if old_key in legacy:
                    merged[new_key] = legacy.get(old_key)

            old_paths = legacy.get("upload_pathes")
            if isinstance(old_paths, (list, tuple)):
                merged["upload_paths"] = list(old_paths)

            data[key] = asdict(PrinterConfig.from_dict(merged))
            imported += 1

        if imported:
            self._save_all(data)
        self._preferences.setValue(self.MOONRAKER_CONNECTION_MIGRATED_KEY, True)
        return imported

    def get(self, machine_id: Optional[str] = None) -> PrinterConfig:
        current_id, _ = self.identity()
        key = str(machine_id or current_id)
        return PrinterConfig.from_dict(self._load_all().get(key))

    def set(self, config: PrinterConfig, machine_id: Optional[str] = None) -> None:
        current_id, _ = self.identity()
        key = str(machine_id or current_id)
        data = self._load_all()
        # Read-modify-write of the raw record: keys this version does not
        # own survive a save, so a downgrade to an older plugin can never
        # destroy the mode field (or anything newer it does not know).
        raw = data.get(key)
        if not isinstance(raw, dict):
            raw = {}
        raw.update(asdict(config))
        data[key] = raw
        self._save_all(data)

    def update(self, **changes: Any) -> PrinterConfig:
        config = self.get()
        data = asdict(config)
        data.update(changes)
        updated = PrinterConfig.from_dict(data)
        self.set(updated)
        return updated
