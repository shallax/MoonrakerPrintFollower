// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-27 [bed0492] Harden RC networking, cache lifecycle, motion coordinates and UI diagnostics
// 2026-09-27 [dd99358] Mirror Cura preview colours and travel states and composite ghost opacity on GPU
// 2026-09-27 [43ab36d] Accelerate follower rendering with retained GPU shader geometry

import QtQuick 2.15
Item {
    property var dataSource: null
    property bool supported: false
    property bool ready: false
    property string error: ""
    property var layers: ({})
    property var settings: ({})
}
