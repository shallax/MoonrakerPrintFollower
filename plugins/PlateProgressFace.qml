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
    property real travelVisualRatio: printerModel != null && printerModel.followerTravelVisualRatio !== undefined ? printerModel.followerTravelVisualRatio : lineScale
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
    // The camera's TWO transforms: viewScale/viewPanX/viewPanY are
    // the TARGET (the exact renderer works toward it, the model's
    // rasters bake it); displayScale/displayPanX/displayPanY are
    // what the scene actually shows. Idle, they coincide. A drag
    // moves both directly; a wheel updates the TARGET and the
    // display eases toward it while the bed point under the cursor
    // stays pinned.
    property real displayScale: 1.0
    property real displayPanX: 0.0
    property real displayPanY: 0.0
    // The zoom animation's focal state: the cursor's position and
    // the bed-fit pixel under it AT THE TARGET — the eased display
    // pan derives from these every tick, so scale and pan converge
    // as one coherent camera transform and the focal point never
    // wanders.
    property real _zoomAnchorX: 0.0
    property real _zoomAnchorY: 0.0
    property real _zoomBedX: 0.0
    property real _zoomBedY: 0.0
    // The drags applied since the last wheel: the eased pan carries
    // them on top of the focal derivation, so a held drag and a
    // live zoom coexist — the glide pans the scene, the deltas ride
    // along, and the ease lands exactly on the dragged target.
    property real _panDragDeltaX: 0.0
    property real _panDragDeltaY: 0.0
    // The toolhead dot's camera: the DISPLAY transform during a
    // gesture (the warm raster's picture — the whole scene rides
    // ONE camera), the target otherwise. The two coincide at every
    // interaction boundary, so the flip never jumps.
    readonly property real _dotScale: root._interactionActive ? root.displayScale : root.viewScale
    readonly property real _dotPanX: root._interactionActive ? root.displayPanX : root.viewPanX
    readonly property real _dotPanY: root._interactionActive ? root.displayPanY : root.viewPanY
    // The camera interaction state: while a gesture is live (or the
    // exact scene is still reassembling toward the final target),
    // the warm navigation raster owns the heavy-scene presentation.
    property bool _interactionActive: false
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
    // The view-settle timer (the awful-zoom report): a zoom or line
    // change during a drag must not re-walk the dense stack per tick
    // — the stack re-rasters ONCE, 60 ms after the last change (the
    // target-stability debounce: the exact scene launches while the
    // display still eases toward the target — the animation hides
    // the exact-render latency, not the other way round), and the
    // raster inputs feed the model at the same settle.
    signal viewSettled
    property alias settleTimer: viewSettleTimer
    Timer {
        id: viewSettleTimer
        interval: 60
        onTriggered: {
            _resetStack();
            root.viewSettled();
        }
    }

    // The pan-only gesture's exit drive: the zoom animator only runs
    // for wheels, so a drag's release re-checks the barrier until the
    // settle's invalidation and the exact re-render complete (the
    // interaction stays on the warm raster through it — never a stale
    // exact scene, never an endless interaction).
    Timer {
        id: panExitCheck
        interval: 80
        onTriggered: {
            if (root._interactionActive && !zoomAnimator.running && _exactReady()) {
                root._interactionActive = false;
            } else if (root._interactionActive && !zoomAnimator.running) {
                restart();  // the exact scene is still reassembling
            }
        }
    }

    // The smooth zoom's frame driver: an exponential ease-out toward
    // the CURRENT target (k per 16 ms tick — the display converges
    // in roughly 150-250 ms, feels immediate, never overshoots).
    // Retargeting is free: every tick reads the latest target, so a
    // wheel burst glides through its intermediate targets without
    // any velocity discontinuity. The pan derives from the focal
    // bed point each tick — scale and pan are one camera transform.
    Timer {
        id: zoomAnimator
        interval: 16
        repeat: true
        onTriggered: {
            var step = 0.30;
            var newScale = root.displayScale + (root.viewScale - root.displayScale) * step;
            if (Math.abs(root.viewScale - newScale) < 0.005) {
                newScale = root.viewScale;  // the exact snap at the end
            }
            root.displayScale = newScale;
            // The derived pan runs EVERY tick — held or not: the
            // focal glide pans the scene while a drag's accumulated
            // deltas ride on top, so the cursor's bed point stays
            // put even with the button down (the live origin-zoom
            // report — the raster once scaled in place while held).
            root.displayPanX = root._zoomAnchorX - root._zoomBedX * root.displayScale + root._panDragDeltaX;
            root.displayPanY = root._zoomAnchorY - root._zoomBedY * root.displayScale + root._panDragDeltaY;
            if (newScale === root.viewScale) {
                zoomAnimator.stop();
                if (root._exactReady() && !viewGesture.pressed) {
                    // The display stands at the target and the
                    // complete exact scene is presentation-ready:
                    // the soft-to-sharp swap.
                    root._interactionActive = false;
                }
            }
        }
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

    // The bed plot, exposed for the model's native renderer: the
    // same mapping the painters used, so the
    // worker-painted raster lands the identical picture.
    property var plot: mapping._plot

    function _rasterOf(layer) {
        // The render key's verdict: a
        // raster counts only while its key matches the surface's
        // CURRENT key — an old-view image never reads as current.
        // The PlateLayer publishes its pixel extents as INTs (the
        // engine cannot see inside a QImage variant); the plain-dict
        // fixtures fall back to the image's own width.
        if (layer == null) {
            return false;
        }
        if (layer.rasterWidth !== undefined) {
            return layer.rasterValid === true && layer.rasterWidth > 0;
        }
        return layer.raster !== undefined && layer.raster != null && layer.raster.width > 0;
    }

    function _baseOf(layer) {
        // The key verdict: a base counts only while its own render
        // key matches the surface's current one AND its file
        // transport succeeded. The plain-dict fixtures fall back
        // to presence.
        if (layer == null) {
            return false;
        }
        if (layer.baseValid !== undefined) {
            return layer.baseValid === true;
        }
        return layer.baseRaster !== undefined && layer.baseRaster != null && layer.baseRaster.width > 0;
    }

    function _travelsOf(layer) {
        if (layer == null) {
            return false;
        }
        if (layer.travelValid !== undefined) {
            return layer.travelValid === true;
        }
        return layer.travelRaster !== undefined && layer.travelRaster != null && layer.travelRaster.width > 0;
    }

    function _ghost(role) {
        var layers = root.progress != null ? root.progress.layers : null;
        return layers != null ? layers[role] : null;
    }

    function _fullRaster() {
        // The raster-only full state: the whole current layer shows
        // through the native Images — no vector, no canvas walk.
        if (!root.available()) {
            return false;
        }
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        return split != null && layer != null && split >= layer.motions && _rasterOf(layer);
    }

    function _prefixModelReady() {
        // The model's side of the prefix's usability: rendered for
        // a split not beyond the live one (a backward move hides it
        // until the fresh prefix lands) and keyed for the current
        // context (a zoom/pan/resize invalidates the old prefix
        // until the fresh one lands).
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        return layer != null && split != null && layer.prefixValid !== undefined && layer.prefixValid === true && layer.prefixSplit >= 0 && layer.prefixSplit <= split && split < layer.motions;
    }

    function _vectorInkless() {
        // No vector geometry exists to own the history: the scrub
        // vector is absent (a detached PlateLayer has no .classes)
        // or its classes are empty — the canvas will never paint.
        var current = _scrubVector();
        return current == null || current.classes === undefined || Object.keys(current.classes).length === 0;
    }

    function navigationData() {
        // The warm interaction raster's ready URL (the model
        // maintains it on the CONTENT state — pan and zoom never
        // regenerate it). The Image below binds this source at all
        // times, so the texture uploads while idle and the first
        // gesture's switch is a visibility flip, never a first-use
        // decode/upload hitch.
        return root.progress != null && root.progress.navigationData !== undefined ? root.progress.navigationData : "";
    }

    function _finishGesture() {
        // The gesture's end — a release OR a cancel (the grab died
        // outside the face, the live wedge: the scene never left the
        // warm raster). Nothing re-syncs here: the accumulated delta
        // already carries the dragged pan through the ease's
        // remaining glide (a re-anchor would jump the display — the
        // live report's release snap). The exit's re-check drives
        // the barrier until the complete exact scene is
        // presentation-ready.
        if (root._interactionActive) {
            panExitCheck.restart();
        }
    }

    function _enterInteraction() {
        // The first camera input: switch the heavy-scene
        // presentation to the warm interaction raster immediately —
        // no synchronous work rides this call. With no ready
        // navigation raster the exact scene simply stays (the
        // safe degraded path, same as before this feature).
        if (!root._interactionActive && navigationData() !== "") {
            root._interactionActive = true;
        }
    }

    function _exactReady() {
        // The exact-scene commit barrier: EVERY required component
        // of the current scene state must be complete and
        // presentation-ready — the full raster (or the partial
        // prefix over its delivered canvas), the grey base, the
        // travels' canvas, the ghosts — each with its scene-graph
        // Image actually Ready. One missing piece keeps the
        // interaction raster as the front buffer.
        var layers = root.progress != null ? root.progress.layers : null;
        if (!root.available() || layers == null || layers.current == null) {
            return false;
        }
        var current = layers.current;
        var split = root.progress.split;
        if (_fullRaster()) {
            if (progressRasterImage.status !== Image.Ready) {
                return false;
            }
            if (root.showTravels && _travelsOf(current) && progressTravelImage.status !== Image.Ready) {
                return false;
            }
        } else if (split != null && split > 0 && split < _motionsOf(current)) {
            // The partial scene: the printed history owned coherently
            // — the Ready prefix over a compatible canvas, or the
            // delivered vector owning the whole interval.
            if (_prefixModelReady() && !_partialPrefixReady()) {
                return false;
            }
            if (!_prefixModelReady() && !(root._textureReady && root._vectorCoversFrom === 0 && root._lastSplit === split)) {
                return false;
            }
            if (_partialBase() && _baseOf(current) && pendingBaseImage.status !== Image.Ready) {
                return false;
            }
        }
        if (root.showPrevious && _ghost("prev") != null && _rasterOf(_ghost("prev")) && prevGhostImage.status !== Image.Ready) {
            return false;
        }
        if (root.showNext && _ghost("next") != null && _rasterOf(_ghost("next")) && nextGhostImage.status !== Image.Ready) {
            return false;
        }
        return true;
    }

    function _fullPictureStanding() {
        // The full picture stands while the full state holds, and —
        // through the 100% -> partial entry — until the replacement
        // partial composition is presentation-ready. The hold rides
        // THIS predicate, never a visible-changed handler: the
        // binding cannot hide the picture before the hold exists
        // (the hide-then-arm order left one blank beat at the
        // entry). The handover is the transaction the contract
        // names: FULL A visible -> partial B presentation-ready ->
        // atomically expose B — never A hidden with nothing ready.
        if (root.progress == null || root.progress.layers == null || root.progress.layers.current == null) {
            return false;
        }
        if (_fullRaster()) {
            return true;
        }
        if (root._fullRasterSeen && root._fullSeenAnchor === root.progress.anchor && _leavingFull() && !_fullReleaseReady()) {
            return true;
        }
        return false;
    }

    function _prefixApplies() {
        // The split arithmetic still names the prefix as the history
        // owner — PURE structural terms, no transient inputs (the
        // model's validity and the Image's status flicker through a
        // re-publish; a hide fired on a flicker drops the standing
        // composition's memory). A full layer or a split below the
        // prefix boundary hides it legitimately.
        var progress = root.progress;
        var layer = progress != null && progress.layers != null ? progress.layers.current : null;
        return progress != null && progress.split != null && layer != null && layer.prefixSplit !== undefined && layer.prefixSplit >= 0 && progress.split > layer.prefixSplit && progress.split < _motionsOf(layer);
    }

    function _leavingFull() {
        // The split has moved out of the full state into a partial
        // one — the entry the hold transaction covers.
        var progress = root.progress;
        var layer = progress != null && progress.layers != null ? progress.layers.current : null;
        return progress != null && progress.split != null && layer != null && progress.split > 0 && progress.split < _motionsOf(layer);
    }

    function _fullReleaseReady() {
        // The replacement composition is presentation-ready: the
        // Ready prefix over a delivered compatible canvas, or the
        // delivered vector owning the whole interval — both behind
        // the one-beat show hold (the scene consumes a painted
        // texture in the sync AFTER the painted signal, so the full
        // picture must stand through that beat; nothing to paint
        // needs no beat).
        if (!_prefixModelReady()) {
            return root._textureReady && root._vectorCoversFrom === 0 && root._lastSplit === root.progress.split && !root._prefixShowHold;
        }
        if (root._vectorCoversFrom === -1 && _vectorInkless()) {
            return true;
        }
        return _partialPrefixReady() && !root._prefixShowHold;
    }

    function _partialPrefixReady() {
        // The compositor's side: the prefix may claim the printed
        // history only once its scene-graph Image has actually
        // uploaded the exact source — a model-side valid URL whose
        // image is still loading (or failed) owns nothing. A prefix
        // that has never been shown may appear only over a canvas
        // bitmap whose PAINTED delivery is confirmed compatible:
        // the full history, the tail from the prefix's own split,
        // or a vector with no geometry at all (there the canvas
        // never paints). Once shown, the prefix keeps its
        // ownership: the paints below it extend or re-derive from
        // its boundary, never leave a gap.
        if (!_prefixModelReady() || progressPrefixImage.status !== Image.Ready) {
            return false;
        }
        var layer = root.progress.layers.current;
        // EVERY vector-backed path rides the delivered record: the
        // painted coverage, the one-beat texture-sync, and the
        // split gate together — a canvas painted for an earlier
        // split is the standing OLD composition, never the current
        // demand's picture (the review's rapid-scrub policy — an
        // intermediate split must not present once the demand moved
        // on). No escape branch admits an older delivery: the
        // shown, the fresh-entry and the compatible-canvas cases
        // are all this one gate.
        var delivered = root._textureReady && root._lastSplit === root.progress.split && (root._vectorCoversFrom === 0 || root._vectorCoversFrom === layer.prefixSplit);
        return delivered || (root._vectorCoversFrom === -1 && _vectorInkless());
    }

    function _prefixFrom() {
        // The canvas tail starts where the prefix ended: the
        // accumulation draws only the motions beyond it. The Image
        // must be Ready: a prefix the scene does not contain yet
        // (loading, failed, or replaced) leaves the whole interval
        // to the vector path.
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        if (layer == null || split == null || layer.prefixValid !== true || layer.prefixSplit === undefined || layer.prefixSplit < 0 || layer.prefixSplit > split || progressPrefixImage.status !== Image.Ready) {
            return -1;
        }
        return layer.prefixSplit;
    }

    function _partialBase() {
        // The base marks the unprinted suffix of a PARTIAL layer
        // : a 0% layer draws nothing
        // (nothing has printed — no boundary to frame), and a full
        // layer's raster covers it entirely.
        if (!root.showBase || !root.available()) {
            return false;
        }
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        return split != null && layer != null && split > 0 && split < layer.motions;
    }

    // The scrub's vector: the current layer's prepared payload,
    // published as its own key — the ONLY geometry QML still walks,
    // and only for the within-layer delta .
    function _scrubVector() {
        if (root.progress == null) {
            return null;
        }
        if (root.progress.scrubVector !== undefined && root.progress.scrubVector != null) {
            return root.progress.scrubVector;
        }
        // The direct-publish fallback (the real-engine fixtures feed
        // plain dict payloads): the layer's own geometry.
        var layers = root.progress.layers;
        if (layers != null && layers.current != null && layers.current.classes !== undefined) {
            return layers.current;
        }
        return null;
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
    // The zoom/pan soft clamp's margin: at least this many pixels of
    // the bed stay visible on every side, however far the camera
    // pans (the live ruling).
    property real _panSoftMargin: 100.0

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

    function _softClampPan(x, y) {
        // The zoom/pan soft clamp: the camera moves freely while
        // any of the bed shows, but the bed may never leave the
        // viewport wholly — _panSoftMargin pixels stay visible on
        // every side (the live ruling: a fully off-bed view is
        // never useful, and the corner camera must not snap the bed
        // to the viewport's edge).
        var plot = mapping._plot;
        if (plot == null) {
            return {
                "x": x,
                "y": y
            };
        }
        var scale = root.viewScale;
        var bed = plot.bed;
        var left = bed.offsetX * scale;
        var right = left + bed.plotWidth * scale;
        var top = bed.offsetY * scale;
        var bottom = top + bed.plotHeight * scale;
        var lowX = root._panSoftMargin - right;
        var highX = width - root._panSoftMargin - left;
        var lowY = root._panSoftMargin - bottom;
        var highY = height - root._panSoftMargin - top;
        return {
            "x": lowX > highX ? x : Math.min(highX, Math.max(lowX, x)),
            "y": lowY > highY ? y : Math.min(highY, Math.max(lowY, y))
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
        // A programmatic camera change: the display lands with the
        // target (no pan easing for a button press).
        root.viewPanX = pan.x;
        root.viewPanY = pan.y;
        root.displayPanX = pan.x;
        root.displayPanY = pan.y;
        root._enterInteraction();
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
            root.displayPanX = pan.x;
            root.displayPanY = pan.y;
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
    // The prefix/vector ownership handoff: the canvas bitmap's own
    // coverage (the motion index below which it does NOT draw — 0
    // means the full history), and the one-frame hold that keeps
    // the old prefix on screen until the canvas has repainted the
    // interval the prefix used to own (the swap must be atomic:
    // never a frame with neither renderer owning the history).
    property int _vectorCoversFrom: -1
    property bool _prefixHold: false
    property bool _prefixWasShown: false
    // The 100% -> partial entry's transaction: the full raster's
    // picture stands until the replacement partial composition is
    // presentation-ready. The seen flag records that the full state
    // stood (the hold rides the standing predicate itself — never a
    // visible-changed arm, which arrived one hide too late), and the
    // anchor tag keeps the record honest across layer switches.
    property bool _fullRasterSeen: false
    property int _fullSeenAnchor: -2
    // The transaction's standing picture: the full raster's last URL
    // survives the entry (a partial payload's rasterData is empty —
    // the hold must keep the PIXELS, not just the visibility flag).
    property string _heldFullSource: ""
    // A paint begun while the entry's hold stood arms the handover
    // beat at its delivery — the release always waits one beat after
    // the LAST delivery (the scene consumes a painted texture in the
    // sync after the painted signal).
    property bool _entryPaintArmedHold: false
    // The prefix's one-beat show lag after a canvas delivery: the
    // scene consumes the painted texture one frame later (the
    // review's hybrid frame).
    property bool _prefixShowHold: false
    // The threaded canvas's scene texture trails its paint by one
    // frame: a paint's coverage record alone must never admit the
    // prefix — the painted delivery confirms the bitmap the scene
    // is about to show.
    property bool _textureReady: false
    // The hold's expiry waits one frame past the painted delivery:
    // the threaded canvas's scene texture commits in the sync AFTER
    // the painted signal, and a hide in the same sync would leave
    // one frame with neither owner.
    property bool _beatPending: false
    Timer {
        id: holdExpiryTimer
        interval: 16
        onTriggered: {
            // Only a beat an arming actually scheduled may clear the
            // standing flags — a stale armed trigger (a choreography
            // that ended by another path) firing mid-steady-state
            // would wipe the shown record and drop the composition's
            // memory (the scrub flake: the next paint lost the
            // prefix's ownership and the frame showed the tail
            // alone).
            if (!root._beatPending) {
                return;
            }
            root._beatPending = false;
            root._prefixHold = false;
            root._prefixShowHold = false;
            // The shown record follows the VISIBLE state: clear it
            // only when the beat actually left the prefix hidden. A
            // standing prefix (the entry's handover keeps it visible
            // through the readiness gate) must keep its memory — the
            // scrub flake: the beat wiped the record while the
            // prefix stood, and the next paint lost the ownership.
            if (!progressPrefixImage.visible) {
                root._prefixWasShown = false;
            }
        }
    }
    // The scrub vector's SOURCE identity: a same-anchor payload swap
    // (an empty fixture replaced by the real layer) must reset the
    // accumulated bitmap — the delta path assumes ink it never drew.
    property int _vectorSourceMotions: -1
    property string _vectorSourceClasses: ""
    // The travels' SOURCE identity: when the travel payload arrives
    // (or the layer changes), the canvas redraws them from the
    // layer's start — the delta path alone would assume ink the
    // canvas never drew.
    property int _travelsSourceMotions: -1
    property int _travelsSourceReady: 0
    // The raster state keys: each canvas records the key of the LAST
    // picture it painted, and a repaint request whose key matches is
    // skipped. That covers the unchanged state (a quiet poll, a
    // payload re-publish of the same content) — it is NOT a cache of
    // previously visited layers: an A → B → A flip re-walks A, since
    // each canvas holds one image . The measured walk cost is
    // ~150 ms for a 150k-point stack at 150% zoom; whether revisit
    // caching is worth a bounded raster pool is decided on the
    // end-to-end measurement, not assumed here. One key per canvas,
    // covering only that canvas' own inputs, so a toggle repaints
    // only what it touches.
    property string _pendingKey: ""
    property string _progressKey: ""

    function _viewKey() {
        return [root.viewScale, root.viewPanX, root.viewPanY, root.lineScale, root.compact ? 1 : 0, width, height, root.toolpathWidthPx()].join("|");
    }

    function _motionsOf(layer) {
        return layer != null && layer.motions !== undefined ? layer.motions : -1;
    }

    function _pendingKeyOf() {
        var progress = root.progress;
        var layers = progress != null ? progress.layers : null;
        // The base raster's own arrival is part of the key: the grey
        // sibling landing must repaint the pending canvas even with
        // every other input unchanged . The
        // SPLIT rides the key too: the base exists only for the
        // partial states — a 0% or 100% move must clear it.
        return [(progress != null ? progress.anchor : -1), layers != null ? _motionsOf(layers.current) : -1, layers != null && _baseOf(layers.current) ? 1 : 0, progress != null && progress.split != null ? progress.split : -1, root.showBase ? 1 : 0, root.available() ? 1 : 0, _viewKey()].join("|");
    }

    function _progressKeyOf() {
        var progress = root.progress;
        var layers = progress != null ? progress.layers : null;
        // The raster, travel and PREFIX arrivals ride the key too:
        // the prefix's landing must reset the stack so the canvas
        // redraws only the tail beyond it.
        return [(progress != null ? progress.anchor : -1), layers != null ? _motionsOf(layers.current) : -1, layers != null && _rasterOf(layers.current) ? 1 : 0, layers != null && _travelsOf(layers.current) ? 1 : 0, layers != null && layers.current != null && layers.current.prefixSplit !== undefined ? layers.current.prefixSplit : -1, progress != null && progress.split != null ? progress.split : -1, root.showTravels ? 1 : 0, root.available() ? 1 : 0, _viewKey()].join("|");
    }

    function _holdPrefixThroughRepaint() {
        // The prefix's model-side validity flips with the publish,
        // but the canvas repaints a frame later: hold the old
        // picture on screen until the vector has repainted the
        // interval — the swap is atomic, never a frame with
        // neither renderer owning the printed history. The repaint
        // must be FORCED here: the invalidation's publish can land
        // without a key change (the settle already consumed it),
        // and an unrequested paint would never clear the hold.
        if (!_prefixModelReady() && root._prefixWasShown) {
            holdExpiryTimer.stop();
            root._prefixHold = true;
            progressCanvas.requestPaint();
        }
    }

    function _resetStack() {
        // The canvas repaints on a toggle flip, a resize or an
        // anchor change; the ghost Images bind themselves and need
        // no repaint gating. A canvas whose key still matches keeps
        // its image (the raster-reuse ruling above).
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
            _holdPrefixThroughRepaint();
        }
    }

    onProgressChanged: {
        if (root.progress == null || root.progress.layers == null) {
            root._lastSplit = -1;
            root._anchor = -1;
            root._prefixHold = false;
            root._prefixWasShown = false;
            return;
        }
        if (root.progress.anchor !== root._anchor) {
            root._anchor = root.progress.anchor;
            _resetStack();
            return;
        }
        // The same anchor, new content: the loading→loaded transition
        // flips the motions from -1 to the real count (and the
        // availability), and the pending canvas' key catches it — the
        // cleared raster must never read as already drawn (the live
        // report: the pending base stayed missing on the first load).
        var pendingKey = _pendingKeyOf();
        if (pendingKey !== root._pendingKey) {
            root._pendingKey = pendingKey;
            pendingCanvas.requestPaint();
        }
        // The progress repaint follows its OWN key: an unchanged split/anchor/payload — a
        // raster or ghost arrival, a quiet poll — never wakes the
        // painter, whose partial path walks dense geometry.
        var progressKey = _progressKeyOf();
        if (progressKey !== root._progressKey) {
            root._progressKey = progressKey;
            progressCanvas.requestPaint();
        }
        // The prefix's model-side validity may flip WITHOUT a key
        // change (the context invalidation's publish: the view key
        // was already consumed by the settle's reset): hold the old
        // picture until the canvas's repaint owns the interval.
        _holdPrefixThroughRepaint();
        // The interaction's exit rides the exact scene's OWN commits
        // (a pan-only gesture never runs the zoom animator): the
        // settle's invalidation publish flips the assets stale, the
        // re-render's publish flips them fresh — the barrier's
        // verdict swaps the scene back exactly when the complete
        // exact scene is presentation-ready.
        if (root._interactionActive && !zoomAnimator.running && _exactReady()) {
            root._interactionActive = false;
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
            _holdPrefixThroughRepaint();
        }
    }
    onLineScaleChanged: {
        _publishView();
        root.settleTimer.restart();
    }
    onViewScaleChanged: {
        _publishView();
        root.settleTimer.restart();
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
        root.settleTimer.restart();
    }
    onViewPanYChanged: {
        _publishView();
        root.settleTimer.restart();
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
            root.settleTimer.restart();
        }
        function onHeightChanged() {
            root.settleTimer.restart();
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
        // Opacity, never visibility: hiding a Canvas discards its
        // buffer and the show rebuilds it (the live report — the
        // loading flash churned the grid on every seek). An opaque
        // zero keeps the canvas alive and painted for free; the
        // input stays gated by the availability. During a camera
        // interaction the grid rides the WARM RASTER (the complete
        // scene switches as one unit — the grid must never move
        // independently of the geometry).
        opacity: root.available() && !root._interactionActive ? 1.0 : 0.0
        enabled: root.available()
        printerModel: root.printerModel
        plate: null
        viewScale: root.viewScale
        viewPanX: root.viewPanX
        viewPanY: root.viewPanY
    }

    // The interaction scene (the pan/zoom navigation raster): one
    // flattened warm full-bed composite at a fixed 4x backing,
    // presented at the DISPLAY transform — the sole heavy-scene
    // representation during any camera gesture. The source binds at
    // all times (the texture uploads while idle; the first gesture
    // flips the visibility, never the source), and the 4x content
    // displayed at width/height = face x displayScale is exactly
    // the displayScale/4 presentation of the specification.
    Image {
        id: navigationImage
        x: root.displayPanX
        y: root.displayPanY
        width: root.width * root.displayScale
        height: root.height * root.displayScale
        visible: root._interactionActive && navigationData() !== ""
        source: navigationData()
        smooth: true
    }

    // The EXACT scene: everything below — the raster stack and the
    // vector canvases — belongs to one presentation unit. During a
    // camera interaction the unit fades out WHOLE (opacity, never
    // visibility: the hidden canvases must keep painting so the
    // exact generation can complete behind the interaction raster),
    // and returns only once the commit barrier passes.
    Item {
        id: exactScene
        anchors.fill: parent
        opacity: root._interactionActive ? 0.0 : 1.0

        // The raster stack, bottom to top (the live order — ghosts,
        // base, prefix, tail, travels): the ghost layers, the grey
        // base, the printed native prefix, then the vector tail canvas
        // with its travels. The rasters are SCENE-GRAPH Images — the
        // engine's Canvas cannot hold a raster image reliably (its
        // internal image cache re-blits a drawn URL on every later
        // paint, and a QImage variant segfaults — engine-proven), so
        // only the genuinely vector paths keep canvases. Transparency
        // does the blending between the layers.

        // The raster-only full state: the whole
        // layer and its travels blit from the native data URLs; the
        // progress canvas below clears itself while these show.
        Image {
            id: progressRasterImage
            anchors.fill: parent
            visible: _fullPictureStanding()
            // The entry's hold keeps the PICTURE: while the full state
            // stands the source is the fresh raster; through the
            // handover it is the captured URL (the partial payload's
            // rasterData is empty — re-binding it would clear the
            // standing pixels, a blank the visibility flag cannot
            // hide).
            source: _fullPictureStanding() ? (_fullRaster() ? root.progress.layers.current.rasterData : root._heldFullSource) : ""
            onVisibleChanged: {
                // The entry's hold needs no handler: the standing
                // predicate owns the transaction (a hide-fired arm
                // would arrive one beat after the picture was already
                // gone). The seen record and the held source re-arm
                // on a genuine full show.
                if (visible) {
                    root._fullRasterSeen = root._fullRaster();
                    root._fullSeenAnchor = root.progress != null ? root.progress.anchor : -2;
                    root._heldFullSource = root.progress != null && root.progress.layers != null && root.progress.layers.current != null ? root.progress.layers.current.rasterData : "";
                }
            }
        }
        Image {
            id: progressTravelImage
            anchors.fill: parent
            opacity: 0.8
            visible: root.showTravels && _fullRaster() && _travelsOf(root.progress.layers.current)
            source: visible ? root.progress.layers.current.travelData : ""
        }
        // The ghost layers: the worker's rasters at ghost opacity —
        // role-free assets, the opacity applied at composition.
        // Until a ghost's raster lands it draws nothing —
        // the context layer appears a beat after the seek, never blocks
        // it.
        Image {
            id: prevGhostImage
            anchors.fill: parent
            opacity: 0.30
            visible: root.available() && root.showPrevious && _ghost("prev") != null && _rasterOf(_ghost("prev"))
            source: visible ? _ghost("prev").rasterData : ""
        }
        Image {
            id: nextGhostImage
            anchors.fill: parent
            opacity: 0.30
            visible: root.available() && root.showNext && _ghost("next") != null && _rasterOf(_ghost("next"))
            source: visible ? _ghost("next").rasterData : ""
        }

        // The grey whole-layer base: the
        // native grey sibling as a scene-graph Image, with the vector
        // canvas below as the pre-arrival fallback.
        Image {
            id: pendingBaseImage
            anchors.fill: parent
            opacity: 0.55
            visible: _partialBase() && _baseOf(root.progress.layers.current)
            source: visible ? root.progress.layers.current.baseData : ""
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
                var layer = root.progress.layers.current;
                // The base marks the unprinted suffix of a PARTIAL layer:
                // a 0% layer draws nothing (nothing has printed — no
                // boundary to frame), and a full layer's raster covers
                // it entirely. The native sibling's arrival hides this
                // fallback (its Image above takes over).
                if (!_partialBase() || layer == null || _baseOf(layer)) {
                    return;
                }
                var current = _scrubVector();
                if (current == null) {
                    return;
                }
                _drawLayer(ctx, current, 0.55, -1, true, -1);
            }
        }

        // The printed PREFIX (the measured verdict: the partial states'
        // QML walk costs ~900 ms at 500k motions): the worker paints
        // the motions below the split and the canvas below draws only
        // the live delta's tail. Declared ABOVE the progress canvas and
        // BELOW the grey base (the live stack order: ghosts, base,
        // prefix, tail) — the base must never wash over printed
        // geometry. Hidden while stale (a backward move renders a fresh
        // prefix first), while its image has not uploaded, and — for
        // one frame — held on screen while the canvas repaints the
        // interval the prefix just relinquished.
        Image {
            id: progressPrefixImage
            anchors.fill: parent
            // The shown prefix STAYS while the split arithmetic still
            // names it as the history owner — a repaint in flight, a
            // delivery from an earlier split, or a transient
            // validity/status flicker through a re-publish must
            // never hide it: the standing picture is the complete
            // OLD composition, and a prefix-less frame would drop
            // the printed history. The normal readiness gate governs
            // only once the delivered canvas IS the current demand's
            // picture.
            visible: _partialPrefixReady() || root._prefixHold || (root._prefixWasShown && _prefixApplies() && !(root._textureReady && root._lastSplit === root.progress.split))
            source: (_prefixModelReady() || root._prefixHold || (root._prefixWasShown && _prefixApplies())) && root.progress != null && root.progress.layers != null && root.progress.layers.current != null ? root.progress.layers.current.prefixData : ""
            onVisibleChanged: {
                // Track what was actually on screen. A hide caused by
                // the model's invalidation (prefixValid flipped false —
                // the render-key mismatch) arms the HOLD right here: the
                // picture stays up until the canvas's full bitmap is
                // delivered and committed (the expiry timer's beat after
                // the painted signal). Hides from the split arithmetic
                // (a full layer, a backward move) are legitimate — the
                // canvas owns the interval by then.
                if (visible) {
                    root._prefixWasShown = true;
                    // The settled single-owner trim: a canvas that
                    // painted the FULL history before this prefix
                    // showed must repaint to the tail alone — its
                    // full bitmap stacked under the prefix doubles
                    // the prefix region's ink (the parity seam: 42
                    // vs 21 at lineScale 0.7). The repaint is FORCED
                    // here: the show can land after the key's paint
                    // already consumed itself.
                    progressCanvas.requestPaint();
                } else if (root._prefixWasShown && root.progress != null && root.progress.layers != null && root.progress.layers.current != null && root.progress.layers.current.prefixValid === false) {
                    holdExpiryTimer.stop();
                    root._prefixHold = true;
                    progressCanvas.requestPaint();
                } else if (!root._prefixApplies()) {
                    // The hide is terminal (a full layer, a sub-prefix
                    // split, a layer change): reset the shown record.
                    // A transient hide keeps the memory — the standing
                    // extension re-asserts the picture.
                    root._prefixHold = false;
                    root._prefixWasShown = false;
                }
            }
            onStatusChanged: progressCanvas.requestPaint()
        }

        Canvas {
            id: progressCanvas
            anchors.fill: parent
            renderTarget: Canvas.Image
            renderStrategy: Canvas.Threaded
            onPainted: {
                // The paint's bitmap is delivered: its coverage record
                // now describes the scene's committed texture. A held
                // prefix over a delivered FULL bitmap can finally
                // relinquesh — one frame later, after the scene pulls
                // the texture (the expiry timer's beat).
                root._textureReady = true;
                if (root._entryPaintArmedHold) {
                    // The entry's delivery: the handover beat starts
                    // here — the full picture stands until one frame
                    // past this painted signal (the atomic previous
                    // -> next swap, zero blank frames).
                    root._entryPaintArmedHold = false;
                    root._beatPending = true;
                    holdExpiryTimer.restart();
                }
                if (root._prefixHold && root._vectorCoversFrom === 0) {
                    root._beatPending = true;
                    holdExpiryTimer.restart();
                }
            }
            onPaint: {
                var ctx = getContext("2d");
                // The settled single-owner trim is a REFINEMENT of an
                // already presentation-complete picture (the full
                // bitmap under the prefix is invisible overlap): its
                // repaint must not withdraw the standing readiness —
                // the seek's ready commit waits for no trim.
                var trimOnly = root.progress != null && root._textureReady && root._lastSplit === root.progress.split && root._vectorCoversFrom === 0 && _prefixFrom() > 0;
                // The committed texture is now one paint behind — the
                // painted signal re-arms the confirmation when the
                // bitmap is delivered.
                if (!trimOnly) {
                    root._textureReady = false;
                }
                // A paint begun while the 100% -> partial hold stands
                // arms the handover beat for its delivery (the
                // release must wait one beat after the LAST delivery,
                // and the show hold's own arming rides this flag —
                // once the handover is over the predicate is false
                // and later polls never re-arm it).
                root._entryPaintArmedHold = _fullPictureStanding() && _leavingFull();
                if (root._entryPaintArmedHold) {
                    root._prefixShowHold = true;
                    holdExpiryTimer.stop();
                }
                if (!root.available() || mapping._plot == null) {
                    // The unavailable surface clears its own ink — the
                    // old raster must never read through the loading text
                    // (the live report).
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._paintsSinceReset = 0;
                    root._vectorCoversFrom = -1;
                    return;
                }
                var layer = root.progress.layers.current;
                if (layer == null) {
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._paintsSinceReset = 0;
                    root._vectorCoversFrom = -1;
                    return;
                }
                var split = root.progress.split;
                // The FULL-layer case: the
                // PlateLayer's OWN motions are the boundary, and the
                // scene-graph Images own the picture — scrubVector is
                // null by design here (a 100% seek, a detached whole
                // layer), so a full raster hit never depends on the
                // giant vector. The vector canvas clears so nothing
                // doubles up.
                if (_fullRaster()) {
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = split;
                    root._paintsSinceReset += 1;
                    root._vectorCoversFrom = -1;
                    return;
                }
                var current = _scrubVector();
                if (current == null) {
                    // No vector and no full raster yet (a cold full seek,
                    // a 0% state): nothing to accumulate.
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._paintsSinceReset = 0;
                    root._vectorCoversFrom = -1;
                    return;
                }
                if (split == null) {
                    // No boundary to draw at — a print without a
                    // position. The layer is its whole base and any
                    // accumulated fill goes with the split.
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._paintsSinceReset = 0;
                    root._progressDirty = false;
                    root._vectorCoversFrom = -1;
                    return;
                }
                // A backward split (a restart), a toggle flip, an anchor
                // change or a SAME-ANCHOR payload swap clears the image
                // (the delta path assumes the bitmap holds the previous
                // vector's ink — a swapped source never drew it); the
                // accumulation also re-rasters fully on its own cadence
                // so it cannot drift. Otherwise the canvas keeps its
                // image and only the new delta is stroked on top.
                var vectorMotions = _motionsOf(current);
                var vectorClasses = current.classes !== undefined ? Object.keys(current.classes).join("|") : "";
                var vectorSourceChanged = vectorMotions !== root._vectorSourceMotions || vectorClasses !== root._vectorSourceClasses;
                root._vectorSourceMotions = vectorMotions;
                root._vectorSourceClasses = vectorClasses;
                var prefixFrom = _prefixFrom();
                if (prefixFrom <= 0 && root._prefixWasShown && root._vectorCoversFrom !== 0 && layer.prefixData !== undefined && layer.prefixData !== "" && split != null && split < layer.motions) {
                    // The stale prefix is being REPLACED (a fresh URL is
                    // in flight): hold the complete old composition —
                    // the held prefix plus this bitmap's old tail — until
                    // the replacement's Image lands and re-triggers the
                    // paint (the atomic previous -> next handoff — the
                    // review's reverse-scrub hybrid frame). A repaint
                    // here would clear the old tail and expose the stale
                    // prefix alone. The skip precedes the reset below.
                    root._progressDirty = true;
                    return;
                }
                var resetPainted = false;
                if (root._progressDirty || split < root._lastSplit || root._paintsSinceReset >= 20 || vectorSourceChanged) {
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._progressDirty = false;
                    root._paintsSinceReset = 0;
                    resetPainted = true;
                }
                // The printed portion, coloured in per feature class from
                // the last painted split up to the live one (the H3
                // floor). The partial scrub keeps the vector delta path;
                // a native prefix below shortens the walk to its tail.
                var coversBefore = root._vectorCoversFrom;
                if (!resetPainted && prefixFrom <= 0 && root._vectorCoversFrom !== 0) {
                    // The prefix no longer owns the history (loading,
                    // stale, or invalidated) but the canvas does not hold
                    // the full picture (nothing painted yet, or only a
                    // tail): repaint the FULL interval — a partial bitmap
                    // under a vanished prefix is the live scrub's missing
                    // history.
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._paintsSinceReset = 0;
                    root._progressDirty = false;
                    resetPainted = true;
                } else if (!resetPainted && prefixFrom > 0 && root._vectorCoversFrom === 0) {
                    // The prefix is Ready over the canvas's FULL bitmap (a
                    // re-show after a scrub through 100% or another layer):
                    // trim to the prefix's own boundary — the commit lag's
                    // stale texture is the full bitmap, complete either
                    // way, and every scrub path settles to the SAME
                    // composition.
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = -1;
                    root._paintsSinceReset = 0;
                    root._progressDirty = false;
                    resetPainted = true;
                }
                // A prefix that has never shown leaves the WHOLE interval
                // to the canvas — its first paint must cover from the
                // layer's start, not merely from the prefix's boundary.
                // The trim above (only ever over a full bitmap) paints
                // from the boundary instead.
                var from = resetPainted && prefixFrom > 0 && coversBefore === 0 ? prefixFrom : Math.max(root._lastSplit, root._prefixWasShown ? prefixFrom : -1);
                var fresh = resetPainted || root._lastSplit < 0;
                _drawLayer(ctx, current, 1.0, split, false, from);
                // The bitmap's coverage below this paint's start: a full
                // paint covers from the layer's start, a tail paint
                // relies on the prefix for the rest, a delta paint
                // extends the existing coverage. A vector with no
                // geometry records nothing: an empty bitmap must never
                // read as a full one (the prefix would trust a hole).
                if (fresh) {
                    root._vectorCoversFrom = vectorClasses !== "" ? (from > 0 ? from : 0) : -1;
                }
                // The travels: the lines only. CURRENT layer only, and
                // only where the toolhead has already passed (the live
                // rulings). The prefix carries NO travels, so a cleared
                // canvas redraws them from the layer's start — the
                // travels below the prefix boundary stay visible. The
                // accumulated delta path keeps its own start (the
                // canvas already holds the printed travels).
                if (root.showTravels) {
                    // The prefix carries NO travels: a cleared canvas
                    // redraws them from the layer's start, and a NEW
                    // travel source does too — the delta path alone
                    // would assume ink the canvas never drew (the
                    // travels arriving with the prefix already in
                    // place).
                    var sourceMotions = root.progress.layers != null ? _motionsOf(root.progress.layers.current) : -1;
                    var sourceReady = _travelsOf(root.progress.layers.current) ? 1 : 0;
                    var travelsChanged = sourceMotions !== root._travelsSourceMotions || sourceReady !== root._travelsSourceReady;
                    root._travelsSourceMotions = sourceMotions;
                    root._travelsSourceReady = sourceReady;
                    var travelFrom = (resetPainted || travelsChanged) ? -1 : root._lastSplit;
                    _drawTravels(ctx, current.travels, split, travelFrom);
                }
                root._lastSplit = split;
                root._paintsSinceReset += 1;
            }
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
        // The motions within a segment never decrease: the first
        // edge at or after `from` is a binary search, never a
        // linear walk — the tail after a native prefix would
        // otherwise re-scan the whole printed portion on every
        // paint.
        if (from <= 0 || points.length < 2) {
            return 1;
        }
        var low = 1;
        var high = points.length - 1;
        while (low < high) {
            var mid = (low + high) >> 1;
            if (points[mid][2] < from) {
                low = mid + 1;
            } else {
                high = mid;
            }
        }
        // Every motion below `from`: the first edge past the
        // segment's end — exactly the linear walk's off-the-end
        // result, which the callers read as "nothing to draw".
        if (points[low][2] < from) {
            return points.length;
        }
        return low;
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
                while (_edgePrinted(points, i, split)) {
                    ctx.lineTo((offsetX + (points[i][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i][1]) * sy) * scale + panY);
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
        x: root.dot != null && mapping._plot != null ? root._dotPanX + (mapping._plot.bed.offsetX + (root.dot.x - mapping._plot.bed.bedXMin) * mapping._plot.sx) * root._dotScale - width / 2 : 0
        y: root.dot != null && mapping._plot != null ? root._dotPanY + (mapping._plot.bed.offsetY + (mapping._plot.bed.bedYMax - root.dot.y) * mapping._plot.sy) * root._dotScale - height / 2 : 0
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
            if (target === root.viewScale && root.displayScale === root.viewScale) {
                return;
            }
            root._enterInteraction();
            // The bed-fit pixel under the cursor AT THE TARGET: the
            // eased display pan derives from it every tick, so scale
            // and pan converge as one coherent camera transform and
            // the focal point never wanders.
            if (target <= 1.0) {
                // 100% is the fit: centred, panning off (the live
                // ruling — no pan at full zoom).
                root.viewScale = 1.0;
                root.viewPanX = 0.0;
                root.viewPanY = 0.0;
            } else {
                // The point under the cursor stays put, computed
                // from the CURRENT DISPLAY transform (the retarget
                // always starts where the camera actually is). The
                // pan is only SOFT-clamped: the bed may never leave
                // the viewport wholly — _panSoftMargin pixels stay
                // visible on every side (the live ruling). Beyond
                // that the camera stays exactly where the focal
                // point puts it; only the 100% fit (above)
                // recentres.
                root.viewScale = target;
                root.viewPanX = wheel.x - (wheel.x - root.displayPanX) / root.displayScale * target;
                root.viewPanY = wheel.y - (wheel.y - root.displayPanY) / root.displayScale * target;
                var clamped = root._softClampPan(root.viewPanX, root.viewPanY);
                root.viewPanX = clamped.x;
                root.viewPanY = clamped.y;
            }
            root._zoomAnchorX = wheel.x;
            root._zoomAnchorY = wheel.y;
            root._zoomBedX = (wheel.x - root.viewPanX) / root.viewScale;
            root._zoomBedY = (wheel.y - root.viewPanY) / root.viewScale;
            // The wheel re-owns the camera promise: the ease runs
            // from the CURRENT display (held-drag pan included) and
            // the deltas from here on ride the glide.
            root._panDragDeltaX = 0.0;
            root._panDragDeltaY = 0.0;
            zoomAnimator.restart();
        }
        onPressed: function (mouse) {
            // The press moves nothing: the camera stays exactly where
            // it is (no start snap) — a running ease keeps gliding
            // underneath and the first move's delta applies from this
            // grab point.
            root._enterInteraction();
            root._dragX = mouse.x;
            root._dragY = mouse.y;
        }
        onPositionChanged: function (mouse) {
            if (!pressed || root.displayScale <= 1.0) {
                return;
            }
            // The pan tracks the pointer directly: the display AND
            // the target move together (no pan easing — the nav
            // raster makes the direct pan cheap). The delta also
            // accumulates into the eased pan, so a drag during a
            // zoom converges exactly onto the dragged target. The
            // soft clamp applies the APPLIED delta: once the bed's
            // margin touches the viewport's edge, the camera stops
            // there while the pointer keeps moving.
            var dx = mouse.x - root._dragX;
            var dy = mouse.y - root._dragY;
            var clamped = root._softClampPan(root.viewPanX + dx, root.viewPanY + dy);
            var appliedX = clamped.x - root.viewPanX;
            var appliedY = clamped.y - root.viewPanY;
            root.viewPanX = clamped.x;
            root.viewPanY = clamped.y;
            root.displayPanX += appliedX;
            root.displayPanY += appliedY;
            root._panDragDeltaX += appliedX;
            root._panDragDeltaY += appliedY;
            root._dragX = mouse.x;
            root._dragY = mouse.y;
        }
        onReleased: root._finishGesture()
        onCanceled: root._finishGesture()
        onDoubleClicked: {
            // The reset is a programmatic camera change: the target
            // and the display land together, and the exact scene
            // returns only once the barrier passes.
            root._enterInteraction();
            root.viewScale = 1.0;
            root.viewPanX = 0.0;
            root.viewPanY = 0.0;
            root.displayScale = 1.0;
            root.displayPanX = 0.0;
            root.displayPanY = 0.0;
            zoomAnimator.stop();
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
                    root._enterInteraction();
                    // A direct manipulation: the display follows the
                    // handle immediately (like the drag pan).
                    root.displayScale = target;
                    root.viewScale = target;
                    if (target <= 1.0) {
                        root.viewPanX = 0.0;
                        root.viewPanY = 0.0;
                        root.displayPanX = 0.0;
                        root.displayPanY = 0.0;
                    }
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
