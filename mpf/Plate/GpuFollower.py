"""Retained scene-graph follower with bounded, cancellable CPU preparation."""

from array import array
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import weakref
import bisect
import ctypes
import math
import struct
import threading
import time

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot, qWarning
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

from ..GCode.TravelStates import is_travel
from .PreviewColours import DEFAULT_CLASSES
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
COLOURS.update({name: colour for name, colour in DEFAULT_CLASSES.items() if is_travel(name)})
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
        if role == "current":
            for event_key, name in (("retractions", "RETRACTION"), ("unretractions", "UNRETRACTION")):
                marks = payload.get(event_key) or ()
                if marks:
                    packed = array("f")
                    for x, y, _motion in marks:
                        packed.extend((x, y, x, y))
                    prepared.append((role, name, tuple(point[2] for point in marks), packed.tobytes()))
        groups = dict(payload.get("classes") or {})
        if "travelClasses" in payload:
            groups.update(payload["travelClasses"])
        else:
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
                profile = payload.get("widths") or ()
                widths = array("f", (max(0.001, profile[int(edge[0])]) if 0 <= int(edge[0]) < len(profile) and profile[int(edge[0])] > 0 else 0.4 for edge in edges))
                speeds, tools = payload.get("speeds") or (), payload.get("Tools") or ()
                metrics = array("f")
                for edge in edges:
                    motion = int(edge[0])
                    metrics.extend((speeds[motion] if motion < len(speeds) else 0., tools[motion] if motion < len(tools) else 0))
                prepared.append((role, name, tuple(edge[0] for edge in edges), packed.tobytes(), widths.tobytes(), metrics.tobytes()))
    result = tuple(prepared)
    # Cache keys hold source payloads to prevent id reuse. Account for their
    # retained Python points as well as packed vertices / motion integers.
    # A long next-layer edge expands into many dashes, but retains its source
    # points only once. Charging a source point per dash refused dense windows
    # even when their actual storage comfortably fitted the cache.
    source_points = sum(len(segment) for _role, payload in payloads if payload
                        for segments in (tuple((payload.get("classes") or {}).values())
                                         + (payload.get("travels") or (),) + tuple((payload.get("travelClasses") or {}).values()))
                        for segment in segments)
    source_points += sum(len(payload.get(key) or ()) for _role, payload in payloads if payload
                         for key in ("retractions", "unretractions"))
    profile_bytes = sum(sum(len(payload.get(key) or ()) * cost for key,cost in (("widths",32),("speeds",32),("Tools",28))) for _role, payload in payloads if payload)
    charge = profile_bytes + sum(len(row[3]) + (sum(len(column) for column in row[4:])) + len(row[2]) * 36 for row in result) + source_points * 192
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
    for row in data:
        role, name, motions, packed = row[:4]
        stroke_width = width * travel_ratio if is_travel(name) else width
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


def marker_counts(data, progress, total=0):
    """Completed event prefixes; partial moves must not reveal their events."""
    completed = max(0, math.floor(progress)) if math.isfinite(progress) else (0 if math.isnan(progress) else float("inf"))
    # Firmware retracts can sit after the last indexed motion. They appear
    # at full playback, without requiring a nonexistent extra motion.
    search = bisect.bisect_right if total > 0 and completed >= total else bisect.bisect_left
    return tuple((name, search(motions, completed)) for role, name, motions, _raw in data
                 if role == "current" and name in ("RETRACTION", "UNRETRACTION"))


def marker_geometry(data, sx, sy, scale, compact, retractions, unretractions, limits=None):
    """Small screen-size hollow arrows, at most one of each kind per 12px cell.

    World-aligned cells keep the selection stable during pan; zoom reveals
    individual events. Glyph geometry is independent of toolpath line width.
    """
    if not (retractions or unretractions) or abs(sx) < 1e-9 or abs(sy) < 1e-9:
        return b""
    half = (3 if compact else min(8, max(4, 4 * math.sqrt(max(1, scale))))) / 2
    outline = ((0, -half), (half, 0), (half * .35, 0), (half * .35, half),
               (-half * .35, half), (-half * .35, 0), (-half, 0), (0, -half))
    vertices = array("f")
    cells = set()
    limits = dict(limits) if limits is not None else None
    for role, name, _motions, raw in data:
        enabled = retractions if name == "RETRACTION" else unretractions if name == "UNRETRACTION" else False
        if role != "current" or not enabled:
            continue
        points = array("f")
        points.frombytes(raw)
        direction = 1 if name == "RETRACTION" else -1
        count = limits.get(name, 0) if limits is not None else len(points) // 4
        for i in range(0, min(len(points), count * 4), 4):
            x, y = points[i], points[i + 1]
            cell = (name, math.floor(x * abs(sx) / 12), math.floor(y * abs(sy) / 12))
            if cell in cells:
                continue
            cells.add(cell)
            for a, b in zip(outline, outline[1:], strict=False):
                vertices.extend((x + a[0] / sx, y - direction * a[1] / sy,
                                 x + b[0] / sx, y - direction * b[1] / sy))
    return vertices.tobytes()


class GpuFollower(QQuickItem):
    dataSourceChanged = pyqtSignal()
    layersChanged = pyqtSignal()
    settingsChanged = pyqtSignal()
    readyChanged = pyqtSignal()
    prepared = pyqtSignal(int, object)
    preparationFailed = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QQuickItem.Flag.ItemHasContents, True)
        self._work = _WorkState()
        self.destroyed.connect(self._work.close)
        app = QGuiApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._work.close)
        self._source = None
        self._layers = {}
        self._settings = {}
        self._generation = 0
        self._preparing_generation = None
        self._data = ()
        self._error = ""
        self._render_generation = -1
        self.prepared.connect(self._prepared)
        self.preparationFailed.connect(self._preparation_failed)

    @pyqtProperty(QObject, notify=dataSourceChanged)
    def dataSource(self):
        return self._source

    @dataSource.setter
    def dataSource(self, source):
        if source is self._source:
            return
        if source is not None and (not isinstance(source, GpuFollower) or source is self or source._source is not None):
            raise ValueError("GPU data source must be an independent follower")
        if self._source is not None:
            self._source.readyChanged.disconnect(self._sync_source)
            self._source.layersChanged.disconnect(self._sync_source)
            self._source.destroyed.disconnect(self._source_destroyed)
        self._source = source
        self._work.close()
        if source is not None:
            source.readyChanged.connect(self._sync_source)
            source.layersChanged.connect(self._sync_source)
            source.destroyed.connect(self._source_destroyed)
        self._sync_source()
        self.dataSourceChanged.emit()

    def _source_destroyed(self):
        self._source = None
        self._sync_source()
        self.dataSourceChanged.emit()

    def _sync_source(self):
        source = self._source
        self._data = source._data if source is not None else ()
        self._layers = source._layers if source is not None else {}
        self._generation = source._generation if source is not None else 0
        self._preparing_generation = source._preparing_generation if source is not None else None
        self._error = source._error if source is not None else ""
        self.readyChanged.emit()
        self.update()

    @pyqtSlot(float, result="QVariantMap")
    def pointAtMotion(self, progress):
        """Read the same retained edge / arc subdivision the shader clips.

        Binary searches over immutable worker-built buffers avoid walking or
        converting layer geometry on animation frames. Missing geometry must
        use the reported position, never a chord across unobserved moves.
        """
        if not math.isfinite(progress) or progress < 0:
            return {"valid": False}
        motion = math.floor(progress)
        endpoint = None
        for role, _name, motions, packed in self._data:
            if role != "current" or _name in ("RETRACTION", "UNRETRACTION"):
                continue
            lo, hi = bisect.bisect_left(motions, motion), bisect.bisect_right(motions, motion)
            if lo < hi:
                # Arc subedges share a motion ID, with cumulative path fractions.
                while lo + 1 < hi:
                    middle = (lo + hi) // 2
                    start = struct.unpack_from("<f", packed, middle * 240 + 24)[0]
                    if start <= progress:
                        lo = middle
                    else:
                        hi = middle
                offset = lo * 240
                x, y, dx, dy = struct.unpack_from("<4f", packed, offset)
                start, span = struct.unpack_from("<2f", packed, offset + 24)
                amount = min(1.0, max(0.0, (progress - start) / max(span, 1e-12)))
                return {"valid": True, "x": x + dx * amount, "y": y + dy * amount}
            if progress == motion:
                previous = bisect.bisect_right(motions, motion - 1) - 1
                if previous >= 0 and motions[previous] == motion - 1:
                    x, y, dx, dy = struct.unpack_from("<4f", packed, previous * 240)
                    endpoint = {"valid": True, "x": x + dx, "y": y + dy}
        return endpoint or {"valid": False}

    @pyqtProperty("QVariantMap", notify=layersChanged)
    def layers(self):
        return self._layers

    @layers.setter
    def layers(self, value):
        value = dict(value or {})
        if value == self._layers:
            return
        if self._source is not None:
            return
        previous = self._layers
        self._layers = value
        self._error = ""
        self._generation += 1
        generation = self._generation
        self._preparing_generation = generation
        # Keep only unchanged channels while an arriving ghost prepares.
        # A new current layer retires its CPU data immediately. The native
        # tree may hold its frozen frame until this generation is prepared.
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
            error = ""
            try:
                data = pack_shader(prepare(payloads, cancel), cancel)
            except Exception as exc:
                data = ()
                error = f"{type(exc).__name__}: {exc}"
            item = owner()
            if cancel.is_set() or item is None:
                return
            try:
                if error:
                    item.preparationFailed.emit(generation, error)
                else:
                    item.prepared.emit(generation, data)
            except RuntimeError:
                pass  # The face was destroyed while its worker completed.

        if payloads:
            self._work.futures.append(_POOL.submit(build))
        else:
            self._preparing_generation = None
        self.layersChanged.emit()

    def _prepared(self, generation, data):
        if generation == self._generation:
            self._error = ""
            self._preparing_generation = None
            self._data = data
            self.readyChanged.emit()
            self.update()

    def _preparation_failed(self, generation, error):
        if generation != self._generation:
            return
        self._preparing_generation = None
        self._data = ()
        self._error = "Unable to render this layer. Change layers to retry."
        qWarning("MoonrakerPrintFollower GPU preparation failed: " + error)
        self.readyChanged.emit()
        self.update()

    @pyqtProperty(str, notify=readyChanged)
    def error(self):
        return self._error

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
        if old is not None and not self._data \
                and self._preparing_generation == self._generation \
                and self._layers.get("current") is not None:
            # Hold the previous native frame without repainting its prefix
            # with the NEW layer's split. Replace it atomically when ready;
            # clears, empty completions and stale workers cannot latch it.
            return old
        if old is not None and getattr(old, "_data", None) is not self._data:
            sip.delete(old)
            old = None
        node = old if old is not None else QSGTransformNode()
        settings = self._settings
        isolated = settings.get("isolatedRole")
        grid_only = bool(settings.get("gridOnly"))
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
            bool(settings.get("showGrid", True)) and not bool(isolated),
        )
        if getattr(node, "_data", None) is not self._data:
            while node.firstChild():
                sip.delete(node.firstChild())
            node._data = self._data
            node._groups = []
            node._grid_nodes = []
            node._grid_key = None
            node._marker_nodes = []
            node._marker_key = None
            for role, name, motions, data in self._data:
                if name in ("RETRACTION", "UNRETRACTION"):
                    continue
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
            node._grid_nodes = self._grid(node, key) if settings.get("showGrid", True) and not isolated else []
            node._grid_key = key
        split = settings.get("split")
        split = float("inf") if split is None else split
        display_split = settings.get("displaySplit", split)
        display_split = split if display_split is None else display_split
        marker_split = settings.get("markerSplit", display_split)
        limits = marker_counts(self._data, float("inf") if marker_split is None else marker_split,
                               settings.get("motionCount", 0))
        marker_key = (sx, sy, scale, bool(settings.get("compact")),
                      bool(settings.get("showRetractions")), bool(settings.get("showUnretractions")),
                      limits,
                      QColor(settings.get("markerInk", "#aaaaaa")).rgba())
        if node._marker_key != marker_key:
            for child in node._marker_nodes:
                sip.delete(child)
            edges = marker_geometry(self._data, *marker_key[:7]) if not isolated and not grid_only else b""
            node._marker_nodes = []
            if edges:
                child = self._node(node, QColor.fromRgba(marker_key[-1]))
                self._vertices(child, edges, len(edges) // 8, 1)
                node._marker_nodes.append(child)
            node._marker_key = marker_key
        matrix = QMatrix4x4()
        matrix.translate(
            bed.get("offsetX", 0) * scale + settings.get("panX", 0) - bed.get("bedXMin", 0) * sx,
            bed.get("offsetY", 0) * scale + settings.get("panY", 0) + bed.get("bedYMax", 0) * sy,
        )
        matrix.scale(sx, -sy)
        node.setMatrix(matrix)
        scheme = settings.get("colourScheme") or {}
        mode = int(scheme.get("mode", 1))
        classes = scheme.get("classes") or COLOURS
        colours = scheme.get("materials") or ["#888888"]
        palette = tuple(QColor(colours[i] if i < len(colours) else colours[0]) for i in range(16))
        for role, name, motions, data, base, printed in node._groups:
            enabled = role == "current" or bool(settings.get("showPrevious" if role == "prev" else "showNext"))
            if is_travel(name):
                enabled = enabled and bool(settings.get("showTravels"))
            count = len(motions) * 6
            if settings.get("isolateTranslucent"):
                enabled = enabled and role == "current"
            if grid_only:
                counts = (0, 0)
            elif isolated:
                draw = role == ("current" if isolated == "ghost" else isolated) and not is_travel(name)
                counts = (count if draw else 0, 0)
            else:
                counts = (
                    count if enabled and not is_travel(name) and (role != "current" or settings.get("showBase") and not settings.get("isolateTranslucent")) else 0,
                    bisect.bisect_left(motions, split) * 6 if enabled and role == "current" else 0,
                )
            for child, count in zip((base, printed), counts, strict=True):
                geometry = child.geometry()
                if geometry.vertexCount() != count:
                    geometry.allocate(count)
                    if count:
                        ctypes.memmove(int(geometry.vertexData()), data, count * 40)
                    geometry.markVertexDataDirty()
                    child.markDirty(QSGNode.DirtyStateBit.DirtyGeometry | QSGNode.DirtyStateBit.DirtyMaterial)
                material = child._material
                grey = child is base and role == "current"
                ink = QColor("#888888" if grey else classes.get(name, "#888888"))
                if mode != 1 and not grey and not is_travel(name):
                    ink.setAlphaF(1.)
                if child is base:
                    ink.setAlphaF(1.0 if isolated else (.55 if role == "current" else .3))
                metadata = (settings.get("layerInfo") or {}).get(role) or {}
                ranges = settings.get("colourRanges") or {}
                bounds = ranges.get({2:"speed", 3:"height", 4:"width", 5:"flow"}.get(mode), (0., 0.))
                options = (float(-1 if grey or is_travel(name) else mode), float(bounds[0]), float(bounds[1]), float(metadata.get("height", .2)))
                if material.colour != ink or material.colour_options != options or material.palette != palette:
                    material.colour, material.colour_options, material.palette = ink, options, palette
                    child.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
                clip = child is printed and role == "current" and math.isfinite(display_split)
                if material.clip != clip or clip and material.split != display_split:
                    material.clip = clip
                    material.split = display_split if clip else 0.0
                    child.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
                material.viewport = (self.width(), self.height()) if isolated else (self.window().width(), self.window().height())
                width = settings.get("lineWidth", 1) * (settings.get("travelRatio", 0.7) if is_travel(name) else 1)
                if is_travel(name) and settings.get("trueThickness"):
                    width = 1.0
                physical = bool(settings.get("trueThickness")) and not is_travel(name)
                if physical:
                    width = abs(sx)
                aa = bool(settings.get("antialiasing"))
                if material.width != width or material.aa != aa or material.physical != physical:
                    material.physical = physical
                    material.width = width
                    material.aa = aa
                    child.markDirty(QSGNode.DirtyStateBit.DirtyMaterial)
        return node

    def _grid(self, parent, key):
        """Bed-space graduations share the toolpaths' retained transform."""
        (xmin, xmax, ymin, ymax), compact, thin, major, sx, sy = key[:6]
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
