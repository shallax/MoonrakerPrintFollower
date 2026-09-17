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
    // Slot rows render the row invisibly beneath their blank and
    // must not answer clicks (opacity blocks nothing).
    property bool interactive: true

    signal toggleRequested
    signal moveRequested(int steps)
    // The press starts the drag; the host's overlay carries the
    // rest, so the delegate can leave the list mid-gesture.
    signal dragRequested
    // The release commits: it lands on EITHER the handle (a quick
    // press-release, before the rebuild kills the delegate) or the
    // overlay (a moved drag) — the host's commit is idempotent, so
    // both paths are safe.
    signal dragReleased

    // The fill MouseArea is a SIBLING of the Row and declared FIRST,
    // so it sits behind: whole-row clicks toggle visibility while the
    // handle, checkbox and arrows above keep their own clicks (the
    // column popup's documented trap — an anchored child disables
    // the positioner).
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        enabled: rowRoot.interactive
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
                enabled: rowRoot.interactive
                onPressed: {
                    handle.pressed = true;
                    rowRoot.dragRequested();
                }
                onReleased: {
                    handle.pressed = false;
                    rowRoot.dragReleased();
                }
                onCanceled: {
                    handle.pressed = false;
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
                enabled: rowRoot.interactive
                onClicked: rowRoot.toggleRequested()
            }
        }

        UM.Label {
            objectName: "sectionConfigureRowTitle"
            text: rowRoot.rowTitle
            width: parent.width - handle.width - checkboxBox.width - 2 * parent.spacing
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
            height: parent.height
            font: UM.Theme.getFont("default")
        }
    }
}
