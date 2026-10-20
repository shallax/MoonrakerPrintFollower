// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-19 [a595e54] base: Ubuntu 26.04 — pinned images, runner labels, toolchain pins, and the 6.10-canonical
//   QML
// 2026-09-19 [450e38b] 4.5.0: in-bounds clicks keep the configure popovers open; the popover close glyphs are
//   gone; every bespoke checkbox and radio becomes Cura's native control
// 2026-09-17 [763abed] 4.4.0 live round: the configure popovers open (the row builders move to the document
//   roots), the collapsed readouts sit BELOW the restored titles in regular text, the reorder drag drops its
//   arrows for the live snap slot, and the shared all/none selector joins both popups

import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The shared all/none three-state selector (a live ruling): the
// native themed checkbox's tri-state — a tick at ALL, a filled
// square at SOME, empty at NONE. View-only: the host owns the counts
// and the action; a click emits toggled.
Row {
    id: selectorRoot
    spacing: UM.Theme.getSize("narrow_margin").width

    property int total: 0
    property int visibleCount: 0

    signal toggled

    UM.CheckBox {
        id: selectorCheck
        objectName: "visibilitySelectorBox"
        anchors.verticalCenter: parent.verticalCenter
        checkState: selectorRoot.visibleCount === 0 ? Qt.Unchecked : (selectorRoot.visibleCount === selectorRoot.total ? Qt.Checked : Qt.PartiallyChecked)
        text: "Show all"
        enabled: selectorRoot.total > 0
        onClicked: {
            selectorRoot.toggled();
            selectorCheck.checkState = Qt.binding(function () {
                return selectorRoot.visibleCount === 0 ? Qt.Unchecked : (selectorRoot.visibleCount === selectorRoot.total ? Qt.Checked : Qt.PartiallyChecked);
            });
        }
    }
}
