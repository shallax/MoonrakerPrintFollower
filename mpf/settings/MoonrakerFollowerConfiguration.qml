import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Cura.MachineAction {
    id: base
    anchors.fill: parent

    property alias validUrl: connectionSettings.validUrl
    property alias insecureKeyWarning: connectionSettings.insecureKeyWarning
    property alias validPollInterval: connectionSettings.validPollInterval
    property alias pollIntervalMoved: connectionSettings.pollIntervalMoved
    property alias validAuxInterval: connectionSettings.validAuxInterval
    property alias validConsoleInterval: connectionSettings.validConsoleInterval
    property alias connectionRequested: connectionSettings.connectionRequested
    property alias validZTolerance: followingSettings.validZTolerance
    property alias validRetryInterval: uploadSettings.validRetryInterval
    property alias validTranslation: uploadSettings.validTranslation
    property alias validCacheMax: diagnosticsSettings.validCacheMax
    property bool canSave: validPollInterval && validAuxInterval && validConsoleInterval && validZTolerance && validCacheMax && validRetryInterval && validTranslation && (!connectionRequested || validUrl)
    // A refused save must be visible: the dialog accepted nothing and
    // said nothing, so the change seemed to revert (the live report).
    // Two refusal causes share the slot with distinct copy: the
    // validation refusal names the fields, the disk refusal names the
    // write — "fix the fields" is a lie for a full disk.
    property bool saveRefused: false
    property string saveRefusalText: ""

    function save(closeDialog) {
        if (!base.canSave) {
            saveRefused = true;
            saveRefusalText = "Settings were not saved — fix the highlighted fields and save again.";
            return;
        }
        var saved = manager.saveConfig(Object.assign({}, connectionSettings.values, followingSettings.values, uploadSettings.values, diagnosticsSettings.values));
        saveRefused = !saved;
        saveRefusalText = saved ? "" : "Settings were not saved — the file could not be written. Check the disk and try again.";
        if (saved && closeDialog)
            actionDialog.close();
    }

    function cancel(closeDialog) {
        manager.cancelTest();
        if (closeDialog)
            actionDialog.close();
    }

    Connections {
        target: actionDialog
        function onAccepted() {
            base.save(false);
            if (base.saveRefused)
                actionDialog.show();  // accepted fires AFTER the close: reopen so the refusal is visible
        }
        function onRejected() {
            base.cancel(false);
        }
        function onClosing() {
            manager.cancelTest();
        }
    }

    UM.Label {
        id: machineLabel
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("default_margin").width
        font: UM.Theme.getFont("large_bold")
        text: manager.machineName
    }

    UM.Label {
        id: saveRefusedLabel
        anchors.top: machineLabel.bottom
        anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("default_margin").width
        visible: saveRefused
        text: saveRefusalText
        color: UM.Theme.getColor("error")
    }

    // The migration-failure notice (the UX ruling): visible on all
    // four tabs, dismissed ONLY by its Dismiss button — closing the
    // dialog, Escape or switching tabs must not dismiss it. The page
    // is no-reflow exempt, so the banner wraps and the tab bar
    // re-anchors to it.
    Item {
        id: migrationNotice
        objectName: "migrationNotice"
        anchors.top: saveRefusedLabel.visible ? saveRefusedLabel.bottom : machineLabel.bottom
        anchors.topMargin: UM.Theme.getSize("default_margin").height / 2
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("default_margin").width
        anchors.right: parent.right
        anchors.rightMargin: UM.Theme.getSize("default_margin").width
        visible: manager.migrationBannerVisible
        implicitHeight: noticeColumn.implicitHeight
        ColumnLayout {
            id: noticeColumn
            anchors.fill: parent
            spacing: UM.Theme.getSize("default_margin").height / 2
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("error")
                text: manager.migrationBannerText
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2
                Item {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 1
                }
                Cura.SecondaryButton {
                    id: migrationShowBackupButton
                    objectName: "migrationShowBackupButton"
                    visible: manager.migrationBackupAvailable
                    text: "Show backup folder"
                    onClicked: manager.openMigrationBackupFolder()
                }
                Cura.SecondaryButton {
                    id: migrationDismissButton
                    objectName: "migrationDismissButton"
                    text: "Dismiss"
                    onClicked: manager.dismissMigrationBanner()
                }
            }
        }
    }

    UM.TabRow {
        id: tabBar
        z: 5
        anchors.top: migrationNotice.visible ? migrationNotice.bottom : (saveRefusedLabel.visible ? saveRefusedLabel.bottom : machineLabel.bottom)
        anchors.topMargin: UM.Theme.getSize("default_margin").height
        width: parent.width

        UM.TabRowButton {
            checked: true
            text: "Connection"
        }
        UM.TabRowButton {
            text: "Following"
        }
        UM.TabRowButton {
            text: "Upload"
        }
        UM.TabRowButton {
            text: "Diagnostics"
        }
    }

    Cura.RoundedRectangle {
        id: tabView
        anchors.top: tabBar.bottom
        anchors.topMargin: -UM.Theme.getSize("default_lining").height
        anchors.bottom: actionButtons.top
        anchors.bottomMargin: UM.Theme.getSize("default_margin").height
        anchors.left: parent.left
        anchors.right: parent.right
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        color: UM.Theme.getColor("main_background")
        radius: UM.Theme.getSize("default_radius").width
        cornerSide: Cura.RoundedRectangle.Direction.Down

        StackLayout {
            anchors.fill: parent
            currentIndex: tabBar.currentIndex

            ConnectionSettings {
                id: connectionSettings
                settings: manager
                followingEnabled: followingSettings.values.enabled
            }

            FollowingSettings {
                id: followingSettings
                settings: manager
            }

            UploadSettings {
                id: uploadSettings
                settings: manager
            }
            DiagnosticsSettings {
                id: diagnosticsSettings
                settings: manager
            }
        }
    }

    Item {
        id: actionButtons
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        height: Math.max(saveButton.implicitHeight, cancelButton.implicitHeight)

        Flow {
            anchors.fill: parent
            layoutDirection: Qt.RightToLeft
            spacing: UM.Theme.getSize("default_margin").width
            Cura.SecondaryButton {
                id: cancelButton
                text: "Cancel"
                onClicked: base.cancel(true)
            }
            Cura.PrimaryButton {
                id: saveButton
                text: "Save"
                enabled: base.canSave
                onClicked: base.save(true)
            }
        }
    }
}
