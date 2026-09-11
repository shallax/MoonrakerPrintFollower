from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from PyQt6.QtCore import QByteArray, QObject, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from UM.Logger import Logger

from .MoonrakerProtocol import _moonraker_error_text, same_origin


JsonCallback = Callable[[Optional[Dict[str, Any]], Optional[str]], None]

# Response-size cap (panel security P2-2): the configured URL is
# user-entered and may point at something that is NOT the printer any
# more — an impostor or stale endpoint answering the poll with an
# unbounded body would otherwise be buffered whole and OOM Cura.
# Nothing legitimate exceeds a few MB (status payloads, webcam lists,
# single-file metadata); 64 MB is headroom beyond generous.
MAX_REPLY_BYTES = 64 * 1024 * 1024


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
        # Round-2 security F1 (proven in the pinned container): the
        # default redirect policy re-sends custom raw headers —
        # X-Api-Key included — across cross-origin redirects. Same-
        # origin only: a cross-origin hop fails the request instead
        # of leaking the key to the redirect target.
        if hasattr(self._network, "setRedirectPolicy"):
            self._network.setRedirectPolicy(QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        self._base_url = ""
        self._api_key = ""
        self._generation = 0
        self._request_serial = 0
        self._pending: Dict[str, _PendingRequest] = {}
        self._metrics: Dict[str, TransportMetrics] = defaultdict(TransportMetrics)
        self._trace_http = False

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

    def set_trace_http(self, enabled) -> None:
        """Per-printer diagnostics toggle: log every request at debug
        (off by default — failures always log a warning)."""
        self._trace_http = bool(enabled)

    def request(self, path_or_url: str, *, timeout_ms: int = 5000) -> QNetworkRequest:
        target = str(path_or_url or "")
        parsed = QUrl(target)
        if not (parsed.isValid() and parsed.scheme() in ("http", "https") and parsed.host()):
            target = self._base_url + "/" + target.lstrip("/")
        request = QNetworkRequest(QUrl(target))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", b"Cura Moonraker Print Follower")
        # Round-2 security F2: the key rides ONLY the printer's own
        # origin. A foreign target (a webcam on another host or port,
        # a tunnel alias) gets no key — fail-closed, the predicate is
        # origin-triple equality (scheme, host, effective port).
        if self._api_key and same_origin(self._base_url, target):
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
        elif method == "GET":
            reply = self._network.get(request)
        elif method == "DELETE":
            # Round-1 C1 / round-2 E1: file deletion is Moonraker's
            # HTTP DELETE /server/files/{root}/{filename}. The old
            # else-branch silently downgraded any unknown verb to a
            # GET — a mis-wired delete would have DOWNLOADED the
            # file and reported success.
            reply = self._network.deleteResource(request)
        else:
            raise ValueError(f"unsupported HTTP verb {method!r}")

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
                # Round-2 A2/F4: a 4xx/5xx body is still readable
                # (proven in-container), and Moonraker's own refusal
                # message lives there — "File currently in use" on a
                # 403 delete. Surface the server's words: error set,
                # payload present, so consumers keep the documented
                # refusal-vs-transport-failure distinction.
                error = reply.errorString()
                try:
                    raw_error = bytes(reply.read(MAX_REPLY_BYTES + 1))
                    if 0 < len(raw_error) <= MAX_REPLY_BYTES:
                        decoded_error = json.loads(raw_error.decode("utf-8", errors="replace"))
                        if isinstance(decoded_error, dict):
                            payload = decoded_error
                            if decoded_error.get("error"):
                                inner = decoded_error.get("error")
                                error = _moonraker_error_text(inner) if isinstance(inner, dict) else str(inner)
                            else:
                                server_words = _moonraker_error_text(decoded_error)
                                if server_words:
                                    error = server_words
                except Exception:
                    pass
            else:
                declared = reply.header(QNetworkRequest.KnownHeaders.ContentLengthHeader)
                if declared is not None and int(declared) > MAX_REPLY_BYTES:
                    raise ValueError("Moonraker response exceeds the size cap")
                raw = bytes(reply.read(MAX_REPLY_BYTES + 1))
                if len(raw) > MAX_REPLY_BYTES or reply.bytesAvailable() > 0:
                    raise ValueError("Moonraker response exceeds the size cap")
                text = raw.decode("utf-8", errors="replace")
                if text.strip():
                    decoded = json.loads(text)
                    if not isinstance(decoded, dict):
                        raise ValueError("Moonraker returned a non-object JSON response")
                    if decoded.get("error"):
                        # A Moonraker error object still leaves the
                        # server's ANSWER in the payload: consumers can
                        # distinguish "the printer refused this" (error
                        # set, payload present) from a transport-level
                        # failure (payload None) — the console's verdict
                        # colours need exactly that distinction. The
                        # script endpoint answers HTTP 200 with the
                        # WHOLE error DICT inside "error" (the author's
                        # live report: "Extrude refused: {'code': 400,
                        # 'message': ...}" — str(dict) was the error):
                        # extract the server's words, never the dict.
                        inner = decoded.get("error")
                        error = _moonraker_error_text(inner) if isinstance(inner, dict) else str(inner)
                    payload = decoded
                else:
                    payload = {}
        except Exception as exc:
            error = str(exc)
        if error:
            metric.failed += 1

        # Failures always surface; the per-request debug line is opt-in
        # (MOONRAKER_FOLLOWER_TRACE_HTTP) — at the poll cadence the
        # unconditional debug log flooded Cura's log.
        if error:
            Logger.log("w", "MoonrakerHTTP %s %s failed: %s", pending.method, key, error)
        elif self._trace_http:
            Logger.log(
                "d",
                "MoonrakerHTTP request_id=%d category=%s channel=%s method=%s elapsed_ms=%.1f outcome=ok",
                pending.request_id,
                pending.category,
                key,
                pending.method,
                elapsed_ms,
            )
        try:
            reply.deleteLater()
        except Exception:
            pass
        callback(payload, error)
