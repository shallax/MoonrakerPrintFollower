import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

Cura.SecondaryButton {
    id: root
    // A single label lets the control centre text without a Row whose
    // width feeds back into the button's implicit content width.
    contentItem: UM.Label {
        text: root.text
        font: UM.Theme.getFont("medium")
        color: root.enabled ? (root.hovered ? root.textHoverColor : root.textColor) : root.textDisabledColor
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
}
