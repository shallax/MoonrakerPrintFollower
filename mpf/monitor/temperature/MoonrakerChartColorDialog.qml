// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-09-23 [2ac0f5e] 4.6.0: the platform pass, the gesture identity and the camera nonce

import QtQuick 2.15
import QtQuick.Dialogs

// The chart's colour picker. It is its own document because the dialog
// implementation is a platform module (QtQuick.Dialogs): the monitor
// creates it on the first click, so building the monitor never waits on
// that module being usable on the host. The monitor owns the behaviour —
// it sets the title and connects accepted to the sensor-colour apply —
// so this file stays the plain dialog.
ColorDialog {}
