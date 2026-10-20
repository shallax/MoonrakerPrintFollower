// Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
// SPDX-License-Identifier: GPL-3.0-only
// Licensed under GNU GPLv3 (version 3 only); see LICENSE.
//
// Release history:
// 2026-10-20 — 6.0.0: Own render receipts, surface epochs, standing assets and consumer file references.

import QtQuick 2.15
import "PlateExactComposition.js" as ExactComposition

QtObject {
    id: state
    objectName: "moonrakerPlateDelivery"
    property var model: null
    property var references: []
    property var assetModel: null
    property int assetOwner: 0
    property var transaction: ExactComposition.empty(0, "")
    property var delivered: null
    property string worldKey: ""
    property string layerKey: ""
    property int epoch: 0
    property int frontBuffer: 0
    property int paintBuffer: -1
    property var standingPrefix: null
    property var retiredPrefixView: null
    property var retiredFullView: null
    property var standingFull: null
    property string gestureSource: ""
    property var classTexture: ({
            source: "",
            status: Image.Null
        })
    property var travelTexture: ({
            source: "",
            status: Image.Null
        })
    property var retainedTexture: ({
            source: "",
            status: Image.Null
        })
    property var prefixTexture: ({
            source: "",
            status: Image.Null
        })
    property bool pendingStanding: false
    property int pendingPaints: 0
    property var backgroundTransaction: ExactComposition.empty(0, "")
    property var backgroundReceipt: null
    property string lastBackgroundKey: ""

    function syncReferences() {
        if (assetModel != null && assetOwner > 0)
            assetModel.setPlateAssetReferences(assetOwner, references);
    }
    function release() {
        if (assetModel != null && assetOwner > 0)
            assetModel.releasePlateAssetOwner(assetOwner);
        assetOwner = 0;
        assetModel = null;
    }
    function adopt() {
        release();
        if (model != null && model.acquirePlateAssetOwner !== undefined) {
            assetModel = model;
            assetOwner = model.acquirePlateAssetOwner();
            syncReferences();
        }
    }
    onReferencesChanged: syncReferences()
    onModelChanged: adopt()
    Component.onCompleted: {
        if (assetOwner === 0)
            adopt();
    }
    Component.onDestruction: release()

    function holdGesture(source) {
        gestureSource = source;
        if (model != null)
            model.setFollowerGestureRaster(source);
    }
    function clearGesture() {
        gestureSource = "";
    }
    function releaseGesture() {
        if (model != null)
            model.setFollowerGestureRaster("");
    }
    function imageStatus(role, source, status) {
        var value = {
            source: source,
            status: status
        };
        if (role === "class")
            classTexture = value;
        else if (role === "travel")
            travelTexture = value;
        else if (role === "prefix")
            prefixTexture = value;
        else if (role === "retained")
            retainedTexture = value;
    }
    function adoptWorld(key, layer) {
        if (key === worldKey)
            return false;
        var layerChanged = layer !== layerKey;
        layerKey = layer;
        worldKey = key;
        epoch += 1;
        transaction = ExactComposition.newWorld(transaction, epoch, key);
        delivered = null;
        if (layerChanged) {
            standingPrefix = null;
            standingFull = null;
        }
        return true;
    }
    function enqueue(reset) {
        transaction = ExactComposition.enqueue(reset === true ? ExactComposition.unchanged(transaction) : transaction);
    }
    function unchanged() {
        transaction = ExactComposition.unchanged(transaction);
    }
    function request() {
        var next = ExactComposition.request(transaction);
        transaction = next.state;
        if (next.start)
            paintBuffer = frontBuffer === 0 ? 1 : 0;
        return next.start;
    }
    function painted(receipt, changed) {
        transaction = changed ? ExactComposition.painted(transaction, receipt) : ExactComposition.unchanged(transaction);
    }
    function prefixValid(source, view) {
        return retiredPrefixView == null || retiredPrefixView.source !== source || retiredPrefixView.view === view;
    }
    function receiptMatches(world, split, paintKey) {
        return transaction.count === 0 && delivered != null && delivered.epoch === epoch && delivered.world === world && delivered.split === split && delivered.paintKey === paintKey;
    }
    function coverageMatches(from, split, currentPrefix, retainedPrefix, inkless) {
        if (delivered == null)
            return false;
        if (from > 0) {
            var chosen = ExactComposition.choosePrefix(currentPrefix, retainedPrefix, split);
            return delivered.from === from && delivered.prefixSource === chosen.source;
        }
        return delivered.from === 0 || (inkless && delivered.from === -1);
    }
    function deliver(world, paintKey, anchor, view, bufferIndex) {
        var hadPaint = transaction.count > 0;
        var result = ExactComposition.delivered(transaction, epoch, world);
        if (result.accepted && bufferIndex !== undefined && result.receipt.paintKey !== paintKey) {
            result.accepted = false;
            result.state.pending = true;
        }
        transaction = result.state;
        if (hadPaint && result.accepted) {
            delivered = result.receipt;
            if (bufferIndex !== undefined)
                frontBuffer = bufferIndex;
        }
        paintBuffer = -1;
        if (result.accepted && prefixValid(result.receipt.prefixSource, view) && result.receipt.from > 0 && typeof result.receipt.prefixSource === "string" && result.receipt.prefixSource !== "") {
            standingPrefix = {
                source: result.receipt.prefixSource,
                from: result.receipt.from,
                anchor: anchor,
                world: result.receipt.world,
                view: view
            };
        }
        result.hadPaint = hadPaint;
        return result;
    }
    function capture(decision, picture) {
        if (decision.kind === "full" && (retiredFullView == null || retiredFullView.source !== picture.raster || retiredFullView.view === picture.view) && (standingFull == null || standingFull.world !== picture.world || standingFull.source !== picture.raster || standingFull.travels !== picture.travels)) {
            standingFull = {
                source: picture.raster,
                travels: picture.travels,
                anchor: picture.anchor,
                world: picture.world,
                view: picture.view
            };
        } else if (decision.kind !== "full" && decision.ready) {
            standingFull = null;
        }
        if (decision.prefix === "current" && decision.ready && prefixValid(picture.prefix.source, picture.view) && (standingPrefix == null || standingPrefix.world !== picture.world || standingPrefix.source !== picture.prefix.source || standingPrefix.from !== picture.prefix.from)) {
            standingPrefix = {
                source: picture.prefix.source,
                from: picture.prefix.from,
                anchor: picture.anchor,
                world: picture.world,
                view: picture.view
            };
        }
    }
    function retirePrefix() {
        standingPrefix = null;
    }
    function retireView() {
        if (standingPrefix != null)
            retiredPrefixView = standingPrefix;
        if (standingFull != null)
            retiredFullView = standingFull;
        if (standingPrefix != null && standingPrefix.source !== "")
            standingPrefix = null;
        standingFull = null;
    }
    function requestBackground(draws, key, lastKey) {
        if (draws || pendingStanding) {
            backgroundTransaction = ExactComposition.enqueue(ExactComposition.newWorld(backgroundTransaction, epoch, key));
            return true;
        }
        lastBackgroundKey = lastKey;
        backgroundReceipt = {
            world: key,
            valid: true,
            ink: false
        };
        return false;
    }
    function requestBackgroundPaint(key, available) {
        backgroundTransaction = ExactComposition.newWorld(backgroundTransaction, epoch, key);
        if (!available || !ExactComposition.needsPaint(backgroundTransaction))
            return false;
        var next = ExactComposition.request(backgroundTransaction);
        backgroundTransaction = next.state;
        return next.start;
    }
    function paintBackground(receipt, lastKey) {
        pendingPaints += 1;
        lastBackgroundKey = lastKey;
        backgroundTransaction = ExactComposition.painted(backgroundTransaction, receipt);
    }
    function deliverBackground(key) {
        var result = ExactComposition.delivered(backgroundTransaction, epoch, key);
        backgroundTransaction = result.state;
        backgroundReceipt = result.accepted ? result.receipt : null;
        if (result.accepted)
            pendingStanding = result.receipt.ink;
        return ExactComposition.needsPaint(backgroundTransaction);
    }
}
