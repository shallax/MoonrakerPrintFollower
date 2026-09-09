import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// Component-rooted DELIBERATELY: Cura's monitor-view loader
// (setMonitorViewQmlPath) creates this document and expects a
// Component it can instantiate — unwrapping to the modern item-root
// form loaded fine in the capture harness but made the real Monitor
// stage fall back to Cura's placeholder. Qt logs a typecompiler
// deprecation for the pattern; that warning is accepted.
Component {
    id: bedMeshComponent
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
