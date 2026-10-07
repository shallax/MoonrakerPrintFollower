import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import QtQuick.Window 2.15
import UM 1.5 as UM

// A value-fed presentation with typed intents bound by its selected owner.
// No printer model, command owner or network object is reachable from this pane.
Item {
    id: root
    objectName: "previewToolheadPane"
    property bool collapsed: false
    property bool connected: false
    property bool jogAllowed: false
    property bool homeAllowed: false
    property bool offsetAllowed: false
    property bool moveToAllowed: false
    property string positionX: "—"
    property string positionY: "—"
    property string positionZ: "—"
    property string offsetText: "—"
    property string statusText: "No printer selected"
    property real jogDistance: 25
    property real offsetStep: 0.01
    property var actionRows: []
    property real availableHeight: 620 * screenScaleFactor
    readonly property real scale: screenScaleFactor
    width: (collapsed ? 38 : 304) * scale
    height: collapsed ? 112 * scale : Math.max(84 * scale, Math.min(availableHeight, body.implicitHeight + 40 * scale + statusStrip.height))
    signal collapseRequested(bool collapsed)
    signal jogDistanceRequested(real distance)
    signal jogRequested(string axis, int direction)
    signal homeRequested(string axis)
    signal moveToRequested(string x, string y, string z)
    signal offsetRequested(real amount)
    signal actionRequested(string key)

    onConnectedChanged: {
        if (!connected)
            resetDrafts();
    }
    onVisibleChanged: {
        if (!visible)
            actions.close();
    }

    function resetDrafts() {
        distancePicker.cancelDraft();
        var focused = root.Window.window ? root.Window.window.activeFocusItem : null;
        while (focused && focused !== root)
            focused = focused.parent;
        var retainedFocus = retireControlFocus(root);
        if (root.visible && (focused === root || retainedFocus))
            (root.collapsed ? reopen : collapseButton).forceActiveFocus(Qt.OtherFocusReason);
        targetX.text = "";
        targetY.text = "";
        targetZ.text = "";
        actions.close();
        scroller.contentY = 0;
    }

    function retireControlFocus(item) {
        // Disabled controls can retain focus without being activeFocusItem.
        // Clear our named controls, preserving native text-field internals.
        var retained = false;
        for (var i = 0; i < item.children.length; i++)
            retained = retireControlFocus(item.children[i]) || retained;
        if (item !== root && item.focus && (item.objectName.indexOf("previewToolhead") === 0 || item.objectName.indexOf("previewJogDistance") === 0)) {
            item.focus = false;
            retained = true;
        }
        return retained;
    }

    function revealControl(control) {
        if (root.collapsed || !control || scroller.height <= 0)
            return;
        var ancestor = control;
        while (ancestor && ancestor !== body)
            ancestor = ancestor.parent;
        if (ancestor !== body)
            return;
        var top = control.mapToItem(body, 0, 0).y;
        var bottom = top + control.height;
        var next = scroller.contentY;
        if (top < next)
            next = top - 4 * root.scale;
        else if (bottom > next + scroller.height)
            next = bottom - scroller.height + 4 * root.scale;
        scroller.contentY = Math.max(0, Math.min(next, scroller.contentHeight - scroller.height));
    }

    Connections {
        target: root.Window.window
        function onActiveFocusItemChanged() {
            Qt.callLater(function () {
                if (root.Window.window)
                    root.revealControl(root.Window.window.activeFocusItem);
            });
        }
    }

    onCollapsedChanged: {
        if (collapsed)
            actions.close();
        Qt.callLater(function () {
            if (!root)
                return;
            if (root.collapsed)
                reopen.forceActiveFocus();
            else
                collapseButton.forceActiveFocus();
        });
    }
    Rectangle {
        anchors.fill: parent
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        radius: 4 * root.scale
    }
    PreviewToolheadButton {
        id: reopen
        objectName: "previewToolheadReopen"
        anchors.fill: parent
        visible: root.collapsed
        text: ""
        Accessible.name: "Expand Toolhead controls"
        onClicked: root.collapseRequested(false)
        UM.ColorImage {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: 6 * root.scale
            width: 18 * root.scale
            height: width
            source: UM.Theme.getIcon("Nozzle")
            color: UM.Theme.getColor("text")
        }
        UM.ColorImage {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 6 * root.scale
            width: 16 * root.scale
            height: width
            source: UM.Theme.getIcon("ChevronSingleRight")
            color: UM.Theme.getColor("text")
        }
        Item {
            y: 28 * root.scale
            width: parent.width
            height: parent.height - y - 26 * root.scale
            UM.Label {
                anchors.centerIn: parent
                rotation: -90
                text: "Toolhead"
            }
        }
    }
    PreviewPanelHeader {
        id: collapseButton
        visible: !root.collapsed
        width: parent.width
        text: "Toolhead · mm"
        iconName: "Nozzle"
        indicatorName: "ChevronSingleLeft"
        titleRightInset: 30 * root.scale
        objectName: "previewToolheadCollapse"
        Accessible.name: "Collapse Toolhead controls to the left"
        onClicked: root.collapseRequested(true)
        PreviewToolheadButton {
            id: menuButton
            objectName: "previewToolheadMenu"
            anchors.right: parent.right
            anchors.rightMargin: 36 * root.scale
            anchors.verticalCenter: parent.verticalCenter
            width: 30 * root.scale
            height: 30 * root.scale
            text: "⋮"
            enabled: root.actionRows.length > 0
            Accessible.name: "Toolhead actions"
            onClicked: actions.open()
        }
    }
    Flickable {
        id: scroller
        objectName: "previewToolheadScroll"
        visible: !root.collapsed
        x: root.scale
        y: 40 * root.scale
        width: parent.width - 2 * root.scale
        height: Math.max(0, parent.height - y - statusStrip.height - root.scale)
        contentWidth: width
        contentHeight: body.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {
            policy: scroller.contentHeight > scroller.height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        }
        Column {
            id: body
            x: 12 * root.scale
            width: scroller.width - 24 * root.scale
            spacing: 10 * root.scale
            topPadding: 10 * root.scale
            bottomPadding: 10 * root.scale
            UM.Label {
                text: "Physical position"
            }
            Row {
                width: parent.width
                Repeater {
                    model: [
                        {
                            axis: "X",
                            value: root.positionX
                        },
                        {
                            axis: "Y",
                            value: root.positionY
                        },
                        {
                            axis: "Z",
                            value: root.positionZ
                        }
                    ]
                    UM.Label {
                        width: parent.width / 3
                        text: modelData.axis + "  " + modelData.value
                        font: UM.Theme.getFont("medium_bold")
                        elide: Text.ElideRight
                    }
                }
            }
            Column {
                width: parent.width
                spacing: 4 * root.scale
                RowLayout {
                    width: parent.width
                    UM.Label {
                        text: "Move to"
                        Layout.fillWidth: true
                    }
                    UM.Label {
                        text: "G-code · absolute"
                        font: UM.Theme.getFont("small")
                    }
                }
                RowLayout {
                    width: parent.width
                    spacing: 5 * root.scale
                    PreviewToolheadTarget {
                        id: targetX
                        objectName: "previewToolheadTargetX"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        axis: "X"
                        enabled: root.moveToAllowed
                    }
                    PreviewToolheadTarget {
                        id: targetY
                        objectName: "previewToolheadTargetY"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        axis: "Y"
                        enabled: root.moveToAllowed
                    }
                    PreviewToolheadTarget {
                        id: targetZ
                        objectName: "previewToolheadTargetZ"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        axis: "Z"
                        enabled: root.moveToAllowed
                    }
                    PreviewToolheadButton {
                        objectName: "previewToolheadMoveTo"
                        Layout.preferredWidth: 64 * root.scale
                        Layout.alignment: Qt.AlignBottom
                        height: 30 * root.scale
                        text: "Move to"
                        enabled: root.moveToAllowed
                        onClicked: root.moveToRequested(targetX.text, targetY.text, targetZ.text)
                    }
                }
            }
            Item {
                width: parent.width
                height: 134 * root.scale
                Repeater {
                    model: [
                        {
                            axis: "y",
                            direction: 1,
                            glyph: "↑",
                            col: 1,
                            row: 0
                        },
                        {
                            axis: "x",
                            direction: -1,
                            glyph: "←",
                            col: 0,
                            row: 1
                        },
                        {
                            axis: "x",
                            direction: 1,
                            glyph: "→",
                            col: 2,
                            row: 1
                        },
                        {
                            axis: "y",
                            direction: -1,
                            glyph: "↓",
                            col: 1,
                            row: 2
                        },
                        {
                            axis: "z",
                            direction: 1,
                            glyph: "↑",
                            col: 3.5,
                            row: 0
                        },
                        {
                            axis: "z",
                            direction: -1,
                            glyph: "↓",
                            col: 3.5,
                            row: 2
                        }
                    ]
                    PreviewToolheadButton {
                        objectName: "previewToolheadJog" + modelData.axis.toUpperCase() + (modelData.direction > 0 ? "Plus" : "Minus")
                        x: 21 * root.scale + modelData.col * 48 * root.scale
                        y: modelData.row * 46 * root.scale
                        width: 44 * root.scale
                        height: 42 * root.scale
                        text: modelData.glyph + "\n" + modelData.axis.toUpperCase() + (modelData.direction > 0 ? "+" : "−")
                        enabled: root.jogAllowed
                        Accessible.name: "Jog " + modelData.axis.toUpperCase() + (modelData.direction > 0 ? " positive" : " negative")
                        onClicked: root.jogRequested(modelData.axis, modelData.direction)
                    }
                }
                PreviewToolheadButton {
                    objectName: "previewToolheadHomeAll"
                    x: 69 * root.scale
                    y: 46 * root.scale
                    width: 44 * root.scale
                    height: 42 * root.scale
                    text: "⌂\nAll"
                    enabled: root.homeAllowed
                    Accessible.name: "Home all axes"
                    onClicked: root.homeRequested("")
                }
                UM.Label {
                    x: 189 * root.scale
                    y: 46 * root.scale
                    width: 44 * root.scale
                    height: 42 * root.scale
                    text: "Z"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }
            PreviewJogDistance {
                id: distancePicker
                width: parent.width
                distance: root.jogDistance
                enabled: root.connected
                onDistanceRequested: function (distance) {
                    root.jogDistanceRequested(distance);
                }
            }
            RowLayout {
                width: parent.width
                spacing: 6 * root.scale
                UM.Label {
                    text: "Home"
                }
                Repeater {
                    model: ["X", "Y", "Z"]
                    PreviewToolheadButton {
                        objectName: "previewToolheadHome" + modelData
                        Layout.fillWidth: true
                        height: 30 * root.scale
                        text: "⌂ " + modelData
                        enabled: root.homeAllowed
                        Accessible.name: "Home " + modelData
                        onClicked: root.homeRequested(modelData.toLowerCase())
                    }
                }
            }
            Rectangle {
                width: parent.width
                height: root.scale
                color: UM.Theme.getColor("lining")
            }
            RowLayout {
                width: parent.width
                UM.Label {
                    text: "Z-offset"
                    Layout.fillWidth: true
                }
                UM.Label {
                    text: root.offsetText
                }
            }
            RowLayout {
                width: parent.width
                spacing: 5 * root.scale
                PreviewToolheadButton {
                    objectName: "previewToolheadOffsetDown"
                    Layout.preferredWidth: 34 * root.scale
                    height: 32 * root.scale
                    text: "↓"
                    enabled: root.offsetAllowed
                    Accessible.name: "Lower nozzle by selected Z offset increment"
                    onClicked: root.offsetRequested(-root.offsetStep)
                }
                Repeater {
                    model: [0.005, 0.01, 0.05]
                    PreviewToolheadButton {
                        objectName: "previewToolheadOffsetStep" + index
                        Layout.fillWidth: true
                        height: 32 * root.scale
                        text: String(modelData)
                        checkable: true
                        checked: modelData === root.offsetStep
                        autoExclusive: true
                        Accessible.name: modelData + " millimetre Z offset increment"
                        Accessible.checkable: true
                        Accessible.checked: checked
                        textColor: modelData === root.offsetStep ? UM.Theme.getColor("primary") : UM.Theme.getColor("text")
                        enabled: root.connected
                        onClicked: root.offsetStep = modelData
                    }
                }
                PreviewToolheadButton {
                    objectName: "previewToolheadOffsetUp"
                    Layout.preferredWidth: 34 * root.scale
                    height: 32 * root.scale
                    text: "↑"
                    enabled: root.offsetAllowed
                    Accessible.name: "Raise nozzle by selected Z offset increment"
                    onClicked: root.offsetRequested(root.offsetStep)
                }
            }
        }
    }
    Item {
        id: statusStrip
        visible: !root.collapsed
        anchors.bottom: parent.bottom
        width: parent.width
        height: Math.max(30 * root.scale, statusLabel.implicitHeight + 16 * root.scale)
        Rectangle {
            width: parent.width
            height: root.scale
            color: UM.Theme.getColor("lining")
        }
        UM.Label {
            id: statusLabel
            objectName: "previewToolheadStatus"
            x: 12 * root.scale
            y: 8 * root.scale
            width: parent.width - 24 * root.scale
            text: root.statusText
            font: UM.Theme.getFont("small")
            wrapMode: Text.WordWrap
        }
    }
    Menu {
        id: actions
        objectName: "previewToolheadActions"
        x: root.width - width - 6 * root.scale
        y: 40 * root.scale
        width: 230 * root.scale
        background: Rectangle {
            color: UM.Theme.getColor("main_background")
            border.color: UM.Theme.getColor("lining")
            radius: 3 * root.scale
        }
        Repeater {
            model: root.actionRows
            MenuItem {
                text: modelData.label
                enabled: modelData.allowed === true
                onTriggered: root.actionRequested(modelData.key)
                contentItem: UM.Label {
                    text: modelData.label
                    color: enabled ? UM.Theme.getColor("text") : UM.Theme.getColor("action_button_disabled_text")
                    elide: Text.ElideRight
                }
            }
        }
    }
}
