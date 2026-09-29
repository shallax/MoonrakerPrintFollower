import QtQuick 2.15
import UM 1.5 as UM
import "theme"

// The Klipper bed-mesh heat map, shared by the Information pane's
// mini widget and the pop-over detail view. `compact` drops the hover
// crosshair; the detail view snaps the cursor to probe points and
// publishes the hovered cell's coordinates and Z offset in
// `hoverText`. The plot mirrors the bed's real X/Y aspect instead of
// forcing the probe grid square.
Item {
    id: root
    // Inert; the harness asserts the rendered mesh view's presence.
    objectName: "moonrakerBedMeshMap"

    property var printer: null
    property bool compact: false
    property bool showProbePoints: false  // grid overlay of every probe position
    property real hoverX: -1  // snapped item x of the hovered probe point
    property real hoverY: -1
    property real _hoverMouseX: -1  // raw cursor, so refresh() can re-snap
    property real _hoverMouseY: -1
    property int hoverColumn: -1
    property int hoverMeshRow: -1
    property bool hoverClamped: false  // the crosshair reads a clamped (extended) value
    property string hoverText: ""
    property string tooltipText: ""
    property color outOfWindowGrey: MoonrakerTheme.outOfWindowGrey
    signal clicked

    function refresh() {
        // A live mesh publish recolours the plot under a parked
        // cursor; re-snap so the readout row and the marker ring
        // follow the NEW data instead of showing the pre-refresh Z
        // (the temperature chart re-snaps the same way).
        if (root._hoverMouseX >= 0) {
            root.snap(root._hoverMouseX, root._hoverMouseY);
        }
        meshCanvas.requestPaint();
    }

    function blendOver(colour, background, alpha) {
        // The fainter look, pre-mixed toward the theme background and
        // painted at FULL opacity: partial-alpha fills that overlap by
        // one pixel double-painted their shared edges into a visible
        // grid (a report), while an opaque overlap is
        // invisible.
        return Qt.rgba(colour.r * alpha + background.r * (1 - alpha), colour.g * alpha + background.g * (1 - alpha), colour.b * alpha + background.b * (1 - alpha), 1.0);
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
        var xMin = 0;
        var yMax = 0;
        if (root.printer != null) {
            xSpan = root.printer.bedMeshXMax - root.printer.bedMeshXMin;
            ySpan = root.printer.bedMeshYMax - root.printer.bedMeshYMin;
            xMin = root.printer.bedMeshXMin;
            yMax = root.printer.bedMeshYMax;
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
            "plotHeight": plotHeight,
            "xMin": xMin,
            "xMax": xMin + xSpan,
            "yMin": yMax - ySpan,
            "yMax": yMax,
            "xSpan": xSpan,
            "ySpan": ySpan
        };
    }

    function bedBounds() {
        // The BED-space mapping (4.2.0): the widget maps the physical
        // bed rectangle, the probed mesh sits inside it at its true
        // position. Returns null when the machine geometry is unknown
        // (the map falls back to the mesh-bounds view). printerY
        // grows away from the front, so the screen's top edge is the
        // bed's y maximum.
        if (root.printer == null) {
            return null;
        }
        var machineWidth = root.printer.bedMeshMachineWidth;
        var machineDepth = root.printer.bedMeshMachineDepth;
        if (!(machineWidth > 0) || !(machineDepth > 0)) {
            return null;
        }
        var center = root.printer.bedMeshCenterIsZero;
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

    function clampedValue(printerX, printerY, values, rows, columns, xMin, xMax, yMin, yMax) {
        // Klipper's own mesh lookup, mirrored: the bilinear sample
        // with BOTH the cell index and the fraction constrained to
        // the probed bounds (bed_mesh._get_linear_index), so a point
        // outside the mesh reads the boundary edge's interpolated
        // value — no slope extrapolation, no drop to zero.
        var u = (printerX - xMin) / (xMax - xMin) * (columns - 1);
        var v = (printerY - yMin) / (yMax - yMin) * (rows - 1);
        u = Math.max(0, Math.min(columns - 1, u));
        v = Math.max(0, Math.min(rows - 1, v));
        var column = Math.min(columns - 2, Math.floor(u));
        var row = Math.min(rows - 2, Math.floor(v));
        var fu = u - column;
        var fv = v - row;
        var a = Number(values[row * columns + column]);
        var b = Number(values[row * columns + column + 1]);
        var c = Number(values[(row + 1) * columns + column]);
        var d = Number(values[(row + 1) * columns + column + 1]);
        var top = a + (b - a) * fu;
        var bottom = c + (d - c) * fu;
        return top + (bottom - top) * fv;
    }

    function inRange(value) {
        // The heightmap range filter (a request): cells
        // outside the shared window render grey, so peaks and
        // troughs stand out while the rest reads as context.
        return root.printer == null || (value >= root.printer.bedMeshThresholdLow && value <= root.printer.bedMeshThresholdHigh);
    }

    function printerToWidget(bed, printerX, printerY) {
        // Returns [x, y] widget coordinates for a printer-space
        // point within the bed.
        var x = bed.offsetX + (printerX - bed.bedXMin) / (bed.bedXMax - bed.bedXMin) * bed.plotWidth;
        var y = bed.offsetY + (bed.bedYMax - printerY) / (bed.bedYMax - bed.bedYMin) * bed.plotHeight;
        return [x, y];
    }

    function snap(mouseX, mouseY) {
        root.hoverX = -1;
        root.hoverY = -1;
        root.hoverColumn = -1;
        root.hoverMeshRow = -1;
        root.hoverText = "";
        root.hoverClamped = false;
        if (root.printer == null || !root.printer.bedMeshAvailable) {
            return;
        }
        var rows = root.printer.bedMeshRows;
        var columns = root.printer.bedMeshColumns;
        var values = root.printer.bedMeshValues;
        if (rows < 2 || columns < 2 || values.length !== rows * columns) {
            return;
        }
        var bed = !root.compact ? root.bedBounds() : null;
        var xSpan = root.printer.bedMeshXMax - root.printer.bedMeshXMin;
        var ySpan = root.printer.bedMeshYMax - root.printer.bedMeshYMin;
        var meshCellW = xSpan / columns;
        var meshCellH = ySpan / rows;
        var column = -1;
        var screenRow = -1;
        if (bed !== null) {
            // Bed-space snapping: widget → printer coords → mesh
            // indices (the same mapping the paint uses).
            var printerX = bed.bedXMin + (mouseX - bed.offsetX) / bed.plotWidth * (bed.bedXMax - bed.bedXMin);
            var printerY = bed.bedYMax - (mouseY - bed.offsetY) / bed.plotHeight * (bed.bedYMax - bed.bedYMin);
            column = Math.floor((printerX - root.printer.bedMeshXMin) / meshCellW);
            screenRow = Math.floor((root.printer.bedMeshYMax - printerY) / meshCellH);
            if (column < 0 || column >= columns || screenRow < 0 || screenRow >= rows) {
                // Outside the probed bounds but (possibly) inside the
                // bed: the CLAMPED hover (a request) — the
                // pointer reads the value Klipper would apply there,
                // the crosshair turns orange, and the readout marks
                // the value as clamped.
                if (printerX < bed.bedXMin || printerX > bed.bedXMax || printerY < bed.bedYMin || printerY > bed.bedYMax) {
                    return;
                }
                var clamped = root.clampedValue(printerX, printerY, values, rows, columns, root.printer.bedMeshXMin, root.printer.bedMeshXMax, root.printer.bedMeshYMin, root.printer.bedMeshYMax);
                root.hoverClamped = true;
                root.hoverX = mouseX;
                root.hoverY = mouseY;
                root.hoverText = "X " + printerX.toFixed(1) + " mm   ·   Y " + printerY.toFixed(1) + " mm   ·   Height " + clamped.toFixed(3) + " mm (clamped)";
                return;
            }
        } else {
            var bounds = plotBounds();
            var cellWidth = bounds.plotWidth / columns;
            var cellHeight = bounds.plotHeight / rows;
            column = Math.floor((mouseX - bounds.offsetX) / cellWidth);
            screenRow = Math.floor((mouseY - bounds.offsetY) / cellHeight);
        }
        if (column < 0 || column >= columns || screenRow < 0 || screenRow >= rows) {
            return;
        }
        var meshRow = rows - 1 - screenRow;
        var value = Number(values[meshRow * columns + column]);
        root.hoverColumn = column;
        root.hoverMeshRow = meshRow;
        if (bed !== null) {
            var centre = root.printerToWidget(bed, root.printer.bedMeshXMin + (column + 0.5) * meshCellW, root.printer.bedMeshYMin + (meshRow + 0.5) * meshCellH);
            root.hoverX = centre[0];
            root.hoverY = centre[1];
        } else {
            var bounds = plotBounds();
            root.hoverX = bounds.offsetX + (column + 0.5) * bounds.plotWidth / columns;
            root.hoverY = bounds.offsetY + (screenRow + 0.5) * bounds.plotHeight / rows;
        }
        root.hoverText = "X " + (root.printer.bedMeshXMin + (column + 0.5) * xSpan / columns).toFixed(1) + " mm   ·   Y " + (root.printer.bedMeshYMin + (meshRow + 0.5) * ySpan / rows).toFixed(1) + " mm   ·   Height " + value.toFixed(3) + " mm";
    }

    // The tooltip sits under the mouse area so it can never steal the
    // click; it only shows when the call site supplies text.
    HoverHandler {
        id: tooltipHover1
    }
    UM.ToolTip {
        visible: tooltipHover1.hovered
        targetPoint: Qt.point(parent.width / 2, 0)
        x: 0
        y: parent.height + UM.Theme.getSize("default_margin").height
        width: UM.Theme.getSize("tooltip").width
        text: root.tooltipText
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
            // The bed-space view applies to BOTH surfaces (the
            // ruling): the Information pane's mini map and
            // the expanded detail — the Preview's overlay already
            // draws the same extension and ribbon.
            var bed = root.bedBounds();
            var xMin = root.printer.bedMeshXMin;
            var xMax = root.printer.bedMeshXMax;
            var yMin = root.printer.bedMeshYMin;
            var yMax = root.printer.bedMeshYMax;
            var meshSpanX = xMax - xMin;
            var meshSpanY = yMax - yMin;
            if (bed !== null && meshSpanX > 0 && meshSpanY > 0) {
                // The bed-space view (4.2.0, an accuracy
                // ruling): the probed cells at their true positions,
                // then ONE extra cell per side to the bed edges —
                // the corners included — sampled with Klipper's own
                // CLAMP (bed_mesh._get_linear_index constrains both
                // index and fraction), so the perimeter shows the
                // boundary values continued, never a made-up slope.
                // The extension is drawn fainter because it is the
                // boundary's continuation, not a measurement.
                var meshTopLeft = root.printerToWidget(bed, xMin, yMax);
                var meshBottomRight = root.printerToWidget(bed, xMax, yMin);
                var meshRectW = meshBottomRight[0] - meshTopLeft[0];
                var meshRectH = meshBottomRight[1] - meshTopLeft[1];
                var meshMinimum = root.printer.bedMeshMinimum;
                var meshMaximum = root.printer.bedMeshMaximum;
                var background = UM.Theme.getColor("main_background");
                var xEdges = [bed.bedXMin, xMin];
                for (var i = 1; i < columns; ++i) {
                    xEdges.push(xMin + i * meshSpanX / columns);
                }
                xEdges.push(xMax, bed.bedXMax);
                // yEdges run from the bed's back (printer yMax, the
                // screen top) down to the front (printer yMin).
                var yEdges = [bed.bedYMax, yMax];
                for (var j = 1; j < rows; ++j) {
                    yEdges.push(yMax - j * meshSpanY / rows);
                }
                yEdges.push(yMin, bed.bedYMin);
                var renderColumns = columns + 2;
                var renderRows = rows + 2;
                for (var renderRow = 0; renderRow < renderRows; ++renderRow) {
                    var cellTop = root.printerToWidget(bed, xMin, yEdges[renderRow])[1];
                    var cellBottom = root.printerToWidget(bed, xMin, yEdges[renderRow + 1])[1];
                    var cellH = Math.max(0, cellBottom - cellTop);
                    var pyCentre = (yEdges[renderRow] + yEdges[renderRow + 1]) / 2;
                    for (var renderCol = 0; renderCol < renderColumns; ++renderCol) {
                        var cellLeft = root.printerToWidget(bed, xEdges[renderCol], yMax)[0];
                        var cellRight = root.printerToWidget(bed, xEdges[renderCol + 1], yMax)[0];
                        var cellW = Math.max(0, cellRight - cellLeft);
                        var pxCentre = (xEdges[renderCol] + xEdges[renderCol + 1]) / 2;
                        var measured = renderRow >= 1 && renderRow <= rows && renderCol >= 1 && renderCol <= columns;
                        var sample = root.clampedValue(pxCentre, pyCentre, values, rows, columns, xMin, xMax, yMin, yMax);
                        var cellColour = root.inRange(sample) ? root.colorFor(sample, meshMinimum, meshMaximum) : root.outOfWindowGrey;
                        ctx.fillStyle = root.blendOver(cellColour, background, measured ? 0.58 : 0.28);
                        if (cellW > 0 && cellH > 0) {
                            ctx.fillRect(cellLeft, cellTop, cellW + 1, cellH + 1);
                        }
                    }
                }
                ctx.globalAlpha = 1.0;
                // The measured-bounds outline (the Preview's neon
                // orange, its 2D form).
                ctx.strokeStyle = MoonrakerTheme.neonOrange;
                ctx.globalAlpha = 0.94;
                ctx.lineWidth = 2 * screenScaleFactor;
                ctx.strokeRect(meshTopLeft[0], meshTopLeft[1], meshRectW, meshRectH);
                ctx.globalAlpha = 1.0;
                // Probe-point overlay at the measured cell centres.
                if (root.showProbePoints) {
                    ctx.fillStyle = UM.Theme.getColor("text");
                    ctx.globalAlpha = 0.8;
                    var cellWp = meshRectW / columns;
                    var cellHp = meshRectH / rows;
                    for (var pr = 0; pr < rows; ++pr) {
                        for (var pc = 0; pc < columns; ++pc) {
                            ctx.beginPath();
                            ctx.arc(meshTopLeft[0] + (pc + 0.5) * cellWp, meshTopLeft[1] + (pr + 0.5) * cellHp, Math.max(1.0, Math.min(2.0, Math.min(cellWp, cellHp) * 0.06)), 0, 2 * Math.PI);
                            ctx.fill();
                        }
                    }
                    ctx.globalAlpha = 1.0;
                }
            } else {
                var bounds = root.plotBounds();
                var cellWidth = bounds.plotWidth / columns;
                var cellHeight = bounds.plotHeight / rows;
                for (var screenRow = 0; screenRow < rows; ++screenRow) {
                    var meshRow = rows - 1 - screenRow;
                    for (var column = 0; column < columns; ++column) {
                        var value = Number(values[meshRow * columns + column]);
                        ctx.fillStyle = root.inRange(value) ? root.colorFor(value, root.printer.bedMeshMinimum, root.printer.bedMeshMaximum) : root.outOfWindowGrey;
                        ctx.fillRect(bounds.offsetX + column * cellWidth, bounds.offsetY + screenRow * cellHeight, cellWidth + 1, cellHeight + 1);
                    }
                }
                // Probe-point overlay: a dot at every probe position so the
                // sampling grid is visible (detail view, toggled).
                if (root.showProbePoints) {
                    ctx.fillStyle = UM.Theme.getColor("text");
                    ctx.globalAlpha = 0.8;
                    for (var pr = 0; pr < rows; ++pr) {
                        for (var pc = 0; pc < columns; ++pc) {
                            ctx.beginPath();
                            ctx.arc(bounds.offsetX + (pc + 0.5) * cellWidth, bounds.offsetY + (pr + 0.5) * cellHeight, Math.max(1.0, Math.min(2.0, Math.min(cellWidth, cellHeight) * 0.06)), 0, 2 * Math.PI);
                            ctx.fill();
                        }
                    }
                    ctx.globalAlpha = 1.0;
                }
            }
            // Hover crosshair: a ring and tick marks around the snapped
            // probe point (detail view only). A CLAMPED hover — the
            // pointer over the extended perimeter — draws the same
            // crosshair in the neon orange so the value's status is
            // visible at the pointer, not just in the readout.
            if (!root.compact && (root.hoverColumn >= 0 || root.hoverClamped)) {
                ctx.strokeStyle = root.hoverClamped ? MoonrakerTheme.neonOrange : UM.Theme.getColor("text");
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
                root._hoverMouseX = mouse.x;
                root._hoverMouseY = mouse.y;
                root.snap(mouse.x, mouse.y);
            }
        }
        onExited: {
            if (!root.compact) {
                root._hoverMouseX = -1;
                root._hoverMouseY = -1;
                root.snap(-1, -1);
            }
        }
        onClicked: root.clicked()
    }

    onPrinterChanged: meshCanvas.requestPaint()
    onShowProbePointsChanged: meshCanvas.requestPaint()
    onHoverXChanged: {
        if (!root.compact) {
            meshCanvas.requestPaint();
        }
    }
}
