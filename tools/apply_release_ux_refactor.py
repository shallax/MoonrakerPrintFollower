from __future__ import annotations

import pathlib
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]


def replace_once(path: pathlib.Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"Missing {label} in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: pathlib.Path, start: str, end: str, replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"Missing start marker for {label} in {path}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"Missing end marker for {label} in {path}")
    path.write_text(text[:start_index] + replacement + text[end_index:], encoding="utf-8")


def write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


# ---------------------------------------------------------------------------
# Architecture: one core-status completion hook, not three update/emission passes.
# ---------------------------------------------------------------------------
model_path = ROOT / "plugins" / "MoonrakerMonitorModel.py"
replace_once(
    model_path,
    "        self._update_eta()\n        self.monitorChanged.emit()\n        self.actionChanged.emit()\n",
    "        self._update_eta()\n        self._after_core_status(status)\n        self.monitorChanged.emit()\n        self.actionChanged.emit()\n",
    "core-status subclass hook",
)
replace_once(
    model_path,
    "\n    def _fetch_metadata(self, filename: str) -> None:\n",
    "\n    def _after_core_status(self, _status: Any) -> None:\n        \"\"\"Subclass hook run after core fields are coherent, before UI notification.\"\"\"\n\n    def _fetch_metadata(self, filename: str) -> None:\n",
    "base core-status hook definition",
)

runtime_path = ROOT / "plugins" / "MoonrakerMonitorRuntime.py"
replace_between(
    runtime_path,
    "    def updateMoonrakerStatus(self, status: Any) -> None:\n",
    "    def _resolve_live_layer(self, status: Any) -> Tuple[Optional[int], Optional[int]]:\n",
    '''    def __init__(self, output_controller: Any, number_of_extruders: int, follower: Any) -> None:
        self._resolved_current_layer: Optional[int] = None
        self._resolved_total_layer: Optional[int] = None
        super().__init__(output_controller, number_of_extruders, follower)

    def _after_core_status(self, status: Any) -> None:
        super()._after_core_status(status)
        current_layer, total_layer = self._resolve_live_layer(status)
        self._resolved_current_layer = current_layer
        self._resolved_total_layer = total_layer
        if current_layer is not None and total_layer is not None:
            self._monitor_layer = f"{current_layer} / {total_layer}"
        elif current_layer is not None:
            self._monitor_layer = str(current_layer)
        elif total_layer is not None:
            self._monitor_layer = f"— / {total_layer}"
        else:
            self._monitor_layer = "—"

''',
    "runtime core-status hook",
)

controls_path = ROOT / "plugins" / "MoonrakerMonitorControls.py"
replace_between(
    controls_path,
    "    @pyqtSlot(object)\n    def updateMoonrakerStatus(self, status: Any) -> None:\n",
    "    def _on_metadata(self, filename: str, payload: Optional[Dict[str, Any]], error: Optional[str]) -> None:\n",
    '''    def _after_core_status(self, status: Any) -> None:
        if isinstance(status, dict):
            print_stats = self._status_object(status, "print_stats")
            filename = str(print_stats.get("filename") or "")
            if filename != self._runtime_metadata_filename:
                self._runtime_metadata_filename = filename
                self._metadata_layer_count = None
                self._metadata_layer_height = None
                self._metadata_first_layer_height = None
                self._monitor_layer_height = "—"

        # Resolve the physical layer exactly once in the runtime layer, after any
        # stale per-file metadata above has been cleared.
        super()._after_core_status(status)
        current_layer = self._resolved_current_layer
        self._monitor_layer_height = self._resolve_layer_height_text(current_layer)

        if isinstance(status, dict):
            gcode_move = self._status_object(status, "gcode_move")
            try:
                self._speed_factor_percent = max(1, int(round(float(gcode_move.get("speed_factor") or 1.0) * 100.0)))
            except (TypeError, ValueError):
                self._speed_factor_percent = 100
            try:
                self._flow_factor_percent = max(1, int(round(float(gcode_move.get("extrude_factor") or 1.0) * 100.0)))
            except (TypeError, ValueError):
                self._flow_factor_percent = 100
            origin = gcode_move.get("homing_origin")
            if isinstance(origin, (list, tuple)) and len(origin) >= 3:
                try:
                    self._z_offset = float(origin[2])
                except (TypeError, ValueError):
                    pass

        self.controlsChanged.emit()

''',
    "controls core-status hook",
)

# ---------------------------------------------------------------------------
# Architecture/performance: never download configfile.config every second.
# ---------------------------------------------------------------------------
replace_between(
    model_path,
    "    def _on_objects_list(\n",
    "    @staticmethod\n    def _friendly_object_name(name: str) -> str:\n",
    '''    def _on_objects_list(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        objects = result.get("objects") if isinstance(result, dict) else None
        if not isinstance(objects, list):
            return
        self._available_objects = sorted(str(item) for item in objects)
        self._aux_objects = [name for name in self._available_objects if self._want_aux_object(name)]
        self._poll_config_snapshot()
        self._poll_aux_status()

    @staticmethod
    def _want_aux_object(name: str) -> bool:
        lower = str(name or "").lower()
        if lower in {"heater_bed", "fan", "exclude_object", "system_stats", "webhooks", "mcu"}:
            return True
        if re.fullmatch(r"extruder\\d*", lower):
            return True
        prefixes = (
            "heater_generic ",
            "temperature_sensor ",
            "temperature_fan ",
            "temperature_host ",
            "temperature_combined ",
            "bme280 ",
            "htu21d ",
            "sht3x ",
            "lm75 ",
            "fan_generic ",
            "heater_fan ",
            "controller_fan ",
            "filament_switch_sensor ",
            "filament_motion_sensor ",
            "mcu ",
        )
        return lower.startswith(prefixes)

    @staticmethod
    def _aux_query_fields(name: str):
        # configfile.config/settings can be very large. Only the two volatile
        # SAVE_CONFIG fields belong in the one-second poll; the full config is
        # refreshed with capability discovery instead.
        if str(name or "").lower() == "configfile":
            return ["save_config_pending", "save_config_pending_items"]
        return None

    @staticmethod
    def _merge_aux_status(current: Any, incoming: Any) -> Dict[str, Any]:
        merged: Dict[str, Any] = dict(current) if isinstance(current, dict) else {}
        if not isinstance(incoming, dict):
            return merged
        for name, value in incoming.items():
            previous = merged.get(name)
            if isinstance(previous, dict) and isinstance(value, dict):
                combined = dict(previous)
                combined.update(value)
                merged[name] = combined
            else:
                merged[name] = value
        return merged

    def _poll_config_snapshot(self) -> None:
        if not any(str(name).lower() == "configfile" for name in self._aux_objects):
            return
        body = {"objects": {"configfile": None}}
        self._json_request(
            "config-static",
            "POST",
            "printer/objects/query",
            self._on_config_snapshot,
            body=body,
            replace=True,
        )

    def _on_config_snapshot(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            return
        self._aux_status = self._merge_aux_status(self._aux_status, status)
        self._rebuild_peripherals()

    def _poll_aux_status(self) -> None:
        if not self._aux_objects:
            return
        body = {"objects": {name: self._aux_query_fields(name) for name in self._aux_objects}}
        self._json_request("aux", "POST", "printer/objects/query", self._on_aux_status, body=body)

    def _on_aux_status(
        self,
        payload: Optional[Dict[str, Any]],
        error: Optional[str],
    ) -> None:
        if error:
            return
        result = self._result(payload)
        status = result.get("status") if isinstance(result, dict) else None
        if not isinstance(status, dict):
            return
        self._aux_status = self._merge_aux_status(self._aux_status, status)
        self._rebuild_peripherals()

''',
    "split static and dynamic aux polling",
)

# Output-device lifecycle: no stale active Monitor if Cura temporarily has no stack.
output_plugin = ROOT / "plugins" / "MoonrakerOutputDevicePlugin.py"
text = output_plugin.read_text(encoding="utf-8")
for line in (
    "# MoonrakerMonitorRuntime remains the proven layer-following base implementation.\n",
    "# MoonrakerMonitorControls remains the tested control base beneath the typed layer.\n",
    "# Compatibility/source-contract markers: \"MoonrakerMonitorDashboard.qml\" still\n",
    "# composes \"MoonrakerMonitor.qml\" underneath the active bed-mesh wrapper.\n",
):
    text = text.replace(line, "")
output_plugin.write_text(text, encoding="utf-8")
replace_once(
    output_plugin,
    '''            stack = self._application.getGlobalContainerStack()
            if stack is None:
                return
''',
    '''            stack = self._application.getGlobalContainerStack()
            if stack is None:
                if self._current is not None:
                    self._set_monitor_active(self._current, False)
                    try:
                        self.getOutputDeviceManager().removeOutputDevice(self._current.getId())
                    except Exception:
                        pass
                    self._current = None
                return
''',
    "no-stack output lifecycle cleanup",
)

# ---------------------------------------------------------------------------
# UX: pending slider values are visible; commands still happen only on release.
# ---------------------------------------------------------------------------
dashboard = ROOT / "plugins" / "MoonrakerMonitorDashboard.qml"
replace_once(
    dashboard,
    '                        UM.Label { text: "Live tuning"; font: UM.Theme.getFont("medium_bold") }\n\n',
    '''                        UM.Label { text: "Live tuning"; font: UM.Theme.getFont("medium_bold") }
                        UM.Label
                        {
                            text: "Drag to preview a value; the change is sent to Klipper when you release the slider."
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }

''',
    "live tuning release hint",
)

# UX: webcam refresh should do what its placement says, and exclusion is irreversible.
monitor_qml = ROOT / "plugins" / "MoonrakerMonitor.qml"
replace_once(
    monitor_qml,
    '''        RowLayout
        {
            anchors.fill: parent
''',
    '''        Cura.MessageDialog
        {
            id: excludeObjectDialog
            property string targetName: ""
            title: "Exclude object?"
            text: targetName.length > 0
                ? "Stop printing '" + targetName + "' for the rest of this job? This cannot be undone without restarting the print."
                : "Stop printing this object for the rest of this job?"
            standardButtons: Dialog.Yes | Dialog.No
            anchors.centerIn: Overlay.overlay
            onAccepted:
            {
                if (root.printer != null && targetName.length > 0)
                {
                    root.printer.excludeObject(targetName)
                }
                targetName = ""
            }
            onRejected: targetName = ""
        }

        RowLayout
        {
            anchors.fill: parent
''',
    "exclude-object confirmation",
)
replace_once(
    monitor_qml,
    '''                        Cura.SecondaryButton
                        {
                            text: "Refresh"
                            onClicked:
                            {
                                if (root.printer != null)
                                {
                                    root.printer.refreshAll()
                                }
                            }
                        }
''',
    '''                        Cura.SecondaryButton
                        {
                            text: "Refresh camera"
                            tooltip: "Refresh Moonraker's webcam list."
                            onClicked:
                            {
                                if (root.printer != null)
                                {
                                    root.printer.refreshWebcams()
                                }
                            }
                        }
''',
    "camera-specific refresh",
)
replace_once(
    monitor_qml,
    '                                        onClicked: root.printer.excludeObject(modelData.name)\n',
    '''                                        onClicked:
                                        {
                                            excludeObjectDialog.targetName = modelData.name
                                            excludeObjectDialog.open()
                                        }
''',
    "exclude button confirmation",
)

# UX: make the saved default folder semantics match the upload dialog.
config_qml = ROOT / "plugins" / "MoonrakerFollowerConfiguration.qml"
replace_once(
    config_qml,
    '                        Cura.TextField { id: uploadPathField; width: parent.width; text: manager.settingsUploadPath; maximumLength: 1024 }\n\n',
    '''                        Cura.TextField
                        {
                            id: uploadPathField
                            width: parent.width
                            text: manager.settingsUploadPath
                            placeholderText: "<root>"
                            maximumLength: 1024
                        }
                        UM.Label
                        {
                            text: "Leave blank to use Moonraker's gcodes root."
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: UM.Theme.getColor("text_inactive")
                            font: UM.Theme.getFont("default_italic")
                        }

''',
    "default upload root help",
)

# UX: remove ambiguity about which remote file the Preview button loads.
for relative in ("PreviewActionPanelControls.qml", "EmptyPreviewLoadButton.qml"):
    path = ROOT / "plugins" / relative
    text = path.read_text(encoding="utf-8")
    if 'text: "Load print"' not in text:
        raise SystemExit(f"Load print label missing in {relative}")
    path.write_text(text.replace('text: "Load print"', 'text: "Load current print"', 1), encoding="utf-8")

# ---------------------------------------------------------------------------
# Tests: replace comment-marker contracts with actual architecture contracts.
# ---------------------------------------------------------------------------
hotfix = ROOT / "tests" / "test_hotfix_regressions.py"
text = hotfix.read_text(encoding="utf-8")
if 'BED_MESH_QML =' not in text:
    text = text.replace(
        'DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()\n',
        'DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()\nBED_MESH_QML = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text()\n',
        1,
    )
text = text.replace(
    '''    def test_output_plugin_selects_typed_dashboard(self):
        self.assertIn("MoonrakerMonitorTypedControls", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorDashboard.qml"', OUTPUT_PLUGIN)
''',
    '''    def test_output_plugin_selects_typed_dashboard(self):
        self.assertIn("MoonrakerMonitorTypedControls", OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorDashboard", BED_MESH_QML)
''',
)
hotfix.write_text(text, encoding="utf-8")

monitor_tests = ROOT / "tests" / "test_monitor_upload_regressions.py"
text = monitor_tests.read_text(encoding="utf-8")
if 'BED_MESH_QML =' not in text:
    text = text.replace(
        'DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()\n',
        'DASHBOARD_QML = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text()\nBED_MESH_QML = (PLUGINS / "MoonrakerMonitorBedMesh.qml").read_text()\n',
        1,
    )
text = text.replace('        self.assertIn("MoonrakerMonitorRuntime", OUTPUT_PLUGIN)\n', '        self.assertIn("MoonrakerMonitorRuntime", MONITOR_CONTROLS)\n')
text = text.replace(
    '''    def test_active_monitor_chain_is_packaged_and_selected(self):
        self.assertIn('"MoonrakerMonitorDashboard.qml"', OUTPUT_PLUGIN)
        self.assertIn('"MoonrakerMonitor.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorTypedControls", OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorControls", MONITOR_TYPED_CONTROLS)
        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)
        self.assertIn("Printer controls", DASHBOARD_QML)
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())
''',
    '''    def test_active_monitor_chain_is_packaged_and_selected(self):
        self.assertIn('"MoonrakerMonitorBedMesh.qml"', OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorTypedControls", OUTPUT_PLUGIN)
        self.assertIn("MoonrakerMonitorControls", MONITOR_TYPED_CONTROLS)
        self.assertIn("MoonrakerMonitorDashboard", BED_MESH_QML)
        self.assertIn("MoonrakerMonitor", DASHBOARD_QML)
        self.assertIn("Printer controls", DASHBOARD_QML)
        self.assertFalse((PLUGINS / "MoonrakerMonitorEnhanced.qml").exists())
''',
)
monitor_tests.write_text(text, encoding="utf-8")

release_tests = ROOT / "tests" / "test_release_hardening.py"
text = release_tests.read_text(encoding="utf-8")
text = text.replace(
    'MONITOR_SOURCE = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")\n',
    'MONITOR_SOURCE = (PLUGINS / "MoonrakerMonitorModel.py").read_text(encoding="utf-8")\nRUNTIME_SOURCE = (PLUGINS / "MoonrakerMonitorRuntime.py").read_text(encoding="utf-8")\nCONTROLS_SOURCE = (PLUGINS / "MoonrakerMonitorControls.py").read_text(encoding="utf-8")\nMONITOR_QML = (PLUGINS / "MoonrakerMonitor.qml").read_text(encoding="utf-8")\nCONFIG_QML = (PLUGINS / "MoonrakerFollowerConfiguration.qml").read_text(encoding="utf-8")\n',
    1,
)
insert = r'''
    def test_core_status_uses_one_subclass_hook_instead_of_reprocessing_three_times(self):
        self.assertIn("self._after_core_status(status)", MONITOR_SOURCE)
        self.assertIn("def _after_core_status", RUNTIME_SOURCE)
        self.assertIn("def _after_core_status", CONTROLS_SOURCE)
        self.assertNotIn("def updateMoonrakerStatus", RUNTIME_SOURCE)
        self.assertNotIn("def updateMoonrakerStatus", CONTROLS_SOURCE)
        self.assertIn("self._resolved_current_layer", RUNTIME_SOURCE)

    def test_full_klipper_config_is_not_polled_every_second(self):
        self.assertIn('["save_config_pending", "save_config_pending_items"]', MONITOR_SOURCE)
        self.assertIn('"config-static"', MONITOR_SOURCE)
        self.assertIn('name: self._aux_query_fields(name)', MONITOR_SOURCE)
        model = load_monitor_model()
        merged = model._merge_aux_status(
            {"configfile": {"config": {"gcode_macro TEST": {"gcode": "G28"}}, "save_config_pending": False}},
            {"configfile": {"save_config_pending": True, "save_config_pending_items": {"bed_mesh": {}}}},
        )
        self.assertIn("config", merged["configfile"])
        self.assertTrue(merged["configfile"]["save_config_pending"])

    def test_request_identity_change_aborts_old_replies(self):
        model = load_monitor_model()
        instance = model.__new__(model)
        reply = DummyReply()
        instance._requests = {"aux": reply}
        instance._request_generation = 7
        instance._request_identity = ("http://old", "old-key")
        class Config:
            url = "http://new"
            api_key = "new-key"
        class Follower:
            @staticmethod
            def current_printer_config():
                return Config()
        instance._follower = Follower()
        instance._ensure_request_session()
        self.assertEqual(instance._request_generation, 8)
        self.assertEqual(instance._request_identity, ("http://new", "new-key"))
        self.assertTrue(reply.aborted)
        self.assertTrue(reply.deleted)
        self.assertEqual(instance._requests, {})

    def test_monitor_ux_release_polish_is_explicit(self):
        self.assertIn("Drag to preview a value; the change is sent to Klipper when you release the slider.", DASHBOARD_SOURCE)
        self.assertIn('text: "Refresh camera"', MONITOR_QML)
        self.assertIn("root.printer.refreshWebcams()", MONITOR_QML)
        self.assertIn('title: "Exclude object?"', MONITOR_QML)
        self.assertIn("excludeObjectDialog.open()", MONITOR_QML)
        self.assertIn('placeholderText: "<root>"', CONFIG_QML)
        self.assertIn("Leave blank to use Moonraker's gcodes root.", CONFIG_QML)
        self.assertIn("stack is None", OUTPUT_PLUGIN_SOURCE)
        self.assertIn("self._set_monitor_active(self._current, False)", OUTPUT_PLUGIN_SOURCE)

'''
marker = '\n\nif __name__ == "__main__":\n'
if marker not in text:
    raise SystemExit("release-hardening test footer missing")
text = text.replace(marker, "\n" + textwrap.dedent(insert).rstrip() + marker, 1)
release_tests.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------------
# Maintainer-facing architecture map and release invariants.
# ---------------------------------------------------------------------------
write(ROOT / "ARCHITECTURE.md", '''
# Architecture

Moonraker Print Follower deliberately keeps Cura-facing UI, Moonraker transport,
G-code indexing, and Preview following separated so a UI or printer capability
change does not have to destabilise the core follower.

## Core following

- `MoonrakerClient.py` owns the follower's core Moonraker status/download requests
  and generation-guards callbacks when printer configuration changes.
- `GCodeIndex.py` is pure G-code indexing/timing/motion logic and is covered by
  file fixtures rather than Cura runtime tests.
- `FollowController.py` contains follow-mode decisions.
- `CuraAdapter.py` and `NativeNozzleFallback.py` isolate Cura-specific Preview
  compatibility details.
- `MoonrakerPrintFollower.py` remains the orchestration boundary: Cura lifecycle,
  scene/view binding, remote-file loading, and coordination of the modules above.

## Monitor

The Monitor model is intentionally layered, with one class at each responsibility:

1. `MoonrakerMonitorModel.py` — HTTP/session lifecycle, core print state, ETA,
   capabilities, webcams, power and basic peripherals.
2. `MoonrakerMonitorRuntime.py` — follower-aware physical-layer resolution.
3. `MoonrakerMonitorControls.py` — printer actions and live tuning.
4. `MoonrakerMonitorTypedControls.py` — typed macro parameters, PWM, MCU stats,
   remembered webcams and bed-mesh Preview integration.

Core status is processed once in the base class and extended through
`_after_core_status`; subclasses must not re-poll or re-emit the same status.
Only the active Cura printer's Monitor may poll. Every Monitor HTTP reply carries a
request-generation token so a late reply from a previous URL/printer is ignored.

`configfile.config` is a static/heavy Klipper object. It is refreshed with
capability discovery, while only `save_config_pending` fields are included in the
one-second dynamic poll.

The active QML chain is `MoonrakerMonitorBedMesh.qml` →
`MoonrakerMonitorDashboard.qml` → `MoonrakerMonitor.qml`.

## Upload/output

- `MoonrakerOutputDevice.py` owns output preparation, power-on/readiness and upload.
- `MoonrakerOutputDeviceLifecycle.py` adds Cura/QML-safe deferred dialog teardown,
  folder discovery and exactly-once terminal write signalling.
- `MoonrakerOutputDevicePlugin.py` owns one cached device per Cura machine and
  activates polling only for the currently selected printer.

## Release invariants

- All QML must pass `tools/check_qml.py`; duplicate properties are a hard failure.
- Tests are discovered automatically with `unittest discover`.
- Python sources compile on 3.10, 3.11 and 3.12 in CI.
- The built `.curapackage` must be an exact byte-for-byte projection of the plugin
  source tree and contain no caches, editor backups or temporary patch files.
- New behaviour after the 3.0.0 seal belongs in a later version.

## Deliberate future refactors

`MoonrakerPrintFollower.py` is still the largest orchestration module, and there
are separate HTTP implementations for following, Monitor, and output/upload.
Those are real maintenance debts, but collapsing them immediately before a release
would couple independently working paths and create more release risk than it
removes. A later refactor should extract orchestration services and a shared small
HTTP/session utility behind existing tests, without changing user-visible behaviour.
''')

print("Applied release UX and architecture audit")
