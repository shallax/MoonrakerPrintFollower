"""Cross-domain orchestration with explicit dependencies; not a shared state bag."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import time

from PyQt6.QtCore import QObject, QTimer
from UM.Logger import Logger

from .LoadStateTracker import LoadStateTracker
from .MonitorFormatting import filament_total_mm_from_file, height_readout, layer_readout, parse_bed_mesh, plate_values, preview_eta_text, result
from .MoonrakerProtocol import live_position_in_gcode_space
from .NextPausePipeline import NextPausePipeline
from .PreviewFormatting import (
    pause_can_toggle,
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
        # The two extracted owners: the load request's lifecycle (its
        # pending flags, the lease handoff, the busy term) and the
        # next-pause pipeline (the anchor, the merged rows, the target).
        self._loads = LoadStateTracker(files=files, index=index, cura=cura)
        self._next_pause = NextPausePipeline(preview=preview, pauses=pauses, index=index)
        self._snapshot = PrintSnapshot()
        self._status = {}
        self._detail = "Not connected"
        self._gate_logged = None
        self._preview_block = None
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
        self._user_detached = False
        # The follower face's MANUAL anchor: an index while the face is
        # detached from the live layer, None while it follows the print.
        self._plate_anchor = None
        # The within-layer scrub, tied to the detach: a motion count
        # while the face is detached and the user has scrubbed, None to
        # draw the frozen layer as its whole base.
        self._plate_split = None
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
        # The card's hourglass click is the monitor's improveEta —
        # the same download_and_index path, the same idempotence.
        presentation.improveEtaRequested.connect(self.download_for_monitor)
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
            detail = self._loads.resolve(job, active)
            if detail is not None:
                self._detail = detail
        finally:
            self._processing = False
        self.refresh()

    def refresh(self):
        if self._closed or self._processing: return
        self._processing = True
        try:
            # The request flags age out against the snapshot's own
            # print state — a standby printer never sends the frame
            # that would settle them (the stuck "Resolving…" report).
            now = time.monotonic()
            detail = self._loads.settle(now, self._snapshot.active)
            if detail is not None:
                self._detail = detail
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
                # The fresh-bind attach's completion (the p1 report):
                # the bind-time gate saw no toolpath yet — the layer
                # render lands after the bind — so the follower
                # attaches when the toolpath ARRIVES. A deliberate
                # detach latches _user_detached, so a later toolpath
                # flap can never re-attach over the user's choice
                # (the panel's catch).
                if not self._preview.state.attached and not self._user_detached:
                    self._preview.attach(True)
            elif not has_toolpath:
                self._had_toolpath = False
                # The ruling (2026-09-17): the attach control pins to
                # detached without a toolpath — the follower detaches
                # for real when the toolpath goes away.
                if self._preview.state.attached:
                    self._preview.attach(False)
            filename = str((self._status.get("print_stats") or {}).get("filename") or "")
            job = self._files.job_key
            # The view is the index's evidence for the CURRENT print —
            # both the view's key and the files service's job can be
            # stale from an earlier load of a DIFFERENT file (the red
            # run: the hourglass never fired for a fresh print after
            # any preview load, because the two stale keys agreed with
            # each other). Compare against the print's own filename.
            view = index_view_for_print(self._index.view, filename)
            # The plate's source: the follower must NOT depend on the
            # preview's toolpath (the live ruling) — the monitor-only
            # index (Improve ETA) builds without a preview load, and
            # its job key is the unresolved identity that gate refuses.
            # The monitor download only ever names the ACTIVE print,
            # so an unresolved-key view is accepted for the plate.
            plate_available = view is not None or (
                self._index.view is not None
                and (not self._index.view.job_key or self._index.view.job_key[0] == filename))
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
            self._next_pause.track(physical.index)
            try:
                estimate = float(metadata.get("estimated_time") or 0)
            except (TypeError, ValueError):
                estimate = 0
            # The file position is resolved ONCE, ahead of BOTH of its
            # consumers — the layer fraction here and the plate split
            # below. The plate's path runs with no identity-checked
            # view at all (the monitor-only index), so a position bound
            # inside the layer branch left that read unbound. Missing
            # or non-numeric is None, which each consumer skips: a real
            # 0 is a position, never an absence.
            sdcard = self._status.get("virtual_sdcard") if isinstance(self._status, dict) else None
            try:
                position = int(sdcard.get("file_position")) if isinstance(sdcard, Mapping) else None
            except (TypeError, ValueError):
                position = None
            # The toolhead's PHYSICAL position, in the G-code's own
            # coordinates: the plate split refines the dispatcher's
            # position with it, exactly as the Preview's follower does
            # (the same helper, the same space), so the painted fill
            # cannot run ahead of the nozzle. Absent telemetry is None,
            # and the split then reads the coarse anchor as it always did.
            live_position = (
                live_position_in_gcode_space(
                    self._status.get("motion_report") or {}, self._status.get("gcode_move") or {})
                if isinstance(self._status, dict) else None)
            layer_progress = None
            if view is not None and physical.index is not None and 0 <= physical.index < len(view.ranges):
                start, end = view.ranges[physical.index]
                if start is not None and end is not None and end > start and position is not None:
                    layer_progress = max(0.0, min(1.0, (position - start) / (end - start)))
            # The monitor-only download's terminal conditions (panel P1-1).
            self._loads.retire_monitor(view is not None)
            load_active = self._loads.active
            filament_total = self._header_total_mm
            if filament_total is None:
                try:
                    filament_total = float(metadata.get("filament_total"))
                except (TypeError, ValueError):
                    filament_total = None
            try:
                elapsed = float((status_stats.get("print_duration") or 0) or 0.0)
            except (TypeError, ValueError):
                elapsed = 0.0
            self._next_pause.update_anchor(job, status_stats.get("state"), elapsed)
            # The merged rows built ONCE per refresh (the perf
            # panel's catch): the compute and the publish share them.
            items = self._next_pause.rebuild(physical.index)
            (next_pause_layer, next_pause_eta,
             next_pause_fraction, next_pause_baked) = self._next_pause.compute(physical, elapsed, items)
            # The follower face's prepared polylines: built HERE from
            # the index (the worker-side prep rule), not in the model.
            layer_count = len(view.ranges) if view is not None else 0
            # The face's anchor: the live layer while the follower
            # follows the print, the manual one while the user has
            # detached it — refused when it points outside this file
            # (a frozen anchor outlives a print and the next one may be
            # shorter).
            plate_progress_payload = None
            manual_payload = None
            plate_visited = frozenset()
            plate_decode_ms = None
            if plate_available and physical.index is not None:
                # The payload is built INSIDE the service — the raw
                # index's arrays never cross its boundary (the
                # architecture contract), so the coordinator asks the
                # service, never the view. The plate is the PRINT's, so
                # it needs the resolved physical layer: there is no
                # anchor without one (the monitor-only index whose
                # layer never resolved), and the plate APIs are never
                # asked for a None one. A frozen layer carries NO
                # file position: the split is a live print's boundary,
                # and on another layer it would be another print's
                # fill.
                # TWO payloads (the live request): the live one serves
                # the mini and the attached popover — the mini NEVER
                # detaches with the popover — and the frozen one the
                # detached popover alone. Attached-ness is the test,
                # never anchor equality — a detach that froze the
                # CURRENT layer still read as following while the print
                # stayed on it (the live report: detaching did nothing
                # visible).
                decode_start = time.monotonic()
                plate_progress_payload = self._index.plate_progress(
                    physical.index, position, live_position)
                if self._plate_anchor is not None:
                    manual_payload = self._index.plate_progress(
                        self._plate_anchor, None, live_position)
                # The seek trace's T6 records the coordinator's own
                # decode cost — the payload build between the slider's
                # commit and the face's arrival, no longer an
                # unbracketed gap.
                plate_decode_ms = (time.monotonic() - decode_start) * 1000.0
                # The per-layer printed objects: the executed motions'
                # polygon visits, read back from the layer's start.
                # The rows go through the SAME normalisation the map
                # uses — the raw polygon may arrive flat or paired,
                # and the point-in-polygon test needs pairs (the
                # green-printed report: the raw rows never matched).
                exclude_status = (self._status.get("exclude_object") or {}) \
                    if isinstance(self._status, dict) else {}
                exclude_rows = plate_values(exclude_status)["objects"]
                visited = getattr(self._index, "plate_visited", None)
                if visited is not None and plate_progress_payload.get("split") is not None:
                    plate_visited = visited(physical.index,
                                            plate_progress_payload["split"], exclude_rows)
            self._snapshot = PrintSnapshot(job, self._jobs.observation, physical,
                estimate if estimate > 0 else None, self._files.metadata_complete,
                layer_progress=layer_progress, index_ready=view is not None,
                download_fraction=self._files.download_fraction,
                indexing=self._index.phase == "indexing",
                index_fraction=self._index.progress if self._index.phase == "indexing" else None,
                next_pause_layer=next_pause_layer,
                next_pause_eta=next_pause_eta,
                next_pause_fraction=next_pause_fraction,
                next_pause_baked=next_pause_baked,
                load_active=load_active,
                filament_total=filament_total if filament_total and filament_total > 0 else None,
                plate_progress=plate_progress_payload,
                plate_manual_progress=manual_payload,
                plate_decode_ms=plate_decode_ms,
                plate_layer_count=layer_count,
                plate_pass_fraction=self._index.plate_pass_fraction() if view is not None else None,
                plate_visited=plate_visited)
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
            detail = self._loads.advance(job)
            if detail is not None:
                self._detail = detail
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
                # The plate's own demand: the follower hydrates the
                # window even when the preview is detached (the
                # monitor-only index — the live ruling). The service
                # dedupes and clamps; this never grows a queue.
                if plate_available and self._index.phase != "indexing" \
                        and isinstance(current, int):
                    for layer in (current - 1, current, current + 1):
                        if layer >= 0:
                            self._index.request_hydration(layer)
                    # The detached face's own demand, re-asked every
                    # poll: the live window's advance is what evicts a
                    # frozen layer's geometry, so the request has to
                    # stand every time the print crosses a layer.
                    if self._plate_anchor is not None:
                        self._index.set_manual_anchor(self._plate_anchor)
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
            self._loads.reset()
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
            # The session boundary owns the pause bar's whole state
            # (the panel's catch); the deliberate-detach latch must
            # never survive into a new session either.
            self._next_pause.reset()
            self._user_detached = False
            self._index.bind(None)
            self._files.bind(None)
            self._pauses.bind(None)
            # A fresh binding has no toolpath of its own: the follower
            # stays detached until one exists (the 2026-09-17 ruling)
            # — a toolpath already in Cura (a local slice) keeps it.
            self._preview.attach(self._cura.has_toolpath)
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
        # The restore needs a toolpath to drive (the 2026-09-17
        # ruling); without one the follower stays detached.
        if was_attached and self._cura.has_toolpath:
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
        self._loads.abandon()
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
        self._loads.request_monitor()
        self._index.request()
        self._message("Downloading and indexing the print for the monitor…")
        self._client.force_refresh()
        QTimer.singleShot(2600, self.refresh)

    def request_load(self):
        if not self._binding.configured:
            self._message("Set a Moonraker URL before loading the current print")
            return
        self._loads.request_load()
        self._message("Resolving current print…")
        self._client.force_refresh()
        QTimer.singleShot(2600, self.refresh)

    def set_plate_anchor(self, anchor):
        """The follower face's anchor (the pop-over's layer slider): an
        index freezes the face on that layer, None rejoins the live
        print. The live-follow state machine is untouched — the
        retention window still tracks the print (the service carries the
        frozen window beside it), so detaching never costs the live
        layer its hydration, and the next live poll keeps resolving the
        print's own layer."""
        self._plate_anchor = anchor if isinstance(anchor, int) and not isinstance(anchor, bool) \
            and anchor >= 0 else None
        self._index.set_manual_anchor(self._plate_anchor)
        self.refresh()

    def set_plate_split(self, motions):
        """The follower face's within-layer scrub (the pop-over's
        progress slider): a motion count the frozen layer draws up to,
        None for the whole base, -1 for the FULL layer (a seek lands
        at 100% — the live request). Only a detach carries it — the
        live split is the print's own."""
        self._plate_split = motions if isinstance(motions, int) and not isinstance(motions, bool) \
            and motions >= -1 else None
        self._index.set_manual_split(self._plate_split)
        self.refresh()

    def toggle_attachment(self):
        # A manual toggle is a deliberate choice: it cancels any pending
        # watchdog re-attach. Without a toolpath the attach direction is
        # refused (the 2026-09-17 ruling) — detaching stays available.
        if not self._cura.has_toolpath and not self._preview.state.attached:
            return
        self._preview.attach(not self._preview.state.attached)
        # The deliberate-detach latch (the panel's catch): the
        # toolpath-arrival edge and the view-swap restore must never
        # undo the user's own detach after a toolpath flap.
        self._user_detached = not self._preview.state.attached
        self.refresh()
        if self._preview.state.attached: self._client.force_refresh()

    def toggle_pause(self, human_layer):
        total = self._snapshot.layer.total
        if total is None and self._cura.max_layer is not None: total = self._cura.max_layer + 1
        layer = int(human_layer) - 1
        # The backstop for the gating above: a baked pause at the layer
        # makes a manual schedule impossible, however the request
        # arrived.
        baked = self._next_pause.baked_layers()
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
        baked = self._next_pause.baked_layers()
        baked_block = selected is not None and selected in baked
        can_toggle = not baked_block and pause_can_toggle(snapshot.active, selected, current, total)
        unavailable = ("a pause is baked into the gcode at this layer" if baked_block
                       else pause_unavailable(snapshot.active, can_toggle, scheduled, current, selected))
        items = self._next_pause.rows(current)
        compact = status_text(
            detail=self._detail,
            load_requested=self._loads.load_requested,
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
            # The determinate fraction through every phase: the
            # download's byte fraction, then the index build's own
            # byte-offset progress — the sweep only when neither
            # exists.
            "loadProgress": (self._files.download_fraction if self._files.download_fraction is not None
                             else self._snapshot.index_fraction if self._snapshot.index_fraction is not None
                             else -1.0),
            # The one-pass index needs no stage suffix (the live
            # ruling): "Indexing…" is the whole story.
            "loadPhase": ("Downloading…" if self._files.phase == "downloading"
                          else "Resolving…" if self._files.phase == "resolving"
                          else "Indexing…" if self._index.phase == "indexing"
                          else "Rendering…" if self._cura.loading
                          else "Resolving current print…" if self._loads.load_requested or self._loads.monitor_requested else ""),
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
            # The card shows the pause list without a toolpath when
            # baked rows exist (the improve-Eta path: the index alone
            # must reveal them) — the flag separates that case from a
            # stale manual schedule. Its pair: the clear-all control
            # hides while only baked rows are listed (the ruling).
            "pauseAtLayerHasBaked": any(item["state"] == "baked" for item in items),
            "pauseAtLayerHasClearable": bool(self._pauses.layers),
            # The status bar's printer-side readouts (the ruling):
            # the live layer and Z height, never the preview's
            # selection. The row hides whole while the resolver has
            # no layer (the ruling) — no em-dash stand-ins.
            "layerReadoutAvailable": snapshot.layer.index is not None,
            "layerReadoutText": layer_readout(snapshot.layer),
            # The height is independently optional (the panel's
            # catch): a resolved layer with no height must not render
            # the banned "—" stand-in.
            "heightReadoutAvailable": snapshot.layer.height is not None,
            "heightReadoutText": height_readout(snapshot.layer),
            # The improve-Eta mirror (the ruling): the card's hourglass
            # is clickable only while the estimate still rides the
            # plain blend and no download runs — the click DISABLES
            # once the download + indexing starts (no retry), and the
            # index keeps it disabled. The click is the monitor's
            # improveEta itself; nothing else changes.
            "improveEtaAvailable": snapshot.active and snapshot.layer_eta is None
                and not (snapshot.load_active or self._loads.monitor_requested),
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


