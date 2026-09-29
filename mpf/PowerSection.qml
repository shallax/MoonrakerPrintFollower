import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Power section (4.3.0 extraction): the per-device toggles and the
// lock reason row out of the dashboard as one property-driven
// component. The power-off confirmation dialog stays with the host —
// the section requests it through a signal.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    signal powerOffConfirmRequested(string deviceName)

    property bool anyPowerLocked: {
        if (printerModel == null || printerModel.powerDevices == null)
            return false;
        for (var i = 0; i < printerModel.powerDevices.length; ++i) {
            if (printerModel.powerDevices[i].locked && !printerModel.powerDevices[i].can_toggle)
                return true;
        }
        return false;
    }

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Power"
        sectionId: "power"
        sectionIconUrl: Qt.resolvedUrl("Power.svg")
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel != null && root.printerModel.powerDevices.length > 0 && root.printerModel.sectionExpandedMap["power"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height / 2

        Repeater {
            model: root.printerModel != null ? root.printerModel.powerDevices : []
            RowLayout {
                Layout.fillWidth: true
                UM.Label {
                    text: modelData.name + "  · " + modelData.status
                    color: UM.Theme.getColor("text_inactive")
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                Cura.SecondaryButton {
                    enabled: root.printerModel != null && modelData.can_toggle && !root.printerModel.actionBusy
                    text: modelData.status === "on" ? "Turn off" : "Turn on"
                    fixedWidthMode: true
                    width: 104 * screenScaleFactor
                    onClicked: {
                        if (modelData.status === "on" && root.printerModel.printActive) {
                            root.powerOffConfirmRequested(modelData.name);
                        } else {
                            root.printerModel.setPowerDevice(modelData.name, modelData.status !== "on");
                        }
                    }
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
                text: root.printerModel != null && root.printerModel.sectionReason !== "" ? root.printerModel.sectionReason : (root.anyPowerLocked ? "Locked during this print" : "—")
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
                    text: root.printerModel != null && root.printerModel.sectionReasonDetail !== "" ? root.printerModel.sectionReasonDetail : (root.anyPowerLocked ? "Power control is locked by Moonraker while this print is active." : "")
                }
            }
        }
    }
}
