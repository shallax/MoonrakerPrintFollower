import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The plate map's progress face (4.6.0): the OctoApp-style
// previous/current/next layer view. The current layer draws as a
// grey base with the printed portion coloured in at poll cadence
// (the walked ruling); the ghost layers and the base are legend
// toggles, and the travel boundaries carry the start/end glyphs.
Item {
    id: root
    objectName: "moonrakerPlateProgressFace"
    clip: true

    property var printerModel: null
    property var progress: null   // the model's plateProgress payload
    property var dot: null
    property bool compact: false
    property bool showPrevious: true
    property bool showNext: true
    property bool showBase: true
    property bool showTravels: false  // lines AND boundary markers — one toggle (the live ruling)
    property real lineScale: 1.0      // the stroke thickness multiplier (the live request)
    // The zoom/pan view (the live request): a scale and a pan in
    // canvas pixels, applied by every raster AND the shared mapping's
    // grid — one transform so the stack moves together.
    property real viewScale: 1.0
    property real viewPanX: 0.0
    property real viewPanY: 0.0
    property real _dragX: 0.0
    property real _dragY: 0.0
    // The scope's dock state: parked out of view until a zoom change
    // docks it, and two idle seconds park it again (the live
    // request — it starts invisible).
    property bool _scopeDocked: false
    Timer {
        id: scopeHideTimer
        interval: 2000
        onTriggered: root._scopeDocked = false
    }

    function classColour(name) {
        switch (name) {
        case "WALL-OUTER":
            return MoonrakerTheme.plateClassWallOuter;
        case "WALL-INNER":
            return MoonrakerTheme.plateClassWallInner;
        case "SKIN":
            return MoonrakerTheme.plateClassSkin;
        case "FILL":
            return MoonrakerTheme.plateClassFill;
        case "SUPPORT":
            return MoonrakerTheme.plateClassSupport;
        case "SKIRT":
            return MoonrakerTheme.plateClassSkirt;
        default:
            return MoonrakerTheme.seriesDefault;
        }
    }

    function available() {
        return root.progress != null && root.progress.available === true;
    }

    // The raster stack's painted state: the split the progress canvas
    // has accumulated up to, the anchor it was built for, and the
    // periodic re-raster counter (the live design: a full re-raster
    // every ~20 paints so the accumulation cannot drift).
    property int _lastSplit: -1
    property int _anchor: -1
    property int _paintsSinceReset: 0
    property bool _progressDirty: false

    function _resetStack() {
        // Everything repaints: a toggle flip, a resize, or an anchor
        // change — the ghosts and the pending base are static
        // rasterisations, the progress starts over from motion zero.
        root._lastSplit = -1;
        root._paintsSinceReset = 0;
        root._progressDirty = true;
        ghostCanvas.requestPaint();
        pendingCanvas.requestPaint();
        progressCanvas.requestPaint();
    }

    onProgressChanged: {
        if (root.progress == null || root.progress.layers == null) {
            root._lastSplit = -1;
            root._anchor = -1;
            return;
        }
        if (root.progress.anchor !== root._anchor) {
            root._anchor = root.progress.anchor;
            _resetStack();
            return;
        }
        progressCanvas.requestPaint();
    }
    onShowPreviousChanged: ghostCanvas.requestPaint()
    onShowNextChanged: ghostCanvas.requestPaint()
    onShowBaseChanged: pendingCanvas.requestPaint()
    onShowTravelsChanged: _resetStack()  // the travels join the accumulated progress
    onLineScaleChanged: _resetStack()
    onViewScaleChanged: {
        _resetStack();
        // The scope docks while the zoom is in use and slides away
        // once it has been idle (the live request): two seconds of
        // no zoom change parks it out of view to the right.
        root._scopeDocked = true;
        scopeHideTimer.restart();
    }
    onViewPanXChanged: _resetStack()
    onViewPanYChanged: _resetStack()

    // The mapping replots on ITS resize; every raster holds the old
    // transform's coordinates and repaints from scratch (the live
    // report: a strip at the card's top — the grown plot must
    // repaint).
    Connections {
        target: mapping
        function onWidthChanged() {
            root._resetStack();
        }
        function onHeightChanged() {
            root._resetStack();
        }
    }

    // The shared base carries the bed mapping — the face's canvas
    // draws the layers through ITS transform, so the one-mapping rule
    // holds across both faces. Hidden while the index is unavailable:
    // the bed grid must not sit under the download offer's text (the
    // live report — it read and clicked badly).
    PlateCanvas {
        id: mapping
        anchors.fill: parent
        visible: root.available()
        printerModel: root.printerModel
        plate: null
        viewScale: root.viewScale
        viewPanX: root.viewPanX
        viewPanY: root.viewPanY
    }

    // The raster stack, bottom to top (the live design): the ghost
    // layers, the pending base, and the accumulated progress — each
    // an image-backed canvas that only repaints when its content
    // changes. The ghosts and the pending base never move while the
    // current layer progresses, so they rasterise once per anchor or
    // toggle; the progress accumulates the printed delta per poll
    // and re-rasters fully every ~20 paints. Transparency does the
    // blending between the layers.
    Canvas {
        id: ghostCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            ctx.clearRect(0, 0, width, height);
            if (!root.available() || mapping._plot == null) {
                return;
            }
            var layers = root.progress.layers;
            // The ghost layers: full paths at low alpha — the stack
            // reads through (the walked transparency ruling).
            if (root.showPrevious && layers.prev != null) {
                _drawLayer(ctx, layers.prev, 0.30, -1, false, -1);
            }
            if (root.showNext && layers.next != null) {
                _drawLayer(ctx, layers.next, 0.30, -1, false, -1);
            }
        }
    }

    Canvas {
        id: pendingCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            ctx.clearRect(0, 0, width, height);
            if (!root.available() || mapping._plot == null) {
                return;
            }
            var current = root.progress.layers.current;
            if (current == null) {
                return;
            }
            // The grey base: the whole layer, one honest colour.
            if (root.showBase) {
                _drawLayer(ctx, current, 0.55, -1, true, -1);
            }
        }
    }

    Canvas {
        id: progressCanvas
        anchors.fill: parent
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        onPaint: {
            var ctx = getContext("2d");
            if (!root.available() || mapping._plot == null) {
                return;
            }
            var current = root.progress.layers.current;
            if (current == null) {
                return;
            }
            var split = root.progress.split;
            if (split == null) {
                return;
            }
            // A backward split (a restart), a toggle flip or an
            // anchor change clears the image; the accumulation also
            // re-rasters fully on its own cadence so it cannot
            // drift. Otherwise the canvas keeps its image and only
            // the new delta is stroked on top.
            if (root._progressDirty || split < root._lastSplit || root._paintsSinceReset >= 20) {
                ctx.reset();
                ctx.clearRect(0, 0, width, height);
                root._lastSplit = -1;
                root._progressDirty = false;
                root._paintsSinceReset = 0;
            }
            // The printed portion, coloured in per feature class from
            // the last painted split up to the live one (the H3
            // floor).
            _drawLayer(ctx, current, 1.0, split, false, root._lastSplit);
            // The travels: lines and boundary markers together, the
            // CURRENT layer only, and only where the toolhead has
            // already passed (the live rulings).
            if (root.showTravels) {
                _drawTravels(ctx, current.travels, split, root._lastSplit);
                _drawGlyphs(ctx, current.travelStarts, true, split, root._lastSplit);
                _drawGlyphs(ctx, current.travelEnds, false, split, root._lastSplit);
            }
            root._lastSplit = split;
            root._paintsSinceReset += 1;
        }
    }

    function _drawLayer(ctx, layer, alpha, split, base, from) {
        // The transform inlined: hundreds of thousands of
        // plateToScene calls per paint were the follower's cost.
        var plot = mapping._plot;
        var sx = plot.sx;
        var sy = plot.sy;
        var offsetX = plot.bed.offsetX;
        var offsetY = plot.bed.offsetY;
        var bedXMin = plot.bed.bedXMin;
        var bedYMax = plot.bed.bedYMax;
        for (var name in layer.classes) {
            var segments = layer.classes[name];
            ctx.strokeStyle = base ? MoonrakerTheme.seriesDefault : root.classColour(name);
            ctx.globalAlpha = alpha;
            // The mini halves the strokes: a 1 px line is ~3 mm of
            // bed at its scale and reads as a blob (the live report).
            ctx.lineWidth = root.lineScale * (root.compact ? 0.5 : 1.0);
            for (var s = 0; s < segments.length; ++s) {
                var points = segments[s];
                if (points.length < 2) {
                    // A one-motion run draws as a dot: the live file's
                    // skin emits per-line extrusion pulses, and
                    // dropping sub-2-point segments erased the skin
                    // entirely (the live report).
                    if (points.length === 1 && (from < 0 || points[0][2] > from) && (split < 0 || points[0][2] <= split)) {
                        var dotX = root.viewPanX + (offsetX + (points[0][0] - bedXMin) * sx) * root.viewScale;
                        var dotY = root.viewPanY + (offsetY + (bedYMax - points[0][1]) * sy) * root.viewScale;
                        // The fill follows the stroke ink: an unset
                        // fillStyle painted black blobs (the live
                        // report).
                        ctx.fillStyle = ctx.strokeStyle;
                        ctx.beginPath();
                        ctx.arc(dotX, dotY, root.lineScale * (root.compact ? 0.5 : 1.0), 0, Math.PI * 2);
                        ctx.fill();
                    }
                    continue;
                }
                ctx.beginPath();
                var started = false;
                var previous = null;
                for (var i = 0; i < points.length; ++i) {
                    if (split >= 0 && points[i][2] > split) {
                        break;
                    }
                    // The travel-broken segments: a fresh path per
                    // segment, so the stroke never bridges a travel.
                    var sceneX = root.viewPanX + (offsetX + (points[i][0] - bedXMin) * sx) * root.viewScale;
                    var sceneY = root.viewPanY + (offsetY + (bedYMax - points[i][1]) * sy) * root.viewScale;
                    if (from >= 0 && points[i][2] <= from) {
                        // Already painted; the accumulation joins the
                        // delta to it so the poll boundary carries no
                        // gap.
                        previous = {
                            "x": sceneX,
                            "y": sceneY
                        };
                        continue;
                    }
                    if (!started) {
                        if (previous != null) {
                            ctx.moveTo(previous.x, previous.y);
                            ctx.lineTo(sceneX, sceneY);
                        } else {
                            ctx.moveTo(sceneX, sceneY);
                        }
                        started = true;
                    } else {
                        ctx.lineTo(sceneX, sceneY);
                    }
                    previous = null;
                }
                ctx.stroke();
            }
            ctx.globalAlpha = 1.0;
        }
    }

    function _drawTravels(ctx, segments, split, from) {
        if (segments == null) {
            return;
        }
        ctx.strokeStyle = MoonrakerTheme.plateTravel;
        ctx.globalAlpha = 0.8;
        ctx.lineWidth = 0.7 * root.lineScale * (root.compact ? 0.5 : 1.0);
        for (var s = 0; s < segments.length; ++s) {
            var points = segments[s];
            if (points.length < 2) {
                continue;
            }
            ctx.beginPath();
            var started = false;
            var previous = null;
            for (var i = 0; i < points.length; ++i) {
                if (split >= 0 && points[i][2] > split) {
                    break;
                }
                var scene = mapping.plateToScene(points[i][0], points[i][1]);
                if (scene == null) {
                    continue;
                }
                var sceneX = root.viewPanX + scene.x * root.viewScale;
                var sceneY = root.viewPanY + scene.y * root.viewScale;
                if (from >= 0 && points[i][2] <= from) {
                    previous = {
                        "x": sceneX,
                        "y": sceneY
                    };
                    continue;
                }
                if (!started) {
                    if (previous != null) {
                        ctx.moveTo(previous.x, previous.y);
                        ctx.lineTo(sceneX, sceneY);
                    } else {
                        ctx.moveTo(sceneX, sceneY);
                    }
                    started = true;
                } else {
                    ctx.lineTo(sceneX, sceneY);
                }
                previous = null;
            }
            ctx.stroke();
        }
        ctx.globalAlpha = 1.0;
    }

    function _drawGlyphs(ctx, marks, start, split, from) {
        for (var i = 0; i < marks.length; ++i) {
            if (from >= 0 && marks[i][2] <= from) {
                continue;
            }
            if (split >= 0 && marks[i][2] > split) {
                break;
            }
            var scene = mapping.plateToScene(marks[i][0], marks[i][1]);
            if (scene == null) {
                continue;
            }
            var sceneX = root.viewPanX + scene.x * root.viewScale;
            var sceneY = root.viewPanY + scene.y * root.viewScale;
            ctx.fillStyle = MoonrakerTheme.plateTravel;
            ctx.beginPath();
            if (start) {
                ctx.moveTo(sceneX, sceneY - 3.5 * screenScaleFactor);
                ctx.lineTo(sceneX - 3 * screenScaleFactor, sceneY + 2.5 * screenScaleFactor);
                ctx.lineTo(sceneX + 3 * screenScaleFactor, sceneY + 2.5 * screenScaleFactor);
            } else {
                ctx.moveTo(sceneX, sceneY + 3.5 * screenScaleFactor);
                ctx.lineTo(sceneX - 3 * screenScaleFactor, sceneY - 2.5 * screenScaleFactor);
                ctx.lineTo(sceneX + 3 * screenScaleFactor, sceneY - 2.5 * screenScaleFactor);
            }
            ctx.closePath();
            ctx.fill();
        }
    }

    // The toolhead dot: scene-graph geometry (a Rectangle binding),
    // never a canvas repaint — the 1 s position publish moves it.
    // Walking the layer path (the H3 alignment — the dot and the
    // fill derive from the same index). The picker's faces draw no
    // dot (the live ruling).
    Rectangle {
        id: toolheadDot
        objectName: "moonrakerPlateToolheadDot"
        width: 7 * screenScaleFactor
        height: width
        radius: width / 2
        color: MoonrakerTheme.plateDot
        border.color: UM.Theme.getColor("main_background")
        border.width: 2
        // The dot rides the LAYERS: no index, no dot (the live
        // report — it rendered over the unavailable card).
        visible: root.available() && mapping._plot != null && root.dot != null && root.dot.valid === true
        x: visible ? root.viewPanX + mapping.plateToScene(root.dot.x, root.dot.y).x * root.viewScale - width / 2 : 0
        y: visible ? root.viewPanY + mapping.plateToScene(root.dot.x, root.dot.y).y * root.viewScale - height / 2 : 0
    }

    // The zoom/pan gestures (the live request): the wheel zooms about
    // the cursor, a drag pans, a double click resets to the full bed.
    MouseArea {
        id: viewGesture
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onWheel: function (wheel) {
            if (mapping._plot == null) {
                return;
            }
            var factor = wheel.angleDelta.y > 0 ? 1.25 : 0.8;
            var target = Math.min(8.0, Math.max(1.0, root.viewScale * factor));
            if (target === root.viewScale) {
                return;
            }
            if (target <= 1.0) {
                // 100% is the fit: centred, panning off (the live
                // ruling — no pan at full zoom).
                root.viewScale = 1.0;
                root.viewPanX = 0.0;
                root.viewPanY = 0.0;
                return;
            }
            // The point under the cursor stays put.
            var ratio = target / root.viewScale;
            root.viewPanX = wheel.x - (wheel.x - root.viewPanX) * ratio;
            root.viewPanY = wheel.y - (wheel.y - root.viewPanY) * ratio;
            root.viewScale = target;
        }
        onPressed: function (mouse) {
            root._dragX = mouse.x;
            root._dragY = mouse.y;
        }
        onPositionChanged: function (mouse) {
            if (!pressed || root.viewScale <= 1.0) {
                return;
            }
            root.viewPanX += mouse.x - root._dragX;
            root.viewPanY += mouse.y - root._dragY;
            root._dragX = mouse.x;
            root._dragY = mouse.y;
        }
        onDoubleClicked: {
            root.viewScale = 1.0;
            root.viewPanX = 0.0;
            root.viewPanY = 0.0;
        }
    }

    // The zoom scope (the live request): a cockpit-HUD scale beside
    // the canvas — a RESERVED readout strip for the percentage, and
    // below it the graduated bar: a complete line at every 100%, a
    // 50%-in edge pair at the half marks and a 25%-in edge pair at
    // the quarters. The marker rides the zoom and doubles as the
    // drag handle; the bottom is the 100% fit.
    Rectangle {
        id: zoomScope
        visible: root.available() && !root.compact
        // Wide enough for the "800%" label (the live report: the
        // percentage overflowed the scope's bounds). Docked it hugs
        // the right edge; parked it slides fully out of view (the
        // live request).
        width: 30 * screenScaleFactor
        height: Math.min(190 * screenScaleFactor, root.height * 0.45)
        x: root._scopeDocked ? root.width - width - UM.Theme.getSize("narrow_margin").width : root.width + 6 * screenScaleFactor
        anchors.verticalCenter: parent.verticalCenter
        Behavior on x {
            NumberAnimation {
                duration: 180
                easing.type: Easing.OutCubic
            }
        }
        radius: 3 * screenScaleFactor
        color: UM.Theme.getColor("main_background")
        opacity: 0.85
        border.color: UM.Theme.getColor("lining")
        border.width: 1
        Item {
            // The reserved readout: the label never shares space
            // with the bar (the live report: the text overlapped
            // the marker).
            id: scopeReadout
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 16 * screenScaleFactor
            UM.Label {
                anchors.centerIn: parent
                text: Math.round(root.viewScale * 100) + "%"
                font: UM.Theme.getFont("small")
                color: UM.Theme.getColor("text")
            }
        }
        Item {
            id: scopeBar
            anchors.top: scopeReadout.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 4 * screenScaleFactor
            // The graduations: every 25% between 100% and 800%,
            // log-spaced like the marker.
            Repeater {
                model: [1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 5.75, 6.0, 6.25, 6.5, 6.75, 7.0, 7.25, 7.5, 7.75, 8.0]
                Item {
                    readonly property real fraction: Math.log(modelData) / Math.log(8.0)
                    readonly property bool major: Math.round(modelData * 100) % 100 === 0
                    readonly property bool half: Math.round(modelData * 100) % 50 === 0
                    anchors.left: parent.left
                    anchors.right: parent.right
                    y: parent.height - fraction * parent.height
                    height: major ? 2 : 1
                    Rectangle {
                        // The left edge's reach: the full width at the
                        // majors, 50% in at the halves, 25% in at the
                        // quarters (the cockpit-HUD request).
                        anchors.left: parent.left
                        width: parent.width * (major ? 1.0 : half ? 0.5 : 0.25)
                        height: parent.height
                        color: major ? UM.Theme.getColor("border") : UM.Theme.getColor("lining")
                    }
                    Rectangle {
                        // The mirrored right edge's reach (the halves
                        // and quarters draw as an edge pair).
                        visible: !major
                        anchors.right: parent.right
                        width: parent.width * (half ? 0.5 : 0.25)
                        height: parent.height
                        color: UM.Theme.getColor("lining")
                    }
                }
            }
            Rectangle {
                // The marker: log-rides the zoom — and doubles as the
                // drag handle.
                id: scopeMarker
                anchors.horizontalCenter: parent.horizontalCenter
                y: parent.height - (Math.log(root.viewScale) / Math.log(8.0)) * parent.height - height / 2
                width: parent.width
                height: 3 * screenScaleFactor
                radius: height / 2
                color: UM.Theme.getColor("primary")
            }
            MouseArea {
                // The draggable scale: the pointer's track position
                // is the zoom — bottom is the 100% fit (centred, no
                // pan), the top is 800%.
                anchors.fill: parent
                onPressed: function (mouse) {
                    scopeApply(mouse.y);
                }
                onPositionChanged: function (mouse) {
                    if (pressed) {
                        scopeApply(mouse.y);
                    }
                }
                function scopeApply(y) {
                    var fraction = Math.min(1.0, Math.max(0.0, (parent.height - y) / parent.height));
                    var target = Math.pow(8.0, fraction);
                    if (target <= 1.0) {
                        root.viewScale = 1.0;
                        root.viewPanX = 0.0;
                        root.viewPanY = 0.0;
                        return;
                    }
                    root.viewScale = target;
                }
            }
        }
    }

    // The view reset (the live request): a text label under the
    // scope, back to the 100% fit and centred — shown only while
    // the view is away from it. A button rendered as an empty box
    // in this overlay (the live report); the label reads as the
    // scope's caption.
    UM.Label {
        id: resetViewLabel
        visible: root.available() && !root.compact && root.viewScale > 1.0 && root._scopeDocked
        anchors.right: parent.right
        anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
        anchors.bottom: parent.bottom
        anchors.bottomMargin: UM.Theme.getSize("narrow_margin").height
        text: "Reset view"
        font: UM.Theme.getFont("small")
        color: UM.Theme.getColor("primary")
        MouseArea {
            anchors.fill: parent
            onClicked: {
                root.viewScale = 1.0;
                root.viewPanX = 0.0;
                root.viewPanY = 0.0;
            }
        }
    }

    // The unavailable state (the live ruling): the shared download
    // action — glyph, bar, percentage and stage — so the follower
    // never reads as a dead end. The mini suppresses the action (its
    // section hosts the placeholder).
    ColumnLayout {
        anchors.centerIn: parent
        width: parent.width * 0.8
        visible: !root.available() && !root.compact
        spacing: UM.Theme.getSize("narrow_margin").height

        PlateDownloadAction {
            Layout.fillWidth: true
            printerModel: root.printerModel
            idleInstruction: root.progress != null && root.progress.reason !== "" ? root.progress.reason : "No index yet — the download button builds one without loading the preview."
        }
    }

    UM.Label {
        anchors.centerIn: parent
        visible: !root.available() && root.compact
        text: "The follower builds its index from the print — open the pop-over."
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("small")
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        width: parent.width * 0.8
    }
}
