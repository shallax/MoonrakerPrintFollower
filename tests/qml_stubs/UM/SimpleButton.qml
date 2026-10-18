// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-08 [0f363ac] Deterministic captures: vendored Cura assets, capture harness, committed screenshots

import QtQuick 2.15

Item {
    signal clicked
    property url iconSource: ""
    property color color: "#606060"
    property color hoverColor: "#202020"
    property string tooltip: ""
    MouseArea {
        anchors.fill: parent
        onClicked: parent.clicked()
    }
}
