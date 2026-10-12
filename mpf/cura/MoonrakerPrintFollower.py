"""Stable Cura extension facade; domain behaviour is composed, not inherited."""
from PyQt6.QtCore import QObject, pyqtSlot
from UM.Extension import Extension

from ..FollowerRuntime import FollowerRuntime

class MoonrakerPrintFollower(QObject, Extension):
    def __init__(self, application):
        QObject.__init__(self)
        Extension.__init__(self)
        self._runtime = FollowerRuntime(application, self)

    @property
    def client(self): return self._runtime.client
    @property
    def session(self): return self.client.session
    @property
    def transport(self): return self.client.transport
    @property
    def print_state(self): return self._runtime.coordinator.snapshot
    def index(self): return self._runtime.index
    @property
    def bed_mesh(self): return self._runtime.bed_mesh
    @property
    def presentation(self): return self._runtime.presentation
    @property
    def preview_toolhead(self): return self._runtime.preview_toolhead
    def has_toolpath(self): return self._runtime.cura.has_toolpath
    def request_file_download(self, relpath): self._runtime.file_download.request_save(relpath)

    @property
    def download_failed(self): return self._runtime.file_download.failed
    def download_progress(self): return self._runtime.file_download.progress()
    def cancel_file_download(self): self._runtime.file_download.cancel()

    def motionSmoothing(self): return not self._runtime.presentation.reported_position
    def current_printer_config(self): return self._runtime.binding.config
    def current_printer_identity(self): return self._runtime.binding.identity
    def apply_printer_config(self, config): return self._runtime.binding.apply(config)

    @property
    def persistence(self): return self._runtime.persistence
    @property
    def detection(self): return self._runtime.detection
    @property
    def toolhead_models(self): return self._runtime.toolhead_models
    def notice(self): return self._runtime.notice
    def whats_new(self): return self._runtime.whats_new
    @pyqtSlot()
    def confirmForceLoadCurrentPrint(self): self._runtime.coordinator.confirm_load()
    def receive_toolhead_fans(self, values): self._runtime.toolhead.set_fan_readings(values)
    def receive_preview_block(self, block): self._runtime.coordinator.receive_preview_block(block)
    def confirmDownloadForMonitor(self): self._runtime.coordinator.download_for_monitor()

    @pyqtSlot()
    def toggleFollowingPause(self): self._runtime.coordinator.toggle_attachment()

    def setPlateAnchor(self, anchor): self._runtime.coordinator.set_plate_anchor(anchor)
    def setPlateSplit(self, motions): self._runtime.coordinator.set_plate_split(motions)
    def setFollowerPopoverOpen(self, popover_open): self._runtime.coordinator.set_popover_open(popover_open)
    # The popover's pause block: the one the Preview card just received.
    def pauseAtLayerBlock(self): return self._runtime.coordinator.pause_block

    def invalidateIndex(self): self._runtime.index.invalidate()
    def deinitialize(self):
        # The leak probe stops with the plugin (no dead runtime, no re-stacked timer).
        from ..diagnostics.LeakProbe import stop_leak_probe
        stop_leak_probe()
        self._runtime.close()
