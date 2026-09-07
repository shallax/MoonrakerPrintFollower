"""Stable Cura extension facade; domain behaviour is composed, not inherited."""
from PyQt6.QtCore import QObject, pyqtSlot
from UM.Extension import Extension

from .FollowerRuntime import FollowerRuntime


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
    @property
    def bed_mesh(self): return self._runtime.bed_mesh

    def current_printer_config(self): return self._runtime.binding.config
    def current_printer_identity(self): return self._runtime.binding.identity
    def apply_printer_config(self, config): self._runtime.binding.apply(config)

    @pyqtSlot()
    def confirmForceLoadCurrentPrint(self): self._runtime.coordinator.confirm_load()

    @pyqtSlot()
    def toggleFollowingPause(self): self._runtime.coordinator.toggle_attachment()

    def deinitialize(self): self._runtime.close()
