// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-01 [d8cf321] Revert "mpf's subpackages are capitalised"
// 2026-09-30 [f425547] mpf's subpackages are capitalised
// 2026-09-15 [c4829c1] 4.3.0 extraction: the Fan-speed section — FansSection.qml owns the per-fan sliders; the
//   slider interaction channel becomes a host sink so the freeze/refocus machinery stays single-owner, and the
//   tuning section rides the same contract; pins retarget same-pass

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../widgets"

// The PWM-outputs section (4.3.0 extraction): the per-output sliders
// out of the dashboard as one property-driven component. The host owns
// the freeze lists and the refocus settle; the sliders report
// interaction through the sink.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    property bool freezeRepeaters: false
    property var frozenItems: []
    property var interactionSink: null
    property var focusSink: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "PWM outputs"
        sectionId: "pwm"
        sectionIcon: "ThreeDots"
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel != null && root.printerModel.pwmOutputItems.length > 0 && root.printerModel.sectionExpandedMap["pwm"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        Repeater {
            id: pwmRepeater
            model: root.freezeRepeaters ? root.frozenItems : (root.printerModel != null ? root.printerModel.pwmOutputItems : [])
            ColumnLayout {
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    UM.Label {
                        text: modelData.name
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    UM.Label {
                        width: 52 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: pwmSlider.selectedValue() + "%"
                    }
                }
                OutlineSlider {
                    id: pwmSlider
                    Layout.fillWidth: true
                    controlObject: modelData.object
                    controlKind: "pwm"
                    from: 0
                    to: 100
                    stepSize: 1
                    live: false
                    value: modelData.percent
                    onValueTuning: {
                        if (root.printerModel != null)
                            root.printerModel.previewPwmOutput(modelData.object, value);
                    }
                    onValueCommitted: {
                        if (root.printerModel != null)
                            root.printerModel.setPwmOutput(modelData.object, value);
                    }
                    onInteractingChanged: {
                        if (root.interactionSink != null)
                            root.interactionSink(interacting, modelData.object, "pwm");
                    }
                    onFocusLostByDestruction: {
                        if (root.focusSink != null)
                            root.focusSink(object, kind);
                    }
                }
            }
        }
    }
}
