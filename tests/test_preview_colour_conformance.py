# Copyright 2026 Thomas Pedley <moonrakerprintfollower@maintain.contact>
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under GNU GPLv3 (version 3 only); see LICENSE.
#
# Release history:
# 2026-10-20 — 6.0.0: Qualify production Python and QML colour functions against one specification.

import json
import os
from pathlib import Path
import unittest
from mpf.plate.PreviewColours import gradient

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'tests/fixtures/preview-colours.json').read_text())['cases']


class PreviewColourConformanceTests(unittest.TestCase):
    def test_python_against_shared_specification(self):
        for case in SPEC:
            with self.subTest(case=case):
                for actual, expected in zip(gradient(case['mode'], case['value'], case['bounds']), case['rgba'], strict=True):
                    self.assertAlmostEqual(actual, expected)

    def test_real_qml_javascript_against_specification_and_python_boundaries(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtQml import QQmlComponent, QQmlEngine
        app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlEngine()
        cases = list(SPEC)
        for mode in (2, 3, 4, 5):
            for bounds in ((10, 110), (4, 4), (4, 4.000099), (4, 4.000101)):
                for fraction in (-1, 0, .125, .25, .375, .375001, .5, .75, .750001, 1, 2):
                    value = bounds[0] + fraction * (bounds[1] - bounds[0])
                    cases.append(dict(mode=mode, value=value, bounds=bounds,
                                      rgba=gradient(mode, value, bounds)))
        source = '''import QtQuick 2.15
import "PreviewColours.js" as Colours
QtObject {
    property var inputs: %s
    property var samples: inputs.map(function(row) {
        var c = Colours.gradient(row.mode, row.value, row.bounds);
        return [c.r, c.g, c.b, c.a];
    })
}''' % json.dumps(cases)
        component = QQmlComponent(engine)
        component.setData(source.encode(), QUrl.fromLocalFile(str(ROOT / 'mpf/plate/Conformance.qml')))
        item = component.create()
        self.assertIsNotNone(item, [error.toString() for error in component.errors()])
        rows = item.property('samples').toVariant()
        self.assertEqual(len(rows), len(cases))
        for case, rgba in zip(cases, rows, strict=True):
            with self.subTest(case=case):
                for actual, expected in zip(rgba, case['rgba'], strict=True):
                    self.assertAlmostEqual(actual, expected, delta=1 / 65535)
        item.deleteLater()
        app.processEvents()
