import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../widgets"

Popup {
    id: root
    objectName: "detectionFirstRunOffer"
    property var detection: null
    readonly property bool busy: detection ? detection.busy : false
    readonly property bool ready: detection ? detection.ready : false
    readonly property string phase: detection ? detection.phase : ""
    readonly property int received: detection ? detection.received : 0
    readonly property int total: detection ? detection.total : 0
    readonly property string error: detection ? detection.error : ""
    modal: true
    focus: true
    closePolicy: Popup.NoAutoClose
    width: Math.min(440 * screenScaleFactor, parent ? parent.width : 440 * screenScaleFactor)
    height: content.implicitHeight + 2 * padding
    padding: UM.Theme.getSize("default_margin").width
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
    }

    Connections {
        target: root.detection || null
        function onStateChanged() {
            if (root.ready)
                root.close();
        }
    }

    contentItem: Column {
        id: content
        spacing: UM.Theme.getSize("default_margin").height

        UM.Label {
            width: parent.width
            text: "Optional local failure detection"
            font: UM.Theme.getFont("large_bold")
            wrapMode: Text.WordWrap
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Analyse the selected webcam on this computer. Nothing is uploaded. Detection is only an assistant, not a safety system: it can miss failures or flag healthy prints. Optional notifications and automatic pause start off. After setup, enable each printer in Printer controls > Failure Detection; the global switch is in Detection settings."
        }
        UM.Label {
            width: parent.width
            textFormat: Text.RichText
            text: "Model by <a href=\"https://www.obico.io/\">Obico</a>"
            onLinkActivated: function (link) {
                Qt.openUrlExternally(link);
            }
        }
        UM.Label {
            width: parent.width
            visible: root.ready
            text: "Local detection is already set up on this computer. No download is needed."
            wrapMode: Text.WordWrap
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: !root.ready
            text: "Download a verified Obico model (192.9 MiB) and a CPU inference runtime (" + (Math.round((root.detection ? root.detection.runtime_size : 0) / 1048576 * 10) / 10) + " MiB), then check performance on this computer. No account, Docker, or external server is needed."
        }
        UM.Label {
            objectName: "detectionOfferLicense"
            width: parent.width
            wrapMode: Text.WordWrap
            textFormat: Text.RichText
            visible: !root.ready
            text: "Obico model weights are licensed under <a href=\"https://github.com/TheSpaghettiDetective/obico-server/blob/release/LICENSE\">GNU AGPL 3.0</a>. Moonraker Print Follower is GNU GPL 3.0; the licences are compatible under GPL 3.0 section 13, with AGPL requirements still applying."
            onLinkActivated: function (link) {
                Qt.openUrlExternally(link);
            }
        }
        UM.Label {
            width: parent.width
            visible: root.busy
            text: root.phase === "benchmark" ? "Checking local inference…" : "Downloading " + (root.phase === "runtime" ? "runtime" : "model") + "…"
        }
        UM.ColorImage {
            anchors.horizontalCenter: parent.horizontalCenter
            source: Qt.resolvedUrl("../resources/svg/Hourglass.svg")
            color: UM.Theme.getColor("text")
            visible: root.busy
            width: 48 * screenScaleFactor
            height: 48 * screenScaleFactor
            SequentialAnimation on rotation {
                running: root.busy
                loops: Animation.Infinite
                NumberAnimation {
                    from: 0
                    to: 180
                    duration: 350
                }
                PauseAnimation {
                    duration: 700
                }
                NumberAnimation {
                    from: 180
                    to: 360
                    duration: 350
                }
                PauseAnimation {
                    duration: 700
                }
            }
        }
        OutlineProgressBar {
            width: parent.width
            height: 10 * screenScaleFactor
            visible: root.busy
            value: root.total > 0 ? Math.round(100 * root.received / root.total) : 0
        }
        UM.Label {
            width: parent.width
            visible: root.busy && root.total > 0
            text: root.total > 0 ? Math.round(100 * root.received / root.total) + "% · " + (Math.round(root.received / 1048576 * 10) / 10) + " MiB / " + (Math.round(root.total / 1048576 * 10) / 10) + " MiB" : ""
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: !!root.error
            color: UM.Theme.getColor("error")
            text: root.error
        }
        RowLayout {
            width: parent.width
            Cura.SecondaryButton {
                objectName: "detectionOfferDismiss"
                text: root.busy ? "Cancel setup" : (root.ready ? "Done" : "Not now")
                onClicked: {
                    if (root.busy) {
                        root.detection.cancel();
                    } else {
                        root.detection.decline_offer();
                        if (!root.detection.should_offer)
                            root.close();
                    }
                }
            }
            Item {
                Layout.fillWidth: true
            }
            Cura.PrimaryButton {
                visible: !root.busy && !root.ready
                text: root.error ? "Retry setup" : "Download and check"
                onClicked: root.detection.setup()
            }
        }
    }
}
