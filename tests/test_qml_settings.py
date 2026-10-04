"""Executable qml settings contracts."""
from unittest.mock import patch

from tests import qml_engine_support as harness

class SettingsCacheSizeTests(harness.SettingsCacheSizeTests):
    def test_detection_settings_resets_the_selected_camera_baseline(self):
        from PyQt6.QtCore import QObject, QMetaObject, pyqtProperty, pyqtSlot
        from types import SimpleNamespace

        class Monitor(QObject):
            detectionBaseline = pyqtProperty(float, lambda self: 1.47, constant=True)
            detectionGlobalEnabled = pyqtProperty(bool, lambda self: True, constant=True)
            detectionEnabled = pyqtProperty(bool, lambda self: True, constant=True)
            detectionCameraReady = pyqtProperty(bool, lambda self: True, constant=True)
            detectionEditingRegions = pyqtProperty(bool, lambda self: False, constant=True)
            resets = 0

            @pyqtSlot()
            def resetPrinterDetectionTraining(self):
                self.resets += 1

        monitor = Monitor()
        document, window = self.open_settings(tab=3, width=700, height=900)
        with patch.object(self.action, "_output_plugin", SimpleNamespace(_current_monitor=lambda: monitor)):
            self.action.settingsChanged.emit()
            self.pump()
            button = document.findChild(harness.QQuickItem, "detectionResetBaselineButton")
            self.assertEqual(button.property("text"), "Reset training data")
            self.assertTrue(button.property("enabled"))
            self.click_item(window, button)
            self.assertEqual(monitor.resets, 0)
            dialog = document.findChild(QObject, "resetPrinterTrainingDialog")
            QMetaObject.invokeMethod(dialog, "accept")
            self.pump()
            self.assertEqual(monitor.resets, 1)

    def test_detection_page_has_no_redundant_model_ready_heading(self):
        document, _window = self.open_settings(tab=3)
        page = next(item for item in document.findChildren(harness.QQuickItem)
                    if item.metaObject().className().startswith("DetectionSettings_"))
        labels = [item.property("text") for item in page.findChildren(harness.QQuickItem)]
        self.assertNotIn("Local model is ready.", labels)

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
        self.activate_item(window, self.clear_cache_button(document))
        self.assertTrue(self.action.cacheStatus, "the cache-clear click did not reach the action")
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

    def test_diagnostics_is_the_last_tab(self):
        document, _window = self.open_settings()
        tabs = [item for item in document.findChildren(harness.QQuickItem)
                if item.property("text") in
                ("Connection", "Following", "Upload", "Detection", "Diagnostics")
                and item.property("checked") is not None]
        self.assertEqual([item.property("text") for item in sorted(tabs, key=lambda item: item.x())],
                         ["Connection", "Following", "Upload", "Detection", "Diagnostics"])

    def test_every_scrollable_tab_keeps_its_scrollbar_visible(self):
        # Force overflow independently of platform font wrapping, and
        # observe the settled layout rather than a fixed event budget.
        document, window = self.open_settings(height=240)
        pages = self.pages(document)
        for index, name in enumerate(("ConnectionSettings", "FollowingSettings",
                                      "UploadSettings", "DetectionSettings", "DiagnosticsSettings")):
            with self.subTest(tab=name):
                self.show_tab(document, index)
                scrollbar = pages[name].findChild(harness.QQuickItem, "settingsScrollbar")
                self.assertIsNotNone(scrollbar)
                self._wait_until(window, lambda _image, bar=scrollbar:
                    bar.property("size") < 1 and bar.property("visible"), timeout=3)
                self.assertLess(scrollbar.property("size"), 1)
                self.assertTrue(scrollbar.property("visible"))

    def pages(self, document):
        types = ("ConnectionSettings", "FollowingSettings", "UploadSettings", "DiagnosticsSettings", "DetectionSettings")
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
            "DetectionSettings": set(),
        }
        seen = set()
        for name, page in pages.items():
            values = page.property("values").toVariant()
            self.assertEqual(set(values), expected[name])
            self.assertFalse(seen.intersection(values), "two tabs own the same setting")
            seen.update(values)
        self.assertEqual(len(seen), 36)
        for index in (4, 3, 1, 2, 0):
            self.show_tab(document, index)
            self.assertEqual(self.pages(document), pages, "a tab switch recreated a draft owner")

    def test_detection_settings_only_owns_shared_setup(self):
        document, _ = self.open_settings()
        page = self.pages(document)["DetectionSettings"]
        self.assertEqual(page.property("values").toVariant(), {})
        self.assertIsNone(page.findChild(harness.QQuickItem, "detectionEnabled"))
        self.assertIsNone(page.findChild(harness.QQuickItem, "detectionThresholdSlider"))
        self.assertIsNotNone(self.item_with_text(document, "Set up local detection"))
        self.show_tab(document, 3)

    def test_detection_attribution_has_room_for_its_link(self):
        document, _ = self.open_settings(tab=3, width=700, height=600)
        attribution = document.findChild(harness.QQuickItem, "detectionModelAttribution")
        self.assertIsNotNone(attribution)
        self.assertGreater(attribution.width(), 300)
        self.assertLessEqual(attribution.property("contentHeight"), attribution.height())

    def test_both_download_offers_link_obicos_agpl_and_explain_gpl_compatibility(self):
        from types import SimpleNamespace
        document, window = self.open_settings(tab=3, width=700, height=600)
        self.action._detection = SimpleNamespace(ready=False, host_error="", busy=False,
                                                 phase="", received=0, total=0, error="",
                                                 benchmark_ms=0)
        self.action.detectionChanged.emit()
        self.pump()
        self.click_item(window, self.item_with_text(document, "Set up local detection"))
        setup = document.findChild(harness.QQuickItem, "detectionSetupLicense")
        self.assertIsNotNone(setup)
        self.assertTrue(setup.isVisible())
        offer = self.mount("DetectionOffer.qml")
        boot = offer.findChild(harness.QQuickItem, "detectionOfferLicense")
        self.assertIsNotNone(boot)
        for label in (setup, boot):
            text = label.property("text")
            self.assertIn("https://github.com/TheSpaghettiDetective/obico-server/blob/release/LICENSE", text)
            self.assertIn("GNU AGPL 3.0", text)
            self.assertIn("GNU GPL 3.0", text)
            self.assertIn("compatible under GPL 3.0 section 13", text)
            self.assertIn("AGPL requirements still applying", text)
        self.assertLessEqual(setup.property("contentHeight"), setup.height())

    def test_detection_settings_warns_before_setup(self):
        document, _ = self.open_settings(tab=3, width=700, height=600)
        warning = document.findChild(harness.QQuickItem, "detectionReliabilityWarning")
        self.assertIsNotNone(warning)
        self.assertTrue(warning.isVisible())
        self.assertIn("only an assistant, not a safety system", warning.property("text"))
        self.assertIn("not guaranteed to detect failures", warning.property("text"))
        self.assertIn("flag healthy prints", warning.property("text"))
        self.assertLessEqual(warning.property("contentHeight"), warning.height())

    def test_global_detection_toggle_stops_detection_without_changing_printer_choice(self):
        document, window = self.open_settings(tab=3, width=700, height=600)
        box = document.findChild(harness.QQuickItem, "detectionGlobalEnabledCheckbox")
        self.assertIsNotNone(box)
        self.assertFalse(box.property("enabled"))
        self.assertFalse(box.property("checked"))

        class DetectionDouble:
            ready = True
            enabled = True
            busy = False
            host_error = ""
            phase = "ready"
            received = 0
            total = 0
            error = ""
            benchmark_ms = 0

            def set_enabled(double, enabled):
                double.enabled = enabled
                self.action.detectionChanged.emit()
                return True

        self.action._detection = DetectionDouble()
        self.action.detectionChanged.emit()
        self.pump()
        self.assertTrue(box.property("enabled"))
        self.assertTrue(box.property("checked"))
        self.click_item(window, box)
        self.assertFalse(self.action._detection.enabled)
        self.assertFalse(box.property("checked"))
        self.assertFalse(self.follower.applied)
        self.click_item(window, box)
        self.assertTrue(self.action._detection.enabled)
        self.assertTrue(box.property("checked"))

    def test_detection_threshold_save_rejects_bad_pairs_without_mutating_config(self):
        document, _ = self.open_settings()
        data = {}
        for page in self.pages(document).values():
            data.update(page.property("values").toVariant())
        for warning, failure in ((-1, 75), (35, 101), (75, 75), (80, 40),
                                 (True, 75), (35.5, 75)):
            with self.subTest(warning=warning, failure=failure):
                data.update(detection_warning_threshold=warning,
                            detection_failure_threshold=failure)
                self.assertFalse(self.action.saveConfig(data))
                self.assertEqual((self.follower.config.detection_warning_threshold,
                                  self.follower.config.detection_failure_threshold), (38, 78))

    def test_diagnostics_reset_only_rearms_the_onboarding_prompts(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        document, window = self.open_settings(tab=4)
        persistence = SimpleNamespace(merge_state_global=Mock(return_value=True))
        detection = SimpleNamespace(reset_offer=Mock())
        self.follower.persistence = persistence
        self.action._detection = detection
        button = document.findChild(harness.QQuickItem, "resetOnboardingButton")
        self.assertIsNotNone(button)
        self.click_item(window, button)
        persistence.merge_state_global.assert_called_once_with({"whatsNewSeen": ""})
        detection.reset_offer.assert_called_once_with()
        self.assertIn("next Cura run", self.action.onboardingResetStatus)
        self.assertEqual(self.follower.applied, [])
        self.assertIsNotNone(self.label_with_text(document, self.action.onboardingResetStatus))

    def test_diagnostics_reset_reports_storage_failure_without_claiming_success(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        _document, _window = self.open_settings(tab=4)
        self.follower.persistence = SimpleNamespace(merge_state_global=Mock(return_value=False))
        self.action._detection = SimpleNamespace(reset_offer=Mock())
        self.action.resetOnboardingForNextRun()
        self.assertIn("Could not reset", self.action.onboardingResetStatus)
        self.action._detection.reset_offer.assert_not_called()

    def test_diagnostics_reports_the_benchmark_and_reveals_the_evidence_folder(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from PyQt6.QtGui import QDesktopServices

        document, window = self.open_settings(self.settings_config(
            detection_enabled=True), tab=4)
        detection = SimpleNamespace(ready=True, enabled=True, host_error="", busy=False,
                                    phase="ready", received=0, total=0, error="",
                                    benchmark_ms=231,
                                    evidence_root=Mock(return_value="/tmp/mpf/evidence"))
        self.action._detection = detection
        self.action.detectionChanged.emit()
        self.pump(20)
        labels = [item.property("text") for item in document.findChildren(harness.QQuickItem)
                  if isinstance(item.property("text"), str)]
        self.assertTrue(any("measured 231 ms per frame" in text for text in labels),
                        "the measured inference cost is not on the Diagnostics page")
        opened = []
        patcher = patch.object(QDesktopServices, "openUrl", staticmethod(opened.append))
        patcher.start()
        self.addCleanup(patcher.stop)
        button = document.findChild(harness.QQuickItem, "revealDetectionEvidenceButton")
        self.assertIsNotNone(button)
        self.activate_item(window, button)
        self.pump(20)
        detection.evidence_root.assert_called_once_with()
        self.assertEqual([url.toLocalFile() for url in opened], ["/tmp/mpf/evidence"])
        self.assertIn("Opened /tmp/mpf/evidence", self.action.detectionEvidenceStatus)

    def test_diagnostics_removes_shared_assets_after_disabling_every_machine(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        document, window = self.open_settings(self.settings_config(
            detection_enabled=True, detection_notify_enabled=True,
            detection_pause_enabled=True,
            detection_regions={"camera": [[[0, 0], [1, 0], [1, 1]]]}), tab=4)
        persistence = SimpleNamespace(disable_all_detection=Mock(return_value=True))
        # A complete service double: the publish below re-evaluates every
        # detection binding the pages own, and a missing attribute in a
        # Qt property getter aborts the process.
        detection = SimpleNamespace(ready=False, enabled=False, host_error="", busy=False,
                                    phase="", received=0, total=0, error="", benchmark_ms=0,
                                    remove_assets=Mock(return_value=True))
        self.follower.persistence = persistence
        self.action._detection = detection
        # The page's controls bind to detectionBusy: publish the swap
        # the way the real service's stateChanged does, so the enabled
        # state the click depends on is settled before it lands.
        self.action.detectionChanged.emit()
        self.pump(20)
        button = document.findChild(harness.QQuickItem, "resetDetectionAssetsButton")
        self.assertIsNotNone(button)
        self.assertTrue(button.property("enabled"),
                        "the remove-downloads button was disabled before the click")
        self.activate_item(window, button)
        persistence.disable_all_detection.assert_called_once_with()
        detection.remove_assets.assert_called_once_with()
        self.assertFalse(self.follower.config.detection_enabled)
        self.assertFalse(self.follower.config.detection_notify_enabled)
        self.assertFalse(self.follower.config.detection_pause_enabled)
        self.assertEqual(self.follower.config.detection_regions, {})
        self.assertIn("Removing", self.action.detectionResetStatus)
        self.action._on_detection_reset_progress()
        self.assertIn("removed", self.action.detectionResetStatus)

    def test_failed_global_detection_disable_never_deletes_assets(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        self.open_settings(tab=4)
        self.follower.persistence = SimpleNamespace(disable_all_detection=Mock(return_value=False))
        self.action._detection = SimpleNamespace(busy=False, remove_assets=Mock())
        self.action.resetDetectionAssets()
        self.assertIn("no downloads were removed", self.action.detectionResetStatus)
        self.action._detection.remove_assets.assert_not_called()

    def test_forged_detection_enable_is_refused_without_setup(self):
        document, _window = self.open_settings()
        data = {}
        for page in self.pages(document).values():
            data.update(page.property("values").toVariant())
        data["detection_enabled"] = True
        self.assertFalse(self.action.saveConfig(data))
        self.assertIn("Enable local detection in Settings", self.action.detectionRefusal)
        self.assertFalse(self.follower.config.detection_enabled)

    def test_detection_enable_cannot_be_saved_with_camera_disabled_in_same_draft(self):
        from types import SimpleNamespace
        document, _window = self.open_settings()
        data = {}
        for page in self.pages(document).values():
            data.update(page.property("values").toVariant())
        self.action._detection = SimpleNamespace(ready=True, enabled=True, host_error="", busy=False,
                                                 phase="ready", received=0, total=0, error="",
                                                 benchmark_ms=0)
        self.action._output_plugin = SimpleNamespace(
            _current_monitor=lambda: SimpleNamespace(
                _camera=SimpleNamespace(url="http://printer-a/webcam"),
                webcamStreamEnabled=True))
        data.update(detection_enabled=True, camera_disabled=True)
        self.assertFalse(self.action.saveConfig(data))
        self.assertFalse(self.follower.config.detection_enabled)
        self.action._detection.enabled = False
        data["camera_disabled"] = False
        self.assertFalse(self.action.saveConfig(data))
        self.assertIn("Enable local detection in Settings", self.action.detectionRefusal)
        self.assertFalse(self.follower.config.detection_enabled)

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
