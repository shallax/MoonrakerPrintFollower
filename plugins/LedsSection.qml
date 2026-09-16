import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The LEDs section (4.3.0 extraction): the per-LED brightness and
// channel sliders out of the dashboard as one property-driven
// component. The host owns the freeze lists and the refocus settle;
// the sliders report interaction through the sink.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    property bool freezeRepeaters: false
    property var frozenItems: []
    property var interactionSink: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "LEDs"
        sectionId: "leds"
        sectionIcon: "Star"
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel != null && root.printerModel.ledItems.length > 0 && root.printerModel.sectionExpandedMap["leds"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("thin_margin").height
        Repeater {
            id: ledRepeater
            model: root.freezeRepeaters ? root.frozenItems : (root.printerModel != null ? root.printerModel.ledItems : [])
            ColumnLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").height

                function previewLedColour() {
                    if (root.printerModel == null)
                        return;
                    root.printerModel.previewLedColor(modelData.object, redSlider.selectedValue(), greenSlider.selectedValue(), blueSlider.selectedValue(), modelData.hasWhite ? whiteSlider.selectedValue() : 0, -1);
                }

                function applyLedColour() {
                    if (root.printerModel == null)
                        return;
                    // The channels are ABSOLUTE values; the
                    // brightness slider is NOT passed —
                    // its own lane scales the colour, and
                    // passing it here as the gain zeroed
                    // every channel nudge while the LED
                    // was off (the author's live report).
                    root.printerModel.setLedColor(modelData.object, redSlider.selectedValue(), greenSlider.selectedValue(), blueSlider.selectedValue(), modelData.hasWhite ? whiteSlider.selectedValue() : 0, -1);
                }

                RowLayout {
                    Layout.fillWidth: true
                    UM.Label {
                        text: modelData.name
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    UM.Label {
                        width: 150 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: "Brightness " + ledSlider.selectedValue() + "%"
                    }
                }
                OutlineSlider {
                    id: ledSlider
                    Layout.fillWidth: true
                    controlObject: modelData.object
                    controlKind: "led-brightness"
                    from: 0
                    to: 100
                    stepSize: 1
                    live: false
                    value: modelData.percent
                    onValueTuning: {
                        if (root.printerModel != null)
                            root.printerModel.previewLedBrightness(modelData.object, value);
                    }
                    onValueCommitted: {
                        if (root.printerModel != null)
                            root.printerModel.setLedBrightness(modelData.object, value);
                    }
                    onInteractingChanged: {
                        if (root.interactionSink != null)
                            root.interactionSink(interacting, modelData.object, "led-brightness");
                    }
                }

                GridLayout {
                    columns: 3
                    Layout.fillWidth: true
                    columnSpacing: UM.Theme.getSize("thin_margin").width
                    rowSpacing: UM.Theme.getSize("thin_margin").height

                    UM.Label {
                        width: 16 * screenScaleFactor
                        text: "R"
                        color: UM.Theme.getColor("text_inactive")
                    }
                    OutlineSlider {
                        id: redSlider
                        Layout.fillWidth: true
                        controlObject: modelData.object
                        controlKind: "led-red"
                        from: 0
                        to: 100
                        stepSize: 1
                        live: false
                        value: modelData.redPercent
                        onValueTuning: previewLedColour()
                        onValueCommitted: applyLedColour()
                        onInteractingChanged: {
                            if (root.interactionSink != null)
                                root.interactionSink(interacting, modelData.object, "led-red");
                        }
                    }
                    UM.Label {
                        width: 52 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: redSlider.selectedValue() + "%"
                    }

                    UM.Label {
                        width: 16 * screenScaleFactor
                        text: "G"
                        color: UM.Theme.getColor("text_inactive")
                    }
                    OutlineSlider {
                        id: greenSlider
                        Layout.fillWidth: true
                        controlObject: modelData.object
                        controlKind: "led-green"
                        from: 0
                        to: 100
                        stepSize: 1
                        live: false
                        value: modelData.greenPercent
                        onValueTuning: previewLedColour()
                        onValueCommitted: applyLedColour()
                        onInteractingChanged: {
                            if (root.interactionSink != null)
                                root.interactionSink(interacting, modelData.object, "led-green");
                        }
                    }
                    UM.Label {
                        width: 52 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: greenSlider.selectedValue() + "%"
                    }

                    UM.Label {
                        width: 16 * screenScaleFactor
                        text: "B"
                        color: UM.Theme.getColor("text_inactive")
                    }
                    OutlineSlider {
                        id: blueSlider
                        Layout.fillWidth: true
                        controlObject: modelData.object
                        controlKind: "led-blue"
                        from: 0
                        to: 100
                        stepSize: 1
                        live: false
                        value: modelData.bluePercent
                        onValueTuning: previewLedColour()
                        onValueCommitted: applyLedColour()
                        onInteractingChanged: {
                            if (root.interactionSink != null)
                                root.interactionSink(interacting, modelData.object, "led-blue");
                        }
                    }
                    UM.Label {
                        width: 52 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: blueSlider.selectedValue() + "%"
                    }

                    UM.Label {
                        visible: modelData.hasWhite
                        width: 16 * screenScaleFactor
                        text: "W"
                        color: UM.Theme.getColor("text_inactive")
                    }
                    OutlineSlider {
                        id: whiteSlider
                        visible: modelData.hasWhite
                        Layout.fillWidth: true
                        controlObject: modelData.object
                        controlKind: "led-white"
                        from: 0
                        to: 100
                        stepSize: 1
                        live: false
                        value: modelData.whitePercent
                        onValueTuning: previewLedColour()
                        onValueCommitted: applyLedColour()
                        onInteractingChanged: {
                            if (root.interactionSink != null)
                                root.interactionSink(interacting, modelData.object, "led-white");
                        }
                    }
                    UM.Label {
                        visible: modelData.hasWhite
                        width: 52 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: whiteSlider.selectedValue() + "%"
                    }
                }
            }
        }
    }
}
