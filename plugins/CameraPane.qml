import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import MoonrakerPrintFollower 1.0
import "theme"

// The webcam card: the stream viewport with its stall watchdog and the
// Live badge, the disconnected veil over a stale frame, and the camera
// control bar. Property-driven — the host passes the printer model and
// its configured flag, keeps the card's Layout properties and owns the
// camera URL (applyCamera); the card keeps no state the model does not.
Cura.RoundedRectangle {
    id: root

    // The printer model the host passes (root.printer): every model
    // read inside the card goes through this property.
    property var printerModel: null

    // The host's cameraConfigured: a webcam URL is present.
    property bool configured: false

    // The viewport's width: the host's squeeze latch reads it.
    property real viewportWidth: cameraViewport.width

    // The card's content height: the host's Layout.preferredHeight
    // binding reads it, so the console pane below keeps absorbing the
    // leftover space exactly as it did when both cards shared one
    // document.
    property real contentHeight: cameraColumn.implicitHeight

    // The image's visible AND source are applied IMPERATIVELY, by the
    // host: bindings in this dynamically created document do not
    // reliably re-evaluate when the model's camera values land (the
    // first-entry stream never starting — the refresh button worked
    // because its nonce bump is the one path that provably re-drives
    // the image on their machine).
    // applyCamera is the stream's SOLE owner (the third camera-delay
    // cause): the forked renderer's setSourceURL restarts a
    // STARTED image, so the previous onSourceChanged start raced that
    // auto-restart into TWO stream starts per application. The image
    // is stopped FIRST — the setter then cannot auto-restart while
    // the assignment is in progress — and exactly one start() runs,
    // at the end, only when a real URL should run.
    property bool _cameraApplyInProgress: false
    // The applied-state latches (the duplicate-start find):
    // the renderer's start() used to be DESTRUCTIVE — it stops the
    // live reply first — so applying the same desired state twice
    // killed a healthy stream. With the latches, a redundant
    // application is a no-op: no stop, no source re-assignment, no
    // start, no second HTTP connection. The latched URL is the
    // _stateKey identity: a rotated upstream nonce in the query is
    // still the same desired stream, while an mpf_reload bump (the
    // model's explicit reload) and a camera= selection change still
    // restart.
    property string _appliedUrl: ""
    property bool _appliedVisible: false
    // The T0-T9 timing chain's T9 gate (the reviewer's cold-start
    // diagnostics): the host mirrors the client's trace flag; the
    // first decoded frame lands on the model's slot so T9 shares the
    // SAME monotonic origin as every Python stage.
    property bool traceCameraTiming: false

    // The pane's process-wide diagnostic id (the cold-start trace):
    // every apply/start/stop line carries it, so two pane instances
    // (two machine models) can never be confused in the log.
    property int paneId: -1

    Component.onCompleted: {
        // The trace-gated console.log stamps every pane creation even
        // when the model has not resolved (the model-slot trace is
        // silent for a null model, which hid the second pane's birth
        // in the 0221 trace).
        if (root.traceCameraTiming) {
            console.log("Moonraker camera pane created (model attached: " + (root.printerModel != null) + ")");
        }
        if (root.printerModel != null) {
            paneId = root.printerModel.cameraPaneInstanceId();
            root.printerModel.cameraPaneTrace(paneId, "pane created");
        }
    }

    onPrinterModelChanged: {
        // A pane whose model lands AFTER construction (the first view
        // instance creates before the printer binding resolves) still
        // needs its diagnostic id — pane -1 in the cold-start trace.
        if (paneId < 0 && root.printerModel != null) {
            paneId = root.printerModel.cameraPaneInstanceId();
            root.printerModel.cameraPaneTrace(paneId, "pane created (late model)");
        }
    }

    function _queryValue(query, name) {
        // The parameter's value, or null when the query has no such
        // parameter (an empty value is still a value).
        var parts = query.split("&");
        for (var i = 0; i < parts.length; i++) {
            var part = parts[i];
            var eq = part.indexOf("=");
            if ((eq >= 0 ? part.slice(0, eq) : part) === name) {
                return eq >= 0 ? part.slice(eq + 1) : "";
            }
        }
        return null;
    }

    function _stateKey(text) {
        // The stream identity: the query-stripped URL plus the
        // parameters that name a DIFFERENT stream — the camera a
        // direct URL selects, and the plugin's own reload marker
        // (manual refresh, watchdog recovery, reconnect). Nothing else
        // in the query counts: the rest is upstream rotation (a token
        // or timestamp the camera service rewrites per poll), and a
        // healthy connection must not be killed for it.
        var cut = text.indexOf("?");
        if (cut < 0) {
            return text;
        }
        var query = text.slice(cut + 1);
        var key = text.slice(0, cut);
        var camera = _queryValue(query, "camera");
        if (camera !== null) {
            key += "?camera=" + camera;
        }
        var reload = _queryValue(query, "mpf_reload");
        if (reload !== null) {
            key += (camera === null ? "?" : "&") + "mpf_reload=" + reload;
        }
        return key;
    }

    function applyCamera(url, visible) {
        var text = url != null ? url.toString() : "";
        var shouldRun = visible && text.length > 0;
        // The trace strips the query: the nonce is noise and a
        // credential must never ride the diagnostic.
        var shown = text.indexOf("?") >= 0 ? text.slice(0, text.indexOf("?")) : text;
        // The trace-gated console.log stamps the apply even when the
        // model has not resolved (the silent apply in the 0221 trace).
        if (root.traceCameraTiming) {
            console.log("Moonraker camera pane " + paneId + ": applyCamera visible=" + visible + " url=" + shown + " (image was visible=" + cameraImage.visible + ")");
        }
        if (root.printerModel != null) {
            root.printerModel.cameraPaneTrace(paneId, "applyCamera visible=" + visible + " url=" + shown + " (image was visible=" + cameraImage.visible + ")");
        }
        if (_stateKey(text) === _appliedUrl && visible === _appliedVisible) {
            if (root.printerModel != null) {
                root.printerModel.cameraPaneTrace(paneId, "applyCamera no-op: state unchanged");
            }
            return;
        }
        _cameraApplyInProgress = true;
        try {
            cameraImage.stop();
            cameraImage.source = url;
            cameraImage.visible = visible;
            if (!shouldRun) {
                // An applied EMPTY url clears the painted frame: the
                // stream-off must not keep the last frame (the live
                // request — the resume flashed it).
                cameraImage.clearFrame();
            }
        } finally {
            // The guard must release even if an assignment throws —
            // a stuck latch would silence the visibility lifecycle
            // handler forever (the review's hardening note).
            _cameraApplyInProgress = false;
        }
        _appliedUrl = _stateKey(text);
        _appliedVisible = visible;
        if (shouldRun) {
            if (root.printerModel != null) {
                root.printerModel.cameraPaneTrace(paneId, "applyCamera starting the stream");
            }
            cameraImage.start();
        }
    }

    // ── the control bar: the zoom scale and the decode rate ─────────
    // The webcam's decode throttle: the renderer decodes at this rate
    // and no faster, so an idle monitor page stops paying for frames
    // nobody sees. The rate is the user's, the ceiling is the
    // camera's own configured target_fps from Moonraker's webcam
    // list (never a product constant).
    readonly property real cameraFps: root.printerModel != null ? root.printerModel.cameraFps : 0
    readonly property real cameraFpsMin: root.printerModel != null ? root.printerModel.cameraFpsMin : 0.5
    readonly property real cameraFpsMax: root.printerModel != null ? root.printerModel.cameraFpsMax : 30

    // ONE bar, TWO faces. The wheel is the picture's gesture, so the
    // zoom scale is the face the bar rests on and a Shift notch turns
    // it over to the rate's (the live request). They never share the
    // box: the bar leaves on the face that was showing and comes back
    // in on the other, so a mode change never reads as one bar
    // changing its mind mid-slide.
    property string cameraBarMode: "zoom"
    // The face a turn-over is on its way to, held aside so the
    // slide-out leg still shows the face that is leaving.
    property string _cameraBarNextMode: "zoom"
    // True while a turn-over is under way: a second gesture landing
    // mid-turn is answered with the face it asked for at once, rather
    // than queued behind the errand.
    property bool _cameraBarTurning: false
    // The dock state, the plate zoom scope's own rhythm: a change docks
    // the bar, five idle seconds park it out of view.
    property bool cameraBarDocked: false
    // A view held past the fit PINS the zoom scale: the zoom is a state
    // the user is holding, and the scale that reads it stays up with it —
    // parking that away hides the control the picture is about (the live
    // report: a rate change took the zoom scale's place for good).
    readonly property bool cameraBarPinned: root.cameraZoom > 1.0
    // On screen while docked, and while a held zoom pins it. A turn-over
    // is out on both counts — the swap needs the bar off the picture to
    // trade faces on — so the pin must not paper over the slide-out leg.
    readonly property bool cameraBarShown: root.cameraBarDocked || (root.cameraBarPinned && !root._cameraBarTurning)
    // The face's own handle while it is held: a press on either scale
    // rides this, so the park can let the handle go instead of leaving a
    // drag alive on a card that has slid out of the picture (the live
    // report: the pointer went on driving an invisible scale).
    property Item cameraBarHandle: null
    // The bar's own box, one width for both faces, and the slide it
    // shares with its compact stand-in: the distance from the docked
    // place to the parked one. Only the SLIDE animates, never x itself —
    // a resized view has to move the docked control with the picture's
    // edge in the same frame instead of dragging it there over 180 ms
    // (the live report: the bar floated and caught up).
    // Wide enough for the widest readout either face shows — the rate's
    // own floor, "0.50 fps" (44.5 px of small font on the capture theme),
    // is what sets it (the live report: the floor's value ran out of the
    // bar). One width for both faces, so the zoom scale's readout rides
    // the same box. The 6 is the bar's own 1px border each side plus the
    // 4 the readout keeps clear; the 52 is the floor the graduations
    // need, and it still wins on the capture font — a host whose font is
    // wider takes the bar past it instead of clipping the readout.
    readonly property real cameraBarWidth: Math.max(52 * screenScaleFactor, cameraFpsReadoutLabel.contentWidth + 6 * screenScaleFactor, cameraZoomReadoutLabel.contentWidth + 6 * screenScaleFactor)
    // The graduations' own baseline: the height the bar is worth showing
    // at, never under 90 either. The fallback rule is measured against
    // THIS, not against the bar's live height, so a taller pane grows the
    // bar without moving where the chip takes over.
    readonly property real cameraBarBaseHeight: 180 * screenScaleFactor
    // What the top-right chip's band costs the picture's height: the bar
    // sits centred, so that band has to clear above and below it.
    readonly property real cameraBarChipBand: 2 * (UM.Theme.getSize("narrow_margin").height + cameraStreamChip.height + UM.Theme.getSize("narrow_margin").height)
    // As tall as the picture carries it — half of a tall pane, the
    // baseline on a picture that only just fits the bar — and never into
    // the chip's band, so the taller bar cannot reach the decode readout.
    readonly property real cameraBarHeight: Math.max(90 * screenScaleFactor, Math.min(Math.max(root.cameraBarBaseHeight, root.cameraPictureHeight / 2), root.cameraPictureHeight - root.cameraBarChipBand))
    // The distance that takes a face of the bar clear of the picture:
    // its own width plus the margin it docks at, and the hair that puts
    // the parked box past the edge rather than on it. Measured per item —
    // a fixed distance shorter than the bar left most of it in frame,
    // hugging the edge (the live report).
    function cameraBarParkSlide(itemWidth) {
        return itemWidth + UM.Theme.getSize("narrow_margin").width + 2 * screenScaleFactor;
    }
    // The last rate seen, so the model binding LANDING never docks the
    // control on its own — only a genuine change does.
    property real _fpsLastSeen: 0

    // The rate the top-right chip reports. The renderer's sample is one
    // second wide, so it cannot resolve a rate of one frame per second
    // or less: a window with no frame reads 0 and the next reads 1, and
    // the field appeared and disappeared with every sample (the live
    // report). At and below that resolution the throttle's own rate IS
    // what the renderer decodes at, so the field holds it and stays
    // put; above it the measured rate is the honest one.
    readonly property bool fpsBelowSample: root.cameraFps > 0 && root.cameraFps <= 1.0
    readonly property real fpsReadout: root.fpsBelowSample ? root.cameraFps : cameraImage.recentDisplayedFPS
    readonly property bool fpsReadoutShown: root.fpsBelowSample || cameraImage.recentDisplayedFPS > 0

    // The control rides the CAMERA VIEW — the decoded frame the Live
    // badge and the stream chip sit on — so both the room it asks for
    // and the edge it docks to are the picture's own, never the
    // letterboxed pane frame's.
    //
    // The picture's VISIBLE extent: the item's own box is the stream's
    // own aspect, and a rotated stream draws it turned, so the box that
    // is actually on screen is the swapped one. Everything that measures
    // the camera view — the fit rules, the frame's clip box — measures
    // this.
    readonly property real cameraPictureWidth: cameraImage.imageRotated ? cameraImage.height : cameraImage.width
    readonly property real cameraPictureHeight: cameraImage.imageRotated ? cameraImage.width : cameraImage.height

    // The full scale needs real room, and the room it competes for is
    // the stream chip's own CORNER: the bar is a column down the right
    // edge, so a picture short enough to push the column's top up into
    // the chip's band has to hand that corner over — the scale would
    // cover the decode readout (the live report: the bar occluded the
    // top-right chip as the view shrank). The bar's top is half of what
    // the picture has over the column's own height, so the picture must
    // clear that band above and below it; anything shorter falls back to
    // the compact chip, well before the bar's own box runs out.
    readonly property bool cameraBarFits: root.cameraPictureWidth >= 200 * screenScaleFactor && root.cameraPictureHeight >= root.cameraBarBaseHeight + root.cameraBarChipBand
    // The overlays' shared liveness contract: no model, no camera or a
    // stopped stream has neither a decode rate to show nor a picture to
    // zoom.
    readonly property bool cameraControlLive: root.configured && root.printerModel != null && root.printerModel.monitorConnected && root.printerModel.webcamStreamEnabled && cameraImage.visible && cameraImage.imageWidth > 0

    onCameraFpsChanged: {
        if (root._fpsLastSeen > 0 && root.cameraFps !== root._fpsLastSeen) {
            root.dockCameraBar("fps");
        }
        root._fpsLastSeen = root.cameraFps;
    }

    // The handle's own press bookkeeping: the scales' MouseAreas report
    // the grab here, and a held handle holds the park off. The five idle
    // seconds measure an idle card, and a card under a hand is not idle:
    // letting the park run there slid the card out from under the pointer
    // and left the press on a scale that was no longer on the picture
    // (the live report: the handle stayed grabbed and went on driving an
    // invisible control). The idle clock starts again at the release.
    function cameraBarHandlePressed(handle) {
        root.cameraBarHandle = handle;
        cameraBarParkTimer.stop();
    }

    function cameraBarHandleReleased() {
        root.cameraBarHandle = null;
        cameraBarParkTimer.restart();
    }

    Timer {
        id: cameraBarParkTimer
        interval: 5000
        onTriggered: {
            if (root.cameraBarHandle !== null) {
                cameraBarParkTimer.restart();
                return;
            }
            if (root.cameraBarPinned && root.cameraBarMode !== "zoom") {
                // The zoomed view's pin outlives the rate card's own five
                // seconds: the bar trades back to the scale the picture is
                // about, which then stays up (the live report: the rate
                // card kept the place).
                root.dockCameraBar("zoom");
            } else {
                root.cameraBarDocked = false;
            }
        }
    }

    Timer {
        // The turn-over's second half, and not a poll: the slide-out leg
        // is the 180 ms the bar's own Behavior runs, plus a frame. The
        // new face is set behind the parked bar, so it rides the slide
        // back in.
        id: cameraBarSwapTimer
        interval: 190
        onTriggered: {
            root._cameraBarTurning = false;
            root.cameraBarMode = root._cameraBarNextMode;
            root.cameraBarDocked = true;
            cameraBarParkTimer.restart();
        }
    }

    function dockCameraBar(mode) {
        // A turn-over already bound for this face is this gesture's own
        // answer: a rate the gesture just set echoes back through the
        // model a moment later, and letting that second ask cut the
        // slide-out short slammed the new card in without a swap (the
        // live report: the two cards never traded places).
        if (root._cameraBarTurning && root._cameraBarNextMode === mode) {
            return;
        }
        // A gesture names the face it belongs to. The same face again is
        // only a nudge to the park timer — and it calls off a turn-over
        // that was leaving this face.
        if (root.cameraBarMode === mode) {
            cameraBarSwapTimer.stop();
            root._cameraBarTurning = false;
            root.cameraBarDocked = true;
            cameraBarParkTimer.restart();
            return;
        }
        if (!root.cameraBarDocked || root._cameraBarTurning) {
            // Nothing on screen to turn over — a parked bar, or a
            // turn-over already under way to the other face. The bar
            // simply arrives (or lands) wearing the face the latest
            // gesture asked for.
            cameraBarSwapTimer.stop();
            root._cameraBarTurning = false;
            root.cameraBarMode = mode;
            root.cameraBarDocked = true;
            cameraBarParkTimer.restart();
            return;
        }
        // Out on the face that is leaving, back in on the other.
        root._cameraBarNextMode = mode;
        root._cameraBarTurning = true;
        root.cameraBarDocked = false;
        cameraBarSwapTimer.restart();
    }

    function fpsText(value) {
        var fps = Number(value);
        if (!(fps > 0)) {
            return "—";
        }
        // The floor's own region keeps two decimals: between 0.5 and 1
        // FPS the one-notch steps are hundredths apart, and a readout
        // that rounded them together would hide the control's work.
        if (fps < 1) {
            return fps.toFixed(2) + " fps";
        }
        return (fps >= 10 ? fps.toFixed(0) : fps.toFixed(1)) + " fps";
    }

    function fpsFraction(value) {
        // The bar's graduations are EVENLY spaced. The log spacing the
        // plate zoom scope uses is right for a 100%..800% zoom — every
        // mark of a camera's own low rates crowds into the bottom of
        // the bar and the top is left bare (the live report) — and
        // wrong for a rate that is read as a rate.
        var low = root.cameraFpsMin;
        var high = Math.max(root.cameraFpsMax, low);
        var fps = Number(value);
        if (!(high > low) || !(fps > 0)) {
            return 0;
        }
        return (Math.min(high, Math.max(low, fps)) - low) / (high - low);
    }

    function fpsFromFraction(fraction) {
        var low = root.cameraFpsMin;
        var high = Math.max(root.cameraFpsMax, low);
        if (!(high > low)) {
            return low;
        }
        var position = Math.min(1.0, Math.max(0.0, Number(fraction)));
        return Math.round((low + position * (high - low)) * 100) / 100;
    }

    function fpsClamped(value) {
        // The one coercion every rate passes through: the camera's own
        // range, rounded to the hundredth the readout and the marker
        // are drawn from.
        var low = root.cameraFpsMin;
        var high = Math.max(root.cameraFpsMax, low);
        return Math.round(Math.min(high, Math.max(low, Number(value))) * 100) / 100;
    }

    function setFps(value) {
        if (root.printerModel == null) {
            return root.cameraFps;
        }
        var target = root.fpsClamped(value);
        // The dock comes first: a rate clamped at the ceiling still
        // deserves the feedback that the input landed.
        root.dockCameraBar("fps");
        if (target !== root.cameraFps) {
            root.printerModel.setCameraFps(target);
        }
        return target;
    }

    readonly property real fpsStep: {
        // The discrete gestures step LINEARLY: one notch is one
        // constant slice of the selected camera's range, so the same
        // travel moves the rate the same amount at every point on the
        // scale (the live report: the old 1.25x step accelerated
        // towards the top end, where a single notch was worth several
        // FPS). Roughly thirty slices span any camera's range, and the
        // slice never falls below the half FPS the floor region is
        // read in.
        var span = Math.max(root.cameraFpsMax, root.cameraFpsMin) - root.cameraFpsMin;
        return Math.max(0.5, Math.round(span / 15) / 2);
    }

    function stepFps(base, direction) {
        // One step of the linear ladder from *base* — the caller's own
        // value, not the published rate: a commit only echoes back
        // through the model on a later turn, so a drag stepping from
        // the published rate would reapply one base for every move it
        // lands inside a turn. The result equals the base exactly when
        // the step cannot move, which is how the caller learns it has
        // reached the floor or the ceiling.
        var current = Number(base);
        if (!(current > 0)) {
            current = root.cameraFps;
        }
        return root.fpsClamped(current + (direction > 0 ? root.fpsStep : -root.fpsStep));
    }

    function wheelNotch(wheel) {
        // The notch is whichever axis the wheel reported: a shifted
        // wheel arrives on the HORIZONTAL axis on some systems, and the
        // gesture must not die with it (the live report: Shift did
        // nothing at all).
        return wheel.angleDelta.y !== 0 ? wheel.angleDelta.y : wheel.angleDelta.x;
    }

    function nudgeFps(delta) {
        if (!(delta > 0) && !(delta < 0)) {
            return false;
        }
        var target = root.stepFps(root.cameraFps, delta);
        root.setFps(target);
        return target !== root.cameraFps;
    }

    readonly property var fpsGraduations: {
        // The ruler, three weights: a thin double tick at every 2.5
        // FPS, a continuous thin line at every 5, a thick line at
        // every 10 — and the range's own ends marked, the camera's
        // ceiling at the top and the 0.5 floor at the bottom.
        var maximum = Math.max(root.cameraFpsMax, root.cameraFpsMin);
        var steps = [
            {
                "value": root.cameraFpsMin,
                "major": true,
                "line": true
            }
        ];
        for (var value = 2.5; value < maximum - 0.001; value += 2.5) {
            steps.push({
                "value": value,
                "major": Math.abs(value % 10) < 1e-9,
                "line": Math.abs(value % 5) < 1e-9
            });
        }
        steps.push({
            "value": maximum,
            "major": true,
            "line": true
        });
        return steps;
    }

    // ── the zoom face's own scale ───────────────────────────────────
    // Every 25% from the fit to the 800% ceiling, LOG-positioned: the
    // fine granularity belongs where the low zooms are, which is the
    // spacing the rate scale deliberately does not borrow. The marker
    // rides the same curve and doubles as the drag handle; the bottom of
    // the bar is the 100% fit.
    readonly property var cameraZoomGraduations: {
        var steps = [];
        for (var value = 1.0; value <= root.cameraZoomMax + 1e-9; value += 0.25) {
            steps.push(value);
        }
        return steps;
    }

    function cameraZoomFraction(value) {
        var zoom = Number(value);
        if (!(zoom > 0)) {
            return 0;
        }
        return Math.log(Math.min(root.cameraZoomMax, Math.max(1.0, zoom))) / Math.log(root.cameraZoomMax);
    }

    function cameraZoomFromFraction(fraction) {
        var position = Math.min(1.0, Math.max(0.0, Number(fraction)));
        return Math.round(Math.pow(root.cameraZoomMax, position) * 1000) / 1000;
    }

    // ── the camera view's zoom and pan (the live request) ───────────
    // The wheel zooms the picture about the pointer and a left drag pans
    // it; the FPS throttle keeps the wheel only while Shift is held and
    // the vertical drag on the right button, so the plain gestures are
    // the ones a picture is expected to answer. The view rides a
    // transform, so the frame box — and with it every overlay, the veil
    // and the FPS control — stays put while the picture moves under it.
    property real cameraZoom: 1.0
    // The pan is stored in the picture's own units and CLAMPED as it is
    // stored — never only on the way to the transform. A pan that ran
    // past the limit and was held there would swallow the first stretch
    // of every drag back (the live report: past a certain zoom the
    // picture would not pan horizontally at all), so the stored value
    // never leaves the limit; the bindings below re-clamp it when the
    // limit itself moves (a shrunken pane), never a timer.
    property real cameraPanX: 0
    property real cameraPanY: 0
    // The 100% fit is the floor, the plate zoom scope's own 800% the
    // ceiling, one wheel notch the FPS throttle's own 1.25x step.
    readonly property real cameraZoomMax: 8.0
    readonly property real cameraZoomStep: 1.25
    readonly property real cameraPanLimitX: Math.max(0, cameraImage.width * (root.cameraZoom - 1) / 2)
    readonly property real cameraPanLimitY: Math.max(0, cameraImage.height * (root.cameraZoom - 1) / 2)
    readonly property real cameraPanOffsetX: Math.max(-root.cameraPanLimitX, Math.min(root.cameraPanLimitX, root.cameraPanX))
    readonly property real cameraPanOffsetY: Math.max(-root.cameraPanLimitY, Math.min(root.cameraPanLimitY, root.cameraPanY))

    onCameraPanLimitXChanged: root.clampCameraPan()
    onCameraPanLimitYChanged: root.clampCameraPan()

    // The selected camera's index: switching cameras is a new picture,
    // and the view starts it at the fit (the live request).
    readonly property int cameraIndex: root.printerModel != null ? root.printerModel.activeWebcamIndex : -1
    onCameraIndexChanged: root.resetCameraView()

    function clampCameraPan() {
        root.cameraPanX = Math.max(-root.cameraPanLimitX, Math.min(root.cameraPanLimitX, root.cameraPanX));
        root.cameraPanY = Math.max(-root.cameraPanLimitY, Math.min(root.cameraPanLimitY, root.cameraPanY));
    }

    // The zoom rides the frame centre and the pan is the displaced
    // frame's offset from it. Both live in the DISPLAYED frame's axes,
    // whatever the camera rotation does: Qt applies the item's own
    // rotation first and the `transform` list after it (probed — a
    // translate under rotation 90 moves the picture along the displayed
    // x axis, not the turned one), so a pointer position and a drag
    // delta are used as they arrive.
    function zoomCamera(step, x, y) {
        // The dock comes first: a zoom clamped at the ceiling or at the
        // fit still deserves the feedback that the input landed.
        root.dockCameraBar("zoom");
        // The scale is anchored at the frame centre, so the point under
        // the pointer would slide out from under it; the pan is
        // re-solved to hold it (the plate scope's no-bed-origin ruling).
        var target = Math.min(root.cameraZoomMax, Math.max(1.0, root.cameraZoom * step));
        if (target === root.cameraZoom) {
            return;
        }
        var ratio = target / root.cameraZoom;
        root.cameraPanX += (x - cameraImage.width / 2 - root.cameraPanX) * (1 - ratio);
        root.cameraPanY += (y - cameraImage.height / 2 - root.cameraPanY) * (1 - ratio);
        root.cameraZoom = target;
        if (target <= 1.0) {
            root.cameraPanX = 0;
            root.cameraPanY = 0;
        }
        root.clampCameraPan();
    }

    function setCameraZoom(value) {
        // The bar handle's own contract: the pointer's track position IS
        // the zoom, and the picture's centre stays where it is — the bar
        // has no pointer over the picture to hold still.
        var target = Math.min(root.cameraZoomMax, Math.max(1.0, Number(value)));
        if (!(target > 0) || target === root.cameraZoom) {
            return;
        }
        root.cameraZoom = target;
        if (target <= 1.0) {
            root.cameraPanX = 0;
            root.cameraPanY = 0;
        }
        root.clampCameraPan();
    }

    function panCamera(dx, dy) {
        // The picture follows the pointer exactly, up to the limit: the
        // clamp lands on the STORED pan, so the next drag in the
        // opposite direction moves the picture at once.
        root.cameraPanX += dx;
        root.cameraPanY += dy;
        root.clampCameraPan();
    }

    function resetCameraView() {
        // The fit and the centre, back in one gesture (the double
        // click). The FPS rate is NOT a view state and is left alone.
        root.cameraZoom = 1.0;
        root.cameraPanX = 0;
        root.cameraPanY = 0;
        // The fit leaves the zoom face nothing to hint at, so the bar
        // goes at once rather than waiting out the idle five seconds —
        // a camera switch parks it through this too (the live request).
        // A camera switch must not leave a stale grab behind: the park
        // reads it, and a stale one would keep the bar up for good.
        root.cameraBarHandle = null;
        cameraBarSwapTimer.stop();
        root._cameraBarTurning = false;
        root.cameraBarDocked = false;
        cameraBarParkTimer.stop();
    }

    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    color: UM.Theme.getColor("main_background")
    radius: UM.Theme.getSize("default_radius").width

    ColumnLayout {
        id: cameraColumn
        anchors.fill: parent
        spacing: 0

        RowLayout {
            // The title row (the live-run ruling): the camera
            // controls ride LEVEL with the pane title, butted
            // against it with one margin — the selector already
            // carries the camera name, so no separate label.
            Layout.fillWidth: true
            Layout.topMargin: UM.Theme.getSize("default_margin").height
            Layout.leftMargin: UM.Theme.getSize("default_margin").width
            Layout.rightMargin: UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").width

            UM.Label {
                // The pane title, in the other panes'
                // style — the same large bold face as
                // Information and Printer status (a
                // live report).
                text: "Webcam"
                font: UM.Theme.getFont("large_bold")
                color: UM.Theme.getColor("text")
            }

            Item {
                // The controls contribute their CRUSH width to the
                // title row's implicit — the combo's minimum plus
                // the button — so the pane's implicit budget stays
                // where the old bar left it and the monitor
                // document's column equilibrium (the squeeze latch
                // and the status column's geometry contract) is
                // unchanged.
                Layout.fillWidth: true
                implicitWidth: cameraControls.visible ? cameraSelector.Layout.minimumWidth + UM.Theme.getSize("small_button_icon").width + UM.Theme.getSize("narrow_margin").width : 0
                Layout.alignment: Qt.AlignVCenter
                height: cameraControls.implicitHeight

                ColumnLayout {
                    id: cameraControls
                    objectName: "cameraControls"
                    // No cameras, no controls: a dropdown and a
                    // refresh button are dead chrome under the
                    // "No webcam configured" message.
                    visible: root.printerModel != null && root.printerModel.webcamNames.length > 0
                    anchors.fill: parent
                    spacing: 0

                    RowLayout {
                        Layout.alignment: Qt.AlignLeft
                        // The inset keeps the combo from ever touching
                        // the pane edge at the crush (a live report).
                        width: Math.min(implicitWidth, parent.width - 2 * UM.Theme.getSize("narrow_margin").width)
                        spacing: UM.Theme.getSize("narrow_margin").width

                        Cura.ComboBox {
                            id: cameraSelector
                            visible: root.printerModel != null && root.printerModel.webcamNames.length > 1
                            Layout.preferredWidth: 180 * screenScaleFactor
                            Layout.minimumWidth: 60 * screenScaleFactor
                            Layout.maximumWidth: 180 * screenScaleFactor
                            Layout.fillWidth: true
                            enabled: visible && (root.printerModel == null || root.printerModel.webcamStreamEnabled)
                            model: root.printerModel != null ? root.printerModel.webcamNames : []
                            currentIndex: root.printerModel != null ? root.printerModel.activeWebcamIndex : -1
                            onActivated: function (index) {
                                if (root.printerModel != null) {
                                    root.printerModel.selectWebcam(index);
                                }
                            }
                        }

                        UM.SimpleButton {
                            width: UM.Theme.getSize("small_button_icon").width
                            height: UM.Theme.getSize("small_button_icon").height
                            enabled: root.printerModel != null && root.printerModel.monitorConnected && root.printerModel.webcamStreamEnabled
                            color: UM.Theme.getColor("text_inactive")
                            hoverColor: UM.Theme.getColor("text")
                            iconSource: UM.Theme.getIcon("ArrowDoubleCircleRight")
                            onClicked: {
                                if (root.printerModel != null) {
                                    root.printerModel.refreshWebcams();
                                }
                            }

                            HoverHandler {
                                id: tooltipHover1
                            }
                            UM.ToolTip {
                                visible: tooltipHover1.hovered
                                targetPoint: Qt.point(parent.width / 2, 0)
                                x: 0
                                y: parent.height + UM.Theme.getSize("default_margin").height
                                width: UM.Theme.getSize("tooltip").width
                                text: "Refresh Moonraker's webcam list."
                            }
                        }

                        // The stream toggle (the live request): OFF
                        // really stops the stream — the bridge halts
                        // its upstream fetch and the controls stand
                        // down until it comes back on.
                        UM.CheckBox {
                            text: "Enable"
                            checked: root.printerModel != null ? root.printerModel.webcamStreamEnabled : true
                            onToggled: {
                                if (root.printerModel != null) {
                                    root.printerModel.setWebcamStreamEnabled(checked);
                                }
                            }
                        }
                    }
                }
            }
        }

        Item {
            id: cameraViewport
            objectName: "cameraViewport"
            Layout.fillWidth: true
            // The camera fills the pane ONLY while the
            // console is collapsed; expanded, the camera
            // fits its stream and the console (the
            // column's last child) absorbs the leftover
            // space (the rulings).
            // The viewport fills the webcam card: a
            // small stream centres inside the card, and
            // the card itself grows or fits with the
            // console's collapse state.
            Layout.fillHeight: true
            Layout.margins: UM.Theme.getSize("default_margin").width

            UM.Label {
                anchors.centerIn: parent
                // "Not configured" and "offline" are
                // different states: a configured webcam is
                // merely unreachable while Moonraker is
                // down, and must not read as missing.
                visible: !root.configured && (root.printerModel == null || root.printerModel.webcamNames.length === 0)
                text: "No webcam configured in Moonraker"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }

            UM.Label {
                anchors.centerIn: parent
                // The deliberate off state must not read as a
                // failure (the live ruling): a disabled stream shows
                // its own neutral notice, never the reconnecting
                // alarm.
                visible: !root.configured && root.printerModel != null && root.printerModel.webcamNames.length > 0 && root.printerModel.webcamStreamEnabled
                text: "Camera offline — reconnecting to Moonraker…"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }

            UM.Label {
                anchors.centerIn: parent
                visible: !root.configured && root.printerModel != null && root.printerModel.webcamNames.length > 0 && !root.printerModel.webcamStreamEnabled
                text: "Stream disabled — turn it back on in the camera controls."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }

            Item {
                // The camera frame: the picture's own bounds, and the
                // clip everything riding the picture is held to. A
                // parked FPS control must vanish as it leaves the
                // PICTURE — never linger over the letterbox beside it
                // (the live request) — and a zoomed-to-overflow picture
                // must not spill onto the rest of the pane either. The
                // box is the VISIBLE extent: a rotated stream draws in
                // the item box's swapped dims, so the item's own box can
                // poke out of this one and must not be the measure.
                id: cameraFrame
                objectName: "cameraFrame"
                width: Math.max(1, root.cameraPictureWidth)
                height: Math.max(1, root.cameraPictureHeight)
                anchors.centerIn: parent
                clip: true

                MoonrakerMJPGImage {
                    id: cameraImage
                    objectName: "cameraImage"
                    // visible and source arrive through the
                    // host's applyCamera() — no bindings
                    // here to go stale. The false default keeps the
                    // image's real state in line with the applied-state
                    // latches from the first application.
                    visible: false
                    // The renderer's own trace rides the pane's trace
                    // flag: the periodic summary and the resync lines
                    // share the cold-start diagnostic's gate.
                    traceEnabled: root.traceCameraTiming
                    rotation: root.printerModel != null ? root.printerModel.cameraRotation : 0
                    // The decode throttle (the idle-load request): the
                    // renderer decodes and repaints no faster than this,
                    // the stream itself is untouched. 0 is the renderer's
                    // own ceiling, for a pane without a model.
                    targetFps: root.printerModel != null ? root.printerModel.cameraFps : 0
                    anchors.centerIn: parent

                    property bool imageRotated: rotation === 90 || rotation === 270
                    property real maxViewWidth: cameraViewport.width
                    property real maxViewHeight: cameraViewport.height
                    property real fitScale: {
                        if (imageWidth <= 0 || imageHeight <= 0) {
                            return 1;
                        }
                        if (imageRotated) {
                            return Math.min(maxViewWidth / imageHeight, maxViewHeight / imageWidth);
                        }
                        return Math.min(maxViewWidth / imageWidth, maxViewHeight / imageHeight);
                    }

                    width: Math.max(1, Math.floor(imageWidth * fitScale))
                    height: Math.max(1, Math.floor(imageHeight * fitScale))

                    // The picture's transform: the mirror pair and the zoom
                    // about the frame centre, then the pan. The ORDER is the
                    // whole of it (probed: each entry applies to the previous
                    // one's result, and the whole list applies outside the
                    // item's own rotation) — the pan is last, so it is a
                    // displacement of the displayed frame and a drag follows
                    // the pointer whatever the camera rotation is.
                    transform: [
                        Scale {
                            origin.x: cameraImage.width / 2
                            origin.y: cameraImage.height / 2
                            xScale: (root.printerModel != null && root.printerModel.cameraFlipHorizontal ? -1 : 1) * root.cameraZoom
                            yScale: (root.printerModel != null && root.printerModel.cameraFlipVertical ? -1 : 1) * root.cameraZoom
                        },
                        Translate {
                            x: root.cameraPanOffsetX
                            y: root.cameraPanOffsetY
                        }
                    ]

                    onVisibleChanged: {
                        // The stage hide/show lifecycle reconciles
                        // THROUGH the applied-state latch: an external
                        // visibility change becomes the new desired state
                        // and the same stop/start decision runs — never a
                        // second start path racing applyCamera (the
                        // duplicate-start find). The in-progress guard
                        // keeps this handler out of applyCamera's own
                        // application.
                        if (_cameraApplyInProgress) {
                            return;
                        }
                        _appliedVisible = visible;
                        if (source !== "") {
                            if (root.printerModel != null) {
                                root.printerModel.cameraPaneTrace(root.paneId, "visibility lifecycle " + (visible ? "start" : "stop"));
                            }
                            if (visible) {
                                start();
                            } else {
                                stop();
                            }
                        }
                    }

                    onImageWidthChanged: {
                        // T9: the first decoded non-zero frame (the
                        // reviewer's cold-start diagnostics — the last
                        // hop of the T0-T9 chain). The model marks it
                        // against the shared monotonic origin, once.
                        if (root.traceCameraTiming && imageWidth > 0 && root.printerModel != null) {
                            root.printerModel.cameraFirstFrameRendered();
                        }
                    }
                }

                Timer {
                    // The render watchdog (a live
                    // report): a stream that
                    // CONNECTED but never painted a
                    // frame raises no error signal.
                    // While configured and visible, a
                    // frame-less image reports the
                    // stall every 8 s; the model's
                    // recovery reloads the source on
                    // its usual 10 s cadence, and the
                    // check skips once any frame has
                    // painted.
                    id: cameraStallWatchdog
                    interval: 8000
                    repeat: true
                    running: root.configured && cameraImage.visible
                    onTriggered: {
                        if (cameraImage.imageWidth > 0)
                            return;
                        if (root.printerModel != null) {
                            root.printerModel.cameraPaneTrace(root.paneId, "stall watchdog fired (imageWidth=" + cameraImage.imageWidth + ")");
                            root.printerModel.cameraRenderStalled();
                        }
                    }
                }

                Rectangle {
                    // A stale frame must not read as
                    // live: while disconnected a heavy
                    // neutral-grey wash and an explicit
                    // caption cover the camera (a
                    // live ruling; true
                    // per-pixel desaturation needs a
                    // shader Cura's Qt 5.15 line cannot
                    // guarantee — roadmap note). The
                    // webcam watchdog reuses the same
                    // veil while a dead stream restarts.
                    visible: root.configured && (root.printerModel == null || !root.printerModel.monitorConnected || (root.printerModel != null && root.printerModel.cameraRecovering))
                    anchors.fill: cameraImage
                    color: MoonrakerTheme.cameraVeil

                    UM.Label {
                        anchors.centerIn: parent
                        text: (root.printerModel != null && root.printerModel.cameraRecovering) ? "Camera recovering…" : "Camera offline"
                        font: UM.Theme.getFont("medium_bold")
                        color: MoonrakerTheme.consoleMuted
                    }
                }

                Rectangle {
                    // A red recording dot plus "Live"
                    // while the stream is genuinely
                    // live (the request).
                    id: cameraLiveBadge
                    objectName: "cameraLiveBadge"
                    visible: root.configured && root.printerModel != null && root.printerModel.monitorConnected
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: UM.Theme.getSize("narrow_margin").height
                    anchors.leftMargin: UM.Theme.getSize("narrow_margin").height
                    height: 20 * screenScaleFactor
                    width: liveLabel.width + 20 * screenScaleFactor
                    radius: 10 * screenScaleFactor
                    color: MoonrakerTheme.cameraLivePill

                    RowLayout {
                        anchors.centerIn: parent
                        spacing: UM.Theme.getSize("narrow_margin").width
                        Rectangle {
                            width: 8 * screenScaleFactor
                            height: 8 * screenScaleFactor
                            radius: 4 * screenScaleFactor
                            color: MoonrakerTheme.errorRed
                        }
                        UM.Label {
                            id: liveLabel
                            objectName: "cameraLiveBadgeText"
                            text: "Live"
                            color: MoonrakerTheme.cameraLiveText
                            font: UM.Theme.getFont("small")
                        }
                    }
                }

                Rectangle {
                    // The stream chip (the overlay request): the decoded
                    // resolution, the renderer's recent bandwidth and the
                    // rate it is actually decoding at, top right of the
                    // viewport — visible only while a live frame is
                    // showing.
                    id: cameraStreamChip
                    objectName: "cameraStreamChip"
                    // Two pills on a narrow frame read as one smear, so
                    // the chip yields the moment it would touch the Live
                    // badge and returns as soon as the frame is wide
                    // enough to carry both (the frame itself is the
                    // measure — it fits the viewport, so the rule follows
                    // every resize without a timer).
                    readonly property real badgeGap: UM.Theme.getSize("narrow_margin").width
                    // The Live badge's liveness contract: no model or a
                    // disconnected one hides the chip — the stats must
                    // never float over the offline veil. (The capture
                    // census exempts camera-overlay text: its ground is
                    // the feed's arbitrary content.)
                    visible: root.configured && root.printerModel != null && root.printerModel.monitorConnected && cameraImage.visible && cameraImage.imageWidth > 0 && root.cameraPictureWidth >= cameraLiveBadge.width + width + 3 * badgeGap
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.topMargin: UM.Theme.getSize("narrow_margin").height
                    anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                    height: 20 * screenScaleFactor
                    width: streamChipLabel.width + 20 * screenScaleFactor
                    radius: 10 * screenScaleFactor
                    color: MoonrakerTheme.cameraLivePill

                    UM.Label {
                        id: streamChipLabel
                        objectName: "cameraStreamChipText"
                        anchors.centerIn: parent
                        text: cameraImage.imageWidth + "×" + cameraImage.imageHeight + (cameraImage.recentBytesPerSec > 0 ? " · " + (cameraImage.recentBytesPerSec * 8 / 1000 / 1000).toFixed(1) + " Mbps" : "") + (root.fpsReadoutShown ? " · " + root.fpsText(root.fpsReadout) : "")
                        color: MoonrakerTheme.cameraLiveText
                        font: UM.Theme.getFont("small")
                    }
                }

                MouseArea {
                    // The camera view's own gestures (the live request):
                    // the wheel zooms the picture, a left drag pans it,
                    // and the FPS throttle keeps the wheel behind Shift
                    // and the vertical drag on the RIGHT button — the
                    // rate is adjustable from anywhere over the picture,
                    // whether or not the scale is on screen, and the
                    // control only ever shows what the gestures can do.
                    // A left double click is back to the fit.
                    id: cameraGestureArea
                    objectName: "cameraGestureArea"
                    anchors.fill: parent
                    enabled: root.cameraControlLive
                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                    cursorShape: _dragButton === Qt.RightButton && pressed ? Qt.SizeVerCursor : root.cameraZoom > 1.0 ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor) : Qt.ArrowCursor
                    property real _lastX: 0
                    property real _lastY: 0
                    // The button the live drag belongs to: a move event
                    // carries no button of its own, so the press decides
                    // whether this drag pans or drives the rate.
                    property int _dragButton: Qt.NoButton
                    // The rate drag's own wheel: one step of the same
                    // linear ladder the wheel takes, per this much
                    // travel, so the two gestures move the rate alike
                    // whatever the pane measures.
                    readonly property real _fpsNotchPixels: 12 * screenScaleFactor
                    property real _fpsTravel: 0
                    // The rate this drag has itself reached: see
                    // root.stepFps — the published rate lags the drag
                    // inside a turn, so the drag carries its own.
                    property real _fpsDragRate: 0
                    onWheel: function (wheel) {
                        var notch = root.wheelNotch(wheel);
                        if (notch === 0) {
                            return;
                        }
                        if (wheel.modifiers & Qt.ShiftModifier) {
                            root.nudgeFps(notch);
                        } else {
                            root.zoomCamera(notch > 0 ? root.cameraZoomStep : 1 / root.cameraZoomStep, wheel.x, wheel.y);
                        }
                    }
                    onPressed: function (mouse) {
                        _lastX = mouse.x;
                        _lastY = mouse.y;
                        _dragButton = mouse.button;
                        _fpsTravel = 0;
                        _fpsDragRate = root.cameraFps;
                        if (mouse.button === Qt.RightButton) {
                            // The rate drag is a grab like the scale's
                            // handle: a hand on the control holds its
                            // park off (see cameraBarHandlePressed).
                            root.cameraBarHandlePressed(cameraGestureArea);
                        }
                    }
                    onPositionChanged: function (mouse) {
                        if (!pressed) {
                            return;
                        }
                        if (_dragButton === Qt.RightButton) {
                            // Dragging UP raises the rate. The step is
                            // measured from the LAST position — a move
                            // carries an absolute point, so leaving the
                            // seed behind would count each step again
                            // in every step after it.
                            _fpsTravel += _lastY - mouse.y;
                            _lastX = mouse.x;
                            _lastY = mouse.y;
                            while (_fpsTravel >= _fpsNotchPixels) {
                                _fpsTravel -= _fpsNotchPixels;
                                var raised = root.stepFps(_fpsDragRate, 1);
                                if (raised <= _fpsDragRate) {
                                    // The ceiling: the travel spent
                                    // against it is dropped, never
                                    // banked (the live report).
                                    _fpsTravel = 0;
                                    break;
                                }
                                _fpsDragRate = raised;
                                root.setFps(raised);
                            }
                            while (_fpsTravel <= -_fpsNotchPixels) {
                                _fpsTravel += _fpsNotchPixels;
                                var lowered = root.stepFps(_fpsDragRate, -1);
                                if (lowered >= _fpsDragRate) {
                                    _fpsTravel = 0;  // the floor, likewise
                                    break;
                                }
                                _fpsDragRate = lowered;
                                root.setFps(lowered);
                            }
                            return;
                        }
                        root.panCamera(mouse.x - _lastX, mouse.y - _lastY);
                        _lastX = mouse.x;
                        _lastY = mouse.y;
                    }
                    onReleased: function (mouse) {
                        if (mouse.button === Qt.RightButton) {
                            root.cameraBarHandleReleased();
                        }
                        _dragButton = Qt.NoButton;
                    }
                    onCanceled: {
                        if (_dragButton === Qt.RightButton) {
                            root.cameraBarHandleReleased();
                        }
                        _dragButton = Qt.NoButton;
                    }
                    onDoubleClicked: function (mouse) {
                        if (mouse.button === Qt.LeftButton) {
                            root.resetCameraView();
                        }
                    }
                }

                Rectangle {
                    // The control bar (the live and idle-load requests):
                    // ONE box, TWO faces — the zoom scale and the rate
                    // scale — each with its own reserved readout strip and
                    // its own graduated track. Only one is ever up, and a
                    // mode change sends the bar out on the face that was
                    // showing and back in on the other. It rides the CAMERA
                    // VIEW (the decoded frame, like the Live badge): docked
                    // to the picture's own right edge and, parked, past the
                    // picture's own edge — the frame container clips it
                    // there, never at the pane's edge (the live request).
                    // Views below the bar's floor cannot carry the
                    // graduations, and the chip below stands in.
                    id: cameraBar
                    objectName: "cameraBar"
                    visible: root.cameraBarFits && root.cameraControlLive
                    width: root.cameraBarWidth
                    height: root.cameraBarHeight
                    anchors.verticalCenter: parent.verticalCenter
                    // x follows the picture's edge hard and only the slide
                    // is animated, so a resize lands the docked bar on the
                    // new edge in that frame (the live report).
                    x: parent.width - width - UM.Theme.getSize("narrow_margin").width + slide
                    property real slide: root.cameraBarShown ? 0 : root.cameraBarParkSlide(width)
                    Behavior on slide {
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
                        // The ZOOM face (the live request): a reserved
                        // percentage strip and, below it, the plate zoom
                        // scope's own graduated bar — whole lines at every
                        // 100%, edge pairs at the halves and quarters, log
                        // spaced so the low zooms keep their room. The
                        // marker rides the zoom and doubles as the drag
                        // handle; the bottom of the track is the 100% fit.
                        id: cameraZoomFace
                        objectName: "cameraZoomScale"
                        anchors.fill: parent
                        visible: root.cameraBarMode === "zoom"

                        Item {
                            id: zoomReadout
                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.right: parent.right
                            height: 16 * screenScaleFactor
                            UM.Label {
                                id: cameraZoomReadoutLabel
                                objectName: "cameraZoomReadout"
                                anchors.centerIn: parent
                                text: Math.round(root.cameraZoom * 100) + "%"
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text")
                            }
                        }

                        Item {
                            id: zoomBar
                            objectName: "cameraZoomBar"
                            anchors.top: zoomReadout.bottom
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 4 * screenScaleFactor

                            Repeater {
                                model: root.cameraZoomGraduations
                                Item {
                                    readonly property real fraction: root.cameraZoomFraction(modelData)
                                    readonly property int percent: Math.round(modelData * 100)
                                    readonly property bool major: percent % 100 === 0
                                    readonly property bool half: percent % 50 === 0
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    y: parent.height - fraction * parent.height
                                    height: major ? 2 : 1
                                    Rectangle {
                                        // The left edge's reach: the full
                                        // width at the majors, half way in
                                        // at the halves, a quarter at the
                                        // quarters (the plate scope's own
                                        // pair).
                                        anchors.left: parent.left
                                        width: parent.width * (major ? 1.0 : half ? 0.5 : 0.25)
                                        height: parent.height
                                        color: major ? UM.Theme.getColor("border") : UM.Theme.getColor("lining")
                                    }
                                    Rectangle {
                                        visible: !major
                                        anchors.right: parent.right
                                        width: parent.width * (half ? 0.5 : 0.25)
                                        height: parent.height
                                        color: UM.Theme.getColor("lining")
                                    }
                                }
                            }

                            Rectangle {
                                // The marker: the zoom itself, and the
                                // handle.
                                id: zoomMarker
                                objectName: "cameraZoomMarker"
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: parent.height - root.cameraZoomFraction(root.cameraZoom) * parent.height - height / 2
                                width: parent.width
                                height: 3 * screenScaleFactor
                                radius: height / 2
                                color: UM.Theme.getColor("primary")
                            }

                            MouseArea {
                                // The draggable scale, the plate zoom bar's
                                // contract: the pointer's track position is
                                // the zoom, the bottom the 100% fit.
                                id: zoomScaleHandle
                                anchors.fill: parent
                                onPressed: function (mouse) {
                                    root.cameraBarHandlePressed(zoomScaleHandle);
                                    zoomApply(mouse.y);
                                }
                                onReleased: root.cameraBarHandleReleased()
                                onCanceled: root.cameraBarHandleReleased()
                                onPositionChanged: function (mouse) {
                                    if (pressed) {
                                        zoomApply(mouse.y);
                                    }
                                }
                                onWheel: function (wheel) {
                                    // The rate is still a Shift notch away
                                    // from here: the scale under the pointer
                                    // is the one a plain wheel drives, but
                                    // the throttle's own gesture reaches
                                    // this box too.
                                    if (wheel.modifiers & Qt.ShiftModifier) {
                                        root.nudgeFps(root.wheelNotch(wheel));
                                    } else {
                                        var notch = root.wheelNotch(wheel);
                                        if (notch !== 0) {
                                            root.setCameraZoom(root.cameraZoom * (notch > 0 ? root.cameraZoomStep : 1 / root.cameraZoomStep));
                                        }
                                    }
                                }
                                function zoomApply(y) {
                                    var fraction = Math.min(1.0, Math.max(0.0, (parent.height - y) / parent.height));
                                    root.setCameraZoom(root.cameraZoomFromFraction(fraction));
                                }
                            }
                        }
                    }

                    Item {
                        // The RATE face (the idle-load request): the same
                        // reserved strip and track, carrying the decode
                        // throttle — the camera's ceiling at the top, the
                        // 0.5 FPS floor at the bottom, EVENLY spaced, since
                        // a rate is read as a rate.
                        id: cameraFpsFace
                        objectName: "cameraFpsScale"
                        anchors.fill: parent
                        visible: root.cameraBarMode === "fps"

                        Item {
                            // The reserved readout: the rate the stream is
                            // decoding at, never sharing space with the bar.
                            id: fpsReadout
                            anchors.top: parent.top
                            anchors.left: parent.left
                            anchors.right: parent.right
                            height: 16 * screenScaleFactor
                            UM.Label {
                                id: cameraFpsReadoutLabel
                                objectName: "cameraFpsReadout"
                                anchors.centerIn: parent
                                text: root.fpsText(root.cameraFps)
                                font: UM.Theme.getFont("small")
                                color: UM.Theme.getColor("text")
                            }
                        }

                        Item {
                            // The scale: the camera's ceiling at the top, the
                            // 0.5 FPS floor at the bottom.
                            id: fpsBar
                            objectName: "cameraFpsBar"
                            anchors.top: fpsReadout.bottom
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 4 * screenScaleFactor

                            Repeater {
                                model: root.fpsGraduations
                                Item {
                                    readonly property real fraction: root.fpsFraction(modelData.value)
                                    readonly property bool major: modelData.major
                                    // A line spans the bar; a double tick
                                    // reaches a quarter in from each side,
                                    // the plate zoom scope's own pair.
                                    readonly property bool line: modelData.line
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    y: parent.height - fraction * parent.height
                                    height: major ? 2 : 1
                                    Rectangle {
                                        anchors.left: parent.left
                                        width: parent.width * (line ? 1.0 : 0.25)
                                        height: parent.height
                                        color: major ? UM.Theme.getColor("border") : UM.Theme.getColor("lining")
                                    }
                                    Rectangle {
                                        visible: !line
                                        anchors.right: parent.right
                                        width: parent.width * 0.25
                                        height: parent.height
                                        color: UM.Theme.getColor("lining")
                                    }
                                }
                            }

                            Rectangle {
                                // The marker: the rate itself, and the handle.
                                id: fpsMarker
                                objectName: "cameraFpsMarker"
                                anchors.horizontalCenter: parent.horizontalCenter
                                y: parent.height - root.fpsFraction(root.cameraFps) * parent.height - height / 2
                                width: parent.width
                                height: 3 * screenScaleFactor
                                radius: height / 2
                                color: UM.Theme.getColor("primary")
                            }

                            MouseArea {
                                // The draggable scale, the plate zoom bar's own
                                // contract: the pointer's track position is the
                                // rate, the top is the camera's ceiling.
                                id: fpsScaleHandle
                                anchors.fill: parent
                                onPressed: function (mouse) {
                                    root.cameraBarHandlePressed(fpsScaleHandle);
                                    fpsApply(mouse.y);
                                }
                                onReleased: root.cameraBarHandleReleased()
                                onCanceled: root.cameraBarHandleReleased()
                                onPositionChanged: function (mouse) {
                                    if (pressed) {
                                        fpsApply(mouse.y);
                                    }
                                }
                                onWheel: function (wheel) {
                                    root.nudgeFps(root.wheelNotch(wheel));
                                }
                                function fpsApply(y) {
                                    var fraction = Math.min(1.0, Math.max(0.0, (parent.height - y) / parent.height));
                                    root.setFps(root.fpsFromFraction(fraction));
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    // The bar's compact stand-in (the idle-load and zoom
                    // requests): where the camera view has no room for the
                    // bar, the face it would be showing rides a chip at the
                    // picture's own vertical centre, on the bar's own dock
                    // and park rhythm.
                    id: cameraBarChip
                    objectName: "cameraBarChip"
                    visible: !root.cameraBarFits && root.cameraControlLive && root.cameraPictureWidth >= 96 * screenScaleFactor && root.cameraPictureHeight >= 96 * screenScaleFactor
                    width: chipLabel.width + 20 * screenScaleFactor
                    height: 20 * screenScaleFactor
                    radius: 10 * screenScaleFactor
                    color: MoonrakerTheme.cameraLivePill
                    anchors.verticalCenter: parent.verticalCenter
                    x: parent.width - width - UM.Theme.getSize("narrow_margin").width + slide
                    property real slide: root.cameraBarShown ? 0 : root.cameraBarParkSlide(width)
                    Behavior on slide {
                        NumberAnimation {
                            duration: 180
                            easing.type: Easing.OutCubic
                        }
                    }

                    UM.Label {
                        id: chipLabel
                        objectName: "cameraBarChipText"
                        anchors.centerIn: parent
                        text: root.cameraBarMode === "zoom" ? Math.round(root.cameraZoom * 100) + "%" : root.fpsText(root.cameraFps)
                        color: MoonrakerTheme.cameraLiveText
                        font: UM.Theme.getFont("small")
                    }
                }
            }
        }
    }
}
