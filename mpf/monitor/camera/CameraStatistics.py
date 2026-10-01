# Copyright (c) 2018 Aldo Hoeben / fieldOfView
# NetworkMJPGImage is released under the terms of the LGPLv3 or higher.
# Camera pipeline component extracted for Moonraker Print Follower.

from __future__ import annotations


class CameraStatistics:
    """Lifetime counters and interval measurements for one camera pipeline."""

    def __init__(self):
        self.bytes_received = 0
        self.frames_parsed = 0
        self.frames_displayed = 0
        self.latest_wins_drops = 0
        self.decode_failures = 0
        self.oversized_drops = 0
        self.requests_started = 0
        self.source_changes = 0
        self.transport_errors = 0
        self.parser_resyncs = 0
        self.buffer_high_water = 0
        self.frame_bytes_total = 0
        self.frame_bytes_max = 0
        self.decode_ms_total = 0.0
        self.decode_ms_max = 0.0
        self.stream_epoch = 0.0
        self.last_stats_snapshot = None
        self.last_stats_at = 0.0
        self.recent_incoming = 0.0
        self.recent_displayed = 0.0
        self.recent_bytes_per_sec = 0.0

        # The interval accounting (the Windows diagnosis). Everything
        # here answers one of three questions the frame rates alone
        # cannot: how much of the Qt thread this pipeline actually
        # costs (the tick and the install, with the drain — the parts
        # that run there now that the decode does not), whether a frame
        # reached the screen late or was never parsed at all (the
        # display lag), and whether the Qt thread was between drains
        # long enough for the camera's own send to block (the drain
        # gap). The maxima are the interval's, reset by every stats
        # tick, so a spike cannot hide inside an average.
        self.drain_ms_total = 0.0
        self.drain_ms_max = 0.0
        self.drain_gap_ms_max = 0.0
        self.last_drain_end = 0.0
        self.tick_ms_total = 0.0
        self.install_ms_total = 0.0
        self.recent_tick_ms = 0.0
        self.recent_install_ms = 0.0
        self.display_lag_ms_total = 0.0
        self.display_lag_ms_max = 0.0
        # The round trip, split where it can stall independently: the
        # wait for the worker, the decode, and the trip back to a Qt
        # thread that may be busy with something else.
        self.queue_wait_ms_total = 0.0
        self.delivery_ms_total = 0.0
        self.round_trip_ms_total = 0.0
        self.round_trip_ms_max = 0.0
        self.recent_queue_wait_ms = 0.0
        self.recent_delivery_ms = 0.0
        self.recent_round_trip_ms = 0.0
        self.recent_round_trip_ms_max = 0.0
        self.recent_install_ms_per_frame = 0.0
        self.decodes_rendered = 0
        self.recent_parsed_count = 0
        self.recent_displayed_count = 0
        self.recent_decodes = 0
        self.recent_interval_s = 0.0
        self.recent_decode_ms = 0.0
        self.recent_decode_ms_max = 0.0
        self.recent_decode_ms_per_frame = 0.0
        self.recent_drain_ms = 0.0
        self.recent_drain_ms_max = 0.0
        self.recent_drain_ms_per_frame = 0.0
        self.recent_drain_gap_ms = 0.0
        self.recent_display_lag_ms = 0.0
        self.recent_display_lag_ms_max = 0.0
        self.recent_pending_age_ms = 0.0
        self.last_meters = None
        self.app_state = ""


    def snapshot(self) -> tuple:
        dropped = self.latest_wins_drops + self.decode_failures + self.oversized_drops
        return (self.bytes_received, self.frames_parsed, self.frames_displayed,
                dropped, self.requests_started, self.source_changes,
                self.transport_errors, self.parser_resyncs, self.buffer_high_water,
                self.frame_bytes_max)


    def sample(self, now, pending_arrival, app_state, emit) -> None:
        snapshot = self.snapshot()
        # perf_counter, with _last_stats_at and _pending_arrival: this
        # stamp is the far end of the interval and of the pending
        # frame's age, and both of those are subtracted from it.
        recent = (self.recent_incoming, self.recent_displayed)
        if self.last_stats_snapshot is not None:
            interval = max(0.001, now - self.last_stats_at)
            self.recent_incoming = round(
                (snapshot[1] - self.last_stats_snapshot[1]) / interval, 1)
            self.recent_displayed = round(
                (snapshot[2] - self.last_stats_snapshot[2]) / interval, 1)
            self.recent_bytes_per_sec = round(
                (snapshot[0] - self.last_stats_snapshot[0]) / interval, 1)
            self.recent_interval_s = round(interval, 2)
            self.recent_parsed_count = snapshot[1] - self.last_stats_snapshot[1]
            self.recent_displayed_count = snapshot[2] - self.last_stats_snapshot[2]
            meters = (self.drain_ms_total, self.display_lag_ms_total,
                      self.decode_ms_total, self.decodes_rendered,
                      self.tick_ms_total, self.install_ms_total,
                      self.queue_wait_ms_total, self.delivery_ms_total,
                      self.round_trip_ms_total)
            if self.last_meters is not None:
                drain_ms = meters[0] - self.last_meters[0]
                lag_ms = meters[1] - self.last_meters[1]
                decode_ms = meters[2] - self.last_meters[2]
                decodes = meters[3] - self.last_meters[3]
                tick_ms = meters[4] - self.last_meters[4]
                install_ms = meters[5] - self.last_meters[5]
                queue_wait_ms = meters[6] - self.last_meters[6]
                delivery_ms = meters[7] - self.last_meters[7]
                round_trip_ms = meters[8] - self.last_meters[8]
                self.recent_decodes = decodes
                # The Qt-thread share of the interval, split into the
                # parts that still run there — and, separately, what the
                # worker now does off it.
                self.recent_decode_ms = round(decode_ms / interval, 1)
                self.recent_drain_ms = round(drain_ms / interval, 1)
                self.recent_tick_ms = round(tick_ms / interval, 1)
                self.recent_install_ms = round(install_ms / interval, 1)
                self.recent_decode_ms_per_frame = round(decode_ms / max(1, decodes), 2)
                self.recent_drain_ms_per_frame = round(
                    drain_ms / max(1, self.recent_parsed_count), 2)
                self.recent_decode_ms_max = round(self.recent_decode_ms_max, 2)
                self.recent_drain_ms_max = round(self.drain_ms_max, 2)
                self.recent_drain_gap_ms = round(self.drain_gap_ms_max, 1)
                self.recent_display_lag_ms = round(lag_ms / max(1, decodes), 1)
                self.recent_display_lag_ms_max = round(self.display_lag_ms_max, 1)
                # The round trip, split by where it stalls: a long queue
                # wait is the worker being scarce, a long decode is the
                # frame, and a long delivery is the Qt thread. The trip
                # is read as the sum of its shares, so it is reported at
                # their precision: rounded a digit coarser, half a unit
                # of its own rounding (5% of frames) comes between the
                # two, while at theirs the sum lands inside 0.01.
                self.recent_queue_wait_ms = round(queue_wait_ms / max(1, decodes), 2)
                self.recent_delivery_ms = round(delivery_ms / max(1, decodes), 2)
                self.recent_round_trip_ms = round(round_trip_ms / max(1, decodes), 2)
                self.recent_round_trip_ms_max = round(self.round_trip_ms_max, 1)
                # The install is the trip's last stage and the only one
                # that touches the scene graph, so it is reported per
                # frame beside the other three.
                self.recent_install_ms_per_frame = round(
                    install_ms / max(1, decodes), 2)
            self.last_meters = meters
        # The maxima are the interval's: reset here, not on the summary's
        # slower cadence, so a spike is attributed to the interval it
        # happened in.
        self.drain_ms_max = 0.0
        self.drain_gap_ms_max = 0.0
        self.display_lag_ms_max = 0.0
        self.round_trip_ms_max = 0.0
        # The age of the frame waiting to be rendered RIGHT NOW: the
        # backlog a displayed rate cannot show, because a frame that
        # never reached the screen never entered any rate. Read at the
        # tick, so it is a sample rather than an interval aggregate.
        self.recent_pending_age_ms = round(
            (now - pending_arrival) * 1000.0, 1) if pending_arrival else 0.0
        self.app_state = app_state
        if snapshot != self.last_stats_snapshot or \
                (self.recent_incoming, self.recent_displayed) != recent:
            emit()
        self.last_stats_snapshot = snapshot
        self.last_stats_at = now
        # The periodic summary rides the stats cadence: a parse or
        # render stall must not silence the very telemetry that
        # diagnoses it.


    def summary(self, now, render_interval_ms, target_fps) -> tuple:
        """The summary's format and its arguments, kept separate from
        the logging call so the numbers the owner pastes are the
        numbers a test can assert on."""
        elapsed = max(0.001, now - self.stream_epoch)
        fmt = (
            "Moonraker MJPEG summary [%s] over %.2f s: parsed %d frames (%.1f fps), "
            "displayed %d frames (%.1f fps), render tick %d ms (asked %.1f fps), "
            "latest-frame drops %.1f fps, "
            "decode failures %d, oversized drops %d, "
            "Qt thread %.1f ms/s (tick %.1f, install %.1f, drain %.1f), "
            "worker decode %.1f ms/s (%.2f ms/frame, max %.2f), "
            "round trip %.1f ms/frame (max %.1f): queue wait %.2f, "
            "decode %.2f, back to the Qt thread %.2f, install %.2f, "
            "drain %.2f ms/frame (max %.2f ms), "
            "drain gap max %.1f ms, display lag %.1f ms mean (max %.1f ms), "
            "pending frame age %.1f ms, bandwidth %.1f KB/s (%.2f Mbps), "
            "average frame %.1f KB, maximum frame %.1f KB, "
            "parser buffer high-water %.1f KB, requests %d, source changes %d, "
            "transport errors %d, parser resyncs %d, "
            "lifetime parsed %.1f fps, displayed %.1f fps"
        )
        values = (
            self.app_state or "unknown",
            self.recent_interval_s,
            self.recent_parsed_count,
            self.recent_incoming,
            self.recent_displayed_count,
            self.recent_displayed,
            # The cadence the pane asked for, beside the rate that came
            # out of it: the pair is what tells a display rate capped by
            # the request from one the Qt thread could not keep up with.
            render_interval_ms,
            target_fps,
            self.latest_wins_drops / elapsed,
            self.decode_failures,
            self.oversized_drops,
            self.recent_tick_ms + self.recent_install_ms + self.recent_drain_ms,
            self.recent_tick_ms,
            self.recent_install_ms,
            self.recent_drain_ms,
            self.recent_decode_ms,
            self.recent_decode_ms_per_frame,
            self.recent_decode_ms_max,
            # What the round trip costs, and which part of it is the
            # delay: a long decode is the frame's size, a long delivery
            # is the Qt thread, and a long queue wait is the worker.
            self.recent_round_trip_ms,
            self.recent_round_trip_ms_max,
            self.recent_queue_wait_ms,
            self.recent_decode_ms_per_frame,
            self.recent_delivery_ms,
            self.recent_install_ms_per_frame,
            self.recent_drain_ms_per_frame,
            self.recent_drain_ms_max,
            self.recent_drain_gap_ms,
            self.recent_display_lag_ms,
            self.recent_display_lag_ms_max,
            self.recent_pending_age_ms,
            self.recent_bytes_per_sec / 1000.0,
            self.recent_bytes_per_sec * 8.0 / 1000.0 / 1000.0,
            self.frame_bytes_total / max(1, self.frames_parsed) / 1000.0,
            self.frame_bytes_max / 1000.0,
            self.buffer_high_water / 1000.0,
            self.requests_started,
            self.source_changes,
            self.transport_errors,
            self.parser_resyncs,
            self.frames_parsed / elapsed,
            self.frames_displayed / elapsed,
        )
        return fmt, values
