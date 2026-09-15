import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The System section (4.3.0 extraction): the host readouts and
// the manual Reconnect out of the monitor as one
// property-driven component.
Column {
    id: root
    property var printerModel: null

    CollapsibleSectionHeader {
        width: parent.width
        printerModel: root.printerModel
        title: "System"
        sectionId: "systeminfo"
        sectionIcon: "Information"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["systeminfo"] !== false
        width: parent.width - UM.Theme.getSize("narrow_margin").width - UM.Theme.getSize("section_icon").width / 2
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        spacing: UM.Theme.getSize("default_margin").height

        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            UM.Label {
                text: "Klippy"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.klippyState : "—"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "Host load"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.hostLoad : "—"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "Memory free"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.memoryAvailable : "—"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "CPU temp"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.cpuTemperature : "—"
                Layout.fillWidth: true
            }

            UM.Label {
                text: "Klipper"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.klipperVersion : "—"
                Layout.fillWidth: true
                elide: Text.ElideMiddle
            }

            UM.Label {
                text: "Moonraker"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                text: root.printerModel != null ? root.printerModel.moonrakerVersion : "—"
                Layout.fillWidth: true
                elide: Text.ElideMiddle
            }
        }

        // The manual Reconnect (the
        // live request): the recovery for a UI
        // stuck after a printer error or a
        // dropped connection — cycles the client
        // and re-arms the monitor.
        Cura.SecondaryButton {
            Layout.alignment: Qt.AlignRight
            text: "Reconnect"
            enabled: root.printerModel != null
            onClicked: {
                if (root.printerModel != null) {
                    root.printerModel.reconnect();
                }
            }
        }
    }
    Item {
        width: 1
        height: UM.Theme.getSize("default_margin").height
    }
}
