import QtQuick 2.15

// Capture stub for Uranium's Python-registered ColorImage: renders the
// SVG in its native colours (Cura's icons are black, which is exactly
// right on the light theme; the production tinting cannot be replicated
// in pure QML).
Item {
    property url source: ""
    property color color: "#000000"
    Image {
        anchors.fill: parent
        source: parent.source
        fillMode: Image.PreserveAspectFit
        visible: parent.source != ""
    }
}
