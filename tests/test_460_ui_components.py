"""The 4.6.0 settings-pane surface, measured on the real engine: the
Diagnostics tab's persistent-cache-size field and its range copy, the
seek-trace toggle, the cache-clear button's effect and its status row,
and the interval sliders' grab contract.

Nothing mounted MoonrakerFollowerConfiguration.qml on an engine before
this file — the pane was pinned by source-string greps, so a renamed
property, a payload key that never reached the action, or a validator
that never reached the Save button all read green. The page mounts here
with the REAL MoonrakerFollowerMachineAction as its ``manager`` context
object (the capture tool's doubles are not reused), the controls are
clicked the way a user hits them, and the assertions read the
PrinterConfig the save wrote. The validator semantics themselves stay in
tests/test_machine_action_coverage.py; this file owns the QML wiring —
the field that feeds the validator, the copy that explains a refusal and
the button that performs the write.

The page is a Cura.MachineAction-rooted document, so ``mount`` returns it
directly. Every check that reads an item's ``visible`` runs with the tab
that owns it CURRENT: a StackLayout hides the pages it is not showing,
and an item's ``visible`` is effective — a hidden page would make every
visibility assertion a lie.
"""
from __future__ import annotations

import contextlib
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, runtime

try:
    from . import test_qml_real_engine as _parent
except ImportError:
    import test_qml_real_engine as _parent

if QT_AVAILABLE:
    from PyQt6.QtCore import QEvent, QObject, QPoint, QPointF, Qt, pyqtSignal
    from PyQt6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
    from PyQt6.QtQuick import QQuickItem, QQuickWindow

    # Stands in for Cura's DefinitionContainer: the action is imported
    # over it and the registry double passes its instances through.
    _DefinitionContainer = type("DefinitionContainer", (), {})

    class _MachineActionBase(QObject):
        """Cura's MachineAction contract: the stored key and label."""

        def __init__(self, key, label):
            super().__init__()
            self._key = key
            self._label = label

        def getKey(self):
            return self._key

        def getLabel(self):
            return self._label

    class _Registry(QObject):
        containerAdded = pyqtSignal(object)

    class _Application(QObject):
        """The host the action registers itself with. The page itself
        never touches it (its QML context object is the action)."""

        globalContainerStackChanged = pyqtSignal()

        def getContainerRegistry(self):
            return _Registry()

        def getMachineActionManager(self):
            return SimpleNamespace(addSupportedAction=Mock())

    class _Follower:
        """The facade the action reads and writes: the current printer's
        config, and whether the persistence layer accepted the write.
        ``apply_printer_config`` installs the new config the way the real
        facade does, so a saved setting republishes through the manager's
        properties."""

        def __init__(self, config):
            self.config = config
            self.identity = ("printer-a", "Printer A")
            self.applied = []
            self.apply_result = True
            self.persistence = None

        def current_printer_config(self):
            return self.config

        def current_printer_identity(self):
            return self.identity

        def apply_printer_config(self, config):
            self.applied.append(config)
            self.config = config
            return self.apply_result

    class _ActionDialogDouble(QObject):
        """The dialog the page calls close() on after a save."""

        accepted = pyqtSignal()
        rejected = pyqtSignal()
        closing = pyqtSignal()

        def close(self):
            pass

    class _CatalogDouble(QObject):
        """Cura's translation catalog, read by the themed controls."""

        def i18nc(self, _context, text):
            return text

        def i18n(self, text):
            return text

    # The context-property wrappers must outlive the documents they are
    # bound to: PyQt6 releases a wrapper when the last Python reference
    # goes, and QML then sees a null context object mid-teardown.
    _LIVE_CONTEXT_OBJECTS = []


class SettingsPageCase(_parent.RealEngineTestCase):
    """The settings pane mounted offscreen against the real machine
    action, in a real window (a windowless mount never lays out twice)."""

    DIAGNOSTICS_TAB = 3

    def setUp(self):
        super().setUp()
        # runtime() brings the Cura module stubs the plugin imports over
        # (UM.Logger, UM.Resources, UM.Preferences...); the registry and
        # container stubs are added for the action's registration path.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.qt = stack.enter_context(runtime())
        stack.enter_context(patch.dict(sys.modules, {
            "cura.MachineAction": SimpleNamespace(MachineAction=_MachineActionBase),
            "UM.Settings": SimpleNamespace(
                DefinitionContainer=SimpleNamespace(DefinitionContainer=_DefinitionContainer)),
            "UM.Settings.DefinitionContainer": SimpleNamespace(
                DefinitionContainer=_DefinitionContainer),
        }))
        self.module = self.qt.load("MoonrakerFollowerMachineAction")
        self.printer_config = self.qt.load("PrinterConfig")

    # ---- mounting ----------------------------------------------------

    def settings_config(self, **overrides):
        """A configured printer. The usable URL is what the page reads to
        make its Save button live: a placeholder URL leaves canSave false
        and every click on Save inert."""
        settings = {"url": "http://printer-a:7125/"}
        settings.update(overrides)
        return self.printer_config.PrinterConfig(**settings)

    def open_settings(self, config=None, *, tab=0, width=760, height=1100):
        """Mount the page in a window and make one tab current."""
        self.follower = _Follower(config if config is not None else self.settings_config())
        self.action = self.module.MoonrakerFollowerMachineAction(_Application(), self.follower, None)
        dialog = _ActionDialogDouble()
        catalog = _CatalogDouble()
        _LIVE_CONTEXT_OBJECTS.extend((self.action, dialog, catalog))
        context = self.engine.rootContext()
        context.setContextProperty("manager", self.action)
        context.setContextProperty("actionDialog", dialog)
        context.setContextProperty("catalog", catalog)
        document = self.mount("MoonrakerFollowerConfiguration.qml")
        window = QQuickWindow()
        window.resize(width, height)
        document.setParentItem(window.contentItem())
        document.setWidth(width)
        document.setHeight(height)
        window.show()
        self.addCleanup(self._close_page, document, window)
        self.pump(30)
        self.show_tab(document, tab)
        return document, window

    def _close_page(self, document, window):
        # Detach before the engine tears the scene down, while the
        # context-object wrappers are still referenced.
        document.setParentItem(None)
        document.deleteLater()
        window.close()
        self.pump(20)

    def show_tab(self, document, index):
        for candidate in document.findChildren(QQuickItem):
            meta = candidate.metaObject()
            name = meta.className() if meta is not None else ""
            if name == "QQuickTabBar" or name.startswith("TabRow_"):
                candidate.setProperty("currentIndex", index)
                self.pump(20)
                return
        self.fail("the settings tab bar did not mount")

    # ---- finding -----------------------------------------------------

    def item_with_text(self, document, text):
        """The control whose text is exactly this. The themed controls
        alias their text onto an inner label, so the match is narrowed to
        the one item that owns the text and knows how to be clicked."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if item.property("text") == text
                   and not item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(matches),
                         "expected exactly one control reading %r, got %d" % (text, len(matches)))
        return matches[0]

    def cache_size_field(self, document):
        """The persistent-cache-size field: the TextField on the row the
        Diagnostics copy labels 'Persistent cache size (MiB)'."""
        row = self._row_of(self.label_with_text(document, "Persistent cache size (MiB)"))
        fields = [child for child in row.childItems()
                  if child.property("maximumLength") is not None]
        self.assertEqual(1, len(fields), "the cache-size row holds no single field")
        return fields[0]

    def cache_range_copy(self, document):
        """The inline range copy on the cache-size row."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if isinstance(item.property("text"), str)
                   and item.property("text").startswith("Cache size must be between")]
        self.assertEqual(1, len(matches), "the cache-size range copy never rendered")
        return matches[0]

    def seek_trace_box(self, document):
        """The seek-timeline diagnostics toggle."""
        matches = [item for item in document.findChildren(QQuickItem)
                   if isinstance(item.property("text"), str)
                   and item.property("text").startswith("Log follower seek timelines")
                   and item.property("checked") is not None]
        self.assertEqual(1, len(matches), "the seek-trace toggle never rendered")
        return matches[0]

    def clear_cache_button(self, document):
        return self.item_with_text(document, "Clear cached downloads and indexes")

    def label_with_text(self, document, text):
        matches = [item for item in document.findChildren(QQuickItem)
                   if item.property("text") == text
                   and item.metaObject().className().startswith("Label")]
        self.assertEqual(1, len(matches),
                         "expected exactly one label reading %r, got %d" % (text, len(matches)))
        return matches[0]

    def interval_slider(self, document, label_text):
        """The slider on the row the caption labels — the caption is a
        sibling of its RowLayout in the column, the row directly below."""
        row = self._row_of(self.label_with_text(document, label_text))
        sliders = [child for child in row.childItems()
                   if "Slider" in child.metaObject().className()
                   and child.property("handle") is not None]
        self.assertEqual(1, len(sliders), "the %s row holds no single slider" % label_text)
        return sliders[0]

    @staticmethod
    def _row_of(label):
        """The RowLayout that lays the label out: the label's own parent
        when it sits inside its row (the Diagnostics cache row), else the
        first row under it in the same column (the interval captions)."""
        parent = label.parentItem()
        if parent.metaObject().className() == "QQuickRowLayout":
            return parent
        label_top = label.mapToItem(parent, QPointF(0.0, 0.0)).y()
        rows = [child for child in parent.childItems()
                if child.metaObject().className() == "QQuickRowLayout"
                and child.mapToItem(parent, QPointF(0.0, 0.0)).y() > label_top]
        if not rows:
            raise AssertionError("no row renders under the label")
        return min(rows, key=lambda row: row.mapToItem(parent, QPointF(0.0, 0.0)).y())

    # ---- driving -----------------------------------------------------

    def click_item(self, window, item, *, dx=0.5, dy=0.5):
        """A real press-and-release at a point inside the item, refused
        unless the point is actually on screen (a click outside the
        window is silently dropped and would read as a pass)."""
        scene = item.mapToScene(QPointF(item.width() * dx, item.height() * dy))
        self._assert_on_screen(window, scene)
        self._send_mouse(window, QEvent.Type.MouseButtonPress, scene,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        self._send_mouse(window, QEvent.Type.MouseButtonRelease, scene,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.pump(20)

    @staticmethod
    def _assert_on_screen(window, scene):
        if not (0.0 <= scene.x() <= window.width() and 0.0 <= scene.y() <= window.height()):
            raise AssertionError("the click at (%.1f, %.1f) is outside the %dx%d window"
                                 % (scene.x(), scene.y(), window.width(), window.height()))

    @staticmethod
    def _send_mouse(window, kind, scene, buttons, button):
        QGuiApplication.sendEvent(window, QMouseEvent(
            kind, QPointF(scene),
            QPointF(window.mapToGlobal(QPoint(int(scene.x()), int(scene.y())))),
            button, buttons, Qt.KeyboardModifier.NoModifier))

    def press_key(self, window, key):
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QGuiApplication.sendEvent(window, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))
        self.pump(20)

    def save_button(self, document):
        return self.item_with_text(document, "Save")

    def type_cache_size(self, document, text):
        """Type into the field the way a user does: the text changes, the
        binding on the field is replaced, and the page re-validates."""
        field = self.cache_size_field(document)
        field.setProperty("text", text)
        self.pump(20)


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsCacheSizeTests(SettingsPageCase):
    """The Diagnostics tab's persistent-cache-size field (4.6.0): the
    limit is per printer, the field is seeded from the stored one, and an
    out-of-range entry is refused before it reaches the settings file."""

    def test_the_cache_size_field_is_seeded_from_the_stored_limit(self):
        document, _window = self.open_settings(self.settings_config(cache_max_mb=2048),
                                               tab=self.DIAGNOSTICS_TAB)
        field = self.cache_size_field(document)
        self.assertEqual("2048", field.property("text"),
                         "the field did not seed from the stored cache limit")
        self.assertEqual("2048", self.action.settingsCacheMaxMb)
        self.assertFalse(self.cache_range_copy(document).property("visible"),
                         "a stored limit in range showed the range copy")
        self.assertTrue(document.property("canSave"))

    def test_an_out_of_range_cache_size_shows_the_copy_and_blocks_the_save(self):
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        copy = self.cache_range_copy(document)
        # Typed the way a user types it: the digits, and the junk a
        # paste or a slipping finger produces.
        for text in ("15", "4097", "not a number", ""):
            with self.subTest(text=text):
                self.type_cache_size(document, text)
                self.assertFalse(document.property("validCacheMax"), text)
                self.assertTrue(copy.property("visible"),
                                "the range copy stayed hidden for %r" % text)
                self.assertFalse(document.property("canSave"), text)
                self.assertFalse(self.save_button(document).property("enabled"),
                                 "the Save button stayed live for %r" % text)
                self.click_item(window, self.save_button(document))
                self.assertEqual([], self.follower.applied,
                                 "an invalid cache size reached the save path")
        # The boundary values are inside the range, one step out are not.
        for text, expected in (("16", True), ("4096", True), ("15", False), ("4097", False)):
            with self.subTest(text=text):
                self.type_cache_size(document, text)
                self.assertEqual(expected, document.property("validCacheMax"), text)
        self.type_cache_size(document, "512")
        self.assertFalse(copy.property("visible"),
                         "the range copy survived a return to a valid size")
        self.assertTrue(document.property("canSave"))

    def test_a_typed_cache_size_lands_in_the_printer_config(self):
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        self.type_cache_size(document, "256")
        self.assertTrue(document.property("canSave"),
                        "a valid cache size left the page unsaveable")
        self.click_item(window, self.save_button(document))
        self.assertTrue(self.follower.applied, "the Save click never reached the action")
        self.assertEqual(256, self.follower.config.cache_max_mb,
                         "the typed cache size never reached the printer config")
        self.assertEqual("256", self.action.settingsCacheMaxMb,
                         "the saved size never republished through the manager")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsSeekTraceTests(SettingsPageCase):
    """The Diagnostics tab's seek-timeline toggle (4.6.0): it shows the
    stored setting, follows a change to it, and saves as its own key."""

    def test_the_seek_trace_box_mirrors_the_stored_setting(self):
        document, _window = self.open_settings(self.settings_config(seek_trace=False),
                                               tab=self.DIAGNOSTICS_TAB)
        box = self.seek_trace_box(document)
        self.assertFalse(box.property("checked"),
                         "the toggle was ticked with the stored trace off")
        # The stored setting changing under a mounted page republishes
        # through settingsChanged — the list's toggle must follow it.
        self.follower.config.seek_trace = True
        self.action.settingsChanged.emit()
        self.pump(20)
        self.assertTrue(box.property("checked"),
                        "the toggle ignored the stored setting's change")

    def test_ticking_the_seek_trace_box_saves_the_toggle(self):
        """The box is bound to ``manager.settingsSeekTrace`` for display;
        the page's ``save()`` payload must carry the toggle through to
        the printer config, never silently clear a stored true."""

        document, window = self.open_settings(self.settings_config(seek_trace=False),
                                              tab=self.DIAGNOSTICS_TAB)
        box = self.seek_trace_box(document)
        self.click_item(window, box)
        self.assertTrue(box.property("checked"), "the click never ticked the toggle")
        self.click_item(window, self.save_button(document))
        self.assertTrue(self.follower.applied, "the Save click never reached the action")
        self.assertTrue(self.follower.config.seek_trace,
                        "the ticked seek trace never reached the printer config")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class SettingsCacheClearTests(SettingsPageCase):
    """The Diagnostics tab's cache-clear button: it wipes the persistent
    cache root (the drop that forces a full re-download and re-index) and
    reports the outcome on its own row rather than in a dialog."""

    def test_the_clear_button_wipes_the_cache_root_and_reports_it(self):
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        resources = sys.modules["UM.Resources"].Resources
        cache_root = os.path.join(resources.getCacheStoragePath(), "MoonrakerPrintFollower")
        entry = os.path.join(cache_root, "index", "layer-index.json")
        os.makedirs(os.path.dirname(entry), exist_ok=True)
        with open(entry, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.click_item(window, self.clear_cache_button(document))
        self.assertFalse(os.path.exists(cache_root),
                         "the clear button left the persistent cache on disk")
        # The result is reported where the click happened: the row's
        # status label reads the manager's own verdict.
        status = self.action.cacheStatus
        self.assertTrue(status, "the clear produced no status text")
        self.assertEqual(status, self.label_with_text(document, status).property("text"),
                         "the row never showed the clear result")


@unittest.skipUnless(QT_AVAILABLE, "Qt runtime required")
class IntervalSliderGrabTests(SettingsPageCase):
    """The interval sliders' grab contract (the 4.6.0 reviewer finding:
    the window ignored the handle's width, so the half of the painted
    handle away from the value never grabbed and the click fell through
    to the track, which jumps). A track click still jumps — that is the
    control that keeps this suite honest."""

    SLIDERS = ("Status update interval (milliseconds)",
               "Auxiliary status interval (milliseconds)",
               "Console output interval (milliseconds)")

    def _painted_handle(self, slider):
        """The handle's own geometry — where it was actually painted, not
        where a formula says it should be."""
        handle = slider.property("handle")
        return handle.x(), handle.x() + handle.width()

    def _press_and_release(self, window, slider, x, dx=0.0):
        y = slider.height() / 2
        start = slider.mapToItem(window.contentItem(), QPointF(x, y))
        end = slider.mapToItem(window.contentItem(), QPointF(x + dx, y))
        self._assert_on_screen(window, start)
        self._send_mouse(window, QEvent.Type.MouseButtonPress, start,
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        if dx:
            self._send_mouse(window, QEvent.Type.MouseMove, end,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
        self._send_mouse(window, QEvent.Type.MouseButtonRelease, end,
                         Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        self.pump(20)

    def test_every_interval_slider_grabs_both_halves_of_the_painted_handle(self):
        # The poll slider is asked for its floor: at the track's left end
        # the old centre-window is furthest from the painted handle, so
        # the far tip is the point that separates the two rules.
        document, window = self.open_settings(self.settings_config(poll_interval_ms=250))
        for caption in self.SLIDERS:
            with self.subTest(slider=caption):
                slider = self.interval_slider(document, caption)
                self.assertTrue(slider.isVisible(), caption)
                self._check_grab(window, slider)

    def _check_grab(self, window, slider):
        left, right = self._painted_handle(slider)
        floor = slider.property("from")
        anchor = slider.property("value")
        # A press on the handle is inert wherever on the handle it lands.
        for x in (left + 1.0, (left + right) / 2.0, right - 0.5):
            self._press_and_release(window, slider, x)
            self.assertEqual(anchor, slider.property("value"),
                             "a press at %.1f moved the slider off its handle" % x)
        # ... and a drag out of either tip grabs it and carries it along.
        # Setting the value directly is the harness's stand-in for the
        # operator having put the handle there.
        for x in (left + 1.0, right - 0.5):
            slider.setProperty("value", floor)
            self.pump(20)
            self._press_and_release(window, slider, x, dx=60.0)
            self.assertGreater(slider.property("value"), floor,
                               "a drag from the handle at %.1f never grabbed" % x)

    def test_a_groove_click_still_jumps_and_the_handle_click_keeps_the_steps(self):
        document, window = self.open_settings(self.settings_config(poll_interval_ms=250))
        slider = self.interval_slider(document, self.SLIDERS[0])
        left, right = self._painted_handle(slider)
        # The control: away from the handle the click must jump, so a
        # grader that reports "nothing moved" for the handle cases is
        # known to be able to see a move at all.
        self.assertEqual(0.0, slider.property("value"),
                         "the poll slider did not start at its floor")
        self._press_and_release(window, slider, right + 30.0)
        self.assertGreater(slider.property("value"), 0.0,
                           "a groove click no longer jumps the slider")
        # A handle click takes the focus; the arrow keys step one step
        # and the step survives the focus change the key path makes.
        self._press_and_release(window, slider, (left + right) / 2.0)
        self.assertTrue(slider.property("activeFocus"),
                        "a handle click never focused the slider")
        anchor = slider.property("value")
        self.press_key(window, Qt.Key.Key_Right)
        self.assertEqual(anchor + 1, slider.property("value"),
                         "an arrow key never nudged one step")
        self.assertTrue(slider.property("activeFocus"),
                        "the key path dropped the slider's focus")
        self.press_key(window, Qt.Key.Key_Left)
        self.assertEqual(anchor, slider.property("value"),
                         "the second arrow key never stepped back")
