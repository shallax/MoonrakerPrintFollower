"""Pixel-width capsule strokes expanded and antialiased by the GPU."""

from array import array
from pathlib import Path
import struct
from PyQt6.QtQuick import QSGMaterial, QSGMaterialType, QSGMaterialShader, QSGGeometry

TYPE = QSGMaterialType()
# Qt owns the native shader; retain its Python virtual-method wrapper.
SHADERS = []


class FollowerStrokeShader(QSGMaterialShader):
    def __init__(self):
        super().__init__()
        for stage, name in ((self.Stage.VertexStage, "stroke.vert.qsb"), (self.Stage.FragmentStage, "stroke.frag.qsb")):
            self.setShaderFileName(stage, str(Path(__file__).parent / "shaders" / name))

    def updateUniformData(self, state, new, old):
        m = state.combinedMatrix()
        colour = new.colour
        alpha = colour.alphaF() * state.opacity()
        values = (
            list(m.data())
            + [colour.redF() * alpha, colour.greenF() * alpha, colour.blueF() * alpha, alpha]
            + [
                new.width * state.devicePixelRatio() / 2,
                new.viewport[0] * state.devicePixelRatio() / 2,
                new.viewport[1] * state.devicePixelRatio() / 2,
                float(new.aa),
            ]
            + [float(new.rounded), 0, 0, 0]
        )
        data = struct.pack("<28f", *values)
        state.uniformData().replace(0, len(data), data)
        return True


class FollowerStrokeMaterial(QSGMaterial):
    def __init__(self, colour, width=1, aa=False, rounded=True):
        super().__init__()
        self.colour = colour
        self.width = width
        self.aa = aa
        self.rounded = rounded
        self.viewport = (556, 556)
        self.setFlag(self.Flag.Blending, True)
        self.setFlag(self.Flag.RequiresFullMatrix, True)

    def type(self):
        return TYPE

    def compare(self, other):
        a = (self.colour.rgba(), self.width, self.aa, self.rounded, self.viewport)
        b = (other.colour.rgba(), other.width, other.aa, other.rounded, other.viewport)
        return (a > b) - (a < b)

    def createShader(self, mode):
        shader = FollowerStrokeShader()
        SHADERS.append(shader)
        return shader


ATTR = QSGGeometry.AttributeSet(
    [
        QSGGeometry.Attribute.create(0, 2, QSGGeometry.Type.FloatType.value, True),
        QSGGeometry.Attribute.create(1, 2, QSGGeometry.Type.FloatType.value),
        QSGGeometry.Attribute.create(2, 2, QSGGeometry.Type.FloatType.value),
    ],
    24,
)


def pack_shader(data, cancel=None):
    try:
        import numpy as np
    except ImportError:
        return _pack_stdlib(data, cancel)
    result = []
    corners = np.array([[-1, 1], [-1, -1], [1, 1], [1, 1], [-1, -1], [1, -1]], dtype=np.float32)
    for role, name, motions, packed in data:
        if cancel is not None and cancel.is_set():
            return ()
        points = np.frombuffer(packed, dtype=np.float32).reshape(-1, 4)
        vertices = np.empty((len(points), 6, 6), dtype=np.float32)
        for i, corner in enumerate(corners):
            vertices[:, i, :2] = points[:, :2] if corner[0] < 0 else points[:, 2:]
        vertices[:, :, 2:4] = (points[:, 2:] - points[:, :2])[:, None, :]
        vertices[:, :, 4:6] = corners[None, :, :]
        result.append((role, name, motions, vertices.tobytes()))
    return tuple(result)


def _pack_stdlib(data, cancel=None):
    """Development fallback; Cura itself supplies NumPy."""
    result = []
    corners = ((-1, 1), (-1, -1), (1, 1), (1, 1), (-1, -1), (1, -1))
    for role, name, motions, packed in data:
        points, vertices = array("f"), array("f")
        points.frombytes(packed)
        for offset in range(0, len(points), 4):
            if offset % 4096 == 0 and cancel is not None and cancel.is_set():
                return ()
            x, y, ex, ey = points[offset : offset + 4]
            for cx, cy in corners:
                vertices.extend((x if cx < 0 else ex, y if cx < 0 else ey, ex - x, ey - y, cx, cy))
        result.append((role, name, motions, vertices.tobytes()))
    return tuple(result)
