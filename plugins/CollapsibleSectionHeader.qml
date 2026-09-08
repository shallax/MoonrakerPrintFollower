import QtQuick 2.15
import UM 1.5 as UM

// Cura-style accordion section header, shared by every Monitor pane.
// Mirrors Cura's CategoryButton: the title hugs the chevron on the right
// (ChevronSingleLeft collapsed / ChevronSingleDown expanded), a lining
// divider across the top belongs to the header so it never collapses
// away, and hover/pressed states tint the row. The expansion state is
// read from the printer model's sectionExpandedMap, keyed by sectionId;
// the click toggles it through setSectionExpanded, so the state persists
// like every other pane setting. The glyph slot keeps a fixed width even
// when no icon is set (or the theme lacks it), so titles line up at the
// same indent across sections.
Item {
    id: headerRoot
    height: UM.Theme.getSize("section_header").height

    property var printerModel: null
    property string title: ""
    property string sectionId: ""
    property string sectionIcon: ""
    property url sectionIconUrl: ""

    property bool expanded: printerModel != null ? (printerModel.sectionExpandedMap[sectionId] !== false) : true
    property bool hovered: false

    Rectangle {
        id: backgroundRectangle
        anchors.fill: parent
        color: headerRoot.hovered ? UM.Theme.getColor("setting_category_hover") : UM.Theme.getColor("setting_category")
        Behavior on color  {
            ColorAnimation {
                duration: 50
            }
        }

        // Lining on top: part of the header, not the content.
        Rectangle {
            anchors.top: parent.top
            color: UM.Theme.getColor("border_main")
            height: UM.Theme.getSize("default_lining").height
            width: parent.width
        }
    }
    MouseArea {
        id: mouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        // entered/exited drive the tint instead of containsMouse, which
        // can stick after scrolling the pane under a stationary cursor;
        // pressing clears it so a click never latches the grey.
        onEntered: headerRoot.hovered = true
        onExited: headerRoot.hovered = false
        onPressed: headerRoot.hovered = false
        onReleased: headerRoot.hovered = mouseArea.containsMouse
        onClicked: {
            if (printerModel != null) {
                printerModel.setSectionExpanded(sectionId, !headerRoot.expanded);
            }
        }
    }
    UM.ColorImage {
        id: sectionGlyph
        visible: sectionIcon != "" || sectionIconUrl != ""
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
        anchors.verticalCenter: parent.verticalCenter
        width: UM.Theme.getSize("section_icon").width
        height: UM.Theme.getSize("section_icon").height
        color: UM.Theme.getColor("setting_category_text")
        source: visible ? (sectionIconUrl != "" ? sectionIconUrl : UM.Theme.getIcon(sectionIcon)) : ""
    }
    UM.Label {
        anchors.left: sectionGlyph.right
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
        anchors.right: chevron.left
        anchors.verticalCenter: parent.verticalCenter
        text: title
        font: UM.Theme.getFont("medium_bold")
        color: headerRoot.hovered ? UM.Theme.getColor("setting_category_active_text") : UM.Theme.getColor("setting_category_text")
        elide: Text.ElideRight
    }
    UM.ColorImage {
        id: chevron
        anchors.right: parent.right
        anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
        anchors.verticalCenter: parent.verticalCenter
        width: UM.Theme.getSize("standard_arrow").width
        height: UM.Theme.getSize("standard_arrow").height
        color: UM.Theme.getColor("setting_control_button")
        source: headerRoot.expanded ? UM.Theme.getIcon("ChevronSingleDown") : UM.Theme.getIcon("ChevronSingleLeft")
    }
}
