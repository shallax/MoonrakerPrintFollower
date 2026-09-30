import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The browser's toolbar: the section title, the clickable breadcrumb of
// the current directory, the refresh readout, the history escape hatch
// and the refresh verb. Navigation and the folder context menu are
// reported out — the shell owns the model call and owns the menu, so
// this row never holds a policy of its own.
RowLayout {
    id: root

    // The current directory as path segments, [] at the root.
    property var directory: []
    // Non-empty while a search scopes the whole tree, which is where the
    // breadcrumb stops describing what is on screen and goes.
    property string search: ""
    property string refreshedAt: ""
    // The bounded-history window still has more to load.
    property bool canLoadAllHistory: false
    property bool connected: false

    signal navigateRequested(var segments, bool up)
    signal directoryMenuRequested(string path)
    signal refreshRequested
    signal loadAllHistoryRequested

    Layout.fillWidth: true
    spacing: UM.Theme.getSize("narrow_margin").width

    UM.Label {
        text: "Files"
        font: UM.Theme.getFont("large_bold")
    }
    // Breadcrumb: one directory at a time, every segment clickable — a
    // click navigates to that directory. While a search is active the
    // scope is the whole tree, not a directory, so the breadcrumb goes.
    RowLayout {
        visible: root.search.length === 0
        spacing: 0
        UM.Label {
            // The root segment reads "<root>", not Moonraker's raw root
            // name — "/" collided with the segment separators.
            text: "<root>"
            color: UM.Theme.getColor("primary")
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.navigateRequested([], false)
            }
        }
        // ONE delegate per segment: a Repeater with two bare children
        // keeps only the LAST as its delegate (engine-proven), so the
        // slash and the segment share a Row.
        Repeater {
            model: root.directory
            Row {
                spacing: 0
                UM.Label {
                    text: " / "
                }
                UM.Label {
                    text: modelData
                    color: UM.Theme.getColor("primary")
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            if (mouse.button === Qt.RightButton) {
                                // The folder context menu: rename or
                                // delete this segment's directory.
                                root.directoryMenuRequested(root.directory.slice(0, index + 1).join("/"));
                                return;
                            }
                            root.navigateRequested(root.directory.slice(0, index + 1), false);
                        }
                    }
                }
            }
        }
    }
    UM.Label {
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignRight
        text: root.refreshedAt
        color: UM.Theme.getColor("text_inactive")
    }
    // The bounded-window escape hatch: the history starts capped, one
    // click loads the complete history — even a file printed a thousand
    // jobs ago resolves. It sits with the refresh affordance, not the
    // recents strip.
    UM.Label {
        visible: root.canLoadAllHistory
        text: "Load all history"
        color: UM.Theme.getColor("primary")
        font: UM.Theme.getFont("small")
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.loadAllHistoryRequested()
        }
    }
    // The refresh button: re-walks the tree and re-fetches the history
    // window on demand.
    Cura.SecondaryButton {
        text: "⟳"
        enabled: root.connected
        onClicked: root.refreshRequested()
        HoverHandler {
            id: tooltipHover2
        }
        UM.ToolTip {
            visible: tooltipHover2.hovered
            targetPoint: Qt.point(parent.width / 2, 0)
            x: 0
            y: parent.height + UM.Theme.getSize("default_margin").height
            width: UM.Theme.getSize("tooltip").width
            text: "Refresh"
        }
    }
}
