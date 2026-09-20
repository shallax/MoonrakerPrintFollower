import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The System section (4.3.0 extraction): the restart buttons and the
// policy-reason row out of the dashboard as one property-driven
// component.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "System"
        sectionId: "system"
        sectionIcon: "Settings"
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["system"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height / 2

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Firmware restart"
                objectName: "moonrakerFirmwareRestart"
                // The policy gate (4.2.0): the
                // reason rides the tooltip when
                // the button is denied.
                enabled: root.printerModel != null && root.printerModel.canRestart
                onClicked: root.printerModel.firmwareRestart()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Restart Klipper's firmware process (FIRMWARE_RESTART)." + (root.printerModel != null && !root.printerModel.canRestart && root.printerModel.restartReasonDetail !== "" ? " " + root.printerModel.restartReasonDetail : "")
                }
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Host restart"
                objectName: "moonrakerHostRestart"
                enabled: root.printerModel != null && root.printerModel.canRestart
                onClicked: root.printerModel.hostRestart()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Reboot the host Moonraker runs on (machine/reboot)." + (root.printerModel != null && !root.printerModel.canRestart && root.printerModel.restartReasonDetail !== "" ? " " + root.printerModel.restartReasonDetail : "")
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2
            Cura.SecondaryButton {
                // On its own row: three long labels
                // in one row crushed each other and
                // the text left its bounds (a live
                // report).
                Layout.fillWidth: true
                text: "Klipper restart"
                objectName: "moonrakerKlipperRestart"
                enabled: root.printerModel != null && root.printerModel.canRestart
                onClicked: root.printerModel.klipperRestart()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: "Restart Klipper entirely (printer/restart): reloads the config and reconnects the MCU." + (root.printerModel != null && !root.printerModel.canRestart && root.printerModel.restartReasonDetail !== "" ? " " + root.printerModel.restartReasonDetail : "")
                }
            }
        }
        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            UM.Label {
                height: 36 * screenScaleFactor
                text: "Status"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                height: 36 * screenScaleFactor
                // The restart reason is the policy's
                // own (4.2.0): the row carries it,
                // the tooltip is enrichment.
                text: root.printerModel != null && root.printerModel.restartReason !== "" ? root.printerModel.restartReason : "—"
                wrapMode: Text.NoWrap
                elide: Text.ElideRight
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                HoverHandler {
                    id: tooltipHover1
                }
                UM.ToolTip {
                    visible: tooltipHover1.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    // Short value in the row, full
                    // sentence in the tooltip (the ruling).
                    text: root.printerModel != null ? root.printerModel.restartReasonDetail : ""
                }
            }
        }
    }
}
