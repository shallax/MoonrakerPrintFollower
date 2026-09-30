import QtQuick 2.15

// Component-rooted DELIBERATELY: Cura's monitor-view loader
// (setMonitorViewQmlPath) creates this document and expects a
// Component it can instantiate — unwrapping to the modern item-root
// form loaded fine in the capture harness but made the real Monitor
// stage fall back to Cura's placeholder. Qt logs a typecompiler
// deprecation for the pattern; that warning is accepted.
// The shell imports QtQuick ONLY so its compile finishes in
// milliseconds: the stage reads the CONSTANT monitorItem property
// exactly once, and a read landing while the document was still
// compiling cached a null forever (the dashboard-absent
// boots). The heavy dashboard compiles asynchronously here, off the
// startup path (nothing on the main thread waits), and its Component
// wrapper is instantiated here too — the dashboard document is
// Component-rooted, so the wrapper, not the raw component, is what
// the inner Loader instantiates.
Component {
    id: bedMeshComponent
    Item {
        id: root

        property var dashboardComponent: null

        Loader {
            id: monitorLoader
            anchors.fill: parent
        }

        function attachDashboard() {
            if (dashboardComponent === null || monitorLoader.item !== null) {
                return;
            }
            var wrapper = dashboardComponent.createObject(monitorLoader);
            if (wrapper !== null) {
                monitorLoader.sourceComponent = wrapper;
            } else {
                console.log("Moonraker dashboard failed to instantiate: " + dashboardComponent.errorString());
            }
        }

        // The status handler is a NAMED function on the root item: the
        // old inline connect() closure resolved attachDashboard in the
        // component TYPE scope and threw ReferenceError on the real
        // Windows run (a log) — named functions resolve
        // through the instance scope reliably.
        function onDashboardStatus(status) {
            if (status === Component.Ready) {
                attachDashboard();
            } else if (status === Component.Error) {
                console.log("Moonraker dashboard failed to compile: " + dashboardComponent.errorString());
            }
        }

        Component.onCompleted: {
            dashboardComponent = Qt.createComponent("MoonrakerMonitorDashboard.qml", Component.Asynchronous);
            if (dashboardComponent.status === Component.Ready) {
                attachDashboard();
            } else if (dashboardComponent.status === Component.Error) {
                console.log("Moonraker dashboard failed to compile: " + dashboardComponent.errorString());
            } else {
                dashboardComponent.statusChanged.connect(onDashboardStatus);
            }
        }
    }
}
