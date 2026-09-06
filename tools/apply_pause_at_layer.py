from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# MoonrakerProtocol: centralise the command endpoint beside other API endpoints.
path = ROOT / "plugins" / "MoonrakerProtocol.py"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "def objects_list_endpoint(base_url: str) -> str:\n    return f\"{base_url.rstrip('/')}\/printer/objects/list\"\n\n\ndef parse_file_identity",
    "def objects_list_endpoint(base_url: str) -> str:\n    return f\"{base_url.rstrip('/')}\/printer/objects/list\"\n\n\ndef gcode_script_endpoint(base_url: str) -> str:\n    return f\"{base_url.rstrip('/')}\/printer/gcode/script\"\n\n\ndef parse_file_identity",
    "Moonraker gcode endpoint",
)
path.write_text(text, encoding="utf-8")


# Preview QML: a stable button + one-line schedule summary.
path = ROOT / "plugins" / "PreviewActionPanelControls.qml"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "    property string selectedLayerEtaText: \"\"\n",
    "    property string selectedLayerEtaText: \"\"\n"
    "    property bool pauseAtLayerActive: false\n"
    "    property int pauseAtLayerCandidate: 0\n"
    "    property bool pauseAtLayerCanToggle: false\n"
    "    property bool pauseAtLayerScheduled: false\n"
    "    property string pauseAtLayerSummary: \"\"\n",
    "Preview pause properties",
)
text = replace_once(
    text,
    "    signal bedMeshVisibilityRequested(bool visible)\n",
    "    signal bedMeshVisibilityRequested(bool visible)\n"
    "    signal pauseAtLayerRequested(int layer)\n",
    "Preview pause signal",
)
eta_block = '''            UM.Label
            {
                width: parent.width
                height: 36 * screenScaleFactor
                text: base.selectedLayerEtaText.length > 0 ? base.selectedLayerEtaText : " "
                opacity: base.selectedLayerEtaText.length > 0 ? 1.0 : 0.0
                color: UM.Theme.getColor("text")
                font: UM.Theme.getFont("default")
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                clip: true
            }
'''
pause_ui = eta_block + '''
            Cura.SecondaryButton
            {
                id: pauseAtLayerButton
                visible: base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive
                width: parent.width
                height: visible ? UM.Theme.getSize("action_button").height : 0
                enabled: base.pauseAtLayerCanToggle
                text: base.pauseAtLayerCandidate <= 0
                    ? "Pause at selected layer"
                    : (base.pauseAtLayerScheduled
                        ? "Remove pause at layer " + base.pauseAtLayerCandidate
                        : (base.pauseAtLayerCanToggle
                            ? "Enable pause at layer " + base.pauseAtLayerCandidate
                            : "Layer " + base.pauseAtLayerCandidate + " already reached"))
                tooltip: base.pauseAtLayerCanToggle
                    ? (base.pauseAtLayerScheduled
                        ? "Remove the scheduled PAUSE for this future layer."
                        : "Call the Klipper PAUSE macro when Moonraker reaches this future layer.")
                    : "Scroll Cura Preview to a layer ahead of the current print layer to schedule PAUSE."
                fixedWidthMode: true
                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            UM.Label
            {
                visible: base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive
                width: parent.width
                height: visible ? 20 * screenScaleFactor : 0
                text: base.pauseAtLayerSummary.length > 0 ? base.pauseAtLayerSummary : " "
                opacity: base.pauseAtLayerSummary.length > 0 ? 1.0 : 0.0
                color: UM.Theme.getColor("text_inactive")
                font: UM.Theme.getFont("default_italic")
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
                clip: true
            }
'''
text = replace_once(text, eta_block, pause_ui, "Preview pause UI")
path.write_text(text, encoding="utf-8")


# Follower implementation.
path = ROOT / "plugins" / "MoonrakerPrintFollower.py"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    "    download_endpoint,\n    live_position_in_gcode_space,\n",
    "    download_endpoint,\n    gcode_script_endpoint,\n    live_position_in_gcode_space,\n",
    "Follower protocol import",
)
text = replace_once(
    text,
    "        self._network = QNetworkAccessManager()\n        self._reply: Optional[QNetworkReply] = None\n        self._reply_purpose: Optional[str] = None\n",
    "        self._network = QNetworkAccessManager()\n"
    "        self._reply: Optional[QNetworkReply] = None\n"
    "        self._reply_purpose: Optional[str] = None\n\n"
    "        # Preview-scheduled PAUSE commands are intentionally print-local.\n"
    "        # They are never persisted into PrinterConfig because carrying a layer\n"
    "        # number into a different G-code file would be unsafe and surprising.\n"
    "        self._pause_network = QNetworkAccessManager()\n"
    "        self._pause_reply: Optional[QNetworkReply] = None\n"
    "        self._pause_reply_generation = 0\n"
    "        self._pause_reply_job_key: Optional[Tuple[str, int, int]] = None\n"
    "        self._scheduled_pause_layers: set[int] = set()\n"
    "        self._last_observed_remote_layer: Optional[int] = None\n",
    "Follower pause state",
)
text = replace_once(
    text,
    "        self._remote_job_key = None\n        self._remote_file_identity = None\n",
    "        self._remote_job_key = None\n"
    "        self._remote_file_identity = None\n"
    "        self._last_observed_remote_layer = None\n"
    "        self._clear_scheduled_pauses(abort_request=True)\n",
    "Machine switch pause cleanup",
)

# Preview state fields are calculated once per refresh and sent to both placements.
old = '''        active_printer_name = self._active_machine_name or machine_name
        for controls in (self._preview_overlay, self._action_panel_controls):
'''
new = '''        active_printer_name = self._active_machine_name or machine_name
        pause_state = self._pause_at_layer_preview_state()
        for controls in (self._preview_overlay, self._action_panel_controls):
'''
text = replace_once(text, old, new, "Preview pause state calculation")
text = replace_once(
    text,
    '''                controls.setProperty("selectedLayerEtaText", self._selected_layer_eta_text)
''',
    '''                controls.setProperty("selectedLayerEtaText", self._selected_layer_eta_text)
                controls.setProperty("pauseAtLayerActive", pause_state["active"])
                controls.setProperty("pauseAtLayerCandidate", pause_state["candidate"])
                controls.setProperty("pauseAtLayerCanToggle", pause_state["canToggle"])
                controls.setProperty("pauseAtLayerScheduled", pause_state["scheduled"])
                controls.setProperty("pauseAtLayerSummary", pause_state["summary"])
''',
    "Preview pause properties sync",
)

# Add preview scheduling methods before duration formatting.
marker = '''    @staticmethod
    def _format_preview_duration(seconds: float) -> str:
'''
methods = '''    def _pause_at_layer_preview_state(self) -> Dict[str, Any]:
        active = bool(
            self._last_remote_state in self.ACTIVE_STATES
            and self._remote_job_key is not None
        )
        candidate = 0
        selected_layer: Optional[int] = None
        view = self._simulation_view() if active else None
        if view is not None:
            try:
                selected_layer = max(0, int(view.getCurrentLayer()))
                candidate = selected_layer + 1
            except Exception:
                selected_layer = None

        current = self._last_observed_remote_layer
        can_toggle = bool(
            active
            and selected_layer is not None
            and current is not None
            and selected_layer > current
        )
        scheduled = bool(selected_layer is not None and selected_layer in self._scheduled_pause_layers)
        layers = ", ".join(str(layer + 1) for layer in sorted(self._scheduled_pause_layers))
        summary = f"Scheduled PAUSE layers: {layers}" if layers else ""
        return {
            "active": active,
            "candidate": candidate,
            "canToggle": can_toggle,
            "scheduled": scheduled,
            "summary": summary,
        }

    def _toggle_pause_at_selected_layer(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return
        current = self._last_observed_remote_layer
        if (
            self._last_remote_state not in self.ACTIVE_STATES
            or self._remote_job_key is None
            or current is None
            or layer <= current
        ):
            self._sync_preview_button_state()
            return

        if layer in self._scheduled_pause_layers:
            self._scheduled_pause_layers.remove(layer)
            self._set_status(f"Removed scheduled PAUSE at layer {layer + 1}")
        else:
            self._scheduled_pause_layers.add(layer)
            self._set_status(f"PAUSE scheduled for layer {layer + 1}")
        self._sync_preview_button_state()

    def _clear_scheduled_pauses(self, *, abort_request: bool = False) -> None:
        self._scheduled_pause_layers.clear()
        if abort_request:
            self._abort_pause_reply()
        self._sync_preview_button_state()

    def _abort_pause_reply(self) -> None:
        reply = self._pause_reply
        self._pause_reply = None
        self._pause_reply_job_key = None
        self._pause_reply_generation += 1
        if reply is not None:
            try:
                if reply.isRunning():
                    reply.abort()
            except Exception:
                pass
            try:
                reply.deleteLater()
            except Exception:
                pass

    def _maybe_trigger_scheduled_pause(self, current_layer: int) -> None:
        if not self._scheduled_pause_layers or self._remote_job_key is None:
            return
        try:
            current_layer = int(current_layer)
        except (TypeError, ValueError):
            return

        due = sorted(layer for layer in self._scheduled_pause_layers if layer <= current_layer)
        if not due:
            return
        # One PAUSE is sufficient if polling skipped across more than one scheduled
        # very short layer. Consume every already-reached target, but keep later ones.
        for layer in due:
            self._scheduled_pause_layers.discard(layer)
        target_layer = due[0]
        self._sync_preview_button_state()
        self._send_scheduled_pause(target_layer, current_layer)

    def _send_scheduled_pause(self, target_layer: int, current_layer: int) -> None:
        if self._pause_reply is not None:
            try:
                if self._pause_reply.isRunning():
                    Logger.log(
                        "w",
                        "Moonraker Print Follower skipped overlapping PAUSE request for layer %d",
                        target_layer + 1,
                    )
                    return
            except Exception:
                pass

        config = self._config_store.get()
        base_url = self._normalise_base_url(config.url)
        if not self._url_is_usable(base_url):
            self._set_status(f"Could not PAUSE at layer {target_layer + 1}: Moonraker URL unavailable")
            return

        request = QNetworkRequest(QUrl(gcode_script_endpoint(base_url)))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"Content-Type", b"application/json")
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(5000)
        if config.api_key:
            request.setRawHeader(b"X-Api-Key", config.api_key.encode("utf-8"))

        payload = json.dumps({"script": "PAUSE"}, separators=(",", ":")).encode("utf-8")
        generation = self._lifecycle_generation
        job_key = self._remote_job_key
        self._pause_reply_generation += 1
        request_generation = self._pause_reply_generation
        self._pause_reply_job_key = job_key
        reply = self._pause_network.post(request, payload)
        self._pause_reply = reply
        reply.finished.connect(
            lambda r=reply, g=generation, rg=request_generation, j=job_key, t=target_layer, c=current_layer:
                self._handle_scheduled_pause_reply(r, g, rg, j, t, c)
        )
        Logger.log(
            "i",
            "Moonraker Print Follower requesting PAUSE for scheduled layer %d (observed layer %d)",
            target_layer + 1,
            current_layer + 1,
        )

    def _handle_scheduled_pause_reply(
        self,
        reply: QNetworkReply,
        lifecycle_generation: int,
        request_generation: int,
        job_key: Optional[Tuple[str, int, int]],
        target_layer: int,
        current_layer: int,
    ) -> None:
        if reply is not self._pause_reply or request_generation != self._pause_reply_generation:
            try:
                reply.deleteLater()
            except Exception:
                pass
            return
        self._pause_reply = None
        self._pause_reply_job_key = None
        try:
            if lifecycle_generation != self._lifecycle_generation or job_key != self._remote_job_key:
                return
            if reply.error() != QNetworkReply.NetworkError.NoError:
                self._set_status(
                    f"PAUSE at layer {target_layer + 1} failed: {reply.errorString()}"
                )
                return
            suffix = "" if current_layer == target_layer else f" (detected at layer {current_layer + 1})"
            self._set_status(f"PAUSE requested at layer {target_layer + 1}{suffix}")
        finally:
            try:
                reply.deleteLater()
            except Exception:
                pass
            self._sync_preview_button_state()

    @staticmethod
    def _format_preview_duration(seconds: float) -> str:
'''
text = replace_once(text, marker, methods, "Pause scheduling methods")

# Connect the new signal on action-panel controls.
text = replace_once(
    text,
    '''                        action_controls.loadClicked.connect(self._confirm_force_load_current_print)
                        action_controls.pauseClicked.connect(self._toggle_following_pause)
''',
    '''                        action_controls.loadClicked.connect(self._confirm_force_load_current_print)
                        action_controls.pauseClicked.connect(self._toggle_following_pause)
                        action_controls.pauseAtLayerRequested.connect(self._toggle_pause_at_selected_layer)
''',
    "Pause signal connection",
)

# Resolve layer numbering through one helper, so scheduling and Preview following
# use the same G-code map / one-based preference / Z fallback semantics.
apply_marker = '''    def _apply_remote_status(
        self,
        print_stats: Dict[str, Any],
'''
resolver = '''    def _resolve_remote_layer_index(
        self,
        print_stats: Dict[str, Any],
        gcode_move: Dict[str, Any],
        filename: str,
        view=None,
    ) -> Tuple[Optional[int], str]:
        info = print_stats.get("info") or {}
        remote_layer = info.get("current_layer")
        target_layer: Optional[int] = None
        source = ""

        if remote_layer is not None:
            try:
                raw_remote_layer = int(remote_layer)
                if (
                    self._remote_index_filename == filename
                    and raw_remote_layer in self._remote_current_layer_map
                ):
                    target_layer = self._remote_current_layer_map[raw_remote_layer]
                    source = "Moonraker current_layer (G-code mapped)"
                else:
                    target_layer = raw_remote_layer
                    if self._pref_bool(self.PREF_ONE_BASED):
                        target_layer -= 1
                    source = "Moonraker current_layer"
            except (TypeError, ValueError):
                target_layer = None

        if target_layer is None and self._pref_bool(self.PREF_Z_FALLBACK) and view is not None:
            target_layer = self._layer_from_z(view, gcode_move)
            if target_layer is not None:
                source = "Z-height fallback"

        return target_layer, source

    def _apply_remote_status(
        self,
        print_stats: Dict[str, Any],
'''
text = replace_once(text, apply_marker, resolver, "Shared remote layer resolver")

# Clear print-local schedule when a new run is detected.
text = replace_once(
    text,
    '''            if new_job:
                self._remote_job_serial += 1
                self._clear_remote_gcode_index()
                self._remote_job_key = (filename, file_size, self._remote_job_serial)
''',
    '''            if new_job:
                self._remote_job_serial += 1
                self._clear_remote_gcode_index()
                self._last_observed_remote_layer = None
                self._clear_scheduled_pauses(abort_request=True)
                self._remote_job_key = (filename, file_size, self._remote_job_serial)
''',
    "New job pause cleanup",
)

# Replace the active-state / paused-follower section so layer observation continues
# while the user is inspecting a future layer in Cura.
old = '''        if state not in self.ACTIVE_STATES:
            self._toolhead_path_valid = False
            self._last_resolved_remote_layer = None
            self._selected_layer_eta_text = ""
            self._hide_toolhead_indicator()
            label = state or "unknown"
            suffix = f" — {filename}" if filename else ""
            self._set_status(f"Moonraker connected; printer is {label}{suffix}")
            return

        if self._following_paused:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="following paused; Moonraker polling continues",
                )
            )
            return

        # Keep the active G-code cached so an explicit Load current print can be
'''
new = '''        if state not in self.ACTIVE_STATES:
            self._toolhead_path_valid = False
            self._last_resolved_remote_layer = None
            self._last_observed_remote_layer = None
            self._selected_layer_eta_text = ""
            if self._scheduled_pause_layers:
                self._clear_scheduled_pauses(abort_request=True)
            self._hide_toolhead_indicator()
            label = state or "unknown"
            suffix = f" — {filename}" if filename else ""
            self._set_status(f"Moonraker connected; printer is {label}{suffix}")
            return

        # Observe the physical layer even while Cura following is manually paused.
        # That is what makes post-start pause scheduling possible: the user's Cura
        # slider is free to inspect a future layer while Moonraker keeps advancing.
        view = self._simulation_view()
        observed_layer, observed_source = self._resolve_remote_layer_index(
            print_stats, gcode_move, filename, view
        )
        if observed_layer is not None:
            observed_layer = max(0, int(observed_layer))
            self._last_observed_remote_layer = observed_layer
            self._maybe_trigger_scheduled_pause(observed_layer)
            if view is not None and hasattr(view, "getMaxLayers"):
                try:
                    observed_max = max(0, int(view.getMaxLayers()))
                    self._last_resolved_remote_layer = min(observed_layer, observed_max)
                except Exception:
                    pass

        if self._following_paused:
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._update_selected_layer_eta(view)
            self._set_status(
                self._active_status_text(
                    filename,
                    remote_layer=(self._last_observed_remote_layer + 1) if self._last_observed_remote_layer is not None else None,
                    total_layer=(print_stats.get("info") or {}).get("total_layer"),
                    detail="following paused; Moonraker polling continues",
                )
            )
            return

        # Keep the active G-code cached so an explicit Load current print can be
'''
text = replace_once(text, old, new, "Paused follower layer observation")

# Reuse the already-resolved observed layer later rather than calculating it twice.
old = '''        view = self._simulation_view()
        if view is None or not hasattr(view, "setLayer"):
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status("Connected, but Cura's SimulationView is unavailable")
            return

        self._maybe_switch_to_preview()

        info = print_stats.get("info") or {}
        remote_layer = info.get("current_layer")
        total_layer = info.get("total_layer")

        target_layer: Optional[int] = None
        source = ""

        if remote_layer is not None:
            try:
                raw_remote_layer = int(remote_layer)
                if (
                    self._remote_index_filename == filename
                    and raw_remote_layer in self._remote_current_layer_map
                ):
                    # Prefer the CURRENT_LAYER values embedded in the actual G-code.
                    # This makes layer numbering self-describing and avoids an
                    # off-by-one mismatch if the preference namespace was reset or
                    # a slicer/macro reports zero-based layers instead of one-based.
                    target_layer = self._remote_current_layer_map[raw_remote_layer]
                    source = "Moonraker current_layer (G-code mapped)"
                else:
                    target_layer = raw_remote_layer
                    if self._pref_bool(self.PREF_ONE_BASED):
                        target_layer -= 1
                    source = "Moonraker current_layer"
            except (TypeError, ValueError):
                target_layer = None

        if target_layer is None and self._pref_bool(self.PREF_Z_FALLBACK):
            target_layer = self._layer_from_z(view, gcode_move)
            if target_layer is not None:
                source = "Z-height fallback"
'''
new = '''        if view is None or not hasattr(view, "setLayer"):
            self._toolhead_path_valid = False
            self._hide_toolhead_indicator()
            self._set_status("Connected, but Cura's SimulationView is unavailable")
            return

        self._maybe_switch_to_preview()

        info = print_stats.get("info") or {}
        total_layer = info.get("total_layer")
        target_layer = observed_layer
        source = observed_source
'''
text = replace_once(text, old, new, "Reuse shared layer resolution")

path.write_text(text, encoding="utf-8")


# New regression tests. Auto-discovery means no CI workflow list needs editing.
test_path = ROOT / "tests" / "test_pause_at_layer.py"
test_path.write_text('''import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
FOLLOWER = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")
QML = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")
CONFIG = (PLUGINS / "PrinterConfig.py").read_text(encoding="utf-8")
PROTOCOL = (PLUGINS / "MoonrakerProtocol.py").read_text(encoding="utf-8")


class PauseAtLayerTests(unittest.TestCase):
    def test_pause_schedule_is_current_print_only(self):
        self.assertIn("self._scheduled_pause_layers: set[int] = set()", FOLLOWER)
        self.assertIn("self._clear_scheduled_pauses(abort_request=True)", FOLLOWER)
        self.assertNotIn("pause_at_layer", CONFIG)

    def test_preview_can_toggle_multiple_future_layers(self):
        self.assertIn("pauseAtLayerRequested", QML)
        self.assertIn('"Enable pause at layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause at layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn("Scheduled PAUSE layers:", FOLLOWER)
        self.assertIn("selected_layer > current", FOLLOWER)

    def test_pause_uses_normal_klipper_macro_through_moonraker(self):
        self.assertIn("def gcode_script_endpoint", PROTOCOL)
        self.assertIn('json.dumps({"script": "PAUSE"}', FOLLOWER)
        self.assertIn("self._pause_network.post", FOLLOWER)

    def test_polling_can_cross_a_short_target_layer_without_missing_pause(self):
        self.assertIn("layer <= current_layer", FOLLOWER)
        self.assertIn("due = sorted", FOLLOWER)
        self.assertIn("for layer in due:", FOLLOWER)

    def test_layer_observation_continues_while_preview_following_is_paused(self):
        observation = FOLLOWER.index("observed_layer, observed_source = self._resolve_remote_layer_index")
        paused = FOLLOWER.index("if self._following_paused:", observation)
        trigger = FOLLOWER.index("self._maybe_trigger_scheduled_pause(observed_layer)", observation)
        self.assertLess(observation, paused)
        self.assertLess(trigger, paused)
        self.assertIn("self._update_selected_layer_eta(view)", FOLLOWER[paused:paused + 1200])

    def test_pause_command_is_guarded_by_print_and_lifecycle_identity(self):
        self.assertIn("lifecycle_generation != self._lifecycle_generation", FOLLOWER)
        self.assertIn("job_key != self._remote_job_key", FOLLOWER)
        self.assertIn("request_generation != self._pause_reply_generation", FOLLOWER)


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print("Applied Preview pause-at-layer feature")
