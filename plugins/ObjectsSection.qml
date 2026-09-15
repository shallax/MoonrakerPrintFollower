import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Objects section (4.3.0 extraction): the exclude-object rows
// out of the monitor as one property-driven component. The
// confirmation dialog stays with the host — the section requests
// it through a signal.
Column {
    id: root
    property var printerModel: null
    signal excludeRequested(string name)

    // NO-REFLOW RULE: the Objects list arrives
    // seconds INTO a print (Klipper reports the
    // slicer's EXCLUDE_OBJECT_DEFINE lines only
    // when they execute), so the section is
    // permanent — empty until then.
    Layout.fillWidth: true
    CollapsibleSectionHeader {
        width: parent.width
        printerModel: root.printerModel
        title: "Objects"
        sectionId: "objects"
        sectionIcon: "MeshTypeNormal"
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["objects"] !== false
        anchors.left: parent.left
        anchors.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        anchors.right: parent.right
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height / 2

        Repeater {
            model: root.printerModel != null ? root.printerModel.excludeObjectItems : []
            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width
                UM.Label {
                    text: modelData.name + (modelData.current ? "  · current" : "") + (modelData.excluded ? "  · excluded" : "")
                    color: modelData.excluded ? UM.Theme.getColor("text_inactive") : UM.Theme.getColor("text")
                    Layout.preferredWidth: 170 * screenScaleFactor
                    elide: Text.ElideRight
                }
                Cura.SecondaryButton {
                    // NO-REFLOW RULE: the button
                    // never disappears — it
                    // disables when the object
                    // cannot be excluded. The
                    // section-level denial joins
                    // the gate (4.2.0, the
                    // adversarial round's H2): a
                    // locked pane must not offer
                    // an action the policy
                    // refuses.
                    enabled: root.printerModel != null && root.printerModel.monitorConnected && !root.printerModel.actionBusy && root.printerModel.printActive && root.printerModel.sectionReason === "" && !modelData.excluded
                    text: "Exclude"
                    onClicked: root.excludeRequested(modelData.name)
                }
            }
        }
        UM.Label {
            // An empty list says so instead of
            // reading as a bug (the
            // live request): the objects arrive
            // when the slicer's EXCLUDE_OBJECT
            // lines execute — seconds into a
            // print.
            visible: root.printerModel != null && root.printerModel.excludeObjectItems.length === 0
            text: root.printerModel != null && root.printerModel.printActive ? "No objects yet — they appear as the print defines them." : "No objects — EXCLUDE_OBJECT data arrives while printing."
            color: UM.Theme.getColor("text_inactive")
            font: UM.Theme.getFont("small")
        }
    }
}
