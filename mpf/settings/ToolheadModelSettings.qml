import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import MoonrakerPrintFollower 1.0

Column {
    id: root
    property var model: null
    property var editorWindow: null
    spacing: UM.Theme.getSize("thin_margin").height

    UM.Label {
        text: "Toolhead model"
        font: UM.Theme.getFont("medium_bold")
    }
    UM.Label {
        width: parent.width
        text: root.model ? root.model.name : "Default indicator"
        elide: Text.ElideMiddle
    }
    Flow {
        width: parent.width
        spacing: UM.Theme.getSize("thin_margin").width
        Cura.SecondaryButton {
            objectName: "toolheadChooseModel"
            text: "Choose model…"
            enabled: root.model && !root.model.busy
            onClicked: picker.open()
        }
        Cura.SecondaryButton {
            objectName: "toolheadUseDefault"
            text: "Use default"
            enabled: root.model && !root.model.busy
            onClicked: root.model.useDefault()
        }
        Cura.SecondaryButton {
            objectName: "toolheadConfigureModel"
            text: "Configure model…"
            enabled: root.model && !root.model.busy
            onClicked: {
                if (!root.editorWindow)
                    root.editorWindow = editorComponent.createObject(root, {
                        "model": root.model
                    });
                root.editorWindow.openEditor();
            }
        }
    }
    UM.Label {
        width: parent.width
        text: "Import STL in millimetres, or STEP/STP with colours. Model Z should point up."
        wrapMode: Text.WordWrap
        color: UM.Theme.getColor("text_inactive")
    }
    UM.Label {
        width: parent.width
        text: "The nozzle tip is detected at the centre of the lowest surface. Use Configure model to pick a different tip, adjust its coordinates, or add lights."
        wrapMode: Text.WordWrap
        color: UM.Theme.getColor("text_inactive")
    }
    UM.Label {
        objectName: "toolheadImportStatus"
        width: parent.width
        text: root.model ? root.model.status : ""
        wrapMode: Text.WordWrap
        maximumLineCount: 3
        elide: Text.ElideRight
        clip: true
        color: UM.Theme.getColor("text")
        visible: text.length > 0
    }
    Row {
        visible: root.model && (root.model.busy || root.model.needsDownload)
        height: visible ? Math.max(downloadButton.implicitHeight, cancelImportButton.implicitHeight) : 0
        spacing: UM.Theme.getSize("thin_margin").width
        Cura.SecondaryButton {
            id: downloadButton
            text: "Download reader & import"
            visible: root.model && root.model.needsDownload
            onClicked: root.model.downloadAndImport()
        }
        Cura.SecondaryButton {
            id: cancelImportButton
            objectName: "toolheadCancelImport"
            text: "Cancel import"
            visible: root.model && (root.model.busy || root.model.needsDownload)
            onClicked: root.model.cancel()
        }
    }
    UM.Label {
        objectName: "toolheadImportElapsed"
        width: parent.width
        text: root.model ? root.model.elapsedText : ""
        visible: text.length > 0
    }
    Component {
        id: editorComponent
        ToolheadModelEditorDialog {}
    }
    FileDialog {
        id: picker
        title: "Choose a toolhead model"
        nameFilters: ["Toolhead models (*.stl *.STL *.step *.STEP *.stp *.STP)"]
        onAccepted: root.model.choose(String(selectedFile))
    }
}
