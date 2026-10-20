# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release and Git history (newest first; original provenance retained):
# 2026-10-20 — 6.0.0: Separate raster painting, Qt assets and worker publication.
# 2026-10-18 — 6.0.0: Convert empty immutable geometry and event markers to Qt-compatible values.
# 2026-10-01 [02e4bd5] Draw switchable bed-axis arrows across plate views
# 2026-10-01 [b5f5511] Revert "Capitalise the remaining top-level directories"
# 2026-09-21 [f130718] 4.6.0: the native render path — dense toolpath geometry rasterises off the QML JavaScript
#   path (QPainterPath per segment on a worker, the measured winner at ~120 ms for 500k motions), the face
#   composes blitted QImages with role-free reuse, the scrub keeps its vector delta, and the whole pipeline is
#   generation-gated and latest-wins coalesced


"""QML-facing layer assets and immutable-geometry conversion."""
from collections import OrderedDict
from itertools import count
from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QImage

_NULL_IMAGE = QImage()
_LAYER_IDENTITIES = count(1)

def qml_geometry(payload):
    """Expose cached immutable polylines as JavaScript arrays for Canvas.

    PyQt transports nested tuples as Python objects rather than QVariant
    arrays. Only the software renderer needs this copy; GPU workers borrow
    the original geometry directly.
    """
    if payload is None:
        return None
    rows = [segment for groups in (payload.get("classes") or {}).values()
            for segment in groups]
    rows.extend(payload.get("travels") or ())
    rows.extend(segment for groups in (payload.get("travelClasses") or {}).values() for segment in groups)
    marks = tuple(point for key in ("travelStarts", "travelEnds", "retractions", "unretractions")
                  for point in payload.get(key) or ())
    if isinstance(payload, dict) and not any(isinstance(segment, tuple) or
               (segment and isinstance(segment[0], tuple)) for segment in rows) \
            and not any(isinstance(point, tuple) for point in marks) \
            and not any(isinstance(payload.get(key), tuple) for key in ("widths", "speeds", "tools")) \
            and not any(isinstance(bounds, tuple) for bounds in (payload.get("colourRanges") or {}).values()):
        return payload
    def segments(rows):
        return [[list(point) for point in segment] for segment in rows]
    return dict(payload,
                **({"widths": list(payload["widths"])} if "widths" in payload else {}),
                **({key: list(payload[key]) for key in ("speeds", "tools") if key in payload}),
                colourRanges={key:list(bounds) for key,bounds in (payload.get("colourRanges") or {}).items()},
                classes={name: segments(rows) for name, rows in
                         (payload.get("classes") or {}).items()},
                travels=segments(payload.get("travels") or ()),
                **({"travelClasses": {name:segments(rows) for name,rows in payload["travelClasses"].items()}} if "travelClasses" in payload else {}),
                **({key: [list(point) for point in payload[key]] for key in ("retractions", "unretractions") if key in payload}),
                travelStarts=[list(point) for point in payload.get("travelStarts") or ()],
                travelEnds=[list(point) for point in payload.get("travelEnds") or ()])


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

    def geometry_payload(self):
        """Borrow the immutable layer geometry without QVariant conversion.

        Scene-graph preparation retains this payload while a worker reads it.
        Callers must never mutate its polylines or motion numbers.
        """
        return self._payload


    def __init__(self, payload: dict, parent: QObject = None) -> None:
        super().__init__(parent)
        self._payload = payload
        self._qml_payload = None
        self._scene_identity = str(next(_LAYER_IDENTITIES))
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
        self._prefix_pending = False
        self._prefix_checkpoints = OrderedDict()
        self._rewind_files = {}
        self._rewind_pending = []
        self._rewind_ticket = None
        self._rewind_cancel = None
        self._expected_key = None

    @pyqtProperty("QVariantList", constant=True)
    def extruderEvents(self):
        return [list(point) + [retract] for key, retract in (("retractions", True), ("unretractions", False))
                for point in self._payload.get(key) or ()]

    @pyqtProperty("QVariantMap", constant=True)
    def colourInfo(self):
        return {"height": self._payload.get("layerHeight", .2), "ranges": {key:list(bounds) for key,bounds in (self._payload.get("colourRanges") or {}).items()}}

    @pyqtProperty(int, constant=True)
    def motions(self) -> int:
        return int(self._payload.get("motions") or 0)

    @pyqtProperty(bool, notify=rasterReady)
    def prefixPending(self):
        return self._prefix_pending

    def set_prefix_pending(self, pending):
        if self._prefix_pending != pending:
            self._prefix_pending = pending
            self.rasterReady.emit()

    @pyqtProperty(str, constant=True)
    def sceneIdentity(self) -> str:
        """An immutable incarnation, independent of layer and motion count."""
        return self._scene_identity

    @pyqtProperty("QVariantMap", constant=True)
    def fallbackVector(self) -> dict:
        """Lazy transport recovery; ordinary raster scenes never read this.

        A missing PNG must leave a complete vector producer available even
        at 100%, where normal publication deliberately omits this costly
        QVariant conversion.
        """
        if self._qml_payload is None:
            self._qml_payload = qml_geometry(self._payload)
        return self._qml_payload

    @pyqtProperty(bool, constant=True)
    def hasFallbackVector(self) -> bool:
        """Capability check without converting the geometry into QVariant."""
        return True

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
            self._prefix_pending = False
            self.clear_prefix_checkpoints()
            self.rasterReady.emit()

    def clear_prefix_checkpoints(self):
        if self._rewind_cancel is not None:
            self._rewind_cancel.set()
        self._rewind_cancel = None
        self._rewind_ticket = None
        self._rewind_files.clear()
        self._rewind_pending.clear()
        self._prefix_checkpoints.clear()

    def prefix_file_seed(self, key, split):
        if key != self._expected_key:
            return "", 0
        boundaries = [p for p in self._rewind_files if p <= split]
        boundary = max(boundaries) if boundaries else 0
        return self._rewind_files.get(boundary, ""), boundary

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
        if image is not None and not image.isNull() and data:
            if self._prefix_checkpoints and next(iter(self._prefix_checkpoints.values()))[2] != key:
                self._prefix_checkpoints.clear()
            self._prefix_checkpoints[split] = (image, data, key)
            self._prefix_checkpoints.move_to_end(split)
            # Four checkpoints, with a stricter 16 MiB pixel cap. The
            # current prefix is already charged separately; large views
            # simply retain fewer historical checkpoints.
            while self._prefix_checkpoints and (len(self._prefix_checkpoints) > 4
                    or sum(entry[0].sizeInBytes() for entry in self._prefix_checkpoints.values()) > 16 * 1024 * 1024):
                self._prefix_checkpoints.popitem(last=False)
        self.rasterReady.emit()

    def prefix_seed(self, key, split):
        candidates = [(boundary, entry) for boundary, entry in self._prefix_checkpoints.items()
                      if boundary <= split and entry[2] == key]
        if not candidates:
            return None, 0
        boundary, entry = max(candidates, key=lambda candidate: candidate[0])
        self._prefix_checkpoints.move_to_end(boundary)
        return entry[0], boundary

    def restore_prefix(self, key, split):
        """Reuse a compatible earlier checkpoint without decoding geometry."""
        image, boundary = self.prefix_seed(key, split)
        if image is None or (self.prefixValid and self.prefixSplit == boundary):
            return False
        data = self._prefix_checkpoints[boundary][1]
        self.set_prefix(image, data, boundary, key)
        return True

    def prefix_references(self):
        return ([entry[1] for entry in self._prefix_checkpoints.values()]
                + list(self._rewind_files.values()) + self._rewind_pending)

    def memory_bytes(self) -> int:
        """The wrapper's pixel bytes, including bounded prefix checkpoints.
        The payload's geometry is charged separately, through the
        service's decoded pins."""
        total = 0
        for image in (self._raster, self._base, self._travels, self._prefix):
            if image is not None and not image.isNull():
                total += image.sizeInBytes()
        total += sum(entry[0].sizeInBytes() for entry in self._prefix_checkpoints.values()
                     if entry[0] is not self._prefix)
        return total
