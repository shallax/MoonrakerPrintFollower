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
    property var dot: null            // {x, y, valid} in bed mm
    property bool compact: false
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
            // top edge (printer y grows away from the front).
            "sy": -bed.plotHeight / (bed.bedYMax - bed.bedYMin)
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
            "y": plot.bed.offsetY + (y - plot.bed.bedYMin) * plot.sy
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
        plateCanvas.requestPaint();
    }
    onPlateChanged: plateCanvas.requestPaint()
    onDotChanged: plateCanvas.requestPaint()

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
            if (plot == null || root.plate == null) {
                return;
            }
            var plate = root.plate;
            for (var i = 0; i < plate.objects.length; ++i) {
                var row = plate.objects[i];
                // The 2 px background halo under every stroke: state
                // ink sits on arbitrary feature ink otherwise, and no
                // contrast value holds without it (the UX ruling).
                var blocked = row.excluded === true && row.restoreAllowed === false;
                var ink = row.excluded === true ? (blocked ? MoonrakerTheme.outOfWindowGrey : MoonrakerTheme.dangerRed) : row.current === true ? MoonrakerTheme.plateCurrent : UM.Theme.getColor("text");
                var widthPx = row.current === true ? 2.5 : 1.5;
                ctx.lineWidth = widthPx + 4;
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
                // The current object's ring badge (the shape channel:
                // no state is colour-only).
                if (row.current === true && row.center != null) {
                    var scene = root.plateToScene(row.center[0], row.center[1]);
                    ctx.lineWidth = 2;
                    ctx.strokeStyle = MoonrakerTheme.plateCurrent;
                    ctx.beginPath();
                    ctx.arc(scene.x, scene.y, 6 * screenScaleFactor, 0, Math.PI * 2);
                    ctx.stroke();
                }
            }
        }
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

    // The toolhead dot: scene-graph geometry (a Rectangle binding),
    // never a canvas repaint — the 1 s position publish moves it.
    Rectangle {
        id: toolheadDot
        width: 7 * screenScaleFactor
        height: width
        radius: width / 2
        color: MoonrakerTheme.plateDot
        border.color: UM.Theme.getColor("main_background")
        border.width: 2
        visible: root._plot != null && root.dot != null && root.dot.valid === true
        x: visible ? root.plateToScene(root.dot.x, root.dot.y).x - width / 2 : 0
        y: visible ? root.plateToScene(root.dot.x, root.dot.y).y - height / 2 : 0
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
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
