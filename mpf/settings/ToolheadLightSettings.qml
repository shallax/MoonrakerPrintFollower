import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Dialogs
import QtQuick.Layouts 1.3
import UM 1.7 as UM
import Cura 1.1 as Cura
import "../widgets"

Item {
    id: root
    property var model: null
    property var preview: null
    property int colourIndex: -1
    property bool sliderInteracting: false
    implicitHeight: 480 * screenScaleFactor

    Flickable {
        id: lightScroll
        objectName: "toolheadLightsScroll"
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: lightColumn.implicitHeight
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: !root.sliderInteracting
        ScrollBar.vertical: UM.ScrollBar {
            id: lightScrollbar
            objectName: "toolheadLightsScrollbar"
            policy: ScrollBar.AlwaysOn
            // UM.ScrollBar normally has zero width when its contents fit.
            // Reserve the same gutter even with no lights or a short list.
            implicitWidth: UM.Theme.getSize("scrollbar").width + leftPadding + rightPadding
        }
        Column {
            id: lightColumn
            width: lightScroll.width - lightScrollbar.width - UM.Theme.getSize("default_margin").width
            spacing: UM.Theme.getSize("default_margin").height
            UM.Label {
                text: "Toolhead lights"
                font: UM.Theme.getFont("medium_bold")
            }
            Cura.SecondaryButton {
                objectName: "toolheadAddLight"
                text: root.preview && root.preview.addingLight ? "Cancel adding light" : "Add light…"
                enabled: root.model && !root.model.busy && root.model.lights.length < 8
                onClicked: {
                    if (root.preview.addingLight)
                        root.preview.picking = false;
                    else
                        root.preview.addingLight = true;
                }
            }
            UM.Label {
                width: parent.width
                text: root.preview && root.preview.addingLight ? "Click a surface. The light shines outward, perpendicular to that face." : "Up to eight lights move with the toolhead. Save keeps your setup."
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }
            Repeater {
                model: root.model ? root.model.lights : []
                delegate: Column {
                    required property int index
                    required property var modelData
                    width: lightColumn.width
                    spacing: UM.Theme.getSize("thin_margin").height
                    Flow {
                        width: parent.width
                        spacing: UM.Theme.getSize("thin_margin").width
                        UM.Label {
                            text: "Light " + (index + 1)
                            height: colourButton.height
                            verticalAlignment: Text.AlignVCenter
                        }
                        Cura.SecondaryButton {
                            id: colourButton
                            objectName: "toolheadLightColour" + index
                            text: "Colour…"
                            onClicked: {
                                root.colourIndex = index;
                                colourPicker.selectedColor = modelData.colour;
                                colourPicker.open();
                            }
                            Rectangle {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: UM.Theme.getSize("default_lining").width
                                height: 3 * screenScaleFactor
                                color: modelData.colour
                            }
                        }
                        Cura.SecondaryButton {
                            objectName: "toolheadRemoveLight" + index
                            text: "Remove"
                            onClicked: root.model.removeLight(index)
                        }
                    }
                    UM.CheckBox {
                        objectName: "toolheadLightPaint" + index
                        text: "Colour this face"
                        checked: modelData.paint
                        onClicked: root.model.setLightPaint(index, checked)
                    }
                    Row {
                        width: parent.width
                        UM.Label {
                            width: parent.width - brightnessValue.implicitWidth
                            text: "Brightness"
                        }
                        UM.Label {
                            id: brightnessValue
                            text: brightnessSlider.selectedValue() + "%"
                        }
                    }
                    OutlineSlider {
                        id: brightnessSlider
                        property color lightColour: modelData.colour
                        objectName: "toolheadLightBrightness" + index
                        Accessible.name: "Light " + (index + 1) + " brightness percent"
                        width: parent.width
                        from: 0
                        to: 100
                        stepSize: 1
                        live: false
                        value: modelData.brightness * 20
                        fillColor: {
                            var intensity = 0.25 + 0.75 * selectedValue() / 100;
                            return Qt.rgba(lightColour.r * intensity, lightColour.g * intensity, lightColour.b * intensity, 1);
                        }
                        onInteractingChanged: root.sliderInteracting = interacting
                        Component.onDestruction: root.sliderInteracting = false
                        onValueTuning: value => root.model.previewLightBrightness(index, value / 20)
                        // Keep the delegate alive throughout the gesture.
                        onValueCommitted: value => root.model.setLightBrightness(index, value / 20)
                    }
                }
            }
        }
    }
    ColorDialog {
        id: colourPicker
        title: "Light colour"
        options: ColorDialog.DontUseNativeDialog
        onAccepted: root.model.setLightColour(root.colourIndex, String(selectedColor))
    }
}
