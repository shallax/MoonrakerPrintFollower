import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "theme"

// The Filament-sensors section (4.3.0 extraction): the sensor rows
// out of the monitor as one property-driven component. The host
// keeps the capability gate.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Filament sensors"
        sectionId: "filament"
        sectionIcon: "Spool"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["filament"] !== false
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height / 2

        Repeater {
            model: root.printerModel != null ? root.printerModel.filamentSensorItems : []
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width
                UM.Label {
                    text: modelData.name
                    color: UM.Theme.getColor("text_inactive")
                    Layout.preferredWidth: 110 * screenScaleFactor
                    elide: Text.ElideRight
                }
                UM.Label {
                    text: modelData.state
                    color: !modelData.enabled ? UM.Theme.getColor("text_inactive") : (modelData.detected ? MoonrakerTheme.filamentDetected : MoonrakerTheme.warningOrange)
                    font: UM.Theme.getFont("medium")
                }
            }
        }
    }
}
