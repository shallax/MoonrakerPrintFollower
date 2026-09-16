import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The File-manager section (4.3.0 extraction): the trigger row out of
// the dashboard as one property-driven component. The popup itself
// stays a stage-level sibling of the panes.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "File manager"
        sectionId: "fileManager"
        sectionIconUrl: Qt.resolvedUrl("Download.svg")
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["fileManager"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height

        UM.Label {
            Layout.fillWidth: true
            height: 36 * screenScaleFactor
            text: "Browse, print and manage the printer's gcode files."
            color: UM.Theme.getColor("text_inactive")
            elide: Text.ElideRight
            wrapMode: Text.NoWrap
        }
        Cura.SecondaryButton {
            Layout.fillWidth: true
            text: "File manager"
            enabled: root.printerModel != null && root.printerModel.monitorConnected
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.setFileManagerOpen(true);
                }
            }
            UM.TooltipArea {
                anchors.fill: parent
                acceptedButtons: Qt.NoButton
                text: root.printerModel != null && root.printerModel.monitorConnected ? "Open the file manager." : "The printer is disconnected."
            }
        }
    }
}
