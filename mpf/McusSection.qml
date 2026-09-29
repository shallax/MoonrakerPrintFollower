import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The MCUs section (4.3.0 extraction): the per-MCU readout
// rows out of the monitor as one property-driven component.
// The host keeps the capability gate.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "MCUs"
        sectionId: "mcus"
        sectionIcon: "Plugin"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["mcus"] !== false
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.rightMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("thin_margin").height

        Repeater {
            model: root.printerModel != null ? root.printerModel.mcuItems : []
            ColumnLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").height
                UM.Label {
                    Layout.fillWidth: true
                    text: modelData.name
                    font: UM.Theme.getFont("medium_bold")
                    elide: Text.ElideRight
                }
                // Each datum gets its own labelled row,
                // in the same fixed 110 px column as
                // the other readouts in the pane.
                GridLayout {
                    columns: 2
                    columnSpacing: UM.Theme.getSize("default_margin").width
                    rowSpacing: UM.Theme.getSize("default_margin").height / 2
                    Layout.fillWidth: true

                    UM.Label {
                        text: "Load"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.preferredWidth: 110 * screenScaleFactor
                    }
                    UM.Label {
                        text: modelData.load
                        Layout.fillWidth: true
                    }

                    UM.Label {
                        text: "Task"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.preferredWidth: 110 * screenScaleFactor
                    }
                    UM.Label {
                        text: modelData.task
                        Layout.fillWidth: true
                    }

                    UM.Label {
                        text: "Clock"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.preferredWidth: 110 * screenScaleFactor
                    }
                    UM.Label {
                        text: modelData.frequency
                        Layout.fillWidth: true
                    }

                    UM.Label {
                        text: "Memory"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.preferredWidth: 110 * screenScaleFactor
                    }
                    UM.Label {
                        text: modelData.memory
                        Layout.fillWidth: true
                    }

                    UM.Label {
                        text: "Connection"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.preferredWidth: 110 * screenScaleFactor
                    }
                    UM.Label {
                        text: modelData.transport
                        Layout.fillWidth: true
                    }

                    UM.Label {
                        text: "Version"
                        color: UM.Theme.getColor("text_inactive")
                        Layout.preferredWidth: 110 * screenScaleFactor
                    }
                    UM.Label {
                        text: modelData.version
                        Layout.fillWidth: true
                        elide: Text.ElideMiddle
                    }
                }
            }
        }
    }
}
