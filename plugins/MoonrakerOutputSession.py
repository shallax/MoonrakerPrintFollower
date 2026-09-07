from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .MoonrakerOutputDeviceLifecycle import MoonrakerOutputDevice as _BaseMoonrakerOutputDevice
from .MoonrakerSession import RequestCategory


class MoonrakerOutputDevice(_BaseMoonrakerOutputDevice):
    """Output device backed by the active shared Moonraker transport/session."""

    def __init__(self, application: Any, follower: Any, machine_id: str) -> None:
        super().__init__(application, follower, machine_id)
        transport = self._shared_transport()
        if transport is not None:
            # The compatibility base creates a manager before this adapter can
            # bind. No request has started yet; release that unused manager and
            # retain the shared active-printer pool for multipart and JSON work.
            legacy_network = getattr(self, "_network", None)
            shared_network = transport.network
            self._network = transport.network
            if legacy_network is not None and legacy_network is not shared_network:
                try:
                    legacy_network.deleteLater()
                except Exception:
                    pass

    def _shared_client(self):
        return getattr(self._follower, "_client", None)

    def _shared_transport(self):
        return getattr(self._shared_client(), "transport", None)

    def _transport_owner(self) -> str:
        return "output:" + str(self._machine_id)

    def _request(self, path: str):
        transport = self._shared_transport()
        if transport is not None:
            return transport.request(path, timeout_ms=15000)
        return super()._request(path)

    @staticmethod
    def _category_for(path: str, method: str) -> RequestCategory:
        path = str(path or "")
        if "device_power" in path:
            return (
                RequestCategory.POWER
                if method.upper() == "GET"
                else RequestCategory.COMMAND
            )
        if path.startswith("server/files/directory"):
            return RequestCategory.DISCOVERY
        if path in {"server/info", "printer/info"}:
            return RequestCategory.SYSTEM
        return (
            RequestCategory.COMMAND
            if method.upper() == "POST"
            else RequestCategory.AUXILIARY
        )

    def _json_request(
        self,
        method: str,
        path: str,
        callback: Callable[[Optional[Dict[str, Any]], Optional[str]], None],
        *,
        body: Optional[bytes] = None,
    ) -> None:
        if not self._busy:
            return
        transport = self._shared_transport()
        if transport is None:
            super()._json_request(method, path, callback, body=body)
            return
        transport.send_json(
            self._transport_owner(),
            "json",
            method,
            path,
            callback,
            body=body,
            replace=True,
            timeout_ms=15000,
            category=self._category_for(path, method).value,
        )

    def _shared_client_ready(self) -> bool:
        client = self._shared_client()
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
            client = self._shared_client()
            refresh = getattr(client, "force_refresh", None)
            if callable(refresh):
                refresh()

    def _cleanup(self, *, keep_message: bool = False) -> None:
        transport = self._shared_transport()
        if transport is not None:
            transport.cancel_owner(self._transport_owner())
        super()._cleanup(keep_message=keep_message)
