// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-08 [0f363ac] Deterministic captures: vendored Cura assets, capture harness, committed screenshots

import QtQuick 2.15

Item {
    property alias text: label.text
    property bool checked: false
    property string tooltip: ""
    implicitWidth: 120
    implicitHeight: 32
    signal clicked
    Rectangle {
        anchors.fill: parent
        color: "#e8e8e8"
        border.color: "#b0b0b0"
        Text {
            id: label
            anchors.centerIn: parent
            font.pixelSize: 14
        }
    }
    MouseArea {
        anchors.fill: parent
        onClicked: parent.clicked()
    }
}
