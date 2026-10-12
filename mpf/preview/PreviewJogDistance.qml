import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import Cura 1.1 as Cura

// Presentation only: exact distances remain separate from the preset index.
Column {
    id: root
    objectName: "previewJogDistance"
    property real distance: 25
    readonly property var presets: [0.1, 0.5, 1, 5, 10, 25, 50, 100, 125]
    property real scale: screenScaleFactor
    property string validationMessage: ""
    property bool discardingDraft: false
    readonly property bool draftValid: exact.text.trim() !== "" && isFinite(Number(exact.text)) && Number(exact.text) >= 0.01 && Number(exact.text) <= 300
    readonly property real previewDistance: exact.activeFocus && draftValid ? Number(exact.text) : root.distance
    signal distanceRequested(real distance)
    spacing: 3 * scale

    function positionFor(value) {
        if (value <= presets[0])
            return 0;
        for (var i = 1; i < presets.length; i++) {
            if (value <= presets[i])
                return i - 1 + (value - presets[i - 1]) / (presets[i] - presets[i - 1]);
        }
        return presets.length - 1;
    }

    function selectPreset(index) {
        root.distanceRequested(root.presets[Math.max(0, Math.min(8, index))]);
    }

    function submitText() {
        var value = Number(exact.text);
        if (root.draftValid) {
            root.distanceRequested(value);
            validationMessage = "";
        } else {
            validationMessage = "Enter 0.01–300 mm";
        }
        exact.text = String(root.distance);
    }

    function cancelDraft() {
        discardingDraft = true;
        exact.text = String(root.distance);
        validationMessage = "";
        exact.focus = false;
        discardingDraft = false;
    }

    onEnabledChanged: if (!enabled)
        cancelDraft()

    onDistanceChanged: {
        if (!exact.activeFocus)
            exact.text = String(root.distance);
    }

    Item {
        width: parent.width
        height: 30 * root.scale
        UM.Label {
            text: "Move distance"
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
        }
        UM.Label {
            anchors.right: exact.left
            anchors.rightMargin: 6 * root.scale
            anchors.verticalCenter: parent.verticalCenter
            text: root.presets.indexOf(root.previewDistance) !== -1 ? "" : root.previewDistance > 125 ? ">125" : root.previewDistance < 0.1 ? "<0.1" : "Custom"
            font: UM.Theme.getFont("small")
        }
        Cura.TextField {
            id: exact
            objectName: "previewJogDistanceExact"
            anchors.right: parent.right
            width: 58 * root.scale
            height: parent.height
            text: String(root.distance)
            selectByMouse: true
            Accessible.name: "Exact jog distance in millimetres"
            onTextEdited: root.validationMessage = root.draftValid ? "" : "Enter 0.01–300 mm"
            onEditingFinished: if (!root.discardingDraft && root.enabled && text !== String(root.distance))
                root.submitText()
        }
    }
    Slider {
        id: slider
        objectName: "previewJogDistanceSlider"
        width: parent.width
        height: 22 * root.scale
        from: 0
        to: 8
        stepSize: 0
        value: root.positionFor(root.previewDistance)
        focusPolicy: Qt.StrongFocus
        Accessible.name: "Jog distance preset"
        Accessible.description: root.distance + " millimetres"
        onMoved: {
            root.selectPreset(Math.round(value));
            slider.value = Qt.binding(function () {
                return root.positionFor(root.previewDistance);
            });
        }
        Keys.onPressed: function (event) {
            var position = root.positionFor(root.distance);
            if (event.key === Qt.Key_Left || event.key === Qt.Key_Down) {
                root.selectPreset(Math.ceil(position) - 1);
            } else if (event.key === Qt.Key_Right || event.key === Qt.Key_Up) {
                root.selectPreset(Math.floor(position) + 1);
            } else if (event.key === Qt.Key_Home) {
                root.selectPreset(0);
            } else if (event.key === Qt.Key_End) {
                root.selectPreset(8);
            } else {
                event.accepted = false;
                return;
            }
            event.accepted = true;
        }
        background: Rectangle {
            x: slider.leftPadding
            y: slider.topPadding + (slider.availableHeight - height) / 2
            width: slider.availableWidth
            height: 3 * root.scale
            radius: height / 2
            color: UM.Theme.getColor("lining")
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + (slider.availableHeight - height) / 2
            width: 14 * root.scale
            height: width
            radius: width / 2
            color: UM.Theme.getColor("primary")
            border.width: slider.activeFocus ? 2 * root.scale : 0
            border.color: UM.Theme.getColor("text")
        }
    }
    Item {
        width: parent.width
        height: 18 * root.scale
        Repeater {
            model: root.presets
            UM.Label {
                objectName: "previewJogDistanceTick" + index
                text: String(modelData)
                font: UM.Theme.getFont("small")
                color: modelData === root.distance ? UM.Theme.getColor("primary") : UM.Theme.getColor("text")
                x: index === 0 ? 0 : index === 8 ? parent.width - width : 7 * root.scale + index / 8 * (parent.width - 14 * root.scale) - width / 2
            }
        }
    }
    UM.Label {
        objectName: "previewJogDistanceValidation"
        width: parent.width
        height: 16 * root.scale
        text: root.validationMessage
        font: UM.Theme.getFont("small")
    }
}
