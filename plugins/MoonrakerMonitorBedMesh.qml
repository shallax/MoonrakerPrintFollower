import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Component {
    id: bedMeshDashboardComponent

    Item {
        id: root
        property var printer: OutputDevice != null ? OutputDevice.activePrinter : null

        MoonrakerMonitorDashboard {
            id: baseDashboardComponent
        }

        Loader {
            anchors.fill: parent
            sourceComponent: baseDashboardComponent
        }
    }
}
