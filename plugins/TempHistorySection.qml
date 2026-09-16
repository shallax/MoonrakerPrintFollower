import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.3
import UM 1.5 as UM
import Cura 1.1 as Cura

// The Temperature-history section (4.3.0 extraction): the mini chart
// and its legend out of the monitor as one property-driven component.
// The host feeds the mini-series state and owns the shared pop-over
// behind a toggle signal.
ColumnLayout {
    id: root
    spacing: 0
    property var printerModel: null
    property var miniSeries: []
    property bool miniHasSeries: false
    signal popOverToggleRequested(string name)

    CollapsibleSectionHeader {
        Layout.fillWidth: true
        printerModel: root.printerModel
        title: "Temperature history"
        sectionId: "temphistory"
        sectionIconUrl: Qt.resolvedUrl("Thermometer.svg")
    }
    ColumnLayout {
        visible: root.printerModel == null || root.printerModel.sectionExpandedMap["temphistory"] !== false
        Layout.topMargin: UM.Theme.getSize("default_margin").height
        Layout.bottomMargin: UM.Theme.getSize("default_margin").height
        Layout.leftMargin: UM.Theme.getSize("narrow_margin").width + UM.Theme.getSize("section_icon").width / 2
        Layout.fillWidth: true
        spacing: UM.Theme.getSize("default_margin").height

        // The mini widget graphs the primary sensors
        // (extruders, bed, chamber heater) honouring
        // the legend's visibility; the full legend,
        // targets and power live in the click-to-
        // enlarge pop-over.
        TemperatureChart {
            Layout.fillWidth: true
            Layout.preferredHeight: 90 * screenScaleFactor
            compact: true
            tooltipText: "Click for the full temperature history (last 30 minutes)."
            chart: {
                var payload = root.printerModel != null ? root.printerModel.temperatureChart : null;
                if (payload == null) {
                    return {
                        "series": [],
                        "showTargets": false,
                        "showPower": false,
                        "palette": [],
                        "filling": false,
                        "wallOrigin": 0
                    };
                }
                return {
                    "series": root.miniSeries,
                    "showTargets": false,
                    "showPower": false,
                    "palette": payload.palette,
                    "filling": payload.filling,
                    "wallOrigin": payload.wallOrigin
                };
            }
            visible: root.miniHasSeries
            onClicked: {
                if (root.printerModel != null)
                    root.popOverToggleRequested("chart");
            }
        }

        // The mini chart's own legend: colour dot,
        // sensor name and live value, so the
        // unlabelled sparklines stay readable.
        Flow {
            Layout.fillWidth: true
            visible: root.miniHasSeries
            spacing: UM.Theme.getSize("default_margin").width
            Repeater {
                model: root.miniSeries
                RowLayout {
                    spacing: UM.Theme.getSize("narrow_margin").width
                    Rectangle {
                        width: 8 * screenScaleFactor
                        height: 8 * screenScaleFactor
                        radius: 4 * screenScaleFactor
                        color: modelData.color
                    }
                    UM.Label {
                        text: {
                            var payload = root.printerModel != null ? root.printerModel.temperatureChart.series : [];
                            var value = "—";
                            for (var i = 0; i < payload.length; ++i) {
                                if (payload[i].name === modelData.name && payload[i].points.length > 0) {
                                    value = payload[i].points[payload[i].points.length - 1][1].toFixed(1) + "°C";
                                }
                            }
                            return modelData.label + " " + value;
                        }
                        elide: Text.ElideRight
                    }
                }
            }
        }

        // The label doubles as the pop-over opener
        // when the mini chart is gone: hiding every
        // primary sensor must not lock the user out
        // of the legend that re-enables them.
        UM.Label {
            Layout.fillWidth: true
            visible: root.printerModel != null && !root.miniHasSeries
            text: root.printerModel != null && root.printerModel.temperatureChart.series.length > 0 ? "All sensors hidden — click to re-enable one in the chart." : "No hotend or bed temperature data yet"
            color: root.printerModel != null && !root.miniHasSeries && root.printerModel.temperatureChart.series.length > 0 ? UM.Theme.getColor("text") : UM.Theme.getColor("text_inactive")
            wrapMode: Text.WordWrap
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                enabled: root.printerModel != null && !root.miniHasSeries && root.printerModel.temperatureChart.series.length > 0
                onClicked: {
                    if (root.printerModel != null)
                        root.popOverToggleRequested("chart");
                }
            }
        }
    }
}
