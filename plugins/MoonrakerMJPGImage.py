# Copyright (c) 2018 Aldo Hoeben / fieldOfView
# NetworkMJPGImage is released under the terms of the LGPLv3 or higher.
#
# Modified for Moonraker Print Follower: the receive path drains
# readyRead (not downloadProgress), parses multipart/x-mixed-replace
# framing with the SOI/EOI scan as the fallback, keeps only the newest
# complete frame, and only the render timer decodes — the network
# arrival cadence no longer decides the repaint cadence. The retained
# data limits resynchronise to the newest frame marker instead of
# reconnecting, and the QNAM lives for the item's lifetime.
# imageSizeChanged now tracks the decoded dimensions correctly.

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtQuick import QQuickPaintedItem

from UM.Logger import Logger

# The render ceiling: decode and repaint at most this often while no
# target rate is set. The source is NEVER throttled — the drain parses
# everything promptly and the superseded frames count as intentionally
# dropped. The Precise timer type keeps the presentation cadence even.
RENDER_INTERVAL_MS = 33

# The safety bounds — two SEPARATE concepts, each documented here:
#
# MAX_IN_PROGRESS_FRAME_BYTES bounds ONE frame in progress (a
# boundary/SOI seen, its EOI/declared body not yet complete). A span
# larger than this is a pathological single frame (or a broken
# stream): it is dropped with a counted resync, and the connection
# survives.
#
# RETAINED_GARBAGE_LIMIT bounds UNFRAMED data — bytes with no usable
# structure at all (no boundary marker, no SOI) while trying to
# resynchronise. Reaching it discards the garbage and keeps the
# connection.
#
# NEITHER bound applies to aggregate valid frames: complete frames are
# extracted as they arrive and only the newest is retained, so "more
# than N bytes arrived" is never an error by itself. Real camera
# frames (tens of KB to a few MB) never approach either bound.
MAX_IN_PROGRESS_FRAME_BYTES = 16 * 1000 * 1000
RETAINED_GARBAGE_LIMIT = 8 * 1000 * 1000
# A multipart part's header block beyond this is not usable framing:
# the part resynchronises at the next marker instead of waiting for a
# terminator that is not coming.
MAX_HEADER_BYTES = 4096

# The diagnostics snapshot cadence: the counters ride the QML surface
# through one low-frequency signal, never per-frame notifications.
STATS_EMIT_INTERVAL_MS = 1000

# The interval the trace summary reports on. The counts, the Qt-thread
# milliseconds and the two ages are all measured over the interval that
# just closed rather than the stream's lifetime: the complaint is a rate
# that COLLAPSES on a phase boundary, and a lifetime average hides
# exactly that.
SUMMARY_INTERVAL_S = 5.0


class MoonrakerMJPGImage(QQuickPaintedItem):
    """A plugin-owned MJPEG renderer forked from Cura's
    NetworkMJPGImage: drains the stream, keeps only the newest
    complete frame, and decodes on a bounded display cadence."""

    statsChanged = pyqtSignal()
    traceEnabledChanged = pyqtSignal()
    targetFpsChanged = pyqtSignal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._stream_buffer = bytearray()
        self._pending_frame: Optional[bytes] = None
        self._network_manager: Optional[QNetworkAccessManager] = None
        self._image_request: Optional[QNetworkRequest] = None
        self._image_reply: Optional[QNetworkReply] = None
        self._reply_finished_cb = None
        self._reply_error_cb = None
        self._image = QImage()
        self._image_rect = None

        self._source_url = QUrl()
        self._started = False
        self._mirror = False
        self._multipart_boundary: Optional[bytes] = None
        self._trace_enabled = False
        # The decode throttle (0 = the ceiling above): the pane's FPS
        # control writes it, and only the render timer's cadence — the
        # spot that decodes and repaints — follows it.
        self._target_fps = 0.0

        # The render scheduler: decode-and-paint only while the stream
        # runs, always from the newest pending frame.
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(RENDER_INTERVAL_MS)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.timeout.connect(self._render)

        # The diagnostics snapshot: the counters emit through one
        # low-frequency signal when they actually moved.
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(STATS_EMIT_INTERVAL_MS)
        self._stats_timer.timeout.connect(self._emit_stats)

        # The explicit diagnostics counters.
        self._bytes_received = 0
        self._frames_parsed = 0
        self._frames_displayed = 0
        self._latest_wins_drops = 0
        self._decode_failures = 0
        self._oversized_drops = 0
        self._requests_started = 0
        self._source_changes = 0
        self._transport_errors = 0
        self._parser_resyncs = 0
        self._buffer_high_water = 0
        self._frame_bytes_total = 0
        self._frame_bytes_max = 0
        self._decode_ms_total = 0.0
        self._decode_ms_max = 0.0
        self._stream_epoch = 0.0
        self._trace_summary_at = 0.0
        self._last_stats_snapshot = None
        self._last_stats_at = 0.0
        self._recent_incoming = 0.0
        self._recent_displayed = 0.0
        self._recent_bytes_per_sec = 0.0

        # The interval accounting (the Windows diagnosis). Everything
        # here answers one of three questions the frame rates alone
        # cannot: how much of the Qt thread this pipeline actually
        # costs (decode and drain, the only parts that run there),
        # whether a frame reached the screen late or was never parsed
        # at all (the display lag), and whether the Qt thread was
        # between drains long enough for the camera's own send to
        # block (the drain gap). The maxima are the interval's, reset
        # by every stats tick, so a spike cannot hide inside an
        # average.
        self._drain_ms_total = 0.0
        self._drain_ms_max = 0.0
        self._drain_gap_ms_max = 0.0
        self._last_drain_end = 0.0
        self._display_lag_ms_total = 0.0
        self._display_lag_ms_max = 0.0
        self._pending_arrival = 0.0
        self._decodes_rendered = 0
        self._recent_parsed_count = 0
        self._recent_displayed_count = 0
        self._recent_decodes = 0
        self._recent_interval_s = 0.0
        self._recent_decode_ms = 0.0
        self._recent_decode_ms_max = 0.0
        self._recent_decode_ms_per_frame = 0.0
        self._recent_drain_ms = 0.0
        self._recent_drain_ms_max = 0.0
        self._recent_drain_ms_per_frame = 0.0
        self._recent_drain_gap_ms = 0.0
        self._recent_display_lag_ms = 0.0
        self._recent_display_lag_ms_max = 0.0
        self._recent_pending_age_ms = 0.0
        self._last_meters = None
        self._app_state = ""

        self.setAntialiasing(True)

    # -- the QML-facing contract -------------------------------------

    def paint(self, painter: QPainter) -> None:
        if self._mirror:
            painter.drawImage(self.contentsBoundingRect(), self._image.mirrored())
            return
        painter.drawImage(self.contentsBoundingRect(), self._image)

    def setSourceURL(self, source_url: QUrl) -> None:
        if source_url == self._source_url:
            return
        self._source_url = source_url
        self._source_changes += 1
        self.sourceURLChanged.emit()
        if self._started:
            # A source change on a RUNNING stream is one transition:
            # stop the old request, start the new one — never two
            # starts. The pane's applyCamera stops first, so its
            # normal path never reaches this branch.
            self._stop_request()
            self._begin_request()

    def getSourceURL(self) -> QUrl:
        return self._source_url

    sourceURLChanged = pyqtSignal()
    source = pyqtProperty(QUrl, fget=getSourceURL, fset=setSourceURL, notify=sourceURLChanged)

    def setMirror(self, mirror: bool) -> None:
        if mirror == self._mirror:
            return
        self._mirror = mirror
        self.mirrorChanged.emit()
        self.update()

    def getMirror(self) -> bool:
        return self._mirror

    mirrorChanged = pyqtSignal()
    mirror = pyqtProperty(bool, fget=getMirror, fset=setMirror, notify=mirrorChanged)

    def setTraceEnabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._trace_enabled:
            return
        self._trace_enabled = enabled
        self.traceEnabledChanged.emit()

    def getTraceEnabled(self) -> bool:
        return self._trace_enabled

    traceEnabled = pyqtProperty(bool, fget=getTraceEnabled, fset=setTraceEnabled,
                                notify=traceEnabledChanged)

    def setTargetFps(self, fps: float) -> None:
        """The decode throttle's target, in frames per second.

        The saving is the DECODE, not the display: the drain still
        parses promptly and only the newest frame survives, while the
        render timer — the one place a JPEG becomes a QImage and the
        item repaints — follows this rate. The target may sit either
        side of the idle ceiling above: below it is the throttle the
        idle-load request asks for, above it is a camera configured to
        run faster, which is the user's call. 0 leaves the ceiling.
        """
        try:
            value = float(fps)
        except (TypeError, ValueError):
            value = 0.0
        if not (value > 0.0):
            # Also catches NaN: a non-positive target is the ceiling.
            value = 0.0
        if value == self._target_fps:
            return
        self._target_fps = value
        self._render_timer.setInterval(self.getRenderIntervalMs())
        self.targetFpsChanged.emit()

    def getTargetFps(self) -> float:
        return self._target_fps

    def getRenderIntervalMs(self) -> int:
        """The cadence the target implies: the interval IS the effective
        decode rate, because the render tick is what decodes. The 1 ms
        floor only keeps a corrupt value from spinning the timer."""
        if self._target_fps <= 0.0:
            return RENDER_INTERVAL_MS
        return max(1, int(round(1000.0 / self._target_fps)))

    targetFps = pyqtProperty(float, fget=getTargetFps, fset=setTargetFps,
                             notify=targetFpsChanged)
    renderIntervalMs = pyqtProperty(int, fget=getRenderIntervalMs,
                                    notify=targetFpsChanged)

    imageSizeChanged = pyqtSignal()

    @pyqtProperty(int, notify=imageSizeChanged)
    def imageWidth(self) -> int:
        return self._image.width()

    @pyqtProperty(int, notify=imageSizeChanged)
    def imageHeight(self) -> int:
        return self._image.height()

    # The diagnostics counters. NOT constant properties: their values
    # mutate, so they ride the low-frequency statsChanged snapshot —
    # never per-frame notifications.

    @pyqtProperty(int, notify=statsChanged)
    def bytesReceived(self) -> int:
        return self._bytes_received

    @pyqtProperty(int, notify=statsChanged)
    def framesParsed(self) -> int:
        return self._frames_parsed

    @pyqtProperty(int, notify=statsChanged)
    def framesDisplayed(self) -> int:
        return self._frames_displayed

    @pyqtProperty(int, notify=statsChanged)
    def framesDropped(self) -> int:
        # The aggregate: latest-frame-wins drops + decode failures +
        # oversized rejections. The summary log names the kinds.
        return self._latest_wins_drops + self._decode_failures + self._oversized_drops

    @pyqtProperty(int, notify=statsChanged)
    def requestsStarted(self) -> int:
        return self._requests_started

    @pyqtProperty(int, notify=statsChanged)
    def sourceChanges(self) -> int:
        return self._source_changes

    @pyqtProperty(int, notify=statsChanged)
    def transportErrors(self) -> int:
        return self._transport_errors

    @pyqtProperty(int, notify=statsChanged)
    def parserResyncs(self) -> int:
        return self._parser_resyncs

    @pyqtProperty(int, notify=statsChanged)
    def bufferHighWaterMark(self) -> int:
        return self._buffer_high_water

    @pyqtProperty(int, notify=statsChanged)
    def maximumFrameSize(self) -> int:
        return self._frame_bytes_max

    @pyqtProperty(float, notify=statsChanged)
    def incomingFPS(self) -> float:
        elapsed = max(0.001, time.monotonic() - self._stream_epoch) if self._stream_epoch else 0.0
        return round(self._frames_parsed / elapsed, 1) if elapsed else 0.0

    @pyqtProperty(float, notify=statsChanged)
    def displayedFPS(self) -> float:
        elapsed = max(0.001, time.monotonic() - self._stream_epoch) if self._stream_epoch else 0.0
        return round(self._frames_displayed / elapsed, 1) if elapsed else 0.0

    @pyqtProperty(float, notify=statsChanged)
    def recentIncomingFPS(self) -> float:
        """Delta frames over the last stats interval — the CURRENT
        smoothness, not the lifetime average."""
        return self._recent_incoming

    @pyqtProperty(float, notify=statsChanged)
    def recentDisplayedFPS(self) -> float:
        return self._recent_displayed

    @pyqtProperty(float, notify=statsChanged)
    def recentBytesPerSec(self) -> float:
        """Delta bytes over the last stats interval — the CURRENT
        MJPEG bandwidth, the number that settles whether the old
        2 MB restarts were bandwidth or buffering behaviour."""
        return self._recent_bytes_per_sec

    @pyqtProperty(float, notify=statsChanged)
    def averageFrameSize(self) -> float:
        return round(self._frame_bytes_total / max(1, self._frames_parsed), 1)

    @pyqtProperty(float, notify=statsChanged)
    def averageDecodeMs(self) -> float:
        return round(self._decode_ms_total / max(1, self._frames_displayed), 1)

    @pyqtProperty(float, notify=statsChanged)
    def maximumDecodeMs(self) -> float:
        return round(self._decode_ms_max, 1)

    # -- the stream lifecycle -----------------------------------------

    @pyqtSlot()
    def start(self) -> None:
        if not self._source_url:
            Logger.log("w", "Unable to start camera stream without target!")
            return
        if self._started and self._image_reply is not None:
            # Already running the desired stream: start() is idempotent,
            # never a destructive restart of a healthy connection.
            return
        self._started = True
        if self._stream_epoch == 0.0:
            self._stream_epoch = time.monotonic()
        self._begin_request()
        self._render_timer.start()
        self._stats_timer.start()

    @pyqtSlot()
    def stop(self) -> None:
        self._stop_request()
        self._stream_buffer = bytearray()
        self._pending_frame = None
        self._pending_arrival = 0.0
        self._last_drain_end = 0.0
        self._multipart_boundary = None
        self._started = False
        self._render_timer.stop()
        self._stats_timer.stop()
        # The last decoded frame stays on the item: the pane's
        # disconnected veil covers a stale frame by design.

    @pyqtSlot()
    def clearFrame(self) -> None:
        """Blank the painted frame (the live request): a disabled
        stream must not keep the last frame on the item — the resume
        would flash whatever was there when the stream stopped."""
        if self._image.isNull() and self._image_rect is None:
            # Already blank: nothing changed, so nothing is announced.
            return
        self._image = QImage()
        # Blanking IS a size change (NxM -> 0x0) and the remembered
        # rect must go with it, or the resumed stream's first frame
        # (the same resolution) re-announces nothing and a consumer
        # that gates on imageWidth keeps whatever it latched before
        # the blank — the dead zoom/FPS after a stream off/on.
        self._image_rect = None
        self.imageSizeChanged.emit()
        self.update()

    def _stop_request(self) -> None:
        # The QNAM owns its replies, so a stopped reply must be
        # released with deleteLater — dropping the Python reference
        # alone would leak it inside the surviving manager. The
        # signals disconnect FIRST, so a late finished/error from the
        # old reply can never reach a newer reply's state.
        reply = self._image_reply
        self._image_reply = None
        self._image_request = None
        if reply is None:
            return
        try:
            try:
                reply.readyRead.disconnect(self._drain)
            except Exception:
                pass
            try:
                reply.finished.disconnect(self._reply_finished_cb)
            except Exception:
                pass
            if hasattr(reply, "errorOccurred"):
                try:
                    reply.errorOccurred.disconnect(self._reply_error_cb)
                except Exception:
                    pass
            if not reply.isFinished():
                reply.abort()
        except Exception:
            # The wrapped C++ object may already be gone.
            pass
        try:
            reply.deleteLater()
        except Exception:
            pass

    def _begin_request(self) -> None:
        # A genuinely new stream: the parser state must not leak from
        # the previous source into this one.
        self._stream_buffer = bytearray()
        self._pending_frame = None
        self._pending_arrival = 0.0
        self._multipart_boundary = None
        self._requests_started += 1
        # The recent-delta baselines restart with the stream: a long
        # stopped interval must not dilute the first measurement. Both
        # are seeded here rather than at the first tick, so every
        # interval the counters report is the SAME window — the counts
        # and the Qt-thread milliseconds can never disagree about which
        # interval they describe.
        self._last_stats_snapshot = self._stats_snapshot()
        self._last_stats_at = time.monotonic()
        self._last_meters = (self._drain_ms_total, self._display_lag_ms_total,
                             self._decode_ms_total, self._decodes_rendered)
        self._last_drain_end = 0.0
        self._drain_ms_max = 0.0
        self._drain_gap_ms_max = 0.0
        self._recent_decode_ms_max = 0.0
        self._display_lag_ms_max = 0.0
        self._recent_incoming = 0.0
        self._recent_displayed = 0.0
        self._recent_bytes_per_sec = 0.0
        if self._network_manager is None:
            self._network_manager = QNetworkAccessManager()
        self._image_request = QNetworkRequest(self._source_url)
        self._image_reply = self._network_manager.get(self._image_request)
        reply = self._image_reply
        self._reply_finished_cb = lambda r=reply: self._on_finished(r)
        self._reply_error_cb = lambda _e, r=reply: self._on_error(r)
        self._image_reply.readyRead.connect(self._drain)
        reply.finished.connect(self._reply_finished_cb)
        if hasattr(reply, "errorOccurred"):
            reply.errorOccurred.connect(self._reply_error_cb)

    def _on_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._image_reply:
            return  # a stale queued callback from a stopped request
        # The stream ended. The pane's existing watchdog and nonce
        # path own the retry — the renderer never reconnects itself.
        self._stop_request()
        self._started = False
        self._render_timer.stop()
        self._stats_timer.stop()

    def _on_error(self, reply: QNetworkReply) -> None:
        if reply is not self._image_reply:
            return  # a stale queued callback from a stopped request
        # A genuine transport failure: recorded, never auto-restarted
        # (the caller's watchdog drives the explicit restart).
        self._transport_errors += 1

    # -- the receive path ---------------------------------------------

    def _detect_boundary(self) -> None:
        # Prefer proper multipart framing: the boundary comes from the
        # response's raw Content-Type header (rawHeader gives the wire
        # bytes; the parsed header accessor is the wrong type to
        # parse). Streams without a usable boundary ride the SOI/EOI
        # scan.
        try:
            content_type = bytes(self._image_reply.rawHeader(b"Content-Type"))
        except Exception:
            return
        if not content_type:
            return
        parts = content_type.split(b";")
        if b"multipart/x-mixed-replace" not in parts[0].lower():
            return
        for part in parts[1:]:
            key, _sep, value = part.strip().partition(b"=")
            if key.strip().lower() == b"boundary":
                boundary = value.strip().strip(b'"')
                # boundary=--frame is the non-compliant real-world
                # form: normalise it so the delimiter is "--frame",
                # never "----frame".
                if boundary.startswith(b"--"):
                    boundary = boundary[2:]
                if boundary:
                    self._multipart_boundary = boundary
                return

    def _drain(self) -> None:
        started = time.perf_counter()
        # The idle gap between drains. The socket is read only when the
        # Qt thread returns to its event loop, so this is the starvation
        # the camera's own send sees: a source that cannot be drained
        # blocks, and the frames it would have sent are the ones the
        # user reads as "backing up".
        if self._last_drain_end:
            gap = (started - self._last_drain_end) * 1000.0
            if gap > self._drain_gap_ms_max:
                self._drain_gap_ms_max = gap
        if self._image_reply is None:
            self._last_drain_end = started
            return
        data = bytes(self._image_reply.readAll())
        if data:
            self._bytes_received += len(data)
            self._stream_buffer += data
            self._buffer_high_water = max(self._buffer_high_water, len(self._stream_buffer))
            if self._multipart_boundary is None:
                self._detect_boundary()
            if self._multipart_boundary is not None:
                self._parse_multipart()
            else:
                self._parse_scan()
            self._apply_limits()
        self._last_drain_end = time.perf_counter()
        elapsed = (self._last_drain_end - started) * 1000.0
        self._drain_ms_total += elapsed
        if elapsed > self._drain_ms_max:
            self._drain_ms_max = elapsed

    def _consume_frame(self, frame: bytes) -> None:
        # The single per-frame validation point every parser path
        # feeds: a COMPLETE frame beyond the documented maximum is
        # pathological — rejected here, before any decode, never by
        # reconnecting.
        if len(frame) > MAX_IN_PROGRESS_FRAME_BYTES:
            self._oversized_drops += 1
            self._parser_resyncs += 1
            self._trace("complete frame exceeds the maximum; rejected")
            return
        self._frames_parsed += 1
        self._frame_bytes_total += len(frame)
        self._frame_bytes_max = max(self._frame_bytes_max, len(frame))
        if self._pending_frame is not None:
            # Latest frame wins: a live camera's stale frames are
            # intentionally dropped before any decode cost.
            self._latest_wins_drops += 1
        self._pending_frame = frame
        # The arrival stamp the display lag is measured from: the age
        # of the frame when it reaches the screen is the latency the
        # user sees, and the one number a decode-rate readout cannot
        # show.
        self._pending_arrival = time.monotonic()

    @staticmethod
    def _content_length(header_block: bytes) -> Optional[int]:
        for line in header_block.split(b"\r\n"):
            if line[:14].lower() == b"content-length":
                try:
                    value = int(line.partition(b":")[2].strip())
                except (ValueError, TypeError):
                    return None
                return value if value > 0 else None
        return None

    def _resync_at(self, offset: int, drop: int, keep: int, message: str) -> None:
        """Drop through a structural point (a boundary marker or an
        SOI) AND the offending remainder, keeping only the minimal
        suffix that could straddle a split future marker. The
        connection is never the response."""
        self._parser_resyncs += 1
        del self._stream_buffer[:offset + drop]
        if len(self._stream_buffer) > keep:
            del self._stream_buffer[:-(keep)]
        self._trace(message)

    def _parse_multipart(self) -> None:
        marker = b"--" + self._multipart_boundary
        mlen = len(marker)
        buffer = self._stream_buffer
        while True:
            idx = buffer.find(marker)
            if idx < 0:
                # Keep only the tail that could straddle a marker.
                if len(buffer) > mlen - 1:
                    del buffer[:-(mlen - 1)]
                return
            if idx > 0:
                # Garbage before the first boundary (a preamble or
                # junk): drop it.
                del buffer[:idx]
            if buffer[mlen:mlen + 2] == b"--":
                # The closing marker: the stream's end.
                del buffer[:mlen + 2]
                continue
            headers_end = buffer.find(b"\r\n\r\n", mlen)
            if headers_end < 0:
                if len(buffer) - mlen > MAX_HEADER_BYTES:
                    # The header block outgrew its bound without a
                    # terminator: this part is malformed. Resynchronise
                    # at the next marker instead of waiting for a
                    # terminator that is not coming.
                    self._resync_at(0, mlen, mlen - 1,
                                    "multipart header block outgrew its bound; resynchronised")
                    continue
                return
            if headers_end - mlen > MAX_HEADER_BYTES:
                # The header block outgrew its bound and then
                # terminated anyway: still malformed — reject the part
                # rather than accepting an arbitrarily large header.
                self._resync_at(0, mlen, mlen - 1,
                                "multipart header block exceeded its bound; part rejected")
                continue
            header_block = bytes(buffer[mlen + 2:headers_end])
            body_start = headers_end + 4
            content_length = self._content_length(header_block)
            if content_length is not None:
                if content_length > MAX_IN_PROGRESS_FRAME_BYTES:
                    # A single declared frame beyond the documented
                    # maximum: pathological — drop the declaration and
                    # the partial body, keep the connection.
                    self._resync_at(0, mlen, mlen - 1,
                                    "declared frame exceeds the maximum; dropped")
                    continue
                if len(buffer) - body_start < content_length:
                    return  # the part is incomplete: wait for more
                frame = bytes(buffer[body_start:body_start + content_length])
                del buffer[:body_start + content_length]
                self._consume_frame(frame)
            else:
                # No usable Content-Length: the SOI/EOI scan within
                # this part, bounded by the next marker.
                next_idx = buffer.find(marker, body_start)
                span_end = next_idx if next_idx >= 0 else len(buffer)
                span = buffer[body_start:span_end]
                soi = span.find(b"\xff\xd8")
                eoi = span.find(b"\xff\xd9", soi + 2) if soi >= 0 else -1
                if soi >= 0 and eoi >= 0:
                    frame = bytes(span[soi:eoi + 2])
                    del buffer[:body_start + eoi + 2]
                    self._consume_frame(frame)
                elif next_idx >= 0:
                    # A boundary with no decodable frame inside: the
                    # part was empty or malformed — drop it.
                    del buffer[:next_idx]
                else:
                    return  # incomplete

    def _parse_scan(self) -> None:
        # The compatibility fallback: raw concatenated JPEGs without
        # usable multipart framing.
        buffer = self._stream_buffer
        while True:
            soi = buffer.find(b"\xff\xd8")
            if soi < 0:
                if len(buffer) > 2:
                    del buffer[:-2]
                break
            if soi > 0:
                del buffer[:soi]
            eoi = buffer.find(b"\xff\xd9", 2)
            if eoi < 0:
                break  # a partial frame: wait for the next chunk
            frame = bytes(buffer[:eoi + 2])
            del buffer[:eoi + 2]
            self._consume_frame(frame)

    def _apply_limits(self) -> None:
        buffer = self._stream_buffer
        if not buffer:
            return
        marker = b"--" + self._multipart_boundary if self._multipart_boundary else None
        structural = -1
        drop = 0
        if marker is not None:
            structural = buffer.rfind(marker)
            drop = len(marker)
        if structural < 0:
            structural = buffer.rfind(b"\xff\xd8")
            drop = 2
        if structural >= 0:
            span = len(buffer) - structural
            if span > MAX_IN_PROGRESS_FRAME_BYTES:
                # One in-progress frame (or part) beyond the maximum:
                # pathological. Drop THROUGH the structural point —
                # whatever follows (usually nothing) becomes the new
                # resynchronisation point, and the connection survives.
                keep = drop - 1
                self._resync_at(structural, drop, keep,
                                "in-progress frame exceeds the maximum; dropped")
        elif len(buffer) > RETAINED_GARBAGE_LIMIT:
            # Unframed data (no marker, no SOI) beyond the garbage
            # bound: discard it and keep the connection.
            self._parser_resyncs += 1
            self._trace("retained garbage limit reached; discarded")
            self._stream_buffer = bytearray()

    # -- the render scheduler -----------------------------------------

    def _render(self) -> None:
        if self._pending_frame is None:
            return
        frame = self._pending_frame
        arrival = self._pending_arrival
        self._pending_frame = None
        self._pending_arrival = 0.0
        started = time.perf_counter()
        image = QImage.fromData(frame)
        elapsed = (time.perf_counter() - started) * 1000.0
        self._decode_ms_total += elapsed
        self._decode_ms_max = max(self._decode_ms_max, elapsed)
        if elapsed > self._recent_decode_ms_max:
            self._recent_decode_ms_max = elapsed
        if image.isNull():
            self._decode_failures += 1
            return
        self._frames_displayed += 1
        self._decodes_rendered += 1
        if arrival:
            # How old the frame was when it reached the screen: a
            # stalled Qt thread shows up here first, because the frame
            # is already in hand and only the hand-off is late.
            lag = (time.monotonic() - arrival) * 1000.0
            self._display_lag_ms_total += lag
            if lag > self._display_lag_ms_max:
                self._display_lag_ms_max = lag
        # The new image lands BEFORE the notify: imageWidth and
        # imageHeight must already expose the new dimensions when the
        # signal fires.
        self._image = image
        rect = image.rect()
        if self._image_rect is None or rect != self._image_rect:
            self._image_rect = rect
            self.imageSizeChanged.emit()
        self.update()

    # -- the diagnostics snapshot -------------------------------------

    def _stats_snapshot(self) -> tuple:
        dropped = self._latest_wins_drops + self._decode_failures + self._oversized_drops
        return (self._bytes_received, self._frames_parsed, self._frames_displayed,
                dropped, self._requests_started, self._source_changes,
                self._transport_errors, self._parser_resyncs, self._buffer_high_water,
                self._frame_bytes_max)

    def _emit_stats(self) -> None:
        snapshot = self._stats_snapshot()
        now = time.monotonic()
        recent = (self._recent_incoming, self._recent_displayed)
        if self._last_stats_snapshot is not None:
            interval = max(0.001, now - self._last_stats_at)
            self._recent_incoming = round(
                (snapshot[1] - self._last_stats_snapshot[1]) / interval, 1)
            self._recent_displayed = round(
                (snapshot[2] - self._last_stats_snapshot[2]) / interval, 1)
            self._recent_bytes_per_sec = round(
                (snapshot[0] - self._last_stats_snapshot[0]) / interval, 1)
            self._recent_interval_s = round(interval, 2)
            self._recent_parsed_count = snapshot[1] - self._last_stats_snapshot[1]
            self._recent_displayed_count = snapshot[2] - self._last_stats_snapshot[2]
            meters = (self._drain_ms_total, self._display_lag_ms_total,
                      self._decode_ms_total, self._decodes_rendered)
            if self._last_meters is not None:
                drain_ms = meters[0] - self._last_meters[0]
                lag_ms = meters[1] - self._last_meters[1]
                decode_ms = meters[2] - self._last_meters[2]
                decodes = meters[3] - self._last_meters[3]
                self._recent_decodes = decodes
                # The Qt-thread share of the interval, in the unit the
                # frame-rate complaint is really about.
                self._recent_decode_ms = round(decode_ms / interval, 1)
                self._recent_drain_ms = round(drain_ms / interval, 1)
                self._recent_decode_ms_per_frame = round(decode_ms / max(1, decodes), 2)
                self._recent_drain_ms_per_frame = round(
                    drain_ms / max(1, self._recent_parsed_count), 2)
                self._recent_decode_ms_max = round(self._recent_decode_ms_max, 2)
                self._recent_drain_ms_max = round(self._drain_ms_max, 2)
                self._recent_drain_gap_ms = round(self._drain_gap_ms_max, 1)
                self._recent_display_lag_ms = round(lag_ms / max(1, decodes), 1)
                self._recent_display_lag_ms_max = round(self._display_lag_ms_max, 1)
            self._last_meters = meters
        # The maxima are the interval's: reset here, not on the summary's
        # slower cadence, so a spike is attributed to the interval it
        # happened in.
        self._drain_ms_max = 0.0
        self._drain_gap_ms_max = 0.0
        self._display_lag_ms_max = 0.0
        # The age of the frame waiting to be rendered RIGHT NOW: the
        # backlog a displayed rate cannot show, because a frame that
        # never reached the screen never entered any rate. Read at the
        # tick, so it is a sample rather than an interval aggregate.
        self._recent_pending_age_ms = round(
            (now - self._pending_arrival) * 1000.0, 1) if self._pending_arrival else 0.0
        self._app_state = self._current_app_state()
        if snapshot != self._last_stats_snapshot or \
                (self._recent_incoming, self._recent_displayed) != recent:
            self.statsChanged.emit()
        self._last_stats_snapshot = snapshot
        self._last_stats_at = now
        # The periodic summary rides the stats cadence: a parse or
        # render stall must not silence the very telemetry that
        # diagnoses it.
        self._trace_summary()

    def _current_app_state(self) -> str:
        # Which application state the interval's numbers were taken in.
        # The owner's complaint is a rate that collapses when Cura is
        # not the active window, and a rate read in one state cannot
        # show that; the label is what makes two pastes comparable.
        try:
            state = QGuiApplication.applicationState()
        except Exception:
            return "unknown"
        # getattr, not attribute access: the binding does not expose
        # every member on every PyQt6 version, and a missing one must
        # degrade to "unknown" rather than raise inside the timer.
        for name, label in (("ApplicationActive", "active"),
                            ("ApplicationInactive", "inactive"),
                            ("ApplicationHidden", "hidden"),
                            ("ApplicationSuspended", "suspended")):
            candidate = getattr(Qt.ApplicationState, name, None)
            if candidate is not None and state == candidate:
                return label
        return "unknown"

    def _trace(self, message: str) -> None:
        if self._trace_enabled:
            Logger.log("i", "Moonraker MJPEG: %s", message)

    def _summary_line(self) -> tuple:
        """The summary's format and its arguments, kept separate from
        the logging call so the numbers the owner pastes are the
        numbers a test can assert on."""
        elapsed = max(0.001, time.monotonic() - self._stream_epoch)
        fmt = (
            "Moonraker MJPEG summary [%s] over %.2f s: parsed %d frames (%.1f fps), "
            "displayed %d frames (%.1f fps), render tick %d ms (asked %.1f fps), "
            "latest-frame drops %.1f fps, "
            "decode failures %d, oversized drops %d, "
            "Qt thread %.1f ms/s (decode %.1f, drain %.1f), "
            "decode %.2f ms/frame (max %.2f), drain %.2f ms/frame (max %.2f ms), "
            "drain gap max %.1f ms, display lag %.1f ms mean (max %.1f ms), "
            "pending frame age %.1f ms, bandwidth %.1f KB/s (%.2f Mbps), "
            "average frame %.1f KB, maximum frame %.1f KB, "
            "parser buffer high-water %.1f KB, requests %d, source changes %d, "
            "transport errors %d, parser resyncs %d, "
            "lifetime parsed %.1f fps, displayed %.1f fps"
        )
        values = (
            self._app_state or "unknown",
            self._recent_interval_s,
            self._recent_parsed_count,
            self._recent_incoming,
            self._recent_displayed_count,
            self._recent_displayed,
            # The cadence the pane asked for, beside the rate that came
            # out of it: the pair is what tells a display rate capped by
            # the request from one the Qt thread could not keep up with.
            self._render_timer.interval(),
            self._target_fps,
            self._latest_wins_drops / elapsed,
            self._decode_failures,
            self._oversized_drops,
            self._recent_decode_ms + self._recent_drain_ms,
            self._recent_decode_ms,
            self._recent_drain_ms,
            self._recent_decode_ms_per_frame,
            self._recent_decode_ms_max,
            self._recent_drain_ms_per_frame,
            self._recent_drain_ms_max,
            self._recent_drain_gap_ms,
            self._recent_display_lag_ms,
            self._recent_display_lag_ms_max,
            self._recent_pending_age_ms,
            self._recent_bytes_per_sec / 1000.0,
            self._recent_bytes_per_sec * 8.0 / 1000.0 / 1000.0,
            self._frame_bytes_total / max(1, self._frames_parsed) / 1000.0,
            self._frame_bytes_max / 1000.0,
            self._buffer_high_water / 1000.0,
            self._requests_started,
            self._source_changes,
            self._transport_errors,
            self._parser_resyncs,
            self._frames_parsed / elapsed,
            self._frames_displayed / elapsed,
        )
        return fmt, values

    def _trace_summary(self) -> None:
        if not self._trace_enabled:
            return
        now = time.monotonic()
        if now - self._trace_summary_at < SUMMARY_INTERVAL_S:
            return
        self._trace_summary_at = now
        fmt, values = self._summary_line()
        Logger.log("i", fmt, *values)

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
