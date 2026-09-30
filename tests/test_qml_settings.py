"""Executable qml settings contracts."""
from tests import qml_engine_support as harness

class SettingsCacheSizeTests(harness.SettingsCacheSizeTests):
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


class SettingsSeekTraceTests(harness.SettingsSeekTraceTests):
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


class SettingsCacheClearTests(harness.SettingsCacheClearTests):
    def test_the_clear_button_wipes_the_cache_root_and_reports_it(self):
        from mpf.gcode.CacheNamespaces import CACHE_DIRECTORY_NAME
        document, window = self.open_settings(tab=self.DIAGNOSTICS_TAB)
        resources = harness.sys.modules["UM.Resources"].Resources
        cache_root = harness.os.path.join(resources.getCacheStoragePath(), CACHE_DIRECTORY_NAME)
        entry = harness.os.path.join(cache_root, "index", "layer-index.json")
        harness.os.makedirs(harness.os.path.dirname(entry), exist_ok=True)
        with open(entry, "w", encoding="utf-8") as handle:
            handle.write("{}")
        self.click_item(window, self.clear_cache_button(document))
        self.assertFalse(harness.os.path.exists(cache_root),
                         "the clear button left the persistent cache on disk")
        # The result is reported where the click happened: the row's
        # status label reads the manager's own verdict.
        status = self.action.cacheStatus
        self.assertTrue(status, "the clear produced no status text")
        self.assertEqual(status, self.label_with_text(document, status).property("text"),
                         "the row never showed the clear result")


class IntervalSliderGrabTests(harness.IntervalSliderGrabTests):
    def test_linear_interval_handle_drag_moves_locally_and_regrabs_without_jump(self):
        document, window = self.open_settings(self.settings_config(
            aux_interval_ms=2500, console_interval_ms=3000))
        for caption in self.SLIDERS[1:]:
            with self.subTest(slider=caption):
                slider = self.interval_slider(document, caption)
                before = slider.property("value")
                left, right = self._painted_handle(slider)
                self._press_and_release(window, slider, (left + right) / 2.0, dx=1.0)
                self.assertLess(slider.property("value"), 60000,
                                "a one-pixel grab jumped to the maximum")
                left, right = self._painted_handle(slider)
                self._press_and_release(window, slider, (left + right) / 2.0, dx=20.0)
                moved = slider.property("value")
                self.assertGreater(moved, before)
                self.assertLess(moved, 60000,
                                "a short handle drag jumped to the maximum")
                left, right = self._painted_handle(slider)
                self._press_and_release(window, slider, (left + right) / 2.0)
                self.assertEqual(moved, slider.property("value"),
                                 "regrabbing the handle changed its value")

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
        self.press_key(window, harness.Qt.Key.Key_Right)
        self.assertEqual(anchor + 1, slider.property("value"),
                         "an arrow key never nudged one step")
        self.assertTrue(slider.property("activeFocus"),
                        "the key path dropped the slider's focus")
        self.press_key(window, harness.Qt.Key.Key_Left)
        self.assertEqual(anchor, slider.property("value"),
                         "the second arrow key never stepped back")


class SettingsTabCompositionTests(harness.SettingsPageCase):
    """Each persistent tab owns a disjoint part of the saved configuration."""

    def pages(self, document):
        types = ("ConnectionSettings", "FollowingSettings", "UploadSettings", "DiagnosticsSettings")
        result = {}
        for item in document.findChildren(harness.QQuickItem):
            name = item.metaObject().className()
            for kind in types:
                if name.startswith(kind + "_"):
                    self.assertNotIn(kind, result)
                    result[kind] = item
        self.assertEqual(set(result), set(types))
        return result

    def test_tabs_publish_disjoint_complete_configuration_blocks(self):
        document, _ = self.open_settings()
        pages = self.pages(document)
        expected = {
            "ConnectionSettings": {"url", "api_key", "feed_mode", "poll_interval_ms", "aux_interval_ms", "console_interval_ms"},
            "FollowingSettings": {"enabled", "follow_mode", "moonraker_layer_is_one_based", "path_follow", "path_smoothing", "eta_learn", "auto_preview", "show_toolhead_indicator", "z_fallback", "z_tolerance"},
            "UploadSettings": {"frontend_url", "output_format", "upload_dialog", "upload_path", "upload_start_print", "upload_remember_state", "upload_autohide_message", "power_devices", "ready_retry_interval_s", "filename_translate_input", "filename_translate_output", "filename_translate_remove"},
            "DiagnosticsSettings": {"cache_max_mb", "trace_layer", "trace_http", "seek_trace", "memory_diagnostics_log", "memory_diagnostics_trace", "camera_disabled", "software_follower_renderer"},
        }
        seen = set()
        for name, page in pages.items():
            values = page.property("values").toVariant()
            self.assertEqual(set(values), expected[name])
            self.assertFalse(seen.intersection(values), "two tabs own the same setting")
            seen.update(values)
        self.assertEqual(len(seen), 36)
        for index in (3, 1, 2, 0):
            self.show_tab(document, index)
            self.assertEqual(self.pages(document), pages, "a tab switch recreated a draft owner")

    def test_hidden_drafts_survive_switches_and_cancel_does_not_save(self):
        document, window = self.open_settings(self.settings_config(cache_max_mb=512), tab=self.DIAGNOSTICS_TAB)
        self.type_cache_size(document, "256")
        self.show_tab(document, 0)
        self.show_tab(document, self.DIAGNOSTICS_TAB)
        self.assertEqual(self.cache_size_field(document).property("text"), "256")
        self.click_item(window, self.item_with_text(document, "Cancel"))
        self.assertEqual(self.follower.applied, [])
        self.assertEqual(self.follower.config.cache_max_mb, 512)

    def test_untouched_poll_interval_survives_save_from_another_tab(self):
        document, window = self.open_settings(self.settings_config(poll_interval_ms=750), tab=2)
        self.assertFalse(document.property("pollIntervalMoved"))
        self.click_item(window, self.save_button(document))
        self.assertTrue(self.follower.applied)
        self.assertEqual(self.follower.config.poll_interval_ms, 750)
