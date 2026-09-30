import QtQuick 2.15
import UM 1.5 as UM

// The information strip's readout: the vertical title and the rotated
// row of peripheral temperatures below it. The pane frames it against
// the header; the row is handed out so the host's fit pass can measure
// it where it lands.
Item {
    id: root

    property bool infoCollapsed: false
    property string hotendText: "—"
    property string bedText: "—"

    readonly property alias readoutRow: infoReadoutRow

    visible: root.infoCollapsed
    width: infoCollapsedTitle.implicitHeight
    height: infoCollapsedTitle.implicitWidth
    UM.Label {
        id: infoCollapsedTitle
        text: "Information"
        font: UM.Theme.getFont("medium_bold")
        color: UM.Theme.getColor("text_inactive")
        rotation: -90
        anchors.centerIn: parent
    }

    Item {
        id: infoCollapsedReadoutBox
        visible: root.infoCollapsed
        clip: true
        anchors.top: parent.bottom
        anchors.topMargin: 2 * UM.Theme.getSize("default_margin").height
        anchors.horizontalCenter: parent.horizontalCenter
        // The box hugs the content: its height tracks
        // the row's implicit width, so the centred row
        // fills it and the strip starts at the margin
        // under the title (direct row positioning hid
        // the content on the engine, the live
        // report). 18 is the label line height.
        width: 18 * screenScaleFactor
        height: infoReadoutRow.implicitWidth
        Row {
            id: infoReadoutRow
            anchors.centerIn: parent
            spacing: 2 * screenScaleFactor
            rotation: -90
            // The fit hides through OPACITY, never
            // the visibility flag: an invisible group
            // keeps its place in the layout, so the
            // survivors can never re-centre in the box
            // (the live report) — the strip stays
            // anchored under
            // the title.
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.hotendText !== "—"
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../Resources/Svg/Thermometer.svg")
            }
            UM.Label {
                objectName: "infoCollapsedReadoutText"
                property bool fitHidden: false
                visible: root.hotendText !== "—"
                opacity: fitHidden ? 0 : 1
                text: root.hotendText
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }
            Item {
                visible: root.bedText !== "—"
                width: 8 * screenScaleFactor
                height: 16 * screenScaleFactor
            }
            UM.ColorImage {
                color: UM.Theme.getColor("text")
                property bool fitHidden: false
                visible: root.bedText !== "—"
                opacity: fitHidden ? 0 : 1
                width: 16 * screenScaleFactor
                height: 16 * screenScaleFactor
                source: Qt.resolvedUrl("../../Resources/Svg/Bed.svg")
            }
            UM.Label {
                property bool fitHidden: false
                visible: root.bedText !== "—"
                opacity: fitHidden ? 0 : 1
                text: root.bedText
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text")
                elide: Text.ElideRight
            }
        }
    }
}
