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
    // model's explicit reload) still restarts.
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

    function _stateKey(text) {
        // The stream identity: the query-stripped URL plus the
        // plugin's own reload marker. Upstream query rotation is
        // noise — the bridge's loopback URL is keyless (the key rides
        // the bridge's own upstream fetch) — while the marker is the
        // model's explicit reload request (manual refresh, watchdog
        // recovery, reconnect) and must still restart the stream.
        var cut = text.indexOf("?");
        var key = cut >= 0 ? text.slice(0, cut) : text;
        if (cut >= 0) {
            var query = text.slice(cut + 1);
            var at = query.indexOf("mpf_reload=");
            if (at >= 0) {
                var value = query.slice(at + "mpf_reload=".length);
                var amp = value.indexOf("&");
                if (amp >= 0) {
                    value = value.slice(0, amp);
                }
                key += "?mpf_reload=" + value;
            }
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
                            enabled: visible
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
                            enabled: root.printerModel != null && root.printerModel.monitorConnected
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
                visible: !root.configured && root.printerModel != null && root.printerModel.webcamNames.length > 0
                text: "Camera offline — reconnecting to Moonraker…"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
            }

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

                transform: Scale {
                    origin.x: cameraImage.width / 2
                    origin.y: cameraImage.height / 2
                    xScale: root.printerModel != null && root.printerModel.cameraFlipHorizontal ? -1 : 1
                    yScale: root.printerModel != null && root.printerModel.cameraFlipVertical ? -1 : 1
                }

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
                visible: root.configured && root.printerModel != null && root.printerModel.monitorConnected
                anchors.top: cameraImage.top
                anchors.left: cameraImage.left
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
                // resolution and the renderer's recent bandwidth, top
                // right of the viewport — visible only while a live
                // frame is showing.
                id: cameraStreamChip
                objectName: "cameraStreamChip"
                // The Live badge's exact liveness contract: no model
                // or a disconnected one hides the chip — the stats
                // must never float over the offline veil. (The
                // capture census exempts camera-overlay text: its
                // ground is the feed's arbitrary content.)
                visible: root.configured && root.printerModel != null && root.printerModel.monitorConnected && cameraImage.visible && cameraImage.imageWidth > 0
                anchors.top: cameraImage.top
                anchors.right: cameraImage.right
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
                    text: cameraImage.imageWidth + "×" + cameraImage.imageHeight + (cameraImage.recentBytesPerSec > 0 ? " · " + (cameraImage.recentBytesPerSec * 8 / 1000 / 1000).toFixed(1) + " Mbps" : "")
                    color: MoonrakerTheme.cameraLiveText
                    font: UM.Theme.getFont("small")
                }
            }
        }
    }
}
