"""Retained object outlines using the follower's scene-graph stroke engine."""
from array import array
import math

from PyQt6.QtCore import pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QMatrix4x4
from PyQt6.QtQuick import QQuickItem, QSGTransformNode, QQuickWindow, QSGRendererInterface
from PyQt6 import sip

from .GpuFollower import GpuFollower, stroke_geometry


def object_strokes(scene):
    """Preserve the Canvas palette, halo, widths and centre-only fallback."""
    plot = scene.get('plot') or {}
    sx, sy = max(.001, abs(plot.get('sx', 1))), max(.001, abs(plot.get('sy', 1)))
    compact_scale = .5 if scene.get('compact') else 1
    result = []
    for row in scene.get('objects') or ():
        points = row.get('polygon')
        if not points and row.get('center'):
            x, y = row['center'][:2]
            radius = 3 * scene.get('screenScale', 1)
            points = [(x + math.cos(i * math.tau / 32) * radius / sx,
                       y + math.sin(i * math.tau / 32) * radius / sy) for i in range(32)]
        if not points:
            continue
        packed = array('f')
        for a, b in zip(points, list(points[1:]) + [points[0]], strict=False):
            packed.extend((a[0], a[1], b[0], b[1]))
        width = (3 if row.get('name') == scene.get('hoveredName') else
                 2.5 if row.get('current') else 1.5) * compact_scale
        colour = scene.get('excludedInk') if row.get('excluded') else (
            scene.get('currentInk') if row.get('current') else
            scene.get('passedInk') if row.get('passed') else scene.get('includedInk'))
        data = (('', '', (), packed.tobytes()),)
        for stroke_width, ink in ((width + 4 * compact_scale, scene.get('halo')), (width, colour)):
            result.append((stroke_geometry(data, stroke_width, sx, sy)[0][3], QColor(ink)))
    return result


class GpuObjectPicker(QQuickItem):
    sceneChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._scene = {}

    @pyqtProperty(bool, constant=True)
    def supported(self):
        return QQuickWindow.graphicsApi() == QSGRendererInterface.GraphicsApi.OpenGL

    @pyqtProperty('QVariantMap', notify=sceneChanged)
    def scene(self):
        return self._scene

    @scene.setter
    def scene(self, value):
        value = dict(value or {})
        if value != self._scene:
            self._scene = value
            self.sceneChanged.emit()
            self.update()

    def updatePaintNode(self, old, update_data):
        node = old or QSGTransformNode()
        scene = self._scene
        if getattr(node, '_scene', None) == scene:
            return node
        node._scene = scene
        while node.firstChild() is not None:
            sip.delete(node.firstChild())
        plot = scene.get('plot') or {}
        bed = plot.get('bed') or {}
        sx, sy = plot.get('sx', 1), plot.get('sy', 1)
        matrix = QMatrix4x4()
        matrix.translate(bed.get('offsetX', 0) - bed.get('bedXMin', 0) * sx,
                         bed.get('offsetY', 0) + bed.get('bedYMax', 0) * sy)
        matrix.scale(sx, -sy)
        node.setMatrix(matrix)
        if scene.get('showGrid'):
            key = (tuple(bed.get(k, 0) for k in ('bedXMin', 'bedXMax', 'bedYMin', 'bedYMax')),
                   bool(scene.get('compact')), QColor(scene.get('gridThin')).rgba(),
                   QColor(scene.get('gridMajor')).rgba(), sx, sy)
            GpuFollower._grid(self, node, key)
        for data, colour in object_strokes(scene):
            child = self._node(node, colour, True)
            self._vertices(child, data, len(data) // 8, 1)
        return node

    _node = staticmethod(GpuFollower._node)
    _vertices = staticmethod(GpuFollower._vertices)
