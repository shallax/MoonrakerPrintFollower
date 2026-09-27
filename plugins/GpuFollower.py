"""Retained scene-graph follower with bounded, cancellable CPU preparation."""

from array import array
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import weakref
import bisect
import ctypes
import math
import threading
import time

from PyQt6.QtCore import pyqtProperty, pyqtSignal
from PyQt6 import sip
from PyQt6.QtGui import QColor, QMatrix4x4, QGuiApplication
from PyQt6.QtQuick import (
    QQuickItem,
    QSGFlatColorMaterial,
    QSGGeometry,
    QSGGeometryNode,
    QSGNode,
    QSGTransformNode,
    QQuickWindow,
    QSGRendererInterface,
)

from .GpuStrokeMaterial import FollowerStrokeMaterial, ATTR, pack_shader

_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="MPF-GPU")
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 48 * 1024 * 1024


class _WorkState:
    def __init__(self):
        self.cancel = threading.Event()
        self.futures = []

    def close(self):
        self.cancel.set()
        for future in self.futures:
            future.cancel()


COLOURS = {
    "WALL-OUTER": "#d32f2f",
    "WALL-INNER": "#388e3c",
    "SKIN": "#e65100",
    "FILL": "#1976d2",
    "SUPPORT": "#00838f",
    "SKIRT": "#00897b",
    "TRAVEL": "#b085e8",
}
WIDE_VERTICES = 30  # Rectangle + two four-triangle semicircular caps.
CAP_POINTS = tuple(
    (math.cos(-math.pi / 2 + i * math.pi / 4), math.sin(-math.pi / 2 + i * math.pi / 4)) for i in range(5)
)


def dashed_edges(points):
    """Bed-space 0.5 mm dashes / gaps, continuous across short edges."""
    phase = 0.0
    for a, b in zip(points, points[1:], strict=False):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        travelled = 0.0
        while travelled < length:
            on = phase < 0.5
            amount = min(length - travelled, (0.5 if on else 1.0) - phase)
            if on and amount > 1e-9:
                lo, hi = travelled / length, (travelled + amount) / length
                yield (b[2], a[0] + dx * lo, a[1] + dy * lo, a[0] + dx * hi, a[1] + dy * hi)
            travelled += amount
            phase = (phase + amount) % 1.0


def prepare(payloads, cancel=None):
    """Sort each class once; every later progress update is a binary search."""
    payloads = tuple(sorted(payloads, key=lambda row: {"prev": 0, "next": 1, "current": 2}.get(row[0], 3)))
    if cancel is not None and cancel.is_set():
        return ()
    if len(payloads) > 1:
        # Ghosts arrive independently. Cache each role/layer so adding one
        # never walks the unchanged current and other ghost again.
        result = tuple(row for payload in payloads for row in prepare((payload,), cancel))
        return () if cancel is not None and cancel.is_set() else result
    key = tuple((role, id(payload)) for role, payload in payloads)
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is not None:
            _CACHE.move_to_end(key)
            return entry[1]
    prepared = []
    deadline = time.perf_counter() + 0.008
    for role, payload in payloads:
        if not payload:
            continue
        groups = dict(payload.get("classes") or {})
        groups["TRAVEL"] = payload.get("travels") or []
        for name, segments in groups.items():
            edges = []
            for points in segments:
                source = (
                    dashed_edges(points)
                    if role == "next"
                    else ((b[2], a[0], a[1], b[0], b[1]) for a, b in zip(points, points[1:], strict=False))
                )
                for edge in source:
                    edges.append(edge)
                    if len(edges) % 256 == 0 and time.perf_counter() > deadline:
                        if cancel is not None and cancel.is_set():
                            return ()
                        time.sleep(0.001)
                        deadline = time.perf_counter() + 0.008
                if time.perf_counter() > deadline:
                    if cancel is not None and cancel.is_set():
                        return ()
                    time.sleep(0.001)
                    deadline = time.perf_counter() + 0.008
            edges.sort(key=lambda edge: edge[0])
            if edges:
                packed = array("f")
                for edge in edges:
                    packed.extend(edge[1:])
                    if time.perf_counter() > deadline:
                        if cancel is not None and cancel.is_set():
                            return ()
                        time.sleep(0.001)
                        deadline = time.perf_counter() + 0.008
                prepared.append((role, name, tuple(edge[0] for edge in edges), packed.tobytes()))
    result = tuple(prepared)
    # Cache keys hold source payloads to prevent id reuse. Account for their
    # retained Python points as well as packed vertices / motion integers.
    # A long next-layer edge expands into many dashes, but retains its source
    # points only once. Charging a source point per dash refused dense windows
    # even when their actual storage comfortably fitted the cache.
    source_points = sum(len(segment) for _role, payload in payloads if payload
                        for segments in (tuple((payload.get("classes") or {}).values())
                                         + (payload.get("travels") or (),))
                        for segment in segments)
    charge = sum(len(row[3]) + len(row[2]) * 36 for row in result) + source_points * 192
    if (cancel is None or not cancel.is_set()) and charge <= _CACHE_BYTES:
        with _CACHE_LOCK:
            _CACHE[key] = (payloads, result, charge)
            _CACHE.move_to_end(key)
            while sum(entry[2] for entry in _CACHE.values()) > _CACHE_BYTES:
                _CACHE.popitem(last=False)
    return result


def stroke_geometry(data, width, sx, sy, travel_ratio=0.7, rounded=True, cancel=None):
    """Expand wide strokes off the render thread; no driver-wide-line reliance."""
    result = []
    deadline = time.perf_counter() + 0.008
    for role, name, motions, packed in data:
        stroke_width = width * travel_ratio if name == "TRAVEL" else width
        points, triangles = array("f"), array("f")
        points.frombytes(packed)
        for i in range(0, len(points), 4):
            ax, ay, bx, by = points[i : i + 4]
            dx, dy = (bx - ax) * sx, (by - ay) * sy
            length = math.hypot(dx, dy)
            nx, ny = (-dy / length, dx / length) if length else (0, 1)
            ox, oy = nx * stroke_width / (2 * sx), ny * stroke_width / (2 * sy)
            triangles.extend(
                (
                    ax + ox,
                    ay + oy,
                    ax - ox,
                    ay - oy,
                    bx + ox,
                    by + oy,
                    bx + ox,
                    by + oy,
                    ax - ox,
                    ay - oy,
                    bx - ox,
                    by - oy,
                )
            )
            if rounded and role != "next":
                ux, uy = (dx / length, dy / length) if length else (1, 0)
                for cx, cy, direction in ((ax, ay, -1), (bx, by, 1)):
                    arc = tuple(
                        (
                            cx + (ux * c * direction + nx * s) * stroke_width / (2 * sx),
                            cy + (uy * c * direction + ny * s) * stroke_width / (2 * sy),
                        )
                        for c, s in CAP_POINTS
                    )
                    for a, b in zip(arc, arc[1:], strict=False):
                        triangles.extend((cx, cy, *a, *b))
            elif rounded:
                # Flat dash ends keep wide strokes from filling the gaps.
                # Match the shared vertex stride with degenerate cap triangles.
                triangles.extend((ax, ay) * (WIDE_VERTICES - 6))
            if time.perf_counter() > deadline:
                if cancel is not None and cancel.is_set():
                    return ()
                time.sleep(0.001)
                deadline = time.perf_counter() + 0.008
        result.append((role, name, motions, triangles.tobytes()))
    return tuple(result)


class GpuFollower(QQuickItem):
    layersChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    readyChanged = pyqtSignal()
    prepared = pyqtSignal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._work = _WorkState()
        self.destroyed.connect(self._work.close)
        app = QGuiApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._work.close)
        self._layers = {}
        self._settings = {}
        self._generation = 0
        self._data = ()
        self._render_generation = -1
        self.prepared.connect(self._prepared)

    @pyqtProperty("QVariantMap", notify=layersChanged)
    def layers(self):
        return self._layers

    @layers.setter
    def layers(self, value):
        value = dict(value or {})
        if value == self._layers:
            return
        previous = self._layers
        self._layers = value
        self._generation += 1
        generation = self._generation
        # Keep only unchanged channels while an arriving ghost prepares.
        # A new current layer always clears the old scene immediately.
        self._data = tuple(row for row in self._data
                           if value.get("current") is previous.get("current")
                           and value.get(row[0]) is previous.get(row[0]))
        self.update()
        self.readyChanged.emit()
        self._work.close()
        self._work.cancel = threading.Event()
        self._work.futures = []
        cancel = self._work.cancel
        owner = weakref.ref(self)
        payloads = tuple(
            (role, layer.geometry_payload())
            for role, layer in value.items()
            if callable(getattr(layer, "geometry_payload", None))
        )

        def build():
            data = pack_shader(prepare(payloads, cancel), cancel)
            item = owner()
            if cancel.is_set() or item is None:
                return
            try:
                item.prepared.emit(generation, data)
            except RuntimeError:
                pass  # The face was destroyed while its worker completed.

        if payloads:
            self._work.futures.append(_POOL.submit(build))
        self.layersChanged.emit()

    def _prepared(self, generation, data):
        if generation == self._generation:
            self._data = data
            self.readyChanged.emit()
            self.update()

    @pyqtProperty(bool, constant=True)
    def supported(self):
        return QQuickWindow.graphicsApi() == QSGRendererInterface.GraphicsApi.OpenGL

    @pyqtProperty(bool, notify=readyChanged)
    def ready(self):
        return bool(self._data)

    @pyqtProperty("QVariantMap", notify=settingsChanged)
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, value):
        value = dict(value or {})
        if value != self._settings:
            self._settings = value
            self.settingsChanged.emit()
            self.update()

    @staticmethod
    def _node(parent, colour, triangles=False):
        node = QSGGeometryNode()
        node.setFlag(QSGNode.Flag.OwnsGeometry, True)
        node.setFlag(QSGNode.Flag.OwnsMaterial, True)
        geometry = QSGGeometry(QSGGeometry.defaultAttributes_Point2D(), 0)
        geometry.setDrawingMode(
            QSGGeometry.DrawingMode.DrawTriangles if triangles else QSGGeometry.DrawingMode.DrawLines
        )
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
            node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry | QSGNode.DirtyStateBit.DirtyMaterial)
        if geometry.lineWidth() != width:
            geometry.setLineWidth(width)
            node.markDirty(QSGNode.DirtyStateBit.DirtyGeometry)

    def updatePaintNode(self, old, update_data):
        if old is not None and getattr(old, "_data", None) is not self._data:
            sip.delete(old)
            old = None
        node = old if old is not None else QSGTransformNode()
        settings = self._settings
        plot = settings.get("plot") or {}
        bed = plot.get("bed") or {}
        scale = settings.get("scale", 1)
        sx, sy = plot.get("sx", 1) * scale, plot.get("sy", 1) * scale
        key = (
            tuple(bed.get(k, 0) for k in ("bedXMin", "bedXMax", "bedYMin", "bedYMax")),
            bool(settings.get("compact")),
            QColor(settings.get("gridThin", "#ffffff")).rgba(),
            QColor(settings.get("gridMajor", "#ffffff")).rgba(),
            sx,
            sy,
        )
        if getattr(node, "_data", None) is not self._data:
            while node.firstChild():
                sip.delete(node.firstChild())
            node._data = self._data
            node._groups = []
            node._grid_nodes = []
            node._grid_key = None
            for role, name, motions, data in self._data:
                pair = []
                for pending in (True, False):
                    colour = QColor("#888888" if pending and role == "current" else COLOURS.get(name, "#888888"))
                    if pending:
                        colour.setAlphaF(0.55 if role == "current" else 0.3)
                    child = QSGGeometryNode()
                    geometry = QSGGeometry(ATTR, 0)
                    geometry.setDrawingMode(QSGGeometry.DrawingMode.DrawTriangles)
                    child.setGeometry(geometry)
                    child.setFlag(QSGNode.Flag.OwnsGeometry, True)
                    material = FollowerStrokeMaterial(colour, rounded=role != "next")
                    child.setMaterial(material)
                    child.setFlag(QSGNode.Flag.OwnsMaterial, True)
                    child._material = material
                    node.appendChildNode(child)
                    pair.append(child)
                node._groups.append((role, name, motions, data, *pair))
        if node._grid_key != key:
            for child in node._grid_nodes:
                sip.delete(child)
            node._grid_nodes = self._grid(node, key)
            node._grid_key = key
        matrix = QMatrix4x4()
        matrix.translate(
            bed.get("offsetX", 0) * scale + settings.get("panX", 0) - bed.get("bedXMin", 0) * sx,
            bed.get("offsetY", 0) * scale + settings.get("panY", 0) + bed.get("bedYMax", 0) * sy,
        )
        matrix.scale(sx, -sy)
        node.setMatrix(matrix)
        split = settings.get("split")
        split = float("inf") if split is None else split
        for role, name, motions, data, base, printed in node._groups:
            enabled = role == "current" or bool(settings.get("showPrevious" if role == "prev" else "showNext"))
            if name == "TRAVEL":
                enabled = enabled and bool(settings.get("showTravels"))
            count = len(motions) * 6
            counts = (
                count if enabled and name != "TRAVEL" and (role != "current" or settings.get("showBase")) else 0,
                bisect.bisect_left(motions, split) * 6 if enabled and role == "current" else 0,
            )
            for child, count in zip((base, printed), counts, strict=True):
                geometry = child.geometry()
                if geometry.vertexCount() != count:
                    geometry.allocate(count)
                    if count:
                        ctypes.memmove(int(geometry.vertexData()), data, count * 24)
                    geometry.markVertexDataDirty()
                    child.markDirty(QSGNode.DirtyStateBit.DirtyGeometry | QSGNode.DirtyStateBit.DirtyMaterial)
                material = child._material
                material.viewport = (self.window().width(), self.window().height())
                width = settings.get("lineWidth", 1) * (settings.get("travelRatio", 0.7) if name == "TRAVEL" else 1)
                aa = bool(settings.get("antialiasing"))
                if material.width != width or material.aa != aa:
                    material.width = width
                    material.aa = aa
                    child.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
        return node

    def _grid(self, parent, key):
        """Bed-space graduations share the toolpaths' retained transform."""
        (xmin, xmax, ymin, ymax), compact, thin, major, sx, sy = key
        if xmax <= xmin or ymax <= ymin:
            return []
        fine, coarse = array("f"), array("f")
        for step, packed in ((10, fine), (50, coarse)):
            if step == 10 and compact:
                continue
            for x in range(math.ceil(xmin / step) * step, math.floor(xmax / step) * step + 1, step):
                if step == 50 or x % 50:
                    packed.extend((x, ymin, x, ymax))
            for y in range(math.ceil(ymin / step) * step, math.floor(ymax / step) * step + 1, step):
                if step == 50 or y % 50:
                    packed.extend((xmin, y, xmax, y))
        coarse.extend((xmin, ymin, xmax, ymin, xmax, ymin, xmax, ymax, xmax, ymax, xmin, ymax, xmin, ymax, xmin, ymin))
        children = []
        for packed, colour, width in ((fine, thin, 1), (coarse, major, 1 if compact else 2)):
            expanded = stroke_geometry(
                (("", "", (), packed.tobytes()),), width, max(0.001, abs(sx)), max(0.001, abs(sy)), rounded=False
            )[0][3]
            child = self._node(parent, QColor.fromRgba(colour), True)
            parent.removeChildNode(child)
            parent.prependChildNode(child)
            self._vertices(child, expanded, len(expanded) // 8, 1)
            children.append(child)
        return children
