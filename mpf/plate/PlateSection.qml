// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-04 [c5fae60] Break mini-bed height layout feedback
// 2026-10-04 [95512c0] Polish monitor mini beds and refresh release screenshots
// 2026-09-20 [01aab4a] 4.6.0: the plate map — the exclude surface end to end: the restore-grace owner with the
//   exact cursor rule, the name-targeted dispatch with the guarded restore verb, the plate canvas with the
//   triple-click gesture, the Objects readout rework, and the dual-lane plate feed

import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../widgets"

// The Plate section (4.6.0): the mini map out of the monitor as one
// property-driven component, mirroring the bed-mesh section. The mini
// is the glance — outlines plus the toolhead dot only, never the
// layer path (dense outlines need their own full view); a click opens
// the pop-over, where the gesture lives.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    property real miniSlotWidth: 90 * screenScaleFactor
    readonly property real bedDepthRatio: printerModel != null && printerModel.bedMeshMachineWidth > 0 && printerModel.bedMeshMachineDepth > 0 ? printerModel.bedMeshMachineDepth / printerModel.bedMeshMachineWidth : 1.0
    signal popOverToggleRequested(string name)

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Exclude Object Picker"
        sectionId: "plate"
        sectionIcon: ""
        sectionIconUrl: Qt.resolvedUrl("../resources/svg/ObjectExclude.svg")
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["plate"] !== false
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height

        // NO-REFLOW: the bed-shaped slot is always reserved; the map fades
        // in and out and the placeholder overlays the same slot, so
        // the plate arriving (connect, print start) never shifts the
        // section.
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(90 * screenScaleFactor, root.miniSlotWidth * root.bedDepthRatio)

            PlateCanvas {
                id: plateMini
                anchors.fill: parent
                compact: true
                printerModel: root.printerModel
                plate: root.printerModel != null ? root.printerModel.plateObjects : null
                showAxisArrows: root.printerModel != null ? root.printerModel.followerShowAxisArrows : true
                // No toolhead dot — the mini map is the picker's
                // control surface, not a follower (the live ruling).
                opacity: root.printerModel != null && root.printerModel.plateObjects.objects.length > 0 ? 1 : 0
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.popOverToggleRequested("plate")
                }
            }

            UM.Label {
                anchors.centerIn: parent
                width: parent.width
                wrapMode: Text.WordWrap
                opacity: root.printerModel == null || root.printerModel.plateObjects.objects.length === 0 ? 1 : 0
                text: root.printerModel != null && root.printerModel.printActive ? "No objects yet — they appear as the print defines them." : "The plate appears while printing — EXCLUDE_OBJECT data arrives from the slicer."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("small")
                horizontalAlignment: Text.AlignHCenter
            }
        }
    }
}
