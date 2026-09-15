import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Macros section (4.3.0 extraction): the selector, the typed
// parameter rows and the run gate moved out of the dashboard as one
// property-driven component, owning the parameter lifecycle.
Column {
    id: root
    property var printerModel: null
    property var macroParameters: []

    function refreshMacroParameters() {
        if (root.printerModel == null || macroSelector.currentIndex < 0) {
            root.macroParameters = [];
            return;
        }
        root.macroParameters = root.printerModel.macroParameterDefinitions(macroSelector.currentText);
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

    Connections {
        target: root.printerModel
        function onControlsChanged() {
            root.refreshMacroParameters();
        }
        function onTypedControlsChanged() {
            root.refreshMacroParameters();
        }
    }

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Macros"
        sectionId: "macros"
        sectionIcon: "Function"
    }
    Item {
        width: 1
        height: UM.Theme.getSize("default_margin").height
    }

    ColumnLayout {
        id: macroSection
        width: parent.width - UM.Theme.getSize("narrow_margin").width - UM.Theme.getSize("section_icon").width / 2
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        visible: root.printerModel != null && root.printerModel.macroNames.length > 0 && root.printerModel.sectionExpandedMap["macros"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height / 2

        Cura.ComboBox {
            id: macroSelector
            Layout.fillWidth: true
            model: root.printerModel != null ? root.printerModel.macroNames : []
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
            enabled: root.printerModel != null && macroSelector.currentIndex >= 0 && !root.printerModel.actionBusy && !root.printerModel.printActive && root.printerModel.sectionReason === "" && root.macroArgumentsValid()
            onClicked: root.printerModel.runMacro(macroSelector.currentText, root.macroArgumentString())
        }
    }

    Item {
        width: 1
        height: UM.Theme.getSize("default_margin").height
    }
}
