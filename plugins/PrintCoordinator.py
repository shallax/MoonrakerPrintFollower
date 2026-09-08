"""Cross-domain orchestration with explicit dependencies; not a shared state bag."""
from __future__ import annotations

from PyQt6.QtCore import QObject
from UM.Logger import Logger

from .MonitorFormatting import parse_bed_mesh
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
        self._processing = self._closed = False
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
            physical = self._layers.resolve(self._status, config, view, self._files.metadata, self._cura.heights)
            try:
                estimate = float(self._files.metadata.get("estimated_time") or 0)
            except (TypeError, ValueError):
                estimate = 0
            self._snapshot = PrintSnapshot(job, self._jobs.observation, physical,
                estimate if estimate > 0 else None, self._files.metadata_complete)
            # The coordinator owns the mesh observation; the Monitor reads the
            # presenter's snapshot but never writes it. The presenter's
            # fingerprint guard makes the per-poll update cheap.
            self._bed_mesh.update(parse_bed_mesh(self._status.get("bed_mesh")))
            self._cura.watch(config.enabled)
            if self._snapshot.active:
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
            self._publish()
        finally:
            self._processing = False

    def reset_binding(self):
        self._processing = True
        try:
            self._load_job = None
            self._load_requested = False
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
        self._load_job = None
        self._load_requested = False
        self._publish()

    def _index_changed(self):
        # A newly installed index resets path anchors, not print-local attachment.
        if self._index.phase == "indexing": self._preview.reset_tracking()
        self.refresh()

    def _position_changed(self):
        if self._binding.config.enabled:
            if self._preview.detect_override(): self._detail = "Detached"
        self._preview.update_eta(self._snapshot, self._index.view)
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

    def request_load(self):
        if not self._binding.configured:
            self._message("Set a Moonraker URL before loading the current print")
            return
        self._load_requested = True
        self._message("Resolving current print…")
        self._client.force_refresh()

    def toggle_attachment(self):
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


