import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The shared all/none three-state selector (the author's live
// ruling): the row selector's vocabulary — empty, a dash for
// mixed, a tick for all. View-only: the host owns the counts and
// the action; a click emits toggled.
Row {
    id: selectorRoot
    spacing: UM.Theme.getSize("narrow_margin").width

    property int total: 0
    property int visibleCount: 0

    signal toggled

    Rectangle {
        objectName: "visibilitySelectorBox"
        width: 16 * screenScaleFactor
        height: 16 * screenScaleFactor
        anchors.verticalCenter: parent.verticalCenter
        radius: 2 * screenScaleFactor
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        // The three states: filled + tick at ALL, filled + dash at
        // SOME, empty at NONE — the fill and the glyph both follow
        // visibleCount (the live report: all rendered empty and none
        // rendered dashed — the conditions were inverted).
        color: selectorRoot.visibleCount > 0 ? UM.Theme.getColor("primary") : "transparent"
        UM.Label {
            id: glyph
            objectName: "visibilitySelectorGlyph"
            visible: selectorRoot.visibleCount > 0
            anchors.centerIn: parent
            text: selectorRoot.visibleCount === selectorRoot.total ? "✓" : "–"
            color: "white"
            font: UM.Theme.getFont("small")
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: selectorRoot.toggled()
        }
    }
    UM.Label {
        anchors.verticalCenter: parent.verticalCenter
        text: "Show all"
        font: UM.Theme.getFont("default")
    }
}
