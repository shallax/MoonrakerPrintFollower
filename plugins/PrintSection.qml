import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Print section (4.3.0 extraction): the header and its content
// moved out of the dashboard as one property-driven component. The
// Layout.* properties of the host pane are reproduced with plain
// anchors and paddings inside the Column — the pane's spacing: 0
// contract stays the host's.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    signal cancelRequested

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Print"
        sectionId: "print"
        sectionIcon: "Printer"
    }

    // The pane's spacing: 0 contract puts the gaps on the children —
    // the top and bottom gaps are explicit spacers (the collapsed
    // content hides them with itself).
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["print"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height

        UM.Label {
            // A permanent state caption gives the always-present
            // action row context (the UX panel: a dead
            // Pause|Resume|Cancel band with no caption read as an
            // error state on idle printers). The caption (4.3.0)
            // reads a STATE property — never a permission boolean,
            // so a busy lane never reads as "Printing".
            height: 36 * screenScaleFactor
            text: root.printerModel != null ? root.printerModel.printJobCaption : ""
            color: UM.Theme.getColor("text_inactive")
            Layout.fillWidth: true
            elide: Text.ElideRight
            wrapMode: Text.NoWrap
        }

        RowLayout {
            // NO-REFLOW RULE (the ruling): the action buttons never
            // disappear — they disable. The row used to vanish
            // entirely when no action applied and reappear
            // mid-session, reflowing every control beneath it (the
            // jog-reflow hazard).
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2

            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Pause"
                enabled: root.printerModel != null && root.printerModel.canPausePrint
                // The refusal words (4.3.0): the policy's short form
                // rides the tooltip — a dead Pause must never be
                // silent.
                onClicked: root.printerModel.pausePrint()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: root.printerModel != null && !root.printerModel.canPausePrint && root.printerModel.pauseReason.length > 0 ? root.printerModel.pauseReason : "Pause the current print immediately (Klipper PAUSE)."
                }
            }

            Cura.PrimaryButton {
                Layout.fillWidth: true
                text: "Resume"
                enabled: root.printerModel != null && root.printerModel.canResumePrint
                onClicked: root.printerModel.resumePrint()
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: root.printerModel != null && !root.printerModel.canResumePrint && root.printerModel.resumeReason.length > 0 ? root.printerModel.resumeReason : "Resume the paused print (Klipper RESUME)."
                }
            }

            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Cancel"
                enabled: root.printerModel != null && root.printerModel.canCancelPrint
                onClicked: root.cancelRequested()
            }
        }

        GridLayout {
            columns: 2
            Layout.fillWidth: true
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            UM.Label {
                text: "Layer height"
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.monitorLayerHeight : "—"
                Layout.fillWidth: true
            }
            UM.Label {
                text: "Current Z offset"
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.zOffsetText : "—"
                Layout.fillWidth: true
            }
        }
    }
}
