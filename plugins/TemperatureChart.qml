import QtQuick 2.15
import UM 1.5 as UM
import "theme"

// The Monitor's temperature history plot: solid actuals, translucent
// target BANDS whose top edge is the setpoint marker (the
// ruling — dashed targets got chopped by the actual line at steady
// state), translucent power areas on a 0-100% second axis, and a
// hover cursor snapped to the 1 s sample grid with per-series values.
// Two canvases: the data canvas repaints only when the payload or
// geometry changes; the overlay repaints on hover and draws nothing
// but the cursor, so mouse motion never re-rasterises the polylines.
Item {
    id: root

    property var chart: ({
            "series": [],
            "showTargets": true,
            "showPower": true,
            "palette": [],
            "filling": false,
            "wallOrigin": 0
        })
    property bool compact: false
    property real hoverX: -1  // item x of the hover cursor; -1 = none
    property string hoverClock: ""  // HH:MM:SS at the snapped cursor
    property var hoverValues: []    // [{label, color, text}] per visible series
    property point hoverCursor: Qt.point(-1, -1)  // raw cursor in item coordinates
    property string tooltipText: ""
    signal clicked

    property real _minTemp: 0
    property real _maxTemp: 100
    property real _minElapsed: 0
    property real _maxElapsed: 1
    property int _hoverSnap: -1  // cursor elapsed rounded to the sample grid
    property var _hoverMarks: []  // [{elapsed, temperature, color}] per hovering series

    function _seriesBounds(series) {
        // The payload carries each series' render domain (the history
        // knows it while the samples are built), so the chart never
        // walks the samples it is about to draw. A payload built
        // without that metadata — a preview, a test — gets the scanned
        // equivalent.
        if (series.bounds !== undefined) {
            return series.bounds;
        }
        var minTemp = Infinity;
        var maxTemp = -Infinity;
        var minElapsed = Infinity;
        var maxElapsed = -Infinity;
        var points = series.points;
        for (var j = 0; j < points.length; ++j) {
            minTemp = Math.min(minTemp, points[j][1]);
            maxTemp = Math.max(maxTemp, points[j][1]);
            minElapsed = Math.min(minElapsed, points[j][0]);
            maxElapsed = Math.max(maxElapsed, points[j][0]);
        }
        var bounds = {
            "tempMin": minTemp,
            "tempMax": maxTemp,
            "elapsedMin": minElapsed,
            "elapsedMax": maxElapsed
        };
        // Lit setpoints only, and only the extremes: the caller decides
        // whether they join the domain at all.
        var minTarget = Infinity;
        var maxTarget = -Infinity;
        var targets = series.targets;
        for (var t = 0; t < targets.length; ++t) {
            for (var u = 0; u < targets[t].length; ++u) {
                if (targets[t][u][1] > 0) {
                    minTarget = Math.min(minTarget, targets[t][u][1]);
                    maxTarget = Math.max(maxTarget, targets[t][u][1]);
                }
            }
        }
        if (minTarget !== Infinity) {
            bounds.targetMin = minTarget;
            bounds.targetMax = maxTarget;
        }
        return bounds;
    }

    function _recomputeBounds() {
        var minTemp = Infinity;
        var maxTemp = -Infinity;
        var minElapsed = Infinity;
        var maxElapsed = -Infinity;
        var series = chart.series !== undefined ? chart.series : [];
        var showTargets = chart.showTargets;
        for (var i = 0; i < series.length; ++i) {
            if (!series[i].visible) {
                continue;
            }
            var bounds = _seriesBounds(series[i]);
            if (bounds.tempMin !== undefined && bounds.tempMin !== null) {
                minTemp = Math.min(minTemp, bounds.tempMin);
                maxTemp = Math.max(maxTemp, bounds.tempMax);
                minElapsed = Math.min(minElapsed, bounds.elapsedMin);
                maxElapsed = Math.max(maxElapsed, bounds.elapsedMax);
            }
            // Setpoints join the domain so a droop to a new target is
            // visible while it happens, not only once the actual nearly
            // arrives (target > 0 = heater on) — and only while targets
            // are shown: hiding them must not leave the domain inflated
            // by a far-away setpoint.
            if (showTargets && bounds.targetMin !== undefined && bounds.targetMin !== null) {
                minTemp = Math.min(minTemp, bounds.targetMin);
                maxTemp = Math.max(maxTemp, bounds.targetMax);
            }
        }
        if (!isFinite(minTemp)) {
            minTemp = 0;
            maxTemp = 100;
        }
        if (!isFinite(minElapsed)) {
            minElapsed = 0;
            maxElapsed = 1;
        }
        var span = maxTemp - minTemp;
        if (span < 1) {
            // A flat line still needs a readable band around it.
            minTemp -= 5;
            maxTemp += 5;
        } else {
            minTemp -= span * 0.08;
            maxTemp += span * 0.08;
        }
        _minTemp = minTemp;
        _maxTemp = maxTemp;
        _minElapsed = minElapsed;
        _maxElapsed = maxElapsed;
    }

    function _rightGutter() {
        // The 0-100% power labels live OUTSIDE the plot, in a reserved
        // right margin (the ruling: chips painted over the
        // data looked janky, and the labels must never collide with the
        // lines). The plot domain shrinks to fit; the compact sparkline
        // keeps its full width.
        return (root.chart.showPower && !root.compact) ? 34 : 0;
    }

    function _xFor(elapsed) {
        if (_maxElapsed <= _minElapsed) {
            return 0;
        }
        return (elapsed - _minElapsed) / (_maxElapsed - _minElapsed) * (width - root._rightGutter());
    }

    function _plotBottom() {
        // Full-size charts reserve a strip below the plot for the
        // HH:MM ticks — with breathing room, not butted against the
        // plot — and so they never paint over the bottom temperature
        // label; the mini sparkline uses the whole height.
        return root.compact ? height : Math.max(1, height - 22);
    }

    function _yFor(value) {
        if (_maxTemp <= _minTemp) {
            return _plotBottom();
        }
        return _plotBottom() - (value - _minTemp) / (_maxTemp - _minTemp) * _plotBottom();
    }

    function _strokeColor(seriesColor, alpha) {
        var base = String(seriesColor || MoonrakerTheme.seriesDefault);
        if (base.charAt(0) === "#" && base.length === 7) {
            return Qt.rgba(parseInt(base.substr(1, 2), 16) / 255, parseInt(base.substr(3, 2), 16) / 255, parseInt(base.substr(5, 2), 16) / 255, alpha);
        }
        return Qt.rgba(0.5, 0.5, 0.5, alpha);
    }

    function _nearestIndex(points, elapsed) {
        // The sample nearest the snapped cursor time, within 1.5 s;
        // -1 means the series has nothing at that instant (it started
        // late or ended early) and the readout shows an honest "—".
        // Samples ascend by elapsed, so the nearest one is one of the
        // two straddling the cursor: a binary search, not a scan — this
        // runs per visible series on every mouse move.
        var count = points.length;
        if (count === 0) {
            return -1;
        }
        var low = 0;
        var high = count;
        while (low < high) {
            var middle = (low + high) >> 1;
            if (points[middle][0] < elapsed) {
                low = middle + 1;
            } else {
                high = middle;
            }
        }
        var best = low < count ? low : count - 1;
        if (low > 0) {
            // A tie keeps the earlier sample, as the scan did.
            if (Math.abs(points[low - 1][0] - elapsed) <= Math.abs(points[best][0] - elapsed)) {
                best = low - 1;
            }
            // Samples can share an elapsed value: the first of the run is
            // the one a scan would have returned.
            while (best > 0 && points[best - 1][0] === points[best][0]) {
                best -= 1;
            }
        }
        return Math.abs(points[best][0] - elapsed) <= 1.5 ? best : -1;
    }

    function _clockText(elapsed) {
        var wall = chart.wallOrigin;
        if (wall === undefined || wall === null) {
            return "";
        }
        var total = Math.round(wall + elapsed);
        var hours = Math.floor(total / 3600) % 24;
        var minutes = Math.floor((total % 3600) / 60);
        var seconds = Math.floor(total % 60);
        var text = hours < 10 ? "0" + hours : "" + hours;
        text += ":" + (minutes < 10 ? "0" + minutes : "" + minutes);
        text += ":" + (seconds < 10 ? "0" + seconds : "" + seconds);
        return text;
    }

    function _clockTextMinutes(elapsed) {
        // Axis ticks carry HH:MM only: the seconds noise ("14:03:05")
        // belongs to the hover readout, not the tick strip (panel UX P3).
        var text = _clockText(elapsed);
        return text.length >= 5 ? text.slice(0, 5) : "";
    }

    function _fontPixels() {
        var font = UM.Theme.getFont("default");
        return Math.max(9, Math.round(font.pointSize * 96 / 72));
    }

    function _fontString() {
        // The canvas context needs a CSS pixel font string; handing it
        // the theme's QFont spams warnings and renders a default font.
        return root._fontPixels() + "px sans-serif";
    }

    function _updateHover(x) {
        // Snap the cursor to the 1 s sample grid and publish the clock
        // and per-series values for the pop-over's readout row. The
        // overlay repaints only when the snapped second changes.
        if (x < 0) {
            _hoverSnap = -1;
            hoverClock = "";
            hoverValues = [];
            _hoverMarks = [];
            overlay.requestPaint();
            return;
        }
        var fraction = Math.max(0, Math.min(1, x / (width - root._rightGutter())));
        var cursor = _minElapsed + fraction * (_maxElapsed - _minElapsed);
        var snapped = Math.round(cursor);
        if (snapped !== _hoverSnap) {
            _hoverSnap = snapped;
            overlay.requestPaint();
            // Publications are gated on the snapped second too: history
            // is append-only, so a given second's values never change,
            // and re-publishing an identical-content array on every
            // mousemove would just fire change signals for nothing.
            hoverClock = _clockText(snapped);
            var values = [];
            var marks = [];
            var series = chart.series !== undefined ? chart.series : [];
            for (var i = 0; i < series.length; ++i) {
                if (!series[i].visible || series[i].points.length === 0) {
                    continue;
                }
                var index = _nearestIndex(series[i].points, snapped);
                values.push({
                        "label": series[i].label,
                        "color": series[i].color,
                        "text": index >= 0 ? series[i].points[index][1].toFixed(1) + "°C" : "—"
                    });
                // The overlay's markers, published with the readout it
                // already searched for: the cursor's nearest sample is
                // found once per move, not again in every repaint.
                if (index >= 0) {
                    marks.push({
                            "elapsed": series[i].points[index][0],
                            "temperature": series[i].points[index][1],
                            "color": series[i].color
                        });
                }
            }
            hoverValues = values;
            _hoverMarks = marks;
        }
    }

    onChartChanged: {
        _recomputeBounds();
        dataCanvas.requestPaint();
        // The window scrolls under a parked cursor: re-snap the hover so
        // the overlay line, markers and readout follow the data instead
        // of sitting at pre-scroll positions. _hoverSnap is cleared
        // first so the publish runs even when the new elapsed second
        // collides with the old one — after a gap reset the wall clock
        // and values both change while the snap can stay equal, and a
        // legend toggle must not leave a ghost row in the tooltip.
        if (!root.compact && root.hoverX >= 0) {
            _hoverSnap = -1;
            _updateHover(root.hoverX);
        }
    }
    onWidthChanged: {
        _recomputeBounds();
        dataCanvas.requestPaint();
        overlay.requestPaint();
    }
    onHeightChanged: {
        _recomputeBounds();
        dataCanvas.requestPaint();
        overlay.requestPaint();
    }
    onVisibleChanged: {
        if (visible) {
            _recomputeBounds();
            dataCanvas.requestPaint();
            // The pop-over can close with the cursor parked over the
            // chart; reopening must not show a stale overlay cursor at
            // a pre-scroll position (the data canvas repaints above,
            // the overlay did not).
            overlay.requestPaint();
        }
    }
    onHoverXChanged: _updateHover(hoverX)
    Component.onCompleted: _recomputeBounds()

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
        id: dataCanvas
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var series = root.chart.series !== undefined ? root.chart.series : [];
            var gridColor = UM.Theme.getColor("lining");
            var labelColor = UM.Theme.getColor("text_inactive");
            var lines = root.compact ? 2 : 4;
            var drewPower = false;
            var gutter = root._rightGutter();

            // The plot geometry, resolved once: mapping a point through
            // root._xFor/root._yFor re-reads six QML properties per
            // coordinate, and the data layers walk every sample of every
            // visible series (up to 1800 each) on every paint. A scale
            // and offset per axis makes each coordinate a multiply-add,
            // which measured at roughly a third of the paint's cost.
            var plotWidth = width - gutter;
            var plotBottom = root._plotBottom();
            var elapsedSpan = root._maxElapsed - root._minElapsed;
            var tempSpan = root._maxTemp - root._minTemp;
            var mapScaleX = elapsedSpan > 0 ? plotWidth / elapsedSpan : 0;
            var mapOffsetX = -root._minElapsed * mapScaleX;
            var mapScaleY = tempSpan > 0 ? -plotBottom / tempSpan : 0;
            var mapOffsetY = tempSpan > 0 ? plotBottom - root._minTemp * mapScaleY : plotBottom;

            // Horizontal grid + temperature labels.
            for (var g = 0; g <= lines; ++g) {
                var gy = g * root._plotBottom() / lines;
                ctx.strokeStyle = gridColor;
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(0, gy);
                ctx.lineTo(width - gutter, gy);
                ctx.stroke();
                if (!root.compact) {
                    var value = root._maxTemp - (root._maxTemp - root._minTemp) * g / lines;
                    ctx.fillStyle = labelColor;
                    ctx.font = root._fontString();
                    ctx.textAlign = "left";
                    // The g = 0 row would baseline above the canvas
                    // edge; clamp by the actual font ascent so the top
                    // value stays fully visible at any theme size.
                    // Temperatures carry their unit: the plugin follows
                    // Cura, which always displays °C.
                    ctx.fillText(value.toFixed(0) + "°C", 4, Math.max(gy - 3, Math.ceil(root._fontPixels() * 0.8) + 3));
                }
            }

            // Time-axis ticks: HH:MM labels under the plot (full size
            // only; the mini widget is a sparkline).
            if (!root.compact && root._maxElapsed > root._minElapsed) {
                var ticks = 4;
                for (var tick = 0; tick <= ticks; ++tick) {
                    var telapsed = root._minElapsed + (root._maxElapsed - root._minElapsed) * tick / ticks;
                    var clock = root._clockTextMinutes(telapsed);
                    if (clock !== "") {
                        ctx.fillStyle = labelColor;
                        ctx.font = root._fontString();
                        // Edge ticks align inward so they never clip at
                        // the canvas sides; the strip sits below the
                        // plot, clear of the temperature labels.
                        if (tick === 0) {
                            ctx.textAlign = "left";
                            ctx.fillText(clock, 2, height - 2);
                        } else if (tick === ticks) {
                            ctx.textAlign = "right";
                            ctx.fillText(clock, width - 2, height - 2);
                        } else {
                            ctx.textAlign = "center";
                            ctx.fillText(clock, root._xFor(telapsed), height - 2);
                        }
                    }
                }
                ctx.textAlign = "left";
            }

            // Power areas (second axis, 0-100%), split at None gaps.
            if (root.chart.showPower) {
                for (var p = 0; p < series.length; ++p) {
                    if (!series[p].visible) {
                        continue;
                    }
                    var powers = series[p].powers;
                    for (var seg = 0; seg < powers.length; ++seg) {
                        var powerSeg = powers[seg];
                        if (powerSeg.length < 2) {
                            continue;
                        }
                        drewPower = true;
                        ctx.fillStyle = root._strokeColor(series[p].color, 0.22);
                        ctx.beginPath();
                        ctx.moveTo(powerSeg[0][0] * mapScaleX + mapOffsetX, plotBottom);
                        for (var q = 0; q < powerSeg.length; ++q) {
                            ctx.lineTo(powerSeg[q][0] * mapScaleX + mapOffsetX, plotBottom - powerSeg[q][1] * plotBottom);
                        }
                        ctx.lineTo(powerSeg[powerSeg.length - 1][0] * mapScaleX + mapOffsetX, plotBottom);
                        ctx.closePath();
                        ctx.fill();
                    }
                }
            }

            // Target bands: a translucent fill from the baseline up to
            // the setpoint curve — the fill's top edge IS the target
            // marker, so at steady state the actual rests exactly on
            // it. A thin same-hue stroke sharpens the boundary.
            if (root.chart.showTargets) {
                for (var t = 0; t < series.length; ++t) {
                    if (!series[t].visible) {
                        continue;
                    }
                    var targets = series[t].targets;
                    for (var band = 0; band < targets.length; ++band) {
                        var targetSeg = targets[band];
                        if (targetSeg.length < 2) {
                            continue;
                        }
                        ctx.fillStyle = root._strokeColor(series[t].color, 0.10);
                        ctx.beginPath();
                        ctx.moveTo(targetSeg[0][0] * mapScaleX + mapOffsetX, plotBottom);
                        for (var u = 0; u < targetSeg.length; ++u) {
                            ctx.lineTo(targetSeg[u][0] * mapScaleX + mapOffsetX, targetSeg[u][1] * mapScaleY + mapOffsetY);
                        }
                        ctx.lineTo(targetSeg[targetSeg.length - 1][0] * mapScaleX + mapOffsetX, plotBottom);
                        ctx.closePath();
                        ctx.fill();
                        ctx.strokeStyle = root._strokeColor(series[t].color, 0.4);
                        ctx.lineWidth = 1;
                        ctx.beginPath();
                        ctx.moveTo(targetSeg[0][0] * mapScaleX + mapOffsetX, targetSeg[0][1] * mapScaleY + mapOffsetY);
                        for (var v = 1; v < targetSeg.length; ++v) {
                            ctx.lineTo(targetSeg[v][0] * mapScaleX + mapOffsetX, targetSeg[v][1] * mapScaleY + mapOffsetY);
                        }
                        ctx.stroke();
                    }
                }
            }

            // Actual temperature lines, drawn last so they always read
            // over the bands.
            for (var s = 0; s < series.length; ++s) {
                if (!series[s].visible || series[s].points.length < 2) {
                    continue;
                }
                var points = series[s].points;
                ctx.strokeStyle = root._strokeColor(series[s].color, 1);
                ctx.lineWidth = root.compact ? 1.2 : 1.6;
                ctx.beginPath();
                ctx.moveTo(points[0][0] * mapScaleX + mapOffsetX, points[0][1] * mapScaleY + mapOffsetY);
                for (var j = 1; j < points.length; ++j) {
                    ctx.lineTo(points[j][0] * mapScaleX + mapOffsetX, points[j][1] * mapScaleY + mapOffsetY);
                }
                ctx.stroke();
                ctx.lineWidth = 1;
            }

            // Power-axis labels (0-100%, pinned — never scaled) sit in
            // the reserved right gutter, clear of the data. Painted
            // last so they are never over-drawn; no background chip —
            // the gutter is empty by construction.
            if (drewPower && !root.compact) {
                var ascent = Math.ceil(root._fontPixels() * 0.8);
                var descent = Math.ceil(root._fontPixels() * 0.2);
                var labelX = width - gutter + 4;
                ctx.font = root._fontString();
                ctx.textAlign = "left";
                ctx.fillStyle = labelColor;
                // The 100% baseline starts 4px+ascent down so its top
                // never clips at the canvas edge; the 0% sits just
                // above the plot's bottom edge.
                ctx.fillText("100%", labelX, 4 + ascent);
                ctx.fillText("0%", labelX, root._plotBottom() - descent - 1);
            }
        }
    }

    Canvas {
        id: overlay
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            if (root.compact || root.hoverX < 0 || root._hoverSnap < 0) {
                return;
            }
            var labelColor = UM.Theme.getColor("text_inactive");
            var x = root._xFor(root._hoverSnap);
            ctx.strokeStyle = labelColor;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height);
            ctx.stroke();
            // Per-series markers, snapped to each series' nearest sample
            // in time — a late-starting sensor shows its honest position,
            // and the readout's "—" marks when it has nothing there. The
            // search already ran when the readout was published; this
            // paint only maps the samples it kept.
            var marks = root._hoverMarks;
            for (var h = 0; h < marks.length; ++h) {
                ctx.fillStyle = root._strokeColor(marks[h].color, 1);
                ctx.beginPath();
                ctx.arc(root._xFor(marks[h].elapsed), root._yFor(marks[h].temperature), 2.5, 0, 2 * Math.PI);
                ctx.fill();
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: !root.compact
        cursorShape: root.compact ? Qt.PointingHandCursor : Qt.CrossCursor
        onPositionChanged: {
            if (!root.compact) {
                root.hoverX = mouse.x;
                root.hoverCursor = Qt.point(mouse.x, mouse.y);
            }
        }
        onExited: {
            if (!root.compact) {
                root.hoverX = -1;
                root.hoverCursor = Qt.point(-1, -1);
            }
        }
        onClicked: root.clicked()
    }
}
