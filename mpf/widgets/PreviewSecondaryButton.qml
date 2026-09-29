import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura

Item {
    id: root

    property string text: ""
    property string tooltip: ""
    // The instance-level tooltips read parent.hovered (the Cura
    // placement pattern); the root is an Item, so the native
    // button's hover state surfaces here.
    property alias hovered: nativeButton.hovered

    signal clicked

    implicitHeight: UM.Theme.getSize("action_button").height
    implicitWidth: 120 * screenScaleFactor

    // Keep Cura's native SecondaryButton for interaction, focus, hover, disabled
    // styling and tooltips, but draw its label ourselves. Cura's ActionButton
    // label does not vertically centre its Text contents and its fixed-width
    // layout can look offset in narrow/plugin-defined widths.
    Cura.SecondaryButton {
        id: nativeButton
        anchors.fill: parent
        text: root.text
        enabled: root.enabled
        fixedWidthMode: true
        textColor: "transparent"
        textHoverColor: "transparent"
        textDisabledColor: "transparent"
        onClicked: root.clicked()
        UM.ToolTip {
            visible: parent.hovered
            targetPoint: Qt.point(parent.width / 2, 0)
            x: 0
            y: parent.height + UM.Theme.getSize("default_margin").height
            width: UM.Theme.getSize("tooltip").width
            text: root.tooltip
        }
    }

    UM.Label {
        anchors.fill: parent
        anchors.leftMargin: UM.Theme.getSize("default_margin").width
        anchors.rightMargin: UM.Theme.getSize("default_margin").width
        text: root.text
        color: root.enabled ? UM.Theme.getColor("secondary_button_text") : UM.Theme.getColor("action_button_disabled_text")
        font: UM.Theme.getFont("medium")
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        clip: true
    }
}
