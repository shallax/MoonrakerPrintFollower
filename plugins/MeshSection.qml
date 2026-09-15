import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Bed-mesh section (4.3.0 extraction): the mini map out of the
// monitor as one property-driven component. The shared pop-over stays
// with the host — the section requests it through a signal.
Column {
    id: root
    property var printerModel: null
    signal popOverToggleRequested(string name)

    CollapsibleSectionHeader {
        width: parent.width
        printerModel: root.printerModel
        title: "Bed mesh"
        sectionId: "meshmap"
        sectionIcon: "Buildplate"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["meshmap"] !== false
        width: parent.width - UM.Theme.getSize("narrow_margin").width - UM.Theme.getSize("section_icon").width / 2
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        spacing: UM.Theme.getSize("default_margin").height

        // The mini map is the at-a-glance widget; a
        // click opens the pop-over detail view with
        // the probe-snapping crosshair. NO-REFLOW
        // RULE: the 90 px slot is always reserved —
        // the map fades in and out, and the
        // placeholder is an overlay inside the same
        // slot, so the mesh arriving (connect,
        // calibrate) never shifts the section.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 90 * screenScaleFactor

            BedMeshMap {
                id: meshMiniMap
                anchors.fill: parent
                compact: true
                printer: root.printerModel
                opacity: root.printerModel != null && root.printerModel.bedMeshAvailable ? 1 : 0
                tooltipText: root.printerModel != null ? "Click for the full bed mesh map (" + root.printerModel.bedMeshRangeText + ")." : "Click for the full bed mesh map."
                onClicked: root.popOverToggleRequested("mesh")
            }

            UM.Label {
                anchors.centerIn: parent
                opacity: root.printerModel == null || !root.printerModel.bedMeshAvailable ? 1 : 0
                text: "No bed mesh to show"
                color: UM.Theme.getColor("text_inactive")
            }
        }
    }

    Item {
        width: 1
        height: UM.Theme.getSize("default_margin").height
    }
}
