// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-07 [6b3d8e9] Add Preview toolhead controls for 5.3.0

import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: root
    property string axis: ""
    property alias text: input.text
    implicitWidth: 60 * screenScaleFactor
    implicitHeight: column.implicitHeight
    Column {
        id: column
        width: parent.width
        spacing: 2 * screenScaleFactor
        UM.Label {
            objectName: root.objectName + "Axis"
            text: root.axis
            font: UM.Theme.getFont("small")
        }
        Cura.TextField {
            id: input
            objectName: root.objectName + "Input"
            width: parent.width
            height: 30 * screenScaleFactor
            placeholderText: "—"
            Accessible.name: "Absolute G-code " + root.axis + " target in millimetres; leave blank to keep unchanged"
        }
    }
}
