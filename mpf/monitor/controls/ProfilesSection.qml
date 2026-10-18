import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../widgets"

// Heater targets and saved temperature profiles.
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

    readonly property int temperatureStep: [1, 5, 10, 25][stepPicker.currentIndex]
    property bool adjustingHeater: false
    property var heldHeaters: []
    function holdHeaters() {
        heldHeaters = printerModel.heaterControlItems;
        adjustingHeater = true;
    }
    Timer {
        id: releaseHeaters
        interval: 0
        onTriggered: root.adjustingHeater = false
    }
    CollapsibleSectionHeader {
        id: sectionHeader
        width: parent.width
        printerModel: root.printerModel
        title: "Temperatures"
        sectionId: "profiles"
        sectionIcon: "PrintQuality"
    }
    ColumnLayout {
        id: sectionBody
        x: root.sideMargin
        y: sectionHeader.height + root.verticalMargin
        width: Math.max(0, root.width - 2 * root.sideMargin)
        visible: root.printerModel != null && root.printerModel.sectionExpandedMap["profiles"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height / 2

        RowLayout {
            Layout.fillWidth: true
            UM.Label {
                text: "± step"
                Layout.fillWidth: true
            }
            ComboBox {
                id: stepPicker
                objectName: "heaterStepPicker"
                model: ["1 °C", "5 °C", "10 °C", "25 °C"]
                currentIndex: 0
            }
        }
        Repeater {
            model: root.adjustingHeater ? root.heldHeaters : (root.printerModel != null && root.printerModel.heaterControlItems !== undefined ? root.printerModel.heaterControlItems : [])
            ColumnLayout {
                Layout.fillWidth: true
                enabled: root.printerModel != null && root.printerModel.canSetHeaterTemperature === true
                RowLayout {
                    Layout.fillWidth: true
                    UM.Label {
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: modelData.name
                    }
                    UM.Label {
                        text: heaterSlider.selectedValue() === 0 ? "Off" : Math.max(modelData.minimum, heaterSlider.selectedValue()) + " °C"
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Cura.SecondaryButton {
                        objectName: "heaterMinus_" + modelData.object
                        text: "−"
                        enabled: modelData.target > 0
                        onClicked: root.printerModel.setHeaterTemperature(modelData.object, modelData.target <= modelData.minimum ? 0 : Math.max(modelData.minimum, modelData.target - root.temperatureStep))
                    }
                    OutlineSlider {
                        id: heaterSlider
                        objectName: "heaterSlider_" + modelData.object
                        Layout.fillWidth: true
                        from: 0
                        to: modelData.maximum
                        stepSize: 1
                        live: false
                        value: modelData.target
                        onInteractingChanged: {
                            if (interacting) {
                                releaseHeaters.stop();
                                root.holdHeaters();
                            } else {
                                releaseHeaters.start();
                            }
                        }
                        onValueCommitted: root.printerModel.setHeaterTemperature(modelData.object, value === 0 ? 0 : Math.max(modelData.minimum, value))
                    }
                    Cura.SecondaryButton {
                        objectName: "heaterPlus_" + modelData.object
                        text: "+"
                        enabled: modelData.target < modelData.maximum
                        onClicked: root.printerModel.setHeaterTemperature(modelData.object, Math.min(modelData.maximum, Math.max(modelData.minimum, modelData.target + root.temperatureStep)))
                    }
                    Cura.SecondaryButton {
                        objectName: "heaterOff_" + modelData.object
                        text: "Off"
                        onClicked: root.printerModel.setHeaterTemperature(modelData.object, 0)
                    }
                }
                UM.Label {
                    Layout.fillWidth: true
                    text: "Off or " + modelData.minimum + "–" + modelData.maximum + " °C"
                    color: UM.Theme.getColor("text_inactive")
                }
            }
        }
        UM.Label {
            visible: root.printerModel != null && root.printerModel.temperaturePresetItems.length > 0
            text: "Profiles"
            color: UM.Theme.getColor("text_inactive")
        }
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
            visible: root.printerModel != null && root.printerModel.temperaturePresetItems.length > 0
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: root.printerModel != null && root.printerModel.canApplyTemperaturePreset ? "Select a saved temperature profile." : "Connect to the printer and unlock controls to select a profile."
            color: UM.Theme.getColor("text_inactive")
        }
    }
}
