"""Structural checks on the suite specs — the host-side guard the
probe-code-in-string-constants pattern lacked: a bad escape or a
typo'd op used to surface only inside the container, forty minutes
into a gate (the engineering panel's finding)."""

import ast
import os
import sys
import unittest

# The harness modules live beside this file; the host discovery
# starts elsewhere.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scenarios as _scenarios
import runner as _runner


def _inline_code_values(spec):
    for step in spec.get("steps", ()):
        code = step.get("code")
        if isinstance(code, str):
            yield code


class HarnessSpecTests(unittest.TestCase):
    def test_ui_test_holds_one_run_per_container(self):
        # The 2026-09-16 census loss: two ui_test runs sharing one
        # container restage the workdir and kill each other's
        # simulator mid-scenario. The script must carry the
        # per-container lock so a second run refuses up front.
        script = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + "/tools/ui_test.sh")
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn(".ui_test-lock-$CONTAINER", source)
        self.assertIn("one run per container at a time", source)
        self.assertIn("trap 'rm -rf \"$LOCK_DIR\"' EXIT", source)

    def test_every_run_scans_the_cura_log_for_plugin_noise(self):
        # The 2026-09-16 log-scan ruling: every run reads the Cura log
        # and fails on plugin-originated noise regardless of scenario
        # — the polish loop slipped through because no scenario
        # asserted it. The scan, its match set and the runner-verdict
        # propagation must all be pinned.
        script = (os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))) + "/tools/ui_test.sh")
        with open(script, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("scan_cura_log", source)
        self.assertIn("grep -nE 'MoonrakerPrintFollower|/Moonraker[A-Za-z]+\\.qml'", source)
        self.assertIn("CURA LOG NOISE (the log-scan ruling)", source)
        self.assertIn("|| RUNNER_RC=$?", source)
        self.assertIn('if [ "${RUNNER_RC:-0}" -ne 0 ]; then', source)

    def test_no_resize_crosses_the_application_window_floor(self):
        # The application's own floor is the bottom: a target below it
        # is a size Cura refuses, a user cannot drag there, so the step
        # would rest on a geometry nobody can reach (and on Windows the
        # refused request silently measured a different size than the
        # spec asked for). The "min" token asks the driver for the
        # platform's own floor; a literal has to clear the largest
        # floor, since one spec runs on every platform.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                if step.get("op") != "resize_window":
                    continue
                if "force" in step:
                    offenders.append(f"{spec['id']}: forces past the window floor")
                for axis, floor in (("w", _scenarios.WINDOW_FLOOR_W),
                                    ("h", _scenarios.WINDOW_FLOOR_H)):
                    value = step.get(axis)
                    if value == "min":
                        continue
                    if not isinstance(value, int) or value < floor:
                        offenders.append(
                            f"{spec['id']}: {axis}={value!r} below the floor {floor}")
        self.assertEqual(offenders, [])

    def test_the_driver_cannot_relax_the_window_floor(self):
        # The floor may not be dropped to build a geometry a user
        # cannot reach: the driver's resize verb takes the minimum from
        # the live window and refuses a target below it, and nothing in
        # the harness overrides the window's own minimum.
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("setMinimumSize", source)
        self.assertIn("below the window minimum", source)

    def test_a_frame_is_captured_only_when_cura_holds_the_display(self):
        # The evidence records the DISPLAY: a step that hands it to
        # another process — a link press opening a browser — makes
        # every frame after it evidence of that process instead (the
        # owner watched Edge take the screen mid-suite-probe). The
        # guard must run BEFORE the capture, its verdict must be able
        # to fail the step, and the driver's answer must read the OS
        # where the OS can be asked rather than assuming Qt's word.
        with open(_runner.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"cmd": "foreground"', source)
        self.assertIn("def foreground_guard(", source)
        self.assertIn("def _foreground_verdict(", source)
        guard = source.index("foreground = foreground_guard()")
        capture = source.index("capture = shot(name)", guard)
        self.assertLess(guard, capture, "the guard must run before the capture")
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            driver_source = handle.read()
        self.assertIn('if cmd == "foreground":', driver_source)
        self.assertIn("GetWindowThreadProcessId", driver_source)
        self.assertIn("def _raise_main_window(", driver_source)
        # A platform with no foreground authority must be recorded,
        # never failed: the calibration is the observed activation.
        self.assertIn("_FOREGROUND_SEEN", driver_source)
        self.assertIn('"none"', driver_source)

    def test_a_press_aims_at_the_body_as_drawn(self):
        # A rotated control (the collapsed rails run at -90°) must be
        # aimed at its own centre. Adding w/2, h/2 to the mapped ORIGIN
        # mixed item axes into scene axes: the aim landed beside the
        # rail, so every press at one hit empty space and every rail
        # rect read "NOT in view" while the rail rendered.
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn("def _aim_point(", source)
        self.assertNotIn("scene.x() + target.width() / 2", source)
        self.assertNotIn("scene.x() + item.width() / 2", source)
        self.assertIn("item.mapToScene(QPointF(float(item.width()) / 2.0",
                      source)

    def test_a_step_never_addresses_a_name_on_a_type_no_walk_reaches(self):
        # The harness addresses the plugin through QQuickItem.childItems()
        # — the VISUAL tree. A Popup, a Menu, a Window: none of those is
        # an Item, so a step naming one can never resolve. It fails
        # slowly and confusingly: the controls INSIDE the popup answer
        # normally, so the leg's other steps pass while the wait for the
        # popup itself times out (measured: the macOS printing leg's
        # wait for the replace prompt timed out while both its buttons
        # were pressed without complaint).
        #
        # A name NOTHING addresses is fine and several exist (a popup's
        # own handle, recorded as exclusions) — the rule is about the
        # names the suite actually presses and reads.
        import re
        from pathlib import Path
        plugins = Path(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))) / "plugins"
        addressed = {step["objectName"] for spec in _scenarios.SCENARIOS
                     for step in spec.get("steps", ()) if step.get("objectName")}
        non_items = ("Popup", "Menu", "Dialog", "Window", "ToolTip", "Action",
                     "MessageDialog", "FileDialog", "ColorDialog", "FolderDialog",
                     "Drawer")
        declaration = re.compile(
            r"^\s*(?:[A-Z][\w.]*\.)?(" + "|".join(non_items) + r")\s*\{\s*$")
        offenders = []
        for path in sorted(plugins.glob("*.qml")):
            lines = path.read_text(encoding="utf-8").split("\n")
            for index, line in enumerate(lines):
                if not declaration.match(line):
                    continue
                depth = line.count("{") - line.count("}")
                for offset in range(index + 1, len(lines)):
                    body = lines[offset]
                    if depth == 0:
                        break
                    name = re.match(r"^\s*objectName\s*:\s*\"([^\"]+)\"", body)
                    if depth == 1 and name and name.group(1) in addressed:
                        offenders.append(f"{path.name}:{offset + 1}: {name.group(1)}")
                    depth += body.count("{") - body.count("}")
        self.assertEqual(offenders, [],
                         "a step addresses a name on a type no walk reaches: %s" % offenders)

    def test_the_replace_prompt_is_the_cards_own_dialog(self):
        # The replace prompt was a QMessageBox, and the box-shaped
        # modal is what broke the macOS legs: a native alert answered by
        # the harness while the plugin never returned from its nested
        # loop, so a dozen steps failed downstream of a step that
        # reported success. It is the card's popup now, answered by a
        # real press on a rendered control like every other scenario.
        #
        # The four halves of the contract, each of which has been the
        # broken one at some point:
        #   * the plugin opens no widget dialog at all,
        #   * the card renders the prompt and owns its two buttons,
        #   * the MODEL is the single authority on whether it is up,
        #   * every leg that loads a print presses the button.
        cura = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "plugins", "CuraIntegration.py")
        with open(cura, encoding="utf-8") as handle:
            integration = handle.read()
        self.assertNotIn("QMessageBox", integration)
        self.assertNotIn("QtWidgets", integration)
        self.assertNotIn("confirm_replace", integration)

        card = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "plugins", "MoonrakerPreviewCard.qml")
        with open(card, encoding="utf-8") as handle:
            qml = handle.read()
        self.assertIn('objectName: "moonrakerReplacePrompt"', qml)
        self.assertIn('objectName: "moonrakerReplaceConfirmButton"', qml)
        self.assertIn('objectName: "moonrakerReplaceCancelButton"', qml)
        self.assertIn("modal: true", qml)
        # The state is the model's, and the popup is written from the
        # change handler rather than bound: bindings on setProperty-fed
        # values go stale on this dynamically created component (the
        # same reason updateCardGate exists), and a stale binding is a
        # prompt that never opens.
        self.assertIn("function updateReplacePrompt()", qml)
        self.assertIn("replacePromptDialog.visible = base.replacePromptVisible && base.gateVisible",
                      qml)
        self.assertIn("onReplacePromptVisibleChanged: updateReplacePrompt()", qml)
        # Gated on the card's own visibility: the card is instantiated
        # twice and both copies receive the published value.
        self.assertNotIn("visible: base.replacePromptVisible", qml)

        coordinator = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "plugins", "PrintCoordinator.py")
        with open(coordinator, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('self._presentation.publish({"replacePromptVisible": True})', source)
        self.assertIn("def _replace_confirmed(self):", source)
        self.assertIn("def _replace_cancelled(self):", source)
        # The pending load is the coordinator's, and answering twice
        # must not run it twice: the handler clears the field it read.
        self.assertIn("action, self._replace_action = self._replace_action, None", source)

        # Every scenario that loads a print over existing contents
        # presses the button - and none of them answers a box.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            steps = spec.get("steps", ())
            for index, step in enumerate(steps):
                if step.get("objectName") != "moonrakerReplaceConfirmButton":
                    continue
                if step.get("op") == "wait_rect":
                    continue  # the witness, judged below
                if step.get("op") != "deliver_click":
                    offenders.append(f"{spec['id']}: {step.get('op')} on the prompt")
                # The press must be WITNESSED first: a press at a
                # control that is not up yet is the race the widget
                # path had, and an absent read afterwards would make
                # the miss a vacuous pass.
                witness = [s for s in steps[:index]
                           if s.get("op") == "wait_rect"
                           and s.get("objectName") == "moonrakerReplaceConfirmButton"
                           and not s.get("absent")]
                if not witness:
                    offenders.append(f"{spec['id']}: the prompt is pressed unpainted")
        self.assertEqual(offenders, [])
        self.assertTrue(any(step.get("objectName") == "moonrakerReplaceConfirmButton"
                            for spec in _scenarios.SCENARIOS
                            for step in spec.get("steps", ())),
                        "no scenario presses the prompt at all")

    def test_a_confirm_is_pressed_not_driven_as_a_slot(self):
        # The static-recording finding: the visual leg confirmed its
        # rename by calling the slot, which leaves the modal popup
        # open — its scrim then covers every later scenario, so the
        # leg's clicks are ones a user could not make and its
        # recording's last minute never moves. A confirm is the
        # dialog's own verb, pressed on screen.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                if step.get("op") not in ("exec_slot", "exec_file_slot"):
                    continue
                if "confirm" in str(step.get("slot", "")).lower():
                    offenders.append(f"{spec['id']}: {step['slot']} driven as a slot")
        self.assertEqual(offenders, [])

    def test_the_rename_flow_witnesses_its_dialog_closing(self):
        # The press that confirms must also be shown to close the
        # popup, and the absence must be WITNESSED: the witness step
        # (the dialog on screen) comes first, so the absent read is
        # never the vacuous "never observed" pass.
        spec = next(s for s in _scenarios.SCENARIOS
                    if s["id"] == "v10")
        steps = spec["steps"]
        witness = next(i for i, s in enumerate(steps)
                       if s.get("op") == "wait_rect" and s.get("text") == "Rename file"
                       and not s.get("absent"))
        press = next(i for i, s in enumerate(steps)
                     if s.get("op") == "deliver_click"
                     and s.get("objectName") == "renameConfirmButton")
        gone = next(i for i, s in enumerate(steps)
                    if s.get("op") == "wait_rect" and s.get("text") == "Rename file"
                    and s.get("absent"))
        self.assertLess(witness, press, "the dialog must be on screen before the press")
        self.assertLess(press, gone, "the press must come before the popup is gone")

    def test_a_visual_scenario_that_opens_the_file_manager_closes_it(self):
        # The manager is a full-area page: left open it hides the
        # panes the rest of the visual leg asserts and presses, so
        # those steps would pass over a screen showing something
        # else entirely. The way out is the page's own Close button
        # (a press, not a slot), so the departure is on screen too.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            if spec.get("group") != "visual":
                continue
            steps = spec.get("steps", ())
            slots = [str(step.get("slot", "")) for step in steps
                     if step.get("op") in ("exec_slot", "exec_file_slot")]
            closes = [step for step in steps
                      if step.get("objectName") == "fileManagerCloseButton"]
            if "openFileManager" in slots and not (closes or "setFileManagerOpen" in slots):
                offenders.append(spec["id"])
        self.assertEqual(offenders, [])

    def test_spec_ids_are_unique(self):
        ids = [spec["id"] for spec in _scenarios.SCENARIOS]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        self.assertEqual(duplicates, [])

    def test_every_probe_constant_compiles(self):
        broken = []
        for name in dir(_scenarios):
            if name.isupper() and isinstance(getattr(_scenarios, name), str):
                try:
                    compile(getattr(_scenarios, name), f"<{name}>", "exec")
                except SyntaxError as exc:
                    broken.append((name, str(exc)))
        self.assertEqual(broken, [])

    def test_every_inline_code_step_compiles(self):
        broken = []
        for spec in _scenarios.SCENARIOS:
            for code in _inline_code_values(spec):
                try:
                    compile(code, f"<{spec['id']}>", "exec")
                except SyntaxError as exc:
                    broken.append((spec["id"], str(exc)[:80]))
        self.assertEqual(broken, [])

    def test_every_exec_code_step_declares_its_verbs(self):
        # The exec_code closure (the round-2 HIGH-1): inline code is
        # the hiding place for direct invocations — E_STOP_SEQUENCE
        # called printer.emergencyStopClick() invisibly. Every
        # exec_code step now declares what it invokes, and each
        # declaration must name something the code actually contains.
        offenders = []
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                if step.get("op") != "exec_code":
                    continue
                verbs = step.get("verbs")
                if verbs is None:
                    offenders.append(f"{spec['id']}: exec_code step without verbs")
                    continue
                code = step.get("code") or ""
                # Template-based steps store the CONSTANT name as
                # code — resolve it so the verb is checked against
                # the code text, not the name.
                resolved = getattr(_scenarios, code, code) if code.isupper() else code
                for verb in verbs:
                    if verb not in resolved:
                        offenders.append(f"{spec['id']}: verbs declares {verb!r}, not in the code")
        self.assertEqual(offenders, [])

    def test_every_spec_op_exists_in_the_runner(self):
        with open(_runner.__file__, encoding="utf-8") as handle:
            source = handle.read()
        ops = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.If) and isinstance(node.test, ast.Compare):
                left, right = node.test.left, node.test.comparators[0]
                if (isinstance(left, ast.Name) and left.id == "op"
                        and isinstance(right, ast.Constant) and isinstance(right.value, str)):
                    ops.add(right.value)
        missing = {}
        for spec in _scenarios.SCENARIOS:
            for step in spec.get("steps", ()):
                op = step.get("op")
                if op and op not in ops:
                    missing.setdefault(op, []).append(spec["id"])
        self.assertEqual(missing, {})

    def test_the_presentation_probe_asks_the_app_not_the_screen(self):
        # A window whose scene graph stopped presenting answers every
        # tree read, so a leg can pass every assertion over a screen
        # that never moved (the static-green ruling). The probe must
        # therefore read the app's own frame count: the driver attaches
        # to frameSwapped and reports it, and the runner samples it at
        # the start and the end of every scenario.
        driver = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "driver", "__init__.py")
        with open(driver, encoding="utf-8") as handle:
            driver_source = handle.read()
        self.assertIn('if cmd == "frames":', driver_source)
        self.assertIn("frameSwapped.connect", driver_source)
        self.assertIn("requestUpdate", driver_source)
        self.assertIn("isExposed", driver_source)
        with open(_runner.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('"cmd": "frames"', source)
        self.assertIn("def frames_probe(", source)
        self.assertIn("def frames_verdict(", source)
        start = source.index('frames_probe("start", spec["id"])')
        end = source.index('frames_probe("end", spec["id"])')
        self.assertLess(start, end)
        self.assertIn('run["frames"] = FRAME_PROBES', source)
        # The kick must not assume the Qt6 spelling: Cura 5.x ships
        # Qt 5.15, where the same request is update().
        self.assertIn("getattr(window, \"requestUpdate\", None) or window.update",
                      driver_source)
        # And the enums go out as names: PyQt6 hands back the wrapper
        # (QWindow.Visibility) and int() on it raises — the first live
        # run of the probe answered every sample with that TypeError.
        self.assertIn("def _enum_name(", driver_source)
        self.assertNotIn("int(window.visibility())", driver_source)


if __name__ == "__main__":
    unittest.main()
