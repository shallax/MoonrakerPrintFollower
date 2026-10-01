"""Bounded reply bodies: the byte cap every hand-read reply shares.

The two replies the file chrome reads itself — the thumbnail fetches and
the upload's acknowledgement — must never buffer an unbounded body, and
both must end with exactly one disposal. That policy lives here once.

The readyRead slot is a BOUND method of this object and the buffer rides
on the reply: a bare function connected to a QtNetwork signal is a
use-after-free trap in PyQt, and a reply that owns its own bytes cannot
outlive them.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject


class ReplyBodyReader(QObject):
    """One reader per owner; the watched replies are transient.

    An over-cap reply is aborted and its bytes dropped, so ``take`` can
    never hand a caller a truncated body as if it were whole. The
    terminal flag marks the reply whose handler has run: overflow then
    must not abort a reply already being taken apart.
    """

    def watch(self, reply, limit: int) -> None:
        reply._mpf_body = bytearray()
        reply._mpf_body_limit = limit
        reply._mpf_body_overflow = False
        reply.setReadBufferSize(256 * 1024)
        reply.readyRead.connect(self._ready)

    def take(self, reply, limit: int) -> bytes:
        self.drain(reply, limit)
        if getattr(reply, "_mpf_body_overflow", False):
            raise ValueError("reply exceeds the size cap")
        return bytes(reply._mpf_body)

    def drain(self, reply, limit: int) -> None:
        if getattr(reply, "_mpf_body_overflow", False):
            return
        body = getattr(reply, "_mpf_body", bytearray())
        reply._mpf_body = body
        # Qt 6.6 returns None when an errored/closed reply has no more
        # readable bytes. Keep any body already drained by readyRead.
        body.extend(bytes(reply.read(limit - len(body) + 1) or b""))
        if len(body) > limit:
            reply._mpf_body_overflow = True
            body.clear()
            if not getattr(reply, "_mpf_body_finished", False):
                reply.abort()

    def _ready(self) -> None:
        reply = self.sender()
        if reply is not None:
            self.drain(reply, reply._mpf_body_limit)
