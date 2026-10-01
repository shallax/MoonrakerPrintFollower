pragma Singleton
import QtQuick 2.15
QtObject {
    // Delegates to the capture harness's Python theme backend, which
    // resolves the real cura-light theme (see tools/theme_support.py).
    function getColor(name) { return themeBackend.getColor(name) }
    function getSize(name) { return themeBackend.getSize(name) }
    function getFont(name) { return themeBackend.getFont(name) }
    function getIcon(name) { return themeBackend.getIcon(name) }
    function getImage(name) { return themeBackend.getImage(name) }
    function iconExists(name) { return themeBackend.iconExists(name) }
}
