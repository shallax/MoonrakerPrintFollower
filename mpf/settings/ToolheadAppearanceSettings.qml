import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import UM 1.7 as UM
import Cura 1.1 as Cura
import "../widgets"

Flickable {
    id: root
    objectName: "toolheadAppearanceScroll"
    property var model: null
    property var preview: null
    property var types: model ? model.materialTypes : []
    property var selected: types.length ? types[Math.max(0, Math.min(finish.currentIndex, types.length - 1))] : null
    contentWidth: width
    contentHeight: content.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: UM.ScrollBar {}
    ColumnLayout {
        id: content
        width: root.width - UM.Theme.getSize("scrollbar").width
        spacing: UM.Theme.getSize("default_margin").height
        UM.Label {
            text: "Selected parts"
        }
        Cura.ComboBox {
            objectName: "toolheadOpacityPickKind"
            Accessible.name: "Select bodies or CAD faces"
            Layout.fillWidth: true
            model: [
                {
                    kind: "body",
                    label: "Bodies"
                },
                {
                    kind: "face",
                    label: "Faces"
                }
            ]
            textRole: "label"
            defaultTextOnEmptyModel: ""
            currentIndex: root.model && root.model.opacityKind === "face" ? 1 : 0
            onActivated: index => root.model.selectOpacityKind(model[index].kind)
        }
        Cura.SecondaryButton {
            objectName: "toolheadSelectOpacity"
            Layout.fillWidth: true
            text: root.preview && root.preview.selectingOpacity ? "Finish selecting" : "Select bodies / faces"
            enabled: root.model && !root.model.busy
            onClicked: {
                if (root.preview.selectingOpacity)
                    root.preview.picking = false;
                else
                    root.preview.selectingOpacity = true;
            }
        }
        Cura.ComboBox {
            objectName: "toolheadOpacityBodyList"
            Accessible.name: "Add or remove a body from the selection, including invisible bodies"
            Layout.fillWidth: true
            model: [
                {
                    body: -1,
                    label: "Select a body by name…"
                }
            ].concat(root.model ? root.model.bodies : [])
            textRole: "label"
            defaultTextOnEmptyModel: ""
            onActivated: index => {
                if (model[index].body >= 0) {
                    root.model.selectOpacityKind("body");
                    root.model.toggleOpacitySelection(model[index].body);
                    root.preview.selectingOpacity = true;
                }
                currentIndex = 0;
            }
        }
        UM.Label {
            Layout.fillWidth: true
            text: root.model && root.model.opacitySelectionCount ? root.model.opacityBodies.length + " bodies · " + root.model.opacityFaces.length + " faces · " + (root.model.selectedOpacity >= 0 ? Math.round(root.model.selectedOpacity * 100) + "% opacity" : "Mixed opacity") : "Select bodies or faces"
        }
        Cura.ComboBox {
            objectName: "toolheadSelectedMaterial"
            Accessible.name: "Material type for selected bodies and faces"
            Layout.fillWidth: true
            model: [
                {
                    kind: "mixed",
                    label: "Mixed material"
                },
                {
                    kind: "automatic",
                    label: "Automatic CAD material"
                }
            ].concat(root.types)
            textRole: "label"
            defaultTextOnEmptyModel: ""
            currentIndex: Math.max(0, model.findIndex(row => row.kind === (root.model ? root.model.selectedMaterial : "mixed")))
            enabled: root.model && !root.model.busy && root.model.opacitySelectionCount > 0
            onActivated: index => root.model.setSelectedMaterial(model[index].kind)
        }
        UM.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Material · " + (root.model ? root.model.selectedSources.material : "Automatic")
        }
        Repeater {
            model: [
                {
                    field: "roughness",
                    propertyName: "selectedRoughness",
                    label: "Selected roughness"
                },
                {
                    field: "reflectivity",
                    propertyName: "selectedReflectivity",
                    label: "Selected reflectivity"
                }
            ]
            delegate: ColumnLayout {
                id: targetFinish
                required property var modelData
                property real resolvedValue: root.model ? root.model[modelData.propertyName] : -1
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    UM.Label {
                        objectName: "toolheadSelectedLabel" + modelData.field
                        text: modelData.label + " · " + (targetFinish.resolvedValue >= 0 ? Math.round(targetFinish.resolvedValue * 100) + "%" : "Mixed") + " · " + (root.model ? root.model.selectedSources[modelData.field] : "")
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        wrapMode: Text.WordWrap
                    }
                    Cura.SecondaryButton {
                        objectName: "toolheadSelectedAuto" + modelData.field
                        text: "Automatic"
                        enabled: root.model && !root.model.busy && root.model.opacitySelectionCount > 0
                        onClicked: root.model.resetSelectedFinish(modelData.field)
                    }
                }
                OutlineSlider {
                    objectName: "toolheadSelected" + modelData.field
                    Accessible.name: modelData.label + " for selected bodies and faces"
                    Layout.fillWidth: true
                    from: 0
                    to: 100
                    stepSize: 1
                    value: targetFinish.resolvedValue >= 0 ? targetFinish.resolvedValue * 100 : 50
                    enabled: root.model && !root.model.busy && root.model.opacitySelectionCount > 0
                    live: false
                    onValueCommitted: value => root.model.setSelectedFinish(modelData.field, value / 100)
                }
            }
        }
        UM.Label {
            text: "Selected opacity · " + (root.model ? root.model.selectedSources.opacity : "")
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        OutlineSlider {
            objectName: "toolheadSelectedOpacity"
            Accessible.name: "Selected opacity. Zero invisible, one hundred opaque."
            Layout.fillWidth: true
            from: 0
            to: 100
            stepSize: 1
            value: root.model && root.model.selectedOpacity >= 0 ? root.model.selectedOpacity * 100 : 50
            enabled: root.model && !root.model.busy && root.model.opacitySelectionCount > 0
            live: false
            onValueCommitted: value => root.model.setSelectedOpacity(value / 100)
        }
        UM.Label {
            Layout.fillWidth: true
            text: "0% invisible · 100% opaque. Click to add or remove bodies or faces; mixed selections stay selected. Cyan marks the selection. Invisible geometry is shown faintly only while selecting."
            wrapMode: Text.WordWrap
            color: UM.Theme.getColor("text_inactive")
        }
        Cura.SecondaryButton {
            objectName: "toolheadResetOpacity"
            Layout.fillWidth: true
            text: "Restore imported transparency"
            enabled: root.model && !root.model.busy && root.model.opacitySelectionCount > 0
            onClicked: root.model.resetSelectedOpacity()
        }
        Cura.SecondaryButton {
            objectName: "toolheadClearOpacitySelection"
            Layout.fillWidth: true
            text: "Clear selection"
            enabled: root.model && root.model.opacitySelectionCount > 0
            onClicked: root.model.clearOpacitySelection()
        }
        UM.Label {
            Layout.fillWidth: true
            textFormat: Text.PlainText
            text: root.model ? root.model.status : ""
            wrapMode: Text.WordWrap
        }
        UM.Label {
            text: "Surface detail"
        }
        OutlineSlider {
            objectName: "toolheadSurfaceDetail"
            Accessible.name: "Plastic and composite surface detail strength. Zero is smooth."
            Layout.fillWidth: true
            from: 0
            to: 100
            stepSize: 1
            value: (root.model ? root.model.surfaceDetail : 0.35) * 100
            enabled: root.model && !root.model.busy
            live: false
            onValueTuning: value => root.model.previewSurfaceDetail(value / 100)
            onValueCommitted: value => root.model.setSurfaceDetail(value / 100)
        }
        UM.Label {
            Layout.fillWidth: true
            text: "Model-anchored grain on plastics and fibre composites. Glass and metal stay smooth."
            wrapMode: Text.WordWrap
            color: UM.Theme.getColor("text_inactive")
        }
        UM.Label {
            text: "Profile defaults"
        }
        Cura.ComboBox {
            id: finish
            objectName: "toolheadMaterialType"
            Accessible.name: "Material profile to adjust"
            Layout.fillWidth: true
            model: root.types
            textRole: "label"
            defaultTextOnEmptyModel: ""
        }
        Repeater {
            model: [
                {
                    field: "roughness",
                    label: "Roughness"
                },
                {
                    field: "reflectivity",
                    label: "Reflectivity"
                }
            ]
            delegate: ColumnLayout {
                required property var modelData
                property bool automatic: root.selected ? root.selected[modelData.field + "Auto"] : true
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    UM.Label {
                        text: modelData.label
                        Layout.fillWidth: true
                    }
                    UM.CheckBox {
                        objectName: "toolheadMaterialAuto" + modelData.field
                        text: "Automatic"
                        checked: automatic
                        enabled: root.model && !root.model.busy && root.selected
                        onClicked: {
                            if (checked)
                                root.model.resetMaterialFinish(root.selected.kind, modelData.field);
                            else
                                root.model.setMaterialFinish(root.selected.kind, modelData.field, root.selected[modelData.field]);
                        }
                    }
                }
                OutlineSlider {
                    objectName: "toolheadMaterial" + modelData.field
                    Accessible.name: modelData.label + " for selected material. Zero to one hundred percent."
                    Layout.fillWidth: true
                    from: 0
                    to: 100
                    stepSize: 1
                    value: root.selected ? root.selected[modelData.field] * 100 : 0
                    enabled: root.model && !root.model.busy && root.selected
                    live: false
                    onValueCommitted: value => root.model.setMaterialFinish(root.selected.kind, modelData.field, value / 100)
                }
            }
        }
        UM.Label {
            Layout.fillWidth: true
            text: "Defaults are appearance estimates. Automatic retains the STEP finish; an adjustment applies to this profile only. Reflection strength and sharpness are independent."
            wrapMode: Text.WordWrap
            color: UM.Theme.getColor("text_inactive")
        }
        UM.Label {
            text: "Material brush"
        }
        Cura.ComboBox {
            objectName: "toolheadMaterialBrush"
            Accessible.name: "Material to assign to a clicked body or face"
            Layout.fillWidth: true
            model: [
                {
                    kind: "automatic",
                    label: "Restore STEP classification"
                }
            ].concat(root.types)
            textRole: "label"
            defaultTextOnEmptyModel: ""
            currentIndex: Math.max(0, model.findIndex(row => row.kind === (root.model ? root.model.paintKind : "automatic")))
            onActivated: index => root.model.selectMaterialPaint(model[index].kind)
        }
        Cura.SecondaryButton {
            objectName: "toolheadPaintMaterials"
            Layout.fillWidth: true
            text: root.preview && root.preview.paintingMaterial ? "Finish painting" : "Paint materials"
            enabled: root.model && !root.model.busy
            onClicked: {
                if (root.preview.paintingMaterial)
                    root.preview.picking = false;
                else
                    root.preview.paintingMaterial = true;
            }
        }
        UM.Label {
            Layout.fillWidth: true
            text: "In paint mode: white plastics, blue glass, gold metal, dark grey composites, grey unidentified. All classified faces are highlighted; original CAD colours return on exit."
            wrapMode: Text.WordWrap
            color: UM.Theme.getColor("text_inactive")
        }
        Cura.SecondaryButton {
            objectName: "toolheadResetPaintMaterials"
            text: "Restore all material assignments"
            Layout.fillWidth: true
            enabled: root.model && !root.model.busy && root.model.paintedFaceCount + root.model.paintedBodyCount > 0
            onClicked: root.model.clearMaterialPaint()
        }
        UM.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: (root.model ? root.model.paintedBodyCount : 0) + " bodies · " + (root.model ? root.model.paintedFaceCount : 0) + " faces manually classified"
            color: UM.Theme.getColor("text_inactive")
        }
        UM.Label {
            text: "CAD material evidence"
        }
        Repeater {
            model: root.model ? root.model.materials : []
            delegate: UM.Label {
                required property var modelData
                Layout.fillWidth: true
                textFormat: Text.PlainText
                text: modelData.name + " · " + modelData.kind + "\n" + (modelData.source === "step-material" ? "Authored STEP material" : modelData.source === "step-name" ? "Inferred from part name" : "No material evidence")
                wrapMode: Text.WordWrap
            }
        }
        UM.Label {
            Layout.fillWidth: true
            text: "Classification follows explicit STEP materials, then clear part names. Transparency and original colours are always retained. Individual transparency edits stay separate."
            wrapMode: Text.WordWrap
            color: UM.Theme.getColor("text_inactive")
        }
    }
}
