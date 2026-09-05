import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3

import UM 1.5 as UM
import Cura 1.1 as Cura

UM.Dialog
{
    id: base
    title: "Upload to Moonraker"
    minimumWidth: 520 * screenScaleFactor
    minimumHeight: 280 * screenScaleFactor

    // Cura.ComboBox expects a catalog in the creation context.  Supplying it here
    // avoids the undefined-catalog QML error seen in Cura 5.13's upload dialog.
    property variant catalog: UM.I18nCatalog { name: "cura" }
    property string forbiddenCharacters: ":*?\"<>|"

    function validFilename(value)
    {
        var name = value.trim()
        if (name.length === 0 || name === "." || name === "..") return false
        for (var i = 0; i < forbiddenCharacters.length; ++i)
        {
            if (name.indexOf(forbiddenCharacters.charAt(i)) !== -1) return false
        }
        return true
    }

    function validPath(value)
    {
        var path = value.trim()
        if (path === "." || path === "..") return false
        for (var i = 0; i < forbiddenCharacters.length; ++i)
        {
            if (path.indexOf(forbiddenCharacters.charAt(i)) !== -1) return false
        }
        return true
    }

    onRejected: manager.cancelUpload()

    ColumnLayout
    {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        spacing: UM.Theme.getSize("default_margin").height

        UM.Label
        {
            text: "Remote folder"
            font: UM.Theme.getFont("medium_bold")
        }

        Cura.ComboBox
        {
            id: pathField
            Layout.fillWidth: true
            editable: true
            model: manager.uploadPathOptions
            Component.onCompleted:
            {
                var index = find(manager.initialUploadPath)
                if (index >= 0) currentIndex = index
                editText = manager.initialUploadPath
            }
        }

        UM.Label
        {
            text: "Folders are read from Moonraker's gcodes directory; you can also type a folder path."
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("default_italic")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }

        UM.Label
        {
            visible: !base.validPath(pathField.editText)
            text: "The remote folder contains characters Moonraker cannot use."
            color: UM.Theme.getColor("error")
            font: UM.Theme.getFont("default_italic")
        }

        UM.Label
        {
            text: "Filename"
            font: UM.Theme.getFont("medium_bold")
        }

        Cura.TextField
        {
            id: filenameField
            Layout.fillWidth: true
            text: manager.initialUploadFilename
            maximumLength: 1024
            selectByMouse: true
        }

        UM.Label
        {
            visible: !base.validFilename(filenameField.text)
            text: "Enter a valid filename. Characters : * ? \" < > | are not allowed."
            color: UM.Theme.getColor("error")
            font: UM.Theme.getFont("default_italic")
        }

        UM.CheckBox
        {
            id: printField
            text: "Start printing after upload"
            checked: manager.initialStartPrint
        }

        Item { Layout.fillHeight: true }

        RowLayout
        {
            Layout.fillWidth: true
            layoutDirection: Qt.RightToLeft
            spacing: UM.Theme.getSize("default_margin").width

            Cura.PrimaryButton
            {
                text: printField.checked ? "Upload and print" : "Upload"
                enabled: base.validFilename(filenameField.text) && base.validPath(pathField.editText)
                onClicked:
                {
                    manager.acceptUpload(pathField.editText, filenameField.text, printField.checked)
                    base.accept()
                }
            }

            Cura.SecondaryButton
            {
                text: "Cancel"
                onClicked: base.reject()
            }
        }
    }
}
