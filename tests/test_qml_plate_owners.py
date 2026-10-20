# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify delivery, camera and overlay owners without mounting a follower face.

from PyQt6.QtCore import QMetaObject, Q_ARG, Q_RETURN_ARG, QVariant, Qt
from tests import qml_engine_support as harness


class PlateOwnerTests(harness.RealEngineTestCase):
    def call(self, owner, name, *values):
        result = QMetaObject.invokeMethod(owner, name, Q_RETURN_ARG(QVariant),
                                         *(Q_ARG(QVariant, value) for value in values))
        return result.toVariant() if hasattr(result, 'toVariant') else result

    def value(self, owner, name):
        value = owner.property(name)
        return value.toVariant() if hasattr(value, 'toVariant') else value

    def test_owner_sources_have_explicit_ports_and_one_state_writer(self):
        for name in ('PlateDeliveryState.qml', 'PlateViewState.qml'):
            source = harness.qml_source(name).read_text()
            self.assertNotRegex(source, r'\b(?:root|viewGesture|mapping|pendingCanvas|progressRasterImage)\.')
        face = harness.qml_source('PlateProgressFace.qml').read_text()
        self.assertNotRegex(face, r'delivery\.(?:transaction|delivered|standingPrefix|standingFull|assetOwner|epoch)\s*=\s*[^=]')
        for declaration in ('property var _canvasTransaction', 'property var _standingPrefix',
                            'property bool _interactionActive', 'property real _zoomAnchorX',
                            'property bool _penguinActive'):
            self.assertNotIn(declaration, face)

    def test_delivery_admits_only_current_paints_and_keeps_exact_coverage(self):
        owner = self.mount('PlateDeliveryState.qml')
        self.assertTrue(self.call(owner, 'adoptWorld', 'world-a', 'layer-a'))
        epoch = self.value(owner, 'epoch')
        self.call(owner, 'enqueue', False)
        self.assertTrue(self.call(owner, 'request'))
        self.assertFalse(self.call(owner, 'request'))
        receipt = dict(epoch=epoch, world='world-a', valid=True, from_=10,
                       split=20, paintKey='paint-a', prefixSource='file:///prefix-a.png')
        receipt['from'] = receipt.pop('from_')
        self.call(owner, 'painted', receipt, True)
        result = self.call(owner, 'deliver', 'world-a', 'paint-a', 2, 'view-a', 1)
        self.assertTrue(result['accepted'])
        self.assertTrue(self.call(owner, 'receiptMatches', 'world-a', 20, 'paint-a'))
        self.assertEqual(self.value(owner, 'frontBuffer'), 1)
        self.assertEqual(self.value(owner, 'standingPrefix')['anchor'], 2)
        prefix = dict(ready=True, source=receipt['prefixSource'], **{'from': 10})
        self.assertTrue(self.call(owner, 'coverageMatches', 10, 20, prefix, dict(ready=False), False))
        self.assertFalse(self.call(owner, 'coverageMatches', 0, 20, None, None, False))
        self.call(owner, 'enqueue', False)
        self.call(owner, 'request')
        self.call(owner, 'painted', receipt, True)
        self.assertFalse(self.call(owner, 'receiptMatches', 'world-a', 20, 'paint-a'))
        result = self.call(owner, 'deliver', 'world-a', 'paint-b', 2, 'view-a', 0)
        self.assertFalse(result['accepted'])
        self.assertTrue(self.value(owner, 'transaction')['pending'])
        self.assertTrue(self.call(owner, 'adoptWorld', 'world-b', 'layer-b'))
        self.assertIsNone(self.value(owner, 'standingPrefix'))
        self.assertIsNone(self.value(owner, 'delivered'))
        self.assertFalse(self.call(owner, 'deliver', 'world-b', 'paint-b', 3, 'view-a', 0)['accepted'])

    def test_background_receipt_clears_once_and_rejects_stale_world(self):
        owner = self.mount('PlateDeliveryState.qml')
        self.call(owner, 'adoptWorld', 'a', 'layer')
        self.assertFalse(self.call(owner, 'requestBackground', False, 'empty', 'empty'))
        self.assertEqual(self.value(owner, 'backgroundReceipt'), dict(world='empty', valid=True, ink=False))
        self.assertTrue(self.call(owner, 'requestBackground', True, 'ink', 'ink'))
        self.assertTrue(self.call(owner, 'requestBackgroundPaint', 'ink', True))
        receipt = dict(epoch=self.value(owner, 'epoch'), world='ink', valid=True, ink=True,
                       split=None, prefixSource='', **{'from': 0})
        self.call(owner, 'paintBackground', receipt, 'ink')
        self.call(owner, 'deliverBackground', 'ink')
        self.assertTrue(self.value(owner, 'pendingStanding'))
        self.assertTrue(self.call(owner, 'requestBackground', False, 'clear', 'clear'))
        self.assertTrue(self.call(owner, 'requestBackgroundPaint', 'clear', True))
        self.call(owner, 'paintBackground', dict(receipt, world='clear', ink=False), 'clear')
        self.call(owner, 'deliverBackground', 'clear')
        self.assertFalse(self.value(owner, 'pendingStanding'))
        self.assertEqual(self.value(owner, 'pendingPaints'), 2)
        self.assertEqual(self.value(owner, 'lastBackgroundKey'), 'clear')

    def test_view_owns_focal_target_and_direct_drag_without_a_renderer(self):
        owner = self.mount('PlateViewState.qml')
        owner.setProperty('viewportWidth', 400)
        owner.setProperty('viewportHeight', 400)
        owner.setProperty('plot', dict(bed=dict(offsetX=0, offsetY=0, plotWidth=400, plotHeight=400,
                                              bedXMin=0, bedYMax=200), sx=2, sy=2))
        entered = []
        owner.enterRequested.connect(lambda: entered.append(True))
        self.call(owner, 'scopeZoom', 2)
        self.assertEqual(self.value(owner, 'viewScale'), 2)
        self.assertEqual(self.value(owner, 'displayScale'), 2)
        self.assertEqual((self.value(owner, 'viewPanX'), self.value(owner, 'displayPanX')), (-200, -200))
        self.call(owner, 'press', 100, 100)
        self.call(owner, 'drag', 140, 120, True)
        self.assertEqual((self.value(owner, 'viewPanX'), self.value(owner, 'displayPanX')), (-160, -160))
        self.assertGreaterEqual(len(entered), 2)
        self.call(owner, 'wheel', 150, 120, 0, 120, False)
        self.assertEqual(self.value(owner, 'viewScale'), 2.5)
        self.assertTrue(self.value(owner, 'zoomRunning'))
        self.call(owner, 'stopZoom')
        self.call(owner, 'activate')
        self.assertTrue(self.value(owner, 'interactionActive'))
        self.call(owner, 'end')
        self.assertFalse(self.value(owner, 'interactionActive'))
        self.call(owner, 'fitNow')
        self.assertEqual((self.value(owner, 'viewScale'), self.value(owner, 'displayScale')), (1, 1))

    def test_overlay_sequence_is_independent_of_toolpath_and_colour_state(self):
        owner = self.mount('PlatePenguinOverlay.qml')
        owner.setProperty('compact', True)
        self.call(owner, 'recordClick', int(Qt.MouseButton.LeftButton.value))
        self.assertEqual(self.value(owner, 'clickCount'), 0)
        owner.setProperty('compact', False)
        for button in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            for _ in range(5): self.call(owner, 'recordClick', int(button.value))
        self.assertTrue(self.value(owner, 'active'))
        self.assertEqual(self.value(owner, 'progress'), 0)
        self.assertEqual(self.value(owner, 'clickCount'), 0)
        self.call(owner, 'recordClick', int(Qt.MouseButton.LeftButton.value))
        self.assertEqual(self.value(owner, 'clickCount'), 0)
