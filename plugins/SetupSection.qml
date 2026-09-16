import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Setup section (4.3.0 extraction): the header and its content
// moved out of the dashboard as one property-driven component.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Setup"
        sectionId: "setup"
        sectionIcon: "House"
    }

    ColumnLayout {
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["setup"] !== false
        enabled: root.printerModel == null || (!root.printerModel.controlsLocked && root.printerModel.monitorConnected)
        spacing: UM.Theme.getSize("default_margin").height

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2
            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Home"
                enabled: root.printerModel != null && root.printerModel.canRunSetup
                onClicked: root.printerModel.homeAll()
            }
            Cura.SecondaryButton {
                // Capability-static gate (the UX panel's ruling): QGL
                // support never changes mid-session — only on a
                // printer switch, which is user-initiated — so the
                // button may stay visibility-gated instead of reading
                // as permanently broken on machines without quad
                // gantry.
                Layout.fillWidth: true
                visible: root.printerModel != null && root.printerModel.hasQuadGantryLevel
                text: "QGL"
                tooltip: "Level the quad gantry."
                enabled: root.printerModel != null && root.printerModel.canRunSetup
                onClicked: root.printerModel.runQuadGantryLevel()
            }
            Cura.SecondaryButton {
                Layout.fillWidth: true
                visible: root.printerModel != null && root.printerModel.hasBedMesh
                text: "Calibrate mesh"
                tooltip: "Probe the bed now and replace the active mesh with a newly calibrated one."
                enabled: root.printerModel != null && root.printerModel.canRunSetup
                onClicked: root.printerModel.calibrateBedMesh()
            }
        }

        RowLayout {
            // Capability-static gate (see the QGL button): visibility,
            // not enabled.
            Layout.fillWidth: true
            visible: root.printerModel != null && root.printerModel.hasBedMesh
            spacing: UM.Theme.getSize("default_margin").width / 2

            Cura.ComboBox {
                id: bedMeshProfileSelector
                Layout.fillWidth: true
                model: root.printerModel != null ? root.printerModel.bedMeshProfileNames : []
                enabled: root.printerModel != null && root.printerModel.canRunSetup && root.printerModel.bedMeshProfileNames.length > 0
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: UM.Theme.getSize("default_margin").width / 2

            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Load saved mesh"
                enabled: root.printerModel != null && root.printerModel.canRunSetup && bedMeshProfileSelector.currentText.length > 0
                tooltip: "Load the selected saved Klipper bed mesh without probing the bed again."
                onClicked: root.printerModel.loadBedMeshProfile(bedMeshProfileSelector.currentText)
            }

            Cura.SecondaryButton {
                Layout.fillWidth: true
                text: "Clear mesh"
                enabled: root.printerModel != null && root.printerModel.canRunSetup && root.printerModel.bedMeshAvailable
                tooltip: "Clear the active Klipper bed mesh and remove its Z adjustment."
                onClicked: root.printerModel.clearBedMesh()
            }
        }

        GridLayout {
            columns: 2
            columnSpacing: UM.Theme.getSize("default_margin").width
            rowSpacing: UM.Theme.getSize("default_margin").height / 2
            Layout.fillWidth: true

            UM.Label {
                text: "Status"
                color: UM.Theme.getColor("text_inactive")
                Layout.preferredWidth: 110 * screenScaleFactor
            }
            UM.Label {
                // One labelled row instead of two always-present blank
                // slots (the author's "white space at the bottom of
                // Setup" report). The emdash is the honest empty
                // value.
                height: 36 * screenScaleFactor
                // The section-level denial (4.2.0): a locked or dead
                // pane says why FIRST — the per-section text is the
                // fallback.
                text: root.printerModel != null ? (root.printerModel.sectionReason !== "" ? root.printerModel.sectionReason : (root.printerModel.printActive ? "Setup disabled during a print" : (root.printerModel.hasBedMesh && root.printerModel.bedMeshProfileNames.length === 0 ? "No saved bed mesh profiles" : "—"))) : "—"
                color: UM.Theme.getColor("text")
                Layout.fillWidth: true
                elide: Text.ElideRight
                wrapMode: Text.NoWrap
                UM.TooltipArea {
                    anchors.fill: parent
                    text: root.printerModel != null ? (root.printerModel.sectionReasonDetail !== "" ? root.printerModel.sectionReasonDetail : (root.printerModel.printActive ? "Homing and bed-mesh setup controls are disabled during a print." : (root.printerModel.hasBedMesh && root.printerModel.bedMeshProfileNames.length === 0 ? "No saved bed mesh profiles reported by Klipper." : ""))) : ""
                    acceptedButtons: Qt.NoButton
                }
            }
        }
    }
}
