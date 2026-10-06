import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: base
    required property var settings
    property bool validZTolerance: settings.validZTolerance(zToleranceField.text)
    readonly property bool validToolhead: !settings.toolheadModel || settings.toolheadModel.valid
    readonly property var values: ({
            "enabled": enabledBox.checked,
            "follow_mode": followMode(),
            "moonraker_layer_is_one_based": oneBasedBox.checked,
            "path_follow": pathFollowBox.checked,
            "eta_learn": etaLearnBox.checked,
            "auto_preview": autoPreviewBox.checked,
            "show_toolhead_indicator": toolheadIndicatorBox.checked,
            "z_fallback": zFallbackBox.checked,
            "z_tolerance": zToleranceField.text
        })

    function followMode() {
        if (completedMode.checked)
            return "completed";
        if (lookAheadMode.checked)
            return "lookahead";
        if (windowMode.checked)
            return "window";
        return "exact";
    }
    property bool toolheadScrollPending: false
    function showToolhead() {
        toolheadScrollPending = true;
        positionToolhead();
    }
    function positionToolhead() {
        if (!toolheadScrollPending || followingScroll.height <= 0 || toolheadSettings.y <= 0 || toolheadSettings.height <= 0 || followingScroll.contentHeight < toolheadSettings.y + toolheadSettings.height)
            return;
        followingScroll.contentY = Math.max(0, Math.min(toolheadSettings.y, followingScroll.contentHeight - followingScroll.height));
        toolheadScrollPending = false;
    }
    Flickable {
        id: followingScroll
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        contentWidth: width
        contentHeight: followingColumn.implicitHeight
        onContentHeightChanged: base.positionToolhead()
        onHeightChanged: base.positionToolhead()
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            objectName: "settingsScrollbar"
        }

        Column {
            id: followingColumn
            width: Math.max(0, parent.width - UM.Theme.getSize("scrollbar").width - 4 * screenScaleFactor)
            spacing: UM.Theme.getSize("default_margin").height

            UM.CheckBox {
                id: enabledBox
                text: "Enable automatic following for this printer"
                checked: settings.settingsEnabled
            }

            UM.Label {
                text: "Follow mode"
                font: UM.Theme.getFont("medium_bold")
            }
            ButtonGroup {
                id: followModeGroup
            }
            Cura.RadioButton {
                id: exactMode
                ButtonGroup.group: followModeGroup
                text: "Exact current layer"
                checked: settings.settingsFollowMode === "exact"
            }
            Cura.RadioButton {
                id: completedMode
                ButtonGroup.group: followModeGroup
                text: "Last completed layer"
                checked: settings.settingsFollowMode === "completed"
            }
            Cura.RadioButton {
                id: lookAheadMode
                ButtonGroup.group: followModeGroup
                text: "Look ahead one layer"
                checked: settings.settingsFollowMode === "lookahead"
            }
            Cura.RadioButton {
                id: windowMode
                ButtonGroup.group: followModeGroup
                text: "Window around current layer (±2)"
                checked: settings.settingsFollowMode === "window"
            }

            UM.CheckBox {
                id: pathFollowBox
                text: "Follow progress through each layer"
                checked: settings.settingsPathFollow
            }

            UM.CheckBox {
                id: etaLearnBox
                text: "Learn ETA drift from observed progress"
                checked: settings.settingsEtaLearn
            }
            UM.Label {
                // A plain caption, not UM.TooltipArea: the
                // tooltip wrapper's sizing in the settings
                // column collapsed this row onto the first
                // checkbox (the live report's overlap).
                text: "Rescales the remaining time by the drift between the slicer's per-layer times and what the printer actually took. Downloads nothing."
                font: UM.Theme.getFont("default")
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
                width: parent.width
            }
            UM.CheckBox {
                id: oneBasedBox
                text: "Treat Moonraker current_layer as 1-based when G-code mapping is unavailable"
                checked: settings.settingsLayerOneBased
            }
            UM.CheckBox {
                id: autoPreviewBox
                text: "Switch to Preview once when a print starts"
                checked: settings.settingsAutoPreview
            }
            UM.CheckBox {
                id: toolheadIndicatorBox
                text: "Show live printhead indicator"
                checked: settings.settingsToolheadIndicator
            }
            UM.CheckBox {
                id: zFallbackBox
                text: "Use Z-height fallback when current_layer is unavailable"
                checked: settings.settingsZFallback
            }
            UM.Label {
                text: "Z-height match tolerance (mm)"
                enabled: zFallbackBox.checked
            }
            Cura.TextField {
                id: zToleranceField
                width: parent.width
                text: settings.settingsZTolerance
                enabled: zFallbackBox.checked
                maximumLength: 16
                onTextChanged: base.validZTolerance = settings.validZTolerance(text)
            }
            UM.Label {
                visible: !base.validZTolerance
                text: "Z-height tolerance must be between 0.005 and 0.250 mm."
                color: UM.Theme.getColor("error")
                font: UM.Theme.getFont("default_italic")
            }
            ToolheadModelSettings {
                id: toolheadSettings
                onYChanged: base.positionToolhead()
                onHeightChanged: base.positionToolhead()
                width: parent.width
                model: settings.toolheadModel
            }
        }
    }
}
