import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../resources/theme"
import "../widgets"

Item {
    id: root
    required property var settings
    readonly property bool detectionReady: settings && settings.detectionReady !== undefined ? settings.detectionReady : false
    readonly property bool detectionBusy: settings && settings.detectionBusy !== undefined ? settings.detectionBusy : false
    readonly property string detectionPhase: settings && settings.detectionPhase !== undefined ? settings.detectionPhase : ""
    readonly property int detectionReceived: settings && settings.detectionReceived !== undefined ? settings.detectionReceived : 0
    readonly property int detectionTotal: settings && settings.detectionTotal !== undefined ? settings.detectionTotal : 0
    readonly property string detectionError: settings && settings.detectionError !== undefined ? settings.detectionError : ""
    readonly property string detectionHostError: settings && settings.detectionHostError !== undefined ? settings.detectionHostError : ""
    readonly property bool detectionCameraReady: settings && settings.detectionCameraReady !== undefined ? settings.detectionCameraReady : false
    readonly property var values: ({})

    Connections {
        target: root.settings || null
        function onDetectionChanged() {
            if (root.detectionBusy) {
                setupOffer.close();
                setupProgress.open();
            } else {
                setupProgress.close();
            }
        }
    }

    Flickable {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        contentWidth: width
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            objectName: "settingsScrollbar"
        }

        Column {
            id: content
            width: Math.max(0, parent.width - UM.Theme.getSize("scrollbar").width - 4 * screenScaleFactor)
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label {
                text: "Failure detection"
                font: UM.Theme.getFont("large_bold")
            }
            UM.Label {
                objectName: "detectionReliabilityWarning"
                width: parent.width
                wrapMode: Text.WordWrap
                font: UM.Theme.getFont("medium_bold")
                text: "Important: Local failure detection is only an assistant, not a safety system. It is not guaranteed to detect failures and can flag healthy prints; automatic pause is not guaranteed. Check your prints yourself and keep normal printer safety measures in place."
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Download and check the shared Obico model here, then configure the active printer in Printer controls > Failure Detection. Nothing is uploaded."
            }
            UM.Label {
                objectName: "detectionModelAttribution"
                width: parent.width
                textFormat: Text.RichText
                text: "Model by <a href=\"https://www.obico.io/\">Obico</a>"
                onLinkActivated: function (link) {
                    Qt.openUrlExternally(link);
                }
            }
            Cura.SecondaryButton {
                text: root.detectionReady ? "Model ready" : "Set up local detection"
                enabled: !root.detectionReady && !root.detectionHostError && !root.detectionBusy
                onClicked: setupOffer.open()
            }
            UM.CheckBox {
                objectName: "detectionGlobalEnabledCheckbox"
                text: "Enable local failure detection globally"
                checked: root.settings && root.settings.detectionGlobalEnabled !== undefined ? root.settings.detectionGlobalEnabled : false
                enabled: root.detectionReady && !root.detectionBusy
                onToggled: {
                    if (!settings.setDetectionGlobalEnabled(checked))
                        checked = settings.detectionGlobalEnabled;
                }
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Turning this off stops detection for every printer. Printers with detection enabled keep their controls visible with a disabled status. Your per-printer choices are kept for when you turn it back on."
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                visible: root.detectionError !== ""
                text: root.detectionError
                color: MoonrakerTheme.dangerRed
            }

            UM.Label {
                text: "Requirements"
                font: UM.Theme.getFont("medium_bold")
            }
            Repeater {
                model: [
                    {
                        "name": "Host hardware",
                        "detail": root.detectionHostError || "Supported CPU and memory",
                        "ok": !root.detectionHostError
                    },
                    {
                        "name": "Inference runtime",
                        "detail": root.detectionReady ? (root.settings.detectionGlobalEnabled ? "Installed and checked" : "Installed; checked when enabled") : "Not checked",
                        "ok": root.detectionReady
                    },
                    {
                        "name": "Model",
                        "detail": root.detectionReady ? (root.settings.detectionGlobalEnabled ? "Installed and checked" : "Installed; checked when enabled") : "Not checked (shared across printers)",
                        "ok": root.detectionReady
                    },
                    {
                        "name": "Camera",
                        "detail": root.detectionCameraReady ? "Selected and connected" : "Select a connected webcam for this printer",
                        "ok": root.detectionCameraReady
                    }
                ]
                delegate: RowLayout {
                    width: content.width
                    spacing: UM.Theme.getSize("default_margin").width
                    Rectangle {
                        width: 12 * screenScaleFactor
                        height: width
                        radius: width / 2
                        color: modelData.ok ? MoonrakerTheme.successGreen : MoonrakerTheme.dangerRed
                    }
                    UM.Label {
                        text: modelData.name + " — " + modelData.detail
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                    }
                }
            }

            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "The live Failure signal on the Monitor webcam is relative to a camera-specific baseline, not a calibrated probability. Its warning and failure positions follow the thresholds in Printer controls."
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "The per-printer safe period suppresses warnings and automatic pause at the start of a print (default five minutes). Only confirmed failures request a pause; prints are never cancelled automatically. Notifications resume after you acknowledge an alert."
                color: UM.Theme.getColor("text_inactive")
            }
        }
    }

    Popup {
        id: setupOffer
        modal: true
        anchors.centerIn: parent
        closePolicy: Popup.NoAutoClose
        padding: UM.Theme.getSize("default_margin").width
        width: Math.min(420 * screenScaleFactor, root.width)
        height: setupContent.implicitHeight + 2 * padding
        background: Rectangle {
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
        }
        contentItem: Column {
            id: setupContent
            spacing: UM.Theme.getSize("default_margin").height
            UM.Label {
                width: parent.width
                text: "Set up local failure detection"
                font: UM.Theme.getFont("large_bold")
                wrapMode: Text.WordWrap
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Download the Obico model (192.9 MiB) and CPU inference runtime (" + (Math.round((root.settings && root.settings.detectionRuntimeSize !== undefined ? root.settings.detectionRuntimeSize : 0) / 1048576 * 10) / 10) + " MiB) to this computer. The model is checked and benchmarked before it can be enabled for a printer. No cloud account, Docker, or printer commands."
            }
            UM.Label {
                objectName: "detectionSetupLicense"
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.RichText
                text: "Obico model weights are licensed under <a href=\"https://github.com/TheSpaghettiDetective/obico-server/blob/release/LICENSE\">GNU AGPL 3.0</a>. Moonraker Print Follower is GNU GPL 3.0; the licences are compatible under GPL 3.0 section 13, with AGPL requirements still applying."
                onLinkActivated: function (link) {
                    Qt.openUrlExternally(link);
                }
            }
            RowLayout {
                width: parent.width
                Cura.SecondaryButton {
                    text: "Not now"
                    onClicked: setupOffer.close()
                }
                Item {
                    Layout.fillWidth: true
                }
                Cura.PrimaryButton {
                    text: "Download and check"
                    onClicked: {
                        settings.startDetectionSetup();
                    }
                }
            }
        }
    }

    Popup {
        id: setupProgress
        objectName: "detectionSetupProgress"
        modal: true
        anchors.centerIn: parent
        closePolicy: Popup.NoAutoClose
        padding: UM.Theme.getSize("default_margin").width
        width: Math.min(360 * screenScaleFactor, root.width)
        height: progressContent.implicitHeight + 2 * padding
        background: Rectangle {
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
        }
        contentItem: Column {
            id: progressContent
            spacing: UM.Theme.getSize("narrow_margin").height
            UM.ColorImage {
                objectName: "detectionDownloadHourglass"
                anchors.horizontalCenter: parent.horizontalCenter
                source: Qt.resolvedUrl("../resources/svg/Hourglass.svg")
                color: UM.Theme.getColor("text")
                width: 56 * screenScaleFactor
                height: 56 * screenScaleFactor
                SequentialAnimation on rotation {
                    objectName: "detectionDownloadHourglassRotation"
                    running: setupProgress.visible
                    loops: Animation.Infinite
                    NumberAnimation {
                        from: 0
                        to: 180
                        duration: 350
                        easing.type: Easing.InOutCubic
                    }
                    PauseAnimation {
                        duration: 700
                    }
                    NumberAnimation {
                        from: 180
                        to: 360
                        duration: 350
                        easing.type: Easing.InOutCubic
                    }
                    PauseAnimation {
                        duration: 700
                    }
                }
            }
            UM.Label {
                width: parent.width
                text: root.detectionPhase === "benchmark" ? "Checking local inference" : "Downloading local detection"
                font: UM.Theme.getFont("large_bold")
                wrapMode: Text.WordWrap
            }
            UM.Label {
                width: parent.width
                text: root.detectionPhase === "runtime" ? "CPU inference runtime" : root.detectionPhase === "model" ? "Failure-detection model" : "Running a real model inference on this computer"
                wrapMode: Text.WordWrap
            }
            OutlineProgressBar {
                width: parent.width
                height: 10 * screenScaleFactor
                value: root.detectionTotal > 0 ? Math.round(100 * root.detectionReceived / root.detectionTotal) : 0
            }
            RowLayout {
                width: parent.width
                UM.Label {
                    text: root.detectionTotal > 0 ? Math.round(100 * root.detectionReceived / root.detectionTotal) + "%" : "Checking…"
                    Layout.fillWidth: true
                }
                UM.Label {
                    text: root.detectionTotal > 0 ? (Math.round(root.detectionReceived / 1048576 * 10) / 10) + " MiB / " + (Math.round(root.detectionTotal / 1048576 * 10) / 10) + " MiB" : ""
                    color: UM.Theme.getColor("text_inactive")
                }
            }
            Cura.SecondaryButton {
                width: parent.width
                text: "Cancel setup"
                onClicked: settings.cancelDetectionSetup()
            }
        }
    }
}
