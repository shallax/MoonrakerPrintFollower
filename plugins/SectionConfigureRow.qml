import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The shared configure row (4.4.0): drag handle + visibility checkbox
// + title + the precise arrow pair, one per section/column entry.
// View-only — the host owns the list, the visibility and the
// move/toggle callbacks (the pane popup and the file-manager column
// popup share this row, never a controller). The handle drag moves a
// host-owned visual proxy; the list itself rebuilds once, on release.
Item {
    id: rowRoot
    height: 32 * screenScaleFactor

    property string rowId: ""
    property string rowTitle: ""
    property bool rowVisible: true
    // The edge rows hide the arrow that would move past the end (the
    // column popup's own idiom; the pane popups pass false).
    property bool rowAtTop: false
    property bool rowAtBottom: false

    signal toggleRequested
    signal moveRequested(int steps)
    signal dragMoved(real delta)
    signal dragReleased

    // The fill MouseArea is a SIBLING of the Row and declared FIRST,
    // so it sits behind: whole-row clicks toggle visibility while the
    // handle, checkbox and arrows above keep their own clicks (the
    // column popup's documented trap — an anchored child disables
    // the positioner).
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: rowRoot.toggleRequested()
    }

    Row {
        id: layoutRow
        anchors.fill: parent
        spacing: UM.Theme.getSize("thin_margin").width

        // The handle: the pill-grip vocabulary (the column-resize
        // precedent) as two bars that read as draggable, with the
        // same preventStealing + onCanceled discipline.
        Item {
            id: handle
            width: 10 * screenScaleFactor
            height: parent.height
            property bool pressed: false
            objectName: "sectionConfigureHandle"

            Rectangle {
                anchors.left: parent.left
                anchors.leftMargin: 2 * screenScaleFactor
                width: 2
                height: 20 * screenScaleFactor
                y: (parent.height - height) / 2
                radius: 1
                color: handleMouse.containsMouse || handle.pressed ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
            }
            Rectangle {
                anchors.right: parent.right
                anchors.rightMargin: 2 * screenScaleFactor
                width: 2
                height: 20 * screenScaleFactor
                y: (parent.height - height) / 2
                radius: 1
                color: handleMouse.containsMouse || handle.pressed ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
            }
            MouseArea {
                id: handleMouse
                anchors.fill: parent
                hoverEnabled: true
                preventStealing: true
                cursorShape: Qt.SizeVerCursor
                property real pressY: 0
                onPressed: {
                    handle.pressed = true;
                    handleMouse.pressY = mouseY;
                }
                onPositionChanged: {
                    if (!handle.pressed) {
                        return;
                    }
                    rowRoot.dragMoved(mouseY - handleMouse.pressY);
                    handleMouse.pressY = mouseY;
                }
                onReleased: {
                    if (handle.pressed) {
                        rowRoot.dragReleased();
                    }
                    handle.pressed = false;
                }
                onCanceled: {
                    handle.pressed = false;
                    rowRoot.dragReleased();
                }
            }
        }

        // The drawn checkbox: a bordered square that fills with a
        // tick when visible — the column popup's exact vocabulary,
        // kept verbatim so the retrofit never restyles a live surface
        // (an eye would fork two idioms).
        Rectangle {
            id: checkboxBox
            width: 20 * screenScaleFactor
            height: 20 * screenScaleFactor
            y: (parent.height - height) / 2
            radius: 4 * screenScaleFactor
            color: rowRoot.rowVisible ? UM.Theme.getColor("primary") : UM.Theme.getColor("main_background")
            border.color: rowRoot.rowVisible ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            UM.Label {
                anchors.centerIn: parent
                text: rowRoot.rowVisible ? "✓" : ""
                color: UM.Theme.getColor("main_background")
                font: UM.Theme.getFont("medium_bold")
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: rowRoot.toggleRequested()
            }
        }

        UM.Label {
            text: rowRoot.rowTitle
            width: parent.width - handle.width - checkboxBox.width - arrowUp.width - arrowDown.width - 4 * parent.spacing
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            height: parent.height
            font: UM.Theme.getFont("default")
        }

        UM.Label {
            id: arrowUp
            text: rowRoot.rowAtTop ? "" : "▲"
            width: 20 * screenScaleFactor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            height: parent.height
            color: UM.Theme.getColor("primary")
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                enabled: !rowRoot.rowAtTop
                onClicked: rowRoot.moveRequested(-1)
            }
        }
        UM.Label {
            id: arrowDown
            text: rowRoot.rowAtBottom ? "" : "▼"
            width: 20 * screenScaleFactor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            height: parent.height
            color: UM.Theme.getColor("primary")
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                enabled: !rowRoot.rowAtBottom
                onClicked: rowRoot.moveRequested(1)
            }
        }
    }
}
