// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-08 [0f363ac] Deterministic captures: vendored Cura assets, capture harness, committed screenshots

import QtQuick 2.15

Item {
    property alias text: label.text
    property alias color: label.color
    property alias font: label.font
    property alias wrapMode: label.wrapMode
    property alias elide: label.elide
    property alias horizontalAlignment: label.horizontalAlignment
    property alias verticalAlignment: label.verticalAlignment
    implicitWidth: label.implicitWidth
    implicitHeight: label.implicitHeight
    Text {
        id: label
        anchors.fill: parent
        renderType: Text.QtRendering
    }
}
