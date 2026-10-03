import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "../../widgets"

// The z-offset nudges: the current offset, the up/down ladders and the
// clear control. The interlock (actionBusy, sectionReason) and the
// G-code offset semantics stay with the model's own command owner; the
// tuning section above keeps the factor sliders.
ColumnLayout {
    id: root
    // The printer model the section passes: every model read inside
    // the nudges goes through this property.
    property var printerModel: null
    Layout.fillWidth: true
    spacing: UM.Theme.getSize("default_margin").height / 2
    RowLayout {
        Layout.fillWidth: true
        UM.Label {
            text: "Z-offset nudges"
            font: UM.Theme.getFont("medium_bold")
            Layout.fillWidth: true
        }
        UM.Label {
            text: root.printerModel != null ? "Current " + root.printerModel.zOffsetText : "Current —"
            font: UM.Theme.getFont("medium_bold")
        }
    }
    Column {
        id: zOffsetGrid
        Layout.fillWidth: true
        spacing: 2 * screenScaleFactor
        property real buttonSpacing: 2 * screenScaleFactor

        // The original two-row layout: all up
        // nudges on the top row, all down on
        // the bottom. Each button takes an
        // exact quarter of the row: fillWidth
        // alone leaves each button its label's
        // implicit width as a base, and the
        // layout shares the leftover in
        // proportion — "↑ 0.005" and "↑ 0.05"
        // came out different widths (a
        // report). A bound preferred
        // width — (row - 3 gaps) / 4 — makes
        // every button the same width without
        // depending on layout distribution.
        RowLayout {
            Layout.fillWidth: true
            spacing: zOffsetGrid.buttonSpacing
            Repeater {
                model: [0.005, 0.01, 0.025, 0.05]
                CentredSecondaryButton {
                    Layout.fillWidth: true
                    Layout.preferredWidth: (zOffsetGrid.width - 3 * zOffsetGrid.buttonSpacing) / 4
                    height: UM.Theme.getSize("action_button").height
                    text: "↑ " + modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                    enabled: root.printerModel != null && !root.printerModel.actionBusy && root.printerModel.sectionReason === ""
                    onClicked: root.printerModel.adjustZOffset(modelData)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Moves the nozzle up, away from the bed."
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: zOffsetGrid.buttonSpacing
            Repeater {
                model: [-0.005, -0.01, -0.025, -0.05]
                CentredSecondaryButton {
                    Layout.fillWidth: true
                    Layout.preferredWidth: (zOffsetGrid.width - 3 * zOffsetGrid.buttonSpacing) / 4
                    height: UM.Theme.getSize("action_button").height
                    text: "↓ " + Math.abs(modelData).toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                    enabled: root.printerModel != null && !root.printerModel.actionBusy && root.printerModel.sectionReason === ""
                    onClicked: root.printerModel.adjustZOffset(modelData)
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Moves the nozzle down, closer to the bed."
                    }
                }
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        CentredSecondaryButton {
            Layout.fillWidth: true
            text: "Clear Z offset"
            enabled: root.printerModel != null && !root.printerModel.actionBusy && root.printerModel.sectionReason === ""
            onClicked: root.printerModel.clearZOffset()
        }
        CentredSecondaryButton {
            objectName: "applyZOffsetButton"
            Layout.fillWidth: true
            text: "Apply Z offset"
            enabled: root.printerModel != null && root.printerModel.canApplyZOffset && !root.printerModel.actionBusy && root.printerModel.sectionReason === ""
            onClicked: root.printerModel.applyZOffset()
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: root.printerModel != null && root.printerModel.zOffsetApplyTarget !== "" ? "Stages the current Z offset for the " + root.printerModel.zOffsetApplyTarget + ". Save configuration after the print to persist it; saving restarts Klipper." : "Apply requires a nonzero offset and an unambiguous Z probe or endstop configuration."
            }
        }
    }
}
