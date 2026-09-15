import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM

// The once-per-version what's-new overlay. A Popup root, created
// with Cura's own main window as the parent: it opens in Cura's
// overlay layer — centered on the window, styled with Cura's theme
// (UM.Theme, like every other dialog here), and Esc / a press
// outside the card / the Close button all dismiss it. Every dismiss
// path lands in the model's dismissWhatsNew(), which records the
// seen version so the overlay stays gone until the next release.
Popup {
    id: popup
    required property var model

    modal: true
    dim: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    // No anchors: a Popup is not an Item. The extension centers it
    // on the main window before opening.
    width: 520
    leftPadding: 24
    rightPadding: 24
    topPadding: 18
    bottomPadding: 18
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
    }
    onClosed: popup.model.dismissWhatsNew()

    contentItem: Column {
        spacing: 12

        Row {
            width: parent.width
            spacing: 12

            Text {
                text: "What's new in " + popup.model.whatsNewContent[0].version
                color: UM.Theme.getColor("text")
                font.pixelSize: 17
                font.bold: true
                elide: Text.ElideRight
                width: parent.width - closeButton.width - parent.spacing
                verticalAlignment: Text.AlignVCenter
            }

            Button {
                id: closeButton
                objectName: "whatsNewCloseButton"
                text: "Close"
                onClicked: {
                    popup.model.dismissWhatsNew();
                    popup.close();
                }
            }
        }

        // One entry per version: the latest renders its items open
        // at the top; previous versions are pre-collapsed sections.
        Repeater {
            model: popup.model.whatsNewContent

            delegate: Column {
                id: entry
                width: popup.availableWidth
                spacing: 4
                property bool open: modelData.isLatest

                Button {
                    id: header
                    objectName: "whatsNewSection_" + modelData.version
                    visible: !modelData.isLatest
                    width: parent.width
                    text: "Version " + modelData.version + (entry.open ? " ▾" : " ▸")
                    flat: true
                    contentItem: Text {
                        text: header.text
                        font.bold: true
                        color: UM.Theme.getColor("text")
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: entry.open = !entry.open
                }

                Column {
                    visible: modelData.isLatest || entry.open
                    width: parent.width
                    spacing: 6

                    Repeater {
                        model: modelData.items
                        delegate: Row {
                            width: entry.width
                            spacing: 8
                            Rectangle {
                                y: 7
                                width: 5
                                height: 5
                                radius: 2.5
                                color: UM.Theme.getColor("primary")
                            }
                            Text {
                                width: entry.width - 13
                                text: modelData
                                wrapMode: Text.Wrap
                                color: UM.Theme.getColor("text")
                            }
                        }
                    }
                }
            }
        }

        // The further-info affordance: the project's home, opened in
        // the system browser (Qt's own behaviour — the harness proves
        // the click lands, never the browser).
        Text {
            id: repoLink
            objectName: "whatsNewRepoLink"
            text: "Project: github.com/shallax/MoonrakerPrintFollower"
            color: UM.Theme.getColor("primary")
            font.underline: true
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: Qt.openUrlExternally("https://github.com/shallax/MoonrakerPrintFollower")
            }
        }
    }
}
