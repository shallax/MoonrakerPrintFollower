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
    // The raster source the CURRENT gesture entered with (the entry
    // latch): set at _enterInteraction, irrelevant once the gesture
    // ends (the idle binding reverts to the live eligible URL).
    property string _gestureNavSource: ""
    // The retained frame's standing state, written by ITS handler —
    // the live prefix's visible reads this one-way value instead of
    // the retained ITEM's visible, whose mutual read the engine
    // flagged as a binding loop (the live warning).
    property bool _retainedStanding: false
    // The full raster's status, mirrored for the canvas: that paint
    // runs on the canvas' render thread, where a live read of this
    // item's status lands whenever it lands (the handover's yield
    // fired on some runs and not others). Written by the signal that
    // owns the transition, read like any other property.
    property bool _rasterStatusReady: false
    property bool _rasterStatusFailed: false
    // The travels raster's status, mirrored for the SAME beat: the
    // travels are the second half of the full state's picture and
    // their texture decodes on its own clock, so the class raster's
    // arrival must not retire the canvas while the travels are still
    // decoding (they blanked for the decode), and the travels Image
    // must not present before the picture's own bake does (its
    // texture can land first, which stood the new travels over the
    // canvas' held picture — the reported shift).
    property bool _travelsStatusReady: false
    property bool _travelsStatusFailed: false
    // The prefix Image's Ready status as a one-way mirror: the
    // visible binding reads THIS (written by the image's own status
    // handler), never the status directly — reading the live status
    // there closed a binding cycle through the same image's source
    // and handlers (the engine's recurring live loop warning).
    property bool _prefixStatusReady: false
    // ...and its FAILED status, mirrored for the same reason the
    // travels' is: a load that errors will never reach Ready, so a
    // barrier waiting on it could never pass.
    property bool _prefixStatusFailed: false
    // The zoom the carried tail last painted at: a mid-gesture
    // settle repaints it only on a real scale change.
    property real _carryZoom: 1.0
    property real _pressX: 0.0
    property real _pressY: 0.0
    property real _pressPanX: 0.0
    property real _pressPanY: 0.0
    // The RETAINED previous prefix (the atomic handover): the last
    // successfully uploaded prefix's source and boundary, frozen at
    // its Ready — shown while the live replacement loads, so the old
    // complete composition is never torn down early. The pixels are
    // evidence about ONE layer, so the record carries the anchor they
    // were frozen for: the payload's anchor names the served layer
    // (a print switch moves it), and a seek that lands on the same
    // motions and the same split arithmetic must never re-stand this
    // layer's pixels over that one.
    property string _retainedPrefixSource: ""
    property int _retainedPrefixSplit: -1
    property int _retainedPrefixAnchor: -2
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
        interval: 5000
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
            // The release alone ends an interaction: a delayed exit
            // check landing mid-second-press flipped the state under
            // a live drag and froze the camera (the two-quick-pans
            // wedge — the drags went dead).
            if (root._interactionActive && !viewGesture.pressed && !zoomAnimator.running && _exactReady()) {
                endInteraction();
            } else if (root._interactionActive && !viewGesture.pressed) {
                // The barrier is still pending: keep re-checking it,
                // and land the repaint the hold deferred — a wheel
                // never releases, so nothing else can complete it
                // once the camera has settled.
                if (!zoomAnimator.running && root._progressDirty) {
                    progressCanvas.requestPaint();
                }
                // The camera has settled, so the hold no longer needs
                // the model to stand still: release the publication
                // freeze HERE, while the warm raster still fronts the
                // picture. Withholding it until the handover made the
                // two wait on each other — the freeze withheld the
                // very assets the barrier was waiting for, and a
                // wheel-only gesture has no release to break the
                // cycle. The handover is unchanged: the exact scene
                // rebuilds BEHIND the raster and the barrier still
                // decides when it may show.
                if (!zoomAnimator.running && !root.settleTimer.running) {
                    root._releasePublishFreeze();
                }
                restart();
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
                    endInteraction();
                } else if (root._interactionActive && !viewGesture.pressed) {
                    // The barrier held. The glide is over, so the
                    // animator is no longer what drives the exit —
                    // and a wheel never releases, which leaves the
                    // pan-only re-check as the only thing that can
                    // hand back.
                    panExitCheck.restart();
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

    function _travelsShown() {
        // The travels belong to the pictured state: only the full
        // state has a travels raster, and only when the scene wants
        // them. One predicate for the image's visibility AND its
        // source, so the decode starts exactly when they are shown.
        return root.showTravels && _fullRaster() && _travelsOf(root.progress.layers.current);
    }

    function _travelsPending() {
        // Shown, and their texture is not here yet. An Error releases
        // the handover: a travel raster that will never decode must
        // not hold the picture (the failed-mirror escape, as for the
        // class raster).
        return _travelsShown() && !root._travelsStatusReady && !root._travelsStatusFailed;
    }

    function _exactFullStanding() {
        // The full state's single-owner predicate: the exact pair —
        // the class raster's texture AND, when the travels are shown,
        // the travels raster's — is here. The canvas yields on THIS
        // (opacity and clear) and the travels Image presents on it,
        // so the two halves swap in one beat and no frame shows one
        // half of a bake over the other's held picture.
        return _fullRaster() && root._rasterStatusReady && !_travelsPending();
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

    function _navBacking() {
        // The warm raster's backing as the model baked it: the
        // carried tail paints at the same factor so the two present
        // pixel-equal through the same display transform.
        return root.progress != null && root.progress.navigationBacking !== undefined && root.progress.navigationBacking > 0 ? root.progress.navigationBacking : 4.0;
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
        // The release's catch-up: the freeze deferred the progress
        // repaints through the gesture — paint the accumulated
        // advance NOW so the picture jumps to the current print
        // position the moment the pan ends (the live request).
        if (root._interactionActive) {
            progressCanvas.requestPaint();
        }
        // The release resumes the model's publications too — the
        // settle has usually done it already; this covers a release
        // that lands before the camera settles.
        root._releasePublishFreeze();
    }

    function _enterInteraction() {
        // The first camera input: switch the heavy-scene
        // presentation to the warm interaction raster immediately —
        // no synchronous work rides this call. With no ready
        // navigation raster the exact scene simply stays (the
        // safe degraded path, same as before this feature). The
        // entry LATCHES the source the gesture entered with: the
        // model retires the published URL the moment the demand
        // moves (a scrub, a toggle, a failed replacement), and a
        // mid-gesture retirement must never unload the scene the
        // gesture is already presenting — the camera transforms
        // ride the QML side, so the latched raster stays coherent
        // for the gesture's whole life.
        if (!root._interactionActive && navigationData() !== "") {
            // The IMMEDIATE entry: the pan starts on the first real
            // movement with whatever raster is eligible. The carried
            // tail completes the raster's picture — the lines since
            // its split re-paint at its backing, so the entry drops
            // nothing and no gated wait delays the drag.
            root._gestureNavSource = navigationData();
            // Tell the model which file this gesture is presenting:
            // a bake committing mid-gesture supersedes the surface's
            // url and unlinks the old one, which is precisely this
            // picture.
            if (root.printerModel != null) {
                root.printerModel.setFollowerGestureRaster(root._gestureNavSource);
            }
            root._interactionActive = true;
            // The barrier's own re-check for the whole gesture: a
            // wheel-only interaction has no release to drive it, and
            // the demand's paint defers while the hold stands.
            panExitCheck.restart();
            root._carryZoom = root.viewScale;
            // The carried tail's first paint: the gesture's frozen
            // picture — the lines since the warm raster's split at
            // its backing. The exact scene keeps rebuilding behind
            // the raster (its own canvas, invisible while held).
            carryCanvas.requestPaint();
            // The freeze: the model holds the picture's publications
            // while the gesture lives — the RELEASE resumes them (the
            // live request). The toolhead dot stays exempt.
            if (root.printerModel != null) {
                root.printerModel.setFollowerInteracting(true);
            }
        }
    }

    function _releasePublishFreeze() {
        // The model's per-poll publications resume while the gesture
        // still OWNS the picture. Safe because the ENTRY LATCH is what
        // keeps the presented raster coherent — a fresher raster
        // landing mid-hold cannot swap it — so the model may breathe
        // without the picture moving. Idempotent, and every exit path
        // routes through it.
        if (root.printerModel != null) {
            root.printerModel.setFollowerInteracting(false);
        }
    }

    function _imageHolds(image) {
        // Whether an image the barrier waits on is still a reason to
        // hold. A load that has FAILED is not: the barrier could
        // never pass, and the hold freezes the very model
        // publications the scene needs to reassemble — the wedge
        // that would not hand back after a camera change. This is
        // the travels' failed-mirror escape, granted to every
        // component of the exact scene.
        return image.status !== Image.Ready && image.status !== Image.Error;
    }

    function _prefixUsable() {
        // Whether the prefix is still a component the barrier may
        // wait on: the model rendered it AND its image has not
        // failed. A failed load leaves the whole interval to the
        // vector path — the rule _prefixFrom already states — so the
        // barrier must stop waiting on it. With the model side alone
        // the condition was unsatisfiable: the prefix never reaches
        // Ready, so the hold never released.
        return _prefixModelReady() && !root._prefixStatusFailed;
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
            if (_imageHolds(progressRasterImage)) {
                return false;
            }
            if (root.showTravels && _travelsOf(current) && _imageHolds(progressTravelImage)) {
                return false;
            }
        } else if (split != null && split > 0 && split < _motionsOf(current)) {
            // The partial scene: the printed history owned coherently
            // — the Ready prefix over a compatible canvas, or the
            // delivered vector owning the whole interval.
            if (_prefixUsable() && !_partialPrefixReady()) {
                return false;
            }
            if (!_prefixUsable() && !(root._textureReady && root._vectorCoversFrom === 0 && root._lastSplit === split)) {
                return false;
            }
            if (_partialBase() && _baseOf(current) && _imageHolds(pendingBaseImage)) {
                return false;
            }
        }
        if (root.showPrevious && _ghost("prev") != null && _rasterOf(_ghost("prev")) && _imageHolds(prevGhostImage)) {
            return false;
        }
        if (root.showNext && _ghost("next") != null && _rasterOf(_ghost("next")) && _imageHolds(nextGhostImage)) {
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

    function _retainedPrefixApplies() {
        // The retained pixels stand only while they are a complete
        // committed composition for the current context: the same
        // partial state, the demand split at or past the retained
        // boundary, never at 0 or the full layer. The split arithmetic
        // alone is not identity — a seek between layers whose motions
        // and demand coincide passes it — so the record's own anchor
        // must still be the served one.
        var progress = root.progress;
        var layer = progress != null && progress.layers != null ? progress.layers.current : null;
        return progress != null && progress.split != null && progress.split > 0 && layer != null && root._retainedPrefixSplit >= 0 && progress.split >= root._retainedPrefixSplit && progress.split < layer.motions && progress.anchor === root._retainedPrefixAnchor;
    }

    function _retireRetained() {
        // The frozen pixels' identity is gone with the layer they
        // were frozen for: a payload that names another layer (or no
        // layer at all), the view transform they baked. Anything that
        // leaves the record armed lets a later split-only match draw
        // another layer's history.
        root._retainedPrefixSource = "";
        root._retainedPrefixSplit = -1;
        root._retainedPrefixAnchor = -2;
    }

    function _prefixHoldsFull() {
        // The full state's entry, the mirror of the leaving-full
        // hold: the raster owns the picture but its texture is still
        // decoding, and no other producer holds the printed head at a
        // full layer — the prefix' pixels stand until the raster's
        // mirror says they may go. Both mirrors are handler-written:
        // reading an image's live status inside its own source binding
        // is the loop the engine warns about. A new layer's world never holds here (its
        // reset cleared the shown record).
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        return _fullRaster() && !root._rasterStatusReady && !root._rasterStatusFailed && root._prefixWasShown && root._prefixStatusReady && _viewKey() === root._prefixShownViewKey && layer != null && layer.prefixValid === true;
    }

    function _prefixApplies() {
        // The split arithmetic still names the prefix as the history
        // owner — PURE structural terms, no transient inputs (the
        // model's validity and the Image's status flicker through a
        // re-publish; a hide fired on a flicker drops the standing
        // composition's memory). The boundary is INCLUSIVE: a fresh
        // prefix renders AT the requested split, and hiding it until
        // the split passed it left the printed history ownerless for
        // every scrub that landed exactly on the boundary (the
        // disappearance). A full layer, a split at 0 (nothing has
        // printed — the prefix owns nothing, ever) or a split below
        // the boundary hides it legitimately.
        var progress = root.progress;
        var layer = progress != null && progress.layers != null ? progress.layers.current : null;
        return progress != null && progress.split != null && progress.split > 0 && layer != null && layer.prefixSplit !== undefined && layer.prefixSplit >= 0 && progress.split >= layer.prefixSplit && progress.split < _motionsOf(layer);
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
            return root._textureReady && root._vectorCoversShown === 0 && root._lastSplit === root.progress.split && !root._prefixShowHold;
        }
        if (root._vectorCoversFrom === -1 && _vectorInkless()) {
            return true;
        }
        return _partialPrefixReady() && !root._prefixShowHold;
    }

    function _splitGate() {
        // The delivery's split gate: the delivered canvas must be
        // the CURRENT demand's picture. A detached scrub demands the
        // EXACT split (the review's rapid-scrub policy — an
        // intermediate split must not present once the demand moved
        // on). The attached live follow advances monotonically: a
        // canvas delivered for the previous poll IS the standing
        // picture — its tail extends with the next paint — and the
        // boundary match below is what keeps it a real composition.
        // The retained frame read the exact split gate, so every
        // poll's advance hid the LIVE prefix and stood the previous
        // checkpoint's stale picture (the lag-behind-the-toolhead
        // report). A backward move falls back to the exact gate.
        var split = root.progress != null ? root.progress.split : null;
        if (root.attached && split != null && root._lastSplit >= 0) {
            return split >= root._lastSplit;
        }
        return root._lastSplit === split;
    }

    function _compositionReady() {
        // The canvas-side half of the joint readiness, WITHOUT the
        // live image's status: the retained frame's visibility reads
        // this, never _partialPrefixReady — reading the live status
        // there closes a binding cycle through the live image's own
        // handlers (the engine's live loop warning disabled the
        // visible binding and froze the face on the attach publish).
        var layer = root.progress != null && root.progress.layers != null ? root.progress.layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        return layer != null && split != null && root._textureReady && root._splitGate() && (root._vectorCoversShown === 0 || root._vectorCoversShown === layer.prefixSplit);
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
        if (!_prefixModelReady() || !root._prefixStatusReady) {
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
        // The FIRST show never stands over the canvas's full bitmap:
        // a covers-0 delivery would double-render the whole history
        // until the trim repaint lands. It waits for the delivered
        // tail matching the prefix's own boundary — the trim paints
        // first, the prefix swaps in over it. A re-show (the scrub
        // through 100% and back) keeps the covers-0 acceptance: the
        // standing picture is the complete one, and the trim follows
        // in place — but only for THIS demand's own delivery. A full
        // bitmap painted at an older split is missing every motion
        // the demand has passed since, and the attached follow's
        // monotonic gate would admit it.
        var delivered = root._textureReady && root._splitGate() && ((root._vectorCoversShown === 0 && root._prefixWasShown && root._lastSplit === root.progress.split) || root._vectorCoversShown === layer.prefixSplit);
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

    function _paintPending(ctx) {
        // The base marks the unprinted suffix of a PARTIAL layer: a 0%
        // layer draws nothing (nothing has printed — no boundary to
        // frame), and a full layer's raster covers it entirely. The
        // native sibling's arrival hides this fallback (its Image above
        // takes over).
        if (!root.available() || mapping._plot == null) {
            return;
        }
        var layer = root.progress.layers.current;
        if (!_partialBase() || layer == null || _baseOf(layer)) {
            return;
        }
        var current = _scrubVector();
        if (current == null) {
            return;
        }
        _drawLayer(ctx, current, 0.55, -1, true, -1);
    }

    function _pendingDraws() {
        // Exactly the conditions _paintPending draws under, named once
        // so the REQUEST can skip a paint that would leave the canvas
        // empty. In the steady state the native grey sibling owns the
        // base, so the fallback draws nothing — yet the split advances
        // every poll, and each request cleared and re-uploaded the
        // whole canvas texture for no pixels.
        if (!root.available() || mapping._plot == null || !_partialBase()) {
            return false;
        }
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        return layer != null && !_baseOf(layer) && _scrubVector() != null;
    }

    // Whether the canvas holds — or is owed — a picture. A skip is
    // only safe while nothing stands: a state that stops drawing must
    // still repaint ONCE to clear the pixels the fallback left.
    property bool _pendingStanding: false
    // The fallback's own arrival word, the pending canvas' `_paints`:
    // written by a paint that ran, never by a skipped request.
    property int _pendingPaints: 0

    function _requestPendingPaint() {
        var draws = _pendingDraws();
        if (draws || root._pendingStanding) {
            root._pendingStanding = draws;
            pendingCanvas.requestPaint();
            return;
        }
        // Nothing to draw and nothing standing: the picture-word is
        // recorded without painting. The canvas holds no pixels, so its
        // picture IS this key's — and the arrival word the composition
        // gates on (_lastPendingKey) stays honest for a canvas that was
        // never painted at all.
        root._lastPendingKey = root._pendingKey;
    }

    function _partialBase() {
        // The base marks the unprinted suffix of a PARTIAL layer
        // — 0% included: the whole layer reads as the grey ghost
        // from the first instant (the live request), and a full
        // layer's raster covers it entirely.
        if (!root.showBase || !root.available()) {
            return false;
        }
        var layers = root.progress != null ? root.progress.layers : null;
        var layer = layers != null ? layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        return split != null && layer != null && split >= 0 && split < layer.motions;
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
        // The jump centres the dot on the canvas, unclamped: the
        // plate may show empty space beyond the bed edge (the live
        // request — the jump must always put the toolhead at the
        // middle, never pin the bed to the view's edge).
        return {
            "x": width / 2 - scene.x * root.viewScale,
            "y": height / 2 - scene.y * root.viewScale
        };
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

    // The centred follow is retired (the live ruling: the per-poll
    // re-pan was too slow). The one-shot Jump to toolhead stays.

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
        // The SAME geometry-width policy the native renderer's pen
        // applies (the parity contract): the nominal width scaled to
        // the presentation, then the same device-coverage floor —
        // min(2/dpr, 1) logical px — so a sub-floor stroke presents
        // at the same full-intensity footprint on both renderers.
        // Without the floor the native raster's backed downscale
        // faded thin strokes while the canvas stroked them raw, and
        // the two halves of ONE layer read as different inks.
        var width = root.nominalToolpathWidthMm * Math.abs(plot.sx) * Math.abs(root._view.scale) * root._view.lineScale * (root._view.compact ? root.compactStrokeBoost : 1.0);
        var dpr = root._view.dpr !== undefined ? Math.max(1.0, root._view.dpr) : 1.0;
        return Math.max(width, Math.min(2.0 / dpr, 1.0));
    }

    function travelWidthPx() {
        return root.toolpathWidthPx() * root.travelVisualRatio;
    }

    function _barZoomPan(target) {
        // The zoom bar's anchor (the live ruling): the scene point
        // under the viewport CENTRE stays put through the scale
        // change — the bar must never zoom around the bed origin.
        // The retarget reads the CURRENT display transform, exactly
        // like the wheel's cursor anchor.
        var cx = root.width / 2;
        var cy = root.height / 2;
        var panX = cx - (cx - root.displayPanX) / root.displayScale * target;
        var panY = cy - (cy - root.displayPanY) / root.displayScale * target;
        var clamped = root._softClampPan(panX, panY);
        return {
            "x": clamped.x,
            "y": clamped.y
        };
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
    // The view the accumulated bitmap was rastered at. The delta path
    // strokes only the motions past the last split, so ink that was
    // rastered at another zoom, pan or line width stays where the old
    // transform put it and the new transform's delta lands beside it
    // (measured: the same wall read as a mixed 57.8 px offset). Every
    // other reset trigger — a backward split, an anchor change, a
    // payload swap — is blind to the view, and the key's own repaint
    // request never invalidated, so the re-bake waits for a paint that
    // notices this.
    property string _accumViewKey: ""
    // The coverage of the last DELIVERED paint — what the scene has
    // actually pulled. The committed record above runs a frame ahead
    // of the display (the threaded canvas commits its bitmap before
    // the sync that shows it), and every ownership decision that
    // puts the prefix's pixels in the scene must read THIS one: a
    // prefix admitted on the committed record stacks its ink over
    // the not-yet-trimmed bitmap for one frame (the additive-AA
    // doubling at the body columns).
    property int _vectorCoversShown: -2
    property bool _prefixHold: false
    property bool _prefixWasShown: false
    // The view the standing prefix' pixels were shown at: they are a
    // bake of ONE view, so a camera move must not hold them (the
    // out-of-scale ghost is exactly this record read across views).
    property string _prefixShownViewKey: ""
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
    // The coverage of every paint the renderer has not delivered yet.
    // A delivery hands the scene the bitmap of ONE paint, and under
    // load the painter runs ahead of the renderer: naming the
    // painter's CURRENT coverage at a delivery claimed a trimmed
    // bitmap while the scene still held an older, full-interval one,
    // and the live prefix stacked its raster over it — the seam's
    // doubled ink, and a layer change's prefix admitted over the
    // previous layer's bitmap. The queue is consumed WHOLE at the
    // delivery, because a sync coalesces a burst into one texture.
    property var _paintCovers: []
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
    // The key the pending canvas has PAINTED, never the one it was asked
    // for: _pendingKey is written where the repaint is REQUESTED, so a
    // starved render thread leaves the previous picture on screen while
    // the key already reads the new one. The base's own state rides that
    // key, and a frame that still holds the old base is a picture of the
    // previous state — which a diff census then counts.
    property string _lastPendingKey: ""
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
        // and an unrequested paint would never clear the hold. The
        // hold is structurally PARTIAL-only: at a full layer (or at
        // 0%) no prefix owns the history, and a hold armed there
        // could never release — a cached bake then stood forever
        // over the valid full raster (the stale-zoom report).
        if (!_prefixModelReady() && root._prefixWasShown && _leavingFull()) {
            holdExpiryTimer.stop();
            root._prefixHold = true;
            if (!root._interactionActive) {
                progressCanvas.requestPaint();
            }
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
            _requestPendingPaint();
        }
        var progressKey = _progressKeyOf();
        if (progressKey !== root._progressKey) {
            root._progressKey = progressKey;
            root._lastSplit = -1;
            root._paintsSinceReset = 0;
            root._progressDirty = true;
            // The queue belongs to the world it was painted in. A
            // layer change or a view change drops it: entries left
            // from the previous world's paints would have the next
            // deliveries publish a coverage the new bitmap does not
            // hold, and the new layer's prefix would be admitted over
            // the old layer's picture (the reported jump straight to
            // the new prefix's boundary).
            root._paintCovers = [];
            progressCanvas.requestPaint();
            _holdPrefixThroughRepaint();
        }
        // A mid-gesture ZOOM re-bakes the carried tail at the new
        // scale (the pan never repaints — the translation carries
        // it); idle settles leave the hidden canvas alone.
        if (root._interactionActive && root.viewScale !== root._carryZoom) {
            root._carryZoom = root.viewScale;
            carryCanvas.requestPaint();
        }
    }

    onProgressChanged: {
        if (root.progress == null || root.progress.layers == null) {
            root._lastSplit = -1;
            root._anchor = -1;
            root._prefixHold = false;
            root._prefixWasShown = false;
            _retireRetained();
            return;
        }
        // A payload that names no layer is a print switch's own frame
        // (an unload, a fresh job): the frozen pixels belong to a
        // layer this payload no longer names, and the next layer may
        // wear the same anchor and the same split arithmetic.
        if (root.progress.layers.current == null) {
            _retireRetained();
        }
        if (root.progress.anchor !== root._anchor) {
            root._anchor = root.progress.anchor;
            // A new layer's world: the standing composition — the
            // retained record, the shown record and the canvas hold
            // that reads them — belongs to the old one and must not
            // stand over the new layer while ITS assets load (the
            // A-to-B seek; a print switch rides the same branch).
            _retireRetained();
            root._prefixWasShown = false;
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
            _requestPendingPaint();
        }
        // The progress repaint follows its OWN key: an unchanged split/anchor/payload — a
        // raster or ghost arrival, a quiet poll — never wakes the
        // painter, whose partial path walks dense geometry. While a
        // gesture is live the repaint DEFERS (the key still records
        // the demand): the pan presents one fixed picture — new
        // lines mid-pan read as jank — and the exit's next poll
        // paints the accumulated advance in one catch-up.
        var progressKey = _progressKeyOf();
        if (progressKey !== root._progressKey) {
            root._progressKey = progressKey;
            if (!root._interactionActive) {
                progressCanvas.requestPaint();
            }
        }
        // The prefix's model-side validity may flip WITHOUT a key
        // change (the context invalidation's publish: the view key
        // was already consumed by the settle's reset): hold the old
        // picture until the canvas's repaint owns the interval.
        _holdPrefixThroughRepaint();
        // The prefix's shown record derives from the publish cycle,
        // never the image's own visible edge — a handler write
        // feeding its own visible binding looped the engine's
        // detector (the live warning). The set waits for the
        // composition's own predicates, the clear waits for a
        // terminal state (the retained no longer applies AND the
        // model publishes no prefix for this anchor).
        if (root._prefixWasShown) {
            // The full state's clear is terminal only once the
            // replacement's texture is HERE: until then the prefix is
            // still the printed head's owner (the gate), and clearing
            // the record here would drop the head before the raster
            // could take it.
            if (!root._prefixApplies() && !root._prefixHold && !_prefixModelReady() && !_prefixHoldsFull()) {
                root._prefixWasShown = false;
            }
        } else if (_partialPrefixReady() || (root._prefixHold && _leavingFull())) {
            root._prefixWasShown = true;
            root._prefixShownViewKey = _viewKey();
            // The set edge must carry the record with it. The shown
            // flag is what keeps the canvas's old tail standing
            // through a handover, so the interior below the boundary
            // is owned by the RETAINED pixels — and the Image's own
            // handlers are the only other place the record is frozen,
            // so a set edge observed here (both of them already past,
            // the readiness only now true) would leave the flag
            // standing over an empty record: the replacement's source
            // change blanks the live Image and the interior has no
            // owner for the frames until the delivery. The condition
            // is the freeze's own joint readiness, never a wider one.
            if (_partialPrefixReady() && root.progress.layers.current.prefixData !== "") {
                root._retainedPrefixSource = root.progress.layers.current.prefixData;
                root._retainedPrefixSplit = root.progress.layers.current.prefixSplit;
                root._retainedPrefixAnchor = root.progress.anchor;
            }
        }
        // The interaction's exit rides the exact scene's OWN commits
        // (a pan-only gesture never runs the zoom animator): the
        // settle's invalidation publish flips the assets stale, the
        // re-render's publish flips them fresh — the barrier's
        // verdict swaps the scene back exactly when the complete
        // exact scene is presentation-ready.
        // No mid-gesture raster swap: the gesture presents the
        // ENTRY latch for its whole life — a fresher compatible
        // raster landing mid-hold would jump the frozen picture
        // forward (the snap-back's second half). The carried tail
        // already completes the latched picture, and the RELEASE
        // resumes the raster updates (the catch-up).
        // The exit waits for the view to settle AND the release:
        // a held-but-still pan stays on the warm raster (the
        // mid-hold settle must not hand back — the snap between the
        // two pictures), while the RELEASE returns the picture to
        // normal through the panExitCheck path. The settle timer
        // runs through every gesture (each view change restarts it),
        // so the swap happens once, after the last camera input and
        // the mouse-up.
        if (root._interactionActive && !zoomAnimator.running && !root.settleTimer.running && _exactReady() && !viewGesture.pressed) {
            endInteraction();
        } else if (root._interactionActive && !zoomAnimator.running && !root.settleTimer.running && !viewGesture.pressed && root._progressDirty) {
            // The wheel's own catch-up: a demand that lands mid-hold
            // has its repaint deferred, and a wheel never releases —
            // without this paint the barrier could never pass and the
            // interaction would stick on the warm raster. The camera
            // has settled, so nothing is left to jank; the delivery's
            // re-check rides the release path's own panExitCheck.
            progressCanvas.requestPaint();
            panExitCheck.restart();
        }
    }
    onShowBaseChanged: {
        var key = _pendingKeyOf();
        if (key !== root._pendingKey) {
            root._pendingKey = key;
            _requestPendingPaint();
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
            // The queue belongs to the world it was painted in. A
            // layer change or a view change drops it: entries left
            // from the previous world's paints would have the next
            // deliveries publish a coverage the new bitmap does not
            // hold, and the new layer's prefix would be admitted over
            // the old layer's picture (the reported jump straight to
            // the new prefix's boundary).
            root._paintCovers = [];
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
        _retireRetainedView();
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
        _retireRetainedView();
        root.settleTimer.restart();
    }
    onViewPanYChanged: {
        _publishView();
        _retireRetainedView();
        root.settleTimer.restart();
    }
    // Detaching hides the dot (the one-shot jump reads it on demand).
    onAttachedChanged: {}
    onCompactChanged: {
        // The product sets compact at construction and never flips
        // it; the repaint keeps the thumbnail honest wherever it is.
        _publishView();
        _resetStack();
        _retireRetainedView();
    }
    Component.onCompleted: _publishView()

    // The mapping replots on ITS resize; every raster holds the old
    // transform's coordinates and repaints from scratch (the live
    // report: a strip at the card's top — the grown plot must
    // repaint).
    Connections {
        target: mapping
        function onWidthChanged() {
            _retireRetainedView();
            root.settleTimer.restart();
        }
        function onHeightChanged() {
            _retireRetainedView();
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
        // scene switches as one unit — the warm raster is ONE flat
        // composite, never two independently-moving layers; the
        // live report: a separately-painted grid panned at its own
        // pace).
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
        // Every plate raster is file-backed (a PNG the model published
        // or a data PNG's URL), so a source install is a DECODE. An
        // inline decode holds the Qt thread for the whole raster --
        // ~66 ms for the 4x navigation image -- which the live report
        // reads as the camera stalling whenever a raster lands. The
        // bake is unchanged and the worker's cost is unchanged: the
        // decode moves off the Qt thread and the presentation is the
        // same pixels, one thread later.
        asynchronous: true
        x: root.displayPanX
        y: root.displayPanY
        width: root.width * root.displayScale
        height: root.height * root.displayScale
        // The interaction source: the gesture LATCHES the raster it
        // entered with (the model retires the published URL the
        // moment the demand moves — a mid-gesture retirement must
        // never unload the scene the gesture presents); idle binds
        // the live eligible URL so the texture preloads. An
        // interaction activated without an entry (the presentation
        // fixtures drive the flag directly) falls back to the live
        // eligible URL.
        visible: root._interactionActive && (root._gestureNavSource !== "" || navigationData() !== "")
        source: root._interactionActive ? (root._gestureNavSource !== "" ? root._gestureNavSource : navigationData()) : navigationData()
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
        // Declaration order IS the z-order, so the ghosts are declared
        // FIRST: the navigation raster's own composite (grid, ghosts,
        // grey base, current layer, travels) puts them beneath
        // everything the current layer draws — a ghost over the full
        // raster dims the geometry it overlaps at 100%, where the two
        // pictures are pixel-for-pixel the same scene.
        // The ghost layers: the worker's rasters at ghost opacity —
        // role-free assets, the opacity applied at composition.
        // Until a ghost's raster lands it draws nothing —
        // the context layer appears a beat after the seek, never blocks
        // it.
        Image {
            id: prevGhostImage
            anchors.fill: parent
            asynchronous: true
            smooth: false
            opacity: 0.30
            visible: root.available() && root.showPrevious && _ghost("prev") != null && _rasterOf(_ghost("prev"))
            source: visible ? _ghost("prev").rasterData : ""
        }
        Image {
            id: nextGhostImage
            anchors.fill: parent
            asynchronous: true
            smooth: false
            opacity: 0.30
            visible: root.available() && root.showNext && _ghost("next") != null && _rasterOf(_ghost("next"))
            source: visible ? _ghost("next").rasterData : ""
        }

        // The raster-only full state: the whole
        // layer and its travels blit from the native data URLs; the
        // progress canvas below clears itself while these show.
        Image {
            id: progressRasterImage
            anchors.fill: parent
            asynchronous: true
            // No smoothing (the stack's rule): the canvas rasterizes
            // crisp at the painted scale, and a bilinear-filtered
            // raster against it reads as a soft ghost — every raster
            // presents nearest-neighbour (only the zoom-navigation
            // image smooths, and only because smooth zooming IS its
            // presentation).
            smooth: false
            visible: _fullPictureStanding()
            // The entry's hold keeps the PICTURE: while the full state
            // stands the source is the fresh raster; through the
            // handover it is the captured URL (the partial payload's
            // rasterData is empty — re-binding it would clear the
            // standing pixels, a blank the visibility flag cannot
            // hide).
            source: _fullPictureStanding() ? (_fullRaster() ? root.progress.layers.current.rasterData : root._heldFullSource) : ""
            // The texture's arrival is what withdraws the canvas below
            // (its yield reads the mirror) and what ends the two
            // holds' ink.
            onStatusChanged: {
                root._rasterStatusReady = status === Image.Ready;
                root._rasterStatusFailed = status === Image.Error;
                progressCanvas.requestPaint();
            }
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
            asynchronous: true
            smooth: false
            // Presentation rides the exact-pair predicate, never a
            // bare visibility: a travels texture that lands before the
            // class raster's would otherwise stand at the new view
            // while the canvas still holds the old picture (a pixels
            // -wide shift that grows with the move). Zero, never
            // hidden — the source binding must stay alive so the
            // decode starts here and the canvas can hand over in the
            // beat the texture arrives.
            opacity: _exactFullStanding() ? 0.8 : 0.0
            visible: _travelsShown()
            source: visible ? root.progress.layers.current.travelData : ""
            onStatusChanged: {
                root._travelsStatusReady = status === Image.Ready;
                root._travelsStatusFailed = status === Image.Error;
            }
        }

        // The grey whole-layer base: the
        // native grey sibling as a scene-graph Image, with the vector
        // canvas below as the pre-arrival fallback.
        Image {
            id: pendingBaseImage
            anchors.fill: parent
            asynchronous: true
            smooth: false
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
                _paintPending(ctx);
                // Landed last, and on every path: the picture the next
                // frame composites belongs to the key read here, cleared
                // base included. The count is the arrival's own word —
                // a request that skipped the paint never reaches here.
                root._pendingPaints += 1;
                root._lastPendingKey = root._pendingKey;
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
            objectName: "moonrakerPlatePrefixImage"
            anchors.fill: parent
            asynchronous: true
            // Nearest, like the retained frame it swaps with: any
            // filter difference across the handover is a whole-raster
            // shimmer, and the canvas's crisp rasterization is the
            // reference both must match.
            smooth: false
            // The shown prefix STAYS while the split arithmetic still
            // names it as the history owner — a repaint in flight, a
            // delivery from an earlier split, or a transient
            // validity/status flicker through a re-publish must
            // never hide it: the standing picture is the complete
            // OLD composition, and a prefix-less frame would drop
            // the printed history. The normal readiness gate governs
            // only once the delivered canvas IS the current demand's
            // picture.
            // The standing clauses below yield to the RETAINED frame:
            // both images standing at once would composite the same
            // semi-transparent pixels twice (the additive-AA flicker
            // — the edges thickened then thinned on every refresh).
            // The yield reads the retained ITEM's visibility, never
            // its source: the source is written by this image's own
            // status/paint handlers, and reading it here looped the
            // binding (the live QML warning).
            visible: (_partialPrefixReady() || ((root._prefixHold && _leavingFull()) || (root._prefixWasShown && _prefixApplies() && !(root._textureReady && root._splitGate()))) || _prefixHoldsFull()) && !root._retainedStanding
            source: (_prefixModelReady() || (root._prefixHold && _leavingFull()) || (root._prefixWasShown && _prefixApplies()) || _prefixHoldsFull()) && root.progress != null && root.progress.layers != null && root.progress.layers.current != null ? root.progress.layers.current.prefixData : ""
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
                    // The settled single-owner trim: a canvas that
                    // painted the FULL history before this prefix
                    // showed must repaint to the tail alone — its
                    // full bitmap stacked under the prefix doubles
                    // the prefix region's ink (the parity seam: 42
                    // vs 21 at lineScale 0.7). The repaint is FORCED
                    // here: the show can land after the key's paint
                    // already consumed itself.
                    progressCanvas.requestPaint();
                } else if (root._prefixWasShown && root.progress != null && root.progress.layers != null && root.progress.layers.current != null && root.progress.layers.current.prefixValid === false && _leavingFull()) {
                    // The invalidation hold keeps the standing picture
                    // while the canvas repaints the full interval. The
                    // hold is structurally partial-only: at 0% nothing
                    // has printed (the 0% picture IS the empty history)
                    // and at a full layer the hold could never release
                    // (the stale-zoom report's standing bake).
                    holdExpiryTimer.stop();
                    root._prefixHold = true;
                    progressCanvas.requestPaint();
                } else if (!root._prefixApplies()) {
                    // The hide is terminal (a full layer, a sub-prefix
                    // split, a layer change): the hold ends. The shown
                    // record clears in the publish cycle, never here.
                    root._prefixHold = false;
                }
            }
            onStatusChanged: {
                root._prefixStatusReady = status === Image.Ready;
                root._prefixStatusFailed = status === Image.Error;
                progressCanvas.requestPaint();
                if (status === Image.Ready && source !== "" && root.progress != null && root.progress.layers != null && root.progress.layers.current != null && root._partialPrefixReady()) {
                    // The handover freeze: the pixels that JUST
                    // uploaded become the retained previous — but
                    // only once the composition is JOINTLY ready.
                    // A freeze during a transition (the canvas still
                    // covers the old boundary) would overwrite the
                    // standing picture with the hybrid half, so the
                    // freeze waits; the delivery re-freezes below.
                    root._retainedPrefixSource = source;
                    root._retainedPrefixSplit = root.progress.layers.current.prefixSplit;
                    root._retainedPrefixAnchor = root.progress.anchor;
                }
            }
        }

        // The RETAINED previous prefix (the composition transaction):
        // the pixels of the last JOINTLY-DISPLAYED prefix stand until
        // the replacement composition is jointly ready — the prefix
        // Image uploaded AND the canvas's delivered coverage matching
        // its boundary. The old complete composition (retained prefix
        // + the standing canvas bitmap, the threaded canvas's own
        // double buffer) is never torn down before the new one swaps
        // in on the same evaluation.
        Image {
            id: retainedPrefixImage
            objectName: "moonrakerPlateRetainedPrefixImage"
            anchors.fill: parent
            asynchronous: true
            // The interior below the boundary is owned by the live
            // prefix's PIXELS, not by its composition bookkeeping: the
            // moment its Image has none (a source swap mid-load, a
            // refresh at the same boundary) the record must stand in
            // the SAME evaluation, so the readiness is read from the
            // live image's own status — a one-way mirror, never a
            // handler-fed flag that lands a beat late. A delivered
            // FULL bitmap still owns everything itself: the record
            // must not stack its pixels over it (the additive-AA
            // doubling). That stand-down is also the interior's one
            // bare frame on a host whose scene texture trails its own
            // painted coverage — accepted, and pinned by the gap
            // census.
            visible: root._retainedPrefixSource !== "" && root._retainedPrefixApplies() && (!root._compositionReady() || progressPrefixImage.status !== Image.Ready) && !(root._textureReady && root._vectorCoversShown === 0)
            source: root._retainedPrefixSource
            smooth: false
            onVisibleChanged: root._retainedStanding = visible
        }

        Canvas {
            id: progressCanvas
            anchors.fill: parent
            renderTarget: Canvas.Image
            renderStrategy: Canvas.Threaded
            // The full state's handover in ONE beat: the standing
            // bitmap is the picture until the replacement's texture is
            // HERE, and the swap is then a compositing change, so the
            // frame that first shows the raster is the frame that
            // drops the canvas. Withdrawing on the predicate alone
            // blanked the whole printed history for the decode;
            // withdrawing on the paint beat left the old ink over the
            // new texture (the liveness control's doubling). Opacity,
            // never visibility: hiding a Canvas discards its buffer.
            // Withdrawing waits for the whole exact pair: the travels'
            // texture is the second half of the same bake, and the
            // canvas is the only producer holding the travels' ink
            // while it decodes.
            opacity: _exactFullStanding() ? 0 : 1
            onPainted: {
                // The paint's bitmap is delivered: its coverage record
                // now describes the scene's committed texture. A held
                // prefix over a delivered FULL bitmap can finally
                // relinquesh — one frame later, after the scene pulls
                // the texture (the expiry timer's beat).
                // The record names the paint the renderer actually
                // pulled: the oldest entry still undelivered. The
                // queue is also drained by the world reset — a layer
                // or view change repaints the canvas, and entries left
                // from the previous world's paints would publish a
                // coverage the new bitmap does not have, admitting the
                // new layer's prefix over the old layer's picture.
                root._vectorCoversShown = root._paintCovers.length > 0 ? root._paintCovers.shift() : root._vectorCoversFrom;
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
                if (root._prefixHold && root._vectorCoversShown === 0) {
                    root._beatPending = true;
                    holdExpiryTimer.restart();
                }
                // The transaction's freeze at the DELIVERY: a Ready
                // that landed mid-transition (the canvas still held
                // the old boundary) skipped its freeze above — the
                // joint readiness that arrived with THIS bitmap is
                // the moment the standing composition became the
                // live prefix's, so the retained moves on.
                if (root._partialPrefixReady() && root.progress != null && root.progress.layers != null && root.progress.layers.current != null && root.progress.layers.current.prefixData !== "") {
                    root._retainedPrefixSource = root.progress.layers.current.prefixData;
                    root._retainedPrefixSplit = root.progress.layers.current.prefixSplit;
                    root._retainedPrefixAnchor = root.progress.anchor;
                }
                // The FIRST show's trim: a delivery whose paint beat
                // the prefix's upload leaves the FULL bitmap under a
                // now-Ready prefix — the readiness gate refuses that
                // overlap, and the prefix's own show cannot request
                // the trim (its visible binding reads the predicate
                // the trim feeds). Request it here, one paint after
                // the delivery that completed the picture.
                if (root._vectorCoversFrom === 0 && _prefixFrom() > 0 && !root._interactionActive) {
                    progressCanvas.requestPaint();
                }
                // The shown record's set edge, the publish cycle's own
                // other half: the delivery that readied the prefix's
                // composition IS the show, and a settled seek publishes
                // no further state to notice it. The clear stays in the
                // publish cycle; the predicate guarantees the canvas no
                // longer owns the interval below the boundary.
                if (_partialPrefixReady()) {
                    root._prefixWasShown = true;
                    root._prefixShownViewKey = _viewKey();
                }
            }
            onPaint: {
                var ctx = getContext("2d");
                // This paint's delivery is queued before the paint knows
                // whether it rebuilds the bitmap: a paint that returns
                // early leaves the buffer as the last one's, so its
                // delivery must name the same coverage. The assignment
                // below rewrites the entry when this paint does rebuild
                // it. The cap drops the oldest if no delivery ever
                // comes, so the queue cannot grow without bound.
                if (root._paintCovers.length >= 8) {
                    root._paintCovers.shift();
                }
                root._paintCovers.push(root._vectorCoversFrom);
                // The full state's HOLD: the model says the raster owns
                // the picture, but its texture is still decoding, so
                // the accumulated ink stands rather than blanking the
                // printed history for the decode's length. A new
                // layer's world never reaches here (its reset dropped
                // the accumulation), nor does another view's: the
                // standing ink was baked for the view it was painted
                // at, and a pan holds nothing. The mirror ends the
                // hold in the same beat the texture lands.
                if (_fullRaster() && !root._rasterStatusReady && !root._rasterStatusFailed && root._lastSplit >= 0 && root._vectorCoversFrom !== -1 && _viewKey() === root._accumViewKey) {
                    return;
                }
                // The settled single-owner trim is a REFINEMENT of an
                // already presentation-complete picture (the full
                // bitmap under the prefix, or the settled tail beside
                // it — both invisible overlap): its repaint must not
                // withdraw the standing readiness — the seek's ready
                // commit waits for no trim, and a withdrawn flag hides
                // the prefix, whose re-show requests yet another
                // repaint (the settle's endless paint loop).
                var trimOnly = root.progress != null && root._textureReady && root._lastSplit === root.progress.split && _prefixFrom() > 0 && (root._vectorCoversFrom === 0 || root._vectorCoversFrom === _prefixFrom());
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
                //
                // The clear waits for the raster's own TEXTURE: the
                // image's decode is off-thread, so a clear on the
                // predicate alone withdrew the whole printed history
                // for the length of the decode (measured: the frame at
                // the full split was bit-identical to the face with no
                // raster at all). Until the texture lands the canvas IS
                // the picture — the vector places the same stroke at
                // the same row (the invariance sweep) — and the slot's
                // own onStatusChanged repaints this canvas the moment
                // it does.
                if (_exactFullStanding()) {
                    ctx.reset();
                    ctx.clearRect(0, 0, width, height);
                    root._lastSplit = split;
                    root._paintsSinceReset += 1;
                    root._vectorCoversFrom = -1;
                    return;
                }
                var current = _scrubVector();
                if (current == null) {
                    // A full-state payload carries no vector, so this
                    // paint can only clear. While the travels' texture
                    // decodes the standing ink is the picture's other
                    // half — hold it for the same view it was painted
                    // at, and let the travels' arrival end the hold.
                    if (_travelsPending() && _viewKey() === root._accumViewKey) {
                        return;
                    }
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
                if (prefixFrom <= 0 && split > 0 && (root._prefixWasShown || (root._retainedPrefixSource !== "" && root._retainedPrefixApplies())) && root._vectorCoversFrom !== 0 && layer.prefixData !== undefined && layer.prefixData !== "" && split != null && split < layer.motions && Math.max(root._lastSplit, root._retainedPrefixSplit) >= split) {
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
                // The anti-drift re-raster: the accumulated delta bitmap
                // fully redraws on its own cadence so it cannot drift.
                // With a prefix owning the history the cadence stretches
                // wide — every prefix refresh re-establishes the picture
                // (the 3% incremental refreshes), and a full re-raster
                // mid-drag is the live hitch the forward scrub showed
                // (a ~200 ms UI-thread walk every 20 paints on a dense
                // layer).
                // The anti-drift re-raster: the accumulated delta bitmap
                // fully redraws on its own cadence so it cannot drift.
                // With a prefix owning the history the cadence stretches
                // wide — every prefix refresh re-establishes the picture
                // (the 3% incremental refreshes), and a full re-raster
                // mid-drag is the live hitch the forward scrub showed
                // on a dense layer.
                var resetCadence = (root._prefixWasShown || _prefixModelReady()) ? 200 : 20;
                // A view change is a re-bake, never a delta: the
                // accumulated bitmap holds the old transform's ink, and
                // stroking the new transform's delta over it leaves the
                // same stroke at two rows. Read here — after the early
                // returns that clear the bitmap whole — so a paint that
                // skipped the reset still sees the change next time.
                var viewRebaked = _viewKey() !== root._accumViewKey;
                root._accumViewKey = _viewKey();
                if (root._progressDirty || split < root._lastSplit || root._paintsSinceReset >= resetCadence || vectorSourceChanged || viewRebaked) {
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
                } else if (!resetPainted && prefixFrom > 0 && root._vectorCoversFrom !== 0 && root._vectorCoversFrom !== prefixFrom) {
                    // The boundary-advance transition: the canvas holds
                    // the OLD tail under the NEW prefix's boundary (the
                    // readiness gate rejects the hybrid, and the
                    // Loading-phase full repaint is timing-dependent).
                    // Repaint the tail from the new boundary — the
                    // retained picture stands until this delivery
                    // completes the joint swap.
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
                    root._paintCovers[root._paintCovers.length - 1] = root._vectorCoversFrom;
                }
                // The freeze rides the paint, not only the delivery: a
                // bitmap starting exactly at the prefix's boundary is
                // the composition formed, and the pixels it was built
                // against must be held from HERE. A boundary advance
                // published while this paint is in flight would
                // otherwise replace the live image's source with a
                // still-loading one under an EMPTY record — and the
                // interior below the boundary belongs to that record
                // the moment the live image blinks. The delivery's own
                // freeze (the joint readiness) still covers the paints
                // that land before the prefix's upload.
                if (from > 0 && from === prefixFrom && layer.prefixData !== undefined && layer.prefixData !== "" && (root._retainedPrefixSource !== layer.prefixData || root._retainedPrefixSplit !== layer.prefixSplit || root._retainedPrefixAnchor !== root.progress.anchor)) {
                    root._retainedPrefixSource = layer.prefixData;
                    root._retainedPrefixSplit = layer.prefixSplit;
                    root._retainedPrefixAnchor = root.progress.anchor;
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

    Canvas {
        id: carryCanvas
        // The carried tail (the gesture's frozen picture): the
        // printed lines since the warm raster's split, re-painted
        // at its backing and presented through the DISPLAY
        // transform exactly like navigationImage — the pan rides
        // the item's translation, never a repaint. Hidden while
        // idle: only the entry and a mid-gesture zoom re-bake
        // request it, and a hidden canvas discards the buffer.
        //
        // A TOP-LEFT origin, because the raster the tail completes is
        // grown by a size change from a fixed corner: at 4x backing
        // the default centre origin swings this item's content by
        // size * (1 - displayScale / backing), further than the face
        // is wide, so a zoomed gesture carried no tail at all.
        visible: root._interactionActive
        width: visible ? root.width * root._navBacking() : 0
        height: visible ? root.height * root._navBacking() : 0
        x: visible ? root.displayPanX : 0
        y: visible ? root.displayPanY : 0
        scale: visible ? root.displayScale / root._navBacking() : 1.0
        transformOrigin: Item.TopLeft
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Threaded
        onPaint: {
            var ctx = getContext("2d");
            root._paintCarry(ctx);
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

    // The first run of a class that can still meet `from`: the runs
    // ascend in motion order, so a run whose LAST motion is below the
    // boundary holds nothing the walk could draw, and the runs below
    // it can be skipped unread. Without the bound a delta paint reads
    // EVERY run of the class — on a layer whose runs are short, that
    // per-run read is the paint's cost, not the stroke it draws.
    function _firstRunAt(segments, from) {
        if (from <= 0) {
            // The reset path repaints from the layer's start.
            return 0;
        }
        var low = 0;
        var high = segments.length;
        while (low < high) {
            var mid = (low + high) >> 1;
            var points = segments[mid];
            if (points.length > 0 && points[points.length - 1][2] >= from) {
                high = mid;
            } else {
                low = mid + 1;
            }
        }
        return low;
    }

    function _edgePrinted(points, i, split) {
        return i < points.length && (split < 0 || points[i][2] < split);
    }

    function _paintCarry(ctx) {
        // The gesture's carried tail: the printed lines since the
        // warm raster's own split, re-painted as vectors at its
        // backing so the entry drops nothing. The window is one
        // follow interval of lines and the paint is pan-free — the
        // canvas item's translation pans it with the raster.
        ctx.reset();
        var backing = root._navBacking();
        ctx.clearRect(0, 0, root.width * backing, root.height * backing);
        if (!root.available() || mapping._plot == null) {
            return;
        }
        var layer = root.progress != null && root.progress.layers != null ? root.progress.layers.current : null;
        var split = root.progress != null ? root.progress.split : null;
        if (layer == null || split == null || split <= 0) {
            return;
        }
        var current = _scrubVector();
        if (current == null) {
            return;
        }
        var navSplit = root.progress != null && root.progress.navigationSplit !== undefined && root.progress.navigationSplit !== null && root.progress.navigationSplit >= 0 ? root.progress.navigationSplit : 0;
        if (navSplit >= split) {
            return;
        }
        var scale = backing;
        // The pen follows the content: toolpathWidthPx() carries the
        // live zoom for the camera-ridden canvases, and the camera is
        // divided back out here so the stroke presents at the same
        // physical width the camera-free raster baked — the same
        // division the renderer's own floor makes.
        var widthScale = backing / (root.viewScale > 0 ? root.viewScale : 1.0);
        _drawLayer(ctx, current, 1.0, split, false, navSplit, scale, widthScale);
        if (root.showTravels) {
            _drawTravels(ctx, current.travels, split, navSplit, scale, widthScale);
        }
    }

    function _drawLayer(ctx, layer, alpha, split, base, from, scaleOverride, widthScale) {
        // The transform inlined: hundreds of thousands of
        // plateToScene calls per paint were the follower's cost.
        var plot = mapping._plot;
        var sx = plot.sx;
        var sy = plot.sy;
        var offsetX = plot.bed.offsetX;
        var offsetY = plot.bed.offsetY;
        var bedXMin = plot.bed.bedXMin;
        var bedYMax = plot.bed.bedYMax;
        // The carry override: painted at the warm raster's backing
        // with the pan dropped (the item's translation carries it).
        var backed = scaleOverride !== undefined && scaleOverride > 0;
        var scale = backed ? scaleOverride : root._view.scale;
        var panX = backed ? 0.0 : root._view.panX;
        var panY = backed ? 0.0 : root._view.panY;
        // The physical stroke: one width for every channel (the
        // ghost/pending/printed parity rule), subpixel at 100%.
        ctx.lineWidth = root.toolpathWidthPx() * (widthScale !== undefined && widthScale > 0 ? widthScale : 1.0);
        // Round joins: the default miter spikes at acute corners with
        // a length that grows with the stroke width — thick lines
        // sprouted sharp edges at every text corner (the live report).
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        for (var name in layer.classes) {
            var segments = layer.classes[name];
            ctx.strokeStyle = base ? MoonrakerTheme.seriesDefault : root.classColour(name);
            ctx.globalAlpha = alpha;
            // ONE path per class, stroked ONCE. A beginPath/stroke per
            // SEGMENT was the follower's dominant cost: every
            // contiguous run became its own software stroke, hundreds
            // or thousands per repaint, each 100-500 ms on the live
            // machine. The fresh path was never what kept a stroke
            // from bridging a travel — a stroke never joins subpaths,
            // and moveTo opens one, so batching is pixel-identical for
            // the opaque classes. For the translucent base (alpha
            // 0.55) it removes the double-compositing where two runs
            // overlap, which is the alpha-accumulation this file
            // documents elsewhere rather than a look worth keeping.
            ctx.beginPath();
            // The walk starts at the first run that can still draw, so
            // the printed runs below the boundary are never read.
            var first_run = _firstRunAt(segments, from);
            for (var s = first_run; s < segments.length; ++s) {
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
                // Opened at the first edge's OWN start vertex: the
                // stroke never bridges a travel, a feature change, or
                // the boundary the last poll painted. No pan term: the
                // item's translation carries the view.
                ctx.moveTo((offsetX + (points[i - 1][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i - 1][1]) * sy) * scale + panY);
                while (_edgePrinted(points, i, split)) {
                    ctx.lineTo((offsetX + (points[i][0] - bedXMin) * sx) * scale + panX, (offsetY + (bedYMax - points[i][1]) * sy) * scale + panY);
                    ++i;
                }
            }
            ctx.stroke();
            ctx.globalAlpha = 1.0;
        }
    }

    function _drawTravels(ctx, segments, split, from, scaleOverride, widthScale) {
        if (segments == null) {
            return;
        }
        var backed = scaleOverride !== undefined && scaleOverride > 0;
        var scale = backed ? scaleOverride : root._view.scale;
        var panX = backed ? 0.0 : root._view.panX;
        var panY = backed ? 0.0 : root._view.panY;
        ctx.strokeStyle = MoonrakerTheme.plateTravel;
        ctx.globalAlpha = 0.8;
        ctx.lineWidth = root.travelWidthPx() * (widthScale !== undefined && widthScale > 0 ? widthScale : 1.0);
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
            ctx.moveTo(scene.x * scale + panX, scene.y * scale + panY);
            while (_edgePrinted(points, i, split)) {
                scene = mapping.plateToScene(points[i][0], points[i][1]);
                if (scene != null) {
                    ctx.lineTo(scene.x * scale + panX, scene.y * scale + panY);
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
            // The press records the grab point only: the warm raster
            // waits for a movement that actually pans (the review's
            // ruling — a click at any zoom never flips the scene).
            root._dragX = mouse.x;
            root._dragY = mouse.y;
            root._pressX = mouse.x;
            root._pressY = mouse.y;
            root._pressPanX = root.viewPanX;
            root._pressPanY = root.viewPanY;
            // The one-off drag snapshot: the press asks the model for
            // an immediate warm-raster bake with the current split,
            // so the first movement latches a raster that has caught
            // up with the painted lines — the press-to-drag latency
            // covers the bake, and the always-running follow window
            // stays wide (the live report: a tight window chased the
            // split per quarter-second and the perf hit followed).
            if (root.printerModel != null && root.attached && root.viewScale > 1.0) {
                root.printerModel.setFollowerGestureBake();
            }
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
            // The clamp works from the PRESS origin, not the live
            // view: while the entry gate holds, the view itself stays
            // frozen (its repaint pipeline is what smeared the lines
            // — the canvas tail re-baked per tick while the prefix
            // image content lagged), so the buffered drag must not
            // accumulate through the frozen target.
            var clamped = root._softClampPan(root._pressPanX + mouse.x - root._pressX, root._pressPanY + mouse.y - root._pressY);
            var appliedX = clamped.x - root.viewPanX;
            var appliedY = clamped.y - root.viewPanY;
            // The interaction raster flips in only when this
            // movement pans for real: a click's zero delta and a
            // boundary-blocked drag never activate it (the review's
            // ruling — no warm-raster transition on inert gestures).
            if (appliedX !== 0.0 || appliedY !== 0.0) {
                root._enterInteraction();
            }
            // The whole camera (target, display AND the raster's
            // presentation) moves only once the interaction has
            // entered — then in one coherent jump to the clamped
            // target. During the gate's hold nothing moves: no
            // smear, no mixed-rate drag. With NO eligible raster at
            // all the camera still follows the pointer directly —
            // the degraded exact-scene path must keep working (the
            // freeze is only the hold, never a dead drag).
            if (root._interactionActive || navigationData() === "") {
                root.viewPanX = clamped.x;
                root.viewPanY = clamped.y;
                root.displayPanX = clamped.x;
                root.displayPanY = clamped.y;
            }
            root._panDragDeltaX += appliedX;
            root._panDragDeltaY += appliedY;
            root._dragX = mouse.x;
            root._dragY = mouse.y;
        }
        onReleased: root._finishGesture()
        onCanceled: root._finishGesture()
        onDoubleClicked: {
            // At the 100% fit there is nothing to reset (the live
            // ruling: click/down/drag stay inert at full zoom).
            if (root.viewScale <= 1.0) {
                return;
            }
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
        objectName: "moonrakerPlateZoomScope"
        visible: root.available() && !root.compact
        // Wide enough for the "800%" label (the live report: the
        // percentage overflowed the scope's bounds). Docked it hugs
        // the right edge; parked it slides fully out of view (the
        // live request).
        width: 38 * screenScaleFactor
        // The full canvas height (the live request: the bar spans
        // the canvas edge to edge now the reset control lives in
        // the host's checkbox row).
        height: parent.height
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
                    // handle immediately (like the drag pan), and the
                    // pan retargets so the viewport CENTRE stays put
                    // (the live ruling — no bed-origin zoom). The
                    // view scale lands FIRST (the soft clamp reads
                    // it), while the retarget still reads the OLD
                    // display transform — assigning the display
                    // scale first would degenerate the anchor to 0.
                    root.viewScale = target;
                    if (target <= 1.0) {
                        root.viewPanX = 0.0;
                        root.viewPanY = 0.0;
                        root.displayPanX = 0.0;
                        root.displayPanY = 0.0;
                        root.displayScale = target;
                    } else {
                        var pan = root._barZoomPan(target);
                        root.viewPanX = pan.x;
                        root.viewPanY = pan.y;
                        root.displayPanX = pan.x;
                        root.displayPanY = pan.y;
                        root.displayScale = target;
                    }
                }
            }
        }
    }

    // The view reset (the live request): back to the 100% fit and
    // centred. The host owns the "Reset view" control — it sits in
    // the checkbox row outside the canvas, so the plate never
    // reflows when the control appears.
    function resetView() {
        root.viewScale = 1.0;
        root.viewPanX = 0.0;
        root.viewPanY = 0.0;
    }

    // The scrub input ends any camera interaction outright: the
    // split change is its own commit, and a latched warm raster
    // standing over the hidden exact scene during scrub repaints is
    // the wrong picture between frames.
    function endInteraction() {
        zoomAnimator.stop();
        root._interactionActive = false;
        // Every exit — the pan release, the zoom snap, the scrub —
        // resumes the model's publications, and releases the file
        // hold so the cache can collect it.
        root._releasePublishFreeze();
        if (root.printerModel != null) {
            root.printerModel.setFollowerGestureRaster("");
        }
    }

    // The retained handover's frozen pixels bake the view transform
    // (the rasters render through the plot): a view change retires
    // them, or the next loading gap would draw the print displaced.
    // The full-picture hold's frame is the same class and retires
    // with it.
    function _retireRetainedView() {
        if (root._retainedPrefixSource !== "") {
            _retireRetained();
        }
        root._heldFullSource = "";
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
