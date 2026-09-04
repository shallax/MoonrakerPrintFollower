"""Plugin-owned live print-head marker for Cura Preview."""

from __future__ import annotations

import os
from typing import Optional, Tuple

from UM.Application import Application
from UM.Math.Color import Color
from UM.Math.Matrix import Matrix
from UM.Math.Vector import Vector
from UM.PluginRegistry import PluginRegistry
from UM.Resources import Resources
from UM.Scene.SceneNode import SceneNode
from UM.View.GL.OpenGL import OpenGL


class ToolheadIndicatorNode(SceneNode):
    """Render Cura's own nozzle model as a plugin-owned fallback marker.

    Cura's SimulationPass can suppress its native NozzleNode while it believes
    the user is switching layers. This node deliberately owns a second render
    instance of the *same nozzle mesh* so the live print-head remains visible
    when that happens.

    The mesh is taken directly from SimulationView's native NozzleNode whenever
    possible. A resource-path fallback loads SimulationView/resources/nozzle.stl,
    which is the exact file Cura's NozzleNode itself loads.
    """

    def __init__(self) -> None:
        super().__init__(name="Moonraker Print Follower live printhead")
        self._shader = None
        self._indicator_position = Vector()
        self._native_mesh_ready = False
        self.setSelectable(False)
        self.setCalculateBoundingBox(False)
        self.setVisible(False)

    def ensureNativeNozzleMesh(self, simulation_view=None) -> bool:
        """Ensure this node uses Cura's native SimulationView nozzle mesh."""
        if self._native_mesh_ready and self.getMeshData():
            return True

        # Best source: Cura's actual NozzleNode. This guarantees the fallback
        # uses precisely the same model as the active SimulationView build.
        if simulation_view is not None:
            try:
                get_nozzle = getattr(simulation_view, "getNozzleNode", None)
                if callable(get_nozzle):
                    native_node = get_nozzle()
                    mesh = native_node.getMeshData() if native_node is not None else None
                    if mesh:
                        self.setMeshData(mesh)
                        self._native_mesh_ready = True
                        return True
            except Exception:
                pass

        # Fallback to the exact resource path used by Cura's NozzleNode.
        try:
            plugin_path = PluginRegistry.getInstance().getPluginPath("SimulationView")
            if plugin_path:
                path = os.path.join(plugin_path, "resources", "nozzle.stl")
                reader = Application.getInstance().getMeshFileHandler().getReaderForFile(path)
                node = reader.read(path) if reader is not None else None
                mesh = node.getMeshData() if node is not None else None
                if mesh:
                    self.setMeshData(mesh)
                    self._native_mesh_ready = True
                    return True
        except Exception:
            pass

        return False

    def setIndicatorPosition(self, position: Tuple[float, float, float]) -> None:
        # Do not call SceneNode.setPosition() here. Cura treats ordinary scene
        # transforms as scene changes; moving a live marker every poll would make
        # SimulationView invalidate its own layer activity. The render transform
        # is therefore kept privately and produces no scene mutation signal.
        self._indicator_position = Vector(
            float(position[0]), float(position[1]), float(position[2])
        )

    def getWorldTransformation(self, copy: bool = True):
        local = Matrix()
        local.setByTranslation(self._indicator_position)
        parent = self.getParent()
        if parent is not None:
            matrix = parent.getWorldTransformation(copy=True)
            matrix.multiply(local)
        else:
            matrix = local
        return matrix.copy() if copy else matrix

    def getWorldPosition(self):
        return self.getWorldTransformation(copy=False).getTranslation()

    def render(self, renderer) -> bool:
        if not self.isVisible() or not self.getMeshData():
            return False
        if self._shader is None:
            self._shader = OpenGL.getInstance().createShaderProgram(
                Resources.getPath(Resources.Shaders, "color.shader")
            )
            # Match Cura's native NozzleNode / SimulationPass colour exactly.
            try:
                color = Color(*Application.getInstance().getTheme().getColor("layerview_nozzle").getRgb())
            except Exception:
                color = Color(1.0, 0.55, 0.0, 1.0)
            self._shader.setUniformValue("u_color", color)
            self._shader.setUniformValue("u_opacity", 0)
        renderer.queueNode(self, shader=self._shader, transparent=True)
        return True
