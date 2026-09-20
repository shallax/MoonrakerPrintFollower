import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The plate map's progress face (4.6.0): the OctoApp-style
// previous/current/next layer view. The current layer draws as a
// grey base with the printed portion coloured in at poll cadence
// (the walked ruling); the ghost layers and the base are legend
// toggles, and the travels draw as lines alone.
Item {
    id: root
    objectName: "moonrakerPlateProgressFace"
    clip: true

    property var printerModel: null
    property var progress: null   // the model's plateProgress payload
    property var dot: null
    property bool compact: false
    // Attached: the face follows the LIVE layer — the dot moves with
    // the print's position and the printed fill grows. Detached: the
    // anchor is frozen on one layer by the user (the pop-over's
    // sliders), the toolhead dot hides (its position belongs to the
    // live layer, not the frozen one) and the payload arrives with no
    // split, so the layer draws as its whole base.
    property bool attached: true
    // The centred-follow option (default OFF — the cheap render path
    // is the default): every toolhead publish re-pans the view onto
    // the dot, clamped so the bed always fills the view.
    property bool keepCentred: false
    property bool showPrevious: true
    property bool showNext: true
    property bool showBase: true
    property bool showTravels: false  // the travel lines
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
    // The zoom/pan view (the live request): a scale in canvas pixels
    // burned into every raster, and a pan applied by the SCENE GRAPH
    // — the rasters paint bed geometry at the zoom alone and the
    // canvas items carry the pan as a translation, so a pan costs a
    // transform and never a repaint (the centred-follow ruling: at a
    // pan per poll, a pan-baked raster is a full re-raster per poll).
    property real viewScale: 1.0
    property real viewPanX: 0.0
    property real viewPanY: 0.0
    // The raster paints run on the threaded canvases' worker
    // contexts, which read var properties fresh but can see primitive
    // properties stale; the view/width state rides this var carrier
    // and every painter reads it (the offscreen-harness repaint
    // findings). The pan IS a paint input: a raster image is exactly
    // the canvas' size, so a zoomed pan that translated the image
    // instead of re-drawing only slid an already-clipped picture (the
    // live report — panning showed blank canvas). The scope, the dot
    // and the grid bindings are scene-graph and read the properties
    // directly.
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

    // The toolhead's own availability: a live dot on a plotted bed. The
    // mini is a thumbnail with no view state, so it never centres; a
    // detached face has no live position to centre on.
    function dotAvailable() {
        return root.available() && !root.compact && root.attached && mapping._plot != null && root.dot != null && root.dot.valid === true;
    }

    // The one pan rule (the centred-follow ruling): the bed always
    // fills the view, so a pan is clamped to the interval that keeps
    // the plot's rectangle covering the face; an axis where the bed is
    // smaller than the face (the full-bed fit) centres there instead —
    // the standing "no pan at full zoom" ruling.
    function _clampPan(x, y) {
        var plot = mapping._plot;
        if (plot == null) {
            return {
                "x": 0.0,
                "y": 0.0
            };
        }
        var scale = root.viewScale;
        var bed = plot.bed;
        var left = bed.offsetX * scale;
        var right = left + bed.plotWidth * scale;
        var top = bed.offsetY * scale;
        var bottom = top + bed.plotHeight * scale;
        var lowX = width - right;
        var highX = -left;
        var lowY = height - bottom;
        var highY = -top;
        return {
            "x": lowX > highX ? 0.0 : Math.min(highX, Math.max(lowX, x)),
            "y": lowY > highY ? 0.0 : Math.min(highY, Math.max(lowY, y))
        };
    }

    function _panOnToolhead() {
        var scene = mapping.plateToScene(root.dot.x, root.dot.y);
        if (scene == null) {
            return null;
        }
        return _clampPan(width / 2 - scene.x * root.viewScale, height / 2 - scene.y * root.viewScale);
    }

    // The one-shot jump (the pop-over's button): the toolhead's bed
    // position lands at the view's centre, the zoom is the user's own.
    // False when there is nothing to centre on.
    function centreOnToolhead() {
        if (!dotAvailable()) {
            return false;
        }
        var pan = _panOnToolhead();
        if (pan == null) {
            return false;
        }
        root.viewPanX = pan.x;
        root.viewPanY = pan.y;
        return true;
    }

    // The centred follow: every toolhead publish re-pans onto the dot —
    // a scene-graph translation, never a raster (the pan-agnostic
    // rasters make the follow cost-free per poll).
    function _followToolhead() {
        if (!root.keepCentred || !dotAvailable()) {
            return;
        }
        var pan = _panOnToolhead();
        if (pan != null && (pan.x !== root.viewPanX || pan.y !== root.viewPanY)) {
            root.viewPanX = pan.x;
            root.viewPanY = pan.y;
        }
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
    // The raster-reuse keys (the live request): a canvas that already
    // holds exactly this picture skips the walk. The seek's 3 s was
    // four full dense re-walks, and a return to an already-drawn
    // layer must not pay them again. One key per canvas, covering
    // only that canvas' own inputs, so a toggle repaints only what
    // it touches.
    property string _ghostKey: ""
    property string _pendingKey: ""
    property string _progressKey: ""

    function _viewKey() {
        return [root.viewScale, root.viewPanX, root.viewPanY, root.lineScale, root.compact ? 1 : 0, width, height, root.toolpathWidthPx()].join("|");
    }

    function _motionsOf(layer) {
        return layer != null && layer.motions !== undefined ? layer.motions : -1;
    }

    function _ghostKeyOf() {
        var progress = root.progress;
        var layers = progress != null ? progress.layers : null;
        return [(progress != null ? progress.anchor : -1), layers != null ? _motionsOf(layers.prev) : -1, layers != null ? _motionsOf(layers.next) : -1, root.showPrevious ? 1 : 0, root.showNext ? 1 : 0, _viewKey()].join("|");
    }

    function _pendingKeyOf() {
        var progress = root.progress;
        var layers = progress != null ? progress.layers : null;
        return [(progress != null ? progress.anchor : -1), layers != null ? _motionsOf(layers.current) : -1, root.showBase ? 1 : 0, _viewKey()].join("|");
    }

    function _progressKeyOf() {
        var progress = root.progress;
        var layers = progress != null ? progress.layers : null;
        return [(progress != null ? progress.anchor : -1), layers != null ? _motionsOf(layers.current) : -1, progress != null && progress.split != null ? progress.split : -1, root.showTravels ? 1 : 0, _viewKey()].join("|");
    }

    function _resetStack() {
        // Everything repaints: a toggle flip, a resize, or an anchor
        // change — the ghosts and the pending base are static
        // rasterisations, the progress starts over from motion zero.
        // A canvas whose key still matches keeps its image (the
        // raster-reuse ruling above).
        var ghostKey = _ghostKeyOf();
        if (ghostKey !== root._ghostKey) {
            root._ghostKey = ghostKey;
            ghostCanvas.requestPaint();
        }
        var pendingKey = _pendingKeyOf();
        if (pendingKey !== root._pendingKey) {
            root._pendingKey = pendingKey;
            pendingCanvas.requestPaint();
        }
        var progressKey = _progressKeyOf();
        if (progressKey !== root._progressKey) {
            root._progressKey = progressKey;
            root._lastSplit = -1;
            root._paintsSinceReset = 0;
            root._progressDirty = true;
            progressCanvas.requestPaint();
        }
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
    onShowPreviousChanged: {
        var key = _ghostKeyOf();
        if (key !== root._ghostKey) {
            root._ghostKey = key;
            ghostCanvas.requestPaint();
        }
    }
    onShowNextChanged: {
        var key = _ghostKeyOf();
        if (key !== root._ghostKey) {
            root._ghostKey = key;
            ghostCanvas.requestPaint();
        }
    }
    onShowBaseChanged: {
        var key = _pendingKeyOf();
        if (key !== root._pendingKey) {
            root._pendingKey = key;
            pendingCanvas.requestPaint();
        }
    }
    onShowTravelsChanged: {
        // The travels join the accumulated progress: a flip repaints
        // the progress canvas (its key carries the flag).
        var key = _progressKeyOf();
        if (key !== root._progressKey) {
            root._progressKey = key;
            root._lastSplit = -1;
            root._paintsSinceReset = 0;
            root._progressDirty = true;
            progressCanvas.requestPaint();
        }
    }
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
    // A pan is baked into the rasters: the image is canvas-sized and
    // clipped, so only a re-draw moves the view (the live report).
    // The follow's per-poll cost is the price of the option — the
    // default path never pans.
    onViewPanXChanged: {
        _publishView();
        _resetStack();
    }
    onViewPanYChanged: {
        _publishView();
        _resetStack();
    }
    // The toolhead publish is the follow's clock: the model republishes
    // the dot when it moves, and only then.
    onDotChanged: root._followToolhead()
    onKeepCentredChanged: root._followToolhead()
    // Detaching hides the dot and, with it, the follow's subject.
    onAttachedChanged: root._followToolhead()
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
            // reads through (the walked transparency ruling). The
            // context walk is strided: a coarse chord trace reads
            // identically at 30% alpha for a fraction of the points
            // (the live request — the seek's full dense walks).
            if (root.showPrevious && layers.prev != null) {
                _drawLayer(ctx, layers.prev, 0.30, -1, false, -1, _ghostStride(layers.prev));
            }
            if (root.showNext && layers.next != null) {
                _drawLayer(ctx, layers.next, 0.30, -1, false, -1, _ghostStride(layers.next));
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
                _drawLayer(ctx, current, 0.55, -1, true, -1, 1);
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
                // The unavailable surface clears its own ink — the
                // old raster must never read through the loading text
                // (the live report).
                ctx.reset();
                ctx.clearRect(0, 0, width, height);
                root._lastSplit = -1;
                root._paintsSinceReset = 0;
                return;
            }
            var current = root.progress.layers.current;
            if (current == null) {
                ctx.reset();
                ctx.clearRect(0, 0, width, height);
                root._lastSplit = -1;
                root._paintsSinceReset = 0;
                return;
            }
            var split = root.progress.split;
            if (split == null) {
                // No boundary to draw at — a detached (frozen) anchor,
                // or a print without a position. The layer is its whole
                // base and any accumulated fill goes with the split.
                ctx.reset();
                ctx.clearRect(0, 0, width, height);
                root._lastSplit = -1;
                root._paintsSinceReset = 0;
                root._progressDirty = false;
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
            _drawLayer(ctx, current, 1.0, split, false, root._lastSplit, 1);
            // The travels: the lines only. CURRENT layer only, and
            // only where the toolhead has already passed (the live
            // rulings).
            if (root.showTravels) {
                _drawTravels(ctx, current.travels, split, root._lastSplit);
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

    function _ghostStride(layer) {
        // The context ghosts' vertex stride: every stride-th vertex is
        // stroked, so the chord trace stays within the travel channel's
        // own budget whatever the layer's density (the live request).
        var motions = layer != null && layer.motions !== undefined ? layer.motions : 0;
        if (motions <= 12000) {
            return 1;
        }
        return Math.min(8, Math.max(1, Math.ceil(motions / 12000)));
    }

    function _drawLayer(ctx, layer, alpha, split, base, from, stride) {
        // The transform inlined: hundreds of thousands of
        // plateToScene calls per paint were the follower's cost.
        var plot = mapping._plot;
        var sx = plot.sx;
        var sy = plot.sy;
        var offsetX = plot.bed.offsetX;
        var offsetY = plot.bed.offsetY;
        var bedXMin = plot.bed.bedXMin;
        var bedYMax = plot.bed.bedYMax;
        var scale = root._view.scale;
        var panX = root._view.panX;
        var panY = root._view.panY;
        // The physical stroke: one width for every channel (the
        // ghost/pending/printed parity rule), subpixel at 100%.
        ctx.lineWidth = root.toolpathWidthPx();
        // Round joins: the default miter spikes at acute corners with
        // a length that grows with the stroke width — thick lines
        // sprouted sharp edges at every text corner (the live report).
        ctx.lineJoin = "round";
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
                // No pan term: the item's translation carries the view.
                ctx.moveTo((offsetX + (points[i - 1][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i - 1][1]) * sy) * scale + panY);
                if (stride <= 1) {
                    while (_edgePrinted(points, i, split)) {
                        ctx.lineTo((offsetX + (points[i][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i][1]) * sy) * scale + panY);
                        ++i;
                    }
                } else {
                    // The strided walk: every stride-th vertex, and the
                    // segment's last printed vertex always lands so the
                    // chord reaches the boundary it belongs to.
                    var first = i;
                    while (_edgePrinted(points, i, split)) {
                        if ((i - first) % stride === 0) {
                            ctx.lineTo((offsetX + (points[i][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i][1]) * sy) * scale + panY);
                        }
                        ++i;
                    }
                    if ((i - 1 - first) % stride !== 0) {
                        ctx.lineTo((offsetX + (points[i - 1][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i - 1][1]) * sy) * scale + panY);
                    }
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
            ctx.moveTo(scene.x * root._view.scale + root._view.panX, scene.y * root._view.scale + root._view.panY);
            while (_edgePrinted(points, i, split)) {
                scene = mapping.plateToScene(points[i][0], points[i][1]);
                if (scene != null) {
                    ctx.lineTo(scene.x * root._view.scale + root._view.panX, scene.y * root._view.scale + root._view.panY);
                }
                ++i;
            }
            ctx.stroke();
        }
        ctx.globalAlpha = 1.0;
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
        // report — it rendered over the unavailable card). Detached it
        // goes too: the position belongs to the live layer, and the
        // frozen anchor is another layer's picture (the live ruling).
        visible: root.available() && root.attached && mapping._plot != null && root.dot != null && root.dot.valid === true
        // The transform inlined: a function call (plateToScene) hides
        // the plot from the binding's dependencies, so a re-fitted
        // plot (a reflow, the popover's grown size) left the dot on
        // stale geometry until the next pan (the live report — the
        // jump landed off the centre). The whole-var read is tracked
        // and the binding re-runs on every re-fit.
        x: root.dot != null && mapping._plot != null ? root.viewPanX + (mapping._plot.bed.offsetX + (root.dot.x - mapping._plot.bed.bedXMin) * mapping._plot.sx) * root.viewScale - width / 2 : 0
        y: root.dot != null && mapping._plot != null ? root.viewPanY + (mapping._plot.bed.offsetY + (mapping._plot.bed.bedYMax - root.dot.y) * mapping._plot.sy) * root.viewScale - height / 2 : 0
    }

    // The zoom/pan gestures (the live request): the wheel zooms about
    // the cursor, a drag pans, a double click resets to the full bed.
    MouseArea {
        id: viewGesture
        anchors.fill: parent
        // The mini is a read-only thumbnail: no zoom, no pan (the
        // live request — its click opens the pop-over instead).
        enabled: !root.compact
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
        width: 38 * screenScaleFactor
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

        // A layer mid-load reads as plain text, never as a download
        // offer (the live request: the glyph and the button implied
        // an action the index already satisfies).
        UM.Label {
            Layout.fillWidth: true
            visible: root.progress != null && root.progress.reason !== ""
            text: root.progress != null ? root.progress.reason : ""
            color: UM.Theme.getColor("text_inactive")
            horizontalAlignment: Text.AlignHCenter
        }
        PlateDownloadAction {
            Layout.fillWidth: true
            visible: root.progress == null || root.progress.reason === ""
            printerModel: root.printerModel
            idleInstruction: "No index yet — the download button builds one without loading the preview."
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
