// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-13 [e2ef237] 4.0.0 live-test round: the locked preview card, camera auto-start, controls nudge, dwell
//   fix, dialog and pane fixes

import QtQuick 2.15
import UM 1.5 as UM

// The corner overlay host for the shared card: shown only while
// Cura's own action panel is gone (the idle empty platform — Cura
// hides its whole panel then, and the panel host with it). The card
// corners bottom-right at the classic margins; nothing of Cura's is
// on screen in this state, so it covers nothing.
Item {
    id: host
    objectName: "moonrakerPreviewCardOverlayHost"
    anchors.fill: parent
    z: 10000

    MoonrakerPreviewCard {
        id: card
        anchors.right: parent.right
        anchors.rightMargin: UM.Theme.getSize("thick_margin").width * 2
        anchors.bottom: parent.bottom
        anchors.bottomMargin: UM.Theme.getSize("thick_margin").height
    }
}
