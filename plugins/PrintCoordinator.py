"""Cross-domain orchestration with explicit dependencies; not a shared state bag."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import time
from urllib.parse import quote

from PyQt6.QtCore import QObject, QTimer
from UM.Logger import Logger

from .MonitorFormatting import parse_bed_mesh, result
from .PreviewFormatting import (
    pause_can_toggle,
    pause_eta,
    pause_summary,
    pause_unavailable,
    status_icon,
    status_text,
)
from .PrintState import LayerResolver, PrintSnapshot
from .RemoteJobService import RemoteJobService


class PrintCoordinator(QObject):
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
        self._load_job = None
        self._load_requested = False
        # Moonraker's file metadata (the slicer header parsed server-side):
        # layer height and slicer estimate for prints the user never
        # loaded. Fetched once per job, retried every 30 s until success.
        self._mr_meta = {}
        self._mr_meta_file = ""
        self._mr_meta_job = ""
        self._mr_meta_at = 0.0
        self._layer_trace_at = 0.0
        self._monitor_requested = False
        self._publish_at = 0.0
        self._processing = self._closed = False
        # An override detach that no further view activity follows is
        # almost certainly Cura's own restoration (a stage switch or a
        # window re-activation can hang and land it late) — the watchdog
        # re-attaches after the view has been quiet, while the user is
        # still in the Preview stage. Manual toggles cancel it.
        self._detach_from_override = False
        self._detach_watchdog = QTimer(self)
        self._detach_watchdog.setSingleShot(True)
        self._detach_watchdog.setInterval(3000)
        self._detach_watchdog.timeout.connect(self._watchdog_reattach)
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
        presentation.loadRequested.connect(self.confirm_load)
        presentation.attachmentRequested.connect(self.toggle_attachment)
        presentation.pauseRequested.connect(self.toggle_pause)
        presentation.removePauseRequested.connect(self.remove_pause)
        presentation.clearPausesRequested.connect(pauses.clear)

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
            config = self._binding.config
            job = self._files.job_key
            view = self._index.view
            if view is not None and view.job_key != job: view = None
            # The downloaded metadata wins; Moonraker's header parse is
            # the fallback that populates the layer-height readout and
            # the slicer estimate without any gcode download.
            metadata = self._files.metadata or self._mr_meta
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
            filename = str((self._status.get("print_stats") or {}).get("filename") or "")
            if self._snapshot.active and filename:
                self._maybe_fetch_mr_metadata(filename, job)
            if config.trace_layer and time.monotonic() - self._layer_trace_at >= 5:
                self._layer_trace_at = time.monotonic()
                info = (self._status.get("print_stats") or {}).get("info") or {}
                gpos = (self._status.get("gcode_move") or {}).get("gcode_position") or ()
                Logger.log("i",
                    "layer trace: raw_current=%s total=%s state=%s z=%s e=%s progress=%s one_based=%s z_fallback=%s mr_meta=%s mr_meta_keys=%s files_meta=%s heights_n=%s deltas=%s ascent=%.3f z_layer=%s -> layer=%s source=%s",
                    info.get("current_layer"), info.get("total_layer"),
                    (self._status.get("print_stats") or {}).get("state"),
                    gpos[2] if len(gpos) >= 3 else None, gpos[3] if len(gpos) >= 4 else None,
                    (self._status.get("virtual_sdcard") or {}).get("progress"),
                    config.moonraker_layer_is_one_based, config.z_fallback,
                    bool(self._mr_meta), sorted(self._mr_meta) if self._mr_meta else [],
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
        header parse, NOT a gcode download (the author's no-silent-
        downloads ruling covers the file itself; the header query is
        what Moonraker's own UI uses for the same readouts).

        The fetch LATCHES on success: a slicer header never changes
        mid-print, so once we hold the metadata for the current job the
        30 s retry ladder retires instead of re-querying for the whole
        print (a 10 h job was ~1,200 requests of the same JSON). The
        latch is job-keyed so a same-name file restart refetches."""
        if self._mr_meta and self._mr_meta_file == filename and self._mr_meta_job == job:
            return
        if filename == self._mr_meta_file and time.monotonic() - self._mr_meta_at < 30:
            return
        self._mr_meta_file = filename
        self._mr_meta_at = time.monotonic()
        self._mr_meta_job = job
        def done(payload, error):
            if self._closed or filename != self._mr_meta_file or job != self._mr_meta_job:
                return
            value = result(payload)
            if error or not isinstance(value, Mapping):
                return
            self._mr_meta = value
            self.refresh()
        # Same quoting as MoonrakerProtocol.metadata_endpoint (safe="/"):
        # subfolder files arrive with their path and must not be
        # %2F-escaped (proxies that reject encoded slashes 404 them).
        self._client.transport.send_json("follower", "mr-metadata", "GET",
            "server/files/metadata?filename=" + quote(filename, safe="/"),
            done, category="metadata")

    def reset_binding(self):
        self._detach_watchdog.stop()
        self._detach_from_override = False
        self._processing = True
        try:
            self._load_job = None
            self._load_requested = False
            # A monitor-only download in flight when the binding changes
            # (machine switch, job change) must not leak its busy flag
            # into the next session — panel finding P1-1.
            self._monitor_requested = False
            self._mr_meta = {}
            self._mr_meta_file = ""
            self._mr_meta_job = ""
            self._status = {}
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
                self._detach_from_override = True
                self._detach_watchdog.start()
        # Cura streams position changes at the render cadence; the
        # panel values do not need that rate. Throttle the ETA and
        # publish to 5 Hz — the author's preview-lag report.
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

    def _watchdog_reattach(self):
        # Only re-attach when nothing has contradicted the detach: the
        # user is still in Preview, has not toggled manually, and the view
        # has been quiet since. Cura's own restoration leaves the view
        # alone afterwards; an inspecting user keeps moving it.
        if (not self._detach_from_override or self._preview.state.attached
                or not self._cura.preview_active or self._closed):
            return
        self._detach_from_override = False
        self._preview.attach(True)
        self._client.force_refresh()

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
        the preview: the author's optimisation — the monitor's better
        layer and ETA must not pay for a scene render the user may not
        want. The index service pulls metadata, restores or downloads
        the file, and builds the index; a later preview load finds the
        file already local and only renders."""
        if not self._binding.configured:
            self._message("Set a Moonraker URL before improving the monitor estimate")
            return
        self._monitor_requested = True
        self._index.request()
        self._message("Downloading and indexing the print for the monitor…")
        self._client.force_refresh()

    def request_load(self):
        if not self._binding.configured:
            self._message("Set a Moonraker URL before loading the current print")
            return
        self._load_requested = True
        self._message("Resolving current print…")
        self._client.force_refresh()

    def toggle_attachment(self):
        # A manual toggle is a deliberate choice: it cancels any pending
        # watchdog re-attach.
        self._detach_watchdog.stop()
        self._detach_from_override = False
        self._preview.attach(not self._preview.state.attached)
        self.refresh()
        if self._preview.state.attached: self._client.force_refresh()

    def toggle_pause(self, human_layer):
        total = self._snapshot.layer.total
        if total is None and self._cura.max_layer is not None: total = self._cura.max_layer + 1
        self._pauses.toggle(int(human_layer) - 1, self._snapshot.layer.index, total)
        self._publish()

    def remove_pause(self, human_layer):
        self._pauses.remove(int(human_layer) - 1)

    def _publish(self):
        if self._closed: return
        config, state, snapshot = self._binding.config, self._preview.state, self._snapshot
        selected, current, total = self._cura.selected_layer, snapshot.layer.index, snapshot.layer.total
        if total is None and self._cura.max_layer is not None: total = self._cura.max_layer + 1
        scheduled = selected is not None and selected in self._pauses.layers
        can_toggle = pause_can_toggle(snapshot.active, selected, current, total)
        unavailable = pause_unavailable(snapshot.active, can_toggle, scheduled, current, selected)
        items = []
        for layer in sorted(self._pauses.layers):
            remaining = self._preview.remaining(layer, self._index.view, end=True)
            items.append({"layer": layer + 1, "eta": pause_eta(remaining, self._preview.format_duration)})
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
            "activePrinterName": self._binding.identity[1], "hasToolpath": self._cura.has_toolpath,
            "statusText": compact, "statusIconName": status_icon(compact),
            "selectedLayerEtaText": state.eta_text,
            "pauseAtLayerActive": snapshot.active, "pauseAtLayerCandidate": selected + 1 if selected is not None else 0,
            "pauseAtLayerCanToggle": can_toggle, "pauseAtLayerScheduled": scheduled,
            "pauseAtLayerSummary": pause_summary(items),
            "pauseAtLayerItems": items, "pauseAtLayerUnavailableText": unavailable,
        })

    def close(self):
        self._closed = True


