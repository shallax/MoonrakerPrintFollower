// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-08 [0f363ac] Deterministic captures: vendored Cura assets, capture harness, committed screenshots

import QtQuick 2.15

Item {
    property string text: ""
    property int maximumLength: 32767
    property bool selectByMouse: false
    signal editingFinished
    property var validator: null
    property string placeholderText: ""
    implicitWidth: 200
    implicitHeight: 32
    Rectangle {
        anchors.fill: parent
        color: "#ffffff"
        border.color: "#b0b0b0"
    }
}
