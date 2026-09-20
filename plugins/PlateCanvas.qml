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
    // The zoom/pan view, owned by the follower face and applied by
    // THIS canvas's grid (the bottom raster must move with the
    // layers): identity elsewhere — the picker never zooms.
    property real viewScale: 1.0
    property real viewPanX: 0.0
    property real viewPanY: 0.0
    // The grid's threaded raster reads the view through a var carrier
    // (worker paints see var properties fresh, primitives stale — the
    // offscreen-harness repaint findings; the face does the same). The
    // carrier is geometry-in only: the pan is the canvas ITEM's
    // translation, never a paint input (the pan-agnostic rasters).
    property var _view: ({
            scale: 1.0
        })

    function _publishView() {
        root._view = {
            scale: root.viewScale
        };
    }
    property string hoveredName: ""
    property color halo: UM.Theme.getColor("main_background")
    signal objectHovered(string name)

    // The degraded hit radius in BED millimetres — a bed-space
    // constant, never pixels (the popover resizes; the review's rule).
    property real hitRadiusMm: 18.0

    property var _plot: null

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
        var plotWidth = width;
        var plotHeight = height;
        var offsetX = 0;
        var offsetY = 0;
        var target = machineWidth / machineDepth;
        if (width / height > target) {
            plotWidth = height * target;
            offsetX = (width - plotWidth) / 2;
        } else {
            plotHeight = width / target;
            offsetY = (height - plotHeight) / 2;
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

    function _replot() {
        // Geometry once per resize, never per paint.
        var bed = _bedBounds();
        if (bed == null) {
            root._plot = null;
            return;
        }
        root._plot = {
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

    function hitTest(x, y) {
        // Nearest CENTRE wins (the live polygons are overlapping
        // bounding boxes — the deterministic precedence the domain
        // round ruled). One implementation for outlines and dots.
        var plot = root._plot;
        if (plot == null || root.plate == null) {
            return "";
        }
        var radiusPx = root.hitRadiusMm * Math.max(Math.abs(plot.sx), Math.abs(plot.sy));
        var best = "";
        var bestDist = Infinity;
        for (var i = 0; i < root.plate.objects.length; ++i) {
            var row = root.plate.objects[i];
            if (row.center == null) {
                continue;
            }
            var scene = root.plateToScene(row.center[0], row.center[1]);
            var d = (scene.x - x) * (scene.x - x) + (scene.y - y) * (scene.y - y);
            if (d < bestDist) {
                bestDist = d;
                best = row.name;
            }
        }
        return bestDist <= radiusPx * radiusPx ? best : "";
    }

    onWidthChanged: {
        _replot();
        plateCanvas.requestPaint();
    }
    onHeightChanged: {
        _replot();
        plateCanvas.requestPaint();
    }
    Component.onCompleted: {
        _replot();
        _publishView();
        plateCanvas.requestPaint();
    }
    onPlateChanged: plateCanvas.requestPaint()
    onHoveredNameChanged: plateCanvas.requestPaint()
    onViewScaleChanged: {
        _publishView();
        plateCanvas.requestPaint();
    }
    // A pan only translates the raster item: the grid is painted once
    // and never re-drawn for the view (the pan-agnostic rasters).

    // The threaded image-backed canvas (the TemperatureChart
    // doctrine): the paint callback touches only the snapshot it is
    // handed, never a live QML item.
    Canvas {
        id: plateCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        // The view's pan as a scene-graph translation (the pan-agnostic
        // rasters): the picker never pans, so this is the identity
        // there.
        transform: Translate {
            x: root.viewPanX
            y: root.viewPanY
        }
        onPaint: {
            var ctx = getContext("2d");
            var plot = root._plot;
            ctx.reset();
            if (plot == null) {
                return;
            }
            _drawGrid(ctx, plot);
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
                var blocked = row.excluded === true && row.restoreAllowed === false;
                // The live palette ruling: current reads as Cura's
                // highlight blue, the passed/printed objects as the
                // green, the pending as the plain text ink.
                var ink = row.excluded === true ? (blocked ? MoonrakerTheme.outOfWindowGrey : MoonrakerTheme.dangerRed) : row.current === true ? UM.Theme.getColor("primary") : row.passed === true ? MoonrakerTheme.plateCurrent : UM.Theme.getColor("text");
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
                if (blocked) {
                    ctx.setLineDash([4, 3]);
                }
                ctx.beginPath();
                _stroke(ctx, row, plot);
                ctx.stroke();
                ctx.setLineDash([]);
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
        // The pan is the item's translation, never a coordinate here.
        var left = bed.offsetX * root._view.scale;
        var top = bed.offsetY * root._view.scale;
        var right = left + bed.plotWidth * root._view.scale;
        var bottom = top + bed.plotHeight * root._view.scale;
        function gridX(bedX) {
            return root.plateToScene(bedX, bed.bedYMin).x * root._view.scale;
        }
        function gridY(bedY) {
            return root.plateToScene(bed.bedXMin, bedY).y * root._view.scale;
        }
        if (!root.compact) {
            ctx.lineWidth = 1;
            ctx.strokeStyle = thin;
            var gx = Math.ceil(bed.bedXMin / 10) * 10;
            for (; gx <= bed.bedXMax; gx += 10) {
                if (Math.round(gx) % 50 === 0) {
                    continue;
                }
                var sx = gridX(gx);
                ctx.beginPath();
                ctx.moveTo(sx, top);
                ctx.lineTo(sx, bottom);
                ctx.stroke();
            }
            var gy = Math.ceil(bed.bedYMin / 10) * 10;
            for (; gy <= bed.bedYMax; gy += 10) {
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
        var hx = Math.ceil(bed.bedXMin / 50) * 50;
        for (; hx <= bed.bedXMax; hx += 50) {
            var sx50 = gridX(hx);
            ctx.beginPath();
            ctx.moveTo(sx50, top);
            ctx.lineTo(sx50, bottom);
            ctx.stroke();
        }
        var hy = Math.ceil(bed.bedYMin / 50) * 50;
        for (; hy <= bed.bedYMax; hy += 50) {
            var sy50 = gridY(hy);
            ctx.beginPath();
            ctx.moveTo(left, sy50);
            ctx.lineTo(right, sy50);
            ctx.stroke();
        }
        // The border closes the graphic.
        ctx.strokeRect(left, top, right - left, bottom - top);
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
