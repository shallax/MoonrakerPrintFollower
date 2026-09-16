import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Fans section (4.3.0 extraction): the per-fan status rows out
// of the monitor as one property-driven component. The host keeps
// the capability gate.
Column {
    id: root
    property var printerModel: null
    CollapsibleSectionHeader {
        width: parent.width
        printerModel: root.printerModel
        title: "Fans"
        sectionId: "fansinfo"
        sectionIcon: "Fan"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["fansinfo"] !== false
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        anchors.right: parent.right
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height / 2

        Repeater {
            model: root.printerModel != null ? root.printerModel.fanItems : []
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width
                UM.Label {
                    text: modelData.name
                    color: UM.Theme.getColor("text_inactive")
                    Layout.preferredWidth: 110 * screenScaleFactor
                    elide: Text.ElideRight
                }
                UM.Label {
                    text: modelData.detail
                }
            }
        }
    }
}
