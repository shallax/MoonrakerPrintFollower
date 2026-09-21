"""The native render path: dense toolpath
geometry rasterises below the QML JavaScript layer.

The measured verdict: QML's per-vertex walk costs ~810 ms on a
500k-motion layer; QPainterPath per segment costs ~92 ms ON A WORKER.
QML's role shrinks to composition — blitting the finished QImages —
while the scrub's within-layer delta keeps the vector payload (the
only consumer that genuinely needs per-motion granularity), published
separately from the raster window.

One render job paints THREE sibling assets: the feature-coloured raster, the grey whole-layer base
(derived from the coloured one via SourceIn — no second geometry
walk) and the travel-only lines. The face composes them: a full
100% layer blits colour + travels, a partial layer blits the grey
base under the vector-walked prefix.

The raster's key inputs (the view, the plot) ride the model's slots;
the pan stays baked into the render (the standing architecture).
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QObject, QRunnable, Qt, QUrl, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

# The plate class colours — the SAME values the QML theme's
# MoonrakerTheme.plateClass* tokens carry (the colours-are-tokens
# rule; the theme file is the single source, mirrored here for the
# native renderer).
_PLATE_CLASS_COLOURS = {
    "WALL-OUTER": "#d32f2f",
    "WALL-INNER": "#388e3c",
    "SKIN": "#e65100",
    "FILL": "#1976d2",
    "SUPPORT": "#00838f",
    "SKIRT": "#00897b",
}
# The theme's seriesDefault (the pending base's grey) and plateTravel.
_PLATE_BASE_COLOUR = "#888888"
_PLATE_TRAVEL_COLOUR = "#b085e8"


# The null stand-in for an unrendered layer's raster property: a
# QImage-typed property must never return None (the live crash — the
# TypeError crashed Cura through its handler).
_NULL_IMAGE = QImage()


def png_file(image: QImage, directory: str, name: str) -> str:
    """The raster transport (engine-proven): a QImage variant
    segfaults the Qt6 Canvas and its QML reads see nothing inside
    it, and a data: URL loads but never renders in a QQuickImage —
    a file:// PNG is the one source the scene-graph Image draws.
    Written on the WORKER, never the owner thread; the name carries
    the generation, so a re-render never clobbers a file an Image
    is still reading."""
    if image is None or image.width() <= 0:
        return ""
    path = os.path.join(directory, f"{name}.png")
    try:
        os.makedirs(directory, exist_ok=True)
        image.save(path, "PNG")
    except OSError:
        return ""
    return QUrl.fromLocalFile(path).toString()


class _RasterJob(QRunnable):
    """A one-shot raster build on Qt's shared pool."""

    def __init__(self, work) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        self._work()


class RasterBridge(QObject):
    """The worker -> owner handoff: the
    worker emits its START and the COMPLETED images through this
    bridge, and the queued cross-thread delivery runs the commit on
    the model's owning thread — workers never touch the model's
    QObjects. The start lets the scheduler count a job superseded
    before it ever ran ."""

    started = pyqtSignal(object)
    done = pyqtSignal(object, object)

    def __init__(self, parent: QObject = None) -> None:
        super().__init__(parent)


class PlateLayer(QObject):
    """One prepared layer's rendered images plus its motion count.

    The rasters land asynchronously (a worker paints them) and the
    `rasterReady` signal tells the model to republish; until then the
    face draws nothing for the slot, and the seek's vector fallback
    serves the current layer. `rasterValid` is the render key's
    verdict: the image shows only while
    its key matches the surface's CURRENT key — an old-view raster
    stays cached (visually useful to revisit) but never reads as
    current after a zoom/pan/resize.
    """

    rasterReady = pyqtSignal()

    def __init__(self, payload: dict, parent: QObject = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._raster = None
        self._base = None
        self._travels = None
        self._raster_data = ""
        self._base_data = ""
        self._travel_data = ""
        self._render_key = None
        self._expected_key = None

    @pyqtProperty(int, constant=True)
    def motions(self) -> int:
        return int(self._payload.get("motions") or 0)

    @pyqtProperty(QImage, notify=rasterReady)
    def raster(self) -> QImage:
        # A QImage-typed property must never return None — the null
        # image stands in until the worker lands (width 0, which the
        # face's _rasterOf gate already reads as "not ready").
        return self._raster if self._raster is not None else _NULL_IMAGE

    # The QML engine cannot see INSIDE QImage values (a QImage
    # variant's .width reads undefined — the live 0% probe proved
    # it), so the readiness gates read these INT properties: the
    # pixel extents ride as plain numbers while the image itself
    # crosses only into ctx.drawImage.
    @pyqtProperty(int, notify=rasterReady)
    def rasterWidth(self) -> int:
        return self._raster.width() if self._raster is not None else 0

    @pyqtProperty(int, notify=rasterReady)
    def rasterHeight(self) -> int:
        return self._raster.height() if self._raster is not None else 0

    @pyqtProperty(int, notify=rasterReady)
    def baseWidth(self) -> int:
        return self._base.width() if self._base is not None else 0

    @pyqtProperty(int, notify=rasterReady)
    def travelWidth(self) -> int:
        return self._travels.width() if self._travels is not None else 0

    @pyqtProperty(QImage, notify=rasterReady)
    def baseRaster(self) -> QImage:
        """The grey whole-layer base ."""
        return self._base if self._base is not None else _NULL_IMAGE

    @pyqtProperty(QImage, notify=rasterReady)
    def travelRaster(self) -> QImage:
        """The travel-only lines ."""
        return self._travels if self._travels is not None else _NULL_IMAGE

    # The canvas-facing transport (engine-proven): base64 PNG data
    # URLs — what ctx.drawImage actually accepts on Qt6.
    @pyqtProperty(str, notify=rasterReady)
    def rasterData(self) -> str:
        return self._raster_data

    @pyqtProperty(str, notify=rasterReady)
    def baseData(self) -> str:
        return self._base_data

    @pyqtProperty(str, notify=rasterReady)
    def travelData(self) -> str:
        return self._travel_data

    @pyqtProperty(bool, notify=rasterReady)
    def rasterValid(self) -> bool:
        return self._raster is not None and self._render_key is not None \
            and self._render_key == self._expected_key

    def set_expected_key(self, key) -> None:
        """The surface's current key: the raster reads valid only
        while its own key equals this one."""
        if self._expected_key != key:
            self._expected_key = key
            self.rasterReady.emit()

    def set_raster(self, image: QImage, key, data: str = None) -> None:
        self._raster = image
        self._render_key = key
        self._raster_data = data if data is not None else ""
        self.rasterReady.emit()

    def set_base(self, image: QImage, data: str = None) -> None:
        # The notify rides EVERY setter: a binding on baseWidth must
        # re-evaluate when the sibling lands, or the face's key
        # caches the pre-arrival null (the live 0% leak).
        self._base = image
        self._base_data = data if data is not None else ""
        self.rasterReady.emit()

    def set_travels(self, image: QImage, data: str = None) -> None:
        self._travels = image
        self._travel_data = data if data is not None else ""
        self.rasterReady.emit()


def _transform(plot: dict, view: dict):
    """The shared mapping (the face's painters' own): sx/sy scaled,
    the offsets with the baked pan, the bed bounds."""
    scale = float(view.get("scale", 1.0))
    sx = float(plot["sx"]) * scale
    sy = float(plot["sy"]) * scale
    offset_x = float(plot["offsetX"]) + float(view.get("panX", 0.0))
    offset_y = float(plot["offsetY"]) + float(view.get("panY", 0.0))
    return (sx, sy, offset_x, offset_y,
            float(plot["bedXMin"]), float(plot["bedYMax"]))


def _paint_segments(painter: QPainter, payload: dict, plot: dict, view: dict,
                    class_names=None, colour=None) -> None:
    """QPainterPath per segment (the measured winner: 92 ms vs
    drawLines' 209 ms and the per-edge loop's 307 ms on a 500k-motion
    layer). `class_names` selects which classes to paint."""
    sx, sy, offset_x, offset_y, bed_x_min, bed_y_max = _transform(plot, view)
    pen = QPen()
    for name, segments in (payload.get("classes") or {}).items():
        if class_names is not None and name not in class_names:
            continue
        pen.setColor(QColor(colour if colour else _PLATE_CLASS_COLOURS.get(name, "#888888")))
        painter.setPen(pen)
        for points in segments:
            if len(points) < 2:
                continue
            path = QPainterPath()
            path.moveTo(offset_x + (points[0][0] - bed_x_min) * sx,
                        offset_y + (bed_y_max - points[0][1]) * sy)
            for i in range(1, len(points)):
                path.lineTo(offset_x + (points[i][0] - bed_x_min) * sx,
                            offset_y + (bed_y_max - points[i][1]) * sy)
            painter.drawPath(path)


def _new_canvas(view: dict) -> QImage:
    image = QImage(int(view["width"]), int(view["height"]),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    return image


def _derive_grey(coloured: QImage) -> QImage:
    """The grey whole-layer base, derived from the coloured raster —
    no second geometry walk ."""
    grey = QImage(coloured.size(), QImage.Format.Format_ARGB32_Premultiplied)
    grey.fill(QColor(0, 0, 0, 0))
    painter = QPainter(grey)
    painter.drawImage(0, 0, coloured)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(0, 0, grey.width(), grey.height(), QColor(_PLATE_BASE_COLOUR))
    painter.end()
    return grey


def render_layer_raster(payload: dict, plot: dict, view: dict) -> tuple:
    """Paint the layer's three sibling assets — (coloured, grey base,
    travels) — the same transform the face's vector painter used, so
    a blit lands the identical picture. Pan-baked: the pan rides the
    offset, never a scene-graph translation."""
    line_scale = float(view.get("lineScale", 0.7))
    compact = bool(view.get("compact", False))
    sx, _sy, _ox, _oy, _bx, _by = _transform(plot, view)
    stroke = max(0.01, float(view.get("nominalWidthMm", 0.2)) * sx * line_scale
                 * (7.0 if compact else 1.0))
    coloured = _new_canvas(view)
    painter = QPainter(coloured)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    pen = QPen()
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    _paint_segments(painter, payload, plot, view)
    painter.end()
    grey = _derive_grey(coloured)
    travels = _NULL_IMAGE
    if payload.get("travels"):
        travels = _new_canvas(view)
        tpainter = QPainter(travels)
        tpainter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        tpen = QPen()
        tpen.setWidthF(max(0.01, stroke * 0.6))
        tpen.setCapStyle(Qt.PenCapStyle.RoundCap)
        tpen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        tpen.setColor(QColor(_PLATE_TRAVEL_COLOUR))
        tpainter.setPen(tpen)
        tx_sx, tx_sy, tx_ox, tx_oy, tx_bx, tx_by = _transform(plot, view)
        for points in payload.get("travels") or []:
            if len(points) < 2:
                continue
            path = QPainterPath()
            path.moveTo(tx_ox + (points[0][0] - tx_bx) * tx_sx,
                        tx_oy + (tx_by - points[0][1]) * tx_sy)
            for i in range(1, len(points)):
                path.lineTo(tx_ox + (points[i][0] - tx_bx) * tx_sx,
                            tx_oy + (tx_by - points[i][1]) * tx_sy)
            tpainter.drawPath(path)
        tpainter.end()
    return coloured, grey, travels
