// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-08 [e2f777d] Add toolhead materials, scene reflections and fan animation
// 2026-10-06 [ab3a658] Add custom toolheads and lighting for v5.2.0

import QtQuick 2.15
Item {
    property var model: null
    property bool picking: false
    property bool addingLight: false
    property bool addingRotor: false
    property bool selectingOpacity: false
    property bool paintingMaterial: false
    property bool slowRotation: false
    property bool animationAvailable: true
    function resetCamera() {}
    property int orbitCalls: 0
    property int panCalls: 0
    property int pickCalls: 0
    function orbit(dx, dy) { orbitCalls++; }
    function pan(dx, dy) { panCalls++; }
    function zoomBy(delta) {}
    function pick(x, y) { if (picking || addingLight) pickCalls++; }
}
