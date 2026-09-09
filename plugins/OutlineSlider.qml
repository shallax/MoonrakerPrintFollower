import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

// A plugin-owned outline-style slider: the themed slider's rail
// rendered as a black slab in the inactive-window palette; this one
// draws a thin outlined track with the progress filled inside it in
// Cura's brand blue, and the blue-ringed handle Cura's own sliders
// use. All slider properties (from/to/stepSize/live/value/onMoved/
// onPressedChanged…) pass straight through.
Slider {
    id: control
    implicitHeight: 28 * screenScaleFactor

    background: Item {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 200 * screenScaleFactor
        implicitHeight: 24 * screenScaleFactor
        width: control.availableWidth
        height: 8 * screenScaleFactor

        Cura.RoundedRectangle {
            anchors.fill: parent
            color: "transparent"
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
            cornerSide: Cura.RoundedRectangle.Direction.All
        }
        Cura.RoundedRectangle {
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.margins: 2 * screenScaleFactor
            width: Math.max(0, control.visualPosition) * (parent.width - 4 * screenScaleFactor)
            // Disabled sliders grey out (the author's live report):
            // the stock dimming does not reach a custom-styled fill.
            color: control.enabled ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_disabled")
            radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
            cornerSide: Cura.RoundedRectangle.Direction.All
        }
    }

    handle: Cura.RoundedRectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        implicitWidth: 16 * screenScaleFactor
        implicitHeight: 16 * screenScaleFactor
        radius: 8 * screenScaleFactor
        cornerSide: Cura.RoundedRectangle.Direction.All
        color: UM.Theme.getColor("main_background")
        border.color: control.enabled ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_disabled")
        border.width: UM.Theme.getSize("default_lining").width
    }
}
