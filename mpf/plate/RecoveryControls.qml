import QtQuick 2.15
import QtQuick.Layouts 1.3
import QtQuick.Controls 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

ColumnLayout {
    id: root
    property var printerModel: null
    signal closeRequested
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    Layout.fillHeight: true
    spacing: UM.Theme.getSize("thin_margin").height

    readonly property var selection: printerModel != null ? printerModel.recoverySelection : ({})
    readonly property var plan: printerModel != null ? printerModel.recoveryPlan : ({})
    readonly property var suggestion: plan.suggestion || ({})
    readonly property var events: printerModel != null ? printerModel.recoveryEvents : []
    readonly property var previousAttempt: printerModel != null ? printerModel.recoveryPreviousAttempt : ({})
    readonly property string selectionKey: selection.offset !== undefined ? [selection.layer, selection.completedMotions, selection.offset, selection.x, selection.y, selection.z].join("|") : ""
    property bool reviewed: false
    property bool resumeSubmitted: false
    readonly property bool temperaturesReady: plan.ready === true && (plan.hotendTarget != null || (hotendField.text.trim() !== "" && isFinite(Number(hotendField.text)) && Number(hotendField.text) > 0)) && (plan.bedTarget != null || (bedField.text.trim() !== "" && isFinite(Number(bedField.text)) && Number(bedField.text) >= 0))
    readonly property bool zReferenceReady: !plan.unloaded || (zReferenceField.text.trim() !== "" && isFinite(Number(zReferenceField.text)) && Number(zReferenceField.text) >= 0 && Number(zReferenceField.text) <= Number(plan.z))
    function hotendTemperature() {
        return plan.hotendTarget != null ? Number(plan.hotendTarget) : Number(hotendField.text);
    }
    function bedTemperature() {
        return plan.bedTarget != null ? Number(plan.bedTarget) : Number(bedField.text);
    }
    function resetView() {
        recoveryScroll.contentItem.contentY = 0;
        confirmScroll.contentItem.contentY = 0;
        stepPreview.resetStep();
    }
    function cappedZ(value) {
        return plan.maximumZ == null ? value : Math.min(value, Number(plan.maximumZ));
    }
    function primeHeight() {
        return cappedZ(Number(plan.primeZ) > Number(plan.z) ? Number(plan.primeZ) : Number(plan.z) + 5);
    }
    function recoveryActions() {
        function n(value) {
            return Number(value).toFixed(2);
        }
        var clearance = plan.clearance === undefined ? 5 : Number(plan.clearance);
        var zSpeed = plan.zSpeed === undefined ? 5 : Number(plan.zSpeed);
        var xySpeed = plan.xySpeed === undefined ? 50 : Number(plan.xySpeed);
        var travelZ = cappedZ(Number(plan.z) + clearance);
        var steps = [];
        var point = [Number(plan.currentX || 0), Number(plan.currentY || 0), Number(plan.currentZ || 0) + (plan.unloaded ? Number(zReferenceField.text) : 0)];
        function add(text, nextPoint) {
            var previous = point.slice();
            if (nextPoint !== undefined)
                point = nextPoint;
            steps.push({
                "text": text,
                "from": previous,
                "position": point.slice()
            });
        }
        if (plan.unloaded)
            add("Select the file without running its print-start macro.");
        add("Heat and wait for nozzle " + n(hotendTemperature()) + " °C" + (bedTemperature() > 0 ? " and bed " + n(bedTemperature()) + " °C" : "; turn the bed off") + ". Heating time varies.");
        if (plan.unloaded && Number(zReferenceField.text) > 0)
            add("Adjust the Z reference without moving: file Z" + n(plan.z) + " corresponds to printer Z" + n(Number(plan.z) - Number(zReferenceField.text)) + ".");
        else if (!plan.unloaded)
            add("Restore the saved print coordinates without moving.");
        if (plan.primeEnabled) {
            var primeTravelZ = Math.max(Number(plan.currentZ || 0) + (plan.unloaded ? Number(zReferenceField.text) : 0), Number(plan.z), primeHeight());
            primeTravelZ = cappedZ(primeTravelZ);
            add("Move vertically to Z" + n(primeTravelZ) + " at " + n(zSpeed * 2) + " mm/s.", [point[0], point[1], primeTravelZ]);
            add("Move to X" + n(plan.primeX) + " Y" + n(plan.primeY) + " at Z" + n(primeTravelZ) + " (" + n(xySpeed) + " mm/s).", [Number(plan.primeX), Number(plan.primeY), primeTravelZ]);
            add("Lower to Z" + n(primeHeight()) + " at " + n(zSpeed) + " mm/s (about " + n((primeTravelZ - primeHeight()) / zSpeed) + " s).", [point[0], point[1], primeHeight()]);
            add("Purge " + n(plan.primeAmount) + " mm of filament at " + n(plan.primeSpeed) + " mm/s; then dwell for " + n(plan.primeDwell === undefined ? 5 : plan.primeDwell) + " s.");
        }
        add("Move vertically to Z" + n(travelZ) + " at " + n(zSpeed * 2) + " mm/s.", [point[0], point[1], travelZ]);
        add("Move to X" + n(plan.x) + " Y" + n(plan.y) + " at Z" + n(travelZ) + " (" + n(xySpeed) + " mm/s).", [Number(plan.x), Number(plan.y), travelZ]);
        add("Check the file position and restore extrusion, fan and motion settings while above the print.");
        add("Lower to Z" + n(plan.z) + " at " + n(zSpeed) + " mm/s (about " + n((travelZ - Number(plan.z)) / zSpeed) + " s).", [Number(plan.x), Number(plan.y), Number(plan.z)]);
        add("Immediately resume printing on layer " + plan.resumeLayer + " at move " + plan.resumeMove + " (" + (Number(plan.resumeMove) - 1) + " completed moves on that layer; byte " + plan.offset + ").");
        return steps;
    }
    function recoverySteps() {
        return recoveryActions().map(function (step, index) {
            return (index + 1) + ". " + step.text;
        }).join("\n\n");
    }
    onSelectionKeyChanged: {
        reviewed = false;
        finalConfirm.close();
        // Apply after SpinBox limits update when a model/index arrives.
        // Qt can clamp the initial value against the old empty range.
        Qt.callLater(function () {
            restartLayer.value = root.selection.layer !== undefined ? root.selection.layer : 1;
            restartMove.value = root.selection.completedMotions !== undefined ? root.selection.completedMotions : 0;
        });
    }

    ScrollView {
        id: recoveryScroll
        objectName: "moonrakerRecoveryScroll"
        Layout.fillWidth: true
        Layout.fillHeight: true
        Layout.minimumHeight: 0
        clip: true
        contentWidth: availableWidth
        ColumnLayout {
            width: recoveryScroll.availableWidth
            spacing: UM.Theme.getSize("thin_margin").height
            UM.Label {
                Layout.fillWidth: true
                text: "Recover an interrupted print"
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Choose where printing should restart."
                color: UM.Theme.getColor("text_inactive")
            }
            RowLayout {
                Layout.fillWidth: true
                visible: root.printerModel != null && root.printerModel.plateLayerCount !== undefined && root.printerModel.plateLayerCount > 0
                UM.Label {
                    text: "Restart layer"
                }
                SpinBox {
                    id: restartLayer
                    objectName: "moonrakerRecoveryLayer"
                    from: 1
                    to: root.printerModel != null && root.printerModel.plateLayerCount !== undefined ? Math.max(1, root.printerModel.plateLayerCount) : 1
                    value: root.selection.layer !== undefined ? root.selection.layer : 1
                    editable: true
                    Layout.fillWidth: true
                }
                Cura.SecondaryButton {
                    objectName: "moonrakerRecoveryChooseLayer"
                    text: "Use layer start"
                    onClicked: {
                        root.reviewed = false;
                        root.printerModel.setFollowerLayerAnchor(restartLayer.value - 1);
                        root.printerModel.setFollowerLayerProgress(0);
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                UM.Label {
                    text: "Completed moves"
                }
                SpinBox {
                    id: restartMove
                    objectName: "moonrakerRecoveryMove"
                    from: 0
                    to: root.printerModel != null && root.printerModel.plateLayerMotionCount !== undefined ? root.printerModel.plateLayerMotionCount : 0
                    value: root.selection.completedMotions !== undefined ? root.selection.completedMotions : 0
                    editable: true
                    Layout.fillWidth: true
                }
                Cura.SecondaryButton {
                    objectName: "moonrakerRecoveryChooseMove"
                    text: "Use move"
                    enabled: restartMove.to > 0
                    onClicked: root.printerModel.setFollowerLayerProgress(restartMove.value)
                }
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Move 0 starts the layer; otherwise restart after the selected move."
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: selection.offset !== undefined ? "Layer " + selection.layer + ", after move " + selection.completedMotions + " · X" + Number(selection.x).toFixed(3) + " Y" + Number(selection.y).toFixed(3) + " Z" + Number(selection.z).toFixed(3) : "Choose an exact indexed move boundary. Download and index the print if needed."
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.offset !== undefined && plan.offset >= 0
                wrapMode: Text.WordWrap
                text: "G-code checked at the selected point."
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.ready === true
                text: "Latest file targets: nozzle " + (plan.hotendTarget == null ? "unknown" : Number(plan.hotendTarget).toFixed(0) + " °C") + ", bed " + (plan.bedTarget == null ? "unknown" : Number(plan.bedTarget).toFixed(0) + " °C")
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.ready === true
                wrapMode: Text.WordWrap
                text: plan.primeEnabled ? "Prime: " + Number(plan.primeAmount).toFixed(1) + " mm at " + Number(plan.primeSpeed).toFixed(1) + " mm/s, at X" + Number(plan.primeX).toFixed(3) + " Y" + Number(plan.primeY).toFixed(3) + " Z" + primeHeight().toFixed(3) + "." : "Priming off."
            }
            RowLayout {
                Layout.fillWidth: true
                visible: plan.ready === true && plan.hotendTarget == null
                UM.Label {
                    text: "Nozzle target (°C)"
                }
                Cura.TextField {
                    id: hotendField
                    Layout.fillWidth: true
                    text: ""
                }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: plan.ready === true && plan.bedTarget == null
                UM.Label {
                    text: "Bed target (°C, 0 if unheated)"
                }
                Cura.TextField {
                    id: bedField
                    Layout.fillWidth: true
                    text: ""
                }
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.ready === true && plan.unloaded
                wrapMode: Text.WordWrap
                text: "Home XYZ clear of the object first. Return travel uses the selected layer plus the configured clearance."
            }
            RowLayout {
                Layout.fillWidth: true
                visible: plan.ready === true && plan.unloaded
                UM.Label {
                    text: "Homed surface height (mm)"
                }
                Cura.TextField {
                    id: zReferenceField
                    Layout.fillWidth: true
                    text: "0"
                }
                Cura.SecondaryButton {
                    text: "Use restart Z"
                    onClicked: zReferenceField.text = Number(root.plan.z).toFixed(3)
                }
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.ready === true && plan.unloaded
                wrapMode: Text.WordWrap
                text: "0 = homed on the bed. If homed on the print, enter that surface’s original height."
            }
            RowLayout {
                Layout.fillWidth: true
                Cura.SecondaryButton {
                    text: "Mark stop estimate"
                    enabled: root.selection.offset !== undefined
                    onClicked: root.printerModel.markRecoveryStop()
                }
                Cura.SecondaryButton {
                    text: "Suggest lead-in"
                    enabled: root.selection.offset !== undefined
                    onClicked: {
                        root.reviewed = false;
                        root.printerModel.suggestRecoveryLeadIn();
                    }
                }
            }
            UM.Label {
                Layout.fillWidth: true
                visible: suggestion.detail !== undefined
                wrapMode: Text.WordWrap
                text: suggestion.detail || ""
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                Layout.fillWidth: true
                visible: suggestion.suspectLayer !== undefined
                wrapMode: Text.WordWrap
                text: "Suspected stop: layer " + suggestion.suspectLayer + " after move " + suggestion.suspectSplit + ". Replay begins: layer " + suggestion.startLayer + " after move " + suggestion.startSplit + "."
            }

            UM.Label {
                Layout.fillWidth: true
                text: "Stop bookmarks and runout markers"
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "Bookmarks save suspected stop points. Use point selects one for recovery; it does not start printing. Remove deletes the bookmark."
            }
            Item {
                Layout.fillWidth: true
                implicitHeight: markerList.implicitHeight
                Column {
                    id: markerList
                    width: parent.width
                    spacing: UM.Theme.getSize("thin_margin").height
                    Repeater {
                        model: root.events
                        delegate: RowLayout {
                            width: parent.width
                            UM.Label {
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                                text: "▲ " + (modelData.kind === "manual" ? "Estimate" : modelData.sensor) + (modelData.layer !== null && modelData.layer !== undefined ? " · layer " + (modelData.layer + 1) + " move " + modelData.split : " · location uncertain")
                            }
                            Cura.SecondaryButton {
                                text: "Use point"
                                objectName: "recoveryUseBookmark"
                                enabled: modelData.layer !== null && modelData.layer !== undefined && modelData.split !== null && modelData.split !== undefined
                                onClicked: {
                                    root.printerModel.setFollowerLayerAnchor(modelData.layer);
                                    root.printerModel.setFollowerLayerProgress(modelData.split);
                                }
                            }
                            Cura.SecondaryButton {
                                objectName: "recoveryRemoveBookmark"
                                text: "Remove"
                                onClicked: root.printerModel.removeRecoveryStop(modelData)
                            }
                        }
                    }
                }
            }
            UM.Label {
                Layout.fillWidth: true
                visible: printerModel != null && printerModel.recoveryPauseNote !== ""
                wrapMode: Text.WordWrap
                text: printerModel != null ? printerModel.recoveryPauseNote : ""
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.detail !== undefined
                wrapMode: Text.WordWrap
                text: plan.detail || ""
            }
            UM.Label {
                Layout.fillWidth: true
                visible: plan.phase === "idle" && previousAttempt.phase !== undefined
                wrapMode: Text.WordWrap
                text: "Previous attempt: " + (previousAttempt.phase || "unknown") + ". " + (previousAttempt.detail || "")
                color: UM.Theme.getColor("text_inactive")
            }
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Cura.SecondaryButton {
            text: "Check G-code (read only)"
            enabled: root.selection.offset !== undefined && !root.plan.active && root.plan.phase !== "planning" && root.plan.phase !== "checking_printer"
            onClicked: {
                root.reviewed = false;
                root.resumeSubmitted = false;
                hotendField.text = "";
                bedField.text = "";
                root.printerModel.prepareRecovery();
            }
        }
        Cura.SecondaryButton {
            text: "Cancel"
            enabled: root.plan.active || (root.plan.phase === "planning" || root.plan.phase === "checking_printer")
            onClicked: root.printerModel.cancelRecovery()
        }
    }
    RowLayout {
        Layout.fillWidth: true
        Cura.SecondaryButton {
            text: "Close"
            onClicked: root.closeRequested()
        }
        Cura.PrimaryButton {
            Layout.fillWidth: true
            objectName: "moonrakerRecoveryReview"
            text: root.plan.unloaded ? "Review recovery start…" : "Review restart…"
            enabled: root.temperaturesReady && root.zReferenceReady && root.selection.offset === root.plan.offset
            onClicked: {
                root.reviewed = false;
                finalConfirm.open();
            }
        }
    }

    Popup {
        id: finalConfirm
        onOpened: {
            confirmScroll.contentItem.contentY = 0;
            stepPreview.resetStep();
        }
        objectName: "moonrakerRecoveryConfirmation"
        parent: root
        x: Math.max(0, (root.width - width) / 2)
        y: Math.max(0, (root.height - height) / 2)
        width: Math.min(600 * screenScaleFactor, root.width)
        height: Math.min(650 * screenScaleFactor, root.height)
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape
        padding: UM.Theme.getSize("default_margin").width
        background: Rectangle {
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            border.width: UM.Theme.getSize("default_lining").width
            radius: UM.Theme.getSize("default_radius").width
        }
        contentItem: ColumnLayout {
            ScrollView {
                id: confirmScroll
                objectName: "moonrakerRecoveryConfirmationScroll"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 0
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: confirmScroll.availableWidth
                    spacing: UM.Theme.getSize("thin_margin").height
                    UM.Label {
                        Layout.fillWidth: true
                        text: root.plan.unloaded ? "Start this unloaded print at the chosen point?" : "Restart this paused print at the chosen point?"
                        font: UM.Theme.getFont("large_bold")
                    }
                    UM.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "File: " + (root.plan.filename || "") + (root.plan.unloaded ? "\nFile-coordinate return nozzle: X" : "\nReturn nozzle: X") + Number(root.plan.x).toFixed(3) + " Y" + Number(root.plan.y).toFixed(3) + " Z" + Number(root.plan.z).toFixed(3) + "\nReplay starts at byte " + root.plan.offset + " with E" + Number(root.plan.e).toFixed(4) + (root.plan.absoluteE ? " (absolute)" : " (relative)") + "."
                    }
                    RecoveryStepPreview {
                        id: stepPreview
                        Layout.fillWidth: true
                        actions: root.recoveryActions()
                        segments: root.plan.previewSegments || []
                        bedBounds: root.plan.bedBounds || []
                        bedZOffset: root.plan.unloaded ? Number(zReferenceField.text) : 0
                        caption: root.plan.previewCaption || "Recorded G-code progress"
                        restartPosition: [Number(root.plan.x), Number(root.plan.y), Number(root.plan.z)]
                    }
                    UM.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        objectName: "moonrakerRecoverySteps"
                        text: (root.plan.skipsForward ? "The selected point is ahead of the reported print position. Restarting here skips the intervening toolpath.\n\n" : "") + root.recoverySteps()
                    }
                    UM.Label {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        text: "Coordinates are in the file's frame. Times are estimates; acceleration and printer limits apply. The path is checked again before movement. Check that it clears the object."
                    }
                    UM.Label {
                        objectName: "moonrakerRecoveryAttemptStatus"
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        visible: root.plan.ready !== true
                        text: root.plan.detail || ""
                    }
                    UM.Label {
                        Layout.fillWidth: true
                        visible: root.plan.pauseState !== undefined && root.plan.pauseState !== ""
                        text: "Pre-park PAUSE state: " + (root.plan.pauseState || "")
                    }
                }
            }
            UM.CheckBox {
                Layout.fillWidth: true
                visible: root.plan.ready === true
                objectName: "moonrakerRecoveryReviewed"
                text: "I checked the restart position and replay path"
                checked: root.reviewed
                onToggled: root.reviewed = checked
            }
            RowLayout {
                Layout.fillWidth: true
                Cura.SecondaryButton {
                    objectName: "moonrakerRecoveryDismiss"
                    text: root.resumeSubmitted ? "Dismiss" : "Back"
                    onClicked: {
                        finalConfirm.close();
                        if (root.resumeSubmitted)
                            root.closeRequested();
                    }
                }
                Cura.PrimaryButton {
                    Layout.fillWidth: true
                    objectName: "moonrakerRecoveryStart"
                    text: root.plan.unloaded ? "Load and restart from point" : "Restart print from point"
                    enabled: root.reviewed && root.temperaturesReady && root.zReferenceReady && root.selection.offset === root.plan.offset
                    onClicked: {
                        root.reviewed = false;
                        root.resumeSubmitted = true;
                        root.printerModel.startRecovery({
                            "hotend": root.hotendTemperature(),
                            "bed": root.bedTemperature(),
                            "zReference": root.plan.unloaded ? Number(zReferenceField.text) : 0
                        });
                    }
                }
            }
        }
    }
}
