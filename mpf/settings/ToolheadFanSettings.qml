import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

Item {
    id: root
    property var model: null
    property var preview: null
    property var draft: model ? model.rotorCandidate : ({})
    property string pendingFan: ""
    property string loadedDraft: ""
    property bool ready: false
    onDraftChanged: if (ready)
        loadDraft(false)
    Component.onCompleted: {
        ready = true;
        loadDraft(false);
    }
    function loadDraft(force) {
        const key = JSON.stringify(draft);
        if (!force && loadedDraft === key)
            return;
        loadedDraft = key;
        pendingFan = draft.fan || "";
        cx.text = draft.centre ? draft.centre[0].toPrecision(6) : "0";
        cy.text = draft.centre ? draft.centre[1].toPrecision(6) : "0";
        cz.text = draft.centre ? draft.centre[2].toPrecision(6) : "0";
        ax.text = draft.axis ? draft.axis[0].toPrecision(6) : "0";
        ay.text = draft.axis ? draft.axis[1].toPrecision(6) : "0";
        az.text = draft.axis ? draft.axis[2].toPrecision(6) : "1";
        rpm.text = draft.rpm !== undefined ? draft.rpm.toString() : "3000";
        direction.currentIndex = draft.direction === -1 ? 1 : 0;
        blur.checked = draft.blur !== false;
    }
    function save(previewOnly) {
        if (draft.body === undefined)
            return;
        const setter = previewOnly ? model.previewRotor : model.setRotor;
        setter({
            body: draft.body,
            centre: [Number(cx.text), Number(cy.text), Number(cz.text)],
            axis: [Number(ax.text), Number(ay.text), Number(az.text)],
            rpm: Number(rpm.text),
            direction: direction.currentIndex === 0 ? 1 : -1,
            fan: pendingFan,
            blur: blur.checked
        });
    }
    Flickable {
        id: scroll
        objectName: "toolheadFansScroll"
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: column.implicitHeight
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: UM.ScrollBar {
            id: scrollbar
            policy: ScrollBar.AlwaysOn
            implicitWidth: UM.Theme.getSize("scrollbar").width + leftPadding + rightPadding
        }
        ColumnLayout {
            id: column
            width: scroll.width - scrollbar.width - UM.Theme.getSize("thin_margin").width
            spacing: UM.Theme.getSize("thin_margin").height
            Cura.SecondaryButton {
                objectName: "toolheadPickRotor"
                Layout.fillWidth: true
                text: preview && preview.addingRotor ? "Cancel body picking" : "Pick rotating body"
                enabled: model && !model.busy
                onClicked: preview.addingRotor = !preview.addingRotor
            }
            Cura.ComboBox {
                objectName: "toolheadRotorBody"
                currentIndex: {
                    for (let i = 0; i < model.length; ++i)
                        if (model[i].body === root.draft.body)
                            return i;
                    return -1;
                }
                Layout.fillWidth: true
                model: root.model ? root.model.bodies : []
                textRole: "label"
                defaultTextOnEmptyModel: ""
                Accessible.name: "Toolhead body to animate"
                onActivated: index => {
                    root.model.pickedBody(model[index].body);
                    root.loadDraft(true);
                }
            }
            UM.Label {
                Layout.fillWidth: true
                text: "Confirm the cyan rotation axis in model coordinates (mm). Direction is viewed from Axis + toward the centre."
                wrapMode: Text.WordWrap
            }
            GridLayout {
                columns: 4
                Layout.fillWidth: true
                enabled: root.draft.body !== undefined && root.model && !root.model.busy
                UM.Label {
                    text: "Centre"
                }
                Cura.TextField {
                    id: cx
                    objectName: "toolheadRotorCentreX"
                    Layout.fillWidth: true
                    maximumLength: 16
                    text: "0"
                    Accessible.name: "Rotor centre X"
                }
                Cura.TextField {
                    id: cy
                    objectName: "toolheadRotorCentreY"
                    Layout.fillWidth: true
                    maximumLength: 16
                    text: "0"
                    Accessible.name: "Rotor centre Y"
                }
                Cura.TextField {
                    id: cz
                    objectName: "toolheadRotorCentreZ"
                    Layout.fillWidth: true
                    maximumLength: 16
                    text: "0"
                    Accessible.name: "Rotor centre Z"
                }
                UM.Label {
                    text: "Axis"
                }
                Cura.TextField {
                    id: ax
                    objectName: "toolheadRotorAxisX"
                    Layout.fillWidth: true
                    maximumLength: 16
                    text: "0"
                    Accessible.name: "Rotor axis X"
                }
                Cura.TextField {
                    id: ay
                    objectName: "toolheadRotorAxisY"
                    Layout.fillWidth: true
                    maximumLength: 16
                    text: "0"
                    Accessible.name: "Rotor axis Y"
                }
                Cura.TextField {
                    id: az
                    objectName: "toolheadRotorAxisZ"
                    Layout.fillWidth: true
                    maximumLength: 16
                    text: "1"
                    Accessible.name: "Rotor axis Z"
                }
            }
            Cura.ComboBox {
                id: fan
                objectName: "toolheadRotorFan"
                Layout.fillWidth: true
                model: {
                    const options = root.model ? Array.from(root.model.fanOptions) : [];
                    if (root.pendingFan && !options.some(row => row.fan === root.pendingFan))
                        options.push({
                            label: root.pendingFan + " (unavailable)",
                            fan: root.pendingFan
                        });
                    return options;
                }
                textRole: "label"
                defaultTextOnEmptyModel: ""
                Accessible.name: "Printer fan telemetry binding. Read only."
                currentIndex: {
                    for (let i = 0; i < model.length; ++i)
                        if (model[i].fan === root.pendingFan)
                            return i;
                    return -1;
                }
                onActivated: index => root.pendingFan = model[index].fan
            }
            RowLayout {
                Layout.fillWidth: true
                UM.Label {
                    text: fan.currentIndex === 0 ? "Visual RPM" : "RPM at 100%"
                }
                Cura.TextField {
                    id: rpm
                    objectName: "toolheadRotorRPM"
                    Layout.fillWidth: true
                    text: "3000"
                    maximumLength: 10
                    Accessible.name: "Visual fan RPM, zero to thirty thousand"
                }
            }
            Cura.ComboBox {
                id: direction
                objectName: "toolheadRotorDirection"
                Layout.fillWidth: true
                model: [
                    {
                        label: "Counterclockwise viewed from Axis +"
                    },
                    {
                        label: "Clockwise viewed from Axis +"
                    }
                ]
                textRole: "label"
                defaultTextOnEmptyModel: ""
                currentIndex: 0
                Accessible.name: "Visual rotation direction"
            }
            UM.CheckBox {
                id: blur
                objectName: "toolheadRotorBlur"
                text: "Blur fast rotation"
                checked: true
            }
            UM.CheckBox {
                objectName: "toolheadRotorSlowPreview"
                text: "Slow visual preview (60 RPM)"
                enabled: root.draft.body !== undefined && root.preview && root.preview.animationAvailable
                checked: root.preview ? root.preview.slowRotation : false
                onClicked: {
                    root.save(true);
                    root.preview.slowRotation = checked;
                }
            }
            UM.Label {
                Layout.fillWidth: true
                text: root.preview && root.preview.animationAvailable ? "Slow preview rotates the proposal at 60 RPM for inspection." : "Slow rotation preview requires a working OpenGL renderer. Axis and centre can still be configured."
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }
            UM.Label {
                Layout.fillWidth: true
                text: "Preview is a proposal. Confirm rotation to keep these fan settings before Done."
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
            }
            Cura.SecondaryButton {
                objectName: "toolheadUpdateRotorPreview"
                text: "Update preview"
                enabled: root.draft.body !== undefined && root.model && !root.model.busy
                onClicked: root.save(true)
            }
            UM.Label {
                objectName: "toolheadRotorValidation"
                Layout.fillWidth: true
                text: root.model ? root.model.status : ""
                textFormat: Text.PlainText
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Cura.SecondaryButton {
                    objectName: "toolheadConfirmRotor"
                    text: "Confirm rotation"
                    enabled: root.draft.body !== undefined && root.model && !root.model.busy
                    onClicked: root.save(false)
                }
                Cura.SecondaryButton {
                    text: "Remove"
                    enabled: root.draft.body !== undefined && root.model && !root.model.busy
                    onClicked: root.model.removeRotor(root.draft.body)
                }
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: UM.Theme.getColor("text_inactive")
                text: "Measured RPM wins, including zero. Otherwise power scales your visual RPM estimate. Missing or stale fan readings stop rotation. This only changes the model."
            }
            UM.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: root.model ? root.model.rotorReadout : ""
                textFormat: Text.PlainText
            }
        }
    }
}
