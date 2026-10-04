import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The webcam card: the pane title with the camera controls, and beneath
// it the camera viewport — the picture, its overlays and its control
// bar. Property-driven — the host passes the printer model and its
// configured flag, keeps the card's Layout properties and owns the
// camera URL (applyCamera, handed straight to the viewport that owns
// the stream); the card keeps no state the model does not.
Cura.RoundedRectangle {
    id: root

    // The printer model the host passes (root.printer): every model
    // read inside the card goes through this property.
    property var printerModel: null

    Cura.MessageDialog {
        id: resetTrainingDialog
        objectName: "resetCameraTrainingDialog"
        property var targetMonitor: null
        property string cameraKey: ""
        property string cameraLabel: ""
        title: "Reset training data for " + cameraLabel + "?"
        text: "This camera will learn a new baseline from six analysed frames. Keep the scene free of spaghetti while learning. Detection zones, tuning and other cameras will be kept."
        standardButtons: Dialog.Yes | Dialog.No
        anchors.centerIn: Overlay.overlay
        onAccepted: {
            if (targetMonitor != null && targetMonitor === root.printerModel && cameraKey === targetMonitor.detectionRegionCamera)
                targetMonitor.resetDetectionBaseline();
        }
    }

    // The host's cameraConfigured: a webcam URL is present.
    property bool configured: false

    // The viewport's width: the host's squeeze latch reads it.
    property real viewportWidth: cameraViewport.width

    // The card's content height: the host's Layout.preferredHeight
    // binding reads it, so the console pane below keeps absorbing the
    // leftover space exactly as it did when both cards shared one
    // document.
    property real contentHeight: cameraColumn.implicitHeight

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

    // ── the control bar: the zoom scale and the camera rate ─────────
    // Above 5 FPS the renderer decodes at this rate without changing
    // the MJPEG download. At 5 FPS or below a configured snapshot URL
    // replaces the stream, reducing network use. The ceiling is the
    // camera's own configured target_fps from Moonraker's webcam
    // list (never a product constant).
    readonly property real cameraFps: root.printerModel != null ? root.printerModel.cameraFps : 0
    readonly property real cameraFpsMin: root.printerModel != null ? root.printerModel.cameraFpsMin : 0.5
    readonly property real cameraFpsMax: root.printerModel != null ? root.printerModel.cameraFpsMax : 30
    readonly property real cameraSnapshotMaxFps: root.printerModel != null ? root.printerModel.cameraSnapshotMaxFps : 5
    readonly property bool cameraSnapshotAvailable: root.printerModel != null && root.printerModel.cameraSnapshotAvailable

    // The host's camera URL: the card publishes the API, the viewport
    // that owns the stream does the applying.
    function applyCamera(url, visible) {
        cameraViewport.applyCamera(url, visible);
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
                id: cameraTitle
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
                            id: refreshButton
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
                            id: webcamToggle
                            text: "Enable"
                            checked: root.printerModel != null ? root.printerModel.webcamStreamEnabled : true
                            onToggled: {
                                if (root.printerModel != null) {
                                    root.printerModel.setWebcamStreamEnabled(checked);
                                }
                            }
                        }
                        Cura.SecondaryButton {
                            id: resetTrainingButton
                            objectName: "resetCameraTrainingButton"
                            text: "Reset training data"
                            // Reserve the selector's minimum width, the refresh
                            // icon and Enable first. Visibility must depend on
                            // the outer width rather than this row's implicit
                            // width, which itself changes with visibility.
                            visible: cameraControls.visible && root.printerModel != null && root.printerModel.detectionGlobalEnabled && root.printerModel.detectionEnabled && root.width >= 450 * screenScaleFactor && root.width - cameraTitle.implicitWidth - 3 * UM.Theme.getSize("default_margin").width >= (cameraSelector.visible ? cameraSelector.Layout.minimumWidth + UM.Theme.getSize("narrow_margin").width : 0) + refreshButton.width + webcamToggle.implicitWidth + resetTrainingButton.implicitWidth + 4 * UM.Theme.getSize("narrow_margin").width
                            enabled: visible && root.printerModel.detectionCameraReady && !root.printerModel.detectionEditingRegions
                            onClicked: {
                                resetTrainingDialog.targetMonitor = root.printerModel;
                                resetTrainingDialog.cameraKey = root.printerModel.detectionRegionCamera;
                                resetTrainingDialog.cameraLabel = root.printerModel.cameraName || "selected camera";
                                resetTrainingDialog.open();
                            }
                        }
                    }
                }
            }
        }

        // The picture, its overlays and its control bar: the viewport
        // owns the stream, the view transform and the bar's rhythm.
        CameraViewport {
            id: cameraViewport
            printerModel: root.printerModel
            configured: root.configured
            cameraFps: root.cameraFps
            cameraFpsMin: root.cameraFpsMin
            cameraFpsMax: root.cameraFpsMax
            cameraSnapshotMaxFps: root.cameraSnapshotMaxFps
            cameraSnapshotAvailable: root.cameraSnapshotAvailable
            traceCameraTiming: root.traceCameraTiming
            paneId: root.paneId
        }
    }
}
