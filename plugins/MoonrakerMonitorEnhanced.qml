import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.1 as Cura

Component
{
    id: enhancedMonitorComponent

    Item
    {
        id: root
        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property bool anyPowerLocked:
        {
            if (printer == null || printer.powerDevices == null)
            {
                return false
            }
            for (var i = 0; i < printer.powerDevices.length; ++i)
            {
                if (printer.powerDevices[i].locked && !printer.powerDevices[i].can_toggle)
                {
                    return true
                }
            }
            return false
        }

        MoonrakerMonitor
        {
            id: baseMonitorComponent
        }

        RowLayout
        {
            anchors.fill: parent
            spacing: 0

            Loader
            {
                id: baseMonitorLoader
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 730 * screenScaleFactor
                sourceComponent: baseMonitorComponent
            }

            Cura.RoundedRectangle
            {
                id: controlsPanel
                Layout.preferredWidth: 380 * screenScaleFactor
                Layout.minimumWidth: 330 * screenScaleFactor
                Layout.maximumWidth: 440 * screenScaleFactor
                Layout.fillHeight: true
                Layout.margins: UM.Theme.getSize("default_margin").width
                Layout.leftMargin: 0
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

                Flickable
                {
                    id: controlFlick
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    clip: true
                    contentWidth: width
                    contentHeight: controlContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: UM.ScrollBar { id: controlScrollbar }

                    ColumnLayout
                    {
                        id: controlContent
                        width: controlFlick.width - controlScrollbar.width - UM.Theme.getSize("default_margin").width
                        spacing: UM.Theme.getSize("default_margin").height

                        UM.Label
                        {
                            text: "Printer controls"
                            font: UM.Theme.getFont("large_bold")
                            Layout.fillWidth: true
                        }

                        GridLayout
                        {
                            columns: 2
                            Layout.fillWidth: true
                            columnSpacing: UM.Theme.getSize("default_margin").width
                            rowSpacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Layer height"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.monitorLayerHeight : "—"; Layout.fillWidth: true }
                            UM.Label { text: "Z offset"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.zOffsetText : "—"; Layout.fillWidth: true }
                        }

                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: UM.Theme.getSize("default_lining").height
                            color: UM.Theme.getColor("lining")
                        }

                        UM.Label { text: "Setup"; font: UM.Theme.getFont("medium_bold") }

                        RowLayout
                        {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").width / 2

                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                text: "Home"
                                enabled: root.printer != null && root.printer.canRunSetup
                                onClicked: root.printer.homeAll()
                            }
                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.hasQuadGantryLevel
                                text: "QGL"
                                enabled: root.printer != null && root.printer.canRunSetup
                                onClicked: root.printer.runQuadGantryLevel()
                            }
                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.hasBedMesh
                                text: "Bed mesh"
                                enabled: root.printer != null && root.printer.canRunSetup
                                onClicked: root.printer.calibrateBedMesh()
                            }
                        }

                        UM.Label
                        {
                            visible: root.printer != null && root.printer.printActive
                            text: "Homing and calibration controls are disabled during a print."
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.macroNames.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Macros"; font: UM.Theme.getFont("medium_bold") }
                            Cura.ComboBox
                            {
                                id: macroSelector
                                Layout.fillWidth: true
                                model: root.printer != null ? root.printer.macroNames : []
                            }
                            TextField
                            {
                                id: macroArguments
                                Layout.fillWidth: true
                                placeholderText: "Arguments (optional)"
                                selectByMouse: true
                            }
                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                text: "Run macro"
                                enabled: root.printer != null && macroSelector.currentIndex >= 0 && !root.printer.actionBusy
                                onClicked: root.printer.runMacro(macroSelector.currentText, macroArguments.text)
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.temperaturePresetNames.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Temperature profile"; font: UM.Theme.getFont("medium_bold") }
                            RowLayout
                            {
                                Layout.fillWidth: true
                                Cura.ComboBox
                                {
                                    id: temperaturePresetSelector
                                    Layout.fillWidth: true
                                    model: root.printer != null ? root.printer.temperaturePresetNames : []
                                }
                                Cura.SecondaryButton
                                {
                                    text: "Apply"
                                    enabled: root.printer != null && temperaturePresetSelector.currentIndex >= 0 && root.printer.canApplyTemperaturePreset
                                    onClicked: root.printer.applyTemperaturePreset(temperaturePresetSelector.currentIndex)
                                }
                            }
                            UM.Label
                            {
                                visible: root.printer != null && root.printer.printActive
                                text: "Temperature profiles are disabled during a print, matching Mainsail."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: UM.Theme.getSize("default_lining").height
                            color: UM.Theme.getColor("lining")
                        }

                        UM.Label { text: "Live tuning"; font: UM.Theme.getFont("medium_bold") }

                        ColumnLayout
                        {
                            Layout.fillWidth: true
                            spacing: 0
                            RowLayout
                            {
                                Layout.fillWidth: true
                                UM.Label { text: "Speed factor"; Layout.fillWidth: true }
                                UM.Label { text: Math.round(speedSlider.value) + "%" }
                            }
                            Slider
                            {
                                id: speedSlider
                                Layout.fillWidth: true
                                from: 10
                                to: 200
                                stepSize: 1
                                value: root.printer != null ? root.printer.speedFactorPercent : 100
                                enabled: root.printer != null
                                onPressedChanged:
                                {
                                    if (!pressed && root.printer != null)
                                    {
                                        root.printer.setSpeedFactor(Math.round(value))
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            Layout.fillWidth: true
                            spacing: 0
                            RowLayout
                            {
                                Layout.fillWidth: true
                                UM.Label { text: "Extrusion multiplier"; Layout.fillWidth: true }
                                UM.Label { text: Math.round(flowSlider.value) + "%" }
                            }
                            Slider
                            {
                                id: flowSlider
                                Layout.fillWidth: true
                                from: 50
                                to: 150
                                stepSize: 1
                                value: root.printer != null ? root.printer.flowFactorPercent : 100
                                enabled: root.printer != null
                                onPressedChanged:
                                {
                                    if (!pressed && root.printer != null)
                                    {
                                        root.printer.setFlowFactor(Math.round(value))
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2
                            RowLayout
                            {
                                Layout.fillWidth: true
                                UM.Label { text: "Z-offset nudges"; font: UM.Theme.getFont("medium_bold"); Layout.fillWidth: true }
                                Cura.SecondaryButton
                                {
                                    text: "Clear"
                                    enabled: root.printer != null && !root.printer.actionBusy
                                    onClicked: root.printer.clearZOffset()
                                }
                            }
                            RowLayout
                            {
                                Layout.fillWidth: true
                                spacing: 2 * screenScaleFactor
                                Repeater
                                {
                                    model: [-0.05, -0.025, -0.01, -0.005]
                                    Cura.SecondaryButton
                                    {
                                        Layout.fillWidth: true
                                        text: modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                        enabled: root.printer != null && !root.printer.actionBusy
                                        onClicked: root.printer.adjustZOffset(modelData)
                                    }
                                }
                            }
                            RowLayout
                            {
                                Layout.fillWidth: true
                                spacing: 2 * screenScaleFactor
                                Repeater
                                {
                                    model: [0.005, 0.01, 0.025, 0.05]
                                    Cura.SecondaryButton
                                    {
                                        Layout.fillWidth: true
                                        text: "+" + modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                        enabled: root.printer != null && !root.printer.actionBusy
                                        onClicked: root.printer.adjustZOffset(modelData)
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.fanControlItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2
                            UM.Label { text: "Fan speed"; font: UM.Theme.getFont("medium_bold") }
                            Repeater
                            {
                                model: root.printer != null ? root.printer.fanControlItems : []
                                ColumnLayout
                                {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    RowLayout
                                    {
                                        Layout.fillWidth: true
                                        UM.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                                        UM.Label { text: Math.round(fanSlider.value) + "%" }
                                    }
                                    Slider
                                    {
                                        id: fanSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        value: modelData.percent
                                        onPressedChanged:
                                        {
                                            if (!pressed && root.printer != null)
                                            {
                                                root.printer.setFanSpeed(modelData.object, Math.round(value))
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.ledItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2
                            UM.Label { text: "LED brightness"; font: UM.Theme.getFont("medium_bold") }
                            Repeater
                            {
                                model: root.printer != null ? root.printer.ledItems : []
                                ColumnLayout
                                {
                                    Layout.fillWidth: true
                                    spacing: 0
                                    RowLayout
                                    {
                                        Layout.fillWidth: true
                                        UM.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                                        UM.Label { text: Math.round(ledSlider.value) + "%" }
                                    }
                                    Slider
                                    {
                                        id: ledSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        value: modelData.percent
                                        onPressedChanged:
                                        {
                                            if (!pressed && root.printer != null)
                                            {
                                                root.printer.setLedBrightness(modelData.object, Math.round(value))
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        UM.Label
                        {
                            visible: root.anyPowerLocked
                            text: "Power control is locked by Moonraker while this print is active."
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.saveConfigPending
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2
                            Rectangle
                            {
                                Layout.fillWidth: true
                                height: UM.Theme.getSize("default_lining").height
                                color: UM.Theme.getColor("lining")
                            }
                            UM.Label { text: "Configuration changes"; font: UM.Theme.getFont("medium_bold") }
                            UM.Label
                            {
                                text: root.printer != null ? root.printer.saveConfigSummary : ""
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            Cura.PrimaryButton
                            {
                                Layout.fillWidth: true
                                text: "Save configuration"
                                enabled: root.printer != null && root.printer.canSaveConfig
                                onClicked: root.printer.saveConfig()
                            }
                            UM.Label
                            {
                                text: root.printer != null && root.printer.printActive ? "SAVE_CONFIG is disabled during a print." : "Saving configuration restarts Klipper."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        Rectangle
                        {
                            Layout.fillWidth: true
                            height: UM.Theme.getSize("default_lining").height
                            color: UM.Theme.getColor("lining")
                        }

                        UM.Label { text: "Emergency"; font: UM.Theme.getFont("medium_bold") }

                        Item
                        {
                            id: emergencyButton
                            Layout.fillWidth: true
                            Layout.preferredHeight: 46 * screenScaleFactor
                            property int clicks: root.printer != null ? root.printer.emergencyStopClicks : 0

                            Rectangle
                            {
                                anchors.fill: parent
                                radius: UM.Theme.getSize("default_radius").width
                                color: "transparent"
                                border.color: "#d32f2f"
                                border.width: 2 * screenScaleFactor
                                clip: true

                                Rectangle
                                {
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    width: parent.width * Math.min(1.0, emergencyButton.clicks / 3.0)
                                    color: "#d32f2f"
                                }
                                UM.Label
                                {
                                    anchors.centerIn: parent
                                    text: emergencyButton.clicks === 0 ? "EMERGENCY STOP — click 3 times" : "EMERGENCY STOP — " + emergencyButton.clicks + "/3"
                                    font: UM.Theme.getFont("medium_bold")
                                    color: emergencyButton.clicks >= 2 ? "white" : UM.Theme.getColor("text")
                                }
                                MouseArea
                                {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked:
                                    {
                                        if (root.printer != null)
                                        {
                                            root.printer.emergencyStopClick()
                                        }
                                    }
                                }
                            }
                        }

                        UM.Label
                        {
                            text: "Each click must be within 1 second of the previous click. The third click stops the printer immediately."
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }
}
