"""Unit coverage for the settings machine action: the validation verbs,
the save path's refusals, the connection probe's status machine, the
cache-clear slot and the migration mirror.

The probe is driven twice: once against a real local Moonraker stub
through the production HTTP transport (the wiring, the endpoints, the
api-key header) and once through a scripted transport, which reaches
the states a live server cannot produce — a request the transport
refuses to start, a payload whose shape breaks the parse.

Every statement in the file is reached here; nothing is left to the
harness scenario legs.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch

from qt_runtime_support import QT_AVAILABLE, PipeSafeHandler, runtime

# Stands in for Cura's DefinitionContainer: the action is imported over
# it, and the containers the registry tests emit are its instances.
_DefinitionContainer = type("DefinitionContainer", (), {})

if QT_AVAILABLE:
    from PyQt6.QtCore import QObject, pyqtSignal

    class _MachineActionBase(QObject):
        """Cura's MachineAction contract: the stored key and label, and the
        public reset() slot that calls the protected _reset() hook."""

        def __init__(self, key, label):
            super().__init__()
            self._key = key
            self._label = label

        def getKey(self):
            return self._key

        def getLabel(self):
            return self._label

        def reset(self):
            self._finished = False
            self._reset()

    class _Registry(QObject):
        """The container registry with a real signal, so the action's
        containerAdded slot is connected and driven as Cura drives it."""

        containerAdded = pyqtSignal(object)

    class _Application(QObject):
        globalContainerStackChanged = pyqtSignal()

        def __init__(self, registry=None, manager=None):
            super().__init__()
            self._registry = registry if registry is not None else _Registry()
            self._manager = manager if manager is not None else SimpleNamespace(
                addSupportedAction=Mock())

        def getContainerRegistry(self):
            return self._registry

        def getMachineActionManager(self):
            return self._manager

    class _BrokenStackSignal:
        """A host whose stack signal refuses the connection: the action
        must still register (the settings page hangs off this object)."""

        def __init__(self, registry):
            self.globalContainerStackChanged = SimpleNamespace(connect=self._refuse)
            self._registry = registry

        @staticmethod
        def _refuse(_handler):
            raise RuntimeError("this host has no stack signal")

        def getContainerRegistry(self):
            return self._registry


class _Follower:
    """The follower facade the action reads: the current printer's config,
    its identity, and whether persistence accepted a write."""

    def __init__(self, config, *, identity=("A", "Printer A"), apply_result=True,
                 persistence=None):
        self.config = config
        self.identity = identity
        self.apply_result = apply_result
        self.persistence = persistence
        self.applied = []

    def current_printer_config(self):
        return self.config

    def current_printer_identity(self):
        return self.identity

    def apply_printer_config(self, config):
        self.applied.append(config)
        if isinstance(self.apply_result, BaseException):
            raise self.apply_result
        return self.apply_result


class _Output:
    def __init__(self, error=None):
        self.error = error
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1
        if self.error is not None:
            raise self.error


class _Persistence:
    def __init__(self, record=None):
        self.record = dict(record or {})
        self.updates = []

    def migration_record(self):
        return dict(self.record)

    def set_migration_record(self, update):
        self.record.update(update)
        self.updates.append(dict(update))


class _ProbeTransport:
    """The probe transport double: records what the action asked for and
    can refuse to start a request (the live transport refuses only while
    a same-lane reply is running, which the probe's replace=True skips)."""

    def __init__(self, *, start=True):
        self.start = start
        self.configures = []
        self.requests = []
        self.cancelled = []

    def configure(self, base_url, api_key):
        self.configures.append((base_url, api_key))
        return True

    def send_json(self, owner, channel, method, path, callback, **options):
        self.requests.append(SimpleNamespace(owner=owner, channel=channel, method=method,
                                            path=path, callback=callback, options=options))
        return self.start

    def cancel_owner(self, owner):
        self.cancelled.append(owner)

    def close(self):
        pass


class _RaisingGet:
    """A payload whose read blows up: the transport only ever hands over
    parsed JSON objects, so this guard is unreachable from the wire."""

    def get(self, _key):
        raise ValueError("payload is not a mapping")


@unittest.skipUnless(QT_AVAILABLE, "Install PyQt6 to run the machine-action suite")
class MachineActionCase(unittest.TestCase):
    """One action per test, built the way Cura builds it: the host base
    class, the registry signal and the follower facade."""

    def setUp(self):
        self.context = runtime()
        self.qt = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.addCleanup(self.qt.events)
        self.host = patch.dict(sys.modules, {
            "cura.MachineAction": SimpleNamespace(MachineAction=_MachineActionBase),
            "UM.Settings": SimpleNamespace(
                DefinitionContainer=SimpleNamespace(DefinitionContainer=_DefinitionContainer)),
            "UM.Settings.DefinitionContainer": SimpleNamespace(
                DefinitionContainer=_DefinitionContainer),
        })
        self.host.start()
        self.addCleanup(self.host.stop)
        self.module = self.qt.load("MoonrakerFollowerMachineAction")
        self.printer_config = self.qt.load("PrinterConfig")
        self.log = self.module.Logger.log

    # ---- construction helpers ---------------------------------------

    def _action(self, follower=None, *, application=None, output_plugin=None, transport=None):
        module = self.module
        application = application if application is not None else _Application()
        follower = follower if follower is not None else self._follower()

        def build():
            return module.MoonrakerFollowerMachineAction(application, follower, output_plugin)

        if transport is None:
            action = build()
        else:
            # The action owns its probe transport; the double stands in
            # for it at the construction seam.
            with patch.object(module, "MoonrakerHttpTransport", lambda _parent: transport):
                action = build()
        self.addCleanup(action.deleteLater)
        return action

    def _follower(self, config=None, **kwargs):
        return _Follower(config if config is not None else self.printer_config.PrinterConfig(),
                         **kwargs)

    def _container(self, container_id="machine-1", container_type="machine"):
        definition_container = self.module.DefinitionContainer

        class _Container(definition_container):
            def getId(self):
                return container_id

            def getMetaDataEntry(self, key):
                return container_type if key == "type" else None

        return _Container()

    @staticmethod
    def _params(**overrides):
        # The dialog's payload: sliders deliver JS numbers, the text
        # fields digits-as-text; both are accepted.
        params = {
            "enabled": True,
            "url": "http://printer-a:7125/",
            "api_key": " k ",
            "poll_interval_ms": 2500,
            "aux_interval_ms": 1000.0,
            "console_interval_ms": "750",
            "z_tolerance": "0.050",
            "cache_max_mb": "1024",
            "ready_retry_interval_s": 1.0,
            "follow_mode": "lookahead",
            "feed_mode": "http",
            "filename_translate_input": "ab",
            "filename_translate_output": "cd",
            "filename_translate_remove": "e",
        }
        params.update(overrides)
        return params

    def _wait(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.qt.events(10)
        return predicate()

    def _moonraker_server(self, *, info=None, objects=None, status=200):
        """A Moonraker stub answering the probe's two endpoints; returns
        its base URL and every (path, api-key) it was asked for."""
        seen = []
        payloads = {
            "/server/info": info if info is not None else {
                "result": {"moonraker_version": "v1.30.0", "klippy_state": "ready"}},
            "/printer/objects/list": objects if objects is not None else {
                "result": {"objects": ["print_stats", "virtual_sdcard", "gcode_move",
                                       "motion_report"]}},
        }

        class Handler(PipeSafeHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("X-Api-Key")))
                body = payloads.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = json.dumps(body).encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:%d" % server.server_port, seen

    # ---- registration ------------------------------------------------

    def test_container_additions_register_machine_definitions_only(self):
        registry = _Registry()
        manager = SimpleNamespace(addSupportedAction=Mock())
        action = self._action(application=_Application(registry, manager))

        registry.containerAdded.emit(object())
        registry.containerAdded.emit(self._container("material-1", "material"))
        manager.addSupportedAction.assert_not_called()

        registry.containerAdded.emit(self._container("machine-1"))
        manager.addSupportedAction.assert_called_once_with("machine-1", action.KEY)

    def test_a_refusing_action_manager_is_logged_not_raised(self):
        registry = _Registry()
        manager = SimpleNamespace(
            addSupportedAction=Mock(side_effect=RuntimeError("no action manager")))
        self._action(application=_Application(registry, manager))

        registry.containerAdded.emit(self._container())

        self.assertEqual(self.log.call_args[0][0], "w")
        self.assertIn("unable to register machine action", self.log.call_args[0][1])

    def test_a_host_without_the_stack_signal_still_builds_the_action(self):
        application = SimpleNamespace(getContainerRegistry=lambda: SimpleNamespace(
            containerAdded=SimpleNamespace(connect=lambda _fn: None)))
        action = self._action(application=application)
        self.assertEqual(action.testStatus, "Not tested")

    def test_a_stack_signal_that_refuses_the_connection_is_survived(self):
        application = _BrokenStackSignal(SimpleNamespace(
            containerAdded=SimpleNamespace(connect=lambda _fn: None)))
        action = self._action(application=application)
        self.assertEqual(action.testStatus, "Not tested")

    def test_a_machine_switch_resets_the_connection_verdict(self):
        transport = _ProbeTransport()
        application = _Application()
        action = self._action(application=application, transport=transport)
        action.testConnection("printer-a:7125", "k")
        self.assertTrue(action.testBusy)

        statuses = []
        busy_states = []
        settings = []
        action.testStatusChanged.connect(lambda: statuses.append(action.testStatus))
        action.testBusyChanged.connect(lambda: busy_states.append(action.testBusy))
        action.settingsChanged.connect(lambda: settings.append(True))

        application.globalContainerStackChanged.emit()

        self.assertEqual(action.testStatus, "Not tested")
        self.assertFalse(action.testBusy)
        self.assertEqual(transport.cancelled, ["probe"])
        self.assertEqual(statuses[-1], "Not tested")
        self.assertEqual(busy_states[-1], False)
        self.assertEqual(settings, [True])

    def test_the_cura_reset_hook_returns_the_page_to_its_open_state(self):
        transport = _ProbeTransport()
        action = self._action(transport=transport)
        action.testConnection("printer-a:7125", "k")
        statuses = []
        settings = []
        action.testStatusChanged.connect(lambda: statuses.append(action.testStatus))
        action.settingsChanged.connect(lambda: settings.append(True))

        action.reset()  # Cura's slot: the base class calls the _reset hook

        self.assertEqual(action.testStatus, "Not tested")
        self.assertFalse(action.testBusy)
        self.assertEqual(transport.cancelled, ["probe"])
        self.assertEqual(statuses[-1], "Not tested")
        self.assertEqual(settings, [True])

    # ---- the settings surface ---------------------------------------

    def test_the_live_settings_properties_mirror_the_config(self):
        config = self.printer_config.PrinterConfig(
            enabled=False, url="http://printer-b:7125", api_key="secret",
            poll_interval_ms=2500, follow_mode="window", moonraker_layer_is_one_based=False,
            path_follow=False, path_smoothing=False, eta_learn=True, auto_preview=True,
            show_toolhead_indicator=False, trace_layer=True, memory_diagnostics_log=True,
            camera_disabled=True, memory_diagnostics_trace=True, aux_interval_ms=1500,
            console_interval_ms=750, feed_mode=self.printer_config.FeedMode.HTTP,
            trace_http=True, z_fallback=False, z_tolerance=0.012)
        action = self._action(self._follower(config))

        self.assertFalse(action.settingsEnabled)
        self.assertEqual(action.settingsUrl, "http://printer-b:7125")
        self.assertEqual(action.settingsApiKey, "secret")
        self.assertEqual(action.settingsPollInterval, "2500")
        self.assertEqual(action.settingsFollowMode, "window")
        self.assertFalse(action.settingsLayerOneBased)
        self.assertFalse(action.settingsPathFollow)
        self.assertFalse(action.settingsPathSmoothing)
        self.assertTrue(action.settingsEtaLearn)
        self.assertTrue(action.settingsAutoPreview)
        self.assertFalse(action.settingsToolheadIndicator)
        self.assertTrue(action.settingsTraceLayer)
        self.assertTrue(action.settingsMemoryDiagnosticsLog)
        self.assertTrue(action.settingsCameraDisabled)
        self.assertTrue(action.settingsMemoryDiagnosticsTrace)
        self.assertEqual(action.settingsAuxInterval, "1500")
        self.assertEqual(action.settingsConsoleInterval, "750")
        self.assertEqual(action.settingsTransportMode, "http")
        self.assertTrue(action.settingsTraceHttp)
        self.assertFalse(action.settingsZFallback)
        self.assertEqual(action.settingsZTolerance, "0.012")

    def test_the_output_settings_properties_mirror_the_config(self):
        config = self.printer_config.PrinterConfig(
            frontend_url="http://ui:8080", output_format="ufp", upload_dialog=False,
            upload_path="sub/dir", upload_start_print=True, upload_remember_state=True,
            upload_autohide_message=False, power_devices="printer-a", ready_retry_interval_s=2.5,
            filename_translate_input="a", filename_translate_output="b",
            filename_translate_remove="c")
        action = self._action(self._follower(config))

        self.assertEqual(action.settingsFrontendUrl, "http://ui:8080")
        self.assertEqual(action.settingsOutputFormat, "ufp")
        self.assertFalse(action.settingsUploadDialog)
        self.assertEqual(action.settingsUploadPath, "sub/dir")
        self.assertTrue(action.settingsUploadStartPrint)
        self.assertTrue(action.settingsUploadRememberState)
        self.assertFalse(action.settingsUploadAutohideMessage)
        self.assertEqual(action.settingsPowerDevices, "printer-a")
        self.assertEqual(action.settingsReadyRetryInterval, "2.5")
        self.assertEqual(action.settingsTranslateInput, "a")
        self.assertEqual(action.settingsTranslateOutput, "b")
        self.assertEqual(action.settingsTranslateRemove, "c")

    def test_the_identity_and_the_transport_help_text(self):
        config = self.printer_config.PrinterConfig()
        action = self._action(self._follower(config, identity=("machine-9", "Printer Zed")))

        self.assertEqual(action.machineName, "Printer Zed")
        self.assertIn("WebSocket", action.transportStatus)
        self.assertIn("HTTP polling", action.transportStatus)
        self.assertIn("always use HTTP", action.transportStatus)
        # A pre-enum record: the property still names its transport.
        config.feed_mode = "websocket"
        self.assertEqual(action.settingsTransportMode, "websocket")

    # ---- the validation verbs ---------------------------------------

    def test_valid_url_accepts_only_http_hosts(self):
        action = self._action()
        for value in ("printer-a:7125", "http://printer-a:7125/", "https://printer-a:7125",
                      "http://127.0.0.1:7125"):
            with self.subTest(url=value):
                self.assertTrue(action.validUrl(value))
        for value in ("", "   ", "http://", "https://", "http://\x01printer-a"):
            with self.subTest(url=value):
                self.assertFalse(action.validUrl(value))
        # A malformed bracketed host used to raise out of urlsplit —
        # a live slot exception that aborts Cura: refused instead.
        self.assertFalse(action.validUrl("http://["))
        self.assertFalse(action.insecureKeyWarning("http://[", "key"))

    def test_an_insecure_key_warning_needs_cleartext_off_loopback(self):
        action = self._action()
        # No key: nothing to leak.
        self.assertFalse(action.insecureKeyWarning("http://printer-a:7125", ""))
        self.assertFalse(action.insecureKeyWarning("http://printer-a:7125", "   "))
        # The key never rides cleartext to a loopback host.
        for url in ("https://printer-a:7125", "http://localhost:7125",
                    "http://LOCALHOST:7125", "http://127.0.0.1:7125", "http://[::1]:7125"):
            with self.subTest(url=url):
                self.assertFalse(action.insecureKeyWarning(url, "key"))
        # A plain-http LAN printer: the key goes out in the clear.
        for url in ("http://printer-a:7125", "http://192.168.1.50:7125"):
            with self.subTest(url=url):
                self.assertTrue(action.insecureKeyWarning(url, "key"))

    def test_the_interval_validators_apply_their_bounds(self):
        action = self._action()
        for value in ("250", 250, 3_600_000, " 3600000 "):
            with self.subTest(accepted=value):
                self.assertTrue(action.validPollInterval(value))
        # The legacy text field's digits only: a decimal slider value is
        # the JS-number path, refused here and coerced by saveConfig.
        for value in ("249", "250.0", 3_600_001, "", "soon", None):
            with self.subTest(refused=value):
                self.assertFalse(action.validPollInterval(value))
        for verb in (action.validAuxInterval, action.validConsoleInterval):
            for value in ("250", 60_000, 1000):
                with self.subTest(verb=verb.__name__, accepted=value):
                    self.assertTrue(verb(value))
            for value in ("249", "60001", "60.0", None):
                with self.subTest(verb=verb.__name__, refused=value):
                    self.assertFalse(verb(value))

    def test_the_tolerance_and_retry_validators_apply_their_bounds(self):
        action = self._action()
        for value in ("0.005", 0.25, "0.040"):
            with self.subTest(accepted=value):
                self.assertTrue(action.validZTolerance(value))
        for value in ("0.004", "0.251", "soon", None):
            with self.subTest(refused=value):
                self.assertFalse(action.validZTolerance(value))

        for value in ("0.1", 60.0, "30"):
            with self.subTest(accepted=value):
                self.assertTrue(action.validRetryInterval(value))
        for value in ("0.09", "60.1", "soon", None):
            with self.subTest(refused=value):
                self.assertFalse(action.validRetryInterval(value))

        for value in ("16", 512, "4096"):
            with self.subTest(accepted=value):
                self.assertTrue(action.validCacheMax(value))
        for value in ("15", "4097", "soon", None):
            with self.subTest(refused=value):
                self.assertFalse(action.validCacheMax(value))

    def test_translation_validation_pairs_the_two_sides(self):
        action = self._action()
        self.assertTrue(action.validTranslation("ab", "cd"))
        self.assertTrue(action.validTranslation("", ""))
        self.assertTrue(action.validTranslation(None, None))
        self.assertFalse(action.validTranslation("ab", "c"))
        self.assertFalse(action.validTranslation("a", None))

    # ---- the save path ----------------------------------------------

    def test_a_complete_save_lands_every_field(self):
        follower = self._follower()
        output = _Output()
        action = self._action(follower, output_plugin=output)
        changed = []
        action.settingsChanged.connect(lambda: changed.append(True))

        self.assertTrue(action.saveConfig(self._params()))

        self.assertEqual(changed, [True])
        self.assertEqual(output.refreshes, 1)
        saved = follower.applied[-1]
        self.assertEqual(saved.url, "http://printer-a:7125")  # the trailing slash is gone
        self.assertEqual(saved.api_key, "k")  # the dialog's padding is not
        self.assertEqual(saved.poll_interval_ms, 2500)
        self.assertEqual(saved.aux_interval_ms, 1000)
        self.assertEqual(saved.console_interval_ms, 750)
        self.assertAlmostEqual(saved.z_tolerance, 0.05)
        self.assertEqual(saved.cache_max_mb, 1024)
        self.assertAlmostEqual(saved.ready_retry_interval_s, 1.0)
        self.assertEqual(saved.follow_mode, "lookahead")
        self.assertEqual(saved.feed_mode.value, "http")
        self.assertEqual(saved.filename_translate_input, "ab")
        self.assertEqual(saved.filename_translate_output, "cd")
        self.assertEqual(saved.filename_translate_remove, "e")

    def test_a_save_payload_that_is_not_a_mapping_is_refused(self):
        follower = self._follower()
        action = self._action(follower)

        self.assertFalse(action.saveConfig(["not", "a", "mapping"]))
        self.assertFalse(action.saveConfig("not a mapping"))

        self.assertEqual(follower.applied, [])

    def test_a_pyqt5_style_variant_payload_is_unwrapped(self):
        follower = self._follower()
        action = self._action(follower)
        # PyQt6 hands a QML object over as a plain dict and its QVariant
        # has no toVariant(); the host that delivers the wrapped form is
        # what this stand-in carries.
        payload = SimpleNamespace(toVariant=lambda: self._params())

        self.assertTrue(action.saveConfig(payload))

        self.assertEqual(follower.applied[-1].url, "http://printer-a:7125")

    def test_every_save_refusal_names_its_reason(self):
        follower = self._follower()
        action = self._action(follower)
        changed = []
        action.settingsChanged.connect(lambda: changed.append(True))
        cases = (
            ("unparsable poll interval", {"poll_interval_ms": "soon"}, "unparsable field"),
            ("poll interval below the floor", {"poll_interval_ms": 249}, "poll interval out of range"),
            ("poll interval above the ceiling", {"poll_interval_ms": 3_600_001},
             "poll interval out of range"),
            ("aux interval above the ceiling", {"aux_interval_ms": 60_001},
             "aux/console interval out of range"),
            ("console interval below the floor", {"console_interval_ms": "249"},
             "aux/console interval out of range"),
            ("z tolerance above the ceiling", {"z_tolerance": "0.251"},
             "z tolerance out of range"),
            ("z tolerance below the floor", {"z_tolerance": "0.004"},
             "z tolerance out of range"),
            ("retry interval above the ceiling", {"ready_retry_interval_s": "60.1"},
             "retry interval out of range"),
            ("retry interval below the floor", {"ready_retry_interval_s": "0.05"},
             "retry interval out of range"),
            ("an enabled connection with an unusable URL", {"url": "http://"},
             "the URL is not usable"),
            ("translation sides of different lengths", {"filename_translate_input": "abc"},
             "translate input/output lengths differ"),
        )
        for name, overrides, reason in cases:
            with self.subTest(case=name):
                self.log.reset_mock()
                self.assertFalse(action.saveConfig(self._params(**overrides)))
                self.assertEqual(self.log.call_args[0][0], "w")
                self.assertIn(reason, self.log.call_args[0][1])

        self.assertEqual(follower.applied, [])
        self.assertEqual(changed, [])
        # The same payloads pass once the refused field is sane: the
        # validators refuse a field, never the whole dialog.
        self.assertTrue(action.saveConfig(self._params()))

    def test_a_disabled_connection_needs_no_url(self):
        follower = self._follower()
        action = self._action(follower)

        self.assertTrue(action.saveConfig(self._params(enabled=False, url="")))

        self.assertEqual(follower.applied[-1].url, "http://")
        self.assertFalse(follower.applied[-1].enabled)

    def test_unknown_modes_keep_a_validated_value(self):
        follower = self._follower(self.printer_config.PrinterConfig(
            feed_mode=self.printer_config.FeedMode.HTTP))
        action = self._action(follower)

        self.assertTrue(action.saveConfig(self._params(follow_mode="bogus", feed_mode="")))

        saved = follower.applied[-1]
        self.assertEqual(saved.follow_mode, "exact")
        self.assertEqual(saved.feed_mode.value, "http")
        # A missing feed_mode is the same fallback as an unknown one.
        self.assertTrue(action.saveConfig(self._params(feed_mode="carrier-pigeon")))
        self.assertEqual(follower.applied[-1].feed_mode.value, "http")

    def test_a_refused_persistence_write_is_not_reported_as_saved(self):
        follower = self._follower(apply_result=False)
        action = self._action(follower)
        changed = []
        action.settingsChanged.connect(lambda: changed.append(True))

        self.assertFalse(action.saveConfig(self._params()))

        self.assertEqual(changed, [])
        self.assertEqual(self.log.call_args[0][0], "w")
        self.assertIn("settings file could not be written", self.log.call_args[0][1])

    def test_a_facade_that_returns_nothing_still_counts_as_a_save(self):
        follower = self._follower(apply_result=None)
        action = self._action(follower)

        self.assertTrue(action.saveConfig(self._params()))

    def test_a_failing_output_refresh_does_not_lose_the_save(self):
        follower = self._follower()
        output = _Output(RuntimeError("no output device"))
        action = self._action(follower, output_plugin=output)

        self.assertTrue(action.saveConfig(self._params()))

        self.assertEqual(output.refreshes, 1)
        self.assertIn("output refresh after save failed", self.log.call_args[0][1])

    def test_an_unexpected_failure_is_reported_not_raised(self):
        follower = self._follower(apply_result=RuntimeError("broken facade"))
        action = self._action(follower)

        self.assertFalse(action.saveConfig(self._params()))

        self.assertEqual(self.log.call_args[0][0], "e")
        self.assertIn("unable to save machine settings", self.log.call_args[0][1])

    # ---- the connection test ----------------------------------------

    def test_a_connection_test_probes_server_info_then_the_object_list(self):
        transport = _ProbeTransport()
        action = self._action(transport=transport)

        action.testConnection("printer-a:7125/", " k ")

        self.assertEqual(transport.configures, [("http://printer-a:7125", "k")])
        self.assertEqual(action.testStatus, "Testing connection…")
        self.assertTrue(action.testBusy)
        self.assertEqual(len(transport.requests), 1)
        first = transport.requests[0]
        self.assertEqual((first.owner, first.channel, first.method), ("probe", "server-info", "GET"))
        self.assertEqual(first.path, "http://printer-a:7125/server/info")
        self.assertTrue(first.options["replace"])
        self.assertEqual(first.options["category"], "discovery")

        first.callback({"result": {"moonraker_version": "v1.30.0", "klippy_state": "ready"}}, None)

        second = transport.requests[1]
        self.assertEqual((second.channel, second.method), ("objects", "GET"))
        self.assertEqual(second.path, "http://printer-a:7125/printer/objects/list")

        second.callback({"result": {"objects": ["print_stats", "virtual_sdcard", "gcode_move",
                                                "motion_report"]}}, None)

        self.assertEqual(action.testStatus,
                         "Connected — Moonraker v1.30.0; Klippy ready; "
                         "required print objects available; live-position refinement available")
        self.assertFalse(action.testBusy)

    def test_the_object_verdict_names_what_is_missing(self):
        cases = (
            ({"result": {"software_version": "1.2.3", "klippy_state": "startup"}},
             {"result": {"objects": ["print_stats", "toolhead"]}},
             "Connected — Moonraker 1.2.3; Klippy startup; missing: gcode_move, virtual_sdcard"),
            ({},
             {"result": {"objects": ["print_stats", "virtual_sdcard", "gcode_move"]}},
             "Connected — Moonraker unknown; Klippy unknown; required print objects available"),
        )
        for info, objects, status in cases:
            with self.subTest(status=status):
                transport = _ProbeTransport()
                action = self._action(transport=transport)
                action.testConnection("printer-a:7125", "k")
                transport.requests[0].callback(info, None)
                transport.requests[1].callback(objects, None)
                self.assertEqual(action.testStatus, status)
                self.assertFalse(action.testBusy)

    def test_an_unusable_url_never_starts_a_probe(self):
        transport = _ProbeTransport()
        action = self._action(transport=transport)

        action.testConnection("http://", "k")

        self.assertEqual(action.testStatus, "Enter a valid Moonraker URL")
        self.assertFalse(action.testBusy)
        self.assertEqual(transport.requests, [])

    def test_a_probe_already_in_flight_is_not_restarted(self):
        transport = _ProbeTransport()
        action = self._action(transport=transport)
        action.testConnection("printer-a:7125", "k")

        action.testConnection("printer-b:7125", "k")

        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(transport.configures, [("http://printer-a:7125", "k")])
        self.assertEqual(action.testStatus, "Testing connection…")

    def test_a_refused_probe_request_reports_the_live_test(self):
        transport = _ProbeTransport(start=False)
        action = self._action(transport=transport)

        action.testConnection("printer-a:7125", "k")

        self.assertEqual(action.testStatus, "A connection test request is already in progress")
        self.assertFalse(action.testBusy)

    def test_a_failed_probe_names_the_error(self):
        action = self._action()

        action._handle_probe_server_info(None, "connection refused")
        self.assertEqual(action.testStatus, "Connection failed: connection refused")
        self.assertFalse(action.testBusy)

        # The status line is one line: a long transport error is cut.
        action._handle_probe_server_info(None, "e" * 200)
        self.assertEqual(action.testStatus, "Connection failed: " + "e" * 160 + "…")

        action._handle_probe_objects(None, "the printer refused the object list")
        self.assertEqual(action.testStatus,
                         "Printer-object test failed: the printer refused the object list")
        self.assertFalse(action.testBusy)

    def test_a_malformed_probe_payload_is_reported_not_raised(self):
        # The handlers are the seam the transport delivers replies to;
        # both shapes below are ones the transport's JSON layer cannot
        # hand over, so they are driven at the seam.
        action = self._action()

        action._handle_probe_server_info(_RaisingGet(), None)
        self.assertIn("Invalid server response", action.testStatus)
        self.assertFalse(action.testBusy)

        action._handle_probe_objects({"result": {"objects": 5}}, None)
        self.assertIn("Invalid printer-object response", action.testStatus)
        self.assertFalse(action.testBusy)

    def test_cancel_test_returns_the_action_to_idle(self):
        transport = _ProbeTransport()
        action = self._action(transport=transport)
        busy_states = []
        action.testBusyChanged.connect(lambda: busy_states.append(action.testBusy))

        action.cancelTest()  # nothing in flight: nothing to report
        self.assertEqual(busy_states, [])
        self.assertEqual(transport.cancelled, ["probe"])

        action.testConnection("printer-a:7125", "k")
        action.cancelTest()

        self.assertEqual(transport.cancelled, ["probe", "probe"])
        self.assertFalse(action.testBusy)
        self.assertEqual(busy_states, [True, False])

    def test_the_probe_talks_to_a_live_moonraker(self):
        url, seen = self._moonraker_server()
        action = self._action()

        action.testConnection(url + "/", "s3cret")

        self.assertTrue(self._wait(lambda: action.testStatus.startswith("Connected")))
        self.assertEqual(action.testStatus,
                         "Connected — Moonraker v1.30.0; Klippy ready; "
                         "required print objects available; live-position refinement available")
        self.assertEqual([path for path, _key in seen],
                         ["/server/info", "/printer/objects/list"])
        self.assertEqual([key for _path, key in seen], ["s3cret", "s3cret"])
        self.assertFalse(action.testBusy)

    def test_the_probe_reports_a_refusing_server(self):
        url, seen = self._moonraker_server(
            info={"error": {"code": 500, "message": "Klippy shutdown"}}, status=500)
        action = self._action()

        action.testConnection(url, "k")

        self.assertTrue(self._wait(lambda: action.testStatus.startswith("Connection failed:")))
        self.assertIn("Klippy shutdown", action.testStatus)
        self.assertEqual([path for path, _key in seen], ["/server/info"])
        self.assertFalse(action.testBusy)

    # ---- the cache and the migration mirror -------------------------

    def test_clear_cache_removes_the_persistent_index(self):
        # The sweep covers EVERY generation the plugin ever used —
        # the legacy package-ID-named directory AND the current
        # renamed one (the live ruling: never only the cache-v2
        # subtree) — and leaves foreign directories alone.
        storage = self.module.Resources.getCacheStoragePath()
        for name in ("MoonrakerPrintFollower", self.module.CACHE_DIRECTORY_NAME):
            root = os.path.join(storage, name)
            os.makedirs(os.path.join(root, "index"))
            with open(os.path.join(root, "index", "part.json"), "w", encoding="utf-8") as handle:
                handle.write("{}")
        foreign = os.path.join(storage, "SomeOtherPlugin")
        os.makedirs(os.path.join(foreign, "index"))
        # The backing files are gone: the index listeners must reset
        # to the no-index state (the live ruling), and the invalidate
        # runs FIRST — the worker and its writer retire before the
        # sweep deletes anything (the review's ordering finding).
        follower = self._follower()
        invalidated = []

        def invalidate_index():
            # At invalidate time the directories must still stand:
            # the deletion follows the retirement, never precedes it.
            invalidated.append([os.path.isdir(os.path.join(storage, name))
                                for name in ("MoonrakerPrintFollower",
                                             self.module.CACHE_DIRECTORY_NAME)])
        follower.invalidateIndex = invalidate_index
        action = self._action(follower)
        statuses = []
        action.cacheStatusChanged.connect(lambda: statuses.append(action.cacheStatus))

        action.clearCache()

        for name in ("MoonrakerPrintFollower", self.module.CACHE_DIRECTORY_NAME):
            self.assertFalse(os.path.exists(os.path.join(storage, name)),
                             "%s survived the clear" % name)
        self.assertTrue(os.path.isdir(foreign), "the sweep reached a foreign plugin")
        self.assertEqual(invalidated, [[True, True]],
                         "the invalidate ran after the deletion (or never ran)")
        self.assertEqual(action.cacheStatus,
                         "Cache cleared. Restart Cura to also drop the session's downloaded file.")
        self.assertEqual(statuses, [action.cacheStatus])

    def test_a_failing_cache_clear_is_reported_not_raised(self):
        action = self._action()

        with patch.object(self.module.shutil, "rmtree", side_effect=OSError("read-only")):
            action.clearCache()

        # The refusal is REPORTED on the status row (the review's
        # finding — a silently-ignored deletion failure read as a
        # full clear), never raised.
        self.assertEqual(action.cacheStatus,
                         "Cache partially cleared — some files are still in use. "
                         "Restart Cura to also drop the session's downloaded file.")

    def test_the_migration_surfaces_read_the_persistence_record(self):
        record = {"status": "failed", "backupWritten": True, "backupName": "cura.cfg.20260919"}
        persistence = _Persistence(record)
        action = self._action(self._follower(persistence=persistence))

        self.assertTrue(action.migrationBannerVisible)
        self.assertIn("cura.cfg.20260919", action.migrationBannerText)
        self.assertTrue(action.migrationBackupAvailable)
        self.assertFalse(action.migrationDiagnosticsVisible)

        action.dismissMigrationBanner()

        self.assertEqual(persistence.updates, [{"bannerDismissed": True}])
        self.assertFalse(action.migrationBannerVisible)
        self.assertTrue(action.migrationDiagnosticsVisible)
        self.assertIn("cura.cfg.20260919", action.migrationDiagnosticsText)

    def test_a_follower_without_persistence_reports_nothing(self):
        action = self._action(self._follower(persistence=None))

        self.assertFalse(action.migrationBannerVisible)
        self.assertEqual(action.migrationBannerText, "")
        self.assertFalse(action.migrationBackupAvailable)
        self.assertFalse(action.migrationDiagnosticsVisible)
        self.assertEqual(action.migrationDiagnosticsText, "")

        changed = []
        action.migrationChanged.connect(lambda: changed.append(True))
        action.dismissMigrationBanner()  # nothing to write: the page still refreshes
        self.assertEqual(changed, [True])

    def test_the_backup_folder_opens_cura_s_config_folder(self):
        from PyQt6.QtGui import QDesktopServices

        opened = []
        action = self._action()

        with patch.object(QDesktopServices, "openUrl",
                          staticmethod(lambda url: opened.append(url.toLocalFile()))):
            action.openMigrationBackupFolder()

        self.assertEqual(opened, [self.module.Resources.getConfigStoragePath()])

    # ---- the page's read-only relays ---------------------------------

    def test_the_status_readers_expose_the_live_state(self):
        action = self._action()
        self.assertEqual((action.testStatus, action.testBusy, action.cacheStatus),
                         ("Not tested", False, ""))


if __name__ == "__main__":
    unittest.main()
