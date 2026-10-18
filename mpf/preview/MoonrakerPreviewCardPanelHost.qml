// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-18 — 6.0.0: Preserve overflow hit testing when Cura replaces the action row.
// 2026-10-18 — 6.0.0: Restore rendered controls after Cura replaces a stage host.
// 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0
// 2026-10-05 [228f698] Finish Preview object banners for 5.1.0
// 2026-09-13 [e2ef237] 4.0.0 live-test round: the locked preview card, camera auto-start, controls nudge, dwell
//   fix, dialog and pane fixes

import QtQuick 2.15
import UM 1.5 as UM

// The action-panel host for the shared card: Cura's saveButton row
// contract. The row centres its components on a line two thick
// margins above the action panel's bottom, so this host reports a
// short strip and the card overflows upward from it — the row never
// grows, and Cura's Post Processing </> button and Slice panel keep
// their own docking. ActionPanelWidget already inserts one default
// margin between extensions; the strip reserves one more so the gap
// from </> to the card matches the gap from the card to Cura's panel.
Item {
    id: host
    objectName: "moonrakerPreviewCardPanelHost"

    // Cura reparents this shared component while its stage Loader destroys
    // the old Row. Qt 6.10+ can retain visible=true while dropping the card's
    // rendered nodes and mouse targets. Renew visibility on the next animation
    // tick, after that teardown. A zero timer/callLater runs too early.
    property bool parentedOnce: false
    onParentChanged: {
        if (host.parent === null)
            return;
        if (parentedOnce) {
            host.visible = false;
            restoreVisibility.restart();
        }
        parentedOnce = true;
    }
    Timer {
        id: restoreVisibility
        interval: 1
        onTriggered: host.visible = host.parent !== null
    }

    property real externalGap: UM.Theme.getSize("default_margin").width
    property real verticalPadding: UM.Theme.getSize("thick_margin").height
    ViewOptionsDockMetrics {
        id: dockMetrics
    }
    property real bannerWidth: dockMetrics.width
    property real bannerGap: dockMetrics.gap

    // Cura measures this width in its additional-components row. Reserve
    // the banner control's space here so other plugins move to its left.
    width: card.panelVisible ? externalGap + bannerWidth + bannerGap + card.panelWidth : 0
    height: card.panelVisible ? 4 * verticalPadding : 0

    // Keep the Row's measured strip height while making its direct child's
    // overflow explicit. Qt can otherwise cache the new Row as containing
    // every input target and reject clicks above it, overlooking the card's
    // already-cached overflow farther down the item tree. This transform and
    // the top anchor preserve the card's original bottom-aligned scene position.
    transform: Translate {
        y: host.height - card.height
    }

    MoonrakerPreviewCard {
        id: card
        anchors.right: parent.right
        anchors.top: parent.top
    }
}
