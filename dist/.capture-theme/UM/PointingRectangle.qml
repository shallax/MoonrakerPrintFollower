// Capture-only stand-in for Uranium's Python-registered
// UM.PointingRectangle; hover-only paths never draw it offscreen.
import QtQuick 2.15
Item {
    property var target: null
    property real offset: 0
    property real arrowSize: 5
    property color color: "#000000"
}
