import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Plate section (4.6.0): the mini map out of the monitor as one
// property-driven component, mirroring the bed-mesh section. The mini
// is the glance — outlines plus the toolhead dot only, never the
// layer path (90 px of 50 outlines is already mush); a click opens
// the pop-over, where the gesture lives.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    signal popOverToggleRequested(string name)

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Plate"
        sectionId: "plate"
        sectionIcon: "Buildplate"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["plate"] !== false
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height

        // NO-REFLOW: the 90 px slot is always reserved; the map fades
        // in and out and the placeholder overlays the same slot, so
        // the plate arriving (connect, print start) never shifts the
        // section.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 90 * screenScaleFactor

            PlateCanvas {
                id: plateMini
                anchors.fill: parent
                compact: true
                printerModel: root.printerModel
                plate: root.printerModel != null ? root.printerModel.plateObjects : null
                dot: root.printerModel != null ? root.printerModel.plateDot : null
                opacity: root.printerModel != null && root.printerModel.plateObjects.objects.length > 0 ? 1 : 0
                MouseArea {
                    anchors.fill: parent
                    onClicked: root.popOverToggleRequested("plate")
                }
            }

            UM.Label {
                anchors.centerIn: parent
                opacity: root.printerModel == null || root.printerModel.plateObjects.objects.length === 0 ? 1 : 0
                text: root.printerModel != null && root.printerModel.printActive ? "No objects yet — they appear as the print defines them." : "The plate appears while printing — EXCLUDE_OBJECT data arrives from the slicer."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("small")
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
