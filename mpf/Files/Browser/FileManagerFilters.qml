import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The filter row: one dropdown per category plus the bare Never
// printed toggle and the Clear all link. The row owns the presentation
// of a filter — which option keys are lit, how many, and whether a
// category is a radio or a checkbox set — and reports every intent as
// a signal. The model writes stay with the shell, so no policy is
// duplicated here.
RowLayout {
    id: root

    // category -> selected keys, and the two tables the dropdowns
    // render: per-category counts for the button faces, and the
    // [key, label, count] option triples.
    property var filters: ({})
    property var filterCounts: ({})
    property var filterOptions: ({})
    // The search alone lights Clear all (the live ruling).
    property string search: ""

    signal filterToggled(string category, string key)
    signal filterValueSet(string category, string key)
    signal clearAllRequested

    function filterValues(category) {
        var values = root.filters[category];
        return values !== undefined ? values : [];
    }
    function filterActive(category) {
        return root.filterValues(category).length > 0;
    }
    function filterCount(category) {
        return root.filterCounts[category] !== undefined ? root.filterCounts[category] : 0;
    }
    function filterAnyActive() {
        if (root.search.length > 0) {
            return true;
        }
        for (var key in root.filterCounts) {
            if (root.filterCounts[key] > 0) {
                return true;
            }
        }
        return false;
    }
    function filterOptionsFor(category) {
        var options = root.filterOptions[category];
        return options !== undefined ? options : [];
    }
    function filterOptionRows(category) {
        // Option triples become self-contained row dicts — the row
        // delegate must not reach up through parent chains (the
        // probe showed that arithmetic is off by one and the
        // category arrives empty).
        var options = root.filterOptionsFor(category);
        var rows = [];
        for (var i = 0; i < options.length; ++i) {
            rows.push({
                "key": options[i][0],
                "label": options[i][1],
                "count": options[i][2],
                "category": category,
                // The single-value categories hold ONE value
                // (the ruling: OR-checkboxes make no sense for
                // windows and bounds).
                "radio": category === "modified" || category === "print_time"
            });
        }
        return rows;
    }

    // The one option-row shape, wired per entry: the row reads its
    // option and its selected state off declared inputs, never off a
    // parent chain.
    Component {
        id: filterOptionRow
        FileFilterOptionRow {
            label: modelData.label
            optionKey: modelData.key
            category: modelData.category
            radio: modelData.radio
            count: modelData.count
            selected: root.filterValues(modelData.category).indexOf(modelData.key) >= 0
            onToggled: function (category, key) {
                root.filterToggled(category, key);
            }
            onValueSet: function (category, key) {
                root.filterValueSet(category, key);
            }
        }
    }

    Layout.fillWidth: true
    spacing: UM.Theme.getSize("default_margin").width / 2

    UM.Label {
        text: "Filters:"
        color: UM.Theme.getColor("text_inactive")
        font: UM.Theme.getFont("medium")
    }
    // The filter slots: BOTH button faces coexist and visibility flips
    // (never a Loader swap — rebuilding the button destroys the open
    // menu the moment a selection lands, the live report: the
    // dropdown dismissed itself). Each menu parents to its SLOT, so
    // `y: parent.height` opens it BELOW the button, not over it (the
    // live report). No CloseOnRelease: a selection never dismisses the
    // menu — the user does (the live ruling).
    Item {
        implicitWidth: slicerPrimary.implicitWidth
        implicitHeight: slicerPrimary.implicitHeight
        Cura.PrimaryButton {
            id: slicerPrimary
            visible: root.filterActive("slicer")
            text: "Slicer ▾ " + root.filterCount("slicer")
            onPressed: slicerPopup.wasOpenAtPress = slicerPopup.opened
            onClicked: {
                if (slicerPopup.wasOpenAtPress) {
                    slicerPopup.close();
                } else {
                    slicerPopup.open();
                }
            }
        }
        Cura.SecondaryButton {
            visible: !root.filterActive("slicer")
            text: "Slicer ▾"
            onPressed: slicerPopup.wasOpenAtPress = slicerPopup.opened
            onClicked: {
                if (slicerPopup.wasOpenAtPress) {
                    slicerPopup.close();
                } else {
                    slicerPopup.open();
                }
            }
        }
        Popup {
            id: slicerPopup
            objectName: "slicerPopup"
            property bool wasOpenAtPress: false
            property string category: "slicer"
            property bool radio: false
            y: parent.height
            x: 0
            padding: 0
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
            contentItem: Rectangle {
                implicitWidth: 240 * screenScaleFactor
                implicitHeight: slicerColumn.height
                color: UM.Theme.getColor("main_background")
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
                Column {
                    id: slicerColumn
                    Repeater {
                        model: root.filterOptionRows("slicer")
                        delegate: filterOptionRow
                    }
                }
            }
        }
    }
    // Modified and Print time are RADIOS (the live ruling:
    // OR-checkboxes make no sense for windows and bounds — one at a
    // time; clicking the active radio clears the filter).
    Item {
        implicitWidth: modifiedPrimary.implicitWidth
        implicitHeight: modifiedPrimary.implicitHeight
        Cura.PrimaryButton {
            id: modifiedPrimary
            visible: root.filterActive("modified")
            text: "Modified ▾ " + root.filterCount("modified")
            onPressed: modifiedPopup.wasOpenAtPress = modifiedPopup.opened
            onClicked: {
                if (modifiedPopup.wasOpenAtPress) {
                    modifiedPopup.close();
                } else {
                    modifiedPopup.open();
                }
            }
        }
        Cura.SecondaryButton {
            visible: !root.filterActive("modified")
            text: "Modified ▾"
            onPressed: modifiedPopup.wasOpenAtPress = modifiedPopup.opened
            onClicked: {
                if (modifiedPopup.wasOpenAtPress) {
                    modifiedPopup.close();
                } else {
                    modifiedPopup.open();
                }
            }
        }
        Popup {
            id: modifiedPopup
            objectName: "modifiedPopup"
            property bool wasOpenAtPress: false
            property string category: "modified"
            property bool radio: true
            y: parent.height
            x: 0
            padding: 0
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
            contentItem: Rectangle {
                implicitWidth: 240 * screenScaleFactor
                implicitHeight: modifiedColumn.height
                color: UM.Theme.getColor("main_background")
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
                Column {
                    id: modifiedColumn
                    Repeater {
                        model: root.filterOptionRows("modified")
                        delegate: filterOptionRow
                    }
                }
            }
        }
    }
    Item {
        implicitWidth: printTimePrimary.implicitWidth
        implicitHeight: printTimePrimary.implicitHeight
        Cura.PrimaryButton {
            id: printTimePrimary
            visible: root.filterActive("print_time")
            text: "Print time ▾ " + root.filterCount("print_time")
            onPressed: printTimePopup.wasOpenAtPress = printTimePopup.opened
            onClicked: {
                if (printTimePopup.wasOpenAtPress) {
                    printTimePopup.close();
                } else {
                    printTimePopup.open();
                }
            }
        }
        Cura.SecondaryButton {
            visible: !root.filterActive("print_time")
            text: "Print time ▾"
            onPressed: printTimePopup.wasOpenAtPress = printTimePopup.opened
            onClicked: {
                if (printTimePopup.wasOpenAtPress) {
                    printTimePopup.close();
                } else {
                    printTimePopup.open();
                }
            }
        }
        Popup {
            id: printTimePopup
            objectName: "printTimePopup"
            property bool wasOpenAtPress: false
            property string category: "print_time"
            property bool radio: true
            y: parent.height
            x: 0
            padding: 0
            closePolicy: Popup.CloseOnEscape | Popup.CloseOnReleaseOutside
            contentItem: Rectangle {
                implicitWidth: 240 * screenScaleFactor
                implicitHeight: printTimeColumn.height
                color: UM.Theme.getColor("main_background")
                border.color: UM.Theme.getColor("lining")
                border.width: UM.Theme.getSize("default_lining").width
                radius: UM.Theme.getSize("default_radius").width
                Column {
                    id: printTimeColumn
                    Repeater {
                        model: root.filterOptionRows("print_time")
                        delegate: filterOptionRow
                    }
                }
            }
        }
    }
    // The Never printed filter is a bare TOGGLE, not a dropdown (the
    // live ruling on the fourth filter category).
    Item {
        implicitWidth: neverPrintedPrimary.implicitWidth
        implicitHeight: neverPrintedPrimary.implicitHeight
        Cura.PrimaryButton {
            id: neverPrintedPrimary
            visible: root.filterActive("never_printed")
            text: "Never printed"
            onClicked: root.filterToggled("never_printed", "true")
        }
        Cura.SecondaryButton {
            visible: !root.filterActive("never_printed")
            text: "Never printed"
            onClicked: root.filterToggled("never_printed", "true")
        }
    }
    // A clickable Clear all in the same style as the [⇄ Columns]
    // trigger (the live ruling); it lights up only while a filter is
    // active.
    UM.Label {
        text: "Clear all"
        color: root.filterAnyActive() ? UM.Theme.getColor("primary") : UM.Theme.getColor("text_inactive")
        MouseArea {
            anchors.fill: parent
            cursorShape: root.filterAnyActive() ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: root.clearAllRequested()
        }
    }
}
