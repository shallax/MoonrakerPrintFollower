import QtQuick 2.15
import UM 1.5 as UM
import "../widgets"

CentredSecondaryButton {
    id: root
    focusPolicy: Qt.StrongFocus
    leftPadding: 3 * screenScaleFactor
    rightPadding: 3 * screenScaleFactor
    topPadding: 2 * screenScaleFactor
    bottomPadding: 2 * screenScaleFactor
    contentItem: UM.Label {
        text: root.text
        font: UM.Theme.getFont("default")
        color: root.enabled ? (root.hovered ? root.textHoverColor : root.textColor) : root.textDisabledColor
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.NoWrap
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: -2 * screenScaleFactor
        radius: 3 * screenScaleFactor
        color: "transparent"
        border.color: UM.Theme.getColor("text")
        border.width: 2 * screenScaleFactor
        visible: root.activeFocus
    }
}
