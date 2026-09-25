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

import math
import os
from contextlib import contextmanager

from PyQt6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QUrl, pyqtProperty, pyqtSignal
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
# The bed grid's colours (the face's theme tokens' values — the
# workers cannot read the QML theme): the 10 mm thin graduations
# and the 50 mm / border strokes.
_PLATE_GRID_THIN = "#cccccc"
_PLATE_GRID_BORDER = "#999999"
# One render-contract value for both the native travel raster and the
# QML Canvas path (the model publishes this value to the face).
_PLATE_TRAVEL_VISUAL_RATIO = 0.7


# The null stand-in for an unrendered layer's raster property: a
# QImage-typed property must never return None (the live crash — the
# TypeError crashed Cura through its handler).
_NULL_IMAGE = QImage()


def png_file(image: QImage, directory: str, name: str) -> str:
    """The raster transport (engine-proven): a QImage variant
    segfaults the Qt6 Canvas and its QML reads see nothing inside
    it, and a data: URL loads but never renders in a QQuickImage —
    a file:// PNG is the one source the scene-graph Image draws.
    Written on the WORKER, never the owner thread. ATOMIC: the PNG
    encodes to a temporary sibling and only renames into the final
    immutable name once the save returned True — a URL is never
    published for a failed write, and a reader never sees a
    half-written file."""
    if image is None or image.width() <= 0:
        return ""
    path = os.path.join(directory, f"{name}.png")
    temp = f"{path}.tmp-{os.getpid()}"
    try:
        os.makedirs(directory, exist_ok=True)
        if not image.save(temp, "PNG"):
            try:
                os.unlink(temp)
            except OSError:
                pass
            return ""
        os.replace(temp, path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass
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


def _bridge_emit(bridge, signal, *args):
    """The worker -> owner handoff with a teardown guard: a late
    worker's emit after the bridge's C++ side died (the owner's
    deleteLater) would raise and abort the pool thread — the job is
    dropped instead; no owner remains to commit it. True when the
    emit landed."""
    try:
        getattr(bridge, signal).emit(*args)
        return True
    except RuntimeError:
        return False


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
        self._prefix = None
        self._raster_data = ""
        self._base_data = ""
        self._travel_data = ""
        self._prefix_data = ""
        self._prefix_split = -1
        self._render_key = None
        self._base_key = None
        self._travel_key = None
        self._prefix_key = None
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

    @pyqtProperty(QImage, notify=rasterReady)
    def prefixRaster(self) -> QImage:
        """The printed prefix, rendered up to `prefixSplit`."""
        return self._prefix if self._prefix is not None else _NULL_IMAGE

    @pyqtProperty(int, notify=rasterReady)
    def prefixWidth(self) -> int:
        return self._prefix.width() if self._prefix is not None else 0

    @pyqtProperty(int, notify=rasterReady)
    def prefixSplit(self) -> int:
        return self._prefix_split

    @pyqtProperty(str, notify=rasterReady)
    def prefixData(self) -> str:
        return self._prefix_data

    @pyqtProperty(bool, notify=rasterReady)
    def rasterValid(self) -> bool:
        # Transport-aware: the QML consumes the FILE URL, not the
        # QImage — validity needs the write to have succeeded and
        # the source to be non-empty, not just pixels in hand. The
        # bool() wrap matters: an and-chain short-circuiting on an
        # empty string would hand the bool-typed property a str,
        # which the engine's converter cannot digest.
        return bool(self._raster is not None and self._raster_data
                    and self._render_key is not None
                    and self._render_key == self._expected_key)

    @pyqtProperty(bool, notify=rasterReady)
    def baseValid(self) -> bool:
        return bool(self._base is not None and self._base_data
                    and self._base_key is not None
                    and self._base_key == self._expected_key)

    @pyqtProperty(bool, notify=rasterReady)
    def travelValid(self) -> bool:
        return bool(self._travels is not None and self._travel_data
                    and self._travel_key is not None
                    and self._travel_key == self._expected_key)

    @pyqtProperty(bool, notify=rasterReady)
    def prefixValid(self) -> bool:
        return bool(self._prefix is not None and self._prefix_data
                    and self._prefix_key is not None
                    and self._prefix_key == self._expected_key)

    def set_expected_key(self, key) -> None:
        """The surface's current key: every asset reads valid only
        while its own key equals this one — a view change
        invalidates the prefix, the base and the travels along
        with the coloured raster."""
        if self._expected_key != key:
            self._expected_key = key
            self.rasterReady.emit()

    def set_raster(self, image: QImage, key, data: str = None) -> None:
        self._raster = image
        self._render_key = key
        self._raster_data = data if data is not None else ""
        self.rasterReady.emit()

    def set_base(self, image: QImage, key, data: str = None) -> None:
        # The notify rides EVERY setter: a binding on baseWidth must
        # re-evaluate when the sibling lands, or the face's key
        # caches the pre-arrival null (the live 0% leak).
        self._base = image
        self._base_key = key
        self._base_data = data if data is not None else ""
        self.rasterReady.emit()

    def set_travels(self, image: QImage, key, data: str = None) -> None:
        self._travels = image
        self._travel_key = key
        self._travel_data = data if data is not None else ""
        self.rasterReady.emit()

    def set_prefix(self, image: QImage, data: str, split: int, key) -> None:
        self._prefix = image
        self._prefix_data = data
        self._prefix_split = split
        self._prefix_key = key
        self.rasterReady.emit()

    def memory_bytes(self) -> int:
        """The wrapper's own pixel bytes — the four sibling images.
        The payload's geometry is charged separately, through the
        service's decoded pins."""
        total = 0
        for image in (self._raster, self._base, self._travels, self._prefix):
            if image is not None and not image.isNull():
                total += image.sizeInBytes()
        return total


def _backing_scale(view: dict) -> float:
    """The device-pixel backing factor (bounded supersampling): a
    DPR-2 screen must never take a 1x logical toolpath raster and
    merely enlarge it — the worker paints at the device resolution
    and the scene-graph samples it down to the logical size. An
    explicit ``backing`` (the navigation raster's fixed 4x) rides
    its own policy."""
    if view.get("backing"):
        return float(view["backing"])
    return min(2.0, max(1.0, float(view.get("dpr", 1.0))))


# An incremental bake copies PIXELS, so a `previous` from any other
# transform blits the history at the old geometry — measured: a delta
# taken across a 40 px pan leaves a 158 px ghost of the old pane, and
# the live travels landing tens of pixels out (worst on the furthest
# move) is the same class. The docstrings
# demanded the caller prove the scene; the image now carries that
# proof itself, so a caller cannot get it wrong by forgetting: every
# bake stamps the image it returns with the context it painted, and a
# copy is taken only from an image stamped with the context being
# asked for. A `previous` from anywhere else — an older view, a
# hand-built image, a caller of an older build — carries no matching
# stamp and is refused, at the cost of one whole bake and no pixels.
_CONTEXT_STAMP = "mpf-render-context"

# Every input the painters read out of `view` and `plot` beyond the
# split (which is a parameter). The list is written against the
# painters' own reads; a key the signature misses is a hole, so a
# change to a painter's inputs belongs here in the same pass.
_CONTEXT_VIEW = ("width", "height", "scale", "panX", "panY", "backing",
                 "dpr", "zoom", "lineScale", "compact", "nominalWidthMm",
                 "travelVisualRatio", "bedWidth", "bedDepth")
_CONTEXT_FLAGS = (("showPrevious", True), ("showNext", True),
                  ("showBase", True), ("showTravels", False))
_CONTEXT_PLOT = ("offsetX", "offsetY", "sx", "sy", "bedXMin", "bedYMax")


def scene_context(plot: dict, view: dict) -> str:
    """The identity of one render context: what the painters read out
    of *plot* and *view*, normalised so an int/float respelling of
    the same number is one context. The same context and the same
    payload draw the same pixels for the same motions."""
    def number(value, default):
        try:
            return round(float(value), 6)
        except (TypeError, ValueError):
            return default
    return repr((
        tuple(number(view.get(key), 0.0) for key in _CONTEXT_VIEW),
        tuple(bool(view.get(key, fallback))
              for key, fallback in _CONTEXT_FLAGS),
        tuple(number(plot.get(key), 0.0) for key in _CONTEXT_PLOT),
    ))


def stamped(image: QImage, plot: dict, view: dict) -> QImage:
    """Mark *image* as belonging to this render context — the same
    stamp every bake writes on the picture it returns. For a caller
    that builds a base of its own (a blank canvas, a frame out of a
    cache) and wants it accepted as an incremental ``previous``: the
    stamp is the caller's assertion that the pixels it is handing
    over were drawn at exactly this plot and this view, and a
    picture carrying no matching stamp is refused."""
    image.setText(_CONTEXT_STAMP, scene_context(plot, view))
    return image


def _transform(plot: dict, view: dict):
    """The shared mapping (the face's painters' own): the WHOLE
    plate term — offset plus delta — rides the zoom, exactly as the
    QML walks it; the pan adds after, baked. The backing scale
    multiplies everything — the canvas is dpr times larger and the
    physical stroke widens with it, so the displayed logical
    picture stays identical."""
    scale = float(view.get("scale", 1.0)) * _backing_scale(view)
    sx = float(plot["sx"]) * scale
    sy = float(plot["sy"]) * scale
    offset_x = float(plot["offsetX"]) * scale + float(view.get("panX", 0.0)) * _backing_scale(view)
    offset_y = float(plot["offsetY"]) * scale + float(view.get("panY", 0.0)) * _backing_scale(view)
    return (sx, sy, offset_x, offset_y,
            float(plot["bedXMin"]), float(plot["bedYMax"]))


def _paint_segments(painter: QPainter, pen: QPen, payload: dict, plot: dict, view: dict,
                    class_names=None, colour=None, cancel=None) -> bool:
    """QPainterPath per segment (the measured winner: 92 ms vs
    drawLines' 209 ms and the per-edge loop's 307 ms on a 500k-motion
    layer). THE caller's configured geometry pen rides in — width,
    RoundCap and RoundJoin come from it; only the colour changes
    per class, so the full layer and the prefix share one physical
    stroke. `class_names` selects which classes to paint. A
    cooperative cancel between segments lets a superseded job stop
    at the next large unit of work."""
    sx, sy, offset_x, offset_y, bed_x_min, bed_y_max = _transform(plot, view)
    for name, segments in (payload.get("classes") or {}).items():
        if class_names is not None and name not in class_names:
            continue
        pen.setColor(QColor(colour if colour else _PLATE_CLASS_COLOURS.get(name, "#888888")))
        painter.setPen(pen)
        for points in segments:
            if cancel is not None and cancel.is_set():
                return False
            if len(points) < 2:
                continue
            path = QPainterPath()
            path.moveTo(offset_x + (points[0][0] - bed_x_min) * sx,
                        offset_y + (bed_y_max - points[0][1]) * sy)
            for i in range(1, len(points)):
                path.lineTo(offset_x + (points[i][0] - bed_x_min) * sx,
                            offset_y + (bed_y_max - points[i][1]) * sy)
            painter.drawPath(path)
    return True


def _new_canvas(view: dict) -> QImage:
    dpr = _backing_scale(view)
    image = QImage(int(view["width"] * dpr), int(view["height"] * dpr),
                   QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    return image


@contextmanager
def _painting(image: QImage):
    """A QPainter that always ends with the block.

    A raise between the begin and the end leaves the painter active,
    and the frame's locals die in assignment order — the canvas
    first — so the device is destroyed under a live painter
    (QPaintDevice warns, then the process dumps core: the render
    worker's failure lane)."""
    painter = QPainter(image)
    try:
        yield painter
    finally:
        painter.end()


def _derive_grey(coloured: QImage) -> QImage:
    """The grey whole-layer base, derived from the coloured raster —
    no second geometry walk ."""
    grey = QImage(coloured.size(), QImage.Format.Format_ARGB32_Premultiplied)
    grey.fill(QColor(0, 0, 0, 0))
    with _painting(grey) as painter:
        painter.drawImage(0, 0, coloured)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(0, 0, grey.width(), grey.height(), QColor(_PLATE_BASE_COLOUR))
    return grey


def _geometry_pen(plot: dict, view: dict) -> QPen:
    """The ONE geometry pen every asset shares: the physical stroke
    (nominal width x plot scale x zoom x lineScale x compact boost),
    round caps and joins. The full layer, the prefix and the grey
    base must never disagree on stroke width."""
    line_scale = float(view.get("lineScale", 0.7))
    compact = bool(view.get("compact", False))
    sx, _sy, _ox, _oy, _bx, _by = _transform(plot, view)
    stroke = max(0.01, float(view.get("nominalWidthMm", 0.2)) * sx * line_scale
                 * (7.0 if compact else 1.0))
    # The device-floor coverage (measured): the QML canvas paints at
    # the window's device grid, so a sub-floor stroke presents as
    # min(2/dpr, 1) FULL-intensity logical px there, while the
    # backed raster's own thin paint downscales to a faded fraction
    # (the ghostly-raster report — the QML reads brighter). The
    # floor gives the raster the same presented footprint WITHOUT
    # the old fixed floor's zoom magnification: the exact rasters
    # paint at the zoom already (floor = min(2, dpr) paint px), and
    # the camera-independent nav raster divides by the view's zoom —
    # a zoom change re-bakes that raster (the zoom rides its key) —
    # so the presented floor stays min(2/dpr, 1) at every zoom.
    backing = _backing_scale(view)
    dpr = max(1.0, float(view.get("dpr", 1.0)))
    if view.get("backing"):
        zoom = max(1.0, float(view.get("zoom") or 1.0))
        floor = backing * min(2.0 / dpr, 1.0) / zoom
    else:
        floor = min(2.0, dpr)
    stroke = max(stroke, floor)
    pen = QPen()
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def render_layer_prefix(payload: dict, plot: dict, view: dict, split: int,
                        cancel=None, previous=None, previous_split=0) -> QImage:
    """The printed PREFIX as its own asset (the measured verdict:
    the QML vertex walk for a partial layer costs ~900 ms at 500k
    motions on the UI thread — the initial paint, jumps and
    backward scrubs all pay it). The worker walks the same
    QPainterPath but strokes only the motions below the split, so
    the partial layer's printed portion arrives as a blit and QML
    draws only the live delta's tail. A previous raster turns the
    render INCREMENTAL: the walk starts at previous_split (the
    segments' motion indices are monotone, so the below-delta
    motions bisect away) and strokes only [previous_split, split)
    over a copy of the previous image — the forward scrub's refresh
    costs O(delta), not O(split), and the gap between refreshes
    shrinks to the delta itself. The previous picture MUST belong to
    the same render context (the caller passes the committed
    wrapper's own image). A cooperative cancel between segments lets
    a superseded job stop at the next large unit."""
    stamp = scene_context(plot, view)
    if previous is not None and 0 < previous_split <= split \
            and previous.width() > 0 and previous.height() > 0 \
            and previous.text(_CONTEXT_STAMP) == stamp:
        image = QImage(previous)
        start = previous_split
    else:
        # A backward demand (previous_split > split) must never copy
        # the larger picture — painting new paths cannot erase the
        # motions beyond the target, and the mislabelled prefix
        # would resurrect them (the backward-scrub ghost).
        image = _new_canvas(view)
        start = 0
    with _painting(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = _geometry_pen(plot, view)
        painter.setPen(pen)
        _paint_below_split(painter, pen, payload, plot, view, split,
                           cancel=cancel, first=start)
    return stamped(image, plot, view)


def _segment_first(points, first: int) -> int:
    """The index of the first point whose motion index is at or after
    `first` — or len(points) (past the end) when no motion reaches
    `first`. The past-the-end result makes the caller paint nothing:
    a fallback to the last vertex would re-stroke the trailing edge,
    re-compositing completed geometry (the alpha-accumulation
    report)."""
    low, high = 0, len(points)
    while low < high:
        middle = (low + high) // 2
        if points[middle][2] < first:
            low = middle + 1
        else:
            high = middle
    return low


def _paint_below_split(painter: QPainter, pen: QPen, payload: dict, plot: dict,
                       view: dict, split, cancel=None, first=0) -> None:
    """Stroke only the edges whose owning motion is in
    [first, split) (the prefix's own rule — an edge draws when its
    motion index is under the boundary, and the motions within a
    segment never decrease, so the first boundary ends the walk).
    `first` is the incremental render's start: the bisect skips the
    already-painted motions without walking them."""
    sx, sy, offset_x, offset_y, bed_x_min, bed_y_max = _transform(plot, view)
    for name, segments in (payload.get("classes") or {}).items():
        if cancel is not None and cancel.is_set():
            return
        pen.setColor(QColor(_PLATE_CLASS_COLOURS.get(name, "#888888")))
        painter.setPen(pen)
        for points in segments:
            if cancel is not None and cancel.is_set():
                return
            if len(points) < 2:
                continue
            begin = _segment_first(points, first) if first > 0 else 0
            if begin >= len(points):
                # Every motion precedes the lower bound: the segment
                # is complete in the copied picture — nothing to add,
                # and nothing to re-stroke.
                continue
            if begin > 0:
                # The edge INTO the first qualifying motion: its own
                # motion index is points[begin][2] >= first, so the
                # lower bound holds. No further back-up — earlier
                # edges carry motions below `first`.
                begin -= 1
            path = QPainterPath()
            drew = False
            for i in range(begin + 1, len(points)):
                if split >= 0 and points[i][2] >= split:
                    break
                # The lower-bound guard per edge (the interval rule:
                # first <= edge.motion < split) — the incremental walk
                # must never touch a completed edge.
                if first > 0 and points[i][2] < first:
                    continue
                if (i - begin) % 512 == 0 and cancel is not None and cancel.is_set():
                    return
                if not drew:
                    path.moveTo(offset_x + (points[i - 1][0] - bed_x_min) * sx,
                                offset_y + (bed_y_max - points[i - 1][1]) * sy)
                    drew = True
                path.lineTo(offset_x + (points[i][0] - bed_x_min) * sx,
                            offset_y + (bed_y_max - points[i][1]) * sy)
            if drew:
                painter.drawPath(path)


def _travels_pen(pen: QPen, view: dict) -> QPen:
    """The travel channel's own pen: the geometry pen narrowed by the
    configured visual ratio, in the travel colour. ONE derivation, so
    the nav composite and the exact scene's travel raster can never
    disagree on the stroke."""
    tpen = QPen(pen)
    tpen.setWidthF(max(0.01, pen.widthF()
                       * float(view.get("travelVisualRatio",
                                        _PLATE_TRAVEL_VISUAL_RATIO))))
    tpen.setColor(QColor(_PLATE_TRAVEL_COLOUR))
    return tpen


def _paint_travels(painter: QPainter, pen: QPen, payload: dict, plot: dict,
                   view: dict, split=None, cancel=None, first=0) -> bool:
    """The travel channel over [first, split): the same interval rule
    the printed prefix walks (an edge draws exactly when its own motion
    index is under the boundary and at or above the lower bound), so
    an uncancelled `split=None` run paints the whole channel and a
    delta run adds only the travels since the previous composite.
    Returns False when a cooperative cancel stopped the walk."""
    travels = payload.get("travels")
    if not travels:
        return True
    painter.setPen(_travels_pen(pen, view))
    tx_sx, tx_sy, tx_ox, tx_oy, tx_bx, tx_by = _transform(plot, view)
    for points in travels or []:
        if cancel is not None and cancel.is_set():
            return False
        if len(points) < 2:
            continue
        begin = _segment_first(points, first) if first > 0 else 0
        if begin >= len(points):
            continue
        if begin > 0:
            begin -= 1
        path = QPainterPath()
        drew = False
        for i in range(begin + 1, len(points)):
            if split is not None and split >= 0 and points[i][2] >= split:
                break
            if first > 0 and points[i][2] < first:
                continue
            if not drew:
                path.moveTo(tx_ox + (points[i - 1][0] - tx_bx) * tx_sx,
                            tx_oy + (tx_by - points[i - 1][1]) * tx_sy)
                drew = True
            path.lineTo(tx_ox + (points[i][0] - tx_bx) * tx_sx,
                        tx_oy + (tx_by - points[i][1]) * tx_sy)
        if drew:
            painter.drawPath(path)
    return True


def _paint_grid(painter: QPainter, plot: dict, view: dict) -> None:
    """The bed grid, the interaction scene's BOTTOM raster: the same
    10 mm thin / 50 mm thick graduations and the border the face's
    mapping canvas paints — the warm raster is ONE flat composite of
    every canvas component (the live ruling: a separately-painted
    grid pans at its own pace, so the grid must never be a second
    layer)."""
    bed_width = float(view.get("bedWidth") or 0)
    bed_depth = float(view.get("bedDepth") or 0)
    if bed_width <= 0 or bed_depth <= 0:
        return
    sx, sy, offset_x, offset_y, bed_x_min, bed_y_max = _transform(plot, view)
    left = offset_x
    top = offset_y
    right = offset_x + bed_width * sx
    bottom = offset_y + bed_depth * sy
    bed_x_max = bed_x_min + bed_width
    bed_y_min = bed_y_max - bed_depth
    # The adaptive width (the live ruling): the raster's camera
    # transform presents a painted pen at painted x zoom / backing,
    # so the pen painted at backing / zoom presents as the canvas's
    # constant 1 (or 2) px AT THIS ZOOM — a zoom change re-bakes the
    # raster (the zoom rides the nav key), and the single flat
    # raster keeps the grid at the correct thickness at every level.
    zoom = max(1.0, float(view.get("zoom") or 1.0))
    backing = _backing_scale(view)
    thin = QPen(QColor(_PLATE_GRID_THIN))
    thin.setWidthF(4.0 * backing / (4.0 * zoom))  # 1 logical px at any zoom
    thick = QPen(QColor(_PLATE_GRID_BORDER))
    thick.setWidthF(8.0 * backing / (4.0 * zoom))  # 2 logical px at any zoom
    painter.setPen(thin)
    gx = math.ceil(bed_x_min / 10.0) * 10.0
    while gx <= bed_x_max:
        if round(gx) % 50 != 0:
            sxg = offset_x + (gx - bed_x_min) * sx
            painter.drawLine(QPointF(sxg, top), QPointF(sxg, bottom))
        gx += 10.0
    gy = math.ceil(bed_y_min / 10.0) * 10.0
    while gy <= bed_y_max:
        if round(gy) % 50 != 0:
            syg = offset_y + (bed_y_max - gy) * sy
            painter.drawLine(QPointF(left, syg), QPointF(right, syg))
        gy += 10.0
    painter.setPen(thick)
    hx = math.ceil(bed_x_min / 50.0) * 50.0
    while hx <= bed_x_max:
        sxg = offset_x + (hx - bed_x_min) * sx
        painter.drawLine(QPointF(sxg, top), QPointF(sxg, bottom))
        hx += 50.0
    hy = math.ceil(bed_y_min / 50.0) * 50.0
    while hy <= bed_y_max:
        syg = offset_y + (bed_y_max - hy) * sy
        painter.drawLine(QPointF(left, syg), QPointF(right, syg))
        hy += 50.0
    painter.drawRect(QRectF(left + 4.0, top + 4.0,
                            right - left - 8.0, bottom - top - 8.0))


def render_navigation_layer(window: dict, plot: dict, view: dict, split=None,
                            cancel=None, previous=None,
                            previous_split=0) -> QImage:
    """The interaction scene (the pan/zoom navigation raster): ONE
    flattened full-bed composite at a FIXED 4x the 100%-fit
    resolution, camera-independent — the pan and the zoom are pure
    presentation transforms over this image, never re-renders. The
    stack mirrors the exact composition: the ghosts at 0.30, the
    grey base, the printed portion of the current layer (the prefix
    rule at a partial split, the full layer otherwise) and the
    travels up to the live split. No screen-space chrome, no
    per-viewport intermediates.

    A `previous` composite turns the bake INCREMENTAL (the measured
    live cadence: the attached throttle re-bakes the whole 4x
    composite — four full geometry walks at 500k motions, ~0.9 s of
    worker CPU — for a delta of a few hundred printed motions). The
    caller guarantees the scene is otherwise identical, i.e. that
    its content key matches the previous bake's on every field but
    the split; the grid, the ghosts and the grey base are already in
    the copied pixels, so only [previous_split, split) is stroked.
    The copied picture MUST belong to the same render context and
    the same scene — a caller that cannot prove that passes None and
    gets the full bake.

    A delta is taken only while BOTH pictures carry the partial
    split's own stack: the grey base lies UNDER the printed prefix,
    so its ink shows through the coloured stroke's anti-aliased
    fringes (measured: the completion transition alone moved 1412
    fringe pixels of the 1.92 M canvas when the copy kept the base
    and the target did not) and the completed layer's picture — the
    full-layer branch, no grey base — cannot be reached by adding
    strokes. The completion therefore bakes whole, once per layer."""
    image = _new_canvas(view)
    stamp = scene_context(plot, view)
    current = window.get("current")
    motions = (current or {}).get("motions") or 0
    delta = (previous is not None and previous_split is not None
             and split is not None and 0 < previous_split <= split
             and 0 <= split < motions
             and current is not None and not previous.isNull()
             and previous.width() == image.width()
             and previous.height() == image.height()
             and previous.text(_CONTEXT_STAMP) == stamp)

    if delta:
        image = QImage(previous)
        with _painting(image) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            pen = _geometry_pen(plot, view)
            painter.setPen(pen)
            _paint_below_split(painter, pen, current, plot, view, split,
                               cancel=cancel, first=previous_split)
            if view.get("showTravels", False):
                _paint_travels(painter, pen, current, plot, view, split,
                               cancel=cancel, first=previous_split)
        # The delta's own copy is re-stamped: the live cadence is a
        # CHAIN, so the stamp has to survive each link or the next
        # refresh bakes whole.
        return stamped(image, plot, view)
    with _painting(image) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        _paint_grid(painter, plot, view)
        pen = _geometry_pen(plot, view)
        painter.setPen(pen)
        # The legend checkboxes are part of the scene's CONTENT: the
        # warm raster must mirror the exact view's toggles — a ghost the
        # user hid, a base or the travels turned off, all stay off.
        if view.get("showPrevious", True):
            for payload in (window.get("prev"),):
                if payload is None:
                    continue
                painter.setOpacity(0.30)
                _paint_segments(painter, pen, payload, plot, view, cancel=cancel)
                painter.setOpacity(1.0)
                if cancel is not None and cancel.is_set():
                    return image
        if view.get("showNext", True):
            for payload in (window.get("next"),):
                if payload is None:
                    continue
                painter.setOpacity(0.30)
                _paint_segments(painter, pen, payload, plot, view, cancel=cancel)
                painter.setOpacity(1.0)
                if cancel is not None and cancel.is_set():
                    return image
        current = window.get("current")
        if current is None:
            return image
        motions = current.get("motions") or 0
        if split is not None and 0 <= split < motions:
            # The partial state: the grey whole-layer base, then the
            # printed prefix (the vector tail beyond the live split is
            # NOT printed — the boundary is the scene's truth).
            if view.get("showBase", True):
                grey_pen = QPen(pen)
                grey_pen.setColor(QColor(_PLATE_BASE_COLOUR))
                painter.setPen(grey_pen)
                painter.setOpacity(0.55)
                # The colour rides the CLASS unless forced: the base is
                # the grey silhouette, never the feature colours (the
                # live bug — the nav read as a 100%-complete layer).
                _paint_segments(painter, grey_pen, current, plot, view,
                                colour=_PLATE_BASE_COLOUR, cancel=cancel)
                painter.setOpacity(1.0)
                painter.setPen(pen)
            _paint_below_split(painter, pen, current, plot, view, split, cancel=cancel)
        else:
            _paint_segments(painter, pen, current, plot, view, cancel=cancel)
        if not view.get("showTravels", False) or not current.get("travels"):
            return stamped(image, plot, view)
        if not _paint_travels(painter, pen, current, plot, view, split,
                              cancel=cancel):
            return stamped(image, plot, view)
    # Every exit carries the context, the delta's own copy included:
    # the live cadence is a CHAIN, so the stamp has to survive each
    # link or the next refresh bakes whole.
    return stamped(image, plot, view)


def render_layer_raster(payload: dict, plot: dict, view: dict, cancel=None) -> tuple:
    """Paint the layer's three sibling assets — (coloured, grey base,
    travels) — the same transform the face's vector painter used, so
    a blit lands the identical picture. Pan-baked: the pan rides the
    offset, never a scene-graph translation. All three share the
    one configured geometry pen, so the prefix and the full layer
    can never disagree on stroke width."""
    coloured = _new_canvas(view)
    with _painting(coloured) as painter:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = _geometry_pen(plot, view)
        painter.setPen(pen)
        _paint_segments(painter, pen, payload, plot, view, cancel=cancel)
    grey = _derive_grey(coloured)
    travels = _NULL_IMAGE
    if payload.get("travels"):
        travels = _new_canvas(view)
        with _painting(travels) as tpainter:
            tpainter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            if not _paint_travels(tpainter, pen, payload, plot, view,
                                  cancel=cancel):
                return coloured, grey, travels
    return coloured, grey, travels
