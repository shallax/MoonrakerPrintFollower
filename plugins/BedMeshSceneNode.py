from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence, Tuple

import numpy

from UM.Mesh.MeshData import MeshData
from UM.Resources import Resources
from UM.Scene.SceneNode import SceneNode
from UM.View.GL.OpenGL import OpenGL


class BedMeshSceneNode(SceneNode):
    """Non-selectable coloured Klipper bed-mesh surface for Cura's 3D scene.

    Klipper only knows the configured mesh bounds. Cura, however, renders the
    complete build plate. The measured/interpolated Klipper surface therefore
    remains exactly where Klipper says it is, while one additional outer ring is
    linearly extrapolated to the physical Cura bed edges. Extrapolated vertices
    are intentionally more transparent so the unprobed region is not presented
    as measured data.
    """

    DEFAULT_EXAGGERATION = 20.0
    SURFACE_ALPHA = 0.58
    EXTRAPOLATED_ALPHA = 0.28
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
        # of printer Y. Non-centred machines additionally put printer (0, 0)
        # at Cura's front-left corner.
        return -printer_y if center_is_zero else machine_depth / 2.0 - printer_y

    @classmethod
    def _colour(
        cls,
        value: float,
        minimum: float,
        maximum: float,
        *,
        extrapolated: bool = False,
    ) -> List[float]:
        limit = max(abs(float(minimum)), abs(float(maximum)), 1e-9)
        t = max(0.0, min(1.0, 0.5 + 0.5 * float(value) / limit))
        stops = (
            (0.00, (0.10, 0.28, 0.95)),
            (0.25, (0.00, 0.72, 1.00)),
            (0.50, (0.20, 0.86, 0.38)),
            (0.75, (1.00, 0.82, 0.12)),
            (1.00, (0.92, 0.16, 0.12)),
        )
        alpha = cls.EXTRAPOLATED_ALPHA if extrapolated else cls.SURFACE_ALPHA
        for index in range(1, len(stops)):
            left_t, left = stops[index - 1]
            right_t, right = stops[index]
            if t <= right_t:
                fraction = (t - left_t) / max(1e-9, right_t - left_t)
                return [
                    left[channel] + (right[channel] - left[channel]) * fraction
                    for channel in range(3)
                ] + [alpha]
        return [*stops[-1][1], alpha]

    @staticmethod
    def _bed_axis_bounds(size: float, center_is_zero: bool) -> Tuple[float, float]:
        half = float(size) / 2.0
        return (-half, half) if center_is_zero else (0.0, float(size))

    @staticmethod
    def _axis_with_bed_edges(
        minimum: float,
        maximum: float,
        count: int,
        bed_minimum: float,
        bed_maximum: float,
    ) -> List[float]:
        values = [minimum + (maximum - minimum) * index / (count - 1) for index in range(count)]
        epsilon = 1e-6
        if bed_minimum < minimum - epsilon:
            values.insert(0, bed_minimum)
        elif bed_minimum > minimum + epsilon:
            # A Cura profile smaller than the configured Klipper mesh is unusual;
            # keep the overlay bounded to the visible Cura plate rather than draw
            # outside it.
            values[0] = bed_minimum
        if bed_maximum > maximum + epsilon:
            values.append(bed_maximum)
        elif bed_maximum < maximum - epsilon:
            values[-1] = bed_maximum
        return values

    @staticmethod
    def _extrapolation_segment(position: float, count: int) -> Tuple[int, float]:
        """Return a cell index and unconstrained fraction for interpolation/extrapolation."""
        if count < 2:
            return 0, 0.0
        if position <= 0.0:
            return 0, position
        maximum = float(count - 1)
        if position >= maximum:
            return count - 2, position - float(count - 2)
        index = min(count - 2, int(math.floor(position)))
        return index, position - float(index)

    @classmethod
    def _sample_matrix(
        cls,
        matrix: Sequence[Sequence[float]],
        printer_x: float,
        printer_y: float,
        x_min: float,
        x_max: float,
        y_min: float,
        y_max: float,
    ) -> float:
        """Bilinearly sample the mesh and linearly extrapolate outside its bounds."""
        rows = len(matrix)
        columns = len(matrix[0]) if rows else 0
        u = (printer_x - x_min) / (x_max - x_min) * (columns - 1)
        v = (printer_y - y_min) / (y_max - y_min) * (rows - 1)
        column, fu = cls._extrapolation_segment(u, columns)
        row, fv = cls._extrapolation_segment(v, rows)

        a = float(matrix[row][column])
        b = float(matrix[row][column + 1])
        c = float(matrix[row + 1][column])
        d = float(matrix[row + 1][column + 1])
        top = a + (b - a) * fu
        bottom = c + (d - c) * fu
        return top + (bottom - top) * fv

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
            values = [float(value) for value in list(snapshot.get("values") or [])]
            x_min = float(snapshot.get("xMin"))
            x_max = float(snapshot.get("xMax"))
            y_min = float(snapshot.get("yMin"))
            y_max = float(snapshot.get("yMax"))
            minimum = float(snapshot.get("minimum"))
            maximum = float(snapshot.get("maximum"))
            machine_width = float(machine_width)
            machine_depth = float(machine_depth)
            exaggeration = max(1.0, min(100.0, float(exaggeration)))
        except (TypeError, ValueError):
            self.clear()
            return False

        if rows < 2 or columns < 2 or len(values) != rows * columns:
            self.clear()
            return False
        if x_max <= x_min or y_max <= y_min or machine_width <= 0 or machine_depth <= 0:
            self.clear()
            return False

        matrix = [values[row * columns:(row + 1) * columns] for row in range(rows)]
        bed_x_min, bed_x_max = self._bed_axis_bounds(machine_width, center_is_zero)
        bed_y_min, bed_y_max = self._bed_axis_bounds(machine_depth, center_is_zero)
        x_axis = self._axis_with_bed_edges(x_min, x_max, columns, bed_x_min, bed_x_max)
        y_axis = self._axis_with_bed_edges(y_min, y_max, rows, bed_y_min, bed_y_max)

        render_rows = len(y_axis)
        render_columns = len(x_axis)
        vertex_count = render_rows * render_columns
        face_count = (render_rows - 1) * (render_columns - 1) * 2
        vertices = numpy.zeros((vertex_count, 3), dtype=numpy.float32)
        colours = numpy.zeros((vertex_count, 4), dtype=numpy.float32)
        indices = numpy.zeros((face_count, 3), dtype=numpy.int32)

        epsilon = 1e-6
        for row, printer_y in enumerate(y_axis):
            scene_z = self._scene_z(printer_y, machine_depth, center_is_zero)
            for column, printer_x in enumerate(x_axis):
                index = row * render_columns + column
                value = self._sample_matrix(matrix, printer_x, printer_y, x_min, x_max, y_min, y_max)
                extrapolated = bool(
                    printer_x < x_min - epsilon
                    or printer_x > x_max + epsilon
                    or printer_y < y_min - epsilon
                    or printer_y > y_max + epsilon
                )
                vertices[index] = (
                    self._scene_x(printer_x, machine_width, center_is_zero),
                    self.SURFACE_LIFT + value * exaggeration,
                    scene_z,
                )
                colours[index] = self._colour(
                    value,
                    minimum,
                    maximum,
                    extrapolated=extrapolated,
                )

        face = 0
        for row in range(render_rows - 1):
            for column in range(render_columns - 1):
                a = row * render_columns + column
                b = a + 1
                c = a + render_columns
                d = c + 1
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
