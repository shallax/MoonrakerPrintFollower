"""The native render path (the review's round 3): dense toolpath
geometry rasterises below the QML JavaScript layer.

The measured verdict: QML's per-vertex walk costs ~810 ms on a
500k-motion layer; QPainterPath per segment costs ~92 ms ON A WORKER.
QML's role shrinks to composition — blitting the finished QImages —
while the scrub's within-layer delta keeps the vector payload (the
only consumer that genuinely needs per-motion granularity), published
separately from the raster window.

The raster's key inputs (the view, the plot) ride the model's slots;
the pan stays a scene-graph translation (the standing architecture).
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, Qt, pyqtProperty, pyqtSignal
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


# The null stand-in for an unrendered layer's raster property: a
# QImage-typed property must never return None (the live crash — the
# TypeError crashed Cura through its handler).
_NULL_IMAGE = QImage()


class _RasterJob(QRunnable):
    """A one-shot raster build on Qt's shared pool."""

    def __init__(self, work) -> None:
        super().__init__()
        self._work = work

    def run(self) -> None:
        self._work()


class PlateLayer(QObject):
    """One prepared layer as a rendered image plus its motion count.

    The raster lands asynchronously (a worker paints it) and the
    `rasterReady` signal tells the model to republish; until then the
    face draws nothing for the slot, and the seek's vector fallback
    serves the current layer.
    """

    rasterReady = pyqtSignal()

    def __init__(self, payload: dict, parent: QObject = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._raster = None

    @pyqtProperty(int, constant=True)
    def motions(self) -> int:
        return int(self._payload.get("motions") or 0)

    @pyqtProperty(QImage, notify=rasterReady)
    def raster(self) -> QImage:
        # A QImage-typed property must never return None — the null
        # image stands in until the worker lands (width 0, which the
        # face's _rasterOf gate already reads as "not ready").
        return self._raster if self._raster is not None else _NULL_IMAGE

    def set_raster(self, image: QImage) -> None:
        self._raster = image
        self.rasterReady.emit()


def render_layer_raster(payload: dict, plot: dict, view: dict) -> QImage:
    """Paint the layer's classes into a canvas-sized image — the same
    transform the face's vector painter used, so a blit lands the
    identical picture. QPainterPath per segment (the measured winner:
    92 ms vs drawLines' 209 ms and the per-edge loop's 307 ms on a
    500k-motion layer). Pan-free: the face's translation carries the
    pan."""
    width = int(view["width"])
    height = int(view["height"])
    scale = float(view.get("scale", 1.0))
    line_scale = float(view.get("lineScale", 0.7))
    compact = bool(view.get("compact", False))
    sx = float(plot["sx"]) * scale
    sy = float(plot["sy"]) * scale
    offset_x = float(plot["offsetX"])
    offset_y = float(plot["offsetY"])
    bed_x_min = float(plot["bedXMin"])
    bed_y_max = float(plot["bedYMax"])
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    stroke = max(0.01, float(view.get("nominalWidthMm", 0.2)) * sx * line_scale
                 * (7.0 if compact else 1.0))
    pen = QPen()
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    for name, segments in (payload.get("classes") or {}).items():
        pen.setColor(QColor(_PLATE_CLASS_COLOURS.get(name, "#888888")))
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
    painter.end()
    return image
