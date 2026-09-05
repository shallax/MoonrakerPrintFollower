from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple


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
    path_follow: bool = True
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

    @classmethod
    def from_dict(cls, value: Any) -> "PrinterConfig":
        raw = value if isinstance(value, dict) else {}
        defaults = cls()
        data: Dict[str, Any] = {}
        for key in asdict(defaults):
            data[key] = raw.get(key, getattr(defaults, key))

        try:
            data["poll_interval_ms"] = max(1, int(data["poll_interval_ms"]))
        except (TypeError, ValueError):
            data["poll_interval_ms"] = defaults.poll_interval_ms
        try:
            data["z_tolerance"] = float(data["z_tolerance"])
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

        for key in (
            "url", "api_key", "follow_mode", "frontend_url", "output_format",
            "upload_path", "power_devices", "filename_translate_input",
            "filename_translate_output", "filename_translate_remove", "camera_url",
        ):
            data[key] = str(data.get(key) or getattr(defaults, key))

        paths = data.get("upload_paths")
        if isinstance(paths, (list, tuple)):
            data["upload_paths"] = [
                str(item).strip().strip("/") for item in paths
                if str(item).strip().strip("/")
            ]
        else:
            data["upload_paths"] = []

        for key in (
            "enabled", "moonraker_layer_is_one_based", "auto_preview",
            "z_fallback", "path_follow", "show_toolhead_indicator",
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

        data["upload_path"] = data["upload_path"].strip().strip("/")
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
        "path_follow": "moonraker_print_follower/path_follow",
    }

    def __init__(self, preferences, identity_provider) -> None:
        self._preferences = preferences
        self._identity_provider = identity_provider
        preferences.addPreference(self.PREF_KEY, "{}")
        preferences.addPreference(self.MIGRATED_KEY, False)
        preferences.addPreference(self.MOONRAKER_CONNECTION_PREF_KEY, "{}")
        preferences.addPreference(self.MOONRAKER_CONNECTION_MIGRATED_KEY, False)

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
        data = self._decode_mapping(self._preferences.getValue(self.PREF_KEY))
        return data if isinstance(data, dict) else {}

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
        if self._truthy(self._preferences.getValue(self.MIGRATED_KEY)):
            return False
        machine_id, _ = self.identity()
        if machine_id == "unknown":
            # Cura can instantiate extensions before the first machine stack is
            # fully available. Defer migration rather than permanently assigning
            # the user's 1.x target to an artificial "unknown" printer.
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

            legacy_url = str(legacy.get("url") or "").strip().rstrip("/")
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
        data[key] = asdict(config)
        self._save_all(data)

    def update(self, **changes: Any) -> PrinterConfig:
        config = self.get()
        data = asdict(config)
        data.update(changes)
        updated = PrinterConfig.from_dict(data)
        self.set(updated)
        return updated
