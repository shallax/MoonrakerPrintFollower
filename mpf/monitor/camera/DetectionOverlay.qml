import QtQuick 2.15
import "../../resources/theme"

Canvas {
    id: root
    objectName: "detectionOverlay"
    property var printerModel: null
    property real viewZoom: 1
    readonly property bool editing: printerModel != null && printerModel.detectionEditingRegions
    property var draftRegions: []
    property int selectedRegion: -1
    property int selectedVertex: -1
    property bool draggingVertex: false
    property bool dragRemembered: false
    property var dragStartPoint: [0, 0]
    property var dragOffset: [0, 0]
    property var undoHistory: []
    property string errorText: ""
    readonly property bool canDeleteVertex: selectedRegion >= 0 && selectedRegion < draftRegions.length && selectedVertex >= 0 && selectedVertex < draftRegions[selectedRegion].length && draftRegions[selectedRegion].length > 3
    readonly property var regions: editing ? draftRegions : printerModel != null ? printerModel.detectionRegions : []
    readonly property var boxes: printerModel != null && printerModel.detectionShowBoxes ? printerModel.detectionBoxes : []

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }
    function begin() {
        draftRegions = clone(printerModel.detectionRegions);
        selectedRegion = draftRegions.length ? 0 : -1;
        selectedVertex = -1;
        undoHistory = [];
        draggingVertex = false;
        errorText = "";
        requestPaint();
    }
    function remember() {
        undoHistory = undoHistory.slice(-63).concat([
            {
                regions: clone(draftRegions),
                region: selectedRegion,
                vertex: selectedVertex
            }
        ]);
    }
    function apply(candidate) {
        errorText = printerModel.validateDetectionRegions(candidate);
        if (errorText !== "")
            return false;
        draftRegions = candidate;
        requestPaint();
        return true;
    }
    function addRectangle() {
        if (draftRegions.length >= 4) {
            errorText = "Four-region limit reached. Delete a shape to add another.";
            return;
        }
        remember();
        var inset = .20 + draftRegions.length * .05;
        draftRegions = draftRegions.concat([[[inset, inset], [inset + .45, inset], [inset + .45, inset + .45], [inset, inset + .45]]]);
        selectedRegion = draftRegions.length - 1;
        selectedVertex = -1;
        errorText = "";
        requestPaint();
    }
    function undo() {
        if (!undoHistory.length)
            return;
        var previous = undoHistory[undoHistory.length - 1];
        undoHistory = undoHistory.slice(0, -1);
        draftRegions = previous.regions;
        selectedRegion = previous.region;
        selectedVertex = previous.vertex;
        draggingVertex = false;
        errorText = "";
        requestPaint();
    }
    function deleteVertex() {
        if (!canDeleteVertex)
            return;
        var candidate = clone(draftRegions);
        candidate[selectedRegion].splice(selectedVertex, 1);
        if (printerModel.validateDetectionRegions(candidate) !== "") {
            errorText = "Removing this vertex would make the shape invalid.";
            return;
        }
        remember();
        apply(candidate);
        selectedVertex = -1;
    }
    function deleteRegion() {
        if (selectedRegion < 0)
            return;
        remember();
        var candidate = clone(draftRegions);
        candidate.splice(selectedRegion, 1);
        draftRegions = candidate;
        selectedRegion = draftRegions.length ? Math.min(selectedRegion, draftRegions.length - 1) : -1;
        selectedVertex = -1;
        errorText = "";
        requestPaint();
    }
    function resetRegions() {
        remember();
        draftRegions = [];
        selectedRegion = selectedVertex = -1;
        errorText = "";
        requestPaint();
    }
    function distance(x, y, point) {
        var dx = (x - point[0]) * width * viewZoom;
        var dy = (y - point[1]) * height * viewZoom;
        return Math.sqrt(dx * dx + dy * dy);
    }
    function inside(x, y, points) {
        var result = false;
        for (var i = 0, j = points.length - 1; i < points.length; j = i++) {
            var a = points[i], b = points[j];
            if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0])
                result = !result;
        }
        return result;
    }
    function press(x, y) {
        draggingVertex = false;
        dragRemembered = false;
        if (!editing || x < 0 || y < 0 || x > 1 || y > 1)
            return;
        // Selected handles take priority when shapes overlap.
        var order = [];
        if (selectedRegion >= 0)
            order.push(selectedRegion);
        for (var r = draftRegions.length - 1; r >= 0; --r)
            if (r !== selectedRegion)
                order.push(r);
        for (var n = 0; n < order.length; ++n) {
            var region = order[n];
            var points = draftRegions[region];
            for (var v = 0; v < points.length; ++v) {
                if (distance(x, y, points[v]) <= 11) {
                    selectedRegion = region;
                    selectedVertex = v;
                    draggingVertex = true;
                    dragStartPoint = [x, y];
                    dragOffset = [points[v][0] - x, points[v][1] - y];
                    errorText = "";
                    requestPaint();
                    return;
                }
            }
            if (region === selectedRegion && insertSelectedAt(x, y))
                return;
        }
        selectedRegion = -1;
        selectedVertex = -1;
        for (var shape = draftRegions.length - 1; shape >= 0; --shape) {
            if (inside(x, y, draftRegions[shape])) {
                selectedRegion = shape;
                break;
            }
        }
        errorText = "";
        requestPaint();
    }
    function insertSelectedAt(x, y) {
        if (selectedRegion >= 0 && selectedRegion < draftRegions.length) {
            var selected = draftRegions[selectedRegion];
            for (var edge = 0; edge < selected.length; ++edge) {
                var a = selected[edge], b = selected[(edge + 1) % selected.length];
                var midpoint = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
                if (distance(x, y, midpoint) <= 10) {
                    if (selected.length >= 32) {
                        errorText = "32-vertex limit reached. Remove a vertex before adding another.";
                        return true;
                    }
                    remember();
                    var candidate = clone(draftRegions);
                    candidate[selectedRegion].splice(edge + 1, 0, midpoint);
                    if (apply(candidate)) {
                        selectedVertex = edge + 1;
                        draggingVertex = true;
                        dragOffset = [0, 0];
                    }
                    dragRemembered = true;
                    return true;
                }
            }
        }
        return false;
    }
    function move(x, y) {
        if (!draggingVertex || selectedRegion < 0 || selectedVertex < 0)
            return;
        if (!dragRemembered && distance(x, y, dragStartPoint) < 3)
            return;
        var candidate = clone(draftRegions);
        candidate[selectedRegion][selectedVertex] = [Math.max(0, Math.min(1, x + dragOffset[0])), Math.max(0, Math.min(1, y + dragOffset[1]))];
        errorText = printerModel.validateDetectionRegions(candidate);
        if (errorText !== "" || JSON.stringify(candidate) === JSON.stringify(draftRegions))
            return;
        if (!dragRemembered) {
            remember();
            dragRemembered = true;
        }
        draftRegions = candidate;
        requestPaint();
    }
    function release() {
        draggingVertex = false;
    }
    onEditingChanged: {
        if (editing)
            begin();
        else {
            draggingVertex = false;
            requestPaint();
        }
    }
    onRegionsChanged: requestPaint()
    onBoxesChanged: requestPaint()
    onSelectedRegionChanged: requestPaint()
    onSelectedVertexChanged: requestPaint()
    onViewZoomChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()
    Connections {
        target: root.printerModel
        function onDetectionChanged() {
            root.requestPaint();
        }
    }
    onPaint: {
        var ctx = getContext("2d");
        ctx.reset();
        function path(polygons) {
            ctx.beginPath();
            for (var i = 0; i < polygons.length; ++i) {
                var points = polygons[i];
                if (!points.length)
                    continue;
                ctx.moveTo(points[0][0] * width, points[0][1] * height);
                for (var j = 1; j < points.length; ++j)
                    ctx.lineTo(points[j][0] * width, points[j][1] * height);
                ctx.closePath();
            }
        }
        if (editing && regions.length) {
            ctx.fillStyle = MoonrakerTheme.detectionExcluded;
            ctx.fillRect(0, 0, width, height);
            ctx.globalCompositeOperation = "destination-out";
            for (var region = 0; region < regions.length; ++region) {
                path([regions[region]]);
                ctx.fill();
            }
            ctx.globalCompositeOperation = "source-over";
        }
        if (!editing) {
            ctx.save();
            if (regions.length) {
                path(regions);
                ctx.clip();
            }
            ctx.strokeStyle = MoonrakerTheme.detectionBox;
            ctx.lineWidth = 2 / viewZoom;
            for (var b = 0; b < boxes.length; ++b) {
                var box = boxes[b];
                ctx.strokeRect(box.x * width, box.y * height, box.width * width, box.height * height);
            }
            ctx.restore();
        }
        ctx.strokeStyle = MoonrakerTheme.detectionRegion;
        ctx.lineWidth = 2 / viewZoom;
        path(regions);
        ctx.stroke();
        if (editing) {
            function handle(point, radius, filled) {
                ctx.beginPath();
                ctx.arc(point[0] * width, point[1] * height, radius / viewZoom, 0, 2 * Math.PI);
                ctx.fillStyle = filled ? MoonrakerTheme.detectionRegion : MoonrakerTheme.cameraLivePill;
                ctx.fill();
                ctx.stroke();
            }
            for (var r = 0; r < regions.length; ++r) {
                for (var v = 0; v < regions[r].length; ++v) {
                    handle(regions[r][v], r === selectedRegion && v === selectedVertex ? 7 : 5, r === selectedRegion);
                    if (r === selectedRegion) {
                        var next = regions[r][(v + 1) % regions[r].length];
                        handle([(regions[r][v][0] + next[0]) / 2, (regions[r][v][1] + next[1]) / 2], 3.5, false);
                    }
                }
            }
        }
    }
}
