import QtQuick 2.15
import UM 1.5 as UM
import "../../resources/theme"

// The camera's control bar: ONE box, TWO faces — the zoom scale the
// picture's own wheel drives, the rate scale the throttle's gesture
// drives — and the dock/park rhythm the two share.
//
// The bar owns that rhythm and the rate's own ladder (the camera's
// range, the linear step, the touchpad accumulation), because both are
// the faces' own behaviour: nothing above tells the bar when to slide,
// and the picture asks for a zoom through a request rather than by
// moving the bar itself. Its box is the picture's to size — the frame
// hands down what the picture can carry.
Rectangle {
    id: root
    objectName: "cameraBar"

    // The printer model the card passes: the ladder reads the camera's
    // own rate range from it and commits through it.
    property var printerModel: null

    // The picture's own numbers, handed down: the bar cannot measure
    // the frame it is clipped to from here.
    property bool cameraBarFits: false
    property bool cameraControlLive: false
    property real cameraBarBaseHeight: 180 * screenScaleFactor
    property real cameraBarChipBand: 0
    property real cameraPictureHeight: 0

    // The zoom the picture is holding, its track position and the
    // scale's marks: the curve is the viewport's, the track here.
    property real cameraZoom: 1.0
    property real cameraZoomFraction: 0
    property var cameraZoomMarks: []

    // The card's model-facing rate readouts.
    property real cameraFps: 0
    property real cameraFpsMin: 0.5
    property real cameraFpsMax: 30
    property real cameraSnapshotMaxFps: 5
    property bool cameraSnapshotAvailable: false

    // A gesture on the zoom scale is a REQUEST: the bar holds no zoom,
    // so it reports the track position (or the wheel) the pointer gave
    // it and the picture decides what that means.
    signal zoomFractionRequested(real fraction)
    signal zoomWheelRequested(var wheel)

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

    onCameraFpsChanged: {
        if (root._fpsLastSeen > 0 && root.cameraFps !== root._fpsLastSeen) {
            root.dock("fps");
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
    function grabHandle(handle) {
        root.cameraBarHandle = handle;
        cameraBarParkTimer.stop();
    }

    function releaseHandle() {
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
                root.dock("zoom");
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

    function dock(mode) {
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

    // The park the picture's own reset asks for: the fit leaves the bar
    // nothing to hold, and a camera switch must not leave a stale grab
    // behind — the park reads it, and a stale one would keep the bar up
    // for good.
    function park() {
        root.cameraBarHandle = null;
        cameraBarSwapTimer.stop();
        root._cameraBarTurning = false;
        root.cameraBarDocked = false;
        cameraBarParkTimer.stop();
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
        root.dock("fps");
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

    property real _fpsTouchpadRaw: 0
    Timer {
        id: fpsTouchpadGesture
        interval: 350
        repeat: false
    }

    function nudgeFpsWheel(wheel) {
        // The same 180-pixel travel is one FPS wheel step. Keep the
        // unrounded target through a gesture: rounding every tiny
        // event independently would amplify or lose its movement.
        var pixels = wheel.pixelDelta.y !== 0 ? wheel.pixelDelta.y : wheel.pixelDelta.x;
        if (pixels !== 0) {
            if (!fpsTouchpadGesture.running) {
                root._fpsTouchpadRaw = root.cameraFps;
            }
            root._fpsTouchpadRaw = Math.min(root.cameraFpsMax, Math.max(root.cameraFpsMin, root._fpsTouchpadRaw + root.fpsStep * pixels / 180.0));
            fpsTouchpadGesture.restart();
            root.setFps(root._fpsTouchpadRaw);
            return;
        }
        fpsTouchpadGesture.stop();
        root.nudgeFps(root.wheelNotch(wheel));
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
    visible: root.cameraBarFits && root.cameraControlLive
    width: root.cameraBarWidth
    height: root.cameraBarHeight
    // The bar rides the picture's frame; created on its own by the
    // engine gate it has no parent to measure, so both rides fall
    // back rather than throw (the browser popup's own guard).
    anchors.verticalCenter: parent !== null ? parent.verticalCenter : undefined
    // x follows the picture's edge hard and only the slide
    // is animated, so a resize lands the docked bar on the
    // new edge in that frame (the live report).
    x: parent !== null ? parent.width - width - UM.Theme.getSize("narrow_margin").width + slide : 0
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
                model: root.cameraZoomMarks
                Item {
                    readonly property real fraction: modelData.fraction
                    readonly property bool major: modelData.major
                    readonly property bool half: modelData.half
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
                y: parent.height - root.cameraZoomFraction * parent.height - height / 2
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
                    root.grabHandle(zoomScaleHandle);
                    zoomApply(mouse.y);
                }
                onReleased: root.releaseHandle()
                onCanceled: root.releaseHandle()
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
                        root.nudgeFpsWheel(wheel);
                    } else {
                        root.zoomWheelRequested(wheel);
                    }
                }
                function zoomApply(y) {
                    var fraction = Math.min(1.0, Math.max(0.0, (parent.height - y) / parent.height));
                    root.zoomFractionRequested(fraction);
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

            Rectangle {
                objectName: "cameraSnapshotRegion"
                visible: root.cameraSnapshotAvailable
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: parent.height * root.fpsFraction(root.cameraSnapshotMaxFps)
                color: MoonrakerTheme.successGreen
                opacity: 0.75
            }

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
                    root.grabHandle(fpsScaleHandle);
                    fpsApply(mouse.y);
                }
                onReleased: root.releaseHandle()
                onCanceled: root.releaseHandle()
                onPositionChanged: function (mouse) {
                    if (pressed) {
                        fpsApply(mouse.y);
                    }
                }
                onWheel: function (wheel) {
                    root.nudgeFpsWheel(wheel);
                }
                function fpsApply(y) {
                    var fraction = Math.min(1.0, Math.max(0.0, (parent.height - y) / parent.height));
                    root.setFps(root.fpsFromFraction(fraction));
                }
            }
        }
    }
}
