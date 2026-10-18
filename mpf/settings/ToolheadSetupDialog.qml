// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

import QtQuick 2.15
import UM 1.5 as UM

UM.Dialog {
    id: actionDialog
    objectName: "moonrakerToolheadSetupDialog"
    title: "Configure Moonraker"
    minimumWidth: UM.Theme.getSize("modal_window_minimum").width
    minimumHeight: UM.Theme.getSize("modal_window_minimum").height
    maximumWidth: minimumWidth * 3
    maximumHeight: minimumHeight * 3

    function openToolheadSettings() {
        loader.source = "";
        configurationManager.reset();
        loader.manager = configurationManager;
        loader.setSource("MoonrakerFollowerConfiguration.qml", {
            "initialTab": 1
        });
        show();
    }
}
