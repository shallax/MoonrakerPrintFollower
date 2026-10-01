import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The plate cards' shared download action (4.6.0): the index
// download with the hourglass glyph, the progress bar, the integer
// percentage and the stage label — one component, both cards (the
// live request: the picker offers the same download as the follower).
Column {
    id: root
    Layout.fillWidth: true
    property var printerModel: null
    property string idleInstruction: "Click here to download and index the print — the plate needs the index."
    property int textAlignment: Text.AlignHCenter
    property bool indexReady: root.printerModel != null && root.printerModel.printIndexReady === true
    spacing: UM.Theme.getSize("narrow_margin").height

    function busy() {
        return root.printerModel != null && root.printerModel.improvingEta;
    }

    function percentText() {
        if (root.printerModel == null || root.printerModel.improveEtaProgress < 0) {
            return "";
        }
        return Math.round(root.printerModel.improveEtaProgress * 100) + "%";
    }

    // The WHOLE row clicks (the live request): the label and the glyph
    // are the same offer, not two targets. A layout-managed item may
    // not be anchored, so a plain Item hosts both the anchored
    // MouseArea and the explicitly sized Row that lays the row out.
    // Width flows down and implicit height flows up: no nested layout
    // solver can feed a wrapping label back into its own width.
    Item {
        id: instructionRow
        visible: !root.indexReady
        width: root.width
        height: instructionRowContent.implicitHeight

        Row {
            id: instructionRowContent
            width: Math.min(parent.width, 16 * screenScaleFactor + spacing + instructionMetrics.advanceWidth)
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: UM.Theme.getSize("narrow_margin").width
            Item {
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                UM.ColorImage {
                    id: downloadGlyph
                    anchors.fill: parent
                    source: root.busy() ? Qt.resolvedUrl("../resources/svg/Hourglass.svg") : Qt.resolvedUrl("../resources/svg/Download.svg")
                    color: UM.Theme.getColor("text")
                    states: [
                        State {
                            name: "idle"
                            when: !root.busy()
                            PropertyChanges {
                                target: downloadGlyph
                                rotation: 0
                            }
                        }
                    ]
                    SequentialAnimation on rotation {
                        running: root.busy()
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
            }
            UM.Label {
                id: instructionLabel
                width: Math.max(0, instructionRowContent.width - 16 * screenScaleFactor - instructionRowContent.spacing)
                text: root.busy() ? "Downloading and indexing the print…" : root.idleInstruction
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("small")
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignLeft
            }
        }
        TextMetrics {
            id: instructionMetrics
            font: instructionLabel.font
            text: instructionLabel.text
        }
        // Last of the wrapper's children, so it sits above the row
        // layout and the whole row stays one target.
        MouseArea {
            anchors.fill: parent
            enabled: root.printerModel != null && root.printerModel.monitorConnected && root.printerModel.printIndexReady !== true
            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.improveEta();
                }
            }
        }
    }

    UM.Label {
        width: root.width
        visible: root.indexReady
        text: "Print indexed — waiting for print movements."
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("small")
        wrapMode: Text.WordWrap
        horizontalAlignment: root.textAlignment
    }

    Row {
        id: progressRow
        objectName: "plateDownloadProgressRow"
        anchors.horizontalCenter: parent.horizontalCenter
        width: Math.min(root.width, 320 * screenScaleFactor)
        opacity: root.busy() ? 1 : 0
        spacing: UM.Theme.getSize("narrow_margin").width

        Item {
            width: Math.max(0, progressRow.width - percentage.width - phase.width - 2 * progressRow.spacing)
            height: 8 * screenScaleFactor
            anchors.verticalCenter: parent.verticalCenter
            clip: true
            property real sweepPhase: 0
            NumberAnimation on sweepPhase {
                running: root.busy() && (root.printerModel == null || root.printerModel.improveEtaProgress < 0)
                from: 0
                to: 1
                duration: 1100
                loops: Animation.Infinite
            }
            Cura.RoundedRectangle {
                anchors.fill: parent
                color: "transparent"
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("progressbar_radius").width
                cornerSide: Cura.RoundedRectangle.Direction.All
            }
            Cura.RoundedRectangle {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.margins: 1 * screenScaleFactor
                width: Math.max(0, Math.min(1, root.printerModel != null ? root.printerModel.improveEtaProgress : 0)) * (parent.width - 2 * screenScaleFactor)
                color: UM.Theme.getColor("primary")
                radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
            }
        }

        UM.Label {
            id: percentage
            anchors.verticalCenter: parent.verticalCenter
            // The integer percentage beside the bar (the live request).
            text: root.percentText()
            color: UM.Theme.getColor("text")
            font: UM.Theme.getFont("small")
        }

        UM.Label {
            id: phase
            anchors.verticalCenter: parent.verticalCenter
            // The stage label (the live request).
            text: root.busy() && root.printerModel != null ? root.printerModel.improveEtaPhase : ""
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("small")
        }
    }
}
