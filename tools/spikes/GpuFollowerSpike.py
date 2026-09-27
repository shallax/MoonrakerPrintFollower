"""Experimental retained GPU toolpaths; installed only by the live spike harness."""
from array import array
import bisect
import ctypes
import math
import threading
import time

from PyQt6.QtCore import QTimer, pyqtProperty, pyqtSignal
from PyQt6 import sip
from PyQt6.QtGui import QColor, QMatrix4x4
from PyQt6.QtQuick import (QQuickItem, QSGFlatColorMaterial, QSGGeometry,
                          QSGGeometryNode, QSGNode, QSGTransformNode)

COLOURS = {'WALL-OUTER': '#d32f2f', 'WALL-INNER': '#388e3c', 'SKIN': '#e65100',
           'FILL': '#1976d2', 'SUPPORT': '#00838f', 'SKIRT': '#00897b',
           'TRAVEL': '#b085e8'}
WIDE_VERTICES = 30  # Rectangle + two four-triangle semicircular caps.
CAP_POINTS = tuple((math.cos(-math.pi / 2 + i * math.pi / 4),
                    math.sin(-math.pi / 2 + i * math.pi / 4)) for i in range(5))


def prepare(payloads):
    """Sort each class once; every later progress update is a binary search."""
    prepared = []
    deadline = time.perf_counter() + .008
    for role, payload in payloads:
        if not payload:
            continue
        groups = dict(payload.get('classes') or {})
        groups['TRAVEL'] = payload.get('travels') or []
        for name, segments in groups.items():
            edges = []
            for points in segments:
                for a, b in zip(points, points[1:], strict=False):
                    edges.append((b[2], a[0], a[1], b[0], b[1]))
                if time.perf_counter() > deadline:
                    time.sleep(.001)
                    deadline = time.perf_counter() + .008
            edges.sort(key=lambda edge: edge[0])
            if edges:
                packed = array('f')
                for edge in edges:
                    packed.extend(edge[1:])
                prepared.append((role, name, tuple(edge[0] for edge in edges), packed.tobytes()))
    return tuple(prepared)


def stroke_geometry(data, width, sx, sy, travel_ratio=.7, rounded=True):
    """Expand wide strokes off the render thread; no driver-wide-line reliance."""
    result = []
    deadline = time.perf_counter() + .008
    for role, name, motions, packed in data:
        stroke_width = width * travel_ratio if name == 'TRAVEL' else width
        points, triangles = array('f'), array('f')
        points.frombytes(packed)
        for i in range(0, len(points), 4):
            ax, ay, bx, by = points[i:i + 4]
            dx, dy = (bx - ax) * sx, (by - ay) * sy
            length = math.hypot(dx, dy)
            nx, ny = (-dy / length, dx / length) if length else (0, 1)
            ox, oy = nx * stroke_width / (2 * sx), ny * stroke_width / (2 * sy)
            triangles.extend((ax + ox, ay + oy, ax - ox, ay - oy, bx + ox, by + oy,
                              bx + ox, by + oy, ax - ox, ay - oy, bx - ox, by - oy))
            if rounded:
                ux, uy = (dx / length, dy / length) if length else (1, 0)
                for cx, cy, direction in ((ax, ay, -1), (bx, by, 1)):
                    arc = tuple((cx + (ux * c * direction + nx * s) * stroke_width / (2 * sx),
                                 cy + (uy * c * direction + ny * s) * stroke_width / (2 * sy))
                                for c, s in CAP_POINTS)
                    for a, b in zip(arc, arc[1:], strict=False):
                        triangles.extend((cx, cy, *a, *b))
            if time.perf_counter() > deadline:
                time.sleep(.001)
                deadline = time.perf_counter() + .008
        result.append((role, name, motions, triangles.tobytes()))
    return tuple(result)


class GpuFollowerSpike(QQuickItem):
    layersChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    readyChanged = pyqtSignal()
    prepared = pyqtSignal(int, object)
    strokePrepared = pyqtSignal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._layers = {}
        self._settings = {}
        self._generation = 0
        self._data = ()
        self._render_generation = -1
        self.prepared.connect(self._prepared)
        self._stroke_data = ()
        self._stroke_key = None
        self._stroke_busy = False
        self._stroke_timer = QTimer(self)
        self._stroke_timer.setSingleShot(True)
        self._stroke_timer.setInterval(80)
        self._stroke_timer.timeout.connect(self._prepare_stroke)
        self.strokePrepared.connect(self._stroke_prepared)

    @pyqtProperty('QVariantMap', notify=layersChanged)
    def layers(self):
        return self._layers

    @layers.setter
    def layers(self, value):
        value = dict(value or {})
        if value == self._layers:
            return
        self._layers = value
        self._generation += 1
        generation = self._generation
        self._data = ()
        self._stroke_data = ()
        self._stroke_key = None
        self.update()
        payloads = tuple((role, getattr(layer, '_payload', None))
                         for role, layer in value.items())
        def build():
            data = prepare(payloads)
            try:
                self.prepared.emit(generation, data)
            except RuntimeError:
                pass  # The face was destroyed while its worker completed.
        threading.Thread(target=build, daemon=True, name='MPF-GPU-spike').start()
        self.layersChanged.emit()

    def _prepared(self, generation, data):
        if generation == self._generation:
            self._data = data
            self.readyChanged.emit()
            self._stroke_timer.start()
            self.update()

    @pyqtProperty(bool, notify=readyChanged)
    def ready(self):
        return bool(self._data)

    @pyqtProperty('QVariantMap', notify=settingsChanged)
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value):
        value = dict(value or {})
        if value != self._settings:
            previous_stroke = self._wanted_stroke()
            self._settings = value
            self.settingsChanged.emit()
            if previous_stroke != self._wanted_stroke():
                self._stroke_timer.start()
            self.update()

    def _wanted_stroke(self):
        plot = self._settings.get('plot') or {}
        scale = float(self._settings.get('scale', 1))
        return (self._generation, float(self._settings.get('lineWidth', 1)),
                max(.001, abs(float(plot.get('sx', 1)) * scale)),
                max(.001, abs(float(plot.get('sy', 1)) * scale)),
                float(self._settings.get('travelRatio', .7)))

    def _prepare_stroke(self):
        key = self._wanted_stroke()
        if not self._data or key == self._stroke_key or self._stroke_busy or key[1] <= 1:
            return
        data = self._data
        self._stroke_busy = True
        def build():
            result = stroke_geometry(data, *key[1:])
            try:
                self.strokePrepared.emit(key, result)
            except RuntimeError:
                pass
        threading.Thread(target=build, daemon=True, name='MPF-GPU-strokes').start()

    def _stroke_prepared(self, key, data):
        self._stroke_busy = False
        if key == self._wanted_stroke():
            self._stroke_key, self._stroke_data = key, data
            self.update()
        else:
            self._stroke_timer.start()

    @staticmethod
    def _node(parent, colour, triangles=False):
        node = QSGGeometryNode()
        node.setFlag(QSGNode.Flag.OwnsGeometry, True)
        node.setFlag(QSGNode.Flag.OwnsMaterial, True)
        geometry = QSGGeometry(QSGGeometry.defaultAttributes_Point2D(), 0)
        geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles if triangles else QSGGeometry.DrawingMode.DrawLines)
        node.setGeometry(geometry)
        material = QSGFlatColorMaterial()
        material.setColor(colour)
        node.setMaterial(material)
        parent.appendChildNode(node)
        return node

    @staticmethod
    def _vertices(node, data, count, width):
        geometry = node.geometry()
        if geometry.vertexCount() != count:
            geometry.allocate(count)
            if count:
                ctypes.memmove(int(geometry.vertexData()), data, count * 8)
            geometry.markVertexDataDirty()
            node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)
        if geometry.lineWidth() != width:
            geometry.setLineWidth(width)
            node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)

    def updatePaintNode(self, old, update_data):
        # All scene-graph ownership remains on Qt's render thread. No
        # QSG nodes or graphics resources are retained by the UI object.
        node = old or QSGTransformNode()
        settings = self._settings
        plot = settings.get('plot') or {}
        bed = plot.get('bed') or {}
        scale = float(settings.get('scale', 1))
        sx, sy = float(plot.get('sx', 1))*scale, float(plot.get('sy', 1))*scale
        wide = float(settings.get('lineWidth', 1)) > 1 and self._stroke_key is not None \
            and self._stroke_key[0] == self._generation \
            and self._stroke_key[2:4] == self._wanted_stroke()[2:4]
        rendered_data = self._stroke_data if wide else self._data
        thin = QColor(settings.get('gridThin', '#cccccc')).rgba()
        major = QColor(settings.get('gridMajor', '#888888')).rgba()
        grid_key = (tuple(bed.get(key, 0) for key in ('bedXMin', 'bedXMax', 'bedYMin', 'bedYMax')),
                    bool(settings.get('compact')), thin, major, sx, sy)
        if getattr(node, '_generation', None) != self._generation \
                or getattr(node, '_prepared_data', None) is not rendered_data:
            while node.firstChild() is not None:
                sip.delete(node.firstChild())
            node._generation = self._generation
            node._prepared_data = rendered_data
            node._groups = []
            node._grid_nodes = []
            node._grid_key = None
            for role, name, motions, data in rendered_data:
                pending = QColor('#888888' if role == 'current' else COLOURS.get(name, '#888888'))
                pending.setAlphaF(.55 if role == 'current' else .3)
                base = self._node(node, pending, wide)
                printed = self._node(node, QColor(COLOURS.get(name, '#888888')), wide)
                node._groups.append((role, name, motions, data, base, printed))
        if node._grid_key != grid_key:
            for child in node._grid_nodes:
                sip.delete(child)
            node._grid_key = grid_key
            node._grid_nodes = self._grid(node, grid_key)
        matrix = QMatrix4x4()
        matrix.translate(float(bed.get('offsetX', 0))*scale
                         + float(settings.get('panX', 0)) - float(bed.get('bedXMin', 0))*sx,
                         float(bed.get('offsetY', 0))*scale
                         + float(settings.get('panY', 0)) + float(bed.get('bedYMax', 0))*sy)
        matrix.scale(sx, -sy)
        node.setMatrix(matrix)
        split = settings.get('split')
        split = float('inf') if split is None else float(split)
        width = max(1, float(settings.get('lineWidth', 1)))
        for role, name, motions, data, base, printed in node._groups:
            travel = name == 'TRAVEL'
            enabled = role == 'current' or bool(settings.get('showPrevious' if role == 'prev' else 'showNext'))
            if travel:
                enabled = enabled and bool(settings.get('showTravels'))
            vertices = WIDE_VERTICES if wide else 2
            full_count = len(motions)*vertices
            base_count = full_count if enabled and not travel and (role != 'current' or settings.get('showBase')) else 0
            printed_count = bisect.bisect_left(motions, split)*vertices if enabled and role == 'current' else 0
            self._vertices(base, data, base_count, width)
            self._vertices(printed, data, printed_count, width*(float(settings.get('travelRatio', .7)) if travel else 1))
        return node

    def _grid(self, parent, key):
        """Bed-space graduations share the toolpaths' retained transform."""
        (xmin, xmax, ymin, ymax), compact, thin, major, sx, sy = key
        if xmax <= xmin or ymax <= ymin:
            return []
        fine, coarse = array('f'), array('f')
        for step, packed in ((10, fine), (50, coarse)):
            if step == 10 and compact:
                continue
            for x in range(math.ceil(xmin / step) * step, math.floor(xmax / step) * step + 1, step):
                if step == 50 or x % 50:
                    packed.extend((x, ymin, x, ymax))
            for y in range(math.ceil(ymin / step) * step, math.floor(ymax / step) * step + 1, step):
                if step == 50 or y % 50:
                    packed.extend((xmin, y, xmax, y))
        coarse.extend((xmin, ymin, xmax, ymin, xmax, ymin, xmax, ymax,
                       xmax, ymax, xmin, ymax, xmin, ymax, xmin, ymin))
        children = []
        for packed, colour, width in ((fine, thin, 1), (coarse, major, 1 if compact else 2)):
            expanded = stroke_geometry((('', '', (), packed.tobytes()),), width,
                                       max(.001, abs(sx)), max(.001, abs(sy)), rounded=False)[0][3]
            child = self._node(parent, QColor.fromRgba(colour), True)
            parent.removeChildNode(child)
            parent.prependChildNode(child)
            self._vertices(child, expanded, len(expanded) // 8, 1)
            children.append(child)
        return children
