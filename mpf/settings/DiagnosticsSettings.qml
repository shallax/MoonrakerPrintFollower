import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: base
    required property var settings
    property bool validCacheMax: settings.validCacheMax(cacheMaxField.text)
    readonly property var values: ({
            "cache_max_mb": cacheMaxField.text,
            "trace_layer": layerTraceBox.checked,
            "trace_http": httpTraceBox.checked,
            "seek_trace": seekTraceBox.checked,
            "memory_diagnostics_log": memoryDiagnosticsBox.checked,
            "memory_diagnostics_trace": memoryDiagnosticsTraceBox.checked,
            "camera_disabled": cameraDisabledBox.checked,
            "software_follower_renderer": softwareFollowerRendererBox.checked
        })
    Flickable {
        anchors.fill: parent
        anchors.margins: UM.Theme.getSize("default_margin").width
        contentWidth: width
        contentHeight: diagnosticsColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        Column {
            id: diagnosticsColumn
            width: parent.width
            spacing: UM.Theme.getSize("default_margin").height

            UM.Label {
                width: parent.width
                text: "Diagnostic logging writes to Cura's log (Help > Show configuration folder). Request failures always log a warning regardless of these toggles."
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }
            UM.CheckBox {
                id: layerTraceBox
                text: "Log layer resolution (diagnostics)"
                checked: settings.settingsTraceLayer
            }
            UM.CheckBox {
                id: seekTraceBox
                text: "Log follower seek timelines (diagnostics — the stage-by-stage layer-seek trace, off by default)"
                checked: settings.settingsSeekTrace
            }
            UM.CheckBox {
                id: httpTraceBox
                text: "Log HTTP requests (diagnostics)"
                checked: settings.settingsTraceHttp
            }
            UM.CheckBox {
                id: memoryDiagnosticsBox
                text: "Log memory diagnostics (diagnostics)"
                checked: settings.settingsMemoryDiagnosticsLog
                // The trace rides the parent (the ruling):
                // unchecking the parent unchecks the trace.
                onCheckedChanged: if (!checked)
                    memoryDiagnosticsTraceBox.checked = false
            }
            UM.CheckBox {
                id: memoryDiagnosticsTraceBox
                text: "Log Python allocation traces (diagnostics — Cura becomes almost unusable while this runs; the first trace starts about a minute after enabling)"
                checked: settings.settingsMemoryDiagnosticsTrace
                // The trace only runs inside the main
                // diagnostics sampler — grey it out while
                // the main toggle is off so the two cannot
                // be read as independent switches.
                enabled: memoryDiagnosticsBox.checked
            }
            UM.CheckBox {
                id: softwareFollowerRendererBox
                text: "Use software Print Follower renderer"
                checked: settings.settingsSoftwareFollowerRenderer
            }
            UM.CheckBox {
                id: cameraDisabledBox
                text: "Disable webcam stream (diagnostics)"
                checked: settings.settingsCameraDisabled
            }
            // The permanent migration-failure row (the UX
            // ruling): the rollback recipe survives the
            // banner's dismissal here, never deleted.
            RowLayout {
                id: migrationDiagnosticsRow
                objectName: "migrationDiagnosticsRow"
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width / 2
                visible: settings.migrationDiagnosticsVisible
                UM.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    color: UM.Theme.getColor("text_inactive")
                    text: settings.migrationDiagnosticsText
                }
                Cura.SecondaryButton {
                    visible: settings.migrationBackupAvailable
                    text: "Show backup folder"
                    onClicked: settings.openMigrationBackupFolder()
                }
            }
            UM.Label {
                width: parent.width
                text: "Memory diagnostics sample every ten seconds and write to moonraker_leak.log in your home folder: the process size and physical footprint, growing QML item classes, growing plugin collections, and the camera stream's gauges. The separate Python-allocation trace is heavier and stalls Cura briefly each minute."
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }

            UM.Label {
                width: parent.width
                text: "Downloads and the G-code index cache keep the Improve-ETA flow fast on a second run. Clear them to watch a full download and index again. The cache size limit is per printer; the clear button wipes the cache for ALL printers."
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width
                UM.Label {
                    text: "Persistent cache size (MiB)"
                }
                Cura.TextField {
                    id: cacheMaxField
                    Layout.preferredWidth: 90
                    text: settings.settingsCacheMaxMb
                    maximumLength: 8
                    onTextChanged: base.validCacheMax = settings.validCacheMax(text)
                }
                UM.Label {
                    visible: !base.validCacheMax
                    text: "Cache size must be between 16 and 4096 MiB."
                    color: UM.Theme.getColor("error")
                    font: UM.Theme.getFont("default_italic")
                }
            }
            RowLayout {
                width: parent.width
                spacing: UM.Theme.getSize("default_margin").width
                Cura.SecondaryButton {
                    text: "Clear cached downloads and indexes"
                    onClicked: settings.clearCache()
                }
                UM.Label {
                    Layout.fillWidth: true
                    text: settings.cacheStatus
                    color: UM.Theme.getColor("text_inactive")
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
