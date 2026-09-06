from __future__ import annotations

from typing import Any

from .MoonrakerOutputDeviceLifecycle import MoonrakerOutputDevice as _BaseMoonrakerOutputDevice


class MoonrakerOutputDevice(_BaseMoonrakerOutputDevice):
    """Output-device adapter that reuses the active shared Moonraker session."""

    def __init__(self, application: Any, follower: Any, machine_id: str) -> None:
        super().__init__(application, follower, machine_id)

    def _shared_client_ready(self) -> bool:
        client = getattr(self._follower, "_client", None)
        if client is None or not bool(getattr(client, "connected", False)):
            return False
        status = getattr(client, "status", {})
        return isinstance(status, dict) and isinstance(status.get("print_stats"), dict)

    def _wait_for_ready(self) -> None:
        # A successful shared printer/objects query is stronger evidence that
        # Klippy is available than issuing another server/info request.
        if self._busy and self._shared_client_ready():
            self._upload_now()
            return
        super()._wait_for_ready()

    def _on_power_on(self, device: str, error: str | None) -> None:
        super()._on_power_on(device, error)
        if not error:
            client = getattr(self._follower, "_client", None)
            refresh = getattr(client, "force_refresh", None)
            if callable(refresh):
                refresh()
