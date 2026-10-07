import QtQuick 2.15
import UM 1.5 as UM

UM.Dialog {
    id: actionDialog
    objectName: "moonrakerToolheadSetupDialog"
    title: "Configure Moonraker"
    minimumWidth: UM.Theme.getSize("modal_window_minimum").width
    minimumHeight: UM.Theme.getSize("modal_window_minimum").height
    maximumWidth: minimumWidth * 3
    maximumHeight: minimumHeight * 3

    function openToolheadSettings() {
        loader.source = "";
        configurationManager.reset();
        loader.manager = configurationManager;
        loader.setSource("MoonrakerFollowerConfiguration.qml", {
            "initialTab": 1
        });
        show();
    }
}
