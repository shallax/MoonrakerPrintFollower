"""Composition root. This module constructs dependencies; it implements no domain policy."""

from __future__ import annotations

import os
from UM.Resources import Resources

from .BedMeshPresenter import BedMeshPresenter
from .CuraIntegration import CuraIntegration
from .GCodeIndex import PersistentIndexCache
from .GCodeIndexService import GCodeIndexService
from .MoonrakerClient import MoonrakerClient
from .PauseController import PauseController
from .PreviewFollower import PreviewFollower
from .PreviewMotion import PreviewMotion
from .PreviewPresentation import PreviewPresentation
from .PrintCoordinator import PrintCoordinator
from .PrinterBinding import PrinterBinding
from .RemoteFileService import RemoteFileService


class FollowerRuntime:
    def __init__(self, application, parent):
        self.client = MoonrakerClient(parent)
        self.binding = PrinterBinding(application, self.client, parent)
        self.cura = CuraIntegration(application, parent)
        self.files = RemoteFileService(self.client.transport, parent)
        cache = PersistentIndexCache(os.path.join(Resources.getCacheStoragePath(), "Moonraker_Print_Follower", "indexes"))
        self.index = GCodeIndexService(self.files, cache, parent)
        self.preview = PreviewFollower(self.cura)
        self.motion = PreviewMotion(self.cura, self.preview.remember, parent)
        self.preview.bind_motion(self.motion)
        self.pauses = PauseController(self.client, parent)
        self.presentation = PreviewPresentation(application, self.cura, parent)
        self.bed_mesh = BedMeshPresenter(application, self.cura, self.presentation, parent)
        self.coordinator = PrintCoordinator(client=self.client, binding=self.binding,
            files=self.files, index=self.index, cura=self.cura, preview=self.preview,
            pauses=self.pauses, presentation=self.presentation, bed_mesh=self.bed_mesh, parent=parent)
        self._closed = False
        self.binding.start()

    def close(self):
        if self._closed: return
        self._closed = True
        self.binding.close()
        self.coordinator.close()
        self.pauses.close()
        self.bed_mesh.close()
        self.presentation.close()
        self.motion.close()
        self.cura.close()
        self.index.close()
        self.files.close()
