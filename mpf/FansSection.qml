import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Fan-speed section (4.3.0 extraction): the per-fan sliders out of
// the dashboard as one property-driven component. The host owns the
// freeze lists and the refocus settle; the sliders report interaction
// through the sink so that machinery stays single-owner.
ColumnLayout {
    id: root
    spacing: 0
    // The harness address (the s8 track-click scenario scrolls the
    // section into view before pressing the fan slider).
    objectName: "moonrakerFansSection"
    property var printerModel: null
    property bool freezeRepeaters: false
    property var frozenItems: []
    property var interactionSink: null
    property var focusSink: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Fan speed"
        sectionId: "fans"
        sectionIcon: "Fan"
    }
    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel != null && root.printerModel.fanControlItems.length > 0 && root.printerModel.sectionExpandedMap["fans"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        Repeater {
            id: fanRepeater
            model: root.freezeRepeaters ? root.frozenItems : (root.printerModel != null ? root.printerModel.fanControlItems : [])
            ColumnLayout {
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    UM.Label {
                        text: modelData.name
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    UM.Label {
                        width: 52 * screenScaleFactor
                        horizontalAlignment: Text.AlignRight
                        text: modelData.writable ? fanSlider.selectedValue() + "%" : modelData.percent + "%"
                    }
                }
                OutlineSlider {
                    id: fanSlider
                    Layout.fillWidth: true
                    visible: modelData.writable
                    controlObject: modelData.object
                    controlKind: "fan"
                    from: 0
                    to: 100
                    stepSize: 1
                    live: false
                    value: modelData.percent
                    onValueTuning: {
                        if (root.printerModel != null)
                            root.printerModel.previewFanSpeed(modelData.object, value);
                    }
                    onValueCommitted: {
                        if (root.printerModel != null)
                            root.printerModel.setFanSpeed(modelData.object, value);
                    }
                    onInteractingChanged: {
                        if (root.interactionSink != null)
                            root.interactionSink(interacting, modelData.object, "fan");
                    }
                    onFocusLostByDestruction: {
                        if (root.focusSink != null)
                            root.focusSink(object, kind);
                    }
                }
                // Firmware-regulated fans render their
                // value without a slider (a live
                // report — the command never
                // sticks).
                UM.Label {
                    visible: !modelData.writable
                    Layout.fillWidth: true
                    text: "Firmware-controlled — speed is read-only"
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default_italic")
                }
            }
        }
    }
}
