import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura
import "../../Widgets"

// The temperature chart's detail card: the enlarged chart, the legend
// with its per-sensor toggles and colours, and the hover state the
// floating tooltip follows. The host owns the overlay frame (position,
// height, one-at-a-time open state) and the chosen sensor, because the
// dashboard's escape ladder clears it.
MonitorPopOver {
    id: root

    property var printerModel: null
    property bool open: false
    // The selection is the HOST's state: it arrives as an input and
    // leaves by signal so the host keeps one owner for it.
    property string selectedChartSensor: ""
    signal sensorSelected(string sensor)

    // Proxied hover state for the floating tooltip, which lives outside
    // the clipped card so it may overflow any boundary. The cursor is
    // in THIS card's frame; the host maps it into the overlay's.
    property string hoverClockProxy: ""
    property var hoverValuesProxy: []
    property point hoverCursor: Qt.point(-1, -1)

    visible: root.open && root.printerModel != null
    // Reset the tooltip proxies on EVERY close path — the outside-click
    // layer, the opener's second click and auto-close all flip
    // `visible` — so a reopen never flashes the previous hover's values
    // at a stale position.
    onVisibleChanged: {
        if (!visible) {
            hoverClockProxy = "";
            hoverValuesProxy = [];
            hoverCursor = Qt.point(-1, -1);
        }
    }

    property bool allChartSensorsHidden: {
        var legend = root.printerModel != null ? root.printerModel.temperatureChartLegend : ({
                "series": []
            });
        if (legend.series.length === 0) {
            return false;
        }
        for (var i = 0; i < legend.series.length; ++i) {
            if (legend.series[i].visible) {
                return false;
            }
        }
        return true;
    }
    property string selectedChartSensorLabel: {
        var legend = root.printerModel != null ? root.printerModel.temperatureChartLegend : ({
                "series": []
            });
        for (var i = 0; i < legend.series.length; ++i) {
            if (legend.series[i].name === root.selectedChartSensor) {
                return legend.series[i].label;
            }
        }
        return root.selectedChartSensor;
    }
    property string selectedChartSensorColor: {
        var legend = root.printerModel != null ? root.printerModel.temperatureChartLegend : ({
                "series": []
            });
        for (var i = 0; i < legend.series.length; ++i) {
            if (legend.series[i].name === root.selectedChartSensor) {
                return legend.series[i].color;
            }
        }
        return "";
    }

    // The colour picker is a platform dialog: the card must not import
    // the module itself, and builds the dialog from its own document on
    // the click — a host whose platform builds no colour dialog still
    // gets the whole monitor.
    property var chartColorDialogComponent: null
    property var chartColorDialog: null
    function openChartColorDialog() {
        if (chartColorDialog === null) {
            if (chartColorDialogComponent === null) {
                chartColorDialogComponent = Qt.createComponent("MoonrakerChartColorDialog.qml");
            }
            if (chartColorDialogComponent.status !== Component.Ready) {
                console.log("Moonraker colour picker unavailable: " + chartColorDialogComponent.errorString());
                return;
            }
            chartColorDialog = chartColorDialogComponent.createObject(root);
            if (chartColorDialog === null) {
                console.log("Moonraker colour picker failed to instantiate: " + chartColorDialogComponent.errorString());
                return;
            }
            chartColorDialog.title = root.printerModel != null && root.printerModel.britishSpelling ? "Sensor colour" : "Sensor color";
            chartColorDialog.accepted.connect(root.applyChartColorChoice);
        }
        chartColorDialog.open();
    }
    function applyChartColorChoice() {
        if (chartColorDialog === null || root.printerModel == null || root.selectedChartSensor === "") {
            return;
        }
        var colour = chartColorDialog.selectedColor;
        var hex = "#" + ((1 << 24) + (Math.round(colour.r * 255) << 16) + (Math.round(colour.g * 255) << 8) + Math.round(colour.b * 255)).toString(16).slice(-6);
        root.printerModel.setTemperatureSensorColor(root.selectedChartSensor, hex);
    }

    // The content loads only while the card is open: a zero-sized
    // Canvas inside a closed card sent the engine into an endless
    // relayout on pane collapses, and hidden bindings never evaluate.
    Loader {
        Layout.fillWidth: true
        Layout.fillHeight: true
        active: root.open
        sourceComponent: chartContent
    }

    Component {
        id: chartContent
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: UM.Theme.getSize("thin_margin").height

            TemperatureChart {
                id: chartPanelChart
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 180 * screenScaleFactor
                Connections {
                    target: chartPanelChart
                    function onHoverClockChanged() {
                        root.hoverClockProxy = chartPanelChart.hoverClock;
                    }
                    function onHoverValuesChanged() {
                        root.hoverValuesProxy = chartPanelChart.hoverValues;
                    }
                    function onHoverCursorChanged() {
                        var p = chartPanelChart.mapToItem(root, chartPanelChart.hoverCursor.x, chartPanelChart.hoverCursor.y);
                        root.hoverCursor = p;
                    }
                }
                // Hidden when the pop-over is closed: the closed
                // card's zero-sized canvas must never paint. The
                // full payload is the model's dormant object until
                // the pop-over opens, so this gate also reads the
                // legend (which never goes dormant) for the
                // has-data case.
                visible: root.open && root.printerModel != null && root.printerModel.temperatureChartLegend.series.length > 0
                chart: root.printerModel != null ? root.printerModel.temperatureChartFull : ({
                        "series": [],
                        "showTargets": true,
                        "showPower": true
                    })
            }

            UM.Label {
                Layout.fillWidth: true
                visible: root.printerModel != null && root.printerModel.temperatureChartLegend.series.length === 0
                text: "No temperature data yet — the chart fills once the printer reports temperatures."
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
            }

            UM.Label {
                Layout.fillWidth: true
                visible: root.allChartSensorsHidden
                text: "All sensors hidden — use the legend to show them again."
                color: UM.Theme.getColor("text_inactive")
                wrapMode: Text.WordWrap
            }

            // Legend: a two-column grid of visibility toggles with
            // live values. It binds to the legend property, which
            // only notifies on real changes, and `toggled` fires on
            // user interaction only — so the delegates are never
            // rebuilt at the 1 Hz sample cadence and re-bound
            // checkboxes cannot rewrite the state file.
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: UM.Theme.getSize("default_margin").width
                rowSpacing: UM.Theme.getSize("narrow_margin").height
                Repeater {
                    model: root.printerModel != null ? root.printerModel.temperatureChartLegend.series : []
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: UM.Theme.getSize("narrow_margin").width
                        UM.CheckBox {
                            checked: modelData.visible
                            // The label lives in the neighbouring
                            // cell — name the control for screen
                            // readers (panel UX P3).
                            Accessible.name: "Show " + modelData.label
                            onToggled: root.printerModel.setTemperatureSensorVisible(modelData.name, checked)
                        }
                        Rectangle {
                            width: 10 * screenScaleFactor
                            height: 10 * screenScaleFactor
                            radius: 5 * screenScaleFactor
                            color: modelData.color
                            border.color: root.selectedChartSensor === modelData.name ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                            border.width: root.selectedChartSensor === modelData.name ? 2 : 1
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.sensorSelected(root.selectedChartSensor === modelData.name ? "" : root.selectedChartSensor)
                            }
                        }
                        UM.Label {
                            Layout.fillWidth: true
                            text: modelData.label
                            elide: Text.ElideRight
                        }
                        UM.Label {
                            text: {
                                // The live value rides the latest
                                // projection — one scalar per
                                // sensor, never a search through
                                // the chart payload.
                                var latest = root.printerModel != null ? root.printerModel.temperatureChartLatest : null;
                                if (latest == null || latest[modelData.name] === undefined) {
                                    return "—";
                                }
                                return Number(latest[modelData.name]).toFixed(1) + "°C";
                            }
                            color: UM.Theme.getColor("text_inactive")
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: root.selectedChartSensor !== ""
                spacing: UM.Theme.getSize("thin_margin").width
                UM.Label {
                    text: root.selectedChartSensorLabel + (root.printerModel != null && root.printerModel.britishSpelling ? " colour:" : " color:")
                    color: UM.Theme.getColor("text_inactive")
                }
                Repeater {
                    model: root.printerModel != null ? root.printerModel.temperatureChartLegend.palette : []
                    Rectangle {
                        width: 16 * screenScaleFactor
                        height: 16 * screenScaleFactor
                        radius: 8 * screenScaleFactor
                        color: modelData
                        border.color: root.selectedChartColor === modelData ? UM.Theme.getColor("primary") : UM.Theme.getColor("lining")
                        border.width: root.selectedChartColor === modelData ? 2 : 1
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.printerModel.setTemperatureSensorColor(root.selectedChartSensor, modelData)
                        }
                    }
                }
                Cura.SecondaryButton {
                    text: "Custom…"
                    onClicked: root.openChartColorDialog()
                    UM.ToolTip {
                        visible: parent.hovered
                        targetPoint: Qt.point(parent.width / 2, 0)
                        x: 0
                        y: parent.height + UM.Theme.getSize("default_margin").height
                        width: UM.Theme.getSize("tooltip").width
                        text: "Pick any " + (root.printerModel != null && root.printerModel.britishSpelling ? "colour" : "color") + " for " + root.selectedChartSensorLabel + "."
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: UM.Theme.getSize("default_margin").width
                UM.CheckBox {
                    checked: root.printerModel != null ? root.printerModel.temperatureChartLegend.showTargets : true
                    onToggled: root.printerModel.setShowTemperatureTargets(checked)
                }
                UM.Label {
                    text: "Targets"
                }
                UM.CheckBox {
                    checked: root.printerModel != null ? root.printerModel.temperatureChartLegend.showPower : true
                    onToggled: root.printerModel.setShowTemperaturePower(checked)
                }
                UM.Label {
                    text: "Heater power"
                }
            }
        }
    }
}
