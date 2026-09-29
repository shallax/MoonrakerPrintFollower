# Copyright (c) 2018 Aldo Hoeben / fieldOfView
# NetworkMJPGImage is released under the terms of the LGPLv3 or higher.
# Camera pipeline component extracted for Moonraker Print Follower.

from __future__ import annotations

from typing import Optional

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

MAX_HEADER_BYTES = 4096


class MJPEGParser:
    """Incremental multipart/JPEG framing, bounded independently of transport."""

    def __init__(self, accept_frame, trace, statistics):
        self.buffer = bytearray()
        self.boundary = None
        self._accept_frame = accept_frame
        self._trace = trace
        self._stats = statistics

    def reset(self) -> None:
        """Retire partial framing when the caller retires a request."""
        self.buffer = bytearray()
        self.boundary = None

    def feed(self, data: bytes) -> None:
        """Consume an entire read before the caller selects its newest frame."""
        self.buffer += data
        self._stats.buffer_high_water = max(self._stats.buffer_high_water, len(self.buffer))
        if self.boundary is not None:
            self.parse_multipart()
        else:
            self.parse_scan()
        self.apply_limits()

    def append_snapshot(self, data: bytes) -> bool:
        """Accumulate one bounded snapshot; False asks the transport to abort."""
        if len(self.buffer) + len(data) > MAX_IN_PROGRESS_FRAME_BYTES:
            self._stats.oversized_drops += 1
            self.buffer.clear()
            return False
        self.buffer += data
        self._stats.buffer_high_water = max(self._stats.buffer_high_water, len(self.buffer))
        return True

    def take_snapshot(self) -> bytes:
        payload = bytes(self.buffer)
        self.buffer.clear()
        return payload

    def detect_boundary(self, content_type: bytes) -> None:
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
                    self.boundary = boundary
                return


    def consume_frame(self, frame: bytes) -> None:
        # The single per-frame validation point every parser path
        # feeds: a COMPLETE frame beyond the documented maximum is
        # pathological — rejected here, before any decode, never by
        # reconnecting.
        if len(frame) > MAX_IN_PROGRESS_FRAME_BYTES:
            self._stats.oversized_drops += 1
            self._stats.parser_resyncs += 1
            self._trace("complete frame exceeds the maximum; rejected")
            return
        self._stats.frames_parsed += 1
        self._stats.frame_bytes_total += len(frame)
        self._stats.frame_bytes_max = max(self._stats.frame_bytes_max, len(frame))
        self._accept_frame(frame)


    @staticmethod
    def content_length(header_block: bytes) -> Optional[int]:
        for line in header_block.split(b"\r\n"):
            if line[:14].lower() == b"content-length":
                try:
                    value = int(line.partition(b":")[2].strip())
                except (ValueError, TypeError):
                    return None
                return value if value > 0 else None
        return None


    def resync_at(self, offset: int, drop: int, keep: int, message: str) -> None:
        """Drop through a structural point (a boundary marker or an
        SOI) AND the offending remainder, keeping only the minimal
        suffix that could straddle a split future marker. The
        connection is never the response."""
        self._stats.parser_resyncs += 1
        del self.buffer[:offset + drop]
        if len(self.buffer) > keep:
            del self.buffer[:-(keep)]
        self._trace(message)


    def parse_multipart(self) -> None:
        marker = b"--" + self.boundary
        mlen = len(marker)
        buffer = self.buffer
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
                    self.resync_at(0, mlen, mlen - 1,
                                    "multipart header block outgrew its bound; resynchronised")
                    continue
                return
            if headers_end - mlen > MAX_HEADER_BYTES:
                # The header block outgrew its bound and then
                # terminated anyway: still malformed — reject the part
                # rather than accepting an arbitrarily large header.
                self.resync_at(0, mlen, mlen - 1,
                                "multipart header block exceeded its bound; part rejected")
                continue
            header_block = bytes(buffer[mlen + 2:headers_end])
            body_start = headers_end + 4
            content_length = self.content_length(header_block)
            if content_length is not None:
                if content_length > MAX_IN_PROGRESS_FRAME_BYTES:
                    # A single declared frame beyond the documented
                    # maximum: pathological — drop the declaration and
                    # the partial body, keep the connection.
                    self.resync_at(0, mlen, mlen - 1,
                                    "declared frame exceeds the maximum; dropped")
                    continue
                if len(buffer) - body_start < content_length:
                    return  # the part is incomplete: wait for more
                frame = bytes(buffer[body_start:body_start + content_length])
                del buffer[:body_start + content_length]
                self.consume_frame(frame)
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
                    self.consume_frame(frame)
                elif next_idx >= 0:
                    # A boundary with no decodable frame inside: the
                    # part was empty or malformed — drop it.
                    del buffer[:next_idx]
                else:
                    return  # incomplete


    def parse_scan(self) -> None:
        # The compatibility fallback: raw concatenated JPEGs without
        # usable multipart framing.
        buffer = self.buffer
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
            self.consume_frame(frame)


    def apply_limits(self) -> None:
        buffer = self.buffer
        if not buffer:
            return
        marker = b"--" + self.boundary if self.boundary else None
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
                self.resync_at(structural, drop, keep,
                                "in-progress frame exceeds the maximum; dropped")
        elif len(buffer) > RETAINED_GARBAGE_LIMIT:
            # Unframed data (no marker, no SOI) beyond the garbage
            # bound: discard it and keep the connection.
            self._stats.parser_resyncs += 1
            self._trace("retained garbage limit reached; discarded")
            self.buffer = bytearray()
