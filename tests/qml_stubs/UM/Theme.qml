pragma Singleton
import QtQuick 2.15

QtObject {
    function getColor(name) {
        var palette = {
            "main_background": "#f5f5f5",
            "lining": "#cccccc",
            "border_main": "#999999",
            "text": "#202020",
            "text_inactive": "#7a7a7a",
            "primary": "#1a73e8",
            "setting_category": "#f5f5f5",
            "setting_category_hover": "#e8e8e8",
            "setting_category_active": "#dcdcdc",
            "setting_category_text": "#202020",
            "setting_category_active_text": "#1a73e8",
            "setting_control_button": "#606060",
            "error": "#c62828"
        };
        return palette[name] !== undefined ? palette[name] : "#202020";
    }
    function getSize(name) {
        var sizes = {
            "default_margin": Qt.size(16, 16),
            "narrow_margin": Qt.size(8, 8),
            "thin_margin": Qt.size(4, 4),
            "default_lining": Qt.size(1, 1),
            "default_radius": Qt.size(6, 6),
            "section_header": Qt.size(0, 40),
            "section_icon": Qt.size(16, 16),
            "standard_arrow": Qt.size(16, 16),
            "small_button_icon": Qt.size(20, 20)
        };
        return sizes[name] !== undefined ? sizes[name] : Qt.size(8, 8);
    }
    function getFont(name) {
        var size = name === "large" || name === "large_bold" ? 22 : name === "medium" || name === "medium_bold" || name === "default_bold" ? 15 : name === "small" ? 12 : 14;
        var bold = name.indexOf("bold") >= 0;
        return Qt.font({
                "family": "sans-serif",
                "pixelSize": size,
                "bold": bold
            });
    }
    function getIcon(name) {
        return "";
    }
}
