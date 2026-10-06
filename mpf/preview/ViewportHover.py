"""Respect the host viewport's hover routing before picking scene objects."""
from PyQt6.QtCore import QPointF


class ViewportHover:
    def __init__(self):
        self._content = self._area = None

    def blocked(self, window, point, controls=()):
        if point is None:
            return False
        position = QPointF(*point)
        try:
            for item in controls:
                if item is not None and item.isVisible() and item.contains(item.mapFromScene(position)):
                    return True
            content = window.contentItem()
            if content is not self._content:
                self._content, self._area = content, None
                # Cura's viewport MouseArea is a direct child of the window
                # content, underneath its UI. Its public containsMouse value
                # follows Qt's actual hover delivery (including foreign panes).
                for item in content.childItems():
                    if (item.inherits("QQuickMouseArea") and item.property("hoverEnabled")
                            and item.width() == content.width() and item.height() == content.height()):
                        self._area = item
                        break
            return self._area is not None and not bool(self._area.property("containsMouse"))
        except (AttributeError, RuntimeError, TypeError):
            self._content = self._area = None
            return False
