// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-18 [53afbd7] 4.5.0: the reviewer-attribution phrasing goes from the codebase
// 2026-09-13 [a5234d9] docs: drop verbatim quotes and author attributions from the repo text
// 2026-09-10 [4990f07] Monitor readouts: layer-anchored ETA, endstops and the first live-pass fixes

import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

// A plugin-owned outline-style progress bar: a thin rounded border
// with NO background fill — the themed ProgressBar's inactive-window
// palette rendered as an ugly black slab (the screenshot) —
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
        // look pointed at is Cura's "little rounded ends".
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
