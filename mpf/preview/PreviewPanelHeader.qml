import QtQuick 2.15
import UM 1.5 as UM
import "../widgets"

// Shared Preview-panel chrome. Clicking only changes local expansion state.
CentredSecondaryButton {
    id: root
    property string iconName: ""
    property string indicatorName: "ChevronSingleDown"
    property real titleRightInset: 0
    property string titleObjectName: ""
    height: 40 * screenScaleFactor
    focusPolicy: Qt.StrongFocus
    leftPadding: 12 * screenScaleFactor
    rightPadding: 10 * screenScaleFactor
    color: UM.Theme.getColor("main_background")
    textColor: UM.Theme.getColor("text")
    textHoverColor: UM.Theme.getColor("text")
    outlineColor: UM.Theme.getColor("lining")
    outlineHoverColor: UM.Theme.getColor("primary")
    Accessible.name: root.text

    contentItem: Item {
        UM.ColorImage {
            id: titleIcon
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: 18 * screenScaleFactor
            height: width
            source: UM.Theme.getIcon(root.iconName)
            color: root.textColor
        }
        UM.Label {
            objectName: root.titleObjectName
            anchors.left: titleIcon.right
            anchors.leftMargin: 8 * screenScaleFactor
            anchors.right: indicator.left
            anchors.rightMargin: 8 * screenScaleFactor + root.titleRightInset
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            font: UM.Theme.getFont("medium_bold")
            color: root.textColor
            elide: Text.ElideRight
        }
        UM.ColorImage {
            id: indicator
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: 20 * screenScaleFactor
            height: width
            source: UM.Theme.getIcon(root.indicatorName)
            color: root.textColor
        }
    }
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: UM.Theme.getColor("text")
        border.width: 2 * screenScaleFactor
        visible: root.activeFocus
    }
}
