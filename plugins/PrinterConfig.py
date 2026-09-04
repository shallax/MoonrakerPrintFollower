from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass
class PrinterConfig:
    enabled: bool = False
    url: str = "http://"
    api_key: str = ""
    poll_interval_ms: int = 750
    moonraker_layer_is_one_based: bool = True
    auto_preview: bool = False
    z_fallback: bool = True
    z_tolerance: float = 0.04
    path_follow: bool = True
    follow_mode: str = "exact"

    @classmethod
    def from_dict(cls, value: Any) -> "PrinterConfig":
        raw = value if isinstance(value, dict) else {}
        defaults = cls()
        data = {}
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
        data["url"] = str(data.get("url") or defaults.url)
        data["api_key"] = str(data.get("api_key") or "")
        data["follow_mode"] = str(data.get("follow_mode") or defaults.follow_mode)
        for key in (
            "enabled", "moonraker_layer_is_one_based", "auto_preview",
            "z_fallback", "path_follow",
        ):
            value = data[key]
            if not isinstance(value, bool):
                data[key] = str(value).strip().lower() in ("1", "true", "yes", "on")
        if data["follow_mode"] not in {"exact", "completed", "lookahead", "window"}:
            data["follow_mode"] = "exact"
        return cls(**data)


class PrinterConfigStore:
    """Persist Moonraker settings against Cura's machine instance, not globally."""

    PREF_KEY = "moonraker_print_follower/printer_configs_v1"
    MIGRATED_KEY = "moonraker_print_follower/printer_configs_migrated_v1"

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

    def identity(self) -> Tuple[str, str]:
        try:
            machine_id, machine_name = self._identity_provider()
        except Exception:
            machine_id, machine_name = "unknown", "Unknown Cura printer"
        machine_id = str(machine_id or "unknown")
        machine_name = str(machine_name or machine_id)
        return machine_id, machine_name

    def _load_all(self) -> Dict[str, Dict[str, Any]]:
        raw = self._preferences.getValue(self.PREF_KEY)
        if isinstance(raw, dict):
            data = raw
        else:
            try:
                data = json.loads(str(raw or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                data = {}
        return data if isinstance(data, dict) else {}

    def _save_all(self, data: Dict[str, Dict[str, Any]]) -> None:
        self._preferences.setValue(
            self.PREF_KEY,
            json.dumps(data, sort_keys=True, separators=(",", ":")),
        )

    def _legacy_config(self) -> PrinterConfig:
        raw = {}
        for field, pref_key in self.LEGACY_MAP.items():
            raw[field] = self._preferences.getValue(pref_key)
        return PrinterConfig.from_dict(raw)

    def migrate_legacy_to_current_machine(self) -> bool:
        migrated = self._preferences.getValue(self.MIGRATED_KEY)
        if isinstance(migrated, bool):
            already = migrated
        else:
            already = str(migrated).strip().lower() in ("1", "true", "yes", "on")
        if already:
            return False
        machine_id, _ = self.identity()
        if machine_id == "unknown":
            # Cura can instantiate extensions before the first machine stack is
            # fully available. Defer migration rather than permanently assigning
            # the user's 1.0.x target to an artificial "unknown" printer.
            return False
        data = self._load_all()
        if machine_id not in data:
            data[machine_id] = asdict(self._legacy_config())
            self._save_all(data)
        self._preferences.setValue(self.MIGRATED_KEY, True)
        return True

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
