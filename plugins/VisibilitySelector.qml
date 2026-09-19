import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM

// The shared all/none three-state selector (a live ruling): the
// native themed checkbox's tri-state — a tick at ALL, a filled
// square at SOME, empty at NONE. View-only: the host owns the counts
// and the action; a click emits toggled.
Row {
    id: selectorRoot
    spacing: UM.Theme.getSize("narrow_margin").width

    property int total: 0
    property int visibleCount: 0

    signal toggled

    UM.CheckBox {
        id: selectorCheck
        objectName: "visibilitySelectorBox"
        anchors.verticalCenter: parent.verticalCenter
        checkState: selectorRoot.visibleCount === 0 ? Qt.Unchecked : (selectorRoot.visibleCount === selectorRoot.total ? Qt.Checked : Qt.PartiallyChecked)
        text: "Show all"
        enabled: selectorRoot.total > 0
        onClicked: {
            selectorRoot.toggled();
            selectorCheck.checkState = Qt.binding(function () {
                    return selectorRoot.visibleCount === 0 ? Qt.Unchecked : (selectorRoot.visibleCount === selectorRoot.total ? Qt.Checked : Qt.PartiallyChecked);
                });
        }
    }
}
