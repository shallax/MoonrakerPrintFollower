import QtQuick 2.15
import UM 1.7 as UM

// Keep UM's indicator, theme and animations. Its inherited state list puts
// hover before disabled; replace that state management with direct bindings
// so parent-enabled transitions cannot retain the disabled background.
UM.CheckBox {
    id: control
    Component.onCompleted: control.states = []

    Binding {
        target: control.indicator
        property: "color"
        value: UM.Theme.getColor(control.enabled ? "checkbox" : "checkbox_disabled")
    }
    Binding {
        target: control.indicator
        property: "border.color"
        value: UM.Theme.getColor(!control.enabled ? "checkbox_border_disabled" : control.hovered ? "checkbox_border_hover" : "checkbox_border")
    }
}
