import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.1 as Cura

Component
{
    id: dashboardComponent

    Item
    {
        id: root
        property variant catalog: UM.I18nCatalog { name: "cura" }
        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property var macroParameters: []
        property bool anyPowerLocked:
        {
            if (printer == null || printer.powerDevices == null) return false
            for (var i = 0; i < printer.powerDevices.length; ++i)
            {
                if (printer.powerDevices[i].locked && !printer.powerDevices[i].can_toggle) return true
            }
            return false
        }

        function refreshMacroParameters()
        {
            if (root.printer == null || macroSelector.currentIndex < 0)
            {
                root.macroParameters = []
                return
            }
            root.macroParameters = root.printer.macroParameterDefinitions(macroSelector.currentText)
        }

        function macroArgumentsValid()
        {
            for (var i = 0; i < macroParameterRepeater.count; ++i)
            {
                var item = macroParameterRepeater.itemAt(i)
                if (item != null && item.argumentRequired && item.argumentValue.trim().length === 0)
                {
                    return false
                }
            }
            return true
        }

        function macroArgumentString()
        {
            var args = []
            for (var i = 0; i < macroParameterRepeater.count; ++i)
            {
                var item = macroParameterRepeater.itemAt(i)
                if (item == null) continue
                var value = item.argumentValue.trim()
                if (value.length > 0) args.push(item.argumentName + "=" + value)
            }
            return args.join(" ")
        }

        MoonrakerMonitor
        {
            id: baseMonitorComponent
        }

        Connections
        {
            target: root.printer
            function onControlsChanged() { root.refreshMacroParameters() }
            function onTypedControlsChanged() { root.refreshMacroParameters() }
        }

        RowLayout
        {
            anchors.fill: parent
            spacing: 0

            Loader
            {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 730 * screenScaleFactor
                sourceComponent: baseMonitorComponent
            }

            Cura.RoundedRectangle
            {
                Layout.preferredWidth: 390 * screenScaleFactor
                Layout.minimumWidth: 340 * screenScaleFactor
                Layout.maximumWidth: 460 * screenScaleFactor
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
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: emergencyDock.top
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width
                    anchors.bottomMargin: UM.Theme.getSize("default_margin").height
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
                            UM.Label { text: "Current Z offset"; color: UM.Theme.getColor("text_inactive") }
                            UM.Label { text: root.printer != null ? root.printer.zOffsetText : "—"; Layout.fillWidth: true }
                        }

                        Rectangle { Layout.fillWidth: true; height: UM.Theme.getSize("default_lining").height; color: UM.Theme.getColor("lining") }

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
                            id: macroSection
                            visible: root.printer != null && root.printer.macroNames.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Macros"; font: UM.Theme.getFont("medium_bold") }
                            Cura.ComboBox
                            {
                                id: macroSelector
                                Layout.fillWidth: true
                                model: root.printer != null ? root.printer.macroNames : []
                                onCurrentTextChanged: root.refreshMacroParameters()
                                Component.onCompleted: root.refreshMacroParameters()
                            }

                            Repeater
                            {
                                id: macroParameterRepeater
                                model: root.macroParameters

                                ColumnLayout
                                {
                                    id: argumentRow
                                    Layout.fillWidth: true
                                    property string argumentName: String(modelData.name)
                                    property bool argumentRequired: Boolean(modelData.required)
                                    property string argumentValue: String(modelData.default)
                                    spacing: 2 * screenScaleFactor

                                    UM.Label
                                    {
                                        Layout.fillWidth: true
                                        text: argumentRow.argumentName + "  (" + String(modelData.type) + (argumentRow.argumentRequired ? ", required" : "") + ")"
                                        color: UM.Theme.getColor("text_inactive")
                                    }

                                    Cura.ComboBox
                                    {
                                        id: boolInput
                                        Layout.fillWidth: true
                                        visible: String(modelData.type) === "bool"
                                        model: Boolean(modelData.hasDefault) ? ["Use macro default", "True", "False"] : ["True", "False"]
                                        currentIndex:
                                        {
                                            if (!Boolean(modelData.hasDefault)) return String(modelData.default).toLowerCase() === "false" ? 1 : 0
                                            return 0
                                        }
                                        onCurrentTextChanged:
                                        {
                                            argumentRow.argumentValue = currentText === "Use macro default" ? "" : currentText
                                        }
                                    }

                                    IntValidator { id: integerValidator }
                                    DoubleValidator { id: floatingValidator; notation: DoubleValidator.StandardNotation }

                                    Cura.TextField
                                    {
                                        id: typedInput
                                        Layout.fillWidth: true
                                        visible: String(modelData.type) !== "bool"
                                        text: String(modelData.default)
                                        placeholderText: argumentRow.argumentRequired ? "Required" : "Optional"
                                        selectByMouse: true
                                        validator: String(modelData.type) === "int" ? integerValidator : (String(modelData.type) === "float" ? floatingValidator : null)
                                        onTextChanged: argumentRow.argumentValue = text
                                    }
                                }
                            }

                            UM.Label
                            {
                                visible: root.macroParameters.length === 0
                                text: "This macro has no detectable params.* inputs."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                text: "Run macro"
                                enabled: root.printer != null && macroSelector.currentIndex >= 0 && !root.printer.actionBusy && root.macroArgumentsValid()
                                onClicked: root.printer.runMacro(macroSelector.currentText, root.macroArgumentString())
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.temperaturePresetItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            UM.Label { text: "Temperature profiles"; font: UM.Theme.getFont("medium_bold") }
                            Repeater
                            {
                                model: root.printer != null ? root.printer.temperaturePresetItems : []
                                Cura.SecondaryButton
                                {
                                    Layout.fillWidth: true
                                    text: modelData.active ? "Active — " + modelData.name : modelData.name
                                    enabled: root.printer != null && root.printer.canApplyTemperaturePreset
                                    onClicked: root.printer.applyTemperaturePreset(modelData.index)
                                }
                            }
                            UM.Label
                            {
                                text: "A profile is marked Active only when all of its enabled heater targets match the printer. G-code-only profiles are never assumed active."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
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

                        Rectangle { Layout.fillWidth: true; height: UM.Theme.getSize("default_lining").height; color: UM.Theme.getColor("lining") }
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
                                to: Math.max(200, root.printer != null ? Math.ceil(root.printer.speedFactorPercent * 2) : 200)
                                stepSize: 1
                                value: root.printer != null ? root.printer.speedFactorPercent : 100
                                enabled: root.printer != null
                                onPressedChanged: if (!pressed && root.printer != null) root.printer.setSpeedFactor(Math.round(value))
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
                                to: Math.max(200, root.printer != null ? Math.ceil(root.printer.flowFactorPercent * 2) : 200)
                                stepSize: 1
                                value: root.printer != null ? root.printer.flowFactorPercent : 100
                                enabled: root.printer != null
                                onPressedChanged: if (!pressed && root.printer != null) root.printer.setFlowFactor(Math.round(value))
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
                                UM.Label { text: root.printer != null ? "Current " + root.printer.zOffsetText : "Current —"; font: UM.Theme.getFont("medium_bold") }
                            }
                            Column
                            {
                                id: zOffsetGrid
                                Layout.fillWidth: true
                                spacing: 2 * screenScaleFactor
                                property real buttonSpacing: 2 * screenScaleFactor
                                property real buttonWidth: (width - 3 * buttonSpacing) / 4

                                Row
                                {
                                    width: parent.width
                                    spacing: zOffsetGrid.buttonSpacing
                                    Repeater
                                    {
                                        model: [-0.005, -0.01, -0.025, -0.05]
                                        Cura.SecondaryButton
                                        {
                                            width: zOffsetGrid.buttonWidth
                                            height: UM.Theme.getSize("action_button").height
                                            fixedWidthMode: true
                                            text: modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                            enabled: root.printer != null && !root.printer.actionBusy
                                            onClicked: root.printer.adjustZOffset(modelData)
                                        }
                                    }
                                }
                                Row
                                {
                                    width: parent.width
                                    spacing: zOffsetGrid.buttonSpacing
                                    Repeater
                                    {
                                        model: [0.005, 0.01, 0.025, 0.05]
                                        Cura.SecondaryButton
                                        {
                                            width: zOffsetGrid.buttonWidth
                                            height: UM.Theme.getSize("action_button").height
                                            fixedWidthMode: true
                                            text: "+" + modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                            enabled: root.printer != null && !root.printer.actionBusy
                                            onClicked: root.printer.adjustZOffset(modelData)
                                        }
                                    }
                                }
                            }
                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                text: "Clear Z offset"
                                enabled: root.printer != null && !root.printer.actionBusy
                                onClicked: root.printer.clearZOffset()
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.fanControlItems.length > 0
                            Layout.fillWidth: true
                            UM.Label { text: "Fan speed"; font: UM.Theme.getFont("medium_bold") }
                            Repeater
                            {
                                model: root.printer != null ? root.printer.fanControlItems : []
                                ColumnLayout
                                {
                                    Layout.fillWidth: true
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
                                        from: 0; to: 100; stepSize: 1
                                        value: modelData.percent
                                        onPressedChanged: if (!pressed && root.printer != null) root.printer.setFanSpeed(modelData.object, Math.round(value))
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.ledItems.length > 0
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("thin_margin").height
                            UM.Label { text: "LEDs"; font: UM.Theme.getFont("medium_bold") }
                            Repeater
                            {
                                model: root.printer != null ? root.printer.ledItems : []
                                ColumnLayout
                                {
                                    Layout.fillWidth: true
                                    spacing: UM.Theme.getSize("thin_margin").height

                                    function applyLedColour()
                                    {
                                        if (root.printer != null)
                                        {
                                            root.printer.setLedColor(
                                                modelData.object,
                                                Math.round(redSlider.value),
                                                Math.round(greenSlider.value),
                                                Math.round(blueSlider.value),
                                                modelData.hasWhite ? Math.round(whiteSlider.value) : 0,
                                                Math.round(ledSlider.value)
                                            )
                                        }
                                    }

                                    RowLayout
                                    {
                                        Layout.fillWidth: true
                                        UM.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                                        UM.Label { text: "Brightness " + Math.round(ledSlider.value) + "%" }
                                    }
                                    Slider
                                    {
                                        id: ledSlider
                                        Layout.fillWidth: true
                                        from: 0; to: 100; stepSize: 1
                                        value: modelData.percent
                                        onPressedChanged: if (!pressed && root.printer != null) root.printer.setLedBrightness(modelData.object, Math.round(value))
                                    }

                                    GridLayout
                                    {
                                        columns: 3
                                        Layout.fillWidth: true
                                        columnSpacing: UM.Theme.getSize("thin_margin").width
                                        rowSpacing: UM.Theme.getSize("thin_margin").height

                                        UM.Label { text: "R"; color: UM.Theme.getColor("text_inactive") }
                                        Slider { id: redSlider; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.redPercent; onPressedChanged: if (!pressed) applyLedColour() }
                                        UM.Label { text: Math.round(redSlider.value) + "%" }

                                        UM.Label { text: "G"; color: UM.Theme.getColor("text_inactive") }
                                        Slider { id: greenSlider; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.greenPercent; onPressedChanged: if (!pressed) applyLedColour() }
                                        UM.Label { text: Math.round(greenSlider.value) + "%" }

                                        UM.Label { text: "B"; color: UM.Theme.getColor("text_inactive") }
                                        Slider { id: blueSlider; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.bluePercent; onPressedChanged: if (!pressed) applyLedColour() }
                                        UM.Label { text: Math.round(blueSlider.value) + "%" }

                                        UM.Label { visible: modelData.hasWhite; text: "W"; color: UM.Theme.getColor("text_inactive") }
                                        Slider { id: whiteSlider; visible: modelData.hasWhite; Layout.fillWidth: true; from: 0; to: 100; stepSize: 1; value: modelData.whitePercent; onPressedChanged: if (!pressed) applyLedColour() }
                                        UM.Label { visible: modelData.hasWhite; text: Math.round(whiteSlider.value) + "%" }
                                    }
                                }
                            }
                        }

                        ColumnLayout
                        {
                            visible: root.printer != null && root.printer.pwmOutputItems.length > 0
                            Layout.fillWidth: true
                            UM.Label { text: "PWM outputs"; font: UM.Theme.getFont("medium_bold") }
                            Repeater
                            {
                                model: root.printer != null ? root.printer.pwmOutputItems : []
                                ColumnLayout
                                {
                                    Layout.fillWidth: true
                                    RowLayout
                                    {
                                        Layout.fillWidth: true
                                        UM.Label { text: modelData.name; Layout.fillWidth: true; elide: Text.ElideRight }
                                        UM.Label { text: Math.round(pwmSlider.value) + "%" }
                                    }
                                    Slider
                                    {
                                        id: pwmSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        value: modelData.percent
                                        onPressedChanged:
                                        {
                                            if (!pressed && root.printer != null)
                                            {
                                                root.printer.setPwmOutput(modelData.object, Math.round(value))
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
                            Rectangle { Layout.fillWidth: true; height: UM.Theme.getSize("default_lining").height; color: UM.Theme.getColor("lining") }
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

                    }
                }

                Column
                {
                    id: emergencyDock
                    z: 20
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width
                    anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                    spacing: UM.Theme.getSize("thin_margin").height

                    Rectangle
                    {
                        width: parent.width
                        height: UM.Theme.getSize("default_lining").height
                        color: UM.Theme.getColor("lining")
                    }
                    UM.Label
                    {
                        width: parent.width
                        text: "Emergency"
                        font: UM.Theme.getFont("medium_bold")
                    }
                    Item
                    {
                        id: emergencyButton
                        width: parent.width
                        height: 46 * screenScaleFactor
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
                                color: "black"
                            }
                            MouseArea
                            {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: if (root.printer != null) root.printer.emergencyStopClick()
                            }
                        }
                    }
                    UM.Label
                    {
                        width: parent.width
                        text: "Each click must be within 1 second of the previous click. The third click stops the printer immediately."
                        color: UM.Theme.getColor("text_inactive")
                        font: UM.Theme.getFont("default")
                        wrapMode: Text.WordWrap
                    }
                }

            }
        }
    }
}
