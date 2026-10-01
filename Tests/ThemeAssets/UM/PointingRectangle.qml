import QtQuick 2.15
// Production registers this from Python (UM/PointingRectangle.py); the
// capture harness needs a minimal QML stand-in for UM.ToolTip's
// dependency — hover-only in real usage, so it never draws here.
Item {
    property var target: null
    property real offset: 0
    property real arrowSize: 5
    property color color: "#000000"
}
