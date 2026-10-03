import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: base
    required property var settings
    property bool validUrl: settings.validUrl(urlField.text)
    property bool insecureKeyWarning: settings.insecureKeyWarning(urlField.text, apiKeyField.text)
    property bool validPollInterval: true
    property bool pollIntervalMoved: false
    property bool validAuxInterval: true
    property bool validConsoleInterval: true
    property bool followingEnabled: false
    readonly property bool connectionRequested: followingEnabled || (urlField.text.trim() !== "" && urlField.text.trim() !== "http://" && urlField.text.trim() !== "https://")
    readonly property var values: ({
            "url": urlField.text,
            "api_key": apiKeyField.text,
            "feed_mode": websocketMode.checked ? "websocket" : "http",
            "poll_interval_ms": base.pollIntervalMoved ? Math.round(250 * Math.pow(2, pollIntervalSlider.value)) : settings.settingsPollInterval,
            "aux_interval_ms": auxIntervalSlider.value,
            "console_interval_ms": consoleIntervalSlider.value
        })
    Flickable {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        contentWidth: width
        contentHeight: connectionColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            objectName: "settingsScrollbar"
        }

        Column {
            id: connectionColumn
            width: Math.max(0, parent.width - UM.Theme.getSize("scrollbar").width - 4 * screenScaleFactor)
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label {
                text: "One Moonraker connection is shared by live following and Cura uploads."
                wrapMode: Text.WordWrap
                width: parent.width
                color: UM.Theme.getColor("text_inactive")
            }

            UM.Label {
                text: "Moonraker URL"
            }
            Cura.TextField {
                id: urlField
                width: parent.width
                text: settings.settingsUrl
                placeholderText: "http://printer.example.invalid:7125"
                maximumLength: 1024
                onTextChanged: base.validUrl = settings.validUrl(text)
            }
            UM.Label {
                // Only once a URL is actually typed: an
                // untouched placeholder must not moan on a
                // fresh dialog (following is on by
                // default, so connectionRequested is
                // already true on first open).
                visible: base.connectionRequested && !base.validUrl && urlField.text.trim() !== ""
                text: "Enter a valid HTTP or HTTPS Moonraker URL."
                color: UM.Theme.getColor("error")
                font: UM.Theme.getFont("default_italic")
            }

            UM.Label {
                text: "API key (optional)"
            }
            Cura.TextField {
                id: apiKeyField
                width: parent.width
                text: settings.settingsApiKey
                echoMode: TextInput.Password
                maximumLength: 4096
            }
            UM.Label {
                visible: base.insecureKeyWarning
                text: "The API key is sent unencrypted over HTTP — anyone on the same network can read it. Use https:// or a local-only address for the key to protect it."
                color: UM.Theme.getColor("warning")
                wrapMode: Text.WordWrap
                width: parent.width
            }

            UM.Label {
                text: "Printer status transport"
                font: UM.Theme.getFont("medium_bold")
            }
            ButtonGroup {
                id: transportModeGroup
            }
            Cura.RadioButton {
                id: websocketMode
                ButtonGroup.group: transportModeGroup
                text: "WebSocket subscription"
                checked: settings.settingsTransportMode === "websocket"
            }
            Cura.RadioButton {
                id: httpMode
                ButtonGroup.group: transportModeGroup
                text: "HTTP polling"
                checked: settings.settingsTransportMode === "http"
            }
            UM.Label {
                // The permanent reason slot: the text changes,
                // the row never appears or disappears.
                text: settings.transportStatus
                wrapMode: Text.WordWrap
                width: parent.width
                font: UM.Theme.getFont("default_italic")
            }

            UM.Label {
                text: "Status update interval (milliseconds)"
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width
                Slider {
                    id: pollIntervalSlider
                    Layout.fillWidth: true
                    // Log-spaced: each step doubles the interval,
                    // so the short end keeps usable precision
                    // while the top end reaches ~34 minutes.
                    from: 0
                    to: 13
                    stepSize: 1
                    // The click behaviours (a live
                    // report): a press that lands within the
                    // handle's extent of the current value is a
                    // no-op — a click on the grab handle must
                    // not move it; and a click focuses the
                    // slider so the arrow keys nudge one step.
                    focusPolicy: Qt.StrongFocus
                    property bool handlePress: false
                    property bool handleDragged: false
                    property real valueBeforePress: 0
                    function pressIsOnHandle(mouseX) {
                        // The handle's own extent: the painted LEFT
                        // edge minus the width correction — the window
                        // centres on the handle's middle and spans its
                        // full width plus the margin (the reviewer's
                        // finding: one side of the grab handle moved
                        // the slider, the other never grabbed).
                        var leftEdge = leftPadding + visualPosition * (availableWidth - handle.width);
                        var centre = leftEdge + handle.width / 2;
                        return Math.abs(mouseX - centre) <= handle.width / 2 + 2 * screenScaleFactor;
                    }
                    MouseArea {
                        anchors.fill: parent
                        onPressed: function (mouse) {
                            parent.forceActiveFocus();
                            parent.valueBeforePress = parent.value;
                            parent.handleDragged = false;
                            parent.handlePress = parent.pressIsOnHandle(mouse.x);
                            mouse.accepted = parent.handlePress;
                        }
                        onPositionChanged: function (mouse) {
                            if (!parent.handlePress) {
                                return;
                            }
                            var steps = Math.round((mouse.x - parent.leftPadding) / Math.max(1, parent.availableWidth) * (parent.to - parent.from) / parent.stepSize);
                            parent.value = Math.max(parent.from, Math.min(parent.to, parent.from + steps * parent.stepSize));
                            if (Math.abs(parent.value - parent.valueBeforePress) > 0.001) {
                                parent.handleDragged = true;
                            }
                            base.pollIntervalMoved = true;
                        }
                        onReleased: function (mouse) {
                            if (!parent.handlePress) {
                                return;
                            }
                            parent.handlePress = false;
                            if (!parent.handleDragged) {
                                parent.value = parent.valueBeforePress;
                            }
                            mouse.accepted = true;
                        }
                    }
                    Keys.onUpPressed: {
                        increase();
                        base.pollIntervalMoved = true;
                        // The apply must not steal the
                        // keyboard path (the reviewer's
                        // finding).
                        forceActiveFocus();
                    }
                    Keys.onDownPressed: {
                        decrease();
                        base.pollIntervalMoved = true;
                        forceActiveFocus();
                    }
                    Keys.onRightPressed: {
                        increase();
                        base.pollIntervalMoved = true;
                        forceActiveFocus();
                    }
                    Keys.onLeftPressed: {
                        decrease();
                        base.pollIntervalMoved = true;
                        forceActiveFocus();
                    }
                    value: Number(settings.settingsPollInterval) > 0 ? Math.max(0, Math.min(13, Math.log2(Number(settings.settingsPollInterval) / 250))) : 1
                    onMoved: {
                        // The groove path (clicks and drags
                        // outside the handle); the handle path
                        // never reaches this handler.
                        base.pollIntervalMoved = true;
                        pollIntervalValueLabel.text = Math.round(250 * Math.pow(2, value)) + " ms";
                    }
                    onValueChanged: {
                        pollIntervalValueLabel.text = Math.round(250 * Math.pow(2, value)) + " ms";
                    }
                }
                UM.Label {
                    id: pollIntervalValueLabel
                    Layout.preferredWidth: 90 * screenScaleFactor
                    horizontalAlignment: Text.AlignRight
                    text: (Number(settings.settingsPollInterval) > 0 ? Number(settings.settingsPollInterval) : 750) + " ms"
                    font: UM.Theme.getFont("default")
                }
            }
            UM.Label {
                text: settings.settingsTransportMode === "websocket" ? "Status arrives from the printer about every 250 ms; this sets how often Cura applies it. Values below 250 ms show no fresher data and do not affect the printer." : "This is how often Cura asks the printer for a full status update. Each request costs the printer serialization work — values below 250 ms load it heavily with no fresher data."
                wrapMode: Text.WordWrap
                width: parent.width
                font: UM.Theme.getFont("default_italic")
            }

            UM.Label {
                text: "Auxiliary status interval (milliseconds)"
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width
                Slider {
                    id: auxIntervalSlider
                    Layout.fillWidth: true
                    from: 250
                    to: 60000
                    stepSize: 250
                    // The click behaviours: see pollIntervalSlider.
                    focusPolicy: Qt.StrongFocus
                    property bool handlePress: false
                    property bool handleDragged: false
                    property real valueBeforePress: 0
                    function pressIsOnHandle(mouseX) {
                        // The handle's own extent: the painted LEFT
                        // edge minus the width correction — the window
                        // centres on the handle's middle and spans its
                        // full width plus the margin (the reviewer's
                        // finding: one side of the grab handle moved
                        // the slider, the other never grabbed).
                        var leftEdge = leftPadding + visualPosition * (availableWidth - handle.width);
                        var centre = leftEdge + handle.width / 2;
                        return Math.abs(mouseX - centre) <= handle.width / 2 + 2 * screenScaleFactor;
                    }
                    MouseArea {
                        anchors.fill: parent
                        onPressed: function (mouse) {
                            parent.forceActiveFocus();
                            parent.valueBeforePress = parent.value;
                            parent.handleDragged = false;
                            parent.handlePress = parent.pressIsOnHandle(mouse.x);
                            mouse.accepted = parent.handlePress;
                        }
                        onPositionChanged: function (mouse) {
                            if (!parent.handlePress) {
                                return;
                            }
                            var steps = Math.round((mouse.x - parent.leftPadding) / Math.max(1, parent.availableWidth) * (parent.to - parent.from) / parent.stepSize);
                            parent.value = Math.max(parent.from, Math.min(parent.to, parent.from + steps * parent.stepSize));
                            if (Math.abs(parent.value - parent.valueBeforePress) > 0.001) {
                                parent.handleDragged = true;
                            }
                        }
                        onReleased: function (mouse) {
                            if (!parent.handlePress) {
                                return;
                            }
                            parent.handlePress = false;
                            if (!parent.handleDragged) {
                                parent.value = parent.valueBeforePress;
                            }
                            mouse.accepted = true;
                        }
                    }
                    Keys.onUpPressed: {
                        increase();
                        forceActiveFocus();
                    }
                    Keys.onDownPressed: {
                        decrease();
                        forceActiveFocus();
                    }
                    Keys.onRightPressed: {
                        increase();
                        forceActiveFocus();
                    }
                    Keys.onLeftPressed: {
                        decrease();
                        forceActiveFocus();
                    }
                    value: Number(settings.settingsAuxInterval) > 0 ? Number(settings.settingsAuxInterval) : 2500
                    onMoved: {
                        auxIntervalValueLabel.text = value + " ms";
                    }
                    onValueChanged: {
                        auxIntervalValueLabel.text = value + " ms";
                    }
                }
                UM.Label {
                    id: auxIntervalValueLabel
                    Layout.preferredWidth: 80 * screenScaleFactor
                    horizontalAlignment: Text.AlignRight
                    text: settings.settingsAuxInterval + " ms"
                    font: UM.Theme.getFont("default")
                }
            }
            UM.Label {
                text: "Temperature, fan and sensor updates. Websocket data arrives from the printer about every 250 ms, so values below that show no fresher data."
                wrapMode: Text.WordWrap
                width: parent.width
                font: UM.Theme.getFont("default_italic")
            }

            UM.Label {
                text: "Console output interval (milliseconds)"
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width
                Slider {
                    id: consoleIntervalSlider
                    Layout.fillWidth: true
                    from: 250
                    to: 60000
                    stepSize: 250
                    // The click behaviours: see pollIntervalSlider.
                    focusPolicy: Qt.StrongFocus
                    property bool handlePress: false
                    property bool handleDragged: false
                    property real valueBeforePress: 0
                    function pressIsOnHandle(mouseX) {
                        // The handle's own extent: the painted LEFT
                        // edge minus the width correction — the window
                        // centres on the handle's middle and spans its
                        // full width plus the margin (the reviewer's
                        // finding: one side of the grab handle moved
                        // the slider, the other never grabbed).
                        var leftEdge = leftPadding + visualPosition * (availableWidth - handle.width);
                        var centre = leftEdge + handle.width / 2;
                        return Math.abs(mouseX - centre) <= handle.width / 2 + 2 * screenScaleFactor;
                    }
                    MouseArea {
                        anchors.fill: parent
                        onPressed: function (mouse) {
                            parent.forceActiveFocus();
                            parent.valueBeforePress = parent.value;
                            parent.handleDragged = false;
                            parent.handlePress = parent.pressIsOnHandle(mouse.x);
                            mouse.accepted = parent.handlePress;
                        }
                        onPositionChanged: function (mouse) {
                            if (!parent.handlePress) {
                                return;
                            }
                            var steps = Math.round((mouse.x - parent.leftPadding) / Math.max(1, parent.availableWidth) * (parent.to - parent.from) / parent.stepSize);
                            parent.value = Math.max(parent.from, Math.min(parent.to, parent.from + steps * parent.stepSize));
                            if (Math.abs(parent.value - parent.valueBeforePress) > 0.001) {
                                parent.handleDragged = true;
                            }
                        }
                        onReleased: function (mouse) {
                            if (!parent.handlePress) {
                                return;
                            }
                            parent.handlePress = false;
                            if (!parent.handleDragged) {
                                parent.value = parent.valueBeforePress;
                            }
                            mouse.accepted = true;
                        }
                    }
                    Keys.onUpPressed: {
                        increase();
                        forceActiveFocus();
                    }
                    Keys.onDownPressed: {
                        decrease();
                        forceActiveFocus();
                    }
                    Keys.onRightPressed: {
                        increase();
                        forceActiveFocus();
                    }
                    Keys.onLeftPressed: {
                        decrease();
                        forceActiveFocus();
                    }
                    value: Number(settings.settingsConsoleInterval) > 0 ? Number(settings.settingsConsoleInterval) : 1000
                    onMoved: {
                        consoleIntervalValueLabel.text = value + " ms";
                    }
                    onValueChanged: {
                        consoleIntervalValueLabel.text = value + " ms";
                    }
                }
                UM.Label {
                    id: consoleIntervalValueLabel
                    Layout.preferredWidth: 80 * screenScaleFactor
                    horizontalAlignment: Text.AlignRight
                    text: settings.settingsConsoleInterval + " ms"
                    font: UM.Theme.getFont("default")
                }
            }

            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width
                Cura.SecondaryButton {
                    text: settings.testBusy ? "Testing…" : "Test connection"
                    enabled: !settings.testBusy && base.validUrl
                    onClicked: settings.testConnection(urlField.text, apiKeyField.text)
                }
                UM.Label {
                    Layout.fillWidth: true
                    text: settings.testStatus
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
