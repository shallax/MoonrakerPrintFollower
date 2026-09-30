import QtQuick 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import "../widgets"

// The bed mesh's detail card: the enlarged map out of the Information
// pane's mini, the range filter shared with the Preview's overlay, the
// probe toggle and the numeric readbacks. The host owns the overlay
// frame (position, one-at-a-time open state) and passes it in.
MonitorPopOver {
    id: root

    property var printerModel: null
    property bool open: false

    visible: root.open && root.printerModel != null && root.printerModel.bedMeshAvailable
    title: "Bed mesh — " + (root.printerModel != null ? root.printerModel.bedMeshProfile : "")

    Loader {
        Layout.fillWidth: true
        Layout.fillHeight: true
        active: root.open && root.printerModel != null && root.printerModel.bedMeshAvailable
        sourceComponent: detailContent
    }

    Component {
        id: detailContent
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: UM.Theme.getSize("thin_margin").height

            BedMeshMap {
                id: meshDetail
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 190 * screenScaleFactor
                printer: root.printerModel
                // Rehydrated at creation: the model already holds the
                // persisted value, and a fresh map must never open
                // with the dots missing while the checkbox shows on.
                showProbePoints: root.printerModel != null ? root.printerModel.showProbePoints : false
            }

            // The dual-ended range filter (a request):
            // the SAME five-stop blue-to-red scale the Preview's
            // bed-mesh overlay uses, shared by both surfaces. The
            // window lives in the model, so the Preview card's
            // slider and this one stay synchronised.
            BedMeshRangeSlider {
                id: meshRangeSlider
                Layout.fillWidth: true
                enabled: root.printerModel != null && root.printerModel.bedMeshAvailable
                minimum: root.printerModel != null ? root.printerModel.bedMeshMinimum : 0
                maximum: root.printerModel != null ? root.printerModel.bedMeshMaximum : 0
                low: root.printerModel != null ? root.printerModel.bedMeshThresholdLow : 0
                high: root.printerModel != null ? root.printerModel.bedMeshThresholdHigh : 0
                onWindowAdjusted: {
                    if (root.printerModel != null) {
                        root.printerModel.setBedMeshThresholds(low, high);
                    }
                }
            }

            Row {
                Layout.fillWidth: true
                UM.Label {
                    width: parent.width / 2
                    text: root.printerModel != null ? "Low " + root.printerModel.bedMeshMinimum.toFixed(3) + " mm" : "Low"
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default")
                }
                UM.Label {
                    width: parent.width / 2
                    text: root.printerModel != null ? "High " + root.printerModel.bedMeshMaximum.toFixed(3) + " mm" : "High"
                    horizontalAlignment: Text.AlignRight
                    color: UM.Theme.getColor("text_inactive")
                    font: UM.Theme.getFont("default")
                }
            }

            UM.Label {
                // The Klipper-clamped disclaimer (a
                // request): the same honest claim the Preview's
                // legend makes.
                Layout.fillWidth: true
                text: "Neon orange outline = the probed mesh bounds; outside = the boundary values, continued as Klipper clamps them"
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                wrapMode: Text.WordWrap
            }

            // The detail map is component-scoped, so its live
            // refresh lives here in scope.
            Connections {
                target: root.printerModel
                function onTypedControlsChanged() {
                    meshDetail.refresh();
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("thin_margin").width
                UM.CheckBox {
                    checked: root.printerModel != null ? root.printerModel.showProbePoints : false
                    onToggled: {
                        if (root.printerModel != null) {
                            root.printerModel.setShowProbePoints(checked);
                        }
                    }
                }
                UM.Label {
                    text: "Probe points"
                }
                Item {
                    Layout.fillWidth: true
                }
            }

            // The crosshair readout row is permanent so the map
            // never resizes on hover; it shows the placeholder until
            // the cursor snaps to a probe point OR reads a clamped
            // (extended) value.
            UM.Label {
                Layout.fillWidth: true
                text: (meshDetail.hoverColumn >= 0 || meshDetail.hoverClamped) ? meshDetail.hoverText : "Hover the map for probe coordinates"
                color: (meshDetail.hoverColumn >= 0 || meshDetail.hoverClamped) ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
                horizontalAlignment: Text.AlignHCenter
            }

            GridLayout {
                columns: 3
                Layout.fillWidth: true
                UM.Label {
                    text: "Min " + (root.printerModel != null ? root.printerModel.bedMeshMinimum.toFixed(3) : "0.000") + " mm"
                }
                UM.Label {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    text: "Range " + (root.printerModel != null ? root.printerModel.bedMeshRange.toFixed(3) : "0.000") + " mm"
                }
                UM.Label {
                    horizontalAlignment: Text.AlignRight
                    text: "Max " + (root.printerModel != null ? root.printerModel.bedMeshMaximum.toFixed(3) : "0.000") + " mm"
                }
            }

            UM.Label {
                Layout.fillWidth: true
                text: root.printerModel != null ? "X " + root.printerModel.bedMeshXMin.toFixed(1) + "…" + root.printerModel.bedMeshXMax.toFixed(1) + " mm   ·   Y " + root.printerModel.bedMeshYMin.toFixed(1) + "…" + root.printerModel.bedMeshYMax.toFixed(1) + " mm" : ""
                color: UM.Theme.getColor("text_inactive")
                horizontalAlignment: Text.AlignHCenter
            }

            UM.Label {
                Layout.fillWidth: true
                text: "The faded perimeter is extrapolated to Cura's bed edge; values shown are the actual Klipper mesh heights. The Preview's height exaggeration adjusts from its card."
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
            }
        }
    }
}
