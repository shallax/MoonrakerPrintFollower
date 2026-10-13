import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: root
    // The QML engine also instantiates this component in isolation for
    // validation; the real machine action replaces this empty facade.
    property var settings: null
    readonly property var activeSettings: settings && settings.settingsRecoverySensorNames !== undefined ? settings : fallbackSettings
    readonly property var fallbackSettings: ({
            settingsRecoverySensorNames: [],
            settingsRecoveryAutoPause: true,
            settingsRecoverySavedState: "PAUSE_STATE",
            settingsRecoveryPrepareMacro: "",
            settingsRecoverySetupMacros: [],
            settingsRecoveryTransparentMacros: [],
            settingsRecoveryNozzleParameter: "EXTRUDER",
            settingsRecoveryBedParameter: "BED",
            settingsRecoveryLeadIn: 5,
            settingsRecoveryLeadInMode: "mm",
            settingsRecoveryLeadInMoves: 3,
            settingsRecoveryClearance: 5,
            settingsRecoveryXySpeed: 50,
            settingsRecoveryZSpeed: 5,
            settingsRecoveryPrimeEnabled: false,
            settingsRecoveryPrimeX: 5,
            settingsRecoveryPrimeY: 5,
            settingsRecoveryPrimeZ: 5,
            settingsRecoveryPrimeAmount: 20,
            settingsRecoveryPrimeSpeed: 2,
            settingsRecoveryCommandTimeout: 120,
            settingsRecoveryHeatingTimeout: 1800,
            settingsRecoveryStatusTimeout: 10,
            recoveryAvailableSensors: [],
            recoverySavedStates: ["PAUSE_STATE"],
            recoveryPrepareMacros: [],
            recoveryDiscoveryStatus: "Printer configuration unavailable",
            refreshRecoveryConfiguration: function () {}
        })
    property var selectedNames: activeSettings.settingsRecoverySensorNames.slice()
    property var setupNames: activeSettings.settingsRecoverySetupMacros.slice()
    property var transparentNames: activeSettings.settingsRecoveryTransparentMacros.slice()
    readonly property bool primeValid: inRange(primeXField.text, 0, 1000) && inRange(primeYField.text, 0, 1000) && inRange(primeZField.text, 0, 1000) && inRange(primeAmountField.text, 0.1, 200) && inRange(primeSpeedField.text, 0.1, 20)
    readonly property bool valid: inRange(leadInField.text, 0.1, 50) && inRange(leadInMovesField.text, 1, 100) && inRange(clearanceField.text, 1, 30) && inRange(xySpeedField.text, 1, 200) && inRange(zSpeedField.text, 0.5, 20) && (!primeEnabledBox.checked || primeValid) && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(nozzleParameterField.text.trim()) && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(bedParameterField.text.trim()) && inRange(timeoutField.text, 10, 600) && inRange(heatingTimeoutField.text, 60, 3600) && inRange(statusTimeoutField.text, 3, 60)
    readonly property var values: ({
            "recovery_sensor_names": selectedNames,
            "recovery_auto_pause": autoPauseBox.checked,
            "recovery_saved_state": savedStateBox.currentText,
            "recovery_prepare_macro": prepareMacroBox.currentIndex > 0 ? prepareMacroBox.currentText : "",
            "recovery_setup_macros": setupNames,
            "recovery_transparent_macros": transparentNames,
            "recovery_nozzle_parameter": nozzleParameterField.text.trim().toUpperCase(),
            "recovery_bed_parameter": bedParameterField.text.trim().toUpperCase(),
            "recovery_lead_in_mm": Number(leadInField.text),
            "recovery_lead_in_mode": leadInModeBox.currentIndex === 1 ? "moves" : "mm",
            "recovery_lead_in_moves": Number(leadInMovesField.text),
            "recovery_clearance_mm": Number(clearanceField.text),
            "recovery_xy_speed_mm_s": Number(xySpeedField.text),
            "recovery_z_speed_mm_s": Number(zSpeedField.text),
            "recovery_prime_enabled": primeEnabledBox.checked,
            "recovery_prime_x": primeValue(primeXField.text, 0, 1000, activeSettings.settingsRecoveryPrimeX),
            "recovery_prime_y": primeValue(primeYField.text, 0, 1000, activeSettings.settingsRecoveryPrimeY),
            "recovery_prime_z": primeValue(primeZField.text, 0, 1000, activeSettings.settingsRecoveryPrimeZ),
            "recovery_prime_amount_mm": primeValue(primeAmountField.text, 0.1, 200, activeSettings.settingsRecoveryPrimeAmount),
            "recovery_prime_speed_mm_s": primeValue(primeSpeedField.text, 0.1, 20, activeSettings.settingsRecoveryPrimeSpeed),
            "recovery_command_timeout_s": Number(timeoutField.text),
            "recovery_heating_timeout_s": Number(heatingTimeoutField.text),
            "recovery_status_timeout_s": Number(statusTimeoutField.text)
        })

    function inRange(value, minimum, maximum) {
        var number = Number(value);
        return String(value).trim() !== "" && isFinite(number) && number >= minimum && number <= maximum;
    }
    function primeValue(value, minimum, maximum, fallback) {
        return inRange(value, minimum, maximum) ? Number(value) : Number(fallback);
    }
    function choicesWithSaved(choices, saved) {
        var copy = choices ? choices.slice() : [];
        if (saved && copy.indexOf(saved) < 0)
            copy.push(saved);
        return copy;
    }
    function selectedPauseOwner() {
        if (selectedNames.length === 0)
            return false;
        var choices = activeSettings.recoveryAvailableSensors || [];
        for (var i = 0; i < choices.length; i++) {
            if (selectedNames.indexOf(choices[i].name) >= 0 && choices[i].pauseOwner === "follower")
                return true;
        }
        return false;
    }

    Flickable {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        contentWidth: width
        contentHeight: recoveryColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            objectName: "settingsScrollbar"
        }

        Column {
            id: recoveryColumn
            width: Math.max(0, parent.width - UM.Theme.getSize("scrollbar").width - 4 * screenScaleFactor)
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label {
                text: "Interrupted print recovery"
                font: UM.Theme.getFont("large_bold")
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Select the filament sensors to record. Klipper remains responsible for sensors with pause_on_runout enabled. For selected sensors with it disabled, the follower can request PAUSE after a confirmed runout transition."
            }
            UM.Label {
                width: parent.width
                text: activeSettings.recoveryDiscoveryStatus
                color: UM.Theme.getColor("text_inactive")
            }
            RowLayout {
                width: parent.width
                ComboBox {
                    id: sensorBox
                    objectName: "recoverySensorDropdown"
                    Layout.fillWidth: true
                    model: activeSettings.recoveryAvailableSensors
                    textRole: "label"
                }
                Cura.SecondaryButton {
                    text: "Add sensor"
                    enabled: sensorBox.currentIndex >= 0 && activeSettings.recoveryAvailableSensors.length > 0
                    onClicked: {
                        var name = activeSettings.recoveryAvailableSensors[sensorBox.currentIndex].name;
                        if (root.selectedNames.indexOf(name) < 0)
                            root.selectedNames = root.selectedNames.concat([name]);
                    }
                }
                Cura.SecondaryButton {
                    text: "Refresh"
                    onClicked: activeSettings.refreshRecoveryConfiguration()
                }
            }
            Repeater {
                model: root.selectedNames
                delegate: RowLayout {
                    width: recoveryColumn.width
                    UM.Label {
                        Layout.fillWidth: true
                        text: "▲ " + modelData
                        elide: Text.ElideRight
                    }
                    Cura.SecondaryButton {
                        text: "Remove"
                        onClicked: root.selectedNames = root.selectedNames.filter(function (name) {
                            return name !== modelData;
                        })
                    }
                }
            }
            UM.CheckBox {
                id: autoPauseBox
                objectName: "recoveryAutoPause"
                text: "Ask Klipper to PAUSE when a selected sensor does not pause itself"
                checked: activeSettings.settingsRecoveryAutoPause
                enabled: root.selectedPauseOwner()
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
                text: "Sensor events are still recorded when Klipper owns the pause. A first false sample after connecting is not treated as a new runout."
            }

            UM.Label {
                text: "Pause-state recovery"
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Source G-code macros require an explicit contract. A setup macro may move the nozzle or change E, so a complete absolute XYZ move and G92 E must follow it before the restart point. A transparent macro must not change coordinates, E, modes, transforms, or print geometry. Only select macros whose behavior you have verified."
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "If your slicer passes temperatures to a setup macro, enter its parameter names. Later explicit M104/M109 and M140/M190 commands in the G-code override these values."
            }
            UM.Label {
                text: "Setup macro nozzle temperature parameter"
            }
            Cura.TextField {
                id: nozzleParameterField
                width: parent.width
                text: activeSettings.settingsRecoveryNozzleParameter
            }
            UM.Label {
                text: "Setup macro bed temperature parameter"
            }
            Cura.TextField {
                id: bedParameterField
                width: parent.width
                text: activeSettings.settingsRecoveryBedParameter
            }
            UM.Label {
                text: "Setup macro in the G-code"
            }
            RowLayout {
                width: parent.width
                ComboBox {
                    id: setupMacroBox
                    Layout.fillWidth: true
                    model: activeSettings.recoveryPrepareMacros
                }
                Cura.SecondaryButton {
                    text: "Add"
                    enabled: setupMacroBox.currentIndex >= 0 && activeSettings.recoveryPrepareMacros.length > 0
                    onClicked: {
                        var name = setupMacroBox.currentText;
                        if (root.setupNames.indexOf(name) < 0 && root.transparentNames.indexOf(name) < 0)
                            root.setupNames = root.setupNames.concat([name]);
                    }
                }
            }
            Repeater {
                model: root.setupNames
                delegate: RowLayout {
                    width: recoveryColumn.width
                    UM.Label {
                        Layout.fillWidth: true
                        text: modelData
                        elide: Text.ElideRight
                    }
                    Cura.SecondaryButton {
                        text: "Remove"
                        onClicked: root.setupNames = root.setupNames.filter(function (name) {
                            return name !== modelData;
                        })
                    }
                }
            }
            UM.Label {
                text: "Transparent macro in the G-code"
            }
            RowLayout {
                width: parent.width
                ComboBox {
                    id: transparentMacroBox
                    Layout.fillWidth: true
                    model: activeSettings.recoveryPrepareMacros
                }
                Cura.SecondaryButton {
                    text: "Add"
                    enabled: transparentMacroBox.currentIndex >= 0 && activeSettings.recoveryPrepareMacros.length > 0
                    onClicked: {
                        var name = transparentMacroBox.currentText;
                        if (root.setupNames.indexOf(name) < 0 && root.transparentNames.indexOf(name) < 0)
                            root.transparentNames = root.transparentNames.concat([name]);
                    }
                }
            }
            Repeater {
                model: root.transparentNames
                delegate: RowLayout {
                    width: recoveryColumn.width
                    UM.Label {
                        Layout.fillWidth: true
                        text: modelData
                        elide: Text.ElideRight
                    }
                    Cura.SecondaryButton {
                        text: "Remove"
                        onClicked: root.transparentNames = root.transparentNames.filter(function (name) {
                            return name !== modelData;
                        })
                    }
                }
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                text: "Select the G-code state saved by your PAUSE macro. Stock Klipper uses PAUSE_STATE. A custom preparation macro is optional and must only restore that state without movement. Ordinary RESUME is not used for an earlier restart point."
            }
            UM.Label {
                text: "Saved G-code state"
            }
            ComboBox {
                id: savedStateBox
                objectName: "recoverySavedStateDropdown"
                width: parent.width
                model: root.choicesWithSaved(activeSettings.recoverySavedStates, activeSettings.settingsRecoverySavedState)
                currentIndex: model.indexOf(activeSettings.settingsRecoverySavedState)
            }
            UM.Label {
                text: "Preparation macro (optional)"
            }
            ComboBox {
                id: prepareMacroBox
                objectName: "recoveryPrepareMacroDropdown"
                width: parent.width
                model: ["Built-in saved-state restore"].concat(root.choicesWithSaved(activeSettings.recoveryPrepareMacros, activeSettings.settingsRecoveryPrepareMacro))
                currentIndex: activeSettings.settingsRecoveryPrepareMacro ? model.indexOf(activeSettings.settingsRecoveryPrepareMacro) : 0
            }

            UM.Label {
                text: "Suggested rewind unit"
            }
            ComboBox {
                id: leadInModeBox
                objectName: "recoveryLeadInMode"
                width: parent.width
                model: ["Millimetres of extrusion path", "Number of extrusion moves"]
                currentIndex: activeSettings.settingsRecoveryLeadInMode === "moves" ? 1 : 0
            }
            UM.Label {
                visible: leadInModeBox.currentIndex === 0
                text: "Suggested lead-in distance (mm)"
            }
            Cura.TextField {
                id: leadInField
                visible: leadInModeBox.currentIndex === 0
                width: parent.width
                text: String(activeSettings.settingsRecoveryLeadIn)
            }
            UM.Label {
                visible: leadInModeBox.currentIndex === 1
                text: "Suggested lead-in moves"
            }
            Cura.TextField {
                id: leadInMovesField
                visible: leadInModeBox.currentIndex === 1
                width: parent.width
                text: String(activeSettings.settingsRecoveryLeadInMoves)
            }
            UM.Label {
                text: "Clearance above printed height (mm)"
            }
            Cura.TextField {
                id: clearanceField
                width: parent.width
                text: String(activeSettings.settingsRecoveryClearance)
            }
            UM.Label {
                text: "Return XY speed (mm/s)"
            }
            Cura.TextField {
                id: xySpeedField
                width: parent.width
                text: String(activeSettings.settingsRecoveryXySpeed)
            }
            UM.Label {
                text: "Return Z speed (mm/s)"
            }
            Cura.TextField {
                id: zSpeedField
                width: parent.width
                text: String(activeSettings.settingsRecoveryZSpeed)
            }
            UM.Label {
                text: "Nozzle priming before return"
                font: UM.Theme.getFont("medium_bold")
            }
            UM.CheckBox {
                id: primeEnabledBox
                objectName: "recoveryPrimeEnabled"
                text: "Extrude filament at a chosen coordinate before returning to the print"
                checked: activeSettings.settingsRecoveryPrimeEnabled
            }
            UM.Label {
                width: parent.width
                wrapMode: Text.WordWrap
                enabled: primeEnabledBox.checked
                text: "Choose a clear location away from the printed object. Recovery lifts above the printed height, travels to this XY position, descends to this Z, primes, then lifts before travelling to the actual restart point. Priming Z must be at or above the selected restart layer."
            }
            UM.Label {
                enabled: primeEnabledBox.checked
                text: "Prime X (mm)"
            }
            Cura.TextField {
                id: primeXField
                enabled: primeEnabledBox.checked
                width: parent.width
                text: String(activeSettings.settingsRecoveryPrimeX)
            }
            UM.Label {
                enabled: primeEnabledBox.checked
                text: "Prime Y (mm)"
            }
            Cura.TextField {
                id: primeYField
                enabled: primeEnabledBox.checked
                width: parent.width
                text: String(activeSettings.settingsRecoveryPrimeY)
            }
            UM.Label {
                enabled: primeEnabledBox.checked
                text: "Prime Z (mm)"
            }
            Cura.TextField {
                id: primeZField
                enabled: primeEnabledBox.checked
                width: parent.width
                text: String(activeSettings.settingsRecoveryPrimeZ)
            }
            UM.Label {
                enabled: primeEnabledBox.checked
                text: "Filament to extrude (mm)"
            }
            Cura.TextField {
                id: primeAmountField
                enabled: primeEnabledBox.checked
                width: parent.width
                text: String(activeSettings.settingsRecoveryPrimeAmount)
            }
            UM.Label {
                enabled: primeEnabledBox.checked
                text: "Extrusion speed (mm/s)"
            }
            Cura.TextField {
                id: primeSpeedField
                enabled: primeEnabledBox.checked
                width: parent.width
                text: String(activeSettings.settingsRecoveryPrimeSpeed)
            }
            UM.Label {
                text: "Command acknowledgement timeout (seconds)"
            }
            Cura.TextField {
                id: timeoutField
                width: parent.width
                text: String(activeSettings.settingsRecoveryCommandTimeout)
            }
            UM.Label {
                text: "Temperature wait timeout (seconds)"
            }
            Cura.TextField {
                id: heatingTimeoutField
                width: parent.width
                text: String(activeSettings.settingsRecoveryHeatingTimeout)
            }
            UM.Label {
                text: "Fresh status timeout (seconds)"
            }
            Cura.TextField {
                id: statusTimeoutField
                width: parent.width
                text: String(activeSettings.settingsRecoveryStatusTimeout)
            }
            UM.Label {
                width: parent.width
                visible: !root.valid
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("error")
                text: "Use lead-in 0.1–50 mm or 1–100 moves, clearance 1–30 mm, XY speed 1–200 mm/s, Z speed 0.5–20 mm/s, prime XYZ 0–1000 mm, prime amount 0.1–200 mm, prime speed 0.1–20 mm/s, command timeout 10–600 seconds, heat wait 60–3600 seconds, and status timeout 3–60 seconds."
            }
        }
    }
}
