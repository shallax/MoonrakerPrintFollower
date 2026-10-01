import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "../resources/theme"
import "../widgets"

// The exclude-object picker's card: the plate as the control, its
// legend, the outcome slot and the two download offers. The host owns
// the overlay frame (position, one-at-a-time open state) and passes the
// printer in.
MonitorPopOver {
    id: root

    property var printerModel: null
    property bool open: false

    visible: root.open && root.printerModel != null
    title: "Exclude Object Picker"

    Loader {
        Layout.fillWidth: true
        Layout.fillHeight: true
        active: root.open && root.printerModel != null
        sourceComponent: pickerContent
    }

    Component {
        id: pickerContent
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: UM.Theme.getSize("thin_margin").height

            // The picker's own download offer (the live request):
            // an empty plate carries the action instead of a dead
            // canvas.
            PlateDownloadAction {
                Layout.fillWidth: true
                visible: root.printerModel != null && !root.printerModel.plateHasObjects
                printerModel: root.printerModel
                idleInstruction: "Click here to download and index the print — the picker draws the objects from the print's data."
            }

            PlateExcludeFace {
                id: plateFace
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 240 * screenScaleFactor
                printerModel: root.printerModel
                plate: root.printerModel != null ? root.printerModel.plateObjects : null
                // No toolhead dot in the picker (the live ruling)
                // — the map reads as the CONTROL, not a follower.
                onExcludeRequested: function (name) {
                    if (root.printerModel != null) {
                        root.printerModel.excludeObject(name);
                    }
                }
                onRestoreRequested: function (name) {
                    if (root.printerModel != null) {
                        root.printerModel.restoreObject(name);
                    }
                }
            }

            UM.Label {
                // The permanent single line (the mesh "hover row"
                // idiom): counter > hover (name and state) > hint.
                // NoWrap is the reflow lock: the theme label
                // defaults to wrapping, and a wrapped verdict
                // grew the row and reflowed the canvas above
                // (the live report).
                Layout.fillWidth: true
                wrapMode: Text.NoWrap
                text: plateFace.clickProgress >= 2 ? "Click again to " + plateFace.pendingAction + " (" + plateFace.clickProgress + " of 3)" : (plateFace.hoveredName !== "" ? plateFace.hoverDetail() : (root.printerModel != null && root.printerModel.plateObjects.truncated > 0 ? root.printerModel.plateObjects.truncated + " objects omitted from this map (256-object limit)" : "Triple-click to exclude, or restore an excluded object"))
                elide: Text.ElideRight
                color: plateFace.hoveredName !== "" ? plateFace.hoverInk() : UM.Theme.getColor("text_inactive")
                horizontalAlignment: Text.AlignHCenter
            }

            Row {
                Layout.alignment: Qt.AlignHCenter
                spacing: UM.Theme.getSize("narrow_margin").width
                Row {
                    spacing: 4 * screenScaleFactor
                    Rectangle {
                        width: 10 * screenScaleFactor
                        height: width
                        radius: width / 2
                        color: UM.Theme.getColor("text")
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    UM.Label {
                        text: "Included"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Row {
                    spacing: 4 * screenScaleFactor
                    Rectangle {
                        width: 10 * screenScaleFactor
                        height: width
                        radius: width / 2
                        color: UM.Theme.getColor("primary")
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    UM.Label {
                        text: "Current"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Row {
                    // The printed state derives from the index's
                    // executed motions — no index, no green (the
                    // live ruling: the legend must not promise
                    // what the data cannot say).
                    visible: root.printerModel != null && root.printerModel.plateTrackingAvailable
                    spacing: 4 * screenScaleFactor
                    Rectangle {
                        width: 10 * screenScaleFactor
                        height: width
                        radius: width / 2
                        color: MoonrakerTheme.plateCurrent
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    UM.Label {
                        text: "Printed"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Row {
                    spacing: 4 * screenScaleFactor
                    Rectangle {
                        width: 10 * screenScaleFactor
                        height: width
                        radius: width / 2
                        color: MoonrakerTheme.dangerRed
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    UM.Label {
                        text: "Excluded"
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }
            UM.Label {
                // The outcome slot: the gesture receipts land here —
                // the no-confirm ruling's confirmation (a refusal
                // must never be silent).
                Layout.fillWidth: true
                text: root.printerModel != null ? root.printerModel.actionStatus : ""
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default_italic")
                horizontalAlignment: Text.AlignHCenter
            }
            // The index offer for the printed state (the live
            // rulings): the picker itself carries the download
            // control — a compact row, back in the layout after
            // the overlay overlapped the plate.
            PlateDownloadAction {
                Layout.fillWidth: true
                visible: root.printerModel != null && root.printerModel.plateHasObjects && !root.printerModel.plateTrackingAvailable
                printerModel: root.printerModel
                idleInstruction: "Download and index the print to track the printed state."
            }

            UM.Label {
                Layout.fillWidth: true
                text: "A triple-click sends the command immediately — there is no confirmation dialog. Restoring an object does not replace skipped layers."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                wrapMode: Text.WordWrap
            }
        }
    }
}
