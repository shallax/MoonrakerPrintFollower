from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QByteArray, QObject, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from UM.Logger import Logger


JsonCallback = Callable[[Optional[Dict[str, Any]], Optional[str]], None]


@dataclass
class TransportMetrics:
    started: int = 0
    completed: int = 0
    failed: int = 0
    total_elapsed_ms: float = 0.0

    @property
    def average_elapsed_ms(self) -> float:
        return self.total_elapsed_ms / self.completed if self.completed else 0.0


@dataclass
class _PendingRequest:
    reply: QNetworkReply
    request_id: int
    method: str
    category: str
    started_at: float


class MoonrakerHttpTransport(QObject):
    """Single HTTP transport and connection pool for one Moonraker binding.

    All ordinary JSON Moonraker traffic uses this object. Streaming download and
    multipart upload may manage their own reply lifecycle, but use the same
    QNetworkAccessManager and request builder so connection pooling, credentials,
    timeouts and request identity remain consistent across the plugin.
    """

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._base_url = ""
        self._api_key = ""
        self._generation = 0
        self._request_serial = 0
        self._pending: Dict[str, _PendingRequest] = {}
        self._metrics: Dict[str, TransportMetrics] = defaultdict(TransportMetrics)

    @property
    def network(self) -> QNetworkAccessManager:
        return self._network

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def identity(self) -> tuple[str, str]:
        return self._base_url, self._api_key

    @property
    def metrics(self) -> Dict[str, Dict[str, float | int]]:
        result: Dict[str, Dict[str, float | int]] = {}
        for category, metric in self._metrics.items():
            result[category] = {
                "started": metric.started,
                "completed": metric.completed,
                "failed": metric.failed,
                "average_elapsed_ms": metric.average_elapsed_ms,
            }
        return result

    def configure(self, base_url: str, api_key: str) -> bool:
        identity = (str(base_url or "").rstrip("/"), str(api_key or ""))
        if identity == self.identity:
            return False
        self._generation += 1
        self.cancel_all()
        self._base_url, self._api_key = identity
        return True

    def request(self, path_or_url: str, *, timeout_ms: int = 5000) -> QNetworkRequest:
        target = str(path_or_url or "")
        parsed = QUrl(target)
        if not (parsed.isValid() and parsed.scheme() in ("http", "https") and parsed.host()):
            target = self._base_url + "/" + target.lstrip("/")
        request = QNetworkRequest(QUrl(target))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"Cura Moonraker Print Follower")
        if self._api_key:
            request.setRawHeader(b"X-Api-Key", self._api_key.encode("utf-8"))
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(max(1, int(timeout_ms)))
        return request

    @staticmethod
    def _key(owner: str, channel: str) -> str:
        return f"{str(owner)}::{str(channel)}"

    def cancel(self, owner: str, channel: str) -> None:
        key = self._key(owner, channel)
        pending = self._pending.pop(key, None)
        if pending is None:
            return
        try:
            if pending.reply.isRunning():
                pending.reply.abort()
        except Exception:
            pass
        try:
            pending.reply.deleteLater()
        except Exception:
            pass

    def cancel_owner(self, owner: str) -> None:
        prefix = f"{str(owner)}::"
        for key in [item for item in self._pending if item.startswith(prefix)]:
            pending = self._pending.pop(key)
            try:
                if pending.reply.isRunning():
                    pending.reply.abort()
            except Exception:
                pass
            try:
                pending.reply.deleteLater()
            except Exception:
                pass

    def cancel_all(self) -> None:
        for key in list(self._pending):
            owner, channel = key.split("::", 1)
            self.cancel(owner, channel)

    def send_json(
        self,
        owner: str,
        channel: str,
        method: str,
        path: str,
        callback: JsonCallback,
        *,
        body: Optional[Dict[str, Any] | bytes] = None,
        replace: bool = False,
        timeout_ms: int = 5000,
        category: str = "auxiliary",
    ) -> bool:
        key = self._key(owner, channel)
        previous = self._pending.get(key)
        if previous is not None:
            try:
                running = previous.reply.isRunning()
            except Exception:
                running = False
            if running and not replace:
                return False
            self.cancel(owner, channel)

        request = self.request(path, timeout_ms=timeout_ms)
        method = str(method or "GET").upper()
        if body is not None:
            request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        if isinstance(body, dict):
            data = QByteArray(json.dumps(body, separators=(",", ":")).encode("utf-8"))
        else:
            data = QByteArray(body or b"")

        if method == "POST":
            reply = self._network.post(request, data)
        else:
            reply = self._network.get(request)

        self._request_serial += 1
        request_id = self._request_serial
        category = str(category or "auxiliary")
        pending = _PendingRequest(reply, request_id, method, category, time.monotonic())
        self._pending[key] = pending
        self._metrics[category].started += 1
        generation = self._generation
        reply.finished.connect(
            lambda r=reply, k=key, cb=callback, g=generation: self._finish_json(k, r, cb, g)
        )
        return True

    def _finish_json(self, key: str, reply: QNetworkReply, callback: JsonCallback, generation: int) -> None:
        pending = self._pending.get(key)
        if pending is None or pending.reply is not reply or generation != self._generation:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        self._pending.pop(key, None)

        elapsed_ms = max(0.0, (time.monotonic() - pending.started_at) * 1000.0)
        metric = self._metrics[pending.category]
        metric.completed += 1
        metric.total_elapsed_ms += elapsed_ms
        payload: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                error = reply.errorString()
            else:
                raw = bytes(reply.readAll()).decode("utf-8", errors="replace")
                if raw.strip():
                    decoded = json.loads(raw)
                    if not isinstance(decoded, dict):
                        raise ValueError("Moonraker returned a non-object JSON response")
                    if decoded.get("error"):
                        raise ValueError(str(decoded.get("error")))
                    payload = decoded
                else:
                    payload = {}
        except Exception as exc:
            error = str(exc)
        if error:
            metric.failed += 1

        Logger.log(
            "d",
            "MoonrakerHTTP request_id=%d category=%s channel=%s method=%s elapsed_ms=%.1f outcome=%s",
            pending.request_id,
            pending.category,
            key,
            pending.method,
            elapsed_ms,
            "error" if error else "ok",
        )
        try:
            reply.deleteLater()
        except Exception:
            pass
        callback(payload, error)
