// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Selected Git history (newest first; oldest and latest two content changes):
// 2026-10-18 [b68f411] Complete print recovery controls, temperature adjustment and release documentation

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "../resources/theme"

ColumnLayout {
    id: root
    property var actions: []
    property var segments: []
    property var bedBounds: []
    property real bedZOffset: 0
    onBedBoundsChanged: plot.requestPaint()
    onBedZOffsetChanged: plot.requestPaint()
    property var restartPosition: [0, 0, 0]
    property string caption: ""
    readonly property var selectedAction: actions.length ? actions[Math.min(stepPicker.value - 1, actions.length - 1)] : ({
            "text": "",
            "position": [0, 0, 0],
            "from": [0, 0, 0]
        })
    function resetStep() {
        stepPicker.value = 1;
    }
    spacing: UM.Theme.getSize("thin_margin").height
    onSelectedActionChanged: plot.requestPaint()
    onSegmentsChanged: plot.requestPaint()
    onRestartPositionChanged: plot.requestPaint()
    RowLayout {
        Layout.fillWidth: true
        UM.Label {
            text: "Preview step"
        }
        SpinBox {
            id: stepPicker
            objectName: "recoveryPreviewStep"
            from: 1
            to: Math.max(1, root.actions.length)
            value: 1
            editable: true
        }
        UM.Label {
            text: "of " + root.actions.length
        }
    }
    UM.Label {
        objectName: "recoveryPreviewDescription"
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: root.selectedAction.text
    }
    UM.Label {
        objectName: "recoveryPreviewCoordinates"
        Layout.fillWidth: true
        text: "Nozzle: X" + Number(root.selectedAction.position[0]).toFixed(2) + "  Y" + Number(root.selectedAction.position[1]).toFixed(2) + "  Z" + Number(root.selectedAction.position[2]).toFixed(2) + " mm"
    }
    Canvas {
        id: plot
        objectName: "recoveryPreviewPlot"
        Layout.fillWidth: true
        Layout.preferredHeight: 440 * screenScaleFactor
        property color ink: UM.Theme.getColor("text")
        property color outline: UM.Theme.getColor("lining")
        property color accent: UM.Theme.getColor("primary")
        property color printed: MoonrakerTheme.recoveryPrinted
        property color paper: UM.Theme.getColor("main_background")
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
        onPaint: {
            var ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            ctx.fillStyle = paper;
            ctx.fillRect(0, 0, width, height);
            var points = [root.restartPosition, [0, 0, 0]];
            var bed = [];
            if (root.bedBounds.length === 5) {
                var b = root.bedBounds, bedZ = Number(b[2]) + root.bedZOffset;
                bed = [[b[0], b[1], bedZ], [b[3], b[1], bedZ], [b[3], b[4], bedZ], [b[0], b[4], bedZ]];
                points = points.concat(bed);
            }
            for (var i = 0; i < root.actions.length; ++i) {
                points.push(root.actions[i].from);
                points.push(root.actions[i].position);
            }
            var low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
            function include(point) {
                for (var axis = 0; axis < 3; ++axis) {
                    if (isFinite(point[axis])) {
                        low[axis] = Math.min(low[axis], point[axis]);
                        high[axis] = Math.max(high[axis], point[axis]);
                    }
                }
            }
            for (i = 0; i < points.length; ++i)
                include(points[i]);
            for (i = 0; i < root.segments.length; ++i) {
                include(root.segments[i].slice(0, 3));
                include(root.segments[i].slice(3, 6));
            }
            var axisLength = Math.max(1, high[0] - low[0], high[1] - low[1], high[2] - low[2]) * 0.2;
            for (var axisIndex = 0; axisIndex < 3; ++axisIndex) {
                var axisEnd = [0, 0, 0];
                axisEnd[axisIndex] = axisLength;
                include(axisEnd);
            }
            function drawView(left, top, horizontalAxis, verticalAxis, title, angled) {
                var w = width / 2 - 8, h = height / 2 - 8;
                function raw(point) {
                    return angled ? [(point[0] + point[1]) * 0.866, point[2] + (point[1] - point[0]) * 0.5] : [point[horizontalAxis], point[verticalAxis]];
                }
                var minU = Infinity, minV = Infinity, maxU = -Infinity, maxV = -Infinity;
                for (var corner = 0; corner < 8; ++corner) {
                    var value = raw([corner & 1 ? high[0] : low[0], corner & 2 ? high[1] : low[1], corner & 4 ? high[2] : low[2]]);
                    minU = Math.min(minU, value[0]);
                    maxU = Math.max(maxU, value[0]);
                    minV = Math.min(minV, value[1]);
                    maxV = Math.max(maxV, value[1]);
                }
                var scale = Math.min((w - 30) / Math.max(1, maxU - minU), (h - 64) / Math.max(1, maxV - minV));
                function project(point) {
                    var value = raw(point);
                    return [left + w / 2 + (value[0] - (minU + maxU) / 2) * scale, top + (h + 24) / 2 - (value[1] - (minV + maxV) / 2) * scale];
                }
                function edge(a, b) {
                    var start = project(a), end = project(b);
                    ctx.moveTo(start[0], start[1]);
                    ctx.lineTo(end[0], end[1]);
                }
                ctx.fillStyle = ink;
                ctx.font = "12px sans-serif";
                ctx.fillText(title, left + 8, top + 16);
                ctx.strokeStyle = outline;
                ctx.lineWidth = 1;
                ctx.strokeRect(left + 3, top + 24, w, h - 24);
                ctx.save();
                ctx.beginPath();
                ctx.rect(left + 4, top + 25, w - 2, h - 26);
                ctx.clip();
                if (bed.length) {
                    ctx.fillStyle = MoonrakerTheme.recoveryBedFill;
                    ctx.strokeStyle = MoonrakerTheme.recoveryBedOutline;
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    var first = project(bed[0]);
                    ctx.moveTo(first[0], first[1]);
                    for (var cornerIndex = 1; cornerIndex < bed.length; ++cornerIndex) {
                        var cornerPoint = project(bed[cornerIndex]);
                        ctx.lineTo(cornerPoint[0], cornerPoint[1]);
                    }
                    ctx.closePath();
                    if (angled || verticalAxis === 1)
                        ctx.fill();
                    ctx.stroke();
                }
                ctx.strokeStyle = printed;
                ctx.lineWidth = 2;
                ctx.beginPath();
                for (var j = 0; j < root.segments.length; ++j)
                    edge(root.segments[j].slice(0, 3), root.segments[j].slice(3, 6));
                ctx.stroke();
                ctx.globalAlpha = 1;
                ctx.strokeStyle = outline;
                ctx.lineWidth = 1;
                ctx.beginPath();
                for (j = 0; j < root.actions.length; ++j)
                    edge(root.actions[j].from, root.actions[j].position);
                ctx.stroke();
                ctx.strokeStyle = accent;
                ctx.lineWidth = 3;
                ctx.beginPath();
                edge(root.selectedAction.from, root.selectedAction.position);
                ctx.stroke();
                var target = project(root.restartPosition);
                ctx.strokeStyle = ink;
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(target[0] - 5, target[1] - 5);
                ctx.lineTo(target[0] + 5, target[1] + 5);
                ctx.moveTo(target[0] + 5, target[1] - 5);
                ctx.lineTo(target[0] - 5, target[1] + 5);
                ctx.stroke();
                var nozzle = project(root.selectedAction.position);
                ctx.fillStyle = accent;
                ctx.beginPath();
                ctx.arc(nozzle[0], nozzle[1], 5, 0, 2 * Math.PI);
                ctx.fill();
                var origin = project([0, 0, 0]);
                var axisColors = [MoonrakerTheme.axisX, MoonrakerTheme.axisY, MoonrakerTheme.axisZ];
                ctx.font = "bold 11px sans-serif";
                for (var a = 0; a < 3; ++a) {
                    var endpoint = [0, 0, 0];
                    endpoint[a] = axisLength;
                    var tip = project(endpoint), dx = tip[0] - origin[0], dy = tip[1] - origin[1];
                    var length = Math.sqrt(dx * dx + dy * dy);
                    ctx.strokeStyle = axisColors[a];
                    ctx.fillStyle = axisColors[a];
                    ctx.lineWidth = 2;
                    ctx.beginPath();
                    if (length > 0.1) {
                        var ux = dx / length, uy = dy / length;
                        ctx.moveTo(origin[0], origin[1]);
                        ctx.lineTo(tip[0], tip[1]);
                        ctx.moveTo(tip[0] - 6 * ux + 3 * uy, tip[1] - 6 * uy - 3 * ux);
                        ctx.lineTo(tip[0], tip[1]);
                        ctx.lineTo(tip[0] - 6 * ux - 3 * uy, tip[1] - 6 * uy + 3 * ux);
                        ctx.stroke();
                        ctx.fillText("XYZ"[a], tip[0] + 4, tip[1] - 4);
                    }
                }
                ctx.fillStyle = ink;
                ctx.fillText("0", origin[0] - 2, origin[1] + 15);
                ctx.restore();
            }
            drawView(0, 0, 0, 1, "3D · Front right", true);
            drawView(width / 2, 0, 0, 1, "Top · X / Y", false);
            drawView(0, height / 2, 0, 2, "Front · X / Z", false);
            drawView(width / 2, height / 2, 1, 2, "Side · Y / Z", false);
        }
    }
    UM.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "● Nozzle   × Restart point   Green: already-printed toolpath. Grey outline: bed / XY travel limits, to scale.\n" + root.caption + ". File coordinates; preview is not a collision check."
        color: UM.Theme.getColor("text_inactive")
    }
}
