import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: base
    required property var settings
    property bool validRetryInterval: settings.validRetryInterval(retryIntervalField.text)
    property bool validTranslation: settings.validTranslation(translateInputField.text, translateOutputField.text)
    readonly property var values: ({
            "frontend_url": frontendUrlField.text,
            "output_format": outputFormatBox.currentIndex === 1 ? "ufp" : "gcode",
            "upload_dialog": uploadDialogBox.checked,
            "upload_path": uploadPathField.text,
            "upload_start_print": uploadStartPrintBox.checked,
            "upload_remember_state": uploadRememberStateBox.checked,
            "upload_autohide_message": uploadAutohideBox.checked,
            "power_devices": powerDevicesField.text,
            "ready_retry_interval_s": retryIntervalField.text,
            "filename_translate_input": translateInputField.text,
            "filename_translate_output": translateOutputField.text,
            "filename_translate_remove": translateRemoveField.text
        })
    Flickable {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        contentWidth: width
        contentHeight: outputColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: outputColumn
            width: parent.width
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label {
                text: "A valid Moonraker URL automatically adds an Upload to printer destination to Cura's save/upload menu."
                wrapMode: Text.WordWrap
                width: parent.width
                color: UM.Theme.getColor("text_inactive")
            }

            UM.Label {
                text: "Frontend URL for Open Browser (optional)"
            }
            Cura.TextField {
                id: frontendUrlField
                width: parent.width
                text: settings.settingsFrontendUrl
                maximumLength: 1024
            }

            UM.Label {
                text: "Upload format"
            }
            Cura.ComboBox {
                id: outputFormatBox
                width: parent.width
                model: ["G-code (.gcode)", "Ultimaker Format Package (.ufp)"]
                currentIndex: settings.settingsOutputFormat === "ufp" ? 1 : 0
            }

            UM.CheckBox {
                id: uploadDialogBox
                text: "Show filename/path dialog before upload"
                checked: settings.settingsUploadDialog
            }

            UM.Label {
                text: "Default remote folder"
            }
            Cura.TextField {
                id: uploadPathField
                width: parent.width
                text: settings.settingsUploadPath
                placeholderText: "<root>"
                maximumLength: 1024
            }
            UM.Label {
                text: "Leave blank to use Moonraker's gcodes root."
                width: parent.width
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
            }

            UM.CheckBox {
                id: uploadStartPrintBox
                text: "Start printing after upload by default"
                checked: settings.settingsUploadStartPrint
            }
            UM.CheckBox {
                id: uploadRememberStateBox
                text: "Remember folder and print checkbox choices from the upload dialog"
                checked: settings.settingsUploadRememberState
            }
            UM.CheckBox {
                id: uploadAutohideBox
                text: "Auto-hide successful upload message"
                checked: settings.settingsUploadAutohideMessage
            }

            UM.Label {
                text: "Moonraker power devices (comma-separated, optional)"
            }
            Cura.TextField {
                id: powerDevicesField
                width: parent.width
                text: settings.settingsPowerDevices
                maximumLength: 1024
            }
            UM.Label {
                text: "When starting a print, the plugin can power these devices on first and wait for Klippy to report ready. The Monitor always shows every device the printer reports."
                wrapMode: Text.WordWrap
                width: parent.width
                color: UM.Theme.getColor("text_inactive")
            }

            UM.Label {
                text: "Printer-ready retry interval (seconds)"
            }
            Cura.TextField {
                id: retryIntervalField
                width: parent.width
                text: settings.settingsReadyRetryInterval
                maximumLength: 16
                onTextChanged: base.validRetryInterval = settings.validRetryInterval(text)
            }
            UM.Label {
                visible: !base.validRetryInterval
                text: "Retry interval must be between 0.1 and 60 seconds."
                color: UM.Theme.getColor("error")
                font: UM.Theme.getFont("default_italic")
            }

            UM.Label {
                text: "Filename character translation"
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                text: "Characters in the first field are replaced position-for-position by the second field."
                wrapMode: Text.WordWrap
                width: parent.width
                color: UM.Theme.getColor("text_inactive")
            }
            Cura.TextField {
                id: translateInputField
                width: parent.width
                text: settings.settingsTranslateInput
                placeholderText: "Characters to replace"
                maximumLength: 256
                onTextChanged: base.validTranslation = settings.validTranslation(text, translateOutputField.text)
            }
            Cura.TextField {
                id: translateOutputField
                width: parent.width
                text: settings.settingsTranslateOutput
                placeholderText: "Replacement characters"
                maximumLength: 256
                onTextChanged: base.validTranslation = settings.validTranslation(translateInputField.text, text)
            }
            Cura.TextField {
                id: translateRemoveField
                width: parent.width
                text: settings.settingsTranslateRemove
                placeholderText: "Characters to remove"
                maximumLength: 256
            }
            UM.Label {
                visible: !base.validTranslation
                text: "The replace and replacement fields must have the same number of characters."
                color: UM.Theme.getColor("error")
                font: UM.Theme.getFont("default_italic")
            }
        }
    }
}
