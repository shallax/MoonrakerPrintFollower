import QtQuick 2.15
import UM 1.5 as UM
import "theme"

// The plate family's shared bed-space canvas (4.6.0): the ONE
// implementation of the bed mapping and the hit test — the faces own
// their palettes, gestures and legends, never the geometry (the
// architecture review's rule). The exclude face consumes this; the
// progress face draws its own layers on the same bed space.
Item {
    id: root
    objectName: "moonrakerPlateCanvas"
    // The canvas paints past its bounds otherwise (the live report:
    // the dot drew over the pane chrome) — the plate clips itself.
    clip: true

    property var printerModel: null
    property var plate: null          // the model's plateObjects payload
    property bool compact: false
    // The bed graphic. Always on in the product; a harness that
    // measures a STROKE turns it off so the frame holds only the ink
    // under test — the grid is drawn on a threaded canvas whose paint
    // lands between grabs, so a measurement that subtracts one frame
    // from another measures the grid and whatever else moved with it.
    property bool showGrid: true
    // The zoom/pan view, owned by the follower face and applied by
    // THIS canvas's grid (the bottom raster must move with the
    // layers): identity elsewhere — the picker never zooms.
    property real viewScale: 1.0
    property real viewPanX: 0.0
    property real viewPanY: 0.0
    // The grid's threaded raster reads the view through a var carrier
    // (worker paints see var properties fresh, primitives stale — the
    // offscreen-harness repaint findings; the face does the same). The
    // pan IS a paint input: a raster image is exactly the canvas' size,
    // so translating an already-clipped image only slides it off (the
    // live report — panning showed blank canvas).
    property var _view: ({
            scale: 1.0,
            panX: 0.0,
            panY: 0.0
        })

    function _publishView() {
        root._view = {
            scale: root.viewScale,
            panX: root.viewPanX,
            panY: root.viewPanY
        };
    }
    property string hoveredName: ""
    property color halo: UM.Theme.getColor("main_background")
    signal objectHovered(string name)

    // The degraded hit radius in BED millimetres — a bed-space
    // constant, never pixels (the popover resizes; the review's rule).
    property real hitRadiusMm: 18.0

    // The mapping is a computed value, never a resize artefact: it
    // re-maps where the canvas stands when the printer attaches, when
    // any bed-geometry input moves, and when the item resizes. The
    // imperative rebuild ran on completion and on resize alone, so a
    // late attach — or a bed switch — left the map blank or on the
    // previous machine's coordinates until the user resized the pane.
    property var _plot: _plotValue()

    function _bedBounds() {
        // The BED-space mapping, mirroring the mesh map's contract:
        // the physical bed rectangle with the centerIsZero convention
        // applied ONCE here (the double-apply trap). Null when the
        // machine geometry is unknown — the canvas then draws nothing
        // and the section says why.
        if (root.printerModel == null) {
            return null;
        }
        var machineWidth = root.printerModel.bedMeshMachineWidth;
        var machineDepth = root.printerModel.bedMeshMachineDepth;
        if (!(machineWidth > 0) || !(machineDepth > 0)) {
            return null;
        }
        var center = root.printerModel.bedMeshCenterIsZero;
        var bedXMin = center ? -machineWidth / 2 : 0;
        var bedXMax = bedXMin + machineWidth;
        var bedYMin = center ? -machineDepth / 2 : 0;
        var bedYMax = bedYMin + machineDepth;
        // The border's own allowance: the fit reserves the stroke's
        // width at every edge, so the centred border never clips
        // against the canvas (the live report — the top and bottom
        // read a pixel thin).
        var allowance = root.compact ? 1 : 2;
        var fitW = width - 2 * allowance;
        var fitH = height - 2 * allowance;
        var plotWidth = fitW;
        var plotHeight = fitH;
        var offsetX = allowance;
        var offsetY = allowance;
        var target = machineWidth / machineDepth;
        if (fitW / fitH > target) {
            plotWidth = fitH * target;
            offsetX = allowance + (fitW - plotWidth) / 2;
        } else {
            plotHeight = fitW / target;
            offsetY = allowance + (fitH - plotHeight) / 2;
        }
        return {
            "offsetX": offsetX,
            "offsetY": offsetY,
            "plotWidth": plotWidth,
            "plotHeight": plotHeight,
            "bedXMin": bedXMin,
            "bedXMax": bedXMax,
            "bedYMin": bedYMin,
            "bedYMax": bedYMax
        };
    }

    function _plotValue() {
        // Geometry per input change, never per paint.
        var bed = _bedBounds();
        if (bed == null) {
            return null;
        }
        return {
            "bed": bed,
            "sx": bed.plotWidth / (bed.bedXMax - bed.bedXMin),
            // Screen y grows down; the bed's y maximum sits at the
            // top edge (printer y grows away from the front) — the
            // mesh map's form, with the y minimum at the bottom.
            "sy": bed.plotHeight / (bed.bedYMax - bed.bedYMin)
        };
    }

    function plateToScene(x, y) {
        // The single printer-to-widget transform — polygons, the hit
        // test and the dot all use it.
        var plot = root._plot;
        if (plot == null) {
            return null;
        }
        return {
            "x": plot.bed.offsetX + (x - plot.bed.bedXMin) * plot.sx,
            "y": plot.bed.offsetY + (plot.bed.bedYMax - y) * plot.sy
        };
    }

    // The per-payload hit index: the rows carrying usable polygons with
    // their bed-space bounding boxes, and the rows that carry only a
    // centre. Rebuilt when the payload is assigned, never per hit test
    // — the picker tests the point on every hover move.
    property var _hitIndex: null
    property var _centreRows: null

    function _rebuildHitIndex() {
        var rows = root.plate != null ? root.plate.objects : null;
        var polygons = [];
        var centres = [];
        for (var i = 0; rows != null && i < rows.length; ++i) {
            var row = rows[i];
            // Three vertices are the polygon's minimum; anything less
            // is not geometry, so the row degrades to its centre.
            if (row.polygon != null && row.polygon.length >= 3) {
                var minX = row.polygon[0][0];
                var maxX = minX;
                var minY = row.polygon[0][1];
                var maxY = minY;
                for (var v = 1; v < row.polygon.length; ++v) {
                    minX = Math.min(minX, row.polygon[v][0]);
                    maxX = Math.max(maxX, row.polygon[v][0]);
                    minY = Math.min(minY, row.polygon[v][1]);
                    maxY = Math.max(maxY, row.polygon[v][1]);
                }
                polygons.push({
                    "name": row.name,
                    "polygon": row.polygon,
                    "minX": minX,
                    "maxX": maxX,
                    "minY": minY,
                    "maxY": maxY
                });
            } else if (row.center != null) {
                centres.push({
                    "name": row.name,
                    "center": row.center
                });
            }
        }
        root._hitIndex = polygons;
        root._centreRows = centres;
    }

    function _bedFromScene(x, y) {
        // The inverse of plateToScene, once per hit test: the polygons
        // live in bed millimetres and the click arrives in item pixels.
        var plot = root._plot;
        if (plot == null) {
            return null;
        }
        return {
            "x": plot.bed.bedXMin + (x - plot.bed.offsetX) / plot.sx,
            "y": plot.bed.bedYMax - (y - plot.bed.offsetY) / plot.sy
        };
    }

    function _pointInPolygon(x, y, polygon) {
        // Ray casting, in the Python probe's own form
        // (MonitorFormatting._point_in_polygon): a point lands in the
        // same object on both sides of the wire.
        var inside = false;
        var j = polygon.length - 1;
        for (var i = 0; i < polygon.length; ++i) {
            var xi = polygon[i][0];
            var yi = polygon[i][1];
            var xj = polygon[j][0];
            var yj = polygon[j][1];
            if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) {
                inside = !inside;
            }
            j = i;
        }
        return inside;
    }

    function hitTest(x, y) {
        // Containment is authoritative: the click maps to bed space once
        // and walks the polygons box-first (the Python walk's shape —
        // polygon_bounds then the edge test). Two overlapping polygons
        // resolve to the FIRST row in the payload's order, the rule the
        // printed-cache probe pins (MonitorFormatting.containing_object)
        // — the same destructive target whichever side is asked.
        var plot = root._plot;
        if (plot == null || root.plate == null || !(plot.sx > 0) || !(plot.sy > 0)) {
            return "";
        }
        if (root._hitIndex === null) {
            _rebuildHitIndex();
        }
        var bed = _bedFromScene(x, y);
        var index = root._hitIndex;
        for (var i = 0; i < index.length; ++i) {
            var entry = index[i];
            if (bed.x < entry.minX || bed.x > entry.maxX || bed.y < entry.minY || bed.y > entry.maxY) {
                continue;
            }
            if (_pointInPolygon(bed.x, bed.y, entry.polygon)) {
                return entry.name;
            }
        }
        // The degraded fallback, for rows that genuinely lack polygon
        // data: the nearest centre within the radius. A row that HAS
        // geometry is never a centre candidate here, so no nearby centre
        // can override a containment result.
        var radiusPx = root.hitRadiusMm * Math.max(Math.abs(plot.sx), Math.abs(plot.sy));
        var best = "";
        var bestDist = Infinity;
        var centres = root._centreRows;
        for (var c = 0; c < centres.length; ++c) {
            var scene = root.plateToScene(centres[c].center[0], centres[c].center[1]);
            var d = (scene.x - x) * (scene.x - x) + (scene.y - y) * (scene.y - y);
            if (d < bestDist) {
                bestDist = d;
                best = centres[c].name;
            }
        }
        return bestDist <= radiusPx * radiusPx ? best : "";
    }

    onWidthChanged: plateCanvas.requestPaint()
    onHeightChanged: plateCanvas.requestPaint()
    on_PlotChanged: plateCanvas.requestPaint()
    Component.onCompleted: {
        _rebuildHitIndex();
        _publishView();
        plateCanvas.requestPaint();
    }
    onPlateChanged: {
        _rebuildHitIndex();
        plateCanvas.requestPaint();
    }
    onHoveredNameChanged: plateCanvas.requestPaint()
    // The grid flag is a paint input, and this canvas paints into an
    // IMAGE: without the repaint the buffer keeps the last picture it
    // was given, so turning the grid off left it on screen.
    onShowGridChanged: plateCanvas.requestPaint()
    onViewScaleChanged: {
        _publishView();
        plateCanvas.requestPaint();
    }
    onViewPanXChanged: {
        _publishView();
        plateCanvas.requestPaint();
    }
    onViewPanYChanged: {
        _publishView();
        plateCanvas.requestPaint();
    }

    // The threaded image-backed canvas (the TemperatureChart
    // doctrine): the paint callback touches only the snapshot it is
    // handed, never a live QML item.
    Canvas {
        id: plateCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        onPaint: {
            var ctx = getContext("2d");
            var plot = root._plot;
            ctx.reset();
            if (plot == null) {
                return;
            }
            if (root.showGrid) {
                _drawGrid(ctx, plot);
            }
            if (root.plate == null) {
                return;
            }
            // Round joins on the object outlines: the default miter
            // pokes pointed corners that grow with the stroke width
            // (the live report's red perimeter spikes).
            ctx.lineJoin = "round";
            ctx.lineCap = "round";
            var plate = root.plate;
            for (var i = 0; i < plate.objects.length; ++i) {
                var row = plate.objects[i];
                // The 2 px background halo under every stroke: state
                // ink sits on arbitrary feature ink otherwise, and no
                // contrast value holds without it (the UX ruling).
                // The palette: current reads as Cura's highlight blue,
                // the passed/printed objects as the green, the
                // excluded as the red, the pending as the plain text
                // ink.
                var ink = row.excluded === true ? MoonrakerTheme.dangerRed : row.current === true ? UM.Theme.getColor("primary") : row.passed === true ? MoonrakerTheme.plateCurrent : UM.Theme.getColor("text");
                // The mini halves the strokes and the halo: at its
                // scale a 4 px halo swallows the neighbours (the
                // live report).
                var compactScale = root.compact ? 0.5 : 1.0;
                // The hovered row thickens (the live request): the
                // hover must read without stealing a state colour.
                var hovered = row.name === root.hoveredName;
                var widthPx = (hovered ? 3.0 : row.current === true ? 2.5 : 1.5) * compactScale;
                ctx.lineWidth = widthPx + 4 * compactScale;
                ctx.strokeStyle = root.halo;
                ctx.beginPath();
                _stroke(ctx, row, plot);
                ctx.stroke();
                ctx.lineWidth = widthPx;
                ctx.strokeStyle = ink;
                ctx.beginPath();
                _stroke(ctx, row, plot);
                ctx.stroke();
                // No centre ring for the current object: the bounds
                // highlight IS the indicator (the live ruling — two
                // current symbols read as two states).
            }
        }
    }

    function _drawGrid(ctx, plot) {
        // The bed graphic, the stack's BOTTOM raster (the live
        // request): thin 10 mm graduations, thick 50 mm ones and a
        // thick border — painted first so every face's content draws
        // over it, and static between resizes. The mini map skips the
        // thin lines: at its size they read as noise. The view
        // transform (the follower's zoom/pan) applies to every
        // coordinate so the grid moves with the layers.
        var bed = plot.bed;
        var thin = UM.Theme.getColor("lining");
        var thick = UM.Theme.getColor("border");
        // The pan rides every coordinate: the image is canvas-sized,
        // so a zoomed view must re-draw at the new offset.
        var left = bed.offsetX * root._view.scale + root._view.panX;
        var top = bed.offsetY * root._view.scale + root._view.panY;
        var right = left + bed.plotWidth * root._view.scale;
        var bottom = top + bed.plotHeight * root._view.scale;
        function gridX(bedX) {
            return root.plateToScene(bedX, bed.bedYMin).x * root._view.scale + root._view.panX;
        }
        function gridY(bedY) {
            return root.plateToScene(bed.bedXMin, bedY).y * root._view.scale + root._view.panY;
        }
        // The visible bed window: at zoom most graduations sit off
        // the canvas, and each costs a stroke — the loops clamp to
        // what the view can see (the live report's pan cost).
        function bedXAt(px) {
            return bed.bedXMin + ((px - root._view.panX) / root._view.scale - bed.offsetX) / plot.sx;
        }
        function bedYAt(py) {
            return bed.bedYMax - ((py - root._view.panY) / root._view.scale - bed.offsetY) / plot.sy;
        }
        var xlo = Math.max(bed.bedXMin, bedXAt(-8));
        var xhi = Math.min(bed.bedXMax, bedXAt(width + 8));
        var ylo = Math.max(bed.bedYMin, bedYAt(height + 8));
        var yhi = Math.min(bed.bedYMax, bedYAt(-8));
        if (!root.compact) {
            ctx.lineWidth = 1;
            ctx.strokeStyle = thin;
            var gx = Math.ceil(xlo / 10) * 10;
            for (; gx <= xhi; gx += 10) {
                if (Math.round(gx) % 50 === 0) {
                    continue;
                }
                var sx = gridX(gx);
                ctx.beginPath();
                ctx.moveTo(sx, top);
                ctx.lineTo(sx, bottom);
                ctx.stroke();
            }
            var gy = Math.ceil(ylo / 10) * 10;
            for (; gy <= yhi; gy += 10) {
                if (Math.round(gy) % 50 === 0) {
                    continue;
                }
                var sy = gridY(gy);
                ctx.beginPath();
                ctx.moveTo(left, sy);
                ctx.lineTo(right, sy);
                ctx.stroke();
            }
        }
        ctx.lineWidth = root.compact ? 1 : 2;
        ctx.strokeStyle = thick;
        var hx = Math.ceil(xlo / 50) * 50;
        for (; hx <= xhi; hx += 50) {
            var sx50 = gridX(hx);
            ctx.beginPath();
            ctx.moveTo(sx50, top);
            ctx.lineTo(sx50, bottom);
            ctx.stroke();
        }
        var hy = Math.ceil(ylo / 50) * 50;
        for (; hy <= yhi; hy += 50) {
            var sy50 = gridY(hy);
            ctx.beginPath();
            ctx.moveTo(left, sy50);
            ctx.lineTo(right, sy50);
            ctx.stroke();
        }
        // The border closes the graphic. Inset by half the stroke:
        // the centred stroke bleeds outside the rect, and at the
        // canvas edges the bleed clips — the top and bottom read a
        // pixel thin (the live report).
        var inset = root.compact ? 0.5 : 1;
        ctx.strokeRect(left + inset, top + inset, right - left - 2 * inset, bottom - top - 2 * inset);
    }

    function _stroke(ctx, row, plot) {
        // One stroke per object: the polygon when present, the centre
        // dot in the degraded mode.
        if (row.polygon != null) {
            var scene = root.plateToScene(row.polygon[0][0], row.polygon[0][1]);
            ctx.moveTo(scene.x, scene.y);
            for (var i = 1; i < row.polygon.length; ++i) {
                scene = root.plateToScene(row.polygon[i][0], row.polygon[i][1]);
                ctx.lineTo(scene.x, scene.y);
            }
            ctx.closePath();
        } else if (row.center != null) {
            var dotScene = root.plateToScene(row.center[0], row.center[1]);
            ctx.moveTo(dotScene.x + 3 * screenScaleFactor, dotScene.y);
            ctx.arc(dotScene.x, dotScene.y, 3 * screenScaleFactor, 0, Math.PI * 2);
        }
    }

    MouseArea {
        anchors.fill: parent
        // The mini maps are read-only thumbnails: no hover
        // highlighting, no exclude interaction (the live request —
        // their clicks open the pop-over instead).
        hoverEnabled: !root.compact
        onPositionChanged: function (mouse) {
            root.hoveredName = root.hitTest(mouse.x, mouse.y);
            root.objectHovered(root.hoveredName);
        }
        onExited: {
            root.hoveredName = "";
            root.objectHovered("");
        }
    }
}
