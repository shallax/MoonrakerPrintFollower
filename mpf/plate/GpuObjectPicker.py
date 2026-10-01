"""Retained object outlines using the follower's scene-graph stroke engine."""
from array import array
import math

from PyQt6.QtCore import pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QMatrix4x4
from PyQt6.QtQuick import QQuickItem, QSGNode, QSGTransformNode, QQuickWindow, QSGRendererInterface
from PyQt6 import sip

from .GpuFollower import GpuFollower, stroke_geometry


def object_style(scene, row):
    """State precedence and pixel widths shared by retained and cold paths."""
    compact_scale = .5 if scene.get('compact') else 1
    width = (3 if row.get('name') == scene.get('hoveredName') else
             2.5 if row.get('current') else 1.5) * compact_scale
    colour = scene.get('excludedInk') if row.get('excluded') else (
        scene.get('currentInk') if row.get('current') else
        scene.get('passedInk') if row.get('passed') else scene.get('includedInk'))
    return width, colour


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
        width, colour = object_style(scene, row)
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
        if not hasattr(node, '_objects'):
            node._grid_node = QSGTransformNode()
            node.appendChildNode(node._grid_node)
            node._grid_key = None
            node._objects = []
        plot = scene.get('plot') or {}
        bed = plot.get('bed') or {}
        sx, sy = plot.get('sx', 1), plot.get('sy', 1)
        matrix = QMatrix4x4()
        matrix.translate(bed.get('offsetX', 0) - bed.get('bedXMin', 0) * sx,
                         bed.get('offsetY', 0) + bed.get('bedYMax', 0) * sy)
        matrix.scale(sx, -sy)
        node.setMatrix(matrix)
        key = None
        if scene.get('showGrid'):
            key = (tuple(bed.get(k, 0) for k in ('bedXMin', 'bedXMax', 'bedYMin', 'bedYMax')),
                   bool(scene.get('compact')), QColor(scene.get('gridThin')).rgba(),
                   QColor(scene.get('gridMajor')).rgba(), sx, sy,
                   QColor(scene.get('axisX', '#ef5350')).rgba(),
                   QColor(scene.get('axisY', '#66bb6a')).rgba(),
                   bool(scene.get('showAxisArrows', True)))
        if key != node._grid_key:
            while node._grid_node.firstChild() is not None:
                sip.delete(node._grid_node.firstChild())
            if key is not None:
                GpuFollower._grid(self, node._grid_node, key)
            node._grid_key = key
        rows = scene.get('objects') or ()
        while len(node._objects) > len(rows):
            sip.delete(node._objects.pop())
        while len(node._objects) < len(rows):
            group = QSGTransformNode()
            group._geometry_key = None
            node.appendChildNode(group)
            node._objects.append(group)
        for row, group in zip(rows, node._objects, strict=True):
            # Retain each object's native geometry independently. Hover and
            # live state changes usually affect just one or two objects; pan
            # only changes the parent matrix. Neither rebuilds the whole bed.
            width, colour = object_style(scene, row)
            geometry_key = (tuple(tuple(point) for point in row.get('polygon') or ()),
                            tuple(row.get('center') or ()), width,
                            bool(scene.get('compact')), scene.get('screenScale', 1), sx, sy)
            if geometry_key != group._geometry_key:
                while group.firstChild() is not None:
                    sip.delete(group.firstChild())
                for data, ink in object_strokes(dict(scene, objects=[row])):
                    child = self._node(group, ink, True)
                    self._vertices(child, data, len(data) // 8, 1)
                group._geometry_key = geometry_key
            child = group.firstChild()
            for ink in (QColor(scene.get('halo')), QColor(colour)):
                if child is None:
                    break
                material = child.material()
                if material.color() != ink:
                    material.setColor(ink)
                    child.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
                child = child.nextSibling()
        return node

    _node = staticmethod(GpuFollower._node)
    _vertices = staticmethod(GpuFollower._vertices)
