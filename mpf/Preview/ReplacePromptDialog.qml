import QtQuick 2.15
import QtQuick.Controls 2.15
import UM 1.5 as UM
import Cura 1.0 as Cura
import "../Widgets"

// The replace confirmation, as a popup in the card rather than a QWidget
// QMessageBox: the box-shaped modal was a native alert on macOS whose
// nested loop could be left running under a hidden dialog, so the side
// doing the clicking saw a correct answer while the plugin never
// returned from the question and the load never ran. A rendered popup
// is answered by a real click like every other control the scenarios
// drive, and it cannot park the application in a loop it cannot leave.
//
// It holds no state of its own: the card's replacePromptVisible is the
// single owner, written by the coordinator, so the dialog cannot
// disagree with the model about whether the question is up. The two
// answers leave as signals for the card to record.
Popup {
    id: root
    signal replaceConfirmed
    signal replaceCancelled
    // Centred on the SCREEN, not on the card: the question is about
    // everything Cura holds, not about the card, and a corner card
    // is a strange place to ask it from. The window's overlay is
    // where the box this replaced put itself, so this is the
    // placement users had before. The model still decides WHICH
    // card raises it, so re-parenting cannot put up two.
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    padding: UM.Theme.getSize("default_margin").width
    modal: true
    closePolicy: Popup.NoAutoClose
    focus: false
    // Written by updateReplacePrompt, never bound: see updateCardGate.
    onOpened: replacePromptFocus.forceActiveFocus()
    background: Rectangle {
        color: UM.Theme.getColor("main_background")
        border.color: UM.Theme.getColor("lining")
        border.width: UM.Theme.getSize("default_lining").width
        radius: UM.Theme.getSize("default_radius").width
    }
    contentItem: Column {
        id: replacePromptFocus
        // The prompt's addressable surface is the popup's own
        // CONTENT, not the Popup object: a Popup is not an Item and
        // never appears in the visual tree a walk can reach, so an
        // objectName on it is a surface nothing can find (measured:
        // the macOS leg's wait for it timed out while the buttons
        // inside it answered).
        objectName: "moonrakerReplacePrompt"
        focus: true
        // The keyboard answers, and they TELL the model: the
        // popup's own close would leave the model saying the
        // question is still up, and updateReplacePrompt would put
        // it straight back. Escape cancels (the same answer as the
        // Cancel button); Return and the keypad's Enter accept.
        // Keys bubble UP the focus chain, so these hold while a
        // button inside has focus too.
        Keys.onEscapePressed: root.replaceCancelled()
        Keys.onReturnPressed: root.replaceConfirmed()
        Keys.onEnterPressed: root.replaceConfirmed()
        spacing: UM.Theme.getSize("narrow_margin").height
        // Twice the delete dialog's width: this prompt carries a
        // full sentence about what the replace discards, and at the
        // narrow width it wrapped to three lines.
        width: 640 * screenScaleFactor
        UM.Label {
            text: "Replace Cura contents?"
            font: UM.Theme.getFont("large_bold")
        }
        UM.Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: "This will discard everything currently loaded in Cura and replace it with the G-code currently printing in Moonraker."
            font: UM.Theme.getFont("medium")
        }
        // The verbs sit together at the bottom right, at equal
        // width — the placement Cura's own dialogs use. Stretched
        // across a 640px dialog they read as a toolbar, and the
        // dialog's width would then be decided by two buttons.
        Item {
            width: parent.width
            height: replaceButtons.height
            Row {
                id: replaceButtons
                anchors.right: parent.right
                spacing: UM.Theme.getSize("narrow_margin").width
                Cura.PrimaryButton {
                    objectName: "moonrakerReplaceConfirmButton"
                    focusPolicy: Qt.StrongFocus
                    // fixedWidthMode gives the label a width to be
                    // centred IN: Cura's ActionButton binds
                    // buttonText.width to `undefined` without it, and
                    // a Text with no width cannot centre itself — the
                    // label sits wherever its own glyphs end (the
                    // same offset PreviewSecondaryButton wraps).
                    fixedWidthMode: true
                    text: "Replace"
                    width: 130 * screenScaleFactor
                    onClicked: root.replaceConfirmed()
                }
                // The plugin's own secondary, not Cura's: this file's
                // buttons are pinned to the wrapper that centres its
                // label (Cura's ActionButton label does not).
                PreviewSecondaryButton {
                    objectName: "moonrakerReplaceCancelButton"
                    text: "Cancel"
                    width: 130 * screenScaleFactor
                    onClicked: root.replaceCancelled()
                }
            }
        }
    }
}
