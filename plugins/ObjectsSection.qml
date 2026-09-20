import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The Objects section (4.6.0 rework): a pure READOUT — names, the
// current highlight, the excluded flags and the restore verdicts in
// words. No per-row buttons: exclusion lives on the plate map
// (triple-click) and in the Exclude current button above the list.
// The row order is the human lexical sort, frozen — never
// reordering (the no-reorder rule).
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null

    // The bounded roster: five rows visible at most, the rest scrolls.
    // The bound is measured from the rows' own pitch — they carry
    // their text's height — so a theme font change cannot clip one.
    readonly property int maxVisibleObjectRows: 5
    readonly property real objectRowSpacing: UM.Theme.getSize("default_margin").height / 2
    readonly property real objectListHeight: objectRows.count > 0 ? Math.min(objectsColumn.implicitHeight, maxVisibleObjectRows * (objectsColumn.implicitHeight + objectRowSpacing) / objectRows.count - objectRowSpacing) : 0

    // NO-REFLOW RULE: the section is permanent — the objects arrive
    // seconds INTO a print (Klipper reports the slicer's DEFINE
    // lines only when they execute), so the rows below start empty
    // and fill in place.
    Layout.fillWidth: true
    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Objects"
        sectionId: "objects"
        sectionIcon: ""
        sectionIconUrl: Qt.resolvedUrl("ObjectExclude.svg")
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["objects"] !== false
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height / 2

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width
            Cura.SecondaryButton {
                // The fast path: one click, no dialog (the walked
                // ruling). The target label beside it names the
                // object the click would kill — the victim is
                // legible before the act.
                objectName: "excludeCurrentButton"
                text: "Exclude current"
                enabled: root.printerModel != null && root.printerModel.monitorConnected && !root.printerModel.actionBusy && root.printerModel.printActive && root.printerModel.sectionReason === "" && root.printerModel.currentObjectName !== ""
                onClicked: {
                    if (root.printerModel != null) {
                        root.printerModel.excludeCurrent();
                    }
                }
            }
            UM.Label {
                Layout.fillWidth: true
                elide: Text.ElideRight
                color: UM.Theme.getColor("text_inactive")
                text: root.printerModel != null && root.printerModel.currentObjectName !== "" ? root.printerModel.currentObjectName : "No object is printing right now"
            }
        }

        UM.Label {
            // The summary count (the product panel's counting rule):
            // the glance-value in one PERMANENT reserved line — empty
            // while no objects exist (the no-reflow rule).
            text: {
                if (root.printerModel == null)
                    return "";
                var rows = root.printerModel.excludeObjectItems;
                var excluded = 0, current = 0;
                for (var i = 0; i < rows.length; ++i) {
                    if (rows[i].excluded)
                        excluded += 1;
                    if (rows[i].current)
                        current += 1;
                }
                return rows.length + " objects — " + (rows.length - excluded) + " printing, " + excluded + " excluded";
            }
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("small")
        }

        // The bounded list (the request): the roster must not dominate
        // the pane. The slot holds the measured cap, never more.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: root.objectListHeight

            Flickable {
                id: objectListFlick
                anchors.fill: parent
                clip: true
                contentWidth: width
                contentHeight: objectsColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                interactive: contentHeight > height

                Column {
                    id: objectsColumn
                    width: objectListFlick.width
                    spacing: root.objectRowSpacing

                    Repeater {
                        id: objectRows
                        model: root.printerModel != null ? root.printerModel.excludeObjectItems : []
                        // The current row carries the Cura-blue bar and ink — the
                        // live request: the current object must read at a glance
                        // (the map's palette ruling, mirrored here). The bar is a
                        // SIBLING behind the label: a wrapper's opacity would
                        // inherit down and hide the text (the live report).
                        Item {
                            width: objectsColumn.width
                            height: rowLabel.implicitHeight
                            Rectangle {
                                anchors.fill: parent
                                visible: modelData.current
                                color: UM.Theme.getColor("primary")
                                opacity: 0.18
                            }
                            UM.Label {
                                id: rowLabel
                                anchors.fill: parent
                                text: modelData.name + (modelData.current ? "  · current" : "") + (modelData.excluded ? (modelData.restoreVerdict === "past_grace" || modelData.restoreVerdict === "in_grace" ? "  · excluded" : "  · restore closed") : "") + (modelData.excluded && modelData.restoreVerdict === "unknown" ? "  · layer unknown" : "")
                                color: modelData.excluded ? (modelData.restoreAllowed === false ? MoonrakerTheme.outOfWindowGrey : MoonrakerTheme.dangerRed) : modelData.current ? UM.Theme.getColor("primary") : UM.Theme.getColor("text")
                                font: modelData.current ? UM.Theme.getFont("default_bold") : UM.Theme.getFont("default")
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }

            // Scroll affordances, the file manager's and what's-new's
            // idiom: small blue chevrons centred over the list — an up
            // arrow near the top while more content is above, a down
            // arrow near the bottom while more is below. They are
            // SIBLINGS of the Flickable, overlaying it: a Flickable's
            // own children scroll with the content and vanish.
            UM.Label {
                text: "↑"
                visible: objectListFlick.height > 0 && objectListFlick.contentY > 2
                anchors.top: objectListFlick.top
                anchors.horizontalCenter: objectListFlick.horizontalCenter
                anchors.topMargin: 4 * screenScaleFactor
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                text: "↓"
                visible: objectListFlick.height > 0 && objectListFlick.contentY < objectListFlick.contentHeight - objectListFlick.height - 2
                anchors.bottom: objectListFlick.bottom
                anchors.horizontalCenter: objectListFlick.horizontalCenter
                anchors.bottomMargin: 4 * screenScaleFactor
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("medium_bold")
            }
        }
        UM.Label {
            // An empty list says so instead of reading as a bug (the
            // live request): the objects arrive when the slicer's
            // EXCLUDE_OBJECT lines execute — seconds into a print.
            visible: root.printerModel != null && root.printerModel.excludeObjectItems.length === 0
            text: root.printerModel != null && root.printerModel.printActive ? "No objects yet — they appear as the print defines them." : "No objects — EXCLUDE_OBJECT data arrives while printing."
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("small")
        }
    }
}
