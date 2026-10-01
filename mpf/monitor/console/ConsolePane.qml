import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../resources/theme"

// One transcript/resize owner. The Monitor supplies only model and geometry.
Cura.RoundedRectangle {
    id: consolePanel
    property var printerModel: null
    // The stable containing frame, not the moving resize handle's coordinates.
    property Item resizeFrame: null
    readonly property real availableHeight: resizeFrame != null ? resizeFrame.height : 0
    property bool _complete: false

    function resetTranscript() {
        // The gcode-store poll follows the console's OWN collapse
        // state (the ruling: poll only while the console
        // is on screen, with a backfill on expand). A printer that
        // attaches with the console collapsed starts without the
        // poll; expanding starts it (and seeds the backfill).
        if (consolePanel.printerModel != null) {
            consolePanel.printerModel.setConsoleExpanded(consolePanel.printerModel.sectionExpandedMap["console"] !== false);
        }
        consoleSection.consoleRenderedLines = 0;
        consoleSection.consoleDroppedSeen = consolePanel.printerModel != null ? consolePanel.printerModel.consoleDropped : 0;
        // Revisions are monotonic per model lifetime; on a new
        // printer the pane re-renders from scratch anyway, so the
        // cursor resets with the other two (the architecture
        // panel's asymmetry note).
        consoleSection.consoleRevisionsSeen = consolePanel.printerModel != null ? consolePanel.printerModel.consoleRevisions : 0;
        consoleText.text = "";
        consoleSection.consoleSyncLines();
    }

    onPrinterModelChanged: {
        if (_complete) {
            resetTranscript();
        }
    }
    Component.onCompleted: {
        _complete = true;
        resetTranscript();
    }
    Layout.fillWidth: true
    // Auto-collapse below 350 px (the
    // live report: the console's buttons overflowed
    // when the window was crushed). The persisted
    // expand state is untouched — the width decides
    // the EFFECTIVE state, and the gcode-store poll
    // follows it (poll only while expanded).
    readonly property bool tooNarrow: consoleColumn.width < 350 * screenScaleFactor
    onTooNarrowChanged: {
        if (consolePanel.printerModel != null) {
            if (tooNarrow || consolePanel.printerModel.sectionExpandedMap["console"] === false) {
                consolePanel.printerModel.setConsoleExpanded(false);
            } else {
                consolePanel.printerModel.setConsoleExpanded(true);
            }
        }
    }
    // NO fillHeight: the card hugs the webcam card
    // directly (a fill slot plus a maximum clamp
    // left a huge gap between the cards — a
    // live report). Its height is the
    // user's, bounded by the clamp window below.
    // UNTIL they drag the handle, the card keeps the
    // pane default: the live test found even
    // 55% of the column "way too high" — ~28% it is.
    // Collapsed, the card is a header strip sized by
    // the HANDLE and the BUTTON with equal top/bottom
    // margins — those two are the largest elements and
    // decide the strip (the ruling). The
    // inner column's implicit does not shrink reliably
    // once its content hides, so the collapsed height
    // is explicit.
    // EXPLICIT height, never the inner column's
    // implicit: the real Cura engine computed the
    // implicit from a collapsed chain and the card
    // rendered two lines tall with a white gap (a
    // report; the harness engine disagreed).
    // The pane bounds are the clamp window: the
    // console's floor keeps the header row and the
    // input row usable, and its ceiling leaves the
    // webcam card its own header plus a viewport — a
    // pane crushed under the pointer is the hazard of
    // the earlier report. A stored height
    // that no longer fits a smaller stage renders
    // inside the clamps WITHOUT losing the user's
    // intent: the model keeps what they set.
    readonly property real consoleHandleHeight: Math.max(12 * screenScaleFactor, UM.Theme.getSize("thin_margin").height)
    readonly property real consoleCollapsedHeight: consoleCollapseButton.height + 2 * UM.Theme.getSize("thin_margin").height + consoleHandleHeight
    readonly property real consoleMinHeight: 140 * screenScaleFactor
    readonly property real consoleWebcamFloor: 150 * screenScaleFactor
    readonly property real consoleMaxHeight: Math.max(consoleMinHeight, consolePanel.availableHeight - consoleWebcamFloor - UM.Theme.getSize("default_margin").height)
    readonly property real consoleDefaultHeight: Math.max(190 * screenScaleFactor, consolePanel.availableHeight * 0.28)
    readonly property bool consoleExpanded: consolePanel.printerModel != null && consolePanel.printerModel.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow
    // The live drag height (0 = not dragging): the
    // drag previews locally and commits ONCE on
    // release, like the tuning sliders — a commit per
    // move would rewrite the state file ~60 times a
    // second for a value nobody reads until the pane
    // settles.
    property real consoleDragHeight: 0
    property real consoleResizeStartY: 0
    property real consoleResizeStartHeight: 0
    readonly property real consoleStoredHeight: consolePanel.printerModel != null && consolePanel.printerModel.consoleHeight > 0 ? consolePanel.printerModel.consoleHeight : consoleDefaultHeight
    readonly property real consoleSettledHeight: Math.max(consoleMinHeight, Math.min(consoleMaxHeight, consoleStoredHeight))
    readonly property real consoleCurrentHeight: consoleDragHeight > 0 ? consoleDragHeight : consoleSettledHeight
    // Below the pane's own minimum there is no room
    // for the body at all: the well cannot hold the
    // prompt, the input and its buttons any more, and
    // a squeezed column pushed them past the black
    // border (the live report). The body
    // FADES out just below the minimum and is gone for
    // the rest of the travel to the collapse position,
    // so the closing pane reads as an empty shell
    // rather than a crushed one — the fade follows the
    // DRAG, so dragging back up brings the console
    // straight back. A settled height never lands in
    // the band (the clamp floor is the minimum), so it
    // is only ever seen mid-drag.
    readonly property real consoleBodyFadeSpan: 24 * screenScaleFactor
    readonly property real consoleBodyOpacity: Math.max(0, Math.min(1, (height - (consoleMinHeight - consoleBodyFadeSpan)) / consoleBodyFadeSpan))
    Layout.preferredHeight: consoleExpanded ? consoleCurrentHeight : consoleCollapsedHeight
    Layout.maximumHeight: consoleExpanded ? consoleCurrentHeight : consoleCollapsedHeight
    border.color: UM.Theme.getColor("lining")
    border.width: UM.Theme.getSize("default_lining").width
    color: UM.Theme.getColor("main_background")
    radius: UM.Theme.getSize("default_radius").width
    // The drag passes through heights SHORTER than the
    // inner column's minimum (on the way down to the
    // collapse position), so the card clips: its
    // content must never paint over the webcam card
    // above it.
    clip: true

    // One owner for the resize. The pointer is read
    // in the PANE's frame, never the handle's: the
    // handle rides the edge it is moving, so a local
    // measurement feeds the new height back into its
    // own delta and the console runs away under the
    // pointer. The height the drag reports is
    // CONTINUOUS — the collapse position is its floor,
    // not a jump — so dragging down shrinks the pane
    // to the strip and collapses it, and dragging back
    // up expands it at the same spot (the edge never
    // detaches from the pointer).
    function consoleSetExpanded(expanded) {
        if (consolePanel.printerModel == null) {
            return;
        }
        // Only a real crossing writes: a drag that
        // wiggles across the threshold must not
        // rewrite the state file per event.
        if ((consolePanel.printerModel.sectionExpandedMap["console"] !== false) === expanded) {
            return;
        }
        consolePanel.printerModel.setSectionExpanded("console", expanded);
        consolePanel.printerModel.setConsoleExpanded(expanded);
    }
    function consoleResizeTo(paneY) {
        var height = consoleResizeStartHeight + (consoleResizeStartY - paneY);
        // Up is taller. The collapse position is the
        // drag floor, so the pane can never be pulled
        // below the strip it collapses into.
        height = Math.max(consoleCollapsedHeight, Math.min(consoleMaxHeight, height));
        consoleDragHeight = height;
        consoleSetExpanded(height > consoleCollapsedHeight + 0.5);
    }
    function consoleResizeCommit() {
        if (consoleDragHeight <= 0) {
            return;  // a click on the handle, not a drag
        }
        var height = consoleDragHeight;
        consoleDragHeight = 0;
        if (height < consoleMinHeight) {
            // Released on the way down, above the
            // collapse position: a crushed console is
            // not a size to keep. Collapse it, and
            // leave the model holding the user's last
            // usable height for the next expand.
            consoleSetExpanded(false);
            return;
        }
        consoleSetExpanded(true);
        if (consolePanel.printerModel != null) {
            // Whole pixels: the model's property is an
            // int, the drag delta a real.
            consolePanel.printerModel.setConsoleHeight(Math.round(height));
        }
    }

    // While collapsed, a click ANYWHERE on the
    // strip expands the pane, like the other
    // panes; the header button sits above this
    // area and keeps its own clicks.
    MouseArea {
        visible: consolePanel.printerModel != null && (consolePanel.printerModel.sectionExpandedMap["console"] === false || consolePanel.tooNarrow)
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            // Auto-collapsed-by-width is not a
            // clickable expand: only a wider window
            // restores the panel.
            if (consolePanel.printerModel != null && !consolePanel.tooNarrow) {
                consolePanel.printerModel.setSectionExpanded("console", true);
                consolePanel.printerModel.setConsoleExpanded(true);
            }
        }
    }

    ColumnLayout {
        id: consoleColumn
        anchors.fill: parent
        spacing: 0

        // The resize handle: the card's TOP edge. It
        // holds its own strip in the layout — never an
        // overlay across the header row — so the
        // collapse toggle keeps every pixel of its hit
        // area. It rides BOTH states: pulling the strip
        // down to the collapse position collapses the
        // pane, and dragging back out of it expands the
        // pane (the request).
        Item {
            id: consoleResizeHandle
            objectName: "consoleResizeHandle"
            // Hidden while the auto-collapse width
            // holds: a grab bar for an expansion
            // that cannot happen would be a lie
            // (a live request).
            visible: !consolePanel.tooNarrow
            Layout.fillWidth: true
            Layout.preferredHeight: consolePanel.consoleHandleHeight

            MouseArea {
                id: consoleResizeArea
                objectName: "consoleResizeArea"
                anchors.fill: parent
                cursorShape: Qt.SizeVerCursor
                hoverEnabled: true
                onPressed: {
                    consolePanel.consoleResizeStartY = mapToItem(consolePanel.resizeFrame, mouse.x, mouse.y).y;
                    consolePanel.consoleResizeStartHeight = consolePanel.height;
                    // A reader at the tail stays at the
                    // tail through the resize; one
                    // scrolled up is never yanked (the
                    // golden rule, drag variant).
                    if (consoleFlick.contentY + consoleFlick.height >= consoleFlick.contentHeight - 2) {
                        consoleFlick.restoreScrollPending = true;
                    }
                }
                onPositionChanged: {
                    if (pressed) {
                        consolePanel.consoleResizeTo(mapToItem(consolePanel.resizeFrame, mouse.x, mouse.y).y);
                    }
                }
                onReleased: consolePanel.consoleResizeCommit()
                // A stolen grab (the window losing the
                // pointer, an ancestor's drag) must
                // still land the height the user
                // dragged to.
                onCanceled: consolePanel.consoleResizeCommit()
            }

            // The grip: the strip's affordance — the
            // resize cursor alone is invisible until
            // the pointer is already on it.
            Rectangle {
                anchors.centerIn: parent
                // Wide and thick enough to read as a
                // grab bar at a glance (the
                // live ruling — the first grip was
                // too subtle to find).
                width: 72 * screenScaleFactor
                height: 5 * screenScaleFactor
                radius: height / 2
                color: consoleResizeArea.containsMouse ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
            }

            HoverHandler {
                id: tooltipHover1
            }
            UM.ToolTip {
                parent: consoleResizeHandle
                visible: tooltipHover1.hovered
                targetPoint: Qt.point(consoleResizeHandle.width / 2, consoleResizeHandle.height / 2)
                x: (consoleResizeHandle.width - width) / 2
                y: consoleResizeHandle.height + UM.Theme.getSize("default_margin").height
                width: UM.Theme.getSize("tooltip").width
                text: "Drag to resize the console."
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: UM.Theme.getSize("thin_margin").height
            // The bottom breathing room keeps the
            // collapsed card from hugging the title
            // on both edges (the report).
            Layout.bottomMargin: UM.Theme.getSize("thin_margin").height
            Layout.leftMargin: UM.Theme.getSize("thin_margin").width
            Layout.rightMargin: UM.Theme.getSize("thin_margin").width
            spacing: UM.Theme.getSize("thin_margin").width

            // The collapse toggle hugs the top
            // LEFT like the other panes' buttons;
            // the chevron points the way the
            // pane will move (up = collapse).
            // The toggle hugs the top LEFT and
            // matches the OTHER panes' collapse
            // buttons (‹/›), not a theme chevron
            // (the ruling).
            Cura.SecondaryButton {
                id: consoleCollapseButton
                Layout.alignment: Qt.AlignVCenter
                fixedWidthMode: true
                // Square at the OLD button width:
                // the theme adds its padding around
                // the 32px content, so the height
                // tracks the rendered width (the
                // ruling).
                width: 28 * screenScaleFactor
                iconSize: 12 * screenScaleFactor
                height: width
                implicitHeight: width

                // The same button style as the other
                // panes, with the theme's UP/DOWN
                // chevrons inside it (the pane
                // collapses upward); the button
                // centres the icon itself.
                // The accordion convention: DOWN when expanded
                // (the ruling — the first direction read
                // inverted).
                iconSource: consolePanel.printerModel != null && consolePanel.printerModel.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow ? UM.Theme.getIcon("ChevronSingleDown") : UM.Theme.getIcon("ChevronSingleUp")
                onClicked: {
                    if (consolePanel.printerModel != null && !consolePanel.tooNarrow) {
                        // The poll follows the pane:
                        // collapsing stops the
                        // gcode-store fetch (the
                        // expanded-only
                        // ruling), expanding starts
                        // it with a backfill seed.
                        var expanding = consolePanel.printerModel.sectionExpandedMap["console"] === false;
                        consolePanel.printerModel.setSectionExpanded("console", expanding);
                        consolePanel.printerModel.setConsoleExpanded(expanding);
                    }
                }
                UM.ToolTip {
                    visible: parent.hovered
                    targetPoint: Qt.point(parent.width / 2, 0)
                    x: 0
                    y: parent.height + UM.Theme.getSize("default_margin").height
                    width: UM.Theme.getSize("tooltip").width
                    text: consolePanel.tooNarrow ? "The window is too narrow — widen it to expand the console." : (consolePanel.printerModel != null && consolePanel.printerModel.sectionExpandedMap["console"] !== false ? "Collapse the console." : "Expand the console.")
                }
            }

            UM.Label {
                Layout.fillWidth: true
                text: "Console"
                font: UM.Theme.getFont("medium_bold")
                // Expanded, a proper title reads in
                // the normal text colour; collapsed
                // it greys like the other panes'
                // collapsed strips.
                color: consolePanel.printerModel != null && consolePanel.printerModel.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
            }
            // The error bell (the live
            // request): while the console is
            // collapsed, a NEW error line rings a
            // red bell next to the header until
            // the console expands.
            UM.ColorImage {
                visible: consolePanel.printerModel != null && consolePanel.printerModel.consoleErrorBell
                Layout.preferredWidth: 14 * screenScaleFactor
                Layout.preferredHeight: 14 * screenScaleFactor
                source: Qt.resolvedUrl("../../resources/svg/Bell.svg")
                color: MoonrakerTheme.consoleBell
            }
        }

        ColumnLayout {
            id: consoleSection
            visible: consolePanel.printerModel != null && consolePanel.printerModel.sectionExpandedMap["console"] !== false && !consolePanel.tooNarrow
            // The body fades as the pane closes (see
            // consoleBodyOpacity): a squeezed body
            // spilled past the card, and the fade
            // keeps the closing pane clean.
            opacity: consolePanel.consoleBodyOpacity
            // The cheat: if the app started
            // with the console collapsed, the FIRST
            // expand scrolls to the tail once (the
            // restore ran collapsed and its metrics
            // were stale). Later collapse/expands
            // never scroll.
            property bool consoleStartedCollapsed: false
            property bool consoleFirstExpandHandled: false
            Component.onCompleted: {
                consoleStartedCollapsed = consolePanel.printerModel != null && consolePanel.printerModel.sectionExpandedMap["console"] === false;
            }
            onVisibleChanged: {
                if (visible && consoleStartedCollapsed && !consoleFirstExpandHandled) {
                    consoleFirstExpandHandled = true;
                    consoleFlick.restoreScrollPending = true;
                }
            }
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: UM.Theme.getSize("default_margin").width
            Layout.topMargin: UM.Theme.getSize("narrow_margin").height
            Layout.bottomMargin: UM.Theme.getSize("default_margin").height
            spacing: UM.Theme.getSize("narrow_margin").height
            // The section stays ENABLED while
            // disconnected: scrolling, selecting and
            // copying the restored history must keep
            // working (the ruling). Only
            // the INPUT surface disables.
            enabled: consolePanel.printerModel != null
            property int consoleRecallIndex: -1
            property string consoleDraft: ""
            // Pick an actually-installed monospace face at
            // runtime: the generic "monospace" and comma
            // lists do not resolve on every machine.
            function monoFamily() {
                try {
                    var names = Qt.fontFamilies();
                    var known = ["consolas", "menlo", "courier", "mono"];
                    for (var i = 0; i < names.length; ++i) {
                        var lower = String(names[i]).toLowerCase();
                        for (var k = 0; k < known.length; ++k) {
                            if (lower.indexOf(known[k]) >= 0) {
                                return names[i];
                            }
                        }
                    }
                } catch (e) {}
                return "monospace";
            }
            // The transcript arrives oldest-first; the pane
            // is ONE rich TextEdit so text selection spans
            // lines (per-line delegates could not). The
            // sync appends only the NEW lines, inserting
            // at the end with the reader's selection
            // saved and restored around it — new output
            // never disturbs a selection or yanks the
            // scroll position.
            property int consoleRenderedLines: 0
            // Ring-rotation lines the pane already saw
            // dropped out of the transcript's head.
            property int consoleDroppedSeen: 0
            property int consoleRevisionsSeen: 0

            function consoleLineHtml(entry) {
                // Terminal voice, three speakers
                // (the rulings): commands
                // carry ">", Moonraker's responses
                // carry "<", and the plugin's own
                // notes carry "#" in amber — a hue
                // Klipper's red/green/grey never
                // uses, so anything the plugin adds
                // is obviously its own. Responses
                // render BRIGHT (red/green); saved
                // commands keep their TEXT light
                // grey and put the verdict on the
                // ">" only (the live
                // ruling: the whole line turning
                // green was too much) — green
                // matches the input row's prompt,
                // red is a failure, quiet grey is
                // no verdict. While unsaved the
                // line stays blue (the
                // ruling). The muted hues are
                // contrast-checked (≥4.5:1 on the
                // dark well) — the old muted
                // family sat near 2:1.
                var hue = MoonrakerTheme.consoleText;
                var promptHue = "";
                if (entry.kind === "response") {
                    hue = entry.error ? MoonrakerTheme.errorRed : (entry.success ? MoonrakerTheme.consoleSuccess : MoonrakerTheme.consoleMuted);
                } else if (entry.kind === "note") {
                    hue = MoonrakerTheme.consoleWarn;
                } else if (entry.saved === false) {
                    // Sent but not yet flushed to
                    // disk: blue until the save
                    // lands (the ruling —
                    // the API verdict flips too
                    // fast to read live).
                    hue = MoonrakerTheme.consoleInfo;
                } else {
                    promptHue = entry.error ? MoonrakerTheme.consolePromptError : (entry.success ? MoonrakerTheme.successGreen : MoonrakerTheme.consoleMuted);
                }
                if (entry.restored) {
                    // Restored lines grey uniformly:
                    // the verdict colours are LIVE
                    // signals, and a restored command
                    // showing its old green read as
                    // current state (the
                    // report). Restored responses
                    // keep their muted hues.
                    hue = entry.kind === "command" ? MoonrakerTheme.consoleCommand : (entry.error ? MoonrakerTheme.consoleHistoryError : (entry.success ? MoonrakerTheme.consoleHistorySuccess : MoonrakerTheme.consoleCommand));
                }
                var escaped = String(entry.text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                if (entry.kind === "note") {
                    return "<span style=\"color:" + hue + ";\"># " + escaped + "</span>";
                }
                if (entry.kind === "response") {
                    return "<span style=\"color:" + hue + ";\">&lt; " + escaped + "</span>";
                }
                if (promptHue) {
                    return "<span style=\"color:" + promptHue + ";\">&gt; </span><span style=\"color:" + hue + ";\">" + escaped + "</span>";
                }
                return "<span style=\"color:" + hue + ";\">&gt; " + escaped + "</span>";
            }

            function consoleSyncLines() {
                var lines = consolePanel.printerModel != null ? consolePanel.printerModel.consoleLines : [];
                var dropped = consolePanel.printerModel != null ? consolePanel.printerModel.consoleDropped : 0;
                var revisions = consolePanel.printerModel != null ? consolePanel.printerModel.consoleRevisions : 0;
                // Captured BEFORE any rebuild below: a
                // rebuild collapses the content height,
                // which would otherwise read as "at the
                // end" and yank a scrolled-up reader.
                var wasAtEnd = consoleFlick.contentY + consoleFlick.height >= consoleFlick.contentHeight - 2;
                // A recolour rebuild (a verdict or a
                // saved-flush) must keep the reader's
                // EXACT place; only a first load may
                // follow the tail.
                var fromEmpty = consoleRenderedLines === 0;
                var rebuildContentY = consoleFlick.contentY;
                // The selection is captured BEFORE the
                // rebuild wipe: capturing it after left
                // the wiped document empty, so the
                // restore below was a silent no-op and
                // a mid-copy selection died on every
                // recolour (the UX panel).
                var selStart = consoleText.selectionStart;
                var selEnd = consoleText.selectionEnd;
                var prevTextHeight = consoleText.height;
                if (lines.length < consoleRenderedLines || dropped > consoleDroppedSeen || revisions > consoleRevisionsSeen) {
                    // A Clear, a printer switch, or the
                    // session ring rotating at its cap
                    // (its length stops growing there, so
                    // the count alone would stall this
                    // sync forever): rebuild the pane
                    // from the transcript.
                    consoleText.text = "";
                    consoleRenderedLines = 0;
                    consoleDroppedSeen = dropped;
                    consoleRevisionsSeen = revisions;
                }
                if (lines.length === consoleRenderedLines) {
                    return;
                }
                var html = "";
                for (var i = consoleRenderedLines; i < lines.length; ++i) {
                    if (i > consoleRenderedLines) {
                        html += "<br>";
                    }
                    html += consoleLineHtml(lines[i]);
                }
                if (consoleRenderedLines === 0) {
                    // The rebuild sets the document
                    // DIRECTLY: clearing the text and
                    // insert()ing afterwards produced
                    // an EMPTY pane in the real Cura
                    // engine (total=53 rendered=53
                    // text='' — the smoking
                    // gun), while the append path's
                    // insert works there. The
                    // restored history then opens at
                    // the NEWEST line (the
                    // ruling).
                    consoleText.text = html;
                    if (fromEmpty) {
                        // The FIRST load follows the
                        // tail (waits for the flick's
                        // metrics to settle, or for the
                        // first expand of a start-
                        // collapsed console — the
                        // cheat). Recolour
                        // rebuilds never follow.
                        consoleFlick.restoreScrollPending = true;
                    } else {
                        // The reader keeps their exact
                        // place across the recolour
                        // (the golden rule). A ring
                        // rotation ALSO removed lines
                        // above the viewport: the
                        // document shrank by exactly
                        // their height, so the position
                        // shifts up by that amount to
                        // keep the visible text
                        // stationary (the engineering
                        // panel's rotation yank).
                        var rotationDrop = Math.max(0, prevTextHeight - consoleText.height);
                        consoleFlick.contentY = Math.max(0, Math.min(rebuildContentY - rotationDrop, consoleFlick.contentHeight - consoleFlick.height));
                    }
                } else {
                    // A chunk appended into a pane
                    // that already shows lines must
                    // start on a fresh line: without
                    // the leading break it glued onto
                    // the last rendered line.
                    html = "<br>" + html;
                    consoleText.cursorPosition = consoleText.length;
                    consoleText.insert(consoleText.length, html);
                }
                consoleRenderedLines = lines.length;
                if (selStart !== selEnd && selStart >= 0) {
                    consoleText.select(selStart, selEnd);
                }
                // Follow the tail ONLY while the
                // reader was already at it AND the
                // pane had content before this sync:
                // the FIRST sync renders an empty
                // pane that reads as "at the end",
                // and auto-scrolling then buried
                // the restored commands at the head
                // (the report).
                if (wasAtEnd && consoleRenderedLines > 0) {
                    consoleFlick.stickToEnd = true;
                    consoleFlick.contentY = consoleFlick.contentHeight - consoleFlick.height;
                }
            }

            function consoleSend() {
                if (consolePanel.printerModel != null) {
                    // A refused send (queue full, lane
                    // busy, Moonraker down) must keep
                    // the typed line: losing an unsent
                    // G-code draft on refusal is data
                    // loss, and the status line already
                    // explains the refusal.
                    if (consolePanel.printerModel.sendConsoleCommand(consoleInput.text)) {
                        consoleInput.text = "";
                        consoleDraft = "";
                        consoleRecallIndex = -1;
                        // A send is the reader signalling
                        // they want to follow the tail
                        // again: return the view to the
                        // prompt even from a scrolled-up
                        // position (the ruling).
                        consoleFlick.stickToEnd = true;
                        consoleFlick.contentY = consoleFlick.contentHeight - consoleFlick.height;
                    }
                    consoleInput.forceActiveFocus();
                }
            }
            function consoleRecall(step) {
                var history = consolePanel.printerModel != null ? consolePanel.printerModel.consoleHistory : [];
                if (history.length === 0) {
                    return;
                }
                if (consoleRecallIndex < 0) {
                    consoleDraft = consoleInput.text;
                }
                consoleRecallIndex = Math.max(-1, Math.min(history.length - 1, consoleRecallIndex + step));
                consoleInput.text = consoleRecallIndex < 0 ? consoleDraft : history[history.length - 1 - consoleRecallIndex];
            }

            // A shell-terminal-styled console: dark,
            // fixed-width, newest line pinned to the
            // bottom — the list slides to the end as
            // each line lands, and the input row lives
            // INSIDE the dark well with the prompt, so
            // the green ">" keeps its contrast on both
            // themes (panel UX P3).
            Cura.RoundedRectangle {
                id: consoleWell
                Layout.fillWidth: true
                Layout.preferredHeight: 190 * screenScaleFactor
                // The terminal is the pane's
                // filler: the extra height goes
                // to the feed, not to dead space
                // under it.
                Layout.fillHeight: true
                // The disconnected state reads in the
                // well itself: grey instead of the
                // terminal black (the live
                // ruling).
                color: consolePanel.printerModel != null && consolePanel.printerModel.monitorConnected ? MoonrakerTheme.consoleBackground : MoonrakerTheme.consoleBackgroundOffline
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
                // The well is a real CONTAINER, not a
                // backdrop: the prompt, the input and
                // its buttons live inside it, and when
                // the pane is dragged shorter than
                // they need they are cut at the well's
                // own edge instead of floating
                // outside the black border (a
                // live report).
                clip: true

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: UM.Theme.getSize("narrow_margin").width
                    spacing: UM.Theme.getSize("thin_margin").height

                    Item {
                        id: consoleOutputHost
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        // 5.11's TextArea metrics inflate the flick's content
                        // implicit height, which FLOORS this fillHeight slot
                        // and pushes the input row out of the well — the
                        // transcript then grabs the Send/Clear presses (the
                        // sweep's d1-07/d3-01 find). The explicit floor
                        // keeps the slot shrinkable on every version.
                        Layout.minimumHeight: 0

                        Flickable {
                            id: consoleFlick
                            anchors.fill: parent
                            clip: true
                            // The restore's tail scroll
                            // fires when the metrics
                            // SETTLE: the content height
                            // updates over several frames
                            // after setting the text, and
                            // one-shot scrolls measured
                            // stale values (the
                            // reports).
                            property bool restoreScrollPending: false
                            // Stick-to-end (the
                            // live report: when the pane
                            // is crushed and the text
                            // wraps, the sync pins to a
                            // height that has not settled,
                            // the viewport lands short of
                            // the tail, and the next poll
                            // snaps back). While the
                            // reader was at the end, every
                            // metric change re-pins — the
                            // wrap's own settle can never
                            // leave the viewport stranded
                            // above the tail.
                            property bool stickToEnd: false
                            // The scroll follows every
                            // metric change until the
                            // layout goes quiet — the
                            // text height settles over
                            // several frames and a
                            // one-shot scroll kept
                            // landing off by the command
                            // bar (the reports).
                            Timer {
                                id: restoreQuietTimer
                                interval: 120
                                repeat: false
                                onTriggered: consoleFlick.restoreScrollPending = false
                            }
                            // GOLDEN RULE: the reader's own
                            // movement cancels the restore's
                            // follow — a user scrolling up
                            // mid-history is never yanked
                            // (the ruling).
                            onMovementStarted: {
                                restoreScrollPending = false;
                                stickToEnd = false;
                            }
                            onContentHeightChanged: {
                                if (restoreScrollPending || stickToEnd) {
                                    contentY = contentHeight - height;
                                    restoreQuietTimer.restart();
                                }
                            }
                            onHeightChanged: {
                                if (restoreScrollPending || stickToEnd) {
                                    contentY = contentHeight - height;
                                    restoreQuietTimer.restart();
                                }
                            }
                            contentWidth: consoleText.width
                            contentHeight: Math.max(consoleText.height, consoleFlick.height)
                            ScrollBar.vertical: UM.ScrollBar {
                                id: consoleScrollbar
                                // GOLDEN RULE, scrollbar
                                // variant: a handle drag
                                // drives contentY directly
                                // and never fires
                                // onMovementStarted — the
                                // reader's drag cancels the
                                // pending restore itself
                                // (the UX panel).
                                onPressedChanged: {
                                    if (pressed) {
                                        restoreScrollPending = false;
                                        consoleFlick.stickToEnd = false;
                                    }
                                }
                            }
                            Column {
                                // The vertical scrollbar
                                // overlays the well's right
                                // edge: the text must stop
                                // short of it or wrapped
                                // lines run underneath (a
                                // live report). A
                                // CONSTANT reserve (the
                                // status gutter's
                                // precedent): the
                                // scrollbar's own width
                                // fed the column's width →
                                // the text's wrap → the
                                // content height → the
                                // scrollbar's visibility —
                                // a binding loop (the
                                // capture's report).
                                width: consoleFlick.width - 14 * screenScaleFactor
                                height: consoleFlick.contentHeight
                                // The spacer pins the sparse
                                // transcript to the shell's
                                // bottom edge; once the text
                                // fills the viewport it scrolls
                                // exactly like a terminal.
                                Item {
                                    width: 1
                                    height: Math.max(0, consoleFlick.height - consoleText.height)
                                }
                                TextEdit {
                                    id: consoleText
                                    // Inert; the harness's rendered-follows
                                    // scenarios read this pane's text.
                                    objectName: "moonrakerConsoleOutput"
                                    width: parent.width
                                    readOnly: true
                                    selectByMouse: true
                                    selectByKeyboard: true
                                    textFormat: TextEdit.RichText
                                    // Long Klipper lines wrap
                                    // instead of overflowing the
                                    // well; wrapping breaks on
                                    // word boundaries (a
                                    // live report).
                                    wrapMode: TextEdit.Wrap
                                    font.family: consoleSection.monoFamily()
                                    color: MoonrakerTheme.consoleText
                                    // No blinking caret: a read-only
                                    // terminal pane has no cursor, and
                                    // the caret's phase made the
                                    // captures nondeterministic.
                                    cursorVisible: false
                                    // No Esc handling here on
                                    // purpose: this read-only
                                    // pane declines the key
                                    // (as any text item
                                    // does), and the host
                                    // page's ladder answers
                                    // it — see
                                    // answerEscape() in
                                    // MoonrakerMonitorDashboard.
                                }
                            }
                        }

                        UM.Label {
                            // The empty-state hint is an
                            // OVERLAY, not a layout child: a
                            // layout slot stole a line from
                            // the output area and the feed
                            // stopped short of the input row
                            // (the live report).
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.bottomMargin: 8 * screenScaleFactor
                            opacity: consolePanel.printerModel == null || consolePanel.printerModel.consoleLines.length === 0 ? 1 : 0
                            text: "No commands yet — lines you send appear here."
                            font.family: consoleSection.monoFamily()
                            color: MoonrakerTheme.consoleTextMuted
                            elide: Text.ElideRight
                        }
                    }

                    Connections {
                        target: consolePanel.printerModel
                        function onConsoleChanged() {
                            consoleSection.consoleSyncLines();
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("thin_margin").width
                        UM.Label {
                            text: ">"
                            font: UM.Theme.getFont("medium_bold")
                            color: MoonrakerTheme.successGreen
                        }
                        Cura.TextField {
                            id: consoleInput
                            objectName: "moonrakerConsoleInput"
                            Layout.fillWidth: true
                            // The row's only shrink absorber: on 5.11's
                            // theme metrics the field's implicit minimum
                            // outran the pane, pushing Send and Clear out
                            // of their cells — the presses landed on the
                            // field's wider region.
                            Layout.minimumWidth: 0
                            // Nothing of the field paints or takes input
                            // outside its own cell.
                            clip: true
                            placeholderText: "G-code command…"
                            // The placeholder must clear the contrast
                            // census on the light pane: the theme's
                            // muted tone, never UM's grey-on-grey.
                            placeholderTextColor: MoonrakerTheme.consoleTextMuted
                            font.family: consoleSection.monoFamily()
                            enabled: consolePanel.printerModel != null && consolePanel.printerModel.monitorConnected
                            Keys.onReturnPressed: consoleSection.consoleSend()
                            Keys.onUpPressed: consoleSection.consoleRecall(1)
                            Keys.onDownPressed: consoleSection.consoleRecall(-1)
                        }
                        Cura.SecondaryButton {
                            text: "Send"
                            objectName: "moonrakerConsoleSend"
                            // The buttons hold their cells; the field
                            // gives up the width instead.
                            Layout.fillWidth: false
                            enabled: consolePanel.printerModel != null && consolePanel.printerModel.monitorConnected
                            onClicked: consoleSection.consoleSend()
                        }
                        Cura.SecondaryButton {
                            text: "Clear"
                            objectName: "moonrakerConsoleClear"
                            Layout.fillWidth: false
                            enabled: consolePanel.printerModel != null && consolePanel.printerModel.monitorConnected && consolePanel.printerModel.consoleLines.length > 0
                            onClicked: consolePanel.printerModel.clearConsoleHistory()
                        }
                    }
                }
            }
        }
    }
}
