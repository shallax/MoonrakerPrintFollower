# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Selected release history (newest first):
# 2026-10-18 — 6.0.0: Load fixture QML from a real local file without holding the loader GIL.
# 2026-10-18 — 6.0.0: Prove real Preview card rendering and clicks survive stage replacement.

"""Exercise Cura's Loader/Row reparenting with real QML, pixels and input."""
from pathlib import Path
import tempfile

from tests import qml_engine_support as harness

if harness.QT_AVAILABLE:
    from PyQt6.QtCore import QPointF, Qt, QUrl
    from PyQt6.QtQml import QQmlComponent
    from PyQt6.QtQuick import QQuickItem, QQuickWindow
    from PyQt6.QtTest import QTest


class PreviewCardLifecycleTests(harness.RealEngineTestCase):
    def test_card_remains_drawn_and_clickable_after_repeated_stage_replacement(self):
        directory = tempfile.TemporaryDirectory(prefix="preview-card-lifecycle-")
        self.addCleanup(directory.cleanup)
        source = Path(directory.name) / "preview-card-lifecycle.qml"
        component = QQmlComponent(self.engine)
        source.write_bytes(b'''import QtQuick 2.15
Item {
    id: root
    width: 1600; height: 900
    property bool preview: true
    Loader {
        objectName: "stageLoader"
        anchors.fill: parent
        sourceComponent: root.preview ? previewStage : prepareStage
    }
    Component { id: previewStage; Item { Row { objectName: "row"; x: 600; y: 800 } } }
    Component { id: prepareStage; Item { Row { objectName: "row"; x: 500; y: 750 } } }
}''')
        # loadUrl releases the GIL, allowing loader-thread diagnostics to
        # reach the Python message handler; synchronous setData does not.
        component.loadUrl(QUrl.fromLocalFile(str(source)))
        root = component.create()
        self.assertIsNotNone(root, harness.qml_error_report(component))
        self.addCleanup(self._delete_document, root)
        window = QQuickWindow()
        window.resize(1600, 900)
        root.setParentItem(window.contentItem())
        window.show()
        self.addCleanup(self._destroy_window, root, window)
        self._shot_windows = [window]
        host = self.mount("MoonrakerPreviewCardPanelHost.qml")
        card = self.find(host, "moonrakerPreviewCard")
        loader = self.find(root, "stageLoader")

        def attach():
            # The live Loader item, not an old row awaiting deferred deletion.
            host.setParentItem(self.find(loader.property("item"), "row"))

        loader.loaded.connect(attach)
        attach()
        for key, value in {"gateVisible": True, "cardExpanded": True,
                           "hasToolpath": True, "followingEnabled": True,
                           "previewStageActive": True,
                           "configuredForFollowing": True}.items():
            card.setProperty(key, value)
        clicked = []
        card.pauseClicked.connect(lambda: clicked.append(True))
        buttons = [item for item in card.findChildren(QQuickItem)
                   if item.property("text") == "Detach" and "Button" in item.metaObject().className()]
        self.assertTrue(buttons)
        button = buttons[0]

        def painted(image):
            # Count visible ink in the actual card bounds, independently of
            # QQuickItem.visible (which stays true throughout the regression).
            rect = self.rect(card, window.contentItem()).toAlignedRect()
            crop = image.copy(rect)
            return sum(crop.pixelColor(x, y).lightness() < 245
                       for y in range(0, crop.height(), 4)
                       for x in range(0, crop.width(), 4)) > 100

        for cycle in range(4):
            with self.subTest(cycle=cycle):
                if cycle:
                    card.setProperty("gateVisible", False)
                    root.setProperty("preview", False)
                    self._pump_ms(50)
                    self.assertFalse(card.property("panelVisible"))
                    # Exercise both model/stage event orders.
                    if cycle % 2:
                        card.setProperty("gateVisible", True)
                    root.setProperty("preview", True)
                    card.setProperty("gateVisible", True)
                # Drain Loader teardown and the next animation tick before
                # sampling: the old scene can still paint its last frame.
                self._pump_ms(80)
                image = self._wait_until(window, painted, timeout=2)
                self.assertTrue(painted(image), "visible card has no rendered pixels after stage change")
                point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=point)
                self.pump()
                self.assertEqual(len(clicked), cycle + 1, "rendered Detach button rejected real input")
