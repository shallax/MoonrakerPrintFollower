import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Live-tuning section (4.3.0 extraction): the factor sliders and
// the z-offset nudges moved out of the dashboard as one
// property-driven component. The sliders report interaction
// through the host sink, so the freeze machinery stays single-owner.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    property var interactionSink: null
    // The factor sliders sync from the model IMPERATIVELY: their own
    // drag writes would destroy a value binding, freezing the handle
    // at the last interaction (the live 200-reset find). The arrival
    // path covers a model that attaches after the section completes.
    onPrinterModelChanged: {
        if (root.printerModel != null) {
            speedSlider.value = root.printerModel.speedFactorPercent !== undefined ? root.printerModel.speedFactorPercent : 100;
            flowSlider.value = root.printerModel.flowFactorPercent !== undefined ? root.printerModel.flowFactorPercent : 100;
        }
    }

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Live tuning"
        sectionId: "tuning"
        sectionIcon: "Sliders"
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["tuning"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height
        UM.Label {
            text: "Drag to preview a value. After release, the latest value is applied once it has been unchanged for 250 ms."
            color: UM.Theme.getColor("text_inactive")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                UM.Label {
                    text: "Speed factor"
                    Layout.fillWidth: true
                }
                UM.Label {
                    // The fixed width keeps the row from
                    // reflowing as the percentage changes
                    // (a live report).
                    width: 52 * screenScaleFactor
                    horizontalAlignment: Text.AlignRight
                    text: speedSlider.selectedValue() + "%"
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").width
                OutlineSlider {
                    id: speedSlider
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    from: 10
                    to: Math.max(200, root.printerModel != null ? Math.ceil(root.printerModel.speedFactorPercent * 2) : 200)
                    stepSize: 1
                    live: false
                    value: 100
                    enabled: root.printerModel != null
                    // The model's percent syncs IMPERATIVELY, never by
                    // binding: the slider's own drag writes control.value
                    // and would destroy a binding, freezing the handle at
                    // the last interaction forever (the live 200-reset
                    // find — the command applied, the UI could not
                    // follow). The change handler only fires when the
                    // percent CHANGES, so a mid-drag poll never snaps.
                    Component.onCompleted: {
                        if (root.printerModel != null)
                            speedSlider.value = root.printerModel.speedFactorPercent;
                    }
                    Connections {
                        target: root.printerModel
                        function onSpeedFactorPercentChanged() {
                            if (root.printerModel != null)
                                speedSlider.value = root.printerModel.speedFactorPercent;
                        }
                    }
                    onValueTuning: {
                        if (root.printerModel != null)
                            root.printerModel.previewSpeedFactor(value);
                    }
                    onValueCommitted: {
                        if (root.printerModel != null)
                            root.printerModel.setSpeedFactor(value);
                    }
                    onInteractingChanged: {
                        if (root.interactionSink != null)
                            root.interactionSink(interacting, "", "");
                    }
                }
                // The reset rides the same command path as the slider's
                // release: M220 S100, the value converging on the next
                // poll (the camera refresh button's glyph and styling).
                UM.SimpleButton {
                    objectName: "moonrakerTuningSpeedReset"
                    width: UM.Theme.getSize("small_button_icon").width
                    height: UM.Theme.getSize("small_button_icon").height
                    // The cell must survive the row: the fillWidth
                    // slider otherwise squeezes it to zero and the
                    // press lands on the slider's track under the
                    // glyph, committing the track position (the live
                    // 200-reset find).
                    Layout.minimumWidth: UM.Theme.getSize("small_button_icon").width
                    Layout.alignment: Qt.AlignVCenter
                    enabled: root.printerModel != null
                    color: UM.Theme.getColor("text_inactive")
                    hoverColor: UM.Theme.getColor("text")
                    iconSource: UM.Theme.getIcon("ArrowDoubleCircleRight")
                    onClicked: {
                        if (root.printerModel != null)
                            root.printerModel.setSpeedFactor(100);
                    }
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Reset the speed factor to 100%."
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            RowLayout {
                Layout.fillWidth: true
                UM.Label {
                    text: "Extrusion multiplier"
                    Layout.fillWidth: true
                }
                UM.Label {
                    width: 52 * screenScaleFactor
                    horizontalAlignment: Text.AlignRight
                    text: flowSlider.selectedValue() + "%"
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").width
                OutlineSlider {
                    id: flowSlider
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    from: 50
                    to: Math.max(200, root.printerModel != null ? Math.ceil(root.printerModel.flowFactorPercent * 2) : 200)
                    stepSize: 1
                    live: false
                    value: 100
                    enabled: root.printerModel != null
                    Component.onCompleted: {
                        if (root.printerModel != null)
                            flowSlider.value = root.printerModel.flowFactorPercent;
                    }
                    Connections {
                        target: root.printerModel
                        function onFlowFactorPercentChanged() {
                            if (root.printerModel != null)
                                flowSlider.value = root.printerModel.flowFactorPercent;
                        }
                    }
                    onValueTuning: {
                        if (root.printerModel != null)
                            root.printerModel.previewFlowFactor(value);
                    }
                    onValueCommitted: {
                        if (root.printerModel != null)
                            root.printerModel.setFlowFactor(value);
                    }
                    onInteractingChanged: {
                        if (root.interactionSink != null)
                            root.interactionSink(interacting, "", "");
                    }
                }
                UM.SimpleButton {
                    objectName: "moonrakerTuningFlowReset"
                    width: UM.Theme.getSize("small_button_icon").width
                    height: UM.Theme.getSize("small_button_icon").height
                    // The cell must survive the row: the fillWidth
                    // slider otherwise squeezes it to zero and the
                    // press lands on the slider's track under the
                    // glyph, committing the track position (the live
                    // 200-reset find).
                    Layout.minimumWidth: UM.Theme.getSize("small_button_icon").width
                    Layout.alignment: Qt.AlignVCenter
                    enabled: root.printerModel != null
                    color: UM.Theme.getColor("text_inactive")
                    hoverColor: UM.Theme.getColor("text")
                    iconSource: UM.Theme.getIcon("ArrowDoubleCircleRight")
                    onClicked: {
                        if (root.printerModel != null)
                            root.printerModel.setFlowFactor(100);
                    }
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Reset the extrusion multiplier to 100%."
                    }
                }
            }
        }

        ColumnLayout {
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
                        Cura.SecondaryButton {
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
                        Cura.SecondaryButton {
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
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Clear Z offset"
                enabled: root.printerModel != null && !root.printerModel.actionBusy && root.printerModel.sectionReason === ""
                onClicked: root.printerModel.clearZOffset()
            }
        }
    }
}
