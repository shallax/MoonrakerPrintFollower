import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Temperature-profiles section (4.3.0 extraction): the preset
// rows, the cooldown and the status readout moved out of the
// dashboard as one property-driven component.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Temperature profiles"
        sectionId: "profiles"
        sectionIcon: "PrintQuality"
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
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
            tooltip: "Turn every heater off: all targets to 0 °C."
            enabled: root.printerModel != null && root.printerModel.canApplyTemperaturePreset
            onClicked: root.printerModel.heatersOff()
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
                text: root.printerModel != null ? (root.printerModel.sectionReason !== "" ? root.printerModel.sectionReason : (root.printerModel.printActive ? "Disabled during a print" : "—")) : "—"
                wrapMode: Text.NoWrap
                elide: Text.ElideRight
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                UM.TooltipArea {
                    anchors.fill: parent
                    // Short value in the row, full
                    // sentence in the tooltip (the ruling).
                    text: root.printerModel != null ? (root.printerModel.sectionReasonDetail !== "" ? root.printerModel.sectionReasonDetail : (root.printerModel.printActive ? "Temperature profiles are disabled during a print, matching Mainsail." : "")) : ""
                    acceptedButtons: Qt.NoButton
                }
            }
        }
    }
}
