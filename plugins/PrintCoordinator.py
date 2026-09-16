"""Cross-domain orchestration with explicit dependencies; not a shared state bag."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
import time

from PyQt6.QtCore import QObject, QTimer
from UM.Logger import Logger

from .MonitorFormatting import filament_total_mm_from_file, parse_bed_mesh, preview_eta_text, result
from .PreviewFormatting import (
    pause_can_toggle,
    pause_items,
    pause_summary,
    pause_unavailable,
    status_icon,
    status_text,
)
from .PrintIdentity import index_view_for_print
from .PrintState import LayerResolver, PrintSnapshot
from .RemoteJobService import RemoteJobService


class PrintCoordinator(QObject):
    # The seam's staleness bound (4.3.0): the block's stamp is the
    # aux landing's clock — older than this at publish time and the
    # strip renders "—" (three missed 2.5 s polls).
    PREVIEW_BLOCK_STALE_S = 8.0
    # The metadata cross-check's bounded give-up (4.3.0): after this
    # many failed checks for the same key the payload is accepted
    # with the failure flagged.
    MR_META_CHECK_LIMIT = 3

    def __init__(self, *, client, binding, files, index, cura, preview, pauses,
                 presentation, bed_mesh, parent=None):
        super().__init__(parent)
        self._client, self._binding = client, binding
        self._files, self._index, self._cura = files, index, cura
        self._preview, self._pauses = preview, pauses
        self._presentation, self._bed_mesh = presentation, bed_mesh
        self._jobs = RemoteJobService({"printing", "paused"})
        self._layers = LayerResolver()
        self._snapshot = PrintSnapshot()
        self._status = {}
        self._detail = "Not connected"
        self._gate_logged = None
        self._preview_block = None
        self._load_job = None
        self._load_requested = False
        self._load_requested_at = 0.0
        # Moonraker's file metadata (the slicer header parsed server-side):
        # layer height and slicer estimate for prints the user never
        # loaded. Fetched once per job, retried every 30 s until success.
        self._mr_meta = {}
        self._mr_meta_key = ("", "")
        self._mr_meta_asked = ("", "")
        self._mr_meta_at = 0.0
        self._mr_meta_pending = False
        self._mr_meta_checks = 0
        # The active print's filament total parsed from the DOWNLOADED
        # file's own header (client-side): Moonraker's metadata
        # undercounts multi-extruder prints (its Cura parser read only
        # the first ';Filament used:' value until the v0.10 series), so
        # the header
        # parse wins whenever the file is local and the metadata total
        # stays the fallback for files never downloaded. The bounded
        # scan is latched per downloaded file path.
        self._header_total_mm = None
        self._header_total_path = ""
        self._layer_trace_at = 0.0
        self._monitor_requested = False
        self._monitor_requested_at = 0.0
        self._publish_at = 0.0
        self._processing = self._closed = False
        self._had_toolpath = False
        client.statusReceived.connect(self.observe)
        client.connectionChanged.connect(self._connection_changed)
        client.sessionInvalidated.connect(self.reset_binding)
        binding.changed.connect(self.refresh)
        files.changed.connect(self.refresh)
        # Service failures are surfaced to the log; the preview status text
        # stays terse on purpose.
        files.failed.connect(lambda message: Logger.log("w", "Remote file service: %s", message))
        index.changed.connect(self._index_changed)
        index.failed.connect(lambda message: Logger.log("w", "G-code index service: %s", message))
        cura.changed.connect(self.refresh)
        cura.positionChanged.connect(self._position_changed)
        cura.invalidated.connect(self._scene_invalidated)
        cura.viewSwapped.connect(self._on_view_swapped)
        cura.fileLoaded.connect(self._file_loaded)
        cura.loadFailed.connect(self._load_failed)
        pauses.changed.connect(self._publish)
        pauses.message.connect(self._message)
        # A rebuilt preview stage destroys and recreates the QML
        # controls (the new-build-plate report); the
        # presentation re-emits after recreating so the fresh card
        # receives the full value set immediately, before any other
        # event would republish it.
        presentation.controlsChanged.connect(self._publish)
        presentation.loadRequested.connect(self.confirm_load)
        presentation.attachmentRequested.connect(self.toggle_attachment)
        presentation.pauseAtLayerRequested.connect(self.toggle_pause)
        presentation.removePauseRequested.connect(self.remove_pause)
        presentation.clearPausesRequested.connect(pauses.clear)

    def receive_preview_block(self, block) -> None:
        """The seam's sink (4.3.0): the Monitor's per-poll value
        block lands here through the output-device edge. Duplicate
        deliveries (the observation rebuilds on core updates too)
        are ignored by the block's own stamp — the aux-landing stamp
        is the one clock the staleness rule reads, never a receipt
        taken in transit."""
        if not isinstance(block, Mapping):
            return
        stamp = float(block.get("stamp") or 0.0)
        if self._preview_block is None or stamp > self._preview_block[1]:
            self._preview_block = (dict(block), stamp)

    @property
    def snapshot(self): return self._snapshot

    def observe(self, status):
        if self._closed or not isinstance(status, dict): return
        self._processing = True
        try:
            # The session boundary already publishes a fully detached copy.
            self._status = status
            stats = status.get("print_stats")
            sd = status.get("virtual_sdcard")
            stats, sd = stats if isinstance(stats, dict) else {}, sd if isinstance(sd, dict) else {}
            transition = self._jobs.observe(stats, sd)
            active = self._jobs.printer_state in {"printing", "paused"}
            job = transition.key if active else None
            if transition.new_job or not active:
                self._preview.reset_print()
                self._layers.reset()
            self._index.bind(job)
            self._files.bind(job)
            self._pauses.bind(job)
            if job != self._mr_meta_key[1]:
                # A job change invalidates the in-flight metadata fetch
                # (the file service's generation guard drops its reply)
                # — the pending flag must not survive the boundary or
                # the lane wedges shut for every later print.
                self._mr_meta_pending = False
            if self._load_requested:
                if not active:
                    self._load_requested = False
                    self._detail = "No active Moonraker print to load"
                else:
                    self._load_job = job
                    self._load_requested = False
                    self._files.request_file(retry=True)
        finally:
            self._processing = False
        self.refresh()

    def refresh(self):
        if self._closed or self._processing: return
        self._processing = True
        try:
            # A load request against a standby printer never resolves
            # through observe() — no status frame arrives to clear the
            # flag (the stuck "Resolving…" report). The snapshot's own
            # print state settles it here: a known-idle printer clears
            # once the refresh the request kicked off has had a moment
            # to land; the 5 s bound catches a stale snapshot.
            now = time.monotonic()
            if self._load_requested and now - self._load_requested_at > (2.0 if not self._snapshot.active else 5.0):
                self._load_requested = False
                if not self._snapshot.active:
                    self._detail = "No active Moonraker print to load"
            if self._monitor_requested and now - self._monitor_requested_at > (2.0 if not self._snapshot.active else 5.0):
                self._monitor_requested = False
                if not self._snapshot.active:
                    self._detail = "No active Moonraker print to load"
            config = self._binding.config
            # The toolpath's arrival (the plugin's load rendered, or a
            # slice) is the moment Cura's controls must come up: the
            # plugin-driven load fires none of Cura's own activity
            # events, so Cura's panel and slider stay dormant until an
            # unrelated event (the live report). Nudge Cura's
            # own computation on the edge, and once more after the
            # render settles.
            has_toolpath = bool(self._cura.has_toolpath)
            if has_toolpath and not self._had_toolpath:
                self._had_toolpath = True
                self._cura.nudge_cura_activity()
                self._cura.nudge_layer_view()
                QTimer.singleShot(1500, self._cura.nudge_cura_activity)
            elif not has_toolpath:
                self._had_toolpath = False
            filename = str((self._status.get("print_stats") or {}).get("filename") or "")
            job = self._files.job_key
            # The view is the index's evidence for the CURRENT print —
            # both the view's key and the files service's job can be
            # stale from an earlier load of a DIFFERENT file (the red
            # run: the hourglass never fired for a fresh print after
            # any preview load, because the two stale keys agreed with
            # each other). Compare against the print's own filename.
            view = index_view_for_print(self._index.view, filename)
            # The downloaded file's OWN header is the authoritative
            # filament total; Moonraker's parse of it (the metadata
            # below) is the fallback. One bounded head read per
            # downloaded file — the files service emits changed when a
            # download completes, so this latch runs on the refresh
            # that immediately follows it.
            path = self._files.path
            if path != self._header_total_path:
                self._header_total_path = path
                self._header_total_mm = filament_total_mm_from_file(path) if path else None
            # The downloaded metadata wins; Moonraker's header parse is
            # the fallback that populates the layer-height readout and
            # the slicer estimate without any gcode download. The
            # fallback serves ONLY the payload whose identity matches
            # the current job — never the previous print's values.
            status_stats = (self._status.get("print_stats") or {}) if isinstance(self._status, dict) else {}
            metadata = self._files.metadata or self._mr_metadata_for(str(status_stats.get("filename") or ""), job)
            physical = self._layers.resolve(self._status, config, view, metadata, self._cura.heights)
            try:
                estimate = float(metadata.get("estimated_time") or 0)
            except (TypeError, ValueError):
                estimate = 0
            layer_progress = None
            if view is not None and physical.index is not None and 0 <= physical.index < len(view.ranges):
                start, end = view.ranges[physical.index]
                try:
                    position = int((self._status.get("virtual_sdcard") or {}).get("file_position") or 0)
                except (TypeError, ValueError):
                    position = None
                if start is not None and end is not None and end > start and position is not None:
                    layer_progress = max(0.0, min(1.0, (position - start) / (end - start)))
            # Terminal conditions for the monitor-only download: the
            # index landed, the build failed, OR the download failed.
            # Without the download-failure branch the flag wedges True
            # forever (the download's retry ladder only re-fires when a
            # consumer re-requests, and the one consumer in monitor-only
            # mode is gated behind has_toolpath — panel finding P1-1).
            if self._monitor_requested and (view is not None
                                            or self._index.phase == "error"
                                            or self._files.phase == "error"):
                self._monitor_requested = False
            load_active = (self._load_requested or self._load_job is not None
                           or self._monitor_requested
                           or self._files.phase in ("resolving", "downloading")
                           or self._index.phase == "indexing"
                           or self._cura.loading)
            filament_total = self._header_total_mm
            if filament_total is None:
                try:
                    filament_total = float(metadata.get("filament_total"))
                except (TypeError, ValueError):
                    filament_total = None
            self._snapshot = PrintSnapshot(job, self._jobs.observation, physical,
                estimate if estimate > 0 else None, self._files.metadata_complete,
                layer_progress=layer_progress, index_ready=view is not None,
                download_fraction=self._files.download_fraction,
                indexing=self._index.phase == "indexing",
                load_active=load_active,
                filament_total=filament_total if filament_total and filament_total > 0 else None)
            if self._snapshot.active and filename:
                self._maybe_fetch_mr_metadata(filename, job)
            if config.trace_layer and time.monotonic() - self._layer_trace_at >= 5:
                self._layer_trace_at = time.monotonic()
                info = (self._status.get("print_stats") or {}).get("info") or {}
                gpos = (self._status.get("gcode_move") or {}).get("gcode_position") or ()
                mr_meta = self._mr_metadata_for(filename, job)
                Logger.log("i",
                    "layer trace: raw_current=%s total=%s state=%s z=%s e=%s progress=%s one_based=%s z_fallback=%s mr_meta=%s mr_meta_keys=%s files_meta=%s heights_n=%s deltas=%s ascent=%.3f z_layer=%s -> layer=%s source=%s",
                    info.get("current_layer"), info.get("total_layer"),
                    (self._status.get("print_stats") or {}).get("state"),
                    gpos[2] if len(gpos) >= 3 else None, gpos[3] if len(gpos) >= 4 else None,
                    (self._status.get("virtual_sdcard") or {}).get("progress"),
                    config.moonraker_layer_is_one_based, config.z_fallback,
                    bool(mr_meta), sorted(mr_meta) if mr_meta else [],
                    bool(self._files.metadata),
                    len(self._cura.heights),
                    self._layers._z_deltas, self._layers._z_ascent, self._layers._z_layer,
                    physical.index, physical.source)
            # The coordinator owns the mesh observation; the Monitor reads the
            # presenter's snapshot but never writes it. The presenter's
            # fingerprint guard makes the per-poll update cheap.
            self._bed_mesh.update(parse_bed_mesh(self._status.get("bed_mesh")))
            self._cura.watch(config.enabled)
            if self._snapshot.active:
                # The metadata and index serve the Preview, which needs the
                # print loaded in Cura. Pulling them for a print that is
                # merely active wastes the download and shows confusing
                # "Downloading… / Indexing…" chatter before the user has
                # loaded anything; the load flow (observe -> request_file)
                # starts the pull itself.
                if self._cura.has_toolpath:
                    self._files.request_metadata()
                    if config.path_follow and config.enabled:
                        self._index.request()
                self._pauses.observe(physical.index)
            if self._load_job is not None:
                if self._load_job != job:
                    self._load_job = None
                    self._detail = "Print changed before it could be loaded"
                elif self._files.path and not self._cura.loading:
                    lease = self._files.lease()
                    self._load_job = None
                    if lease is not None: self._cura.load(lease)
            if self._client.connected:
                # The retention window's anchor is the LIVE layer,
                # updated every poll even when already hydrated — the
                # hydration tuple below carries REQUESTED layers
                # (prefetch included), which must never anchor the
                # window.
                current = self._snapshot.layer.index
                if isinstance(current, int):
                    self._index.set_followed_layer(current)
                self._detail, hydration = self._preview.observe(self._snapshot, self._status, config, view)
                for layer in hydration: self._index.request_hydration(layer)
            else:
                self._preview.invalidate_view()
            self._preview.update_eta(self._snapshot, view)
            self._snapshot = replace(self._snapshot,
                layer_eta=self._preview.remaining_end(view, self._snapshot.estimated_time))
            self._publish()
        finally:
            self._processing = False

    def _maybe_fetch_mr_metadata(self, filename, job):
        """The active print's file metadata from Moonraker — a tiny JSON
        header parse, NOT a gcode download (the no-silent-
        downloads ruling covers the file itself; the header query is
        what Moonraker's own UI uses for the same readouts).

        The CONTRACT (4.3.0, the distribution): the FETCH is the file
        service's request_metadata_only (identity-neutral, its own
        single-flight and generation guard); the RETAINED PAYLOAD and
        the LATCH stay here, keyed on the (filename, job) identity
        pair; the history CROSS-CHECK lives with run identity
        (RemoteJobService.current_job_matches — the pure decision
        only). CLOCKS: the 30 s throttle reads time.monotonic (a
        wall-clock step must not re-arm or stall the ladder); wall
        time never enters the latch key.

        The fetch LATCHES on a completed fetch for THIS job: a slicer
        header never changes mid-print, so once we hold the metadata
        the 30 s retry ladder retires instead of re-querying for the
        whole print (a 10 h job was ~1,200 requests of the same JSON).
        A failed fetch never latches — the previous job's payload must
        never read as this job's — and the job-keyed throttle still
        lets a same-name restart refetch."""
        asked = (filename, job)
        if self._mr_meta_key == asked:
            return  # a completed fetch already latched this job
        if self._mr_meta_pending:
            return  # one request at a time
        if self._mr_meta_asked == asked and time.monotonic() - self._mr_meta_at < 30:
            return  # inside the throttle window for this job
        started = self._files.request_metadata_only(
            lambda p, e, a=asked: self._mr_meta_done(a, p, e))
        if not started:
            # A dropped send must leave no identity pointing at a job
            # that was never queried (the old latch satisfied forever).
            self._mr_meta_asked = ("", "")
            self._mr_meta_at = 0.0
            return
        # Request identity commits only when the send actually started.
        # The give-up counter spans the retry ladder for ONE key — a
        # NEW key (a restart) starts fresh.
        if self._mr_meta_asked != asked:
            self._mr_meta_checks = 0
        self._mr_meta_asked = asked
        self._mr_meta_at = time.monotonic()
        self._mr_meta_pending = True

    def _mr_meta_done(self, asked, payload, error):
        self._mr_meta_pending = False
        value = result(payload) if payload else {}
        if self._closed or asked != self._mr_meta_asked:
            return  # a reset or a new request superseded this one
        if error or not isinstance(value, Mapping):
            # Never latch a failed fetch; the stale payload (if any)
            # already fails the key check and is never served.
            self.refresh()
            return
        job_id = value.get("job_id")
        if job_id is None:
            # A null job id means "no job identity" (Moonraker writes
            # the field only when a print ran): accept without the
            # history cross-check.
            self._mr_meta = value
            self._mr_meta_key = asked
            self.refresh()
            return
        # The metadata alone cannot prove the job is the CURRENT one:
        # cross-check its job id against the newest history row over
        # the HTTP lane (the websocket history notification is
        # off-limits by the transport discipline).
        self._client.transport.send_json("follower", "mr-history", "GET",
            "server/history/list?limit=1&order=desc",
            lambda p, e, a=asked, v=value: self._mr_history_checked(a, v, p, e),
            category="metadata")

    def _mr_history_checked(self, asked, value, payload, error):
        if self._closed or asked != self._mr_meta_asked:
            return  # a new fetch or a reset superseded this check
        job_id = value.get("job_id")
        verdict = self._jobs.current_job_matches(payload, job_id)
        if verdict:
            self._mr_meta = value
            self._mr_meta_key = asked
            self._mr_meta_checks = 0
        elif error or verdict is None:
            # The cross-check can only REFUSE, never serve a wrong
            # payload — but a check that can never pass is silent and
            # permanent (the ~2x request load of the defect the latch
            # retired, and a dead ETA/filament anchor for the rest of
            # the print). The bounded give-up (4.3.0): after N failed
            # checks for the same key the payload is accepted with
            # the failure flagged. Only the unattestable causes may
            # give up — see the mismatch branch below.
            self._mr_meta_checks += 1
            cause = "the history request failed" if error else "the history reply was unattestable"
            if self._mr_meta_checks >= self.MR_META_CHECK_LIMIT:
                self._mr_meta = value
                self._mr_meta_key = asked
                self._mr_meta_checks = 0
                Logger.log("w", "Moonraker metadata latched after %d failed cross-checks (%s) — the ETA and filament anchors run on an unattested header", self.MR_META_CHECK_LIMIT, cause)
            else:
                Logger.log("w", "Moonraker metadata cross-check failed: %s", cause)
        else:
            # A mismatched job id is PROOF the payload describes a
            # different job — the give-up must never latch it (the
            # identity bleed the cross-check exists to prevent). The
            # anchors stay empty for this print.
            self._mr_meta_checks = 0
            Logger.log("w", "Moonraker metadata refused: the job id mismatched — the ETA and filament anchors stay empty for this print")
        self.refresh()

    def _mr_metadata_for(self, filename, job):
        """The successfully-received metadata for (filename, job), or an
        empty mapping — never the previous print's payload."""
        if self._mr_meta_key == (filename, job):
            return self._mr_meta
        return {}

    def reset_binding(self):
        self._processing = True
        try:
            self._load_job = None
            self._load_requested = False
            # A monitor-only download in flight when the binding changes
            # (machine switch, job change) must not leak its busy flag
            # into the next session — panel finding P1-1.
            self._monitor_requested = False
            self._mr_meta = {}
            self._mr_meta_key = ("", "")
            self._mr_meta_asked = ("", "")
            self._mr_meta_at = 0.0
            self._mr_meta_pending = False
            self._mr_meta_checks = 0
            self._header_total_mm = None
            self._header_total_path = ""
            self._status = {}
            self._preview_block = None
            self._jobs.reset()
            self._layers.reset()
            self._snapshot = PrintSnapshot()
            self._index.bind(None)
            self._files.bind(None)
            self._pauses.bind(None)
            self._preview.attach(True)
            self._preview.reset_print()
            self._bed_mesh.clear()
            self._cura.invalidate("active printer binding changed")
            self._detail = "Not connected"
        finally:
            self._processing = False
        self._publish()

    def _connection_changed(self, connected, detail):
        if not connected: self._preview.invalidate_view()
        self._detail = detail
        self._publish()

    def _scene_invalidated(self, reason):
        self._preview.invalidate_view()
        # The load flow must survive a scene invalidation: switching to
        # the Monitor mid-load invalidates the preview scene, and the
        # old clear here aborted the download. The load itself swaps
        # Cura back to the Preview when it lands.
        self._publish()

    def _on_view_swapped(self):
        # The old view's layer/path handles are gone. Until the new view
        # has accepted the follower's next drive, nothing may count as a
        # user override — Cura's own late restoration after a stage-switch
        # hang would otherwise detach the follower. If the follower was
        # attached before the switch, restore that state: switching stages
        # is navigation, not a detach request.
        was_attached = self._preview.state.attached
        self._preview.invalidate_view()
        if was_attached:
            self._preview.attach(True)

    def _index_changed(self):
        # A newly installed index resets path anchors, not print-local attachment.
        if self._index.phase == "indexing": self._preview.reset_tracking()
        self.refresh()

    def _position_changed(self):
        if self._binding.config.enabled:
            if self._preview.detect_override():
                self._detail = "Detached"
        # Cura streams position changes at the render cadence; the
        # panel values do not need that rate. Throttle the ETA and
        # publish to 5 Hz — the preview-lag report.
        now = time.monotonic()
        if now - self._publish_at < 0.2:
            return
        self._publish_at = now
        self._preview.update_eta(self._snapshot, self._index.view)
        # The same job-key gate as refresh(): a Cura scene movement must
        # never publish an ETA anchored to a different file's index.
        view = self._index.view
        if view is not None and view.job_key == self._files.job_key:
            self._snapshot = replace(self._snapshot,
                layer_eta=self._preview.remaining_end(view, self._snapshot.estimated_time))
        self._publish()

    def _file_loaded(self, path):
        self._preview.invalidate_view()
        self._client.force_refresh()

    def _load_failed(self, error):
        self._load_job = None
        self._detail = "Could not load current print: " + error
        self._publish()

    def _message(self, text):
        self._detail = text
        self._publish()

    def confirm_load(self):
        self._cura.confirm_replace(self.request_load)

    def download_for_monitor(self):
        """Download and index the active print WITHOUT loading it into
        the preview: the optimisation — the monitor's better
        layer and ETA must not pay for a scene render the user may not
        want. The index service pulls metadata, restores or downloads
        the file, and builds the index; a later preview load finds the
        file already local and only renders."""
        if not self._binding.configured:
            self._message("Set a Moonraker URL before improving the monitor estimate")
            return
        self._monitor_requested = True
        self._monitor_requested_at = time.monotonic()
        self._index.request()
        self._message("Downloading and indexing the print for the monitor…")
        self._client.force_refresh()
        QTimer.singleShot(2600, self.refresh)

    def request_load(self):
        if not self._binding.configured:
            self._message("Set a Moonraker URL before loading the current print")
            return
        self._load_requested = True
        self._load_requested_at = time.monotonic()
        self._message("Resolving current print…")
        self._client.force_refresh()
        QTimer.singleShot(2600, self.refresh)

    def toggle_attachment(self):
        # A manual toggle is a deliberate choice: it cancels any pending
        # watchdog re-attach.
        self._preview.attach(not self._preview.state.attached)
        self.refresh()
        if self._preview.state.attached: self._client.force_refresh()

    def toggle_pause(self, human_layer):
        total = self._snapshot.layer.total
        if total is None and self._cura.max_layer is not None: total = self._cura.max_layer + 1
        layer = int(human_layer) - 1
        # The backstop for the gating above: a baked pause at the layer
        # makes a manual schedule impossible, however the request
        # arrived.
        baked = set(self._index.view.pause_layers) if self._index.view is not None else set()
        if layer in baked:
            return
        self._pauses.toggle(layer, self._snapshot.layer.index, total)
        self._publish()

    def remove_pause(self, human_layer):
        self._pauses.remove(int(human_layer) - 1)

    def _publish(self):
        if self._closed: return
        config, state, snapshot = self._binding.config, self._preview.state, self._snapshot
        selected, current, total = self._cura.selected_layer, snapshot.layer.index, snapshot.layer.total
        if total is None and self._cura.max_layer is not None: total = self._cura.max_layer + 1
        scheduled = selected is not None and selected in self._pauses.layers
        # The gcode's baked pauses join the list as read-only rows (the
        # ruling), and a baked layer blocks the manual toggle — the two
        # can never double up.
        baked = set(self._index.view.pause_layers) if self._index.view is not None else set()
        baked_block = selected is not None and selected in baked
        can_toggle = not baked_block and pause_can_toggle(snapshot.active, selected, current, total)
        unavailable = ("a pause is baked into the gcode at this layer" if baked_block
                       else pause_unavailable(snapshot.active, can_toggle, scheduled, current, selected))
        items = pause_items(
            set(self._pauses.layers), self._pauses.states, baked,
            lambda layer: self._preview.remaining(layer, self._index.view, end=True),
            self._preview.format_duration,
            current=current,
            clock=lambda remaining: (datetime.now().astimezone() + timedelta(seconds=remaining)).strftime("%H:%M"),
        )
        compact = status_text(
            detail=self._detail,
            load_requested=self._load_requested,
            loading=self._cura.loading,
            files_phase=self._files.phase,
            index_phase=self._index.phase,
            attached=state.attached,
            enabled=config.enabled,
            connected=self._client.connected,
            configured=self._binding.configured,
        )
        # Transition diagnostics (INFO, only on change): the card's
        # visibility terms log themselves so a vanish can be traced to
        # the term that stayed false.
        gate = (self._binding.configured and config.enabled,
                self._snapshot.load_active and not self._snapshot.index_ready,
                self._cura.has_toolpath,
                self._cura.preview_active)
        if gate != self._gate_logged:
            self._gate_logged = gate
            Logger.log("i", "Moonraker preview card gates: configured=%s loadBusy=%s hasToolpath=%s previewStage=%s",
                       gate[0], gate[1], gate[2], gate[3])
        self._presentation.publish({
            "followingPaused": not state.attached, "followingEnabled": config.enabled,
            # The preview's load feedback: busy until the load reaches a
            # terminal state (requested, downloading, indexing, rendering),
            # with the same determinate/indeterminate progress contract as
            # the Monitor's Improve-ETA bar.
            "loadBusy": self._snapshot.load_active and not self._snapshot.index_ready,
            "loadProgress": self._files.download_fraction if self._files.download_fraction is not None else -1.0,
            "loadPhase": ("Downloading…" if self._files.phase == "downloading"
                          else "Resolving…" if self._files.phase == "resolving"
                          else "Indexing…" if self._index.phase == "indexing"
                          else "Rendering…" if self._cura.loading
                          else "Resolving current print…" if self._load_requested or self._monitor_requested else ""),
            "configuredForFollowing": self._binding.configured and config.enabled,
            # The stage state rides THIS publish path: the presenter's
            # own refresh-side publish proved unreliable on the
            # dynamically created cards (the harness probe caught the
            # value never arriving after a stage click), while every
            # value in this dict demonstrably lands.
            "previewStageActive": self._cura.preview_active,
            "activePrinterName": self._binding.identity[1], "hasToolpath": self._cura.has_toolpath,
            "sceneHasObjects": self._cura.scene_has_objects,
            "statusText": compact, "statusIconName": status_icon(compact),
            "selectedLayerEtaText": state.eta_text,
            "pauseAtLayerActive": snapshot.active, "pauseAtLayerCandidate": selected + 1 if selected is not None else 0,
            "pauseAtLayerCanToggle": can_toggle, "pauseAtLayerScheduled": scheduled,
            "pauseAtLayerSummary": pause_summary(items),
            "pauseAtLayerItems": items, "pauseAtLayerUnavailableText": unavailable,
            # The Preview value block rides through to the card as-is
            # — the strip applies the staleness rule against the
            # block's aux-landing stamp. The connection truth joins
            # the staleness rule (4.3.0, the domain re-review): a
            # dead feed publishes no events, so the stamp alone can
            # never age on the exact path the rule exists for — the
            # client's tri-state is the freshest signal there is.
            "previewBlock": self._preview_block[0] if self._preview_block is not None else {},
            "previewBlockStale": not self._client.connected or self._preview_block is None or
                time.monotonic() - self._preview_block[1] > self.PREVIEW_BLOCK_STALE_S,
            # The strip's middle-slot ETA: the print remaining/finish
            # (the Monitor's own pair, composed) — the selected-layer
            # line stays in its slot untouched.
            "previewEtaText": preview_eta_text(self._status or {}, self._snapshot),
        })

    def close(self):
        self._closed = True


