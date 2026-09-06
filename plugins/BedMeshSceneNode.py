from __future__ import annotations

from typing import Any, Dict, List

import numpy

from UM.Math.Color import Color
from UM.Mesh.MeshData import MeshData
from UM.Resources import Resources
from UM.Scene.SceneNode import SceneNode
from UM.View.GL.OpenGL import OpenGL


class BedMeshSceneNode(SceneNode):
    """Non-selectable coloured Klipper bed-mesh surface for Cura's 3D scene."""

    DEFAULT_EXAGGERATION = 20.0
    SURFACE_ALPHA = 0.58
    SURFACE_LIFT = 0.035

    def __init__(self) -> None:
        super().__init__(name="Moonraker bed mesh", node_id="MoonrakerPrintFollowerBedMesh")
        self.setSelectable(False)
        self.setCalculateBoundingBox(False)
        self._shader = None

    @staticmethod
    def _scene_x(printer_x: float, machine_width: float, center_is_zero: bool) -> float:
        return printer_x if center_is_zero else printer_x - machine_width / 2.0

    @staticmethod
    def _scene_z(printer_y: float, machine_depth: float, center_is_zero: bool) -> float:
        # Cura's scene is Y-up and its build-plate depth axis is the negative
        # of printer Y.  Non-centred machines additionally put printer (0, 0)
        # at Cura's front-left corner.
        return -printer_y if center_is_zero else machine_depth / 2.0 - printer_y

    @classmethod
    def _colour(cls, value: float, minimum: float, maximum: float) -> List[float]:
        limit = max(abs(float(minimum)), abs(float(maximum)), 1e-9)
        t = max(0.0, min(1.0, 0.5 + 0.5 * float(value) / limit))
        stops = (
            (0.00, (0.10, 0.28, 0.95)),
            (0.25, (0.00, 0.72, 1.00)),
            (0.50, (0.20, 0.86, 0.38)),
            (0.75, (1.00, 0.82, 0.12)),
            (1.00, (0.92, 0.16, 0.12)),
        )
        for index in range(1, len(stops)):
            left_t, left = stops[index - 1]
            right_t, right = stops[index]
            if t <= right_t:
                fraction = (t - left_t) / max(1e-9, right_t - left_t)
                return [
                    left[channel] + (right[channel] - left[channel]) * fraction
                    for channel in range(3)
                ] + [cls.SURFACE_ALPHA]
        return [*stops[-1][1], cls.SURFACE_ALPHA]

    def clear(self) -> None:
        self.setMeshData(None)
        self.setVisible(False)

    def updateMesh(
        self,
        snapshot: Dict[str, Any],
        machine_width: float,
        machine_depth: float,
        center_is_zero: bool,
        exaggeration: float = DEFAULT_EXAGGERATION,
    ) -> bool:
        try:
            rows = int(snapshot.get("rows") or 0)
            columns = int(snapshot.get("columns") or 0)
            values = list(snapshot.get("values") or [])
            x_min = float(snapshot.get("xMin"))
            x_max = float(snapshot.get("xMax"))
            y_min = float(snapshot.get("yMin"))
            y_max = float(snapshot.get("yMax"))
            minimum = float(snapshot.get("minimum"))
            maximum = float(snapshot.get("maximum"))
            exaggeration = max(1.0, min(100.0, float(exaggeration)))
        except (TypeError, ValueError):
            self.clear()
            return False

        if rows < 2 or columns < 2 or len(values) != rows * columns:
            self.clear()
            return False
        if x_max <= x_min or y_max <= y_min:
            self.clear()
            return False

        vertex_count = rows * columns
        face_count = (rows - 1) * (columns - 1) * 2
        vertices = numpy.zeros((vertex_count, 3), dtype=numpy.float32)
        colours = numpy.zeros((vertex_count, 4), dtype=numpy.float32)
        indices = numpy.zeros((face_count, 3), dtype=numpy.int32)

        for row in range(rows):
            printer_y = y_min + (y_max - y_min) * row / (rows - 1)
            scene_z = self._scene_z(printer_y, machine_depth, center_is_zero)
            for column in range(columns):
                printer_x = x_min + (x_max - x_min) * column / (columns - 1)
                scene_x = self._scene_x(printer_x, machine_width, center_is_zero)
                index = row * columns + column
                value = float(values[index])
                vertices[index] = (
                    scene_x,
                    self.SURFACE_LIFT + value * exaggeration,
                    scene_z,
                )
                colours[index] = self._colour(value, minimum, maximum)

        face = 0
        for row in range(rows - 1):
            for column in range(columns - 1):
                a = row * columns + column
                b = a + 1
                c = a + columns
                d = c + 1
                # Winding is intentionally consistent but back-face culling is
                # disabled when rendering, so the map remains visible from below.
                indices[face] = (a, c, b)
                face += 1
                indices[face] = (b, c, d)
                face += 1

        self.setMeshData(MeshData(vertices=vertices, indices=indices, colors=colours))
        return True

    def render(self, renderer) -> bool:
        mesh = self.getMeshData()
        if mesh is None or not self.isVisible():
            return True
        if self._shader is None:
            self._shader = OpenGL.getInstance().createShaderProgram(
                Resources.getPath(Resources.Shaders, "default.shader")
            )
        renderer.queueNode(
            self,
            shader=self._shader,
            transparent=True,
            backface_cull=False,
            sort=-4,
        )
        return True
