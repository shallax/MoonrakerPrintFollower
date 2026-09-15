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
    // The card grows with its content only up to a sensible ceiling;
    // beyond that the content scrolls — expanding the older sections
    // never makes the dialog taller than the window can take. The
    // height reads the laid-out children directly: the Flickable's
    // own implicit height is 0, and a popup sized from it collapses
    // to its padding (the empty-dialog failure).
    height: Math.min(titleText.implicitHeight + closeRow.implicitHeight + contentColumn.implicitHeight + contentArea.spacing * 2 + topPadding + bottomPadding, 560)
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
        id: contentArea
        spacing: 12

        // The frozen header: the title stays put while the sections
        // and the link scroll beneath it.
        Text {
            id: titleText
            // The plugin names itself explicitly: this popup is
            // the plugin's own, never to be read as Cura's.
            text: "What's new in Moonraker Print Follower " + popup.model.whatsNewContent[0].version
            width: parent.width
            color: UM.Theme.getColor("text")
            font.pixelSize: 17
            font.bold: true
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }

        Item {
            id: scrollArea
            width: parent.width
            height: parent.height - titleText.height - closeRow.height - parent.spacing * 2

            Flickable {
                id: flick
                anchors.fill: parent
                clip: true
                contentWidth: width
                contentHeight: contentColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {
                }

                Column {
                    id: contentColumn
                    // The gutter keeps the text clear of the
                    // scrollbar's overlay.
                    width: flick.width - 14
                    spacing: 12

                    // One entry per version: the latest renders its items open
                    // at the top; previous versions are pre-collapsed sections.
                    Repeater {
                        model: popup.model.whatsNewContent

                        delegate: Column {
                            id: entry
                            width: contentColumn.width
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

                                // The release's headline: what the
                                // version IS, in one or two
                                // sentences, above its bullets.
                                Text {
                                    width: parent.width
                                    text: modelData.headline
                                    font.bold: true
                                    wrapMode: Text.Wrap
                                    color: UM.Theme.getColor("text")
                                }

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

            // Scroll affordances, the file manager's idiom: small
            // chevrons centred over the list — an up arrow near the
            // top while more content is above, a down arrow near the
            // bottom while more content is below. They are SIBLINGS
            // of the Flickable, overlaying it: a Flickable's own
            // children scroll with the content, which hid them.
            UM.Label {
                text: "↑"
                visible: flick.height > 0 && flick.contentY > 2
                anchors.top: flick.top
                anchors.horizontalCenter: flick.horizontalCenter
                anchors.topMargin: 4 * screenScaleFactor
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("medium_bold")
            }
            UM.Label {
                text: "↓"
                visible: flick.height > 0 && flick.contentY < flick.contentHeight - flick.height - 2
                anchors.bottom: flick.bottom
                anchors.horizontalCenter: flick.horizontalCenter
                anchors.bottomMargin: 4 * screenScaleFactor
                color: UM.Theme.getColor("primary")
                font: UM.Theme.getFont("medium_bold")
            }
        }

        // The frozen footer: a Cura-blue Close at the bottom right
        // (the author's ruling — the X read too small and its button
        // bounds showed).
        Row {
            id: closeRow
            width: parent.width
            layoutDirection: Qt.RightToLeft
            Button {
                id: closeButton
                objectName: "whatsNewCloseButton"
                text: "Close"
                flat: true
                contentItem: Text {
                    text: closeButton.text
                    color: UM.Theme.getColor("primary")
                    font: UM.Theme.getFont("medium_bold")
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: {
                    popup.model.dismissWhatsNew();
                    popup.close();
                }
            }
        }
    }
}
