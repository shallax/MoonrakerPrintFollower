import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
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
    // cause): Cura's NetworkMJPGImage.setSourceURL auto-restarts a
    // STARTED image, so the previous onSourceChanged start raced that
    // auto-restart into TWO stream starts per application. The image
    // is stopped FIRST — the setter then cannot auto-restart while
    // the assignment is in progress — and exactly one start() runs,
    // at the end, only when a real URL should run.
    property bool _cameraApplyInProgress: false
    // The T0-T9 timing chain's T9 gate (the reviewer's cold-start
    // diagnostics): the host mirrors the client's trace flag, and
    // stamps the chain's origin.
    property bool traceCameraTiming: false
    property real cameraTraceOrigin: 0

    function applyCamera(url, visible) {
        var text = url != null ? url.toString() : "";
        var shouldRun = visible && text.length > 0;
        _cameraApplyInProgress = true;
        cameraImage.stop();
        cameraImage.source = url;
        cameraImage.visible = visible;
        _cameraApplyInProgress = false;
        if (shouldRun) {
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

        UM.Label {
            // The pane title, in the other panes'
            // style — the same large bold face as
            // Information and Printer status (a
            // live report).
            text: "Webcam"
            font: UM.Theme.getFont("large_bold")
            color: UM.Theme.getColor("text")
            Layout.fillWidth: true
            Layout.topMargin: UM.Theme.getSize("default_margin").height
            Layout.leftMargin: UM.Theme.getSize("default_margin").width
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

            Cura.NetworkMJPGImage {
                id: cameraImage
                // visible and source arrive through the
                // host's applyCamera() — no bindings
                // here to go stale.
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
                    // The stage hide/show lifecycle ONLY: applyCamera
                    // owns every start/stop during an application, and
                    // the in-progress guard keeps this handler out of
                    // its way.
                    if (_cameraApplyInProgress) {
                        return;
                    }
                    if (source !== "") {
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
                    // hop of the T0-T9 chain).
                    if (root.traceCameraTiming && imageWidth > 0) {
                        console.log("camera first frame at +" + ((Date.now() - root.cameraTraceOrigin) / 1000.0).toFixed(2) + " s");
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
                        text: "Live"
                        color: MoonrakerTheme.cameraLiveText
                        font: UM.Theme.getFont("small")
                    }
                }
            }
        }

        // Camera control bar: a centred "Camera: <webcam>
        // <refresh>" group tucked under the feed. The dropdown
        // already carries the selected name, so no label repeats
        // it. (A plain Column ignores Layout.alignment, so the
        // bar must be a ColumnLayout for the centring to hold.)
        ColumnLayout {
            // No cameras, no bar: "Camera:" with an empty
            // dropdown and a refresh button is dead chrome
            // under the "No webcam configured" message.
            visible: root.printerModel != null && root.printerModel.webcamNames.length > 0
            Layout.fillWidth: true
            spacing: 0

            Rectangle {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }

            Item {
                Layout.fillWidth: true
                height: cameraControls.height + 2 * UM.Theme.getSize("narrow_margin").height

                // The final ruling: the
                // label sits PERMANENTLY above the
                // dropdown, centred, no colon — no
                // conditional layouts, nothing to
                // overlap the pane at any width.
                ColumnLayout {
                    id: cameraControls
                    objectName: "cameraControls"
                    anchors.centerIn: parent
                    width: parent.width
                    spacing: 0

                    UM.Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Camera"
                        font: UM.Theme.getFont("medium")
                        color: UM.Theme.getColor("text")
                    }

                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        // The inset keeps the combo
                        // from ever touching the pane
                        // edge at the crush (a
                        // live report).
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
    }
}
