import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.1 as Cura

Cura.MachineAction
{
    id: base
    anchors.fill: parent

    property bool validUrl: manager.validUrl(urlField.text)
    property bool validPollInterval: manager.validPollInterval(pollIntervalField.text)
    property bool validZTolerance: manager.validZTolerance(zToleranceField.text)
    property bool canSave: validPollInterval && validZTolerance && (!enabledBox.checked || validUrl)

    function followMode()
    {
        if (completedMode.checked) return "completed"
        if (lookAheadMode.checked) return "lookahead"
        if (windowMode.checked) return "window"
        return "exact"
    }

    function save(closeDialog)
    {
        if (!base.canSave)
        {
            return
        }
        var saved = manager.saveConfig({
            enabled: enabledBox.checked,
            url: urlField.text,
            api_key: apiKeyField.text,
            poll_interval_ms: pollIntervalField.text,
            follow_mode: followMode(),
            moonraker_layer_is_one_based: oneBasedBox.checked,
            path_follow: pathFollowBox.checked,
            auto_preview: autoPreviewBox.checked,
            show_toolhead_indicator: toolheadIndicatorBox.checked,
            z_fallback: zFallbackBox.checked,
            z_tolerance: zToleranceField.text
        })
        if (saved && closeDialog)
        {
            actionDialog.close()
        }
    }

    function cancel(closeDialog)
    {
        manager.cancelTest()
        if (closeDialog)
        {
            actionDialog.close()
        }
    }

    Connections
    {
        target: actionDialog
        function onAccepted() { base.save(false) }
        function onRejected() { base.cancel(false) }
        function onClosing() { manager.cancelTest() }
    }

    UM.Label
    {
        id: machineLabel
        anchors
        {
            top: parent.top
            left: parent.left
            leftMargin: UM.Theme.getSize("default_margin").width
        }
        font: UM.Theme.getFont("large_bold")
        text: manager.machineName
    }

    UM.TabRow
    {
        id: tabBar
        z: 5
        anchors
        {
            top: machineLabel.bottom
            topMargin: UM.Theme.getSize("default_margin").height
        }
        width: parent.width

        UM.TabRowButton
        {
            checked: true
            text: "Connection"
        }
        UM.TabRowButton
        {
            text: "Following"
        }
    }

    Cura.RoundedRectangle
    {
        id: tabView
        anchors
        {
            top: tabBar.bottom
            topMargin: -UM.Theme.getSize("default_lining").height
            bottom: actionButtons.top
            bottomMargin: UM.Theme.getSize("default_margin").height
            left: parent.left
            right: parent.right
        }
        border
        {
            color: UM.Theme.getColor("lining")
            width: UM.Theme.getSize("default_lining").width
        }
        color: UM.Theme.getColor("main_background")
        radius: UM.Theme.getSize("default_radius").width
        cornerSide: Cura.RoundedRectangle.Direction.Down

        StackLayout
        {
            anchors.fill: parent
            currentIndex: tabBar.currentIndex

            Item
            {
                Flickable
                {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    contentWidth: width
                    contentHeight: connectionColumn.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    Column
                    {
                        id: connectionColumn
                        width: parent.width
                        spacing: UM.Theme.getSize("default_margin").height

                        UM.CheckBox
                        {
                            id: enabledBox
                            text: "Enable automatic following for this printer"
                            checked: manager.settingsEnabled
                        }

                        UM.Label { text: "Moonraker URL" }
                        Cura.TextField
                        {
                            id: urlField
                            width: parent.width
                            text: manager.settingsUrl
                            placeholderText: "http://printer.example.invalid:7125"
                            maximumLength: 1024
                            onTextChanged: base.validUrl = manager.validUrl(text)
                        }
                        UM.Label
                        {
                            visible: enabledBox.checked && !base.validUrl
                            text: "Enter a valid HTTP or HTTPS Moonraker URL."
                            color: UM.Theme.getColor("error")
                            font: UM.Theme.getFont("default_italic")
                        }

                        UM.Label { text: "API key (optional)" }
                        Cura.TextField
                        {
                            id: apiKeyField
                            width: parent.width
                            text: manager.settingsApiKey
                            echoMode: TextInput.Password
                            maximumLength: 4096
                        }

                        UM.Label { text: "Polling interval (milliseconds)" }
                        Cura.TextField
                        {
                            id: pollIntervalField
                            width: parent.width
                            text: manager.settingsPollInterval
                            maximumLength: 12
                            onTextChanged: base.validPollInterval = manager.validPollInterval(text)
                        }
                        UM.Label
                        {
                            visible: !base.validPollInterval
                            text: "Polling interval must be a positive whole number."
                            color: UM.Theme.getColor("error")
                            font: UM.Theme.getFont("default_italic")
                        }

                        RowLayout
                        {
                            width: parent.width
                            spacing: UM.Theme.getSize("default_margin").width

                            Cura.SecondaryButton
                            {
                                text: manager.testBusy ? "Testing…" : "Test connection"
                                enabled: !manager.testBusy && base.validUrl
                                onClicked: manager.testConnection(urlField.text, apiKeyField.text)
                            }

                            UM.Label
                            {
                                Layout.fillWidth: true
                                text: manager.testStatus
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }

            Item
            {
                Flickable
                {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("default_margin").width
                    contentWidth: width
                    contentHeight: followingColumn.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    Column
                    {
                        id: followingColumn
                        width: parent.width
                        spacing: UM.Theme.getSize("default_margin").height

                        UM.Label
                        {
                            text: "Follow mode"
                            font: UM.Theme.getFont("medium_bold")
                        }

                        ButtonGroup { id: followModeGroup }

                        Cura.RadioButton
                        {
                            id: exactMode
                            ButtonGroup.group: followModeGroup
                            text: "Exact current layer"
                            checked: manager.settingsFollowMode === "exact"
                        }
                        Cura.RadioButton
                        {
                            id: completedMode
                            ButtonGroup.group: followModeGroup
                            text: "Last completed layer"
                            checked: manager.settingsFollowMode === "completed"
                        }
                        Cura.RadioButton
                        {
                            id: lookAheadMode
                            ButtonGroup.group: followModeGroup
                            text: "Look ahead one layer"
                            checked: manager.settingsFollowMode === "lookahead"
                        }
                        Cura.RadioButton
                        {
                            id: windowMode
                            ButtonGroup.group: followModeGroup
                            text: "Window around current layer (±2)"
                            checked: manager.settingsFollowMode === "window"
                        }

                        Item { width: 1; height: UM.Theme.getSize("default_margin").height / 2 }

                        UM.CheckBox
                        {
                            id: pathFollowBox
                            text: "Follow progress through each layer"
                            checked: manager.settingsPathFollow
                        }
                        UM.CheckBox
                        {
                            id: oneBasedBox
                            text: "Treat Moonraker current_layer as 1-based when G-code mapping is unavailable"
                            checked: manager.settingsLayerOneBased
                        }
                        UM.CheckBox
                        {
                            id: autoPreviewBox
                            text: "Switch to Preview once when a print starts"
                            checked: manager.settingsAutoPreview
                        }
                        UM.CheckBox
                        {
                            id: toolheadIndicatorBox
                            text: "Show live printhead indicator"
                            checked: manager.settingsToolheadIndicator
                        }
                        UM.Label
                        {
                            text: "Keeps Cura's native nozzle visible at the followed path when Cura would otherwise suppress it during live layer changes."
                            wrapMode: Text.WordWrap
                            width: parent.width
                            color: UM.Theme.getColor("text_inactive")
                        }
                        UM.CheckBox
                        {
                            id: zFallbackBox
                            text: "Use Z-height fallback when current_layer is unavailable"
                            checked: manager.settingsZFallback
                        }

                        UM.Label
                        {
                            text: "Z-height match tolerance (mm)"
                            enabled: zFallbackBox.checked
                        }
                        Cura.TextField
                        {
                            id: zToleranceField
                            width: parent.width
                            text: manager.settingsZTolerance
                            enabled: zFallbackBox.checked
                            maximumLength: 16
                            onTextChanged: base.validZTolerance = manager.validZTolerance(text)
                        }
                        UM.Label
                        {
                            visible: !base.validZTolerance
                            text: "Z-height tolerance must be between 0.005 and 0.250 mm."
                            color: UM.Theme.getColor("error")
                            font: UM.Theme.getFont("default_italic")
                        }
                    }
                }
            }
        }
    }

    Item
    {
        id: actionButtons
        anchors
        {
            bottom: parent.bottom
            left: parent.left
            right: parent.right
        }
        height: Math.max(saveButton.implicitHeight, cancelButton.implicitHeight)

        Flow
        {
            anchors.fill: parent
            layoutDirection: Qt.RightToLeft
            spacing: UM.Theme.getSize("default_margin").width

            Cura.SecondaryButton
            {
                id: cancelButton
                text: "Cancel"
                onClicked: base.cancel(true)
            }

            Cura.PrimaryButton
            {
                id: saveButton
                text: "Save"
                enabled: base.canSave
                onClicked: base.save(true)
            }
        }
    }
}
