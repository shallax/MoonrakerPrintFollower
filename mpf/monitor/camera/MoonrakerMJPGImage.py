# Copyright (c) 2018 Aldo Hoeben / fieldOfView
# NetworkMJPGImage is released under the terms of the LGPLv3 or higher.
#
# Modified for Moonraker Print Follower: the receive path drains
# readyRead (not downloadProgress), parses multipart/x-mixed-replace
# framing with the SOI/EOI scan as the fallback, keeps only the newest
# complete frame, and only the render timer hands a frame to the
# decoder — the network arrival cadence no longer decides the repaint
# cadence. The retained
# data limits resynchronise to the newest frame marker instead of
# reconnecting, and the QNAM lives for the item's lifetime.
# imageSizeChanged now tracks the decoded dimensions correctly.

from __future__ import annotations

from .FrameDecoder import FrameDecoder
from .CameraStatistics import CameraStatistics
from .MJPEGParser import MJPEGParser

import math
import time
from typing import Optional

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtQuick import QQuickPaintedItem
from PyQt6 import sip

from UM.Logger import Logger


def _receiver_deleted(receiver: QObject) -> bool:
    """True when Python still holds a receiver whose C++ side is gone."""
    try:
        return sip.isdeleted(receiver)
    except Exception:
        return False

# The render ceiling: one decode and a repaint at most this often
# while no target rate is set. In MJPEG mode the drain parses everything
# promptly and superseded frames count as intentionally dropped. Snapshot
# mode uses the target rate for one-shot GETs as well as decoding.
# The Precise timer type keeps the presentation cadence even.
RENDER_INTERVAL_MS = 33

# The diagnostics snapshot cadence: the counters ride the QML surface
# through one low-frequency signal, never per-frame notifications.
STATS_EMIT_INTERVAL_MS = 1000
SNAPSHOT_REQUEST_TIMEOUT_MS = 10000

# The interval the trace summary reports on. The counts, the Qt-thread
# milliseconds and the two ages are all measured over the interval that
# just closed rather than the stream's lifetime: the complaint is a rate
# that COLLAPSES on a phase boundary, and a lifetime average hides
# exactly that.
SUMMARY_INTERVAL_S = 5.0

def _horizontal_mirror(image: QImage) -> QImage:
    """A horizontal flip that survives every Qt the plugin meets.

    Qt 6.9+ renamed this to flipped(); the classic mirrored() called
    with no arguments is a silent no-op on at least Qt 6.11 — an
    unflipped copy comes back — so the explicit forms are used:
    flipped() where it exists, mirrored(True, False) on Cura 5.13's
    pinned Qt 6.6.
    """
    flipped = getattr(image, "flipped", None)
    if flipped is not None:
        try:
            return flipped(Qt.Orientation.Horizontal)
        except TypeError:
            pass
    return image.mirrored(True, False)


class MoonrakerMJPGImage(QQuickPaintedItem):
    """A plugin-owned MJPEG renderer forked from Cura's
    NetworkMJPGImage: drains the stream, keeps only the newest
    complete frame, and decodes on a bounded display cadence of its own
    thread."""

    statsChanged = pyqtSignal()
    traceEnabledChanged = pyqtSignal()
    targetFpsChanged = pyqtSignal()
    snapshotModeChanged = pyqtSignal()
    detectionReceiverChanged = pyqtSignal()

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self._stats = CameraStatistics()
        self._parser = MJPEGParser(self._accept_frame, self._trace, self._stats)

        self._pending_frame: Optional[bytes] = None
        self._network_manager: Optional[QNetworkAccessManager] = None
        self._image_request: Optional[QNetworkRequest] = None
        self._image_reply: Optional[QNetworkReply] = None
        self._reply_finished_cb = None
        self._reply_error_cb = None
        self._image = QImage()
        self._detection_receiver = None
        self._detection_faulted = False
        self._image_rect = None

        self._source_url = QUrl()
        self._started = False
        self._mirror = False
        self._trace_enabled = False
        # The pane's FPS control sets the decode cadence and, in
        # snapshot mode, the interval between one-shot requests.
        self._target_fps = 0.0
        self._snapshot_mode = False
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setSingleShot(True)
        self._snapshot_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._snapshot_timer.timeout.connect(self._begin_snapshot_request)
        self._snapshot_timeout_timer = QTimer(self)
        self._snapshot_timeout_timer.setSingleShot(True)
        self._snapshot_timeout_timer.timeout.connect(self._on_snapshot_timeout)

        # The render scheduler: install-and-paint only while the stream
        # runs, always from the newest pending frame.
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(RENDER_INTERVAL_MS)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.timeout.connect(self._render)
        self._dispatch_timer = QTimer(self)
        self._dispatch_timer.setSingleShot(True)
        self._dispatch_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._dispatch_timer.timeout.connect(self._dispatch_from_completion)

        # The decode worker: the JPEG decode is the one large piece of
        # work this plugin used to do on the Qt thread, and it is a
        # value transform — bytes in, QImage out — so it runs here
        # instead. Created with the stream, joined by stop().
        self._decoder: Optional[FrameDecoder] = None
        self._decode_generation = 0
        self._decode_in_flight = 0
        self._in_flight_arrival = 0.0
        # Every hand-over shares one deadline, independent of whether
        # a render tick or a worker completion requested it.
        self._last_dispatch_at = 0.0

        # The diagnostics snapshot: the counters emit through one
        # low-frequency signal when they actually moved.
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(STATS_EMIT_INTERVAL_MS)
        self._stats_timer.timeout.connect(self._emit_stats)

        # The explicit diagnostics counters.
        self._trace_summary_at = 0.0
        self._pending_arrival = 0.0

        self.setAntialiasing(True)
        # Ignored by Qt 6.0-6.8 (including Cura 5.13's Qt 6.6).
        # Qt 6.9+ can paint directly into an OpenGL framebuffer instead
        # of rasterising an intermediate image and uploading it.
        self.setRenderTarget(QQuickPaintedItem.RenderTarget.FramebufferObject)

    # -- the QML-facing contract -------------------------------------

    def paint(self, painter: QPainter) -> None:
        if self._mirror:
            painter.drawImage(self.contentsBoundingRect(), _horizontal_mirror(self._image))
            return
        painter.drawImage(self.contentsBoundingRect(), self._image)

    def setSourceURL(self, source_url: QUrl) -> None:
        if source_url == self._source_url:
            return
        self._source_url = source_url
        self._stats.source_changes += 1
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

    def setDetectionReceiver(self, receiver: QObject | None) -> None:
        if receiver is self._detection_receiver:
            return
        self._detection_receiver = receiver
        self._detection_faulted = False
        self.detectionReceiverChanged.emit()

    def _deliver_detection_frame(self, image: QImage) -> None:
        """The frame hand-off runs inside the Qt thread's install slot.

        An exception escaping a slot aborts Cura, so a receiver that
        raises or whose C++ side has been deleted is contained here: a
        deleted receiver is detached (the binding clears), anything
        else is reported once and retried on the next frame, because a
        silently skipped frame is worse for detection than a repeated
        call.
        """
        receiver = self._detection_receiver
        if receiver is None:
            return
        try:
            receiver.acceptDetectionFrame(image)
        except Exception:
            if not self._detection_faulted:
                self._detection_faulted = True
                Logger.logException("e", "The detection receiver refused an analysed frame")
            if _receiver_deleted(receiver):
                self.setDetectionReceiver(None)

    def getDetectionReceiver(self) -> QObject | None:
        return self._detection_receiver

    detectionReceiver = pyqtProperty(QObject, fget=getDetectionReceiver,
                                      fset=setDetectionReceiver, notify=detectionReceiverChanged)

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
        """The requested display rate and, for snapshots, poll rate.

        MJPEG drains promptly and keeps only its newest frame. The
        render timer — the one place a JPEG becomes a QImage and the
        item repaints — follows this rate. Snapshot mode also waits
        this interval after each completed GET. The target may sit either
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
        if self._snapshot_timer.isActive():
            self._snapshot_timer.start(self.getRenderIntervalMs())
        self.targetFpsChanged.emit()

    def setSnapshotMode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._snapshot_mode:
            return
        self._snapshot_mode = enabled
        self.snapshotModeChanged.emit()
        if self._started:
            # Closing the continuous MJPEG reply is what saves network
            # bandwidth. Only the selected snapshot endpoint is fetched
            # after this transition.
            self._snapshot_timer.stop()
            self._stop_request()
            self._begin_request()

    def getSnapshotMode(self) -> bool:
        return self._snapshot_mode

    snapshotMode = pyqtProperty(bool, fget=getSnapshotMode, fset=setSnapshotMode,
                                notify=snapshotModeChanged)

    def getTargetFps(self) -> float:
        return self._target_fps

    def getRenderIntervalMs(self) -> int:
        """The cadence the target implies: the interval IS the effective
        decode rate, because the render tick is the one hand-over per
        decode. The 1 ms floor only keeps a corrupt value from spinning
        the timer."""
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
        return self._stats.bytes_received

    @pyqtProperty(int, notify=statsChanged)
    def framesParsed(self) -> int:
        return self._stats.frames_parsed

    @pyqtProperty(int, notify=statsChanged)
    def framesDisplayed(self) -> int:
        return self._stats.frames_displayed

    @pyqtProperty(int, notify=statsChanged)
    def framesDropped(self) -> int:
        # The aggregate: latest-frame-wins drops + decode failures +
        # oversized rejections. The summary log names the kinds.
        return self._stats.latest_wins_drops + self._stats.decode_failures + self._stats.oversized_drops

    @pyqtProperty(int, notify=statsChanged)
    def requestsStarted(self) -> int:
        return self._stats.requests_started

    @pyqtProperty(int, notify=statsChanged)
    def sourceChanges(self) -> int:
        return self._stats.source_changes

    @pyqtProperty(int, notify=statsChanged)
    def transportErrors(self) -> int:
        return self._stats.transport_errors

    @pyqtProperty(int, notify=statsChanged)
    def parserResyncs(self) -> int:
        return self._stats.parser_resyncs

    @pyqtProperty(int, notify=statsChanged)
    def bufferHighWaterMark(self) -> int:
        return self._stats.buffer_high_water

    @pyqtProperty(int, notify=statsChanged)
    def maximumFrameSize(self) -> int:
        return self._stats.frame_bytes_max

    @pyqtProperty(float, notify=statsChanged)
    def incomingFPS(self) -> float:
        elapsed = max(0.001, time.monotonic() - self._stats.stream_epoch) if self._stats.stream_epoch else 0.0
        return round(self._stats.frames_parsed / elapsed, 1) if elapsed else 0.0

    @pyqtProperty(float, notify=statsChanged)
    def displayedFPS(self) -> float:
        elapsed = max(0.001, time.monotonic() - self._stats.stream_epoch) if self._stats.stream_epoch else 0.0
        return round(self._stats.frames_displayed / elapsed, 1) if elapsed else 0.0

    @pyqtProperty(float, notify=statsChanged)
    def recentIncomingFPS(self) -> float:
        """Delta frames over the last stats interval — the CURRENT
        smoothness, not the lifetime average."""
        return self._stats.recent_incoming

    @pyqtProperty(float, notify=statsChanged)
    def recentDisplayedFPS(self) -> float:
        return self._stats.recent_displayed

    @pyqtProperty(float, notify=statsChanged)
    def recentBytesPerSec(self) -> float:
        """Delta bytes over the last stats interval — the CURRENT
        MJPEG bandwidth, the number that settles whether the old
        2 MB restarts were bandwidth or buffering behaviour."""
        return self._stats.recent_bytes_per_sec

    @pyqtProperty(float, notify=statsChanged)
    def averageFrameSize(self) -> float:
        return round(self._stats.frame_bytes_total / max(1, self._stats.frames_parsed), 1)

    @pyqtProperty(float, notify=statsChanged)
    def averageDecodeMs(self) -> float:
        return round(self._stats.decode_ms_total / max(1, self._stats.frames_displayed), 1)

    @pyqtProperty(float, notify=statsChanged)
    def maximumDecodeMs(self) -> float:
        return round(self._stats.decode_ms_max, 1)

    # -- the stream lifecycle -----------------------------------------

    @pyqtSlot()
    def start(self) -> None:
        if not self._source_url:
            Logger.log("w", "Unable to start camera stream without target!")
            return
        if self._started and (self._image_reply is not None or self._snapshot_mode):
            # Already running the desired stream: start() is idempotent,
            # never a destructive restart of a healthy connection.
            return
        self._started = True
        if self._stats.stream_epoch == 0.0:
            self._stats.stream_epoch = time.monotonic()
        self._begin_request()
        if self._decoder is None:
            self._decoder = FrameDecoder(self)
            # Queued explicitly: the result arrives on the worker and is
            # installed on this thread, and the connection type is part
            # of the contract rather than of the emitting thread.
            self._decoder.decoded.connect(self._on_decoded,
                                          Qt.ConnectionType.QueuedConnection)
        if not self._decoder.isRunning():
            self._decoder.start()
        self._render_timer.start()
        self._stats_timer.start()

    @pyqtSlot()
    def stop(self) -> None:
        self._snapshot_timer.stop()
        self._snapshot_timeout_timer.stop()
        self._stop_request()
        self._parser.reset()
        self._pending_frame = None
        self._pending_arrival = 0.0
        self._stats.last_drain_end = 0.0
        self._started = False
        self._render_timer.stop()
        self._dispatch_timer.stop()
        self._stats_timer.stop()
        # A decode in flight belongs to a stream that no longer exists:
        # bumping the generation discards its result on arrival, and
        # the join means no worker outlives the items it would install
        # into.
        self._decode_generation += 1
        self._decode_in_flight = 0
        self._in_flight_arrival = 0.0
        if self._decoder is not None and not self._decoder.stop():
            # The worker could not be joined and now belongs to nobody:
            # a restart builds a fresh one rather than reusing a thread
            # that is still winding down.
            self._decoder = None
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
        self._snapshot_timeout_timer.stop()
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
        self._snapshot_timer.stop()
        self._parser.reset()
        self._pending_frame = None
        self._pending_arrival = 0.0
        # A result from the previous source must never be installed on
        # this one: the generation is what a late decode is measured
        # against.
        self._decode_generation += 1
        self._decode_in_flight = 0
        self._in_flight_arrival = 0.0
        # The recent-delta baselines restart with the stream: a long
        # stopped interval must not dilute the first measurement. Both
        # are seeded here rather than at the first tick, so every
        # interval the counters report is the SAME window — the counts
        # and the Qt-thread milliseconds can never disagree about which
        # interval they describe.
        self._stats.last_stats_snapshot = self._stats.snapshot()
        self._stats.last_stats_at = time.perf_counter()
        self._stats.last_meters = (self._stats.drain_ms_total, self._stats.display_lag_ms_total,
                             self._stats.decode_ms_total, self._stats.decodes_rendered,
                             self._stats.tick_ms_total, self._stats.install_ms_total,
                             self._stats.queue_wait_ms_total, self._stats.delivery_ms_total,
                             self._stats.round_trip_ms_total)
        self._stats.last_drain_end = 0.0
        self._last_dispatch_at = 0.0
        self._dispatch_timer.stop()
        self._stats.drain_ms_max = 0.0
        self._stats.drain_gap_ms_max = 0.0
        self._stats.recent_decode_ms_max = 0.0
        self._stats.display_lag_ms_max = 0.0
        self._stats.round_trip_ms_max = 0.0
        self._stats.recent_round_trip_ms_max = 0.0
        self._stats.recent_incoming = 0.0
        self._stats.recent_displayed = 0.0
        self._stats.recent_bytes_per_sec = 0.0
        self._open_request()

    def _begin_snapshot_request(self) -> None:
        if not self._started or not self._snapshot_mode or self._image_reply is not None:
            return
        # A snapshot is one independent JPEG. Keep diagnostic counters
        # and decode state across polls, but never parse a previous
        # response's tail as part of the next image.
        self._parser.reset()
        self._open_request()

    def _open_request(self) -> None:
        self._stats.requests_started += 1
        if self._network_manager is None:
            self._network_manager = QNetworkAccessManager()
        self._image_request = QNetworkRequest(self._source_url)
        if self._snapshot_mode:
            self._image_request.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                                             QNetworkRequest.CacheLoadControl.AlwaysNetwork)
            self._image_request.setRawHeader(b"Cache-Control", b"no-cache")
        self._image_reply = self._network_manager.get(self._image_request)
        if self._snapshot_mode:
            self._snapshot_timeout_timer.start(SNAPSHOT_REQUEST_TIMEOUT_MS)
        reply = self._image_reply
        self._reply_finished_cb = (lambda r=reply: self._on_snapshot_finished(r)) if self._snapshot_mode \
            else (lambda r=reply: self._on_finished(r))
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
        self._dispatch_timer.stop()
        self._stats_timer.stop()

    def _on_snapshot_finished(self, reply: QNetworkReply) -> None:
        if reply is not self._image_reply:
            return
        # Qt may emit finished with the last bytes still buffered.
        self._drain()
        payload = self._parser.take_snapshot()
        if payload:
            # A snapshot is one whole response, not a multipart stream.
            # Let QImageReader detect its image format on the decoder
            # thread (some snapshot endpoints return PNG).
            self._parser.consume_frame(payload)
            self._dispatch_from_completion()
        self._stop_request()
        if self._started and self._snapshot_mode:
            # Start the next poll only after the previous response has
            # closed. A slow camera can never accumulate requests.
            self._snapshot_timer.start(self.getRenderIntervalMs())

    def _on_snapshot_timeout(self) -> None:
        if not self._started or not self._snapshot_mode or self._image_reply is None:
            return
        self._stats.transport_errors += 1
        self._stop_request()
        self._snapshot_timer.start(self.getRenderIntervalMs())

    def _on_error(self, reply: QNetworkReply) -> None:
        if reply is not self._image_reply:
            return  # a stale queued callback from a stopped request
        # A genuine transport failure: recorded, never auto-restarted
        # (the caller's watchdog drives the explicit restart).
        self._stats.transport_errors += 1

    # -- the receive path ---------------------------------------------

    def _detect_boundary(self) -> None:
        try:
            content_type = bytes(self._image_reply.rawHeader(b"Content-Type"))
        except Exception:
            return
        self._parser.detect_boundary(content_type)

    def _drain(self) -> None:
        started = time.perf_counter()
        # The idle gap between drains. The socket is read only when the
        # Qt thread returns to its event loop, so this is the starvation
        # the camera's own send sees: a source that cannot be drained
        # blocks, and the frames it would have sent are the ones the
        # user reads as "backing up".
        if self._stats.last_drain_end:
            gap = (started - self._stats.last_drain_end) * 1000.0
            if gap > self._stats.drain_gap_ms_max:
                self._stats.drain_gap_ms_max = gap
        if self._image_reply is None:
            self._stats.last_drain_end = started
            return
        data = bytes(self._image_reply.readAll())
        if data:
            self._stats.bytes_received += len(data)
            if self._snapshot_mode:
                if not self._parser.append_snapshot(data):
                    self._stop_request()
                    if self._started:
                        self._snapshot_timer.start(self.getRenderIntervalMs())
            else:
                if self._parser.boundary is None:
                    self._detect_boundary()
                self._parser.feed(data)
                # Parse the whole read before choosing its latest frame. A frame
                # arriving just after a periodic tick must not wait an extra
                # period once the decoder and the shared deadline are free.
                if self._started:
                    self._dispatch_from_completion()
        self._stats.last_drain_end = time.perf_counter()
        elapsed = (self._stats.last_drain_end - started) * 1000.0
        self._stats.drain_ms_total += elapsed
        if elapsed > self._stats.drain_ms_max:
            self._stats.drain_ms_max = elapsed

    def _accept_frame(self, frame: bytes) -> None:
        if self._pending_frame is not None:
            # Latest frame wins: a live camera's stale frames are
            # intentionally dropped before any decode cost.
            self._stats.latest_wins_drops += 1
        self._pending_frame = frame
        # The arrival stamp the display lag is measured from: the age
        # of the frame when it reaches the screen is the latency the
        # user sees, and the one number a decode-rate readout cannot
        # show. perf_counter, because the lag subtracts it from a stamp
        # taken where the round trip ends.
        self._pending_arrival = time.perf_counter()


      # incomplete


    # -- the render scheduler -----------------------------------------

    def _render(self) -> None:
        """Ask for the newest frame at the presentation cadence."""
        self._dispatch_from_completion()

    def _dispatch_from_completion(self) -> None:
        """Dispatch at the shared deadline, or arrange its one wake-up.

        A completion can fall between periodic ticks. Skipping the next
        tick unconditionally then wastes a whole period, while waiting
        for another tick quantises the rate to half the requested FPS.
        The single-shot covers the remaining cap interval; both timers
        re-check the same clock and cannot stack hand-overs into a burst.
        """
        if self._pending_frame is None or self._decode_in_flight:
            return
        remaining = self._render_timer.interval() / 1000.0 \
            - (time.perf_counter() - self._last_dispatch_at)
        if remaining > 0:
            self._dispatch_timer.start(max(1, math.ceil(remaining * 1000)))
            return
        self._dispatch()

    def _dispatch(self) -> None:
        """Hand the newest pending frame to the worker, if one is not
        already out.

        One decode at a time: a dispatch that finds one in flight leaves
        the newest frame pending rather than queueing a second behind
        it, which keeps the decodes no more frequent than the cap and
        the memory at one frame plus one image. Latest-wins at both
        ends, and the handed-over frame is the one the generation gate
        will accept.
        """
        if self._pending_frame is None or self._decode_in_flight:
            return
        if self._decoder is None:
            # No worker (a stream that stopped under a queued tick):
            # nothing to hand a frame to, and nothing lost either — the
            # frame stays pending where the next stream's dispatch can
            # take it.
            return
        started = time.perf_counter()
        frame = self._pending_frame
        arrival = self._pending_arrival
        self._pending_frame = None
        self._pending_arrival = 0.0
        self._decode_generation += 1
        self._decode_in_flight = self._decode_generation
        self._in_flight_arrival = arrival
        self._last_dispatch_at = time.perf_counter()
        self._dispatch_timer.stop()
        self._decoder.submit(self._decode_generation, frame)
        self._stats.tick_ms_total += (time.perf_counter() - started) * 1000.0

    def _on_decoded(self, generation: int, payload: object) -> None:
        """A decoded frame, on the Qt thread.

        The generation gate is latest-wins at the far end of the pipe:
        a decode whose frame a restart (or a stop) has superseded is
        dropped here, never installed. Only one decode is ever in
        flight, so results arrive in order — a late one can never
        overwrite a newer picture.

        The round trip ends here: the queue wait and the decode are the
        worker's two shares of it (they travel in the payload) and the
        trip back is this slot's own lateness, which is what a busy Qt
        thread inflates.
        """
        if generation != self._decode_in_flight:
            return
        arrived_at = time.perf_counter()
        self._decode_in_flight = 0
        image, decode_ms, queue_wait_ms, emitted_at = payload
        self._stats.decode_ms_total += decode_ms
        self._stats.decode_ms_max = max(self._stats.decode_ms_max, decode_ms)
        if decode_ms > self._stats.recent_decode_ms_max:
            self._stats.recent_decode_ms_max = decode_ms
        delivery_ms = (arrived_at - emitted_at) * 1000.0
        round_trip_ms = queue_wait_ms + decode_ms + delivery_ms
        self._stats.queue_wait_ms_total += queue_wait_ms
        self._stats.delivery_ms_total += delivery_ms
        self._stats.round_trip_ms_total += round_trip_ms
        if round_trip_ms > self._stats.round_trip_ms_max:
            self._stats.round_trip_ms_max = round_trip_ms
        if image is None:
            self._stats.decode_failures += 1
            # A failed decode is a completed round trip too: the frame
            # behind it must not wait out a period for a result that
            # will never be installed.
            self._dispatch_from_completion()
            return
        started = time.perf_counter()
        self._stats.frames_displayed += 1
        self._stats.decodes_rendered += 1
        arrival = self._in_flight_arrival
        self._in_flight_arrival = 0.0
        if arrival:
            # How old the frame was when it reached the screen: with
            # the decode off this thread, what is left of the age is
            # the decode plus however long the Qt thread took to come
            # back to it.
            lag = (arrived_at - arrival) * 1000.0
            self._stats.display_lag_ms_total += lag
            if lag > self._stats.display_lag_ms_max:
                self._stats.display_lag_ms_max = lag
        # The new image lands BEFORE the notify: imageWidth and
        # imageHeight must already expose the new dimensions when the
        # signal fires.
        self._image = image
        self._deliver_detection_frame(image)
        rect = image.rect()
        if self._image_rect is None or rect != self._image_rect:
            self._image_rect = rect
            self.imageSizeChanged.emit()
        self.update()
        self._stats.install_ms_total += (time.perf_counter() - started) * 1000.0
        # This install is the round trip's end, so the successor's
        # hand-over belongs here: waiting for the next tick would leave
        # it a whole period behind a decode that has just proved it can
        # finish inside the interval.
        self._dispatch_from_completion()

    # -- the diagnostics snapshot -------------------------------------


    def _emit_stats(self) -> None:
        self._stats.sample(time.perf_counter(), self._pending_arrival,
                           self._current_app_state(), self.statsChanged.emit)
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


    def _trace_summary(self) -> None:
        if not self._trace_enabled:
            return
        now = time.monotonic()
        if now - self._trace_summary_at < SUMMARY_INTERVAL_S:
            return
        self._trace_summary_at = now
        fmt, values = self._stats.summary(
            time.monotonic(), self._render_timer.interval(), self._target_fps)
        Logger.log("i", fmt, *values)

    def __del__(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
