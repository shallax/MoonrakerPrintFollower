// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-27 [e1fbc8d] Prevent popover gestures from reaching the webcam underneath
// 2026-09-19 [450e38b] 4.5.0: in-bounds clicks keep the configure popovers open; the popover close glyphs are
//   gone; every bespoke checkbox and radio becomes Cura's native control
// 2026-09-10 [713f61c] 3.5.0 groundwork: aux-fed temperature history, charts and the shared pop-over shell

import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Information pane's shared pop-over shell: a titled floating
// card that hosts one widget's enlarged content. The pane's small
// glanceable widgets open their pop-over on click; a click outside
// the card dismisses it and the host owns the open flag. The card
// grows with its content up to the parent's size, so a machine with
// many sensors gets a taller legend instead of a crushed chart.
Cura.RoundedRectangle {
    id: root

    property string title: ""

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

    // The floating card owns its whole footprint. Unhandled buttons and
    // wheels must not reach the webcam's pan/FPS gestures underneath it.
    MouseArea {
        objectName: "monitorPopoverPointerBarrier"
        parent: root
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onWheel: function (wheel) {
            wheel.accepted = true;
        }
    }

    ColumnLayout {
        id: column
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        spacing: UM.Theme.getSize("thin_margin").height

        RowLayout {
            Layout.fillWidth: true
            UM.Label {
                Layout.fillWidth: true
                text: root.title
                font: UM.Theme.getFont("medium_bold")
                elide: Text.ElideRight
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
