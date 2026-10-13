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
    property bool reviewed: false
    readonly property bool temperaturesReady: plan.ready === true && (plan.hotendTarget != null || (hotendField.text.trim() !== "" && isFinite(Number(hotendField.text)) && Number(hotendField.text) > 0)) && (plan.bedTarget != null || (bedField.text.trim() !== "" && isFinite(Number(bedField.text)) && Number(bedField.text) >= 0))
    readonly property bool zReferenceReady: !plan.unloaded || (zReferenceField.text.trim() !== "" && isFinite(Number(zReferenceField.text)) && Number(zReferenceField.text) >= 0 && Number(zReferenceField.text) <= Number(plan.z))
    function hotendTemperature() {
        return plan.hotendTarget != null ? Number(plan.hotendTarget) : Number(hotendField.text);
    }
    function bedTemperature() {
        return plan.bedTarget != null ? Number(plan.bedTarget) : Number(bedField.text);
    }
    onSelectionChanged: {
        reviewed = false;
        finalConfirm.close();
    }

    UM.Label {
        Layout.fillWidth: true
        text: "Recover an interrupted print"
        font: UM.Theme.getFont("medium_bold")
    }
    UM.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: "The follower sliders choose the suspected stop. Mark that point or choose a sensor marker, then optionally rewind to replay a short lead-in. Normal Resume simply continues a paused print; this workflow seeks backward and reprints toolpath."
        color: UM.Theme.getColor("text_inactive")
    }
    UM.Label {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: selection.offset !== undefined ? "Selected replay start: layer " + selection.layer + ", after move " + selection.completedMotions + " · X" + Number(selection.x).toFixed(3) + " Y" + Number(selection.y).toFixed(3) + " Z" + Number(selection.z).toFixed(3) + " · next byte " + selection.offset : "Choose an exact indexed move boundary. Download and index the print if needed."
    }
    UM.Label {
        Layout.fillWidth: true
        visible: plan.offset !== undefined && plan.offset >= 0
        wrapMode: Text.WordWrap
        text: "Verified restart: X" + Number(plan.x).toFixed(3) + " Y" + Number(plan.y).toFixed(3) + " Z" + Number(plan.z).toFixed(3) + " · next byte " + plan.offset + " · E" + Number(plan.e || 0).toFixed(4) + (plan.absoluteE ? " (accumulated absolute E)" : " (relative E)")
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
        text: plan.primeEnabled ? "Nozzle prime before return: " + Number(plan.primeAmount).toFixed(1) + " mm at " + Number(plan.primeSpeed).toFixed(1) + " mm/s, at X" + Number(plan.primeX).toFixed(3) + " Y" + Number(plan.primeY).toFixed(3) + " Z" + Number(plan.primeZ).toFixed(3) + ". Change or disable this in Recovery settings, then prepare again." : "Nozzle priming is off. Enable and configure it in Recovery settings if needed."
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
        text: "Clearance uses the highest extrusion Z in the indexed file (Z" + Number(plan.suggestedTopZ).toFixed(3) + " mm), including layers after the chosen restart. Home XYZ safely away from the object before recovery. The nozzle will lift above this bound before XY travel."
    }
    RowLayout {
        Layout.fillWidth: true
        visible: plan.ready === true && plan.unloaded
        UM.Label {
            text: "File Z at printer's homed Z0 (mm)"
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
        text: "Use 0 when Z was homed on the bed. If Z was homed on the existing print, enter that surface's original file Z. The reviewed restart at file Z" + Number(plan.z).toFixed(3) + " becomes printer Z" + (Number(plan.z) - Number(zReferenceField.text)).toFixed(3) + "."
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
        text: "▲ Runout and manual markers"
        font: UM.Theme.getFont("medium_bold")
    }
    Item {
        Layout.fillWidth: true
        Layout.fillHeight: true
        clip: true
        Column {
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
                        text: "Select"
                        enabled: modelData.layer !== null && modelData.layer !== undefined && modelData.split !== null && modelData.split !== undefined
                        onClicked: {
                            root.printerModel.setFollowerLayerAnchor(modelData.layer);
                            root.printerModel.setFollowerLayerProgress(modelData.split);
                        }
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
    RowLayout {
        Layout.fillWidth: true
        Cura.SecondaryButton {
            text: "Check G-code (read only)"
            enabled: root.selection.offset !== undefined && !root.plan.active && root.plan.phase !== "planning"
            onClicked: {
                root.reviewed = false;
                hotendField.text = "";
                bedField.text = "";
                root.printerModel.prepareRecovery();
            }
        }
        Cura.SecondaryButton {
            text: "Cancel"
            enabled: root.plan.active || root.plan.phase === "planning"
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
            text: root.plan.unloaded ? "Review recovery start…" : "Review printer rewind…"
            enabled: root.temperaturesReady && root.zReferenceReady && root.selection.offset === root.plan.offset
            onClicked: {
                root.reviewed = false;
                finalConfirm.open();
            }
        }
    }

    Popup {
        id: finalConfirm
        parent: root
        x: Math.max(0, (root.width - width) / 2)
        y: Math.max(0, (root.height - height) / 2)
        width: Math.min(470 * screenScaleFactor, root.width)
        height: Math.min(440 * screenScaleFactor, root.height)
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
            spacing: UM.Theme.getSize("thin_margin").height
            UM.Label {
                Layout.fillWidth: true
                text: root.plan.unloaded ? "Start this unloaded print at the chosen point?" : "Rewind this paused print?"
                font: UM.Theme.getFont("large_bold")
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: "File: " + (root.plan.filename || "") + (root.plan.unloaded ? "\nFile-coordinate return nozzle: X" : "\nReturn nozzle: X") + Number(root.plan.x).toFixed(3) + " Y" + Number(root.plan.y).toFixed(3) + " Z" + Number(root.plan.z).toFixed(3) + "\nReplay starts at byte " + root.plan.offset + " with E" + Number(root.plan.e).toFixed(4) + (root.plan.absoluteE ? " (absolute)" : " (relative)") + "."
            }
            UM.Label {
                Layout.fillWidth: true
                text: "Wait for nozzle " + root.hotendTemperature().toFixed(0) + " °C and bed " + root.bedTemperature().toFixed(0) + " °C before moving."
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: root.plan.primeEnabled ? "Prime " + Number(root.plan.primeAmount).toFixed(1) + " mm at " + Number(root.plan.primeSpeed).toFixed(1) + " mm/s at X" + Number(root.plan.primeX).toFixed(3) + " Y" + Number(root.plan.primeY).toFixed(3) + " Z" + Number(root.plan.primeZ).toFixed(3) + " before travelling to the restart point." : "No nozzle prime will be performed."
            }
            UM.Label {
                Layout.fillWidth: true
                visible: root.plan.unloaded === true
                text: "Conservative printed-height bound: file Z" + Number(root.plan.suggestedTopZ).toFixed(3) + " mm. File Z" + Number(root.plan.z).toFixed(3) + " resumes at printer Z" + (Number(root.plan.z) - Number(zReferenceField.text)).toFixed(3) + " mm."
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: root.plan.unloaded ? "This selects the G-code without running its print-start macro, moves the nozzle at or above the restart layer, then begins at the reviewed byte. Confirm the existing object and nozzle path are clear." : "This will pause if needed, move the nozzle, seek the paused file backward and reprint already completed toolpath. Restore filament and clear the nozzle first."
            }
            UM.CheckBox {
                Layout.fillWidth: true
                text: "I checked the restart position and replay path"
                checked: root.reviewed
                onToggled: root.reviewed = checked
            }
            RowLayout {
                Layout.fillWidth: true
                Cura.SecondaryButton {
                    text: "Back"
                    onClicked: finalConfirm.close()
                }
                Cura.PrimaryButton {
                    Layout.fillWidth: true
                    text: root.plan.unloaded ? "Load and restart from point" : "Rewind and restart print"
                    enabled: root.reviewed && root.temperaturesReady && root.zReferenceReady && root.selection.offset === root.plan.offset
                    onClicked: {
                        finalConfirm.close();
                        root.reviewed = false;
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
