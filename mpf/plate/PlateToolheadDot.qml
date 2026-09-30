import QtQuick 2.15
import UM 1.5 as UM
import "../resources/theme"

// The live toolhead's position on the plate: scene-graph geometry (a
// Rectangle binding), never a canvas repaint — the 1 s position publish
// moves it. Walking the layer path (the dot and the printed fill derive
// from the same motion index). The picker's faces draw no dot (the live
// ruling).
Rectangle {
    id: root
    objectName: "moonrakerPlateToolheadDot"

    // The bed mapping the position lands through, and the camera it is
    // presented at. Both are read WHOLE inside the bindings below: a
    // function call would hide the plot from the binding's dependencies,
    // so a re-fitted plot (a reflow, the popover's grown size) left the
    // dot on stale geometry until the next pan (the live report — the
    // jump landed off the centre).
    property var plot: null
    property real viewScale: 1.0
    property real panX: 0.0
    property real panY: 0.0
    // The position in BED millimetres, already smoothed by its owner.
    property real plateX: 0.0
    property real plateY: 0.0
    // Whether a live position exists to show at all. The dot rides the
    // LAYERS: no index, no dot (the live report — it rendered over the
    // unavailable card). Detached it goes too: the position belongs to
    // the live layer, and the frozen anchor is another layer's picture.
    property bool positionValid: false

    width: 7 * screenScaleFactor
    height: width
    radius: width / 2
    color: MoonrakerTheme.plateDot
    border.color: UM.Theme.getColor("main_background")
    border.width: 2
    visible: root.positionValid && root.plot != null
    x: root.visible ? root.panX + (root.plot.bed.offsetX + (root.plateX - root.plot.bed.bedXMin) * root.plot.sx) * root.viewScale - width / 2 : 0
    y: root.visible ? root.panY + (root.plot.bed.offsetY + (root.plot.bed.bedYMax - root.plateY) * root.plot.sy) * root.viewScale - height / 2 : 0
}
