import QtQuick 2.15
Item {
    property var dataSource: null
    property bool supported: false
    property bool ready: false
    property string error: ""
    property var layers: ({})
    property var settings: ({})
}
