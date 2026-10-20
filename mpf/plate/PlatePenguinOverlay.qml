// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Release history:
// 2026-10-20 — 6.0.0: Own the optional click sequence and animated overlay independently of follower rendering.

import QtQuick 2.15
import "PenguinToolpaths.js" as Penguin
import "../resources/theme"

Item {
    id: state
    objectName: "moonrakerPlatePenguin"
    property bool compact: false
    property int clickCount: 0
    property real lastClickMs: 0
    property real pressMs: 0
    property bool active: false
    property real progress: 0

    function press() {
        pressMs = Date.now();
    }
    function release(button, dx, dy) {
        if (Date.now() - pressMs <= 350 && Math.abs(dx) <= 4 && Math.abs(dy) <= 4)
            recordClick(button);
        else
            cancelSequence();
    }
    function cancelSequence() {
        clickCount = 0;
    }

    function recordClick(button) {
        if (state.compact || state.active)
            return;
        var now = Date.now();
        if (state.clickCount > 0 && now - state.lastClickMs > 450)
            state.clickCount = 0;
        var expected = state.clickCount < 5 ? Qt.LeftButton : state.clickCount < 10 ? Qt.MiddleButton : Qt.RightButton;
        if (button === expected) {
            state.clickCount++;
        } else {
            state.clickCount = button === Qt.LeftButton ? 1 : 0;
        }
        state.lastClickMs = now;
        if (state.clickCount === 15) {
            state.clickCount = 0;
            state.active = true;
            penguinCanvas.drawn = 0;
            state.progress = 0;
            penguinCanvas.requestPaint();
            penguinFadeIn.start();
            penguinDrawDelay.start();
        }
    }

    anchors.fill: parent
    z: 100
    opacity: 0
    visible: state.active || opacity > 0

    Rectangle {
        anchors.fill: parent
        color: MoonrakerTheme.penguinBackdrop
    }
    Canvas {
        id: penguinCanvas
        anchors.fill: parent
        property real progress: state.progress
        property int drawn: 0
        onProgressChanged: requestPaint()
        onWidthChanged: {
            drawn = 0;
            requestPaint();
        }
        onHeightChanged: {
            drawn = 0;
            requestPaint();
        }
        onPaint: {
            var context = getContext("2d");
            var count = Math.floor(Penguin.strokes.length * progress);
            if (drawn === 0 || drawn > count)
                context.clearRect(0, 0, width, height);
            var side = Math.min(width, height);
            var left = (width - side) / 2;
            var top = (height - side) / 2;
            context.lineWidth = Math.max(1.2, side / 250 * 0.8);
            context.lineCap = "round";
            var colours = [MoonrakerTheme.penguinBlack, MoonrakerTheme.penguinWhite, MoonrakerTheme.penguinYellow];
            for (var index = drawn; index < count; index++) {
                var stroke = Penguin.strokes[index];
                context.strokeStyle = colours[stroke[4]];
                context.beginPath();
                context.moveTo(left + stroke[0] * side / 250, top + (250 - stroke[1]) * side / 250);
                context.lineTo(left + stroke[2] * side / 250, top + (250 - stroke[3]) * side / 250);
                context.stroke();
            }
            drawn = count;
        }
    }

    NumberAnimation {
        id: penguinFadeIn
        target: state
        property: "opacity"
        from: 0
        to: 1
        duration: 250
    }
    Timer {
        id: penguinDrawDelay
        interval: 250
        onTriggered: penguinDraw.start()
    }
    NumberAnimation {
        id: penguinDraw
        target: state
        property: "progress"
        from: 0
        to: 1
        duration: 1800
        onFinished: penguinHold.start()
    }
    Timer {
        id: penguinHold
        interval: 3000
        onTriggered: penguinFadeOut.start()
    }
    NumberAnimation {
        id: penguinFadeOut
        target: state
        property: "opacity"
        from: 1
        to: 0
        duration: 350
        onFinished: state.active = false
    }
}
