// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
// 2026-09-30 [f425547] mpf's subpackages are capitalised
// 2026-09-16 [7a4dd40] 4.3.0 extraction: the Fans, Filament-sensors and Objects sections — three property-driven
//   components; the host keeps the capability gates and the exclude-object dialog behind a confirmation signal;
//   pins retarget same-pass

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../widgets"

// The Fans section (4.3.0 extraction): the per-fan status rows out
// of the monitor as one property-driven component. The host keeps
// the capability gate.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Fans"
        sectionId: "fansinfo"
        sectionIcon: "Fan"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["fansinfo"] !== false
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height / 2

        Repeater {
            model: root.printerModel != null ? root.printerModel.fanItems : []
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width
                UM.Label {
                    text: modelData.name
                    color: UM.Theme.getColor("text_inactive")
                    Layout.preferredWidth: 110 * screenScaleFactor
                    elide: Text.ElideRight
                }
                UM.Label {
                    text: modelData.detail
                }
            }
        }
    }
}
