import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import MoonrakerPrintFollower 1.0
import "../../resources/theme"

// The camera picture and the view it is presented through: the frame
// every overlay is clipped to, the renderer image with its aspect fit,
// its rotation and its mirror pair, the zoom/pan transform and the
// gestures that drive it, the overlays (the veil, the Live badge, the
// decode chip, the compact control stand-in) and the stall watchdog.
//
// The view transform belongs HERE and nowhere else: the picture's own
// dimensions are its limits, the frame is its box, and the control bar
// it is clipped with lives inside it. The card above passes the camera
// and the rate it publishes; nothing above holds a second copy of the
// view. The host drives the stream through applyCamera.
Item {
    id: root
    objectName: "cameraViewport"

    // The printer model the card passes: every model read inside the
    // viewport goes through this property.
    property var printerModel: null

    // The card's configured flag: a webcam URL is present.
    property bool configured: false

    // The card's model-facing rate readouts, handed down: the chip
    // reports the decode rate and the rate gestures step from it.
    property real cameraFps: 0
    property real cameraFpsMin: 0.5
    property real cameraFpsMax: 30
    property real cameraSnapshotMaxFps: 5
    property bool cameraSnapshotAvailable: false

    // The T0-T9 timing chain's gate and the pane's diagnostic id: the
    // apply, watchdog and first-frame traces report under both.
    property bool traceCameraTiming: false
    property int paneId: -1
    readonly property string signalState: root.printerModel != null ? root.printerModel.detectionState : "idle"
    readonly property bool signalLive: (root.signalState === "normal" || root.signalState === "warning" || root.signalState === "failure") && root.cameraControlLive
    readonly property bool signalWaiting: root.signalState === "waiting" && root.cameraControlLive
    readonly property bool signalShown: root.signalLive || root.signalWaiting
    readonly property bool signalCompact: !root.cameraBarFits
    readonly property int signalScore: root.printerModel != null ? root.printerModel.detectionScore : -1
    readonly property string signalName: root.printerModel != null ? root.printerModel.detectionStatus : "Unavailable"
    readonly property color signalColor: root.signalWaiting ? UM.Theme.getColor("text_inactive") : root.signalState === "failure" ? MoonrakerTheme.dangerRed : root.signalState === "warning" ? MoonrakerTheme.warningOrange : MoonrakerTheme.successGreen

    // The card's own Layout slot: the viewport fills it, so the pane's
    // column equilibrium is unchanged.
    Layout.fillWidth: true
    Layout.fillHeight: true
    Layout.margins: UM.Theme.getSize("default_margin").width

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
    property bool _appliedSnapshotMode: false
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
        var snapshotMode = root.printerModel != null && root.printerModel.cameraSnapshotMode;
        // A snapshot URL names a finite request; its query is part of
        // the endpoint (often ?action=snapshot). Stream queries still
        // use the stable key so rotating tokens do not reconnect MJPEG.
        var desiredKey = snapshotMode ? text : _stateKey(text);
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
        if (desiredKey === _appliedUrl && visible === _appliedVisible && snapshotMode === _appliedSnapshotMode) {
            if (root.printerModel != null) {
                root.printerModel.cameraPaneTrace(paneId, "applyCamera no-op: state unchanged");
            }
            return;
        }
        _cameraApplyInProgress = true;
        try {
            cameraImage.stop();
            cameraImage.snapshotMode = snapshotMode;
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
        _appliedUrl = desiredKey;
        _appliedVisible = visible;
        _appliedSnapshotMode = snapshotMode;
        if (shouldRun) {
            if (root.printerModel != null) {
                root.printerModel.cameraPaneTrace(paneId, "applyCamera starting the stream");
            }
            cameraImage.start();
        }
    }

    // The graduations' own baseline: the height the bar is worth showing
    // at, never under 90 either. The fallback rule is measured against
    // THIS, not against the bar's live height, so a taller pane grows the
    // bar without moving where the chip takes over.
    readonly property real cameraBarBaseHeight: 180 * screenScaleFactor
    // What the top-right chip's band costs the picture's height: the bar
    // sits centred, so that band has to clear above and below it.
    readonly property real cameraBarChipBand: 2 * (UM.Theme.getSize("narrow_margin").height + cameraStreamChip.height + UM.Theme.getSize("narrow_margin").height)
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
    // ── the zoom face's own scale ───────────────────────────────────
    // Every 25% from the fit to the 800% ceiling, LOG-positioned: the
    // fine granularity belongs where the low zooms are, which is the
    // spacing the rate scale deliberately does not borrow. The marker
    // rides the same curve and doubles as the drag handle; the bottom of
    // the bar is the 100% fit.

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

    // The zoom scale's marks: the viewport owns the curve and the bar
    // draws the track, so each mark carries its own position on it.
    readonly property var cameraZoomMarks: {
        var marks = [];
        for (var value = 1.0; value <= root.cameraZoomMax + 1e-9; value += 0.25) {
            var percent = Math.round(value * 100);
            marks.push({
                "value": value,
                "fraction": root.cameraZoomFraction(value),
                "major": percent % 100 === 0,
                "half": percent % 50 === 0
            });
        }
        return marks;
    }
    // ── the camera view's zoom and pan (the live request) ───────────
    // The wheel zooms the picture about the pointer and a left drag pans
    // it; the FPS throttle keeps the wheel only while Shift is held and
    // the vertical drag on the right button, so the plain gestures are
    // the ones a picture is expected to answer. The view rides a
    // transform, so the frame box — and with it every overlay, the veil
    // and the FPS control — stays put while the picture moves under it.
    property real cameraZoom: 1.0
    // Match the follower's retargetable ease-out. Only the image's
    // transform moves; decoded frames and the control overlays stay put.
    property real cameraDisplayZoom: 1.0
    property real cameraDisplayPanX: 0
    property real cameraDisplayPanY: 0
    readonly property real cameraDisplayOffsetX: Math.max(-cameraImage.width * (cameraDisplayZoom - 1) / 2, Math.min(cameraImage.width * (cameraDisplayZoom - 1) / 2, cameraDisplayPanX))
    readonly property real cameraDisplayOffsetY: Math.max(-cameraImage.height * (cameraDisplayZoom - 1) / 2, Math.min(cameraImage.height * (cameraDisplayZoom - 1) / 2, cameraDisplayPanY))

    Timer {
        id: cameraZoomAnimator
        interval: 16
        repeat: true
        onTriggered: {
            var next = root.cameraDisplayZoom + (root.cameraZoom - root.cameraDisplayZoom) * 0.30;
            var settled = Math.abs(root.cameraZoom - next) < 0.005;
            root.cameraDisplayZoom = settled ? root.cameraZoom : next;
            root.cameraDisplayPanX = settled ? root.cameraPanOffsetX : root.cameraDisplayPanX + (root.cameraPanOffsetX - root.cameraDisplayPanX) * 0.30;
            root.cameraDisplayPanY = settled ? root.cameraPanOffsetY : root.cameraDisplayPanY + (root.cameraPanOffsetY - root.cameraDisplayPanY) * 0.30;
            if (settled) {
                stop();
            }
        }
    }
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

    function wheelNotch(wheel) {
        // The notch is whichever axis the wheel reported: a shifted
        // wheel arrives on the HORIZONTAL axis on some systems, and the
        // gesture must not die with it (the live report: Shift did
        // nothing at all). The bar's rate wheel reads the same rule over
        // its own events.
        return wheel.angleDelta.y !== 0 ? wheel.angleDelta.y : wheel.angleDelta.x;
    }

    function wheelZoomFactor(wheel) {
        // Match Print Follower: 180 touchpad pixels equal one 25%
        // mouse-wheel step. Tiny pixel deltas stay continuous.
        var pixels = wheel.pixelDelta.y;
        if (pixels !== 0) {
            return Math.pow(root.cameraZoomStep, pixels / 180.0);
        }
        var notch = root.wheelNotch(wheel);
        return notch > 0 ? root.cameraZoomStep : notch < 0 ? 1 / root.cameraZoomStep : 1;
    }

    function zoomCamera(step, x, y) {
        // The dock comes first: a zoom clamped at the ceiling or at the
        // fit still deserves the feedback that the input landed.
        controlBar.dock("zoom");
        // The scale is anchored at the frame centre, so the point under
        // the pointer would slide out from under it; the pan is
        // re-solved to hold it (the plate scope's no-bed-origin ruling).
        var target = Math.min(root.cameraZoomMax, Math.max(1.0, root.cameraZoom * step));
        if (target === root.cameraZoom) {
            return;
        }
        var ratio = target / root.cameraDisplayZoom;
        root.cameraPanX = root.cameraDisplayOffsetX + (x - cameraImage.width / 2 - root.cameraDisplayOffsetX) * (1 - ratio);
        root.cameraPanY = root.cameraDisplayOffsetY + (y - cameraImage.height / 2 - root.cameraDisplayOffsetY) * (1 - ratio);
        root.cameraZoom = target;
        if (target <= 1.0) {
            root.cameraPanX = 0;
            root.cameraPanY = 0;
        }
        root.clampCameraPan();
        cameraZoomAnimator.restart();
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
        cameraZoomAnimator.restart();
    }

    function panCamera(dx, dy) {
        // The picture follows the pointer exactly, up to the limit: the
        // clamp lands on the STORED pan, so the next drag in the
        // opposite direction moves the picture at once.
        var oldX = root.cameraPanX;
        var oldY = root.cameraPanY;
        root.cameraPanX += dx;
        root.cameraPanY += dy;
        root.clampCameraPan();
        root.cameraDisplayPanX += root.cameraPanX - oldX;
        root.cameraDisplayPanY += root.cameraPanY - oldY;
    }

    function resetCameraView() {
        // The fit and the centre, back in one gesture (the double
        // click). The FPS rate is NOT a view state and is left alone.
        root.cameraZoom = 1.0;
        root.cameraPanX = 0;
        root.cameraPanY = 0;
        cameraZoomAnimator.stop();
        root.cameraDisplayZoom = 1.0;
        root.cameraDisplayPanX = 0;
        root.cameraDisplayPanY = 0;
        // The fit leaves the zoom face nothing to hint at, so the bar
        // goes at once rather than waiting out the idle five seconds —
        // a camera switch parks it through this too (the live request).
        controlBar.park();
    }

    UM.Label {
        anchors.centerIn: parent
        width: Math.max(0, parent.width - 2 * UM.Theme.getSize("default_margin").width)
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
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
        width: Math.max(0, parent.width - 2 * UM.Theme.getSize("default_margin").width)
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
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
        objectName: "cameraStreamDisabledNotice"
        anchors.centerIn: parent
        width: Math.max(0, parent.width - 2 * UM.Theme.getSize("default_margin").width)
        wrapMode: Text.WordWrap
        horizontalAlignment: Text.AlignHCenter
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
            // Above 5 FPS this caps MJPEG decoding. At 5 FPS
            // or below a configured snapshot URL is polled
            // instead, closing the continuous stream.
            targetFps: root.printerModel != null ? root.printerModel.cameraFps : 0
            detectionReceiver: root.printerModel
            anchors.centerIn: parent

            property bool imageRotated: rotation === 90 || rotation === 270
            property real maxViewWidth: root.width
            property real maxViewHeight: root.height
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
                    xScale: (root.printerModel != null && root.printerModel.cameraFlipHorizontal ? -1 : 1) * root.cameraDisplayZoom
                    yScale: (root.printerModel != null && root.printerModel.cameraFlipVertical ? -1 : 1) * root.cameraDisplayZoom
                },
                Translate {
                    x: root.cameraDisplayOffsetX
                    y: root.cameraDisplayOffsetY
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
                text: cameraImage.imageWidth + "×" + cameraImage.imageHeight + (cameraImage.recentBytesPerSec > 0 ? " · " + (cameraImage.recentBytesPerSec * 8 / 1000 / 1000).toFixed(1) + " Mbps" : "") + (root.fpsReadoutShown ? " · " + controlBar.fpsText(root.fpsReadout) : "")
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
            // The rate this drag has itself reached: see the
            // bar's stepFps — the published rate lags the drag
            // inside a turn, so the drag carries its own.
            property real _fpsDragRate: 0
            onWheel: function (wheel) {
                if (wheel.modifiers & Qt.ShiftModifier) {
                    controlBar.nudgeFpsWheel(wheel);
                } else {
                    var factor = root.wheelZoomFactor(wheel);
                    if (factor !== 1) {
                        root.zoomCamera(factor, wheel.x, wheel.y);
                    }
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
                    // park off (see controlBar.grabHandle).
                    controlBar.grabHandle(cameraGestureArea);
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
                        var raised = controlBar.stepFps(_fpsDragRate, 1);
                        if (raised <= _fpsDragRate) {
                            // The ceiling: the travel spent
                            // against it is dropped, never
                            // banked (the live report).
                            _fpsTravel = 0;
                            break;
                        }
                        _fpsDragRate = raised;
                        controlBar.setFps(raised);
                    }
                    while (_fpsTravel <= -_fpsNotchPixels) {
                        _fpsTravel += _fpsNotchPixels;
                        var lowered = controlBar.stepFps(_fpsDragRate, -1);
                        if (lowered >= _fpsDragRate) {
                            _fpsTravel = 0;  // the floor, likewise
                            break;
                        }
                        _fpsDragRate = lowered;
                        controlBar.setFps(lowered);
                    }
                    return;
                }
                root.panCamera(mouse.x - _lastX, mouse.y - _lastY);
                _lastX = mouse.x;
                _lastY = mouse.y;
            }
            onReleased: function (mouse) {
                if (mouse.button === Qt.RightButton) {
                    controlBar.releaseHandle();
                }
                _dragButton = Qt.NoButton;
            }
            onCanceled: {
                if (_dragButton === Qt.RightButton) {
                    controlBar.releaseHandle();
                }
                _dragButton = Qt.NoButton;
            }
            onDoubleClicked: function (mouse) {
                if (mouse.button === Qt.LeftButton) {
                    root.resetCameraView();
                }
            }
        }

        // The bar rides the picture's own clip, so it lives inside
        // the frame it is held to; the chip below stands in where
        // the picture cannot carry it.
        CameraControlBar {
            id: controlBar
            printerModel: root.printerModel
            cameraBarFits: root.cameraBarFits
            cameraControlLive: root.cameraControlLive
            cameraBarBaseHeight: root.cameraBarBaseHeight
            cameraBarChipBand: root.cameraBarChipBand
            cameraPictureHeight: root.cameraPictureHeight
            cameraZoom: root.cameraZoom
            cameraZoomFraction: root.cameraZoomFraction(root.cameraZoom)
            cameraZoomMarks: root.cameraZoomMarks
            cameraFps: root.cameraFps
            cameraFpsMin: root.cameraFpsMin
            cameraFpsMax: root.cameraFpsMax
            cameraSnapshotMaxFps: root.cameraSnapshotMaxFps
            cameraSnapshotAvailable: root.cameraSnapshotAvailable
            onZoomFractionRequested: function (fraction) {
                root.setCameraZoom(root.cameraZoomFromFraction(fraction));
            }
            onZoomWheelRequested: function (wheel) {
                var factor = root.wheelZoomFactor(wheel);
                if (factor !== 1) {
                    root.setCameraZoom(root.cameraZoom * factor);
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
            property real slide: controlBar.cameraBarShown ? 0 : controlBar.cameraBarParkSlide(width)
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
                text: controlBar.cameraBarMode === "zoom" ? Math.round(root.cameraZoom * 100) + "%" : controlBar.fpsText(root.cameraFps)
                color: MoonrakerTheme.cameraLiveText
                font: UM.Theme.getFont("small")
            }
        }
        FailureSignalBar {
            visible: root.signalShown && !root.signalCompact
            score: root.signalScore
            signalColor: root.signalColor
            waiting: root.signalWaiting
            anchors.left: parent.left
            anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
            anchors.verticalCenter: parent.verticalCenter
            width: controlBar.cameraBarWidth
            height: controlBar.cameraBarHeight
        }
        Rectangle {
            objectName: "failureSignalPill"
            visible: root.signalShown && root.signalCompact
            width: signalPillLabel.width + 20 * screenScaleFactor
            height: 20 * screenScaleFactor
            radius: 10 * screenScaleFactor
            color: MoonrakerTheme.cameraLivePill
            anchors.left: parent.left
            anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
            anchors.verticalCenter: parent.verticalCenter
            UM.Label {
                id: signalPillLabel
                objectName: "failureSignalPillText"
                anchors.centerIn: parent
                text: root.signalWaiting ? "Wait" : (root.signalScore / 100).toFixed(2) + " · " + root.signalName
                color: root.signalColor
                font: UM.Theme.getFont("small")
            }
        }
        Rectangle {
            objectName: "failureSignalFrame"
            visible: root.signalShown
            anchors.fill: parent
            color: "transparent"
            border.width: 3 * screenScaleFactor
            border.color: root.signalColor
        }
    }
}
