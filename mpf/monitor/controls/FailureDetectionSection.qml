import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "../../bedmesh"
import "../../resources/theme"
import "../../widgets"

ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    readonly property bool detectionReady: root.printerModel != null && root.printerModel.detectionReady
    readonly property bool controlsEnabled: root.detectionReady && root.printerModel.detectionEnabled
    onPrinterModelChanged: {
        if (root.printerModel != null) {
            safePeriodSlider.previewSafeSeconds = -1;
            safePeriodSlider.value = root.printerModel.detectionSafeSeconds;
        }
    }
    function safePeriodLabel(seconds) {
        var minutes = Math.floor(seconds / 60);
        var remainder = seconds % 60;
        return minutes > 0 ? minutes + "m" + (remainder ? " " + remainder + "s" : "") : remainder + "s";
    }

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Failure Detection"
        sectionId: "failureDetection"
        sectionIcon: "Printer"
        // The standing alert shows here too, so a collapsed section
        // still says something is waiting to be acknowledged.
        alertPending: root.printerModel != null && root.printerModel.detectionAlertPending
        alertColor: root.printerModel != null && root.printerModel.detectionAlertLevel === "failure" ? MoonrakerTheme.dangerRed : MoonrakerTheme.warningOrange
    }

    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["failureDetection"] !== false
        spacing: UM.Theme.getSize("default_margin").height / 2

        UM.CheckBox {
            objectName: "detectionMainEnabledCheckbox"
            text: "Enable"
            checked: root.printerModel != null ? root.printerModel.detectionEnabled : false
            enabled: root.detectionReady && (checked || root.printerModel.detectionCameraReady)
            onToggled: {
                if (root.detectionReady)
                    root.printerModel.setDetectionEnabled(checked);
            }
        }

        UM.Label {
            text: "Failure signal thresholds"
            font: UM.Theme.getFont("medium_bold")
        }

        BedMeshRangeSlider {
            objectName: "detectionControlsThresholdSlider"
            Layout.fillWidth: true
            minimum: 0.00
            maximum: 1.00
            minWindowFraction: 0.01
            low: root.printerModel != null ? root.printerModel.detectionWarningThreshold / 100 : 0
            high: root.printerModel != null ? root.printerModel.detectionFailureThreshold / 100 : 1
            segmented: true
            enabled: root.controlsEnabled
            onWindowAdjusted: function (low, high) {
                if (root.controlsEnabled) {
                    var warning = Math.min(99, Math.max(0, Math.round(low * 100)));
                    var failure = Math.min(100, Math.max(warning + 1, Math.round(high * 100)));
                    root.printerModel.setDetectionThresholds(warning, failure);
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            UM.Label {
                text: root.printerModel != null ? "Warning at " + (root.printerModel.detectionWarningThreshold / 100).toFixed(2) : "Warning at —"
                color: MoonrakerTheme.warningOrange
            }
            Item {
                Layout.fillWidth: true
            }
            UM.Label {
                text: root.printerModel != null ? "Failure at " + (root.printerModel.detectionFailureThreshold / 100).toFixed(2) : "Failure at —"
                color: MoonrakerTheme.dangerRed
            }
        }

        RowLayout {
            Layout.fillWidth: true
            UM.Label {
                text: "Safe period at print start"
                Layout.fillWidth: true
            }
            UM.Label {
                objectName: "detectionSafePeriodValue"
                text: root.safePeriodLabel(safePeriodSlider.previewSafeSeconds >= 0 ? safePeriodSlider.previewSafeSeconds : root.printerModel != null ? root.printerModel.detectionSafeSeconds : 300)
            }
        }

        OutlineSlider {
            id: safePeriodSlider
            objectName: "detectionSafePeriodSlider"
            property int previewSafeSeconds: -1
            Layout.fillWidth: true
            from: 0
            to: 900
            stepSize: 10
            live: false
            enabled: root.controlsEnabled
            Component.onCompleted: {
                if (root.printerModel != null)
                    value = root.printerModel.detectionSafeSeconds;
            }
            Connections {
                target: root.printerModel
                function onDetectionChanged() {
                    if (!safePeriodSlider.interacting)
                        safePeriodSlider.value = root.printerModel.detectionSafeSeconds;
                }
            }
            onValueTuning: function (value) {
                safePeriodSlider.previewSafeSeconds = Math.round(value / 10) * 10;
            }
            onValueCommitted: function (value) {
                safePeriodSlider.previewSafeSeconds = -1;
                if (root.controlsEnabled)
                    root.printerModel.setDetectionSafeSeconds(Math.round(value / 10) * 10);
            }
        }

        UM.CheckBox {
            objectName: "detectionNotifyCheckbox"
            text: "Notify on failure"
            checked: root.printerModel != null ? root.printerModel.detectionNotifyEnabled : false
            enabled: root.controlsEnabled
            onToggled: {
                if (root.controlsEnabled)
                    root.printerModel.setDetectionNotifyEnabled(checked);
            }
        }

        UM.CheckBox {
            objectName: "detectionPauseCheckbox"
            text: "Pause on confirmed failure"
            checked: root.printerModel != null ? root.printerModel.detectionPauseEnabled : false
            enabled: root.controlsEnabled
            onToggled: {
                if (root.controlsEnabled)
                    root.printerModel.setDetectionPauseEnabled(checked);
            }
        }

        CentredSecondaryButton {
            objectName: "detectionAcknowledgeButton"
            Layout.fillWidth: true
            text: "Acknowledge failure alert"
            enabled: root.controlsEnabled && root.printerModel.detectionAlertPending
            onClicked: root.printerModel.acknowledgeDetectionAlert()
        }

        CentredSecondaryButton {
            objectName: "detectionRearmPauseButton"
            Layout.fillWidth: true
            text: "Re-arm automatic pause"
            enabled: root.controlsEnabled && root.printerModel.detectionPauseRearmable
            onClicked: root.printerModel.rearmDetectionPause()
            UM.ToolTip {
                visible: parent.hovered
                targetPoint: Qt.point(parent.width / 2, 0)
                x: 0
                y: parent.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "After fixing the print, explicitly allow one more automatic pause. If the same failure is still detected, the print may pause again after fresh analysis."
            }
        }
    }
}
