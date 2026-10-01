// Capture-only stand-in: the real UM.ToolTip depends on UM.Enums and
// UM.PointingRectangle, which have no QML assets, so the real chain
// cannot compile in the capture engine. This Item provides the surface
// the real widgets touch (UM.CheckBox, UM.TooltipArea,
// Cura.ActionButton) and stays invisible and inert.
import QtQuick 2.15

Item {
    property string text: ""
    property string tooltipText: ""
    property int delay: 0
    property int contentAlignment: Qt.AlignLeft
    property real arrowSize: 0
    property var targetPoint: Qt.point(0, 0)
    function show() {}
    function hide() {}
}
