import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Information pane's shared pop-over shell: a titled, closable
// floating card that hosts one widget's enlarged content. The pane's
// small glanceable widgets open their pop-over on click; the Close
// button emits `closed` so the call site owns its open flag. The card
// grows with its content up to the parent's size, so a machine with
// many sensors gets a taller legend instead of a crushed chart.
Cura.RoundedRectangle {
    id: root

    property string title: ""
    signal closed

    property real contentWidth: 520 * screenScaleFactor

    // The card clips its content: an overflowing child (a chart whose
    // layout budget was exceeded) must never draw past the card edge.
    clip: true

    z: 999
    width: parent != null ? Math.min(root.contentWidth, parent.width - 2 * UM.Theme.getSize("default_margin").width) : root.contentWidth
    // Height is owned by the call sites: an internal height binding
    // cycled through the fillHeight content and sent the layout engine
    // into an endless, memory-eating relayout whenever a pop-over
    // opened. The chart card grows with the legend-row count; the mesh
    // card keeps its fixed comfortable size.
    color: UM.Theme.getColor("main_background")
    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    radius: UM.Theme.getSize("default_radius").width

    // Content lands in the layout below the title row.
    default property alias content: contentColumn.data

    ColumnLayout {
        id: column
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        spacing: UM.Theme.getSize("thin_margin").height

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width
            UM.Label {
                Layout.fillWidth: true
                text: root.title
                font: UM.Theme.getFont("medium_bold")
                elide: Text.ElideRight
            }
            Cura.SecondaryButton {
                text: "Close"
                onClicked: root.closed()
            }
        }

        ColumnLayout {
            id: contentColumn
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: UM.Theme.getSize("thin_margin").height
        }
    }
}
