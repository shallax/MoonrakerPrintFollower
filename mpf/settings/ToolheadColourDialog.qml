// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-08 [ea3aa7d] Fix fan rotation and add toolhead colour and reflection controls

import QtQuick 2.15
import QtQuick.Dialogs

// Loaded only when the user opens the picker; opacity is edited separately.
ColorDialog {
    objectName: "toolheadColourDialog"
    title: "Selected parts colour"
}
