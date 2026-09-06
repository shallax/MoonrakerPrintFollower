from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


follower = ROOT / "plugins" / "MoonrakerPrintFollower.py"
replace_once(
    follower,
    '''        self._selected_layer_eta_text = ""
        self._last_speed_factor = 1.0
        self._scene = None
''',
    '''        self._selected_layer_eta_text = ""
        self._last_speed_factor = 1.0
        # ETA estimates use the slicer's cumulative layer timings, but keep an
        # independent Moonraker print-duration anchor for the current layer.
        # This lets ETAs continue counting down while Cura Preview is detached
        # and its own path slider is no longer being advanced by the follower.
        self._eta_anchor_layer: Optional[int] = None
        self._eta_anchor_print_duration: Optional[float] = None
        self._eta_current_print_duration: Optional[float] = None
        self._scene = None
''',
    "ETA anchor state",
)
replace_once(
    follower,
    '''        items = [{"layer": layer + 1} for layer in sorted(self._scheduled_pause_layers)]
        layers = ", ".join(str(item["layer"]) for item in items)
''',
    '''        items = []
        for layer in sorted(self._scheduled_pause_layers):
            remaining = self._estimate_layer_boundary_remaining(layer, end_of_layer=True)
            eta = (
                f"in {self._format_preview_duration(remaining)}"
                if remaining is not None
                else "ETA unavailable"
            )
            items.append({"layer": layer + 1, "eta": eta})
        layers = ", ".join(str(item["layer"]) for item in items)
''',
    "scheduled pause ETA items",
)
replace_once(
    follower,
    '''    @staticmethod
    def _format_preview_duration(seconds: float) -> str:
        total = max(0, int(round(float(seconds))))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _update_selected_layer_eta(self, view=None) -> None:
        """Show ETA to the manually selected Cura layer while following is paused."""
        text = ""
        if self._following_paused and self._last_remote_state in self.ACTIVE_STATES:
            if view is None:
                view = self._simulation_view()
            index = self._remote_index_data
            times = list(getattr(index, "layer_elapsed_times", []) or []) if index is not None else []
            current_layer = self._last_resolved_remote_layer
            if view is not None and current_layer is not None:
                try:
                    selected_layer = max(0, int(view.getCurrentLayer()))
                except Exception:
                    selected_layer = None
                if selected_layer is not None:
                    human_layer = selected_layer + 1
                    if selected_layer < current_layer:
                        text = f"Selected layer {human_layer} — already printed"
                    elif selected_layer == current_layer:
                        text = f"Selected layer {human_layer} — current print layer"
                    elif selected_layer < len(times):
                        def layer_start_elapsed(layer: int) -> Optional[float]:
                            if layer <= 0:
                                return 0.0
                            boundary = layer - 1
                            if 0 <= boundary < len(times) and times[boundary] is not None:
                                return float(times[boundary])
                            return None

                        target_elapsed = layer_start_elapsed(selected_layer)
                        current_start = layer_start_elapsed(current_layer)
                        current_end = (
                            float(times[current_layer])
                            if 0 <= current_layer < len(times) and times[current_layer] is not None
                            else None
                        )
                        if target_elapsed is not None and current_start is not None:
                            fraction = 0.0
                            if self._path_progress_layer == current_layer and self._path_progress_fraction is not None:
                                fraction = max(0.0, min(1.0, float(self._path_progress_fraction)))
                            planned_now = current_start
                            if current_end is not None and current_end >= current_start:
                                planned_now += (current_end - current_start) * fraction
                            remaining = max(0.0, target_elapsed - planned_now)
                            speed = max(0.05, float(self._last_speed_factor or 1.0))
                            remaining /= speed
                            finish = datetime.now().astimezone() + timedelta(seconds=remaining)
                            clock = finish.strftime("%a %H:%M") if remaining >= 20 * 3600 else finish.strftime("%H:%M")
                            text = (
                                f"Selected layer {human_layer} — in {self._format_preview_duration(remaining)} "
                                f"· ~{clock}"
                            )
                        else:
                            text = f"Selected layer {human_layer} — ETA unavailable (no layer timing)"
                    else:
                        text = f"Selected layer {human_layer} — ETA unavailable"

        if text == self._selected_layer_eta_text:
            return
        self._selected_layer_eta_text = text
        self._sync_preview_button_state()
''',
    '''    @staticmethod
    def _format_preview_duration(seconds: float) -> str:
        total = max(0, int(round(float(seconds))))
        hours, remainder = divmod(total, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _estimate_layer_boundary_remaining(
        self, target_layer: int, *, end_of_layer: bool
    ) -> Optional[float]:
        """Estimate seconds until a layer starts or finishes.

        ``layer_elapsed_times[n]`` is the slicer's cumulative planned elapsed
        time at the end of zero-based layer ``n``. The current position is
        refined by whichever progress signal is furthest ahead: exact path
        progress while attached, or Moonraker print-duration elapsed within the
        current layer while Preview is detached. Taking the maximum keeps the
        ETA monotonic and prevents an old Preview fraction from making it jump
        backwards after detaching.
        """
        try:
            target_layer = max(0, int(target_layer))
        except (TypeError, ValueError):
            return None
        index = self._remote_index_data
        times = list(getattr(index, "layer_elapsed_times", []) or []) if index is not None else []
        current_layer = (
            self._last_observed_remote_layer
            if self._last_observed_remote_layer is not None
            else self._last_resolved_remote_layer
        )
        if current_layer is None or not times:
            return None
        current_layer = max(0, int(current_layer))

        def layer_start_elapsed(layer: int) -> Optional[float]:
            if layer <= 0:
                return 0.0
            boundary = layer - 1
            if 0 <= boundary < len(times) and times[boundary] is not None:
                try:
                    return float(times[boundary])
                except (TypeError, ValueError):
                    return None
            return None

        target_boundary = target_layer if end_of_layer else target_layer - 1
        if target_boundary < 0:
            target_elapsed = 0.0
        elif target_boundary < len(times) and times[target_boundary] is not None:
            try:
                target_elapsed = float(times[target_boundary])
            except (TypeError, ValueError):
                return None
        else:
            return None

        current_start = layer_start_elapsed(current_layer)
        if current_start is None:
            return None
        current_end = None
        if 0 <= current_layer < len(times) and times[current_layer] is not None:
            try:
                current_end = float(times[current_layer])
            except (TypeError, ValueError):
                current_end = None

        speed = max(0.05, float(self._last_speed_factor or 1.0))
        fractions: List[float] = []
        if self._path_progress_layer == current_layer and self._path_progress_fraction is not None:
            try:
                fractions.append(max(0.0, min(1.0, float(self._path_progress_fraction))))
            except (TypeError, ValueError):
                pass
        if (
            self._eta_anchor_layer == current_layer
            and self._eta_anchor_print_duration is not None
            and self._eta_current_print_duration is not None
            and current_end is not None
            and current_end > current_start
        ):
            actual_into_layer = max(
                0.0,
                float(self._eta_current_print_duration) - float(self._eta_anchor_print_duration),
            )
            planned_layer_duration = current_end - current_start
            fractions.append(
                max(0.0, min(1.0, actual_into_layer * speed / planned_layer_duration))
            )
        fraction = max(fractions) if fractions else 0.0
        planned_now = current_start
        if current_end is not None and current_end >= current_start:
            planned_now += (current_end - current_start) * fraction
        remaining_planned = max(0.0, target_elapsed - planned_now)
        return remaining_planned / speed

    def _update_selected_layer_eta(self, view=None) -> None:
        """Show a live ETA for the layer selected in Cura Preview."""
        text = ""
        if self._last_remote_state in self.ACTIVE_STATES:
            if view is None:
                view = self._simulation_view()
            current_layer = (
                self._last_observed_remote_layer
                if self._last_observed_remote_layer is not None
                else self._last_resolved_remote_layer
            )
            if view is not None and current_layer is not None:
                try:
                    selected_layer = max(0, int(view.getCurrentLayer()))
                except Exception:
                    selected_layer = None
                if selected_layer is not None:
                    human_layer = selected_layer + 1
                    if selected_layer < current_layer:
                        text = f"Selected layer {human_layer} — already printed"
                    elif selected_layer == current_layer:
                        if self._following_paused:
                            text = f"Selected layer {human_layer} — current print layer"
                    else:
                        remaining = self._estimate_layer_boundary_remaining(
                            selected_layer, end_of_layer=False
                        )
                        if remaining is None:
                            text = f"Selected layer {human_layer} — ETA unavailable (no layer timing)"
                        else:
                            finish = datetime.now().astimezone() + timedelta(seconds=remaining)
                            clock = (
                                finish.strftime("%a %H:%M")
                                if remaining >= 20 * 3600
                                else finish.strftime("%H:%M")
                            )
                            text = (
                                f"Selected layer {human_layer} — in {self._format_preview_duration(remaining)} "
                                f"· ~{clock}"
                            )

        if text == self._selected_layer_eta_text:
            return
        self._selected_layer_eta_text = text
        self._sync_preview_button_state()
''',
    "live layer ETA estimator",
)
replace_once(
    follower,
    '''        previous_filename = self._last_remote_filename
        self._update_remote_job_identity(print_stats, virtual_sdcard)
        try:
            reported_size = int(virtual_sdcard.get("file_size") or 0)
''',
    '''        previous_filename = self._last_remote_filename
        self._update_remote_job_identity(print_stats, virtual_sdcard)
        try:
            self._eta_current_print_duration = max(0.0, float(print_stats.get("print_duration") or 0.0))
        except (TypeError, ValueError):
            self._eta_current_print_duration = None
        try:
            reported_size = int(virtual_sdcard.get("file_size") or 0)
''',
    "Moonraker ETA print duration",
)
replace_once(
    follower,
    '''        if state not in self.ACTIVE_STATES:
            self._toolhead_path_valid = False
            self._last_resolved_remote_layer = None
            self._last_observed_remote_layer = None
            self._selected_layer_eta_text = ""
''',
    '''        if state not in self.ACTIVE_STATES:
            self._toolhead_path_valid = False
            self._last_resolved_remote_layer = None
            self._last_observed_remote_layer = None
            self._eta_anchor_layer = None
            self._eta_anchor_print_duration = None
            self._eta_current_print_duration = None
            self._selected_layer_eta_text = ""
''',
    "inactive ETA reset",
)
replace_once(
    follower,
    '''        if observed_layer is not None:
            observed_layer = max(0, int(observed_layer))
            self._last_observed_remote_layer = observed_layer
            self._maybe_trigger_scheduled_pause(observed_layer)
''',
    '''        if observed_layer is not None:
            observed_layer = max(0, int(observed_layer))
            if self._eta_anchor_layer != observed_layer:
                self._eta_anchor_layer = observed_layer
                self._eta_anchor_print_duration = self._eta_current_print_duration
            self._last_observed_remote_layer = observed_layer
            self._maybe_trigger_scheduled_pause(observed_layer)
''',
    "ETA layer anchor update",
)
replace_once(
    follower,
    '''        self._path_progress_layer = None
        self._path_progress_fraction = None
        self._last_resolved_remote_layer = None
        self._selected_layer_eta_text = ""
''',
    '''        self._path_progress_layer = None
        self._path_progress_fraction = None
        self._last_resolved_remote_layer = None
        self._eta_anchor_layer = None
        self._eta_anchor_print_duration = None
        self._eta_current_print_duration = None
        self._selected_layer_eta_text = ""
''',
    "remote index ETA reset",
)

preview = ROOT / "plugins" / "PreviewActionPanelControls.qml"
replace_once(
    preview,
    '''                        property int pauseLayer: Number(modelData.layer)

                        UM.Label
                        {
                            width: Math.max(0, parent.width - removePauseButton.width - parent.spacing)
                            height: parent.height
                            text: "End of layer " + parent.pauseLayer
''',
    '''                        property int pauseLayer: Number(modelData.layer)
                        property string pauseEta: String(modelData.eta || "")

                        UM.Label
                        {
                            width: Math.max(0, parent.width - removePauseButton.width - parent.spacing)
                            height: parent.height
                            text: "End of layer " + parent.pauseLayer
                                + (parent.pauseEta.length > 0 ? " · " + parent.pauseEta : "")
''',
    "scheduled pause ETA display",
)

typed = ROOT / "plugins" / "MoonrakerMonitorTypedControls.py"
replace_once(
    typed,
    '''        self._bed_mesh_snapshot: Dict[str, Any] = {}
        self._bed_mesh_fingerprint: Optional[Tuple[Any, ...]] = None
        self._bound_preview_control_ids: set[int] = set()
''',
    '''        self._bed_mesh_snapshot: Dict[str, Any] = {}
        self._bed_mesh_fingerprint: Optional[Tuple[Any, ...]] = None
        self._bed_mesh_profile_names: List[str] = []
        self._bound_preview_control_ids: set[int] = set()
''',
    "bed mesh profile state",
)
replace_once(
    typed,
    '''        self._pwm_output_items = self._build_pwm_output_items()
        self._mcu_items = self._build_mcu_items()
        self._update_bed_mesh_snapshot(self._aux_status.get("bed_mesh"))
''',
    '''        self._pwm_output_items = self._build_pwm_output_items()
        self._mcu_items = self._build_mcu_items()
        bed_mesh_status = self._aux_status.get("bed_mesh")
        self._bed_mesh_profile_names = self._bed_mesh_profiles_from_status(bed_mesh_status)
        self._update_bed_mesh_snapshot(bed_mesh_status)
''',
    "bed mesh profile refresh",
)
replace_once(
    typed,
    '''    @staticmethod
    def _normalise_bed_mesh_matrix(raw: Any) -> Optional[List[List[float]]]:
''',
    '''    @staticmethod
    def _bed_mesh_profiles_from_status(status: Any) -> List[str]:
        if not isinstance(status, dict):
            return []
        raw = status.get("profiles")
        names: List[str] = []
        if isinstance(raw, dict):
            names = [str(name).strip() for name in raw.keys()]
        elif isinstance(raw, (list, tuple, set)):
            for item in raw:
                if isinstance(item, str):
                    names.append(item.strip())
                elif isinstance(item, dict):
                    candidate = str(item.get("name") or item.get("profile") or "").strip()
                    if candidate:
                        names.append(candidate)
        names = sorted({name for name in names if name}, key=str.casefold)
        active = str(status.get("profile_name") or "").strip()
        if active in names:
            names.remove(active)
            names.insert(0, active)
        elif "default" in names:
            names.remove("default")
            names.insert(0, "default")
        return names

    @staticmethod
    def _normalise_bed_mesh_matrix(raw: Any) -> Optional[List[List[float]]]:
''',
    "bed mesh profile parser",
)
replace_once(
    typed,
    '''        self._bed_mesh_snapshot = {}
        self._bed_mesh_fingerprint = None
        setattr(self._follower, "_bed_mesh_snapshot", {})
''',
    '''        self._bed_mesh_snapshot = {}
        self._bed_mesh_fingerprint = None
        self._bed_mesh_profile_names = []
        setattr(self._follower, "_bed_mesh_snapshot", {})
''',
    "bed mesh profile machine reset",
)
replace_once(
    typed,
    '''    @pyqtProperty(str, notify=typedControlsChanged)
    def bedMeshProfile(self) -> str:
        if not self._bed_mesh_snapshot:
            return ""
        return str(self._bed_mesh_snapshot.get("profile") or "Current mesh")

''',
    '''    @pyqtProperty(str, notify=typedControlsChanged)
    def bedMeshProfile(self) -> str:
        if not self._bed_mesh_snapshot:
            return ""
        return str(self._bed_mesh_snapshot.get("profile") or "Current mesh")

    @pyqtProperty(QVariant, notify=typedControlsChanged)
    def bedMeshProfileNames(self) -> QVariant:
        return QVariant(list(self._bed_mesh_profile_names))

    @pyqtSlot(str)
    def loadBedMeshProfile(self, profile_name: str) -> None:
        name = str(profile_name or "").strip()
        if (
            not name
            or name not in self._bed_mesh_profile_names
            or not self._has_bed_mesh
            or not self.canRunSetup
        ):
            return
        safe = name.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")
        self._send_gcode_action(f"Load mesh {name}", f'BED_MESH_PROFILE LOAD="{safe}"')

    @pyqtSlot()
    def clearBedMesh(self) -> None:
        if self._has_bed_mesh and self.canRunSetup and self.bedMeshAvailable:
            self._send_gcode_action("Clear bed mesh", "BED_MESH_CLEAR")

''',
    "bed mesh profile and clear actions",
)

dashboard = ROOT / "plugins" / "MoonrakerMonitorDashboard.qml"
replace_once(
    dashboard,
    '''                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.hasBedMesh
                                text: "Bed mesh"
                                enabled: root.printer != null && root.printer.canRunSetup
                                onClicked: root.printer.calibrateBedMesh()
                            }
                        }
                        UM.Label
                        {
                            visible: root.printer != null && root.printer.printActive
                            text: "Homing and calibration controls are disabled during a print."
''',
    '''                            Cura.SecondaryButton
                            {
                                Layout.fillWidth: true
                                visible: root.printer != null && root.printer.hasBedMesh
                                text: "Calibrate mesh"
                                tooltip: "Probe the bed now and replace the active mesh with a newly calibrated one."
                                enabled: root.printer != null && root.printer.canRunSetup
                                onClicked: root.printer.calibrateBedMesh()
                            }
                        }

                        RowLayout
                        {
                            visible: root.printer != null && root.printer.hasBedMesh
                            Layout.fillWidth: true
                            spacing: UM.Theme.getSize("default_margin").width / 2

                            Cura.ComboBox
                            {
                                id: bedMeshProfileSelector
                                Layout.fillWidth: true
                                model: root.printer != null ? root.printer.bedMeshProfileNames : []
                                enabled: root.printer != null
                                    && root.printer.canRunSetup
                                    && root.printer.bedMeshProfileNames.length > 0
                            }

                            Cura.SecondaryButton
                            {
                                text: "Load saved mesh"
                                enabled: root.printer != null
                                    && root.printer.canRunSetup
                                    && bedMeshProfileSelector.currentText.length > 0
                                tooltip: "Load the selected saved Klipper bed mesh without probing the bed again."
                                onClicked: root.printer.loadBedMeshProfile(bedMeshProfileSelector.currentText)
                            }

                            Cura.SecondaryButton
                            {
                                text: "Clear mesh"
                                enabled: root.printer != null
                                    && root.printer.canRunSetup
                                    && root.printer.bedMeshAvailable
                                tooltip: "Clear the active Klipper bed mesh and remove its Z adjustment."
                                onClicked: root.printer.clearBedMesh()
                            }
                        }
                        UM.Label
                        {
                            visible: root.printer != null && root.printer.hasBedMesh
                                && root.printer.bedMeshProfileNames.length === 0
                            text: "No saved bed mesh profiles reported by Klipper."
                            color: UM.Theme.getColor("text_inactive")
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                        }
                        UM.Label
                        {
                            visible: root.printer != null && root.printer.printActive
                            text: "Homing and bed-mesh setup controls are disabled during a print."
''',
    "bed mesh setup UX",
)

release_test = ROOT / "tests" / "test_eta_bed_mesh_release.py"
release_test.write_text('''import pathlib\nimport unittest\n\nROOT = pathlib.Path(__file__).resolve().parents[1]\nPLUGINS = ROOT / "plugins"\nFOLLOWER = (PLUGINS / "MoonrakerPrintFollower.py").read_text(encoding="utf-8")\nPREVIEW = (PLUGINS / "PreviewActionPanelControls.qml").read_text(encoding="utf-8")\nTYPED = (PLUGINS / "MoonrakerMonitorTypedControls.py").read_text(encoding="utf-8")\nDASHBOARD = (PLUGINS / "MoonrakerMonitorDashboard.qml").read_text(encoding="utf-8")\n\n\nclass EtaAndBedMeshReleaseTests(unittest.TestCase):\n    def test_selected_layer_eta_uses_live_remote_layer_and_duration_anchor(self):\n        self.assertIn("def _estimate_layer_boundary_remaining", FOLLOWER)\n        self.assertIn("self._last_observed_remote_layer", FOLLOWER)\n        self.assertIn("self._eta_anchor_print_duration", FOLLOWER)\n        self.assertIn("self._eta_current_print_duration", FOLLOWER)\n        self.assertIn("actual_into_layer * speed / planned_layer_duration", FOLLOWER)\n        self.assertIn("if self._last_remote_state in self.ACTIVE_STATES:", FOLLOWER)\n        self.assertNotIn("if self._following_paused and self._last_remote_state in self.ACTIVE_STATES:", FOLLOWER)\n\n    def test_each_scheduled_pause_has_a_live_end_of_layer_eta(self):\n        self.assertIn("end_of_layer=True", FOLLOWER)\n        self.assertIn('items.append({"layer": layer + 1, "eta": eta})', FOLLOWER)\n        self.assertIn("property string pauseEta", PREVIEW)\n        self.assertIn('" · " + parent.pauseEta', PREVIEW)\n\n    def test_saved_bed_mesh_profiles_are_discovered_and_loadable(self):\n        self.assertIn("_bed_mesh_profiles_from_status", TYPED)\n        self.assertIn('status.get("profiles")', TYPED)\n        self.assertIn("bedMeshProfileNames", TYPED)\n        self.assertIn("loadBedMeshProfile", TYPED)\n        self.assertIn('BED_MESH_PROFILE LOAD="{safe}"', TYPED)\n        self.assertIn('text: "Load saved mesh"', DASHBOARD)\n        self.assertIn("bedMeshProfileSelector", DASHBOARD)\n\n    def test_active_bed_mesh_can_be_cleared_without_deleting_saved_profiles(self):\n        self.assertIn("def clearBedMesh", TYPED)\n        self.assertIn('"BED_MESH_CLEAR"', TYPED)\n        self.assertIn('text: "Clear mesh"', DASHBOARD)\n        self.assertIn("root.printer.bedMeshAvailable", DASHBOARD)\n        self.assertIn('text: "Calibrate mesh"', DASHBOARD)\n\n\nif __name__ == "__main__":\n    unittest.main()\n''', encoding="utf-8")

print("Applied live pause ETAs plus saved/clear bed mesh controls")
