import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Component {
    id: dashboardComponent

    Item {
        id: root
        property variant catalog: UM.I18nCatalog {
            name: "cura"
        }
        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null
        property bool controlsCollapsed: root.printer != null ? root.printer.controlsCollapsed : false
        property var macroParameters: []

        Cura.MessageDialog {
            id: cancelPrintDialog
            title: "Cancel print?"
            text: "This will cancel the current print on the printer."
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted: {
                if (root.printer != null) {
                    root.printer.cancelPrint();
                }
            }
        }

        Cura.MessageDialog {
            id: powerOffDialog
            property string deviceName: ""
            title: "Turn off power device?"
            text: "A print is active. Turning this device off may stop the printer immediately."
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted: {
                if (root.printer != null && deviceName.length > 0) {
                    root.printer.setPowerDevice(deviceName, false);
                }
            }
        }
        property bool tuningSliderPressed: false
        property bool anyPowerLocked: {
            if (printer == null || printer.powerDevices == null)
                return false;
            for (var i = 0; i < printer.powerDevices.length; ++i) {
                if (printer.powerDevices[i].locked && !printer.powerDevices[i].can_toggle)
                    return true;
            }
            return false;
        }

        function sliderSelection(slider) {
            if (slider == null)
                return 0;
            return Math.round(slider.valueAt(slider.position));
        }

        function refreshMacroParameters() {
            if (root.printer == null || macroSelector.currentIndex < 0) {
                root.macroParameters = [];
                return;
            }
            root.macroParameters = root.printer.macroParameterDefinitions(macroSelector.currentText);
        }

        function macroArgumentsValid() {
            for (var i = 0; i < macroParameterRepeater.count; ++i) {
                var item = macroParameterRepeater.itemAt(i);
                if (item != null && item.argumentRequired && item.argumentValue.trim().length === 0) {
                    return false;
                }
            }
            return true;
        }

        function macroArgumentString() {
            var args = [];
            for (var i = 0; i < macroParameterRepeater.count; ++i) {
                var item = macroParameterRepeater.itemAt(i);
                if (item == null)
                    continue;
                var value = item.argumentValue.trim();
                if (value.length > 0)
                    args.push(item.argumentName + "=" + value);
            }
            return args.join(" ");
        }

        MoonrakerMonitor {
            id: baseMonitorComponent
        }

        Connections {
            target: root.printer
            function onControlsChanged() {
                root.refreshMacroParameters();
            }
            function onTypedControlsChanged() {
                root.refreshMacroParameters();
            }
        }

        Component {
            id: emergencyButtonComponent
            Item {
                id: emergencyButton
                property int clicks: root.printer != null ? root.printer.emergencyStopClicks : 0
                Rectangle {
                    anchors.fill: parent
                    radius: UM.Theme.getSize("default_radius").width
                    color: "transparent"
                    border.color: "#d32f2f"
                    border.width: 2 * screenScaleFactor
                    clip: true
                    Rectangle {
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.bottom: parent.bottom
                        width: parent.width * Math.min(1.0, (emergencyButton.clicks + (root.printer != null ? root.printer.emergencyHoldProgress : 0)) / 3.0)
                        color: "#d32f2f"
                    }
                    UM.Label {
                        anchors.centerIn: parent
                        text: (root.printer != null && root.printer.emergencyHoldProgress > 0) ? "EMERGENCY STOP — keep holding" : (emergencyButton.clicks === 0 ? "EMERGENCY STOP — click twice, then hold" : (emergencyButton.clicks === 1 ? "EMERGENCY STOP — one more click, then hold" : "EMERGENCY STOP — press and hold to fire"))
                        font: UM.Theme.getFont("medium_bold")
                        color: "black"
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: if (root.printer != null)
                            root.printer.emergencyStopClick()
                        onPressed: if (root.printer != null)
                            root.printer.emergencyHoldStarted()
                        onReleased: if (root.printer != null)
                            root.printer.emergencyHoldReleased()
                        // A grab stolen mid-hold (modal dialog, window
                        // deactivation) delivers a canceled instead of a
                        // release; without this the armed hold timer would
                        // still fire the stop after the user let go.
                        onCanceled: if (root.printer != null)
                            root.printer.emergencyHoldReleased()
                    }
                }
            }
        }

        // The emergency stop is a permanently visible section pinned to the
        // bottom of the Monitor panel, outside the status scroll.
        Column {
            id: emergencyDock
            z: 20
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: UM.Theme.getSize("default_margin").width
            anchors.rightMargin: UM.Theme.getSize("default_margin").width
            anchors.bottomMargin: UM.Theme.getSize("default_margin").height
            spacing: UM.Theme.getSize("thin_margin").height

            Rectangle {
                width: parent.width
                height: UM.Theme.getSize("default_lining").height
                color: UM.Theme.getColor("lining")
            }
            UM.Label {
                width: parent.width
                text: "Emergency"
                font: UM.Theme.getFont("medium_bold")
            }
            Loader {
                width: parent.width
                height: 46 * screenScaleFactor
                sourceComponent: emergencyButtonComponent
            }
            UM.Label {
                width: parent.width
                text: "Click twice, then press and hold the third time: the stop fires after 0.6 seconds of holding, and releasing early cancels. The arm resets after 1 second of inactivity."
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default")
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: emergencyDock.top
            spacing: 0

            Loader {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 640 * screenScaleFactor
                sourceComponent: baseMonitorComponent
            }

            Cura.RoundedRectangle {
                // Collapsed, the pane shrinks to the toggle button and its
                // margins; the vertical title below explains the strip.
                Layout.preferredWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 390 * screenScaleFactor)
                Layout.minimumWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 340 * screenScaleFactor)
                // Shrink-only: max == preferred keeps the wide layout
                // unchanged, but narrow stages may compress the pane.
                Layout.maximumWidth: (root.controlsCollapsed ? collapseButton.width + 2 * UM.Theme.getSize("thin_margin").width : 390 * screenScaleFactor)
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: UM.Theme.getSize("default_margin").width
                Layout.leftMargin: 0
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                color: UM.Theme.getColor("main_background")
                radius: UM.Theme.getSize("default_radius").width

                // While collapsed, a click anywhere on the strip expands
                // the pane; the header button stays on top of this area.
                MouseArea {
                    visible: root.controlsCollapsed
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (root.printer != null) {
                            root.printer.setControlsCollapsed(false);
                        }
                    }
                }

                RowLayout {
                    id: controlHeader
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
                    anchors.leftMargin: UM.Theme.getSize("thin_margin").width
                    anchors.rightMargin: UM.Theme.getSize("thin_margin").width
                    spacing: UM.Theme.getSize("thin_margin").width
                    // The pane title stays frozen in the header row; the
                    // lock sits beside it and the collapse toggle hugs the
                    // edge the pane collapses into (right, as the rightmost
                    // pane).
                    UM.Label {
                        Layout.fillWidth: true
                        visible: !root.controlsCollapsed
                        text: "Printer controls"
                        font: UM.Theme.getFont("large_bold")
                        elide: Text.ElideRight
                    }
                    // Lock/unlock as a padlock glyph; the state is the
                    // icon (closed = locked), the tooltip carries the action.
                    UM.SimpleButton {
                        id: lockButton
                        visible: !root.controlsCollapsed
                        Layout.alignment: Qt.AlignVCenter
                        width: 28 * screenScaleFactor
                        height: 28 * screenScaleFactor
                        color: root.printer != null && root.printer.controlsLocked ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
                        hoverColor: root.printer != null && root.printer.controlsLocked ? UM.Theme.getColor("primary") : UM.Theme.getColor("text")
                        iconSource: root.printer != null && root.printer.controlsLocked ? Qt.resolvedUrl("PadlockLocked.svg") : Qt.resolvedUrl("PadlockUnlocked.svg")
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setControlsLocked(!root.printer.controlsLocked);
                            }
                        }

                        UM.TooltipArea {
                            anchors.fill: parent
                            text: root.printer != null && root.printer.controlsLocked ? "Unlock all controls." : "Lock all controls."
                            acceptedButtons: Qt.NoButton
                        }
                    }
                    Cura.SecondaryButton {
                        id: collapseButton
                        fixedWidthMode: true
                        width: 32 * screenScaleFactor
                        text: root.controlsCollapsed ? "‹" : "›"
                        tooltip: root.controlsCollapsed ? "Show the printer controls." : "Hide the printer controls."
                        onClicked: {
                            if (root.printer != null) {
                                root.printer.setControlsCollapsed(!root.controlsCollapsed);
                            }
                        }
                    }
                }

                Flickable {
                    id: controlFlick
                    visible: !root.controlsCollapsed
                    anchors.top: controlHeader.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.topMargin: UM.Theme.getSize("default_margin").height
                    anchors.leftMargin: UM.Theme.getSize("default_margin").width
                    anchors.rightMargin: UM.Theme.getSize("default_margin").width
                    anchors.bottomMargin: UM.Theme.getSize("default_margin").height
                    clip: true
                    contentWidth: width
                    contentHeight: controlContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    interactive: !root.tuningSliderPressed
                    ScrollBar.vertical: UM.ScrollBar {
                        id: controlScrollbar
                    }

                    ColumnLayout {
                        id: controlContent
                        width: controlFlick.width - controlScrollbar.width - UM.Theme.getSize("default_margin").width
                        // Spacing lives on the children, not the layout: a
                        // collapsed section's hidden content must contribute
                        // nothing, so stacked headers sit flush like Cura's.
                        spacing: 0

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Print"
                            sectionId: "print"
                            sectionIcon: "Printer"
                        }
                        ColumnLayout {
                            visible: root.printer == null || root.printer.sectionExpandedMap["print"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            spacing: UM.Theme.getSize("default_margin").height

                            RowLayout {
                                visible: root.printer != null && (root.printer.canPausePrint || root.printer.canResumePrint || root.printer.canCancelPrint)
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.canPausePrint
                                    text: "Pause"
                                    onClicked: root.printer.pausePrint()
                                }

                                Cura.PrimaryButton {
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.canResumePrint
                                    text: "Resume"
                                    onClicked: root.printer.resumePrint()
                                }

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.canCancelPrint
                                    text: "Cancel"
                                    onClicked: cancelPrintDialog.open()
                                }
                            }

                            UM.Label {
                                visible: root.printer != null && root.printer.actionStatus.length > 0
                                text: root.printer != null ? root.printer.actionStatus : ""
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            GridLayout {
                                columns: 2
                                Layout.fillWidth: true
                                columnSpacing: UM.Theme.getSize("default_margin").width
                                rowSpacing: UM.Theme.getSize("default_margin").height / 2
                                UM.Label {
                                    text: "Layer height"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.monitorLayerHeight : "—"
                                    Layout.fillWidth: true
                                }
                                UM.Label {
                                    text: "Current Z offset"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                UM.Label {
                                    text: root.printer != null ? root.printer.zOffsetText : "—"
                                    Layout.fillWidth: true
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Setup"
                            sectionId: "setup"
                            sectionIcon: "House"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["setup"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home"
                                    enabled: root.printer != null && root.printer.canRunSetup
                                    onClicked: root.printer.homeAll()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.hasQuadGantryLevel
                                    text: "QGL"
                                    enabled: root.printer != null && root.printer.canRunSetup
                                    onClicked: root.printer.runQuadGantryLevel()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    visible: root.printer != null && root.printer.hasBedMesh
                                    text: "Calibrate mesh"
                                    tooltip: "Probe the bed now and replace the active mesh with a newly calibrated one."
                                    enabled: root.printer != null && root.printer.canRunSetup
                                    onClicked: root.printer.calibrateBedMesh()
                                }
                            }

                            RowLayout {
                                visible: root.printer != null && root.printer.hasBedMesh
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                Cura.ComboBox {
                                    id: bedMeshProfileSelector
                                    Layout.fillWidth: true
                                    model: root.printer != null ? root.printer.bedMeshProfileNames : []
                                    enabled: root.printer != null && root.printer.canRunSetup && root.printer.bedMeshProfileNames.length > 0
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Load saved mesh"
                                    enabled: root.printer != null && root.printer.canRunSetup && bedMeshProfileSelector.currentText.length > 0
                                    tooltip: "Load the selected saved Klipper bed mesh without probing the bed again."
                                    onClicked: root.printer.loadBedMeshProfile(bedMeshProfileSelector.currentText)
                                }

                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Clear mesh"
                                    enabled: root.printer != null && root.printer.canRunSetup && root.printer.bedMeshAvailable
                                    tooltip: "Clear the active Klipper bed mesh and remove its Z adjustment."
                                    onClicked: root.printer.clearBedMesh()
                                }
                            }
                            UM.Label {
                                visible: root.printer != null && root.printer.hasBedMesh && root.printer.bedMeshProfileNames.length === 0
                                text: "No saved bed mesh profiles reported by Klipper."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            UM.Label {
                                visible: root.printer != null && root.printer.printActive
                                text: "Homing and bed-mesh setup controls are disabled during a print."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Toolhead"
                            sectionId: "toolhead"
                            sectionIcon: "Nozzle"
                        }
                        ColumnLayout {
                            id: toolheadSection
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.sectionExpandedMap["toolhead"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2
                            readonly property var jogPresets: [0.1, 0.5, 1, 5, 10, 25, 50, 100, 125]

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                UM.Label {
                                    text: "Move distance"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                Cura.ComboBox {
                                    id: jogDistanceSelector
                                    Layout.fillWidth: true
                                    model: toolheadSection.jogPresets
                                    currentIndex: 5
                                    onActivated: function (index) {
                                        if (root.printer != null) {
                                            root.printer.setJogDistance(toolheadSection.jogPresets[index]);
                                        }
                                    }
                                }
                                Cura.TextField {
                                    id: jogDistanceField
                                    Layout.fillWidth: true
                                    text: root.printer != null ? root.printer.jogDistance.toString() : ""
                                    onEditingFinished: {
                                        var value = parseFloat(jogDistanceField.text);
                                        if (root.printer != null) {
                                            root.printer.setJogDistance(value);
                                            jogDistanceField.text = root.printer.jogDistance.toString();
                                        }
                                    }
                                }
                                UM.Label {
                                    text: "mm"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: 3
                                    columnSpacing: UM.Theme.getSize("thin_margin").width
                                    rowSpacing: UM.Theme.getSize("thin_margin").height
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "↑ Y"

                                        tooltip: "Move the toolhead towards the Y maximum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("y", 1)
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "← X"

                                        tooltip: "Move the toolhead towards the X minimum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("x", -1)
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "→ X"

                                        tooltip: "Move the toolhead towards the X maximum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("x", 1)
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                    PreviewSecondaryButton {

                                        Layout.fillWidth: true

                                        text: "↓ Y"

                                        tooltip: "Move the toolhead towards the Y minimum."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("y", -1)
                                    }
                                    Item {
                                        Layout.fillWidth: true
                                    }
                                }
                                ColumnLayout {
                                    spacing: UM.Theme.getSize("thin_margin").height
                                    PreviewSecondaryButton {

                                        text: "↑ Z"

                                        tooltip: "Move the toolhead up."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("z", 1)
                                    }
                                    PreviewSecondaryButton {

                                        text: "↓ Z"

                                        tooltip: "Move the toolhead down."

                                        enabled: root.printer != null && root.printer.jogEnabled

                                        onClicked: root.printer.jog("z", -1)
                                    }
                                }
                            }

                            UM.Label {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: root.printer != null ? "Homed: " + (root.printer.homedAxes.length > 0 ? root.printer.homedAxes.toUpperCase().split('').join(' ') : "—") + "  ·  " + root.printer.positionMode + " moves  ·  " + root.printer.monitorPosition : ""
                                color: UM.Theme.getColor("text_inactive")
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home X"
                                    tooltip: "Home the X axis."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.home("x")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home Y"
                                    tooltip: "Home the Y axis."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.home("y")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Home Z"
                                    tooltip: "Home the Z axis."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.home("z")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Motors off"
                                    tooltip: "Disable the stepper motors so the toolhead can be moved by hand."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.motorsOff()
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Centre toolhead"
                                    tooltip: "Move X and Y to the build plate centre, 5 cm above the plate."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.centerToolhead()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Z to 0"
                                    tooltip: "Move Z down to 0, the bed level after homing."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.zToZero()
                                }
                            }

                            UM.Label {
                                text: "Extrusion"
                                font: UM.Theme.getFont("medium_bold")
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "5"
                                    tooltip: "Extrude distance: 5 mm."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeDistance(5)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "10"
                                    tooltip: "Extrude distance: 10 mm."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeDistance(10)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "15"
                                    tooltip: "Extrude distance: 15 mm."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeDistance(15)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "25"
                                    tooltip: "Extrude distance: 25 mm."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeDistance(25)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "75"
                                    tooltip: "Extrude distance: 75 mm."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeDistance(75)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "100"
                                    tooltip: "Extrude distance: 100 mm."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeDistance(100)
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                Cura.TextField {
                                    id: extrudeDistanceField
                                    Layout.fillWidth: true
                                    text: root.printer != null ? root.printer.extrudeDistance.toString() : ""
                                    onEditingFinished: {
                                        var value = parseFloat(extrudeDistanceField.text);
                                        if (root.printer != null) {
                                            root.printer.setExtrudeDistance(value);
                                            extrudeDistanceField.text = root.printer.extrudeDistance.toString();
                                        }
                                    }
                                }
                                UM.Label {
                                    text: "mm"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Extrude"
                                    tooltip: "Extrude the configured distance at the configured speed."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.extrude(1)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Retract"
                                    tooltip: "Retract the configured distance at the configured speed."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.extrude(-1)
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("thin_margin").width
                                UM.Label {
                                    text: "Speed"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "1"
                                    tooltip: "Extrusion speed: 1 mm/s."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeSpeed(60)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "2"
                                    tooltip: "Extrusion speed: 2 mm/s."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeSpeed(120)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "5"
                                    tooltip: "Extrusion speed: 5 mm/s."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeSpeed(300)
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "25"
                                    tooltip: "Extrusion speed: 25 mm/s."
                                    enabled: root.printer != null && root.printer.jogEnabled
                                    onClicked: root.printer.setExtrudeSpeed(1500)
                                }
                                UM.Label {
                                    text: "mm/s"
                                    color: UM.Theme.getColor("text_inactive")
                                }
                            }

                            UM.Label {
                                visible: root.printer != null && root.printer.canResumePrint
                                text: "Printer is paused — moves run immediately; a print resumes from Klipper's recorded position."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            UM.Label {
                                visible: root.printer != null && root.printer.canPausePrint
                                text: "Toolhead moves are disabled during a print — pause first."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            UM.Label {
                                visible: root.printer != null && root.printer.jogStatus.length > 0
                                text: root.printer != null ? root.printer.jogStatus : ""
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Macros"
                            sectionId: "macros"
                            sectionIcon: "Function"
                        }
                        ColumnLayout {
                            id: macroSection
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.macroNames.length > 0 && root.printer.sectionExpandedMap["macros"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            Cura.ComboBox {
                                id: macroSelector
                                Layout.fillWidth: true
                                model: root.printer != null ? root.printer.macroNames : []
                                onCurrentTextChanged: root.refreshMacroParameters()
                                Component.onCompleted: root.refreshMacroParameters()
                            }

                            Repeater {
                                id: macroParameterRepeater
                                model: root.macroParameters

                                ColumnLayout {
                                    id: argumentRow
                                    Layout.fillWidth: true
                                    property string argumentName: String(modelData.name)
                                    property bool argumentRequired: Boolean(modelData.required)
                                    property string argumentValue: String(modelData.default)
                                    spacing: 2 * screenScaleFactor

                                    UM.Label {
                                        Layout.fillWidth: true
                                        text: argumentRow.argumentName + "  (" + String(modelData.type) + (argumentRow.argumentRequired ? ", required" : "") + ")"
                                        color: UM.Theme.getColor("text_inactive")
                                    }

                                    Cura.ComboBox {
                                        id: boolInput
                                        Layout.fillWidth: true
                                        visible: String(modelData.type) === "bool"
                                        model: Boolean(modelData.hasDefault) ? ["Use macro default", "True", "False"] : ["True", "False"]
                                        currentIndex: {
                                            if (!Boolean(modelData.hasDefault))
                                                return String(modelData.default).toLowerCase() === "false" ? 1 : 0;
                                            return 0;
                                        }
                                        onCurrentTextChanged: {
                                            argumentRow.argumentValue = currentText === "Use macro default" ? "" : currentText;
                                        }
                                    }

                                    IntValidator {
                                        id: integerValidator
                                    }
                                    DoubleValidator {
                                        id: floatingValidator
                                        notation: DoubleValidator.StandardNotation
                                    }

                                    Cura.TextField {
                                        id: typedInput
                                        Layout.fillWidth: true
                                        visible: String(modelData.type) !== "bool"
                                        text: String(modelData.default)
                                        placeholderText: argumentRow.argumentRequired ? "Required" : "Optional"
                                        selectByMouse: true
                                        validator: String(modelData.type) === "int" ? integerValidator : (String(modelData.type) === "float" ? floatingValidator : null)
                                        onTextChanged: argumentRow.argumentValue = text
                                    }
                                }
                            }

                            UM.Label {
                                visible: root.macroParameters.length === 0
                                text: "This macro has no detectable params.* inputs."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            Cura.SecondaryButton {
                                Layout.fillWidth: true
                                text: "Run macro"
                                enabled: root.printer != null && macroSelector.currentIndex >= 0 && !root.printer.actionBusy && !root.printer.printActive && root.macroArgumentsValid()
                                onClicked: root.printer.runMacro(macroSelector.currentText, root.macroArgumentString())
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Temperature profiles"
                            sectionId: "profiles"
                            sectionIcon: "PrintQuality"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.temperaturePresetItems.length > 0 && root.printer.sectionExpandedMap["profiles"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            Repeater {
                                model: root.printer != null ? root.printer.temperaturePresetItems : []
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: modelData.active ? "Active — " + modelData.name : modelData.name
                                    enabled: root.printer != null && root.printer.canApplyTemperaturePreset
                                    onClicked: root.printer.applyTemperaturePreset(modelData.index)
                                }
                            }
                            Cura.SecondaryButton {
                                Layout.fillWidth: true
                                text: "Cooldown"
                                tooltip: "Turn every heater off: all targets to 0 °C."
                                enabled: root.printer != null && root.printer.canApplyTemperaturePreset
                                onClicked: root.printer.heatersOff()
                            }
                            UM.Label {
                                text: "A profile is marked Active only when all of its enabled heater targets match the printer. G-code-only profiles are never assumed active."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            UM.Label {
                                visible: root.printer != null && root.printer.printActive
                                text: "Temperature profiles are disabled during a print, matching Mainsail."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Live tuning"
                            sectionId: "tuning"
                            sectionIcon: "Sliders"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["tuning"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height
                            UM.Label {
                                text: "Drag to preview a value. After release, the latest value is applied once it has been unchanged for 250 ms."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: "Speed factor"
                                        Layout.fillWidth: true
                                    }
                                    UM.Label {
                                        text: root.sliderSelection(speedSlider) + "%"
                                    }
                                }
                                Slider {
                                    id: speedSlider
                                    Layout.fillWidth: true
                                    from: 10
                                    to: Math.max(200, root.printer != null ? Math.ceil(root.printer.speedFactorPercent * 2) : 200)
                                    stepSize: 1
                                    live: false
                                    value: root.printer != null ? root.printer.speedFactorPercent : 100
                                    enabled: root.printer != null
                                    onMoved: {
                                        if (root.printer == null)
                                            return;
                                        var selected = root.sliderSelection(speedSlider);
                                        root.printer.previewSpeedFactor(selected);
                                        if (!pressed)
                                            root.printer.setSpeedFactor(selected);
                                    }
                                    onPressedChanged: {
                                        root.tuningSliderPressed = pressed;
                                        if (!pressed && root.printer != null)
                                            root.printer.setSpeedFactor(root.sliderSelection(speedSlider));
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: "Extrusion multiplier"
                                        Layout.fillWidth: true
                                    }
                                    UM.Label {
                                        text: root.sliderSelection(flowSlider) + "%"
                                    }
                                }
                                Slider {
                                    id: flowSlider
                                    Layout.fillWidth: true
                                    from: 50
                                    to: Math.max(200, root.printer != null ? Math.ceil(root.printer.flowFactorPercent * 2) : 200)
                                    stepSize: 1
                                    live: false
                                    value: root.printer != null ? root.printer.flowFactorPercent : 100
                                    enabled: root.printer != null
                                    onMoved: {
                                        if (root.printer == null)
                                            return;
                                        var selected = root.sliderSelection(flowSlider);
                                        root.printer.previewFlowFactor(selected);
                                        if (!pressed)
                                            root.printer.setFlowFactor(selected);
                                    }
                                    onPressedChanged: {
                                        root.tuningSliderPressed = pressed;
                                        if (!pressed && root.printer != null)
                                            root.printer.setFlowFactor(root.sliderSelection(flowSlider));
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").height / 2
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: "Z-offset nudges"
                                        font: UM.Theme.getFont("medium_bold")
                                        Layout.fillWidth: true
                                    }
                                    UM.Label {
                                        text: root.printer != null ? "Current " + root.printer.zOffsetText : "Current —"
                                        font: UM.Theme.getFont("medium_bold")
                                    }
                                }
                                Column {
                                    id: zOffsetGrid
                                    Layout.fillWidth: true
                                    spacing: 2 * screenScaleFactor
                                    property real buttonSpacing: 2 * screenScaleFactor
                                    property real buttonWidth: (width - 3 * buttonSpacing) / 4

                                    Row {
                                        width: parent.width
                                        spacing: zOffsetGrid.buttonSpacing
                                        Repeater {
                                            model: [0.005, 0.01, 0.025, 0.05]
                                            Cura.SecondaryButton {
                                                width: zOffsetGrid.buttonWidth
                                                height: UM.Theme.getSize("action_button").height
                                                fixedWidthMode: true
                                                text: "↑ " + modelData.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                                tooltip: "Moves the nozzle up, away from the bed."
                                                enabled: root.printer != null && !root.printer.actionBusy
                                                onClicked: root.printer.adjustZOffset(modelData)
                                            }
                                        }
                                    }
                                    Row {
                                        width: parent.width
                                        spacing: zOffsetGrid.buttonSpacing
                                        Repeater {
                                            model: [-0.005, -0.01, -0.025, -0.05]
                                            Cura.SecondaryButton {
                                                width: zOffsetGrid.buttonWidth
                                                height: UM.Theme.getSize("action_button").height
                                                fixedWidthMode: true
                                                text: "↓ " + Math.abs(modelData).toFixed(3).replace(/0+$/, "").replace(/\.$/, "")
                                                tooltip: "Moves the nozzle down, closer to the bed."
                                                enabled: root.printer != null && !root.printer.actionBusy
                                                onClicked: root.printer.adjustZOffset(modelData)
                                            }
                                        }
                                    }
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Clear Z offset"
                                    enabled: root.printer != null && !root.printer.actionBusy
                                    onClicked: root.printer.clearZOffset()
                                }
                            }
                        }
                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Fan speed"
                            sectionId: "fans"
                            sectionIcon: "Fan"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.fanControlItems.length > 0 && root.printer.sectionExpandedMap["fans"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            Repeater {
                                model: root.printer != null ? root.printer.fanControlItems : []
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(fanSlider) + "%"
                                        }
                                    }
                                    Slider {
                                        id: fanSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onMoved: {
                                            if (root.printer == null)
                                                return;
                                            var selected = root.sliderSelection(fanSlider);
                                            root.printer.previewFanSpeed(modelData.object, selected);
                                            if (!pressed)
                                                root.printer.setFanSpeed(modelData.object, selected);
                                        }
                                        onPressedChanged: {
                                            root.tuningSliderPressed = pressed;
                                            if (!pressed && root.printer != null)
                                                root.printer.setFanSpeed(modelData.object, root.sliderSelection(fanSlider));
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "LEDs"
                            sectionId: "leds"
                            sectionIcon: "Star"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.ledItems.length > 0 && root.printer.sectionExpandedMap["leds"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("thin_margin").height
                            Repeater {
                                model: root.printer != null ? root.printer.ledItems : []
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: UM.Theme.getSize("thin_margin").height

                                    function previewLedColour() {
                                        if (root.printer == null)
                                            return;
                                        root.printer.previewLedColor(modelData.object, root.sliderSelection(redSlider), root.sliderSelection(greenSlider), root.sliderSelection(blueSlider), modelData.hasWhite ? root.sliderSelection(whiteSlider) : 0, root.sliderSelection(ledSlider));
                                    }

                                    function applyLedColour() {
                                        if (root.printer == null)
                                            return;
                                        root.printer.setLedColor(modelData.object, root.sliderSelection(redSlider), root.sliderSelection(greenSlider), root.sliderSelection(blueSlider), modelData.hasWhite ? root.sliderSelection(whiteSlider) : 0, root.sliderSelection(ledSlider));
                                    }

                                    function ledColourMoved(slider) {
                                        previewLedColour();
                                        if (!slider.pressed)
                                            applyLedColour();
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: "Brightness " + root.sliderSelection(ledSlider) + "%"
                                        }
                                    }
                                    Slider {
                                        id: ledSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onMoved: {
                                            if (root.printer == null)
                                                return;
                                            var selected = root.sliderSelection(ledSlider);
                                            root.printer.previewLedBrightness(modelData.object, selected);
                                            if (!pressed)
                                                root.printer.setLedBrightness(modelData.object, selected);
                                        }
                                        onPressedChanged: {
                                            root.tuningSliderPressed = pressed;
                                            if (!pressed && root.printer != null)
                                                root.printer.setLedBrightness(modelData.object, root.sliderSelection(ledSlider));
                                        }
                                    }

                                    GridLayout {
                                        columns: 3
                                        Layout.fillWidth: true
                                        columnSpacing: UM.Theme.getSize("thin_margin").width
                                        rowSpacing: UM.Theme.getSize("thin_margin").height

                                        UM.Label {
                                            text: "R"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        Slider {
                                            id: redSlider
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.redPercent
                                            onMoved: ledColourMoved(redSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(redSlider) + "%"
                                        }

                                        UM.Label {
                                            text: "G"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        Slider {
                                            id: greenSlider
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.greenPercent
                                            onMoved: ledColourMoved(greenSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(greenSlider) + "%"
                                        }

                                        UM.Label {
                                            text: "B"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        Slider {
                                            id: blueSlider
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.bluePercent
                                            onMoved: ledColourMoved(blueSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(blueSlider) + "%"
                                        }

                                        UM.Label {
                                            visible: modelData.hasWhite
                                            text: "W"
                                            color: UM.Theme.getColor("text_inactive")
                                        }
                                        Slider {
                                            id: whiteSlider
                                            visible: modelData.hasWhite
                                            Layout.fillWidth: true
                                            from: 0
                                            to: 100
                                            stepSize: 1
                                            live: false
                                            value: modelData.whitePercent
                                            onMoved: ledColourMoved(whiteSlider)
                                            onPressedChanged: {
                                                root.tuningSliderPressed = pressed;
                                                if (!pressed)
                                                    applyLedColour();
                                            }
                                        }
                                        UM.Label {
                                            visible: modelData.hasWhite
                                            text: root.sliderSelection(whiteSlider) + "%"
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "PWM outputs"
                            sectionId: "pwm"
                            sectionIcon: "ThreeDots"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.pwmOutputItems.length > 0 && root.printer.sectionExpandedMap["pwm"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            Repeater {
                                model: root.printer != null ? root.printer.pwmOutputItems : []
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    RowLayout {
                                        Layout.fillWidth: true
                                        UM.Label {
                                            text: modelData.name
                                            Layout.fillWidth: true
                                            elide: Text.ElideRight
                                        }
                                        UM.Label {
                                            text: root.sliderSelection(pwmSlider) + "%"
                                        }
                                    }
                                    Slider {
                                        id: pwmSlider
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        stepSize: 1
                                        live: false
                                        value: modelData.percent
                                        onMoved: {
                                            if (root.printer == null)
                                                return;
                                            var selected = root.sliderSelection(pwmSlider);
                                            root.printer.previewPwmOutput(modelData.object, selected);
                                            if (!pressed)
                                                root.printer.setPwmOutput(modelData.object, selected);
                                        }
                                        onPressedChanged: {
                                            root.tuningSliderPressed = pressed;
                                            if (!pressed && root.printer != null) {
                                                root.printer.setPwmOutput(modelData.object, root.sliderSelection(pwmSlider));
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Power"
                            sectionId: "power"
                            sectionIconUrl: Qt.resolvedUrl("Power.svg")
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.powerDevices.length > 0 && root.printer.sectionExpandedMap["power"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            Repeater {
                                model: root.printer != null ? root.printer.powerDevices : []
                                RowLayout {
                                    Layout.fillWidth: true
                                    UM.Label {
                                        text: modelData.name + "  · " + modelData.status
                                        color: UM.Theme.getColor("text_inactive")
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Cura.SecondaryButton {
                                        enabled: root.printer != null && modelData.can_toggle && !root.printer.actionBusy
                                        text: modelData.status === "on" ? "Turn off" : "Turn on"
                                        fixedWidthMode: true
                                        width: 104 * screenScaleFactor
                                        onClicked: {
                                            if (modelData.status === "on" && root.printer.printActive) {
                                                powerOffDialog.deviceName = modelData.name;
                                                powerOffDialog.open();
                                            } else {
                                                root.printer.setPowerDevice(modelData.name, modelData.status !== "on");
                                            }
                                        }
                                    }
                                }
                            }
                            UM.Label {
                                visible: root.anyPowerLocked
                                text: "Power control is locked by Moonraker while this print is active."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "System"
                            sectionId: "system"
                            sectionIcon: "Settings"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer == null || root.printer.sectionExpandedMap["system"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").height / 2

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: UM.Theme.getSize("default_margin").width / 2
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Firmware restart"
                                    tooltip: "Restart Klipper's firmware process (FIRMWARE_RESTART)."
                                    enabled: root.printer != null && !root.printer.printActive
                                    onClicked: root.printer.firmwareRestart()
                                }
                                Cura.SecondaryButton {
                                    Layout.fillWidth: true
                                    text: "Host restart"
                                    tooltip: "Reboot the host Moonraker runs on (machine/reboot)."
                                    enabled: root.printer != null && !root.printer.printActive
                                    onClicked: root.printer.hostRestart()
                                }
                            }
                            UM.Label {
                                visible: root.printer != null && root.printer.printActive
                                text: "System restarts are disabled during a print."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        CollapsibleSectionHeader {
                            Layout.fillWidth: true
                            printerModel: root.printer
                            title: "Configuration changes"
                            sectionId: "save"
                            sectionIcon: "Save"
                        }
                        ColumnLayout {
                            Layout.topMargin: UM.Theme.getSize("default_margin").height
                            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
                            visible: root.printer != null && root.printer.saveConfigPending && root.printer.sectionExpandedMap["save"] !== false
                            Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
                            enabled: root.printer == null || !root.printer.controlsLocked
                            Layout.fillWidth: true

                            UM.Label {
                                text: root.printer != null ? root.printer.saveConfigSummary : ""
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            Cura.PrimaryButton {
                                Layout.fillWidth: true
                                text: "Save configuration"
                                enabled: root.printer != null && root.printer.canSaveConfig
                                onClicked: root.printer.saveConfig()
                            }
                            UM.Label {
                                text: root.printer != null && root.printer.printActive ? "SAVE_CONFIG is disabled during a print." : "Saving configuration restarts Klipper."
                                color: UM.Theme.getColor("text_inactive")
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }

                // The collapsed strip: the toggle stays at the top and the
                // panel title reads top-to-bottom directly under it. The
                // wrapper box swaps the label's extents so the rotated text
                // starts exactly where the box begins.
                // The collapsed strip: the toggle stays at the top and the
                // pane title reads top-to-bottom directly under it. The
                // wrapper box swaps the label's extents so the rotated
                // text occupies the box exactly, starting at its top.
                Item {
                    id: collapsedTitleBox
                    visible: root.controlsCollapsed
                    // Anchor to the header ROW (a sibling): anchoring to
                    // the toggle inside it is illegal in QML and silently
                    // drops the anchor, which un-pinned the title.
                    anchors.top: controlHeader.bottom
                    anchors.topMargin: UM.Theme.getSize("thin_margin").height
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: collapsedTitle.implicitHeight
                    height: collapsedTitle.implicitWidth
                    UM.Label {
                        id: collapsedTitle
                        text: "Printer controls"
                        font: UM.Theme.getFont("medium_bold")
                        color: UM.Theme.getColor("text_inactive")
                        rotation: 90
                        anchors.centerIn: parent
                    }
                }
            }
        }
    }
}
