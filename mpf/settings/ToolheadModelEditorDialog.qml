import QtQuick 2.15
import QtQuick.Window 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

UM.Dialog {
    id: root
    objectName: "toolheadModelEditorDialog"
    title: "Configure toolhead model"
    property var model: null
    property bool editing: false
    closeOnAccept: model && model.valid
    onClosing: {
        if (editing && model)
            model.endEdit(false);
        editing = false;
    }
    minimumWidth: 760 * screenScaleFactor
    minimumHeight: 520 * screenScaleFactor
    width: Math.max(minimumWidth, Math.min(1120 * screenScaleFactor, Screen.width * 0.9))
    height: Math.max(minimumHeight, Math.min(820 * screenScaleFactor, Screen.height * 0.9))

    function openEditor() {
        if (!model || model.busy)
            return;
        model.beginEdit();
        editing = true;
        open();
    }
    onRejected: {
        if (editing && model)
            model.endEdit(false);
        editing = false;
    }
    onAccepted: {
        if (!model || !model.valid)
            return;
        if (editing && model)
            model.endEdit(true);
        editing = false;
    }
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        spacing: UM.Theme.getSize("default_margin").height
        Loader {
            id: editorLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: root.visible && root.editing
            sourceComponent: ToolheadModelEditor {
                model: root.model
            }
        }
        RowLayout {
            Layout.fillWidth: true
            UM.Label {
                text: "Done keeps these changes. Save in printer settings makes them permanent."
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }
            Cura.SecondaryButton {
                objectName: "toolheadEditorCancel"
                text: "Cancel"
                onClicked: root.reject()
            }
            Cura.PrimaryButton {
                objectName: "toolheadEditorDone"
                text: "Done"
                enabled: root.model && root.model.valid
                onClicked: root.accept()
            }
        }
    }
}
