// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

import QtQuick 2.15

// The visible dock and Cura's extension row must reserve exactly the same
// scaled width and gap so neighbouring plugin controls cannot overlap it.
QtObject {
    readonly property real width: 280 * screenScaleFactor
    readonly property real gap: 12 * screenScaleFactor
}
