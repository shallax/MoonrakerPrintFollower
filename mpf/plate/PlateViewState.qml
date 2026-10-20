// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Release history:
// 2026-10-20 — 6.0.0: Own follower view/gesture state and focal animation through explicit renderer handoff signals.

import QtQuick 2.15
import "PlateViewPolicy.js" as ViewPolicy

Item {
    id: camera
    objectName: "moonrakerPlateViewState"
    visible: false
    property var plot: null
    property real viewportWidth: 0
    property real viewportHeight: 0
    property bool keepCentred: false
    property bool allowDirectPan: true
    property real viewScale: 1
    property real viewPanX: 0
    property real viewPanY: 0
    property real displayScale: 1
    property real displayPanX: 0
    property real displayPanY: 0
    property real zoomAnchorX: 0
    property real zoomAnchorY: 0
    property real zoomBedX: 0
    property real zoomBedY: 0
    property real panDragDeltaX: 0
    property real panDragDeltaY: 0
    property real pressX: 0
    property real pressY: 0
    property real pressPanX: 0
    property real pressPanY: 0
    property real dragX: 0
    property real dragY: 0
    property real softMargin: 100
    property bool interactionActive: false
    readonly property bool zoomRunning: zoomAnimator.running
    signal enterRequested
    signal manuallyPanned
    signal zoomSettled

    function activate() {
        interactionActive = true;
    }
    function end() {
        zoomAnimator.stop();
        interactionActive = false;
    }
    function startZoom() {
        zoomAnimator.restart();
    }
    function stopZoom() {
        zoomAnimator.stop();
    }
    function softClampPan(x, y) {
        return ViewPolicy.softClampPan(plot, viewScale, viewportWidth, viewportHeight, softMargin, x, y);
    }
    function panOnToolhead(x, y) {
        return ViewPolicy.centrePan(plot, x, y, viewScale, viewportWidth, viewportHeight);
    }
    function barZoomPan(target) {
        return softClampPan(ViewPolicy.focalPan(viewportWidth / 2, displayPanX, displayScale, target), ViewPolicy.focalPan(viewportHeight / 2, displayPanY, displayScale, target));
    }
    function centreOnToolhead(x, y) {
        var pan = panOnToolhead(x, y);
        if (pan == null)
            return false;
        viewPanX = pan.x;
        viewPanY = pan.y;
        displayPanX = pan.x;
        displayPanY = pan.y;
        enterRequested();
        return true;
    }
    function followToolhead(targetX, targetY, shownX, shownY) {
        var target = panOnToolhead(targetX, targetY);
        var display = panOnToolhead(shownX, shownY);
        if (target != null) {
            viewPanX = target.x;
            viewPanY = target.y;
        }
        if (display != null) {
            displayPanX = display.x;
            displayPanY = display.y;
        }
    }
    function press(x, y) {
        dragX = pressX = x;
        dragY = pressY = y;
        pressPanX = viewPanX;
        pressPanY = viewPanY;
    }
    function wheel(x, y, pixels, angle, pressed) {
        if (camera.plot == null) {
            return;
        }
        if (pixels === 0 && angle === 0)
            return;
        var target = ViewPolicy.wheelScale(pixels, angle, camera.viewScale, ViewPolicy.MAX_SCALE);
        if (target === camera.viewScale && camera.displayScale === camera.viewScale) {
            return;
        }
        camera.enterRequested();
        // The bed-fit pixel under the cursor AT THE TARGET: the
        // eased display pan derives from it every tick, so scale
        // and pan converge as one coherent camera transform and
        // the focal point never wanders.
        if (target <= 1.0) {
            // 100% is the fit: centred, panning off (the live
            // ruling — no pan at full zoom).
            camera.viewScale = 1.0;
            camera.viewPanX = 0.0;
            camera.viewPanY = 0.0;
        } else {
            // The point under the cursor stays put, computed
            // from the CURRENT DISPLAY transform (the retarget
            // always starts where the camera actually is). The
            // pan is only SOFT-clamped: the bed may never leave
            // the viewport wholly — _panSoftMargin pixels stay
            // visible on every side (the live ruling). Beyond
            // that the camera stays exactly where the focal
            // point puts it; only the 100% fit (above)
            // recentres.
            camera.viewScale = target;
            camera.viewPanX = ViewPolicy.focalPan(x, camera.displayPanX, camera.displayScale, target);
            camera.viewPanY = ViewPolicy.focalPan(y, camera.displayPanY, camera.displayScale, target);
            var clamped = camera.softClampPan(camera.viewPanX, camera.viewPanY);
            camera.viewPanX = clamped.x;
            camera.viewPanY = clamped.y;
        }
        camera.zoomAnchorX = x;
        camera.zoomAnchorY = y;
        camera.zoomBedX = ViewPolicy.focalBed(x, camera.viewPanX, camera.viewScale);
        camera.zoomBedY = ViewPolicy.focalBed(y, camera.viewPanY, camera.viewScale);
        // The wheel re-owns the camera promise: the ease runs
        // from the CURRENT display (held-drag pan included) and
        // the deltas from here on ride the glide.
        camera.panDragDeltaX = 0.0;
        camera.panDragDeltaY = 0.0;
        if (pressed) {
            // The wheel made a new target camera while the drag
            // still owns the pointer. Its old press pan belongs
            // to the PRE-zoom camera; using it on the next move
            // would jump back to that camera until the ease ends.
            camera.pressX = x;
            camera.pressY = y;
            camera.pressPanX = camera.viewPanX;
            camera.pressPanY = camera.viewPanY;
            camera.dragX = x;
            camera.dragY = y;
        }
        camera.startZoom();
    }
    function drag(x, y, pressed) {
        if (!pressed || camera.viewScale <= 1.0 || camera.displayScale <= 1.0) {
            return;
        }
        // The pan tracks the pointer directly: the display AND
        // the target move together (no pan easing — the nav
        // raster makes the direct pan cheap). The delta also
        // accumulates into the eased pan, so a drag during a
        // zoom converges exactly onto the dragged target. The
        // soft clamp applies the APPLIED delta: once the bed's
        // margin touches the viewport's edge, the camera stops
        // there while the pointer keeps moving.
        var dx = x - camera.dragX;
        var dy = y - camera.dragY;
        // The clamp works from the PRESS origin, not the live
        // view: while the entry gate holds, the view itself stays
        // frozen (its repaint pipeline is what smeared the lines
        // — the canvas tail re-baked per tick while the prefix
        // image content lagged), so the buffered drag must not
        // accumulate through the frozen target.
        var clamped = camera.softClampPan(camera.pressPanX + x - camera.pressX, camera.pressPanY + y - camera.pressY);
        var appliedX = clamped.x - camera.viewPanX;
        var appliedY = clamped.y - camera.viewPanY;
        // The interaction raster flips in only when this
        // movement pans for real: a click's zero delta and a
        // boundary-blocked drag never activate it (the review's
        // ruling — no warm-raster transition on inert gestures).
        if (appliedX !== 0.0 || appliedY !== 0.0) {
            if (camera.keepCentred)
                camera.manuallyPanned();
            camera.enterRequested();
        }
        // The whole camera (target, display AND the raster's
        // presentation) moves only once the interaction has
        // entered — then in one coherent jump to the clamped
        // target. During the gate's hold nothing moves: no
        // smear, no mixed-rate drag. With NO eligible raster at
        // all the camera still follows the pointer directly —
        // the degraded exact-scene path must keep working (the
        // freeze is only the hold, never a dead drag).
        if (camera.interactionActive || camera.allowDirectPan) {
            camera.viewPanX = clamped.x;
            camera.viewPanY = clamped.y;
            // During a wheel ease the display trails the target.
            // Translate BOTH cameras by the applied drag instead
            // of snapping the display straight to that target.
            camera.displayPanX += appliedX;
            camera.displayPanY += appliedY;
        }
        camera.panDragDeltaX += appliedX;
        camera.panDragDeltaY += appliedY;
        camera.dragX = x;
        camera.dragY = y;
    }
    function scopeZoom(target) {
        enterRequested();
        viewScale = target;
        if (target <= 1) {
            viewPanX = 0;
            viewPanY = 0;
            displayPanX = 0;
            displayPanY = 0;
            displayScale = target;
        } else {
            var pan = barZoomPan(target);
            viewPanX = pan.x;
            viewPanY = pan.y;
            displayPanX = pan.x;
            displayPanY = pan.y;
            displayScale = target;
        }
    }
    function resetTarget() {
        viewScale = 1;
        viewPanX = viewPanY = 0;
    }
    function fitNow() {
        if (viewScale <= 1)
            return;
        enterRequested();
        viewScale = 1;
        viewPanX = 0;
        viewPanY = 0;
        displayScale = 1;
        displayPanX = 0;
        displayPanY = 0;
        stopZoom();
    }
    Timer {
        id: zoomAnimator
        interval: 16
        repeat: true
        onTriggered: {
            var next = ViewPolicy.easeScale(camera.displayScale, camera.viewScale, 0.30);
            camera.displayScale = next;
            camera.displayPanX = ViewPolicy.glidePan(camera.zoomAnchorX, camera.zoomBedX, next, camera.panDragDeltaX);
            camera.displayPanY = ViewPolicy.glidePan(camera.zoomAnchorY, camera.zoomBedY, next, camera.panDragDeltaY);
            if (next === camera.viewScale) {
                stop();
                camera.zoomSettled();
            }
        }
    }
}
