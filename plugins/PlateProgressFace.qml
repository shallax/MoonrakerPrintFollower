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
    // The stroke thickness multiplier (the live request). The 0.7
    // default keeps a dense hatch (the skin's ~0.4 mm pitch)
    // legible as individual lines instead of fusing into a blob
    // (the live report: the renderer's strokes swallowed the
    // diagonals the reference renderers show).
    property real lineScale: 0.7
    // The toolpath ink's PHYSICAL width: strokes are bed-space
    // geometry, never screen pixels. The nominal is the slicer's
    // line at this print's scale, calibrated so ~300% keeps the
    // previously-good screen weight (the old 0.7 px stroke there
    // read back as ~0.16-0.23 mm of bed). At 100% a stroke is
    // legitimately SUBPIXEL — the live report's merged Day strokes
    // and premature solid SKIN were the screen-constant ink, not
    // the geometry.
    property real nominalToolpathWidthMm: 0.2
    // Travels draw thinner than extrusion ink: a VISUAL ratio over
    // the same physical basis, never its own pixel count.
    property real travelVisualRatio: 0.7
    // The mini face is a fixed-size thumbnail: honest physical
    // strokes there are ~0.05 px and invisible, so the mini boosts
    // the weight (the old halved 1 px read the same way). The
    // zoomable popover never reads this.
    property real compactStrokeBoost: 7.0
    // The zoom/pan view (the live request): a scale and a pan in
    // canvas pixels, applied by every raster AND the shared mapping's
    // grid — one transform so the stack moves together.
    property real viewScale: 1.0
    property real viewPanX: 0.0
    property real viewPanY: 0.0
    // The raster paints run on the threaded canvases' worker
    // contexts, which read var properties fresh but can see primitive
    // properties stale; the view/width state rides this var carrier
    // and every painter reads it (the offscreen-harness repaint
    // findings). The scope, the dot and the grid bindings are
    // scene-graph and keep reading the properties directly.
    property var _view: ({
            scale: 1.0,
            panX: 0.0,
            panY: 0.0,
            lineScale: 0.7,
            compact: false
        })

    function _publishView() {
        root._view = {
            scale: root.viewScale,
            panX: root.viewPanX,
            panY: root.viewPanY,
            lineScale: root.lineScale,
            compact: root.compact
        };
    }
    property real _dragX: 0.0
    property real _dragY: 0.0
    // The scope's dock state: parked out of view until a zoom change
    // docks it, and two idle seconds park it again (the live
    // request — it starts invisible).
    property bool _scopeDocked: false
    // The scope's graduation labels: every 25% between 100% and
    // 2000%, log-positioned along the bar (the live request: the
    // cap moved from 800% to 2000%).
    readonly property var zoomGraduations: {
        var steps = [];
        for (var v = 1.0; v <= 20.0 + 1e-9; v += 0.25) {
            steps.push(v);
        }
        return steps;
    }
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

    // The ONE physical stroke-width calculation, shared by the ghost,
    // pending, printed and travel painters: nominal bed mm through
    // the live plot's px-per-mm and the view zoom, times the user's
    // line scale. The plot is aspect-preserved so sx equals sy (the
    // mapping's own contract). Screen-space annotations — the
    // toolhead dot, the travel glyphs, the grid — never read this.
    function toolpathWidthPx() {
        var plot = mapping._plot;
        if (plot == null) {
            return 0;
        }
        return root.nominalToolpathWidthMm * Math.abs(plot.sx) * Math.abs(root._view.scale) * root._view.lineScale * (root._view.compact ? root.compactStrokeBoost : 1.0);
    }

    function travelWidthPx() {
        return root.toolpathWidthPx() * root.travelVisualRatio;
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
    onLineScaleChanged: {
        _publishView();
        _resetStack();
    }
    onViewScaleChanged: {
        _publishView();
        _resetStack();
        // The scope docks while the zoom is in use and slides away
        // once it has been idle (the live request): two seconds of
        // no zoom change parks it out of view to the right.
        root._scopeDocked = true;
        scopeHideTimer.restart();
    }
    onViewPanXChanged: {
        _publishView();
        _resetStack();
    }
    onViewPanYChanged: {
        _publishView();
        _resetStack();
    }
    onCompactChanged: {
        // The product sets compact at construction and never flips
        // it; the repaint keeps the thumbnail honest wherever it is.
        _publishView();
        _resetStack();
    }
    Component.onCompleted: _publishView()

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

    // The painter's ONE rule, shared by the strokes and the glyphs: the
    // payload's vertices are the G-code's own motion edges (edge i runs
    // from points[i - 1] to points[i] and belongs to the motion
    // points[i][2] names, and a segment's indices never decrease), the
    // split is a COUNT of printed motions, and an edge is drawn exactly
    // when its own motion is below it. Both halves of the paint read it
    // the same way: the full repaint from -1, the accumulated delta on
    // top of the last count.
    function _firstEdge(points, from) {
        var i = 1;
        while (i < points.length && from >= 0 && points[i][2] < from) {
            ++i;
        }
        return i;
    }

    function _edgePrinted(points, i, split) {
        return i < points.length && (split < 0 || points[i][2] < split);
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
        var panX = root._view.panX;
        var panY = root._view.panY;
        var scale = root._view.scale;
        // The physical stroke: one width for every channel (the
        // ghost/pending/printed parity rule), subpixel at 100%.
        ctx.lineWidth = root.toolpathWidthPx();
        // Round joins: the default miter spikes at acute corners with
        // a length that grows with the stroke width — thick lines
        // sprouted sharp edges at every text corner (the live report).
        ctx.lineJoin = "round";
        // Round caps: square ends jut half a width past each stroke
        // end, the same thick-line artifact at travel seams.
        ctx.lineCap = "round";
        for (var name in layer.classes) {
            var segments = layer.classes[name];
            ctx.strokeStyle = base ? MoonrakerTheme.seriesDefault : root.classColour(name);
            ctx.globalAlpha = alpha;
            for (var s = 0; s < segments.length; ++s) {
                var points = segments[s];
                // Every segment is at least one EDGE — two vertices — so
                // a one-motion extrusion draws its true line, never a
                // dot: the payload carries the move's start position.
                if (points.length < 2) {
                    continue;
                }
                var i = _firstEdge(points, from);
                if (!_edgePrinted(points, i, split)) {
                    continue;
                }
                ctx.beginPath();
                // A fresh path per segment, opened at the first edge's
                // OWN start vertex: the stroke never bridges a travel, a
                // feature change, or the boundary the last poll painted.
                ctx.moveTo(panX + (offsetX + (points[i - 1][0] - bedXMin) * sx) * scale, panY + (offsetY + (bedYMax - points[i - 1][1]) * sy) * scale);
                while (_edgePrinted(points, i, split)) {
                    ctx.lineTo(panX + (offsetX + (points[i][0] - bedXMin) * sx) * scale, panY + (offsetY + (bedYMax - points[i][1]) * sy) * scale);
                    ++i;
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
        ctx.lineWidth = root.travelWidthPx();
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        for (var s = 0; s < segments.length; ++s) {
            var points = segments[s];
            if (points.length < 2) {
                continue;
            }
            var i = _firstEdge(points, from);
            if (!_edgePrinted(points, i, split)) {
                continue;
            }
            ctx.beginPath();
            var scene = mapping.plateToScene(points[i - 1][0], points[i - 1][1]);
            if (scene == null) {
                continue;
            }
            ctx.moveTo(root._view.panX + scene.x * root._view.scale, root._view.panY + scene.y * root._view.scale);
            while (_edgePrinted(points, i, split)) {
                scene = mapping.plateToScene(points[i][0], points[i][1]);
                if (scene != null) {
                    ctx.lineTo(root._view.panX + scene.x * root._view.scale, root._view.panY + scene.y * root._view.scale);
                }
                ++i;
            }
            ctx.stroke();
        }
        ctx.globalAlpha = 1.0;
    }

    function _drawGlyphs(ctx, marks, start, split, from) {
        // A glyph belongs to the motion that owns its boundary, and is
        // painted with the same rule as the strokes (the marks arrive in
        // motion order, so the first unprinted one ends the sweep). The
        // triangle SIZE is a screen-space annotation, like the toolhead
        // dot and the grid: it never scales with the toolpath ink.
        for (var i = 0; i < marks.length; ++i) {
            if (from >= 0 && marks[i][2] < from) {
                continue;
            }
            if (split >= 0 && marks[i][2] >= split) {
                break;
            }
            var scene = mapping.plateToScene(marks[i][0], marks[i][1]);
            if (scene == null) {
                continue;
            }
            var sceneX = root._view.panX + scene.x * root._view.scale;
            var sceneY = root._view.panY + scene.y * root._view.scale;
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
            var target = Math.min(20.0, Math.max(1.0, root.viewScale * factor));
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
        // The full canvas height minus the reserved bottom strip the
        // Reset view label owns (the live request: the bar spans the
        // canvas and the label keeps its own gap at the bottom).
        height: Math.max(60 * screenScaleFactor, root.height - 30 * screenScaleFactor)
        x: root._scopeDocked ? root.width - width - UM.Theme.getSize("narrow_margin").width : root.width + 6 * screenScaleFactor
        anchors.top: parent.top
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
                model: root.zoomGraduations
                Item {
                    readonly property real fraction: Math.log(modelData) / Math.log(20.0)
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
                y: parent.height - (Math.log(root.viewScale) / Math.log(20.0)) * parent.height - height / 2
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
                    var target = Math.pow(20.0, fraction);
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
        // Not tied to the scope's park: the reset stays put while the
        // overlay slides away (the live request).
        visible: root.available() && !root.compact && root.viewScale > 1.0
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
