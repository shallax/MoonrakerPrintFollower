import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

// A plugin-owned outline-style progress bar: a thin rounded border
// with NO background fill — the themed ProgressBar's inactive-window
// palette rendered as an ugly black slab (the author's screenshot) —
// and the completed fraction filled inside the outline in Cura's
// brand blue (the same accent as buttons and slider handles).
Item {
    id: root
    property real from: 0
    property real to: 100
    property real value: 0
    property real inset: 2 * screenScaleFactor

    Cura.RoundedRectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        // Cura's own corner radius for progress bars, not a pill: the
        // author pointed at Cura's "little rounded ends" as the look.
        // cornerSide is REQUIRED: without it the component forces
        // radius to 0 and the corners render square.
        radius: UM.Theme.getSize("progressbar_radius").width
        cornerSide: Cura.RoundedRectangle.Direction.All
    }
    Cura.RoundedRectangle {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.margins: root.inset
        width: Math.max(0, Math.min(1, (root.value - root.from) / (root.to - root.from))) * (parent.width - 2 * root.inset)
        color: UM.Theme.getColor("primary")
        radius: Math.min(UM.Theme.getSize("progressbar_radius").width, height / 2)
        cornerSide: Cura.RoundedRectangle.Direction.All
    }
}
