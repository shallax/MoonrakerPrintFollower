import QtQuick 2.15
import UM 1.5 as UM

// The Klipper bed-mesh heat map, shared by the Information pane's
// mini widget and the pop-over detail view. `compact` drops the hover
// crosshair; the detail view snaps the cursor to probe points and
// publishes the hovered cell's coordinates and Z offset in
// `hoverText`. The plot mirrors the bed's real X/Y aspect instead of
// forcing the probe grid square.
Item {
    id: root

    property var printer: null
    property bool compact: false
    property real hoverX: -1  // snapped item x of the hovered probe point
    property real hoverY: -1
    property int hoverColumn: -1
    property int hoverMeshRow: -1
    property string hoverText: ""
    property string tooltipText: ""
    signal clicked

    function refresh() {
        meshCanvas.requestPaint();
    }

    function colorFor(value, minimum, maximum) {
        var limit = Math.max(Math.abs(minimum), Math.abs(maximum), 0.000000001);
        var t = Math.max(0.0, Math.min(1.0, 0.5 + 0.5 * value / limit));
        var stops = [[0.00, 0.10, 0.28, 0.95], [0.25, 0.00, 0.72, 1.00], [0.50, 0.20, 0.86, 0.38], [0.75, 1.00, 0.82, 0.12], [1.00, 0.92, 0.16, 0.12]];
        for (var i = 1; i < stops.length; ++i) {
            if (t <= stops[i][0]) {
                var left = stops[i - 1];
                var right = stops[i];
                var f = (t - left[0]) / Math.max(0.000000001, right[0] - left[0]);
                return Qt.rgba(left[1] + (right[1] - left[1]) * f, left[2] + (right[2] - left[2]) * f, left[3] + (right[3] - left[3]) * f, 1.0);
            }
        }
        return Qt.rgba(0.92, 0.16, 0.12, 1.0);
    }

    function plotBounds() {
        var xSpan = 0;
        var ySpan = 0;
        if (root.printer != null) {
            xSpan = root.printer.bedMeshXMax - root.printer.bedMeshXMin;
            ySpan = root.printer.bedMeshYMax - root.printer.bedMeshYMin;
        }
        var plotWidth = width;
        var plotHeight = height;
        var offsetX = 0;
        var offsetY = 0;
        if (xSpan > 0 && ySpan > 0) {
            var target = xSpan / ySpan;
            if (width / height > target) {
                plotWidth = height * target;
                offsetX = (width - plotWidth) / 2;
            } else {
                plotHeight = width / target;
                offsetY = (height - plotHeight) / 2;
            }
        }
        return {
            "offsetX": offsetX,
            "offsetY": offsetY,
            "plotWidth": plotWidth,
            "plotHeight": plotHeight
        };
    }

    function snap(mouseX, mouseY) {
        root.hoverX = -1;
        root.hoverY = -1;
        root.hoverColumn = -1;
        root.hoverMeshRow = -1;
        root.hoverText = "";
        if (root.printer == null || !root.printer.bedMeshAvailable) {
            return;
        }
        var rows = root.printer.bedMeshRows;
        var columns = root.printer.bedMeshColumns;
        var values = root.printer.bedMeshValues;
        if (rows < 2 || columns < 2 || values.length !== rows * columns) {
            return;
        }
        var bounds = plotBounds();
        var cellWidth = bounds.plotWidth / columns;
        var cellHeight = bounds.plotHeight / rows;
        var column = Math.floor((mouseX - bounds.offsetX) / cellWidth);
        var screenRow = Math.floor((mouseY - bounds.offsetY) / cellHeight);
        if (column < 0 || column >= columns || screenRow < 0 || screenRow >= rows) {
            return;
        }
        var meshRow = rows - 1 - screenRow;
        var value = Number(values[meshRow * columns + column]);
        var xSpan = root.printer.bedMeshXMax - root.printer.bedMeshXMin;
        var ySpan = root.printer.bedMeshYMax - root.printer.bedMeshYMin;
        root.hoverColumn = column;
        root.hoverMeshRow = meshRow;
        root.hoverX = bounds.offsetX + (column + 0.5) * cellWidth;
        root.hoverY = bounds.offsetY + (screenRow + 0.5) * cellHeight;
        root.hoverText = "X " + (root.printer.bedMeshXMin + (column + 0.5) * xSpan / columns).toFixed(1) + " mm   ·   Y " + (root.printer.bedMeshYMin + (meshRow + 0.5) * ySpan / rows).toFixed(1) + " mm   ·   Z " + value.toFixed(3) + " mm";
    }

    // The tooltip sits under the mouse area so it can never steal the
    // click; it only shows when the call site supplies text.
    UM.TooltipArea {
        anchors.fill: parent
        visible: root.tooltipText.length > 0
        text: root.tooltipText
        acceptedButtons: Qt.NoButton
    }

    Canvas {
        id: meshCanvas
        anchors.fill: parent

        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            if (root.printer == null || !root.printer.bedMeshAvailable) {
                return;
            }
            var rows = root.printer.bedMeshRows;
            var columns = root.printer.bedMeshColumns;
            var values = root.printer.bedMeshValues;
            if (rows < 2 || columns < 2 || values.length !== rows * columns) {
                return;
            }
            var bounds = root.plotBounds();
            var cellWidth = bounds.plotWidth / columns;
            var cellHeight = bounds.plotHeight / rows;
            for (var screenRow = 0; screenRow < rows; ++screenRow) {
                var meshRow = rows - 1 - screenRow;
                for (var column = 0; column < columns; ++column) {
                    var value = Number(values[meshRow * columns + column]);
                    ctx.fillStyle = root.colorFor(value, root.printer.bedMeshMinimum, root.printer.bedMeshMaximum);
                    ctx.fillRect(bounds.offsetX + column * cellWidth, bounds.offsetY + screenRow * cellHeight, cellWidth + 1, cellHeight + 1);
                }
            }
            // Hover crosshair: a ring and tick marks around the snapped
            // probe point (detail view only).
            if (!root.compact && root.hoverColumn >= 0) {
                ctx.strokeStyle = UM.Theme.getColor("text");
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.arc(root.hoverX, root.hoverY, 6, 0, 2 * Math.PI);
                ctx.stroke();
                ctx.beginPath();
                ctx.moveTo(root.hoverX - 9, root.hoverY);
                ctx.lineTo(root.hoverX + 9, root.hoverY);
                ctx.moveTo(root.hoverX, root.hoverY - 9);
                ctx.lineTo(root.hoverX, root.hoverY + 9);
                ctx.stroke();
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: !root.compact
        cursorShape: root.compact ? Qt.PointingHandCursor : Qt.CrossCursor
        onPositionChanged: {
            if (!root.compact) {
                root.snap(mouse.x, mouse.y);
            }
        }
        onExited: {
            if (!root.compact) {
                root.snap(-1, -1);
            }
        }
        onClicked: root.clicked()
    }

    onPrinterChanged: meshCanvas.requestPaint()
    onHoverXChanged: {
        if (!root.compact) {
            meshCanvas.requestPaint();
        }
    }
}
