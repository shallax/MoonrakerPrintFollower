import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Configuration-changes section (4.3.0 extraction): the save
// button, the pending summary and the save-reason row out of the
// dashboard as one property-driven component.
Column {
    id: root
    property var printerModel: null

    CollapsibleSectionHeader {
        width: parent.width
        printerModel: root.printerModel
        title: "Configuration changes"
        sectionId: "save"
        sectionIcon: "Save"
    }
    Item {
        width: 1
        height: UM.Theme.getSize("default_margin").height
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["save"] !== false
    }

    ColumnLayout {
        // NO-REFLOW RULE: this whole section used to
        // pop into existence when Klipper flagged a
        // pending config (save_config_pending flips
        // mid-session, e.g. after a mesh calibration),
        // shoving every section beneath it. It now
        // always renders — the button disables and
        // the summary goes quiet when nothing is
        // pending.
        width: parent.width - UM.Theme.getSize("narrow_margin").width - UM.Theme.getSize("section_icon").width / 2
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["save"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)

        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            UM.Label {
                height: 36 * screenScaleFactor
                text: "Pending"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                height: 36 * screenScaleFactor
                text: root.printerModel != null && root.printerModel.saveConfigSummary.length > 0 ? root.printerModel.saveConfigSummary : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
                UM.TooltipArea {
                    anchors.fill: parent
                    text: parent.text
                    acceptedButtons: Qt.NoButton
                }
            }
        }
        Cura.PrimaryButton {
            Layout.fillWidth: true
            text: "Save configuration"
            enabled: root.printerModel != null && root.printerModel.canSaveConfig
            onClicked: root.printerModel.saveConfig()
        }
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            UM.Label {
                height: 36 * screenScaleFactor
                text: "Save"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                height: 36 * screenScaleFactor
                text: root.printerModel != null ? (root.printerModel.sectionReason !== "" ? root.printerModel.sectionReason : (root.printerModel.printActive ? "Disabled during a print" : root.printerModel.canSaveConfig ? "Restarts Klipper" : "—")) : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
                UM.TooltipArea {
                    anchors.fill: parent
                    // Short value in the row, full
                    // sentence in the tooltip (the
                    // author's ruling).
                    text: root.printerModel != null ? (root.printerModel.sectionReasonDetail !== "" ? root.printerModel.sectionReasonDetail : (root.printerModel.printActive ? "SAVE_CONFIG is disabled during a print." : root.printerModel.canSaveConfig ? "Saving configuration restarts Klipper." : "")) : ""
                    acceptedButtons: Qt.NoButton
                }
            }
        }
    }

    Item {
        width: 1
        height: UM.Theme.getSize("default_margin").height
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["save"] !== false
    }
}
