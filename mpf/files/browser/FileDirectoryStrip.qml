import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The folder strip: one chip per subdirectory of the CURRENT level, a
// click descends and the breadcrumb climbs. Directories never join the
// metadata list — they carry no print history and no meaningful sizes.
// The strip scrolls horizontally: a wrapping Flow grew rows of chips on
// folder-heavy printers and crushed the grid.
Flickable {
    id: root

    // The current directory's path segments; the leading ".." chip
    // exists exactly when there is a parent to climb to.
    property var directory: []
    property var directories: []

    signal navigateRequested(var segments, bool up)
    signal directoryMenuRequested(string path)

    Layout.fillWidth: true
    Layout.preferredHeight: 28 * screenScaleFactor
    clip: true
    contentWidth: chipsRow.width
    contentHeight: 28 * screenScaleFactor
    boundsBehavior: Flickable.StopAtBounds
    flickableDirection: Flickable.HorizontalFlick
    ScrollBar.horizontal: ScrollBar {}
    Row {
        id: chipsRow
        spacing: UM.Theme.getSize("narrow_margin").width
        // The up directory: a chip whenever a parent exists — the root
        // view has nowhere to go. It leads the strip, like a file
        // manager's ".." entry.
        Rectangle {
            visible: root.directory.length > 0
            width: upName.width + 48 * screenScaleFactor
            height: 28 * screenScaleFactor
            radius: UM.Theme.getSize("default_radius").width
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            color: "transparent"
            UM.Label {
                anchors.left: parent.left
                anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                anchors.verticalCenter: parent.verticalCenter
                text: "↑"
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("default")
            }
            UM.Label {
                id: upName
                anchors.left: parent.left
                anchors.leftMargin: 16 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").width
                anchors.verticalCenter: parent.verticalCenter
                text: ".."
                font: UM.Theme.getFont("default")
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.navigateRequested(root.directory, true)
            }
        }
        Repeater {
            model: root.directories
            Rectangle {
                width: folderName.width + 48 * screenScaleFactor
                height: 28 * screenScaleFactor
                radius: UM.Theme.getSize("default_radius").width
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: "transparent"
                UM.ColorImage {
                    anchors.left: parent.left
                    anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
                    anchors.verticalCenter: parent.verticalCenter
                    width: 16 * screenScaleFactor
                    height: 16 * screenScaleFactor
                    source: UM.Theme.getIcon("Folder")
                    color: UM.Theme.getColor("primary")
                }
                UM.Label {
                    id: folderName
                    anchors.left: parent.left
                    anchors.leftMargin: 16 * screenScaleFactor + 2 * UM.Theme.getSize("narrow_margin").width
                    anchors.verticalCenter: parent.verticalCenter
                    text: modelData
                    elide: Text.ElideRight
                    font: UM.Theme.getFont("default")
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (mouse.button === Qt.RightButton) {
                            // The folder context menu (the shell owns
                            // it): rename or delete this directory.
                            root.directoryMenuRequested(root.directory.concat(modelData).join("/"));
                            return;
                        }
                        root.navigateRequested(root.directory.concat(modelData), false);
                    }
                }
                // The visible menu affordance: the right-click menu alone
                // was undiscoverable (folder deletion could not be found
                // at all).
                UM.Label {
                    anchors.right: parent.right
                    anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
                    anchors.verticalCenter: parent.verticalCenter
                    text: "⋮"
                    font: UM.Theme.getFont("medium_bold")
                    color: UM.Theme.getColor("primary")
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.directoryMenuRequested(root.directory.concat(modelData).join("/"))
                    }
                }
            }
        }
    }
}
