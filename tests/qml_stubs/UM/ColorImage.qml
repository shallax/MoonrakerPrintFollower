// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-08 [0f363ac] Deterministic captures: vendored Cura assets, capture harness, committed screenshots

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
