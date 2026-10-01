"""Whole-print colour limits, tool identities and retained GPU colour updates."""
from array import array
import math
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
import importlib.util
from pathlib import Path

from mpf.GCode.GCodeIndex import build_index_from_file
from mpf.GCode.IndexHydrator import hydrate_layer_from_file
from mpf.GCode.IndexCache import PersistentIndexCache
from mpf.Moonraker.MoonrakerProtocol import RemoteFileIdentity
from mpf.GCode.PlateProgress import prepare_layer, encode_layer, decode_layer
from mpf.Plate.PreviewColours import gradient, motion_colour


class ColourMetricsTests(unittest.TestCase):
    def test_multitool_metrics_survive_compact_index_cache_and_distant_hydration(self):
        data = (b";EXTRUDER_TRAIN.0.MATERIAL.DIAMETER:2\n;EXTRUDER_TRAIN.1.MATERIAL.DIAMETER:1\n"
                b"M83\nT0\n;LAYER:0\nG0 Z0.2\n;TYPE:WALL-OUTER\nG1 X10 E0.2 F600\n"
                b"T1\nG1 X20 E0.4 F1200\n;LAYER:1\nG0 Z0.5\nG1 X30 E0.6\nT0\nG1 X40 E0.3 F1800\n")
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"colours.gcode"
            path.write_bytes(data)
            full=build_index_from_file(str(path), compact=False)
            compact=build_index_from_file(str(path), compact=True)
            self.assertEqual(full.colour_ranges, compact.colour_ranges)
            self.assertEqual(full.colour_ranges["speed"], (10,30))
            self.assertAlmostEqual(full.colour_ranges["height"][0], .2)
            self.assertAlmostEqual(full.colour_ranges["height"][1], .3)
            identity=RemoteFileIdentity("colours.gcode", len(data), 1, "colours")
            cache=PersistentIndexCache(directory)
            cache.save(identity,compact)
            restored=cache.load(identity)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.filament_diameters, {0:2,1:1})
            for layer in (1,0):
                self.assertTrue(hydrate_layer_from_file(restored,str(path),layer))
                a,b=prepare_layer(full,layer),prepare_layer(restored,layer)
                self.assertEqual(a,b)
                self.assertEqual(encode_layer(a),encode_layer(decode_layer(encode_layer(a),immutable=True)))
            a=prepare_layer(full,0)
            self.assertEqual(a["Tools"],(0,0,1))
            self.assertEqual(a["speeds"],(0,10,20))
            self.assertAlmostEqual(a["widths"][1], math.pi*.2/(10*.2), places=6)
            self.assertAlmostEqual(a["widths"][2], math.pi*.4/(4*10*.2), places=6)
            scheme={"mode":0,"materials":["#ff0000","#00ff00"]}
            self.assertEqual(motion_colour(a,"WALL-OUTER",1,scheme),"#ff0000")
            self.assertEqual(motion_colour(a,"WALL-OUTER",2,scheme),"#00ff00")
            cache.save(identity,full)
            again=cache.load(identity)
            self.assertEqual(again.motion_speeds,full.motion_speeds)
            self.assertEqual(again.motion_tools,full.motion_tools)

    def test_gradients_match_cura_and_constant_ranges(self):
        self.assertEqual(gradient(2,0,(0,100)),(0,0,1,1))
        self.assertEqual(gradient(2,100,(0,100)),(1,.5,0,1))
        self.assertEqual(gradient(3,100,(0,100)),(1,1,0,1))
        self.assertEqual(gradient(5,50,(0,100)),(.5,1,.5,1))
        self.assertEqual(gradient(5,4,(4,4)),(.5,1,.5,1))
        self.assertEqual(gradient(4,4,(4,4)),(.5,.5,0,1))

    def test_metric_colours_use_the_indexed_motion_values(self):
        payload = {
            "speeds": (10, 20), "widths": (.4, .8), "layerHeight": .25,
            "colourRanges": {
                "speed": (10, 20), "height": (.2, .3),
                "width": (.4, .8), "flow": (1, 4),
            },
        }
        for mode, value, limits in ((2, 20, (10, 20)),
                                    (3, .25, (.2, .3)),
                                    (4, .8, (.4, .8)),
                                    (5, 4, (1, 4))):
            with self.subTest(mode=mode):
                self.assertEqual(
                    motion_colour(payload, "SKIN", 1, {"mode": mode}),
                    "#ff" + "".join(f"{round(channel * 255):02x}" for channel in
                                    gradient(mode, value, limits)[:3]),
                )

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_shader_colour_modes_preserve_buffers_and_pending_grey(self):
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtQuick import QQuickWindow
        from PyQt6 import sip
        from mpf.Plate.GpuFollower import GpuFollower,prepare
        from mpf.Plate.GpuStrokeMaterial import pack_shader,_pack_stdlib
        app=QGuiApplication.instance() or QGuiApplication([])
        payload={"classes":{"SKIN":[[(0,0,0),(10,0,0),(20,0,1)]]},"widths":(.4,.8),"speeds":(10,20),"Tools":(0,1)}
        packed=prepare((("current",payload),("prev",payload),("next",payload)))
        data=pack_shader(packed)
        self.assertEqual(data,_pack_stdlib(packed))
        current=next(row for row in data if row[0]=="current")
        values=array("f");values.frombytes(current[3])
        self.assertEqual(tuple(values[8:10]),(10,0))
        self.assertEqual(tuple(values[68:70]),(20,1))
        window=QQuickWindow();item=GpuFollower(window.contentItem());item._data=data
        common={"split":2,"showBase":True,"showPrevious":True,"showNext":True,"colourRanges":{"speed":(10,20)},"layerInfo":{"current":{"height":.3}}}
        item.settings=common;node=item.updatePaintNode(None,None)
        pointers=[int(row[-2 if row[0] != "current" else -1].geometry().vertexData()) for row in node._groups]
        for mode in range(6):
            item.settings=dict(common,colourScheme={"mode":mode,"materials":["#ff0000","#00ff00"]})
            item.updatePaintNode(node,None)
            self.assertEqual(pointers,[int(row[-2 if row[0] != "current" else -1].geometry().vertexData()) for row in node._groups])
            for role,_name,_motions,_raw,base,printed in node._groups:
                self.assertEqual(base._material.colour_options[0],-1 if role=="current" else mode)
                if role=="current":
                    self.assertEqual(base._material.colour.name(),"#888888")
                    self.assertEqual(printed._material.colour_options[3],.3)
        sip.delete(node);sip.delete(window)
        self.assertIsNotNone(app)

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_shared_host_preference_updates_both_directions(self):
        from PyQt6.QtCore import QObject,pyqtSignal
        from mpf.Plate.FollowerColourScheme import FollowerColourScheme
        class Preferences(QObject):
            preferenceChanged=pyqtSignal(str)
            def __init__(self): super().__init__(); self.values={}
            def addPreference(self,key,value): self.values.setdefault(key,value)
            def getValue(self,key): return self.values[key]
            def setValue(self,key,value): self.values[key]=value;self.preferenceChanged.emit(key)
        class Host:
            def __init__(self): self.prefs=Preferences()
            def getPreferences(self): return self.prefs
        host=Host();service=FollowerColourScheme(host)
        service.set_mode(5)
        self.assertEqual(host.prefs.getValue(service.PREFERENCE),5)
        host.prefs.setValue(service.PREFERENCE,0)
        self.assertEqual(service.snapshot["mode"],0)
        service.set_mode(6)
        self.assertEqual(service.snapshot["mode"],0)

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_host_models_are_not_created_during_plugin_registration(self):
        from PyQt6.QtCore import QObject,pyqtSignal
        from PyQt6.QtGui import QColor
        from mpf.Plate.FollowerColourScheme import FollowerColourScheme
        class Preferences(QObject):
            preferenceChanged=pyqtSignal(str)
            def addPreference(self,key,value):
                if key=="layerview/layer_view_type": self.value=value
            def getValue(self,key): return self.value if key=="layerview/layer_view_type" else ""
            def setValue(self,key,value): pass
        class Theme(QObject):
            themeLoaded=pyqtSignal()
            def getColor(self,key): return QColor("#123456")
        class Extruders(QObject):
            itemsChanged=pyqtSignal()
            items=[{"index":0,"color":"#abcdef"},{"index":1,"color":"#fedcba"}]
        class Host(QObject):
            engineCreatedSignal=pyqtSignal()
            _qml_engine=None
            def __init__(self):
                super().__init__(); self.prefs=Preferences();self.theme=Theme();self.extruders=Extruders();self.calls=0
            def getPreferences(self): return self.prefs
            def getTheme(self): return self.theme
            def getExtrudersModel(self):
                if self._qml_engine is None: raise AssertionError("premature extruder model construction")
                self.calls+=1;return self.extruders
        host=Host();scheme=FollowerColourScheme(host)
        self.assertEqual(host.calls,0)
        host._qml_engine=object();scheme.host_ready()
        self.assertEqual(scheme.snapshot["materials"],["#ffabcdef","#fffedcba"])
        self.assertEqual(scheme.snapshot["classes"]["SKIN"],"#ff123456")
        host.extruders.items=[{"index":0,"color":"#ffffff"},{"index":1,"color":"#000000"}]
        host.extruders.itemsChanged.emit()
        self.assertEqual(scheme.snapshot["materials"],["#ffffffff","#ff000000"])

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_qml_can_read_numeric_range_bounds_from_native_layer(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtQml import QQmlComponent,QQmlEngine
        from mpf.Plate.PlateQt import PlateLayer
        app=QGuiApplication.instance() or QGuiApplication([])
        engine=QQmlEngine()
        layer=PlateLayer({"colourRanges":{"speed":(10.,30.)},"layerHeight":.2})
        engine.rootContext().setContextProperty("layer",layer)
        component=QQmlComponent(engine)
        component.setData(b'import QtQuick 2.15; QtObject { property var bounds:layer.colourInfo.ranges.speed; property real low:bounds[0]; property real high:bounds[1] }',QUrl())
        root=component.create()
        self.assertIsNotNone(root,component.errors())
        self.assertEqual(root.property("low"),10.)
        self.assertEqual(root.property("high"),30.)
        self.assertIsNotNone(app)

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_missing_host_colour_apis_do_not_prevent_registration(self):
        from mpf.Plate.FollowerColourScheme import FollowerColourScheme
        class Preferences:
            def addPreference(self,key,value): pass
            def getValue(self,key): return 2
        class Host:
            _qml_engine=object()
            def getPreferences(self): return Preferences()
            def getTheme(self): raise AttributeError("changed host API")
        service=FollowerColourScheme(Host())
        self.assertEqual(service.snapshot["mode"],2)
        self.assertEqual(service.snapshot["materials"],["#888888"])

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_true_thickness_travel_is_one_pixel_independent_of_zoom_and_saved_width(self):
        from PyQt6.QtGui import QGuiApplication,QPen
        from PyQt6.QtQuick import QQuickWindow
        from PyQt6 import sip
        from mpf.Plate.GpuFollower import GpuFollower,prepare
        from mpf.Plate.GpuStrokeMaterial import pack_shader
        from mpf.Plate.PlateQt import _travels_pen
        app=QGuiApplication.instance() or QGuiApplication([])
        window=QQuickWindow();item=GpuFollower(window.contentItem())
        item._data=pack_shader(prepare((("current",{"travels":[[(0,0,0),(10,0,0)]]}),)))
        node=None
        for zoom in (1,5,20):
            for saved in (1,8):
                item.settings={"split":1,"showTravels":True,"trueThickness":True,"lineWidth":saved,"scale":zoom}
                node=item.updatePaintNode(node,None)
                material=node._groups[0][-1]._material
                self.assertEqual(material.width,1)
                self.assertFalse(material.physical)
                pen=QPen();pen.setWidthF(saved)
                self.assertEqual(_travels_pen(pen,{"trueThickness":True,"dpr":2}).widthF(),2)
                self.assertAlmostEqual(_travels_pen(pen,{"trueThickness":True,"backing":4,"zoom":zoom}).widthF(),4/zoom)
        sip.delete(node);sip.delete(window)
        self.assertIsNotNone(app)

    @unittest.skipUnless(importlib.util.find_spec("PyQt6") is not None, "PyQt6 is unavailable")
    def test_unavailable_colour_api_reuses_last_successful_cura_palette(self):
        from PyQt6.QtGui import QColor
        from mpf.Plate.FollowerColourScheme import FollowerColourScheme
        class Preferences:
            def __init__(self): self.values={}
            def addPreference(self,key,value): self.values.setdefault(key,value)
            def getValue(self,key): return self.values[key]
            def setValue(self,key,value): self.values[key]=value
        class Theme:
            themeLoaded=None
            def getColor(self,key): return QColor("#123456")
        class Signal:
            def connect(self,callback): pass
        class Host:
            _qml_engine=object()
            def __init__(self,prefs): self.prefs=prefs;self.theme=Theme();self.theme.themeLoaded=Signal()
            def getPreferences(self): return self.prefs
            def getTheme(self): return self.theme
        prefs=Preferences();host=Host(prefs)
        first=FollowerColourScheme(host)
        self.assertEqual(first.snapshot["classes"]["SKIN"],"#ff123456")
        self.assertTrue(prefs.getValue(first.PALETTE))
        class Unavailable(Host):
            def getTheme(self): raise AttributeError("API unavailable")
        restored=FollowerColourScheme(Unavailable(prefs))
        self.assertEqual(restored.snapshot,first.snapshot)
