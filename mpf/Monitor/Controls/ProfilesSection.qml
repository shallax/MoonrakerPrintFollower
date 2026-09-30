import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../Widgets"

// The Temperature-profiles section (4.3.0 extraction): the preset
// rows, the cooldown and the status readout moved out of the
// dashboard as one property-driven component.
Item {
    id: root
    // Width flows down from the pane; content height flows back up only
    // through this implicit size. A layout around the inner layout made
    // late telemetry/preset rows recursively polish both layout solvers.
    readonly property real sideMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
    readonly property real verticalMargin: UM.Theme.getSize("default_margin").height
    implicitWidth: sectionBody.implicitWidth + 2 * sideMargin
    implicitHeight: sectionHeader.height + (sectionBody.visible ? sectionBody.implicitHeight + 2 * verticalMargin : 0)
    property var printerModel: null

    CollapsibleSectionHeader {
        id: sectionHeader
        width: parent.width
        printerModel: root.printerModel
        title: "Temperature profiles"
        sectionId: "profiles"
        sectionIcon: "PrintQuality"
    }
    ColumnLayout {
        id: sectionBody
        x: root.sideMargin
        y: sectionHeader.height + root.verticalMargin
        width: Math.max(0, root.width - 2 * root.sideMargin)
        visible: root.printerModel != null && root.printerModel.temperaturePresetItems.length > 0 && root.printerModel.sectionExpandedMap["profiles"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height / 2

        Repeater {
            model: root.printerModel != null ? root.printerModel.temperaturePresetItems : []
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: modelData.active ? "Active — " + modelData.name : modelData.name
                enabled: root.printerModel != null && root.printerModel.canApplyTemperaturePreset
                onClicked: root.printerModel.applyTemperaturePreset(modelData.index)
            }
        }
        Cura.SecondaryButton {
            Layout.fillWidth: true
            text: "Cooldown"
            enabled: root.printerModel != null && root.printerModel.canApplyTemperaturePreset
            onClicked: root.printerModel.heatersOff()
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "Turn every heater off: all targets to 0 °C."
            }
        }
        UM.Label {
            text: "A profile is marked Active only when all of its enabled heater targets match the printer. G-code-only profiles are never assumed active."
            color: UM.Theme.getColor("text_inactive")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
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
                text: root.printerModel != null ? (root.printerModel.sectionReason !== "" ? root.printerModel.sectionReason : (root.printerModel.printActive && !root.printerModel.canApplyTemperaturePreset ? "Disabled during a print" : "—")) : "—"
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
                    text: root.printerModel != null ? (root.printerModel.sectionReasonDetail !== "" ? root.printerModel.sectionReasonDetail : (root.printerModel.printActive && !root.printerModel.canApplyTemperaturePreset ? "Temperature profiles are disabled while the print is running. A paused print allows them." : "")) : ""
                }
            }
        }
    }
}
