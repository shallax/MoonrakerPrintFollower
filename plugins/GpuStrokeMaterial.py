"""Pixel-width capsule strokes expanded and antialiased by the GPU."""

from array import array
from pathlib import Path
import struct
from PyQt6.QtGui import QColor
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
            + [float(new.rounded), new.split, float(new.clip), float(new.physical)]
        )
        values += list(new.colour_options)
        for ink in new.palette:
            values += [ink.redF(), ink.greenF(), ink.blueF(), ink.alphaF()]
        data = struct.pack("<96f", *values)
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
        self.split = 0.0
        self.clip = False
        self.physical = False
        self.colour_options = (-1., 0., 0., .2)
        self.palette = tuple(QColor("#888888") for _ in range(16))
        self.setFlag(self.Flag.Blending, True)
        self.setFlag(self.Flag.RequiresFullMatrix, True)

    def type(self):
        return TYPE

    def compare(self, other):
        a = (self.colour.rgba(), self.width, self.aa, self.rounded, self.viewport, self.split, self.clip, self.physical, self.colour_options, tuple(c.rgba() for c in self.palette))
        b = (other.colour.rgba(), other.width, other.aa, other.rounded, other.viewport, other.split, other.clip, other.physical, other.colour_options, tuple(c.rgba() for c in other.palette))
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
        QSGGeometry.Attribute.create(3, 2, QSGGeometry.Type.FloatType.value),
        QSGGeometry.Attribute.create(4, 2, QSGGeometry.Type.FloatType.value),
    ],
    40,
)


def pack_shader(data, cancel=None):
    try:
        import numpy as np
    except ImportError:
        return _pack_stdlib(data, cancel)
    result = []
    corners = np.array([[-1, 1], [-1, -1], [1, 1], [1, 1], [-1, -1], [1, -1]], dtype=np.float32)
    for row in data:
        role, name, motions, packed = row[:4]
        if name in ("RETRACTION", "UNRETRACTION"):
            result.append((role, name, motions, packed))
            continue
        if cancel is not None and cancel.is_set():
            return ()
        points = np.frombuffer(packed, dtype=np.float32).reshape(-1, 4)
        vertices = np.empty((len(points), 6, 10), dtype=np.float32)
        for i, corner in enumerate(corners):
            vertices[:, i, :2] = points[:, :2] if corner[0] < 0 else points[:, 2:]
        vertices[:, :, 2:4] = (points[:, 2:] - points[:, :2])[:, None, :]
        vertices[:, :, 4:6] = corners[None, :, :]
        if len(row) > 4:
            vertices[:, :, 4] *= np.frombuffer(row[4], dtype=np.float32)[:, None]
        vertices[:, :, 8:10] = np.frombuffer(row[5], dtype=np.float32).reshape(-1, 2)[:, None, :] if len(row) > 5 else 0
        motion = np.asarray(motions, dtype=np.float32)
        starts = np.zeros(len(points), dtype=np.float32)
        spans = np.ones(len(points), dtype=np.float32)
        if len(motion) > 1 and np.any(motion[1:] == motion[:-1]):
            boundaries = np.r_[0, np.flatnonzero(motion[1:] != motion[:-1]) + 1]
            lengths = np.linalg.norm(points[:, 2:] - points[:, :2], axis=1)
            cumulative = np.r_[0.0, np.cumsum(lengths, dtype=np.float64)]
            counts = np.diff(np.r_[boundaries, len(points)])
            totals = np.repeat(np.add.reduceat(lengths, boundaries), counts)
            totals = np.maximum(totals, 1e-12)
            starts = (cumulative[:-1] - np.repeat(cumulative[boundaries], counts)) / totals
            spans = lengths / totals
        vertices[:, :, 6] = (motion + starts)[:, None]
        vertices[:, :, 7] = spans[:, None]
        result.append((role, name, motions, vertices.tobytes()))
    return tuple(result)


def _pack_stdlib(data, cancel=None):
    """Development fallback; Cura itself supplies NumPy."""
    result = []
    corners = ((-1, 1), (-1, -1), (1, 1), (1, 1), (-1, -1), (1, -1))
    for row in data:
        role, name, motions, packed = row[:4]
        if name in ("RETRACTION", "UNRETRACTION"):
            result.append((role, name, motions, packed))
            continue
        widths = array("f")
        if len(row) > 4:
            widths.frombytes(row[4])
        metrics = array("f")
        if len(row) > 5:
            metrics.frombytes(row[5])
        points, vertices = array("f"), array("f")
        points.frombytes(packed)
        ranges = []
        group = 0
        while group < len(motions):
            end = group + 1
            while end < len(motions) and motions[end] == motions[group]:
                end += 1
            lengths = [((points[i * 4 + 2] - points[i * 4]) ** 2 + (points[i * 4 + 3] - points[i * 4 + 1]) ** 2) ** .5 for i in range(group, end)]
            total = max(sum(lengths), 1e-12)
            walked = 0.0
            for length in lengths:
                ranges.append((motions[group] + walked / total, length / total))
                walked += length
            group = end
        for offset in range(0, len(points), 4):
            if offset % 4096 == 0 and cancel is not None and cancel.is_set():
                return ()
            x, y, ex, ey = points[offset : offset + 4]
            for cx, cy in corners:
                vertices.extend((x if cx < 0 else ex, y if cx < 0 else ey, ex - x, ey - y, cx * (widths[offset // 4] if widths else 1.0), cy, *ranges[offset // 4], *(metrics[offset // 2:offset // 2 + 2] if metrics else (0., 0.))))
        result.append((role, name, motions, vertices.tobytes()))
    return tuple(result)
