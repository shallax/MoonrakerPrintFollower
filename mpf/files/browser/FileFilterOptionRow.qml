import QtQuick 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

// One option row of a filter dropdown. The row owns its selection
// FACE — the native Cura.RadioButton for the single-value categories,
// the themed checkbox for the multi-select ones — and reports the
// intent; which model write that becomes stays with the shell. Nothing
// here reads a model or a parent chain: the option arrives as a
// declared input.
Item {
    id: root

    property string label: ""
    property string optionKey: ""
    property string category: ""
    property bool radio: false
    property bool selected: false
    property int count: 0

    // The two selection semantics, reported apart so the shell maps
    // each to its own model write and never re-derives the rule.
    signal toggled(string category, string key)
    signal valueSet(string category, string key)

    width: 240 * screenScaleFactor
    height: 28 * screenScaleFactor
    Rectangle {
        anchors.fill: parent
        // Inset by the border width: a flush hover rect paints OVER the
        // dropdown's outline (the live report: the border vanished on
        // hover).
        anchors.margins: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
        color: rowMouse.containsMouse ? UM.Theme.getColor("setting_category") : "transparent"
    }
    // The selection marker: the native themed checkbox for
    // multi-select categories, the native Cura.RadioButton for radios
    // (the Uranium-controls-first ruling).
    Cura.RadioButton {
        id: optionRadio
        visible: root.radio
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
        anchors.verticalCenter: parent.verticalCenter
        checked: root.selected
        onClicked: {
            root.valueSet(root.category, root.optionKey);
            optionRadio.checked = Qt.binding(function () {
                return root.selected;
            });
        }
    }
    UM.CheckBox {
        id: optionCheck
        visible: !root.radio
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width
        anchors.verticalCenter: parent.verticalCenter
        checked: root.selected
        onClicked: {
            root.toggled(root.category, root.optionKey);
            optionCheck.checked = Qt.binding(function () {
                return root.selected;
            });
        }
    }
    UM.Label {
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + 22 * screenScaleFactor
        anchors.right: parent.right
        anchors.rightMargin: 60 * screenScaleFactor
        anchors.verticalCenter: parent.verticalCenter
        text: root.label
        elide: Text.ElideRight
        font: UM.Theme.getFont("default")
    }
    UM.Label {
        anchors.right: parent.right
        anchors.rightMargin: UM.Theme.getSize("narrow_margin").width
        anchors.verticalCenter: parent.verticalCenter
        text: "(" + root.count + ")"
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("default")
    }
    MouseArea {
        id: rowMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            if (root.radio) {
                root.valueSet(root.category, root.optionKey);
            } else {
                root.toggled(root.category, root.optionKey);
            }
        }
    }
}
