from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# Core: make end-of-layer trigger semantics directly unit-testable.
path = ROOT / "plugins" / "Core.py"
text = path.read_text(encoding="utf-8")
marker = '''\n\ndef preview_override_kind(\n'''
helper = '''\n\ndef due_end_of_layer_pauses(scheduled_layers, current_layer: int):\n    """Return scheduled zero-based layers whose *end* has been crossed.\n\n    A target layer is due only after Moonraker has advanced to a strictly later\n    layer. Reaching the target layer itself must never pause at its beginning.\n    """\n    try:\n        current = int(current_layer)\n    except (TypeError, ValueError):\n        return []\n\n    due = []\n    for raw_layer in scheduled_layers or ():\n        try:\n            layer = int(raw_layer)\n        except (TypeError, ValueError):\n            continue\n        if layer < current:\n            due.append(layer)\n    return sorted(set(due))\n\n\ndef preview_override_kind(\n'''
text = replace_once(text, marker, helper, "Core end-of-layer helper")
path.write_text(text, encoding="utf-8")


# Follower: list management, final-layer guard, and strict end-of-layer triggering.
path = ROOT / "plugins" / "MoonrakerPrintFollower.py"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    'from .Core import OperationContext, OperationPhase, RemoteFileIdentity, preview_override_kind\n',
    'from .Core import (\n    OperationContext,\n    OperationPhase,\n    RemoteFileIdentity,\n    due_end_of_layer_pauses,\n    preview_override_kind,\n)\n',
    "Follower Core import",
)

text = replace_once(
    text,
    '''                controls.setProperty("pauseAtLayerScheduled", pause_state["scheduled"])
                controls.setProperty("pauseAtLayerSummary", pause_state["summary"])
''',
    '''                controls.setProperty("pauseAtLayerScheduled", pause_state["scheduled"])
                controls.setProperty("pauseAtLayerSummary", pause_state["summary"])
                controls.setProperty("pauseAtLayerItems", pause_state["items"])
                controls.setProperty("pauseAtLayerUnavailableText", pause_state["unavailable"])
''',
    "Preview pause list sync",
)

old_state = '''    def _pause_at_layer_preview_state(self) -> Dict[str, Any]:
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
'''
new_state = '''    def _pause_at_layer_preview_state(self) -> Dict[str, Any]:
        active = bool(
            self._last_remote_state in self.ACTIVE_STATES
            and self._remote_job_key is not None
        )
        candidate = 0
        selected_layer: Optional[int] = None
        max_layer: Optional[int] = None
        view = self._simulation_view() if active else None
        if view is not None:
            try:
                selected_layer = max(0, int(view.getCurrentLayer()))
                candidate = selected_layer + 1
            except Exception:
                selected_layer = None
            try:
                if hasattr(view, "getMaxLayers"):
                    max_layer = max(0, int(view.getMaxLayers()))
            except Exception:
                max_layer = None

        current = self._last_observed_remote_layer
        scheduled = bool(selected_layer is not None and selected_layer in self._scheduled_pause_layers)
        is_final_layer = bool(
            selected_layer is not None
            and max_layer is not None
            and selected_layer >= max_layer
        )
        can_toggle = bool(
            active
            and selected_layer is not None
            and current is not None
            and selected_layer > current
            and not is_final_layer
        )

        unavailable = ""
        if active and selected_layer is not None and not scheduled and not can_toggle:
            if current is None:
                unavailable = "Waiting for current print layer"
            elif selected_layer <= current:
                unavailable = f"Layer {selected_layer + 1} already reached"
            elif is_final_layer:
                unavailable = "Final layer ends the print"
            else:
                unavailable = "Select a future layer"

        items = [{"layer": layer + 1} for layer in sorted(self._scheduled_pause_layers)]
        layers = ", ".join(str(item["layer"]) for item in items)
        summary = f"End-of-layer PAUSE: {layers}" if layers else ""
        return {
            "active": active,
            "candidate": candidate,
            "canToggle": can_toggle,
            "scheduled": scheduled,
            "summary": summary,
            "items": items,
            "unavailable": unavailable,
        }

    def _toggle_pause_at_selected_layer(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return

        # A scheduled pause can always be removed until its trigger has fired,
        # including while the printer is physically printing that target layer.
        if layer in self._scheduled_pause_layers:
            self._scheduled_pause_layers.remove(layer)
            self._set_status(f"Removed end-of-layer PAUSE after layer {layer + 1}")
            self._sync_preview_button_state()
            return

        state = self._pause_at_layer_preview_state()
        if (
            int(state.get("candidate") or 0) != layer + 1
            or not bool(state.get("canToggle"))
        ):
            self._sync_preview_button_state()
            return

        self._scheduled_pause_layers.add(layer)
        self._set_status(f"PAUSE scheduled for end of layer {layer + 1}")
        self._sync_preview_button_state()

    def _remove_scheduled_pause(self, human_layer: int) -> None:
        try:
            layer = int(human_layer) - 1
        except (TypeError, ValueError):
            return
        if layer not in self._scheduled_pause_layers:
            self._sync_preview_button_state()
            return
        self._scheduled_pause_layers.remove(layer)
        self._set_status(f"Removed end-of-layer PAUSE after layer {layer + 1}")
        self._sync_preview_button_state()

    def _clear_scheduled_pauses_from_preview(self) -> None:
        count = len(self._scheduled_pause_layers)
        if count <= 0:
            self._sync_preview_button_state()
            return
        self._scheduled_pause_layers.clear()
        suffix = "pause" if count == 1 else "pauses"
        self._set_status(f"Cleared {count} scheduled end-of-layer {suffix}")
        self._sync_preview_button_state()

    def _clear_scheduled_pauses(self, *, abort_request: bool = False) -> None:
        self._scheduled_pause_layers.clear()
        if abort_request:
            self._abort_pause_reply()
        self._sync_preview_button_state()
'''
text = replace_once(text, old_state, new_state, "Follower pause management state")

old_trigger = '''        due = sorted(layer for layer in self._scheduled_pause_layers if layer <= current_layer)
        if not due:
            return
        # One PAUSE is sufficient if polling skipped across more than one scheduled
        # very short layer. Consume every already-reached target, but keep later ones.
        for layer in due:
            self._scheduled_pause_layers.discard(layer)
        target_layer = due[0]
        self._sync_preview_button_state()
        self._send_scheduled_pause(target_layer, current_layer)
'''
new_trigger = '''        # End-of-layer semantics are deliberately strict: merely arriving at a
        # scheduled layer must not pause at its beginning. A target becomes due
        # only after Moonraker advances to a later layer. If polling skips across
        # several very short layers, one PAUSE is sufficient and all crossed
        # targets are consumed together.
        due = due_end_of_layer_pauses(self._scheduled_pause_layers, current_layer)
        if not due:
            return
        for layer in due:
            self._scheduled_pause_layers.discard(layer)
        target_layer = due[0]
        self._sync_preview_button_state()
        self._send_scheduled_pause(target_layer, current_layer)
'''
text = replace_once(text, old_trigger, new_trigger, "End-of-layer trigger")

text = text.replace(
    '"Moonraker Print Follower skipped overlapping PAUSE request for layer %d",',
    '"Moonraker Print Follower skipped overlapping end-of-layer PAUSE request after layer %d",',
)
text = text.replace(
    'self._set_status(f"Could not PAUSE at layer {target_layer + 1}: Moonraker URL unavailable")',
    'self._set_status(f"Could not PAUSE after layer {target_layer + 1}: Moonraker URL unavailable")',
)
text = text.replace(
    '"Moonraker Print Follower requesting PAUSE for scheduled layer %d (observed layer %d)",',
    '"Moonraker Print Follower requesting PAUSE after scheduled layer %d (observed layer %d)",',
)
text = text.replace(
    'f"PAUSE at layer {target_layer + 1} failed: {reply.errorString()}"',
    'f"PAUSE after layer {target_layer + 1} failed: {reply.errorString()}"',
)
old_success = '''            suffix = "" if current_layer == target_layer else f" (detected at layer {current_layer + 1})"
            self._set_status(f"PAUSE requested at layer {target_layer + 1}{suffix}")
'''
new_success = '''            suffix = f" (transition observed at layer {current_layer + 1})"
            self._set_status(f"PAUSE requested after layer {target_layer + 1}{suffix}")
'''
text = replace_once(text, old_success, new_success, "Pause success wording")

text = replace_once(
    text,
    '''                        action_controls.pauseClicked.connect(self._toggle_following_pause)
                        action_controls.pauseAtLayerRequested.connect(self._toggle_pause_at_selected_layer)
''',
    '''                        action_controls.pauseClicked.connect(self._toggle_following_pause)
                        action_controls.pauseAtLayerRequested.connect(self._toggle_pause_at_selected_layer)
                        action_controls.removePauseAtLayerRequested.connect(self._remove_scheduled_pause)
                        action_controls.clearPauseAtLayersRequested.connect(self._clear_scheduled_pauses_from_preview)
''',
    "Pause management signal connections",
)
path.write_text(text, encoding="utf-8")


# Preview QML: explicit end-of-layer wording and a manageable scheduled-pause list.
path = ROOT / "plugins" / "PreviewActionPanelControls.qml"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''    property bool pauseAtLayerScheduled: false
    property string pauseAtLayerSummary: ""
''',
    '''    property bool pauseAtLayerScheduled: false
    property string pauseAtLayerSummary: ""
    property var pauseAtLayerItems: []
    property string pauseAtLayerUnavailableText: ""
''',
    "QML pause list properties",
)
text = replace_once(
    text,
    '''    signal bedMeshVisibilityRequested(bool visible)
    signal pauseAtLayerRequested(int layer)
''',
    '''    signal bedMeshVisibilityRequested(bool visible)
    signal pauseAtLayerRequested(int layer)
    signal removePauseAtLayerRequested(int layer)
    signal clearPauseAtLayersRequested()
''',
    "QML pause management signals",
)
old_ui = '''            Cura.SecondaryButton
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
new_ui = '''            Cura.SecondaryButton
            {
                id: pauseAtLayerButton
                visible: base.hasToolpath && base.followingEnabled && base.pauseAtLayerActive
                width: parent.width
                height: visible ? UM.Theme.getSize("action_button").height : 0
                enabled: base.pauseAtLayerScheduled || base.pauseAtLayerCanToggle
                text: base.pauseAtLayerCandidate <= 0
                    ? "Pause at end of selected layer"
                    : (base.pauseAtLayerScheduled
                        ? "Remove pause after layer " + base.pauseAtLayerCandidate
                        : (base.pauseAtLayerCanToggle
                            ? "Enable pause at end of layer " + base.pauseAtLayerCandidate
                            : (base.pauseAtLayerUnavailableText.length > 0
                                ? base.pauseAtLayerUnavailableText
                                : "Pause unavailable")))
                tooltip: base.pauseAtLayerScheduled
                    ? "Remove the scheduled end-of-layer PAUSE."
                    : (base.pauseAtLayerCanToggle
                        ? "Call the Klipper PAUSE macro once this layer has finished and Moonraker advances to the following layer."
                        : "Scroll Cura Preview to a future non-final layer to schedule an end-of-layer PAUSE.")
                fixedWidthMode: true
                onClicked: base.pauseAtLayerRequested(base.pauseAtLayerCandidate)
            }

            Column
            {
                id: scheduledPauseList
                visible: base.hasToolpath
                    && base.followingEnabled
                    && base.pauseAtLayerActive
                    && base.pauseAtLayerItems.length > 0
                width: parent.width
                height: visible ? implicitHeight : 0
                spacing: 2 * screenScaleFactor

                UM.Label
                {
                    width: parent.width
                    text: "Enabled pauses"
                    color: UM.Theme.getColor("text")
                    font: UM.Theme.getFont("default_bold")
                }

                Repeater
                {
                    model: base.pauseAtLayerItems
                    delegate: Row
                    {
                        width: scheduledPauseList.width
                        height: UM.Theme.getSize("action_button").height
                        spacing: base.buttonSpacing
                        property int pauseLayer: Number(modelData.layer)

                        UM.Label
                        {
                            width: Math.max(0, parent.width - removePauseButton.width - parent.spacing)
                            height: parent.height
                            text: "End of layer " + parent.pauseLayer
                            color: UM.Theme.getColor("text")
                            font: UM.Theme.getFont("default")
                            verticalAlignment: Text.AlignVCenter
                            elide: Text.ElideRight
                        }

                        Cura.SecondaryButton
                        {
                            id: removePauseButton
                            width: 88 * screenScaleFactor
                            height: parent.height
                            text: "Remove"
                            tooltip: "Remove the scheduled PAUSE after layer " + parent.pauseLayer + "."
                            fixedWidthMode: true
                            onClicked: base.removePauseAtLayerRequested(parent.pauseLayer)
                        }
                    }
                }

                Cura.SecondaryButton
                {
                    width: parent.width
                    height: UM.Theme.getSize("action_button").height
                    text: "Clear all pauses"
                    tooltip: "Remove every scheduled end-of-layer PAUSE for the current print."
                    fixedWidthMode: true
                    onClicked: base.clearPauseAtLayersRequested()
                }
            }
'''
text = replace_once(text, old_ui, new_ui, "QML scheduled pause management UI")
path.write_text(text, encoding="utf-8")


# Core tests: behavioural coverage for the strict end-of-layer transition rule.
path = ROOT / "tests" / "test_core.py"
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    'from Core import OperationContext, OperationPhase, RemoteFileIdentity, preview_override_kind\n',
    'from Core import (\n    OperationContext,\n    OperationPhase,\n    RemoteFileIdentity,\n    due_end_of_layer_pauses,\n    preview_override_kind,\n)\n',
    "Core test import",
)
marker = '''    def test_preview_override_detects_upper_layer_change(self):
'''
tests = '''    def test_end_of_layer_pause_is_not_due_when_target_layer_is_reached(self):
        self.assertEqual(due_end_of_layer_pauses({91}, 91), [])

    def test_end_of_layer_pause_becomes_due_after_transition(self):
        self.assertEqual(due_end_of_layer_pauses({91}, 92), [91])

    def test_end_of_layer_pause_handles_poll_skips_and_orders_targets(self):
        self.assertEqual(due_end_of_layer_pauses({94, 91, 92}, 94), [91, 92])

    def test_preview_override_detects_upper_layer_change(self):
'''
text = replace_once(text, marker, tests, "Core pause trigger tests")
path.write_text(text, encoding="utf-8")


# Pause-specific release contracts.
path = ROOT / "tests" / "test_pause_at_layer.py"
text = path.read_text(encoding="utf-8")
old_method = '''    def test_preview_can_toggle_multiple_future_layers(self):
        self.assertIn("pauseAtLayerRequested", QML)
        self.assertIn('"Enable pause at layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause at layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn("Scheduled PAUSE layers:", FOLLOWER)
        self.assertIn("selected_layer > current", FOLLOWER)
'''
new_method = '''    def test_preview_can_toggle_and_manage_multiple_future_layers(self):
        self.assertIn("pauseAtLayerRequested", QML)
        self.assertIn("removePauseAtLayerRequested", QML)
        self.assertIn("clearPauseAtLayersRequested", QML)
        self.assertIn('"Enable pause at end of layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('"Remove pause after layer " + base.pauseAtLayerCandidate', QML)
        self.assertIn('text: "Enabled pauses"', QML)
        self.assertIn('text: "Clear all pauses"', QML)
        self.assertIn("pauseAtLayerItems", FOLLOWER)
        self.assertIn("def _remove_scheduled_pause", FOLLOWER)
        self.assertIn("def _clear_scheduled_pauses_from_preview", FOLLOWER)
        self.assertIn("selected_layer > current", FOLLOWER)
        self.assertIn("selected_layer >= max_layer", FOLLOWER)
'''
text = replace_once(text, old_method, new_method, "Pause management test")
old_poll = '''    def test_polling_can_cross_a_short_target_layer_without_missing_pause(self):
        self.assertIn("layer <= current_layer", FOLLOWER)
        self.assertIn("due = sorted", FOLLOWER)
        self.assertIn("for layer in due:", FOLLOWER)
'''
new_poll = '''    def test_pause_occurs_only_after_target_layer_has_finished(self):
        self.assertIn("due_end_of_layer_pauses", FOLLOWER)
        self.assertIn("layer < current", (PLUGINS / "Core.py").read_text(encoding="utf-8"))
        self.assertIn("for layer in due:", FOLLOWER)
        self.assertIn("transition observed at layer", FOLLOWER)
'''
text = replace_once(text, old_poll, new_poll, "End-of-layer contract test")
path.write_text(text, encoding="utf-8")

print("Applied pause management and end-of-layer semantics")
